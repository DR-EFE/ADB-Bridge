#!/usr/bin/env python3
"""
Script de verificación y prueba de apertura para WhatsAppClient.
Uso:
    python test_whatsapp_open.py
    python test_whatsapp_open.py --serial 192.168.1.50:5555
"""

import argparse
import logging
import sys

from ui_automation.device import DeviceManager
from ui_automation.whatsapp import WhatsAppClient


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def test_whatsapp_connection(serial: str = None) -> bool:
    """Verifica la conexión y apertura de WhatsApp."""
    print("============================================================")
    print("       PRUEBA DE APERTURA Y NAVEGACION DE WHATSAPP         ")
    print("============================================================")

    dev_mgr = DeviceManager(serial=serial)
    try:
        print("[1/3] Conectando con el dispositivo...")
        d = dev_mgr.get_device()
        print(f"      Conectado a: {d.serial}")

        print("[2/3] Inicializando WhatsAppClient...")
        client = WhatsAppClient(dev_mgr)

        pkg = client.detect_installed_package()
        if not pkg:
            print("      [AVISO] No se encontró WhatsApp estándar ni WhatsApp Business instalado.")
            return False
        print(f"      Paquete objetivo detectado: {pkg}")

        print("[3/3] Abriendo WhatsApp y esperando pantalla de chats...")
        opened = client.open_whatsapp(timeout=15.0)

        if opened:
            print("      [EXITO] Se abrió WhatsApp y se confirmó la lista principal de chats.")
        else:
            print("      [FALLO] No se logró confirmar la lista principal de chats.")

        client.close()
        return opened

    except Exception as exc:
        print(f"      [ERROR] Falló la verificación: {exc}")
        return False


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Prueba de apertura de WhatsApp")
    parser.add_argument("--serial", type=str, default=None, help="Serial ADB o IP:puerto")
    args = parser.parse_args()

    success = test_whatsapp_connection(serial=args.serial)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
