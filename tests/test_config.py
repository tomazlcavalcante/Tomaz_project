from __future__ import annotations

import pytest

from secretario.config import ConfigError, ModelProfile, Settings, load_settings
from secretario.core.types import Label


def test_shipped_settings_are_valid(settings):
    assert settings.models["local"].is_local
    assert not settings.models["gemini"].is_local
    assert settings.models["gemini"].max_label == Label.PUBLICO
    assert settings.ui.host == "127.0.0.1"


@pytest.mark.parametrize(
    ("base_url", "model", "expected"),
    [
        ("http://127.0.0.1:11434/v1", "qwen3.5:4b", True),
        ("http://localhost:11434/v1", "gemma4:e4b", True),
        ("http://[::1]:11434/v1", "gemma4:e4b", True),
        ("http://127.0.0.1:11434/v1", "gemma4:cloud", False),  # Ollama manda para a nuvem
        ("http://127.0.0.1:11434/v1", "gemma4:31b-cloud", False),
        ("http://192.168.0.10:11434/v1", "qwen3.5:4b", False),  # outra máquina, tráfego na rede
        ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-flash-latest", False),
    ],
)
def test_is_local(base_url, model, expected):
    p = ModelProfile(display_name="x", base_url=base_url, model=model, max_label="publico")
    assert p.is_local is expected


def test_external_model_cannot_take_confidential_data_by_default():
    with pytest.raises(ValueError, match="externo"):
        ModelProfile(display_name="x", base_url="https://api.exemplo.com/v1", model="m", max_label="confidencial")


def test_external_model_can_be_explicitly_allowed():
    p = ModelProfile(
        display_name="x",
        base_url="https://api.exemplo.com/v1",
        model="m",
        max_label="confidencial",
        allow_sensitive_external=True,
    )
    assert p.max_label == Label.CONFIDENCIAL


def test_ui_refuses_non_loopback_host(settings):
    raw = settings.model_dump(exclude={"root"})
    raw["ui"]["host"] = "0.0.0.0"
    with pytest.raises(ValueError, match="rede"):
        Settings.model_validate(raw)


def test_fallback_must_be_local(settings):
    raw = settings.model_dump(exclude={"root"})
    raw["app"]["sensitive_fallback_model"] = "gemini"
    with pytest.raises(ValueError, match="local"):
        Settings.model_validate(raw)


def test_unknown_keys_are_rejected(project):
    path = project / "config" / "settings.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n[models.local2]\ndisplay_name='a'\nbase_url='http://127.0.0.1:1/v1'\nmodel='m'\nmodelo_errado=1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(project)


def test_label_parse_accepts_accents_and_case():
    assert Label.parse("Público") == Label.PUBLICO
    assert Label.parse("CONFIDENCIAL") == Label.CONFIDENCIAL
    with pytest.raises(ValueError):
        Label.parse("secreto")
