"""Circuit breaker compartido entre los dos pipelines (fijo y agéntico) para no
seguir golpeando un proveedor que ya sabemos que está sin cupo — cada intento
inútil cuesta latencia real en una llamada de voz y, si el límite es por minuto,
puede estar retrasando su propia recuperación.
"""

import time

# Cuota diaria agotada (ej. Gemini free tier, Groq TPD): no vale la pena
# reintentar pronto.
COOLDOWN_DAILY_SECONDS = 600
# Cuota por minuto (ej. Groq TPM): se recupera solo, cooldown corto.
COOLDOWN_MINUTE_SECONDS = 65

_down_until: dict[str, float] = {}


def is_down(provider: str) -> bool:
    return time.monotonic() < _down_until.get(provider, 0.0)


def mark_down(provider: str, error: object) -> float:
    """Registra el proveedor como caído; usa un cooldown más largo si el error
    menciona un límite diario/por día, corto si es por minuto, y el default
    diario si no se puede determinar (más seguro subestimar la recuperación)."""
    text = str(error).lower()
    if "per minute" in text or "tokens per minute" in text or "tpm" in text:
        cooldown = COOLDOWN_MINUTE_SECONDS
    else:
        cooldown = COOLDOWN_DAILY_SECONDS
    _down_until[provider] = time.monotonic() + cooldown
    return cooldown


def reset(provider: str) -> None:
    _down_until.pop(provider, None)
