import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.sql_loader import load_sql

pool = AsyncConnectionPool(
    conninfo=settings.database_url,
    open=False,
    kwargs={"row_factory": dict_row},
)


def to_cents(value: float | int | str | None) -> int | None:
    """Convert a monetary amount to integer cents without float drift."""
    if value is None:
        return None
    dec = Decimal(str(value))
    return int((dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _slugify(value: str) -> str:
    """Normalize an institution name into an ASCII URL-safe slug."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug or "institution"


async def _upsert_institution_from_connector(
    conn: Any,
    connector_id: int | None,
    connector_name: str | None,
) -> UUID | None:
    """Resolve or create an institution row for a Pluggy connector."""
    if connector_id is None and not connector_name:
        return None

    name = (connector_name or f"Connector {connector_id}").strip()
    base_slug = _slugify(name)

    if connector_id is not None:
        cur = await conn.execute(
            load_sql("institutions/find_by_connector_id.sql"),
            (connector_id,),
        )
        row = await cur.fetchone()
        if row:
            await conn.execute(
                load_sql("institutions/update_name.sql"),
                (name, row["id"], name),
            )
            return row["id"]

        slug = base_slug
        cur = await conn.execute(load_sql("institutions/find_by_slug.sql"), (slug,))
        existing_slug = await cur.fetchone()
        if existing_slug:
            if existing_slug["pluggy_connector_id"] is None:
                await conn.execute(
                    load_sql("institutions/attach_connector_by_id.sql"),
                    (connector_id, name, existing_slug["id"]),
                )
                return existing_slug["id"]
            slug = f"{base_slug}-{connector_id}"

        cur = await conn.execute(
            load_sql("institutions/upsert_by_connector.sql"),
            (name, slug, connector_id),
        )
        inserted = await cur.fetchone()
        return inserted["id"] if inserted else None

    cur = await conn.execute(
        load_sql("institutions/upsert_by_slug.sql"),
        (name, base_slug),
    )
    row = await cur.fetchone()
    return row["id"] if row else None


async def init_pluggy_schema() -> None:
    """Ensure all tables, migrations, and unified views exist idempotently."""
    async with pool.connection() as conn:
        await conn.execute(load_sql("schema/init.sql"))

        cur = await conn.execute(load_sql("pluggy/list_items_pending_institution.sql"))
        pending_items = await cur.fetchall()
        for row in pending_items:
            inst_id = await _upsert_institution_from_connector(
                conn, row["connector_id"], row["connector_name"]
            )
            if inst_id:
                await conn.execute(
                    load_sql("pluggy/update_item_institution.sql"),
                    (inst_id, row["pluggy_item_id"]),
                )

        await conn.execute(load_sql("schema/backfill_manual_institutions.sql"))
        await conn.execute(load_sql("schema/backfill_pluggy_users.sql"))


async def _resolve_user_id_from_client_user_id(
    conn: Any, client_user_id: str | None
) -> UUID | None:
    if not client_user_id:
        return None
    cur = await conn.execute(
        load_sql("users/resolve_by_client_user_id.sql"),
        (client_user_id,),
    )
    row = await cur.fetchone()
    return row["id"] if row else None


async def upsert_item(
    item: dict[str, Any],
    user_id: UUID | str | None = None,
) -> None:
    connector = item.get("connector") or {}
    connector_id = connector.get("id")
    connector_name = connector.get("name")
    client_user_id = item.get("clientUserId")

    async with pool.connection() as conn:
        resolved_user_id = user_id or await _resolve_user_id_from_client_user_id(
            conn, client_user_id
        )
        institution_id = await _upsert_institution_from_connector(
            conn, connector_id, connector_name
        )
        params = {
            "pluggy_item_id": item["id"],
            "user_id": str(resolved_user_id) if resolved_user_id else None,
            "institution_id": institution_id,
            "connector_id": connector_id,
            "connector_name": connector_name,
            "status": item.get("status", "UPDATING"),
            "execution_status": item.get("executionStatus"),
            "client_user_id": client_user_id,
            "last_updated_at": item.get("lastUpdatedAt"),
            "next_auto_sync_at": item.get("nextAutoSyncAt"),
            "raw_payload": Jsonb(item),
        }
        await conn.execute(load_sql("pluggy/upsert_item.sql"), params)


async def upsert_accounts(item_id: str, accounts: list[dict[str, Any]]) -> None:
    if not accounts:
        return

    rows = []
    for acc in accounts:
        credit_data = acc.get("creditData") or {}
        rows.append(
            {
                "pluggy_account_id": acc["id"],
                "pluggy_item_id": item_id,
                "type": acc.get("type", "BANK"),
                "subtype": acc.get("subtype"),
                "name": acc.get("name"),
                "number": acc.get("number"),
                "balance_cents": to_cents(acc.get("balance", 0)) or 0,
                "currency_code": acc.get("currencyCode", "BRL"),
                "credit_limit_cents": to_cents(credit_data.get("creditLimit")),
                "available_credit_limit_cents": to_cents(
                    credit_data.get("availableCreditLimit")
                ),
                "raw_payload": Jsonb(acc),
            }
        )

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(load_sql("pluggy/upsert_accounts.sql"), rows)


async def upsert_transactions(
    item_id: str, account_id: str, transactions: list[dict[str, Any]]
) -> int:
    if not transactions:
        return 0

    rows = []
    for tx in transactions:
        payment_data = tx.get("paymentData") or {}
        merchant = tx.get("merchant") or {}
        rows.append(
            {
                "pluggy_transaction_id": tx["id"],
                "pluggy_account_id": tx.get("accountId") or account_id,
                "pluggy_item_id": item_id,
                "description": tx.get("description") or "",
                "description_raw": tx.get("descriptionRaw"),
                "amount_cents": to_cents(tx.get("amount", 0)) or 0,
                "currency_code": tx.get("currencyCode", "BRL"),
                "date": tx["date"],
                "type": tx.get("type", "DEBIT"),
                "status": tx.get("status"),
                "category": tx.get("category"),
                "category_id": tx.get("categoryId"),
                "payment_method": payment_data.get("paymentMethod"),
                "merchant_name": merchant.get("name"),
                "raw_payload": Jsonb(tx),
            }
        )

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(load_sql("pluggy/upsert_transactions.sql"), rows)

    return len(rows)


async def delete_transactions_by_ids(transaction_ids: list[str]) -> int:
    if not transaction_ids:
        return 0
    async with pool.connection() as conn:
        res = await conn.execute(
            load_sql("pluggy/delete_transactions_by_ids.sql"),
            (transaction_ids,),
        )
        return res.rowcount or 0


async def delete_item_cascade(item_id: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(load_sql("pluggy/delete_item_cascade.sql"), (item_id,))


async def get_account_item_map(account_ids: list[str]) -> dict[str, str]:
    if not account_ids:
        return {}
    async with pool.connection() as conn:
        cur = await conn.execute(
            load_sql("pluggy/get_account_item_map.sql"),
            (account_ids,),
        )
        rows = await cur.fetchall()
        return {r["pluggy_account_id"]: r["pluggy_item_id"] for r in rows}


async def list_institutions() -> list[dict[str, Any]]:
    async with pool.connection() as conn:
        cur = await conn.execute(load_sql("institutions/list.sql"))
        return await cur.fetchall()


async def list_items(user_id: UUID | str | None = None) -> list[dict[str, Any]]:
    params = {"user_id": str(user_id) if user_id else None}
    async with pool.connection() as conn:
        cur = await conn.execute(load_sql("pluggy/list_items.sql"), params)
        return await cur.fetchall()


async def list_accounts(
    item_id: str | None = None,
    user_id: UUID | str | None = None,
) -> list[dict[str, Any]]:
    params = {
        "item_id": item_id,
        "user_id": str(user_id) if user_id else None,
    }
    async with pool.connection() as conn:
        cur = await conn.execute(load_sql("pluggy/list_accounts.sql"), params)
        return await cur.fetchall()


async def list_transactions(
    *,
    account_id: str | None = None,
    item_id: str | None = None,
    user_id: UUID | str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    params = {
        "account_id": account_id,
        "item_id": item_id,
        "user_id": str(user_id) if user_id else None,
        "limit": limit,
        "offset": offset,
    }
    async with pool.connection() as conn:
        cur = await conn.execute(load_sql("pluggy/list_transactions.sql"), params)
        return await cur.fetchall()


async def list_unified_transactions(
    *,
    user_id: UUID | str | None = None,
    institution_id: UUID | str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    params = {
        "user_id": str(user_id) if user_id else None,
        "institution_id": str(institution_id) if institution_id else None,
        "source": source,
        "limit": limit,
        "offset": offset,
    }
    async with pool.connection() as conn:
        cur = await conn.execute(load_sql("transactions/list_unified.sql"), params)
        return await cur.fetchall()


async def create_user(
    *,
    name: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    """Create a new user row and return it."""
    async with pool.connection() as conn:
        cur = await conn.execute(load_sql("users/create.sql"), (name, email))
        row = await cur.fetchone()
        assert row is not None
        return row


async def get_user(user_id: UUID | str) -> dict[str, Any] | None:
    """Fetch a user by UUID."""
    async with pool.connection() as conn:
        cur = await conn.execute(load_sql("users/get_by_id.sql"), (str(user_id),))
        return await cur.fetchone()


async def get_user_by_identity(channel: str, external_id: str) -> dict[str, Any] | None:
    """Resolve a user from a channel identity (e.g., telegram + chat_id)."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            load_sql("users/get_by_identity.sql"),
            (channel, external_id),
        )
        return await cur.fetchone()


async def link_user_identity(
    *,
    user_id: UUID | str,
    channel: str,
    external_id: str,
    username: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind a channel identity (e.g. telegram_chat_id) to an existing user."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            load_sql("users/link_identity.sql"),
            (
                str(user_id),
                channel,
                external_id,
                username,
                Jsonb(metadata) if metadata is not None else None,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def get_or_create_user_by_identity(
    *,
    channel: str,
    external_id: str,
    name: str | None = None,
    username: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the user for a channel identity, creating both user and identity if needed."""
    existing = await get_user_by_identity(channel, external_id)
    if existing:
        return existing

    async with pool.connection() as conn:
        cur = await conn.execute(load_sql("users/create.sql"), (name, None))
        user = await cur.fetchone()
        assert user is not None
        await conn.execute(
            load_sql("users/insert_identity_if_absent.sql"),
            (
                str(user["id"]),
                channel,
                external_id,
                username,
                Jsonb(metadata) if metadata is not None else None,
            ),
        )
        cur = await conn.execute(
            load_sql("users/get_by_identity.sql"),
            (channel, external_id),
        )
        row = await cur.fetchone()
        assert row is not None
        return row


async def get_or_create_chat_session(
    *,
    user_id: UUID | str,
    channel: str,
    external_session_id: str | None = None,
) -> dict[str, Any]:
    """Fetch or create a persistent chat session for a user and channel."""
    async with pool.connection() as conn:
        if external_session_id is not None:
            cur = await conn.execute(
                load_sql("chat/upsert_session_with_external.sql"),
                (str(user_id), channel, external_session_id),
            )
        else:
            cur = await conn.execute(
                load_sql("chat/upsert_session_default.sql"),
                (str(user_id), channel),
            )
        row = await cur.fetchone()
        assert row is not None
        return row


async def set_session_pending_transaction(
    session_id: UUID | str,
    pending_transaction_id: UUID | str | None,
) -> None:
    """Attach or clear the active pending manual transaction on a chat session."""
    async with pool.connection() as conn:
        await conn.execute(
            load_sql("chat/set_session_pending_transaction.sql"),
            (
                str(pending_transaction_id) if pending_transaction_id else None,
                str(session_id),
            ),
        )


async def append_chat_message(
    *,
    session_id: UUID | str,
    user_id: UUID | str,
    role: str,
    content: str,
    channel: str,
    input_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a chat message and touch the parent session's last_message_at."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            load_sql("chat/append_message.sql"),
            (
                str(session_id),
                str(user_id),
                role,
                content,
                channel,
                input_type,
                Jsonb(metadata) if metadata is not None else None,
            ),
        )
        msg = await cur.fetchone()
        await conn.execute(
            load_sql("chat/touch_session_last_message.sql"),
            (str(session_id),),
        )
        assert msg is not None
        return msg


async def list_chat_messages(
    session_id: UUID | str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return the most recent messages for a session in chronological order."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            load_sql("chat/list_messages.sql"),
            (str(session_id), limit),
        )
        return await cur.fetchall()
