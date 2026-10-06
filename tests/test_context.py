from __future__ import annotations

from secretario.core.context import TRUNCATION_MARK, build_context
from secretario.core.session import Session
from secretario.core.types import Message


def _session(*pairs: tuple[str, str]) -> Session:
    s = Session(model_key="local")
    for role, content in pairs:
        s.add(Message(role=role, content=content))
    return s


def test_system_first_and_everything_fits():
    s = _session(("user", "oi"), ("assistant", "olá"), ("user", "tudo bem?"))
    ctx = build_context(s, "SYS", max_chars=1000, max_messages=10)
    assert [m.role for m in ctx.messages] == ["system", "user", "assistant", "user"]
    assert ctx.messages[0].content == "SYS"
    assert ctx.dropped == 0 and not ctx.truncated_last


def test_keeps_newest_messages_within_char_budget():
    s = _session(("user", "a" * 100), ("assistant", "b" * 100), ("user", "c" * 100), ("assistant", "d" * 100), ("user", "e" * 50))
    ctx = build_context(s, "S", max_chars=1 + 260, max_messages=10)
    contents = [m.content[0] for m in ctx.messages[1:]]
    # cabem e(50) + d(100) + c(100); o histórico precisa começar por "user"
    assert contents == ["c", "d", "e"]
    assert ctx.dropped == 2


def test_message_count_limit():
    s = _session(*[("user" if i % 2 == 0 else "assistant", str(i)) for i in range(10)])
    ctx = build_context(s, "S", max_chars=10_000, max_messages=4)
    assert [m.content for m in ctx.messages[1:]] == ["6", "7", "8", "9"]


def test_history_never_starts_with_assistant():
    s = _session(("user", "x" * 500), ("assistant", "curta"), ("user", "pergunta"))
    ctx = build_context(s, "S", max_chars=1 + 100, max_messages=10)
    assert ctx.messages[1].role == "user"
    assert ctx.messages[1].content == "pergunta"


def test_oversized_last_message_is_cut_in_the_middle():
    big = "INICIO" + "x" * 5000 + "FIM"
    s = _session(("user", big))
    ctx = build_context(s, "S", max_chars=1 + 400, max_messages=10)
    text = ctx.messages[-1].content
    assert ctx.truncated_last
    assert text.startswith("INICIO") and text.endswith("FIM") and TRUNCATION_MARK in text
    assert len(text) <= 400


def test_system_prompt_date_is_in_portuguese():
    from datetime import datetime

    from secretario.core.context import render_system_prompt

    text = render_system_prompt("BASE", datetime(2026, 10, 6, 18, 5))
    assert text.startswith("BASE") and "terça-feira, 06/10/2026 18:05" in text
