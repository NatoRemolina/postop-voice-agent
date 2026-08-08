import logging

from fastapi import FastAPI

from app.routers import chat, documents

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Agente postoperatorio — Tech Sphere Challenge 2026")

app.include_router(chat.router)
app.include_router(documents.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
