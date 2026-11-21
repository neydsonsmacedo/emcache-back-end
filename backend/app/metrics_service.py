"""Read-side queries for LLM usage indicators."""

from datetime import date
from typing import Any
from uuid import UUID

from app import db
from app.sql_loader import load_sql

VALID_GRANULARITIES = {"day", "week", "month"}


def _params(
    user_id: UUID | str | None, date_from: date | None, date_to: date | None
) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from não pode ser maior que date_to")
    return {
        "user_id": str(user_id) if user_id else None,
        "date_from": date_from,
        "date_to": date_to,
    }


async def _fetch(sql: str, params: dict[str, Any], *, one: bool = False) -> Any:
    async with db.pool.connection() as conn:
        cur = await conn.execute(load_sql(sql), params)
        return (await cur.fetchone() or {}) if one else await cur.fetchall()


def _rate(part: Any, total: Any) -> float:
    return round(int(part or 0) / int(total), 4) if total else 0.0


async def get_summary(
    user_id: UUID | str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    params = _params(user_id, date_from, date_to)
    calls = await _fetch("llm_metrics/summary_calls.sql", params, one=True)
    runs = await _fetch("llm_metrics/summary_runs.sql", params, one=True)
    return {
        **calls,
        **runs,
        "call_error_rate": _rate(calls.get("error_calls"), calls.get("llm_calls")),
        "call_fallback_rate": _rate(calls.get("fallback_calls"), calls.get("llm_calls")),
        "run_failure_rate": _rate(runs.get("failed_runs"), runs.get("runs")),
        "run_fallback_rate": _rate(runs.get("fallback_runs"), runs.get("runs")),
    }


async def get_daily(
    user_id: UUID | str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    granularity: str = "day",
) -> list[dict[str, Any]]:
    if granularity not in VALID_GRANULARITIES:
        raise ValueError(f"Granularidade inválida: {granularity}")
    params = {**_params(user_id, date_from, date_to), "granularity": granularity}
    return await _fetch("llm_metrics/daily.sql", params)


async def get_by_model(
    user_id: UUID | str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    return await _fetch("llm_metrics/by_model.sql", _params(user_id, date_from, date_to))


async def get_by_user(
    date_from: date | None = None, date_to: date | None = None
) -> list[dict[str, Any]]:
    params = _params(None, date_from, date_to)
    params.pop("user_id")
    return await _fetch("llm_metrics/by_user.sql", params)


async def get_tools_usage(
    user_id: UUID | str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    return await _fetch("llm_metrics/tools.sql", _params(user_id, date_from, date_to))


async def prune_calls(older_than_days: int) -> int:
    if older_than_days < 1:
        raise ValueError("older_than_days deve ser >= 1")
    async with db.pool.connection() as conn:
        res = await conn.execute(load_sql("llm_metrics/prune.sql"), {"days": older_than_days})
        return res.rowcount or 0
