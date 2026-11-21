"""Telegram Bot API adapter: normalizes updates and forwards them to the agent."""

import logging
from collections import deque
from typing import Any
from uuid import UUID

import httpx

from app import agent_service, db
from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE = 4096
MAX_MEDIA_BYTES = 15 * 1024 * 1024
_recent_updates: deque[int] = deque(maxlen=500)

WELCOME = (
    "Olá! Eu sou o Otto, seu assistente financeiro. 💰\n"
    "Envie gastos por texto, áudio ou foto de comprovante "
    "(ex.: \"Gastei 50 reais no mercado no débito\") e pergunte sobre saldo e gastos.\n\n"
    "Já usa o EmCache pelo site? Vincule esta conversa à sua conta com:\n"
    "/vincular SEU_ID_DE_USUARIO"
)


class TelegramNotConfiguredError(RuntimeError):
    pass


def _api_url(method: str) -> str:
    if not settings.telegram_bot_token:
        raise TelegramNotConfiguredError("TELEGRAM_BOT_TOKEN não configurado")
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


async def send_message(chat_id: int | str, text: str) -> None:
    text = text[:TELEGRAM_MAX_MESSAGE] or "…"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(_api_url("sendMessage"), json={"chat_id": chat_id, "text": text})
    if resp.status_code >= 400:
        logger.error("Telegram sendMessage falhou (%s): %s", resp.status_code, resp.text[:200])


async def send_typing(chat_id: int | str) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                _api_url("sendChatAction"), json={"chat_id": chat_id, "action": "typing"}
            )
    except Exception:
        logger.debug("sendChatAction falhou", exc_info=True)


async def download_file(file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=60) as client:
        info = await client.get(_api_url("getFile"), params={"file_id": file_id})
        info.raise_for_status()
        file_path = info.json()["result"]["file_path"]
        resp = await client.get(
            f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{file_path}"
        )
        resp.raise_for_status()
    if len(resp.content) > MAX_MEDIA_BYTES:
        raise ValueError("Arquivo muito grande")
    return resp.content


def _extract_media_ref(msg: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """Return (kind, file_id, mime_type, filename) for supported attachments."""
    if msg.get("voice"):
        v = msg["voice"]
        return "audio", v["file_id"], v.get("mime_type", "audio/ogg"), "voice.ogg"
    if msg.get("audio"):
        a = msg["audio"]
        return "audio", a["file_id"], a.get("mime_type", "audio/mpeg"), a.get("file_name", "audio.mp3")
    if msg.get("photo"):
        return "image", msg["photo"][-1]["file_id"], "image/jpeg", "photo.jpg"
    doc = msg.get("document")
    if doc and str(doc.get("mime_type", "")).startswith("image/"):
        return "image", doc["file_id"], doc["mime_type"], doc.get("file_name", "image")
    return None


async def _handle_command(chat_id: int, text: str, msg: dict[str, Any]) -> bool:
    """Handle /start and /vincular. Returns True if the message was a command."""
    command, _, arg = text.partition(" ")
    command = command.split("@")[0].lower()
    if command == "/start":
        await send_message(chat_id, WELCOME)
        return True
    if command == "/vincular":
        try:
            user_id = UUID(arg.strip())
        except ValueError:
            await send_message(chat_id, "Uso: /vincular SEU_ID_DE_USUARIO (UUID).")
            return True
        if not await db.get_user(user_id):
            await send_message(chat_id, "Usuário não encontrado. Confira o ID e tente de novo.")
            return True
        await db.link_user_identity(
            user_id=user_id,
            channel="telegram",
            external_id=str(chat_id),
            username=(msg.get("from") or {}).get("username"),
        )
        await send_message(chat_id, "Conta vinculada! Agora é só conversar comigo. ✅")
        return True
    return False


async def process_update(update: dict[str, Any]) -> None:
    """Process one Telegram update end-to-end. Never raises (runs in background)."""
    update_id = update.get("update_id")
    if update_id is not None:
        if update_id in _recent_updates:
            return
        _recent_updates.append(update_id)

    msg = update.get("message")
    if not msg:
        return
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat.get("type") != "private":
        return

    try:
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text.startswith("/") and await _handle_command(chat_id, text, msg):
            return

        media_ref = _extract_media_ref(msg)
        if not text and not media_ref:
            await send_message(chat_id, "Envie texto, áudio ou foto de comprovante, por favor.")
            return

        sender = msg.get("from") or {}
        user = await db.get_or_create_user_by_identity(
            channel="telegram",
            external_id=str(chat_id),
            name=sender.get("first_name"),
            username=sender.get("username"),
        )
        await send_typing(chat_id)

        media = None
        if media_ref:
            kind, file_id, mime, filename = media_ref
            try:
                data = await download_file(file_id)
            except Exception:
                logger.exception("Falha ao baixar mídia do Telegram")
                await send_message(chat_id, "Não consegui baixar o arquivo. Tente novamente.")
                return
            media = agent_service.Media(kind, data, mime, filename)

        response = await agent_service.handle_message(
            user_id=user["id"],
            channel="telegram",
            message=text if not media or msg.get("caption") else None,
            media=media,
        )
        await send_message(chat_id, response.reply)
    except Exception:
        logger.exception("Erro ao processar update do Telegram")
        try:
            await send_message(chat_id, "Desculpe, tive um problema. Tente novamente em instantes.")
        except Exception:
            pass
