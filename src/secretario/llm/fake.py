"""Modelo falso, determinístico, para testes e para rodar sem LLM nenhum."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from secretario.config import ModelProfile
from secretario.core.types import Message
from secretario.llm.base import Delta, Done, LLMError, LLMEvent


class FakeClient:
    def __init__(self, profile: ModelProfile, *, reply: str | None = None, fail: LLMError | None = None):
        self.profile = profile
        self.reply = reply
        self.fail = fail
        self.calls: list[list[Message]] = []

    async def stream_chat(self, messages: Sequence[Message]) -> AsyncIterator[LLMEvent]:
        self.calls.append(list(messages))
        if self.fail:
            raise self.fail
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        text = self.reply if self.reply is not None else f"Eco: {last_user}"
        for word in text.split(" "):
            await asyncio.sleep(0)
            yield Delta(word + " ")
        yield Done(model=f"fake-{self.profile.key}", finish_reason="stop", prompt_tokens=None, completion_tokens=None)
