"""Aggregator and upstream fetch auth tests."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from server.aggregate import (
    DeviceAggregator,
    DeviceSnapshot,
    build_snapshot,
    default_fetcher,
    normalize_api_url,
)
from server.config import DeviceConfig, WebUIConfig


def test_normalize_api_url():
    assert normalize_api_url("http://x/api/v1/") == "http://x/api/v1"


def test_build_snapshot_error():
    device = DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1")
    snap = build_snapshot(device, {"ok": False, "error": "down", "http_status": 500, "latency_ms": 3})
    assert snap.online is False
    assert snap.status == "offline"
    assert snap.error == "down"
    assert snap.to_public_dict()["healthy"] is False


def test_build_snapshot_success():
    device = DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1")
    snap = build_snapshot(
        device,
        {
            "ok": True,
            "http_status": 200,
            "latency_ms": 8,
            "payload": {
                "device_id": "a",
                "status": "active",
                "app": {"process_name": "Code.exe", "display_name": "VS Code"},
                "timestamp": "2026-01-01T00:00:00Z",
            },
        },
    )
    assert snap.online is True
    assert snap.app["display_name"] == "VS Code"
    assert snap.to_public_dict()["healthy"] is True


def test_build_snapshot_stopped_is_not_online():
    device = DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1")
    snap = build_snapshot(
        device,
        {
            "ok": True,
            "http_status": 200,
            "latency_ms": 8,
            "payload": {"status": "stopped", "app": None},
        },
    )
    assert snap.online is False
    pub = snap.to_public_dict()
    assert pub["online"] is False
    assert pub["healthy"] is False


def test_disabled_device_public_not_online():
    snap = DeviceSnapshot(id="d", name="D", enabled=False, online=True, status="active")
    pub = snap.to_public_dict()
    assert pub["online"] is False
    assert pub["healthy"] is False


def test_empty_payload_not_online():
    device = DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1")
    snap = build_snapshot(
        device,
        {"ok": True, "http_status": 200, "latency_ms": 3, "payload": {}},
    )
    assert snap.online is False
    assert snap.error == "empty device payload"


def test_multidevice_list_no_match_not_fabricated():
    from server.aggregate import _extract_device_payload

    data = {
        "devices": [
            {"device_id": "other", "status": "active", "app": {"process_name": "x"}},
            {"device_id": "another", "status": "active", "app": {"process_name": "y"}},
        ]
    }
    assert _extract_device_payload(data, "a") == {}


def test_default_fetcher_sends_bearer_api_token():
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "ok",
                "data": {
                    "device_id": "a",
                    "status": "active",
                    "app": {"process_name": "Code.exe", "display_name": "VS Code"},
                    "timestamp": "2026-01-01T00:00:00Z",
                },
            },
        )

    device = DeviceConfig(
        id="a",
        name="A",
        api_base_url="http://x/api/v1",
        api_token="secret-token",
    )

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await default_fetcher(device, client=client)

    result = asyncio.run(run())
    assert result["ok"] is True
    assert seen["authorization"] == "Bearer secret-token"


def test_default_fetcher_omits_auth_when_token_empty():
    seen: dict[str, str | None] = {"authorization": "unset"}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"code": 0, "message": "ok", "data": {"device_id": "a", "status": "active", "app": None}},
        )

    device = DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1", api_token="")

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await default_fetcher(device, client=client)

    result = asyncio.run(run())
    assert result["ok"] is True
    assert seen["authorization"] is None


def test_default_fetcher_unauthorized_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 40100, "message": "invalid api token", "data": None})

    device = DeviceConfig(id="a", name="A", api_base_url="http://x/api/v1", api_token="bad")

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await default_fetcher(device, client=client)

    result = asyncio.run(run())
    assert result["ok"] is False
    assert result["http_status"] == 401
    assert "api_token" in result["error"]


def test_aggregator_refresh_with_fetcher(webui_config: WebUIConfig):
    async def fetcher(device: DeviceConfig):
        return {
            "ok": True,
            "http_status": 200,
            "latency_ms": 5,
            "payload": {
                "status": "active",
                "app": {"process_name": "a.exe", "display_name": "A"},
                "online": True,
            },
        }

    async def run():
        agg = DeviceAggregator(webui_config, fetcher=fetcher)
        snaps = await agg.refresh_once()
        by_id = {s.id: s for s in snaps}
        assert by_id["pc-1"].online is True
        # disabled device still listed but not forced online
        assert by_id["pc-2"].enabled is False
        public = agg.get_public_devices()
        assert any(d["id"] == "pc-1" and d["online"] for d in public)

    asyncio.run(run())


def test_aggregator_handles_fetch_exception(webui_config: WebUIConfig):
    async def fetcher(device: DeviceConfig):
        raise RuntimeError("boom")

    async def run():
        agg = DeviceAggregator(webui_config, fetcher=fetcher)
        snaps = await agg.refresh_once()
        by_id = {s.id: s for s in snaps}
        assert by_id["pc-1"].online is False
        assert by_id["pc-1"].error is not None

    asyncio.run(run())
