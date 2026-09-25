"""Device list helpers for admin CRUD and history proxy."""

from __future__ import annotations

from typing import Any

import httpx

from server.config import ConfigError, DeviceConfig, WebUIConfig, validate_config_dict
from server.aggregate import normalize_api_url


def device_to_dict(device: DeviceConfig) -> dict[str, Any]:
    return {
        "id": device.id,
        "name": device.name,
        "api_base_url": device.api_base_url,
        "viewer_token": device.viewer_token,
        "enabled": device.enabled,
    }


def apply_scheme(url: str, scheme: str, default_scheme: str = "http") -> str:
    """Ensure URL has a scheme; rewrite scheme when only host path given or scheme mismatch on update."""
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        # Explicit scheme wins unless caller passes empty original scheme rewrite
        return normalize_api_url(url)
    url = url.lstrip("/")
    chosen = scheme if scheme in ("http", "https") else default_scheme
    return normalize_api_url(f"{chosen}://{url}")


def upsert_device(config: WebUIConfig, raw: dict[str, Any], *, replace_id: str | None = None) -> WebUIConfig:
    """Return a new validated config with device inserted/updated."""
    data = {
        "version": config.version,
        "admin_token": config.admin_token,
        "session_ttl_seconds": config.session_ttl_seconds,
        "login_max_failures": config.login_max_failures,
        "ban_duration_hours": config.ban_duration_hours,
        "login_rate_limit_per_minute": config.login_rate_limit_per_minute,
        "refresh_interval_seconds": config.refresh_interval_seconds,
        "page_title": config.page_title,
        "page_subtitle": config.page_subtitle,
        "show_history": config.show_history,
        "devices_per_page": config.devices_per_page,
        "trust_proxy": config.trust_proxy,
        "default_device_scheme": config.default_device_scheme,
        "serve": {
            "mode": config.serve.mode,
            "host": config.serve.host,
            "port": config.serve.port,
            "ssl_certfile": config.serve.ssl_certfile,
            "ssl_keyfile": config.serve.ssl_keyfile,
        },
        "devices": [device_to_dict(d) for d in config.devices],
    }

    device_id = str(raw.get("id") or "").strip()
    if replace_id is not None:
        device_id = replace_id
        raw = {**raw, "id": replace_id}

    if not device_id:
        raise ConfigError("device id is required")

    api_base_url = apply_scheme(
        str(raw.get("api_base_url") or ""),
        config.default_device_scheme,
    )
    entry = {
        "id": device_id,
        "name": str(raw.get("name") or device_id),
        "api_base_url": api_base_url,
        "viewer_token": str(raw.get("viewer_token") or ""),
        "enabled": bool(raw.get("enabled", True)),
    }

    devices = data["devices"]
    for i, existing in enumerate(devices):
        if existing["id"] == device_id:
            if replace_id is None and raw.get("id") and raw.get("id") != existing["id"]:
                raise ConfigError("device id mismatch")
            devices[i] = entry
            break
    else:
        devices.append(entry)

    return validate_config_dict(data)


def delete_device(config: WebUIConfig, device_id: str) -> WebUIConfig:
    data_devices = [device_to_dict(d) for d in config.devices if d.id != device_id]
    if len(data_devices) == len(config.devices):
        raise ConfigError(f"device not found: {device_id}")
    return upsert_config_devices(config, data_devices)


def upsert_config_devices(config: WebUIConfig, devices: list[dict[str, Any]]) -> WebUIConfig:
    data = {
        "version": config.version,
        "admin_token": config.admin_token,
        "session_ttl_seconds": config.session_ttl_seconds,
        "login_max_failures": config.login_max_failures,
        "ban_duration_hours": config.ban_duration_hours,
        "login_rate_limit_per_minute": config.login_rate_limit_per_minute,
        "refresh_interval_seconds": config.refresh_interval_seconds,
        "page_title": config.page_title,
        "page_subtitle": config.page_subtitle,
        "show_history": config.show_history,
        "devices_per_page": config.devices_per_page,
        "trust_proxy": config.trust_proxy,
        "default_device_scheme": config.default_device_scheme,
        "serve": {
            "mode": config.serve.mode,
            "host": config.serve.host,
            "port": config.serve.port,
            "ssl_certfile": config.serve.ssl_certfile,
            "ssl_keyfile": config.serve.ssl_keyfile,
        },
        "devices": devices,
    }
    return validate_config_dict(data)


async def test_device_connection(device: DeviceConfig, timeout: float = 5.0) -> dict[str, Any]:
    """Probe a device API health/status endpoint and return a connectivity report."""
    base = normalize_api_url(device.api_base_url)
    headers = {"Accept": "application/json"}
    if device.viewer_token:
        headers["Authorization"] = f"Bearer {device.viewer_token}"
    started = httpx.AsyncClient(timeout=timeout)
    import time

    t0 = time.perf_counter()
    async with started as client:
        for path in (f"{base}/health", f"{base}/status", f"{base}/devices/{device.id}"):
            try:
                resp = await client.get(path, headers=headers)
            except httpx.HTTPError as exc:
                return {
                    "ok": False,
                    "path": path,
                    "http_status": None,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "error": str(exc) or exc.__class__.__name__,
                }
            latency_ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                message = "ok"
                try:
                    body = resp.json()
                    if isinstance(body, dict):
                        message = str(body.get("message") or "ok")
                except ValueError:
                    message = "ok (non-json)"
                return {
                    "ok": True,
                    "path": path,
                    "http_status": 200,
                    "latency_ms": latency_ms,
                    "error": None,
                    "message": message,
                }
            if resp.status_code in (401, 403):
                return {
                    "ok": False,
                    "path": path,
                    "http_status": resp.status_code,
                    "latency_ms": latency_ms,
                    "error": "unauthorized",
                }
    return {
        "ok": False,
        "path": f"{base}/health",
        "http_status": None,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "error": "no reachable endpoint",
    }


async def fetch_device_history(
    device: DeviceConfig,
    limit: int = 50,
    timeout: float = 5.0,
) -> dict[str, Any]:
    base = normalize_api_url(device.api_base_url)
    headers = {"Accept": "application/json"}
    if device.viewer_token:
        headers["Authorization"] = f"Bearer {device.viewer_token}"
    url = f"{base}/devices/{device.id}/history"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers, params={"limit": limit})
        if resp.status_code == 404:
            # Fall back to local history endpoint
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
