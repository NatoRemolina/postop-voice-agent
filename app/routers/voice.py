import logging

import httpx
from fastapi import APIRouter, HTTPException

from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

ELEVENLABS_SIGNED_URL = (
    "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"
)


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
