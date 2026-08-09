"""Puente de streaming entre el agente LangGraph (`create_agent`) y el SSE que
consume `app/routers/chat.py`.

`stream_agentic_response` es un reemplazo de contrato idéntico a
`app.agent.llm.stream_response`: recibe el historial de la llamada y una
`TurnUsage` a poblar, y yieldea texto (str) turno a turno. A diferencia del
pipeline actual (RAG pre-inyectado + bloque `<control>` escrito por el propio
modelo), aquí el conocimiento clínico y la evaluación de criticidad llegan por
tool calls (`app.graph.tools.make_tools`); el bloque `<control>` final se
sintetiza acá a partir del `turn_state` que esas tools van llenando, para que
`app/routers/chat.py` lo siga parseando sin cambios.
"""

import json
import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from app.agent.llm import TurnUsage
from app.config import settings
from app.graph.agent_prompt import AGENTIC_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def _build_gemini_model():
    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        temperature=0.4,
        max_output_tokens=512,
        # Voz en tiempo real: el razonamiento extendido dispara la latencia del
        # primer token. "minimal" es el mínimo que exponen los modelos 3.x.
        thinking_level="minimal",
        google_api_key=settings.gemini_api_key,
    )


def _build_groq_model():
    return ChatGroq(
        model=settings.groq_model,
        temperature=0.4,
        max_tokens=512,
        api_key=settings.groq_api_key,
    )


def _history_to_messages(history: list[dict]) -> list:
    messages: list = []
    for m in history:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if m.get("role") == "user":
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    return messages


def _infer_confianza(turn_state: dict) -> str:
    """No hay un campo de confianza explícito reportado por `registrar_evaluacion`
    (ver app/graph/tools.py); se aproxima comparando el juicio del LLM contra el
    modelo de triaje entrenado (`criticidad_ml`) cuando ambos están disponibles.
    """
    llm_crit = turn_state.get("criticidad_llm")
    ml_crit = turn_state.get("criticidad_ml")
    if llm_crit and ml_crit:
        return "alta" if llm_crit == ml_crit else "media"
    if llm_crit:
        return "media"
    return turn_state.get("confianza") or "baja"


def _control_payload(turn_state: dict) -> dict:
    criticidad = (
        turn_state.get("criticidad_final") or turn_state.get("criticidad_llm") or "verde"
    )
    # Red de seguridad: si por cualquier motivo (ej. presupuesto de tool calls
    # agotado a mitad del turno en app.graph.tools) `escalar_a_equipo_clinico`
    # no llegó a marcar turn_state["escalar"], una criticidad "rojo" igual
    # fuerza el escalamiento — nunca se sub-reporta silenciosamente un caso rojo.
    escalar = bool(turn_state.get("escalar", False)) or criticidad == "rojo"
    return {
        "criticidad": criticidad,
        "confianza": _infer_confianza(turn_state),
        "dimensiones_cubiertas": turn_state.get("dimensiones_cubiertas") or [],
        "red_flags": turn_state.get("red_flags") or [],
        "escalar": escalar,
        "fin_llamada": bool(turn_state.get("fin_llamada", False)),
    }


def _control_block(payload: dict) -> str:
    return f"<control>{json.dumps(payload, ensure_ascii=False)}</control>"


async def _run_turn(
    model,
    tools: list,
    system_prompt: str,
    lc_messages: list,
    usage: TurnUsage,
    telemetry: dict,
    model_name: str,
) -> AsyncIterator[str]:
    """Una corrida completa del grafo con UN modelo fijo. Puede lanzar
    excepción a mitad de camino (se propaga tal cual al llamador, que decide
    si reintentar entero con otro proveedor o rendirse)."""
    from langchain.agents import create_agent

    agent_graph = create_agent(model=model, tools=tools, system_prompt=system_prompt)

    async for chunk in agent_graph.astream({"messages": lc_messages}, stream_mode="messages"):
        if not isinstance(chunk, tuple) or len(chunk) != 2:
            continue
        msg, meta = chunk
        meta = meta if isinstance(meta, dict) else {}
        node = meta.get("langgraph_node")

        usage_meta = getattr(msg, "usage_metadata", None)
        if usage_meta:
            usage.input_tokens += usage_meta.get("input_tokens") or 0
            usage.output_tokens += usage_meta.get("output_tokens") or 0
            usage.model_calls += 1

        if isinstance(msg, ToolMessage):
            name = getattr(msg, "name", None)
            if name:
                telemetry["herramientas_invocadas"].append(name)
            continue

        if node != "model":
            continue

        content = getattr(msg, "content", None)
        if content:
            usage.model_used = model_name
            yield content


async def stream_agentic_response(
    history: list[dict],
    patient_context: str,
    scenario: str | None,
    usage: TurnUsage,
    telemetry: dict,
) -> AsyncIterator[str]:
    """Gemini primero, con reintento COMPLETO del turno en Groq si Gemini falla
    ANTES de que se haya dicho algo al paciente (nunca a medio turno: una vez
    que algo se le habló, no se puede "retractar" — se corta con gracia).

    Nota de diseño (reemplaza un intento anterior con `ChatModel.with_fallbacks`):
    se probó en vivo que el fallback automático de LangChain dentro del ciclo
    de tool-calling de `create_agent` NO conmuta de forma confiable — a veces
    la excepción de Gemini se escapa igual y deja al paciente en silencio
    total. Este reintento manual, a nivel de turno completo, replica el mismo
    patrón ya probado y estable de `app.agent.llm.stream_response`. Como
    ninguna tool tiene efectos externos (todo vive en `turn_state`, que se
    descarta y se vuelve a crear limpio en el reintento), repetir el turno
    entero con otro proveedor es seguro incluso si Gemini ya alcanzó a llamar
    alguna tool antes de fallar.
    """
    telemetry["escenario"] = scenario
    telemetry["herramientas_invocadas"] = []
    telemetry["grounded"] = False

    # Un turno agéntico puede llamar al modelo varias veces (ciclo de tool
    # calling); se cuenta desde cero y se acumula por chunk, en vez de heredar
    # el default de TurnUsage pensado para una sola llamada por turno.
    usage.model_calls = 0
    usage.input_tokens = 0
    usage.output_tokens = 0
    usage.model_used = ""
    usage.fallback_used = False

    system_prompt = AGENTIC_SYSTEM_PROMPT.format(patient_context=patient_context)
    lc_messages = _history_to_messages(history) or [HumanMessage(content="Hola")]

    turn_state: dict = {}
    content_yielded = False
    try:
        from app.graph.tools import make_tools

        tools = make_tools(scenario, turn_state)
        async for chunk in _run_turn(
            _build_gemini_model(), tools, system_prompt, lc_messages,
            usage, telemetry, settings.gemini_model,
        ):
            content_yielded = True
            yield chunk
    except Exception as exc:
        if content_yielded:
            logger.exception(
                "Gemini falló a mitad de turno agéntico (ya se había hablado "
                "algo al paciente); no se reintenta, se corta con gracia."
            )
        else:
            logger.warning(
                "Gemini falló antes de decir nada este turno (%s); "
                "reintentando el turno completo con Groq.", exc,
            )
            turn_state.clear()
            telemetry["herramientas_invocadas"] = []
            usage.model_calls = 0
            usage.input_tokens = 0
            usage.output_tokens = 0
            usage.fallback_used = True
            # SIN try/except acá a propósito: si Groq también falla, la
            # excepción debe propagarse hacia app/routers/chat.py, que ya
            # tiene el mensaje de disculpa hablado probado en producción
            # (idéntico al que usa app.agent.llm.stream_response cuando
            # ambos proveedores caen). Absorberla acá dejaría al paciente en
            # silencio total — exactamente el riesgo que encontró la
            # verificación en la versión anterior con with_fallbacks().
            tools = make_tools(scenario, turn_state)
            async for chunk in _run_turn(
                _build_groq_model(), tools, system_prompt, lc_messages,
                usage, telemetry, settings.groq_model,
            ):
                content_yielded = True
                yield chunk

    if usage.model_calls == 0:
        usage.model_calls = 1
    if not usage.model_used:
        usage.model_used = settings.groq_model if usage.fallback_used else settings.gemini_model

    telemetry["grounded"] = bool(turn_state.get("grounded", False))
    telemetry["criticidad_llm"] = turn_state.get("criticidad_llm")
    telemetry["criticidad_ml"] = turn_state.get("criticidad_ml")
    telemetry["rag_sources"] = turn_state.get("rag_sources", [])
    # "escalar" es el valor crudo que reportó la tool; "escalar_efectivo" es el
    # que realmente viaja en el <control> ya con la red de seguridad aplicada
    # (ver _control_block) — se guardan ambos para poder auditar discrepancias.
    telemetry["escalar"] = bool(turn_state.get("escalar", False))
    telemetry["fin_llamada"] = bool(turn_state.get("fin_llamada", False))

    control_payload = _control_payload(turn_state)
    telemetry["escalar_efectivo"] = control_payload["escalar"]

    yield _control_block(control_payload)
