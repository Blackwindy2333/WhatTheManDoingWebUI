"""Runtime state store tests."""

from __future__ import annotations

from server.state import StateStore, utc_now_iso


def test_visit_counting(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.record_visit("2026-01-01")
    store.record_visit("2026-01-01")
    store.record_visit("2026-01-02")
    data = store.get_visits()
    assert data["total_visits"] == 3
    assert data["visits_by_date"]["2026-01-01"] == 2
    assert data["visits_by_date"]["2026-01-02"] == 1


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.record_visit("2026-01-01")
    store.append_audit("1.2.3.4", "login_ok", ts=utc_now_iso())
    reloaded = StateStore(path)
    assert reloaded.get_visits()["total_visits"] == 1
    assert reloaded.list_audit()[0]["action"] == "login_ok"


def test_login_failure_reset(tmp_path):
    store = StateStore(tmp_path / "state.json")
    assert store.record_login_failure("ip") == 1
    assert store.record_login_failure("ip") == 2
    store.reset_login_failures("ip")
    assert store.get_login_failures("ip") == 0


def test_ban_list_and_unban(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ban_ip("9.9.9.9", utc_now_iso(), "2099-01-01T00:00:00Z")
    assert store.get_ban("9.9.9.9") is not None
    assert store.unban_ip("9.9.9.9") is True
    assert store.get_ban("9.9.9.9") is None
    assert store.unban_ip("9.9.9.9") is False


def test_audit_cap(tmp_path):
    store = StateStore(tmp_path / "state.json")
    for i in range(250):
        store.append_audit("ip", f"act-{i}")
    entries = store.list_audit(limit=50)
    assert len(entries) == 50
    assert entries[-1]["action"] == "act-249"
