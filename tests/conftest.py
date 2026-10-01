"""Pytest fixtures for WebUI tests (no live server)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.aggregate import DeviceAggregator, DeviceSnapshot
from server.config import DeviceConfig, WebUIConfig, ensure_config
from server.main import create_app


@pytest.fixture()
def tmp_paths(tmp_path):
    config_path = tmp_path / "config.json"
    return {"config": config_path}


@pytest.fixture()
def webui_config(tmp_paths) -> WebUIConfig:
    cfg = ensure_config(tmp_paths["config"])
    cfg.refresh_interval_seconds = 5
    cfg.trust_proxy = False
    cfg.devices = [
        DeviceConfig(
            id="pc-1",
            name="PC One",
            api_base_url="http://127.0.0.1:8765/api/v1",
            api_token="api-token-1",
            enabled=True,
        ),
        DeviceConfig(
            id="pc-2",
            name="PC Two",
            api_base_url="https://example.test/api/v1",
            api_token="",
            enabled=False,
        ),
    ]
    return cfg


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
def client(webui_config, tmp_paths, aggregator) -> TestClient:
    app = create_app(
        webui_config,
        config_path=tmp_paths["config"],
        aggregator=aggregator,
        start_aggregator=False,
    )
    with TestClient(app) as c:
        yield c
