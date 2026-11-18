import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from app import db, sync_service
from app.pluggy_client import PluggyNotConfiguredError, pluggy_client


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str, ensure_ascii=False))


async def _run_command(args: argparse.Namespace) -> int:
    await db.pool.open(wait=True)
    await db.init_pluggy_schema()
    try:
        if args.command == "register":
            result = await sync_service.sync_full_item(args.item_id)
            _print_json(result)
            return 0

        if args.command == "sync":
            result = await sync_service.sync_full_item(
                args.item_id, date_from=args.date_from
            )
            _print_json(result)
            return 0

        if args.command == "refresh":
            result = await sync_service.refresh_item(
                args.item_id,
                wait=not args.no_wait,
                timeout_seconds=args.timeout,
            )
            _print_json(result)
            return 0

        if args.command == "status":
            items = await db.list_items()
            _print_json({"results": items})
            return 0

        return 1
    except PluggyNotConfiguredError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPStatusError as exc:
        print(
            f"Pluggy API error ({exc.response.status_code}): {exc.response.text}",
            file=sys.stderr,
        )
        return 3
    finally:
        await pluggy_client.close()
        await db.pool.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="CLI for managing and syncing Pluggy Open Finance items.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_register = sub.add_parser(
        "register", help="Register an existing Pluggy itemId and run initial sync."
    )
    p_register.add_argument("item_id", help="Pluggy item ID from the Pluggy Dashboard")

    p_sync = sub.add_parser(
        "sync", help="Pull latest accounts and transactions from Pluggy into Postgres."
    )
    p_sync.add_argument("item_id", help="Pluggy item ID")
    p_sync.add_argument(
        "--from",
        dest="date_from",
        default=None,
        help="Optional start date (YYYY-MM-DD)",
    )

    p_refresh = sub.add_parser(
        "refresh",
        help="Trigger Pluggy to re-fetch from the bank (PATCH /items/{id}).",
    )
    p_refresh.add_argument("item_id", help="Pluggy item ID")
    p_refresh.add_argument(
        "--no-wait",
        action="store_true",
        help="Return immediately instead of polling until UPDATED and syncing DB.",
    )
    p_refresh.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Max seconds to wait for UPDATED when polling (default: 60).",
    )

    sub.add_parser("status", help="List all registered items and their sync status.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_command(args)))


if __name__ == "__main__":
    main()
