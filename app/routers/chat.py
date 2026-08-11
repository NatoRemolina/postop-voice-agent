import asyncio
import json
import logging
import re
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.agent.llm import TurnUsage, stream_response
from app.agent.prompts import DEFAULT_PATIENT_CONTEXT, SYSTEM_PROMPT, format_rag_context
from app.agent.scenario import detect_scenario
from app.agent.summary import build_call_summary, persist_summary
from app.config import settings
from app.rag.retrieve import retrieve
from app.storage import append_jsonl

if settings.agentic_rag_enabled:
    from app.graph.agent import stream_agentic_response

logger = logging.getLogger(__name__)
router = APIRouter()

CONTROL_RE = re.compile(r"<control>(.*?)</control>", re.DOTALL)
CONTROL_TAG = "<control>"
# Pausas prosódicas que el modelo escribe para el TTS. Verificado contra
# eleven_flash_v2_5: el tag se respeta (+1.15 s reales al pedir 1.5 s) mientras
# que los puntos suspensivos NO añaden silencio, solo cambian la entonación.
BREAK_RE = re.compile(r'<break\s+time="[^"]*"\s*/>')


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


def _strip_breaks(text: str) -> str:
    """Quita los tags de pausa sin dejar el doble espacio del hueco."""
    return re.sub(r"[ \t]{2,}", " ", BREAK_RE.sub(" ", text))


def _for_tts(text: str) -> str:
    """Interruptor de emergencia para las pausas prosódicas. El tag `<break/>`
    solo lo entienden los modelos de la familia v2 (el agente usa
    eleven_flash_v2_5); si alguna vez la capa de voz cambiara de modelo o
    dejara de interpretarlo, el tag se leería EN VOZ ALTA al paciente. Con
    VOICE_PAUSES_ENABLED=false se eliminan del stream sin tocar el prompt ni
    redesplegar lógica."""
    if settings.voice_pauses_enabled:
        return text
    return _strip_breaks(text)


async def _auto_summarize(conversation_id: str) -> None:
    """Se dispara en background (nunca bloquea el SSE) cuando el propio modelo
    marca fin_llamada=true. Cubre el caso donde el webhook de ElevenLabs no
    llega (red, timing) — sin esto, esa llamada se quedaba sin resumen hasta
    que alguien lo pidiera manualmente. Idempotente con el webhook: ambos
    caminos llaman a la misma persist_summary, y `latest_summaries` siempre
    toma el más reciente."""
    try:
        summary = await build_call_summary(conversation_id)
        persist_summary(summary)
    except Exception:
        logger.exception("auto-resumen falló para %s", conversation_id)


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

    scenario = extra.get("scenario") or detect_scenario([m["content"] for m in history])

    rag_query = None
    chunks = []
    system_prompt = None
    if not settings.agentic_rag_enabled:
        rag_query = _build_rag_query(history)
        chunks = retrieve(rag_query, scenario=scenario)
        system_prompt = SYSTEM_PROMPT.format(
            patient_context=patient_context,
            rag_context=format_rag_context(chunks),
        )

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created = int(time.time())
    usage = TurnUsage()
    telemetry: dict = {}

    async def event_stream():
        t_first_token = None
        spoken_parts: list[str] = []
        held = ""  # tail holdback so <control>... never reaches the TTS
        control_buf: str | None = None

        yield _sse(_chunk_payload(chat_id, created, "", None))
        if settings.agentic_rag_enabled:
            stream = stream_agentic_response(history, patient_context, scenario, usage, telemetry)
        else:
            stream = stream_response(system_prompt, history, usage)
        try:
            async for text in stream:
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
                # Un <break .../> partido entre dos chunks SSE llegaría roto al
                # TTS y se leería en voz alta. Si queda un "<" sin su ">", se
                # retiene hasta completarse (al cerrar el turno se vacía igual).
                lt = emit.rfind("<")
                if lt != -1 and ">" not in emit[lt:]:
                    held = emit[lt:] + held
                    emit = emit[:lt]
                if emit:
                    spoken_parts.append(emit)
                    yield _sse(
                        _chunk_payload(chat_id, created, _for_tts(emit), None)
                    )
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
            yield _sse(_chunk_payload(chat_id, created, _for_tts(held), None))

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
        if settings.agentic_rag_enabled:
            rag_sources = telemetry.get("rag_sources", [])
            record_extra = {
                "orquestacion": "agentic_langgraph",
                "herramientas_invocadas": telemetry.get("herramientas_invocadas", []),
                "grounded": telemetry.get("grounded", False),
                "criticidad_llm": telemetry.get("criticidad_llm"),
                "criticidad_ml": telemetry.get("criticidad_ml"),
                "escalar_efectivo": telemetry.get("escalar_efectivo"),
            }
        else:
            rag_sources = [
                {"source": c.source, "scenario": c.scenario, "page": c.page,
                 "score": round(c.score, 4)}
                for c in chunks
            ]
            record_extra = {"orquestacion": "pipeline_fijo"}

        append_jsonl(
            "turns.jsonl",
            {
                "conversation_id": conversation_id,
                "turn_index": sum(1 for m in history if m["role"] == "user"),
                "user_text": next(
                    (m["content"] for m in reversed(history) if m["role"] == "user"), ""
                ),
                "rag_query": rag_query,
                "rag_scenario": scenario,
                "rag_sources": rag_sources,
                # Sin los tags de pausa: lo que se dijo es la frase, no el
                # marcado prosódico (mantiene limpias las transcripciones que
                # lee el jurado, la consola y el arnés de evaluación).
                "spoken_text": _strip_breaks("".join(spoken_parts)).strip(),
                "control": control,
                "latency_first_token_ms": round(
                    ((t_first_token or t_end) - t_start) * 1000
                ),
                "latency_total_ms": round((t_end - t_start) * 1000),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "model_calls": usage.model_calls,
                "rag_queries": 1,
                "model": usage.model_used or settings.gemini_model,
                "fallback_used": usage.fallback_used,
                **record_extra,
            },
        )

        if control and control.get("fin_llamada"):
            asyncio.create_task(_auto_summarize(conversation_id))

    return StreamingResponse(event_stream(), media_type="text/event-stream")
