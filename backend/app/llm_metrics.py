"""Per-run LLM usage tracking persisted to PostgreSQL (agent_runs + llm_calls)."""

import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from langchain_core.callbacks import AsyncCallbackHandler
from psycopg.types.json import Jsonb

from app import db
from app.sql_loader import load_sql

logger = logging.getLogger(__name__)

ERROR_MESSAGE_MAX = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truncate(text: str | None) -> str | None:
    return text[:ERROR_MESSAGE_MAX] if text else None


def _error_info(exc: BaseException) -> dict[str, Any]:
    return {
        "error_type": type(exc).__name__,
        "error_message": _truncate(str(exc)),
        "http_status": getattr(exc, "status_code", None),
    }


def estimate_cost(call: dict[str, Any], pricing: dict[tuple[str, str], dict]) -> float | None:
    price = pricing.get((call.get("provider"), call.get("model")))
    if not price:
        return None
    cost = 0.0
    priced = False
    prompt, completion = call.get("prompt_tokens"), call.get("completion_tokens")
    cached = call.get("cached_tokens") or 0
    if prompt and price["input_per_million_usd"] is not None:
        cached_price = price["cached_input_per_million_usd"]
        if cached_price is None:
            cached_price = price["input_per_million_usd"]
        cost += (prompt - cached) * price["input_per_million_usd"] / 1e6
        cost += cached * cached_price / 1e6
        priced = True
    if completion and price["output_per_million_usd"] is not None:
        cost += completion * price["output_per_million_usd"] / 1e6
        priced = True
    seconds = call.get("audio_seconds")
    if seconds and price["audio_per_minute_usd"] is not None:
        cost += float(seconds) / 60 * price["audio_per_minute_usd"]
        priced = True
    return round(cost, 6) if priced else None


class MetricsRecorder:
    """Accumulates calls for one agent run and persists them in a single transaction."""

    def __init__(self, *, user_id: UUID | str | None, channel: str, persist_run: bool = True):
        self.run_id = uuid4()
        self.user_id = str(user_id) if user_id else None
        self.channel = channel
        self.persist_run = persist_run
        self.session_id: str | None = None
        self.input_type = "text"
        self.status = "success"
        self.transaction_id: str | None = None
        self.error_type: str | None = None
        self.error_message: str | None = None
        self.calls: list[dict[str, Any]] = []
        self.tools_used: list[str] = []
        self.started_at = _now()
        self._t0 = time.perf_counter()

    def set_error(self, exc: BaseException) -> None:
        info = _error_info(exc)
        self.error_type, self.error_message = info["error_type"], info["error_message"]

    def record_call(self, **fields: Any) -> None:
        call = {
            "operation": "chat", "provider": None, "model": None, "llm_config_id": None,
            "attempt": 1, "is_fallback": False, "status": "success", "http_status": None,
            "error_type": None, "error_message": None, "latency_ms": 0,
            "time_to_first_token_ms": None, "prompt_tokens": None,
            "completion_tokens": None, "total_tokens": None, "cached_tokens": None,
            "audio_seconds": None, "finish_reason": None, "tool_calls_requested": [],
            "metadata": None, "created_at": _now(),
        }
        call.update(fields)
        self.calls.append(call)

    def record_tool(self, name: str | None) -> None:
        if name:
            self.tools_used.append(name)

    async def flush(self) -> None:
        """Persist metrics. Never raises: metrics must not break the user response."""
        try:
            await self._flush()
        except Exception:
            logger.warning("Falha ao gravar métricas de LLM", exc_info=True)

    async def _flush(self) -> None:
        if not self.calls and not self.persist_run:
            return
        async with db.pool.connection() as conn:
            cur = await conn.execute(load_sql("llm_metrics/get_pricing.sql"))
            pricing = {(r["provider"], r["model"]): r for r in await cur.fetchall()}
            for call in self.calls:
                call["estimated_cost_usd"] = estimate_cost(call, pricing)

            if self.persist_run:
                finished = _now()
                costs = [c["estimated_cost_usd"] for c in self.calls if c["estimated_cost_usd"] is not None]
                await conn.execute(
                    load_sql("llm_metrics/insert_run.sql"),
                    {
                        "id": str(self.run_id),
                        "user_id": self.user_id,
                        "session_id": self.session_id,
                        "channel": self.channel,
                        "input_type": self.input_type,
                        "status": self.status,
                        "started_at": self.started_at,
                        "finished_at": finished,
                        "latency_ms": int((time.perf_counter() - self._t0) * 1000),
                        "llm_calls": len(self.calls),
                        "tool_calls": len(self.tools_used),
                        "tools_used": self.tools_used,
                        "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in self.calls),
                        "completion_tokens": sum(c["completion_tokens"] or 0 for c in self.calls),
                        "total_tokens": sum(c["total_tokens"] or 0 for c in self.calls),
                        "estimated_cost_usd": round(sum(costs), 6) if costs else None,
                        "fallback_used": any(c["is_fallback"] for c in self.calls),
                        "transaction_id": self.transaction_id,
                        "error_type": self.error_type,
                        "error_message": self.error_message,
                    },
                )
            for call in self.calls:
                await conn.execute(
                    load_sql("llm_metrics/insert_call.sql"),
                    {
                        **call,
                        "run_id": str(self.run_id) if self.persist_run else None,
                        "user_id": self.user_id,
                        "llm_config_id": str(call["llm_config_id"]) if call["llm_config_id"] else None,
                        "metadata": Jsonb(call["metadata"]) if call["metadata"] else None,
                    },
                )


class LLMMetricsCallbackHandler(AsyncCallbackHandler):
    """LangChain callback that feeds a MetricsRecorder (chat model calls and tools)."""

    def __init__(self, recorder: MetricsRecorder):
        self.recorder = recorder
        self._pending: dict[UUID, dict[str, Any]] = {}

    async def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, tags=None, metadata=None, **kwargs
    ) -> None:
        self._pending[run_id] = {
            "t0": time.perf_counter(),
            "created_at": _now(),
            "meta": metadata or {},
        }

    def _base(self, run_id: UUID) -> dict[str, Any]:
        info = self._pending.pop(run_id, None) or {
            "t0": time.perf_counter(), "created_at": _now(), "meta": {},
        }
        meta = info["meta"]
        index = int(meta.get("target_index", 0))
        return {
            "operation": meta.get("operation", "chat"),
            "provider": meta.get("provider"),
            "model": meta.get("model"),
            "llm_config_id": meta.get("config_id"),
            "attempt": index + 1,
            "is_fallback": index > 0,
            "latency_ms": int((time.perf_counter() - info["t0"]) * 1000),
            "created_at": info["created_at"],
        }

    async def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        call = self._base(run_id)
        message = None
        try:
            message = response.generations[0][0].message
        except (AttributeError, IndexError):
            pass

        usage = dict(getattr(message, "usage_metadata", None) or {})
        if not usage and response.llm_output:
            raw = response.llm_output.get("token_usage") or {}
            usage = {
                "input_tokens": raw.get("prompt_tokens"),
                "output_tokens": raw.get("completion_tokens"),
                "total_tokens": raw.get("total_tokens"),
            }
        details = usage.get("input_token_details") or {}
        resp_meta = getattr(message, "response_metadata", None) or {}
        call.update(
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            total_tokens=usage.get("total_tokens"),
            cached_tokens=details.get("cache_read"),
            finish_reason=resp_meta.get("finish_reason"),
            tool_calls_requested=[
                tc.get("name") for tc in (getattr(message, "tool_calls", None) or []) if tc.get("name")
            ],
            metadata={"request_id": resp_meta.get("id")} if resp_meta.get("id") else None,
        )
        self.recorder.record_call(**call)

    async def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        call = self._base(run_id)
        call.update(status="error", **_error_info(error))
        self.recorder.record_call(**call)

    async def on_tool_start(self, serialized, input_str, *, run_id, **kwargs) -> None:
        name = kwargs.get("name") or (serialized or {}).get("name")
        self.recorder.record_tool(name)
