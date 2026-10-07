"""Contrato do cliente de LLM.

Qualquer implementação (Ollama, Gemini, um modelo falso nos testes) só
precisa produzir esta sequência de eventos: zero ou mais `Delta`, no
máximo um `ToolRequest` (quando o modelo pede ferramentas) e, no fim,
exatamente um `Done`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from secretario.config import ModelProfile
from secretario.core.types import Message, ToolCall


@dataclass
class Delta:
    text: str


@dataclass
class Done:
    model: str  # nome do modelo informado pelo provedor
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass
class ToolRequest:
    """O modelo pediu uma ou mais ferramentas (os argumentos ainda não foram validados)."""

    calls: list[ToolCall]


LLMEvent = Delta | ToolRequest | Done


class LLMError(Exception):
    """Erro de chamada ao modelo, já com mensagem amigável em português."""

    def __init__(self, message: str, *, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind


class LLMClient(Protocol):
    profile: ModelProfile

    def stream_chat(
        self, messages: Sequence[Message], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[LLMEvent]:  # pragma: no cover - contrato
        """`tools`: ferramentas oferecidas, no formato OpenAI; None = nenhuma."""
        ...
