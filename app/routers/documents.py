import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.rag.ingest import delete_document, ingest_pdf, list_documents

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents")


@router.get("")
async def get_documents():
    docs = await run_in_threadpool(list_documents)
    return {"documents": docs, "count": len(docs)}


@router.post("")
async def upload_document(file: UploadFile, scenario: str = "subido_en_vivo"):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos PDF")
    content = await file.read()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / file.filename
        path.write_bytes(content)
        result = await run_in_threadpool(ingest_pdf, path, scenario, True)
    if result.n_chunks == 0:
        raise HTTPException(
            status_code=422,
            detail=f"No se pudo extraer texto del PDF: {result.warning or 'vacío'}",
        )
    return {
        "status": "procesado y disponible",
        "doc_id": result.doc_id,
        "source": result.source_name,
        "scenario": result.scenario,
        "n_chunks": result.n_chunks,
    }


@router.delete("/{doc_id}")
async def remove_document(doc_id: str):
    n = await run_in_threadpool(delete_document, doc_id)
    if n == 0:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {"status": "eliminado", "doc_id": doc_id, "chunks_removed": n}
