"""Trilha de auditoria: o que aconteceu, quando e com qual modelo.

Grava só metadados (modelo, se é local, nível de sigilo, tamanhos, tempos,
erros). O conteúdo das mensagens fica na tabela de conversas. Assim dá
para responder perguntas como "algum dado confidencial foi para a nuvem?"
com uma consulta SQL, sem expor o conteúdo de novo.
"""

from __future__ import annotations

import json
from typing import Any

from secretario.core.types import utcnow
from secretario.storage.db import Database, dumps

FORBIDDEN_KEYS = {"content", "text", "api_key", "authorization", "password", "secret"}


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def record(self, kind: str, session_id: str | None = None, **data: Any) -> None:
        leaked = FORBIDDEN_KEYS & {k.lower() for k in data}
        if leaked:
            # Proteção contra erro de programação: auditoria não guarda conteúdo nem segredos.
            raise ValueError(f"Campos proibidos na auditoria: {sorted(leaked)}")
        self.db.execute(
            "INSERT INTO audit_events (ts, session_id, kind, data) VALUES (?, ?, ?, ?)",
            (utcnow().isoformat(), session_id, kind, dumps(data)),
        )

    def events(self, session_id: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT ts, session_id, kind, data FROM audit_events WHERE 1=1"
        params: list[Any] = []
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY id"
        return [
            {"ts": r["ts"], "session_id": r["session_id"], "kind": r["kind"], **json.loads(r["data"])}
            for r in self.db.query(sql, params)
        ]
