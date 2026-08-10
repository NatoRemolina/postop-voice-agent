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
import re
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from app.agent import circuit_breaker
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
        # El default de LangChain es 6 reintentos internos con backoff ante un
        # 429 — verificado en producción: eso agrega varios segundos de espera
        # ANTES de que nuestro propio reintento a Groq (arriba) siquiera se
        # entere del fallo, y ElevenLabs se cansa de esperar y corta el turno
        # (silencio total al paciente). En una cuota diaria agotada, reintentar
        # no ayuda — mejor fallar rápido y dejar que el reintento manual de
        # turno completo hacia Groq tome el control de inmediato.
        max_retries=0,
        # El cliente de Gemini exige un mínimo de 10s ("Manually set deadline
        # 8s is too short") — con 8s fallaba SIEMPRE de inmediato, ni siquiera
        # llegaba a intentar la solicitud real. Verificado en producción.
        timeout=15,
    )


def _build_groq_model():
    return ChatGroq(
        model=settings.groq_model,
        temperature=0.4,
        max_tokens=512,
        api_key=settings.groq_api_key,
        max_retries=0,
        timeout=15,
    )


def _build_groq_fallback_model():
    return ChatGroq(
        model=settings.groq_fallback_model,
        temperature=0.4,
        max_tokens=512,
        api_key=settings.groq_api_key,
        max_retries=0,
        timeout=15,
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
    escalar_crudo = bool(turn_state.get("escalar", False))
    # Red de seguridad (doble vía):
    # 1. criticidad "rojo" siempre fuerza escalar=true, pase lo que pase con
    #    el estado crudo de la tool — nunca se sub-reporta un caso rojo.
    # 2. escalar=true (via escalar_a_equipo_clinico) sube la criticidad a
    #    "rojo" si registrar_evaluacion no alcanzó a correr en el mismo turno
    #    (ej. presupuesto de tool calls agotado) — visto en producción: el
    #    modelo escalaba con red_flags reales pero criticidad quedaba en
    #    "verde" por defecto, una inconsistencia que un jurado detectaría de
    #    inmediato. escalar_a_equipo_clinico solo se llama ante alarma real
    #    (ver su docstring), así que nunca es razonable que coexista con verde.
    if escalar_crudo and criticidad == "verde":
        criticidad = "rojo"
    escalar = escalar_crudo or criticidad == "rojo"
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


def _content_to_text(content) -> str:
    """`AIMessageChunk.content` puede llegar como str o como lista de bloques
    (algunos modelos/proveedores devuelven [{"type": "text", "text": "..."}]
    en vez de un string plano) — verificado en producción: pasar la lista tal
    cual río abajo rompía `held += text` en app/routers/chat.py con
    TypeError. Aplana cualquiera de las dos formas a texto plano."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _prefetch_context(history: list[dict], scenario: str | None) -> tuple[str, list[dict]]:
    """Recupera contexto clínico para el último turno del paciente, antes de
    invocar al modelo. Devuelve (texto formateado con citas, fuentes)."""
    last_user = next(
        (m["content"] for m in reversed(history) if m.get("role") == "user"), ""
    )
    if not last_user.strip():
        return "(sin consulta clínica en este turno)", []
    try:
        from app.graph.retrieval import search

        chunks = search(last_user, scenario, top_k=3)
    except Exception:
        logger.warning("prefetch de contexto clínico falló", exc_info=True)
        return "(no disponible en este turno)", []
    if not chunks:
        return "(sin resultados relevantes en el corpus para este turno)", []
    lines = [
        f"[{c.get('source', 'desconocido')}, pág {c.get('page', 0)}] {c.get('text', '')}"
        for c in chunks
    ]
    sources = [
        {
            "source": c.get("source"),
            "scenario": c.get("scenario"),
            "page": c.get("page"),
            "score": c.get("score"),
        }
        for c in chunks
    ]
    return "\n".join(lines), sources


_CONTROL_RE = re.compile(r"<control>(.*?)</control>", re.DOTALL)
_CONTROL_OPEN = "<control>"


async def _run_turn(
    model,
    tools: list,
    system_prompt: str,
    lc_messages: list,
    usage: TurnUsage,
    telemetry: dict,
    model_name: str,
    turn_state: dict,
) -> AsyncIterator[str]:
    """Una corrida completa del grafo con UN modelo fijo. Puede lanzar
    excepción a mitad de camino (se propaga tal cual al llamador, que decide
    si reintentar entero con otro proveedor o rendirse).

    El modelo emite su evaluación como bloque `<control>` al final del texto
    (una sola invocación por turno = primera palabra en ~1s, verificado en el
    pipeline fijo). Ese bloque se retiene aquí (nunca se yieldea crudo) y se
    parsea hacia `turn_state`; el llamador sintetiza el `<control>` definitivo
    con las redes de seguridad y el ensemble de ML."""
    from langchain.agents import create_agent

    agent_graph = create_agent(model=model, tools=tools, system_prompt=system_prompt)

    held = ""
    control_buf: str | None = None

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

        content = _content_to_text(getattr(msg, "content", None))
        if not content:
            continue
        usage.model_used = model_name

        if control_buf is not None:
            control_buf += content
            continue
        held += content
        idx = held.find(_CONTROL_OPEN)
        if idx != -1:
            emit, control_buf, held = held[:idx], held[idx:], ""
            if emit:
                yield emit
            continue
        # retener una cola que podría ser el inicio de un "<control>" partido
        keep = 0
        for k in range(min(len(_CONTROL_OPEN) - 1, len(held)), 0, -1):
            if held.endswith(_CONTROL_OPEN[:k]):
                keep = k
                break
        emit = held[: len(held) - keep] if keep else held
        held = held[len(held) - keep:] if keep else ""
        if emit:
            yield emit

    if held:
        yield held
    if control_buf:
        m = _CONTROL_RE.search(control_buf)
        if m:
            try:
                parsed = json.loads(m.group(1))
            except json.JSONDecodeError:
                logger.warning("control del modelo no parseable: %s", m.group(1)[:150])
            else:
                turn_state["criticidad_llm"] = parsed.get("criticidad")
                turn_state["confianza"] = parsed.get("confianza")
                turn_state["dimensiones_cubiertas"] = parsed.get("dimensiones_cubiertas") or []
                turn_state["red_flags"] = parsed.get("red_flags") or []
                turn_state["sintomas"] = parsed.get("sintomas") or {}
                if parsed.get("escalar"):
                    turn_state["escalar"] = True
                if parsed.get("fin_llamada"):
                    turn_state["fin_llamada"] = True


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

    # Pre-inyección del contexto clínico: sin esto el modelo gastaba una ronda
    # completa de tool-calling (buscar → resultado → recién ahí hablar) antes de
    # decir una sola palabra, con 4-6s de silencio medidos en producción — y
    # ElevenLabs terminaba cortando la llamada. El retrieval corre acá una sola
    # vez, en paralelo conceptual al turno, y el modelo ya recibe los pasajes
    # con sus citas; la tool sigue disponible para cuando necesite algo más.
    rag_context, rag_sources = _prefetch_context(history, scenario)
    telemetry["rag_prefetch_sources"] = rag_sources
    system_prompt = AGENTIC_SYSTEM_PROMPT.format(
        patient_context=patient_context, rag_context=rag_context
    )
    lc_messages = _history_to_messages(history) or [HumanMessage(content="Hola")]

    from app.graph.tools import make_tools

    # Cascada de proveedores: cada uno tiene su propia cuota gratuita diaria e
    # independiente, así que agotar una no deja la llamada sin voz. El orden es
    # por calidad de razonamiento clínico descendente. La clave del breaker
    # distingue groq-70b de groq-8b: son cupos separados en la misma cuenta
    # (uno diario, otro por minuto), así que agotar uno no debe bloquear el otro.
    cascada = [
        ("gemini", _build_gemini_model, settings.gemini_model),
        ("groq-70b", _build_groq_model, settings.groq_model),
        ("groq-8b", _build_groq_fallback_model, settings.groq_fallback_model),
    ]

    turn_state: dict = {}
    content_yielded = False
    for idx, (breaker_key, build_model, model_name) in enumerate(cascada):
        es_ultimo = idx == len(cascada) - 1
        if circuit_breaker.is_down(breaker_key):
            logger.info("%s en cooldown, se salta sin intentar.", model_name)
            if es_ultimo:
                raise RuntimeError(f"Todos los proveedores en cooldown (último: {model_name})")
            continue
        # Turno limpio en cada intento: ninguna tool tiene efectos externos
        # (todo vive en turn_state), así que reintentar el turno completo con
        # otro proveedor es seguro aunque el anterior ya hubiera llamado tools.
        turn_state.clear()
        telemetry["herramientas_invocadas"] = []
        usage.model_calls = 0
        usage.input_tokens = 0
        usage.output_tokens = 0
        usage.fallback_used = idx > 0

        try:
            # Solo la búsqueda queda como herramienta real: la evaluación viaja
            # en el bloque <control> del propio texto (una invocación por turno
            # = primera palabra en ~1s; con la evaluación como tool, el modelo
            # la ejecutaba ANTES de hablar y el paciente esperaba 4-7s en
            # silencio, con llamadas cortadas por ElevenLabs — medido en vivo).
            tools = [t for t in make_tools(scenario, turn_state)
                     if t.name == "buscar_conocimiento_clinico"]
            async for chunk in _run_turn(
                build_model(), tools, system_prompt, lc_messages,
                usage, telemetry, model_name, turn_state,
            ):
                content_yielded = True
                yield chunk
            break
        except Exception as exc:
            cooldown = circuit_breaker.mark_down(breaker_key, exc)
            if content_yielded:
                # Ya se le habló algo al paciente: no se puede "retractar" y
                # repetir el turno con otro modelo. Se corta con gracia.
                logger.exception(
                    "%s falló a mitad de turno agéntico; se corta sin reintentar.",
                    model_name,
                )
                break
            if es_ultimo:
                # Se agotó la cascada sin decir nada: la excepción debe
                # propagarse a app/routers/chat.py, que tiene el mensaje de
                # disculpa hablado. Absorberla acá dejaría silencio total.
                logger.error("Todos los proveedores fallaron este turno agéntico.")
                raise
            logger.warning(
                "%s no disponible (%s, cooldown %ss); reintentando el turno completo con %s.",
                model_name, exc, cooldown, cascada[idx + 1][2],
            )

    if usage.model_calls == 0:
        usage.model_calls = 1
    if not usage.model_used:
        usage.model_used = settings.groq_model if usage.fallback_used else settings.gemini_model

    # Ensemble con el modelo de triaje (antes vivía en la tool
    # registrar_evaluacion): corre DESPUÉS del streaming, así no agrega ni un
    # milisegundo antes de la primera palabra. Solo puede SUBIR la criticidad.
    sintomas = turn_state.get("sintomas") or {}
    if sintomas:
        try:
            from app.agent import triage_model

            ml = triage_model.predict(sintomas)
        except Exception:
            ml = None
        if ml:
            orden = {"verde": 0, "amarillo": 1, "rojo": 2}
            turn_state["criticidad_ml"] = ml["criticidad"]
            llm_crit = turn_state.get("criticidad_llm") or "verde"
            if orden.get(ml["criticidad"], 0) > orden.get(llm_crit, 0):
                turn_state["criticidad_final"] = ml["criticidad"]

    # El contexto pre-inyectado también fundamenta la respuesta, así que cuenta
    # como "grounded" y sus fuentes son igual de citables que las de la tool.
    telemetry["grounded"] = bool(turn_state.get("grounded", False)) or bool(rag_sources)
    telemetry["criticidad_llm"] = turn_state.get("criticidad_llm")
    telemetry["criticidad_ml"] = turn_state.get("criticidad_ml")
    telemetry["rag_sources"] = (turn_state.get("rag_sources") or []) + rag_sources
    # "escalar" es el valor crudo que reportó la tool; "escalar_efectivo" es el
    # que realmente viaja en el <control> ya con la red de seguridad aplicada
    # (ver _control_block) — se guardan ambos para poder auditar discrepancias.
    telemetry["escalar"] = bool(turn_state.get("escalar", False))
    telemetry["fin_llamada"] = bool(turn_state.get("fin_llamada", False))

    control_payload = _control_payload(turn_state)
    telemetry["escalar_efectivo"] = control_payload["escalar"]

    yield _control_block(control_payload)
