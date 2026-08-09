"""Interfaz de predicción para el modelo de triaje entrenado por separado.

Mientras `data/triage_model.pkl` no exista (aún no se ha entrenado), `predict`
degrada a None de inmediato: el resto del sistema debe tratar eso como "no
hay opinión del modelo de ML todavía" y apoyarse solo en el juicio del LLM.
"""

import logging
import pickle
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_MODEL_PATH = settings.data_dir / "triage_model.pkl"
_CRITICALITY_LEVELS = ("verde", "amarillo", "rojo")

# Claves que espera el modelo en `features` (pueden faltar; se rellenan con
# valores neutros/razonables si no vienen del turno):
#   dolor_nrs (0-10), fiebre_c (°C), movilidad, herida, apetito, sueno
#   (categorías reportadas por el paciente), procedimiento (str, nombre de
#   escenario), dia_postop (int), edad (int), genero (str), n_comorbilidades (int)
_NEUTRAL_DEFAULTS: dict[str, Any] = {
    "dolor_nrs": 3,
    "fiebre_c": 36.8,
    "movilidad": "normal",
    "herida": "normal",
    "apetito": "normal",
    "sueno": "normal",
    "procedimiento": "desconocido",
    "dia_postop": 3,
    "edad": 50,
    "genero": "desconocido",
    "n_comorbilidades": 0,
}

_model: Any = None
_model_load_attempted = False


def _load_model() -> Any:
    global _model, _model_load_attempted
    if _model_load_attempted:
        return _model
    _model_load_attempted = True
    if not _MODEL_PATH.exists():
        return None
    try:
        with open(_MODEL_PATH, "rb") as f:
            _model = pickle.load(f)
    except Exception as exc:
        logger.warning("No se pudo cargar triage_model.pkl: %s", exc)
        _model = None
    return _model


def predict(features: dict) -> dict | None:
    """Predice criticidad a partir de features del turno.

    Devuelve None si el modelo aún no existe o si la predicción falla por
    cualquier motivo (nunca propaga una excepción hacia el llamador).
    """
    model = _load_model()
    if model is None:
        return None

    try:
        import pandas as pd

        row = {**_NEUTRAL_DEFAULTS}
        for key, value in features.items():
            if value is not None:
                row[key] = value

        frame = pd.DataFrame([row])
        proba = model.predict_proba(frame)[0]
        classes = list(getattr(model, "classes_", _CRITICALITY_LEVELS))
        probabilidades = {str(cls): float(p) for cls, p in zip(classes, proba)}

        criticidad = max(probabilidades, key=probabilidades.get)
        if criticidad not in _CRITICALITY_LEVELS:
            criticidad = "verde"

        return {"criticidad": criticidad, "probabilidades": probabilidades}
    except Exception as exc:
        logger.warning("Fallo en predicción del modelo de triaje: %s", exc)
        return None
