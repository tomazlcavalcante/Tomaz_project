"""Sobe a interface web com as travas de segurança da V0.

    uv run secretario-ui
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from secretario.config import ConfigError, load_settings

# Variáveis que fariam o Chainlit enviar conversas a um banco ou serviço
# externo (camadas de dados do próprio Chainlit). A V0 guarda tudo no SQLite local.
_EXTERNAL_DATA_ENV = ("LITERAL_API_KEY", "DATABASE_URL")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 1

    for name in _EXTERNAL_DATA_ENV:
        if os.environ.pop(name, None):
            print(f"Aviso: ignorando {name} (a V0 não envia conversas para fora).", file=sys.stderr)
    os.environ["TRACELOOP_TELEMETRY"] = "false"

    os.environ["CHAINLIT_HOST"] = settings.ui.host
    os.environ["CHAINLIT_PORT"] = str(settings.ui.port)
    os.environ["CHAINLIT_APP_ROOT"] = str(settings.root)
    os.environ["SECRETARIO_HOME"] = str(settings.root)
    os.chdir(settings.root)

    # Importado só agora: o Chainlit lê CHAINLIT_APP_ROOT ao ser importado.
    from chainlit.cli import run_chainlit

    target = Path(__file__).with_name("chainlit_app.py")
    print(f"Secretário V0 em http://{settings.ui.host}:{settings.ui.port}  (Ctrl+C para encerrar)")
    run_chainlit(str(target))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
