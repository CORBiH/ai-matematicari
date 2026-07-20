# -*- coding: utf-8 -*-
"""Phase 7 hardening — canary cohort marker + Exam Engine drain/rollback.

The canary marker is telemetry ONLY (never touches grading/state/counters/prose).
The exam drain mode makes disabling V2 safe: an already-active V2 exam finishes,
no new V2 exams start, and a completed exam is never reopened. With the flag fully
off, stale V2 exam state is stripped so the legacy normalizer can never corrupt it.
"""
import types

import pytest

from matbot import engine_v2
from matbot import exam_engine as ee
from matbot import sheets_log
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.answer_checker import derive_expected, _fmt_expected


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "a.sqlite3"))
    monkeypatch.delenv("ENGINE_CANARY", raising=False)
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "off")
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "off")
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _no_model():
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        raise AssertionError("model must not be called on a V2 exam turn")
    return chat


def _model(reply="1. Zadatak jedan\n2. Zadatak dva"):
    calls = {"n": 0}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=reply))])
    chat.calls = calls
    return chat


def _exam(master, tmap, msg, prev, chat=None, oblast="Razlomci", session="drain-s"):
    payload = {"grade": 6, "mode": "exam", "session_id": session,
               "selected_oblast": oblast, "student_message": msg}
    if prev:
        payload["previous_next_state"] = prev
    return svc.handle_chat(payload, chat or _no_model(), master, tmap,
                           model="m", timeout=1)


# --------------------------------------------------------------------------- #
# 1. Canary cohort marker                                                     #
# --------------------------------------------------------------------------- #
def test_canary_defaults_off(monkeypatch):
    monkeypatch.delenv("ENGINE_CANARY", raising=False)
    assert engine_v2.canary_enabled() is False
    assert engine_v2.canary_marker() == "0"


@pytest.mark.parametrize("val,expected", [("1", True), ("true", True), ("on", True),
                                          ("0", False), ("no", False), ("", False)])
def test_canary_flag_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("ENGINE_CANARY", val)
    assert engine_v2.canary_enabled() is expected


def test_sheets_row_carries_canary_marker(monkeypatch, master, tmap):
    assert sheets_log.SHEET_HEADERS[-1] == "engine_canary"
    monkeypatch.setenv("ENGINE_CANARY", "1")
    out = svc.handle_chat({"grade": 6, "mode": "practice",
                           "interaction_phase": "answering_practice_task",
                           "last_tutor_task": "Izračunaj: 1/4 + 1/4.",
                           "student_message": "1/2"},
                          _model("Tačno."), master, tmap, model="m", timeout=1)
    row = sheets_log._build_transcript_row({"session_id": "s"}, out)
    assert len(row) == len(sheets_log.SHEET_HEADERS)
    assert row[sheets_log.SHEET_HEADERS.index("engine_canary")] == "1"


def test_diag_metrics_include_canary_and_flags(monkeypatch):
    monkeypatch.setenv("ENGINE_CANARY", "1")
    m = engine_v2.get_metrics()
    assert m["canary"] == "1"
    assert "MATBOT_ENGINE_V2" in m["flags"]


def test_canary_does_not_change_behavior(monkeypatch, master, tmap):
    """Marker must not affect grading, state, counters or the visible answer."""
    payload = {"grade": 6, "mode": "practice",
               "interaction_phase": "answering_practice_task",
               "last_tutor_task": "Izračunaj: 1/4 + 1/4.", "student_message": "1/2"}
    monkeypatch.delenv("ENGINE_CANARY", raising=False)
    off = svc.handle_chat(dict(payload), _model("Tačno."), master, tmap, model="m", timeout=1)
    monkeypatch.setenv("ENGINE_CANARY", "1")
    on = svc.handle_chat(dict(payload), _model("Tačno."), master, tmap, model="m", timeout=1)
    for key in ("answer", "answer_verdict", "answer_verdict_detail", "task_status",
                "last_tutor_task", "wrong_attempt_count", "hint_count"):
        assert on.get(key) == off.get(key), key
    assert on["next_state"]["correct_streak"] == off["next_state"]["correct_streak"]


# --------------------------------------------------------------------------- #
# 2. Exam mode semantics                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("val,mode", [("on", "on"), ("drain", "drain"), ("off", "off"),
                                      ("weird", "off"), ("", "off")])
def test_exam_mode_parsing(monkeypatch, val, mode):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", val)
    assert ee.exam_mode() == mode
    assert ee.exam_enabled() is (mode in ("on", "drain"))
    assert ee.accepts_new_exams() is (mode == "on")


def test_should_handle_matrix(monkeypatch):
    v2 = ee.start_exam(seed="s").to_dict()
    for mode, fresh_ok, existing_ok in (("on", True, True), ("drain", False, True),
                                        ("off", False, False)):
        monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", mode)
        assert ee.should_handle(prev_exam=None, mode="exam") is fresh_ok, mode
        assert ee.should_handle(prev_exam=v2, mode="exam") is existing_ok, mode


# --------------------------------------------------------------------------- #
# 3. on → drain transition                                                    #
# --------------------------------------------------------------------------- #
def test_active_exam_survives_on_to_drain(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    started = _exam(master, tmap, "kontrolni", None)
    exam_id = started["exam_state"]["exam_id"]
    # operator flips to drain mid-exam
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "drain")
    q1 = started["exam_state"]["items"][0]["question"]
    nxt = _exam(master, tmap, _fmt_expected(derive_expected(q1)), started["next_state"])
    assert nxt["engine"] == "exam_v2"                      # still owned by V2
    assert nxt["exam_state"]["exam_id"] == exam_id         # same exam
    assert nxt["exam_state"]["current_item_index"] == 1    # progressed, no bleed
    assert nxt["answer_verdict"] == "correct"
    assert nxt["mode"] == "exam" and nxt["session_mode"] == "exam"


def test_drain_lets_existing_exam_complete(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = _exam(master, tmap, "kontrolni", None, session="d-complete")
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "drain")
    for q in [it["question"] for it in out["exam_state"]["items"]]:
        out = _exam(master, tmap, _fmt_expected(derive_expected(q)),
                    out["next_state"], session="d-complete")
    assert out["exam_state"]["exam_status"] == "completed"
    assert all(it["correct"] for it in out["exam_state"]["items"])


def test_drain_does_not_create_new_v2_exam(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "drain")
    chat = _model()
    out = _exam(master, tmap, "daj mi kontrolni", None, chat=chat, session="d-new")
    assert out.get("engine") != "exam_v2"     # legacy handled it
    assert chat.calls["n"] >= 1               # legacy called the model


def test_drain_new_exam_request_after_completion_releases_state(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = _exam(master, tmap, "kontrolni", None, session="d-rel")
    out = _exam(master, tmap, "predaj", out["next_state"], session="d-rel")
    assert out["exam_state"]["exam_status"] == "completed"
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "drain")
    rel = _exam(master, tmap, "novi kontrolni", out["next_state"], session="d-rel")
    # V2 refuses to start a new exam and RELEASES its state → next turn is legacy.
    assert rel["exam_state"] is None
    assert rel["next_state"]["exam_state"] is None
    assert rel["answer_verdict_detail"] == "exam_released"


@pytest.mark.parametrize("mode", ["drain", "off"])
def test_completed_exam_never_reopens(monkeypatch, master, tmap, mode):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = _exam(master, tmap, "kontrolni", None, session=f"nr-{mode}")
    out = _exam(master, tmap, "predaj", out["next_state"], session=f"nr-{mode}")
    assert out["exam_state"]["exam_status"] == "completed"
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", mode)
    after = _exam(master, tmap, "objasni prvi", out["next_state"],
                  chat=_model("U redu."), session=f"nr-{mode}")
    es = after.get("exam_state")
    if es and es.get("engine") == "v2":
        assert es["exam_status"] == "completed"      # still terminal
    else:
        assert not (es or {}).get("exam_status") == "active"


# --------------------------------------------------------------------------- #
# 4. off: stale V2 state handled safely                                       #
# --------------------------------------------------------------------------- #
def test_strip_stale_v2_exam_helper():
    v2 = ee.start_exam(seed="stale").to_dict()
    data = {"previous_next_state": {"exam_state": v2, "active_task_kind": "exam",
                                    "correct_streak": 2}}
    out = svc._strip_stale_v2_exam(data)
    assert out["previous_next_state"]["exam_state"] is None
    assert out["_v2_exam_state_discarded"] is True
    assert out["previous_next_state"]["correct_streak"] == 2   # rest preserved
    assert data["previous_next_state"]["exam_state"] is v2     # input untouched


def test_off_does_not_feed_v2_state_to_legacy(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    started = _exam(master, tmap, "kontrolni", None, session="off-stale")
    assert started["exam_state"]["exam_status"] == "active"
    # hard rollback mid-exam
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "off")
    chat = _model("U redu.")
    after = _exam(master, tmap, "1/2", started["next_state"], chat=chat,
                  session="off-stale")
    assert after.get("engine") != "exam_v2"                 # legacy owns it now
    es = after.get("exam_state") or {}
    assert es.get("engine") != "v2"                          # V2 state not resurrected
    assert es.get("exam_status") != "completed"              # nothing spuriously finished


def test_transition_writes_exactly_one_sheets_row(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    counter = {"n": 0}
    orig = svc.log_transcript_to_sheet
    svc.log_transcript_to_sheet = lambda p, r: counter.__setitem__("n", counter["n"] + 1)
    try:
        out = _exam(master, tmap, "kontrolni", None, session="rows")
        assert counter["n"] == 1
        monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "drain")
        counter["n"] = 0
        _exam(master, tmap, "ne znam", out["next_state"], session="rows")
        assert counter["n"] == 1
    finally:
        svc.log_transcript_to_sheet = orig


def test_practice_and_grading_unaffected_by_exam_mode(monkeypatch, master, tmap):
    payload = {"grade": 6, "mode": "practice",
               "interaction_phase": "answering_practice_task",
               "last_tutor_task": "Izračunaj: 1/4 + 1/4.", "student_message": "1/2"}
    results = {}
    for mode in ("off", "on", "drain"):
        monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", mode)
        out = svc.handle_chat(dict(payload), _model("Tačno."), master, tmap,
                              model="m", timeout=1)
        results[mode] = (out["answer_verdict"], out["task_status"], out["answer"])
    assert results["off"] == results["on"] == results["drain"]
