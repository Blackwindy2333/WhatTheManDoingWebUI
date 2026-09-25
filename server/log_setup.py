"""Logging setup: console + rotating file, helpers for access/business logs."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Iterable

from server.config import LogConfig

ROOT = Path(__file__).resolve().parents[1]

LOGGER_NAME = "webui"
_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_configured = False
_log_file_path: Path | None = None


def resolve_log_path(log_cfg: LogConfig, base: Path | None = None) -> Path:
    root = base or ROOT
    directory = Path(log_cfg.dir)
    if not directory.is_absolute():
        directory = root / directory
    return directory / log_cfg.filename


def setup_logging(log_cfg: LogConfig, *, base: Path | None = None, force: bool = False) -> Path:
    """Configure root webui logger. Safe to call multiple times with force=True."""
    global _configured, _log_file_path

    log_path = resolve_log_path(log_cfg, base)
    level = _LEVELS.get(log_cfg.level.upper(), logging.INFO)

    root_logger = logging.getLogger(LOGGER_NAME)
    if force or not _configured:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.setLevel(level)
        root_logger.propagate = False

        formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=log_cfg.max_bytes,
            backupCount=log_cfg.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)

        if log_cfg.console:
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            console.setLevel(level)
            root_logger.addHandler(console)

        # Quiet noisy third-party loggers to WARNING by default
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

        _configured = True
    else:
        root_logger.setLevel(level)
        for handler in root_logger.handlers:
            handler.setLevel(level)

    _log_file_path = log_path
    return log_path


def get_logger(child: str | None = None) -> logging.Logger:
    name = LOGGER_NAME if not child else f"{LOGGER_NAME}.{child}"
    return logging.getLogger(name)


def current_log_path() -> Path | None:
    return _log_file_path


def tail_file(path: Path | str, lines: int = 100) -> list[str]:
    """Return the last N non-empty-safe lines from a text file (UTF-8)."""
    path = Path(path)
    if lines < 1:
        return []
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    all_lines = text.splitlines()
    return all_lines[-lines:]


def tail_logs(lines: int = 100, path: Path | str | None = None) -> dict:
    log_path = Path(path) if path else _log_file_path
    if log_path is None:
        return {"path": None, "lines": [], "count": 0}
    entries = tail_file(log_path, lines)
    return {"path": str(log_path), "lines": entries, "count": len(entries)}


def reset_logging_state() -> None:
    """Test helper: clear module configuration flag and close handlers."""
    global _configured, _log_file_path
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass
    _configured = False
    _log_file_path = None
