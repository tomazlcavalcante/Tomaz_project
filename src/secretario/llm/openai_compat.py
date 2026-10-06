"""Cliente para qualquer servidor com API compatível com a da OpenAI.

Serve para o Ollama local (http://127.0.0.1:11434/v1), para o Gemini
(https://generativelanguage.googleapis.com/v1beta/openai/) e para a maioria
dos provedores. Trocar de modelo é trocar o perfil em config/settings.toml.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI

from secretario.config import ModelProfile
from secretario.core.types import Message
from secretario.llm.base import Delta, Done, LLMError, LLMEvent
from secretario.llm.think_filter import ThinkFilter
from secretario.secrets import SecretNotFound, get_secret

NO_KEY = "sem-chave"  # o SDK exige um valor; servidores locais o ignoram


class OpenAICompatClient:
    def __init__(self, profile: ModelProfile, *, http_client: httpx.AsyncClient | None = None):
        self.profile = profile
        self._http_client = http_client
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            api_key = NO_KEY
            if self.profile.api_key_secret:
                try:
                    api_key = get_secret(self.profile.api_key_secret)
                except SecretNotFound as exc:
                    raise LLMError(str(exc), kind="missing_secret") from exc
            self._client = AsyncOpenAI(
                base_url=self.profile.base_url,
                api_key=api_key,
                timeout=self.profile.timeout_s,
                max_retries=1,
                http_client=self._http_client,
            )
        return self._client

    def _request_kwargs(self, messages: Sequence[Message]) -> dict[str, Any]:
        p = self.profile
        kwargs: dict[str, Any] = {
            "model": p.model,
            "messages": [m.to_api() for m in messages],
            "stream": True,
        }
        if p.stream_usage:
            kwargs["stream_options"] = {"include_usage": True}
        if p.temperature is not None:
            kwargs["temperature"] = p.temperature
        if p.max_output_tokens is not None:
            kwargs["max_tokens"] = p.max_output_tokens
        if p.reasoning_effort is not None:
            kwargs["reasoning_effort"] = p.reasoning_effort
        return kwargs

    async def stream_chat(self, messages: Sequence[Message]) -> AsyncIterator[LLMEvent]:
        client = self._get_client()
        think = ThinkFilter()
        model_name = self.profile.model
        finish_reason: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None

        try:
            stream = await client.chat.completions.create(**self._request_kwargs(messages))
            async for chunk in stream:
                model_name = getattr(chunk, "model", None) or model_name
                if chunk.usage is not None:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                for choice in chunk.choices or []:
                    # O raciocínio ("reasoning"), quando existe, vem em campo
                    # separado e é ignorado de propósito: só o texto final segue.
                    text = choice.delta.content if choice.delta else None
                    if text:
                        visible = think.feed(text)
                        if visible:
                            yield Delta(visible)
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
        except openai.APITimeoutError as exc:  # antes de APIConnectionError: é subclasse dela
            raise LLMError(
                f"O modelo '{self.profile.display_name}' demorou mais de "
                f"{self.profile.timeout_s:.0f}s para responder.",
                kind="timeout",
            ) from exc
        except openai.APIConnectionError as exc:
            raise LLMError(self._connection_hint(), kind="connection") from exc
        except openai.AuthenticationError as exc:
            raise LLMError(
                f"Chave recusada pelo provedor de '{self.profile.display_name}'. "
                f"Confira o segredo {self.profile.api_key_secret!r}.",
                kind="auth",
            ) from exc
        except openai.PermissionDeniedError as exc:
            raise LLMError(
                f"Acesso negado por '{self.profile.display_name}': {_short(exc)}", kind="auth"
            ) from exc
        except openai.RateLimitError as exc:
            raise LLMError(
                f"Limite de uso atingido em '{self.profile.display_name}' "
                "(no plano gratuito, os limites por minuto e por dia são baixos). "
                "Espere um pouco ou use /modelo local.",
                kind="rate_limit",
            ) from exc
        except openai.NotFoundError as exc:
            raise LLMError(self._not_found_hint(exc), kind="not_found") from exc
        except openai.APIStatusError as exc:
            raise LLMError(
                f"Erro {exc.status_code} de '{self.profile.display_name}': {_short(exc)}",
                kind="status",
            ) from exc

        rest = think.flush()
        if rest:
            yield Delta(rest)
        yield Done(
            model=model_name,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def _connection_hint(self) -> str:
        if self.profile.is_local:
            return (
                f"Não consegui falar com o servidor local em {self.profile.base_url}. "
                "O Ollama está aberto? (no Windows, veja o ícone perto do relógio)"
            )
        return f"Sem conexão com '{self.profile.display_name}' ({self.profile.base_url})."

    def _not_found_hint(self, exc: Exception) -> str:
        if self.profile.is_local:
            return (
                f"O modelo '{self.profile.model}' não está instalado no Ollama. "
                f"Rode: ollama pull {self.profile.model}"
            )
        return (
            f"O provedor não reconheceu o modelo '{self.profile.model}'. "
            "Confira o nome em config/settings.toml. Detalhe: " + _short(exc)
        )


def _short(exc: Exception, limit: int = 300) -> str:
    text = str(exc).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"
