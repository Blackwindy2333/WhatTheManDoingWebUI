"""WebUI configuration: load, validate, persist, create defaults when missing."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config.json"
DEFAULT_STATE_PATH = ROOT / "state.json"

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ConfigError(ValueError):
    """Raised when WebUI configuration is invalid."""


@dataclass
class ServeConfig:
    mode: str = "http"
    host: str = "127.0.0.1"
    port: int = 8080
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None


@dataclass
class DeviceConfig:
    id: str = "my-pc"
    name: str = "My PC"
    api_base_url: str = "http://127.0.0.1:8765/api/v1"
    viewer_token: str = ""
    enabled: bool = True


@dataclass
class WebUIConfig:
    version: int = 1
    admin_token: str = "change-me"
    session_ttl_seconds: int = 3600
    login_max_failures: int = 5
    ban_duration_hours: int = 24
    login_rate_limit_per_minute: int = 10
    refresh_interval_seconds: int = 5
    page_title: str = "在干什么"
    page_subtitle: str = "What The Man Doing"
    show_history: bool = True
    devices_per_page: int = 12
    trust_proxy: bool = False
    default_device_scheme: str = "http"
    serve: ServeConfig = field(default_factory=ServeConfig)
    devices: list[DeviceConfig] = field(default_factory=list)


def default_config() -> WebUIConfig:
    return WebUIConfig(
        devices=[
            DeviceConfig(
                id="my-pc",
                name="My PC",
                api_base_url="http://127.0.0.1:8765/api/v1",
                viewer_token="",
                enabled=True,
            )
        ]
    )


def _require_type(data: dict[str, Any], key: str, expected: type) -> Any:
    if key not in data:
        raise ConfigError(f"missing required config key: {key}")
    value = data[key]
    if expected is int and isinstance(value, bool):
        raise ConfigError(f"config key {key!r} must be int, got bool")
    if expected is bool and not isinstance(value, bool):
        raise ConfigError(f"config key {key!r} must be bool")
    if expected is not bool and not isinstance(value, expected):
        raise ConfigError(
            f"config key {key!r} must be {expected.__name__}, got {type(value).__name__}"
        )
    return value


def _validate_device(raw: Any, index: int) -> DeviceConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"devices[{index}] must be an object")
    device_id = raw.get("id")
    if not isinstance(device_id, str) or not _ID_RE.match(device_id):
        raise ConfigError(f"devices[{index}].id must match {_ID_RE.pattern}")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"devices[{index}].name must be a non-empty string")
    api_base_url = raw.get("api_base_url")
    if not isinstance(api_base_url, str) or not api_base_url.strip():
        raise ConfigError(f"devices[{index}].api_base_url must be a non-empty string")
    if not api_base_url.startswith(("http://", "https://")):
        raise ConfigError(f"devices[{index}].api_base_url must start with http:// or https://")
    viewer_token = raw.get("viewer_token", "")
    if not isinstance(viewer_token, str):
        raise ConfigError(f"devices[{index}].viewer_token must be a string")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"devices[{index}].enabled must be a bool")
    return DeviceConfig(
        id=device_id,
        name=name.strip(),
        api_base_url=api_base_url.strip().rstrip("/"),
        viewer_token=viewer_token,
        enabled=enabled,
    )


def validate_config_dict(data: dict[str, Any]) -> WebUIConfig:
    if not isinstance(data, dict):
        raise ConfigError("config root must be a JSON object")

    version = data.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ConfigError("version must be an int >= 1")

    admin_token = _require_type(data, "admin_token", str)
    if not admin_token.strip():
        raise ConfigError("admin_token must be non-empty")

    session_ttl_seconds = data.get("session_ttl_seconds", 3600)
    if isinstance(session_ttl_seconds, bool) or not isinstance(session_ttl_seconds, int):
        raise ConfigError("session_ttl_seconds must be int")
    if session_ttl_seconds < 60 or session_ttl_seconds > 7 * 24 * 3600:
        raise ConfigError("session_ttl_seconds must be between 60 and 604800")

    login_max_failures = data.get("login_max_failures", 5)
    if isinstance(login_max_failures, bool) or not isinstance(login_max_failures, int):
        raise ConfigError("login_max_failures must be int")
    if login_max_failures < 1 or login_max_failures > 100:
        raise ConfigError("login_max_failures must be between 1 and 100")

    ban_duration_hours = data.get("ban_duration_hours", 24)
    if isinstance(ban_duration_hours, bool) or not isinstance(ban_duration_hours, int):
        raise ConfigError("ban_duration_hours must be int")
    if ban_duration_hours < 1 or ban_duration_hours > 24 * 30:
        raise ConfigError("ban_duration_hours must be between 1 and 720")

    login_rate_limit_per_minute = data.get("login_rate_limit_per_minute", 10)
    if (
        isinstance(login_rate_limit_per_minute, bool)
        or not isinstance(login_rate_limit_per_minute, int)
    ):
        raise ConfigError("login_rate_limit_per_minute must be int")
    if login_rate_limit_per_minute < 1 or login_rate_limit_per_minute > 1000:
        raise ConfigError("login_rate_limit_per_minute must be between 1 and 1000")

    refresh_interval_seconds = data.get("refresh_interval_seconds", 5)
    if isinstance(refresh_interval_seconds, bool) or not isinstance(refresh_interval_seconds, int):
        raise ConfigError("refresh_interval_seconds must be int")
    if refresh_interval_seconds < 1 or refresh_interval_seconds > 60:
        raise ConfigError("refresh_interval_seconds must be between 1 and 60")

    page_title = data.get("page_title", "在干什么")
    page_subtitle = data.get("page_subtitle", "What The Man Doing")
    if not isinstance(page_title, str) or not page_title.strip():
        raise ConfigError("page_title must be a non-empty string")
    if not isinstance(page_subtitle, str):
        raise ConfigError("page_subtitle must be a string")

    show_history = data.get("show_history", True)
    if not isinstance(show_history, bool):
        raise ConfigError("show_history must be a bool")

    devices_per_page = data.get("devices_per_page", 12)
    if isinstance(devices_per_page, bool) or not isinstance(devices_per_page, int):
        raise ConfigError("devices_per_page must be int")
    if devices_per_page < 1 or devices_per_page > 100:
        raise ConfigError("devices_per_page must be between 1 and 100")

    trust_proxy = data.get("trust_proxy", False)
    if not isinstance(trust_proxy, bool):
        raise ConfigError("trust_proxy must be a bool")

    default_device_scheme = data.get("default_device_scheme", "http")
    if default_device_scheme not in ("http", "https"):
        raise ConfigError("default_device_scheme must be http or https")

    serve_raw = data.get("serve", {})
    if not isinstance(serve_raw, dict):
        raise ConfigError("serve must be an object")
    serve_mode = serve_raw.get("mode", "http")
    if serve_mode not in ("http", "https"):
        raise ConfigError("serve.mode must be http or https")
    serve_host = serve_raw.get("host", "127.0.0.1")
    if not isinstance(serve_host, str) or not serve_host.strip():
        raise ConfigError("serve.host must be a non-empty string")
    serve_port = serve_raw.get("port", 8080)
    if isinstance(serve_port, bool) or not isinstance(serve_port, int):
        raise ConfigError("serve.port must be int")
    if not (1 <= serve_port <= 65535):
        raise ConfigError("serve.port must be between 1 and 65535")
    ssl_certfile = serve_raw.get("ssl_certfile")
    ssl_keyfile = serve_raw.get("ssl_keyfile")
    for key, val in (("ssl_certfile", ssl_certfile), ("ssl_keyfile", ssl_keyfile)):
        if val is not None and not isinstance(val, str):
            raise ConfigError(f"serve.{key} must be a string or null")
    if serve_mode == "https" and (not ssl_certfile or not ssl_keyfile):
        raise ConfigError("serve.mode https requires ssl_certfile and ssl_keyfile")

    devices_raw = data.get("devices", [])
    if not isinstance(devices_raw, list):
        raise ConfigError("devices must be a list")
    devices: list[DeviceConfig] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(devices_raw):
        device = _validate_device(raw, i)
        if device.id in seen_ids:
            raise ConfigError(f"duplicate device id: {device.id}")
        seen_ids.add(device.id)
        devices.append(device)

    return WebUIConfig(
        version=version,
        admin_token=admin_token,
        session_ttl_seconds=session_ttl_seconds,
        login_max_failures=login_max_failures,
        ban_duration_hours=ban_duration_hours,
        login_rate_limit_per_minute=login_rate_limit_per_minute,
        refresh_interval_seconds=refresh_interval_seconds,
        page_title=page_title.strip(),
        page_subtitle=page_subtitle,
        show_history=show_history,
        devices_per_page=devices_per_page,
        trust_proxy=trust_proxy,
        default_device_scheme=default_device_scheme,
        serve=ServeConfig(
            mode=serve_mode,
            host=serve_host.strip(),
            port=serve_port,
            ssl_certfile=ssl_certfile or None,
            ssl_keyfile=ssl_keyfile or None,
        ),
        devices=devices,
    )


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> WebUIConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return validate_config_dict(data)


def save_config(config: WebUIConfig, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    # Normalize Nones for JSON readability
    serve = payload.get("serve") or {}
    for key in ("ssl_certfile", "ssl_keyfile"):
        if serve.get(key) is None:
            serve[key] = None
    payload["serve"] = serve
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def ensure_config(path: Path | str = DEFAULT_CONFIG_PATH) -> WebUIConfig:
    """Load config from path; create with defaults when missing."""
    path = Path(path)
    if path.exists():
        return load_config(path)
    config = default_config()
    save_config(config, path)
    return config


def config_to_public_dict(config: WebUIConfig) -> dict[str, Any]:
    """Safe subset for unauthenticated viewers."""
    return {
        "page_title": config.page_title,
        "page_subtitle": config.page_subtitle,
        "refresh_interval_seconds": config.refresh_interval_seconds,
        "show_history": config.show_history,
    }


def config_to_admin_dict(config: WebUIConfig) -> dict[str, Any]:
    """Full config for admin UI, without secrets that should not round-trip casually."""
    data = asdict(config)
    # Keep admin_token out of GET responses; updates send a replacement when changing it.
    data.pop("admin_token", None)
    data["admin_token_set"] = bool(config.admin_token)
    return data
