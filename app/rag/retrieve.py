from dataclasses import dataclass

from app.config import settings
from app.rag.store import embed_query, get_collection


@dataclass
class RetrievedChunk:
    text: str
    source: str
    scenario: str
    page: int
    score: float


def retrieve(query: str, top_k: int | None = None) -> list[RetrievedChunk]:
    collection = get_collection()
    if collection.count() == 0:
        return []
    result = collection.query(
        query_embeddings=[embed_query(query)],
        n_results=top_k or settings.rag_top_k,
        include=["documents", "metadatas", "distances"],
    )
    chunks = []
    for text, meta, dist in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        chunks.append(
            RetrievedChunk(
                text=text,
                source=meta["source"],
                scenario=meta["scenario"],
                page=meta.get("page", 0),
                score=1 - dist,
            )
        )
    return chunks
