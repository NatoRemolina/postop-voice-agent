"""Resumen estructurado post-llamada generado por un agente con herramientas.

A diferencia de `app.agent.summary` (una sola llamada al modelo que resume la
transcripción "a ciegas"), este módulo arma un agente que puede releer la
transcripción completa, verificar afirmaciones clínicas contra el corpus y
contrastar los síntomas contra el modelo de triaje antes de entregar el
resumen final. El contrato de salida (claves del diccionario) es idéntico al
de `build_call_summary` para que el resto del sistema (persistencia, paneles,
alertas) no necesite distinguir cuál de los dos lo produjo, salvo por el
campo `generated_by`.

Nota de diseño (ver informe): se evaluó `deepagents.create_deep_agent` y se
abandonó por incompatibilidad real y verificada entre su capa de
`response_format` y modelos servidos vía Groq (el modelo intentó invocar una
tool con nombre `" TinySummary "` con espacios, y Groq la rechazó por no
declarada) además de traer herramientas de filesystem/shell/subagentes
innecesarias para esta tarea puntual. Se usa `langchain.agents.create_agent`,
el mismo patrón que ya emplea el resto del sistema.
"""

import asyncio
import json
import logging

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from app.config import settings
from app.storage import read_jsonl

logger = logging.getLogger(__name__)

AGENT_TIMEOUT_SECONDS = 60

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

DEEP_SUMMARY_SYSTEM_PROMPT = """\
Eres un auditor clínico que redacta el resumen final de una llamada de \
seguimiento postoperatorio para el equipo médico. A diferencia de un resumen \
automático simple, tu trabajo es VERIFICAR antes de afirmar.

Flujo obligatorio:
1. Llama a `leer_turnos` con el conversation_id indicado para obtener la \
transcripción completa (paciente/agente) y los bloques de control de cada turno.
2. Si el paciente reportó síntomas o afirmaciones clínicas cuya gravedad no \
sea evidente, llama a `buscar_en_corpus` con una consulta breve para verificar \
si están sustentadas en las guías clínicas disponibles.
3. Si hay síntomas reportados (dolor, fiebre, estado de la herida, etc.), \
llama a `consultar_triaje` con esos síntomas como diccionario para contrastar \
la criticidad observada contra el modelo de triaje independiente, si existe.
4. Entrega el resumen final estructurado únicamente después de completar los \
pasos anteriores.

Reglas:
- Responde siempre en español.
- No inventes datos: si algo no se menciona explícitamente en la \
transcripción (por ejemplo el nombre del paciente o el procedimiento \
quirúrgico), usa null o una lista vacía según corresponda. Los pasajes que \
te devuelva `buscar_en_corpus` son para verificar afirmaciones clínicas, NO \
son evidencia de qué procedimiento tuvo el paciente: no infieras el \
procedimiento a partir de las fuentes citadas, solo a partir de lo que el \
paciente o el agente dijeron literalmente en la llamada.
- La criticidad clínica y las señales de alarma ya fueron determinadas \
durante la llamada en tiempo real; tu resumen es descriptivo y de \
verificación, no vuelve a triar al paciente ni contradice esos datos de \
control, que se te entregan como contexto.
"""

DEEP_SUMMARY_USER_TEMPLATE = """\
conversation_id: {conversation_id}

Datos de control ya registrados durante la llamada (no los repitas como si \
fueran tu hallazgo, úsalos como contexto para tu verificación):
- criticidad final: {criticidad}
- señales de alarma: {red_flags}
- dimensiones cubiertas: {dimensiones}
- escalar al equipo clínico: {escalar}

Sigue el flujo obligatorio (leer_turnos, y si aplica buscar_en_corpus y \
consultar_triaje) y entrega el resumen estructurado final.
"""


class DeepSummaryFields(BaseModel):
    paciente: str | None = Field(
        default=None, description="Nombre del paciente si se menciona en la llamada, o null"
    )
    procedimiento: str | None = Field(
        default=None, description="Cirugía o procedimiento mencionado, o null"
    )
    sintomas_reportados: list[str] = Field(
        default_factory=list,
        description="Síntomas que reportó el paciente, en frases cortas en español",
    )
    proximos_pasos: str = Field(
        default="", description="Qué deben hacer el equipo clínico y el paciente a continuación"
    )
    resumen_narrativo: str = Field(
        default="",
        description="Resumen de la llamada en 3-5 frases en español, para la historia clínica",
    )


def _turns_for_conversation(conversation_id: str) -> list[dict]:
    turns = [
        t for t in read_jsonl("turns.jsonl") if t.get("conversation_id") == conversation_id
    ]
    turns.sort(key=lambda t: t.get("ts") or 0)
    return turns


def _chunk_field(chunk, name: str, default=""):
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)


@tool
def buscar_en_corpus(consulta: str) -> str:
    """Verifica si una afirmación clínica está sustentada en el corpus de guías.

    Recibe una consulta breve en lenguaje natural (por ejemplo, un síntoma o
    una afirmación clínica dicha durante la llamada) y devuelve los pasajes
    más relevantes del corpus con su fuente y página, o un aviso si no hay
    nada relevante. Úsala para no repetir en el resumen final una afirmación
    que no tenga respaldo.
    """
    try:
        try:
            from app.graph.retrieval import search as _search

            chunks = _search(consulta)
        except ImportError:
            from app.rag.retrieve import retrieve as _search

            chunks = _search(consulta)

        if not chunks:
            return "No se encontraron pasajes relevantes en el corpus para esa consulta."

        lines = []
        for chunk in chunks:
            text = str(_chunk_field(chunk, "text", "")).strip().replace("\n", " ")
            if len(text) > 400:
                text = text[:397] + "..."
            source = _chunk_field(chunk, "source", "desconocida")
            page = _chunk_field(chunk, "page", "?")
            score = _chunk_field(chunk, "score", 0.0)
            try:
                score_text = f"{float(score):.2f}"
            except (TypeError, ValueError):
                score_text = str(score)
            lines.append(f"[fuente: {source}, pág. {page}, score {score_text}] {text}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("buscar_en_corpus falló para %r: %s", consulta, exc)
        return "No se pudo consultar el corpus en este momento (error interno de búsqueda)."


@tool
def leer_turnos(conversation_id: str) -> str:
    """Lee la transcripción completa de una llamada dado su conversation_id.

    Devuelve, turno por turno, lo que dijo el paciente, lo que respondió el
    agente y el bloque de control (criticidad, señales de alarma, dimensiones
    cubiertas) registrado en ese turno, si lo hay. Es el punto de partida
    obligatorio antes de redactar el resumen.
    """
    try:
        turns = _turns_for_conversation(conversation_id)
        if not turns:
            return f"No se encontraron turnos para conversation_id={conversation_id}."

        lines = []
        for i, turn in enumerate(turns, start=1):
            patient = str(turn.get("user_text") or turn.get("rag_query") or "").strip()
            agent = str(turn.get("spoken_text") or "").strip()
            control = turn.get("control")
            lines.append(f"--- Turno {i} ---")
            if patient:
                lines.append(f"Paciente: {patient}")
            if agent:
                lines.append(f"Agente: {agent}")
            if isinstance(control, dict) and control:
                lines.append(f"Control: {json.dumps(control, ensure_ascii=False)}")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("leer_turnos falló para %s: %s", conversation_id, exc)
        return f"No se pudo leer la transcripción de {conversation_id} (error interno)."


@tool
def consultar_triaje(sintomas: dict) -> str:
    """Contrasta un conjunto de síntomas contra el modelo de triaje independiente.

    Recibe un diccionario de síntomas/características del paciente (por
    ejemplo dolor_nrs, fiebre_c, herida, procedimiento) y devuelve la
    criticidad predicha por el modelo entrenado por separado, o un aviso si
    el modelo aún no está disponible.
    """
    try:
        try:
            from app.agent.triage_model import predict as _predict
        except ImportError:
            return "sin modelo de triaje disponible"

        result = _predict(sintomas if isinstance(sintomas, dict) else {})
        if result is None:
            return "sin modelo de triaje disponible"
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        logger.warning("consultar_triaje falló: %s", exc)
        return "sin modelo de triaje disponible"


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


def _fallback_symptoms(turns: list[dict]) -> list[str]:
    symptoms: list[str] = []
    seen: set[str] = set()
    for turn in turns:
        text = str(turn.get("user_text") or turn.get("rag_query") or "").strip()
        if len(text) < 15:
            continue
        snippet = text if len(text) <= 140 else text[:137] + "..."
        if snippet not in seen:
            seen.add(snippet)
            symptoms.append(snippet)
    return symptoms[:10]


def _fallback_narrative(
    n_turns: int, criticality: str, dimensions: list[str], red_flags: list[str], escalate: bool
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


def _build_model():
    primary = ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        api_key=settings.gemini_api_key,
        temperature=0.2,
        max_output_tokens=1024,
    )
    fallback = ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=0.2,
        max_tokens=1024,
    )
    return primary.with_fallbacks([fallback])


def _build_agent(model=None):
    return create_agent(
        model=model or _build_model(),
        tools=[buscar_en_corpus, leer_turnos, consultar_triaje],
        system_prompt=DEEP_SUMMARY_SYSTEM_PROMPT,
        response_format=DeepSummaryFields,
    )


def _usage_from_messages(messages: list) -> tuple[int, int, int]:
    input_tokens = output_tokens = calls = 0
    for m in messages:
        if type(m).__name__ != "AIMessage":
            continue
        calls += 1
        usage = getattr(m, "usage_metadata", None) or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return input_tokens, output_tokens, calls


def _parse_json_fallback(messages: list) -> DeepSummaryFields | None:
    """Último recurso si `structured_response` viene vacío: busca un bloque
    JSON en el texto del último mensaje del asistente, igual que hace
    `app.agent.summary` con la respuesta cruda de Gemini."""
    for m in reversed(messages):
        if type(m).__name__ != "AIMessage":
            continue
        text = m.content if isinstance(m.content, str) else ""
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            continue
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            try:
                return DeepSummaryFields(**data)
            except Exception:
                continue
    return None


async def _build_deep_summary_impl(conversation_id: str, model=None) -> dict:
    turns = await run_in_threadpool(_turns_for_conversation, conversation_id)
    if not turns:
        raise ValueError(f"no hay turnos registrados para conversation_id={conversation_id}")

    controls = _controls(turns)
    criticality = _max_criticality(controls)
    red_flags = _union_str_lists(controls, "red_flags")
    dimensions = _union_str_lists(controls, "dimensiones_cubiertas")
    escalate = criticality == "rojo" or any(bool(c.get("escalar")) for c in controls)

    input_tokens = sum(int(t.get("input_tokens") or 0) for t in turns)
    output_tokens = sum(int(t.get("output_tokens") or 0) for t in turns)
    model_calls = sum(int(t.get("model_calls") or 0) for t in turns)
    rag_queries = sum(int(t.get("rag_queries") or 0) for t in turns)

    user_prompt = DEEP_SUMMARY_USER_TEMPLATE.format(
        conversation_id=conversation_id,
        criticidad=criticality,
        red_flags=", ".join(red_flags) or "ninguna",
        dimensiones=", ".join(dimensions) or "ninguna",
        escalar="sí" if escalate else "no",
    )

    agent = _build_agent(model=model)
    result = await asyncio.wait_for(
        agent.ainvoke({"messages": [{"role": "user", "content": user_prompt}]}),
        timeout=AGENT_TIMEOUT_SECONDS,
    )

    messages = result.get("messages") or []
    data = result.get("structured_response")
    if data is None:
        data = _parse_json_fallback(messages)
    if data is None:
        raise ValueError("el agente no produjo un resumen estructurado válido")

    extra_in, extra_out, extra_calls = _usage_from_messages(messages)

    patient = (data.paciente or "").strip() or "no identificado"
    procedure = (data.procedimiento or "").strip() or None
    symptoms = [s.strip() for s in data.sintomas_reportados if str(s).strip()]
    if not symptoms:
        symptoms = _fallback_symptoms(turns)
    next_steps = (data.proximos_pasos or "").strip() or NEXT_STEPS_BY_CRITICALITY[criticality]
    narrative = (data.resumen_narrativo or "").strip() or _fallback_narrative(
        len(turns), criticality, dimensions, red_flags, escalate
    )

    return {
        "conversation_id": conversation_id,
        "started_ts": turns[0].get("ts"),
        "ended_ts": turns[-1].get("ts"),
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
        "tokens": {"input": input_tokens + extra_in, "output": output_tokens + extra_out},
        "model_calls": model_calls + extra_calls,
        "rag_queries": rag_queries,
        "generated_by": "deep_agent",
    }


async def build_deep_summary(conversation_id: str) -> dict:
    """Genera el resumen estructurado post-llamada con el agente verificador.

    Nunca propaga una excepción: ante cualquier fallo (del agente, del
    parseo de su salida, o timeout) cae a `app.agent.summary.build_call_summary`,
    que ya tiene su propio resumen determinista de última instancia.
    """
    try:
        return await _build_deep_summary_impl(conversation_id)
    except Exception:
        logger.exception(
            "deep_summary falló para %s; usando app.agent.summary como respaldo",
            conversation_id,
        )
        try:
            from app.agent.summary import build_call_summary

            return await build_call_summary(conversation_id)
        except Exception:
            logger.exception(
                "respaldo app.agent.summary también falló para %s; "
                "devolviendo resumen mínimo de última instancia",
                conversation_id,
            )
            turns = await run_in_threadpool(_turns_for_conversation, conversation_id)
            controls = _controls(turns)
            criticality = _max_criticality(controls)
            red_flags = _union_str_lists(controls, "red_flags")
            dimensions = _union_str_lists(controls, "dimensiones_cubiertas")
            escalate = criticality == "rojo" or any(bool(c.get("escalar")) for c in controls)
            return {
                "conversation_id": conversation_id,
                "started_ts": turns[0].get("ts") if turns else None,
                "ended_ts": turns[-1].get("ts") if turns else None,
                "n_turnos": len(turns),
                "paciente": "no identificado",
                "procedimiento": None,
                "sintomas_reportados": _fallback_symptoms(turns),
                "criticidad_final": criticality,
                "escalar": escalate,
                "red_flags": red_flags,
                "dimensiones_cubiertas": dimensions,
                "referencias": _unique_references(turns),
                "proximos_pasos": NEXT_STEPS_BY_CRITICALITY[criticality],
                "resumen_narrativo": _fallback_narrative(
                    len(turns), criticality, dimensions, red_flags, escalate
                ),
                "tokens": {"input": 0, "output": 0},
                "model_calls": 0,
                "rag_queries": sum(int(t.get("rag_queries") or 0) for t in turns),
                "generated_by": "fallback",
            }
