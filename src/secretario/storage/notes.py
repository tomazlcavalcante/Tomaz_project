"""Notas do usuário: texto simples no SQLite, com busca por palavras (FTS5).

A busca ignora acentos e maiúsculas ("reuniao" acha "Reunião"). Quem decide
se uma nota pode ser criada ou apagada é o gateway (aprovação humana);
aqui só se guarda e se busca.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from secretario.core.types import utcnow
from secretario.storage.db import Database


@dataclass
class Note:
    id: int
    title: str
    body: str
    updated_at: str


@dataclass
class SearchHit:
    note: Note
    snippet: str


class NoteStore:
    def __init__(self, db: Database):
        self.db = db

    def get(self, note_id: int) -> Note | None:
        rows = self.db.query("SELECT id, title, body, updated_at FROM notes WHERE id = ?", (note_id,))
        return _note(rows[0]) if rows else None

    def find_by_title(self, title: str) -> Note | None:
        rows = self.db.query("SELECT id, title, body, updated_at FROM notes WHERE title = ?", (title.strip(),))
        return _note(rows[0]) if rows else None

    def save(self, title: str, body: str, session_id: str | None = None) -> tuple[Note, bool]:
        """Cria a nota, ou substitui o texto da que tem o mesmo título. Retorna (nota, criada?)."""
        title = title.strip()
        now = utcnow().isoformat()
        existing = self.find_by_title(title)
        if existing:
            self.db.execute("UPDATE notes SET body = ?, updated_at = ? WHERE id = ?", (body, now, existing.id))
            return self.get(existing.id), False
        self.db.execute(
            "INSERT INTO notes (title, body, created_at, updated_at, session_id) VALUES (?, ?, ?, ?, ?)",
            (title, body, now, now, session_id),
        )
        return self.find_by_title(title), True

    def delete(self, note_id: int) -> bool:
        return self.db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount > 0

    def count(self) -> int:
        return self.db.query("SELECT COUNT(*) FROM notes")[0][0]

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        """Notas com qualquer uma das palavras, as mais relevantes primeiro. Sem palavras: as mais recentes."""
        words = _words(query)
        if not words:
            rows = self.db.query(
                "SELECT id, title, body, updated_at FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
            return [SearchHit(_note(r), _preview(r["body"])) for r in rows]
        # Cada palavra entre aspas: o texto do modelo nunca vira sintaxe do FTS5 (AND, NEAR, *, ...)
        match = " OR ".join(f'"{w}"' for w in words)
        rows = self.db.query(
            """SELECT n.id, n.title, n.body, n.updated_at,
                      snippet(notes_fts, 1, '[', ']', ' … ', 12) AS snip
               FROM notes_fts JOIN notes n ON n.id = notes_fts.rowid
               WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts, 5.0, 1.0) LIMIT ?""",
            (match, limit),
        )
        return [SearchHit(_note(r), r["snip"] or _preview(r["body"])) for r in rows]


def _note(row) -> Note:
    return Note(id=row["id"], title=row["title"], body=row["body"], updated_at=row["updated_at"])


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text, flags=re.UNICODE)[:12]


def _preview(body: str, size: int = 120) -> str:
    flat = " ".join(body.split())
    return flat if len(flat) <= size else flat[:size] + "…"
