"""Testa o cliente real contra um servidor falso que fala o protocolo da OpenAI.

Cobre o caminho completo de rede (SDK openai → HTTP → streaming SSE) sem
precisar de Ollama nem de chave de API.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from secretario.config import ModelProfile
from secretario.core.types import Message, ToolCall
from secretario.llm.base import Delta, Done, LLMError, ToolRequest
from secretario.llm.openai_compat import OpenAICompatClient

RECEIVED: list[dict] = []


def _chunk(content=None, finish=None, usage=None, tool_calls=None):
    delta = {}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = tool_calls
    body = {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "modelo-falso",
        "choices": [] if usage else [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n"


async def chat(request: Request):
    payload = await request.json()
    RECEIVED.append({"body": payload, "auth": request.headers.get("authorization")})
    model = payload["model"]
    if model == "inexistente":
        return JSONResponse({"error": {"message": "model not found"}}, status_code=404)
    if model == "limitado":
        return JSONResponse({"error": {"message": "rate limit"}}, status_code=429)
    if model == "ferramenta-em-pedacos":  # estilo OpenAI: argumentos fragmentados
        pieces = [
            _chunk(tool_calls=[{"index": 0, "id": "call_a", "type": "function", "function": {"name": "list_files", "arguments": ""}}]),
            _chunk(tool_calls=[{"index": 0, "function": {"arguments": '{"pa'}}]),
            _chunk(tool_calls=[{"index": 0, "function": {"arguments": 'th": "rel"}'}}]),
            _chunk(tool_calls=[{"index": 1, "id": "call_b", "type": "function", "function": {"name": "get_datetime", "arguments": "{}"}}]),
            _chunk(finish="tool_calls"),
            "data: [DONE]\n\n",
        ]
        return StreamingResponse(iter(pieces), media_type="text/event-stream")
    if model == "ferramenta-sem-indice":  # cada pedido inteiro num pedaço, sem "index" nem "id"
        pieces = [
            _chunk(content="Vou olhar."),
            _chunk(tool_calls=[{"type": "function", "function": {"name": "list_files", "arguments": "{}"}}]),
            _chunk(tool_calls=[{"type": "function", "function": {"name": "get_datetime", "arguments": "{}"}}]),
            _chunk(finish="tool_calls"),
            "data: [DONE]\n\n",
        ]
        return StreamingResponse(iter(pieces), media_type="text/event-stream")

    async def gen():
        for piece in ["<thi", "nk>pensando</th", "ink>Olá", ", mundo", "!"]:
            yield _chunk(piece)
        yield _chunk(finish="stop")
        yield _chunk(usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15})
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@pytest.fixture(scope="module")
def server_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = Starlette(routes=[Route("/v1/chat/completions", chat, methods=["POST"])])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    thread.join(timeout=5)


def _profile(url, **kw):
    base = dict(display_name="Falso", base_url=url, model="m", max_label="restrito", reasoning_effort="none", max_output_tokens=50)
    base.update(kw)
    return ModelProfile(**base)


async def _run(client):
    return [e async for e in client.stream_chat([Message(role="system", content="s"), Message(role="user", content="oi")])]


async def test_streaming_strips_thinking_and_reads_usage(server_url):
    RECEIVED.clear()
    events = await _run(OpenAICompatClient(_profile(server_url)))
    text = "".join(e.text for e in events if isinstance(e, Delta))
    assert text == "Olá, mundo!"
    done = events[-1]
    assert isinstance(done, Done)
    assert (done.prompt_tokens, done.completion_tokens, done.finish_reason) == (12, 3, "stop")

    body = RECEIVED[-1]["body"]
    assert body["stream"] is True and body["reasoning_effort"] == "none" and body["max_tokens"] == 50
    assert body["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "oi"}]


async def test_api_key_comes_from_secret_store(server_url, monkeypatch):
    RECEIVED.clear()
    monkeypatch.setenv("CHAVE_TESTE", "segredo-123")
    monkeypatch.setattr("secretario.secrets._keyring", lambda: (None, Exception))
    await _run(OpenAICompatClient(_profile(server_url, api_key_secret="CHAVE_TESTE")))
    assert RECEIVED[-1]["auth"] == "Bearer segredo-123"


async def test_missing_secret_is_friendly(server_url, monkeypatch):
    monkeypatch.delenv("NAO_EXISTE", raising=False)
    monkeypatch.setattr("secretario.secrets._keyring", lambda: (None, Exception))
    with pytest.raises(LLMError, match="secretario-secrets set NAO_EXISTE"):
        await _run(OpenAICompatClient(_profile(server_url, api_key_secret="NAO_EXISTE")))


async def test_model_not_installed_hint(server_url):
    with pytest.raises(LLMError, match="ollama pull inexistente") as info:
        await _run(OpenAICompatClient(_profile(server_url, model="inexistente")))
    assert info.value.kind == "not_found"


async def test_rate_limit_hint(server_url):
    with pytest.raises(LLMError, match="Limite de uso") as info:
        await _run(OpenAICompatClient(_profile(server_url, model="limitado")))
    assert info.value.kind == "rate_limit"


async def test_connection_refused_hint():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # porta livre: ninguém escutando
    client = OpenAICompatClient(_profile(f"http://127.0.0.1:{port}/v1", timeout_s=5))
    with pytest.raises(LLMError, match="Ollama está aberto") as info:
        await _run(client)
    assert info.value.kind == "connection"


TOOLS = [{"type": "function", "function": {"name": "list_files", "description": "d", "parameters": {"type": "object"}}}]


async def test_tool_calls_assembled_from_fragments(server_url):
    RECEIVED.clear()
    client = OpenAICompatClient(_profile(server_url, model="ferramenta-em-pedacos"))
    events = [e async for e in client.stream_chat([Message(role="user", content="liste")], tools=TOOLS)]
    request = next(e for e in events if isinstance(e, ToolRequest))
    assert request.calls == [
        ToolCall(id="call_a", name="list_files", arguments='{"path": "rel"}'),
        ToolCall(id="call_b", name="get_datetime", arguments="{}"),
    ]
    assert isinstance(events[-1], Done) and events[-1].finish_reason == "tool_calls"
    assert RECEIVED[-1]["body"]["tools"] == TOOLS


async def test_tool_calls_without_index_or_id(server_url):
    client = OpenAICompatClient(_profile(server_url, model="ferramenta-sem-indice"))
    events = [e async for e in client.stream_chat([Message(role="user", content="liste")], tools=TOOLS)]
    assert "".join(e.text for e in events if isinstance(e, Delta)) == "Vou olhar."
    request = next(e for e in events if isinstance(e, ToolRequest))
    assert [(c.id, c.name) for c in request.calls] == [("call_0", "list_files"), ("call_1", "get_datetime")]


async def test_no_tools_key_when_none_offered(server_url):
    RECEIVED.clear()
    await _run(OpenAICompatClient(_profile(server_url)))
    assert "tools" not in RECEIVED[-1]["body"]


async def test_tool_messages_serialized_for_api(server_url):
    RECEIVED.clear()
    call = ToolCall(id="call_a", name="list_files", arguments="{}")
    messages = [
        Message(role="user", content="liste"),
        Message(role="assistant", content="", tool_calls=[call]),
        Message(role="tool", content='{"itens": []}', tool_call_id="call_a"),
    ]
    [e async for e in OpenAICompatClient(_profile(server_url)).stream_chat(messages)]
    sent = RECEIVED[-1]["body"]["messages"]
    assert sent[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_a", "type": "function", "function": {"name": "list_files", "arguments": "{}"}}],
    }
    assert sent[2] == {"role": "tool", "content": '{"itens": []}', "tool_call_id": "call_a"}
