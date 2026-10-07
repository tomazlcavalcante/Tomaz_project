"""SQLite: um arquivo, nenhum serviço.

Guarda as conversas (conteúdo) e a trilha de auditoria (metadados).
Migrações são uma lista de SQL aplicada em ordem; a versão atual fica em
PRAGMA user_version. Para mudar o esquema, acrescente um item no fim da
lista, nunca edite um item que já existe.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from secretario.core.session import Session
from secretario.core.types import Label, Message

MIGRATIONS: list[str] = [
    # 1: conversas, mensagens e auditoria
    """
    CREATE TABLE sessions (
        id          TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL,
        model_key   TEXT NOT NULL,
        label       INTEGER NOT NULL,
        title       TEXT
    );
    CREATE TABLE messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id  TEXT NOT NULL REFERENCES sessions(id),
        role        TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant')),
        content     TEXT NOT NULL,
        model_key   TEXT,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX idx_messages_session ON messages(session_id, id);
    CREATE TABLE audit_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT NOT NULL,
        session_id  TEXT,
        kind        TEXT NOT NULL,
        data        TEXT NOT NULL
    );
    CREATE INDEX idx_audit_session ON audit_events(session_id, id);
    CREATE INDEX idx_audit_kind ON audit_events(kind, id);
    """,
    # 2: perfil de ferramentas de cada conversa (V1)
    """
    ALTER TABLE sessions ADD COLUMN tool_profile TEXT;
    """,
]


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            current = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
                self._conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")

    @property
    def schema_version(self) -> int:
        return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class ConversationStore:
    def __init__(self, db: Database):
        self.db = db

    def save_session(self, session: Session) -> None:
        self.db.execute(
            """INSERT INTO sessions (id, created_at, model_key, label, tool_profile) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET model_key = excluded.model_key, label = excluded.label,
                                             tool_profile = excluded.tool_profile""",
            (session.id, session.created_at.isoformat(), session.model_key, int(session.label), session.tool_profile),
        )

    def add_message(self, session_id: str, message: Message) -> None:
        self.db.execute(
            "INSERT INTO messages (session_id, role, content, model_key, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, message.role, message.content, message.model_key, message.created_at.isoformat()),
        )

    def set_title_if_empty(self, session_id: str, title: str) -> None:
        self.db.execute(
            "UPDATE sessions SET title = ? WHERE id = ? AND title IS NULL", (title[:80], session_id)
        )

    def load_session(self, session_id: str) -> Session | None:
        rows = self.db.query("SELECT * FROM sessions WHERE id = ?", (session_id,))
        if not rows:
            return None
        row = rows[0]
        session = Session(
            id=row["id"],
            model_key=row["model_key"],
            label=Label(row["label"]),
            tool_profile=row["tool_profile"] or "nenhum",  # conversas da V0 não tinham ferramentas
            created_at=datetime.fromisoformat(row["created_at"]),
        )
        for m in self.db.query("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,)):
            session.messages.append(
                Message(
                    role=m["role"],
                    content=m["content"],
                    model_key=m["model_key"],
                    created_at=datetime.fromisoformat(m["created_at"]),
                )
            )
        return session

    def recent_sessions(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.query(
            """SELECT s.id, s.created_at, s.title, s.label, s.model_key,
                      (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS n_messages
               FROM sessions s ORDER BY s.created_at DESC LIMIT ?""",
            (limit,),
        )


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, default=str, sort_keys=True)
