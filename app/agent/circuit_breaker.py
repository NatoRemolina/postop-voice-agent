"""Circuit breaker compartido entre los dos pipelines (fijo y agéntico) para no
seguir golpeando un proveedor que ya sabemos que está sin cupo — cada intento
inútil cuesta latencia real en una llamada de voz y, si el límite es por minuto,
puede estar retrasando su propia recuperación.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Cuota diaria agotada (ej. Gemini free tier, Groq TPD): no vale la pena
# reintentar pronto.
COOLDOWN_DAILY_SECONDS = 600
# Cuota por minuto (ej. Groq TPM): se recupera solo, cooldown corto.
COOLDOWN_MINUTE_SECONDS = 65

# Errores que significan "esta petición está mal formada", no "el proveedor no
# está disponible". Ver la nota en mark_down().
_ERRORES_DE_PETICION = (
    "does not support model prefilling",
    "final request turn must be a user message",
    "invalid_request",
    "invalid argument",
    "contents is not specified",
)

_down_until: dict[str, float] = {}


def is_down(provider: str) -> bool:
    return time.monotonic() < _down_until.get(provider, 0.0)


def mark_down(provider: str, error: object) -> float:
    """Registra el proveedor como caído; usa un cooldown más largo si el error
    menciona un límite diario/por día, corto si es por minuto, y el default
    diario si no se puede determinar (más seguro subestimar la recuperación)."""
    text = str(error).lower()
    # Un error de VALIDACIÓN de la petición no es indisponibilidad: el proveedor
    # está vivo y rechazaría igual a cualquier cliente con esa misma petición.
    # Sacarlo de servicio por esto es el peor error posible, y pasó en
    # producción: un historial que terminaba en turno del asistente hizo que
    # Gemini devolviera "does not support model prefilling", el breaker lo leyó
    # como cuota agotada y lo apagó 10 minutos en plena llamada, dejando todo el
    # peso en los respaldos hasta agotarlos también.
    if any(p in text for p in _ERRORES_DE_PETICION):
        logger.warning(
            "%s devolvió un error de petición, no de cuota: NO se marca como caído (%s)",
            provider, str(error)[:200],
        )
        return 0.0
    if "per minute" in text or "tokens per minute" in text or "tpm" in text:
        cooldown = COOLDOWN_MINUTE_SECONDS
    else:
        cooldown = COOLDOWN_DAILY_SECONDS
    _down_until[provider] = time.monotonic() + cooldown
    return cooldown


def reset(provider: str) -> None:
    _down_until.pop(provider, None)
