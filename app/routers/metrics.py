from fastapi import APIRouter, Query

from app.metrics import compute_metrics
from app.privacy import first_name
from app.storage import read_jsonl

router = APIRouter()


@router.get("/api/metrics")
async def get_metrics():
    return compute_metrics()


@router.get("/api/turns")
async def get_turns(limit: int = Query(50, ge=1, le=500)):
    """Los últimos turnos del log crudo, para que las métricas publicadas sean
    auditables sin acceso al servidor.

    El README y el informe ofrecen `data/turns.jsonl` como evidencia
    verificable, pero el jurado no tiene shell en la máquina: sin este endpoint
    la afirmación no era comprobable. Devuelve exactamente los campos con los
    que se calculan las métricas (latencia, tokens, modelo real que respondió,
    fuentes citadas con su relevancia y la decisión del turno).

    Minimización de datos (Ley 1581 de 2012, ver docs/gobernanza-datos.md): se
    omite el texto del paciente y el del agente — lo auditable aquí son las
    métricas y las decisiones, no el contenido clínico de la conversación.
    """
    rows = read_jsonl("turns.jsonl")
    recientes = rows[-limit:]
    return {
        "n_total": len(rows),
        "n_devueltos": len(recientes),
        "nota": (
            "Texto de la conversación omitido por minimización de datos. "
            "El detalle completo de una llamada concreta está en "
            "/api/calls/{conversation_id}."
        ),
        "turns": [
            {
                "conversation_id": r.get("conversation_id"),
                "turn_index": r.get("turn_index"),
                "paciente": first_name(r.get("paciente") or "") or None,
                "latency_first_token_ms": r.get("latency_first_token_ms"),
                "latency_total_ms": r.get("latency_total_ms"),
                "input_tokens": r.get("input_tokens"),
                "output_tokens": r.get("output_tokens"),
                "model_calls": r.get("model_calls"),
                "model": r.get("model"),
                "fallback_used": r.get("fallback_used"),
                "rag_queries": r.get("rag_queries"),
                "rag_sources": r.get("rag_sources"),
                "control": r.get("control"),
                "orquestacion": r.get("orquestacion"),
            }
            for r in recientes
        ],
    }
