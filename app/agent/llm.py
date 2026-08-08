import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

_client: genai.Client | None = None


def get_gemini() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


@dataclass
class TurnUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 1
    finished: bool = field(default=False)


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
            max_output_tokens=400,
        ),
    )
    async for chunk in stream:
        if chunk.usage_metadata:
            usage.input_tokens = chunk.usage_metadata.prompt_token_count or 0
            usage.output_tokens = chunk.usage_metadata.candidates_token_count or 0
        if chunk.text:
            yield chunk.text
    usage.finished = True
