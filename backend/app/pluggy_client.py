import asyncio
import time
from typing import Any

import httpx

from app.config import settings


class PluggyNotConfiguredError(RuntimeError):
    """Raised when Pluggy credentials are missing."""


class PluggyClient:
    """Async REST client for Pluggy Open Finance API with automatic API key refresh."""

    TOKEN_TTL_SECONDS = 2 * 60 * 60  # 2 hours
    REFRESH_BUFFER_SECONDS = 5 * 60  # Refresh 5 minutes before expiration

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.pluggy_base_url.rstrip("/"),
            timeout=30.0,
        )
        self._api_key: str | None = None
        self._expires_at: float = 0.0
        self._auth_lock = asyncio.Lock()

    async def close(self) -> None:
        await self._http.aclose()

    async def _get_api_key(self) -> str:
        now = time.monotonic()
        if self._api_key and now < (self._expires_at - self.REFRESH_BUFFER_SECONDS):
            return self._api_key

        async with self._auth_lock:
            now = time.monotonic()
            if self._api_key and now < (self._expires_at - self.REFRESH_BUFFER_SECONDS):
                return self._api_key

            if not settings.pluggy_client_id or not settings.pluggy_client_secret:
                raise PluggyNotConfiguredError(
                    "PLUGGY_CLIENT_ID e PLUGGY_CLIENT_SECRET não configurados no .env"
                )

            response = await self._http.post(
                "/auth",
                json={
                    "clientId": settings.pluggy_client_id,
                    "clientSecret": settings.pluggy_client_secret,
                },
            )
            response.raise_for_status()
            data = response.json()
            self._api_key = data["apiKey"]
            self._expires_at = time.monotonic() + self.TOKEN_TTL_SECONDS
            return self._api_key

    async def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        api_key = await self._get_api_key()
        headers = {"X-API-KEY": api_key}

        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            response = await self._http.request(
                method, path_or_url, headers=headers, params=params, json=json
            )
        else:
            response = await self._http.request(
                method, path_or_url, headers=headers, params=params, json=json
            )

        # Retry once on 401 in case token was invalidated early
        if response.status_code == 401:
            self._api_key = None
            api_key = await self._get_api_key()
            headers["X-API-KEY"] = api_key
            response = await self._http.request(
                method, path_or_url, headers=headers, params=params, json=json
            )

        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def create_connect_token(
        self,
        *,
        item_id: str | None = None,
        client_user_id: str | None = None,
        webhook_url: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        options: dict[str, Any] = {}

        if item_id:
            payload["itemId"] = item_id
        if client_user_id:
            options["clientUserId"] = client_user_id
        effective_webhook = webhook_url or settings.pluggy_webhook_url
        if effective_webhook:
            options["webhookUrl"] = effective_webhook
        if options:
            payload["options"] = options

        return await self._request("POST", "/connect_token", json=payload)

    async def get_item(self, item_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/items/{item_id}")

    async def delete_item(self, item_id: str) -> None:
        await self._request("DELETE", f"/items/{item_id}")

    async def list_accounts(self, item_id: str) -> list[dict[str, Any]]:
        data = await self._request("GET", "/accounts", params={"itemId": item_id})
        return data.get("results", [])

    async def list_all_transactions(
        self,
        account_id: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all transactions for an account using Pluggy v2 cursor pagination."""
        all_results: list[dict[str, Any]] = []
        after: str | None = None
        next_url: str | None = None

        while True:
            if next_url:
                data = await self._request("GET", next_url)
            else:
                params: dict[str, Any] = {
                    "accountId": account_id,
                }
                if date_from:
                    params["from"] = date_from
                if date_to:
                    params["to"] = date_to
                if after:
                    params["after"] = after
                data = await self._request("GET", "/v2/transactions", params=params)

            results = data.get("results", [])
            all_results.extend(results)

            next_token = data.get("next")
            if not next_token or not results:
                break

            if isinstance(next_token, str) and (
                next_token.startswith("http://") or next_token.startswith("https://")
            ):
                next_url = next_token
            else:
                next_url = None
                after = next_token

        return all_results

    async def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/transactions/{transaction_id}")

    async def get_transactions_by_link(self, url: str) -> list[dict[str, Any]]:
        """Fetch transactions from a webhook's createdTransactionsLink."""
        all_results: list[dict[str, Any]] = []
        next_url: str | None = url

        while next_url:
            data = await self._request("GET", next_url)
            all_results.extend(data.get("results", []))
            next_url = data.get("next")

        return all_results


pluggy_client = PluggyClient()
