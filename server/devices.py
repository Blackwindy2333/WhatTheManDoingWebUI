"""History proxy for configured WhatTheManDoing devices."""

from __future__ import annotations

from typing import Any

import httpx

from server.aggregate import normalize_api_url
from server.config import DeviceConfig
from server.log_setup import get_logger

logger = get_logger("devices")


def auth_headers(device: DeviceConfig) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if device.api_token:
        headers["Authorization"] = f"Bearer {device.api_token}"
    return headers


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
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return {"device_id": device.id, "history": []}
        history = data.get("history")
        return {
            "device_id": data.get("device_id") or device.id,
            "history": history if isinstance(history, list) else [],
        }
