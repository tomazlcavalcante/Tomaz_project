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


Role = Literal["system", "user", "assistant", "tool"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ToolCall:
    """Pedido de ferramenta feito pelo modelo. `arguments` é o JSON cru, ainda não validado."""

    id: str
    name: str
    arguments: str

    def to_api(self) -> dict:
        return {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.arguments}}


@dataclass
class Message:
    role: Role
    content: str
    created_at: datetime = field(default_factory=utcnow)
    model_key: str | None = None  # qual perfil de modelo gerou (só para assistant)
    # Só nas mensagens do turno em andamento (não vão para o banco):
    tool_calls: list[ToolCall] = field(default_factory=list)  # assistant pedindo ferramentas
    tool_call_id: str | None = None  # resposta de ferramenta (role "tool")

    @property
    def size(self) -> int:
        """Caracteres que esta mensagem ocupa no contexto do modelo."""
        return len(self.content) + sum(len(c.name) + len(c.arguments) for c in self.tool_calls)

    def to_api(self) -> dict:
        """Formato aceito pelas APIs compatíveis com OpenAI."""
        if self.tool_calls:
            # Alguns provedores rejeitam texto vazio junto com pedidos de ferramenta.
            return {
                "role": self.role,
                "content": self.content or None,
                "tool_calls": [c.to_api() for c in self.tool_calls],
            }
        if self.tool_call_id is not None:
            return {"role": self.role, "content": self.content, "tool_call_id": self.tool_call_id}
        return {"role": self.role, "content": self.content}


@dataclass
class ApprovalRequest:
    """O que o usuário vê antes de aprovar uma ação de risco: tudo, sem resumir."""

    session_id: str
    tool: str
    risk: str
    arguments: dict  # argumentos completos, já validados
    preview: str  # o que vai acontecer, descrito pela própria ferramenta
    tainted: bool  # a conversa já leu conteúdo não confiável (arquivo, e-mail, web)


# ---------------------------------------------------------------------------
# Eventos que o núcleo emite para a interface (UI web, CLI ou testes).
# O pedido de aprovação não é um evento: a interface entrega ao turno uma
# função que pergunta ao usuário (ver Approver em tools/gateway.py).
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
class ToolStarted:
    """O modelo pediu uma ferramenta; o gateway vai decidir se ela roda."""

    call_id: str
    name: str
    arguments: str


@dataclass
class ToolFinished:
    call_id: str
    name: str
    ok: bool
    decision: str  # ex.: "executed", "invalid_args", "denied_not_in_profile" (ver tools/gateway.py)
    output: str  # o que o modelo recebeu de volta
    duration_s: float


@dataclass
class TurnDone:
    model_key: str
    model_display: str
    is_local: bool
    latency_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str | None
    tool_calls: int = 0  # quantas ferramentas foram pedidas no turno


@dataclass
class TurnError:
    message: str


AgentEvent = Notice | TextDelta | ToolStarted | ToolFinished | TurnDone | TurnError
