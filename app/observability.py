"""Bandera compartida de MLflow: `app/main.py` la enciende solo si la
inicialización (set_tracking_uri + set_experiment + autolog) terminó bien
DENTRO del timeout duro de 5s. `app/graph/agent.py` la consulta en vivo antes
de abrir cada span — así, si el servidor de trazas nunca respondió (o cayó a
mitad de la demo), el agente deja de intentar tocarlo en cada turno de voz en
vez de arriesgarse a la misma llamada colgada que motivó este guardián."""

_ready = False


def mark_ready() -> None:
    global _ready
    _ready = True


def is_ready() -> bool:
    return _ready
