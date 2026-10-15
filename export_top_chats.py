#!/usr/bin/env python3
"""
Punto de entrada CLI para exportar los primeros N chats más recientes de WhatsApp.

Uso:
    python export_top_chats.py --limit 5
    python export_top_chats.py -n 10 --include-media
    python export_top_chats.py --serial 192.168.1.50:5555 --limit 3 --output-dir ./mis_chats
"""

import argparse
import logging
import sys
from pathlib import Path

from ui_automation.device import DeviceManager
from ui_automation.whatsapp import WhatsAppClient


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main():
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Exportar los primeros N chats más recientes de WhatsApp"
    )
    parser.add_argument(
        "-n", "--limit",
        type=int,
        default=10,
        help="Número de chats más recientes a exportar (predeterminado: 10)",
    )
    parser.add_argument(
        "--include-media",
        action="store_true",
        help="Incluir fotos y archivos multimedia (.zip en lugar de .txt)",
    )
    parser.add_argument(
        "--serial",
        type=str,
        default=None,
        help="Serial ADB o IP:puerto del dispositivo. Por defecto autodetecta.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./exports",
        help="Carpeta local de destino para las descargas (predeterminado: ./exports)",
    )

    args = parser.parse_args()

    dev_mgr = DeviceManager(serial=args.serial)
    client = WhatsAppClient(dev_mgr, auto_watchers=True)

    try:
        results = client.export_top_chats(
            n=args.limit,
            include_media=args.include_media,
            local_dir=args.output_dir,
        )
        success_count = sum(1 for r in results if r.get("success"))
        sys.exit(0 if success_count > 0 else 1)
    except KeyboardInterrupt:
        print("\n[AVISO] Proceso interrumpido por el usuario.")
        sys.exit(130)
    except Exception as exc:
        logging.critical("Fallo inesperado: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    main()
