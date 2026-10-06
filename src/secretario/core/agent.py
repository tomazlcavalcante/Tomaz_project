"""Núcleo do agente.

Na V0, um turno é: registrar a mensagem, escolher o modelo pelo sigilo,
montar o contexto, transmitir a resposta, registrar resposta e auditoria.
Na V1 este mesmo laço ganha a etapa de ferramentas:

    for passo in range(max_passos):
        resposta = llm(contexto, ferramentas_do_perfil)
        se não pediu ferramenta: fim
        para cada pedido: gateway.execute(pedido)   # valida, autoriza, aprova, audita

Nada aqui conhece Chainlit, terminal ou HTTP: a interface consome os
eventos de `run_turn`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import datetime

from secretario.audit.log import AuditLog
from secretario.config import Settings
from secretario.core.context import build_context, render_system_prompt
from secretario.core.session import LabelError, Session
from secretario.core.types import (
    AgentEvent,
    Label,
    Message,
    Notice,
    TextDelta,
    TurnDone,
    TurnError,
)
from secretario.llm.base import Delta, Done, LLMError
from secretario.llm.router import ModelRouter, RouteError
from secretario.storage.db import ConversationStore

HELP = """Comandos disponíveis:
/status            mostra modelo, nível de sigilo e tamanho da conversa
/modelos           lista os modelos configurados
/modelo <chave>    troca o modelo preferido (ex.: /modelo gemini)
/sigilo <nível>    sobe o nível de sigilo: publico, interno, confidencial, restrito
/historico         lista as conversas recentes
/ajuda             mostra esta lista"""


class Agent:
    def __init__(self, settings: Settings, router: ModelRouter, store: ConversationStore, audit: AuditLog):
        self.settings = settings
        self.router = router
        self.store = store
        self.audit = audit
        self._system_prompt = settings.system_prompt()

    # ------------------------------------------------------------------ sessões
    def new_session(self) -> Session:
        session = Session(model_key=self.settings.app.default_model, label=self.settings.app.default_label)
        self.store.save_session(session)
        self.audit.record(
            "session_started", session.id, model_key=session.model_key, label=session.label.nome
        )
        return session

    # ----------------------------------------------------------------- comandos
    def handle_command(self, session: Session, text: str) -> str:
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/ajuda", "/help"):
            return HELP

        if cmd == "/status":
            profile = self.settings.models[session.model_key]
            try:
                decision = self.router.route(session)
                effective = decision.profile.display_name
            except RouteError as exc:
                effective = f"nenhum ({exc})"
            return (
                f"Modelo preferido: {profile.display_name} — {'local' if profile.is_local else 'externo'}\n"
                f"Modelo efetivo agora: {effective}\n"
                f"Nível de sigilo: {session.label.nome}\n"
                f"Mensagens na conversa: {len(session.messages)}\n"
                f"Sessão: {session.id}"
            )

        if cmd == "/modelos":
            lines = []
            for key, p in self.settings.models.items():
                where = "local" if p.is_local else "EXTERNO"
                mark = "*" if key == session.model_key else " "
                lines.append(f"{mark} {key:<12} {p.display_name} [{where}, aceita até '{p.max_label.nome}']")
            return "Modelos (* = preferido nesta conversa):\n" + "\n".join(lines)

        if cmd == "/modelo":
            if arg not in self.settings.models:
                return f"Modelo desconhecido: {arg!r}. Veja /modelos."
            session.model_key = arg
            self.store.save_session(session)
            self.audit.record("model_selected", session.id, model_key=arg)
            profile = self.settings.models[arg]
            msg = f"Modelo preferido: {profile.display_name}."
            if profile.max_label < session.label:
                msg += (
                    f" Atenção: a conversa está em '{session.label.nome}', acima do que esse modelo aceita; "
                    "as respostas continuarão vindo do modelo local."
                )
            elif not profile.is_local:
                msg += " Os dados desta conversa passarão a sair da sua máquina."
            return msg

        if cmd == "/sigilo":
            if not arg:
                return f"Nível atual: {session.label.nome}. Uso: /sigilo confidencial"
            try:
                new = Label.parse(arg)
                changed = session.raise_label(new)
            except (ValueError, LabelError) as exc:
                return str(exc)
            if changed:
                self.store.save_session(session)
                self.audit.record("label_raised", session.id, label=new.nome)
            return f"Nível de sigilo: {session.label.nome}."

        if cmd == "/historico":
            rows = self.store.recent_sessions(10)
            if not rows:
                return "Nenhuma conversa registrada ainda."
            lines = [
                f"{r['created_at'][:16].replace('T', ' ')}  {r['n_messages']:>3} msgs  "
                f"[{Label(r['label']).nome}]  {r['title'] or '(sem título)'}"
                for r in rows
            ]
            return "Conversas recentes (UTC):\n" + "\n".join(lines)

        return f"Comando desconhecido: {cmd}. Digite /ajuda."

    # ------------------------------------------------------------------- turnos
    async def run_turn(self, session: Session, user_text: str) -> AsyncIterator[AgentEvent]:
        user_msg = session.add(Message(role="user", content=user_text))
        self.store.add_message(session.id, user_msg)
        self.store.set_title_if_empty(session.id, user_text.strip().splitlines()[0] if user_text.strip() else "")

        try:
            decision = self.router.route(session)
        except RouteError as exc:
            self.audit.record("route_refused", session.id, label=session.label.nome, reason=str(exc))
            yield TurnError(str(exc))
            return

        profile = decision.profile
        if decision.fell_back:
            self.audit.record(
                "route_fallback",
                session.id,
                preferred=session.model_key,
                used=profile.key,
                label=session.label.nome,
            )
            yield Notice(decision.reason or "Usando o modelo local de reserva.")

        system = render_system_prompt(self._system_prompt, datetime.now().astimezone())
        ctx = build_context(
            session,
            system,
            max_chars=profile.max_context_chars,
            max_messages=self.settings.limits.max_history_messages,
        )
        if ctx.truncated_last:
            yield Notice("Sua mensagem é maior que o limite de contexto deste modelo; o meio dela foi omitido.")

        client = self.router.client_for(profile)
        started = time.perf_counter()
        parts: list[str] = []
        finished = False
        base_audit = {
            "model_key": profile.key,
            "model": profile.model,
            "is_local": profile.is_local,
            "label": session.label.nome,
            "prompt_chars": ctx.chars,
            "history_dropped": ctx.dropped,
        }

        try:
            async for event in client.stream_chat(ctx.messages):
                if isinstance(event, Delta):
                    parts.append(event.text)
                    yield TextDelta(event.text)
                elif isinstance(event, Done):
                    finished = True
                    latency = time.perf_counter() - started
                    answer = "".join(parts).strip()
                    reply = session.add(Message(role="assistant", content=answer, model_key=profile.key))
                    self.store.add_message(session.id, reply)
                    self.audit.record(
                        "llm_call",
                        session.id,
                        **base_audit,
                        ok=True,
                        provider_model=event.model,
                        latency_ms=round(latency * 1000),
                        completion_chars=len(answer),
                        prompt_tokens=event.prompt_tokens,
                        completion_tokens=event.completion_tokens,
                        finish_reason=event.finish_reason,
                    )
                    yield TurnDone(
                        model_key=profile.key,
                        model_display=profile.display_name,
                        is_local=profile.is_local,
                        latency_s=latency,
                        prompt_tokens=event.prompt_tokens,
                        completion_tokens=event.completion_tokens,
                        finish_reason=event.finish_reason,
                    )
        except LLMError as exc:
            self.audit.record(
                "llm_call",
                session.id,
                **base_audit,
                ok=False,
                error_kind=exc.kind,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
            yield TurnError(str(exc))
        except (asyncio.CancelledError, GeneratorExit):
            # Usuário clicou em parar (a tarefa é cancelada, ou quem consome fecha
            # este gerador): guarda o que já tinha chegado e registra. Sem await aqui.
            if finished:
                raise
            partial = "".join(parts).strip()
            if partial:
                reply = session.add(
                    Message(role="assistant", content=partial + "\n\n[interrompido]", model_key=profile.key)
                )
                self.store.add_message(session.id, reply)
            self.audit.record(
                "llm_call",
                session.id,
                **base_audit,
                ok=False,
                error_kind="cancelled",
                completion_chars=len(partial),
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
            raise
