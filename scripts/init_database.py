"""Inicializa la base SQLite local de Android Tools."""

from __future__ import annotations

import argparse
from pathlib import Path

from ui.services.database import SQLiteStore, get_default_database_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicializa Android Tools SQLite")
    parser.add_argument("--path", type=Path, default=None, help="Ruta de android_admin.db")
    args = parser.parse_args()
    store = SQLiteStore(args.path or get_default_database_path())
    print(f"Base SQLite lista: {store.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
