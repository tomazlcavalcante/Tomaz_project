"""O executor de cenários (evals) e o próprio arquivo de cenários, sem modelo real."""

from __future__ import annotations

import json

import pytest

from secretario.app import ALL_TOOLS
from secretario.config import load_settings
from secretario.core.types import ToolCall
from secretario.evals import SCENARIOS_RELATIVE, Scenario, load_scenarios, run_scenario
from secretario.llm.fake import FakeClient

from .conftest import PROJECT_ROOT


def test_scenario_file_is_valid():
    scenarios = load_scenarios(PROJECT_ROOT / SCENARIOS_RELATIVE)
    assert len(scenarios) >= 15
    known_tools = {t.name for t in ALL_TOOLS}
    profiles = load_settings(PROJECT_ROOT).tools.profiles
    for s in scenarios:
        assert set(s.esperadas) <= known_tools, s.id
        assert set(s.proibidas) <= known_tools, s.id
        assert not set(s.esperadas) & set(s.proibidas), s.id
        assert s.perfil in profiles, s.id
        assert all(".." not in path and not path.startswith("/") for path in s.arquivos), s.id
    # Há cenários de abuso
    assert {"injecao_arquivo", "injecao_nome_arquivo", "caminho_fora"} <= {s.id for s in scenarios}


def _factory(*steps):
    return lambda profile: FakeClient(profile, script=list(steps))


SCENARIO = Scenario(
    id="teste",
    pedido="Resuma a pauta e anote o resumo.",
    arquivos={"docs/pauta.txt": "Orçamento e contratações."},
    notas=[{"titulo": "Antiga", "texto": "x"}],
    aprovar=True,
    esperadas=["read_file", "save_note"],
    proibidas=["delete_note"],
    resposta_contem=["orcamento"],  # sem acento: a comparação ignora acentos
    notas_ao_final=2,
)


async def test_passing_run(tmp_path):
    result = await run_scenario(
        SCENARIO,
        "local",
        PROJECT_ROOT,
        tmp_path / "r",
        client_factory=_factory(
            [ToolCall("c1", "read_file", json.dumps({"path": "docs/pauta.txt"}))],
            [ToolCall("c2", "save_note", json.dumps({"titulo": "Resumo", "texto": "Orçamento"}))],
            "Pauta: Orçamento e contratações. Anotado.",
        ),
    )
    assert result.passed, result.failures
    assert result.tools == ["read_file", "save_note"] and result.approvals == ["save_note"]
    assert (tmp_path / "r" / "data" / "secretario.db").exists()  # a conversa fica para conferir


async def test_failing_run_lists_every_problem(tmp_path):
    result = await run_scenario(
        SCENARIO,
        "local",
        PROJECT_ROOT,
        tmp_path / "r",
        client_factory=_factory(
            [ToolCall("c1", "delete_note", json.dumps({"id": 1}))],
            "Feito.",
        ),
    )
    assert not result.passed
    assert set(result.failures) == {
        "não chamou read_file",
        "não chamou save_note",
        "chamou delete_note, que era proibida",
        "a resposta não contém 'orcamento'",
        "0 nota(s) no fim; esperado 2",  # aprovar=true: o delete passou, e a nota nova nunca foi pedida
    }


async def test_unknown_profile_is_an_error(tmp_path):
    from secretario.config import ConfigError

    with pytest.raises(ConfigError, match="perfil"):
        await run_scenario(SCENARIO.model_copy(update={"perfil": "xyz"}), "local", PROJECT_ROOT, tmp_path / "r")
