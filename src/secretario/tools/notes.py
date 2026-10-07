"""Ferramentas de notas: buscar (livre), salvar e apagar (com aprovação)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from secretario.storage.notes import NoteStore
from secretario.tools.base import Risk, Tool, ToolContext, ToolError

MAX_NOTE_CHARS = 20_000


def _notes(ctx: ToolContext) -> NoteStore:
    if ctx.notes is None:  # pragma: no cover - erro de montagem, não do modelo
        raise ToolError("As notas não estão disponíveis.")
    return ctx.notes


# ----------------------------------------------------------------- search_notes
class SearchNotesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consulta: str = Field(default="", description="Palavras a procurar. Vazio = as notas mais recentes.")
    limite: int = Field(default=5, ge=1, le=20, description="Quantas notas devolver, no máximo.")


def search_notes(args: SearchNotesArgs, ctx: ToolContext) -> dict:
    hits = _notes(ctx).search(args.consulta, args.limite)
    return {
        "consulta": args.consulta,
        "encontradas": len(hits),
        "notas": [
            {"id": h.note.id, "titulo": h.note.title, "trecho": h.snippet, "atualizada": h.note.updated_at[:16]}
            for h in hits
        ],
    }


SEARCH_NOTES = Tool(
    name="search_notes",
    description=(
        "Busca nas notas do usuário por palavras (ignora acentos e maiúsculas). Devolve id, título e um trecho; "
        "o texto completo de uma nota vem em 'trecho' só em parte."
    ),
    args_model=SearchNotesArgs,
    func=search_notes,
    risk=Risk.READ,
)


# -------------------------------------------------------------------- save_note
class SaveNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titulo: str = Field(min_length=1, max_length=200, description="Título da nota. Se já existir, a nota é substituída.")
    texto: str = Field(max_length=MAX_NOTE_CHARS, description="Texto completo da nota.")


def _save_preview(args: SaveNoteArgs, ctx: ToolContext) -> str:
    existing = _notes(ctx).find_by_title(args.titulo)
    if existing:
        return (
            f"SUBSTITUIR a nota #{existing.id} \"{existing.title}\".\n\n"
            f"Texto atual:\n{existing.body}\n\nTexto novo:\n{args.texto}"
        )
    return f"CRIAR a nota \"{args.titulo.strip()}\".\n\nTexto:\n{args.texto}"


def save_note(args: SaveNoteArgs, ctx: ToolContext) -> dict:
    note, created = _notes(ctx).save(args.titulo, args.texto, ctx.session_id)
    return {"id": note.id, "titulo": note.title, "acao": "criada" if created else "substituída"}


SAVE_NOTE = Tool(
    name="save_note",
    description=(
        "Cria uma nota, ou substitui o texto de uma nota com o mesmo título. "
        "Só roda depois que o usuário aprovar."
    ),
    args_model=SaveNoteArgs,
    func=save_note,
    risk=Risk.WRITE,
    preview=_save_preview,
)


# ------------------------------------------------------------------ delete_note
class DeleteNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1, description="Id da nota (veja com search_notes).")


def _delete_preview(args: DeleteNoteArgs, ctx: ToolContext) -> str:
    note = _notes(ctx).get(args.id)
    if note is None:
        raise ToolError(f"Não existe nota com id {args.id}.")
    return f"APAGAR a nota #{note.id} \"{note.title}\" (não dá para desfazer).\n\nTexto:\n{note.body}"


def delete_note(args: DeleteNoteArgs, ctx: ToolContext) -> dict:
    if not _notes(ctx).delete(args.id):
        raise ToolError(f"Não existe nota com id {args.id}.")
    return {"id": args.id, "acao": "apagada"}


DELETE_NOTE = Tool(
    name="delete_note",
    description="Apaga uma nota pelo id (uma por vez). Só roda depois que o usuário aprovar.",
    args_model=DeleteNoteArgs,
    func=delete_note,
    risk=Risk.DESTRUCTIVE,
    preview=_delete_preview,
)


NOTE_TOOLS = [SEARCH_NOTES, SAVE_NOTE, DELETE_NOTE]
