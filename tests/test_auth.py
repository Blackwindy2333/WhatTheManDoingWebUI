"""Auth, session, ban and IP extraction tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from server.auth import (
    AdminAuthenticator,
    BanService,
    extract_client_ip,
    parse_iso,
    utc_now,
)
from server.state import StateStore, utc_now_iso


def test_extract_client_ip_untrusted():
    ip = extract_client_ip("9.9.9.9", "8.8.8.8", "1.2.3.4", trust_proxy=False)
    assert ip == "1.2.3.4"


def test_extract_client_ip_trust_proxy():
    ip = extract_client_ip("9.9.9.9, 7.7.7.7", "8.8.8.8", "1.2.3.4", trust_proxy=True)
    assert ip == "9.9.9.9"


def test_login_success_and_session(authenticator, webui_config):
    result = authenticator.login("10.0.0.1", webui_config.admin_token)
    assert result["ok"] is True
    assert result["session_token"]
    session = authenticator.sessions.get(result["session_token"])
    assert session is not None


def test_login_wrong_token_counts_failures(authenticator):
    ip = "10.0.0.2"
    for i in range(2):
        result = authenticator.login(ip, "wrong")
        assert result["ok"] is False
        assert result["failures"] == i + 1
        assert result["banned"] is False


def test_login_bans_after_max_failures(authenticator, webui_config):
    ip = "10.0.0.3"
    for _ in range(webui_config.login_max_failures - 1):
        authenticator.login(ip, "wrong")
    result = authenticator.login(ip, "wrong")
    assert result["banned"] is True
    assert result["expires_at"]
    banned, expires = authenticator.bans.is_banned(ip)
    assert banned is True
    # further login attempts blocked
    blocked = authenticator.login(ip, webui_config.admin_token)
    assert blocked["ok"] is False
    assert blocked["status"] == 403


def test_ban_expiry(tmp_path):
    store = StateStore(tmp_path / "state.json")
    bans = BanService(store)
    past = (utc_now() - timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    store.ban_ip("1.1.1.1", utc_now_iso(), past)
    banned, _ = bans.is_banned("1.1.1.1")
    assert banned is False


def test_parse_iso():
    dt = parse_iso("2026-01-01T12:00:00Z")
    assert dt is not None
    assert dt.year == 2026
    assert parse_iso("not-a-date") is None


def test_session_revoke(authenticator, webui_config):
    result = authenticator.login("10.0.0.4", webui_config.admin_token)
    token = result["session_token"]
    assert authenticator.sessions.get(token) is not None
    authenticator.logout("10.0.0.4", token)
    assert authenticator.sessions.get(token) is None


def test_require_session_bearer(authenticator, webui_config):
    result = authenticator.login("10.0.0.5", webui_config.admin_token)
    token = result["session_token"]
    assert authenticator.require_session(f"Bearer {token}") is not None
    assert authenticator.require_session(None) is None
    assert authenticator.require_session("Bearer nope") is None
