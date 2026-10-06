from __future__ import annotations

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from secretario.ui.origin_guard import LocalOriginGuard, local_addresses


def _app():
    app = Starlette(routes=[Route("/", lambda request: PlainTextResponse("ok"))])
    hosts, origins = local_addresses(8000)
    return LocalOriginGuard(app, hosts, origins)


async def _get(headers):
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
        return await client.get("/", headers=headers)


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 200),  # navegação direta: sem Origin
        ({"Origin": "http://127.0.0.1:8000"}, 200),
        ({"Host": "localhost:8000", "Origin": "http://localhost:8000"}, 200),
        ({"Origin": "https://site-malicioso.com"}, 403),  # outra página no navegador
        ({"Origin": "null"}, 403),  # arquivo local / iframe isolado
        ({"Host": "site-malicioso.com:8000"}, 403),  # DNS rebinding
        ({"Origin": "http://127.0.0.1:9999"}, 403),  # outra porta local
    ],
)
async def test_guard(headers, status):
    response = await _get(headers)
    assert response.status_code == status
