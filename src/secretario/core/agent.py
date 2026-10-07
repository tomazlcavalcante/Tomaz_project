"""Núcleo do agente.

Um turno é: registrar a mensagem do usuário e repetir, até `max_steps` vezes,

    escolher o modelo pelo sigilo e montar o contexto (a cada passo)
    chamar o modelo, oferecendo as ferramentas do perfil da sessão
    se ele respondeu com texto: registrar a resposta e terminar
    se pediu ferramentas: executar cada pedido pelo gateway
        (que valida, autoriza, pede aprovação e audita) e voltar ao início

A aprovação é pedida pela interface: quem chama `run_turn` passa um
`approver`, uma função que mostra o pedido ao usuário e devolve a resposta.
Sem ele, nada que precise de aprovação executa.

Na última volta as ferramentas não são oferecidas, para o modelo ser
obrigado a responder. Os pedidos e resultados de ferramenta valem só
durante o turno; no histórico fica a resposta final, e na auditoria, os
metadados de cada chamada.

Nada aqui conhece Chainlit, terminal ou HTTP: a interface consome os
eventos de `run_turn`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime

from secretario.audit.log import AuditLog
from secretario.config import Settings
from secretario.core.context import ContextOverflow, build_context, render_system_prompt
from secretario.core.session import LabelError, Session
from secretario.core.types import (
    AgentEvent,
    Label,
    Message,
    Notice,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnDone,
    TurnError,
)
from secretario.llm.base import Delta, Done, LLMError, ToolRequest
from secretario.llm.router import ModelRouter, RouteError
from secretario.storage.db import ConversationStore
from secretario.tools.gateway import Approver, ToolGateway
from secretario.tools.registry import ToolRegistry

HELP = """Comandos disponíveis:
/status            mostra modelo, nível de sigilo e tamanho da conversa
/modelos           lista os modelos configurados
/modelo <chave>    troca o modelo preferido (ex.: /modelo gemini)
/sigilo <nível>    sobe o nível de sigilo: publico, interno, confidencial, restrito
/ferramentas       lista as ferramentas desta conversa
/ferramentas <p>   troca o perfil de ferramentas (ex.: /ferramentas nenhum)
/historico         lista as conversas recentes
/ajuda             mostra esta lista"""


class Agent:
    def __init__(
        self,
        settings: Settings,
        router: ModelRouter,
        store: ConversationStore,
        audit: AuditLog,
        tools: ToolRegistry,
        gateway: ToolGateway,
    ):
        self.settings = settings
        self.router = router
        self.store = store
        self.audit = audit
        self.tools = tools
        self.gateway = gateway
        self._system_prompt = settings.system_prompt()

    # ------------------------------------------------------------------ sessões
    def new_session(self) -> Session:
        session = Session(
            model_key=self.settings.app.default_model,
            label=self.settings.app.default_label,
            tool_profile=self.settings.tools.profile,
        )
        self.store.save_session(session)
        self.audit.record(
            "session_started",
            session.id,
            model_key=session.model_key,
            label=session.label.nome,
            tool_profile=session.tool_profile,
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
                f"Perfil de ferramentas: {session.tool_profile}\n"
                f"Conteúdo não confiável na conversa: {'sim' if session.tainted else 'não'}\n"
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

        if cmd == "/ferramentas":
            if arg:
                if arg not in self.tools.profiles:
                    return f"Perfil desconhecido: {arg!r}. Perfis: {', '.join(self.tools.profiles)}."
                session.tool_profile = arg
                self.store.save_session(session)
                self.audit.record("tool_profile_selected", session.id, tool_profile=arg)
            lines = [f"Perfil de ferramentas: {session.tool_profile}"]
            tools = self.tools.for_profile(session.tool_profile)
            if not tools:
                lines.append("  (nenhuma ferramenta: o modelo só conversa)")
            for t in tools:
                approval = ", pede aprovação" if t.needs_approval else ""
                lines.append(f"  {t.name:<14} risco: {t.risk.value}{approval}")
            lines.append(f"Perfis: {', '.join(self.tools.profiles)}. Troque com /ferramentas <perfil>.")
            return "\n".join(lines)

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
    async def run_turn(
        self, session: Session, user_text: str, approver: Approver | None = None
    ) -> AsyncIterator[AgentEvent]:
        user_msg = session.add(Message(role="user", content=user_text))
        self.store.add_message(session.id, user_msg)
        self.store.set_title_if_empty(session.id, user_text.strip().splitlines()[0] if user_text.strip() else "")

        specs = [t.openai_spec() for t in self.tools.for_profile(session.tool_profile)]
        specs_chars = len(json.dumps(specs, ensure_ascii=False)) if specs else 0
        max_steps = self.settings.tools.max_steps
        system = render_system_prompt(self._system_prompt, datetime.now().astimezone())

        pending: list[Message] = []  # pedidos e resultados de ferramenta deste turno
        shown: list[str] = []  # todo o texto que o usuário já viu neste turno
        turn_started = time.perf_counter()
        tool_count = 0
        warned_fallback = False
        finished = False
        phase = "model"  # "model" ou "tool": onde o turno estava se for interrompido
        base_audit: dict = {}
        step_started = turn_started

        try:
            for step in range(1, max_steps + 1):
                phase = "model"
                try:
                    decision = self.router.route(session)
                except RouteError as exc:
                    self.audit.record("route_refused", session.id, label=session.label.nome, reason=str(exc))
                    yield TurnError(str(exc))
                    return

                profile = decision.profile
                if decision.fell_back and not warned_fallback:
                    warned_fallback = True
                    self.audit.record(
                        "route_fallback",
                        session.id,
                        preferred=session.model_key,
                        used=profile.key,
                        label=session.label.nome,
                    )
                    yield Notice(decision.reason or "Usando o modelo local de reserva.")

                # Na última volta, sem ferramentas: o modelo precisa responder.
                offer = specs if specs and step < max_steps else None
                try:
                    ctx = build_context(
                        session,
                        system,
                        max_chars=profile.max_context_chars,
                        max_messages=self.settings.limits.max_history_messages,
                        pending=pending,
                        reserved_chars=specs_chars if offer else 0,
                    )
                except ContextOverflow as exc:
                    self.audit.record("context_overflow", session.id, model_key=profile.key, step=step)
                    yield TurnError(str(exc))
                    return
                if ctx.truncated_last and step == 1:
                    yield Notice("Sua mensagem é maior que o limite de contexto deste modelo; o meio dela foi omitido.")

                client = self.router.client_for(profile)
                step_started = time.perf_counter()
                parts: list[str] = []
                calls = []
                done: Done | None = None
                base_audit = {
                    "model_key": profile.key,
                    "model": profile.model,
                    "is_local": profile.is_local,
                    "label": session.label.nome,
                    "prompt_chars": ctx.chars,
                    "history_dropped": ctx.dropped,
                    "step": step,
                    "tools_offered": len(offer or []),
                }

                try:
                    async for event in client.stream_chat(ctx.messages, tools=offer):
                        if isinstance(event, Delta):
                            if not parts and shown and not shown[-1].endswith("\n"):
                                # separa o texto deste passo do texto do passo anterior
                                shown.append("\n\n")
                                yield TextDelta("\n\n")
                            parts.append(event.text)
                            shown.append(event.text)
                            yield TextDelta(event.text)
                        elif isinstance(event, ToolRequest):
                            calls = event.calls
                        elif isinstance(event, Done):
                            done = event
                except LLMError as exc:
                    self.audit.record(
                        "llm_call",
                        session.id,
                        **base_audit,
                        ok=False,
                        error_kind=exc.kind,
                        latency_ms=round((time.perf_counter() - step_started) * 1000),
                    )
                    yield TurnError(str(exc))
                    return

                done = done or Done(model=profile.model, finish_reason=None, prompt_tokens=None, completion_tokens=None)
                step_text = "".join(parts).strip()
                self.audit.record(
                    "llm_call",
                    session.id,
                    **base_audit,
                    ok=True,
                    provider_model=done.model,
                    latency_ms=round((time.perf_counter() - step_started) * 1000),
                    completion_chars=len(step_text),
                    prompt_tokens=done.prompt_tokens,
                    completion_tokens=done.completion_tokens,
                    finish_reason=done.finish_reason,
                    tool_calls=len(calls),
                )

                if not calls:
                    answer = "".join(shown).strip()
                    reply = session.add(Message(role="assistant", content=answer, model_key=profile.key))
                    self.store.add_message(session.id, reply)
                    finished = True
                    yield TurnDone(
                        model_key=profile.key,
                        model_display=profile.display_name,
                        is_local=profile.is_local,
                        latency_s=time.perf_counter() - turn_started,
                        prompt_tokens=done.prompt_tokens,
                        completion_tokens=done.completion_tokens,
                        finish_reason=done.finish_reason,
                        tool_calls=tool_count,
                    )
                    return

                if offer is None:
                    # Pediu ferramenta sem que nenhuma tivesse sido oferecida.
                    self.audit.record("tool_request_refused", session.id, step=step, max_steps=max_steps)
                    if specs:
                        yield TurnError(
                            f"O modelo continuou pedindo ferramentas depois do limite de {max_steps} passos. "
                            "Tente um pedido mais simples ou divida-o em partes."
                        )
                    else:
                        yield TurnError("O modelo tentou usar uma ferramenta, mas esta conversa não tem ferramentas.")
                    return

                pending.append(Message(role="assistant", content=step_text, model_key=profile.key, tool_calls=calls))
                phase = "tool"
                for call in calls:
                    yield ToolStarted(call_id=call.id, name=call.name, arguments=call.arguments)
                    outcome = await self.gateway.execute(session, call, step=step, approver=approver)
                    tool_count += 1
                    pending.append(Message(role="tool", content=outcome.content, tool_call_id=call.id))
                    if outcome.tainted_now:
                        self.store.save_session(session)
                        self.audit.record("session_tainted", session.id, tool=call.name)
                        yield Notice(
                            "Esta conversa agora contém conteúdo de arquivo, que pode trazer instruções "
                            "maliciosas. Confira com atenção qualquer ação que eu pedir para aprovar."
                        )
                    yield ToolFinished(
                        call_id=call.id,
                        name=call.name,
                        ok=outcome.ok,
                        decision=outcome.decision,
                        output=outcome.content,
                        duration_s=outcome.duration_s,
                    )
                if step + 1 == max_steps:
                    yield Notice(
                        f"Limite de {max_steps} passos por mensagem: o modelo vai responder agora, sem mais ferramentas."
                    )
        except (asyncio.CancelledError, GeneratorExit):
            # Usuário clicou em parar (a tarefa é cancelada, ou quem consome fecha
            # este gerador): guarda o que já tinha chegado e registra. Sem await aqui.
            if finished:
                raise
            partial = "".join(shown).strip()
            if partial:
                reply = session.add(
                    Message(
                        role="assistant",
                        content=partial + "\n\n[interrompido]",
                        model_key=base_audit.get("model_key"),
                    )
                )
                self.store.add_message(session.id, reply)
            if phase == "model" and base_audit:
                self.audit.record(
                    "llm_call",
                    session.id,
                    **base_audit,
                    ok=False,
                    error_kind="cancelled",
                    completion_chars=len(partial),
                    latency_ms=round((time.perf_counter() - step_started) * 1000),
                )
            self.audit.record("turn_cancelled", session.id, phase=phase, tool_calls=tool_count)
            raise
