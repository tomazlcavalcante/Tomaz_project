"""Registro: todas as ferramentas que existem, e quais cada perfil oferece ao modelo."""

from __future__ import annotations

from secretario.config import ConfigError, ToolSettings
from secretario.tools.base import Tool


class ToolRegistry:
    def __init__(self, tools: list[Tool], profiles: dict[str, list[str]]):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Ferramenta registrada duas vezes: {tool.name}")
            self._tools[tool.name] = tool
        for profile, names in profiles.items():
            unknown = [n for n in names if n not in self._tools]
            if unknown:
                raise ConfigError(
                    f"O perfil de ferramentas {profile!r} cita ferramentas que não existem: {unknown}. "
                    f"Disponíveis: {sorted(self._tools)}."
                )
        self.profiles = {name: list(names) for name, names in profiles.items()}

    @classmethod
    def from_settings(cls, settings: ToolSettings, tools: list[Tool]) -> ToolRegistry:
        return cls(tools, settings.profiles)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def for_profile(self, profile: str) -> list[Tool]:
        """Ferramentas oferecidas ao modelo. Perfil desconhecido = nenhuma (falha fechada)."""
        return [self._tools[n] for n in self.profiles.get(profile, [])]
