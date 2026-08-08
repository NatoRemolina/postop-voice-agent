import json
import logging

from google.genai import types
from starlette.concurrency import run_in_threadpool

from app.agent.llm import get_gemini
from app.config import settings
from app.storage import append_jsonl, read_jsonl

logger = logging.getLogger(__name__)

CRITICALITY_ORDER = {"verde": 0, "amarillo": 1, "rojo": 2}

NEXT_STEPS_BY_CRITICALITY = {
    "verde": (
        "Continuar con los cuidados básicos indicados al alta: reposo relativo, "
        "manejo del dolor según la fórmula médica y cuidados de la herida. "
        "Mantener el siguiente control programado."
    ),
    "amarillo": (
        "Mantener vigilancia de los síntomas reportados durante las próximas 24-48 "
        "horas. El equipo clínico debe revisar este reporte y contactar al paciente "
        "si los síntomas persisten o empeoran."
    ),
    "rojo": (
        "Escalamiento inmediato al equipo clínico: revisar este caso con prioridad, "
        "contactar al paciente y valorar remisión a urgencias según las señales de "
        "alarma detectadas."
    ),
}

SUMMARY_SYSTEM_PROMPT = (
    "Eres un asistente clínico que redacta resúmenes de llamadas de seguimiento "
    "postoperatorio para el equipo médico. Respondes ÚNICAMENTE con un objeto JSON "
    "válido, sin markdown ni texto adicional."
)

SUMMARY_PROMPT_TEMPLATE = """\
Con base en la transcripción de una llamada de seguimiento postoperatorio, genera un \
objeto JSON con exactamente estas claves:
- "paciente": nombre del paciente si se menciona en la llamada, o null
- "procedimiento": cirugía o procedimiento mencionado, o null
- "sintomas_reportados": lista de síntomas que reportó el paciente (frases cortas en español)
- "proximos_pasos": qué deben hacer el equipo clínico y el paciente a continuación (2-3 frases)
- "resumen_narrativo": resumen de la llamada en 3-5 frases, en español, para la historia clínica

Datos de control registrados durante la llamada:
- criticidad final: {criticidad}
- señales de alarma: {red_flags}
- dimensiones cubiertas: {dimensiones}
- escalar al equipo clínico: {escalar}

Transcripción:
{transcript}
"""


def turns_for_conversation(conversation_id: str) -> list[dict]:
    turns = [
        t
        for t in read_jsonl("turns.jsonl")
        if t.get("conversation_id") == conversation_id
    ]
    turns.sort(key=lambda t: t.get("ts") or 0)
    return turns


def _patient_text(turn: dict) -> str:
    return str(turn.get("user_text") or turn.get("rag_query") or "").strip()


def _controls(turns: list[dict]) -> list[dict]:
    return [t["control"] for t in turns if isinstance(t.get("control"), dict)]


def _max_criticality(controls: list[dict]) -> str:
    level = "verde"
    for control in controls:
        crit = str(control.get("criticidad") or "").strip().lower()
        if crit in CRITICALITY_ORDER and CRITICALITY_ORDER[crit] > CRITICALITY_ORDER[level]:
            level = crit
    return level


def _union_str_lists(controls: list[dict], key: str) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for control in controls:
        values = control.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                merged.append(text)
    return merged


def _unique_references(turns: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    references: list[dict] = []
    for turn in turns:
        sources = turn.get("rag_sources")
        if not isinstance(sources, list):
            continue
        for src in sources:
            if not isinstance(src, dict):
                continue
            key = (src.get("source"), src.get("scenario"), src.get("page"))
            if key in seen:
                continue
            seen.add(key)
            references.append(
                {
                    "source": src.get("source"),
                    "scenario": src.get("scenario"),
                    "page": src.get("page"),
                }
            )
    return references


def _coerce_str_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _parse_json_block(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _build_transcript(turns: list[dict]) -> str:
    lines: list[str] = []
    for turn in turns:
        patient = _patient_text(turn)
        agent = str(turn.get("spoken_text") or "").strip()
        if patient:
            lines.append(f"Paciente: {patient}")
        if agent:
            lines.append(f"Agente: {agent}")
    return "\n".join(lines) or "(sin transcripción disponible)"


def _fallback_symptoms(turns: list[dict]) -> list[str]:
    symptoms: list[str] = []
    seen: set[str] = set()
    for turn in turns:
        text = _patient_text(turn)
        if len(text) < 15:
            continue
        snippet = text if len(text) <= 140 else text[:137] + "..."
        if snippet not in seen:
            seen.add(snippet)
            symptoms.append(snippet)
    return symptoms[:10]


def _fallback_narrative(
    n_turns: int,
    criticality: str,
    dimensions: list[str],
    red_flags: list[str],
    escalate: bool,
) -> str:
    parts = [
        f"Llamada de seguimiento postoperatorio con {n_turns} turnos del agente.",
        f"Criticidad final de la llamada: {criticality}.",
    ]
    if dimensions:
        parts.append("Dimensiones evaluadas: " + ", ".join(dimensions) + ".")
    if red_flags:
        parts.append("Señales de alarma detectadas: " + ", ".join(red_flags) + ".")
    else:
        parts.append("No se registraron señales de alarma.")
    if escalate:
        parts.append("El caso quedó marcado para escalamiento al equipo clínico.")
    return " ".join(parts)


async def _generate_with_gemini(
    turns: list[dict],
    criticality: str,
    dimensions: list[str],
    red_flags: list[str],
    escalate: bool,
) -> tuple[dict, int, int]:
    prompt = SUMMARY_PROMPT_TEMPLATE.format(
        criticidad=criticality,
        red_flags=", ".join(red_flags) or "ninguna",
        dimensiones=", ".join(dimensions) or "ninguna",
        escalar="sí" if escalate else "no",
        transcript=_build_transcript(turns),
    )
    response = await get_gemini().aio.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SUMMARY_SYSTEM_PROMPT,
            temperature=0.2,
            max_output_tokens=700,
        ),
    )
    data = _parse_json_block(response.text or "")
    if data is None:
        raise ValueError("gemini did not return parseable JSON")
    input_tokens = output_tokens = 0
    if response.usage_metadata:
        input_tokens = response.usage_metadata.prompt_token_count or 0
        output_tokens = response.usage_metadata.candidates_token_count or 0
    return data, input_tokens, output_tokens


async def build_call_summary(
    conversation_id: str, turns: list[dict] | None = None
) -> dict:
    if turns is None:
        turns = await run_in_threadpool(turns_for_conversation, conversation_id)

    controls = _controls(turns)
    criticality = _max_criticality(controls)
    red_flags = _union_str_lists(controls, "red_flags")
    dimensions = _union_str_lists(controls, "dimensiones_cubiertas")
    escalate = criticality == "rojo" or any(
        bool(c.get("escalar")) for c in controls
    )

    input_tokens = sum(int(t.get("input_tokens") or 0) for t in turns)
    output_tokens = sum(int(t.get("output_tokens") or 0) for t in turns)
    model_calls = sum(int(t.get("model_calls") or 0) for t in turns)
    rag_queries = sum(int(t.get("rag_queries") or 0) for t in turns)

    patient = "no identificado"
    procedure: str | None = None
    symptoms = _fallback_symptoms(turns)
    next_steps = NEXT_STEPS_BY_CRITICALITY[criticality]
    narrative = _fallback_narrative(
        len(turns), criticality, dimensions, red_flags, escalate
    )
    generated_by = "fallback"

    if turns:
        try:
            data, sum_in, sum_out = await _generate_with_gemini(
                turns, criticality, dimensions, red_flags, escalate
            )
            gemini_patient = str(data.get("paciente") or "").strip()
            if gemini_patient and gemini_patient.lower() not in ("null", "none"):
                patient = gemini_patient
            gemini_procedure = str(data.get("procedimiento") or "").strip()
            if gemini_procedure and gemini_procedure.lower() not in ("null", "none"):
                procedure = gemini_procedure
            gemini_symptoms = _coerce_str_list(data.get("sintomas_reportados"))
            if gemini_symptoms:
                symptoms = gemini_symptoms
            gemini_next = str(data.get("proximos_pasos") or "").strip()
            if gemini_next:
                next_steps = gemini_next
            gemini_narrative = str(data.get("resumen_narrativo") or "").strip()
            if gemini_narrative:
                narrative = gemini_narrative
            input_tokens += sum_in
            output_tokens += sum_out
            model_calls += 1
            generated_by = "gemini"
        except Exception:
            logger.exception(
                "gemini summary failed for %s; using deterministic fallback",
                conversation_id,
            )

    return {
        "conversation_id": conversation_id,
        "started_ts": turns[0].get("ts") if turns else None,
        "ended_ts": turns[-1].get("ts") if turns else None,
        "n_turnos": len(turns),
        "paciente": patient,
        "procedimiento": procedure,
        "sintomas_reportados": symptoms,
        "criticidad_final": criticality,
        "escalar": escalate,
        "red_flags": red_flags,
        "dimensiones_cubiertas": dimensions,
        "referencias": _unique_references(turns),
        "proximos_pasos": next_steps,
        "resumen_narrativo": narrative,
        "tokens": {"input": input_tokens, "output": output_tokens},
        "model_calls": model_calls,
        "rag_queries": rag_queries,
        "generated_by": generated_by,
    }


def persist_summary(summary: dict) -> None:
    append_jsonl("call_summaries.jsonl", summary)
    if summary.get("escalar"):
        append_jsonl(
            "alerts.jsonl",
            {
                "conversation_id": summary.get("conversation_id"),
                "criticidad": summary.get("criticidad_final"),
                "red_flags": summary.get("red_flags") or [],
                "paciente": summary.get("paciente") or "no identificado",
                "resumen_narrativo": summary.get("resumen_narrativo") or "",
            },
        )


def latest_summaries() -> list[dict]:
    by_id: dict[str, dict] = {}
    for summary in read_jsonl("call_summaries.jsonl"):
        cid = summary.get("conversation_id")
        if not cid:
            continue
        previous = by_id.get(cid)
        if previous is None or (summary.get("ts") or 0) >= (previous.get("ts") or 0):
            by_id[cid] = summary
    return sorted(
        by_id.values(), key=lambda s: s.get("ended_ts") or s.get("ts") or 0, reverse=True
    )
