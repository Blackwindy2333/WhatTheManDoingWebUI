"""Public and admin HTTP API tests via TestClient (no live process)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.aggregate import DeviceSnapshot
from server.config import DeviceConfig
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


def test_admin_login_wrong_token(client: TestClient):
    resp = client.post("/api/admin/login", json={"token": "nope"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 40100


def test_admin_login_and_me(client: TestClient, webui_config, admin_headers):
    me = client.get("/api/admin/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["data"]["authenticated"] is True


def test_admin_requires_session(client: TestClient):
    resp = client.get("/api/admin/devices")
    assert resp.status_code == 401


def test_device_crud(client: TestClient, admin_headers):
    created = client.post(
        "/api/admin/devices",
        headers=admin_headers,
        json={
            "id": "lap-1",
            "name": "Laptop",
            "api_base_url": "lap.example:8765/api/v1",
            "viewer_token": "",
            "enabled": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["data"]["api_base_url"] == "http://lap.example:8765/api/v1"

    listed = client.get("/api/admin/devices", headers=admin_headers).json()
    ids = [d["id"] for d in listed["data"]["devices"]]
    assert "lap-1" in ids

    updated = client.put(
        "/api/admin/devices/lap-1",
        headers=admin_headers,
        json={
            "id": "lap-1",
            "name": "Laptop Renamed",
            "api_base_url": "https://lap.example/api/v1",
            "enabled": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "Laptop Renamed"
    assert updated.json()["data"]["api_base_url"].startswith("https://")

    deleted = client.delete("/api/admin/devices/lap-1", headers=admin_headers)
    assert deleted.status_code == 200
    listed2 = client.get("/api/admin/devices", headers=admin_headers).json()
    assert "lap-1" not in [d["id"] for d in listed2["data"]["devices"]]


def test_device_create_duplicate_rejected(client: TestClient, admin_headers):
    resp = client.post(
        "/api/admin/devices",
        headers=admin_headers,
        json={"id": "pc-1", "name": "dup", "api_base_url": "http://x/api/v1"},
    )
    assert resp.status_code == 400


def test_update_settings(client: TestClient, admin_headers):
    resp = client.patch(
        "/api/admin/config",
        headers=admin_headers,
        json={"page_title": "新标题", "refresh_interval_seconds": 8},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["page_title"] == "新标题"
    assert data["refresh_interval_seconds"] == 8
    public = client.get("/api/public/config").json()["data"]
    assert public["page_title"] == "新标题"
    assert public["refresh_interval_seconds"] == 8


def test_update_serve_connection_mode(client: TestClient, admin_headers):
    resp = client.patch(
        "/api/admin/config",
        headers=admin_headers,
        json={
            "serve": {
                "mode": "https",
                "host": "0.0.0.0",
                "port": 8443,
                "ssl_certfile": "certs/c.pem",
                "ssl_keyfile": "certs/k.pem",
            },
            "default_device_scheme": "https",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["serve"]["mode"] == "https"
    assert data["serve"]["port"] == 8443
    assert data["default_device_scheme"] == "https"


def test_stats_visits(client: TestClient, admin_headers):
    client.get("/api/public/devices")
    client.get("/api/public/devices")
    resp = client.get("/api/admin/stats", headers=admin_headers)
    data = resp.json()["data"]
    assert data["total_visits"] >= 2
    assert data["visits_by_date"]


def test_export_devices(client: TestClient, admin_headers):
    resp = client.get("/api/admin/devices/export", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "devices" in data
    assert any(d["id"] == "pc-1" for d in data["devices"])


def test_audit_after_admin_actions(client: TestClient, admin_headers):
    client.patch("/api/admin/config", headers=admin_headers, json={"page_subtitle": "x"})
    resp = client.get("/api/admin/audit", headers=admin_headers)
    actions = [e["action"] for e in resp.json()["data"]["entries"]]
    assert "update_config" in actions


def test_unban_endpoint(client: TestClient, admin_headers, state_store):
    state_store.ban_ip("5.5.5.5", "2026-01-01T00:00:00Z", "2099-01-01T00:00:00Z")
    listed = client.get("/api/admin/bans", headers=admin_headers).json()["data"]["bans"]
    assert any(b["ip"] == "5.5.5.5" for b in listed)
    unban = client.delete("/api/admin/bans/5.5.5.5", headers=admin_headers)
    assert unban.status_code == 200
    assert unban.json()["data"]["unbanned"] is True


def test_banned_ip_rejected(client: TestClient, state_store):
    # TestClient uses "testclient" as host by default
    state_store.ban_ip("testclient", "2026-01-01T00:00:00Z", "2099-01-01T00:00:00Z")
    resp = client.get("/api/public/devices")
    assert resp.status_code == 403
    assert resp.json()["code"] == 40300


def test_logout(client: TestClient, admin_headers):
    resp = client.post("/api/admin/logout", headers=admin_headers)
    assert resp.status_code == 200
    me = client.get("/api/admin/me", headers=admin_headers)
    assert me.status_code == 401
