"""Carrega e valida config/settings.toml.

Regras de segurança ficam aqui, em código, e não na disciplina de quem
edita o arquivo:

- um modelo só é "local" se o endereço for de loopback e o nome do modelo
  não for um modelo de nuvem do Ollama (ex.: "gemma4:cloud");
- um modelo externo não pode receber dados acima de "interno" sem
  `allow_sensitive_external = true` explícito (pensado para um futuro
  contrato corporativo, não para o plano gratuito);
- a interface só sobe em endereço de loopback, porque a V0 não tem login.
"""

from __future__ import annotations

import ipaddress
import os
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from secretario.core.types import Label

CONFIG_RELATIVE = Path("config") / "settings.toml"
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


class ConfigError(Exception):
    pass


def _is_loopback(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _looks_like_cloud_model(model: str) -> bool:
    # O Ollama roteia para a nuvem dele modelos com sufixo "cloud"
    # (ex.: "gemma4:cloud", "gemma4:31b-cloud"), mesmo com o servidor local.
    tag = model.split(":", 1)[1] if ":" in model else ""
    return tag == "cloud" or tag.endswith("-cloud")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelProfile(Strict):
    key: str = ""  # preenchido a partir do nome da seção
    display_name: str
    base_url: str
    model: str
    api_key_secret: str | None = None  # nome do segredo no cofre; None = sem chave
    max_label: Label = Label.PUBLICO
    allow_sensitive_external: bool = False
    temperature: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    stream_usage: bool = True
    timeout_s: float = 180.0
    max_context_chars: int = Field(default=20_000, ge=1_000)

    @field_validator("max_label", mode="before")
    @classmethod
    def _parse_label(cls, v):
        return Label.parse(v)

    @property
    def is_local(self) -> bool:
        host = urlparse(self.base_url).hostname or ""
        return _is_loopback(host) and not _looks_like_cloud_model(self.model)

    @model_validator(mode="after")
    def _guard_external(self):
        if not self.is_local and self.max_label > Label.INTERNO and not self.allow_sensitive_external:
            raise ValueError(
                f"O modelo '{self.display_name}' é externo (os dados saem da sua máquina) "
                f"e não pode receber dados '{self.max_label.nome}'. "
                "Use max_label = 'publico' ou 'interno', ou declare "
                "allow_sensitive_external = true se houver contrato que permita."
            )
        return self


class AppSettings(Strict):
    data_dir: Path = Path("data")
    system_prompt_file: Path = Path("config") / "system_prompt.md"
    default_model: str
    sensitive_fallback_model: str
    default_label: Label = Label.PUBLICO

    @field_validator("default_label", mode="before")
    @classmethod
    def _parse_label(cls, v):
        return Label.parse(v)


class UISettings(Strict):
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def _loopback_only(cls, v: str) -> str:
        if not _is_loopback(v):
            raise ValueError(
                f"ui.host = {v!r} exporia o agente na rede. A V0 não tem login, "
                "então só aceita 127.0.0.1 ou localhost."
            )
        return v


class Limits(Strict):
    max_history_messages: int = Field(default=40, ge=2)


class Settings(Strict):
    app: AppSettings
    ui: UISettings = UISettings()
    limits: Limits = Limits()
    models: dict[str, ModelProfile]

    root: Path = Path(".")  # raiz do projeto; preenchida por load_settings

    @model_validator(mode="after")
    def _check_models(self):
        if not self.models:
            raise ValueError("Nenhum modelo configurado em [models.*].")
        for key, profile in self.models.items():
            profile.key = key
        for field_name in ("default_model", "sensitive_fallback_model"):
            key = getattr(self.app, field_name)
            if key not in self.models:
                raise ValueError(f"app.{field_name} = {key!r} não existe em [models].")
        fallback = self.models[self.app.sensitive_fallback_model]
        if not fallback.is_local:
            raise ValueError("app.sensitive_fallback_model precisa ser um modelo local.")
        return self

    # Caminhos sempre resolvidos a partir da raiz do projeto
    @property
    def data_path(self) -> Path:
        return (self.root / self.app.data_dir).resolve()

    @property
    def db_path(self) -> Path:
        return self.data_path / "secretario.db"

    @property
    def system_prompt_path(self) -> Path:
        return (self.root / self.app.system_prompt_file).resolve()

    def system_prompt(self) -> str:
        path = self.system_prompt_path
        if not path.exists():
            raise ConfigError(f"Prompt de sistema não encontrado: {path}")
        return path.read_text(encoding="utf-8").strip()


def find_project_root(start: Path | None = None) -> Path:
    """Raiz = SECRETARIO_HOME, ou o primeiro diretório acima de `start` com config/settings.toml."""
    env = os.environ.get("SECRETARIO_HOME")
    if env:
        root = Path(env).expanduser().resolve()
        if not (root / CONFIG_RELATIVE).exists():
            raise ConfigError(f"SECRETARIO_HOME={root} não contém {CONFIG_RELATIVE}.")
        return root
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / CONFIG_RELATIVE).exists():
            return candidate
    raise ConfigError(
        f"Não encontrei {CONFIG_RELATIVE}. Rode os comandos a partir da pasta do projeto "
        "ou defina a variável SECRETARIO_HOME."
    )


def load_settings(root: Path | None = None) -> Settings:
    root = root or find_project_root()
    path = root / CONFIG_RELATIVE
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Erro de sintaxe em {path}: {exc}") from exc
    try:
        settings = Settings.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"Configuração inválida em {path}:\n{exc}") from exc
    settings.root = root
    return settings
