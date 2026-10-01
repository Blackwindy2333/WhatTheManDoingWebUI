"""Public HTTP API tests via TestClient (no live process)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.aggregate import DeviceSnapshot
from tests.conftest import DummyAggregator


def _snap(device_id: str, **kwargs) -> DeviceSnapshot:
    snap = DeviceSnapshot(id=device_id, name=device_id, enabled=True)
    snap.online = kwargs.get("online", True)
    snap.status = kwargs.get("status", "active")
    snap.app = kwargs.get("app") or {"process_name": "Code.exe", "display_name": "VS Code", "window_title": None}
    snap.latency_ms = kwargs.get("latency_ms", 12)
    snap.http_status = kwargs.get("http_status", 200)
    snap.error = kwargs.get("error")
    return snap


def test_public_config(client: TestClient):
    resp = client.get("/api/public/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert "page_title" in body["data"]
    assert "admin_token" not in body["data"]


def test_public_probe_reports_proxy_headers(client: TestClient):
    resp = client.get(
        "/api/public/probe",
        headers={
            "X-Forwarded-For": "203.0.113.9, 10.0.0.1",
            "X-Forwarded-Proto": "https",
            "X-Real-IP": "203.0.113.9",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"]["x_forwarded_proto"] == "https"
    assert data["forwarded"]["x_forwarded_for"] == "203.0.113.9, 10.0.0.1"
    assert data["trust_proxy"] is False
    # Without trust_proxy, resolved_ip should not use XFF
    assert data["resolved_ip"] == data["client_host"]


def test_public_probe_trust_proxy(client: TestClient, webui_config):
    webui_config.trust_proxy = True
    resp = client.get(
        "/api/public/probe",
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["trust_proxy"] is True
    assert data["resolved_ip"] == "198.51.100.7"


def test_public_devices_lists_enabled_and_health(client: TestClient, aggregator: DummyAggregator):
    aggregator._forced["pc-1"] = _snap("pc-1", name="PC One")
    resp = client.get("/api/public/devices")
    body = resp.json()
    assert body["code"] == 0
    devices = {d["id"]: d for d in body["data"]["devices"]}
    assert set(devices) == {"pc-1", "pc-2"}
    assert devices["pc-1"]["online"] is True
    assert devices["pc-1"]["app"]["display_name"] == "VS Code"
    assert devices["pc-1"]["healthy"] is True


def test_index_served(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_admin_routes_absent(client: TestClient):
    for method, path in (
        ("GET", "/api/admin/config"),
        ("GET", "/api/admin/devices"),
        ("POST", "/api/admin/login"),
        ("GET", "/api/admin/stats"),
    ):
        resp = client.request(method, path)
        assert resp.status_code == 404, path


def test_device_history_not_found_returns_envelope(client: TestClient):
    resp = client.get("/api/public/devices/no-such/history")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 40400
    assert "message" in body


def test_stream_route_registered(webui_config, tmp_paths, aggregator):
    from fastapi.routing import APIRoute

    from server.main import create_app

    app = create_app(
        webui_config,
        config_path=tmp_paths["config"],
        aggregator=aggregator,
        start_aggregator=False,
    )
    paths = {getattr(r, "path", None) for r in app.routes if isinstance(r, APIRoute)}
    assert "/api/public/stream" in paths
    assert not any(p and p.startswith("/api/admin") for p in paths)
