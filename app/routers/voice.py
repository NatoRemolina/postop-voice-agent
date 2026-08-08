import logging

import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

ELEVENLABS_SIGNED_URL = (
    "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"
)
ELEVENLABS_VOICES = "https://api.elevenlabs.io/v1/voices"


@router.get("/api/voice/voices")
async def list_spanish_voices():
    """Voces en español de la cuenta, para el selector de acento de la llamada."""
    if not settings.elevenlabs_api_key:
        raise HTTPException(
            status_code=503, detail="Falta configurar ELEVENLABS_API_KEY en .env"
        )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                ELEVENLABS_VOICES,
                params={"show_legacy": "false"},
                headers={"xi-api-key": settings.elevenlabs_api_key},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo contactar a ElevenLabs: {exc}") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"ElevenLabs respondió {resp.status_code}")
    voices = []
    for v in resp.json().get("voices", []):
        labels = v.get("labels") or {}
        if labels.get("language") != "es":
            continue
        voices.append(
            {
                "voice_id": v["voice_id"],
                "name": v.get("name", ""),
                "gender": labels.get("gender", ""),
                "accent": labels.get("accent", "") or labels.get("description", ""),
            }
        )
    return {"voices": voices}


@router.get("/api/voice/signed-url")
async def get_signed_url():
    if not settings.elevenlabs_api_key or not settings.elevenlabs_agent_id:
        raise HTTPException(
            status_code=503,
            detail="Falta configurar ELEVENLABS_API_KEY / ELEVENLABS_AGENT_ID en .env",
        )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                ELEVENLABS_SIGNED_URL,
                params={"agent_id": settings.elevenlabs_agent_id},
                headers={"xi-api-key": settings.elevenlabs_api_key},
            )
    except httpx.HTTPError as exc:
        logger.error("Error contactando a ElevenLabs: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo contactar a ElevenLabs: {exc}",
        ) from exc
    if resp.status_code != 200:
        logger.error(
            "ElevenLabs respondió %s: %s", resp.status_code, resp.text[:500]
        )
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs respondió {resp.status_code}: {resp.text[:500]}",
        )
    signed_url = resp.json().get("signed_url")
    if not signed_url:
        raise HTTPException(
            status_code=502,
            detail="La respuesta de ElevenLabs no incluyó signed_url",
        )
    return {"signed_url": signed_url}
