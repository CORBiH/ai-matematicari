# -*- coding: utf-8 -*-
"""Phase 4 — Stable Exam Engine (deterministic, flag-gated).

Flag off (default) → legacy exam path, unchanged. Flag on → the engine owns
whole exam turns deterministically (no model call): pre-validated items, one
answer per item, help with zero reveal, terminal completion, no reopen.
"""
import types

import pytest

from matbot import exam_engine as ee
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.answer_checker import derive_expected, _fmt_expected


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
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
        raise AssertionError("model MUST NOT be called during an exam-engine turn")
    return chat


def _exam_turn(master, tmap, message, prev, session="s1"):
    payload = {"grade": 6, "mode": "exam", "session_id": session, "student_message": message}
    if prev:
        payload["previous_next_state"] = prev
    return svc.handle_chat(payload, _no_model(), master, tmap, model="m", timeout=1)


def _correct(question, item=None):
    """The canonical, VALIDATED answer for an exam item.

    Prefer the item's stored ``expected_display`` (the template's self-validated
    answer, which for explanation tasks includes the required reasoning); fall
    back to the checker's formatted expectation."""
    if item and item.get("expected_display"):
        return item["expected_display"]
    return _fmt_expected(derive_expected(question))


# --------------------------------------------------------------------------- #
# Pure engine                                                                 #
# --------------------------------------------------------------------------- #
def test_all_pool_items_are_validated():
    assert ee._validated_pool() == ee._ITEM_POOL      # every item has an expected answer


def test_flag_semantics(monkeypatch):
    monkeypatch.delenv("MATBOT_ENGINE_V2_EXAM", raising=False)
    assert ee.exam_mode() == "off"
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    assert ee.exam_enabled() is True
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "weird")
    assert ee.exam_mode() == "off"


def test_start_exam_builds_validated_items():
    state = ee.start_exam(seed="abc", count=3)
    assert state.exam_status == "active"
    assert state.current_index == 0
    assert len(state.items) == 3
    for it in state.items:
        assert it.expected_display                     # pre-validated (has expected)
        assert it.status == "unanswered"


def test_start_is_deterministic_per_seed():
    a = [it.question for it in ee.start_exam(seed="X", count=3).items]
    b = [it.question for it in ee.start_exam(seed="X", count=3).items]
    assert a == b


def test_load_state_roundtrip():
    state = ee.start_exam(seed="rt", count=3)
    back = ee.load_state(state.to_dict())
    assert back is not None
    assert [it.question for it in back.items] == [it.question for it in state.items]


def test_load_state_rejects_legacy_shape():
    # A legacy exam_state (no engine=="v2") is not owned by the v2 loader.
    assert ee.load_state({"exam_status": "active", "items": [{"question": "x"}]}) is None


def test_should_handle_routing(monkeypatch):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    v2 = ee.start_exam(seed="r").to_dict()
    assert ee.should_handle(prev_exam=v2, mode="explain") is True         # continue v2
    assert ee.should_handle(prev_exam=None, mode="exam") is True          # fresh start
    assert ee.should_handle(prev_exam=None, mode="practice") is False     # not an exam
    legacy = {"exam_status": "active", "items": [{"question": "x"}]}
    assert ee.should_handle(prev_exam=legacy, mode="exam") is False       # leave legacy alone
    assert ee.should_handle(prev_exam=v2, mode="exam", has_active_image=True) is False


# --------------------------------------------------------------------------- #
# Service — full lifecycle (no model called)                                  #
# --------------------------------------------------------------------------- #
def test_flag_off_uses_legacy(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "off")
    chat = lambda *a, **k: types.SimpleNamespace(  # noqa: E731
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="1. a\n2. b"))])
    out = svc.handle_chat({"grade": 6, "mode": "exam", "selected_oblast": "Razlomci",
                           "session_id": "L", "student_message": "daj mi kontrolni"},
                          chat, master, tmap, model="m", timeout=1)
    assert out.get("engine") != "exam_v2"              # legacy path, not the v2 engine


def test_start_presents_validated_items(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = _exam_turn(master, tmap, "daj mi kontrolni", None)
    es = out["exam_state"]
    assert out["engine"] == "exam_v2"
    assert out["mode"] == "exam" and out["session_mode"] == "exam"
    assert es["exam_status"] == "active" and es["current_item_index"] == 0
    assert len(es["items"]) == 3
    for it in es["items"]:
        assert derive_expected(it["question"]) is not None   # gradeable


def test_one_answer_one_item(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _exam_turn(master, tmap, "kontrolni", None)["next_state"]
    es_items = ns["exam_state"]["items"]
    q1 = es_items[0]["question"]
    out = _exam_turn(master, tmap, _correct(q1, es_items[0]), ns)
    es = out["exam_state"]
    # exactly ONE item graded, cursor advanced by one (no bleed).
    graded = [it["correct"] for it in es["items"]]
    assert graded[0] is True and graded[1] is None and graded[2] is None
    assert es["current_item_index"] == 1
    assert out["answer_verdict"] == "correct"


def test_full_exam_completes_and_scores(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _exam_turn(master, tmap, "kontrolni", None)["next_state"]
    items = list(ns["exam_state"]["items"])
    out = None
    for it in items:
        out = _exam_turn(master, tmap, _correct(it["question"], it), ns)
        ns = out["next_state"]
    es = out["exam_state"]
    assert es["exam_status"] == "completed"
    assert es["current_item_index"] is None
    assert all(it["correct"] for it in es["items"])
    assert "3/3" in out["answer"]
    assert out["task_status"] == "completed"


def test_wrong_answer_scored_incorrect(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _exam_turn(master, tmap, "kontrolni", None)["next_state"]
    out = _exam_turn(master, tmap, "netačno999", ns)
    assert out["answer_verdict"] == "incorrect"
    assert out["exam_state"]["items"][0]["correct"] is False


def test_help_gives_no_reveal_no_advance(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    # Razlomci → distinctive fraction answers, so the "no reveal" check is meaningful
    # (a 2-letter "da"/"ne" would be a substring of "Zadatak").
    payload = {"grade": 6, "mode": "exam", "session_id": "help1",
               "selected_oblast": "Razlomci", "student_message": "kontrolni"}
    ns = svc.handle_chat(payload, _no_model(), master, tmap, model="m", timeout=1)["next_state"]
    exp = ns["exam_state"]["items"][0]["expected_display"]
    hp = {"grade": 6, "mode": "exam", "session_id": "help1", "selected_oblast": "Razlomci",
          "student_message": "ne znam", "previous_next_state": ns}
    out = svc.handle_chat(hp, _no_model(), master, tmap, model="m", timeout=1)
    es = out["exam_state"]
    assert es["current_item_index"] == 0                # no advance
    assert es["items"][0]["status"] == "unanswered"     # not graded/completed
    assert out["answer_verdict"] is None                # help is not a verdict
    assert exp not in out["answer"]                     # zero reveal of the answer
    assert es["exam_status"] == "active"


def test_help_never_creates_new_exam(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    first = _exam_turn(master, tmap, "kontrolni", None)["next_state"]
    exam_id = first["exam_state"]["exam_id"]
    out = _exam_turn(master, tmap, "pomozi", first)
    assert out["exam_state"]["exam_id"] == exam_id       # same exam, not a new one


# --------------------------------------------------------------------------- #
# Completion / post-exam behavior                                             #
# --------------------------------------------------------------------------- #
def _complete_exam(master, tmap, session="s1"):
    ns = _exam_turn(master, tmap, "kontrolni", None, session)["next_state"]
    for it in list(ns["exam_state"]["items"]):
        ns = _exam_turn(master, tmap, _correct(it["question"], it), ns, session)["next_state"]
    return ns


def test_post_exam_explain_does_not_reopen(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _complete_exam(master, tmap)
    out = _exam_turn(master, tmap, "objasni treći", ns)
    assert out["exam_state"]["exam_status"] == "completed"   # still terminal
    assert out["task_status"] == "completed"
    assert "3." in out["answer"]                             # explains item 3


def test_post_exam_random_message_does_not_generate_items(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _complete_exam(master, tmap)
    ids_before = [it["item_id"] for it in ns["exam_state"]["items"]]
    out = _exam_turn(master, tmap, "hvala", ns)
    assert out["exam_state"]["exam_status"] == "completed"
    assert [it["item_id"] for it in out["exam_state"]["items"]] == ids_before


def test_explicit_new_exam_starts_fresh(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _complete_exam(master, tmap)
    old_id = ns["exam_state"]["exam_id"]
    out = _exam_turn(master, tmap, "novi kontrolni", ns)
    assert out["exam_state"]["exam_status"] == "active"
    assert out["exam_state"]["exam_id"] != old_id


def test_submit_completes_remaining(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    ns = _exam_turn(master, tmap, "kontrolni", None)["next_state"]
    out = _exam_turn(master, tmap, "predaj", ns)
    assert out["exam_state"]["exam_status"] == "completed"
    assert all(it["status"] == "graded" for it in out["exam_state"]["items"])


# --------------------------------------------------------------------------- #
# Separation from the Practice Step Engine                                     #
# --------------------------------------------------------------------------- #
def test_exam_and_practice_engines_do_not_double_fire(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    # An exam turn is owned by the exam engine — never a practice step_cursor.
    ns = _exam_turn(master, tmap, "kontrolni", None)["next_state"]
    out = _exam_turn(master, tmap, "netačno1", ns)
    assert out["next_state"].get("step_cursor") is None
    assert out["engine"] == "exam_v2"

    # A practice turn is owned by the practice engine — never an exam_state change.
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="ok"))])
    pout = svc.handle_chat({"grade": 6, "mode": "practice",
                            "interaction_phase": "answering_practice_task",
                            "last_tutor_task": "Rastavi 12 na proste faktore.",
                            "student_message": "2"}, chat, master, tmap, model="m", timeout=1)
    assert pout.get("engine") != "exam_v2"
    assert pout["next_state"].get("step_cursor") is not None


def test_practice_turn_not_hijacked_by_exam(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    assert svc._exam_engine_should_handle(
        {"grade": 6, "mode": "practice", "student_message": "1/2"}) is False
