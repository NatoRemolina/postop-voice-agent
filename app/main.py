import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import calls, chat, documents, metrics, voice, web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
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
