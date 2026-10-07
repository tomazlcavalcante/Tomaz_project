"""Raiz de composição: o único lugar que decide quais implementações usar.

Trocar o banco, o cliente de LLM ou a interface começa (e quase sempre
termina) aqui.
"""

from __future__ import annotations

import logging

from secretario.audit.log import AuditLog
from secretario.config import Settings, load_settings
from secretario.core.agent import Agent
from secretario.llm.router import ClientFactory, ModelRouter
from secretario.llm.openai_compat import OpenAICompatClient
from secretario.storage.db import ConversationStore, Database
from secretario.tools.base import Tool
from secretario.tools.builtin import BUILTIN_TOOLS
from secretario.tools.gateway import ToolGateway
from secretario.tools.registry import ToolRegistry


def setup_logging(settings: Settings) -> None:
    log_dir = settings.data_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "secretario.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("secretario")
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Bibliotecas HTTP em INFO registram URLs; o suficiente é WARNING.
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def build_agent(
    settings: Settings | None = None,
    client_factory: ClientFactory = OpenAICompatClient,
    tools: list[Tool] | None = None,
) -> Agent:
    settings = settings or load_settings()
    setup_logging(settings)
    settings.workspace_path.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    audit = AuditLog(db)
    registry = ToolRegistry.from_settings(settings.tools, BUILTIN_TOOLS if tools is None else tools)
    return Agent(
        settings=settings,
        router=ModelRouter(settings, client_factory),
        store=ConversationStore(db),
        audit=audit,
        tools=registry,
        gateway=ToolGateway(settings, registry, audit),
    )
