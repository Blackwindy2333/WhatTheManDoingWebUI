"""Aggregator and device helper tests."""

from __future__ import annotations

import asyncio

import pytest

from server.aggregate import DeviceAggregator, DeviceSnapshot, build_snapshot, normalize_api_url
from server.config import ConfigError, DeviceConfig, WebUIConfig
from server.devices import apply_scheme, delete_device, device_to_dict, upsert_device


def test_normalize_api_url():
    assert normalize_api_url("http://x/api/v1/") == "http://x/api/v1"


def test_apply_scheme_default():
    assert apply_scheme("host:8765/api/v1", "http") == "http://host:8765/api/v1"
    assert apply_scheme("https://host/api/v1", "http") == "https://host/api/v1"


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


def test_upsert_and_delete_device(webui_config: WebUIConfig):
    cfg = upsert_device(
        webui_config,
        {"id": "new-1", "name": "New", "api_base_url": "new:1/api/v1", "enabled": True},
    )
    assert any(d.id == "new-1" for d in cfg.devices)
    cfg2 = upsert_device(
        cfg,
        {"name": "New2", "api_base_url": "https://new2/api/v1", "enabled": False},
        replace_id="new-1",
    )
    item = next(d for d in cfg2.devices if d.id == "new-1")
    assert item.name == "New2"
    assert item.api_base_url == "https://new2/api/v1"
    cfg3 = delete_device(cfg2, "new-1")
    assert all(d.id != "new-1" for d in cfg3.devices)
    with pytest.raises(ConfigError):
        delete_device(cfg3, "new-1")


def test_device_to_dict_keys(webui_config: WebUIConfig):
    data = device_to_dict(webui_config.devices[0])
    assert set(data) == {"id", "name", "api_base_url", "viewer_token", "enabled"}


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
