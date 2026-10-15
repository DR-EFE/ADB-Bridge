"""Módulo de gestión de conexión con dispositivos Android usando uiautomator2."""

import logging
from typing import Optional, Any
import uiautomator2 as u2
import adbutils

logger = logging.getLogger("ui_automation.device")


class DeviceConnectionError(Exception):
    """Excepción personalizada para fallos de conexión con el dispositivo."""
    pass


class DeviceManager:
    """Administrador de ciclo de vida y conexión para uiautomator2."""

    def __init__(self, serial: Optional[str] = None):
        """
        Inicializa el DeviceManager.

        :param serial: Serial del dispositivo (ej: 'emulator-5554' o '192.168.1.50:5555').
                       Si es None, uiautomator2 intentará conectarse al único dispositivo disponible.
        """
        self.serial: Optional[str] = serial
        self._device: Optional[u2.Device] = None

    def connect(self) -> u2.Device:
        """
        Establece la conexión con el dispositivo Android vía uiautomator2.
        
        :return: Objeto uiautomator2.Device conectado.
        :raises DeviceConnectionError: Si no se puede establecer la conexión.
        """
        try:
            logger.info("Iniciando conexión con dispositivo (serial=%s)...", self.serial or "auto-detect")
            
            # Verificación preliminar de dispositivos vía adbutils
            connected_devices = adbutils.adb.device_list()
            if not connected_devices and not self.serial:
                raise DeviceConnectionError(
                    "No se detectaron dispositivos Android conectados por USB ni Wi-Fi. "
                    "Verifica que el teléfono tenga 'Depuración USB' activa o ejecuta 'adb connect <IP>:<PORT>'."
                )

            if self.serial:
                # Conexión a serial o IP:PORT específico
                self._device = u2.connect(self.serial)
            else:
                # Conexión al único dispositivo detectado
                self._device = u2.connect()

            # Prueba de comunicación rápida para verificar que el daemon uiautomator responde
            info = self._device.info
            actual_serial = self._device.serial
            model = info.get("productName", "Desconocido")
            logger.info("Dispositivo conectado exitosamente: %s (Modelo: %s)", actual_serial, model)
            return self._device

        except DeviceConnectionError:
            raise
        except Exception as exc:
            error_msg = (
                f"Error crítico al conectar con el dispositivo Android ({self.serial or 'auto'}): {exc}\n"
                "Sugerencias:\n"
                " 1. Comprueba que el teléfono esté encendido y desbloqueado.\n"
                " 2. Acepta el diálogo de autorización 'Permitir depuración USB'.\n"
                " 3. Ejecuta 'adb devices' para validar el estado (debe figurar como 'device', no 'unauthorized')."
            )
            logger.error(error_msg)
            raise DeviceConnectionError(error_msg) from exc

    def get_device(self) -> u2.Device:
        """
        Obtiene la instancia activa de uiautomator2.Device.
        Si aún no está conectada, ejecuta connect().

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
