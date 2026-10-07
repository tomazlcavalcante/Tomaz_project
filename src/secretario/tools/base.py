"""O que é uma ferramenta.

Uma ferramenta é uma função Python comum (síncrona: sem async) mais o que
o modelo e o gateway precisam saber sobre ela:

- nome e descrição, que o modelo lê para decidir quando usá-la;
- um modelo Pydantic com os argumentos, que vira o JSON Schema enviado ao
  modelo e valida o que ele pedir;
- o nível de risco, que decide se precisa de aprovação humana.

Exemplo:

    class Args(BaseModel):
        path: str = ""

    def listar(args: Args, ctx: ToolContext) -> dict:
        ...

    LISTAR = Tool(name="list_files", description="...", args_model=Args, func=listar, risk=Risk.READ)

A função nunca é chamada diretamente: quem a executa é o gateway.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from secretario.storage.notes import NoteStore


class Risk(StrEnum):
    NONE = "nenhum"  # não lê nem altera dados (ex.: data e hora)
    READ = "leitura"  # lê dados locais confiáveis (ex.: nomes de arquivos)
    READ_UNTRUSTED = "leitura_nao_confiavel"  # traz conteúdo que pode conter instruções maliciosas
    WRITE = "escrita"  # cria ou altera dados
    DESTRUCTIVE = "destrutiva"  # apaga dados


# Riscos que sempre exigem aprovação humana. Não há como uma ferramenta
# dispensar isso: a regra é do risco, não de quem escreve a ferramenta.
NEEDS_APPROVAL = {Risk.WRITE, Risk.DESTRUCTIVE}


class ToolError(Exception):
    """Falha esperada (pasta inexistente, caminho proibido...). A mensagem vai para o modelo."""


class NoArgs(BaseModel):
    """Argumentos de uma ferramenta que não recebe nada."""

    model_config = ConfigDict(extra="forbid")


@dataclass
class ToolContext:
    """O que uma ferramenta pode saber do mundo. Ela não recebe a sessão inteira de propósito."""

    session_id: str
    workspace: Path
    now: Callable[[], datetime]
    notes: NoteStore | None = None
    max_chars: int = 4000  # tamanho máximo do resultado que vai ao modelo


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    func: Callable[[Any, ToolContext], Any]  # recebe (argumentos validados, contexto); devolve str, dict ou list
    risk: Risk
    timeout_s: float | None = None  # None = o padrão de [tools] timeout_s
    # Para ferramentas com aprovação: descreve, para o usuário, o que vai acontecer
    # (ex.: o texto da nota que será apagada). Pode lançar ToolError, e aí nem se pergunta.
    preview: Callable[[Any, ToolContext], str] | None = None

    @property
    def needs_approval(self) -> bool:
        return self.risk in NEEDS_APPROVAL

    @property
    def untrusted(self) -> bool:
        """Traz conteúdo de fora (arquivo, e-mail, web): contamina a conversa."""
        return self.risk == Risk.READ_UNTRUSTED

    def input_schema(self) -> dict[str, Any]:
        return _strip_titles(self.args_model.model_json_schema())

    def spec(self) -> dict[str, Any]:
        """Formato do MCP (Model Context Protocol): nome, descrição e JSON Schema."""
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema()}

    def openai_spec(self) -> dict[str, Any]:
        """O mesmo conteúdo, no formato das APIs compatíveis com OpenAI (Ollama, Gemini)."""
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.input_schema()},
        }


def _strip_titles(schema: Any) -> Any:
    # O Pydantic põe "title" em tudo; para o modelo é só ruído que gasta contexto.
    # Cuidado: um campo chamado "title" fica dentro de "properties" e não pode sumir.
    if isinstance(schema, dict):
        return {
            k: (
                {name: _strip_titles(sub) for name, sub in v.items()}
                if k == "properties"
                else _strip_titles(v)
            )
            for k, v in schema.items()
            if k != "title"
        }
    if isinstance(schema, list):
        return [_strip_titles(v) for v in schema]
    return schema
