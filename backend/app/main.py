import logging
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel

from app import db, sync_service
from app.pluggy_client import PluggyNotConfiguredError, pluggy_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("emcache.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.pool.open(wait=True)
    await db.init_pluggy_schema()
    yield
    await pluggy_client.close()
    await db.pool.close()


app = FastAPI(
    title="emcache API",
    description="Backend emcache com integração Pluggy Open Finance",
    version="0.1.0",
    lifespan=lifespan,
)


class ConnectTokenRequest(BaseModel):
    itemId: str | None = None
    clientUserId: str | None = None


class RegisterItemRequest(BaseModel):
    itemId: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/pluggy/connect-token")
async def create_connect_token(
    body: ConnectTokenRequest | None = None,
) -> dict[str, Any]:
    try:
        req = body or ConnectTokenRequest()
        data = await pluggy_client.create_connect_token(
            item_id=req.itemId,
            client_user_id=req.clientUserId,
        )
        return {"accessToken": data["accessToken"]}
    except PluggyNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Erro da Pluggy: {exc.response.text}",
        ) from exc


@app.post("/api/pluggy/items")
async def register_item(
    body: RegisterItemRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Register a newly connected itemId and start syncing in the background."""
    try:
        item, accounts = await sync_service.sync_item_and_accounts(body.itemId)
        background_tasks.add_task(sync_service.sync_full_item, body.itemId)
        return {
            "item_id": body.itemId,
            "status": item.get("status"),
            "connector": (item.get("connector") or {}).get("name"),
            "accounts_found": len(accounts),
            "sync_started": True,
        }
    except PluggyNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Erro da Pluggy: {exc.response.text}",
        ) from exc


@app.post("/api/pluggy/items/{item_id}/sync")
async def sync_item(
    item_id: str,
    date_from: str | None = Query(
        default=None,
        description="Data inicial YYYY-MM-DD (opcional)",
    ),
) -> dict[str, Any]:
    """Manually trigger full sync of an item (item, accounts, transactions)."""
    try:
        return await sync_service.sync_full_item(item_id, date_from=date_from)
    except PluggyNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Erro da Pluggy: {exc.response.text}",
        ) from exc


@app.post("/api/pluggy/items/{item_id}/refresh")
async def refresh_item(
    item_id: str,
    wait: bool = Query(
        default=False,
        description="Aguardar Pluggy concluir a coleta no banco e sincronizar no DB",
    ),
) -> dict[str, Any]:
    """Trigger Pluggy to re-fetch fresh data from the financial institution."""
    try:
        return await sync_service.refresh_item(item_id, wait=wait)
    except PluggyNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Erro da Pluggy: {exc.response.text}",
        ) from exc


@app.get("/api/institutions")
async def get_institutions() -> dict[str, Any]:
    institutions = await db.list_institutions()
    return {"results": institutions}


@app.get("/api/transactions")
async def get_unified_transactions(
    user_id: UUID | None = None,
    institution_id: UUID | None = None,
    source: str | None = Query(default=None, pattern="^(pluggy|manual)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    transactions = await db.list_unified_transactions(
        user_id=user_id,
        institution_id=institution_id,
        source=source,
        limit=limit,
        offset=offset,
    )
    return {"results": transactions}


@app.get("/api/pluggy/items")
async def get_items(user_id: UUID | None = None) -> dict[str, Any]:
    items = await db.list_items(user_id=user_id)
    return {"results": items}


@app.get("/api/pluggy/accounts")
async def get_accounts(
    item_id: str | None = None,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    accounts = await db.list_accounts(item_id=item_id, user_id=user_id)
    return {"results": accounts}


@app.get("/api/pluggy/transactions")
async def get_transactions(
    account_id: str | None = None,
    item_id: str | None = None,
    user_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    transactions = await db.list_transactions(
        account_id=account_id,
        item_id=item_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    return {"results": transactions}


@app.post("/api/pluggy/webhook")
async def pluggy_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> dict[str, bool]:
    """Respond quickly (<5s) and process Pluggy webhook in the background."""
    background_tasks.add_task(sync_service.handle_webhook_event, payload)
    return {"received": True}
