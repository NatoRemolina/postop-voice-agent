"""Fábrica de herramientas del agente, ligadas por turno a `turn_state`.

`make_tools` se llama una vez por turno de conversación: crea un lote nuevo
de tools con su propio contador de presupuesto y su propio `turn_state`
cerrado por clausura, así que dos turnos nunca comparten contador ni estado.
"""

import logging

from langchain_core.tools import tool

from app.agent import triage_model

logger = logging.getLogger(__name__)

TOOL_CALL_BUDGET = 2
BUDGET_EXCEEDED_MSG = (
    "Presupuesto de herramientas agotado para este turno. Responde con lo que ya sabes."
)

_CRITICALITY_ORDER = {"verde": 0, "amarillo": 1, "rojo": 2}
_VALID_CRITICALITY = set(_CRITICALITY_ORDER)


def make_tools(scenario: str | None, turn_state: dict) -> list:
    call_count = {"n": 0}

    def _consume_budget() -> bool:
        if call_count["n"] >= TOOL_CALL_BUDGET:
            return False
        call_count["n"] += 1
        return True

    @tool
    def buscar_conocimiento_clinico(consulta: str) -> str:
        """Busca información clínica verificada en el corpus de guías y protocolos
        postoperatorios para el procedimiento del paciente. Úsala SIEMPRE antes de
        dar cualquier recomendación clínica, rango normal de síntoma, cuidado de
        herida, indicación de medicación o señal de alarma: nunca respondas ese
        tipo de preguntas de memoria sin haber consultado esta herramienta primero.
        """
        if not _consume_budget():
            return BUDGET_EXCEEDED_MSG

        try:
            from app.graph.retrieval import search, sustentan_la_respuesta

            # Mismo criterio que el prefetch: solo se devuelve (y se cita) lo
            # que de verdad sustenta la respuesta. La fusión por ranking
            # siempre entrega k pasajes aunque ninguno venga al caso.
            chunks = sustentan_la_respuesta(search(consulta, scenario, top_k=4))
        except Exception as exc:
            logger.warning("Fallo en búsqueda de conocimiento clínico: %s", exc)
            return "No se encontró información relevante en el corpus para esta consulta."

        if not chunks:
            return "No se encontró información relevante en el corpus para esta consulta."

        turn_state["grounded"] = True
        sources = turn_state.setdefault("rag_sources", [])
        lines = []
        for chunk in chunks:
            text = chunk.get("text", "") if isinstance(chunk, dict) else getattr(chunk, "text", "")
            source = (
                chunk.get("source", "desconocido")
                if isinstance(chunk, dict)
                else getattr(chunk, "source", "desconocido")
            )
            page = chunk.get("page", 0) if isinstance(chunk, dict) else getattr(chunk, "page", 0)
            score = chunk.get("score") if isinstance(chunk, dict) else getattr(chunk, "score", None)
            relevancia = (
                chunk.get("relevancia") if isinstance(chunk, dict)
                else getattr(chunk, "relevancia", None)
            )
            sources.append(
                {"source": source, "scenario": scenario, "page": page,
                 "score": score, "relevancia": relevancia}
            )
            lines.append(f"[Fuente: {source}, pág {page}] {text}")
        return "\n\n".join(lines)

    @tool
    def registrar_evaluacion(
        sintomas: dict,
        criticidad: str,
        red_flags: list[str],
        dimensiones_cubiertas: list[str],
    ) -> str:
        """Registra la evaluación clínica estructurada de este turno: los síntomas
        relevados al paciente, tu estimación de criticidad ("verde"=estable,
        "amarillo"=vigilar, "rojo"=urgente), las señales de alarma detectadas y
        qué dimensiones del checklist postoperatorio ya cubriste en la llamada.
        Llámala cuando ya tengas suficiente información del paciente para emitir
        un juicio clínico de este turno.
        """
        if not _consume_budget():
            return BUDGET_EXCEEDED_MSG

        advertencia = None
        if criticidad not in _VALID_CRITICALITY:
            advertencia = f"criticidad '{criticidad}' inválida, se usó 'verde' por defecto"
            criticidad = "verde"

        ml_features: dict = dict(sintomas)
        ml_features.setdefault("procedimiento", turn_state.get("procedimiento", scenario or ""))
        for key in ("edad", "genero", "dia_postop", "n_comorbilidades"):
            if key not in ml_features and key in turn_state:
                ml_features[key] = turn_state[key]

        ml_result = None
        try:
            ml_result = triage_model.predict(ml_features)
        except Exception as exc:
            logger.warning("Fallo inesperado llamando al modelo de triaje: %s", exc)
            ml_result = None

        criticidad_ml = ml_result["criticidad"] if ml_result else None
        criticidad_final = criticidad
        if criticidad_ml and _CRITICALITY_ORDER[criticidad_ml] > _CRITICALITY_ORDER[criticidad_final]:
            criticidad_final = criticidad_ml

        turn_state["sintomas"] = sintomas
        turn_state["criticidad_llm"] = criticidad
        turn_state["criticidad_ml"] = criticidad_ml
        turn_state["criticidad_final"] = criticidad_final
        turn_state["red_flags"] = red_flags
        turn_state["dimensiones_cubiertas"] = dimensiones_cubiertas

        resumen = f"Evaluación registrada: criticidad final = {criticidad_final}."
        if advertencia:
            resumen += f" (advertencia: {advertencia})"
        return resumen

    @tool
    def escalar_a_equipo_clinico(motivo: str, red_flags: list[str]) -> str:
        """Marca la llamada para escalamiento inmediato al equipo clínico humano.
        Úsala cuando detectes señales de alarma que requieren atención urgente
        (por ejemplo: fiebre alta persistente, dolor incontrolable, sangrado
        activo, signos de infección severa, o cualquier riesgo para la vida del
        paciente). No la uses para casos leves o de rutina.
        """
        # Exenta del presupuesto de herramientas a propósito: es la acción de
        # seguridad clínica del turno y nunca puede perderse silenciosamente
        # contra el presupuesto compartido con las otras 3 tools (bug real
        # encontrado en pruebas: una llamada legítima devolvía
        # BUDGET_EXCEEDED_MSG sin aplicar el escalamiento).
        turn_state["escalar"] = True
        turn_state["motivo_escalamiento"] = motivo
        existing_flags = turn_state.get("red_flags", [])
        turn_state["red_flags"] = list(dict.fromkeys([*existing_flags, *red_flags]))
        return f"Escalamiento registrado: {motivo}"

    @tool
    def finalizar_llamada(resumen_corto: str) -> str:
        """Marca que la llamada puede cerrarse porque ya se cubrió lo necesario
        con el paciente. Incluye un resumen corto (1-2 frases) de lo conversado.
        """
        if not _consume_budget():
            return BUDGET_EXCEEDED_MSG

        turn_state["fin_llamada"] = True
        turn_state["resumen_corto"] = resumen_corto
        return "Cierre de llamada registrado."

    return [
        buscar_conocimiento_clinico,
        registrar_evaluacion,
        escalar_a_equipo_clinico,
        finalizar_llamada,
    ]
