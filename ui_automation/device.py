"""Módulo de gestión de conexión con dispositivos Android usando uiautomator2."""

from __future__ import annotations

import logging
import time
from typing import Optional, Union
import adbutils
import uiautomator2 as u2

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
        # Obtener dispositivo con adbutils para comprobar conexión física o de red
        if serial:
            adb_dev = adbutils.adb.device(serial=serial)
        else:
            devices = adbutils.adb.device_list()
            if not devices:
                raise DeviceConnectionError(
                    "No se detectaron dispositivos ADB conectados. "
                    "Conecta primero el dispositivo por Wireless Debugging o USB."
                )
            adb_dev = devices[0]

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

    def connect(self) -> u2.Device:
        """
        Establece la conexión con el dispositivo Android vía uiautomator2 asegurando
        que esté inicializado previamente.
        
        :return: Objeto uiautomator2.Device conectado.
        :raises DeviceConnectionError: Si no se puede establecer la conexión.
        """
        try:
            logger.info("Iniciando conexión con dispositivo (serial=%s)...", self.serial or "auto-detect")
            
            # 1. Asegurar uiautomator2 listo (USB o inalámbrico)
            ensure_uiautomator_ready(serial=self.serial)

            # 2. Conectar instancia
            if self.serial:
                self._device = u2.connect(self.serial)
            else:
                self._device = u2.connect()

            actual_serial = self._device.serial
            model = self._device.info.get("productName", "Desconocido")
            logger.info("Dispositivo conectado exitosamente: %s (Modelo: %s)", actual_serial, model)
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
        """Cierra la referencia actual al dispositivo."""
        self._device = None
        logger.info("Conexión con el dispositivo liberada.")
