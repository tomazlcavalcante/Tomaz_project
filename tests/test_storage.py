from __future__ import annotations

from secretario.core.session import Session
from secretario.core.types import Label, Message
from secretario.storage.db import MIGRATIONS, ConversationStore, Database


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "x.db"
    db = Database(path)
    assert db.schema_version == len(MIGRATIONS)
    db.close()
    db2 = Database(path)  # reabrir não reaplica nada
    assert db2.schema_version == len(MIGRATIONS)


def test_roundtrip(tmp_path):
    store = ConversationStore(Database(tmp_path / "x.db"))
    s = Session(model_key="local", label=Label.INTERNO, tool_profile="basico")
    store.save_session(s)
    store.add_message(s.id, Message(role="user", content="Reunião às 15h, acentuação ç ã é"))
    store.add_message(s.id, Message(role="assistant", content="Anotado.", model_key="local"))
    store.set_title_if_empty(s.id, "Primeiro título")
    store.set_title_if_empty(s.id, "Não substitui")

    loaded = store.load_session(s.id)
    assert loaded.label == Label.INTERNO and loaded.tool_profile == "basico"
    assert [m.content for m in loaded.messages] == ["Reunião às 15h, acentuação ç ã é", "Anotado."]
    assert loaded.messages[1].model_key == "local"
    assert store.recent_sessions()[0]["title"] == "Primeiro título"
    assert store.load_session("inexistente") is None


def test_v0_database_is_upgraded(tmp_path):
    """Um banco criado pela V0 ganha a coluna nova; conversas antigas ficam sem ferramentas."""
    import sqlite3

    path = tmp_path / "v0.db"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0] + "\nPRAGMA user_version = 1;")
    conn.execute("INSERT INTO sessions (id, created_at, model_key, label) VALUES ('antiga', '2026-10-01T10:00:00+00:00', 'local', 0)")
    conn.commit()
    conn.close()

    store = ConversationStore(Database(path))
    assert store.db.schema_version == len(MIGRATIONS)
    assert store.load_session("antiga").tool_profile == "nenhum"
