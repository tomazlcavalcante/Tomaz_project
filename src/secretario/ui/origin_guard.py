"""Bloqueia acessos à interface vindos de outros sites.

Um servidor em 127.0.0.1 não fica protegido só por estar no localhost:
qualquer página aberta no seu navegador pode tentar abrir uma conexão
WebSocket para http://127.0.0.1:8000 e conversar com o agente (foi esse
tipo de falha que afetou o OpenClaw em 2026). O Chainlit aceita qualquer
origem por padrão, então este middleware recusa:

- pedidos cujo cabeçalho Host não é o endereço local esperado
  (protege contra "DNS rebinding");
- pedidos com cabeçalho Origin de qualquer outro site.

Pedidos sem Origin (ex.: abrir a página digitando o endereço) passam.
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def local_addresses(port: int) -> tuple[set[str], set[str]]:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    return hosts, origins


class LocalOriginGuard:
    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str], allowed_origins: Iterable[str]):
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.allowed_origins = {o.lower() for o in allowed_origins}

    def _allowed(self, scope: Scope) -> bool:
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "").lower()
        origin = headers.get("origin")
        if host not in self.allowed_hosts:
            return False
        if origin is not None and origin.lower() not in self.allowed_origins:
            return False
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or self._allowed(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            response = PlainTextResponse("Origem não permitida.", status_code=403)
            await response(scope, receive, send)
            return
        # WebSocket: recusa antes de aceitar a conexão (o servidor responde 403)
        await receive()
        await send({"type": "websocket.close", "code": 1008})
