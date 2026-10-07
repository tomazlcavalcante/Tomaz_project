"""Interface web (Chainlit). Adaptador fino: só traduz eventos do núcleo em mensagens.

Não rode este arquivo diretamente; use `uv run secretario-ui`, que valida a
configuração e garante que o servidor só escute em 127.0.0.1.
"""

from __future__ import annotations

from contextlib import aclosing

import chainlit as cl
from chainlit.server import app as chainlit_server
from chainlit.utils import utc_now

from secretario.app import build_agent
from secretario.core.session import Session
from secretario.core.types import Notice, TextDelta, ToolFinished, ToolStarted, TurnDone, TurnError
from secretario.ui.origin_guard import LocalOriginGuard, local_addresses

AGENT = build_agent()

_hosts, _origins = local_addresses(AGENT.settings.ui.port)
chainlit_server.add_middleware(LocalOriginGuard, allowed_hosts=_hosts, allowed_origins=_origins)

SYSTEM_AUTHOR = "Sistema"


def _session() -> Session:
    session = cl.user_session.get("agent_session")
    if session is None:
        session = AGENT.new_session()
        cl.user_session.set("agent_session", session)
    return session


@cl.on_chat_start
async def on_chat_start() -> None:
    session = _session()
    profile = AGENT.settings.models[session.model_key]
    where = "roda na sua máquina" if profile.is_local else "**externo: os dados saem da sua máquina**"
    await cl.Message(
        author=SYSTEM_AUTHOR,
        content=(
            f"Modelo: **{profile.display_name}** ({where}). "
            f"Nível de sigilo: **{session.label.nome}**. "
            f"Ferramentas: **{session.tool_profile}**.\n\n"
            "Digite `/ajuda` para ver os comandos (trocar de modelo, subir o sigilo, ferramentas, histórico)."
        ),
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    session = _session()
    text = message.content or ""

    if message.elements:
        await cl.Message(
            author=SYSTEM_AUTHOR, content="Anexos ainda não são lidos na V0; o arquivo foi ignorado."
        ).send()

    if text.strip().startswith("/"):
        reply = AGENT.handle_command(session, text)
        await cl.Message(author=SYSTEM_AUTHOR, content=f"```text\n{reply}\n```").send()
        return

    if not text.strip():
        return

    answer = cl.Message(content="")
    steps: dict[str, cl.Step] = {}  # passos de ferramenta abertos, por id do pedido
    # aclosing: se você clicar em parar, o núcleo registra a resposta parcial na hora
    async with aclosing(AGENT.run_turn(session, text)) as events:
        async for event in events:
            if isinstance(event, TextDelta):
                await answer.stream_token(event.text)
            elif isinstance(event, ToolStarted):
                if answer.content:
                    # Fecha o texto já escrito, para os passos aparecerem depois dele e a resposta final abaixo
                    await answer.send()
                    answer = cl.Message(content="")
                # Sem `async with`, o Chainlit não descobre o pai sozinho e o passo iria para o fim da conversa
                run = cl.context.current_step
                step = cl.Step(name=event.name, type="tool", show_input="json", parent_id=run.id if run else None)
                step.input = event.arguments or "{}"
                step.start = utc_now()
                steps[event.call_id] = step
                await step.send()
            elif isinstance(event, ToolFinished):
                step = steps.pop(event.call_id, None)
                if step is not None:
                    step.output = event.output
                    if event.output.startswith(("{", "[")):
                        step.language = "json"
                    step.is_error = not event.ok
                    step.end = utc_now()
                    await step.update()
            elif isinstance(event, Notice):
                await cl.Message(author=SYSTEM_AUTHOR, content=f"Aviso: {event.text}").send()
            elif isinstance(event, TurnError):
                await cl.Message(author=SYSTEM_AUTHOR, content=f"**Erro:** {event.message}").send()
            elif isinstance(event, TurnDone):
                where = "local" if event.is_local else "externo"
                tokens = f" · {event.completion_tokens} tokens" if event.completion_tokens else ""
                tools = f" · {event.tool_calls} ferramenta(s)" if event.tool_calls else ""
                if event.finish_reason == "length":
                    await answer.stream_token("\n\n_[resposta cortada pelo limite de tamanho]_")
                await answer.stream_token(
                    f"\n\n_{event.model_display} · {where} · {event.latency_s:.1f}s{tokens}{tools}_"
                )
    if answer.content:
        await answer.send()
