"""Persistent runtime state: visit counts, IP bans, audit log."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.config import DEFAULT_STATE_PATH

_AUDIT_MAX = 200


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class BanRecord:
    ip: str
    banned_at: str
    expires_at: str
    reason: str = "login_failures"


@dataclass
class AuditEntry:
    ts: str
    ip: str
    action: str
    detail: str = ""


@dataclass
class RuntimeState:
    total_visits: int = 0
    visits_by_date: dict[str, int] = field(default_factory=dict)
    login_failures: dict[str, int] = field(default_factory=dict)
    bans: dict[str, BanRecord] = field(default_factory=dict)
    audit: list[AuditEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_visits": self.total_visits,
            "visits_by_date": dict(self.visits_by_date),
            "login_failures": dict(self.login_failures),
            "bans": {ip: asdict(rec) for ip, rec in self.bans.items()},
            "audit": [asdict(e) for e in self.audit],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeState:
        bans: dict[str, BanRecord] = {}
        for ip, raw in (data.get("bans") or {}).items():
            if not isinstance(raw, dict):
                continue
            bans[ip] = BanRecord(
                ip=raw.get("ip", ip),
                banned_at=str(raw.get("banned_at", "")),
                expires_at=str(raw.get("expires_at", "")),
                reason=str(raw.get("reason", "login_failures")),
            )
        audit: list[AuditEntry] = []
        for raw in data.get("audit") or []:
            if not isinstance(raw, dict):
                continue
            audit.append(
                AuditEntry(
                    ts=str(raw.get("ts", "")),
                    ip=str(raw.get("ip", "")),
                    action=str(raw.get("action", "")),
                    detail=str(raw.get("detail", "")),
                )
            )
        failures_raw = data.get("login_failures") or {}
        failures = {
            str(ip): int(count)
            for ip, count in failures_raw.items()
            if isinstance(count, int) and not isinstance(count, bool)
        }
        visits_by_date = {
            str(day): int(count)
            for day, count in (data.get("visits_by_date") or {}).items()
            if isinstance(count, int) and not isinstance(count, bool)
        }
        total = data.get("total_visits", 0)
        return cls(
            total_visits=total if isinstance(total, int) and not isinstance(total, bool) else 0,
            visits_by_date=visits_by_date,
            login_failures=failures,
            bans=bans,
            audit=audit[-_AUDIT_MAX:],
        )


class StateStore:
    """Thread-safe state store with JSON persistence."""

    def __init__(self, path: Path | str = DEFAULT_STATE_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._state = self._load()

    def _load(self) -> RuntimeState:
        if not self.path.exists():
            return RuntimeState()
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                return RuntimeState()
            return RuntimeState.from_dict(data)
        except (OSError, json.JSONDecodeError):
            return RuntimeState()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(self._state.to_dict(), fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    def snapshot(self) -> RuntimeState:
        with self._lock:
            return RuntimeState.from_dict(self._state.to_dict())

    def record_visit(self, day: str) -> None:
        with self._lock:
            self._state.total_visits += 1
            self._state.visits_by_date[day] = self._state.visits_by_date.get(day, 0) + 1
            self._save()

    def get_visits(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_visits": self._state.total_visits,
                "visits_by_date": dict(self._state.visits_by_date),
            }

    def record_login_failure(self, ip: str) -> int:
        with self._lock:
            count = self._state.login_failures.get(ip, 0) + 1
            self._state.login_failures[ip] = count
            self._save()
            return count

    def reset_login_failures(self, ip: str) -> None:
        with self._lock:
            self._state.login_failures.pop(ip, None)
            self._save()

    def get_login_failures(self, ip: str) -> int:
        with self._lock:
            return self._state.login_failures.get(ip, 0)

    def ban_ip(self, ip: str, banned_at: str, expires_at: str, reason: str = "login_failures") -> None:
        with self._lock:
            self._state.bans[ip] = BanRecord(
                ip=ip,
                banned_at=banned_at,
                expires_at=expires_at,
                reason=reason,
            )
            self._save()

    def unban_ip(self, ip: str) -> bool:
        with self._lock:
            existed = self._state.bans.pop(ip, None) is not None
            if existed:
                self._save()
            return existed

    def list_bans(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(rec) for rec in self._state.bans.values()]

    def get_ban(self, ip: str) -> BanRecord | None:
        with self._lock:
            return self._state.bans.get(ip)

    def prune_expired_bans(self, now_iso: str) -> list[str]:
        with self._lock:
            expired = [ip for ip, rec in self._state.bans.items() if rec.expires_at <= now_iso]
            for ip in expired:
                self._state.bans.pop(ip, None)
            if expired:
                self._save()
            return expired

    def append_audit(self, ip: str, action: str, detail: str = "", ts: str | None = None) -> None:
        with self._lock:
            self._state.audit.append(
                AuditEntry(ts=ts or utc_now_iso(), ip=ip, action=action, detail=detail)
            )
            self._state.audit = self._state.audit[-_AUDIT_MAX:]
            self._save()

    def list_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            entries = [asdict(e) for e in self._state.audit]
        return entries[-limit:]
