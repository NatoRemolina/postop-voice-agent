import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.llm import TurnUsage, stream_gemini
from app.agent.prompts import DEFAULT_PATIENT_CONTEXT, SYSTEM_PROMPT, format_rag_context
from app.config import settings
from app.rag.retrieve import retrieve
from app.storage import append_jsonl

logger = logging.getLogger(__name__)
router = APIRouter()

CONTROL_RE = re.compile(r"<control>(.*?)</control>", re.DOTALL)
CONTROL_TAG = "<control>"


def _chunk_payload(chat_id: str, created: int, content: str | None, finish: str | None):
    delta = {"content": content} if content is not None else {}
    return {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": settings.gemini_model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_rag_query(history: list[dict]) -> str:
    """Last patient utterance, prefixed with the agent's question when it's a short reply."""
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    if len(last_user) < 25:
        last_assistant = next(
            (m["content"] for m in reversed(history) if m["role"] == "assistant"), ""
        )
        return f"{last_assistant} {last_user}".strip()
    return last_user


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    t_start = time.perf_counter()

    raw_messages = body.get("messages", [])
    extra = body.get("elevenlabs_extra_body") or {}
    conversation_id = (
        extra.get("conversation_id")
        or body.get("user")
        or f"conv_{uuid.uuid4().hex[:10]}"
    )
    patient_context = extra.get("patient_context") or DEFAULT_PATIENT_CONTEXT

    history = [
        {"role": m["role"], "content": m.get("content") or ""}
        for m in raw_messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    if not history:
        history = [{"role": "user", "content": "Hola"}]

    rag_query = _build_rag_query(history)
    chunks = retrieve(rag_query)
    system_prompt = SYSTEM_PROMPT.format(
        patient_context=patient_context,
        rag_context=format_rag_context(chunks),
    )

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    usage = TurnUsage()

    async def event_stream():
        t_first_token = None
        spoken_parts: list[str] = []
        held = ""  # tail holdback so <control>... never reaches the TTS
        control_buf: str | None = None

        yield _sse(_chunk_payload(chat_id, created, "", None))
        try:
            async for text in stream_gemini(system_prompt, history, usage):
                if t_first_token is None:
                    t_first_token = time.perf_counter()
                if control_buf is not None:
                    control_buf += text
                    continue
                held += text
                idx = held.find(CONTROL_TAG)
                if idx != -1:
                    emit = held[:idx]
                    control_buf = held[idx:]
                    held = ""
                    if emit:
                        spoken_parts.append(emit)
                        yield _sse(_chunk_payload(chat_id, created, emit, None))
                    continue
                # keep a tail that could be the start of a partial <control> tag
                keep = 0
                for k in range(min(len(CONTROL_TAG) - 1, len(held)), 0, -1):
                    if held.endswith(CONTROL_TAG[:k]):
                        keep = k
                        break
                emit = held[: len(held) - keep] if keep else held
                held = held[len(held) - keep :] if keep else ""
                if emit:
                    spoken_parts.append(emit)
                    yield _sse(_chunk_payload(chat_id, created, emit, None))
        except Exception:
            logger.exception("gemini stream failed")
            fallback = (
                "Disculpe, tuve un problema técnico en este momento. "
                "¿Me repite por favor lo último que me dijo?"
            )
            spoken_parts.append(fallback)
            yield _sse(_chunk_payload(chat_id, created, fallback, None))

        if held:
            spoken_parts.append(held)
            yield _sse(_chunk_payload(chat_id, created, held, None))

        yield _sse(_chunk_payload(chat_id, created, None, "stop"))
        yield "data: [DONE]\n\n"

        control = None
        if control_buf:
            m = CONTROL_RE.search(control_buf)
            if m:
                try:
                    control = json.loads(m.group(1))
                except json.JSONDecodeError:
                    logger.warning("unparseable control block: %s", m.group(1)[:200])

        t_end = time.perf_counter()
        append_jsonl(
            "turns.jsonl",
            {
                "conversation_id": conversation_id,
                "turn_index": sum(1 for m in history if m["role"] == "user"),
                "user_text": next(
                    (m["content"] for m in reversed(history) if m["role"] == "user"), ""
                ),
                "rag_query": rag_query,
                "rag_sources": [
                    {"source": c.source, "scenario": c.scenario, "page": c.page,
                     "score": round(c.score, 4)}
                    for c in chunks
                ],
                "spoken_text": "".join(spoken_parts).strip(),
                "control": control,
                "latency_first_token_ms": round(
                    ((t_first_token or t_end) - t_start) * 1000
                ),
                "latency_total_ms": round((t_end - t_start) * 1000),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "model_calls": usage.model_calls,
                "rag_queries": 1,
                "model": settings.gemini_model,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")
