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
        if args.command in {"register", "sync"} and args.user_id:
            user = await db.get_user(args.user_id)
            if not user:
                print(f"User not found: {args.user_id}", file=sys.stderr)
                return 4

        if args.command == "register":
            result = await sync_service.sync_full_item(
                args.item_id, user_id=args.user_id
            )
            _print_json(result)
            return 0

        if args.command == "sync":
            result = await sync_service.sync_full_item(
                args.item_id, date_from=args.date_from, user_id=args.user_id
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
            items = await db.list_items(user_id=args.user_id)
            _print_json({"results": items})
            return 0

        if args.command == "metrics":
            from datetime import date, timedelta

            from app import metrics_service

            since = date.today() - timedelta(days=args.days)
            _print_json(
                {
                    "since": since,
                    "summary": await metrics_service.get_summary(None, since, None),
                    "by_model": await metrics_service.get_by_model(None, since, None),
                    "tools": await metrics_service.get_tools_usage(None, since, None),
                }
            )
            return 0

        if args.command == "metrics-prune":
            from app import metrics_service

            deleted = await metrics_service.prune_calls(args.older_than_days)
            print(f"llm_calls removidas: {deleted}")
            return 0

        if args.command == "chat":
            return await _chat(args)

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


async def _chat(args: argparse.Namespace) -> int:
    """Talk to the agent from the terminal (dev/test helper, no HTTP channel needed)."""
    import mimetypes
    from pathlib import Path

    from app import agent_service

    user = (
        await db.get_user(args.user_id)
        if args.user_id
        else await db.get_or_create_user_by_identity(
            channel="cli", external_id="dev", name="Dev CLI"
        )
    )
    if not user:
        print(f"User not found: {args.user_id}", file=sys.stderr)
        return 4
    print(f"user_id: {user['id']}", file=sys.stderr)

    async def send(text: str | None, media: Any = None) -> None:
        resp = await agent_service.handle_message(
            user_id=user["id"], channel="cli", message=text, media=media
        )
        if resp.transcript:
            print(f"[transcrição] {resp.transcript}")
        print(f"Otto: {resp.reply}")
        if resp.actions:
            print(f"  (tools: {[a['tool'] for a in resp.actions]})")
        if resp.transaction:
            print(f"  (transação: {json.dumps(resp.transaction, default=str, ensure_ascii=False)})")

    media = None
    for kind, path in (("audio", args.audio), ("image", args.image)):
        if path:
            p = Path(path)
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            media = agent_service.Media(kind, p.read_bytes(), mime, p.name)

    if args.message or media:
        await send(args.message, media)
        return 0

    print("Modo interativo (Ctrl+D ou 'sair' para encerrar)")
    while True:
        try:
            text = await asyncio.to_thread(input, "Você: ")
        except EOFError:
            return 0
        if text.strip().lower() in {"sair", "exit", "quit"}:
            return 0
        if text.strip():
            await send(text)


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
    p_register.add_argument(
        "--user-id",
        dest="user_id",
        default=None,
        help="Optional user UUID to link this Pluggy item to",
    )

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
    p_sync.add_argument(
        "--user-id",
        dest="user_id",
        default=None,
        help="Optional user UUID to link this Pluggy item to",
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

    p_status = sub.add_parser("status", help="List all registered items and their sync status.")
    p_status.add_argument(
        "--user-id",
        dest="user_id",
        default=None,
        help="Optional user UUID to filter items",
    )

    p_metrics = sub.add_parser("metrics", help="Show LLM usage indicators.")
    p_metrics.add_argument("--days", type=int, default=7, help="Window in days (default: 7)")

    p_prune = sub.add_parser("metrics-prune", help="Delete old detailed llm_calls rows.")
    p_prune.add_argument("--older-than-days", type=int, default=180, dest="older_than_days")

    p_chat = sub.add_parser("chat", help="Talk to the Otto agent (interactive or one-shot).")
    p_chat.add_argument("message", nargs="?", default=None, help="One-shot text message")
    p_chat.add_argument("--audio", default=None, help="Path to an audio file")
    p_chat.add_argument("--image", default=None, help="Path to a receipt image")
    p_chat.add_argument("--user-id", dest="user_id", default=None, help="Existing user UUID")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_command(args)))


if __name__ == "__main__":
    main()
