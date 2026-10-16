"""Módulo de gestión y estabilización de ADB (Android Debug Bridge).

Garantiza:
1. Un único daemon ADB activo y controlado (eliminando colisiones entre procesos).
2. Cliente singleton de `adbutils.AdbClient` para todo el proyecto.
3. Watchdog ligero en segundo plano que monitorea la conexión del dispositivo y
   reconecta automáticamente conexiones Wi-Fi caídas SIN reiniciar el daemon.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from typing import Dict, List, Optional

import adbutils

logger = logging.getLogger("ui_automation.adb_manager")

# Instancia singleton del cliente ADB
_adb_client: Optional[adbutils.AdbClient] = None
_client_lock = threading.Lock()

# Control de hilos watchdog activos por serial
_watchdog_lock = threading.Lock()
_active_watchdogs: Dict[str, threading.Event] = {}


def get_adb_executable() -> str:
    """
    Obtiene la ruta canónica al ejecutable adb.
    Prioriza el binario resuelto por adbutils para mantener consistencia de versión
    de protocolo del servidor (puerto 5037).
    """
    try:
        path = adbutils.adb_path()
        if path and os.path.isfile(path):
            return path
    except Exception as exc:
        logger.debug("No se pudo obtener adb_path desde adbutils (%s). Usando 'adb' estándar.", exc)

    return "adb"


def ensure_single_adb_server(host: str = "127.0.0.1", port: int = 5037, force: bool = False) -> adbutils.AdbClient:
    """
    Garantiza que exista un único servidor/daemon ADB limpio y controlado.
    
    Si `force` es False y el daemon actual ya responde correctamente, se reutiliza
    para no interrumpir conexiones USB o Wi-Fi activas.
    Si `force` es True o el daemon no responde:
    1. Ejecuta `adb kill-server` para terminar daemons previos o huérfanos.
    2. Espera la liberación del puerto.
    3. Ejecuta `adb start-server` para arrancar un daemon limpio.
    4. Inicializa y devuelve la instancia singleton de `AdbClient`.

    :param host: Dirección del host del servidor ADB (default: 127.0.0.1).
    :param port: Puerto TCP del servidor ADB (default: 5037).
    :param force: Si es True, fuerza reinicio del servidor matando cualquier proceso previo.
    :return: Cliente adbutils.AdbClient listo para operar.
    """
    global _adb_client

    if not force:
        client = get_adb_client(host=host, port=port)
        try:
            client.server_version()
            logger.debug("Daemon ADB activo y respondiendo (puerto %d). No requiere reinicio.", port)
            return client
        except Exception:
            logger.info("Daemon ADB no responde o no está iniciado. Se procederá a arrancarlo...")

    adb_bin = get_adb_executable()
    logger.info("Deteniendo daemons ADB previos (kill-server) usando %s...", adb_bin)

    cmd_kwargs = {"capture_output": True, "text": True, "timeout": 15, "check": False}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        cmd_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[assignment]

    # 1. Matar servidores previos
    try:
        subprocess.run([adb_bin, "kill-server"], **cmd_kwargs)
        logger.info("Comando kill-server ejecutado exitosamente.")
    except Exception as exc:
        logger.warning("Aviso durante la ejecución de kill-server: %s", exc)

    # Breve pausa para asegurar liberación de sockets en el OS
    time.sleep(1.0)

    # 2. Iniciar servidor limpio
    logger.info("Iniciando daemon ADB único y limpio (start-server)...")
    try:
        res = subprocess.run([adb_bin, "start-server"], **cmd_kwargs)
        if res.returncode == 0:
            logger.info("Daemon ADB iniciado correctamente.")
        else:
            logger.warning("Aviso en start-server (código %d): %s", res.returncode, res.stderr.strip() or res.stdout.strip())
    except Exception as exc:
        logger.error("Error crítico al ejecutar start-server: %s", exc)

    time.sleep(0.5)

    # 3. Reestablecer cliente singleton
    with _client_lock:
        _adb_client = adbutils.AdbClient(host=host, port=port)

    return _adb_client


def get_adb_client(host: str = "127.0.0.1", port: int = 5037) -> adbutils.AdbClient:
    """
    Devuelve la instancia singleton de `AdbClient` para que todo el proyecto
    reutilice el mismo canal de comunicación con el daemon ADB.

    :param host: Dirección del host del servidor ADB.
    :param port: Puerto TCP del servidor ADB.
    :return: Instancia compartida de AdbClient.
    """
    global _adb_client
    if _adb_client is None:
        with _client_lock:
            if _adb_client is None:
                _adb_client = adbutils.AdbClient(host=host, port=port)
    return _adb_client


def is_network_serial(serial: str) -> bool:
    """
    Determina si un serial corresponde a una conexión TCP/IP (Wi-Fi).
    Comprueba si contiene ':' (ej: '192.168.1.50:5555') o formato IP.
    """
    if not serial:
        return False
    return ":" in serial or (serial.count(".") == 3 and not serial.isalpha())


def _discover_mdns_endpoints() -> List[str]:
    """Devuelve endpoints adb-tls-connect anunciados por Wireless Debugging."""
    adb_bin = get_adb_executable()
    try:
        result = subprocess.run(
            [adb_bin, "mdns", "services"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return []
    endpoints: List[str] = []
    output = "\n".join(filter(None, (result.stdout or "", result.stderr or "")))
    for line in output.splitlines():
        if "adb-tls-connect" not in line:
            continue
        match = re.search(r"(\[[^\]]+\]:\d{1,5}|[A-Za-z0-9._-]+:\d{1,5})", line)
        if match and match.group(1) not in endpoints:
            endpoints.append(match.group(1))
    return endpoints


def _watchdog_loop(serial: str, interval: int, stop_event: threading.Event) -> None:
    """
    Bucle en segundo plano para verificar periódicamente la salud de la conexión
    y reintentar reconexión TCP/IP sin reiniciar el daemon ADB.
    """
    logger.info("Watchdog de conexión ADB activado para %s (chequeo cada %ds).", serial, interval)
    is_network = is_network_serial(serial)

    while not stop_event.is_set():
        if stop_event.wait(interval):
            break

        try:
            client = get_adb_client()
            all_devices = client.list()
            matched_info = next((d for d in all_devices if d.serial == serial), None)

            current_state = "disconnected"
            if matched_info is not None:
                current_state = matched_info.state

            if current_state != "device":
                logger.warning(
                    "Watchdog ADB: Dispositivo %s en estado anormal '%s'.",
                    serial,
                    current_state,
                )

                if is_network:
                    logger.info("Watchdog ADB: Intentando reconexión Wi-Fi a %s (sin reiniciar daemon)...", serial)
                    try:
                        res = client.connect(serial, timeout=10.0)
                        if re.search(r"failed|cannot|refused|timed out|no route", res or "", re.IGNORECASE):
                            raise RuntimeError(res.strip() or "adb connect falló")
                        logger.info("Watchdog ADB: Reconexión para %s -> %s", serial, res.strip())
                    except Exception as conn_exc:
                        # Wireless Debugging moderno publica un puerto nuevo
                        # cuando cambia la red. Probar mDNS evita pedir USB otra vez.
                        reconnected = False
                        for endpoint in _discover_mdns_endpoints():
                            if endpoint == serial:
                                continue
                            try:
                                mdns_res = client.connect(endpoint, timeout=8.0)
                                mdns_info = next(
                                    (item for item in client.list() if item.serial == endpoint and item.state == "device"),
                                    None,
                                )
                                if mdns_info is not None:
                                    serial = endpoint
                                    reconnected = True
                                    logger.info("Watchdog ADB: ReconexiÃ³n mDNS exitosa a %s -> %s", endpoint, mdns_res.strip())
                                    break
                            except Exception as mdns_exc:
                                logger.debug("Watchdog ADB: endpoint mDNS %s no disponible: %s", endpoint, mdns_exc)
                        if not reconnected:
                            logger.warning("Watchdog ADB: Falló el intento de reconexión a %s: %s", serial, conn_exc)
                else:
                    if current_state == "unauthorized":
                        logger.warning("Watchdog ADB: Dispositivo USB %s requiere autorización en pantalla.", serial)
                    else:
                        logger.warning("Watchdog ADB: Dispositivo USB %s desconectado o no disponible.", serial)

        except Exception as loop_exc:
            logger.debug("Aviso en ciclo de verificación de watchdog (%s): %s", serial, loop_exc)

    logger.info("Watchdog de conexión ADB finalizado para %s.", serial)


def start_reconnect_watchdog(serial: str, interval: int = 20) -> threading.Event:
    """
    Inicia un watchdog ligero en un hilo daemon para monitorear el estado de conexión del dispositivo.
    Si el dispositivo pasa a offline o se desconecta, intenta reconectarlo con `adb connect <serial>`
    SIN reiniciar el daemon ADB.

    :param serial: Serial del dispositivo (ej: '192.168.1.50:5555' o USB serial).
    :param interval: Intervalo en segundos entre cada comprobación (default: 20s).
    :return: Evento de parada (threading.Event) asociado al watchdog.
    """
    if not serial:
        dummy = threading.Event()
        dummy.set()
        return dummy

    with _watchdog_lock:
        existing = _active_watchdogs.get(serial)
        if existing and not existing.is_set():
            logger.debug("El watchdog de reconexión para %s ya está en ejecución.", serial)
            return existing

        stop_event = threading.Event()
        _active_watchdogs[serial] = stop_event

        thread = threading.Thread(
            target=_watchdog_loop,
            args=(serial, interval, stop_event),
            daemon=True,
            name=f"AdbWatchdog-{serial}",
        )
        thread.start()
        return stop_event


def stop_reconnect_watchdog(serial: Optional[str] = None) -> None:
    """
    Detiene el watchdog de reconexión.
    
    :param serial: Serial específico a detener. Si es None, detiene todos los watchdogs activos.
    """
    with _watchdog_lock:
        if serial:
            stop_event = _active_watchdogs.pop(serial, None)
            if stop_event:
                stop_event.set()
                logger.info("Señal de detención enviada al watchdog de %s.", serial)
        else:
            for s, stop_event in list(_active_watchdogs.items()):
                stop_event.set()
                logger.info("Señal de detención enviada al watchdog de %s.", s)
            _active_watchdogs.clear()


def is_watchdog_active(serial: str) -> bool:
    """
    Comprueba si existe un watchdog activo corriendo para el serial dado.
    """
    with _watchdog_lock:
        event = _active_watchdogs.get(serial)
        return event is not None and not event.is_set()
