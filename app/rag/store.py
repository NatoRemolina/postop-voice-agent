import threading

import chromadb
from fastembed import TextEmbedding

from app.config import settings

COLLECTION = "clinical_corpus"

_lock = threading.Lock()
_client: chromadb.ClientAPI | None = None
_embedder: TextEmbedding | None = None


def get_client() -> chromadb.ClientAPI:
    global _client
    with _lock:
        if _client is None:
            _client = chromadb.PersistentClient(path=str(settings.chroma_dir))
        return _client


def get_collection() -> chromadb.Collection:
    return get_client().get_or_create_collection(
        COLLECTION, metadata={"hnsw:space": "cosine"}
    )


def get_embedder() -> TextEmbedding:
    global _embedder
    with _lock:
        if _embedder is None:
            _embedder = TextEmbedding(model_name=settings.embedding_model)
        return _embedder


def _uses_e5_prefixes() -> bool:
    return "e5" in settings.embedding_model.lower()


def embed_passages(texts: list[str]) -> list[list[float]]:
    if _uses_e5_prefixes():
        texts = [f"passage: {t}" for t in texts]
    return [e.tolist() for e in get_embedder().embed(texts)]


def embed_query(text: str) -> list[float]:
    if _uses_e5_prefixes():
        text = f"query: {text}"
    return next(iter(get_embedder().embed([text]))).tolist()
