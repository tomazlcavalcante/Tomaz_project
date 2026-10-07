"""Modelo falso, determinístico, para testes e para rodar sem LLM nenhum."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from secretario.config import ModelProfile
from secretario.core.types import Message, ToolCall
from secretario.llm.base import Delta, Done, LLMError, LLMEvent, ToolRequest

# Um passo do roteiro: texto de resposta ou uma lista de pedidos de ferramenta.
ScriptStep = str | list[ToolCall]


class FakeClient:
    """Sem roteiro, responde "Eco: <última mensagem do usuário>".

    Com `script`, cada chamada consome o próximo passo: um texto vira a
    resposta; uma lista de ToolCall vira um pedido de ferramentas. Quando o
    roteiro acaba, volta ao eco.
    """

    def __init__(
        self,
        profile: ModelProfile,
        *,
        reply: str | None = None,
        fail: LLMError | None = None,
        script: list[ScriptStep] | None = None,
    ):
        self.profile = profile
        self.reply = reply
        self.fail = fail
        self.script = list(script or [])
        self.calls: list[list[Message]] = []
        self.tools_offered: list[list[dict[str, Any]] | None] = []

    async def stream_chat(
        self, messages: Sequence[Message], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append(list(messages))
        self.tools_offered.append(tools)
        if self.fail:
            raise self.fail
        step = self.script.pop(0) if self.script else None
        if isinstance(step, list):
            yield ToolRequest(step)
            yield Done(model=f"fake-{self.profile.key}", finish_reason="tool_calls", prompt_tokens=None, completion_tokens=None)
            return
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        text = step if step is not None else self.reply if self.reply is not None else f"Eco: {last_user}"
        for word in text.split(" "):
            await asyncio.sleep(0)
            yield Delta(word + " ")
        yield Done(model=f"fake-{self.profile.key}", finish_reason="stop", prompt_tokens=None, completion_tokens=None)
