#!/usr/bin/env python3
"""
Ejemplo completo de flujo de exportación de chat de WhatsApp:
1. Conectar con el dispositivo (DeviceManager con ensure_uiautomator_ready).
2. Abrir WhatsApp y verificar pantalla de chats.
3. Exportar el chat nativamente.
4. Descargar el archivo generado (.txt o .zip) al PC.
5. Limpiar y eliminar el archivo del teléfono.

Uso:
    python export_chat_example.py --chat "NombreContactoOGrupo"
    python export_chat_example.py --chat "NombreContactoOGrupo" --include-media
"""

import argparse
import logging
import sys
from pathlib import Path

from ui_automation.device import DeviceManager
from ui_automation.whatsapp import WhatsAppClient, ChatExportError


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def run_export_flow(
    chat_name: str,
    include_media: bool = False,
    serial: str = None,
    output_dir: str = "./exports",
) -> bool:
    print("============================================================")
    print("       EXPORTACION AUTOMATIZADA DE CHAT DE WHATSAPP         ")
    print("============================================================")
    print(f"Chat objetivo : {chat_name}")
    print(f"Multimedia    : {'Sí (.zip)' if include_media else 'No (.txt)'}")
    print(f"Destino local : {Path(output_dir).resolve()}\n")

    # 1. Conexión con el dispositivo (gestiona uiautomator y WiFi automáticamente)
    print("[1/5] Conectando con el dispositivo Android...")
    dev_mgr = DeviceManager(serial=serial)
    client = WhatsAppClient(dev_mgr, auto_watchers=True)

    try:
        # 2. Abrir WhatsApp y asegurar estar en la lista de chats
        print("[2/5] Abriendo WhatsApp...")
        if not client.open_whatsapp(timeout=15.0):
            print("ERROR: No se pudo abrir WhatsApp en la pantalla de chats.")
            return False

        # 3. Exportar el chat mediante el menú nativo
        print(f"[3/5] Exportando chat '{chat_name}'...")
        remote_path = client.export_chat(
            chat_name=chat_name,
            include_media=include_media,
            timeout=35.0,
        )

        if not remote_path:
            # Si no devolvió ruta directa, intentar búsqueda por fecha reciente
            print("      Buscando archivo exportado recientemente en el dispositivo...")
            remote_path = client.find_exported_chat_file(chat_name=chat_name, prefer_zip=include_media)

        if not remote_path:
            print("ERROR: No se localizó el archivo exportado en el dispositivo.")
            return False

        print(f"      Archivo generado en teléfono: {remote_path}")

        # 4. Descargar el archivo al PC vía ADB
        print(f"[4/5] Descargando archivo a la computadora ({output_dir})...")
        local_file = client.pull_exported_chat(remote_path, local_dir=output_dir)
        print(f"      Descarga completada: {local_file} ({local_file.stat().st_size} bytes)")

        # 5. Limpieza del archivo en el teléfono
        print(f"[5/5] Eliminando archivo temporal en el teléfono ({remote_path})...")
        client.delete_exported_file(remote_path)
        print("      Limpieza remota finalizada.")

        # Volver a la lista de chats para dejar la app limpia
        client.go_back_to_chat_list()

        print("\n============================================================")
        print(" [EXITO] Proceso de exportación y descarga completado.")
        print(f" Archivo listo en: {local_file.resolve()}")
        print("============================================================")
        return True

    except ChatExportError as exc:
        print(f"\n[ERROR DE EXPORTACION]: {exc}")
        return False
    except Exception as exc:
        print(f"\n[ERROR INESPERADO]: {exc}")
        return False
    finally:
        client.close()


def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="Exportar chat de WhatsApp vía UI Automation")
    parser.add_argument("--chat", type=str, required=True, help="Nombre del contacto o grupo a exportar")
    parser.add_argument("--include-media", action="store_true", help="Incluir fotos y multimedia (.zip)")
    parser.add_argument("--serial", type=str, default=None, help="Serial ADB o IP:puerto")
    parser.add_argument("--output-dir", type=str, default="./exports", help="Carpeta de destino local")

    args = parser.parse_args()
    success = run_export_flow(
        chat_name=args.chat,
        include_media=args.include_media,
        serial=args.serial,
        output_dir=args.output_dir,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
