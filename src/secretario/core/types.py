"""Tipos compartilhados por todos os componentes.

Nada aqui depende de LLM, UI ou banco: são só estruturas de dados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Literal


class Label(IntEnum):
    """Nível de sigilo. A ordem importa: um número maior é mais sigiloso.

    O rótulo de uma sessão só sobe (ver Session.raise_label). Cada modelo
    declara o rótulo máximo de dado que pode receber.
    """

    PUBLICO = 0
    INTERNO = 1
    CONFIDENCIAL = 2
    RESTRITO = 3

    @classmethod
    def parse(cls, value: str | int | Label) -> Label:
        if isinstance(value, Label):
            return value
        if isinstance(value, int):
            return cls(value)
        key = value.strip().upper()
        # aceita com ou sem acento
        key = key.replace("Ú", "U")
        try:
            return cls[key]
        except KeyError as exc:
            names = ", ".join(n.lower() for n in cls.__members__)
            raise ValueError(f"Nível de sigilo desconhecido: {value!r}. Use: {names}.") from exc

    @property
    def nome(self) -> str:
        return {0: "público", 1: "interno", 2: "confidencial", 3: "restrito"}[int(self)]


Role = Literal["system", "user", "assistant"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Message:
    role: Role
    content: str
    created_at: datetime = field(default_factory=utcnow)
    model_key: str | None = None  # qual perfil de modelo gerou (só para assistant)

    def to_api(self) -> dict[str, str]:
        """Formato aceito pelas APIs compatíveis com OpenAI."""
        return {"role": self.role, "content": self.content}


# ---------------------------------------------------------------------------
# Eventos que o núcleo emite para a interface (UI web, CLI ou testes).
# A V1 vai acrescentar eventos de ferramenta e de pedido de aprovação.
# ---------------------------------------------------------------------------


@dataclass
class Notice:
    """Aviso ao usuário que não faz parte da resposta do modelo."""

    text: str


@dataclass
class TextDelta:
    """Pedaço de texto da resposta, para exibição em streaming."""

    text: str


@dataclass
class TurnDone:
    model_key: str
    model_display: str
    is_local: bool
    latency_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str | None


@dataclass
class TurnError:
    message: str


AgentEvent = Notice | TextDelta | TurnDone | TurnError
