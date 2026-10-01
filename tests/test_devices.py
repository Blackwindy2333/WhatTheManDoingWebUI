"""History proxy tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from server.config import DeviceConfig
from server.devices import (
    UpstreamAuthError,
    UpstreamHistoryError,
    auth_headers,
    fetch_device_history,
)


def test_auth_headers_includes_bearer():
    device = DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1", api_token="tok")
    assert auth_headers(device)["Authorization"] == "Bearer tok"
    empty = DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1", api_token="")
    assert "Authorization" not in auth_headers(empty)


def _device() -> DeviceConfig:
    return DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1", api_token="tok")


def test_history_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/devices/a/history")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "ok",
                "data": {
                    "device_id": "a",
                    "history": [
                        {
                            "id": 1,
                            "device_id": "a",
                            "status": "active",
                            "process_name": "Code.exe",
                            "display_name": "VS Code",
                            "window_title": None,
                            "timestamp": "2026-01-01T00:00:00Z",
                        }
                    ],
                },
            },
        )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # Patch AsyncClient used inside fetch via monkeypatch of constructor is heavy;
            # instead call with a real client by temporarily wrapping — see below.
            return await _fetch_with_transport(handler)

    data = asyncio.run(run())
    assert data["device_id"] == "a"
    assert len(data["history"]) == 1


async def _fetch_with_transport(handler) -> dict:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    class PatchedClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", transport)
            super().__init__(*args, **kwargs)

    import server.devices as devices_mod

    original = devices_mod.httpx.AsyncClient
    devices_mod.httpx.AsyncClient = PatchedClient  # type: ignore[misc]
    try:
        return await fetch_device_history(_device(), limit=5)
    finally:
        devices_mod.httpx.AsyncClient = original  # type: ignore[misc]


def test_history_falls_back_to_status_history_on_404():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/devices/a/history"):
            return httpx.Response(404, json={"code": 40400, "message": "not found", "data": None})
        return httpx.Response(
            200,
            json={"code": 0, "message": "ok", "data": {"device_id": "a", "history": []}},
        )

    data = asyncio.run(_fetch_with_transport(handler))
    assert data["history"] == []
    assert any(p.endswith("/status/history") for p in calls)


def test_history_http_401_raises_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 40100, "message": "invalid api token", "data": None})

    with pytest.raises(UpstreamAuthError, match="api_token"):
        asyncio.run(_fetch_with_transport(handler))


def test_history_http_403_raises_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": 40300, "message": "forbidden", "data": None})

    with pytest.raises(UpstreamAuthError, match="api_token"):
        asyncio.run(_fetch_with_transport(handler))


def test_history_envelope_40100_raises_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 40100, "message": "invalid api token", "data": None})

    with pytest.raises(UpstreamAuthError, match="api_token"):
        asyncio.run(_fetch_with_transport(handler))


def test_history_other_envelope_error_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 50000, "message": "boom", "data": None})

    with pytest.raises(UpstreamHistoryError, match="boom"):
        asyncio.run(_fetch_with_transport(handler))
