"""Procesamiento local de exportaciones de chats de WhatsApp.

El módulo no necesita un teléfono conectado: acepta el ``.txt`` producido por
WhatsApp o un ``.zip`` que incluya el chat y sus adjuntos.  Las operaciones que
recorren muchos mensajes aceptan una señal de cancelación y un callback de
progreso para poder usarse desde la CLI y la interfaz.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Callable, Iterable, Optional, Sequence
from xml.sax.saxutils import escape as xml_escape


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class ChatMessage:
    index: int
    timestamp: Optional[datetime]
    sender: str
    text: str
    is_outgoing: bool
    is_system: bool
    attachment_name: Optional[str] = None
    attachment_path: Optional[str] = None
    attachment_type: Optional[str] = None


@dataclass
class MediaAttachment:
    filename: str
    file_type: str
    size_bytes: int
    relative_path: str
    linked: bool = False


@dataclass
class ChatAnalytics:
    chat_name: str
    is_group: bool
    total_messages: int
    outgoing_count: int
    incoming_count: int
    system_count: int
    senders_summary: dict[str, int]
    date_first_msg: Optional[datetime]
    date_last_msg: Optional[datetime]
    media_counts_by_type: dict[str, int]
    my_name: Optional[str] = None

    @classmethod
    def calculate(
        cls, chat_name: str, is_group: bool, messages: Sequence[ChatMessage], my_name: Optional[str] = None
    ) -> "ChatAnalytics":
        regular = [message for message in messages if not message.is_system]
        dated = sorted((message.timestamp for message in regular if message.timestamp is not None))
        senders: dict[str, int] = {}
        media: dict[str, int] = {}
        for message in regular:
            senders[message.sender] = senders.get(message.sender, 0) + 1
            if message.attachment_type:
                media[message.attachment_type] = media.get(message.attachment_type, 0) + 1
        return cls(
            chat_name=chat_name,
            is_group=is_group,
            total_messages=len(messages),
            outgoing_count=sum(not item.is_system and item.is_outgoing for item in messages),
            incoming_count=sum(not item.is_system and not item.is_outgoing for item in messages),
            system_count=sum(item.is_system for item in messages),
            senders_summary=senders,
            date_first_msg=dated[0] if dated else None,
            date_last_msg=dated[-1] if dated else None,
            media_counts_by_type=media,
            my_name=my_name,
        )


@dataclass
class ChatConversation:
    chat_name: str
    source_file: str
    extracted_dir: Optional[str]
    is_group: bool
    my_name: Optional[str]
    messages: list[ChatMessage]
    media_files: list[MediaAttachment]
    analytics: ChatAnalytics


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp"}
AUDIO_EXTENSIONS = {".opus", ".ogg", ".mp3", ".m4a", ".aac", ".wav", ".amr"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".3gp", ".mkv"}
STICKER_EXTENSIONS = {".webp"}
MEDIA_OMITTED_RE = re.compile(
    r"<(?:media omitted|archivo(?:s)? multimedia omitido(?:s)?|multimedia omitted)>|"
    r"(?:imagen|image|video|audio|sticker|documento|document) omitted", re.IGNORECASE
)
SYSTEM_RE = re.compile(
    r"(?:messages and calls are end-to-end encrypted|los mensajes y las llamadas están cifrados|"
    r"created (?:group|this group)|creó (?:el grupo|este grupo)|added |añadió |agregó |"
    r"removed |eliminó |left|salió|joined|se unió|changed (?:the )?(?:group )?(?:subject|description)|"
    r"cambió (?:el )?(?:asunto|nombre|icono)|you were added|te añadieron|te agregaron)",
    re.IGNORECASE,
)
GROUP_RE = re.compile(r"(?:created (?:group|this group)|creó (?:el grupo|este grupo)|group (?:subject|description)|grupo)", re.I)
SENDER_RE = re.compile(r"^(?P<date>\[?\s*[^\]\n]+?\s*\]?)(?:\s+-\s+|\s+)(?P<body>.*)$")
DATE_PREFIX_RE = re.compile(
    r"^\[?(?P<date>(?:\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}|\d{1,2}\s+"
    r"(?:de\s+)?[A-Za-záéíóúñ]+(?:\s+de)?\s+\d{2,4}|[A-Za-záéíóúñ]+\s+\d{1,2},?\s+\d{2,4})\s*,?\s+\d{1,2}:\d{2}(?::\d{2})?"
    r"(?:\s*[AaPp]\.?\s*[Mm]\.?)?)\]?\s*(?:-\s*)?(?P<body>.*)$"
)


def _normalise(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", value.casefold()) if unicodedata.category(ch) != "Mn").strip()


def _cancelled(cancel_event: Optional[Event]) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _file_type(filename: str) -> str:
    extension = Path(filename).suffix.casefold()
    if extension in STICKER_EXTENSIONS and "sticker" in filename.casefold():
        return "sticker"
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    return "document"


def _chat_name_from_path(path: Path) -> str:
    name = path.stem
    name = re.sub(r"^(?:whatsapp chat (?:with|con)\s*|chat (?:with|con)\s*)", "", name, flags=re.I)
    return name.strip() or path.stem


def _parse_datetime(value: str) -> Optional[datetime]:
    value = value.replace("\u200e", "").strip().strip("[]")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"([AaPp])\.\s*[Mm]\.?(?=$|\s)", r"\1M", value)
    formats = (
        "%d/%m/%y, %H:%M", "%d/%m/%Y, %H:%M", "%m/%d/%y, %H:%M", "%m/%d/%Y, %H:%M",
        "%d/%m/%y, %H:%M:%S", "%d/%m/%Y, %H:%M:%S", "%m/%d/%y, %H:%M:%S", "%m/%d/%Y, %H:%M:%S",
        "%d.%m.%y, %H:%M", "%d.%m.%Y, %H:%M", "%Y-%m-%d, %H:%M", "%Y-%m-%d %H:%M",
        "%d/%m/%y, %I:%M %p", "%d/%m/%Y, %I:%M %p", "%m/%d/%y, %I:%M %p", "%m/%d/%Y, %I:%M %p",
        "%d/%m/%y, %I:%M:%S %p", "%d/%m/%Y, %I:%M:%S %p", "%m/%d/%y, %I:%M:%S %p", "%m/%d/%Y, %I:%M:%S %p",
    )
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    # Fechas con nombre de mes (exportaciones ES/EN).  Se traduce a número para
    # no depender de la configuración regional del equipo.
    months = {
        "january": 1, "jan": 1, "enero": 1, "february": 2, "feb": 2, "febrero": 2,
        "march": 3, "mar": 3, "marzo": 3, "april": 4, "apr": 4, "abril": 4,
        "may": 5, "mayo": 5, "june": 6, "jun": 6, "junio": 6, "july": 7, "jul": 7, "julio": 7,
        "august": 8, "aug": 8, "agosto": 8, "september": 9, "sep": 9, "septiembre": 9,
        "october": 10, "oct": 10, "octubre": 10, "november": 11, "nov": 11, "noviembre": 11,
        "december": 12, "dec": 12, "diciembre": 12,
    }
    named = re.match(r"(\d{1,2})\s+(?:de\s+)?([A-Za-záéíóúñ]+)(?:\s+de)?\s+(\d{2,4}),?\s+(.*)", value, re.I)
    named_parts: Optional[tuple[str, str, str, str]] = named.groups() if named else None
    if named_parts is None:
        month_first = re.match(r"([A-Za-záéíóúñ]+)\s+(\d{1,2}),?\s+(\d{2,4}),?\s+(.*)", value, re.I)
        if month_first:
            month_name, day, year, clock = month_first.groups()
            named_parts = (day, month_name, year, clock)
    if named_parts and _normalise(named_parts[1]) in months:
        day, month_name, year, clock = named_parts
        year_num = int(year) + (2000 if len(year) == 2 else 0)
        clock = clock.replace(".", "")
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
            try:
                parsed_clock = datetime.strptime(clock, fmt)
                return datetime(year_num, months[_normalise(month_name)], int(day), parsed_clock.hour, parsed_clock.minute, parsed_clock.second)
            except ValueError:
                continue
    return None


class WhatsAppParser:
    """Convierte texto exportado por WhatsApp en mensajes estructurados."""

    def __init__(self, chat_name: Optional[str] = None, my_name: Optional[str] = None) -> None:
        self.chat_name = chat_name
        self.my_name = my_name

    def parse(self, text: str, cancel_event: Optional[Event] = None, progress_callback: Optional[ProgressCallback] = None) -> tuple[list[ChatMessage], bool]:
        messages: list[ChatMessage] = []
        current: Optional[ChatMessage] = None
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for line_number, line in enumerate(lines, 1):
            if _cancelled(cancel_event):
                raise InterruptedError("Procesamiento cancelado por el usuario.")
            parsed = self._parse_line(line)
            if parsed is None:
                if current is not None:
                    current.text = f"{current.text}\n{line}" if current.text else line
                continue
            timestamp, sender, body, is_system = parsed
            current = ChatMessage(len(messages) + 1, timestamp, sender, body, False, is_system)
            messages.append(current)
            if progress_callback and (line_number % 250 == 0 or line_number == len(lines)):
                progress_callback(line_number, len(lines), "Analizando mensajes")

        senders = {_normalise(message.sender) for message in messages if not message.is_system and message.sender}
        literal_me = {"tu", "you", "yo"}
        chat_contact = _normalise(self.chat_name or "")
        # WhatsApp no incluye metadatos de tipo de chat en el TXT.  Además de
        # los avisos de grupo y tres participantes, el caso de dos remitentes
        # que no coincide con el título (y ninguno es el marcador de "yo") es
        # una señal útil de grupo pequeño.
        is_group = (
            len(senders) > 2
            or any(message.is_system and GROUP_RE.search(message.text) for message in messages)
            or (len(senders) >= 2 and chat_contact not in senders and not (senders & literal_me))
        )
        self._classify_outgoing(messages, is_group)
        return messages, is_group

    @staticmethod
    def _parse_line(line: str) -> Optional[tuple[Optional[datetime], str, str, bool]]:
        match = DATE_PREFIX_RE.match(line.lstrip("\ufeff\u200e"))
        if not match:
            return None
        timestamp = _parse_datetime(match.group("date"))
        if timestamp is None:
            return None
        body = match.group("body").strip()
        sender_match = re.match(r"^(?P<sender>[^:]{1,150}):\s?(?P<text>.*)$", body)
        if sender_match:
            return timestamp, sender_match.group("sender").strip(), sender_match.group("text"), False
        return timestamp, "Sistema", body, bool(SYSTEM_RE.search(body) or True)

    def _classify_outgoing(self, messages: Iterable[ChatMessage], is_group: bool) -> None:
        chat_contact = _normalise(self.chat_name or "")
        my_name = _normalise(self.my_name or "")
        mine = {"tu", "you", "yo"}
        if my_name:
            mine.add(my_name)
        for message in messages:
            if message.is_system:
                continue
            sender = _normalise(message.sender)
            if sender in mine:
                message.is_outgoing = True
            elif is_group:
                message.is_outgoing = False
            else:
                # En un chat individual el nombre del archivo/chat identifica al
                # interlocutor; cualquier remitente distinto es el propietario.
                message.is_outgoing = bool(chat_contact and sender != chat_contact)


class WhatsAppUnpacker:
    """Extrae ZIPs de forma segura y cataloga los medios incluidos."""

    def __init__(self, source_file: str | Path) -> None:
        self.source_file = Path(source_file)

    def unpack(self, cancel_event: Optional[Event] = None, progress_callback: Optional[ProgressCallback] = None) -> tuple[Path, Optional[Path], list[MediaAttachment]]:
        if not self.source_file.is_file():
            raise FileNotFoundError(f"No existe el archivo de exportación: {self.source_file}")
        if self.source_file.suffix.casefold() != ".zip":
            if self.source_file.suffix.casefold() != ".txt":
                raise ValueError("La exportación debe ser un archivo .txt o .zip.")
            return self.source_file, None, []
        destination = self.source_file.with_name(f"{self.source_file.stem}_extraido")
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.source_file) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            for index, item in enumerate(members, 1):
                if _cancelled(cancel_event):
                    raise InterruptedError("Extracción cancelada por el usuario.")
                relative = PurePosixPath(item.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"ZIP inseguro: ruta no permitida '{item.filename}'.")
                target = (destination / Path(*relative.parts)).resolve()
                if destination.resolve() not in target.parents and target != destination.resolve():
                    raise ValueError(f"ZIP inseguro: ruta no permitida '{item.filename}'.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if progress_callback:
                    progress_callback(index, len(members), "Extrayendo ZIP")
        txt_files = sorted(path for path in destination.rglob("*.txt") if path.is_file())
        if not txt_files:
            raise ValueError("El ZIP no contiene un archivo .txt de chat.")
        # Normalmente solo hay uno; el mayor es el candidato más probable cuando
        # también se incluye documentación pequeña.
        chat_file = max(txt_files, key=lambda path: path.stat().st_size)
        media: list[MediaAttachment] = []
        for path in destination.rglob("*"):
            if path.is_file() and path != chat_file:
                media.append(MediaAttachment(path.name, _file_type(path.name), path.stat().st_size, str(path.resolve())))
        return chat_file, destination, media

    @staticmethod
    def link_media(messages: Sequence[ChatMessage], media_files: Sequence[MediaAttachment]) -> None:
        available = list(media_files)
        for message in messages:
            mentioned = _attachment_name_from_text(message.text)
            candidate = None
            if mentioned:
                normalized_name = _normalise(Path(mentioned).name)
                candidate = next((item for item in available if _normalise(item.filename) == normalized_name), None)
            if candidate is None:
                normalized_text = _normalise(message.text)
                # Es más fiable contrastar contra el catálogo real del ZIP que
                # adivinar nombres con espacios o paréntesis mediante regex.
                candidate = next(
                    (item for item in sorted(available, key=lambda media: len(media.filename), reverse=True)
                     if _normalise(item.filename) in normalized_text),
                    None,
                )
            if candidate is None and MEDIA_OMITTED_RE.search(message.text):
                candidate = next((item for item in available if not item.linked), None)
            if candidate:
                candidate.linked = True
                message.attachment_name = candidate.filename
                message.attachment_path = candidate.relative_path
                message.attachment_type = candidate.file_type


def _attachment_name_from_text(text: str) -> Optional[str]:
    # Nombre de archivo explícito de exportaciones Android/iOS y documentos.
    match = re.search(r"(?:^|\s|[\"'“”])([^\\/:*?<>|\n]+?\.(?:jpe?g|png|gif|webp|heic|bmp|opus|ogg|mp3|m4a|aac|wav|amr|mp4|mov|avi|3gp|mkv|pdf|docx?|xlsx?|pptx?|zip|txt))(?=$|\s|[\"'”.,;)\]])", text, re.I)
    return match.group(1).strip() if match else None


class ChatFilter:
    """Filtros composables, siempre sin modificar la conversación original."""

    @staticmethod
    def apply(
        messages: Iterable[ChatMessage], sender: Optional[str] = None, date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None, text: Optional[str] = None, has_media: Optional[bool] = None,
        exact_sender: bool = False,
    ) -> list[ChatMessage]:
        wanted_sender = _normalise(sender) if sender else ""
        wanted_text = _normalise(text) if text else ""
        result: list[ChatMessage] = []
        for message in messages:
            normalized_sender = _normalise(message.sender)
            if wanted_sender and (normalized_sender != wanted_sender if exact_sender else wanted_sender not in normalized_sender):
                continue
            if date_from and (message.timestamp is None or message.timestamp < date_from):
                continue
            if date_to and (message.timestamp is None or message.timestamp > date_to):
                continue
            if wanted_text and wanted_text not in _normalise(message.text):
                continue
            if has_media is not None and bool(message.attachment_path) != has_media:
                continue
            result.append(message)
        return result

    filter = apply


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    raise TypeError(f"No se puede serializar {type(value)!r}")


class ChatExporter:
    """Genera los formatos de intercambio de una única conversación."""

    MESSAGE_COLUMNS = ("Índice", "Fecha y hora", "Remitente", "Mensaje", "Enviado por mí", "Mensaje del sistema", "Archivo adjunto", "Ruta del archivo", "Tipo de archivo")

    def __init__(self, conversation: ChatConversation) -> None:
        self.conversation = conversation

    def to_json(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.conversation)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
        return path

    def to_csv(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(self.MESSAGE_COLUMNS)
            for item in self.conversation.messages:
                writer.writerow(_message_row(item))
        return path

    def to_excel(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sheets = [
            ("Mensajes", [self.MESSAGE_COLUMNS] + [_message_row(item) for item in self.conversation.messages]),
            ("Resumen Analítico", _analytics_rows(self.conversation.analytics)),
            ("Archivos Multimedia", [("Archivo", "Tipo", "Tamaño (bytes)", "Ruta real", "Vinculado")] + [
                (item.filename, item.file_type, item.size_bytes, item.relative_path, "Sí" if item.linked else "No") for item in self.conversation.media_files
            ]),
        ]
        try:
            from openpyxl import Workbook  # type: ignore[import-not-found]
            book = Workbook()
            book.remove(book.active)
            for name, rows in sheets:
                worksheet = book.create_sheet(name)
                for row in rows:
                    worksheet.append(list(row))
                worksheet.freeze_panes = "A2"
                for column in worksheet.columns:
                    letter = column[0].column_letter
                    worksheet.column_dimensions[letter].width = min(70, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
            book.save(path)
        except ImportError:
            _write_minimal_xlsx(path, sheets)
        return path

    def to_summary_report(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        stats = self.conversation.analytics
        first = stats.date_first_msg.isoformat(sep=" ") if stats.date_first_msg else "Sin fecha"
        last = stats.date_last_msg.isoformat(sep=" ") if stats.date_last_msg else "Sin fecha"
        lines = [
            f"# Reporte de chat: {stats.chat_name}", "", f"- Tipo: {'Grupo' if stats.is_group else 'Individual'}",
            f"- Mensajes totales: {stats.total_messages}", f"- Enviados: {stats.outgoing_count}",
            f"- Recibidos: {stats.incoming_count}", f"- Sistema: {stats.system_count}", f"- Primer mensaje: {first}",
            f"- Último mensaje: {last}", "", "## Participantes", "",
        ]
        lines.extend(f"- {sender}: {count}" for sender, count in sorted(stats.senders_summary.items(), key=lambda pair: (-pair[1], pair[0])))
        lines += ["", "## Multimedia", ""]
        lines.extend(f"- {kind}: {count}" for kind, count in sorted(stats.media_counts_by_type.items()))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


def _message_row(item: ChatMessage) -> tuple[object, ...]:
    return (item.index, item.timestamp.isoformat(sep=" ") if item.timestamp else "", item.sender, item.text,
            "Sí" if item.is_outgoing else "No", "Sí" if item.is_system else "No", item.attachment_name or "",
            item.attachment_path or "", item.attachment_type or "")


def _analytics_rows(stats: ChatAnalytics) -> list[tuple[object, object]]:
    rows: list[tuple[object, object]] = [
        ("Chat", stats.chat_name), ("Tipo", "Grupo" if stats.is_group else "Individual"), ("Mi nombre", stats.my_name or ""),
        ("Mensajes totales", stats.total_messages), ("Enviados", stats.outgoing_count), ("Recibidos", stats.incoming_count),
        ("Sistema", stats.system_count), ("Primer mensaje", stats.date_first_msg.isoformat(sep=" ") if stats.date_first_msg else ""),
        ("Último mensaje", stats.date_last_msg.isoformat(sep=" ") if stats.date_last_msg else ""), ("", ""), ("Mensajes por remitente", "Cantidad"),
    ]
    rows.extend(stats.senders_summary.items())
    rows.append(("Multimedia por tipo", "Cantidad"))
    rows.extend(stats.media_counts_by_type.items())
    return rows


def _excel_column(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _write_minimal_xlsx(path: Path, sheets: Sequence[tuple[str, Sequence[Sequence[object]]]]) -> None:
    """Escritor XLSX pequeño de respaldo para no imponer dependencias externas."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1)) + "</Types>")
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        sheet_tags = "".join(f'<sheet name="{xml_escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1))
        archive.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheet_tags}</sheets></workbook>')
        rels = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
        archive.writestr("xl/_rels/workbook.xml.rels", f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for sheet_index, (_, rows) in enumerate(sheets, 1):
            xml_rows = []
            for row_index, row in enumerate(rows, 1):
                cells = "".join(f'<c r="{_excel_column(col_index)}{row_index}" t="inlineStr"><is><t>{xml_escape(str(value if value is not None else ""))}</t></is></c>' for col_index, value in enumerate(row, 1))
                xml_rows.append(f'<row r="{row_index}">{cells}</row>')
            archive.writestr(f"xl/worksheets/sheet{sheet_index}.xml", '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>")


def process_chat_file(
    local_path: str | Path, my_name: Optional[str] = None, chat_name: Optional[str] = None,
    cancel_event: Optional[Event] = None, progress_callback: Optional[ProgressCallback] = None,
) -> ChatConversation:
    """Procesa una exportación local completa y devuelve la conversación."""
    source = Path(local_path)
    unpacker = WhatsAppUnpacker(source)
    text_path, extracted_dir, media_files = unpacker.unpack(cancel_event, progress_callback)
    try:
        text = text_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = text_path.read_text(encoding="utf-8", errors="replace")
    final_chat_name = chat_name or _chat_name_from_path(text_path)
    messages, is_group = WhatsAppParser(final_chat_name, my_name).parse(text, cancel_event, progress_callback)
    WhatsAppUnpacker.link_media(messages, media_files)
    analytics = ChatAnalytics.calculate(final_chat_name, is_group, messages, my_name)
    return ChatConversation(final_chat_name, str(source.resolve()), str(extracted_dir.resolve()) if extracted_dir else None,
                            is_group, my_name, messages, media_files, analytics)


def export_processed_chat(conversation: ChatConversation, output_dir: str | Path, formats: Sequence[str] = ("json", "csv", "xlsx"), report: bool = False) -> dict[str, str]:
    """Exporta una conversación. Cada invocación crea archivos de un solo chat."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[<>:\\/*?|\"\x00-\x1f]+", "_", conversation.chat_name).strip(" ._") or "chat"
    exporter = ChatExporter(conversation)
    generated: dict[str, str] = {}
    aliases = {"excel": "xlsx", "all": "all"}
    selected = {aliases.get(item.casefold(), item.casefold()) for item in formats}
    if "all" in selected:
        selected.update({"json", "csv", "xlsx"})
    writers = {"json": (exporter.to_json, ".json"), "csv": (exporter.to_csv, ".csv"), "xlsx": (exporter.to_excel, ".xlsx")}
    for fmt in ("json", "csv", "xlsx"):
        if fmt in selected:
            generated[fmt] = str(writers[fmt][0](directory / f"{safe_name}.{fmt}").resolve())
    if report:
        generated["report"] = str(exporter.to_summary_report(directory / f"{safe_name}_reporte.md").resolve())
    return generated
