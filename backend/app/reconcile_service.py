import re
import unicodedata
from datetime import date
from typing import Any
from uuid import UUID

from app import db
from app.sql_loader import load_sql

DEFAULT_DATE_WINDOW_DAYS = 3
DEFAULT_AUTO_RECONCILE_MIN_SCORE = 0.75

_STOPWORDS = {"de", "da", "do", "das", "dos", "em", "no", "na", "para", "com", "e", "a", "o"}


def _tokenize(text: str | None) -> set[str]:
    if not text:
        return set()
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()
    tokens = {
        tok
        for tok in re.findall(r"[a-z0-9]+", ascii_text)
        if len(tok) >= 2 and tok not in _STOPWORDS
    }
    return tokens


def _description_similarity(manual_text: str | None, pluggy_desc: str | None, merchant: str | None) -> float:
    manual_tokens = _tokenize(manual_text)
    pluggy_tokens = _tokenize(pluggy_desc) | _tokenize(merchant)
    if not manual_tokens or not pluggy_tokens:
        return 0.0
    overlap = manual_tokens & pluggy_tokens
    if not overlap:
        return 0.0
    return len(overlap) / len(manual_tokens | pluggy_tokens)


def score_candidate(
    manual_tx: dict[str, Any],
    pluggy_tx: dict[str, Any],
    *,
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS,
) -> tuple[float, list[str]] | None:
    """Return (score, reasons) if pluggy_tx is a valid reconciliation candidate, else None."""
    if manual_tx.get("type") != pluggy_tx.get("type"):
        return None

    if manual_tx.get("valor") != pluggy_tx.get("amount_cents"):
        return None

    m_date: date | None = manual_tx.get("data")
    p_date: date | None = pluggy_tx.get("transaction_date")
    if m_date is None or p_date is None:
        return None

    date_diff = abs((p_date - m_date).days)
    if date_diff > date_window_days:
        return None

    m_acc = manual_tx.get("pluggy_account_id")
    if m_acc and m_acc != pluggy_tx.get("pluggy_account_id"):
        return None

    m_inst = manual_tx.get("institution_id")
    p_inst = pluggy_tx.get("institution_id")
    if m_inst and p_inst and str(m_inst) != str(p_inst):
        return None

    score = 0.50
    reasons = ["exact_amount", "same_type"]

    if date_diff == 0:
        score += 0.25
        reasons.append("same_date")
    elif date_diff == 1:
        score += 0.18
        reasons.append("date_diff_1d")
    elif date_diff == 2:
        score += 0.10
        reasons.append("date_diff_2d")
    else:
        score += 0.04
        reasons.append(f"date_diff_{date_diff}d")

    m_inst_name = (manual_tx.get("institution_name") or manual_tx.get("origem") or "").strip().lower()
    p_inst_name = (pluggy_tx.get("institution_name") or "").strip().lower()
    if m_inst and p_inst and str(m_inst) == str(p_inst):
        score += 0.15
        reasons.append("same_institution")
    elif m_inst_name and p_inst_name and (m_inst_name in p_inst_name or p_inst_name in m_inst_name):
        score += 0.12
        reasons.append("institution_name_match")

    sim = _description_similarity(
        manual_tx.get("resumo_item"),
        pluggy_tx.get("description"),
        pluggy_tx.get("merchant_name"),
    )
    if sim > 0:
        score += round(0.10 * sim, 4)
        reasons.append("description_similarity")

    return round(min(score, 1.0), 4), reasons


async def find_reconciliation_candidates(
    user_id: UUID | str,
    *,
    manual_transaction_id: UUID | str | None = None,
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Find and rank Pluggy transaction candidates for unmatched manual transactions."""
    params_manual = {
        "user_id": str(user_id),
        "manual_transaction_id": str(manual_transaction_id) if manual_transaction_id else None,
    }
    async with db.pool.connection() as conn:
        cur_m = await conn.execute(
            load_sql("reconciliation/list_unmatched_manual.sql"),
            params_manual,
        )
        manual_rows = await cur_m.fetchall()
        if not manual_rows:
            return []

        cur_p = await conn.execute(
            load_sql("reconciliation/list_candidate_pluggy.sql"),
            {"user_id": str(user_id)},
        )
        pluggy_rows = await cur_p.fetchall()

    suggestions: list[dict[str, Any]] = []
    for manual_tx in manual_rows:
        candidates: list[dict[str, Any]] = []
        for pluggy_tx in pluggy_rows:
            scored = score_candidate(
                manual_tx,
                pluggy_tx,
                date_window_days=date_window_days,
            )
            if scored is None:
                continue
            score, reasons = scored
            candidates.append(
                {
                    "score": score,
                    "reasons": reasons,
                    "pluggy_transaction": pluggy_tx,
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        if candidates:
            suggestions.append(
                {
                    "manual_transaction": manual_tx,
                    "candidates": candidates,
                }
            )
    return suggestions


async def reconcile_manual_transaction(
    manual_transaction_id: UUID | str,
    pluggy_transaction_id: str,
    *,
    user_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Explicitly link a manual transaction to a Pluggy transaction."""
    async with db.pool.connection() as conn:
        cur_m = await conn.execute(
            load_sql("manual_transactions/get_by_id.sql"),
            {
                "id": str(manual_transaction_id),
                "user_id": str(user_id) if user_id else None,
            },
        )
        manual_tx = await cur_m.fetchone()
        if not manual_tx:
            raise LookupError("Transação manual não encontrada")

        cur_p = await conn.execute(
            load_sql("reconciliation/get_pluggy_tx_for_user.sql"),
            {"pluggy_transaction_id": pluggy_transaction_id},
        )
        pluggy_tx = await cur_p.fetchone()
        if not pluggy_tx:
            raise LookupError("Transação Pluggy não encontrada")

        if user_id and str(pluggy_tx.get("user_id")) != str(user_id):
            raise PermissionError("Transação Pluggy pertence a outro usuário")

        if (
            manual_tx.get("user_id")
            and pluggy_tx.get("user_id")
            and str(manual_tx["user_id"]) != str(pluggy_tx["user_id"])
        ):
            raise PermissionError("Transações pertencem a usuários diferentes")

        already_matched = pluggy_tx.get("already_matched_manual_id")
        if already_matched and str(already_matched) != str(manual_transaction_id):
            raise ValueError(
                f"Transação Pluggy já conciliada com manual_transaction {already_matched}"
            )

        cur_u = await conn.execute(
            load_sql("manual_transactions/set_matched_pluggy_tx.sql"),
            {
                "id": str(manual_transaction_id),
                "user_id": str(user_id) if user_id else None,
                "pluggy_transaction_id": pluggy_transaction_id,
            },
        )
        updated = await cur_u.fetchone()
        assert updated is not None
        return updated


async def unreconcile_manual_transaction(
    manual_transaction_id: UUID | str,
    *,
    user_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    """Remove the Pluggy reconciliation link from a manual transaction."""
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            load_sql("manual_transactions/set_matched_pluggy_tx.sql"),
            {
                "id": str(manual_transaction_id),
                "user_id": str(user_id) if user_id else None,
                "pluggy_transaction_id": None,
            },
        )
        return await cur.fetchone()


async def auto_reconcile_user(
    user_id: UUID | str,
    *,
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS,
    min_score: float = DEFAULT_AUTO_RECONCILE_MIN_SCORE,
) -> dict[str, Any]:
    """Automatically link unambiguous high-confidence manual <-> Pluggy matches."""
    suggestions = await find_reconciliation_candidates(
        user_id,
        date_window_days=date_window_days,
    )

    proposed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    pluggy_claim_counts: dict[str, int] = {}

    for group in suggestions:
        candidates = group["candidates"]
        best = candidates[0]
        if best["score"] < min_score:
            continue
        if len(candidates) > 1 and (best["score"] - candidates[1]["score"]) < 0.10:
            continue

        pluggy_id = best["pluggy_transaction"]["pluggy_transaction_id"]
        pluggy_claim_counts[pluggy_id] = pluggy_claim_counts.get(pluggy_id, 0) + 1
        proposed.append((group["manual_transaction"], best))

    reconciled: list[dict[str, Any]] = []
    skipped_ambiguous = 0

    for manual_tx, best in proposed:
        pluggy_id = best["pluggy_transaction"]["pluggy_transaction_id"]
        if pluggy_claim_counts[pluggy_id] > 1:
            skipped_ambiguous += 1
            continue

        updated = await reconcile_manual_transaction(
            manual_tx["id"],
            pluggy_id,
            user_id=user_id,
        )
        reconciled.append(
            {
                "manual_transaction_id": str(updated["id"]),
                "pluggy_transaction_id": pluggy_id,
                "score": best["score"],
                "reasons": best["reasons"],
            }
        )

    return {
        "user_id": str(user_id),
        "reconciled_count": len(reconciled),
        "skipped_ambiguous": skipped_ambiguous,
        "reconciled": reconciled,
    }
