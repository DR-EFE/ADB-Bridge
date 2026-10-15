#!/usr/bin/env python3
"""
Punto de entrada principal para el monitoreo de notificaciones y automatización de UI.
Uso:
    python monitor_notificaciones.py
    python monitor_notificaciones.py --serial 192.168.1.50:5555 --interval 2.0
"""

import argparse
import logging
import sys
from pathlib import Path

from ui_automation.monitor import NotificationMonitor


def setup_logging():
    """Configura formato de logs en consola."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_arguments():
    """Parsea argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Monitor de Notificaciones y Automatización de UI en Android"
    )
    parser.add_argument(
        "--serial",
        type=str,
        default=None,
        help="Serial del dispositivo USB o IP:PORT (ej: 192.168.1.100:5555). Por defecto autodetecta.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=3.0,
        help="Intervalo de sondeo en segundos (predeterminado: 3.0).",
    )
    parser.add_argument(
        "--logs-dir",
        type=str,
        default="logs",
        help="Directorio donde guardar los archivos JSON con notificaciones (predeterminado: logs).",
    )
    parser.add_argument(
        "--no-watchers",
        action="store_true",
        help="Desactiva los observadores automáticos de popups/permisos.",
    )
    return parser.parse_args()


def main():
    setup_logging()
    args = parse_arguments()

    print("============================================================")
    print("      ANDROID UI AUTOMATION - MONITOR DE NOTIFICACIONES    ")
    print("============================================================")
    print(f"Dispositivo  : {args.serial or 'Autodetección'}")
    print(f"Intervalo    : {args.interval}s")
    print(f"Carpeta logs : {Path(args.logs_dir).resolve()}")
    print(f"Watchers     : {'Desactivados' if args.no_watchers else 'Activos'}")
    print("Presiona Ctrl+C para finalizar.\n")

    monitor = NotificationMonitor(
        serial=args.serial,
        logs_dir=args.logs_dir,
        poll_interval=args.interval,
        enable_watchers=not args.no_watchers,
    )

    try:
        monitor.start()
    except Exception as exc:
        logging.critical("Error fatal en el monitor: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
