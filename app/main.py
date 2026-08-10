import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import calls, chat, documents, metrics, patients, voice, web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

def _init_mlflow_tracing() -> None:
    """Se ejecuta en un hilo aparte con timeout duro (ver abajo): el cliente
    de trazas habla por HTTP con un `mlflow server` que puede no estar
    corriendo (clon local sin el paso opcional, o caído en producción). Se
    verificó en pruebas que una conexión colgada al tracking server puede
    bloquear más de 60s con la configuración por defecto — inaceptable para
    el arranque (compuerta de 15 min) o para un turno de voz en curso. Los
    envvars de abajo fuerzan un fallo rápido (~1-2s); el hilo+timeout es la
    segunda red de seguridad si aun así algo se cuelga.
    """
    import os

    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "3")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    import mlflow

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_experiment_name)
    # Captura automática de cada invocación LangChain/LangGraph dentro del
    # agente (prompts, tokens, latencia por nodo) como spans hijos de nuestro
    # span manual por turno (ver app/graph/agent.py).
    import mlflow.langchain

    mlflow.langchain.autolog()

    from app import observability

    observability.mark_ready()
    logger.info("MLflow tracing activo: %s / experimento '%s'",
                settings.mlflow_tracking_uri, settings.mlflow_experiment_name)


if settings.mlflow_enabled:
    import threading

    _t = threading.Thread(target=_init_mlflow_tracing, daemon=True)
    _t.start()
    _t.join(timeout=5)
    if _t.is_alive():
        logger.warning(
            "MLflow no respondió en 5s (¿mlflow server no está corriendo en %s?); "
            "el agente arranca igual, sin trazas. Ver README (paso opcional de MLflow).",
            settings.mlflow_tracking_uri,
        )

app = FastAPI(title="Agente postoperatorio — Tech Sphere Challenge 2026")

# El frontend de demo (Lovable) corre en otro origen; la API debe aceptar CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(calls.router)
app.include_router(metrics.router)
app.include_router(patients.router)
app.include_router(voice.router)
app.include_router(web.router)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "web" / "static"),
    name="static",
)


@app.get("/health")
async def health():
    return {"status": "ok"}
