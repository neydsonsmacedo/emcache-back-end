import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import settings

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
            """
            SELECT id FROM institutions WHERE pluggy_connector_id = %s;
            """,
            (connector_id,),
        )
        row = await cur.fetchone()
        if row:
            await conn.execute(
                """
                UPDATE institutions
                SET name = %s, updated_at = NOW()
                WHERE id = %s AND name IS DISTINCT FROM %s;
                """,
                (name, row["id"], name),
            )
            return row["id"]

        slug = base_slug
        cur = await conn.execute("SELECT id, pluggy_connector_id FROM institutions WHERE slug = %s;", (slug,))
        existing_slug = await cur.fetchone()
        if existing_slug:
            if existing_slug["pluggy_connector_id"] is None:
                await conn.execute(
                    """
                    UPDATE institutions
                    SET pluggy_connector_id = %s, name = %s, updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (connector_id, name, existing_slug["id"]),
                )
                return existing_slug["id"]
            slug = f"{base_slug}-{connector_id}"

        cur = await conn.execute(
            """
            INSERT INTO institutions (name, slug, pluggy_connector_id, kind)
            VALUES (%s, %s, %s, 'bank')
            ON CONFLICT (pluggy_connector_id) DO UPDATE SET
                name = EXCLUDED.name,
                updated_at = NOW()
            RETURNING id;
            """,
            (name, slug, connector_id),
        )
        inserted = await cur.fetchone()
        return inserted["id"] if inserted else None

    cur = await conn.execute(
        """
        INSERT INTO institutions (name, slug, pluggy_connector_id, kind)
        VALUES (%s, %s, NULL, 'bank')
        ON CONFLICT (slug) DO UPDATE SET
            updated_at = NOW()
        RETURNING id;
        """,
        (name, base_slug),
    )
    row = await cur.fetchone()
    return row["id"] if row else None


async def init_pluggy_schema() -> None:
    """Ensure all tables, migrations, and unified views exist idempotently."""
    ddl = """
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

    CREATE TABLE IF NOT EXISTS institutions (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name VARCHAR(150) NOT NULL,
        slug VARCHAR(100) UNIQUE NOT NULL,
        pluggy_connector_id INTEGER UNIQUE,
        kind VARCHAR(30) NOT NULL DEFAULT 'bank',
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    INSERT INTO institutions (name, slug, pluggy_connector_id, kind)
    VALUES
        ('Carteira', 'carteira', NULL, 'wallet'),
        ('Dinheiro', 'dinheiro', NULL, 'wallet'),
        ('Vale Alimentação', 'vale-alimentacao', NULL, 'benefit'),
        ('Vale Refeição', 'vale-refeicao', NULL, 'benefit')
    ON CONFLICT (slug) DO NOTHING;

    CREATE TABLE IF NOT EXISTS pluggy_items (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        pluggy_item_id VARCHAR(100) UNIQUE NOT NULL,
        institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL,
        connector_id INTEGER,
        connector_name VARCHAR(150),
        status VARCHAR(50) NOT NULL DEFAULT 'UPDATING',
        execution_status VARCHAR(50),
        client_user_id VARCHAR(150),
        last_updated_at TIMESTAMPTZ,
        next_auto_sync_at TIMESTAMPTZ,
        raw_payload JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    ALTER TABLE pluggy_items
        ADD COLUMN IF NOT EXISTS institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL;

    CREATE TABLE IF NOT EXISTS pluggy_accounts (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        pluggy_account_id VARCHAR(100) UNIQUE NOT NULL,
        pluggy_item_id VARCHAR(100) NOT NULL REFERENCES pluggy_items(pluggy_item_id) ON DELETE CASCADE,
        type VARCHAR(50) NOT NULL,
        subtype VARCHAR(50),
        name VARCHAR(255),
        number VARCHAR(100),
        balance_cents BIGINT NOT NULL DEFAULT 0,
        currency_code VARCHAR(10) NOT NULL DEFAULT 'BRL',
        credit_limit_cents BIGINT,
        available_credit_limit_cents BIGINT,
        raw_payload JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS pluggy_transactions (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        pluggy_transaction_id VARCHAR(100) UNIQUE NOT NULL,
        pluggy_account_id VARCHAR(100) NOT NULL REFERENCES pluggy_accounts(pluggy_account_id) ON DELETE CASCADE,
        pluggy_item_id VARCHAR(100) NOT NULL REFERENCES pluggy_items(pluggy_item_id) ON DELETE CASCADE,
        description TEXT NOT NULL,
        description_raw TEXT,
        amount_cents BIGINT NOT NULL,
        currency_code VARCHAR(10) NOT NULL DEFAULT 'BRL',
        date TIMESTAMPTZ NOT NULL,
        type VARCHAR(20) NOT NULL,
        status VARCHAR(20),
        category VARCHAR(150),
        category_id VARCHAR(50),
        payment_method VARCHAR(50),
        merchant_name VARCHAR(255),
        raw_payload JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    DO $$
    BEGIN
        IF to_regclass('public.transactions') IS NOT NULL
           AND to_regclass('public.manual_transactions') IS NULL THEN
            ALTER TABLE transactions RENAME TO manual_transactions;
        END IF;
    END $$;

    ALTER INDEX IF EXISTS idx_transactions_data RENAME TO idx_manual_transactions_data;

    CREATE TABLE IF NOT EXISTS manual_transactions (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        data DATE,
        valor BIGINT,
        type VARCHAR(20) NOT NULL DEFAULT 'DEBIT',
        categoria VARCHAR(100),
        tipo_transacao VARCHAR(50),
        origem VARCHAR(100),
        institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL,
        pluggy_account_id VARCHAR(100) REFERENCES pluggy_accounts(pluggy_account_id) ON DELETE SET NULL,
        matched_pluggy_transaction_id VARCHAR(100) UNIQUE REFERENCES pluggy_transactions(pluggy_transaction_id) ON DELETE SET NULL,
        resumo_item VARCHAR(255) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pendente',
        dados_faltantes TEXT[] DEFAULT '{}',
        canal_entrada VARCHAR(20) NOT NULL,
        raw_input TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    ALTER TABLE manual_transactions
        ADD COLUMN IF NOT EXISTS type VARCHAR(20) NOT NULL DEFAULT 'DEBIT',
        ADD COLUMN IF NOT EXISTS institution_id UUID REFERENCES institutions(id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS pluggy_account_id VARCHAR(100) REFERENCES pluggy_accounts(pluggy_account_id) ON DELETE SET NULL,
        ADD COLUMN IF NOT EXISTS matched_pluggy_transaction_id VARCHAR(100) UNIQUE REFERENCES pluggy_transactions(pluggy_transaction_id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_pluggy_items_institution ON pluggy_items (institution_id);
    CREATE INDEX IF NOT EXISTS idx_pluggy_accounts_item ON pluggy_accounts (pluggy_item_id);
    CREATE INDEX IF NOT EXISTS idx_pluggy_transactions_date ON pluggy_transactions (date DESC);
    CREATE INDEX IF NOT EXISTS idx_pluggy_transactions_account ON pluggy_transactions (pluggy_account_id);
    CREATE INDEX IF NOT EXISTS idx_manual_transactions_data ON manual_transactions (data DESC);
    CREATE INDEX IF NOT EXISTS idx_manual_transactions_institution ON manual_transactions (institution_id);
    CREATE INDEX IF NOT EXISTS idx_manual_transactions_status ON manual_transactions (status);

    CREATE OR REPLACE VIEW unified_transactions AS
    SELECT
        pt.id::TEXT AS id,
        'pluggy'::VARCHAR(20) AS source,
        pt.date AS occurred_at,
        pt.date::DATE AS transaction_date,
        ABS(pt.amount_cents) AS amount_cents,
        pt.currency_code,
        pt.type,
        pt.status,
        pt.description,
        pt.category,
        pt.payment_method,
        pt.merchant_name,
        pi.institution_id,
        COALESCE(inst.name, pi.connector_name) AS institution_name,
        pt.pluggy_account_id AS account_id,
        pa.name AS account_name,
        pt.pluggy_transaction_id,
        pt.created_at
    FROM pluggy_transactions pt
    JOIN pluggy_accounts pa ON pa.pluggy_account_id = pt.pluggy_account_id
    JOIN pluggy_items pi ON pi.pluggy_item_id = pt.pluggy_item_id
    LEFT JOIN institutions inst ON inst.id = pi.institution_id

    UNION ALL

    SELECT
        mt.id::TEXT AS id,
        'manual'::VARCHAR(20) AS source,
        COALESCE(mt.data::TIMESTAMPTZ, mt.created_at) AS occurred_at,
        COALESCE(mt.data, mt.created_at::DATE) AS transaction_date,
        COALESCE(mt.valor, 0) AS amount_cents,
        'BRL'::VARCHAR(10) AS currency_code,
        mt.type,
        CASE WHEN mt.status = 'completo' THEN 'POSTED' ELSE 'PENDING' END AS status,
        mt.resumo_item AS description,
        mt.categoria AS category,
        mt.tipo_transacao AS payment_method,
        NULL::VARCHAR(255) AS merchant_name,
        mt.institution_id,
        COALESCE(inst.name, mt.origem) AS institution_name,
        mt.pluggy_account_id AS account_id,
        pa.name AS account_name,
        mt.matched_pluggy_transaction_id AS pluggy_transaction_id,
        mt.created_at
    FROM manual_transactions mt
    LEFT JOIN institutions inst ON inst.id = mt.institution_id
    LEFT JOIN pluggy_accounts pa ON pa.pluggy_account_id = mt.pluggy_account_id
    WHERE mt.status = 'completo'
      AND mt.matched_pluggy_transaction_id IS NULL;
    """
    async with pool.connection() as conn:
        await conn.execute(ddl)

        # Backfill institution_id for existing pluggy_items
        cur = await conn.execute(
            """
            SELECT pluggy_item_id, connector_id, connector_name
            FROM pluggy_items
            WHERE institution_id IS NULL
              AND (connector_id IS NOT NULL OR connector_name IS NOT NULL);
            """
        )
        pending_items = await cur.fetchall()
        for row in pending_items:
            inst_id = await _upsert_institution_from_connector(
                conn, row["connector_id"], row["connector_name"]
            )
            if inst_id:
                await conn.execute(
                    "UPDATE pluggy_items SET institution_id = %s WHERE pluggy_item_id = %s;",
                    (inst_id, row["pluggy_item_id"]),
                )

        # Backfill institution_id for existing manual_transactions matching known institutions
        await conn.execute(
            """
            UPDATE manual_transactions mt
            SET institution_id = inst.id
            FROM institutions inst
            WHERE mt.institution_id IS NULL
              AND mt.origem IS NOT NULL
              AND (LOWER(TRIM(mt.origem)) = LOWER(inst.name) OR LOWER(TRIM(mt.origem)) = inst.slug);
            """
        )


async def upsert_item(item: dict[str, Any]) -> None:
    connector = item.get("connector") or {}
    connector_id = connector.get("id")
    connector_name = connector.get("name")
    sql = """
    INSERT INTO pluggy_items (
        pluggy_item_id, institution_id, connector_id, connector_name, status, execution_status,
        client_user_id, last_updated_at, next_auto_sync_at, raw_payload, updated_at
    ) VALUES (
        %(pluggy_item_id)s, %(institution_id)s, %(connector_id)s, %(connector_name)s, %(status)s, %(execution_status)s,
        %(client_user_id)s, %(last_updated_at)s, %(next_auto_sync_at)s, %(raw_payload)s, NOW()
    )
    ON CONFLICT (pluggy_item_id) DO UPDATE SET
        institution_id = COALESCE(EXCLUDED.institution_id, pluggy_items.institution_id),
        connector_id = EXCLUDED.connector_id,
        connector_name = EXCLUDED.connector_name,
        status = EXCLUDED.status,
        execution_status = EXCLUDED.execution_status,
        client_user_id = COALESCE(EXCLUDED.client_user_id, pluggy_items.client_user_id),
        last_updated_at = EXCLUDED.last_updated_at,
        next_auto_sync_at = EXCLUDED.next_auto_sync_at,
        raw_payload = EXCLUDED.raw_payload,
        updated_at = NOW();
    """
    async with pool.connection() as conn:
        institution_id = await _upsert_institution_from_connector(
            conn, connector_id, connector_name
        )
        params = {
            "pluggy_item_id": item["id"],
            "institution_id": institution_id,
            "connector_id": connector_id,
            "connector_name": connector_name,
            "status": item.get("status", "UPDATING"),
            "execution_status": item.get("executionStatus"),
            "client_user_id": item.get("clientUserId"),
            "last_updated_at": item.get("lastUpdatedAt"),
            "next_auto_sync_at": item.get("nextAutoSyncAt"),
            "raw_payload": Jsonb(item),
        }
        await conn.execute(sql, params)


async def upsert_accounts(item_id: str, accounts: list[dict[str, Any]]) -> None:
    if not accounts:
        return

    sql = """
    INSERT INTO pluggy_accounts (
        pluggy_account_id, pluggy_item_id, type, subtype, name, number,
        balance_cents, currency_code, credit_limit_cents, available_credit_limit_cents,
        raw_payload, updated_at
    ) VALUES (
        %(pluggy_account_id)s, %(pluggy_item_id)s, %(type)s, %(subtype)s, %(name)s, %(number)s,
        %(balance_cents)s, %(currency_code)s, %(credit_limit_cents)s, %(available_credit_limit_cents)s,
        %(raw_payload)s, NOW()
    )
    ON CONFLICT (pluggy_account_id) DO UPDATE SET
        type = EXCLUDED.type,
        subtype = EXCLUDED.subtype,
        name = EXCLUDED.name,
        number = EXCLUDED.number,
        balance_cents = EXCLUDED.balance_cents,
        currency_code = EXCLUDED.currency_code,
        credit_limit_cents = EXCLUDED.credit_limit_cents,
        available_credit_limit_cents = EXCLUDED.available_credit_limit_cents,
        raw_payload = EXCLUDED.raw_payload,
        updated_at = NOW();
    """
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
            await cur.executemany(sql, rows)


async def upsert_transactions(
    item_id: str, account_id: str, transactions: list[dict[str, Any]]
) -> int:
    if not transactions:
        return 0

    sql = """
    INSERT INTO pluggy_transactions (
        pluggy_transaction_id, pluggy_account_id, pluggy_item_id,
        description, description_raw, amount_cents, currency_code,
        date, type, status, category, category_id,
        payment_method, merchant_name, raw_payload, updated_at
    ) VALUES (
        %(pluggy_transaction_id)s, %(pluggy_account_id)s, %(pluggy_item_id)s,
        %(description)s, %(description_raw)s, %(amount_cents)s, %(currency_code)s,
        %(date)s, %(type)s, %(status)s, %(category)s, %(category_id)s,
        %(payment_method)s, %(merchant_name)s, %(raw_payload)s, NOW()
    )
    ON CONFLICT (pluggy_transaction_id) DO UPDATE SET
        description = EXCLUDED.description,
        description_raw = EXCLUDED.description_raw,
        amount_cents = EXCLUDED.amount_cents,
        currency_code = EXCLUDED.currency_code,
        date = EXCLUDED.date,
        type = EXCLUDED.type,
        status = EXCLUDED.status,
        category = EXCLUDED.category,
        category_id = EXCLUDED.category_id,
        payment_method = EXCLUDED.payment_method,
        merchant_name = EXCLUDED.merchant_name,
        raw_payload = EXCLUDED.raw_payload,
        updated_at = NOW();
    """
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
            await cur.executemany(sql, rows)

    return len(rows)


async def delete_transactions_by_ids(transaction_ids: list[str]) -> int:
    if not transaction_ids:
        return 0
    sql = "DELETE FROM pluggy_transactions WHERE pluggy_transaction_id = ANY(%s);"
    async with pool.connection() as conn:
        res = await conn.execute(sql, (transaction_ids,))
        return res.rowcount or 0


async def delete_item_cascade(item_id: str) -> None:
    sql = "DELETE FROM pluggy_items WHERE pluggy_item_id = %s;"
    async with pool.connection() as conn:
        await conn.execute(sql, (item_id,))


async def get_account_item_map(account_ids: list[str]) -> dict[str, str]:
    if not account_ids:
        return {}
    sql = """
    SELECT pluggy_account_id, pluggy_item_id
    FROM pluggy_accounts
    WHERE pluggy_account_id = ANY(%s);
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql, (account_ids,))
        rows = await cur.fetchall()
        return {r["pluggy_account_id"]: r["pluggy_item_id"] for r in rows}


async def list_institutions() -> list[dict[str, Any]]:
    sql = """
    SELECT id, name, slug, pluggy_connector_id, kind, created_at, updated_at
    FROM institutions
    ORDER BY name;
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql)
        return await cur.fetchall()


async def list_items() -> list[dict[str, Any]]:
    sql = """
    SELECT i.pluggy_item_id, i.institution_id, inst.name AS institution_name,
           i.connector_id, i.connector_name, i.status, i.execution_status,
           i.client_user_id, i.last_updated_at, i.next_auto_sync_at,
           i.created_at, i.updated_at
    FROM pluggy_items i
    LEFT JOIN institutions inst ON inst.id = i.institution_id
    ORDER BY i.updated_at DESC;
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql)
        return await cur.fetchall()


async def list_accounts(item_id: str | None = None) -> list[dict[str, Any]]:
    base = """
    SELECT a.pluggy_account_id, a.pluggy_item_id, i.connector_name,
           a.type, a.subtype, a.name, a.number,
           a.balance_cents, a.currency_code,
           a.credit_limit_cents, a.available_credit_limit_cents,
           a.updated_at
    FROM pluggy_accounts a
    JOIN pluggy_items i ON i.pluggy_item_id = a.pluggy_item_id
    """
    if item_id:
        sql = base + " WHERE a.pluggy_item_id = %s ORDER BY i.connector_name, a.name;"
        params: tuple[Any, ...] = (item_id,)
    else:
        sql = base + " ORDER BY i.connector_name, a.name;"
        params = ()

    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def list_transactions(
    *,
    account_id: str | None = None,
    item_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if account_id:
        clauses.append("t.pluggy_account_id = %s")
        params.append(account_id)
    if item_id:
        clauses.append("t.pluggy_item_id = %s")
        params.append(item_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    sql = f"""
    SELECT t.pluggy_transaction_id, t.pluggy_account_id, t.pluggy_item_id,
           i.connector_name, a.name AS account_name,
           t.description, t.amount_cents, t.currency_code, t.date,
           t.type, t.status, t.category, t.payment_method, t.merchant_name
    FROM pluggy_transactions t
    JOIN pluggy_accounts a ON a.pluggy_account_id = t.pluggy_account_id
    JOIN pluggy_items i ON i.pluggy_item_id = t.pluggy_item_id
    {where}
    ORDER BY t.date DESC
    LIMIT %s OFFSET %s;
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql, tuple(params))
        return await cur.fetchall()


async def list_unified_transactions(
    *,
    institution_id: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if institution_id:
        clauses.append("institution_id = %s")
        params.append(institution_id)
    if source:
        clauses.append("source = %s")
        params.append(source)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    sql = f"""
    SELECT id, source, occurred_at, transaction_date, amount_cents, currency_code,
           type, status, description, category, payment_method, merchant_name,
           institution_id, institution_name, account_id, account_name,
           pluggy_transaction_id, created_at
    FROM unified_transactions
    {where}
    ORDER BY occurred_at DESC
    LIMIT %s OFFSET %s;
    """
    async with pool.connection() as conn:
        cur = await conn.execute(sql, tuple(params))
        return await cur.fetchall()
