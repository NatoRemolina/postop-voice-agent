import logging

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.agent.summary import (
    build_call_summary,
    latest_summaries,
    persist_summary,
    turns_for_conversation,
)
from app.storage import append_jsonl, read_jsonl

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/calls")
def get_calls():
    # una alerta por conversación (la más reciente), descendente por ts
    alerts: dict[str, dict] = {}
    for alert in read_jsonl("alerts.jsonl"):
        cid = alert.get("conversation_id")
        if cid:
            alerts[cid] = alert
    return {
        "calls": latest_summaries(),
        "alerts": sorted(alerts.values(), key=lambda a: a.get("ts", 0), reverse=True),
    }


@router.get("/calls/{conversation_id}")
async def get_call(conversation_id: str):
    turns = await run_in_threadpool(turns_for_conversation, conversation_id)
    if not turns:
        raise HTTPException(
            status_code=404, detail="No hay turnos registrados para esa conversación"
        )
    summaries = await run_in_threadpool(latest_summaries)
    summary = next(
        (s for s in summaries if s.get("conversation_id") == conversation_id), None
    )
    if summary is None:
        summary = await build_call_summary(conversation_id, turns=turns)
    return {"summary": summary, "turns": turns}


@router.post("/calls/{conversation_id}/summarize")
async def summarize_call(conversation_id: str):
    turns = await run_in_threadpool(turns_for_conversation, conversation_id)
    if not turns:
        raise HTTPException(
            status_code=404, detail="No hay turnos registrados para esa conversación"
        )
    summary = await build_call_summary(conversation_id, turns=turns)
    await run_in_threadpool(persist_summary, summary)
    return summary


@router.post("/webhooks/elevenlabs")
async def elevenlabs_webhook(request: Request):
    try:
        try:
            payload = await request.json()
        except Exception:
            body = await request.body()
            payload = {"raw": body.decode("utf-8", errors="replace")}
        await run_in_threadpool(append_jsonl, "webhooks.jsonl", {"payload": payload})

        conversation_id = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                cid = data.get("conversation_id")
                if isinstance(cid, str) and cid.strip():
                    conversation_id = cid.strip()

        if conversation_id:
            turns = await run_in_threadpool(turns_for_conversation, conversation_id)
            if turns:
                summary = await build_call_summary(conversation_id, turns=turns)
                await run_in_threadpool(persist_summary, summary)
            else:
                logger.info(
                    "webhook for %s without local turns; skipping summary",
                    conversation_id,
                )
    except Exception:
        logger.exception("elevenlabs webhook processing failed")
    return {"received": True}
