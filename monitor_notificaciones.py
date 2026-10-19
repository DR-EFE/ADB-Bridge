#!/usr/bin/env python3
"""CLI para iniciar el monitor de notificaciones Android desde la interfaz."""

import argparse
import logging
import sys

from ui_automation.monitor import NotificationMonitor


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitorea notificaciones Android mediante ADB")
    parser.add_argument("--serial", default=None, help="Serial ADB o IP:puerto del dispositivo")
    parser.add_argument("--interval", type=float, default=3.0, help="Intervalo de lectura en segundos")
    parser.add_argument("--no-ai", action="store_true", help="Usar solo reglas, sin Ollama")
    parser.add_argument("--no-watchers", action="store_true", help="No activar cerradores de popups")
    parser.add_argument("--no-device-copy", action="store_true", help="No copiar alertas importantes al teléfono")
    parser.add_argument("--device-copy-path", default=None, help="Ruta de copia de alertas en Android")
    parser.add_argument("--logs-dir", default="notifications", help="Carpeta local para los registros JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    monitor = NotificationMonitor(
        serial=args.serial,
        logs_dir=args.logs_dir,
        poll_interval=max(0.5, args.interval),
        enable_watchers=not args.no_watchers,
        use_ai=not args.no_ai,
        device_copy_path=None if args.no_device_copy else (
            args.device_copy_path or "/sdcard/Download/notificaciones_otp.json"
        ),
    )
    monitor.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
