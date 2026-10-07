"""Gateway de políticas: o único caminho entre um pedido do modelo e a execução de uma ferramenta.

Cada pedido passa por estas etapas, nesta ordem, e para na primeira que recusar:

    1. a ferramenta existe e está no perfil da sessão?
    2. os argumentos são JSON válido e passam na validação Pydantic?
    3. o risco exige aprovação humana? (sem aprovação, não roda)
    4. execução com tempo limite e resultado com tamanho máximo
    5. auditoria (sempre, inclusive das recusas)

Uma recusa não é um erro do programa: vira um texto "ERRO: ..." que volta
para o modelo, para ele corrigir o pedido ou explicar ao usuário.

As ferramentas são funções síncronas comuns. O gateway as roda numa
thread separada (`asyncio.to_thread`) para a interface não travar enquanto
elas trabalham. Se o tempo limite estoura, o modelo recebe o erro na hora;
a thread em si não pode ser interrompida à força e termina sozinha depois.
Por isso ferramentas que podem demorar devem ter limites próprios.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from secretario.audit.log import AuditLog
from secretario.config import Settings
from secretario.core.session import Session
from secretario.core.types import ToolCall
from secretario.tools.base import Tool, ToolContext, ToolError
from secretario.tools.registry import ToolRegistry

log = logging.getLogger(__name__)

# Valores de "decision" na auditoria e nos eventos
EXECUTED = "executed"
UNKNOWN_TOOL = "denied_unknown_tool"
NOT_IN_PROFILE = "denied_not_in_profile"
INVALID_ARGS = "invalid_args"
NEEDS_APPROVAL = "denied_needs_approval"
REJECTED = "denied_by_user"
TIMEOUT = "timeout"
FAILED = "error"
CANCELLED = "cancelled"

# Na auditoria, um argumento maior que isto vira só o seu tamanho:
# a trilha registra o que foi pedido, não o conteúdo (ex.: o texto de uma nota).
AUDIT_ARG_LIMIT = 200

# Quem aprova ações de risco: recebe a sessão, a ferramenta e os argumentos
# completos e devolve True para aprovar. A interface ganha os botões na etapa 3
# da V1; até lá é None e toda ferramenta que precisa de aprovação é recusada.
Approver = Callable[[Session, Tool, dict[str, Any]], Awaitable[bool]]


@dataclass
class ToolOutcome:
    decision: str
    content: str  # o que volta para o modelo
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.decision == EXECUTED


class ToolGateway:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        audit: AuditLog,
        *,
        approver: Approver | None = None,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ):
        self.settings = settings
        self.registry = registry
        self.audit = audit
        self.approver = approver
        self._now = now

    async def execute(self, session: Session, call: ToolCall, *, step: int) -> ToolOutcome:
        started = time.perf_counter()
        tool = self.registry.get(call.name)
        record: dict[str, Any] = {
            "tool": call.name,
            "call_id": call.id,
            "step": step,
            "risk": tool.risk.value if tool else None,
            "label": session.label.nome,
            "tool_profile": session.tool_profile,
        }

        def finish(decision: str, content: str, **extra: Any) -> ToolOutcome:
            duration = time.perf_counter() - started
            self.audit.record(
                "tool_call",
                session.id,
                **record,
                decision=decision,
                ok=decision == EXECUTED,
                result_chars=len(content),
                duration_ms=round(duration * 1000),
                **extra,
            )
            return ToolOutcome(decision, content, duration)

        # 1. existe e está no perfil?
        if tool is None:
            return finish(UNKNOWN_TOOL, f"ERRO: a ferramenta '{call.name}' não existe.")
        allowed = {t.name for t in self.registry.for_profile(session.tool_profile)}
        if call.name not in allowed:
            return finish(NOT_IN_PROFILE, f"ERRO: a ferramenta '{call.name}' não está disponível nesta conversa.")

        # 2. argumentos válidos?
        try:
            raw = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            record["args"] = _summarize_value(call.arguments)
            return finish(INVALID_ARGS, f"ERRO: os argumentos de '{call.name}' não são um JSON válido.")
        if raw is None:
            raw = {}
        record["args"] = _summarize_args(raw) if isinstance(raw, dict) else _summarize_value(raw)
        if not isinstance(raw, dict):
            return finish(INVALID_ARGS, f"ERRO: os argumentos de '{call.name}' precisam ser um objeto JSON.")
        try:
            args = tool.args_model.model_validate(raw)
        except ValidationError as exc:
            return finish(INVALID_ARGS, f"ERRO: argumentos inválidos para '{call.name}': {_validation_text(exc)}")

        # 3. aprovação humana
        if tool.needs_approval:
            if self.approver is None:
                return finish(
                    NEEDS_APPROVAL,
                    f"ERRO: '{call.name}' precisa de aprovação do usuário, e a aprovação ainda não está "
                    "disponível nesta versão. Explique ao usuário o que você faria.",
                )
            if not await self.approver(session, tool, raw):
                return finish(REJECTED, f"ERRO: o usuário não aprovou '{call.name}'. Não tente de novo sem ele pedir.")

        # 4. execução com tempo limite
        ctx = ToolContext(session_id=session.id, workspace=self.settings.workspace_path, now=self._now)
        timeout = tool.timeout_s or self.settings.tools.timeout_s
        try:
            result = await asyncio.wait_for(asyncio.to_thread(tool.func, args, ctx), timeout)
        except TimeoutError:
            return finish(TIMEOUT, f"ERRO: '{call.name}' passou do tempo limite de {timeout:g}s.")
        except ToolError as exc:
            return finish(FAILED, f"ERRO: {exc}", error_kind="tool_error")
        except asyncio.CancelledError:
            # Usuário clicou em parar durante a execução. Registra e deixa o cancelamento seguir.
            finish(CANCELLED, "")
            raise
        except Exception as exc:
            log.exception("Falha inesperada na ferramenta %s", call.name)
            return finish(
                FAILED, f"ERRO: falha interna na ferramenta '{call.name}'.", error_kind=type(exc).__name__
            )

        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1)
        limit = self.settings.tools.max_result_chars
        truncated = len(text) > limit
        if truncated:
            text = text[:limit] + f"\n[... resultado cortado: {len(text)} caracteres no total ...]"
        return finish(EXECUTED, text, truncated=truncated)


def _summarize_value(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= AUDIT_ARG_LIMIT else f"<texto com {len(value)} caracteres>"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    size = len(json.dumps(value, ensure_ascii=False, default=str))
    return value if size <= AUDIT_ARG_LIMIT else f"<valor com {size} caracteres>"


def _summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    return {str(k)[:AUDIT_ARG_LIMIT]: _summarize_value(v) for k, v in args.items()}


def _validation_text(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        where = ".".join(str(x) for x in err["loc"]) or "(argumentos)"
        if err["type"] == "extra_forbidden":
            parts.append(f"o argumento '{where}' não existe")
        elif err["type"] == "missing":
            parts.append(f"falta o argumento '{where}'")
        else:
            parts.append(f"'{where}': {err['msg']}")
    return "; ".join(parts)
