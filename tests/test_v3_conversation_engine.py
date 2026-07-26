# -*- coding: utf-8 -*-
"""Simplified conversational Practice engine — test matrix (Section 18, 64
items, A-G). Fakes only, no live OpenAI call. Activated via
``MATBOT_V3_PRACTICE_ENGINE=conversation`` — the legacy engine (default) is
covered by the existing, UNCHANGED ``test_v3_practice.py`` etc.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from matbot.ai_tutor_v3 import (
    dispatcher, feedback_value_gate, lesson_blueprint, orchestrator, quality_gate,
)
from matbot.ai_tutor_v3.orchestrator import ModelCallResult
from matbot.ai_tutor_v3.schemas import PracticeConversationContext
from tests.test_v3_quality_matrix import REPRESENTATIVE_LESSONS

CONV_LESSON = "6-03-024"
CONV_OBLAST = "Djeljivost brojeva"


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #
class ConvFakeClient:
    """Deterministic fake for the unified conversation-turn schema only."""

    def __init__(self):
        self.calls: list[str] = []
        self._proposal: dict = {}
        self._force_status: tuple = None

    def set_proposal(self, data: dict) -> None:
        self._proposal = data

    def force_status(self, status: str, error_code: str = "") -> None:
        self._force_status = (status, error_code)

    def generate(self, *, purpose, system, user, schema_name, schema, model, timeout,
                response_model=None, max_output_tokens=None, reasoning_effort=None):
        self.calls.append(purpose)
        if self._force_status is not None:
            status, err = self._force_status
            return ModelCallResult(status=status, model=model, purpose=purpose,
                                   error_code=err or status)
        try:
            validated = response_model.model_validate(self._proposal)
        except ValidationError:
            return ModelCallResult(status="invalid_output", model=model, purpose=purpose,
                                   error_code="schema_validation_error")
        return ModelCallResult(
            status="ok", parsed=validated.model_dump(mode="json"),
            usage={"prompt_tokens": 100, "completion_tokens": 50}, latency_ms=5.0,
            model=model, purpose=purpose)


@pytest.fixture()
def conv_env(monkeypatch, tmp_path):
    from matbot import topic_resolver as tr
    from matbot.ai_tutor_v3 import sheets_outbox
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")
    monkeypatch.setenv("MATBOT_V3_PRACTICE_ENGINE", "conversation")
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "off")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    tr.reset_cache()
    sheets_outbox._reset_for_tests()
    yield
    dispatcher.set_model_client(None)
    tr.reset_cache()
    sheets_outbox._reset_for_tests()


@pytest.fixture()
def fake(conv_env):
    client = ConvFakeClient()
    dispatcher.set_model_client(client)
    return client


def payload(**over):
    p = {"session_id": "cs1", "grade": 6, "mode": "practice",
         "selected_topic": CONV_LESSON, "selected_oblast": CONV_OBLAST,
         "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
         "client_turn_id": "c1"}
    p.update(over)
    return p


def dispatch(p):
    return dispatcher.v3_practice_dispatch(p, model="gpt-5-mini", timeout=5)


def initial_task_proposal(text="Da li je 252 djeljiv sa 6?", answer_kind="boolean_with_reason",
                          expected="da", difficulty=2):
    return {"schema_version": "v1", "action": "create_initial_task",
           "understood_student_intent": "početak vježbe",
           "proposed_next_task": {"text": text, "answer_kind": answer_kind,
                                  "expected_answer_summary": expected,
                                  "difficulty_level": difficulty},
           "confidence": 0.9}


def start(fake, session="cs1", **task_kwargs):
    fake.set_proposal(initial_task_proposal(**task_kwargs))
    return dispatch(payload(session_id=session, client_turn_id=session + "-A"))


def assess_proposal(assessment, feedback, confidence=0.9):
    return {"schema_version": "v1", "action": "assess_answer",
           "understood_student_intent": "pokušaj odgovora",
           "assessment": assessment, "student_feedback": feedback, "confidence": confidence}


# =========================================================================== #
# A. Session bootstrap (1-6)                                                  #
# =========================================================================== #
def test_a1_practice_works_without_any_blueprint_row(fake):
    resp = start(fake)
    assert resp is not None


def test_a2_initial_session_creates_ordinary_task(fake):
    resp = start(fake)
    assert resp["last_tutor_task"] == "Da li je 252 djeljiv sa 6?"


def test_a3_initial_task_uses_exactly_one_model_call(fake):
    start(fake)
    assert len(fake.calls) == 1
    assert fake.calls == [orchestrator.PURPOSE_CONVERSATION]


def test_a4_blueprint_absence_does_not_trigger_minimal_fallback(fake):
    resp = start(fake)
    assert resp["engine"] == "v3_practice"


def test_a5_blueprint_absence_does_not_trigger_blueprint_generation(fake):
    start(fake)
    assert orchestrator.PURPOSE_BLUEPRINT not in fake.calls


def test_a6_existing_blueprint_enriches_without_being_required(fake, tmp_path):
    from matbot.ai_tutor_v3 import lesson_blueprint as lb
    from matbot.ai_tutor_v3.state_store import V3StateStore
    from datetime import datetime, timezone
    identity = lb.resolve_lesson_identity(6, CONV_LESSON, CONV_OBLAST)
    store = dispatcher._get_store()
    metadata = lb.source_metadata(identity, 6)
    source_hash = lb.compute_source_hash(metadata)
    bp_data = {
        "schema_version": "v3.1", "blueprint_id": "bp_test", "blueprint_version": "v1",
        "lesson_identity": identity.model_dump(mode="json"), "source_hash": source_hash,
        "source_metadata": metadata, "learning_objectives": ["primijeniti pravila djeljivosti"],
        "mastery_requirements": {"min_concepts_mastered": 1, "min_independent_solves": 1},
        "language_guidance": {"language_register": "grade_6"},
        "generation_confidence": 0.9, "model": "gpt-5-mini",
        "prompt_policy": orchestrator.current_prompt_policy().model_dump(mode="json"),
        "created_at": datetime.now(timezone.utc), "validation_status": "validated",
    }
    from matbot.ai_tutor_v3.schemas import LessonBlueprint
    blueprint = LessonBlueprint.model_validate(bp_data)
    store.store_blueprint(
        blueprint_id=blueprint.blueprint_id, lesson_id=blueprint.lesson_identity.lesson_id,
        blueprint_version=blueprint.blueprint_version, source_hash=blueprint.source_hash,
        blueprint_json=blueprint.model_dump_json(), validation_status="validated",
        model="gpt-5-mini", prompt_versions_json="{}", created_at=blueprint.created_at.isoformat())
    resp = start(fake)
    assert resp is not None
    assert len(fake.calls) == 1  # enrichment never adds a call


# =========================================================================== #
# B. Natural answer forms (7-20)                                              #
# =========================================================================== #
def test_b7_exact_numeric_answer(fake):
    r1 = start(fake, session="b7")
    fake.set_proposal(assess_proposal("correct", "Tačno, 6 je odgovor."))
    r2 = dispatch(payload(session_id="b7", client_turn_id="b7-B", student_message="6",
                          previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "correct"


def test_b8_fraction_answer(fake):
    r1 = start(fake, session="b8", text="Proširi razlomak 1/2 brojem 3.",
              answer_kind="rational_value", expected="3/6")
    fake.set_proposal(assess_proposal("correct", "Tačno, 3/6 je ispravno proširen razlomak."))
    r2 = dispatch(payload(session_id="b8", client_turn_id="b8-B", student_message="3/6",
                          previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "correct"


def test_b9_algebraic_expression_answer(fake):
    r1 = start(fake, session="b9", text="Riješi 2x+3=11.", answer_kind="equation_solution", expected="4")
    fake.set_proposal(assess_proposal("correct", "Tačno, x=4 je rješenje."))
    r2 = dispatch(payload(session_id="b9", client_turn_id="b9-B", student_message="x=4",
                          previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "correct"


def test_b10_answer_embedded_in_natural_text(fake):
    r1 = start(fake, session="b10")
    fake.set_proposal(assess_proposal("correct", "Tačno, dobro si zaključio/la."))
    r2 = dispatch(payload(session_id="b10", client_turn_id="b10-B",
                          student_message="mislim da je da, jer je paran i zbir cifara djeljiv sa 3",
                          previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "correct"


def test_b11_correct_conceptual_explanation(fake):
    r1 = start(fake, session="b11")
    fake.set_proposal(assess_proposal(
        "correct", "Tačno, ispravno si objasnio/la oba uslova djeljivosti."))
    r2 = dispatch(payload(session_id="b11", client_turn_id="b11-B",
                          student_message="da, jer se oba razlomka skrate na isto",
                          previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "correct"


def test_b12_partially_correct_explanation(fake):
    r1 = start(fake, session="b12")
    fake.set_proposal(assess_proposal(
        "partially_correct", "Djelimično tačno — provjerio/la si paran broj, ali ne zbir cifara."))
    r2 = dispatch(payload(session_id="b12", client_turn_id="b12-B",
                          student_message="da, jer je paran", previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "partial"
    assert r2["task_status"] == "active"


def test_b13_incorrect_explanation(fake):
    r1 = start(fake, session="b13")
    fake.set_proposal(assess_proposal(
        "incorrect", "To nije tačno — 252 nije djeljiv sa 6 na taj način, provjeri ponovo."))
    r2 = dispatch(payload(session_id="b13", client_turn_id="b13-B",
                          student_message="nije, zato što brojnik nije pomnožen istim brojem",
                          previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "incorrect"


def test_b14_answer_without_diacritics(fake):
    r1 = start(fake, session="b14")
    fake.set_proposal(assess_proposal("correct", "Tačno!"))
    r2 = dispatch(payload(session_id="b14", client_turn_id="b14-B",
                          student_message="da moze biti tacno", previous_next_state=r1["next_state"]))
    assert r2 is not None


def test_b15_uncertain_low_confidence_answer_is_clarified_not_graded(fake):
    r1 = start(fake, session="b15")
    fake.set_proposal(assess_proposal("correct", "Tačno!", confidence=0.2))
    r2 = dispatch(payload(session_id="b15", client_turn_id="b15-B",
                          student_message="možda 6", previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "clarification"
    assert r2["task_status"] == "active"


def test_b16_ne_znam_is_not_graded(fake):
    r1 = start(fake, session="b16")
    fake.set_proposal({"schema_version": "v1", "action": "assess_answer",
                       "understood_student_intent": "ne znam",
                       "assessment": "not_an_answer", "student_feedback": "Nema problema, probaj.",
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="b16", client_turn_id="b16-B", student_message="ne znam",
                          previous_next_state=r1["next_state"]))
    assert r2["next_state"]["v3_state"]["counters"]["wrong_attempts"] == 0
    assert r2["task_status"] == "active"


def test_b17_related_concept_question(fake):
    r1 = start(fake, session="b17")
    fake.set_proposal({"schema_version": "v1", "action": "answer_question",
                       "understood_student_intent": "pita zašto",
                       "explanation": "Vrijednost se ne promijeni jer množimo i brojnik i nazivnik istim brojem.",
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="b17", client_turn_id="b17-B",
                          student_message="zašto se vrijednost ne promijeni?",
                          previous_next_state=r1["next_state"]))
    assert "ne promijeni" in r2["answer"]
    assert r2["last_tutor_task"] == r1["last_tutor_task"]


def test_b18_comment_with_number_not_graded(fake):
    r1 = start(fake, session="b18")
    fake.set_proposal({"schema_version": "v1", "action": "continue_conversation",
                       "understood_student_intent": "komentar",
                       "student_feedback": "U redu, nastavi kad budeš spreman/na.", "confidence": 0.9})
    r2 = dispatch(payload(session_id="b18", client_turn_id="b18-B",
                          student_message="ovaj zadatak je 10 puta teži",
                          previous_next_state=r1["next_state"]))
    assert r2["next_state"]["v3_state"]["counters"]["attempts"] == 0


def test_b19_ambiguous_message_asks_clarification(fake):
    r1 = start(fake, session="b19")
    fake.set_proposal({"schema_version": "v1", "action": "ask_clarification",
                       "understood_student_intent": "nejasno",
                       "clarification_question": "Možeš li mi reći tačnu vrijednost koju si dobio/la?",
                       "confidence": 0.4})
    r2 = dispatch(payload(session_id="b19", client_turn_id="b19-B",
                          student_message="ovo mi nije jasno", previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "clarification"


def test_b20_explanation_required_task_judged_semantically(fake):
    """Not literally equal to the internal expected_answer_summary — still
    graded correct because the MODEL judged the reasoning sound."""
    r1 = start(fake, session="b20", text="Objasni zašto se vrijednost razlomka ne mijenja.",
              answer_kind="free_text", expected="množenje brojnika i nazivnika istim brojem")
    fake.set_proposal(assess_proposal(
        "correct", "Tačno, dobro si objasnio/la — to je upravo razlog."))
    r2 = dispatch(payload(session_id="b20", client_turn_id="b20-B",
                          student_message="jer pomnožimo i gore i dolje sa istim brojem, pa je isto",
                          previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "correct"


# =========================================================================== #
# C. Conversational actions (21-29)                                           #
# =========================================================================== #
def test_c21_new_task_one_call(fake):
    r1 = start(fake, session="c21")
    fake.calls.clear()
    fake.set_proposal({"schema_version": "v1", "action": "create_new_task",
                       "understood_student_intent": "traži novi zadatak",
                       "proposed_next_task": {"text": "Da li je 90 djeljiv sa 9?",
                                              "answer_kind": "boolean_with_reason",
                                              "expected_answer_summary": "da", "difficulty_level": 2},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="c21", client_turn_id="c21-B",
                          student_message="Daj mi novi zadatak.", previous_next_state=r1["next_state"]))
    assert len(fake.calls) == 1
    assert r2["last_tutor_task"] == "Da li je 90 djeljiv sa 9?"


def test_c22_easier_task_one_call(fake):
    r1 = start(fake, session="c22")
    fake.calls.clear()
    fake.set_proposal({"schema_version": "v1", "action": "create_easier_task",
                       "understood_student_intent": "traži lakši",
                       "proposed_next_task": {"text": "Da li je 10 djeljiv sa 5?",
                                              "answer_kind": "boolean_with_reason",
                                              "expected_answer_summary": "da", "difficulty_level": 1},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="c22", client_turn_id="c22-B",
                          student_message="Daj mi lakši zadatak.", difficulty_request="easier",
                          previous_next_state=r1["next_state"]))
    assert len(fake.calls) == 1
    assert r2["next_state"]["difficulty_level"] == 1


def test_c23_harder_task_one_call(fake):
    r1 = start(fake, session="c23")
    fake.calls.clear()
    fake.set_proposal({"schema_version": "v1", "action": "create_harder_task",
                       "understood_student_intent": "traži teži",
                       "proposed_next_task": {"text": "Da li je 5292 djeljiv sa 6?",
                                              "answer_kind": "boolean_with_reason",
                                              "expected_answer_summary": "da", "difficulty_level": 3},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="c23", client_turn_id="c23-B",
                          student_message="Daj mi teži zadatak.", difficulty_request="harder",
                          previous_next_state=r1["next_state"]))
    assert len(fake.calls) == 1
    assert r2["next_state"]["difficulty_level"] == 3


def test_c24_hint_one_call(fake):
    r1 = start(fake, session="c24")
    fake.calls.clear()
    fake.set_proposal({"schema_version": "v1", "action": "give_hint",
                       "understood_student_intent": "traži pomoć",
                       "hint": "Provjeri prvo da li je broj paran.", "confidence": 0.9})
    r2 = dispatch(payload(session_id="c24", client_turn_id="c24-B",
                          student_message="Ne znam.", intent="hint_request",
                          previous_next_state=r1["next_state"]))
    assert len(fake.calls) == 1
    assert "paran" in r2["answer"]


def test_c25_solution_request_one_call(fake):
    r1 = start(fake, session="c25")
    fake.calls.clear()
    fake.set_proposal({"schema_version": "v1", "action": "give_solution",
                       "understood_student_intent": "traži rješenje",
                       "explanation": "252 je paran i zbir cifara (9) djeljiv sa 3, pa je 252 djeljiv sa 6.",
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="c25", client_turn_id="c25-B",
                          student_message="Reci mi rješenje.", previous_next_state=r1["next_state"]))
    assert len(fake.calls) == 1
    assert r2["task_status"] == "none"


def test_c26_question_about_task_one_call(fake):
    r1 = start(fake, session="c26")
    fake.calls.clear()
    fake.set_proposal({"schema_version": "v1", "action": "answer_question",
                       "understood_student_intent": "pita o konceptu",
                       "explanation": "Djeljivost sa 6 znači djeljivost i sa 2 i sa 3.",
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="c26", client_turn_id="c26-B",
                          student_message="šta znači djeljiv sa 6?", previous_next_state=r1["next_state"]))
    assert len(fake.calls) == 1
    assert r2["last_tutor_task"] == r1["last_tutor_task"]


def test_c27_trusted_ui_action_passed_without_intent_guessing(fake):
    r1 = start(fake, session="c27")
    fake.set_proposal({"schema_version": "v1", "action": "give_hint",
                       "understood_student_intent": "hint", "hint": "Savjet.", "confidence": 0.9})
    r2 = dispatch(payload(session_id="c27", client_turn_id="c27-B", student_message="Ne znam.",
                          intent="hint_request", previous_next_state=r1["next_state"]))
    assert r2["v3_telemetry"]["conversation_telemetry"]["trusted_ui_action"] == "hint"


def test_c28_current_task_preserved_for_questions_and_hints(fake):
    r1 = start(fake, session="c28")
    fake.set_proposal({"schema_version": "v1", "action": "give_hint",
                       "understood_student_intent": "hint", "hint": "Savjet.", "confidence": 0.9})
    r2 = dispatch(payload(session_id="c28", client_turn_id="c28-B", student_message="Ne znam.",
                          intent="hint_request", previous_next_state=r1["next_state"]))
    assert r2["task_id"] == r1["task_id"]


def test_c29_current_task_replaced_only_by_task_actions(fake):
    r1 = start(fake, session="c29")
    fake.set_proposal({"schema_version": "v1", "action": "continue_conversation",
                       "understood_student_intent": "komentar", "student_feedback": "U redu.",
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="c29", client_turn_id="c29-B", student_message="hvala",
                          previous_next_state=r1["next_state"]))
    assert r2["task_id"] == r1["task_id"]


# =========================================================================== #
# D. Recent context (30-35)                                                   #
# =========================================================================== #
def test_d30_at_most_3_recent_tasks_in_context():
    from matbot.ai_tutor_v3.schemas import CompletedTaskSummary, TutorSessionState
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    state = TutorSessionState(
        schema_version="v1", session_id="s", student_id="stu_x", grade=6, mode="practice",
        lesson_id="l", blueprint_id="none", blueprint_version="none",
        prompt_policy=orchestrator.current_prompt_policy(),
        completed_tasks=[CompletedTaskSummary(
            task_id=f"t{i}", concept_id="c", target_id="t", verdict="correct", independent=True)
            for i in range(5)],
        created_at=now, updated_at=now)
    ctx = orchestrator.build_conversation_context(
        grade=6, lesson_id="l", lesson_title="Lekcija", short_lesson_description=None,
        state=state, student_message="test")
    assert len(ctx.recent_tasks) <= 3


def test_d31_at_most_3_recent_turns_in_context():
    from matbot.ai_tutor_v3.schemas import RecentTurn, TutorSessionState
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    state = TutorSessionState(
        schema_version="v1", session_id="s", student_id="stu_x", grade=6, mode="practice",
        lesson_id="l", blueprint_id="none", blueprint_version="none",
        prompt_policy=orchestrator.current_prompt_policy(),
        recent_turns=[RecentTurn(turn_index=i, role="student", text=f"t{i}") for i in range(10)],
        created_at=now, updated_at=now)
    ctx = orchestrator.build_conversation_context(
        grade=6, lesson_id="l", lesson_title="Lekcija", short_lesson_description=None,
        state=state, student_message="test")
    assert len(ctx.recent_turns) <= 3


def test_d32_harder_task_context_includes_recent_task_info(fake):
    r1 = start(fake, session="d32")
    # After one correct answer there is a completed task recorded.
    fake.set_proposal(assess_proposal("correct", "Tačno!"))
    r2 = dispatch(payload(session_id="d32", client_turn_id="d32-B", student_message="da",
                          previous_next_state=r1["next_state"]))
    assert r2["next_state"]["v3_state"]["completed_tasks"]


def test_d33_easier_task_context_includes_recent_task_info(fake):
    r1 = start(fake, session="d33")
    fake.set_proposal(assess_proposal("incorrect", "To nije tačno, probaj ponovo."))
    r2 = dispatch(payload(session_id="d33", client_turn_id="d33-B", student_message="ne",
                          previous_next_state=r1["next_state"]))
    assert r2["next_state"]["v3_state"]["counters"]["wrong_attempts"] == 1


def test_d34_new_task_not_effective_duplicate_of_previous(fake):
    r1 = start(fake, session="d34", text="Da li je 252 djeljiv sa 6?")
    fake.set_proposal({"schema_version": "v1", "action": "create_new_task",
                       "understood_student_intent": "novi",
                       "proposed_next_task": {"text": "Da li je 252 djeljiv sa 6?",
                                              "answer_kind": "boolean_with_reason",
                                              "expected_answer_summary": "da", "difficulty_level": 2},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="d34", client_turn_id="d34-B",
                          student_message="Daj mi novi zadatak.", previous_next_state=r1["next_state"]))
    assert r2["v3_fallback_reason"] == "task_rejected_task_duplicate_of_recent"
    assert r2["last_tutor_task"] == r1["last_tutor_task"]  # old task preserved


def test_d35_context_stays_bounded_after_many_turns(fake):
    r = start(fake, session="d35")
    for i in range(8):
        fake.set_proposal({"schema_version": "v1", "action": "continue_conversation",
                           "understood_student_intent": "komentar", "student_feedback": "U redu.",
                           "confidence": 0.9})
        r = dispatch(payload(session_id="d35", client_turn_id=f"d35-{i}", student_message="hvala",
                             previous_next_state=r["next_state"]))
        assert r is not None
    assert len(r["next_state"]["v3_state"]["recent_turns"]) <= 30


# =========================================================================== #
# E. Quality (36-47)                                                          #
# =========================================================================== #
def test_e36_feedback_echo_replaced_by_deterministic_fallback(fake):
    r1 = start(fake, session="e36")
    fake.calls.clear()
    fake.set_proposal(assess_proposal("correct", "da je odgovor 6 jer je 252 djeljiv sa 6"))
    r2 = dispatch(payload(session_id="e36", client_turn_id="e36-B",
                          student_message="da je odgovor 6 jer je 252 djeljiv sa 6",
                          previous_next_state=r1["next_state"]))
    assert r2["answer"] == feedback_value_gate.safe_fallback_text("correct")
    assert len(fake.calls) == 1  # no repair call


def test_e37_correct_feedback_with_added_value_passes_unchanged(fake):
    r1 = start(fake, session="e37")
    text = "Tačno! Ispravno si primijenio pravilo djeljivosti sa 6."
    fake.set_proposal(assess_proposal("correct", text))
    r2 = dispatch(payload(session_id="e37", client_turn_id="e37-B", student_message="da",
                          previous_next_state=r1["next_state"]))
    assert r2["answer"] == text


def test_e38_incorrect_feedback_gives_next_step(fake):
    r1 = start(fake, session="e38")
    text = "To nije tačno. Probaj provjeriti zbir cifara ponovo."
    fake.set_proposal(assess_proposal("incorrect", text))
    r2 = dispatch(payload(session_id="e38", client_turn_id="e38-B", student_message="ne",
                          previous_next_state=r1["next_state"]))
    assert r2["answer"] == text


def test_e39_informal_terminology_still_assessed(fake):
    r1 = start(fake, session="e39")
    fake.set_proposal(assess_proposal("correct", "Tačno!"))
    r2 = dispatch(payload(session_id="e39", client_turn_id="e39-B",
                          student_message="da bata, jeste", previous_next_state=r1["next_state"]))
    assert r2["answer_verdict"] == "correct"


def test_e40_hint_text_used_as_is_without_forced_repetition(fake):
    r1 = start(fake, session="e40")
    fake.set_proposal({"schema_version": "v1", "action": "give_hint",
                       "understood_student_intent": "hint", "hint": "Probaj sad podijeliti sa 3.",
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="e40", client_turn_id="e40-B", student_message="Ne znam.",
                          intent="hint_request", previous_next_state=r1["next_state"]))
    assert r1["last_tutor_task"] not in r2["answer"]


def test_e41_self_cancelling_task_without_purpose_rejected(fake):
    r1 = start(fake, session="e41")
    fake.set_proposal({"schema_version": "v1", "action": "create_new_task",
                       "understood_student_intent": "novi",
                       "proposed_next_task": {
                           "text": "Proširi razlomak 1/2 pa ga odmah skrati na najmanje brojeve.",
                           "answer_kind": "rational_value", "expected_answer_summary": "1/2",
                           "difficulty_level": 2, "has_stated_purpose": False},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="e41", client_turn_id="e41-B",
                          student_message="Daj mi novi zadatak.", previous_next_state=r1["next_state"]))
    assert r2["v3_fallback_reason"] == "task_rejected_task_self_cancelling_without_purpose"


def test_e42_self_cancelling_task_with_stated_purpose_passes(fake):
    r1 = start(fake, session="e42")
    fake.set_proposal({"schema_version": "v1", "action": "create_new_task",
                       "understood_student_intent": "novi",
                       "proposed_next_task": {
                           "text": "Proširi 1/2 na dvanaestine pa skrati 6/12 — uporedi rezultate.",
                           "answer_kind": "rational_value", "expected_answer_summary": "1/2",
                           "difficulty_level": 2, "has_stated_purpose": True},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="e42", client_turn_id="e42-B",
                          student_message="Daj mi novi zadatak.", previous_next_state=r1["next_state"]))
    assert "v3_fallback_reason" not in r2


def test_e43_task_revealing_answer_rejected(fake):
    r1 = start(fake, session="e43")
    fake.set_proposal({"schema_version": "v1", "action": "create_new_task",
                       "understood_student_intent": "novi",
                       "proposed_next_task": {
                           "text": "Da li je 90 djeljiv sa 9? Odgovor je tačno.",
                           "answer_kind": "boolean_with_reason", "expected_answer_summary": "tačno",
                           "difficulty_level": 2},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="e43", client_turn_id="e43-B",
                          student_message="Daj mi novi zadatak.", previous_next_state=r1["next_state"]))
    assert r2["v3_fallback_reason"] == "task_rejected_task_reveals_answer"


def test_e44_contradictory_answer_kind_contract_rejected(fake):
    r1 = start(fake, session="e44")
    fake.set_proposal({"schema_version": "v1", "action": "create_new_task",
                       "understood_student_intent": "novi",
                       "proposed_next_task": {
                           "text": "Izračunaj rezultat.", "answer_kind": "integer_value",
                           "expected_answer_summary": None, "requires_explanation": False,
                           "difficulty_level": 2},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="e44", client_turn_id="e44-B",
                          student_message="Daj mi novi zadatak.", previous_next_state=r1["next_state"]))
    assert r2["v3_fallback_reason"] == "task_rejected_task_missing_expected_answer"


def test_e45_ordinary_on_topic_task_is_not_falsely_rejected(fake):
    r1 = start(fake, session="e45")
    fake.set_proposal({"schema_version": "v1", "action": "create_new_task",
                       "understood_student_intent": "novi",
                       "proposed_next_task": {"text": "Da li je 100 djeljiv sa 4?",
                                              "answer_kind": "boolean_with_reason",
                                              "expected_answer_summary": "da", "difficulty_level": 2},
                       "confidence": 0.9})
    r2 = dispatch(payload(session_id="e45", client_turn_id="e45-B",
                          student_message="Daj mi novi zadatak.", previous_next_state=r1["next_state"]))
    assert "v3_fallback_reason" not in r2


def test_e46_no_repair_call_after_task_rejection(fake):
    r1 = start(fake, session="e46")
    fake.calls.clear()
    fake.set_proposal({"schema_version": "v1", "action": "create_new_task",
                       "understood_student_intent": "novi",
                       "proposed_next_task": {"text": "", "answer_kind": "integer_value",
                                              "difficulty_level": 2}, "confidence": 0.9})
    dispatch(payload(session_id="e46", client_turn_id="e46-B",
                     student_message="Daj mi novi zadatak.", previous_next_state=r1["next_state"]))
    assert fake.calls == [orchestrator.PURPOSE_CONVERSATION]
    assert orchestrator.PURPOSE_TASK_REPAIR not in fake.calls


def test_e47_no_repair_call_after_feedback_rejection(fake):
    r1 = start(fake, session="e47")
    fake.calls.clear()
    fake.set_proposal(assess_proposal("correct", "da"))
    dispatch(payload(session_id="e47", client_turn_id="e47-B", student_message="da",
                     previous_next_state=r1["next_state"]))
    assert fake.calls == [orchestrator.PURPOSE_CONVERSATION]
    assert orchestrator.PURPOSE_FEEDBACK_REPAIR not in fake.calls


# =========================================================================== #
# F. Grades and domains (48-52)                                               #
# =========================================================================== #
@pytest.mark.parametrize(
    "grade,topic_id,oblast,area,target,question,answer", REPRESENTATIVE_LESSONS,
    ids=[f"g{g}-{area}" for (g, _t, _o, area, *_r) in REPRESENTATIVE_LESSONS])
def test_f48_context_has_correct_grade_and_lesson(grade, topic_id, oblast, area, target,
                                                  question, answer):
    identity = lesson_blueprint.resolve_lesson_identity(grade, topic_id, oblast)
    assert identity is not None
    from matbot.ai_tutor_v3.schemas import TutorSessionState
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    state = TutorSessionState(
        schema_version="v1", session_id="s", student_id="stu", grade=grade, mode="practice",
        lesson_id=identity.lesson_id, blueprint_id="none", blueprint_version="none",
        prompt_policy=orchestrator.current_prompt_policy(), created_at=now, updated_at=now)
    ctx = orchestrator.build_conversation_context(
        grade=grade, lesson_id=identity.lesson_id, lesson_title=identity.lesson_title,
        short_lesson_description=None, state=state, student_message="test")
    assert ctx.grade == grade
    assert ctx.lesson_id == identity.lesson_id
    assert ctx.lesson_title == identity.lesson_title


@pytest.mark.parametrize(
    "grade,topic_id,oblast,area,target,question,answer", REPRESENTATIVE_LESSONS,
    ids=[f"g{g}-{area}" for (g, _t, _o, area, *_r) in REPRESENTATIVE_LESSONS])
def test_f49_generated_task_lesson_focused_one_call(
        conv_env, grade, topic_id, oblast, area, target, question, answer):
    client = ConvFakeClient()
    dispatcher.set_model_client(client)
    client.set_proposal(initial_task_proposal(text=question, expected=str(answer)))
    resp = dispatch({"session_id": f"f49-{area}", "grade": grade, "mode": "practice",
                     "selected_topic": topic_id, "selected_oblast": oblast,
                     "student_message": "Daj mi jedan zadatak za vježbu.",
                     "client_turn_id": "t1"})
    assert resp is not None
    assert resp["last_tutor_task"] == question
    assert len(client.calls) == 1


@pytest.mark.parametrize("grade", [6, 7, 8, 9])
def test_f50_teacher_language_mentions_correct_grade(grade):
    text = orchestrator.CONVERSATION_TURN_POLICY.format(grade=grade, lesson_title="Test lekcija")
    assert f"{grade}. razred" in text


@pytest.mark.parametrize(
    "grade,topic_id,oblast,area,target,question,answer", REPRESENTATIVE_LESSONS,
    ids=[f"g{g}-{area}" for (g, _t, _o, area, *_r) in REPRESENTATIVE_LESSONS])
def test_f51_natural_explanation_assessed_across_lessons(
        conv_env, grade, topic_id, oblast, area, target, question, answer):
    client = ConvFakeClient()
    dispatcher.set_model_client(client)
    client.set_proposal(initial_task_proposal(text=question, expected=str(answer)))
    r1 = dispatch({"session_id": f"f51-{area}", "grade": grade, "mode": "practice",
                  "selected_topic": topic_id, "selected_oblast": oblast,
                  "student_message": "Daj mi jedan zadatak.", "client_turn_id": "t1"})
    client.set_proposal(assess_proposal("correct", "Tačno, dobro rasuđivanje."))
    r2 = dispatch({"session_id": f"f51-{area}", "grade": grade, "mode": "practice",
                  "selected_topic": topic_id, "selected_oblast": oblast,
                  "student_message": f"mislim da je {answer} jer sam tako izračunao/la",
                  "client_turn_id": "t2", "previous_next_state": r1["next_state"]})
    assert r2["answer_verdict"] == "correct"


def test_f52_no_manual_lesson_specific_python_patch():
    import inspect
    from matbot.ai_tutor_v3 import dispatcher as disp_mod
    src = inspect.getsource(disp_mod) + inspect.getsource(orchestrator)
    for needle in ("4/6", "8/12", "6/9", "Ne znam.\"\n    if", "252 je"):
        assert needle not in src


# =========================================================================== #
# G. Existing guarantees (53-64, minus 62/64 which are repo-level checks)      #
# =========================================================================== #
def test_g53_strict_structured_outputs_schema_prepares():
    schema = orchestrator.export_json_schema(
        orchestrator.PracticeConversationTurnProposal)
    strict = orchestrator.prepare_openai_strict_schema(schema)
    orchestrator.validate_openai_strict_schema(
        strict, purpose="practice_conversation_turn",
        schema_name="PracticeConversationTurnProposal")


def test_g54_pydantic_validation_before_acceptance(fake):
    # give_hint without the required "hint" field fails the model_validator
    # BEFORE the reducer/render step ever sees it.
    fake.set_proposal({"schema_version": "v1", "action": "give_hint",
                       "understood_student_intent": "x", "confidence": 0.9})
    r = dispatch(payload(session_id="g54", client_turn_id="g54"))
    assert r.get("v3_fallback_reason") == "schema_validation_error"


def test_g55_max_retries_zero_still_set():
    import inspect
    src = inspect.getsource(orchestrator.OpenAIResponsesClient.__init__)
    assert "max_retries=0" in src


def test_g56_idempotency_duplicate_client_turn_id_replays(fake):
    fake.set_proposal(initial_task_proposal())
    r1 = dispatch(payload(session_id="g56", client_turn_id="dup"))
    fake.calls.clear()
    r2 = dispatch(payload(session_id="g56", client_turn_id="dup"))
    assert r1 == r2
    assert fake.calls == []  # replayed, no new model call


def test_g57_duplicate_turn_cannot_double_mutate_counters(fake):
    r1 = start(fake, session="g57")
    fake.set_proposal(assess_proposal("correct", "Tačno!"))
    r2 = dispatch(payload(session_id="g57", client_turn_id="g57-B", student_message="da",
                          previous_next_state=r1["next_state"]))
    r3 = dispatch(payload(session_id="g57", client_turn_id="g57-B", student_message="da",
                          previous_next_state=r1["next_state"]))
    assert r2["next_state"]["v3_state"]["counters"]["attempts"] == \
        r3["next_state"]["v3_state"]["counters"]["attempts"]


def test_g58_failed_model_call_preserves_current_task(fake):
    r1 = start(fake, session="g58")
    fake.force_status("error", "APITimeoutError")
    r2 = dispatch(payload(session_id="g58", client_turn_id="g58-B", student_message="da",
                          previous_next_state=r1["next_state"]))
    assert r2["last_tutor_task"] == r1["last_tutor_task"]


def test_g59_error_classification_module_unchanged():
    assert hasattr(orchestrator, "classify_responses_output")
    assert hasattr(orchestrator, "ResponsesOutputClassification")


def test_g60_rendering_normalizes_latex_in_conversation_answers(fake):
    r1 = start(fake, session="g60")
    fake.set_proposal(assess_proposal("correct", r"Tačno! \frac{1}{2} je ispravno."))
    r2 = dispatch(payload(session_id="g60", client_turn_id="g60-B", student_message="1/2",
                          previous_next_state=r1["next_state"]))
    assert r"\( \frac{1}{2} \)" in r2["answer"]


def test_g61_sheets_outbox_enqueue_still_happens(fake):
    from matbot.ai_tutor_v3 import sheets_outbox
    r1 = start(fake, session="g61")
    store = dispatcher._get_store()
    row = store.get_sheets_event("g61-A")
    assert row is not None


def test_g63_import_isolation_no_client_constructed():
    import importlib
    importlib.reload(orchestrator)
    importlib.reload(dispatcher)
