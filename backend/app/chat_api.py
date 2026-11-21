"""Channel adapters: web chat (/api/chat) and Telegram webhook."""

import hmac
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, Request, UploadFile

from app import agent_service, db, telegram_service
from app.config import settings

logger = logging.getLogger("emcache.chat")
router = APIRouter()

MAX_UPLOAD_BYTES = 15 * 1024 * 1024


async def _read_media(file: UploadFile) -> agent_service.Media:
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime.startswith("audio/") or mime in {"video/ogg", "application/ogg"}:
        kind = "audio"
    elif mime.startswith("image/"):
        kind = "image"
    else:
        raise HTTPException(status_code=415, detail="Envie um arquivo de áudio ou imagem")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Arquivo muito grande (máx. 15 MB)")
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    return agent_service.Media(kind, data, mime, file.filename)


@router.post("/api/chat")
async def chat(
    user_id: UUID = Form(...),
    message: str | None = Form(default=None),
    session_id: str | None = Form(default=None, description="ID externo opcional da conversa"),
    file: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    """Send text and/or an audio/image attachment to the agent (web channel)."""
    if not await db.get_user(user_id):
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    media = await _read_media(file) if file is not None and file.filename else None
    if not (message and message.strip()) and media is None:
        raise HTTPException(status_code=400, detail="Informe message ou file")

    response = await agent_service.handle_message(
        user_id=user_id,
        channel="web",
        message=message,
        media=media,
        external_session_id=session_id,
    )
    return response.to_dict()


@router.get("/api/chat/history")
async def chat_history(
    user_id: UUID,
    session_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Restore the web conversation (most recent messages, chronological)."""
    if not await db.get_user(user_id):
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    session = await db.get_or_create_chat_session(
        user_id=user_id, channel="web", external_session_id=session_id
    )
    messages = await db.list_chat_messages(session["id"], limit=limit)
    return {
        "session_id": str(session["id"]),
        "pending_transaction_id": session["pending_transaction_id"],
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "input_type": m["input_type"],
                "metadata": m["metadata"],
                "created_at": m["created_at"],
            }
            for m in messages
        ],
    }


@router.post("/api/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    """Receive Bot API updates; processing happens in background to ack quickly."""
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="Telegram não configurado")
    secret = settings.telegram_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="TELEGRAM_WEBHOOK_SECRET não configurado")
    if not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        raise HTTPException(status_code=401, detail="Secret inválido")

    try:
        update = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc
    background.add_task(telegram_service.process_update, update)
    return {"ok": True}
