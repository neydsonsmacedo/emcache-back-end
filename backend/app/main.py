import logging
import time
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from uuid import UUID

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app import (
    db,
    financial_service,
    llm_client,
    llm_registry,
    metrics_service,
    reconcile_service,
    sync_service,
)
from app.chat_api import router as chat_router
from app.llm_metrics import LLMMetricsCallbackHandler, MetricsRecorder
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

app.include_router(chat_router)


class ConnectTokenRequest(BaseModel):
    itemId: str | None = None
    clientUserId: str | None = None


class RegisterItemRequest(BaseModel):
    itemId: str


class ManualTransactionCreateRequest(BaseModel):
    user_id: UUID | None = None
    data: date | None = None
    valor_cents: int | None = Field(default=None, ge=0)
    type: str = Field(default="DEBIT", pattern="^(DEBIT|CREDIT)$")
    categoria: str | None = None
    tipo_transacao: str | None = None
    origem: str | None = None
    institution_id: UUID | None = None
    pluggy_account_id: str | None = None
    resumo_item: str | None = None
    canal_entrada: str = "api"
    raw_input: str | None = None


class ManualTransactionUpdateRequest(BaseModel):
    data: date | None = None
    valor_cents: int | None = Field(default=None, ge=0)
    type: str | None = Field(default=None, pattern="^(DEBIT|CREDIT)$")
    categoria: str | None = None
    tipo_transacao: str | None = None
    origem: str | None = None
    institution_id: UUID | None = None
    pluggy_account_id: str | None = None
    resumo_item: str | None = None


class ReconcileRequest(BaseModel):
    pluggy_transaction_id: str
    user_id: UUID | None = None


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


# ============================================================================
# Analytics & Financial Services (Fase 2)
# ============================================================================


@app.get("/api/analytics/summary")
async def get_analytics_summary(
    user_id: UUID | None = None,
    period: str | None = Query(default=None, pattern="^(day|week|month|custom)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    institution_id: UUID | None = None,
) -> dict[str, Any]:
    try:
        return await financial_service.get_analytics_summary(
            user_id=user_id,
            period=period,
            date_from=date_from,
            date_to=date_to,
            institution_id=institution_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/analytics/by-category")
async def get_analytics_by_category(
    user_id: UUID | None = None,
    type: str | None = Query(default="DEBIT", pattern="^(DEBIT|CREDIT)$"),
    period: str | None = Query(default=None, pattern="^(day|week|month|custom)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    institution_id: UUID | None = None,
) -> dict[str, Any]:
    try:
        rows = await financial_service.get_spending_by_category(
            user_id=user_id,
            entry_type=type,
            period=period,
            date_from=date_from,
            date_to=date_to,
            institution_id=institution_id,
        )
        return {"results": rows}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/analytics/by-institution")
async def get_analytics_by_institution(
    user_id: UUID | None = None,
    period: str | None = Query(default=None, pattern="^(day|week|month|custom)$"),
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    try:
        rows = await financial_service.get_spending_by_institution(
            user_id=user_id,
            period=period,
            date_from=date_from,
            date_to=date_to,
        )
        return {"results": rows}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/analytics/cashflow")
async def get_analytics_cashflow(
    user_id: UUID | None = None,
    granularity: str = Query(default="day", pattern="^(day|week|month)$"),
    period: str | None = Query(default=None, pattern="^(day|week|month|custom)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    institution_id: UUID | None = None,
) -> dict[str, Any]:
    try:
        rows = await financial_service.get_cashflow(
            user_id=user_id,
            granularity=granularity,
            period=period,
            date_from=date_from,
            date_to=date_to,
            institution_id=institution_id,
        )
        return {"results": rows}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ============================================================================
# Manual Transactions CRUD (Fase 2)
# ============================================================================


@app.get("/api/manual-transactions")
async def list_manual_transactions(
    user_id: UUID | None = None,
    status: str | None = Query(default=None, pattern="^(completo|pendente)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = await financial_service.list_manual_transactions(
        user_id=user_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"results": rows}


@app.post("/api/manual-transactions", status_code=201)
async def create_manual_transaction(
    body: ManualTransactionCreateRequest,
) -> dict[str, Any]:
    try:
        return await financial_service.create_manual_transaction(
            user_id=body.user_id,
            data=body.data,
            valor_cents=body.valor_cents,
            entry_type=body.type,
            categoria=body.categoria,
            tipo_transacao=body.tipo_transacao,
            origem=body.origem,
            institution_id=body.institution_id,
            pluggy_account_id=body.pluggy_account_id,
            resumo_item=body.resumo_item,
            canal_entrada=body.canal_entrada,
            raw_input=body.raw_input,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/manual-transactions/{transaction_id}")
async def get_manual_transaction(
    transaction_id: UUID,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    row = await financial_service.get_manual_transaction(
        transaction_id,
        user_id=user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Transação manual não encontrada")
    return row


@app.patch("/api/manual-transactions/{transaction_id}")
async def update_manual_transaction(
    transaction_id: UUID,
    body: ManualTransactionUpdateRequest,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    kwargs: dict[str, Any] = {}
    field_map = {
        "data": "data",
        "valor_cents": "valor_cents",
        "type": "entry_type",
        "categoria": "categoria",
        "tipo_transacao": "tipo_transacao",
        "origem": "origem",
        "institution_id": "institution_id",
        "pluggy_account_id": "pluggy_account_id",
        "resumo_item": "resumo_item",
    }
    for key, val in fields.items():
        kwargs[field_map[key]] = val

    try:
        row = await financial_service.update_manual_transaction(
            transaction_id,
            user_id=user_id,
            **kwargs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not row:
        raise HTTPException(status_code=404, detail="Transação manual não encontrada")
    return row


@app.delete("/api/manual-transactions/{transaction_id}")
async def delete_manual_transaction(
    transaction_id: UUID,
    user_id: UUID | None = None,
) -> dict[str, bool]:
    deleted = await financial_service.delete_manual_transaction(
        transaction_id,
        user_id=user_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Transação manual não encontrada")
    return {"deleted": True}


# ============================================================================
# Reconciliation (Fase 2)
# ============================================================================


@app.get("/api/reconciliation/suggestions")
async def get_reconciliation_suggestions(
    user_id: UUID,
    manual_transaction_id: UUID | None = None,
    date_window_days: int = Query(default=3, ge=0, le=30),
) -> dict[str, Any]:
    results = await reconcile_service.find_reconciliation_candidates(
        user_id,
        manual_transaction_id=manual_transaction_id,
        date_window_days=date_window_days,
    )
    return {"results": results}


@app.post("/api/reconciliation/auto")
async def run_auto_reconciliation(
    user_id: UUID,
    date_window_days: int = Query(default=3, ge=0, le=30),
    min_score: float = Query(default=0.75, ge=0.5, le=1.0),
) -> dict[str, Any]:
    return await reconcile_service.auto_reconcile_user(
        user_id,
        date_window_days=date_window_days,
        min_score=min_score,
    )


@app.post("/api/manual-transactions/{transaction_id}/reconcile")
async def reconcile_manual_transaction(
    transaction_id: UUID,
    body: ReconcileRequest,
) -> dict[str, Any]:
    try:
        return await reconcile_service.reconcile_manual_transaction(
            transaction_id,
            body.pluggy_transaction_id,
            user_id=body.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/manual-transactions/{transaction_id}/reconcile")
async def unreconcile_manual_transaction(
    transaction_id: UUID,
    user_id: UUID | None = None,
) -> dict[str, Any]:
    row = await reconcile_service.unreconcile_manual_transaction(
        transaction_id,
        user_id=user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Transação manual não encontrada")
    return row


# ---------------------------------------------------------------------------
# LLM provider configs (fallback chain) and usage metrics
# ---------------------------------------------------------------------------


class LLMConfigCreateRequest(BaseModel):
    user_id: UUID
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=30)
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    chat_model: str = Field(min_length=1)
    vision_model: str | None = None
    transcription_model: str | None = None
    priority: int = Field(default=1, ge=1, le=100)
    is_active: bool = True


class LLMConfigUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    chat_model: str | None = None
    vision_model: str | None = None
    transcription_model: str | None = None
    priority: int | None = Field(default=None, ge=1, le=100)
    is_active: bool | None = None


@app.get("/api/llm/configs")
async def list_llm_configs(user_id: UUID) -> dict[str, Any]:
    return {"results": await llm_registry.list_configs(user_id)}


@app.post("/api/llm/configs", status_code=201)
async def create_llm_config(payload: LLMConfigCreateRequest) -> dict[str, Any]:
    try:
        return await llm_registry.create_config(**payload.model_dump())
    except llm_registry.LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/llm/configs/{config_id}")
async def update_llm_config(
    config_id: UUID, user_id: UUID, payload: LLMConfigUpdateRequest
) -> dict[str, Any]:
    try:
        row = await llm_registry.update_config(
            config_id, user_id, payload.model_dump(exclude_unset=True)
        )
    except llm_registry.LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not row:
        raise HTTPException(status_code=404, detail="Configuração não encontrada")
    return row


@app.delete("/api/llm/configs/{config_id}")
async def delete_llm_config(config_id: UUID, user_id: UUID) -> dict[str, bool]:
    if not await llm_registry.delete_config(config_id, user_id):
        raise HTTPException(status_code=404, detail="Configuração não encontrada")
    return {"deleted": True}


@app.post("/api/llm/configs/{config_id}/test")
async def test_llm_config(config_id: UUID, user_id: UUID) -> dict[str, Any]:
    try:
        target = await llm_registry.get_target_by_config(config_id, user_id)
    except llm_registry.LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not target:
        raise HTTPException(status_code=404, detail="Configuração não encontrada")

    recorder = MetricsRecorder(user_id=user_id, channel="config_test", persist_run=False)
    started = time.perf_counter()
    try:
        model = llm_client.build_chat_model(target, "chat")
        await model.ainvoke(
            "Responda apenas: ok",
            config={"callbacks": [LLMMetricsCallbackHandler(recorder)]},
        )
        ok, error = True, None
    except Exception as exc:
        ok, error = False, str(exc)[:300]
    await recorder.flush()
    return {
        "ok": ok,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "error": error,
    }


@app.get("/api/metrics/llm/summary")
async def llm_metrics_summary(
    user_id: UUID | None = None, date_from: date | None = None, date_to: date | None = None
) -> dict[str, Any]:
    try:
        return await metrics_service.get_summary(user_id, date_from, date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/metrics/llm/daily")
async def llm_metrics_daily(
    user_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    granularity: str = Query(default="day", pattern="^(day|week|month)$"),
) -> dict[str, Any]:
    try:
        rows = await metrics_service.get_daily(user_id, date_from, date_to, granularity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"results": rows}


@app.get("/api/metrics/llm/by-model")
async def llm_metrics_by_model(
    user_id: UUID | None = None, date_from: date | None = None, date_to: date | None = None
) -> dict[str, Any]:
    return {"results": await metrics_service.get_by_model(user_id, date_from, date_to)}


@app.get("/api/metrics/llm/by-user")
async def llm_metrics_by_user(
    date_from: date | None = None, date_to: date | None = None
) -> dict[str, Any]:
    return {"results": await metrics_service.get_by_user(date_from, date_to)}


@app.get("/api/metrics/llm/tools")
async def llm_metrics_tools(
    user_id: UUID | None = None, date_from: date | None = None, date_to: date | None = None
) -> dict[str, Any]:
    return {"results": await metrics_service.get_tools_usage(user_id, date_from, date_to)}
