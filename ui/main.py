"""Punto de entrada de la UI.

Uso (desde la raíz del proyecto)::

    python -m ui.main
    python -m ui.main --serial 192.168.1.50:37123
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional

import flet as ft

from .app import AndroidToolsApp

_app: Optional[AndroidToolsApp] = None


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Android Tools (Flet)")
    parser.add_argument("--serial", default=None, help="Serial ADB o IP:puerto. Por defecto: devices.json / autodetección.")
    args = parser.parse_args()
    _setup_logging()

    def _page_main(page: ft.Page) -> None:
        global _app
        _app = AndroidToolsApp(page, serial=args.serial)
        _app.start()

    try:
        ft.run(_page_main)
    finally:
        if _app is not None:
            _app.shutdown()


if __name__ == "__main__":
    main()
