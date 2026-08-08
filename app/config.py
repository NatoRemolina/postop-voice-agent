from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    gemini_api_key: str = ""
    elevenlabs_api_key: str = ""

    gemini_model: str = "gemini-3.6-flash"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    chroma_dir: Path = BASE_DIR / "chroma_data"
    data_dir: Path = BASE_DIR / "data"
    corpus_dir: Path = BASE_DIR / "dataset" / "textos"

    rag_top_k: int = 4
    chunk_size_chars: int = 2200
    chunk_overlap_chars: int = 300


settings = Settings()
