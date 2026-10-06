from __future__ import annotations

import pytest

from secretario.llm.think_filter import ThinkFilter


def run(chunks):
    f = ThinkFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_passthrough():
    assert run(["Olá, ", "tudo ", "bem?"]) == "Olá, tudo bem?"


def test_removes_block():
    assert run(["<think>raciocínio</think>\nResposta"]) == "Resposta"


@pytest.mark.parametrize("cut", range(1, 30))
def test_markers_split_across_chunks(cut):
    text = "<think>pensando muito</think>Resposta final <b>ok</b>"
    assert run([text[:cut], text[cut:]]) == "Resposta final <b>ok</b>"


def test_char_by_char():
    text = "A<think>x</think>B<thi"
    assert run(list(text)) == "AB<thi"


def test_unclosed_block_is_dropped():
    assert run(["Antes <think>sem fim"]) == "Antes "
