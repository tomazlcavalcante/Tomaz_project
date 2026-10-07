"""Ferramentas da V1, etapa 1: só leitura, sem aprovação."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from secretario.core.context import WEEKDAYS
from secretario.tools.base import NoArgs, Risk, Tool, ToolContext, ToolError
from secretario.tools.workspace import display_path, resolve_in_workspace

MAX_LIST_ENTRIES = 200


# ----------------------------------------------------------------- get_datetime
def get_datetime(args: NoArgs, ctx: ToolContext) -> dict:
    now = ctx.now()
    offset = now.strftime("%z")  # ex.: "-0300"; vazio se a hora não tiver fuso
    return {
        "data": f"{now:%d/%m/%Y}",
        "dia_da_semana": WEEKDAYS[now.weekday()],
        "hora": f"{now:%H:%M}",
        "fuso_utc": f"{offset[:3]}:{offset[3:]}" if offset else "desconhecido",
        "iso": now.isoformat(timespec="seconds"),
    }


GET_DATETIME = Tool(
    name="get_datetime",
    description="Devolve a data, o dia da semana e a hora atuais no computador do usuário.",
    args_model=NoArgs,
    func=get_datetime,
    risk=Risk.NONE,
)


# ------------------------------------------------------------------- list_files
class ListFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        default="",
        description="Subpasta, relativa à pasta de trabalho (ex.: 'relatorios/2026'). Vazio = a raiz.",
    )


def list_files(args: ListFilesArgs, ctx: ToolContext) -> dict:
    folder = resolve_in_workspace(ctx.workspace, args.path)
    shown = display_path(ctx.workspace, folder)
    if not folder.exists():
        raise ToolError(f"A pasta '{shown}' não existe na pasta de trabalho.")
    if not folder.is_dir():
        raise ToolError(f"'{shown}' é um arquivo, não uma pasta.")

    root = ctx.workspace.resolve()
    entries = []
    hidden_links = 0
    # Pastas primeiro, depois arquivos, em ordem alfabética
    for child in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.is_symlink() and not child.resolve().is_relative_to(root):
            hidden_links += 1  # link para fora da pasta de trabalho: nem o nome aparece
            continue
        try:
            stat = child.stat()
        except OSError:
            continue  # link quebrado ou arquivo que sumiu no meio da listagem
        is_dir = child.is_dir()
        item = {"nome": child.name, "tipo": "pasta" if is_dir else "arquivo"}
        if not is_dir:
            item["tamanho_bytes"] = stat.st_size
        item["modificado"] = datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M")
        entries.append(item)

    result: dict = {"pasta": shown, "total": len(entries), "itens": entries[:MAX_LIST_ENTRIES]}
    if len(entries) > MAX_LIST_ENTRIES:
        result["aviso"] = f"Mostrando só os primeiros {MAX_LIST_ENTRIES} itens."
    if hidden_links:
        result["links_ignorados"] = hidden_links
    return result


LIST_FILES = Tool(
    name="list_files",
    description=(
        "Lista os arquivos e as pastas da pasta de trabalho do usuário (ou de uma subpasta dela), "
        "com tamanho e data de modificação. Não lê o conteúdo dos arquivos."
    ),
    args_model=ListFilesArgs,
    func=list_files,
    risk=Risk.READ,
)


BUILTIN_TOOLS = [GET_DATETIME, LIST_FILES]
