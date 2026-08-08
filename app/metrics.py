"""Métricas de latencia, consumo y costo a partir de data/turns.jsonl."""

import statistics
from collections import defaultdict
from typing import Any

from app.storage import read_jsonl

GEMINI_INPUT_USD_PER_1M = 1.50
GEMINI_OUTPUT_USD_PER_1M = 7.50

COST_ASSUMPTIONS = [
    (
        "Precios de lista de gemini-3.6-flash: "
        f"USD {GEMINI_INPUT_USD_PER_1M:.2f} por 1M de tokens de entrada y "
        f"USD {GEMINI_OUTPUT_USD_PER_1M:.2f} por 1M de tokens de salida."
    ),
    (
        "El reto se ejecuta en el free tier de Gemini; el costo se extrapola "
        "a los precios de lista de producción."
    ),
    (
        "No incluye el costo de la plataforma de voz de ElevenLabs "
        "(ASR, TTS ni telefonía)."
    ),
]


def _p50(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    return float(statistics.quantiles(values, n=20)[9])


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    return float(statistics.quantiles(values, n=20)[18])


def _mean(values: list[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def _dist_full(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(_p50(values), 1),
        "p95": round(_p95(values), 1),
        "mean": round(_mean(values), 1),
    }


def _dist_p50(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(_p50(values), 1),
        "mean": round(_mean(values), 1),
    }


def compute_metrics() -> dict[str, Any]:
    turns = read_jsonl("turns.jsonl")

    by_call: dict[str, list[dict]] = defaultdict(list)
    for turn in turns:
        by_call[str(turn.get("conversation_id") or "unknown")].append(turn)

    first_token_ms = [
        float(t["latency_first_token_ms"])
        for t in turns
        if t.get("latency_first_token_ms") is not None
    ]
    total_ms = [
        float(t["latency_total_ms"])
        for t in turns
        if t.get("latency_total_ms") is not None
    ]

    input_per_turn = [float(t.get("input_tokens") or 0) for t in turns]
    output_per_turn = [float(t.get("output_tokens") or 0) for t in turns]
    model_calls_per_turn = [float(t.get("model_calls") or 0) for t in turns]

    input_per_call = [
        float(sum(t.get("input_tokens") or 0 for t in call_turns))
        for call_turns in by_call.values()
    ]
    output_per_call = [
        float(sum(t.get("output_tokens") or 0 for t in call_turns))
        for call_turns in by_call.values()
    ]
    rag_per_call = [
        float(sum(t.get("rag_queries") or 0 for t in call_turns))
        for call_turns in by_call.values()
    ]
    cost_per_call = [
        tokens_in / 1_000_000 * GEMINI_INPUT_USD_PER_1M
        + tokens_out / 1_000_000 * GEMINI_OUTPUT_USD_PER_1M
        for tokens_in, tokens_out in zip(input_per_call, output_per_call)
    ]

    return {
        "n_turns": len(turns),
        "n_calls": len(by_call),
        "latency": {
            "first_token_ms": _dist_full(first_token_ms),
            "total_ms": _dist_full(total_ms),
        },
        "tokens_per_turn": {
            "input": _dist_p50(input_per_turn),
            "output": _dist_p50(output_per_turn),
        },
        "tokens_per_call": {
            "input": _dist_p50(input_per_call),
            "output": _dist_p50(output_per_call),
        },
        "model_calls_per_turn": round(_mean(model_calls_per_turn), 2),
        "rag_queries_per_call": round(_mean(rag_per_call), 2),
        "cost_per_call_usd": {
            "p50": round(_p50(cost_per_call), 6),
            "mean": round(_mean(cost_per_call), 6),
            "assumptions": COST_ASSUMPTIONS,
        },
    }
