"""Ferramentas da etapa 1, a trava da pasta de trabalho e o registro por perfil."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from secretario.config import ConfigError
from secretario.tools.base import NoArgs, Risk, ToolContext, ToolError
from secretario.tools.builtin import BUILTIN_TOOLS, GET_DATETIME, LIST_FILES, ListFilesArgs
from secretario.tools.registry import ToolRegistry
from secretario.tools.workspace import resolve_in_workspace

FIXED_NOW = datetime(2026, 10, 6, 18, 5, tzinfo=timezone(timedelta(hours=-3)))


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "relatorios" / "2026").mkdir(parents=True)
    (ws / "notas.txt").write_text("oi", encoding="utf-8")
    (ws / "relatorios" / "vendas.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return ws


def _ctx(ws):
    return ToolContext(session_id="s", workspace=ws, now=lambda: FIXED_NOW)


def _list(ws, path=""):
    return LIST_FILES.func(ListFilesArgs(path=path), _ctx(ws))


# ------------------------------------------------------------------ esquemas
def test_specs_in_mcp_and_openai_formats():
    spec = LIST_FILES.spec()
    assert set(spec) == {"name", "description", "inputSchema"}
    schema = spec["inputSchema"]
    assert schema["type"] == "object" and "path" in schema["properties"]
    assert schema["additionalProperties"] is False  # argumento extra é recusado
    assert "title" not in schema and "title" not in schema["properties"]["path"]

    openai = LIST_FILES.openai_spec()
    assert openai["type"] == "function"
    assert openai["function"]["name"] == "list_files" and openai["function"]["parameters"] == schema


def test_risk_decides_approval():
    assert not GET_DATETIME.needs_approval and not LIST_FILES.needs_approval
    assert {t.risk for t in BUILTIN_TOOLS} <= {Risk.NONE, Risk.READ}


# ------------------------------------------------------------------ registro
def test_profiles_select_tools():
    reg = ToolRegistry(BUILTIN_TOOLS, {"nenhum": [], "basico": ["get_datetime", "list_files"]})
    assert [t.name for t in reg.for_profile("basico")] == ["get_datetime", "list_files"]
    assert reg.for_profile("nenhum") == []
    assert reg.for_profile("perfil-que-nao-existe") == []  # falha fechada


def test_profile_with_unknown_tool_is_config_error():
    with pytest.raises(ConfigError, match="delete_everything"):
        ToolRegistry(BUILTIN_TOOLS, {"basico": ["list_files", "delete_everything"]})


def test_settings_profile_must_exist(project):
    from secretario.config import load_settings

    path = project / "config" / "settings.toml"
    path.write_text(path.read_text(encoding="utf-8").replace('profile = "basico"', 'profile = "xyz"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="xyz"):
        load_settings(project)


# -------------------------------------------------------------- get_datetime
def test_get_datetime_in_portuguese():
    out = GET_DATETIME.func(NoArgs(), _ctx(None))
    assert out["data"] == "06/10/2026" and out["dia_da_semana"] == "terça-feira"
    assert out["hora"] == "18:05" and out["fuso_utc"] == "-03:00"


# ---------------------------------------------------------------- list_files
def test_list_root_and_subfolder(workspace):
    out = _list(workspace)
    assert out["pasta"] == "."
    assert [(i["nome"], i["tipo"]) for i in out["itens"]] == [("relatorios", "pasta"), ("notas.txt", "arquivo")]
    assert out["itens"][1]["tamanho_bytes"] == 2

    out = _list(workspace, "relatorios")
    assert out["pasta"] == "relatorios"
    assert [i["nome"] for i in out["itens"]] == ["2026", "vendas.csv"]
    assert _list(workspace, "relatorios\\2026")["total"] == 0  # separador do Windows também vale


@pytest.mark.parametrize("alias", ["", ".", "/", "./"])
def test_root_aliases(workspace, alias):
    assert _list(workspace, alias)["pasta"] == "."


def test_missing_folder_and_file_are_friendly_errors(workspace):
    with pytest.raises(ToolError, match="não existe"):
        _list(workspace, "nada")
    with pytest.raises(ToolError, match="arquivo, não uma pasta"):
        _list(workspace, "notas.txt")


# ------------------------------------------- abuso: sair da pasta de trabalho
@pytest.mark.parametrize(
    "path",
    [
        "..",
        "../",
        "relatorios/../..",
        "relatorios/../notas.txt",  # ficaria dentro, mas ".." é recusado sempre
        "..\\..\\Windows",
        "/etc",
        "/etc/passwd",
        "C:\\Windows\\System32",
        "C:relativo-ao-disco",
        "\\\\servidor\\compartilhado",
        "nome\x00oculto",
    ],
)
def test_paths_outside_workspace_are_refused(workspace, path):
    with pytest.raises(ToolError):
        resolve_in_workspace(workspace, path)
    with pytest.raises(ToolError):
        _list(workspace, path)


@pytest.mark.skipif(os.name == "nt", reason="criar links simbólicos no Windows exige permissão especial")
def test_symlink_pointing_outside_is_refused_and_hidden(workspace, tmp_path):
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "segredo.txt").write_text("x", encoding="utf-8")
    (workspace / "atalho").symlink_to(outside, target_is_directory=True)
    (workspace / "link-arquivo").symlink_to(outside / "segredo.txt")
    (workspace / "link-interno").symlink_to(workspace / "relatorios", target_is_directory=True)

    with pytest.raises(ToolError, match="fora da pasta de trabalho"):
        _list(workspace, "atalho")

    out = _list(workspace)
    names = [i["nome"] for i in out["itens"]]
    assert "atalho" not in names and "link-arquivo" not in names  # nem o nome vaza
    assert "link-interno" in names  # link que fica dentro da pasta é normal
    assert out["links_ignorados"] == 2
