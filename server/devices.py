"""History proxy for configured WhatTheManDoing devices."""

from __future__ import annotations

from typing import Any

import httpx

from server.aggregate import AUTH_ERROR, ENVELOPE_UNAUTHORIZED, normalize_api_url
from server.config import DeviceConfig
from server.log_setup import get_logger

logger = get_logger("devices")


class UpstreamAuthError(Exception):
    """Upstream rejected the api_token (HTTP 401/403 or envelope code=40100)."""


class UpstreamHistoryError(Exception):
    """Upstream history request failed for a non-auth reason."""


def auth_headers(device: DeviceConfig) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if device.api_token:
        headers["Authorization"] = f"Bearer {device.api_token}"
    return headers


def _raise_for_auth_or_error(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise UpstreamAuthError(AUTH_ERROR)
    if resp.status_code >= 400:
        raise UpstreamHistoryError(f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise UpstreamHistoryError("invalid JSON") from exc
    if not isinstance(body, dict):
        raise UpstreamHistoryError("invalid JSON")
    code = body.get("code")
    if code == ENVELOPE_UNAUTHORIZED:
        raise UpstreamAuthError(AUTH_ERROR)
    if code not in (0, None):
        raise UpstreamHistoryError(str(body.get("message") or f"code {code}"))


async def fetch_device_history(
    device: DeviceConfig,
    limit: int = 50,
    timeout: float = 5.0,
) -> dict[str, Any]:
    base = normalize_api_url(device.api_base_url)
    headers = auth_headers(device)
    url = f"{base}/devices/{device.id}/history"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers, params={"limit": limit})
        if resp.status_code == 404:
            # Fall back to local history endpoint (single-machine API)
            resp = await client.get(f"{base}/status/history", headers=headers, params={"limit": limit})
        _raise_for_auth_or_error(resp)
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return {"device_id": device.id, "history": []}
        history = data.get("history")
        return {
            "device_id": data.get("device_id") or device.id,
            "history": history if isinstance(history, list) else [],
        }
