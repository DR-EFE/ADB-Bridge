"""Persistencia local SQLite para Android Tools.

SQLite es la fuente de verdad. Los métodos de export/import son explícitos y
no intervienen en la ejecución normal de jobs.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from sqlalchemy import create_engine
except ImportError:  # pragma: no cover - helpful during minimal bootstrap
    create_engine = None


SCHEMA_VERSION = 1


def get_default_database_path() -> Path:
    root = os.environ.get("ANDROID_TOOLS_DATA_DIR")
    if root:
        return Path(root) / "android_admin.db"
    return Path(__file__).resolve().parents[2] / "data" / "android_admin.db"


class SQLiteStore:
    """Repositorio pequeño, thread-safe por conexión, sin depender de ORM."""

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path or get_default_database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = (
            create_engine(f"sqlite:///{self.path.as_posix()}", future=True)
            if create_engine else None
        )
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def initialize(self) -> None:
        schema = """
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version(version)
                SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
            CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, serial TEXT NOT NULL UNIQUE, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS connection_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, endpoint TEXT NOT NULL, serial TEXT,
                success INTEGER NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, serial TEXT, status TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS backups (
                id TEXT PRIMARY KEY, serial TEXT NOT NULL, status TEXT NOT NULL,
                payload_json TEXT NOT NULL, created_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, serial TEXT, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, serial TEXT,
                message TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metrics (
                name TEXT PRIMARY KEY, value REAL NOT NULL, payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS health_snapshots (
                id TEXT PRIMARY KEY, serial TEXT NOT NULL, captured_at TEXT NOT NULL,
                overall_status TEXT NOT NULL, payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_connections_endpoint ON connection_attempts(endpoint);
            CREATE INDEX IF NOT EXISTS idx_health_snapshots_serial_time ON health_snapshots(serial, captured_at DESC);
            """
        if self.engine is not None:
            # SQLAlchemy controla la conexión/migración inicial; los repositorios
            # siguen usando SQL parametrizado para evitar una capa ORM innecesaria.
            with self.engine.begin() as conn:
                for statement in schema.split(";"):
                    statement = statement.strip()
                    if statement:
                        conn.exec_driver_sql(statement)
        else:
            with self.connect() as conn:
                conn.executescript(schema)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _put(self, table: str, key: str, payload: Dict[str, Any], *, serial: Optional[str] = None,
             status: Optional[str] = None) -> None:
        now = self._now()
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        with self.connect() as conn:
            if table == "jobs":
                conn.execute("""INSERT INTO jobs(id,serial,status,payload_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET serial=excluded.serial,
                    status=excluded.status,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                    (key, serial, status or "pending", encoded, payload.get("created_at", now), now))
            elif table == "backups":
                conn.execute("""INSERT INTO backups(id,serial,status,payload_json,created_at,completed_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                    payload_json=excluded.payload_json,completed_at=excluded.completed_at""",
                    (key, serial or "", status or "pending", encoded, payload.get("created_at", now), payload.get("completed_at")))
            elif table == "devices":
                conn.execute("""INSERT INTO devices(id,serial,payload_json,created_at,updated_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET serial=excluded.serial,
                    payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                    (key, serial or payload.get("serial", ""), encoded, payload.get("created_at", now), now))

    def save_device(self, payload: Dict[str, Any]) -> None:
        self._put("devices", str(payload.get("id") or payload["serial"]), payload, serial=payload["serial"])

    def save_job(self, payload: Dict[str, Any]) -> None:
        self._put("jobs", str(payload["id"]), payload, serial=payload.get("serial"), status=payload.get("status"))

    def save_backup(self, payload: Dict[str, Any]) -> None:
        self._put("backups", str(payload["id"]), payload, serial=payload.get("serial"), status=payload.get("status"))

    def record_connection(self, payload: Dict[str, Any], serial: Optional[str] = None) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO connection_attempts(endpoint,serial,success,payload_json,created_at) VALUES(?,?,?,?,?)",
                         (payload.get("endpoint", ""), serial, int(bool(payload.get("success"))), json.dumps(payload, default=str), self._now()))

    def record_notification(self, payload: Dict[str, Any], serial: Optional[str] = None) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO notifications(serial,payload_json,created_at) VALUES(?,?,?)",
                         (serial, json.dumps(payload, default=str), self._now()))

    def record_error(self, message: str, payload: Optional[Dict[str, Any]] = None, *, job_id: Optional[str] = None,
                     serial: Optional[str] = None) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO errors(job_id,serial,message,payload_json,created_at) VALUES(?,?,?,?,?)",
                         (job_id, serial, message, json.dumps(payload or {}, default=str), self._now()))

    def record_metric(self, name: str, value: float, payload: Optional[Dict[str, Any]] = None) -> None:
        now = self._now()
        with self.connect() as conn:
            conn.execute("""INSERT INTO metrics(name,value,payload_json,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value,payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                         (name, value, json.dumps(payload or {}, default=str), now))

    def save_health_snapshot(self, payload: Dict[str, Any]) -> None:
        """Persiste un snapshot de salud; cada captura conserva su propio historial."""
        with self.connect() as conn:
            conn.execute("""INSERT INTO health_snapshots(id,serial,captured_at,overall_status,payload_json)
                VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json,
                overall_status=excluded.overall_status""", (
                    str(payload["snapshot_id"]), str(payload["serial"]), str(payload["captured_at"]),
                    str(payload.get("overall_status", "PARTIAL")), json.dumps(payload, default=str),
                ))

    def list_health_snapshots(self, serial: str, *, limit: int = 100,
                              start_utc: Optional[str] = None, end_utc: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT payload_json FROM health_snapshots WHERE serial=?"
        params: List[Any] = [serial]
        if start_utc:
            query += " AND captured_at>=?"
            params.append(start_utc)
        if end_utc:
            query += " AND captured_at<=?"
            params.append(end_utc)
        query += " ORDER BY captured_at DESC LIMIT ?"
        params.append(max(1, limit))
        with self.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute(query, tuple(params))]

    def list_jobs(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT payload_json FROM jobs"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status=?"
            params = (status,)
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute(query, params)]

    def export_json(self, path: Path | str) -> None:
        target = Path(path)
        payload = {"jobs": self.list_jobs()}
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

