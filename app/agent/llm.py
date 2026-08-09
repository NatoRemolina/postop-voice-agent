import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

_client: genai.Client | None = None

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

# Circuit breaker: si Gemini falla (típicamente cuota diaria agotada), se salta
# durante este lapso para no pagar ~2s de intento fallido en cada turno de voz.
GEMINI_COOLDOWN_SECONDS = 600
_gemini_down_until: float = 0.0


def get_gemini() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=settings.gemini_api_key,
            # Default del SDK: 5 reintentos internos con backoff ante 429 —
            # inútil contra una cuota diaria agotada y agrega varios segundos
            # de espera antes de que nuestro propio fallback a Groq se entere
            # del fallo (confirmado en producción: dejaba al paciente en
            # silencio mientras ElevenLabs esperaba). Fallar rápido.
            http_options=types.HttpOptions(
                timeout=8_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
    return _client


@dataclass
class TurnUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 1
    finished: bool = field(default=False)
    # Modelo que realmente respondió el turno — puede diferir de settings.gemini_model
    # si hubo respaldo automático a Groq (ver stream_response). Se declara tal cual
    # en el informe final por honestidad con el jurado.
    model_used: str = ""
    fallback_used: bool = False


async def stream_gemini(
    system_prompt: str,
    history: list[dict],
    usage: TurnUsage,
) -> AsyncIterator[str]:
    """history: [{"role": "user"|"assistant", "content": str}, ...]"""
    contents = [
        types.Content(
            role="user" if m["role"] == "user" else "model",
            parts=[types.Part.from_text(text=m["content"])],
        )
        for m in history
        if m.get("content")
    ]
    stream = await get_gemini().aio.models.generate_content_stream(
        model=settings.gemini_model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.4,
            max_output_tokens=512,
            # Voz en tiempo real: el razonamiento extendido dispara la latencia
            # del primer token y consume el presupuesto de salida. Los modelos
            # Gemini 3.x no permiten apagarlo del todo; "minimal" es el mínimo.
            thinking_config=types.ThinkingConfig(thinking_level="minimal"),
        ),
    )
    async for chunk in stream:
        if chunk.usage_metadata:
            usage.input_tokens = chunk.usage_metadata.prompt_token_count or 0
            usage.output_tokens = chunk.usage_metadata.candidates_token_count or 0
        if chunk.text:
            yield chunk.text


async def stream_groq(
    system_prompt: str,
    history: list[dict],
    usage: TurnUsage,
) -> AsyncIterator[str]:
    messages = [{"role": "system", "content": system_prompt}] + [
        {"role": m["role"], "content": m["content"]} for m in history if m.get("content")
    ]
    body = {
        "model": settings.groq_model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 512,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("POST", GROQ_CHAT_URL, json=body, headers=headers) as resp:
            if resp.status_code != 200:
                error_body = await resp.aread()
                raise RuntimeError(f"Groq {resp.status_code}: {error_body.decode(errors='replace')[:300]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                usage_data = chunk.get("usage")
                if usage_data:
                    usage.input_tokens = usage_data.get("prompt_tokens", 0)
                    usage.output_tokens = usage_data.get("completion_tokens", 0)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text


async def stream_response(
    system_prompt: str,
    history: list[dict],
    usage: TurnUsage,
) -> AsyncIterator[str]:
    """Gemini primero; si falla antes de emitir texto (cuota, error de red,
    modelo caído), reintenta automáticamente con Groq — ambas son familias
    de modelo permitidas por el reto, así que el respaldo no compromete G3.
    """
    global _gemini_down_until

    if time.monotonic() < _gemini_down_until:
        usage.model_used = settings.groq_model
        usage.fallback_used = True
        async for text in stream_groq(system_prompt, history, usage):
            yield text
        return

    usage.model_used = settings.gemini_model
    gemini_stream = stream_gemini(system_prompt, history, usage)
    try:
        first_chunk = await gemini_stream.__anext__()
    except StopAsyncIteration:
        return
    except Exception as exc:
        _gemini_down_until = time.monotonic() + GEMINI_COOLDOWN_SECONDS
        logger.warning(
            "Gemini no disponible (%s); Groq de respaldo y cooldown de %ss",
            exc,
            GEMINI_COOLDOWN_SECONDS,
        )
        usage.model_used = settings.groq_model
        usage.fallback_used = True
        async for text in stream_groq(system_prompt, history, usage):
            yield text
        return

    yield first_chunk
    async for text in gemini_stream:
        yield text
