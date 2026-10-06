from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from secretario.app import build_agent
from secretario.config import load_settings
from secretario.llm.fake import FakeClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Cópia isolada da pasta config/, com dados em diretório temporário."""
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    return tmp_path


@pytest.fixture
def settings(project: Path):
    return load_settings(project)


@pytest.fixture
def fakes():
    """Registro dos clientes falsos criados, por chave de modelo."""
    return {}


@pytest.fixture
def agent(settings, fakes):
    def factory(profile):
        client = FakeClient(profile)
        fakes[profile.key] = client
        return client

    return build_agent(settings, client_factory=factory)


async def collect(agen):
    return [event async for event in agen]
