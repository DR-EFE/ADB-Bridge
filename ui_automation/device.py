"""Módulo de gestión de conexión con dispositivos Android usando uiautomator2."""

from __future__ import annotations

import logging
import time
from typing import Optional, Union

import adbutils
import uiautomator2 as u2

from .adb_manager import (
    ensure_single_adb_server,
    get_adb_client,
    is_network_serial,
    start_reconnect_watchdog,
    stop_reconnect_watchdog,
)

logger = logging.getLogger("ui_automation.device")


class DeviceConnectionError(Exception):
    """Excepción personalizada para fallos de conexión con el dispositivo."""
    pass


def ensure_uiautomator_ready(serial: Optional[str] = None, timeout: float = 20.0) -> bool:
    """
    Verifica si uiautomator2 está listo en el dispositivo especificado (por USB o WiFi).
    Si NO está listo o faltan dependencias internas (u2.jar/servicios), ejecuta
    automáticamente la inicialización respetando la conexión inalámbrica sin requerir USB.

    :param serial: Serial del dispositivo (ej: '192.168.1.50:5555' o USB serial).
                   Si es None, usa el dispositivo activo disponible.
    :param timeout: Tiempo máximo en segundos para confirmar disponibilidad.
    :return: True si el servicio quedó disponible y respondiendo.
    :raises DeviceConnectionError: Si no se puede comunicar o inicializar el dispositivo.
    """
    target_str = serial or "auto-detect"
    logger.info("Comprobando disponibilidad de uiautomator2 en %s...", target_str)

    try:
        # Usar el cliente gestionado por adb_manager para evitar clientes sueltos
        client = get_adb_client()
        all_info = client.list()

        if serial:
            dev_info = next((d for d in all_info if d.serial == serial), None)
            if dev_info and dev_info.state == "unauthorized":
                raise DeviceConnectionError(
                    f"El dispositivo {serial} está conectado pero en estado NO AUTORIZADO (UNAUTHORIZED).\n"
                    "Desbloquea la pantalla de tu celular y pulsa 'Permitir siempre la depuración USB'."
                )

            # Si es serial de red y aún no aparece en device_list, intentar conectar antes de verificar
            if is_network_serial(serial):
                if not dev_info or dev_info.state != "device":
                    logger.info("Conectando cliente ADB al dispositivo de red %s...", serial)
                    try:
                        client.connect(serial, timeout=10.0)
                    except Exception as conn_err:
                        logger.warning("Aviso al intentar conectar ADB a %s: %s", serial, conn_err)

            adb_dev = client.device(serial=serial)
        else:
            if not all_info:
                raise DeviceConnectionError(
                    "No se detectaron dispositivos ADB conectados. "
                    "Conecta primero el dispositivo por Wireless Debugging o USB."
                )

            unauth = next((d for d in all_info if d.state == "unauthorized"), None)
            ready_devices = client.device_list()
            if not ready_devices:
                if unauth:
                    raise DeviceConnectionError(
                        f"Dispositivo detectado ({unauth.serial}) pero en estado NO AUTORIZADO (UNAUTHORIZED).\n"
                        "Desbloquea la pantalla de tu celular y pulsa 'Permitir siempre la depuración USB'."
                    )
                raise DeviceConnectionError(
                    f"Dispositivo detectado ({all_info[0].serial}) en estado '{all_info[0].state}'. "
                    "Verifica que la pantalla esté encendida y desbloqueada."
                )
            adb_dev = ready_devices[0]

        actual_serial = adb_dev.serial

        # Comprobar si ya responde uiautomator2 rápidamente
        d = u2.connect(actual_serial)
        try:
            # Comprobación ligera
            _ = d.info
            logger.info("uiautomator2 ya está listo y respondiendo en %s.", actual_serial)
            return True
        except Exception:
            logger.info("uiautomator2 no responde de inmediato en %s. Inicializando automáticamente...", actual_serial)

        # Inicialización automática no invasiva: instala assets/u2.jar a través del canal ADB (funciona 100% sobre WiFi)
        try:
            if hasattr(d, "_setup_jar"):
                d._setup_jar()
            if hasattr(d, "_setup_ime"):
                d._setup_ime()
        except Exception as init_exc:
            logger.warning("Aviso durante la configuración preliminar de uiautomator2: %s", init_exc)

        # Esperar a que el servicio responda
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if d.info:
                    logger.info("uiautomator2 inicializado exitosamente en %s.", actual_serial)
                    return True
            except Exception:
                time.sleep(1.0)

        # Si tras el tiempo límite no responde, lanzar error claro
        raise DeviceConnectionError(
            f"El dispositivo {actual_serial} está conectado a ADB pero uiautomator2 no logró responder "
            f"tras {timeout} segundos. Verifica que la pantalla esté encendida y no haya bloqueos de seguridad."
        )

    except DeviceConnectionError:
        raise
    except Exception as exc:
        msg = f"Error al verificar/inicializar uiautomator2 en {target_str}: {exc}"
        logger.error(msg)
        raise DeviceConnectionError(msg) from exc


class DeviceManager:
    """Administrador de ciclo de vida y conexión para uiautomator2."""

    def __init__(self, serial: Optional[str] = None):
        """
        Inicializa el DeviceManager.

        :param serial: Serial del dispositivo (ej: 'emulator-5554' o '192.168.1.50:5555').
                       Si es None, uiautomator2 intentará conectarse al dispositivo disponible.
        """
        self.serial: Optional[str] = serial
        self._device: Optional[u2.Device] = None
        self._watchdog_event: Optional[object] = None

    def connect(self) -> u2.Device:
        """
        Establece la conexión con el dispositivo Android vía uiautomator2 asegurando:
        1. Un único daemon ADB activo, limpio y controlado.
        2. Enlace inicial de red si se trata de un serial Wi-Fi.
        3. Inicialización garantizada de uiautomator2.
        4. Activación de watchdog de reconexión automática en segundo plano.
        
        :return: Objeto uiautomator2.Device conectado.
        :raises DeviceConnectionError: Si no se puede establecer la conexión.
        """
        try:
            logger.info("Iniciando conexión con dispositivo (serial=%s)...", self.serial or "auto-detect")
            
            # 1. Asegurar único daemon ADB activo y controlado
            client = ensure_single_adb_server()

            # Si es conexión TCP/IP (Wi-Fi), asegurar enlace inicial en el daemon limpio
            if self.serial and is_network_serial(self.serial):
                logger.info("Estableciendo enlace ADB Wi-Fi con %s...", self.serial)
                try:
                    conn_res = client.connect(self.serial, timeout=10.0)
                    logger.info("Resultado de conexión ADB Wi-Fi inicial: %s", conn_res.strip())
                except Exception as net_exc:
                    logger.warning("Aviso en conexión ADB inicial a %s: %s", self.serial, net_exc)

            # 2. Asegurar uiautomator2 listo (USB o inalámbrico)
            ensure_uiautomator_ready(serial=self.serial)

            # 3. Conectar instancia uiautomator2
            if self.serial:
                self._device = u2.connect(self.serial)
            else:
                self._device = u2.connect()

            actual_serial = self._device.serial
            model = self._device.info.get("productName", "Desconocido")
            logger.info("Dispositivo conectado exitosamente: %s (Modelo: %s)", actual_serial, model)

            # 4. Iniciar watchdog de reconexión automática en segundo plano
            self._watchdog_event = start_reconnect_watchdog(actual_serial)

            return self._device

        except DeviceConnectionError:
            raise
        except Exception as exc:
            error_msg = (
                f"Error crítico al conectar con el dispositivo Android ({self.serial or 'auto'}): {exc}\n"
                "Sugerencias:\n"
                " 1. Comprueba que el teléfono esté conectado por Wi-Fi o USB y en estado 'device'.\n"
                " 2. Acepta el diálogo de autorización 'Permitir depuración USB/Inalámbrica' en la pantalla.\n"
                " 3. Ejecuta 'adb devices' para validar el estado."
            )
            logger.error(error_msg)
            raise DeviceConnectionError(error_msg) from exc

    def get_device(self) -> u2.Device:
        """
        Obtiene la instancia activa de uiautomator2.Device asegurando que esté lista.
        Si aún no está conectada, ejecuta connect() que llama a ensure_uiautomator_ready().

        :return: Instancia lista para automatización.
        """
        if self._device is None:
            return self.connect()
        return self._device

    def is_alive(self) -> bool:
        """Verifica si la sesión con el dispositivo sigue respondiendo."""
        if self._device is None:
            return False
        try:
            return bool(self._device.info)
        except Exception:
            return False

    def disconnect(self) -> None:
        """Cierra la referencia actual al dispositivo y detiene el watchdog de reconexión."""
        target_serial = None
        if self._device is not None:
            try:
                target_serial = self._device.serial
            except Exception:
                pass
        target_serial = target_serial or self.serial

        if target_serial:
            stop_reconnect_watchdog(target_serial)
        else:
            stop_reconnect_watchdog()

        self._watchdog_event = None
        self._device = None
        logger.info("Conexión con el dispositivo liberada y watchdog detenido.")

