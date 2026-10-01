"""Config load/validate/ensure tests."""

from __future__ import annotations

import json

import pytest

from server.config import (
    ConfigError,
    default_config,
    ensure_config,
    load_config,
    save_config,
    validate_config_dict,
)


def test_ensure_config_creates_default(tmp_path):
    path = tmp_path / "config.json"
    cfg = ensure_config(path)
    assert path.exists()
    assert cfg.serve.mode == "http"
    assert any(d.id == "my-pc" for d in cfg.devices)
    assert not hasattr(cfg, "admin_token")


def test_ensure_config_loads_existing(tmp_path):
    path = tmp_path / "config.json"
    cfg = default_config()
    cfg.page_title = "自定义"
    cfg.devices[0].api_token = "secret"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.page_title == "自定义"
    assert loaded.devices[0].api_token == "secret"


def test_validate_rejects_duplicate_device_ids():
    data = {
        "version": 1,
        "devices": [
            {"id": "a", "name": "A", "api_base_url": "http://x/api/v1", "api_token": "", "enabled": True},
            {"id": "a", "name": "B", "api_base_url": "http://x/api/v1", "api_token": "", "enabled": True},
        ],
    }
    with pytest.raises(ConfigError, match="duplicate"):
        validate_config_dict(data)


def test_validate_rejects_bad_scheme():
    data = {
        "version": 1,
        "devices": [
            {"id": "a", "name": "A", "api_base_url": "ftp://x/api/v1", "api_token": "", "enabled": True},
        ],
    }
    with pytest.raises(ConfigError, match="http"):
        validate_config_dict(data)


def test_validate_rejects_https_without_certs():
    data = {
        "version": 1,
        "serve": {"mode": "https", "host": "0.0.0.0", "port": 8443},
        "devices": [],
    }
    with pytest.raises(ConfigError, match="ssl"):
        validate_config_dict(data)


def test_validate_refresh_bounds():
    base = {"version": 1, "devices": []}
    with pytest.raises(ConfigError, match="refresh_interval"):
        validate_config_dict({**base, "refresh_interval_seconds": 0})
    with pytest.raises(ConfigError, match="refresh_interval"):
        validate_config_dict({**base, "refresh_interval_seconds": 61})


def test_save_load_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = default_config()
    cfg.trust_proxy = True
    cfg.forwarded_allow_ips = "127.0.0.1,10.0.0.1"
    save_config(cfg, path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["trust_proxy"] is True
    assert raw["forwarded_allow_ips"] == "127.0.0.1,10.0.0.1"
    assert "admin_token" not in raw
    assert "default_device_scheme" not in raw
    loaded = load_config(path)
    assert loaded.trust_proxy is True
    assert loaded.forwarded_allow_ips == "127.0.0.1,10.0.0.1"


def test_validate_rejects_empty_forwarded_allow_ips():
    data = {
        "version": 1,
        "forwarded_allow_ips": "  ",
        "devices": [],
    }
    with pytest.raises(ConfigError, match="forwarded_allow_ips"):
        validate_config_dict(data)


def test_validate_rejects_admin_keys():
    for key in (
        "admin_token",
        "session_ttl_seconds",
        "login_max_failures",
        "ban_duration_hours",
        "login_rate_limit_per_minute",
        "default_device_scheme",
    ):
        with pytest.raises(ConfigError, match="no longer supported"):
            validate_config_dict({"version": 1, key: "x", "devices": []})


def test_validate_rejects_viewer_token():
    data = {
        "version": 1,
        "devices": [
            {"id": "a", "name": "A", "api_base_url": "http://x/api/v1", "viewer_token": "old", "enabled": True},
        ],
    }
    with pytest.raises(ConfigError, match="api_token"):
        validate_config_dict(data)


def test_device_api_token_default_empty():
    data = {
        "version": 1,
        "devices": [
            {"id": "a", "name": "A", "api_base_url": "http://x/api/v1", "enabled": True},
        ],
    }
    cfg = validate_config_dict(data)
    assert cfg.devices[0].api_token == ""
