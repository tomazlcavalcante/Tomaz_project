"""Gateway de políticas: cada etapa recusa o que deve, e tudo vai para a auditoria."""

from __future__ import annotations

import json
import time

import pytest
from pydantic import BaseModel, ConfigDict

from secretario.audit.log import AuditLog
from secretario.core.session import Session
from secretario.core.types import ToolCall
from secretario.storage.db import Database
from secretario.tools import gateway as gw
from secretario.tools.base import NoArgs, Risk, Tool, ToolError
from secretario.tools.builtin import BUILTIN_TOOLS
from secretario.tools.registry import ToolRegistry


class NoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    titulo: str
    texto: str = ""


EXECUTED_WRITES: list[str] = []


def _save_note(args: NoteArgs, ctx) -> str:
    EXECUTED_WRITES.append(args.titulo)
    return "salvo"


def _slow(args, ctx) -> str:
    time.sleep(0.5)
    return "tarde demais"


def _fails(args, ctx) -> str:
    raise ToolError("a pasta sumiu")


def _explodes(args, ctx) -> str:
    raise RuntimeError("bug interno com /caminho/secreto")


def _big(args, ctx) -> str:
    return "x" * 10_000


TEST_TOOLS = [
    *BUILTIN_TOOLS,
    Tool("save_note", "Salva uma nota.", NoteArgs, _save_note, Risk.WRITE),
    Tool("slow", "Demora.", NoArgs, _slow, Risk.NONE, timeout_s=0.05),
    Tool("fails", "Falha.", NoArgs, _fails, Risk.NONE),
    Tool("explodes", "Explode.", NoArgs, _explodes, Risk.NONE),
    Tool("big", "Resultado grande.", NoArgs, _big, Risk.NONE),
    Tool("fora_do_perfil", "Existe, mas não está no perfil.", NoArgs, lambda a, c: "ok", Risk.NONE),
]
PROFILE = ["get_datetime", "list_files", "save_note", "slow", "fails", "explodes", "big"]


@pytest.fixture
def audit(settings):
    return AuditLog(Database(settings.db_path))


@pytest.fixture
def make_gateway(settings, audit):
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    registry = ToolRegistry(TEST_TOOLS, {"teste": PROFILE, "nenhum": []})

    def make(approver=None):
        return gw.ToolGateway(settings, registry, audit, approver=approver)

    return make


@pytest.fixture
def session():
    return Session(model_key="local", tool_profile="teste")


@pytest.fixture(autouse=True)
def _clear_writes():
    EXECUTED_WRITES.clear()


def _call(name, args="{}"):
    return ToolCall(id="c1", name=name, arguments=args if isinstance(args, str) else json.dumps(args))


async def _run(gateway, session, name, args="{}"):
    return await gateway.execute(session, _call(name, args), step=1)


def _last_audit(audit, session):
    return audit.events(session.id, kind="tool_call")[-1]


async def test_executes_and_audits(make_gateway, session, audit):
    out = await _run(make_gateway(), session, "list_files", {"path": ""})
    assert out.ok and out.decision == gw.EXECUTED
    assert json.loads(out.content)["pasta"] == "."
    rec = _last_audit(audit, session)
    assert rec["tool"] == "list_files" and rec["decision"] == "executed" and rec["ok"] is True
    assert rec["args"] == {"path": ""} and rec["risk"] == "leitura" and rec["step"] == 1
    assert rec["duration_ms"] >= 0 and rec["result_chars"] == len(out.content)


async def test_unknown_tool(make_gateway, session, audit):
    out = await _run(make_gateway(), session, "rm_rf")
    assert not out.ok and out.decision == gw.UNKNOWN_TOOL and out.content.startswith("ERRO")
    assert _last_audit(audit, session)["decision"] == gw.UNKNOWN_TOOL


async def test_tool_outside_profile_is_denied(make_gateway, session):
    out = await _run(make_gateway(), session, "fora_do_perfil")
    assert out.decision == gw.NOT_IN_PROFILE

    session.tool_profile = "nenhum"
    out = await _run(make_gateway(), session, "get_datetime")
    assert out.decision == gw.NOT_IN_PROFILE


@pytest.mark.parametrize(
    "args, expected",
    [
        ("{nao é json", "JSON válido"),
        ("[1, 2]", "objeto JSON"),
        ({"path": 123}, "'path'"),
        ({"path": "", "extra": 1}, "'extra' não existe"),
    ],
)
async def test_invalid_args_go_back_to_model(make_gateway, session, args, expected):
    out = await _run(make_gateway(), session, "list_files", args)
    assert out.decision == gw.INVALID_ARGS
    assert expected in out.content


async def test_missing_required_arg(make_gateway, session):
    out = await _run(make_gateway(), session, "save_note", {})
    assert out.decision == gw.INVALID_ARGS and "falta o argumento 'titulo'" in out.content


async def test_empty_arguments_mean_no_args(make_gateway, session):
    out = await _run(make_gateway(), session, "get_datetime", "")
    assert out.ok


async def test_write_tool_never_runs_without_approver(make_gateway, session, audit):
    """Abuso: mesmo no perfil e com argumentos válidos, escrita sem aprovação não executa."""
    out = await _run(make_gateway(), session, "save_note", {"titulo": "x"})
    assert out.decision == gw.NEEDS_APPROVAL and "aprovação" in out.content
    assert EXECUTED_WRITES == []
    assert _last_audit(audit, session)["risk"] == "escrita"


async def test_approver_decides_with_full_arguments(make_gateway, session):
    seen = []

    async def reject(request):
        seen.append((request.tool, request.arguments, request.risk))
        return False

    async def approve(request):
        return True

    out = await _run(make_gateway(reject), session, "save_note", {"titulo": "x", "texto": "corpo"})
    assert out.decision == gw.REJECTED and EXECUTED_WRITES == []
    assert seen == [("save_note", {"titulo": "x", "texto": "corpo"}, "escrita")]

    out = await _run(make_gateway(approve), session, "save_note", {"titulo": "x"})
    assert out.ok and EXECUTED_WRITES == ["x"]


async def test_timeout(make_gateway, session):
    out = await _run(make_gateway(), session, "slow")
    assert out.decision == gw.TIMEOUT and "tempo limite" in out.content
    assert out.duration_s < 0.4  # não esperou a ferramenta terminar


async def test_expected_failure_message_goes_to_model(make_gateway, session, audit):
    out = await _run(make_gateway(), session, "fails")
    assert out.decision == gw.FAILED and out.content == "ERRO: a pasta sumiu"
    assert _last_audit(audit, session)["error_kind"] == "tool_error"


async def test_unexpected_exception_does_not_leak_details(make_gateway, session, audit):
    out = await _run(make_gateway(), session, "explodes")
    assert out.decision == gw.FAILED
    assert "/caminho/secreto" not in out.content
    assert _last_audit(audit, session)["error_kind"] == "RuntimeError"


async def test_big_result_is_cut(make_gateway, session, settings, audit):
    out = await _run(make_gateway(), session, "big")
    limit = settings.tools.max_result_chars
    assert out.ok and out.content.startswith("x" * limit) and "resultado cortado: 10000" in out.content
    assert _last_audit(audit, session)["truncated"] is True


async def test_audit_keeps_long_arguments_out(make_gateway, session, audit):
    texto = "conteúdo confidencial " * 50

    async def approve(request):
        return True

    await _run(make_gateway(approve), session, "save_note", {"titulo": "curto", "texto": texto})
    rec = _last_audit(audit, session)
    assert rec["args"]["titulo"] == "curto"
    assert rec["args"]["texto"] == f"<texto com {len(texto)} caracteres>"
    assert "confidencial" not in json.dumps(rec, ensure_ascii=False)
