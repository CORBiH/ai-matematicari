# -*- coding: utf-8 -*-
"""The existing manual ``/sheets/flush`` endpoint now also drains the durable
V3 Sheets outbox (bounded), reusing the same atomic claim primitive as the
background worker and startup recovery — never called from the student
request path. No live/paid OpenAI or Google Sheets call is made anywhere.
"""
from __future__ import annotations

import app as app_module

from matbot.ai_tutor_v3 import sheets_outbox, state_store as ss


def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    s = ss.V3StateStore()
    s.init_db()
    return s


def test_sheets_flush_drains_pending_v3_rows(client, tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    sheets_outbox._reset_for_tests()
    store.enqueue_sheets_event(client_turn_id="t1", session_id="s1",
                              payload_json="{}", response_json="{}",
                              created_at=ss.now_iso())
    monkeypatch.setattr(
        "matbot.sheets_log.sheets_append_row_safe", lambda row: True)

    resp = client.post("/sheets/flush", json={"timeout": 1, "v3_limit": 10})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["v3_outbox"]["delivered"] == 1
    assert store.get_sheets_event("t1")["status"] == "delivered"
    sheets_outbox._reset_for_tests()


def test_sheets_flush_v3_drain_is_bounded(client, tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    sheets_outbox._reset_for_tests()
    for i in range(5):
        store.enqueue_sheets_event(client_turn_id=f"t{i}", session_id="s1",
                                  payload_json="{}", response_json="{}",
                                  created_at=ss.now_iso())
    monkeypatch.setattr(
        "matbot.sheets_log.sheets_append_row_safe", lambda row: True)

    resp = client.post("/sheets/flush", json={"timeout": 1, "v3_limit": 2})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["v3_outbox"]["delivered"] == 2
    assert store.count_sheets_events(status="pending") == 3
    sheets_outbox._reset_for_tests()


def test_sheets_flush_v3_limit_is_clamped_to_a_sane_bound(client, tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    sheets_outbox._reset_for_tests()
    resp = client.post("/sheets/flush", json={"v3_limit": 999999})
    assert resp.status_code == 200
    assert resp.get_json()["v3_outbox"]["delivered"] == 0   # nothing pending, but no error
    sheets_outbox._reset_for_tests()


def test_sheets_flush_v3_zero_limit_skips_the_v3_drain(client, tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    sheets_outbox._reset_for_tests()
    store.enqueue_sheets_event(client_turn_id="t1", session_id="s1",
                              payload_json="{}", response_json="{}",
                              created_at=ss.now_iso())
    resp = client.post("/sheets/flush", json={"v3_limit": 0})
    assert resp.status_code == 200
    assert resp.get_json()["v3_outbox"]["delivered"] == 0
    assert store.get_sheets_event("t1")["status"] == "pending"  # untouched
    sheets_outbox._reset_for_tests()


def test_sheets_flush_requires_diag_access(client, monkeypatch):
    monkeypatch.setattr(app_module, "LOCAL_MODE", False)
    monkeypatch.setattr(app_module, "DIAG_TOKEN", "")
    resp = client.post("/sheets/flush", json={})
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# Startup recovery kick (module-level, runs once per process)                #
# --------------------------------------------------------------------------- #
def test_startup_recovery_is_a_noop_when_v3_flag_is_off(monkeypatch):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "off")
    called = []
    monkeypatch.setattr(sheets_outbox, "kick_startup_drain", lambda *a, **k: called.append(1))
    app_module._kick_v3_sheets_startup_recovery()
    assert called == []   # off deployment: no V3 storage touched at all


def test_startup_recovery_kicks_drain_when_v3_flag_is_on(monkeypatch):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    called = []
    monkeypatch.setattr(sheets_outbox, "kick_startup_drain", lambda *a, **k: called.append(1))
    app_module._kick_v3_sheets_startup_recovery()
    assert called == [1]


def test_startup_recovery_never_raises_on_failure(monkeypatch):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(sheets_outbox, "kick_startup_drain", _boom)
    app_module._kick_v3_sheets_startup_recovery()  # must not raise


def test_kick_startup_drain_itself_is_non_blocking_and_bounded(tmp_path, monkeypatch):
    """The actual production entry point: a real store, a slow fake Sheets
    append, confirms the call returns immediately and later recovers the row."""
    import time
    store = _store(tmp_path, monkeypatch)
    sheets_outbox._reset_for_tests()
    store.enqueue_sheets_event(client_turn_id="t1", session_id="s1",
                              payload_json="{}", response_json="{}",
                              created_at=ss.now_iso())
    monkeypatch.setattr("matbot.sheets_log.sheets_append_row_safe", lambda row: True)

    t0 = time.monotonic()
    sheets_outbox.kick_startup_drain(store, limit=10)
    assert time.monotonic() - t0 < 0.5

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if store.get_sheets_event("t1")["status"] == "delivered":
            break
        time.sleep(0.02)
    assert store.get_sheets_event("t1")["status"] == "delivered"
    sheets_outbox._reset_for_tests()
