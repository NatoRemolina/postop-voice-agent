"""Indexa todo dataset/textos/ en ChromaDB. Correr una sola vez (o tras limpiar chroma_data/).

Uso: .venv/bin/python -m scripts.ingest_corpus
"""

import logging
import sys
import time

from app.config import settings
from app.rag.ingest import ingest_pdf
from app.rag.store import get_collection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ingest_corpus")


def main() -> int:
    corpus = settings.corpus_dir
    if not corpus.is_dir():
        logger.error("no existe %s", corpus)
        return 1

    pdfs = sorted(corpus.rglob("*.pdf"))
    logger.info("encontrados %d PDFs en %s", len(pdfs), corpus)

    t0 = time.time()
    ok, skipped = 0, []
    for i, path in enumerate(pdfs, 1):
        scenario = path.parent.name
        try:
            result = ingest_pdf(path, scenario)
        except Exception:
            logger.exception("[%d/%d] FALLÓ %s", i, len(pdfs), path.name)
            skipped.append((path.name, "excepción"))
            continue
        if result.n_chunks == 0:
            skipped.append((path.name, result.warning or "sin texto"))
            logger.warning("[%d/%d] OMITIDO %s (%s)", i, len(pdfs), path.name, result.warning)
        else:
            ok += 1
            logger.info(
                "[%d/%d] %s → %d chunks (%d chars)",
                i, len(pdfs), path.name, result.n_chunks, result.n_chars,
            )

    total_chunks = get_collection().count()
    logger.info(
        "listo en %.1f min: %d docs indexados, %d omitidos, %d chunks totales",
        (time.time() - t0) / 60, ok, len(skipped), total_chunks,
    )
    for name, reason in skipped:
        logger.info("  omitido: %s (%s)", name, reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
