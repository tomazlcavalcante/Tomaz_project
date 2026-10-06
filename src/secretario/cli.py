"""Conversa pelo terminal, sem navegador. Útil para testar o núcleo e os modelos.

    uv run secretario-cli
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import aclosing

from secretario.app import build_agent
from secretario.config import ConfigError
from secretario.core.types import Notice, TextDelta, TurnDone, TurnError


async def _chat() -> None:
    agent = build_agent()
    session = agent.new_session()
    profile = agent.settings.models[session.model_key]
    print(f"Secretário V0 — modelo: {profile.display_name}. /ajuda para comandos, /nova, /sair.\n")

    loop = asyncio.get_running_loop()
    while True:
        try:
            text = await loop.run_in_executor(None, input, "você> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        text = text.strip()
        if not text:
            continue
        if text in ("/sair", "/exit", "/quit"):
            return
        if text == "/nova":
            session = agent.new_session()
            print("Nova conversa iniciada.\n")
            continue
        if text.startswith("/"):
            print(agent.handle_command(session, text), end="\n\n")
            continue

        started = False
        async with aclosing(agent.run_turn(session, text)) as events:
            async for event in events:
                if isinstance(event, TextDelta):
                    if not started:
                        print("agente> ", end="", flush=True)
                        started = True
                    print(event.text, end="", flush=True)
                elif isinstance(event, Notice):
                    print(f"[aviso] {event.text}")
                elif isinstance(event, TurnError):
                    print(f"\n[erro] {event.message}\n" if started else f"[erro] {event.message}\n")
                elif isinstance(event, TurnDone):
                    where = "local" if event.is_local else "externo"
                    print(f"\n  ({event.model_display}, {where}, {event.latency_s:.1f}s)\n")


def main() -> int:
    try:
        asyncio.run(_chat())
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
