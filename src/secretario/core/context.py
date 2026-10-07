"""Monta o que vai para o modelo em cada chamada.

O contexto é reconstruído a cada passo, não acumulado: prompt de sistema
+ as mensagens mais recentes que couberem no orçamento do modelo + as
mensagens de ferramenta do turno em andamento. Na V2 entram aqui o resumo
rolante e os fatos recuperados da memória.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from secretario.core.session import Session
from secretario.core.types import Message

TRUNCATION_MARK = "\n\n[... trecho omitido por exceder o limite de contexto ...]\n\n"


@dataclass
class BuiltContext:
    messages: list[Message]
    dropped: int  # mensagens antigas que ficaram de fora
    truncated_last: bool  # a última mensagem precisou ser cortada

    @property
    def chars(self) -> int:
        return sum(m.size for m in self.messages)


class ContextOverflow(ValueError):
    """Nem o prompt de sistema e as mensagens obrigatórias do turno cabem no limite do modelo."""


WEEKDAYS = ("segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo")


def render_system_prompt(base: str, now: datetime) -> str:
    # Sem strftime("%A"): o nome do dia dependeria do idioma do sistema operacional.
    stamp = f"{WEEKDAYS[now.weekday()]}, {now:%d/%m/%Y %H:%M}"
    return f"{base}\n\nData e hora atuais (do computador do usuário): {stamp}."


def build_context(
    session: Session,
    system_prompt: str,
    *,
    max_chars: int,
    max_messages: int,
    pending: Sequence[Message] = (),
    reserved_chars: int = 0,
) -> BuiltContext:
    """`pending`: pedidos e resultados de ferramenta do turno atual; vão inteiros, no fim.
    `reserved_chars`: espaço já ocupado por outras coisas (ex.: a descrição das ferramentas).
    """
    system = Message(role="system", content=system_prompt)
    budget = max_chars - len(system.content) - reserved_chars
    if budget <= 0:
        raise ContextOverflow("O prompt de sistema sozinho já excede o limite de contexto do modelo.")
    budget -= sum(m.size for m in pending)
    if budget <= 0:
        raise ContextOverflow(
            "Os resultados das ferramentas deste turno passaram do limite de contexto do modelo."
        )

    all_history = [m for m in session.messages if m.role != "system"]
    history = all_history[-max_messages:]

    kept: list[Message] = []
    used = 0
    truncated_last = False
    for msg in reversed(history):
        size = msg.size
        if used + size <= budget:
            kept.append(msg)
            used += size
            continue
        if not kept:
            # A mensagem mais recente sozinha não cabe: corta o meio dela,
            # preservando começo e fim, que costumam ter o pedido.
            room = max(budget - len(TRUNCATION_MARK), 0)
            head, tail = room // 2, room - room // 2
            text = msg.content[:head] + TRUNCATION_MARK + (msg.content[-tail:] if tail else "")
            kept.append(Message(role=msg.role, content=text, created_at=msg.created_at))
            truncated_last = True
        break

    kept.reverse()
    # Garante que o histórico enviado comece por uma mensagem do usuário:
    # alguns provedores rejeitam conversas que começam pelo assistente.
    while kept and kept[0].role != "user":
        kept.pop(0)

    return BuiltContext(
        messages=[system, *kept, *pending],
        dropped=len(all_history) - len(kept),
        truncated_last=truncated_last,
    )
