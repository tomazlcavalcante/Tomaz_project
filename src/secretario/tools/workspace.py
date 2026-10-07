"""A pasta de trabalho: o único lugar do disco que as ferramentas de arquivo enxergam.

Toda ferramenta que recebe um caminho do modelo passa por
`resolve_in_workspace`. O modelo pode ter sido enganado por um texto
malicioso, então o caminho é tratado como entrada hostil:

- caminho absoluto (/etc, C:\\Windows, \\\\servidor) é recusado;
- qualquer ".." é recusado, mesmo que o resultado ficasse dentro da pasta;
- links simbólicos (e junções do Windows) que apontam para fora são recusados,
  porque a verificação é feita no caminho real, depois de resolver os links.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

from secretario.tools.base import ToolError

# Formas que o modelo costuma usar para dizer "a raiz da pasta de trabalho".
ROOT_ALIASES = {"", ".", "/", "./", "\\"}


def resolve_in_workspace(workspace: Path, relative: str) -> Path:
    text = relative.strip()
    if text in ROOT_ALIASES:
        return workspace.resolve()
    if "\x00" in text:
        raise ToolError("Caminho inválido.")

    win = PureWindowsPath(text)
    if PurePosixPath(text).is_absolute() or win.is_absolute() or win.drive or text.startswith("\\"):
        raise ToolError(
            f"Caminho absoluto não é permitido: {relative!r}. "
            "Use caminhos relativos à pasta de trabalho, por exemplo 'relatorios' ou '' para a raiz."
        )

    # No Windows "\" separa pastas; aqui ele é tratado assim em qualquer sistema.
    parts = PurePosixPath(text.replace("\\", "/")).parts
    if ".." in parts:
        raise ToolError(f"Caminho com '..' não é permitido: {relative!r}.")

    root = workspace.resolve()
    candidate = root.joinpath(*parts).resolve()  # resolve os links simbólicos
    if not candidate.is_relative_to(root):
        raise ToolError(f"O caminho {relative!r} aponta para fora da pasta de trabalho.")
    return candidate


def display_path(workspace: Path, path: Path) -> str:
    """Caminho como o modelo e o usuário devem vê-lo: relativo, com "/"."""
    rel = path.relative_to(workspace.resolve()).as_posix()
    return rel if rel != "." else "."
