"""Imprime el reporte de métricas para el README.

Uso: .venv/bin/python -m scripts.report_metrics
"""

from app.metrics import compute_metrics


def _ms(value: float) -> str:
    return f"{value:.0f} ms"


def _usd(value: float) -> str:
    return f"USD {value:.4f}"


def main() -> int:
    m = compute_metrics()
    if m["n_turns"] == 0:
        print("No hay turnos registrados en data/turns.jsonl.")
        print("Realice al menos una llamada al agente y vuelva a ejecutar este reporte.")
        return 0

    lat = m["latency"]
    tpt = m["tokens_per_turn"]
    tpc = m["tokens_per_call"]
    cost = m["cost_per_call_usd"]

    print("## Métricas del agente")
    print()
    print(f"Turnos registrados: {m['n_turns']} · Llamadas: {m['n_calls']}")
    print()
    print("### Latencia")
    print()
    print(
        "Medida en el servidor: desde que llega la petición de ElevenLabs hasta el "
        "primer token y hasta la respuesta completa (no incluye ASR/TTS ni red de ElevenLabs)."
    )
    print()
    ft = lat["first_token_ms"]
    tt = lat["total_ms"]
    print(f"- Primer token: p50 {_ms(ft['p50'])} · p95 {_ms(ft['p95'])} · media {_ms(ft['mean'])}")
    print(f"- Respuesta completa: p50 {_ms(tt['p50'])} · p95 {_ms(tt['p95'])} · media {_ms(tt['mean'])}")
    print()
    print("### Consumo")
    print()
    print(f"- Tokens de entrada por turno: p50 {tpt['input']['p50']:.0f} · media {tpt['input']['mean']:.0f}")
    print(f"- Tokens de salida por turno: p50 {tpt['output']['p50']:.0f} · media {tpt['output']['mean']:.0f}")
    print(f"- Tokens de entrada por llamada: p50 {tpc['input']['p50']:.0f} · media {tpc['input']['mean']:.0f}")
    print(f"- Tokens de salida por llamada: p50 {tpc['output']['p50']:.0f} · media {tpc['output']['mean']:.0f}")
    print(f"- Invocaciones al modelo por turno (media): {m['model_calls_per_turn']:.2f}")
    print(f"- Consultas RAG por llamada (media): {m['rag_queries_per_call']:.2f}")
    print()
    print("### Costo estimado")
    print()
    print(f"- Costo por llamada: p50 {_usd(cost['p50'])} · media {_usd(cost['mean'])}")
    print()
    print("### Supuestos")
    print()
    for assumption in cost["assumptions"]:
        print(f"- {assumption}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
