from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    gemini_api_key: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    groq_api_key: str = ""

    gemini_model: str = "gemini-3.6-flash"
    # Respaldo automático si Gemini agota su cuota gratuita diaria — también
    # de una familia permitida por el reto (Meta Llama vía Groq).
    groq_model: str = "llama-3.3-70b-versatile"
    # Tercer nivel de respaldo: cuota diaria de tokens independiente de la del
    # 70B, así una llamada nunca se queda sin modelo que responda.
    groq_fallback_model: str = "llama-3.1-8b-instant"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    chroma_dir: Path = BASE_DIR / "chroma_data"
    data_dir: Path = BASE_DIR / "data"
    corpus_dir: Path = BASE_DIR / "dataset" / "textos"

    rag_top_k: int = 4
    chunk_size_chars: int = 2200
    chunk_overlap_chars: int = 300

    # Interruptor de seguridad: agente model-driven en LangGraph (app/graph/)
    # vs. el pipeline fijo original (RAG pre-inyectado + Gemini/Groq directo).
    # En False, el sistema se comporta exactamente como antes de la migración.
    agentic_rag_enabled: bool = True

    # Pausas prosódicas `<break time="0.3s" />` en el texto hablado. Solo las
    # entiende la familia v2 de ElevenLabs (el agente usa eleven_flash_v2_5,
    # verificado). En false se eliminan del stream antes de llegar a la voz —
    # interruptor de emergencia si alguna vez el TTS las leyera en voz alta.
    voice_pauses_enabled: bool = True

    # Observabilidad: MLflow Tracing. Si el servidor de trazas no está corriendo
    # o mlflow no está instalado, el sistema funciona idéntico con un warning —
    # nunca bloquea una llamada de voz (ver el guardián en app/main.py).
    mlflow_enabled: bool = True
    # Apunta a un `mlflow server` real corriendo aparte (ver
    # requirements-mlflow-ui.txt): el cliente ligero (mlflow-tracing) NO sabe
    # escribir directo a un archivo/SQLite, solo hablar con un servidor por
    # HTTP. Si nadie levantó ese servidor (p. ej. un clon local sin el paso
    # opcional), la conexión falla rápido (ver guardián de timeout en
    # app/main.py) y el sistema sigue funcionando idéntico, sin trazas.
    mlflow_tracking_uri: str = "http://127.0.0.1:5605"
    mlflow_experiment_name: str = "clara-postop-agent"


settings = Settings()
