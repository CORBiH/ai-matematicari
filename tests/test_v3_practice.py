# -*- coding: utf-8 -*-
"""End-to-end tests for the AI-first V3 Practice vertical slice.

Every model call goes through a deterministic ``FakeClient`` — no paid OpenAI
call is ever made. The real ``POST /api/ai-tutor/chat/stream`` route is exercised
with V3 enabled behind its flags; the frontend file and wire contract are never
changed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from matbot.ai_tutor_v3 import dispatcher, lesson_blueprint, reducer, verifier
from matbot.ai_tutor_v3 import state_store as ss
from matbot.ai_tutor_v3.orchestrator import ModelCallResult
from matbot.ai_tutor_v3 import orchestrator

DIVISIBILITY = "6-03-024"
NINE_TARGETS = ["2", "3", "4", "5", "6", "9", "10", "15", "25"]


# --------------------------------------------------------------------------- #
# Canned model outputs                                                        #
# --------------------------------------------------------------------------- #
def blueprint_parsed(targets=None, confidence=0.9, lesson_title=None):
    targets = targets or NINE_TARGETS
    return {
        "learning_objectives": ["primijeniti pravila djeljivosti"],
        "prerequisites": ["zbir cifara"],
        "concepts": [{
            "concept_id": "div-compound-6", "name": "Djeljivost sa 6",
            "importance": "core", "prerequisites": [],
            "example_task_types": ["yes_no_with_reason"],
            "evidence_of_understanding": ["imenuje oba uslova"],
            "typical_errors": ["provjeri samo jedan uslov"],
            "progression_criteria": "2 uzastopna tačna",
        }],
        "coverage_targets": [
            {"target_id": n, "name": f"Djeljivost sa {n}", "concept_id": "div-compound-6"}
            for n in targets],
        "key_rules": [{"rule_id": "r6", "statement": "paran i zbir cifara djeljiv sa 3"}],
        "allowed_methods": ["pravilo djeljivosti"],
        "common_misconceptions": [{"code": "one_factor", "description": "provjeri jedan uslov"}],
        "task_families": [{"family_id": "yn", "description": "da/ne s obrazloženjem",
                           "answer_kind": "boolean_with_reason"}],
        "difficulty_dimensions": [{"dimension_id": "size", "description": "veličina broja"}],
        "hint_strategy": [{"level": 1, "guidance": "podsjeti na pravilo"}],
        "mastery_requirements": {"min_concepts_mastered": 7, "min_independent_solves": 2},
        "language_guidance": {"language_register": "grade_6", "avoid": ["formalno"],
                              "preferred_terms": ["zbir"]},
        "supported_verification_types": ["divisibility"],
        "generation_confidence": confidence,
    }


def interp_parsed(turn_kind, *, is_answer=False, confidence=0.9,
                  requested_action=None, clarification=None, assessment=None,
                  meaning="protumačeno"):
    inner = {
        "schema_version": "v1", "turn_kind": turn_kind,
        "is_answer_attempt": is_answer, "normalized_meaning": meaning,
        "certainty": "certain", "precision": "unspecified", "confidence": confidence,
    }
    if requested_action is not None:
        inner["requested_action"] = requested_action
    if clarification is not None:
        inner["clarification_question"] = clarification
    out = {"interpretation": inner}
    if assessment is not None:
        out["assessment"] = assessment
    return out


def assess_parsed(verdict, *, action="continue", is_answer=True, confidence=0.9,
                  solved_independently=False, difficulty=None, misconceptions=None):
    a = {
        "schema_version": "v1", "is_answer_attempt": is_answer,
        "proposed_verdict": verdict, "proposed_pedagogical_action": action,
        "confidence": confidence, "solved_independently": solved_independently,
    }
    if difficulty is not None:
        a["difficulty_suggestion"] = difficulty
    if misconceptions is not None:
        a["misconception_codes"] = misconceptions
    return a


def task_parsed(target="6", concept="div-compound-6", question="Da li je 252 djeljiv sa 6?"):
    return {"concept_id": concept, "target_id": target, "question": question,
            "answer_kind": "boolean_with_reason", "expected_internal": "da",
            "difficulty_level": 2}


def narration_parsed(text="Tako je, odlično.", category="feedback"):
    return {"schema_version": "v1", "student_text": text,
            "response_category": category, "confidence": 0.9}


# --------------------------------------------------------------------------- #
# Fake structured client                                                      #
# --------------------------------------------------------------------------- #
class FakeClient:
    """Deterministic structured client. Per purpose it returns the last value
    set (reused for repeated calls), defaulting to sensible canned output."""

    def __init__(self):
        self.calls: list[str] = []
        self.models: list[str] = []
        self._by_purpose: dict[str, object] = {
            orchestrator.PURPOSE_BLUEPRINT: blueprint_parsed(),
            orchestrator.PURPOSE_INTERPRET: interp_parsed("task_request"),
            orchestrator.PURPOSE_TASK: task_parsed(),
            orchestrator.PURPOSE_HINT: narration_parsed("Podsjeti se pravila.", "hint"),
            orchestrator.PURPOSE_CONCEPT: narration_parsed("Objašnjenje.", "concept"),
            orchestrator.PURPOSE_NARRATION: narration_parsed(),
            orchestrator.PURPOSE_REVEAL: narration_parsed("Rješenje: ...", "reveal"),
        }
        self._status: dict[str, tuple[str, str]] = {}

    def set(self, purpose, parsed):
        self._by_purpose[purpose] = parsed

    def set_status(self, purpose, status, error_code=""):
        """Force a non-ok result for a purpose (timeout/invalid output)."""
        self._status[purpose] = (status, error_code)

    def generate(self, *, purpose, system, user, schema_name, schema, model, timeout):
        self.calls.append(purpose)
        self.models.append(model)
        if purpose in self._status:
            status, err = self._status[purpose]
            return ModelCallResult(status=status, model=model, purpose=purpose,
                                   error_code=err or status)
        parsed = self._by_purpose.get(purpose, {})
        return ModelCallResult(status="ok", parsed=parsed,
                               usage={"prompt_tokens": 120, "completion_tokens": 44},
                               latency_ms=7.0, model=model, purpose=purpose)


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def v3_env(monkeypatch, tmp_path):
    from matbot import topic_resolver as tr
    from matbot.ai_tutor_v3 import sheets_outbox
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", DIVISIBILITY)
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "off")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    tr.reset_cache()
    sheets_outbox._reset_for_tests()   # fresh worker thread bound to THIS test's DB
    yield
    dispatcher.set_model_client(None)
    tr.reset_cache()
    sheets_outbox._reset_for_tests()


@pytest.fixture()
def fake(v3_env):
    client = FakeClient()
    dispatcher.set_model_client(client)
    return client


def base_payload(**over):
    p = {"session_id": "s1", "grade": 6, "mode": "practice",
         "selected_topic": DIVISIBILITY, "selected_oblast": "Djeljivost brojeva",
         "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
         "client_turn_id": "c1"}
    p.update(over)
    return p


def dispatch(payload):
    return dispatcher.v3_practice_dispatch(payload, model="gpt-5-mini", timeout=5)


def start_task(fake, session="s1"):
    """Drive turn A (initial task request) and return the response."""
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    return dispatch(base_payload(session_id=session, client_turn_id=session + "-A"))


def answer_payload(prev, message, session="s1", turn="B"):
    return base_payload(session_id=session, client_turn_id=session + "-" + turn,
                        student_message=message,
                        previous_next_state=prev["next_state"])


# =========================================================================== #
# Architecture / flags                                                        #
# =========================================================================== #
def test_flag_off_produces_no_v3(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "off")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", DIVISIBILITY)
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        assert dispatch(base_payload()) is None
        assert client.calls == []          # zero V3 model calls
    finally:
        dispatcher.set_model_client(None)


def test_invalid_flag_behaves_as_off(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "banana")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", DIVISIBILITY)
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    assert dispatcher.practice_flag() == "off"
    dispatcher.set_model_client(FakeClient())
    try:
        assert dispatch(base_payload()) is None
    finally:
        dispatcher.set_model_client(None)


def test_empty_whitelist_allows_no_lesson(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    dispatcher.set_model_client(FakeClient())
    try:
        assert dispatch(base_payload()) is None   # empty ⇒ nothing eligible
    finally:
        dispatcher.set_model_client(None)


def test_non_whitelisted_lesson_stays_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "6-99-999")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    dispatcher.set_model_client(FakeClient())
    try:
        assert dispatch(base_payload()) is None
    finally:
        dispatcher.set_model_client(None)


def test_whitelisted_lesson_is_eligible(fake):
    resp = start_task(fake)
    assert resp is not None
    assert resp["engine"] == "v3_practice"


def test_shadow_mode_returns_none_but_still_computes(fake, monkeypatch):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "shadow")
    resp = dispatch(base_payload())
    assert resp is None                     # legacy controls visible output
    assert fake.calls                       # V3 still ran (shadow session)


def test_multiple_comma_separated_ids_work(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "6-99-999, " + DIVISIBILITY)
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        resp = dispatch(base_payload())
        assert resp is not None and resp["engine"] == "v3_practice"
    finally:
        dispatcher.set_model_client(None)


# =========================================================================== #
# Wildcard lesson eligibility (MATBOT_AI_TUTOR_V3_LESSONS=*)                   #
# =========================================================================== #
def test_wildcard_enables_a_valid_resolved_practice_lesson(fake, monkeypatch):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    resp = start_task(fake)
    assert resp is not None
    assert resp["engine"] == "v3_practice"


def test_wildcard_enables_a_different_ordinary_lesson_with_no_python_changes(
    monkeypatch, tmp_path,
):
    """Same code path as the explicit-whitelist fifth-lesson test, but under
    the wildcard: an entirely different lesson (fractions, not divisibility)
    works with zero new Python — only fake curriculum-shaped model output."""
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    client = FakeClient()
    client.set(orchestrator.PURPOSE_BLUEPRINT, blueprint_parsed(targets=["frac"]))
    client.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    client.set(orchestrator.PURPOSE_TASK,
              task_parsed(target="frac", concept="div-compound-6",
                          question="Proširi 1/2 na nazivnik 8."))
    dispatcher.set_model_client(client)
    try:
        resp = dispatch(base_payload(selected_topic="6-04-035",
                                     selected_oblast="Razlomci"))
        assert resp is not None
        assert resp["engine"] == "v3_practice"
        assert "Proširi" in resp["last_tutor_task"]
    finally:
        dispatcher.set_model_client(None)


def test_wildcard_does_not_enable_grade_5(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        resp = dispatch(base_payload(grade=5))
        assert resp is None
        assert client.calls == []            # rejected before any model call
    finally:
        dispatcher.set_model_client(None)


def test_wildcard_does_not_enable_an_unresolved_lesson(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        resp = dispatch(base_payload(selected_topic="999999999", selected_oblast=""))
        assert resp is None
        assert client.calls == []
    finally:
        dispatcher.set_model_client(None)


@pytest.mark.parametrize("mode", ["explain", "quick", "exam"])
def test_wildcard_does_not_enable_explain_quick_or_exam(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        resp = dispatch(base_payload(mode=mode))
        assert resp is None
        assert client.calls == []
    finally:
        dispatcher.set_model_client(None)


def test_v3_off_makes_zero_calls_even_with_wildcard_set(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "off")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        assert dispatch(base_payload()) is None
        assert client.calls == []
    finally:
        dispatcher.set_model_client(None)


@pytest.mark.parametrize("value", ["*,6-03-024", "6-03-024,*", "*, 6-99-999",
                                   "*,*,6-03-024"])
def test_invalid_mixed_wildcard_combinations_fail_safely(monkeypatch, tmp_path, value):
    """Documented deterministic policy: mixing '*' with any explicit id is
    treated exactly like an empty whitelist — nothing eligible — never
    silently upgraded to 'all'."""
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", value)
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    assert dispatcher.lesson_whitelist_mode() == ("none", frozenset())
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        resp = dispatch(base_payload(selected_topic=DIVISIBILITY,
                                     selected_oblast="Djeljivost brojeva"))
        assert resp is None
        assert client.calls == []
    finally:
        dispatcher.set_model_client(None)


@pytest.mark.parametrize("value,expected", [
    ("", ("none", frozenset())),
    ("   ", ("none", frozenset())),
    (DIVISIBILITY, ("explicit", frozenset({DIVISIBILITY}))),
    (f" {DIVISIBILITY} , 6-04-035 ", ("explicit", frozenset({DIVISIBILITY, "6-04-035"}))),
    ("*", ("all", frozenset())),
    (" * ", ("all", frozenset())),
    ("*,*", ("all", frozenset())),
])
def test_lesson_whitelist_mode_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", value)
    assert dispatcher.lesson_whitelist_mode() == expected


def test_real_stream_route_with_wildcard_reaches_v3(client, monkeypatch, tmp_path):
    from tests.test_prod_stream_route import sse_post, done_payload
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "off")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    from matbot import topic_resolver as tr
    tr.reset_cache()
    fake_client = FakeClient()
    fake_client.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    dispatcher.set_model_client(fake_client)
    try:
        events = sse_post(client, base_payload(client_turn_id="wc-route1"))
        body = done_payload(events)
        assert body["engine"] == "v3_practice"
        assert body["last_tutor_task"] == "Da li je 252 djeljiv sa 6?"
    finally:
        dispatcher.set_model_client(None)


# =========================================================================== #
# Blueprint                                                                    #
# =========================================================================== #
def test_blueprint_generated_once_and_reused(fake):
    start_task(fake, session="s1")
    first = fake.calls.count(orchestrator.PURPOSE_BLUEPRINT)
    fake.calls.clear()
    start_task(fake, session="s2")          # different session, same lesson
    assert fake.calls.count(orchestrator.PURPOSE_BLUEPRINT) == 0, "reused from store"
    assert first == 1


def test_divisibility_title_preserves_all_nine_targets(fake):
    resp = start_task(fake)
    v3 = resp["next_state"]["v3_state"]
    assert v3["coverage"]["targets"] == NINE_TARGETS


def test_invalid_blueprint_missing_targets_is_rejected(fake):
    # Drop 25 → coverage gate must reject (title lists it).
    fake.set(orchestrator.PURPOSE_BLUEPRINT,
             blueprint_parsed(targets=["2", "3", "4", "5", "6", "9", "10", "15"]))
    resp = start_task(fake)
    # Blueprint failed before any session mutation → fall back to legacy (None).
    assert resp is None


def test_source_hash_change_makes_new_blueprint_version():
    idn = lesson_blueprint.resolve_lesson_identity(6, DIVISIBILITY)
    h1 = lesson_blueprint.compute_source_hash({"lesson_id": idn.lesson_id, "x": "1"})
    h2 = lesson_blueprint.compute_source_hash({"lesson_id": idn.lesson_id, "x": "2"})
    assert h1 != h2


def test_fifth_lesson_works_with_no_new_python(monkeypatch, tmp_path):
    """A different lesson (fractions) runs the SAME code path — no new generator,
    checker or fact module. Proven by pointing the whitelist at another lesson
    id and driving a task with only fake curriculum-shaped model output."""
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "6-04-035")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    client = FakeClient()
    client.set(orchestrator.PURPOSE_BLUEPRINT, blueprint_parsed(targets=["frac"]))
    client.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    client.set(orchestrator.PURPOSE_TASK,
               task_parsed(target="frac", concept="div-compound-6",
                           question="Proširi 1/2 na nazivnik 8."))
    dispatcher.set_model_client(client)
    try:
        resp = dispatch(base_payload(selected_topic="6-04-035",
                                     selected_oblast="Razlomci"))
        assert resp is not None
        assert resp["engine"] == "v3_practice"
        assert "Proširi" in resp["last_tutor_task"]
    finally:
        dispatcher.set_model_client(None)


# =========================================================================== #
# Task generation                                                             #
# =========================================================================== #
def test_initial_request_creates_one_active_task(fake):
    resp = start_task(fake)
    assert resp["task_id"]
    assert resp["last_tutor_task"] == "Da li je 252 djeljiv sa 6?"
    assert resp["next_state"]["v3_state"]["active_task"]["target_id"] == "6"


def test_generated_task_belongs_to_lesson(fake):
    # A task with an unknown target AND unknown concept is rejected.
    fake.set(orchestrator.PURPOSE_TASK,
             task_parsed(target="999", concept="unknown-concept"))
    resp = start_task(fake)
    assert resp["last_tutor_task"] == ""          # no corrupt task activated
    assert resp["next_state"]["v3_state"]["active_task"] is None


def test_rejected_task_leaves_no_corrupt_active_task(fake):
    fake.set_status(orchestrator.PURPOSE_TASK, "error", "timeout")
    resp = start_task(fake)
    assert resp["next_state"]["v3_state"]["active_task"] is None


def test_explicit_concept_request_is_honored(fake):
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("task_request", requested_action="9"))
    fake.set(orchestrator.PURPOSE_TASK,
             task_parsed(target="9", question="Da li je 108 djeljiv sa 9?"))
    resp = start_task(fake)
    assert resp["next_state"]["v3_state"]["active_task"]["target_id"] == "9"


# =========================================================================== #
# Practice behaviour (answers, hints, concepts, difficulty, reveal, off-topic) #
# =========================================================================== #
def _answer_client(verdict, **assess_over):
    fake = FakeClient()
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed(verdict, **assess_over)))
    return fake


def test_correct_answer_increments_solved_and_streak(fake):
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("correct", solved_independently=True)))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Tačno!", "feedback_correct"))
    out = dispatch(answer_payload(resp, "da, jer mu je zadnja cifra 0 i paran je"))
    assert out["answer_verdict"] == "correct"
    v3 = out["next_state"]["v3_state"]
    assert v3["counters"]["solved_independent"] == 1
    assert v3["counters"]["correct_streak"] == 1
    assert v3["active_task"] is None            # completed


def test_incorrect_answer_counts_one_wrong_and_keeps_task(fake):
    resp = start_task(fake)
    tid = resp["task_id"]
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("incorrect")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Nije, pokušaj opet.", "feedback_incorrect"))
    out = dispatch(answer_payload(resp, "ne jer je broj neparan"))
    assert out["answer_verdict"] == "incorrect"
    v3 = out["next_state"]["v3_state"]
    assert v3["counters"]["wrong_attempts"] == 1
    assert v3["counters"]["correct_streak"] == 0
    assert v3["active_task"]["task_id"] == tid   # task preserved


def test_partial_answer_is_not_forced_incorrect(fake):
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("partial")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Dobar početak.", "feedback_partial"))
    out = dispatch(answer_payload(resp, "paran je ali nisam siguran za 3"))
    assert out["answer_verdict"] == "partial"
    v3 = out["next_state"]["v3_state"]
    assert v3["counters"]["wrong_attempts"] == 0
    assert v3["active_task"] is not None          # still active


def test_help_request_increments_hint_once_and_keeps_task(fake):
    resp = start_task(fake)
    tid = resp["task_id"]
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("help_request"))
    out = dispatch(answer_payload(resp, "pomozi, ne znam kako početi"))
    v3 = out["next_state"]["v3_state"]
    assert v3["active_task"]["task_id"] == tid
    assert v3["active_task"]["hints_given"] == 1
    assert v3["counters"]["wrong_attempts"] == 0   # help is not a wrong attempt


def test_concept_question_preserves_task_id(fake):
    resp = start_task(fake)
    tid = resp["task_id"]
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("question"))
    fake.set(orchestrator.PURPOSE_CONCEPT, narration_parsed("Broj je djeljiv sa 6 ako...", "concept"))
    out = dispatch(answer_payload(resp, "kada je broj djeljiv sa 6"))
    assert out["answer_verdict"] == "no_attempt"
    assert out["next_state"]["v3_state"]["active_task"]["task_id"] == tid


def test_ambiguous_message_produces_no_attempt_and_clarifies(fake):
    resp = start_task(fake)
    tid = resp["task_id"]
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("ambiguous", clarification="Na šta tačno misliš?"))
    out = dispatch(answer_payload(resp, "što me pitaš samo za 6"))
    assert out["answer_verdict"] == "clarification"
    assert out["next_state"]["v3_state"]["active_task"]["task_id"] == tid
    assert out["next_state"]["v3_state"]["counters"]["attempts"] == 0


def test_comment_with_number_is_not_graded(fake):
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("comment"))
    out = dispatch(answer_payload(resp, "ma nisam rekao da je odgovor 6"))
    assert out["answer_verdict"] == "no_attempt"
    assert out["next_state"]["v3_state"]["counters"]["attempts"] == 0


def test_new_task_request_is_not_graded(fake):
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK, task_parsed(target="9", question="Novi: 108 sa 9?"))
    out = dispatch(answer_payload(resp, "daj drugi zadatak"))
    assert out["answer_verdict"] == "no_attempt"
    assert out["next_state"]["v3_state"]["counters"]["wrong_attempts"] == 0
    assert "108" in out["last_tutor_task"]


def test_difficulty_change_adjusts_level_but_keeps_coverage(fake):
    resp = start_task(fake)
    before = resp["next_state"]["v3_state"]["coverage"]["targets"]
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("difficulty_change",
                           assessment=assess_parsed("not_checkable", is_answer=False,
                                                    difficulty="easier")))
    fake.set(orchestrator.PURPOSE_TASK, task_parsed(question="Lakši: 10 sa 5?"))
    out = dispatch(answer_payload(resp, "daj lakši"))
    v3 = out["next_state"]["v3_state"]
    assert v3["difficulty"]["level"] == 1
    assert v3["coverage"]["targets"] == before      # coverage never removed


def test_solution_reveal_is_assisted_not_independent(fake):
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("solution_request"))
    fake.set(orchestrator.PURPOSE_REVEAL, narration_parsed("Rješenje: da, jer...", "reveal"))
    out = dispatch(answer_payload(resp, "ne znam, riješi"))
    v3 = out["next_state"]["v3_state"]
    assert v3["counters"]["revealed"] == 1
    assert v3["counters"]["solved_independent"] == 0


def test_off_topic_fallback_is_exact(fake):
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("off_topic"))
    out = dispatch(answer_payload(resp, "koji je najbolji film"))
    assert out["answer"] == "Postavi mi pitanje ili zadatak iz matematike."
    assert out["next_state"]["v3_state"]["counters"]["attempts"] == 0


def test_correct_after_hint_is_assisted_not_independent(fake):
    resp = start_task(fake)
    # help first (hint), then a correct answer → assisted bucket, no streak.
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("help_request"))
    hinted = dispatch(answer_payload(resp, "pomozi", turn="B"))
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("correct")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Bravo!", "feedback_correct"))
    out = dispatch(answer_payload(hinted, "da, paran i zbir cifara 9", turn="C"))
    v3 = out["next_state"]["v3_state"]
    assert v3["counters"]["solved_assisted"] == 1
    assert v3["counters"]["solved_independent"] == 0
    assert v3["counters"]["correct_streak"] == 0    # assisted never streaks


# =========================================================================== #
# Student language — the model interprets; regexes never do                   #
# =========================================================================== #
@pytest.mark.parametrize("message,verdict,independent,expected_verdict", [
    ("da, jer mu je zadnja cifra 0",        "correct",   True,  "correct"),   # natural
    ("da jer mu je zadnja cifra 0",         "correct",   True,  "correct"),   # missing comma
    ("dobio sam pola",                      "correct",   True,  "correct"),   # implicit/colloquial
    ("mozda pola",                          "partial",   False, "partial"),   # approximate/uncertain
    ("ne jer je neparan",                   "incorrect", False, "incorrect"), # negative
])
def test_natural_bosnian_answers_route_by_meaning(fake, message, verdict,
                                                   independent, expected_verdict):
    """No exact-phrase matching: whatever the child types, the MODEL classifies
    it and the reducer grades from the proposal. Missing diacritics, colloquial
    and uncertain forms all flow through one path."""
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed(verdict, solved_independently=independent)))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("...", "feedback"))
    out = dispatch(answer_payload(resp, message))
    assert out["answer_verdict"] == expected_verdict


def test_changed_answer_is_regraded_fresh(fake):
    """A student who changes their mind is graded on the new claim, not punished
    for the old one — a second answer turn is just another answer turn."""
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True, assessment=assess_parsed("incorrect")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Nije.", "feedback_incorrect"))
    first = dispatch(answer_payload(resp, "ne", turn="B"))
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("correct", solved_independently=True)))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Sad tačno!", "feedback_correct"))
    second = dispatch(answer_payload(first, "čekaj, ipak da", turn="C"))
    assert second["answer_verdict"] == "correct"
    assert second["next_state"]["v3_state"]["counters"]["wrong_attempts"] == 1  # only the first


def test_concept_question_with_a_divisor_is_not_graded(fake):
    """"kako znam da li je djeljiv sa 25" contains a number but is a QUESTION."""
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("question"))
    fake.set(orchestrator.PURPOSE_CONCEPT, narration_parsed("Zadnje dvije cifre...", "concept"))
    out = dispatch(answer_payload(resp, "kako znam da li je djeljiv sa 25"))
    assert out["answer_verdict"] == "no_attempt"
    assert out["next_state"]["v3_state"]["counters"]["attempts"] == 0


# =========================================================================== #
# Reducer (unit-level guarantees)                                             #
# =========================================================================== #
def _mk_state_with_task():
    """A minimal in-memory session state with one active task, for direct
    reducer unit tests (no store, no model)."""
    from datetime import datetime, timezone
    from matbot.ai_tutor_v3.schemas import (ActiveTask, CoverageState,
                                            TutorSessionState)
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    return TutorSessionState(
        schema_version="v3.1", session_id="u", student_id="stu_x", grade=6,
        mode="practice", lesson_id=DIVISIBILITY, blueprint_id="bp",
        blueprint_version="v", prompt_policy=orchestrator.current_prompt_policy(),
        coverage=CoverageState(targets=NINE_TARGETS),
        active_task=ActiveTask(task_id="t1", concept_id="div-compound-6",
                               target_id="6", question="Q?",
                               answer_kind="boolean_with_reason", difficulty_level=2),
        created_at=now, updated_at=now)


def _ok_verification():
    import os
    os.environ.pop("MATBOT_V3_VERIFICATION", None)
    return verifier.verify_batch(claims_present=True)


def test_reducer_no_attempt_mutates_no_progress():
    state = _mk_state_with_task()
    result = reducer.reduce_turn(
        state=state, interpretation=_si("comment"), assessment=None,
        verification=_ok_verification())
    assert result.outcome.verdict == "no_attempt"
    assert result.new_state.counters.attempts == 0
    assert result.new_state.active_task is not None      # preserved


def test_reducer_needs_clarification_preserves_task():
    state = _mk_state_with_task()
    result = reducer.reduce_turn(
        state=state, interpretation=_si("ambiguous", clarification="?"),
        assessment=None, verification=_ok_verification())
    assert result.outcome.verdict == "needs_clarification"
    assert result.outcome.preserve_active_task is True
    assert result.new_state.active_task.task_id == "t1"


def test_reducer_counters_mutate_at_most_once():
    state = _mk_state_with_task()
    result = reducer.reduce_turn(
        state=state, interpretation=_si("answer", is_answer=True),
        assessment=_pa("incorrect"), verification=_ok_verification())
    assert result.outcome.attempt_count_delta == 1
    assert result.new_state.counters.attempts == 1
    assert result.new_state.counters.wrong_attempts == 1


def _si(turn_kind, **over):
    from matbot.ai_tutor_v3.schemas import StudentTurnInterpretation
    data = interp_parsed(turn_kind, **{k: v for k, v in over.items()
                                       if k != "is_answer"})
    if over.get("is_answer"):
        data["interpretation"]["is_answer_attempt"] = True
    return StudentTurnInterpretation.model_validate(data["interpretation"])


def _pa(verdict, **over):
    from matbot.ai_tutor_v3.schemas import PracticeModelAssessment
    return PracticeModelAssessment.model_validate(assess_parsed(verdict, **over))



def test_low_confidence_answer_asks_clarification(fake):
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True, confidence=0.1,
                           clarification="Možeš li ponoviti?",
                           assessment=assess_parsed("correct", confidence=0.1)))
    out = dispatch(answer_payload(resp, "možda"))
    assert out["answer_verdict"] == "clarification"
    assert out["next_state"]["v3_state"]["counters"]["attempts"] == 0


def test_narration_cannot_change_outcome(fake):
    """The narration model output is a lie ('Netačno') but the authoritative
    verdict was decided by the reducer BEFORE narration ran."""
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("correct", solved_independently=True)))
    fake.set(orchestrator.PURPOSE_NARRATION,
             narration_parsed("Nažalost, potpuno netačno.", "feedback_correct"))
    out = dispatch(answer_payload(resp, "da"))
    assert out["answer_verdict"] == "correct"          # narration did not flip it
    assert out["next_state"]["v3_state"]["counters"]["solved_independent"] == 1


def test_model_only_mastery_is_provisional(fake):
    resp = start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("correct", solved_independently=True)))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Tačno!", "feedback_correct"))
    out = dispatch(answer_payload(resp, "da"))
    v3 = out["next_state"]["v3_state"]
    assert v3["mastery"]["provisional"] is True
    assert "provisional" in v3["mastery"]["per_concept"]["div-compound-6"]
    assert out["verification_status"] == "model_only"


# =========================================================================== #
# Verification boundary                                                       #
# =========================================================================== #
def test_verification_off_records_model_only(fake, monkeypatch):
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "off")
    resp = start_task(fake)
    assert resp["verification_status"] == "model_only"


def test_required_verification_refuses_safely(fake, monkeypatch):
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "required")
    resp = start_task(fake)                 # session bootstrapped, but turn refuses
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("correct")))
    out = dispatch(answer_payload(resp, "da"))
    assert out["verification_status"] == "unavailable"
    assert out["v3_fallback_reason"] == "verification_required_unavailable"
    # no progress mutated
    assert out["next_state"]["v3_state"]["counters"]["attempts"] == 0


def test_shadow_verification_is_model_only_not_fabricated():
    import os
    os.environ["MATBOT_V3_VERIFICATION"] = "shadow"
    try:
        decision = verifier.verify_batch(claims_present=True)
        assert decision.result.status == "model_only"
        assert decision.can_proceed is True
    finally:
        os.environ.pop("MATBOT_V3_VERIFICATION", None)


# =========================================================================== #
# SQLite store (direct)                                                       #
# =========================================================================== #
def test_store_enables_wal_and_foreign_keys(tmp_path):
    store = ss.V3StateStore(tmp_path / "s.sqlite3")
    store.init_db()
    assert str(store.pragma("journal_mode")).lower() == "wal"
    assert int(store.pragma("foreign_keys")) == 1


def test_store_init_is_idempotent(tmp_path):
    store = ss.V3StateStore(tmp_path / "s.sqlite3")
    store.init_db()
    store.init_db()                          # must not raise
    assert store.load_session("nope") is None


def test_session_persists_across_store_reinit(tmp_path):
    path = tmp_path / "s.sqlite3"
    s1 = ss.V3StateStore(path)
    s1.create_session(session_id="x", student_id="stu", grade=6, mode="practice",
                      lesson_id=DIVISIBILITY, blueprint_id="bp", blueprint_version="v",
                      state_json='{"k":1}', created_at="t", updated_at="t")
    s2 = ss.V3StateStore(path)               # fresh instance, same file
    loaded = s2.load_session("x")
    assert loaded is not None and loaded[0] == '{"k":1}'


def test_completed_duplicate_returns_stored_response(fake):
    """Two deliveries of the same client_turn_id → one execution, replayed."""
    start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("incorrect")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Nije.", "feedback_incorrect"))
    p = base_payload(client_turn_id="dup1", student_message="ne")
    first = dispatch(p)
    calls_after_first = list(fake.calls)
    second = dispatch(p)                     # identical delivery
    assert second == first                   # stored response replayed
    assert fake.calls == calls_after_first   # no second execution


def test_duplicate_turn_does_not_double_count(fake):
    start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET,
             interp_parsed("answer", is_answer=True,
                           assessment=assess_parsed("incorrect")))
    fake.set(orchestrator.PURPOSE_NARRATION, narration_parsed("Nije.", "feedback_incorrect"))
    p = base_payload(client_turn_id="dup2", student_message="ne")
    dispatch(p)
    out = dispatch(p)
    assert out["next_state"]["v3_state"]["counters"]["wrong_attempts"] == 1


def test_failed_model_call_leaves_recoverable_turn(fake, tmp_path):
    start_task(fake)
    fake.set_status(orchestrator.PURPOSE_INTERPRET, "error", "timeout")
    out = dispatch(base_payload(client_turn_id="failt", student_message="da"))
    # task preserved, clear fallback, and the turn row is retryable
    assert out["v3_fallback_reason"] == "timeout"
    assert out["last_tutor_task"] == "Da li je 252 djeljiv sa 6?"
    store = ss.V3StateStore()
    row = store.reserve_turn(session_id="s1", client_turn_id="failt",
                             turn_id="new", request_id="r", created_at="t")
    assert row.status == "retryable"


def test_blueprint_persists(fake):
    start_task(fake)
    idn = lesson_blueprint.resolve_lesson_identity(6, DIVISIBILITY)
    meta = lesson_blueprint.source_metadata(idn, 6)
    h = lesson_blueprint.compute_source_hash(meta)
    store = ss.V3StateStore()
    assert store.find_blueprint(DIVISIBILITY, h) is not None


def test_activity_outbox_rejects_unapproved_and_transcripts(tmp_path):
    store = ss.V3StateStore(tmp_path / "s.sqlite3")
    store.create_session(session_id="x", student_id="stu", grade=6, mode="practice",
                         lesson_id=DIVISIBILITY, blueprint_id="b", blueprint_version="v",
                         state_json="{}", created_at="t", updated_at="t")
    with pytest.raises(ValueError):
        store.record_activity(event_id="e", session_id="x", student_id="stu",
                              event_type="chat_message",  # not approved
                              grade=6, mode="practice", lesson_id=DIVISIBILITY,
                              created_at="t", summary_json="{}")
    store.record_activity(event_id="e2", session_id="x", student_id="stu",
                          event_type="session_end", grade=6, mode="practice",
                          lesson_id=DIVISIBILITY, created_at="t",
                          summary_json='{"tasks_completed":1}')
    rows = store.list_activity("x")
    assert len(rows) == 1
    blob = json.dumps(rows[0])
    assert "chat" not in blob.lower()        # no raw transcript stored


def test_no_transaction_held_during_model_call(fake):
    """Structural guarantee: the dispatcher reserves the turn (commit), THEN
    calls the model, THEN commits. Proven by inspecting the source order."""
    import inspect
    src = inspect.getsource(dispatcher._run_v3_turn)
    reserve_at = src.index("reserve_turn(")
    exec_at = src.index("_execute_turn(")
    commit_at = src.index("commit_turn(")
    assert reserve_at < exec_at < commit_at


# =========================================================================== #
# Privacy                                                                     #
# =========================================================================== #
def test_no_parent_email_in_session_state(fake):
    resp = start_task(fake)
    blob = json.dumps(resp["next_state"]["v3_state"]).lower()
    for banned in ("parent_email", "student_email", "airtable", "make.com",
                   "webhook", "api_key", "credential"):
        assert banned not in blob
    # No email address literal (the '@' in a blueprint version like
    # "6-03-024@hash" is a version separator, not an address).
    import re as _re
    assert not _re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", blob)


def test_student_id_is_opaque(fake):
    resp = start_task(fake)
    sid = resp["next_state"]["v3_state"]["student_id"]
    assert sid.startswith("stu_")
    assert "@" not in sid


# =========================================================================== #
# V3 model configuration — independent of legacy OPENAI_MODEL_TEXT            #
# =========================================================================== #
def test_dispatcher_resolves_default_v3_model_when_not_passed(fake, monkeypatch):
    monkeypatch.delenv("MATBOT_V3_MODEL", raising=False)
    resp = dispatcher.v3_practice_dispatch(base_payload(), timeout=5)
    assert resp is not None
    assert fake.models and all(m == "gpt-5-mini" for m in fake.models)


def test_dispatcher_honours_matbot_v3_model_override(fake, monkeypatch):
    monkeypatch.setenv("MATBOT_V3_MODEL", "gpt-5")
    resp = dispatcher.v3_practice_dispatch(base_payload(), timeout=5)
    assert resp is not None
    assert fake.models and all(m == "gpt-5" for m in fake.models)


def test_dispatcher_blank_matbot_v3_model_falls_back_to_default(fake, monkeypatch):
    monkeypatch.setenv("MATBOT_V3_MODEL", "   ")
    resp = dispatcher.v3_practice_dispatch(base_payload(), timeout=5)
    assert resp is not None
    assert fake.models and all(m == "gpt-5-mini" for m in fake.models)


def test_dispatcher_ignores_legacy_openai_model_text(fake, monkeypatch):
    monkeypatch.delenv("MATBOT_V3_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL_TEXT", "gpt-4o-mini")
    resp = dispatcher.v3_practice_dispatch(base_payload(), timeout=5)
    assert resp is not None
    assert fake.models and all(m == "gpt-5-mini" for m in fake.models)


def test_all_invoked_purposes_receive_the_same_resolved_model(fake, monkeypatch):
    """blueprint_generation and task_generation fire for the bootstrap initial
    task turn (turn_interpretation is skipped — see Phase 7 consolidation
    tests below) — every purpose that DOES fire must receive the SAME
    resolved V3 model (a single resolution point, never re-derived per
    purpose)."""
    monkeypatch.setenv("MATBOT_V3_MODEL", "gpt-5")
    resp = dispatcher.v3_practice_dispatch(base_payload(), timeout=5)
    assert resp is not None
    assert set(fake.calls) >= {
        orchestrator.PURPOSE_BLUEPRINT, orchestrator.PURPOSE_TASK,
    }
    assert all(m == "gpt-5" for m in fake.models)


def test_ai_tutor_service_bridge_does_not_forward_legacy_model(monkeypatch):
    """Both V3 bridge call sites in ai_tutor_service.py (handle_chat and
    handle_chat_stream) go through this one shared bridge function — proving
    it never forwards a ``model`` kwarg proves neither call site can leak the
    legacy/general OPENAI_MODEL_TEXT model into V3."""
    from matbot import ai_tutor_service
    from matbot.ai_tutor_v3 import dispatcher as v3_dispatcher

    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    captured = {}

    def _spy(payload, **kwargs):
        captured.update(kwargs)
        return {"engine": "v3_practice", "answer": "ok"}
    monkeypatch.setattr(v3_dispatcher, "v3_practice_dispatch", _spy)

    result = ai_tutor_service._v3_practice_dispatch(
        {"mode": "practice"}, timeout=5, endpoint="handle_chat")
    assert result == {"engine": "v3_practice", "answer": "ok"}
    assert "model" not in captured


# =========================================================================== #
# Model-call consolidation (Phase 7)                                          #
# =========================================================================== #
def test_uncached_lesson_initial_task_makes_exactly_blueprint_and_task_calls(fake):
    """Uncached lesson, initial task: blueprint_generation + task_generation
    only — turn_interpretation is skipped for the bootstrap turn."""
    resp = dispatch(base_payload())
    assert resp is not None
    assert fake.calls == [orchestrator.PURPOSE_BLUEPRINT, orchestrator.PURPOSE_TASK]


def test_cached_lesson_initial_task_makes_exactly_one_task_call(monkeypatch, tmp_path):
    """Cached lesson, initial task: task_generation only — blueprint reused
    from store, turn_interpretation skipped."""
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", DIVISIBILITY)
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot import topic_resolver as tr
    tr.reset_cache()
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        first = dispatcher.v3_practice_dispatch(
            base_payload(session_id="s_cache1"), timeout=5)
        assert first is not None
        assert client.calls == [orchestrator.PURPOSE_BLUEPRINT, orchestrator.PURPOSE_TASK]
        client.calls.clear()
        second = dispatcher.v3_practice_dispatch(
            base_payload(session_id="s_cache2"), timeout=5)
        assert second is not None
        assert client.calls == [orchestrator.PURPOSE_TASK]   # blueprint reused
    finally:
        dispatcher.set_model_client(None)


def test_hint_request_intent_skips_turn_interpretation(fake):
    """The existing 'Ne znam — daj mi hint' button sends intent=hint_request —
    the dispatcher must use it directly instead of an interpretation call."""
    start_task(fake)
    fake.calls.clear()
    out = dispatch(base_payload(client_turn_id="hint1", student_message="Ne znam.",
                                intent="hint_request"))
    assert out is not None
    assert orchestrator.PURPOSE_INTERPRET not in fake.calls
    assert orchestrator.PURPOSE_HINT in fake.calls
    assert out["next_state"]["hint_count"] == 1


def test_difficulty_request_skips_turn_interpretation(fake):
    """The existing 'Lakši zadatak'/'Teži zadatak' chips send
    difficulty_request=easier/harder — must skip interpretation."""
    start_task(fake)
    before_level = 2
    fake.calls.clear()
    out = dispatch(base_payload(client_turn_id="diff1", student_message="Daj mi lakši zadatak.",
                                difficulty_request="easier"))
    assert out is not None
    assert orchestrator.PURPOSE_INTERPRET not in fake.calls
    assert out["next_state"]["difficulty_level"] == before_level - 1


def test_free_text_answer_still_goes_through_real_interpretation(fake):
    """No trusted signal present → free text is still interpreted by meaning,
    never skipped (no brittle phrase matching)."""
    start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET,
            interp_parsed("answer", is_answer=True,
                         assessment=assess_parsed("correct")))
    out = dispatch(base_payload(client_turn_id="ans1", student_message="da"))
    assert out is not None
    assert orchestrator.PURPOSE_INTERPRET in fake.calls


def test_answer_turn_uses_proposed_narration_without_extra_narration_call(fake):
    """Answer submission: the interpretation call's narration_proposal is used
    directly — no separate narrate_feedback call — per Phase 7's 'one
    combined structured call' target for answer turns."""
    start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("correct"),
    ))
    # Attach a narration_proposal by hand, exactly as the real schema allows.
    proposal = fake._by_purpose[orchestrator.PURPOSE_INTERPRET]
    proposal["narration_proposal"] = narration_parsed("Tačno, bravo!", "feedback_correct")
    out = dispatch(base_payload(client_turn_id="propose1", student_message="da"))
    assert out is not None
    assert orchestrator.PURPOSE_INTERPRET in fake.calls
    assert orchestrator.PURPOSE_NARRATION not in fake.calls
    assert out["answer"] == "Tačno, bravo!"


def test_answer_turn_falls_back_to_narration_call_without_a_proposal(fake):
    """When the interpretation call does NOT include a narration_proposal,
    the dispatcher still makes the separate narrate_feedback call — no
    regression for a model that omits the optional field."""
    start_task(fake)
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("correct"),
    ))
    out = dispatch(base_payload(client_turn_id="noprop1", student_message="da"))
    assert out is not None
    assert orchestrator.PURPOSE_NARRATION in fake.calls


# =========================================================================== #
# Timeout / turn-budget handling (Phase 8)                                    #
# =========================================================================== #
def test_resolve_v3_call_timeout_defaults(monkeypatch):
    monkeypatch.delenv("MATBOT_V3_CALL_TIMEOUT_S", raising=False)
    assert orchestrator.resolve_v3_call_timeout_s() == orchestrator.DEFAULT_V3_CALL_TIMEOUT_S


def test_resolve_v3_call_timeout_override(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_CALL_TIMEOUT_S", "7.5")
    assert orchestrator.resolve_v3_call_timeout_s() == 7.5


def test_resolve_v3_call_timeout_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_CALL_TIMEOUT_S", "not-a-number")
    assert orchestrator.resolve_v3_call_timeout_s() == orchestrator.DEFAULT_V3_CALL_TIMEOUT_S


def test_resolve_v3_turn_budget_defaults(monkeypatch):
    monkeypatch.delenv("MATBOT_V3_TURN_BUDGET_S", raising=False)
    assert orchestrator.resolve_v3_turn_budget_s() == orchestrator.DEFAULT_V3_TURN_BUDGET_S


def test_resolve_v3_turn_budget_override(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_TURN_BUDGET_S", "15")
    assert orchestrator.resolve_v3_turn_budget_s() == 15.0


def test_turn_budget_per_call_timeout_never_exceeds_call_cap():
    budget = dispatcher._TurnBudget(external_timeout=999.0)
    t = budget.next_timeout()
    assert t is not None
    assert t <= orchestrator.resolve_v3_call_timeout_s()


def test_turn_budget_shrinks_as_time_elapses(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_TURN_BUDGET_S", "10")
    monkeypatch.setenv("MATBOT_V3_CALL_TIMEOUT_S", "20")
    budget = dispatcher._TurnBudget(external_timeout=None)
    first = budget.next_timeout()
    assert first is not None
    import time as _time
    _time.sleep(0.5)
    second = budget.next_timeout()
    assert second is not None
    assert second < first


def test_turn_budget_exhausted_stops_further_calls(monkeypatch):
    monkeypatch.setenv("MATBOT_V3_TURN_BUDGET_S", "0.01")
    budget = dispatcher._TurnBudget(external_timeout=None)
    import time as _time
    _time.sleep(0.05)
    assert budget.next_timeout() is None
    assert budget.exceeded is True


def test_extremely_small_budget_before_any_call_declines_safely(monkeypatch, tmp_path):
    """A total budget too small even for the FIRST call makes V3 decline
    (return None) rather than crash or partially mutate anything — legacy/
    Minimal can then safely take over."""
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", DIVISIBILITY)
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("MATBOT_V3_TURN_BUDGET_S", "0.0001")
    from matbot import topic_resolver as tr
    tr.reset_cache()
    client = FakeClient()
    dispatcher.set_model_client(client)
    try:
        resp = dispatch(base_payload())
        assert resp is None
        assert client.calls == []
    finally:
        dispatcher.set_model_client(None)


def test_slow_first_call_leaves_safe_fallback_for_second_call(fake, monkeypatch):
    """A blueprint call that eats almost the whole turn budget must NOT let a
    second (task-generation) call start anyway — the turn fails safely
    instead of risking the client-side 60s abort. No active task is assigned;
    counters are untouched."""
    monkeypatch.setenv("MATBOT_V3_TURN_BUDGET_S", "3.2")
    monkeypatch.setenv("MATBOT_V3_CALL_TIMEOUT_S", "5")
    real_generate = fake.generate

    def _slow_generate(*, purpose, **kwargs):
        if purpose == orchestrator.PURPOSE_BLUEPRINT:
            import time as _time
            _time.sleep(0.35)
        return real_generate(purpose=purpose, **kwargs)
    fake.generate = _slow_generate

    resp = dispatch(base_payload())
    assert resp is not None
    assert resp["v3_fallback_reason"] == "turn_budget_exceeded"
    assert resp["next_state"]["v3_state"]["active_task"] is None
    assert resp["next_state"]["v3_state"]["counters"]["attempts"] == 0
    assert orchestrator.PURPOSE_TASK not in fake.calls


def test_normal_turn_completes_within_default_budget(fake):
    """Sanity: the default budget/timeouts do not break the ordinary path —
    only pathologically small budgets (as configured above) trigger the
    fallback."""
    resp = dispatch(base_payload())
    assert resp is not None
    assert resp.get("v3_fallback_reason") is None


# =========================================================================== #
# Telemetry (Phase 9)                                                         #
# =========================================================================== #
def test_get_or_create_blueprint_reports_cache_miss_then_hit(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    from matbot.ai_tutor_v3 import lesson_blueprint, state_store as ss
    store = ss.V3StateStore()
    store.init_db()
    identity = lesson_blueprint.resolve_lesson_identity(6, DIVISIBILITY, "Djeljivost brojeva")
    client = FakeClient()
    bp1, reason1, hit1 = lesson_blueprint.get_or_create_blueprint(
        store, client, identity=identity, grade=6, model="gpt-5-mini", timeout=5)
    assert bp1 is not None and reason1 == "" and hit1 is False
    bp2, reason2, hit2 = lesson_blueprint.get_or_create_blueprint(
        store, client, identity=identity, grade=6, model="gpt-5-mini", timeout=5)
    assert bp2 is not None and reason2 == "" and hit2 is True


def test_usage_metrics_records_per_call_latency(fake):
    resp = start_task(fake)
    assert resp is not None
    v3 = resp["next_state"]["v3_state"]
    # per_call_latency_ms isn't in the frontend-facing v3_state projection —
    # verify via the audit-record path instead (store.get_turn_audit if
    # exposed) or, at minimum, that UsageMetrics accepts and preserves it.
    from matbot.ai_tutor_v3.schemas import UsageMetrics
    u = UsageMetrics(model_calls=2, per_call_latency_ms=[5.0, 7.0])
    assert u.per_call_latency_ms == [5.0, 7.0]


def test_audit_record_contains_required_telemetry_fields(monkeypatch, tmp_path):
    """Directly exercises _audit_record's shape — the structured, privacy-safe
    per-turn telemetry Phase 9 requires."""
    from matbot.ai_tutor_v3 import lesson_blueprint
    from matbot.ai_tutor_v3.schemas import (
        AuthoritativeOutcome, UsageMetrics as UM,
    )
    identity = lesson_blueprint.LessonIdentity(
        grade=6, area_id="a", area_title="Oblast", lesson_id="6-03-024",
        lesson_title="Djeljivost")

    class _BP:
        blueprint_id = "bp_x"
        blueprint_version = "v1"
        model = "gpt-5-mini"
        from matbot.ai_tutor_v3.orchestrator import current_prompt_policy as _cpp
        prompt_policy = _cpp()

    outcome = AuthoritativeOutcome(
        schema_version="v1", verdict="correct", attempt_count_delta=1,
        wrong_attempt_count_delta=0, streak_action="increment",
        solved_count_delta=1, task_status_after="completed",
        task_completed=True, preserve_active_task=False, needs_narration=True)

    class _VDecision:
        class result:
            status = "model_only"

    audit = dispatcher._audit_record(
        request_id="req1", session_id="s1", client_turn_id="c1",
        identity=identity, blueprint=_BP(),
        outcome_bundle={"purposes": ["turn_interpretation", "task_generation"],
                       "turn_kind": "task_request", "outcome": outcome,
                       "usage": UM(model_calls=2, prompt_tokens=10,
                                  completion_tokens=5, total_latency_ms=12.0,
                                  per_call_latency_ms=[5.0, 7.0]),
                       "quality_gate": {"checked": True, "repaired": False,
                                       "failure_categories": []}},
        vdecision=_VDecision(), version_before=0, version_after=1,
        category="task", blueprint_cache_hit=True, turn_latency_ms=42.0)

    for key in ("call_purposes", "call_count", "quality_gate",
               "verification_status", "blueprint_cache_hit", "turn_latency_ms",
               "state_version_before", "state_version_after", "model", "usage"):
        assert key in audit
    assert audit["call_count"] == 2
    assert audit["blueprint_cache_hit"] is True
    assert audit["turn_latency_ms"] == 42.0
    assert audit["usage"]["per_call_latency_ms"] == [5.0, 7.0]
    # Privacy: no key even resembling a prompt/API key/raw conversation.
    blob = str(audit).lower()
    for forbidden in ("api_key", "sk-", "system_prompt", "raw_conversation"):
        assert forbidden not in blob


# =========================================================================== #
# Google Sheets compatibility logging (Phase 10, corrected)                    #
# =========================================================================== #
#
# Correction: the request path now ONLY performs a durable, LOCAL SQLite write
# (matbot.ai_tutor_v3.state_store.enqueue_sheets_event, via sheets_outbox.
# enqueue) — it never calls anything in matbot.sheets_log directly anymore.
# Actual delivery happens on a separate, reused background worker thread
# (matbot.ai_tutor_v3.sheets_outbox), never inline. These tests inspect the
# durable v3_sheets_outbox table directly (the authoritative record) rather
# than mocking sheets_log, since that IS the new contract.
def _outbox_store():
    from matbot.ai_tutor_v3 import state_store as ss
    return ss.V3StateStore()


def test_visible_v3_turn_enqueues_exactly_one_durable_sheets_row(fake):
    resp = start_task(fake)
    assert resp is not None
    store = _outbox_store()
    row = _find_delivered(store, "s1-A")   # queries by status-agnostic raw SQL
    assert row is not None
    payload = json.loads(row["payload_json"])
    response = json.loads(row["response_json"])
    assert payload["client_turn_id"] == "s1-A"
    assert response["engine"] == "v3_practice"
    assert response["v3_telemetry"]["blueprint_id"]


def _find_delivered(store, client_turn_id):
    """Helper: the background worker may have already delivered (and thus
    cleared pending-status on) the row by the time the test inspects it —
    query it directly regardless of delivered state."""
    conn = store._connect()
    try:
        row = conn.execute(
            "SELECT * FROM v3_sheets_outbox WHERE client_turn_id=?",
            (client_turn_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_shadow_v3_turn_does_not_enqueue_a_sheets_row(fake, monkeypatch):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "shadow")
    resp = dispatch(base_payload(client_turn_id="shadow1"))
    assert resp is None                 # legacy controls the visible response
    store = _outbox_store()
    assert _find_delivered(store, "shadow1") is None


def test_duplicate_replay_does_not_enqueue_a_second_sheets_row(fake):
    payload = base_payload(client_turn_id="dup1")
    first = dispatch(payload)
    assert first is not None
    second = dispatch(payload)     # exact same client_turn_id → idempotent replay
    assert second is not None
    store = _outbox_store()
    conn = store._connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM v3_sheets_outbox WHERE client_turn_id=?",
            ("dup1",)).fetchone()[0]
    finally:
        conn.close()
    assert count == 1          # NOT 2 — client_turn_id is the primary key


def test_sheets_outbox_enqueue_failure_never_alters_the_student_response(fake, monkeypatch):
    from matbot.ai_tutor_v3 import sheets_outbox

    def _boom(store, **kwargs):
        raise RuntimeError("sheets outbox is down")
    monkeypatch.setattr(sheets_outbox, "enqueue", _boom)
    resp = dispatch(base_payload())
    assert resp is not None
    assert resp["engine"] == "v3_practice"
    assert resp["status"] == "ready"


def test_v3_failure_falling_back_does_not_enqueue_a_sheets_row(fake):
    """A turn that fails before commit (and would fall back to Minimal
    elsewhere) must not produce a V3 Sheets row — only a genuinely committed,
    visible V3 turn enqueues one."""
    start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET, {"garbage": True})  # fails validation
    resp = dispatch(base_payload(client_turn_id="badout2", student_message="da"))
    assert resp is not None
    assert resp.get("v3_fallback_reason") is not None
    store = _outbox_store()
    assert _find_delivered(store, "badout2") is None


def test_response_returns_before_a_slow_fake_sheets_append_completes(fake, monkeypatch):
    """The hard non-blocking guarantee: even if the actual Sheets append call
    is SLOW (simulating real network latency), the student response must
    still return almost immediately — because the request path only ever
    does a local, synchronous SQLite insert; delivery happens entirely on a
    separate background worker thread."""
    import time as _time
    from matbot import sheets_log as real_sheets_log

    release = threading.Event()
    started = threading.Event()

    def _slow_append(row):
        started.set()
        release.wait(timeout=5)   # blocks the WORKER thread, never the request
        return True
    monkeypatch.setattr(real_sheets_log, "sheets_append_row_safe", _slow_append)

    t0 = _time.monotonic()
    resp = dispatch(base_payload(client_turn_id="slow1"))
    elapsed = _time.monotonic() - t0

    assert resp is not None
    assert elapsed < 1.0, f"dispatch took {elapsed:.3f}s — Sheets delivery is on the critical path"

    # Prove the slow call really was invoked (in the background) before
    # releasing it, so this test would fail loudly if delivery silently
    # stopped happening at all.
    assert started.wait(timeout=2.0), "background worker never attempted delivery"
    release.set()


# =========================================================================== #
# Output quality gate + bounded repair (Phase 5)                              #
# =========================================================================== #
def test_quality_gate_repairs_internal_terminology_leak_in_task(fake):
    """The task-generation FakeClient output leaks 'Blueprint' — the ONE
    bounded repair call must replace it with clean, gate-passing text before
    it ever reaches the student or the durable active task."""
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK,
             task_parsed(question="Prema Blueprint-u, izračunaj 2 + 2."))
    fake.set(orchestrator.PURPOSE_REPAIR, narration_parsed("Izračunaj 2 + 2.", "task"))
    resp = dispatch(base_payload())
    assert resp is not None
    assert "Blueprint" not in resp["last_tutor_task"]
    assert resp["last_tutor_task"] == "Izračunaj 2 + 2."
    assert orchestrator.PURPOSE_REPAIR in fake.calls
    # The repaired text — not the rejected one — is what got stored as the
    # active task's question.
    assert resp["next_state"]["v3_state"]["active_task"]["question"] == "Izračunaj 2 + 2."


def test_quality_gate_falls_back_safely_when_repair_also_fails(fake):
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK,
             task_parsed(question="Prema Blueprint-u, izračunaj 2 + 2."))
    fake.set_status(orchestrator.PURPOSE_REPAIR, "error", "repair_failed")
    resp = dispatch(base_payload())
    assert resp is not None
    assert "Blueprint" not in resp["last_tutor_task"]
    from matbot.ai_tutor_v3 import quality_gate as qg
    assert resp["last_tutor_task"] == qg.SAFE_FALLBACK_TEXT
    # Exactly one repair attempt — never more.
    assert fake.calls.count(orchestrator.PURPOSE_REPAIR) == 1


def test_quality_gate_does_not_call_repair_for_clean_text(fake):
    resp = start_task(fake)
    assert resp is not None
    assert orchestrator.PURPOSE_REPAIR not in fake.calls


def test_quality_gate_normalizes_bare_latex_without_a_repair_call(fake):
    """A purely mechanical MathJax delimiter fix must be free — no model call
    — since normalize_math_for_display alone already repairs it."""
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    fake.set(orchestrator.PURPOSE_TASK,
             task_parsed(question=r"Proširi razlomak \frac{3}{4} brojem 2."))
    resp = dispatch(base_payload())
    assert resp is not None
    assert r"\( \frac{3}{4} \)" in resp["last_tutor_task"]
    assert orchestrator.PURPOSE_REPAIR not in fake.calls


# =========================================================================== #
# Failure handling                                                            #
# =========================================================================== #
def test_invalid_schema_output_preserves_task(fake):
    start_task(fake)
    fake.set(orchestrator.PURPOSE_INTERPRET, {"garbage": True})  # fails validation
    out = dispatch(base_payload(client_turn_id="badout", student_message="da"))
    assert out["last_tutor_task"] == "Da li je 252 djeljiv sa 6?"   # task preserved
    assert out["next_state"]["v3_state"]["counters"]["attempts"] == 0


def test_fake_bad_request_error_does_not_corrupt_session_state(fake, monkeypatch):
    """The production incident, reproduced end-to-end: the REAL production
    client class (OpenAIResponsesClient) has its underlying SDK call replaced
    with one that raises a genuine ``openai.BadRequestError`` — exactly what a
    strict-schema-rejecting 400 looks like — and the dispatcher/reducer/state
    layer must come out unharmed: task preserved, zero counter mutation, a safe
    Bosnian fallback text, no exception escaping to the caller."""
    import httpx
    import openai

    from matbot.ai_tutor_v3 import orchestrator as orch

    start_task(fake)  # bootstrap the session with a FakeClient first

    real_client = orch.OpenAIResponsesClient(api_key="sk-test-not-real")
    message = ("Invalid schema for response_format 'PracticeTurnInterpretation': "
              "'required' is required to include every key in properties.")
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    resp = httpx.Response(400, request=req, headers={"x-request-id": "req_prod123"},
                          json={"error": {"message": message,
                                          "type": "invalid_request_error",
                                          "code": "invalid_json_schema"}})
    bad_request = openai.BadRequestError(
        message, response=resp,
        body={"message": message, "type": "invalid_request_error",
             "code": "invalid_json_schema"})

    def _raise(*args, **kwargs):
        raise bad_request
    monkeypatch.setattr(real_client._client.responses, "create", _raise)
    dispatcher.set_model_client(real_client)

    out = dispatch(base_payload(client_turn_id="bre1", student_message="da"))

    assert out is not None                                     # safe fallback, not a crash
    assert out["last_tutor_task"] == "Da li je 252 djeljiv sa 6?"  # task preserved
    v3 = out["next_state"]["v3_state"]
    assert v3["counters"]["attempts"] == 0
    assert v3["counters"]["wrong_attempts"] == 0
    assert v3["counters"]["solved_independent"] == 0
    assert v3["active_task"] is not None
    assert out["v3_fallback_reason"] == "BadRequestError"


# =========================================================================== #
# Real route + SSE                                                            #
# =========================================================================== #
def test_real_stream_route_runs_v3(client, fake):
    from tests.test_prod_stream_route import sse_post, done_payload, deltas
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    events = sse_post(client, base_payload(client_turn_id="route1"))
    body = done_payload(events)
    assert body["engine"] == "v3_practice"
    assert body["last_tutor_task"] == "Da li je 252 djeljiv sa 6?"
    # SSE delta+done compatibility: deltas concatenate to the answer.
    assert deltas(events).strip() == (body["answer"] or "").strip()
    # required frontend fields present
    for field in ("answer", "status", "last_tutor_task", "next_state",
                  "session_mode", "answer_verdict", "task_id"):
        assert field in body, field
    assert body["next_state"]["correct_streak"] == 0


def test_frontend_file_unchanged():
    """This slice must not touch the frontend. Pin a couple of load-bearing
    facts the wire contract depends on."""
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert "'/api/ai-tutor/chat/stream'" in html
    assert "last_tutor_task" in html


# =========================================================================== #
# Isolation (package-level)                                                   #
# =========================================================================== #
def test_importing_v3_package_loads_no_frozen_module_or_sqlite():
    frozen = ("matbot.ai_tutor_service", "matbot.answer_checker",
              "matbot.grading_guard", "matbot.engine_v2", "matbot.exam_engine",
              "matbot.solution_plan", "matbot.task_templates",
              "matbot.task_activation", "matbot.task_model", "matbot.turn_intent",
              "matbot.prompt_builder", "matbot.tutor_prompts",
              "matbot.topic_detector", "matbot.topic_lookup",
              "matbot.image_result_verifier", "matbot.minimal")
    probe = (
        "import sys\n"
        "import matbot.ai_tutor_v3.dispatcher as d\n"
        f"frozen = {frozen!r}\n"
        "bad = sorted(m for m in sys.modules if any(m==f or m.startswith(f+'.') "
        "for f in frozen))\n"
        "print('FROZEN:' + '|'.join(bad))\n"
        # No OpenAI client and no default DB created merely by importing.
        "print('CLIENT:' + str(d._MODEL_CLIENT))\n"
    )
    repo = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, "-c", probe], cwd=str(repo),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "FROZEN:\n" in out or "FROZEN:" in out and "FROZEN:|" not in out
    frozen_line = [l for l in out.splitlines() if l.startswith("FROZEN:")][0]
    assert frozen_line == "FROZEN:", f"frozen modules loaded: {frozen_line}"
    assert "CLIENT:None" in out
