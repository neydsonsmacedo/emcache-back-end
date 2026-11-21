"""Agent tools: thin, user-scoped wrappers over financial_service (no LLM-generated SQL)."""

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.tools import StructuredTool

from app import db, financial_service as fs

PERIOD_PROPS = {
    "period": {"type": "string", "enum": ["day", "week", "month"]},
    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
}

_TX_PROPS = {
    "data": {"type": "string", "description": "YYYY-MM-DD"},
    "valor": {"type": "number", "description": "Valor em reais, ex.: 150.00"},
    "categoria": {"type": "string"},
    "tipo_transacao": {
        "type": "string",
        "enum": ["Crédito", "Débito", "Dinheiro", "Pix", "Transferência"],
    },
    "origem": {"type": "string", "description": "Banco/fintech/carteira"},
    "resumo_item": {"type": "string"},
    "entry_type": {"type": "string", "enum": ["DEBIT", "CREDIT"]},
}


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool("get_balance", "Saldo consolidado (contas bancárias, carteiras manuais e cartão).", {}),
    _tool("get_period_summary", "Receitas, despesas e saldo líquido de um período.", PERIOD_PROPS),
    _tool(
        "get_spending_by_category",
        "Totais agrupados por categoria em um período.",
        {**PERIOD_PROPS, "entry_type": {"type": "string", "enum": ["DEBIT", "CREDIT"]}},
    ),
    _tool("get_spending_by_institution", "Totais agrupados por instituição/origem.", PERIOD_PROPS),
    _tool("list_pending_transactions", "Lista transações manuais pendentes do usuário.", {}),
    _tool(
        "register_transaction",
        "Registra nova transação manual. Informe apenas campos conhecidos; omita os demais.",
        _TX_PROPS,
    ),
    _tool(
        "update_pending_transaction",
        "Atualiza/completa uma transação pendente. Sem transaction_id usa a pendente da conversa.",
        {"transaction_id": {"type": "string"}, **_TX_PROPS},
    ),
]


@dataclass
class ToolContext:
    user_id: UUID | str
    channel: str
    input_type: str = "text"
    raw_input: str | None = None
    pending_transaction_id: str | None = None
    # Filled by tools so the orchestrator can update session state / metadata.
    touched_transactions: list[dict[str, Any]] = field(default_factory=list)


def _period_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: args.get(k) for k in ("period", "date_from", "date_to")}


def _tx_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "data": row["data"].isoformat() if row.get("data") else None,
        "valor_cents": row.get("valor"),
        "type": row.get("type"),
        "categoria": row.get("categoria"),
        "tipo_transacao": row.get("tipo_transacao"),
        "origem": row.get("origem"),
        "resumo_item": row.get("resumo_item"),
        "status": row.get("status"),
        "dados_faltantes": row.get("dados_faltantes") or [],
    }


def _tx_kwargs(args: dict[str, Any]) -> dict[str, Any]:
    """Map tool args to financial_service kwargs, only for keys the LLM provided."""
    out: dict[str, Any] = {}
    for key in ("data", "categoria", "tipo_transacao", "origem", "resumo_item", "entry_type"):
        if args.get(key) not in (None, ""):
            out[key] = args[key]
    if args.get("valor") not in (None, ""):
        out["valor_cents"] = db.to_cents(args["valor"])
    return out


async def execute_tool(name: str, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Run a tool and return a JSON-serializable result. Errors are returned, not raised."""
    uid = ctx.user_id
    try:
        if name == "get_balance":
            bal = await fs.get_consolidated_balance(uid)
            return {k: v for k, v in bal.items() if k.endswith("_cents")}
        if name == "get_period_summary":
            return await fs.get_period_totals(uid, **_period_args(args))
        if name == "get_spending_by_category":
            return {
                "rows": await fs.get_spending_by_category(
                    uid, entry_type=args.get("entry_type", "DEBIT"), **_period_args(args)
                )
            }
        if name == "get_spending_by_institution":
            return {"rows": await fs.get_spending_by_institution(uid, **_period_args(args))}
        if name == "list_pending_transactions":
            rows = await fs.list_pending_manual_transactions(uid, limit=20)
            return {"rows": [_tx_summary(r) for r in rows]}
        if name == "register_transaction":
            kw = _tx_kwargs(args)
            kw.setdefault("entry_type", "DEBIT")
            row = await fs.create_manual_transaction(
                user_id=uid,
                canal_entrada=ctx.input_type,
                raw_input=ctx.raw_input,
                **kw,
            )
            ctx.touched_transactions.append(row)
            ctx.pending_transaction_id = str(row["id"])
            return _tx_summary(row)
        if name == "update_pending_transaction":
            tx_id = args.get("transaction_id") or ctx.pending_transaction_id
            if not tx_id:
                return {"error": "Nenhuma transação pendente para atualizar."}
            row = await fs.update_manual_transaction(tx_id, user_id=uid, **_tx_kwargs(args))
            if row is None:
                return {"error": "Transação não encontrada."}
            ctx.touched_transactions.append(row)
            ctx.pending_transaction_id = str(row["id"])
            return _tx_summary(row)
    except (ValueError, TypeError) as exc:
        return {"error": str(exc)}
    return {"error": f"Ferramenta desconhecida: {name}"}


def dump_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def build_tools(ctx: ToolContext) -> list[StructuredTool]:
    """Build LangChain tools bound to this request's user context."""
    tools: list[StructuredTool] = []
    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        name = fn["name"]

        async def _run(_name: str = name, **kwargs: Any) -> str:
            return dump_result(await execute_tool(_name, kwargs, ctx))

        tools.append(
            StructuredTool(
                name=name,
                description=fn["description"],
                args_schema={"title": name, **fn["parameters"]},
                coroutine=_run,
            )
        )
    return tools
