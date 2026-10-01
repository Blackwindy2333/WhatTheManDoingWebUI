"""Logging setup tests."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from server.config import ConfigError, LogConfig, validate_config_dict
from server.log_setup import (
    get_logger,
    reset_logging_state,
    resolve_log_path,
    setup_logging,
    tail_file,
    tail_logs,
)


def test_validate_log_config_defaults():
    cfg = validate_config_dict({"version": 1, "devices": []})
    assert cfg.log.level == "INFO"
    assert cfg.log.dir == "logs"
    assert cfg.log.filename == "webui.log"
    assert cfg.log.access_log is True


def test_validate_log_config_rejects_bad_level():
    try:
        validate_config_dict(
            {
                "version": 1,
                "log": {"level": "VERBOSE"},
                "devices": [],
            }
        )
        raise AssertionError("expected ConfigError")
    except ConfigError as exc:
        assert "log.level" in str(exc)


def test_validate_log_config_rejects_tiny_rotation():
    try:
        validate_config_dict(
            {
                "version": 1,
                "log": {"max_bytes": 10},
                "devices": [],
            }
        )
        raise AssertionError("expected ConfigError")
    except ConfigError as exc:
        assert "max_bytes" in str(exc)


def test_setup_logging_writes_file(tmp_path):
    reset_logging_state()
    try:
        log_cfg = LogConfig(level="INFO", dir="logs", filename="webui.log", console=False)
        path = setup_logging(log_cfg, base=tmp_path, force=True)
        assert path == tmp_path / "logs" / "webui.log"
        logger = get_logger("test")
        logger.info("hello log")
        for handler in logging.getLogger("webui").handlers:
            handler.flush()
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "hello log" in content
        assert "webui.test" in content
    finally:
        reset_logging_state()


def test_tail_file_and_tail_logs(tmp_path):
    reset_logging_state()
    try:
        log_cfg = LogConfig(dir="logs", filename="webui.log", console=False)
        path = setup_logging(log_cfg, base=tmp_path, force=True)
        logger = get_logger("tail")
        for i in range(5):
            logger.info("line-%s", i)
        for handler in logging.getLogger("webui").handlers:
            handler.flush()
        lines = tail_file(path, lines=3)
        assert len(lines) == 3
        assert "line-4" in lines[-1]
        payload = tail_logs(lines=2, path=path)
        assert payload["count"] == 2
        assert payload["path"] == str(path)
    finally:
        reset_logging_state()


def test_resolve_log_path_absolute(tmp_path):
    cfg = LogConfig(dir=str(tmp_path), filename="x.log")
    assert resolve_log_path(cfg, base=Path("/should/not/matter")) == tmp_path / "x.log"


def test_access_log_records_requests(client: TestClient, tmp_paths):
    client.get("/api/public/config")
    # Access logging writes to the config-adjacent logs dir created by create_app
    log_path = tmp_paths["config"].parent / "logs" / "webui.log"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "request" in content
    assert "/api/public/config" in content
