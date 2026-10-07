"""Estado de uma conversa: histórico, modelo preferido, rótulo de sigilo e perfil de ferramentas."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from secretario.core.types import Label, Message, utcnow


class LabelError(ValueError):
    pass


@dataclass
class Session:
    model_key: str
    label: Label = Label.PUBLICO
    tool_profile: str = "nenhum"  # sem ferramentas, a menos que a configuração diga outra coisa
    # Contaminada: algum conteúdo não confiável (arquivo, e-mail, web) entrou na conversa.
    # Como o rótulo de sigilo, só vai de False para True.
    tainted: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=utcnow)
    messages: list[Message] = field(default_factory=list)

    def add(self, message: Message) -> Message:
        self.messages.append(message)
        return message

    def raise_label(self, new: Label) -> bool:
        """Sobe o rótulo. Retorna True se mudou.

        Nunca desce: depois que um dado sigiloso entrou no contexto, ele
        continua lá. Para "baixar", abra uma conversa nova.
        """
        if new < self.label:
            raise LabelError(
                f"A sessão já está em '{self.label.nome}'. O nível de sigilo só sobe; "
                "para voltar a um nível menor, comece uma conversa nova."
            )
        changed = new != self.label
        self.label = new
        return changed
