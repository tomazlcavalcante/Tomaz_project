"""Notas: armazenamento com busca FTS5 e as ferramentas, com aprovação."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from secretario.audit.log import AuditLog
from secretario.core.session import Session
from secretario.core.types import ToolCall
from secretario.storage.db import Database
from secretario.storage.notes import NoteStore
from secretario.tools import gateway as gw
from secretario.tools.base import ToolContext, ToolError
from secretario.tools.notes import NOTE_TOOLS, DeleteNoteArgs, SaveNoteArgs, _delete_preview, _save_preview
from secretario.tools.registry import ToolRegistry


@pytest.fixture
def notes(tmp_path):
    return NoteStore(Database(tmp_path / "x.db"))


# ------------------------------------------------------------------ armazenamento
def test_search_ignores_accents_and_case(notes):
    notes.save("Reunião de orçamento", "Discutir verba de marketing com a diretoria.")
    notes.save("Viagem", "Passagens para São Paulo em novembro.")
    hits = notes.search("reuniao ORCAMENTO")
    assert [h.note.title for h in hits] == ["Reunião de orçamento"]
    assert [h.note.title for h in notes.search("sao paulo")] == ["Viagem"]
    assert "[marketing]" in notes.search("marketing")[0].snippet


def test_title_weighs_more_than_body(notes):
    notes.save("Notas soltas", "lembrar do contrato")
    notes.save("Contrato de aluguel", "vence em março")
    assert notes.search("contrato")[0].note.title == "Contrato de aluguel"


def test_update_and_delete_keep_index_in_sync(notes):
    note, created = notes.save("Lista", "comprar café")
    assert created
    same, created = notes.save("Lista", "comprar chá")
    assert not created and same.id == note.id and notes.count() == 1
    assert notes.search("café") == [] and notes.search("chá")[0].note.id == note.id
    assert notes.delete(note.id) and not notes.delete(note.id)
    assert notes.search("chá") == []


def test_empty_query_lists_recent(notes):
    notes.save("A", "1")
    notes.save("B", "2")
    assert {h.note.title for h in notes.search("")} == {"A", "B"}


@pytest.mark.parametrize("query", ['" OR 1=1 --', "NEAR(a b)", "titulo:*", "a AND", "***", "'; DROP TABLE notes;"])
def test_query_syntax_from_model_is_harmless(notes, query):
    """Abuso: o texto do modelo nunca vira sintaxe do FTS5 nem de SQL."""
    notes.save("Segura", "a b titulo")
    notes.search(query)  # não pode lançar erro
    assert notes.count() == 1


# --------------------------------------------------------------- prévias de aprovação
def _ctx(notes):
    return ToolContext(session_id="s", workspace=None, now=datetime.now, notes=notes)


def test_previews_show_what_will_happen(notes):
    assert _save_preview(SaveNoteArgs(titulo="Nova", texto="oi"), _ctx(notes)).startswith('CRIAR a nota "Nova"')
    note, _ = notes.save("Velha", "texto antigo")
    preview = _save_preview(SaveNoteArgs(titulo="Velha", texto="texto novo"), _ctx(notes))
    assert "SUBSTITUIR" in preview and "texto antigo" in preview and "texto novo" in preview

    preview = _delete_preview(DeleteNoteArgs(id=note.id), _ctx(notes))
    assert "APAGAR" in preview and "texto antigo" in preview  # mostra o conteúdo que vai sumir
    with pytest.raises(ToolError, match="Não existe nota"):
        _delete_preview(DeleteNoteArgs(id=999), _ctx(notes))


# ------------------------------------------------------------------- pelo gateway
@pytest.fixture
def gateway(settings):
    db = Database(settings.db_path)
    registry = ToolRegistry(NOTE_TOOLS, {"notas": ["search_notes", "save_note", "delete_note"]})
    return gw.ToolGateway(settings, registry, AuditLog(db), notes=NoteStore(db))


@pytest.fixture
def session():
    return Session(model_key="local", tool_profile="notas")


class Approver:
    """Aprovador de teste: responde sempre o mesmo e guarda o que viu."""

    def __init__(self, answer: bool):
        self.answer = answer
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        return self.answer


async def _exec(gateway, session, name, args, approver=None):
    return await gateway.execute(session, ToolCall("c1", name, json.dumps(args)), step=1, approver=approver)


async def test_save_needs_approval(gateway, session):
    store = gateway.notes
    out = await _exec(gateway, session, "save_note", {"titulo": "T", "texto": "x"})
    assert out.decision == gw.NEEDS_APPROVAL and store.count() == 0

    no = Approver(False)
    out = await _exec(gateway, session, "save_note", {"titulo": "T", "texto": "x"}, no)
    assert out.decision == gw.REJECTED and store.count() == 0
    assert no.requests[0].preview.startswith('CRIAR a nota "T"')

    yes = Approver(True)
    out = await _exec(gateway, session, "save_note", {"titulo": "T", "texto": "x"}, yes)
    assert out.ok and json.loads(out.content)["acao"] == "criada" and store.count() == 1
    rec = gateway.audit.events(session.id, kind="tool_call")[-1]
    assert rec["approval"] == "approved" and "approval_wait_ms" in rec


async def test_search_needs_no_approval(gateway, session):
    gateway.notes.save("Fornecedores", "ligar para a gráfica")
    out = await _exec(gateway, session, "search_notes", {"consulta": "grafica"})
    assert out.ok and json.loads(out.content)["notas"][0]["titulo"] == "Fornecedores"


async def test_delete_missing_note_does_not_bother_user(gateway, session):
    asked = Approver(True)
    out = await _exec(gateway, session, "delete_note", {"id": 42}, asked)
    assert out.decision == gw.FAILED and "Não existe nota" in out.content
    assert asked.requests == []


async def test_delete_shows_content_and_respects_rejection(gateway, session):
    note, _ = gateway.notes.save("Senhas do wifi", "rede: escritorio")
    no = Approver(False)
    await _exec(gateway, session, "delete_note", {"id": note.id}, no)
    assert gateway.notes.get(note.id) is not None
    assert "rede: escritorio" in no.requests[0].preview and no.requests[0].risk == "destrutiva"

    await _exec(gateway, session, "delete_note", {"id": note.id}, Approver(True))
    assert gateway.notes.get(note.id) is None


async def test_note_too_long_is_invalid(gateway, session):
    out = await _exec(gateway, session, "save_note", {"titulo": "T", "texto": "x" * 20_001}, Approver(True))
    assert out.decision == gw.INVALID_ARGS and gateway.notes.count() == 0
