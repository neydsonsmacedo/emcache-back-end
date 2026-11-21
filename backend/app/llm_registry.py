"""LLM provider resolution (fallback chain), per-user configs and API-key encryption."""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from app import db
from app.config import settings
from app.sql_loader import load_sql

logger = logging.getLogger(__name__)

CAPABILITIES = ("chat", "vision", "transcription")


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMTarget:
    provider: str
    base_url: str
    api_key: str
    chat_model: str | None = None
    vision_model: str | None = None
    transcription_model: str | None = None
    config_id: UUID | None = None
    source: str = "env"  # 'user' | 'env' | 'env_fallback'

    def model_for(self, capability: str) -> str | None:
        if capability not in CAPABILITIES:
            raise ValueError(f"Capability inválida: {capability}")
        return getattr(self, f"{capability}_model")

    def identity(self, capability: str) -> tuple:
        return (self.base_url.rstrip("/"), self.model_for(capability), self.api_key)


# --------------------------------------------------------------------------- crypto


def _fernet() -> Fernet:
    key = settings.llm_keys_encryption_key
    if not key:
        raise LLMError("LLM_KEYS_ENCRYPTION_KEY não configurada")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise LLMError("LLM_KEYS_ENCRYPTION_KEY inválida (use Fernet.generate_key())") from exc


def encrypt_api_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode()).decode()


def decrypt_api_key(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise LLMError("Não foi possível descriptografar a chave da API") from exc


# --------------------------------------------------------------------------- chain


def _env_targets() -> list[LLMTarget]:
    targets: list[LLMTarget] = []
    if settings.elevenlabs_api_key and settings.elevenlabs_transcription_model:
        targets.append(
            LLMTarget(
                provider="elevenlabs",
                base_url=settings.elevenlabs_base_url,
                api_key=settings.elevenlabs_api_key,
                transcription_model=settings.elevenlabs_transcription_model,
                source="env",
            )
        )
    if settings.llm_api_key:
        targets.append(
            LLMTarget(
                provider=settings.llm_provider,
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                chat_model=settings.llm_model,
                vision_model=settings.vision_model,
                transcription_model=settings.transcription_model,
                source="env",
            )
        )
    if settings.llm_fallback_api_key:
        targets.append(
            LLMTarget(
                provider=settings.llm_fallback_provider,
                base_url=settings.llm_fallback_base_url,
                api_key=settings.llm_fallback_api_key,
                chat_model=settings.llm_fallback_model,
                vision_model=settings.llm_fallback_vision_model,
                transcription_model=settings.llm_fallback_transcription_model,
                source="env_fallback",
            )
        )
    return targets


def _row_to_target(row: dict[str, Any]) -> LLMTarget:
    return LLMTarget(
        provider=row["provider"],
        base_url=row["base_url"],
        api_key=decrypt_api_key(row["api_key_encrypted"]),
        chat_model=row["chat_model"],
        vision_model=row["vision_model"],
        transcription_model=row["transcription_model"],
        config_id=row["id"],
        source="user",
    )


async def _user_targets(user_id: UUID | str) -> list[LLMTarget]:
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            load_sql("llm_configs/list_by_user.sql"),
            {"user_id": str(user_id), "only_active": True},
        )
        rows = await cur.fetchall()
    targets: list[LLMTarget] = []
    for row in rows:
        try:
            targets.append(_row_to_target(row))
        except LLMError as exc:
            logger.warning("Config LLM %s ignorada: %s", row["id"], exc)
    return targets


async def resolve_llm_chain(
    user_id: UUID | str | None, capability: str = "chat"
) -> list[LLMTarget]:
    """Ordered targets (user configs → env primary → env fallback) supporting a capability."""
    candidates: list[LLMTarget] = []
    if user_id:
        try:
            candidates.extend(await _user_targets(user_id))
        except Exception:
            logger.exception("Falha ao carregar configs LLM do usuário")
    candidates.extend(_env_targets())

    chain: list[LLMTarget] = []
    seen: set[tuple] = set()
    for target in candidates:
        if not target.model_for(capability):
            continue
        ident = target.identity(capability)
        if ident in seen:
            continue
        seen.add(ident)
        chain.append(target)
    return chain


# --------------------------------------------------------------------------- CRUD


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k != "api_key_encrypted"}
    out["api_key_masked"] = f"••••{row['api_key_last4']}"
    return out


async def list_configs(user_id: UUID | str) -> list[dict[str, Any]]:
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            load_sql("llm_configs/list_by_user.sql"),
            {"user_id": str(user_id), "only_active": False},
        )
        return [_public(r) for r in await cur.fetchall()]


async def _get_row(config_id: UUID | str, user_id: UUID | str) -> dict[str, Any] | None:
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            load_sql("llm_configs/get_by_id.sql"),
            {"id": str(config_id), "user_id": str(user_id)},
        )
        return await cur.fetchone()


async def get_target_by_config(config_id: UUID | str, user_id: UUID | str) -> LLMTarget | None:
    row = await _get_row(config_id, user_id)
    return _row_to_target(row) if row else None


async def create_config(
    *,
    user_id: UUID | str,
    name: str,
    provider: str,
    base_url: str,
    api_key: str,
    chat_model: str,
    vision_model: str | None = None,
    transcription_model: str | None = None,
    priority: int = 1,
    is_active: bool = True,
) -> dict[str, Any]:
    if not api_key.strip():
        raise ValueError("api_key obrigatória")
    params = {
        "user_id": str(user_id),
        "name": name.strip(),
        "provider": provider.strip().lower(),
        "base_url": base_url.strip(),
        "api_key_encrypted": encrypt_api_key(api_key.strip()),
        "api_key_last4": api_key.strip()[-4:],
        "chat_model": chat_model.strip(),
        "vision_model": (vision_model or "").strip() or None,
        "transcription_model": (transcription_model or "").strip() or None,
        "priority": priority,
        "is_active": is_active,
    }
    async with db.pool.connection() as conn:
        cur = await conn.execute(load_sql("llm_configs/create.sql"), params)
        row = await cur.fetchone()
    assert row is not None
    return _public(row)


async def update_config(
    config_id: UUID | str, user_id: UUID | str, changes: dict[str, Any]
) -> dict[str, Any] | None:
    existing = await _get_row(config_id, user_id)
    if not existing:
        return None

    params = {
        "id": str(config_id),
        "user_id": str(user_id),
        **{
            k: existing[k]
            for k in (
                "name", "provider", "base_url", "api_key_encrypted", "api_key_last4",
                "chat_model", "vision_model", "transcription_model", "priority", "is_active",
            )
        },
    }
    new_key = changes.pop("api_key", None)
    if new_key:
        params["api_key_encrypted"] = encrypt_api_key(new_key.strip())
        params["api_key_last4"] = new_key.strip()[-4:]
    for key, value in changes.items():
        if key in params and key not in ("id", "user_id"):
            params[key] = value.strip() if isinstance(value, str) else value
    for key in ("vision_model", "transcription_model"):
        params[key] = params[key] or None

    async with db.pool.connection() as conn:
        cur = await conn.execute(load_sql("llm_configs/update.sql"), params)
        row = await cur.fetchone()
    return _public(row) if row else None


async def delete_config(config_id: UUID | str, user_id: UUID | str) -> bool:
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            load_sql("llm_configs/delete.sql"),
            {"id": str(config_id), "user_id": str(user_id)},
        )
        return await cur.fetchone() is not None
