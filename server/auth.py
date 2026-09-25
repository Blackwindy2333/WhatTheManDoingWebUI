"""Admin auth: token login, short-lived sessions, IP extraction, ban enforcement."""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from server.config import WebUIConfig
from server.log_setup import get_logger
from server.state import StateStore, utc_now_iso

logger = get_logger("auth")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime | None:
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def extract_client_ip(
    x_forwarded_for: str | None,
    x_real_ip: str | None,
    client_host: str | None,
    trust_proxy: bool,
) -> str:
    if trust_proxy:
        if x_forwarded_for:
            first = x_forwarded_for.split(",")[0].strip()
            if first:
                return first
        if x_real_ip and x_real_ip.strip():
            return x_real_ip.strip()
    return client_host or "unknown"


@dataclass
class Session:
    token: str
    created_at: float
    expires_at: float

    def is_valid(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < self.expires_at


class SessionManager:
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._sessions: dict[str, Session] = {}

    def set_ttl(self, ttl_seconds: int) -> None:
        with self._lock:
            self.ttl_seconds = ttl_seconds

    def create(self) -> Session:
        now = time.time()
        token = secrets.token_urlsafe(32)
        session = Session(token=token, created_at=now, expires_at=now + self.ttl_seconds)
        with self._lock:
            self._prune(now)
            self._sessions[token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = time.time()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(token)
            if session and session.is_valid(now):
                return session
            if session:
                self._sessions.pop(token, None)
            return None

    def revoke(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return self._sessions.pop(token, None) is not None

    def revoke_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _prune(self, now: float) -> None:
        expired = [t for t, s in self._sessions.items() if not s.is_valid(now)]
        for t in expired:
            self._sessions.pop(t, None)


class BanService:
    """Track login failures and ban IPs after threshold."""

    def __init__(self, store: StateStore):
        self.store = store

    def is_banned(self, ip: str) -> tuple[bool, str | None]:
        record = self.store.get_ban(ip)
        if not record:
            return False, None
        expires = parse_iso(record.expires_at)
        now = utc_now()
        # Unparseable expiry → treat as expired rather than permanent ban
        if expires is None or expires <= now:
            self.store.unban_ip(ip)
            return False, None
        return True, record.expires_at

    def register_failure(
        self,
        ip: str,
        max_failures: int,
        ban_duration_hours: int,
    ) -> dict[str, Any]:
        count = self.store.record_login_failure(ip)
        banned = False
        expires_at: str | None = None
        if count >= max_failures:
            now = utc_now()
            expires = now + timedelta(hours=ban_duration_hours)
            expires_at = expires.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self.store.ban_ip(ip, utc_now_iso(), expires_at)
            self.store.reset_login_failures(ip)
            banned = True
            logger.warning("ban ip=%s until=%s reason=login_failures", ip, expires_at)
        return {"failures": count, "banned": banned, "expires_at": expires_at}

    def clear_failures(self, ip: str) -> None:
        self.store.reset_login_failures(ip)


class AdminAuthenticator:
    def __init__(
        self,
        config: WebUIConfig,
        store: StateStore,
        sessions: SessionManager | None = None,
        ban_service: BanService | None = None,
    ):
        self.config = config
        self.store = store
        self.sessions = sessions or SessionManager(config.session_ttl_seconds)
        self.bans = ban_service or BanService(store)
        # simple in-memory sliding window for login attempts
        self._login_hits: dict[str, list[float]] = {}
        self._login_lock = threading.RLock()

    def update_config(self, config: WebUIConfig) -> None:
        self.config = config
        self.sessions.set_ttl(config.session_ttl_seconds)

    def login_rate_limited(self, ip: str) -> bool:
        window = 60.0
        now = time.time()
        limit = self.config.login_rate_limit_per_minute
        with self._login_lock:
            hits = [ts for ts in self._login_hits.get(ip, []) if now - ts < window]
            limited = len(hits) >= limit
            hits.append(now)
            self._login_hits[ip] = hits
            return limited

    def check_password(self, token: str) -> bool:
        return hmac.compare_digest(token.encode("utf-8"), self.config.admin_token.encode("utf-8"))

    def login(self, ip: str, token: str) -> dict[str, Any]:
        """Attempt login. Returns result dict; may raise via status flags."""
        banned, expires_at = self.bans.is_banned(ip)
        if banned:
            logger.warning("login blocked (banned) ip=%s until=%s", ip, expires_at)
            return {
                "ok": False,
                "status": 403,
                "message": "IP banned",
                "banned": True,
                "expires_at": expires_at,
            }
        if self.login_rate_limited(ip):
            logger.warning("login rate limited ip=%s", ip)
            return {
                "ok": False,
                "status": 429,
                "message": "too many login attempts, slow down",
                "banned": False,
                "expires_at": None,
            }
        if not self.check_password(token or ""):
            outcome = self.bans.register_failure(
                ip,
                self.config.login_max_failures,
                self.config.ban_duration_hours,
            )
            self.store.append_audit(ip, "login_failed", f"failures={outcome['failures']}")
            logger.warning(
                "login failed ip=%s failures=%s banned=%s",
                ip,
                outcome["failures"],
                outcome["banned"],
            )
            return {
                "ok": False,
                "status": 401,
                "message": "invalid admin token",
                "banned": outcome["banned"],
                "expires_at": outcome["expires_at"],
                "failures": outcome["failures"],
            }
        self.bans.clear_failures(ip)
        session = self.sessions.create()
        self.store.append_audit(ip, "login_ok")
        logger.info("login ok ip=%s", ip)
        return {
            "ok": True,
            "status": 200,
            "message": "ok",
            "session_token": session.token,
            "expires_at": datetime.fromtimestamp(session.expires_at, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }

    def logout(self, ip: str, session_token: str | None) -> None:
        self.sessions.revoke(session_token)
        self.store.append_audit(ip, "logout")
        logger.info("logout ip=%s", ip)

    def require_session(self, authorization: str | None) -> Session | None:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return self.sessions.get(token)
