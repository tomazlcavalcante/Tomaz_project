"""Avaliação com o modelo real: roda os cenários de evals/cenarios.toml e compara modelos.

    uv run secretario-evals                          # todos os cenários, modelo padrão
    uv run secretario-evals --modelos local gemini   # compara dois perfis de modelo
    uv run secretario-evals --cenarios ler_arquivo injecao_arquivo
    uv run secretario-evals --listar

Os testes (pytest) verificam o código com um modelo falso. Aqui a pergunta é
outra: este modelo escolhe as ferramentas certas e resiste a injeções?
Modelos erram de vez em quando, então vale rodar mais de uma vez antes de
concluir algo.

Cada cenário roda numa pasta própria em data/evals/<data-hora>/, com
configuração copiada, pasta de trabalho, notas e banco novos. O relatório
fica em data/evals/<data-hora>/relatorio.json, e o banco de cada cenário
guarda a conversa e a auditoria, para conferir depois.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
import tomllib
import unicodedata
from contextlib import aclosing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from secretario.app import build_agent
from secretario.config import ConfigError, find_project_root, load_settings
from secretario.core.context import WEEKDAYS
from secretario.core.types import ApprovalRequest, TextDelta, ToolStarted, TurnDone, TurnError
from secretario.llm.openai_compat import OpenAICompatClient
from secretario.llm.router import ClientFactory
from secretario.storage.notes import NoteStore

SCENARIOS_RELATIVE = Path("evals") / "cenarios.toml"


class SeedNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    titulo: str
    texto: str


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    descricao: str = ""
    pedido: str
    perfil: str = "completo"
    arquivos: dict[str, str] = Field(default_factory=dict)
    notas: list[SeedNote] = Field(default_factory=list)
    aprovar: bool = False
    esperadas: list[str] = Field(default_factory=list)
    proibidas: list[str] = Field(default_factory=list)
    resposta_contem: list[str] = Field(default_factory=list)
    resposta_nao_contem: list[str] = Field(default_factory=list)
    notas_ao_final: int | None = None


@dataclass
class Result:
    scenario: str
    model: str
    passed: bool
    failures: list[str]
    tools: list[str]  # ferramentas pedidas pelo modelo, na ordem
    approvals: list[str]  # ferramentas para as quais o modelo pediu aprovação
    seconds: float
    answer: str = ""
    error: str | None = None
    folder: str = ""
    extra: dict = field(default_factory=dict)


def load_scenarios(path: Path) -> list[Scenario]:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    scenarios = [Scenario.model_validate(item) for item in raw.get("cenario", [])]
    ids = [s.id for s in scenarios]
    duplicated = {i for i in ids if ids.count(i) > 1}
    if duplicated:
        raise ValueError(f"Cenários com id repetido: {sorted(duplicated)}")
    return scenarios


def _norm(text: str) -> str:
    """Minúsculas e sem acentos, para comparar respostas."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _fill(text: str, now: datetime) -> str:
    return text.replace("{dia_da_semana}", WEEKDAYS[now.weekday()])


def prepare_folder(scenario: Scenario, project_root: Path, folder: Path) -> None:
    """Pasta isolada: config copiada, arquivos do cenário na pasta de trabalho."""
    folder.mkdir(parents=True)
    shutil.copytree(project_root / "config", folder / "config")
    workspace = folder / "data" / "workspace"
    workspace.mkdir(parents=True)
    for rel, content in scenario.arquivos.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


async def run_scenario(
    scenario: Scenario,
    model_key: str,
    project_root: Path,
    folder: Path,
    client_factory: ClientFactory = OpenAICompatClient,
) -> Result:
    prepare_folder(scenario, project_root, folder)
    settings = load_settings(folder)
    if model_key not in settings.models:
        raise ConfigError(f"Modelo {model_key!r} não existe em config/settings.toml.")
    if scenario.perfil not in settings.tools.profiles:
        raise ConfigError(f"Cenário {scenario.id}: perfil {scenario.perfil!r} não existe.")

    agent = build_agent(settings, client_factory=client_factory, configure_logging=False)
    notes = NoteStore(agent.store.db)
    for seed in scenario.notas:
        notes.save(seed.titulo, seed.texto)

    session = agent.new_session()
    session.model_key = model_key
    session.tool_profile = scenario.perfil
    agent.store.save_session(session)

    approvals: list[str] = []

    async def approver(request: ApprovalRequest) -> bool:
        approvals.append(request.tool)
        return scenario.aprovar

    tools: list[str] = []
    parts: list[str] = []
    error: str | None = None
    done: TurnDone | None = None
    started = time.perf_counter()
    async with aclosing(agent.run_turn(session, scenario.pedido, approver=approver)) as events:
        async for event in events:
            if isinstance(event, ToolStarted):
                tools.append(event.name)
            elif isinstance(event, TextDelta):
                parts.append(event.text)
            elif isinstance(event, TurnError):
                error = event.message
            elif isinstance(event, TurnDone):
                done = event
    seconds = time.perf_counter() - started
    answer = "".join(parts).strip()

    failures: list[str] = []
    if error:
        failures.append(f"erro no turno: {error}")
    for name in scenario.esperadas:
        if name not in tools:
            failures.append(f"não chamou {name}")
    for name in scenario.proibidas:
        if name in tools:
            failures.append(f"chamou {name}, que era proibida")
    now = datetime.now()
    for piece in scenario.resposta_contem:
        piece = _fill(piece, now)
        if _norm(piece) not in _norm(answer):
            failures.append(f"a resposta não contém {piece!r}")
    for piece in scenario.resposta_nao_contem:
        if _norm(piece) in _norm(answer):
            failures.append(f"a resposta contém {piece!r}")
    if scenario.notas_ao_final is not None and notes.count() != scenario.notas_ao_final:
        failures.append(f"{notes.count()} nota(s) no fim; esperado {scenario.notas_ao_final}")

    agent.store.db.close()
    return Result(
        scenario=scenario.id,
        model=model_key,
        passed=not failures,
        failures=failures,
        tools=tools,
        approvals=approvals,
        seconds=round(seconds, 1),
        answer=answer,
        error=error,
        folder=str(folder),
        extra={"model_used": done.model_key if done else None},
    )


def _print_result(r: Result) -> None:
    mark = "ok  " if r.passed else "FALHOU"
    tools = ", ".join(r.tools) or "-"
    print(f"  {mark:<6} {r.scenario:<24} {r.seconds:>6.1f}s  ferramentas: {tools}")
    for failure in r.failures:
        print(f"         - {failure}")


async def _main_async(args: argparse.Namespace) -> int:
    root = find_project_root()
    scenarios = load_scenarios(root / SCENARIOS_RELATIVE)
    if args.listar:
        for s in scenarios:
            print(f"{s.id:<24} {s.descricao}")
        return 0
    if args.cenarios:
        unknown = set(args.cenarios) - {s.id for s in scenarios}
        if unknown:
            print(f"Cenários desconhecidos: {sorted(unknown)}. Veja --listar.", file=sys.stderr)
            return 2
        scenarios = [s for s in scenarios if s.id in args.cenarios]

    settings = load_settings(root)
    models = args.modelos or [settings.app.default_model]
    unknown = set(models) - set(settings.models)
    if unknown:
        print(f"Modelos desconhecidos: {sorted(unknown)}. Veja config/settings.toml.", file=sys.stderr)
        return 2
    for key in models:
        if not settings.models[key].is_local:
            print(f"Aviso: '{key}' é externo; os pedidos e arquivos dos cenários (sintéticos) sairão da máquina.")

    run_dir = settings.data_path / "evals" / datetime.now().strftime("%Y%m%d-%H%M%S")
    results: list[Result] = []
    for key in models:
        print(f"\n== {settings.models[key].display_name} ({key}) ==")
        for scenario in scenarios:
            result = await run_scenario(scenario, key, root, run_dir / key / scenario.id)
            results.append(result)
            _print_result(result)

    print("\n== Resumo ==")
    for key in models:
        mine = [r for r in results if r.model == key]
        passed = sum(r.passed for r in mine)
        avg = sum(r.seconds for r in mine) / len(mine) if mine else 0
        print(f"  {key:<12} {passed}/{len(mine)} cenários ok, {avg:.1f}s em média")

    report = run_dir / "relatorio.json"
    report.write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nRelatório: {report}")
    return 0 if all(r.passed for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="secretario-evals", description=__doc__.split("\n\n")[0])
    parser.add_argument("--modelos", nargs="+", help="chaves de [models.*] (padrão: app.default_model)")
    parser.add_argument("--cenarios", nargs="+", help="ids dos cenários (padrão: todos)")
    parser.add_argument("--listar", action="store_true", help="só lista os cenários")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_main_async(args))
    except (ConfigError, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
