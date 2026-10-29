"""Servicio avanzado de exploración y gestión de archivos en Android ADB (Fase 5B).

Provee capacidades de:
1. Listado de directorios con soporte para tamaños, fechas UNIX y enlaces (list_dir).
2. Exploración en árbol acotada en profundidad (tree) para evitar sobrecargar memoria/ADB.
3. Búsqueda de archivos y carpetas con filtros por glob, extensión, tamaño y fecha (search).
4. Estadísticas de archivos (stat) y estimación de tamaño de carpetas (folder_stat con 'du' o UNSUPPORTED).
5. Operaciones masivas:
   - download_paths: descarga masiva hacia el host (síncrona o encolable vía JobQueueService).
   - upload_paths: subida masiva hacia el dispositivo (síncrona o encolable vía JobQueueService).
   - delete_remote: eliminación segura con papelera remota (.trash) por defecto en lugar de rm -rf.
6. Robustez y seguridad:
   - Selección explícita obligatoria de serial.
   - Bloqueo preventivo de operaciones masivas sobre rutas raíz o de sistema sin flag confirm_root_access=True.
   - Manejo estricto de timeouts, OperationResult y escape de rutas.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.domain.models.adb import OperationResult
from core.infrastructure.adb.adb_service import AdbError, AdbService
from core.domain.models.explorer import (
    BulkItemResult,
    BulkOperationResult,
    BulkOperationType,
    ExplorerFilter,
    FileNode,
    FolderStatResult,
    SearchResult,
)
from core.services.job_queue_service import JobQueueService, JobType

logger = logging.getLogger("ui.services.file_explorer_service")

# Rutas del sistema consideradas críticas o peligrosas para operaciones masivas destructivas
DANGEROUS_ROOT_PATHS: Set[str] = {
    "/",
    "/system",
    "/system/bin",
    "/system/xbin",
    "/vendor",
    "/boot",
    "/recovery",
    "/etc",
    "/dev",
    "/proc",
    "/sys",
    "/data",
}


def _escape_shell_path(path: str) -> str:
    """Escapa comillas simples en una ruta para uso seguro dentro de comillas simples en sh/toybox."""
    return path.replace("'", "'\\''")


def _normalize_remote_path(path: str) -> str:
    """Normaliza una ruta remota estilo UNIX."""
    cleaned = path.replace("\\", "/").strip()
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    # Eliminar barras redundantes
    parts = [p for p in cleaned.split("/") if p and p != "."]
    normalized = "/" + "/".join(parts)
    return normalized if normalized else "/"


class FileExplorerService:
    """Servicio de exploración de archivos y operaciones remotas masivas."""

    def __init__(
        self,
        adb_service: Optional[AdbService] = None,
        job_queue_service: Optional[JobQueueService] = None,
        trash_dir: str = "/sdcard/.trash",
    ) -> None:
        self.adb = adb_service or AdbService()
        self.job_queue = job_queue_service
        self.trash_dir = _normalize_remote_path(trash_dir)

    # -------------------------------------------------------------------------
    # Métodos Auxiliares y Validaciones
    # -------------------------------------------------------------------------

    def _require_serial(self, serial: Optional[str]) -> str:
        """Verifica que el serial esté presente y no sea nulo ni vacío."""
        if not serial or not serial.strip():
            raise ValueError("SERIAL_REQUERIDO: Debe especificar el serial del dispositivo para operaciones del explorador.")
        return serial.strip()

    def _check_dangerous_path(self, path: str, confirm_root_access: bool) -> None:
        """Impide operar sobre rutas críticas de sistema sin confirmación explícita."""
        norm = _normalize_remote_path(path)
        is_dangerous = (
            norm == "/"
            or norm in DANGEROUS_ROOT_PATHS
            or any(norm.startswith(dp.rstrip("/") + "/") for dp in DANGEROUS_ROOT_PATHS if dp != "/")
        )
        if is_dangerous:
            if not confirm_root_access:
                raise ValueError(
                    f"OPERACION_PELIGROSA: No se permite operar sobre la ruta protegida '{norm}' "
                    "sin el parámetro explícito 'confirm_root_access=True'."
                )

    def _parse_ls_line(self, line: str, parent_path: str) -> Optional[FileNode]:
        """Parsea una línea devuelta por ls -la o ls -l."""
        line = line.strip()
        if not line or line.startswith("total "):
            return None

        # Regex para Toybox moderna (Android 7+): YYYY-MM-DD HH:MM
        iso_pattern = re.compile(
            r"^([dlcbps-][rwxstST-]{9})\s+(\d+\s+)?(\S+)\s+(\S+)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)\s+(.+)$"
        )
        m = iso_pattern.match(line)
        if m:
            perms = m.group(1)
            size = int(m.group(5))
            date_str = m.group(6)
            time_str = m.group(7)
            name_part = m.group(8)

            mtime = None
            try:
                mtime = datetime.strptime(f"{date_str} {time_str[:5]}", "%Y-%m-%d %H:%M")
            except Exception:
                pass

            is_dir = perms.startswith("d")
            is_link = perms.startswith("l")
            link_target = None
            name = name_part
            if is_link and " -> " in name_part:
                name, link_target = name_part.split(" -> ", 1)
                name = name.strip()
                link_target = link_target.strip()

            if name in (".", ".."):
                return None

            clean_parent = parent_path.rstrip("/")
            full_path = f"{clean_parent}/{name}" if clean_parent else f"/{name}"
            return FileNode(
                path=full_path,
                name=name,
                is_dir=is_dir,
                size=size if not is_dir else 0,
                mtime=mtime,
                is_link=is_link,
                link_target=link_target,
                permissions=perms,
            )

        # Regex tradicional (Month DD HH:MM / Year)
        trad_pattern = re.compile(
            r"^([dlcbps-][rwxstST-]{9})\s+(\d+\s+)?(\S+)\s+(\S+)\s+(\d+)\s+([A-Za-z]{3}\s+\d{1,2}\s+(?:\d{4}|\d{2}:\d{2}))\s+(.+)$"
        )
        m2 = trad_pattern.match(line)
        if m2:
            perms = m2.group(1)
            size = int(m2.group(5))
            name_part = m2.group(7)
            is_dir = perms.startswith("d")
            is_link = perms.startswith("l")
            link_target = None
            name = name_part
            if is_link and " -> " in name_part:
                name, link_target = name_part.split(" -> ", 1)
                name = name.strip()
                link_target = link_target.strip()

            if name in (".", ".."):
                return None

            clean_parent = parent_path.rstrip("/")
            full_path = f"{clean_parent}/{name}" if clean_parent else f"/{name}"
            return FileNode(
                path=full_path,
                name=name,
                is_dir=is_dir,
                size=size if not is_dir else 0,
                is_link=is_link,
                link_target=link_target,
                permissions=perms,
            )

        # Formato fallback (ls -1ap)
        clean_name = line.rstrip("\r\n")
        if clean_name.endswith("/"):
            name = clean_name[:-1]
            is_dir = True
        else:
            name = clean_name
            is_dir = False

        if name in (".", "..") or not name:
            return None

        clean_parent = parent_path.rstrip("/")
        full_path = f"{clean_parent}/{name}" if clean_parent else f"/{name}"
        return FileNode(
            path=full_path,
            name=name,
            is_dir=is_dir,
            size=0,
        )

    # -------------------------------------------------------------------------
    # Listado, Árbol y Estadísticas
    # -------------------------------------------------------------------------

    def list_dir(self, path: str, serial: str, timeout: float = 15.0) -> List[FileNode]:
        """
        Lista los archivos y directorios contenidos en la ruta remota especificada.
        Retorna la lista de objetos FileNode.
        """
        target_serial = self._require_serial(serial)
        clean_path = _normalize_remote_path(path)
        escaped = _escape_shell_path(clean_path)

        cmd = f"ls -la '{escaped}'"
        result: OperationResult = self.adb.shell(cmd, serial=target_serial, timeout=timeout)

        nodes: List[FileNode] = []
        if not result.success:
            # Si ls -la falló, intentar fallback con ls -1ap
            cmd_fallback = f"ls -1ap '{escaped}'"
            fallback_res = self.adb.shell(cmd_fallback, serial=target_serial, timeout=timeout)
            if not fallback_res.success:
                logger.warning("Fallo al listar ruta remota '%s' en [%s]: %s", clean_path, target_serial, result.output)
                return []
            lines = fallback_res.stdout.splitlines()
        else:
            lines = result.stdout.splitlines()

        for line in lines:
            node = self._parse_ls_line(line, clean_path)
            if node:
                nodes.append(node)

        # Ordenar: primero carpetas, luego archivos alfabéticamente
        nodes.sort(key=lambda n: (not n.is_dir, n.name.lower()))
        return nodes

    def tree(
        self,
        path: str,
        serial: str,
        max_depth: int = 2,
        filter: Optional[ExplorerFilter] = None,
        timeout: float = 30.0,
    ) -> FileNode:
        """
        Genera una estructura de árbol acotada hasta max_depth (para evitar sobrecargar ADB).
        """
        target_serial = self._require_serial(serial)
        clean_path = _normalize_remote_path(path)
        bounded_depth = max(0, min(max_depth, 5))

        root_name = clean_path.split("/")[-1] if clean_path != "/" else "/"
        root_node = FileNode(
            path=clean_path,
            name=root_name or "/",
            is_dir=True,
            children=[],
        )

        def _populate_children(parent: FileNode, current_depth: int) -> None:
            if current_depth >= bounded_depth:
                return

            try:
                children = self.list_dir(parent.path, serial=target_serial, timeout=timeout)
            except Exception as exc:
                logger.debug("Error listando rama %s: %s", parent.path, exc)
                children = []

            filtered_children: List[FileNode] = []
            for child in children:
                if filter and not filter.matches(child) and not child.is_dir:
                    continue
                if child.is_dir:
                    child.children = []
                    _populate_children(child, current_depth + 1)
                filtered_children.append(child)

            parent.children = filtered_children

        _populate_children(root_node, 0)
        return root_node

    def stat(self, path: str, serial: str, timeout: float = 10.0) -> Optional[FileNode]:
        """Obtiene información detallada de un archivo o carpeta específico."""
        target_serial = self._require_serial(serial)
        clean_path = _normalize_remote_path(path)
        escaped = _escape_shell_path(clean_path)

        cmd = f"ls -ld '{escaped}'"
        result: OperationResult = self.adb.shell(cmd, serial=target_serial, timeout=timeout)
        if not result.success or not result.stdout.strip():
            return None

        parent = "/".join(clean_path.rstrip("/").split("/")[:-1]) or "/"
        return self._parse_ls_line(result.stdout.strip().splitlines()[0], parent)

    def folder_stat(self, path: str, serial: str, timeout: float = 15.0) -> FolderStatResult:
        """
        Calcula el tamaño aproximado de una carpeta remota usando 'du -sk'.
        Si no es soportado o falla, devuelve supported=False con mensaje descriptivo.
        """
        target_serial = self._require_serial(serial)
        clean_path = _normalize_remote_path(path)
        escaped = _escape_shell_path(clean_path)

        cmd = f"du -sk '{escaped}' 2>/dev/null"
        result: OperationResult = self.adb.shell(cmd, serial=target_serial, timeout=timeout)

        if not result.success or not result.stdout.strip():
            return FolderStatResult(
                path=clean_path,
                supported=False,
                total_size_bytes=None,
                raw_output=result.output,
                message="UNSUPPORTED: El comando 'du' no está disponible o el acceso fue denegado.",
            )

        output_line = result.stdout.strip().splitlines()[0]
        m = re.match(r"^(\d+)\s+", output_line)
        if m:
            kb = int(m.group(1))
            return FolderStatResult(
                path=clean_path,
                supported=True,
                total_size_bytes=kb * 1024,
                raw_output=output_line,
                message="OK",
            )

        return FolderStatResult(
            path=clean_path,
            supported=False,
            total_size_bytes=None,
            raw_output=output_line,
            message="UNSUPPORTED: Formato de salida de 'du' no reconocido.",
        )

    # -------------------------------------------------------------------------
    # Búsqueda
    # -------------------------------------------------------------------------

    def search(
        self,
        root: str,
        filter: ExplorerFilter,
        serial: str,
        max_results: int = 100,
        timeout: float = 30.0,
    ) -> SearchResult:
        """
        Busca archivos y directorios a partir de una ruta raíz aplicando filtros.
        Utiliza 'find' de Toybox con límite de resultados y fallback a recorrido en árbol.
        """
        target_serial = self._require_serial(serial)
        clean_root = _normalize_remote_path(root)
        escaped_root = _escape_shell_path(clean_root)
        start_time = time.monotonic()

        pattern = filter.name_glob or "*"
        escaped_pattern = _escape_shell_path(pattern)

        # Ejecutar find con head para evitar volcado masivo en stdout
        cmd = f"find '{escaped_root}' -name '{escaped_pattern}' 2>/dev/null | head -n {max_results + 1}"
        result = self.adb.shell(cmd, serial=target_serial, timeout=timeout)

        items: List[FileNode] = []
        truncated = False

        if result.success and result.stdout.strip():
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            for p in lines:
                if p == clean_root:
                    continue
                leaf_name = p.rstrip("/").split("/")[-1]
                # Determinar si es dir básico
                is_dir = p.endswith("/")
                node = FileNode(
                    path=p,
                    name=leaf_name,
                    is_dir=is_dir,
                    size=0,
                )
                if filter.matches(node):
                    items.append(node)
                    if len(items) >= max_results:
                        truncated = True
                        break
        else:
            # Fallback: Búsqueda controlada usando list_dir hasta profundidad 3
            queue: List[str] = [clean_root]
            depth_map: Dict[str, int] = {clean_root: 0}

            while queue and len(items) < max_results:
                current_dir = queue.pop(0)
                curr_depth = depth_map.get(current_dir, 0)
                if curr_depth > 3:
                    continue

                try:
                    entries = self.list_dir(current_dir, serial=target_serial, timeout=10.0)
                except Exception:
                    continue

                for entry in entries:
                    if filter.matches(entry):
                        items.append(entry)
                        if len(items) >= max_results:
                            truncated = True
                            break
                    if entry.is_dir and (curr_depth + 1) <= 3:
                        queue.append(entry.path)
                        depth_map[entry.path] = curr_depth + 1

        duration = time.monotonic() - start_time
        return SearchResult(
            root=clean_root,
            total_found=len(items),
            items=items,
            truncated=truncated,
            duration_seconds=duration,
        )

    # -------------------------------------------------------------------------
    # Operaciones Masivas (Bulk Operations)
    # -------------------------------------------------------------------------

    def download_paths(
        self,
        serial: str,
        remote_paths: List[str],
        local_dir: str,
        enqueue_in_job_queue: bool = False,
        confirm_root_access: bool = False,
    ) -> BulkOperationResult:
        """
        Descarga masiva de una lista de rutas remotas hacia un directorio local.
        Si enqueue_in_job_queue=True y se configuró JobQueueService, se encola en la cola de trabajos.
        """
        target_serial = self._require_serial(serial)
        for rp in remote_paths:
            self._check_dangerous_path(rp, confirm_root_access)

        start_time = time.monotonic()
        dest_dir = Path(local_dir).resolve()
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Encolamiento opcional en JobQueueService
        if enqueue_in_job_queue and self.job_queue:
            job_id = self.job_queue.enqueue(
                job_type=JobType.FILE_TRANSFER,
                title=f"Descarga masiva de {len(remote_paths)} elementos",
                serial=target_serial,
                payload={
                    "operation": BulkOperationType.DOWNLOAD.value,
                    "remote_paths": remote_paths,
                    "local_dir": str(dest_dir),
                },
            )
            return BulkOperationResult(
                operation=BulkOperationType.DOWNLOAD,
                serial=target_serial,
                total_items=len(remote_paths),
                job_id=job_id,
                message=f"Descarga masiva encolada exitosamente en JobQueueService con ID: {job_id}",
            )

        # Ejecución directa y secuencial
        results: List[BulkItemResult] = []
        succeeded = 0
        failed = 0

        for rp in remote_paths:
            clean_rp = _normalize_remote_path(rp)
            leaf_name = clean_rp.rstrip("/").split("/")[-1]
            target_local = dest_dir / leaf_name

            res = self.adb.pull(clean_rp, str(target_local), serial=target_serial)
            if res.success:
                succeeded += 1
                bytes_done = target_local.stat().st_size if target_local.is_file() else 0
                results.append(
                    BulkItemResult(
                        source=clean_rp,
                        destination=str(target_local),
                        success=True,
                        bytes_transferred=bytes_done,
                    )
                )
            else:
                failed += 1
                results.append(
                    BulkItemResult(
                        source=clean_rp,
                        destination=str(target_local),
                        success=False,
                        error=res.output or "Error en adb pull",
                    )
                )

        duration = time.monotonic() - start_time
        return BulkOperationResult(
            operation=BulkOperationType.DOWNLOAD,
            serial=target_serial,
            total_items=len(remote_paths),
            succeeded_items=succeeded,
            failed_items=failed,
            results=results,
            duration_seconds=duration,
            message="Descarga masiva completada.",
        )

    def upload_paths(
        self,
        serial: str,
        local_paths: List[str],
        remote_dir: str,
        enqueue_in_job_queue: bool = False,
        confirm_root_access: bool = False,
    ) -> BulkOperationResult:
        """
        Subida masiva de una lista de rutas locales hacia un directorio remoto en el dispositivo.
        Si enqueue_in_job_queue=True y se configuró JobQueueService, se encola en la cola de trabajos.
        """
        target_serial = self._require_serial(serial)
        clean_remote_dir = _normalize_remote_path(remote_dir)
        self._check_dangerous_path(clean_remote_dir, confirm_root_access)

        start_time = time.monotonic()

        # Encolamiento opcional en JobQueueService
        if enqueue_in_job_queue and self.job_queue:
            job_id = self.job_queue.enqueue(
                job_type=JobType.FILE_TRANSFER,
                title=f"Subida masiva de {len(local_paths)} elementos",
                serial=target_serial,
                payload={
                    "operation": BulkOperationType.UPLOAD.value,
                    "local_paths": local_paths,
                    "remote_dir": clean_remote_dir,
                },
            )
            return BulkOperationResult(
                operation=BulkOperationType.UPLOAD,
                serial=target_serial,
                total_items=len(local_paths),
                job_id=job_id,
                message=f"Subida masiva encolada exitosamente en JobQueueService con ID: {job_id}",
            )

        # Asegurar directorio remoto de destino
        escaped_dest = _escape_shell_path(clean_remote_dir)
        self.adb.shell(f"mkdir -p '{escaped_dest}'", serial=target_serial)

        results: List[BulkItemResult] = []
        succeeded = 0
        failed = 0

        for lp in local_paths:
            path_obj = Path(lp)
            if not path_obj.exists():
                failed += 1
                results.append(
                    BulkItemResult(
                        source=lp,
                        destination=clean_remote_dir,
                        success=False,
                        error=f"Archivo local no encontrado: {lp}",
                    )
                )
                continue

            target_remote = f"{clean_remote_dir.rstrip('/')}/{path_obj.name}"
            res = self.adb.push(str(path_obj.resolve()), target_remote, serial=target_serial)

            if res.success:
                succeeded += 1
                bytes_done = path_obj.stat().st_size if path_obj.is_file() else 0
                results.append(
                    BulkItemResult(
                        source=lp,
                        destination=target_remote,
                        success=True,
                        bytes_transferred=bytes_done,
                    )
                )
            else:
                failed += 1
                results.append(
                    BulkItemResult(
                        source=lp,
                        destination=target_remote,
                        success=False,
                        error=res.output or "Error en adb push",
                    )
                )

        duration = time.monotonic() - start_time
        return BulkOperationResult(
            operation=BulkOperationType.UPLOAD,
            serial=target_serial,
            total_items=len(local_paths),
            succeeded_items=succeeded,
            failed_items=failed,
            results=results,
            duration_seconds=duration,
            message="Subida masiva completada.",
        )

    def delete_remote(
        self,
        serial: str,
        remote_paths: List[str],
        move_to_trash: bool = True,
        confirm_root_access: bool = False,
        confirm_permanent_delete: bool = False,
    ) -> BulkOperationResult:
        """
        Elimina de forma remota una lista de rutas.
        Por defecto implementa borrado seguro moviendo los elementos a la papelera remota (.trash)
        con sufijo temporal, evitando comandos rm -rf destructivos.
        Si move_to_trash=False, requiere confirm_permanent_delete=True obligatoriamente.
        """
        target_serial = self._require_serial(serial)
        for rp in remote_paths:
            self._check_dangerous_path(rp, confirm_root_access)

        if not move_to_trash and not confirm_permanent_delete:
            raise ValueError(
                "ELIMINACION_PERMANENTE_REQUERIDA: Para eliminar permanentemente archivos sin papelera "
                "debe proporcionar confirm_permanent_delete=True."
            )

        start_time = time.monotonic()
        results: List[BulkItemResult] = []
        succeeded = 0
        failed = 0

        if move_to_trash:
            # Crear directorio de papelera remoto si no existe
            escaped_trash = _escape_shell_path(self.trash_dir)
            self.adb.shell(f"mkdir -p '{escaped_trash}'", serial=target_serial)

            timestamp = int(time.time())
            for rp in remote_paths:
                clean_rp = _normalize_remote_path(rp)
                leaf_name = clean_rp.rstrip("/").split("/")[-1]
                trash_target = f"{self.trash_dir.rstrip('/')}/{leaf_name}_{timestamp}"

                cmd = f"mv '{_escape_shell_path(clean_rp)}' '{_escape_shell_path(trash_target)}'"
                res = self.adb.shell(cmd, serial=target_serial)

                if res.success:
                    succeeded += 1
                    results.append(
                        BulkItemResult(
                            source=clean_rp,
                            destination=trash_target,
                            success=True,
                        )
                    )
                else:
                    failed += 1
                    results.append(
                        BulkItemResult(
                            source=clean_rp,
                            destination=trash_target,
                            success=False,
                            error=res.output or "Error al mover a papelera",
                        )
                    )
        else:
            # Eliminación permanente con confirmación explícita
            for rp in remote_paths:
                clean_rp = _normalize_remote_path(rp)
                cmd = f"rm -r '{_escape_shell_path(clean_rp)}'"
                res = self.adb.shell(cmd, serial=target_serial)

                if res.success:
                    succeeded += 1
                    results.append(
                        BulkItemResult(
                            source=clean_rp,
                            destination=None,
                            success=True,
                        )
                    )
                else:
                    failed += 1
                    results.append(
                        BulkItemResult(
                            source=clean_rp,
                            destination=None,
                            success=False,
                            error=res.output or "Error al eliminar remotamente",
                        )
                    )

        duration = time.monotonic() - start_time
        return BulkOperationResult(
            operation=BulkOperationType.TRASH if move_to_trash else BulkOperationType.DELETE,
            serial=target_serial,
            total_items=len(remote_paths),
            succeeded_items=succeeded,
            failed_items=failed,
            results=results,
            duration_seconds=duration,
            message="Operación de eliminación/papelera completada.",
        )
