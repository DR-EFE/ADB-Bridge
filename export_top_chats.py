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
from ui_automation.whatsapp.processor import export_processed_chat, process_chat_file


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
    parser.add_argument("--process", action="store_true", help="Procesar los chats descargados y generar análisis local")
    parser.add_argument("--format", choices=("json", "csv", "xlsx", "all"), default="all", help="Formato analítico al usar --process")
    parser.add_argument("--report", action="store_true", help="Generar también el reporte Markdown")
    parser.add_argument("--my-name", default=None, help="Nombre propio para identificar mensajes enviados en grupos")
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
        if args.process:
            for result in results:
                local_path = result.get("local_path")
                if not result.get("success") or not local_path:
                    continue
                try:
                    conversation = process_chat_file(local_path, my_name=args.my_name, chat_name=result.get("chat_name"))
                    generated = export_processed_chat(
                        conversation, args.output_dir, formats=(args.format,), report=args.report
                    )
                    print(f"[ANÁLISIS] {conversation.chat_name}: " + ", ".join(generated.values()))
                except Exception as exc:
                    result["processing_error"] = str(exc)
                    logging.error("No se pudo procesar '%s': %s", result.get("chat_name"), exc)
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
