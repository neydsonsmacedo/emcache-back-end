import unicodedata
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from app import db
from app.sql_loader import load_sql

VALID_ENTRY_TYPES = {"DEBIT", "CREDIT"}
VALID_GRANULARITIES = {"day", "week", "month"}
VALID_PERIODS = {"day", "week", "month", "custom"}
VALID_INPUT_CHANNELS = {"text", "audio", "image", "api", "manual"}
REQUIRED_MANUAL_FIELDS = ("data", "valor", "categoria", "tipo_transacao", "origem")

_CATEGORY_MAP = {
    "alimentacao": "Alimentação",
    "transporte": "Transporte",
    "moradia": "Moradia",
    "lazer": "Lazer",
    "saude": "Saúde",
    "educacao": "Educação",
    "compras": "Compras",
    "servicos": "Serviços",
    "outros": "Outros",
}

_PAYMENT_TYPE_MAP = {
    "credito": "Crédito",
    "cartao de credito": "Crédito",
    "debito": "Débito",
    "cartao de debito": "Débito",
    "dinheiro": "Dinheiro",
    "pix": "Pix",
    "transferencia": "Transferência",
}


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_category(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    key = _strip_accents(cleaned).lower()
    return _CATEGORY_MAP.get(key, cleaned)


def normalize_payment_type(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    key = _strip_accents(cleaned).lower()
    return _PAYMENT_TYPE_MAP.get(key, cleaned)


def parse_date_value(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    cleaned = value.strip()
    if not cleaned:
        return None
    return date.fromisoformat(cleaned)


def resolve_period_dates(
    period: str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    *,
    reference_date: date | None = None,
) -> tuple[date | None, date | None]:
    """Resolve a named period ('day', 'week', 'month', 'custom') into concrete dates."""
    parsed_from = parse_date_value(date_from)
    parsed_to = parse_date_value(date_to)

    if period is not None and period not in VALID_PERIODS:
        raise ValueError(f"Período inválido: {period}")

    if parsed_from is not None or parsed_to is not None or period == "custom":
        if parsed_from and parsed_to and parsed_from > parsed_to:
            raise ValueError("date_from não pode ser maior que date_to")
        return parsed_from, parsed_to

    if period is None:
        return None, None

    today = reference_date or date.today()
    if period == "day":
        return today, today
    if period == "week":
        start_of_week = today - timedelta(days=today.weekday())
        return start_of_week, today
    if period == "month":
        return today.replace(day=1), today

    return None, None


def compute_manual_completeness(
    *,
    data: date | None,
    valor: int | None,
    categoria: str | None,
    tipo_transacao: str | None,
    origem: str | None,
    institution_id: UUID | str | None,
) -> tuple[str, list[str]]:
    """Compute ('completo' | 'pendente', dados_faltantes) for a manual transaction."""
    missing: list[str] = []
    if data is None:
        missing.append("data")
    if valor is None or valor <= 0:
        missing.append("valor")
    if not categoria or not categoria.strip():
        missing.append("categoria")
    if not tipo_transacao or not tipo_transacao.strip():
        missing.append("tipo_transacao")
    if (not origem or not origem.strip()) and institution_id is None:
        missing.append("origem")

    status = "completo" if not missing else "pendente"
    return status, missing


async def _resolve_institution(
    conn: Any,
    *,
    institution_id: UUID | str | None,
    origem: str | None,
    tipo_transacao: str | None,
) -> tuple[UUID | None, str | None]:
    cleaned_origem = origem.strip() if origem and origem.strip() else None

    if institution_id is not None:
        cur = await conn.execute(
            load_sql("institutions/get_by_id.sql"),
            {"id": str(institution_id)},
        )
        inst = await cur.fetchone()
        if not inst:
            raise ValueError(f"Instituição não encontrada: {institution_id}")
        return inst["id"], cleaned_origem or inst["name"]

    if cleaned_origem:
        cur = await conn.execute(
            load_sql("institutions/find_by_name_or_slug.sql"),
            {"query": cleaned_origem},
        )
        inst = await cur.fetchone()
        if inst:
            return inst["id"], cleaned_origem
        return None, cleaned_origem

    if tipo_transacao == "Dinheiro":
        cur = await conn.execute(
            load_sql("institutions/find_by_name_or_slug.sql"),
            {"query": "dinheiro"},
        )
        inst = await cur.fetchone()
        if inst:
            return inst["id"], inst["name"]
        return None, "Dinheiro"

    return None, None


async def get_consolidated_balance(
    user_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Return consolidated balances across Pluggy accounts and manual wallets/benefits."""
    uid = str(user_id) if user_id else None
    async with db.pool.connection() as conn:
        cur_pluggy = await conn.execute(
            load_sql("analytics/balance_pluggy_accounts.sql"),
            {"user_id": uid},
        )
        pluggy_accounts = await cur_pluggy.fetchall()

        cur_wallets = await conn.execute(
            load_sql("analytics/balance_manual_wallets.sql"),
            {"user_id": uid},
        )
        manual_wallets = await cur_wallets.fetchall()

    bank_balance_cents = sum(
        int(acc["balance_cents"] or 0)
        for acc in pluggy_accounts
        if acc["type"] != "CREDIT"
    )
    credit_card_balance_cents = sum(
        int(acc["balance_cents"] or 0)
        for acc in pluggy_accounts
        if acc["type"] == "CREDIT"
    )
    manual_wallets_balance_cents = sum(
        int(wallet["balance_cents"] or 0) for wallet in manual_wallets
    )
    consolidated_balance_cents = bank_balance_cents + manual_wallets_balance_cents
    net_balance_cents = consolidated_balance_cents - credit_card_balance_cents

    return {
        "bank_balance_cents": bank_balance_cents,
        "manual_wallets_balance_cents": manual_wallets_balance_cents,
        "consolidated_balance_cents": consolidated_balance_cents,
        "credit_card_balance_cents": credit_card_balance_cents,
        "net_balance_cents": net_balance_cents,
        "pluggy_accounts": pluggy_accounts,
        "manual_wallets": manual_wallets,
    }


async def get_period_totals(
    user_id: UUID | str | None = None,
    *,
    period: str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    institution_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Return income, expense, net, and count from unified_transactions for a period."""
    resolved_from, resolved_to = resolve_period_dates(period, date_from, date_to)
    params = {
        "user_id": str(user_id) if user_id else None,
        "institution_id": str(institution_id) if institution_id else None,
        "date_from": resolved_from,
        "date_to": resolved_to,
    }
    async with db.pool.connection() as conn:
        cur = await conn.execute(load_sql("analytics/period_totals.sql"), params)
        row = await cur.fetchone() or {}

        cur_pending = await conn.execute(
            load_sql("manual_transactions/count_pending.sql"),
            {"user_id": params["user_id"]},
        )
        pending_row = await cur_pending.fetchone() or {}

    income = int(row.get("total_income_cents") or 0)
    expense = int(row.get("total_expense_cents") or 0)
    return {
        "date_from": resolved_from.isoformat() if resolved_from else None,
        "date_to": resolved_to.isoformat() if resolved_to else None,
        "total_income_cents": income,
        "total_expense_cents": expense,
        "net_cents": income - expense,
        "transaction_count": int(row.get("transaction_count") or 0),
        "pending_manual_count": int(pending_row.get("pending_count") or 0),
    }


async def get_analytics_summary(
    user_id: UUID | str | None = None,
    *,
    period: str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    institution_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Combine consolidated balances and period totals into a single summary."""
    balances = await get_consolidated_balance(user_id=user_id)
    period_totals = await get_period_totals(
        user_id=user_id,
        period=period,
        date_from=date_from,
        date_to=date_to,
        institution_id=institution_id,
    )
    return {
        "balances": balances,
        "period": period_totals,
    }


async def get_spending_by_category(
    user_id: UUID | str | None = None,
    *,
    entry_type: str | None = "DEBIT",
    period: str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    institution_id: UUID | str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate unified_transactions grouped by category."""
    if entry_type is not None and entry_type not in VALID_ENTRY_TYPES:
        raise ValueError(f"Tipo inválido: {entry_type}")

    resolved_from, resolved_to = resolve_period_dates(period, date_from, date_to)
    params = {
        "user_id": str(user_id) if user_id else None,
        "type": entry_type,
        "institution_id": str(institution_id) if institution_id else None,
        "date_from": resolved_from,
        "date_to": resolved_to,
    }
    async with db.pool.connection() as conn:
        cur = await conn.execute(load_sql("analytics/by_category.sql"), params)
        return await cur.fetchall()


async def get_spending_by_institution(
    user_id: UUID | str | None = None,
    *,
    period: str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate unified_transactions grouped by institution."""
    resolved_from, resolved_to = resolve_period_dates(period, date_from, date_to)
    params = {
        "user_id": str(user_id) if user_id else None,
        "date_from": resolved_from,
        "date_to": resolved_to,
    }
    async with db.pool.connection() as conn:
        cur = await conn.execute(load_sql("analytics/by_institution.sql"), params)
        return await cur.fetchall()


async def get_cashflow(
    user_id: UUID | str | None = None,
    *,
    granularity: str = "day",
    period: str | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    institution_id: UUID | str | None = None,
) -> list[dict[str, Any]]:
    """Return time-series cashflow buckets (income, expense, net) from unified_transactions."""
    if granularity not in VALID_GRANULARITIES:
        raise ValueError(f"Granularidade inválida: {granularity}")

    resolved_from, resolved_to = resolve_period_dates(period, date_from, date_to)
    params = {
        "user_id": str(user_id) if user_id else None,
        "granularity": granularity,
        "institution_id": str(institution_id) if institution_id else None,
        "date_from": resolved_from,
        "date_to": resolved_to,
    }
    async with db.pool.connection() as conn:
        cur = await conn.execute(load_sql("analytics/cashflow.sql"), params)
        rows = await cur.fetchall()

    for row in rows:
        row["net_cents"] = int(row["income_cents"] or 0) - int(row["expense_cents"] or 0)
    return rows


async def create_manual_transaction(
    *,
    user_id: UUID | str | None = None,
    data: date | str | None = None,
    valor_cents: int | None = None,
    entry_type: str = "DEBIT",
    categoria: str | None = None,
    tipo_transacao: str | None = None,
    origem: str | None = None,
    institution_id: UUID | str | None = None,
    pluggy_account_id: str | None = None,
    resumo_item: str | None = None,
    canal_entrada: str = "api",
    raw_input: str | None = None,
) -> dict[str, Any]:
    """Validate, compute completeness, and insert a manual transaction."""
    if entry_type not in VALID_ENTRY_TYPES:
        raise ValueError(f"Tipo inválido: {entry_type}")
    if valor_cents is not None and valor_cents < 0:
        raise ValueError("valor_cents não pode ser negativo")
    if canal_entrada not in VALID_INPUT_CHANNELS:
        raise ValueError(f"canal_entrada inválido: {canal_entrada}")

    parsed_date = parse_date_value(data)
    norm_category = normalize_category(categoria)
    norm_payment = normalize_payment_type(tipo_transacao)
    cleaned_resumo = (resumo_item or "").strip() or "nao_definido"

    async with db.pool.connection() as conn:
        resolved_inst_id, resolved_origem = await _resolve_institution(
            conn,
            institution_id=institution_id,
            origem=origem,
            tipo_transacao=norm_payment,
        )
        status, dados_faltantes = compute_manual_completeness(
            data=parsed_date,
            valor=valor_cents,
            categoria=norm_category,
            tipo_transacao=norm_payment,
            origem=resolved_origem,
            institution_id=resolved_inst_id,
        )
        params = {
            "user_id": str(user_id) if user_id else None,
            "data": parsed_date,
            "valor": valor_cents,
            "type": entry_type,
            "categoria": norm_category,
            "tipo_transacao": norm_payment,
            "origem": resolved_origem,
            "institution_id": str(resolved_inst_id) if resolved_inst_id else None,
            "pluggy_account_id": pluggy_account_id,
            "resumo_item": cleaned_resumo,
            "status": status,
            "dados_faltantes": dados_faltantes,
            "canal_entrada": canal_entrada,
            "raw_input": raw_input,
        }
        cur = await conn.execute(load_sql("manual_transactions/create.sql"), params)
        row = await cur.fetchone()
        assert row is not None
        return row


async def get_manual_transaction(
    transaction_id: UUID | str,
    *,
    user_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    """Fetch a single manual transaction by ID."""
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            load_sql("manual_transactions/get_by_id.sql"),
            {
                "id": str(transaction_id),
                "user_id": str(user_id) if user_id else None,
            },
        )
        return await cur.fetchone()


async def list_manual_transactions(
    *,
    user_id: UUID | str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List manual transactions filtered by user_id and optional status."""
    if status is not None and status not in ("completo", "pendente"):
        raise ValueError(f"Status inválido: {status}")

    params = {
        "user_id": str(user_id) if user_id else None,
        "status": status,
        "limit": limit,
        "offset": offset,
    }
    async with db.pool.connection() as conn:
        cur = await conn.execute(load_sql("manual_transactions/list.sql"), params)
        return await cur.fetchall()


async def list_pending_manual_transactions(
    user_id: UUID | str | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List pending manual transactions for a user."""
    return await list_manual_transactions(
        user_id=user_id,
        status="pendente",
        limit=limit,
        offset=offset,
    )


_UNSET = object()


async def update_manual_transaction(
    transaction_id: UUID | str,
    *,
    user_id: UUID | str | None = None,
    data: Any = _UNSET,
    valor_cents: Any = _UNSET,
    entry_type: Any = _UNSET,
    categoria: Any = _UNSET,
    tipo_transacao: Any = _UNSET,
    origem: Any = _UNSET,
    institution_id: Any = _UNSET,
    pluggy_account_id: Any = _UNSET,
    resumo_item: Any = _UNSET,
) -> dict[str, Any] | None:
    """Update fields on an existing manual transaction and recalculate completeness."""
    existing = await get_manual_transaction(transaction_id, user_id=user_id)
    if not existing:
        return None

    new_data = existing["data"] if data is _UNSET else parse_date_value(data)
    new_valor = existing["valor"] if valor_cents is _UNSET else valor_cents
    if new_valor is not None and new_valor < 0:
        raise ValueError("valor_cents não pode ser negativo")

    new_type = existing["type"] if entry_type is _UNSET else entry_type
    if new_type not in VALID_ENTRY_TYPES:
        raise ValueError(f"Tipo inválido: {new_type}")

    new_cat = (
        existing["categoria"]
        if categoria is _UNSET
        else normalize_category(categoria)
    )
    new_payment = (
        existing["tipo_transacao"]
        if tipo_transacao is _UNSET
        else normalize_payment_type(tipo_transacao)
    )
    new_origem = existing["origem"] if origem is _UNSET else origem
    new_inst_id = (
        existing["institution_id"] if institution_id is _UNSET else institution_id
    )
    new_account_id = (
        existing["pluggy_account_id"]
        if pluggy_account_id is _UNSET
        else pluggy_account_id
    )
    new_resumo = (
        existing["resumo_item"]
        if resumo_item is _UNSET
        else ((resumo_item or "").strip() or "nao_definido")
    )

    async with db.pool.connection() as conn:
        if origem is not _UNSET and institution_id is _UNSET:
            new_inst_id = None
        resolved_inst_id, resolved_origem = await _resolve_institution(
            conn,
            institution_id=new_inst_id,
            origem=new_origem,
            tipo_transacao=new_payment,
        )
        status, dados_faltantes = compute_manual_completeness(
            data=new_data,
            valor=new_valor,
            categoria=new_cat,
            tipo_transacao=new_payment,
            origem=resolved_origem,
            institution_id=resolved_inst_id,
        )
        params = {
            "id": str(transaction_id),
            "user_id": str(user_id) if user_id else None,
            "data": new_data,
            "valor": new_valor,
            "type": new_type,
            "categoria": new_cat,
            "tipo_transacao": new_payment,
            "origem": resolved_origem,
            "institution_id": str(resolved_inst_id) if resolved_inst_id else None,
            "pluggy_account_id": new_account_id,
            "resumo_item": new_resumo,
            "status": status,
            "dados_faltantes": dados_faltantes,
        }
        cur = await conn.execute(load_sql("manual_transactions/update.sql"), params)
        return await cur.fetchone()


async def delete_manual_transaction(
    transaction_id: UUID | str,
    *,
    user_id: UUID | str | None = None,
) -> bool:
    """Delete a manual transaction by ID (optionally scoped to user_id)."""
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            load_sql("manual_transactions/delete.sql"),
            {
                "id": str(transaction_id),
                "user_id": str(user_id) if user_id else None,
            },
        )
        row = await cur.fetchone()
        return row is not None
