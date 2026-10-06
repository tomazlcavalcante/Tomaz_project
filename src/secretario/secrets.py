"""Segredos (chaves de API) fora do código, do git e do prompt.

Ordem de busca:
1. cofre do sistema operacional, via `keyring`
   (Windows: Gerenciador de Credenciais; macOS: Keychain; Linux: Secret Service);
2. variável de ambiente com o mesmo nome (útil em servidores Ubuntu sem cofre).

Uso pela linha de comando:
    uv run secretario-secrets set GEMINI_API_KEY
    uv run secretario-secrets status GEMINI_API_KEY
    uv run secretario-secrets delete GEMINI_API_KEY
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

SERVICE = "secretario"


class SecretNotFound(Exception):
    pass


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError

        return keyring, KeyringError
    except Exception:  # pragma: no cover - keyring ausente ou quebrado
        return None, Exception


def get_secret(name: str) -> str:
    keyring, keyring_error = _keyring()
    if keyring is not None:
        try:
            value = keyring.get_password(SERVICE, name)
            if value:
                return value
        except keyring_error:
            pass  # sem backend de cofre (comum em servidor Linux): tenta o ambiente
    value = os.environ.get(name)
    if value:
        return value
    raise SecretNotFound(
        f"Segredo {name!r} não encontrado. Rode: uv run secretario-secrets set {name}"
    )


def secret_source(name: str) -> str | None:
    """De onde o segredo viria ('cofre' ou 'ambiente'), sem revelar o valor."""
    keyring, keyring_error = _keyring()
    if keyring is not None:
        try:
            if keyring.get_password(SERVICE, name):
                return "cofre do sistema"
        except keyring_error:
            pass
    if os.environ.get(name):
        return "variável de ambiente"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="secretario-secrets", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("set", "status", "delete"):
        p = sub.add_parser(cmd)
        p.add_argument("name", help="nome do segredo, ex.: GEMINI_API_KEY")
    args = parser.parse_args(argv)

    keyring, keyring_error = _keyring()

    if args.cmd == "status":
        source = secret_source(args.name)
        print(f"{args.name}: {'configurado (' + source + ')' if source else 'não configurado'}")
        return 0

    if keyring is None:
        print("O pacote keyring não está disponível; use uma variável de ambiente.", file=sys.stderr)
        return 1

    try:
        if args.cmd == "set":
            value = getpass.getpass(f"Cole o valor de {args.name} (não aparece na tela): ").strip()
            if not value:
                print("Nada foi salvo: valor vazio.", file=sys.stderr)
                return 1
            keyring.set_password(SERVICE, args.name, value)
            print(f"{args.name} salvo no cofre do sistema.")
        elif args.cmd == "delete":
            keyring.delete_password(SERVICE, args.name)
            print(f"{args.name} removido do cofre do sistema.")
    except keyring_error as exc:
        print(
            f"O cofre do sistema não está disponível ({exc.__class__.__name__}). "
            f"Defina a variável de ambiente {args.name} no lugar.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
