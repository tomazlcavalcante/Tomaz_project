"""Escolhe o modelo de cada chamada pelo rótulo de sigilo da sessão.

Regra: o modelo preferido da sessão só é usado se puder receber dados do
nível atual. Se não puder, cai no modelo local de reserva. Se nem ele
puder, a chamada é recusada. Essa decisão é feita em código, a cada
chamada, e fica registrada na auditoria.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from secretario.config import ModelProfile, Settings
from secretario.core.session import Session
from secretario.llm.base import LLMClient
from secretario.llm.openai_compat import OpenAICompatClient


class RouteError(Exception):
    pass


@dataclass
class RouteDecision:
    profile: ModelProfile
    fell_back: bool
    reason: str | None = None


ClientFactory = Callable[[ModelProfile], LLMClient]


class ModelRouter:
    def __init__(self, settings: Settings, client_factory: ClientFactory = OpenAICompatClient):
        self.profiles = settings.models
        self.fallback_key = settings.app.sensitive_fallback_model
        self._factory = client_factory
        self._clients: dict[str, LLMClient] = {}

    def route(self, session: Session) -> RouteDecision:
        preferred = self.profiles[session.model_key]
        if preferred.max_label >= session.label:
            return RouteDecision(preferred, fell_back=False)

        fallback = self.profiles[self.fallback_key]
        if fallback.max_label >= session.label:
            return RouteDecision(
                fallback,
                fell_back=True,
                reason=(
                    f"A conversa está no nível '{session.label.nome}' e "
                    f"'{preferred.display_name}' só aceita até '{preferred.max_label.nome}'. "
                    f"Usando '{fallback.display_name}'."
                ),
            )
        raise RouteError(
            f"Nenhum modelo configurado pode receber dados '{session.label.nome}'."
        )

    def client_for(self, profile: ModelProfile) -> LLMClient:
        if profile.key not in self._clients:
            self._clients[profile.key] = self._factory(profile)
        return self._clients[profile.key]
