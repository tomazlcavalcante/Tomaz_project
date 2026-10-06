"""Remove blocos <think>...</think> de um fluxo de texto.

O Ollama normalmente devolve o raciocínio em um campo separado, mas
alguns modelos e servidores o colocam no próprio texto. O filtro funciona
mesmo quando as marcas chegam partidas entre dois pedaços do streaming.
"""

from __future__ import annotations

OPEN = "<think>"
CLOSE = "</think>"


class ThinkFilter:
    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        out: list[str] = []
        while self._buffer:
            if self._inside:
                end = self._buffer.find(CLOSE)
                if end == -1:
                    # Guarda só o suficiente para detectar a marca de fechamento partida
                    self._buffer = self._buffer[-(len(CLOSE) - 1):]
                    break
                self._buffer = self._buffer[end + len(CLOSE):].lstrip("\n")
                self._inside = False
            else:
                start = self._buffer.find(OPEN)
                if start == -1:
                    keep = _partial_suffix(self._buffer, OPEN)
                    out.append(self._buffer[: len(self._buffer) - keep])
                    self._buffer = self._buffer[len(self._buffer) - keep:]
                    break
                out.append(self._buffer[:start])
                self._buffer = self._buffer[start + len(OPEN):]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self._inside else self._buffer
        self._buffer = ""
        return rest


def _partial_suffix(text: str, marker: str) -> int:
    """Tamanho do maior sufixo de `text` que é prefixo de `marker`."""
    for size in range(min(len(text), len(marker) - 1), 0, -1):
        if marker.startswith(text[-size:]):
            return size
    return 0
