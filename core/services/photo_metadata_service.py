"""Servicio de dominio para extracción, análisis y gestión de metadatos fotográficos."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..domain.models.photo_metadata import (
    EvidenceManifest,
    MetadataAnalysisSummary,
    MetadataSessionResult,
    PhotoMetadataRecord,
)

logger = logging.getLogger("core.photo_metadata_service")


class PhotoMetadataService:
    """Orquesta la ejecución de análisis forense EXIF/GPS y la gestión de sesiones."""

    def __init__(
        self,
        script_path: Optional[Path] = None,
        base_backup_dir: Optional[Path] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> None:
        if script_path is None:
            # Buscar android_photo_metadata.ps1 en la raíz del proyecto
            root_candidate = Path(__file__).resolve().parent.parent.parent / "android_photo_metadata.ps1"
            if root_candidate.exists():
                self.script_path = root_candidate
            else:
                self.script_path = Path("android_photo_metadata.ps1").resolve()
        else:
            self.script_path = Path(script_path).resolve()

        if base_backup_dir is None:
            self.base_backup_dir = Path(os.environ.get("USERPROFILE", "")) / "Downloads" / "AndroidBackup"
        else:
            self.base_backup_dir = Path(base_backup_dir).resolve()

        self.on_log = on_log
        self.on_progress = on_progress
        self._current_process: Optional[subprocess.Popen] = None
        self._cancel_requested = False

    def _log(self, message: str, level: str = "INFO") -> None:
        logger.info("[%s] %s", level, message)
        if self.on_log:
            try:
                self.on_log(message, level)
            except Exception:
                pass

    def _update_progress(self, percent: float, status: str) -> None:
        if self.on_progress:
            try:
                self.on_progress(percent, status)
            except Exception:
                pass

    def cancel(self) -> None:
        """Solicita cancelación del proceso en ejecución."""
        self._cancel_requested = True
        if self._current_process and self._current_process.poll() is None:
            try:
                self._current_process.terminate()
                self._log("Proceso de metadatos cancelado por el usuario.", "WARN")
            except Exception as ex:
                self._log(f"Error al cancelar proceso: {ex}", "ERROR")

    def run_analysis(
        self,
        input_type: str,
        input_path: str = "",
        reuse_backup: bool = True,
        fresh_pull: bool = False,
        output_dir: Optional[str] = None,
        download_exiftool: bool = False,
    ) -> MetadataSessionResult:
        """Ejecuta el pipeline forense en PowerShell y retorna el resultado de la sesión.

        Args:
            input_type: 'single', 'folder', 'archive', 'device', 'download_tool'
            input_path: Ruta del archivo o carpeta a procesar
            reuse_backup: Si es True y input_type=='device', reusa descarga previa
            fresh_pull: Si es True y input_type=='device', fuerza descarga fresca
            output_dir: Directorio de salida opcional
            download_exiftool: Descarga la herramienta ExifTool portable
        """
        self._cancel_requested = False

        if not self.script_path.exists():
            raise FileNotFoundError(f"No se encontró el script de PowerShell: {self.script_path}")

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-NoOpenBrowser",
            "-NoZip",
        ]

        if download_exiftool:
            cmd.append("-DownloadExifTool")
        elif input_type == "single":
            if not input_path or not Path(input_path).exists():
                raise FileNotFoundError(f"El archivo de imagen no existe: {input_path}")
            cmd.extend(["-ImagePath", str(Path(input_path).resolve())])
        elif input_type == "folder":
            if not input_path or not Path(input_path).exists():
                raise FileNotFoundError(f"La carpeta no existe: {input_path}")
            cmd.extend(["-FolderPath", str(Path(input_path).resolve())])
        elif input_type == "archive":
            if not input_path or not Path(input_path).exists():
                raise FileNotFoundError(f"El archivo comprimido no existe: {input_path}")
            cmd.extend(["-ArchivePath", str(Path(input_path).resolve())])
        elif input_type == "device":
            cmd.append("-Device")
            if reuse_backup and not fresh_pull:
                cmd.append("-Reuse")
            elif fresh_pull:
                cmd.append("-Fresh")
        else:
            raise ValueError(f"Modo de entrada no reconocido: {input_type}")

        if output_dir:
            cmd.extend(["-OutputDir", str(Path(output_dir).resolve())])

        self._log(f"Iniciando ejecución de análisis de metadatos ({input_type})...", "INFO")
        self._update_progress(0.1, "Iniciando análisis forense...")

        detected_workdir: Optional[Path] = None

        try:
            self._current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            if self._current_process.stdout:
                for line in iter(self._current_process.stdout.readline, ""):
                    if self._cancel_requested:
                        break
                    line_clean = line.strip()
                    if not line_clean:
                        continue

                    # Parsear líneas de log emitidas por el script de PowerShell
                    if "[INFO]" in line_clean:
                        self._log(line_clean.split("[INFO]")[-1].strip(), "INFO")
                    elif "[SUCCESS]" in line_clean:
                        self._log(line_clean.split("[SUCCESS]")[-1].strip(), "SUCCESS")
                    elif "[WARN]" in line_clean:
                        self._log(line_clean.split("[WARN]")[-1].strip(), "WARN")
                    elif "[ERROR]" in line_clean:
                        self._log(line_clean.split("[ERROR]")[-1].strip(), "ERROR")
                    else:
                        self._log(line_clean, "DEBUG")

                    # Detección de progreso y rutas de trabajo
                    if "Directorio de trabajo:" in line_clean:
                        parts = line_clean.split("Directorio de trabajo:")
                        if len(parts) > 1:
                            detected_workdir = Path(parts[1].strip())
                    elif "Directorio de Resultados :" in line_clean:
                        parts = line_clean.split("Directorio de Resultados :")
                        if len(parts) > 1:
                            detected_workdir = Path(parts[1].strip())

                    if "Extrayendo archivo comprimido" in line_clean:
                        self._update_progress(0.2, "Descomprimiendo en directorio dedicado...")
                    elif "Extrayendo metadatos" in line_clean:
                        self._update_progress(0.4, "Extrayendo metadatos EXIF / GPS...")
                    elif "Normalizando" in line_clean:
                        self._update_progress(0.65, "Normalizando registros y generando miniaturas...")
                    elif "Generando estadisticas forenses" in line_clean:
                        self._update_progress(0.8, "Calculando clusters geoespaciales...")
                    elif "Compilando mapa interactivo" in line_clean:
                        self._update_progress(0.9, "Generando mapa y dashboard...")
                    elif "Calculando hashes SHA-256" in line_clean:
                        self._update_progress(0.95, "Sellando evidencias criptográficas...")

            self._current_process.wait()
            ret_code = self._current_process.returncode

            if ret_code != 0 and not self._cancel_requested:
                raise RuntimeError(f"El proceso de PowerShell finalizó con código de error {ret_code}.")

        finally:
            self._current_process = None

        self._update_progress(1.0, "Análisis completado con éxito.")

        # Determinar la carpeta de la sesión generada
        if output_dir and Path(output_dir).exists():
            final_dir = Path(output_dir)
        elif detected_workdir and detected_workdir.exists():
            final_dir = detected_workdir
        else:
            # Buscar la sesión más reciente en base_backup_dir
            latest = self.get_latest_session_dir()
            if latest:
                final_dir = latest
            else:
                raise FileNotFoundError("No se encontró el directorio de resultados generado.")

        return self.load_session(final_dir)

    def get_latest_session_dir(self) -> Optional[Path]:
        """Obtiene la carpeta de sesión más reciente en Downloads/AndroidBackup."""
        if not self.base_backup_dir.exists():
            return None
        dirs = [
            d for d in self.base_backup_dir.iterdir()
            if d.is_dir() and d.name.startswith("Metadata_")
        ]
        if not dirs:
            return None
        dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
        return dirs[0]

    def list_previous_sessions(self) -> List[Dict[str, Any]]:
        """Lista todas las sesiones previas de análisis de metadatos disponibles."""
        sessions: List[Dict[str, Any]] = []
        if not self.base_backup_dir.exists():
            return sessions

        for d in self.base_backup_dir.iterdir():
            if not d.is_dir() or not d.name.startswith("Metadata_"):
                continue

            manifest_file = d / "evidence_manifest.json"
            summary_file = d / "analysis_summary.json"

            session_info = {
                "session_id": d.name,
                "path": str(d),
                "created_time": d.stat().st_mtime,
                "total_images": 0,
                "gps_images": 0,
                "source_type": "Desconocido",
            }

            if manifest_file.exists():
                try:
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        m_data = json.load(f)
                        session_info["source_type"] = m_data.get("SourceType", "Desconocido")
                        session_info["total_images"] = m_data.get("TotalImages", 0)
                except Exception:
                    pass

            if summary_file.exists():
                try:
                    with open(summary_file, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                        session_info["gps_images"] = s_data.get("GpsImages", 0)
                        if not session_info["total_images"]:
                            session_info["total_images"] = s_data.get("TotalImages", 0)
                except Exception:
                    pass

            sessions.append(session_info)

        sessions.sort(key=lambda s: s["created_time"], reverse=True)
        return sessions

    def load_session(self, session_dir: Path | str) -> MetadataSessionResult:
        """Carga los artefactos de una sesión previa o recién generada."""
        s_path = Path(session_dir).resolve()
        if not s_path.exists():
            raise FileNotFoundError(f"La carpeta de sesión no existe: {s_path}")

        manifest_file = s_path / "evidence_manifest.json"
        summary_file = s_path / "analysis_summary.json"
        norm_json_file = s_path / "normalized_metadata.json"
        map_html = s_path / "map.html"
        dash_html = s_path / "dashboard.html"
        raw_csv = s_path / "raw_metadata.csv"

        manifest = EvidenceManifest(session_id=s_path.name)
        if manifest_file.exists():
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = EvidenceManifest.from_dict(json.load(f))
            except Exception as ex:
                self._log(f"Aviso: Error al parsear evidence_manifest.json: {ex}", "WARN")

        summary = MetadataAnalysisSummary()
        if summary_file.exists():
            try:
                with open(summary_file, "r", encoding="utf-8") as f:
                    summary = MetadataAnalysisSummary.from_dict(json.load(f))
            except Exception as ex:
                self._log(f"Aviso: Error al parsear analysis_summary.json: {ex}", "WARN")

        records: List[PhotoMetadataRecord] = []
        if norm_json_file.exists():
            try:
                with open(norm_json_file, "r", encoding="utf-8") as f:
                    raw_records = json.load(f)
                    if isinstance(raw_records, list):
                        records = [PhotoMetadataRecord.from_dict(r) for r in raw_records]
                    elif isinstance(raw_records, dict):
                        records = [PhotoMetadataRecord.from_dict(raw_records)]
            except Exception as ex:
                self._log(f"Aviso: Error al parsear normalized_metadata.json: {ex}", "WARN")

        return MetadataSessionResult(
            session_dir=str(s_path),
            manifest=manifest,
            summary=summary,
            records=records,
            map_html_path=str(map_html) if map_html.exists() else "",
            dashboard_html_path=str(dash_html) if dash_html.exists() else "",
            raw_csv_path=str(raw_csv) if raw_csv.exists() else "",
        )

    def verify_manifest_integrity(self, session_dir: Path | str) -> Dict[str, Dict[str, Any]]:
        """Verifica la integridad criptográfica SHA-256 de cada artefacto frente al manifiesto."""
        s_path = Path(session_dir).resolve()
        manifest_file = s_path / "evidence_manifest.json"
        if not manifest_file.exists():
            raise FileNotFoundError(f"No existe evidence_manifest.json en: {s_path}")

        with open(manifest_file, "r", encoding="utf-8") as f:
            m_data = json.load(f)
        manifest = EvidenceManifest.from_dict(m_data)

        verification_results: Dict[str, Dict[str, Any]] = {}

        for art in manifest.artifacts:
            art_path = s_path / art.file_name
            if not art_path.exists():
                verification_results[art.file_name] = {
                    "expected_sha256": art.sha256,
                    "actual_sha256": "",
                    "valid": False,
                    "error": "Archivo no encontrado en disco",
                }
                continue

            try:
                sha256 = hashlib.sha256()
                with open(art_path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        sha256.update(chunk)
                current_hash = sha256.hexdigest().upper()
                is_valid = (current_hash == art.sha256.upper())
                verification_results[art.file_name] = {
                    "expected_sha256": art.sha256.upper(),
                    "actual_sha256": current_hash,
                    "valid": is_valid,
                    "error": "" if is_valid else "El hash no coincide (posible alteración)",
                }
            except Exception as ex:
                verification_results[art.file_name] = {
                    "expected_sha256": art.sha256,
                    "actual_sha256": "",
                    "valid": False,
                    "error": str(ex),
                }

        return verification_results

