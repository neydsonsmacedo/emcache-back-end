import logging
from collections import defaultdict
from typing import Any

from app import db
from app.pluggy_client import pluggy_client

logger = logging.getLogger("emcache.sync")


async def sync_item_and_accounts(item_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch an Item and its Accounts from Pluggy and persist them."""
    item = await pluggy_client.get_item(item_id)
    await db.upsert_item(item)

    accounts = await pluggy_client.list_accounts(item_id)
    await db.upsert_accounts(item_id, accounts)
    return item, accounts


async def sync_full_item(item_id: str, date_from: str | None = None) -> dict[str, Any]:
    """Sync Item metadata, all Accounts, and all paginated Transactions."""
    item, accounts = await sync_item_and_accounts(item_id)

    total_transactions = 0
    for acc in accounts:
        txs = await pluggy_client.list_all_transactions(acc["id"], date_from=date_from)
        total_transactions += await db.upsert_transactions(item_id, acc["id"], txs)

    return {
        "item_id": item_id,
        "connector": (item.get("connector") or {}).get("name"),
        "status": item.get("status"),
        "accounts_synced": len(accounts),
        "transactions_synced": total_transactions,
    }


async def _persist_transactions_batch(
    transactions: list[dict[str, Any]], fallback_item_id: str | None = None
) -> int:
    if not transactions:
        return 0

    account_ids = list({tx["accountId"] for tx in transactions if tx.get("accountId")})
    acc_item_map = await db.get_account_item_map(account_ids)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for tx in transactions:
        acc_id = tx.get("accountId")
        if not acc_id:
            continue
        item_id = acc_item_map.get(acc_id) or fallback_item_id
        if not item_id:
            logger.warning("Skipping transaction %s: unknown item_id for account %s", tx.get("id"), acc_id)
            continue
        grouped[(item_id, acc_id)].append(tx)

    total = 0
    for (item_id, acc_id), batch in grouped.items():
        total += await db.upsert_transactions(item_id, acc_id, batch)
    return total


async def handle_webhook_event(payload: dict[str, Any]) -> None:
    """Process Pluggy webhook events asynchronously."""
    event = payload.get("event")
    item_id = payload.get("itemId")
    logger.info("Processing Pluggy webhook event=%s itemId=%s", event, item_id)

    try:
        if event in ("item/created", "item/updated"):
            if item_id:
                await sync_full_item(item_id)
        elif event in ("item/error", "item/waiting_user_input"):
            if item_id:
                item = await pluggy_client.get_item(item_id)
                await db.upsert_item(item)
        elif event == "item/deleted":
            if item_id:
                await db.delete_item_cascade(item_id)
        elif event == "transactions/created":
            link = payload.get("createdTransactionsLink")
            if link:
                txs = await pluggy_client.get_transactions_by_link(link)
                await _persist_transactions_batch(txs, fallback_item_id=item_id)
            elif item_id:
                await sync_full_item(item_id)
        elif event == "transactions/updated":
            tx_ids = payload.get("transactionIds") or []
            fetched = []
            for tx_id in tx_ids:
                fetched.append(await pluggy_client.get_transaction(tx_id))
            await _persist_transactions_batch(fetched, fallback_item_id=item_id)
        elif event == "transactions/deleted":
            tx_ids = payload.get("transactionIds") or []
            await db.delete_transactions_by_ids(tx_ids)
        else:
            logger.info("Unhandled Pluggy webhook event: %s", event)
    except Exception:
        logger.exception("Error processing Pluggy webhook event=%s itemId=%s", event, item_id)
