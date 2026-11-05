"""Servicio unificado de acceso y ejecución ADB (Python).

Cumple el mismo contrato que AndroidAdb.psm1 (Fase 1):
1. Selección explícita de dispositivo (-s <serial>). Nunca asume el primero si hay >1.
2. Timeouts por comando con cancelación de proceso (kill).
3. Reintentos configurables con backoff.
4. Códigos de error consistentes (OperationResult con exit_code y success).
5. Logging estructurado.
6. Operaciones: devices, connect, disconnect, shell, pull, push, install, forward, pair, get_property.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from core.domain.models.adb import ConnectionAttempt, ConnectionMode, Device, DeviceState, OperationResult, AdbError

logger = logging.getLogger("ui.services.adb_service")


class AdbError(RuntimeError):
    """Excepción base para fallos del contrato ADB con código de error normalizado."""
    def __init__(self, code: str, message: str, operation_result: Optional[OperationResult] = None):
        self.code = code
        self.message = message
        self.result = operation_result
        super().__init__(f"[{code}] {message}")


class AdbService:
    """Implementación del contrato ADB unificado en Python."""

    def __init__(self, adb_path: Optional[str] = None):
        self._adb_path = adb_path or self._detect_adb()

    @staticmethod
    def _detect_adb() -> str:
        """Encuentra la ruta del binario adb en el sistema."""
        try:
            import adbutils
            path = adbutils.adb_path()
            if path and os.path.isfile(path):
                return path
        except Exception:
            pass
        return "adb"

    @property
    def adb_executable(self) -> str:
        return self._adb_path

    def run_raw(
        self,
        arguments: List[str],
        serial: Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 0,
        retry_delay: float = 1.0,
    ) -> OperationResult:
        """
        Ejecuta un comando ADB con serial explícito opcional (-s <serial>),
        control de timeout, reintentos y logs estructurados.
        """
        cmd: List[str] = [self._adb_path]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(arguments)

        max_attempts = max(1, 1 + retries)
        last_result: Optional[OperationResult] = None

        kwargs: Dict[str, Any] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        display_cmd = " ".join(cmd)
        if "pair" in arguments and len(arguments) >= 3:
            # Ocultar código de pairing en logs
            display_cmd = f"{self._adb_path} pair {arguments[1]} <codigo_oculto>"

        for attempt in range(1, max_attempts + 1):
            start_time = time.monotonic()
            try:
                proc = subprocess.Popen(cmd, **kwargs)
                try:
                    stdout, stderr = proc.communicate(timeout=timeout if timeout > 0 else None)
                    exit_code = proc.returncode
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        stdout, stderr = proc.communicate(timeout=1.0)
                    except Exception:
                        stdout, stderr = "", ""
                    duration = time.monotonic() - start_time
                    last_result = OperationResult(
                        success=False,
                        exit_code=-1,
                        stdout=stdout or "",
                        stderr=stderr or "",
                        command=cmd,
                        serial=serial,
                        duration_seconds=duration,
                        message=f"TIMEOUT_EXCEDIDO: Timeout ({timeout}s) agotado para '{display_cmd}'",
                    )
                    logger.error(
                        "[TIMEOUT] Serial=%s Cmd=%s Attempt=%d/%d Duration=%.2fs",
                        serial or "-", display_cmd, attempt, max_attempts, duration
                    )
                    if attempt < max_attempts:
                        time.sleep(retry_delay)
                        continue
                    return last_result

                duration = time.monotonic() - start_time
                success = (exit_code == 0)
                last_result = OperationResult(
                    success=success,
                    exit_code=exit_code,
                    stdout=stdout or "",
                    stderr=stderr or "",
                    command=cmd,
                    serial=serial,
                    duration_seconds=duration,
                    message="OK" if success else (stderr or stdout).strip(),
                )

                if success:
                    logger.debug(
                        "[SUCCESS] Serial=%s Cmd=%s Duration=%.2fs",
                        serial or "-", display_cmd, duration
                    )
                    return last_result
                else:
                    logger.warning(
                        "[FAIL] Serial=%s Cmd=%s ExitCode=%d Output=%s",
                        serial or "-", display_cmd, exit_code, (stderr or stdout).strip()[:200]
                    )
                    if attempt < max_attempts:
                        time.sleep(retry_delay)
                        continue
                    return last_result

            except Exception as exc:
                duration = time.monotonic() - start_time
                last_result = OperationResult(
                    success=False,
                    exit_code=-2,
                    stdout="",
                    stderr=str(exc),
                    command=cmd,
                    serial=serial,
                    duration_seconds=duration,
                    message=f"EXCEPTION: {exc}",
                )
                logger.error("[ERROR] Serial=%s Cmd=%s Error=%s", serial or "-", display_cmd, exc)
                if attempt < max_attempts:
                    time.sleep(retry_delay)
                    continue
                return last_result

        return last_result or OperationResult(success=False, exit_code=-99, message="No attempt executed")

    def devices(self, timeout: float = 10.0) -> List[Device]:
        """
        Consulta 'adb devices -l' y devuelve la lista estructurada de objetos Device.
        """
        result = self.run_raw(["devices", "-l"], timeout=timeout)
        if not result.success:
            raise AdbError("ADB_DEVICES_FAILED", f"adb devices falló: {result.output}", result)

        device_list: List[Device] = []
        for line in result.stdout.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("List of devices"):
                continue

            parts = line_str.split()
            if len(parts) >= 2:
                serial = parts[0]
                state_str = parts[1].lower()
                state = DeviceState(state_str)

                # Extraer propiedades informativas (model:..., product:..., etc.)
                model: Optional[str] = None
                usb_serial: Optional[str] = None
                for token in parts[2:]:
                    if token.startswith("model:"):
                        model = token.split(":", 1)[1]
                    elif token.startswith("usb:"):
                        usb_serial = token.split(":", 1)[1]

                device_list.append(
                    Device(
                        serial=serial,
                        state=state,
                        model=model,
                        usb_serial=usb_serial,
                    )
                )

        return device_list

    def resolve_serial(self, serial: Optional[str] = None) -> str:
        """
        Garantiza selección explícita si hay múltiples dispositivos.
        Si se pasa serial, verifica que exista y esté listo.
        Si no se pasa serial:
          - Falla si no hay dispositivos.
          - Falla si hay >1 dispositivos listos pidiendo selección explícita.
          - Retorna el serial único si solo hay 1 dispositivo en estado 'device'.
        """
        devs = self.devices()
        ready_devs = [d for d in devs if d.is_ready]

        if serial:
            target = serial.strip()
            # Buscar en devs
            match = next((d for d in devs if d.serial == target), None)
            if not match:
                raise AdbError("DISPOSITIVO_NO_ENCONTRADO", f"El dispositivo '{target}' no figura en adb devices.")
            if not match.is_ready:
                raise AdbError("DISPOSITIVO_NO_LISTO", f"El dispositivo '{target}' no está en estado 'device' (estado: {match.state}).")
            return target

        if not ready_devs:
            raise AdbError("DISPOSITIVO_NO_SELECCIONADO", "No hay dispositivos ADB en estado 'device' conectados.")

        if len(ready_devs) > 1:
            serials_str = ", ".join(d.serial for d in ready_devs)
            raise AdbError(
                "MULTIPLE_DEVICES",
                f"Hay {len(ready_devs)} dispositivos listos ({serials_str}). Selección explícita obligatoria vía serial."
            )

        return ready_devs[0].serial

    def connect(self, endpoint: str, timeout: float = 15.0) -> ConnectionAttempt:
        """
        Conecta a un endpoint de red (IP:Puerto).
        Verifica que llegue efectivamente a estado 'device'. No guarda si falló.
        """
        ep = endpoint.strip()
        if not ep:
            raise AdbError("ENDPOINT_INVALIDO", "Endpoint vacío no permitido.")
        if ":" not in ep:
            ep = f"{ep}:5555"

        host_part, port_str = ep.rsplit(":", 1)
        port = int(port_str) if port_str.isdigit() else 5555

        result = self.run_raw(["connect", ep], timeout=timeout)
        out_text = result.output

        # Verificar respuesta
        failed_patterns = ["cannot connect", "failed to connect", "unable to connect", "connection refused", "timed out", "no route"]
        if any(pat in out_text.lower() for pat in failed_patterns):
            return ConnectionAttempt(
                endpoint=ep,
                host=host_part,
                port=port,
                mode=ConnectionMode.TCPIP if port == 5555 else ConnectionMode.WIRELESS_TLS,
                success=False,
                message=f"Conexión rechazada: {out_text}",
                exit_code=result.exit_code,
            )

        # Esperar hasta confirmar estado 'device'
        deadline = time.monotonic() + timeout
        confirmed = False
        while time.monotonic() < deadline:
            devs = self.devices(timeout=3.0)
            target = next((d for d in devs if d.serial == ep), None)
            if target and target.is_ready:
                confirmed = True
                break
            time.sleep(0.5)

        if not confirmed:
            return ConnectionAttempt(
                endpoint=ep,
                host=host_part,
                port=port,
                mode=ConnectionMode.TCPIP if port == 5555 else ConnectionMode.WIRELESS_TLS,
                success=False,
                message=f"El dispositivo {ep} no alcanzó el estado 'device' (offline o no detectado)",
                exit_code=-1,
            )

        return ConnectionAttempt(
            endpoint=ep,
            host=host_part,
            port=port,
            mode=ConnectionMode.TCPIP if port == 5555 else ConnectionMode.WIRELESS_TLS,
            success=True,
            message="Conexión establecida exitosamente",
            exit_code=0,
        )

    def disconnect(self, endpoint_or_serial: str, timeout: float = 10.0) -> OperationResult:
        """
        Desconecta un endpoint de red vía 'adb disconnect <endpoint>'.
        Falla limpiamente con USB_DISCONNECT_UNSUPPORTED si es un serial USB físico.
        """
        target = endpoint_or_serial.strip()
        if not target:
            raise AdbError("PARAMETRO_INVALIDO", "Debe proporcionar un endpoint o serial a desconectar.")

        if ":" not in target:
            raise AdbError(
                "USB_DISCONNECT_UNSUPPORTED",
                f"El dispositivo '{target}' es un dispositivo USB físico y no puede desconectarse por software."
            )

        return self.run_raw(["disconnect", target], timeout=timeout)

    def shell(
        self,
        command: str,
        serial: Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 0,
    ) -> OperationResult:
        """Ejecuta un comando en la shell del dispositivo seleccionado con serial explícito."""
        target_serial = self.resolve_serial(serial)
        return self.run_raw(["shell", command], serial=target_serial, timeout=timeout, retries=retries)

    def pull(
        self,
        remote_path: str,
        local_path: str,
        serial: Optional[str] = None,
        timeout: float = 300.0,
        retries: int = 0,
    ) -> OperationResult:
        """Descarga un archivo o directorio desde el dispositivo al host."""
        target_serial = self.resolve_serial(serial)
        return self.run_raw(["pull", remote_path, local_path], serial=target_serial, timeout=timeout, retries=retries)

    def push(
        self,
        local_path: str,
        remote_path: str,
        serial: Optional[str] = None,
        timeout: float = 300.0,
        retries: int = 0,
    ) -> OperationResult:
        """Sube un archivo o directorio local hacia el dispositivo."""
        target_serial = self.resolve_serial(serial)
        return self.run_raw(["push", local_path, remote_path], serial=target_serial, timeout=timeout, retries=retries)

    def install(
        self,
        apk_path: str,
        serial: Optional[str] = None,
        options: Optional[List[str]] = None,
        timeout: float = 180.0,
        retries: int = 0,
    ) -> OperationResult:
        """Instala un paquete APK en el dispositivo."""
        if not os.path.isfile(apk_path):
            raise AdbError("APK_NO_ENCONTRADO", f"No existe el archivo APK en '{apk_path}'.")

        target_serial = self.resolve_serial(serial)
        opts = options or ["-r"]
        args = ["install"] + opts + [apk_path]
        res = self.run_raw(args, serial=target_serial, timeout=timeout, retries=retries)
        if not res.success or "success" not in res.output.lower():
            res.success = False
            res.message = f"INSTALL_FAILED: {res.output}"
        return res

    def forward(
        self,
        local_spec: str,
        remote_spec: str,
        serial: Optional[str] = None,
        timeout: float = 15.0,
    ) -> OperationResult:
        """Configura un reenvío de puertos (forward) hacia el dispositivo."""
        target_serial = self.resolve_serial(serial)
        return self.run_raw(["forward", local_spec, remote_spec], serial=target_serial, timeout=timeout)

    def pair(self, endpoint: str, pairing_code: str, timeout: float = 30.0) -> ConnectionAttempt:
        """
        Empareja un dispositivo en Wireless Debugging vía 'adb pair <endpoint> <code_6_digits>'.
        """
        ep = endpoint.strip()
        code = pairing_code.strip()
        if not re.fullmatch(r"\d{6}", code):
            raise AdbError("CODIGO_INVALIDO", "El código de emparejamiento debe contener exactamente 6 dígitos numéricos.")

        host_part, port_str = ep.rsplit(":", 1) if ":" in ep else (ep, "0")
        port = int(port_str) if port_str.isdigit() else 0

        res = self.run_raw(["pair", ep, code], timeout=timeout)
        normalized = res.output.lower()
        success = ("successfully paired" in normalized or "pairing successful" in normalized or "emparejamiento exitoso" in normalized)

        return ConnectionAttempt(
            endpoint=ep,
            host=host_part,
            port=port,
            mode=ConnectionMode.WIRELESS_TLS,
            success=success,
            message=res.output.strip(),
            exit_code=res.exit_code if success else (res.exit_code if res.exit_code != 0 else -1),
        )

    def get_property(self, property_name: str, serial: Optional[str] = None, timeout: float = 10.0) -> str:
        """Consulta una propiedad del sistema (getprop <prop>) y retorna el valor en texto limpio."""
        target_serial = self.resolve_serial(serial)
        res = self.run_raw(["shell", "getprop", property_name], serial=target_serial, timeout=timeout)
        if not res.success:
            raise AdbError("GETPROP_FAILED", f"No se pudo consultar propiedad '{property_name}': {res.output}", res)
        return res.stdout.strip()
