"""Pytest fixtures for WebUI tests (no live server)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.aggregate import DeviceAggregator, DeviceSnapshot
from server.auth import AdminAuthenticator
from server.config import DeviceConfig, WebUIConfig, ensure_config
from server.main import create_app
from server.state import StateStore


@pytest.fixture()
def tmp_paths(tmp_path):
    config_path = tmp_path / "config.json"
    state_path = tmp_path / "state.json"
    return {"config": config_path, "state": state_path}


@pytest.fixture()
def webui_config(tmp_paths) -> WebUIConfig:
    cfg = ensure_config(tmp_paths["config"])
    cfg.admin_token = "test-admin-token"
    cfg.login_max_failures = 3
    cfg.ban_duration_hours = 24
    cfg.login_rate_limit_per_minute = 50
    cfg.refresh_interval_seconds = 5
    cfg.trust_proxy = False
    cfg.devices = [
        DeviceConfig(
            id="pc-1",
            name="PC One",
            api_base_url="http://127.0.0.1:8765/api/v1",
            viewer_token="view-token",
            enabled=True,
        ),
        DeviceConfig(
            id="pc-2",
            name="PC Two",
            api_base_url="https://example.test/api/v1",
            viewer_token="",
            enabled=False,
        ),
    ]
    return cfg


@pytest.fixture()
def state_store(tmp_paths) -> StateStore:
    return StateStore(tmp_paths["state"])


@pytest.fixture()
def authenticator(webui_config, state_store) -> AdminAuthenticator:
    return AdminAuthenticator(webui_config, state_store)


class DummyAggregator(DeviceAggregator):
    def __init__(self, config, snapshots: list[DeviceSnapshot] | None = None):
        super().__init__(config, fetcher=None)
        self._forced = {s.id: s for s in (snapshots or [])}

    def get_snapshots(self):
        out = []
        for device in self.config.devices:
            existing = self._forced.get(device.id)
            if existing:
                out.append(existing)
            else:
                out.append(DeviceSnapshot(id=device.id, name=device.name, enabled=device.enabled))
        return out

    async def refresh_once(self):
        return self.get_snapshots()


@pytest.fixture()
def aggregator(webui_config) -> DummyAggregator:
    return DummyAggregator(webui_config)


@pytest.fixture()
def client(webui_config, tmp_paths, state_store, authenticator, aggregator) -> TestClient:
    app = create_app(
        webui_config,
        config_path=tmp_paths["config"],
        state_store=state_store,
        aggregator=aggregator,
        authenticator=authenticator,
        start_aggregator=False,
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_headers(client, webui_config):
    resp = client.post("/api/admin/login", json={"token": webui_config.admin_token})
    assert resp.status_code == 200
    token = resp.json()["data"]["session_token"]
    return {"Authorization": f"Bearer {token}"}
