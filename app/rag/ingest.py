import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.config import settings
from app.rag.store import embed_passages, get_collection

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    doc_id: str
    source_name: str
    scenario: str
    n_chunks: int
    n_chars: int
    warning: str | None = None


def make_doc_id(source_name: str, scenario: str) -> str:
    raw = f"{scenario}/{source_name}"
    slug = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", slug).strip("_").lower()[:80]
    digest = hashlib.sha1(raw.encode()).hexdigest()[:8]
    return f"{slug}_{digest}"


def extract_pdf_pages(path: Path) -> list[tuple[int, str]]:
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("page %s of %s failed to extract: %s", i + 1, path.name, exc)
            text = ""
        text = re.sub(r"[ \t]+", " ", text).strip()
        if text:
            pages.append((i + 1, text))
    return pages


def chunk_pages(pages: list[tuple[int, str]]) -> list[tuple[str, int, int]]:
    """Divide el texto en fragmentos solapados.

    Devuelve (texto, página_inicial, página_final). Se guardan AMBAS porque un
    fragmento de 2.200 caracteres suele cruzar el salto de página: citando solo
    la inicial, quien vaya a verificar la cita puede abrir esa página y no
    encontrar la frase (comprobado en auditoría: la respuesta salía de la
    página siguiente a la citada). La rúbrica exige que la referencia resista
    una verificación contra la fuente real.
    """
    size = settings.chunk_size_chars
    overlap = settings.chunk_overlap_chars
    full = ""
    page_starts: list[tuple[int, int]] = []  # (char_offset, page_number)
    for page_num, text in pages:
        page_starts.append((len(full), page_num))
        full += text + "\n\n"

    def page_for_offset(offset: int) -> int:
        page = page_starts[0][1] if page_starts else 1
        for start, num in page_starts:
            if start <= offset:
                page = num
            else:
                break
        return page

    chunks: list[tuple[str, int, int]] = []
    pos = 0
    while pos < len(full):
        end = min(pos + size, len(full))
        if end < len(full):
            window = full[pos:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "))
            if cut > size // 2:
                end = pos + cut + 1
        chunk = full[pos:end].strip()
        if len(chunk) > 80:
            chunks.append((chunk, page_for_offset(pos), page_for_offset(max(pos, end - 1))))
        if end >= len(full):
            break
        pos = max(end - overlap, pos + 1)
    return chunks


def ingest_pdf(path: Path, scenario: str, uploaded: bool = False) -> IngestResult:
    source_name = path.name
    doc_id = make_doc_id(source_name, scenario)
    pages = extract_pdf_pages(path)
    n_chars = sum(len(t) for _, t in pages)

    if n_chars < 200:
        warning = "sin capa de texto extraible (PDF escaneado?) — omitido"
        logger.warning("%s: %s", source_name, warning)
        return IngestResult(doc_id, source_name, scenario, 0, n_chars, warning)

    chunks = chunk_pages(pages)
    collection = get_collection()
    # Re-ingesting the same doc replaces it entirely (stale chunks must not linger)
    collection.delete(where={"doc_id": doc_id})

    batch = 32
    for i in range(0, len(chunks), batch):
        part = chunks[i : i + batch]
        texts = [c[0] for c in part]
        collection.add(
            ids=[f"{doc_id}::{i + j}" for j in range(len(part))],
            documents=texts,
            embeddings=embed_passages(texts),
            metadatas=[
                {
                    "doc_id": doc_id,
                    "source": source_name,
                    "scenario": scenario,
                    "page": page,
                    "page_end": page_end,
                    "uploaded": uploaded,
                }
                for _, page, page_end in part
            ],
        )
    return IngestResult(doc_id, source_name, scenario, len(chunks), n_chars)


def delete_document(doc_id: str) -> int:
    collection = get_collection()
    existing = collection.get(where={"doc_id": doc_id}, include=[])
    n = len(existing["ids"])
    if n:
        collection.delete(where={"doc_id": doc_id})
    return n


def list_documents() -> list[dict]:
    collection = get_collection()
    result = collection.get(include=["metadatas"])
    docs: dict[str, dict] = {}
    for meta in result["metadatas"]:
        d = docs.setdefault(
            meta["doc_id"],
            {
                "doc_id": meta["doc_id"],
                "source": meta["source"],
                "scenario": meta["scenario"],
                "uploaded": meta.get("uploaded", False),
                "n_chunks": 0,
            },
        )
        d["n_chunks"] += 1
    return sorted(docs.values(), key=lambda d: (d["scenario"], d["source"]))
