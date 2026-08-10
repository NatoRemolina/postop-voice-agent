"""Recuperación híbrida (densa + BM25) con reescritura de consulta y calificación
de relevancia, todo sobre el mismo Chroma que usa el pipeline actual.

Diseñado para nunca romper la voz: cualquier fallo de un paso (Groq caído,
timeout, colección vacía, parsing) degrada al siguiente paso disponible en
lugar de propagar una excepción.
"""

import logging
import time

from langchain_chroma import Chroma
from langchain_classic.retrievers.ensemble import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

from app.agent import circuit_breaker
from app.config import settings
from app.rag.store import COLLECTION, embed_passages, embed_query, get_client, get_collection

logger = logging.getLogger(__name__)

_REWRITE_MODEL = "llama-3.1-8b-instant"
_QUALIFY_MODEL = "llama-3.1-8b-instant"
# Misma clave que usa la cascada de app/graph/agent.py para este modelo: es el
# mismo cupo real (tokens/minuto de llama-3.1-8b-instant), así que compartir el
# breaker evita que retrieval y la cascada se saturen mutuamente sin enterarse.
_HELPER_BREAKER_KEY = "groq-8b"
_LLM_TIMEOUT_S = 8
_BRANCH_K = 8
_SNIPPET_CHARS = 300
# Con lotes grandes el modelo 8B a veces degenera generando una llamada a
# función inválida/gigantesca antes de fallar, tardando decenas de segundos
# (presupuesto de voz). Se limita el lote calificado para acotar ese riesgo;
# el resto de candidatos igual queda disponible si el reintento se activa.
_MAX_QUALIFY_BATCH = 10


class Reescritura(BaseModel):
    consulta_principal: str
    sub_consultas: list[str] = Field(default_factory=list)


class Calificacion(BaseModel):
    relevantes: list[bool]


class _ChromaStoreEmbeddings(Embeddings):
    """Adaptador Embeddings de LangChain que delega en app.rag.store (fastembed),
    para no reimplementar embeddings ni divergir del pipeline de ingesta."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return embed_passages(texts)

    def embed_query(self, text: str) -> list[float]:
        return embed_query(text)


_STORE_EMBEDDINGS = _ChromaStoreEmbeddings()


def _make_groq(model: str) -> ChatGroq:
    return ChatGroq(
        model=model,
        temperature=0,
        api_key=settings.groq_api_key,
        request_timeout=_LLM_TIMEOUT_S,
        max_retries=1,
    )


def _scenario_filter(scenario: str | None) -> dict | None:
    if not scenario:
        return None
    # Documentos subidos en vivo desde la consola siempre son elegibles (compuerta G5).
    return {"$or": [{"scenario": scenario}, {"uploaded": True}]}


def _rewrite_query(query: str) -> tuple[Reescritura, bool]:
    """Traduce la consulta coloquial del paciente a lenguaje clínico buscable.

    Devuelve (reescritura, ok). Si algo falla, ok=False y se usa la consulta
    original tal cual (sin sub-consultas).
    """
    # El 8B tiene un tope de 6.000 tokens/minuto y esta función lo consume dos
    # veces por turno junto con _filter_relevant. En una llamada de voz real
    # (turnos cada ~10s) ese presupuesto se agota en 2-3 turnos, y desde ahí
    # cada intento es un 429 garantizado que solo agrega ~2s de latencia antes
    # de degradar igual. Verificado en producción: fue la causa de que
    # ElevenLabs cortara llamadas con "custom_llm generation failed".
    if circuit_breaker.is_down(_HELPER_BREAKER_KEY):
        return Reescritura(consulta_principal=query, sub_consultas=[]), False
    try:
        structured = _make_groq(_REWRITE_MODEL).with_structured_output(Reescritura)
        prompt = (
            "Eres un asistente clínico. Traduce la consulta coloquial o regional de un "
            "paciente postoperatorio a lenguaje clínico buscable en literatura médica "
            "(guías, protocolos). Genera una 'consulta_principal' clara y concisa, y hasta "
            "2 'sub_consultas' con formulaciones o sinónimos clínicos alternativos.\n"
            f'Consulta del paciente: "{query}"'
        )
        result = structured.invoke(prompt)
        if not isinstance(result, Reescritura) or not result.consulta_principal.strip():
            raise ValueError("reescritura vacía o con forma inesperada")
        result.sub_consultas = [s.strip() for s in result.sub_consultas if s and s.strip()][:2]
        return result, True
    except Exception as exc:
        circuit_breaker.mark_down(_HELPER_BREAKER_KEY, exc)
        logger.warning("query rewrite failed, using original query", exc_info=True)
        return Reescritura(consulta_principal=query, sub_consultas=[]), False


def _build_dense_retriever(where: dict | None):
    vectorstore = Chroma(
        client=get_client(),
        collection_name=COLLECTION,
        embedding_function=_STORE_EMBEDDINGS,
    )
    search_kwargs: dict = {"k": _BRANCH_K}
    if where:
        search_kwargs["filter"] = where
    return vectorstore.as_retriever(search_kwargs=search_kwargs)


def _build_bm25_retriever(where: dict | None):
    collection = get_collection()
    if collection.count() == 0:
        return None
    result = collection.get(include=["documents", "metadatas"], where=where)
    texts = result.get("documents") or []
    metas = result.get("metadatas") or []
    if not texts:
        return None
    documents = [
        Document(page_content=text, metadata=meta or {}) for text, meta in zip(texts, metas)
    ]
    return BM25Retriever.from_documents(documents, k=_BRANCH_K)


def _hybrid_search_one(query: str, where: dict | None) -> tuple[list[Document], dict]:
    """Busca `query` en las ramas densa y BM25 y las combina por rank fusion.

    No propaga excepciones: cualquier rama que falle simplemente se omite.
    """
    branch_meta = {"n_denso": 0, "n_bm25": 0, "bm25_disponible": False, "error": None}

    dense_retriever = None
    dense_docs: list[Document] = []
    try:
        dense_retriever = _build_dense_retriever(where)
        dense_docs = dense_retriever.invoke(query)
        branch_meta["n_denso"] = len(dense_docs)
    except Exception:
        logger.warning("dense retrieval failed for %r", query, exc_info=True)
        dense_retriever = None

    bm25_retriever = None
    bm25_docs: list[Document] = []
    try:
        bm25_retriever = _build_bm25_retriever(where)
        if bm25_retriever is not None:
            bm25_docs = bm25_retriever.invoke(query)
            branch_meta["n_bm25"] = len(bm25_docs)
            branch_meta["bm25_disponible"] = True
    except Exception:
        logger.warning("bm25 retrieval failed for %r", query, exc_info=True)
        bm25_retriever = None
        bm25_docs = []

    if dense_retriever is None and bm25_retriever is None:
        branch_meta["error"] = "ninguna rama de búsqueda disponible"
        return [], branch_meta

    if dense_retriever is not None and bm25_retriever is not None:
        try:
            # weighted_reciprocal_rank fusiona los resultados ya obtenidos sin
            # volver a invocar cada retriever (EnsembleRetriever.invoke() los
            # re-consultaría, duplicando llamadas a Chroma/BM25 sin necesidad).
            ensemble = EnsembleRetriever(
                retrievers=[dense_retriever, bm25_retriever], weights=[0.6, 0.4]
            )
            combined = ensemble.weighted_reciprocal_rank([dense_docs, bm25_docs])
            return combined, branch_meta
        except Exception:
            logger.warning("rank fusion failed, falling back to dense only", exc_info=True)
            return dense_docs, branch_meta

    return dense_docs or bm25_docs, branch_meta


def _dedup_docs(docs: list[Document]) -> list[Document]:
    seen: set[tuple] = set()
    unique: list[Document] = []
    for doc in docs:
        meta = doc.metadata or {}
        key = (meta.get("source"), meta.get("page"), doc.page_content[:80])
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)
    return unique


def _filter_relevant(query: str, docs: list[Document]) -> list[Document]:
    """Calificación de relevancia en un solo batch. Si falla, se consideran
    todos relevantes (no se bloquea la respuesta por un fallo de calificación)."""
    if not docs:
        return []
    # Mismo cupo de 6.000 tokens/minuto que _rewrite_query (ver nota allí):
    # si ya está saturado, calificar es un 429 seguro que solo cuesta latencia.
    if circuit_breaker.is_down(_HELPER_BREAKER_KEY):
        return docs
    try:
        structured = _make_groq(_QUALIFY_MODEL).with_structured_output(Calificacion)
        snippets = "\n".join(
            f"{i}. {doc.page_content[:_SNIPPET_CHARS]}" for i, doc in enumerate(docs)
        )
        prompt = (
            "Un paciente postoperatorio hizo esta consulta:\n"
            f'"{query}"\n\n'
            "A continuación hay exactamente "
            f"{len(docs)} fragmentos numerados (0 a {len(docs) - 1}) recuperados de guías "
            "clínicas. Responde con 'relevantes': un array de EXACTAMENTE "
            f"{len(docs)} booleanos, uno por fragmento en el mismo orden (ni uno más, ni uno "
            "menos). true si el fragmento ayuda a responder la consulta del paciente, false "
            "si no.\n\n"
            f"{snippets}"
        )
        result = structured.invoke(prompt)
        if not isinstance(result, Calificacion) or len(result.relevantes) != len(docs):
            raise ValueError("calificación con forma inesperada")
        return [doc for doc, ok in zip(docs, result.relevantes) if ok]
    except Exception as exc:
        circuit_breaker.mark_down(_HELPER_BREAKER_KEY, exc)
        logger.warning("relevance qualification failed, keeping all candidates", exc_info=True)
        return docs


def _to_result(doc: Document, rank: int, total: int) -> dict:
    meta = doc.metadata or {}
    # EnsembleRetriever no expone un score numérico de fusión; se aproxima con
    # la posición en el ranking final (1.0 = mejor, decreciente y lineal).
    score = 1.0 - (rank / total if total else 0.0)
    return {
        "text": doc.page_content,
        "source": meta.get("source", "desconocido"),
        "scenario": meta.get("scenario", "desconocido"),
        "page": meta.get("page", 0),
        "score": round(score, 4),
    }


def search_diagnostics(
    query: str, scenario: str | None = None, top_k: int = 6, fast: bool = False
) -> dict:
    """Ejecuta el pipeline completo y devuelve todo el detalle intermedio
    (para telemetría) además de los resultados finales en "resultados".

    fast=True omite los dos pasos que usan un LLM auxiliar (reescritura de la
    consulta y calificación de relevancia) y va directo a la búsqueda híbrida:
    ~200-400ms en vez de 2-3s. Es el modo del PREFETCH del turno de voz, donde
    esos 2-3s ocurren ANTES de la primera palabra del agente (medido en
    producción: eran la mayor parte de los 4-6s de silencio que hacían caer
    llamadas). El modo completo queda para la tool explícita del modelo y el
    agente post-llamada, donde la latencia no bloquea la voz.
    """
    t_start = time.perf_counter()
    diag: dict = {
        "query_original": query,
        "scenario": scenario,
        "consulta_reescrita": query,
        "sub_consultas": [],
        "reescritura_ok": False,
        "reescritura_ms": 0.0,
        "rondas": [],
        "n_candidatos": 0,
        "n_calificados": 0,
        "n_relevantes": 0,
        "calificacion_ms": 0.0,
        "reintento": False,
        "reintento_motivo": None,
        "total_ms": 0.0,
        "resultados": [],
    }

    if not query or not query.strip():
        diag["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
        return diag

    if fast:
        reescritura = Reescritura(consulta_principal=query, sub_consultas=[])
    else:
        try:
            t0 = time.perf_counter()
            reescritura, ok = _rewrite_query(query)
            diag["reescritura_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            diag["reescritura_ok"] = ok
            diag["consulta_reescrita"] = reescritura.consulta_principal
            diag["sub_consultas"] = list(reescritura.sub_consultas)
        except Exception:
            logger.error("unexpected failure around query rewrite", exc_info=True)
            reescritura = Reescritura(consulta_principal=query, sub_consultas=[])

    seen_q: set[str] = set()
    queries: list[str] = []
    for q in [reescritura.consulta_principal, *reescritura.sub_consultas]:
        q = (q or "").strip()
        if q and q.lower() not in seen_q:
            seen_q.add(q.lower())
            queries.append(q)
        if len(queries) == 3:
            break
    if not queries:
        queries = [query]

    candidates: list[Document] = []
    try:
        where = _scenario_filter(scenario)
        for q in queries:
            t0 = time.perf_counter()
            docs, branch_meta = _hybrid_search_one(q, where)
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            diag["rondas"].append({"query": q, "ms": elapsed, **branch_meta})
            candidates.extend(docs)
    except Exception:
        logger.error("unexpected failure during hybrid search rounds", exc_info=True)

    candidates = _dedup_docs(candidates)
    diag["n_candidatos"] = len(candidates)

    if fast:
        filtered = candidates
        diag["n_relevantes"] = len(filtered)
    else:
        to_qualify = candidates[:_MAX_QUALIFY_BATCH]
        diag["n_calificados"] = len(to_qualify)
        t0 = time.perf_counter()
        filtered = _filter_relevant(query, to_qualify) if to_qualify else []
        diag["calificacion_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        diag["n_relevantes"] = len(filtered)

    if not filtered:
        diag["reintento"] = True
        diag["reintento_motivo"] = (
            "sin candidatos en la búsqueda inicial"
            if not candidates
            else "ningún candidato calificó como relevante"
        )
        try:
            t0 = time.perf_counter()
            retry_docs, retry_meta = _hybrid_search_one(query, None)
            diag["rondas"].append(
                {
                    "query": query,
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                    "reintento": True,
                    **retry_meta,
                }
            )
            filtered = retry_docs
        except Exception:
            logger.error("unexpected failure during retry round", exc_info=True)
            filtered = []

    filtered = _dedup_docs(filtered)[:top_k]
    total = len(filtered)
    diag["resultados"] = [_to_result(doc, i, total) for i, doc in enumerate(filtered)]
    diag["total_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
    return diag


def search(
    query: str, scenario: str | None = None, top_k: int = 6, fast: bool = False
) -> list[dict]:
    """Búsqueda híbrida con reescritura y calificación de relevancia.

    Nunca levanta excepción: cualquier fallo interno degrada a lo mejor
    disponible, y un fallo catastrófico devuelve lista vacía.
    """
    try:
        return search_diagnostics(query, scenario, top_k, fast=fast)["resultados"]
    except Exception:
        logger.error("search() failed unexpectedly, returning empty results", exc_info=True)
        return []
