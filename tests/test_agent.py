from __future__ import annotations

import asyncio
from contextlib import aclosing

import pytest

from secretario.core.types import Label, Notice, TextDelta, TurnDone, TurnError
from secretario.llm.base import LLMError
from secretario.llm.fake import FakeClient

from .conftest import collect


async def test_turn_streams_persists_and_audits(agent, fakes):
    session = agent.new_session()
    events = await collect(agent.run_turn(session, "olá mundo"))

    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text.strip() == "Eco: olá mundo"
    done = [e for e in events if isinstance(e, TurnDone)]
    assert len(done) == 1 and done[0].is_local and done[0].model_key == "local"

    # O modelo recebeu o prompt de sistema + a mensagem do usuário
    sent = fakes["local"].calls[0]
    assert sent[0].role == "system" and "Secretário" in sent[0].content
    assert sent[-1].content == "olá mundo"

    # Persistência: recarregar do banco devolve a mesma conversa
    reloaded = agent.store.load_session(session.id)
    assert [(m.role, m.content) for m in reloaded.messages] == [
        ("user", "olá mundo"),
        ("assistant", "Eco: olá mundo"),
    ]

    calls = agent.audit.events(session.id, kind="llm_call")
    assert len(calls) == 1
    assert calls[0]["ok"] is True and calls[0]["is_local"] is True and calls[0]["label"] == "público"
    # A auditoria não guarda conteúdo
    assert "olá mundo" not in str(calls[0])


async def test_second_turn_sends_history(agent, fakes):
    session = agent.new_session()
    await collect(agent.run_turn(session, "primeira"))
    await collect(agent.run_turn(session, "segunda"))
    sent = fakes["local"].calls[1]
    assert [m.content for m in sent[1:]] == ["primeira", "Eco: primeira", "segunda"]


async def test_external_model_used_only_while_public(agent, fakes):
    session = agent.new_session()
    agent.handle_command(session, "/modelo gemini")

    events = await collect(agent.run_turn(session, "dado público"))
    assert [e for e in events if isinstance(e, TurnDone)][0].model_key == "gemini"
    assert not any(isinstance(e, Notice) for e in events)

    reply = agent.handle_command(session, "/sigilo confidencial")
    assert "confidencial" in reply
    events = await collect(agent.run_turn(session, "dado confidencial"))
    done = [e for e in events if isinstance(e, TurnDone)][0]
    assert done.model_key == "local" and done.is_local
    assert any(isinstance(e, Notice) for e in events)

    # O Gemini nunca recebeu a mensagem confidencial
    assert all("dado confidencial" not in m.content for call in fakes["gemini"].calls for m in call)
    assert agent.audit.events(session.id, kind="route_fallback")


async def test_label_only_goes_up(agent):
    session = agent.new_session()
    agent.handle_command(session, "/sigilo interno")
    reply = agent.handle_command(session, "/sigilo publico")
    assert "só sobe" in reply
    assert session.label == Label.INTERNO
    assert agent.store.load_session(session.id).label == Label.INTERNO


async def test_refuses_when_no_model_allowed(agent, settings):
    settings.models["local"].max_label = Label.INTERNO
    session = agent.new_session()
    agent.handle_command(session, "/sigilo restrito")
    events = await collect(agent.run_turn(session, "x"))
    assert isinstance(events[-1], TurnError)
    assert agent.audit.events(session.id, kind="route_refused")


async def test_llm_error_becomes_friendly_event(agent, fakes):
    session = agent.new_session()
    profile = agent.settings.models["local"]
    agent.router._clients["local"] = FakeClient(profile, fail=LLMError("Ollama fora do ar", kind="connection"))
    events = await collect(agent.run_turn(session, "oi"))
    assert isinstance(events[-1], TurnError) and "Ollama" in events[-1].message
    call = agent.audit.events(session.id, kind="llm_call")[0]
    assert call["ok"] is False and call["error_kind"] == "connection"


async def test_cancellation_keeps_partial_answer(agent):
    """Botão "parar": a tarefa é cancelada enquanto a resposta chega."""
    session = agent.new_session()
    task = asyncio.create_task(_consume_slowly(agent, session))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    call = agent.audit.events(session.id, kind="llm_call")[-1]
    assert call["ok"] is False and call["error_kind"] == "cancelled"
    last = agent.store.load_session(session.id).messages[-1]
    assert last.role == "assistant" and last.content.endswith("[interrompido]")


async def _consume_slowly(agent, session):
    async with aclosing(agent.run_turn(session, "texto longo " * 50)) as events:
        async for _ in events:
            await asyncio.sleep(0.01)


def test_commands(agent):
    session = agent.new_session()
    assert "/modelo" in agent.handle_command(session, "/ajuda")
    assert "EXTERNO" in agent.handle_command(session, "/modelos")
    assert "desconhecido" in agent.handle_command(session, "/modelo nao-existe")
    assert "sair da sua máquina" in agent.handle_command(session, "/modelo gemini")
    assert "Gemini" in agent.handle_command(session, "/status")
    assert "desconhecido" in agent.handle_command(session, "/xyz")
    assert "sem título" in agent.handle_command(session, "/historico") or "Conversas" in agent.handle_command(session, "/historico")


def test_secret_values_cannot_reach_audit(agent):
    with pytest.raises(ValueError):
        agent.audit.record("teste", None, api_key="abc")
    with pytest.raises(ValueError):
        agent.audit.record("teste", None, content="texto da conversa")
