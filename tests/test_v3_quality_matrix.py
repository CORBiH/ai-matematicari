# -*- coding: utf-8 -*-
"""Phase 11: a broad, deterministic quality matrix across representative
curriculum lessons in grades 6–9 — not just divisibility/fractions.

Every lesson below is a REAL, successfully-resolved curriculum topic id (see
``matbot.topic_resolver``/``matbot.content_loader``), used only as metadata —
no lesson-specific Python tutoring logic is added anywhere in this file.
Model output is entirely FAKE (``FakeClient``, imported from
``test_v3_practice``): no live/paid OpenAI call occurs.

This file cannot prove pedagogy is excellent — it proves the STRUCTURAL
contracts (rendering, quality gate, call counts, reducer authority, task
preservation) hold across a spread of grades and mathematical areas. Real
language quality still needs human/live review — see the final report.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from matbot.ai_tutor_v3 import dispatcher, orchestrator, quality_gate
from tests.test_v3_practice import (
    FakeClient, assess_parsed, blueprint_parsed, interp_parsed,
    narration_parsed, task_parsed,
)

# --------------------------------------------------------------------------- #
# Representative curriculum lessons — real topic ids, one per area           #
# --------------------------------------------------------------------------- #
REPRESENTATIVE_LESSONS = [
    # (grade, topic_id, oblast, area_label, coverage_target, task_question, answer)
    (6, "6-03-021", "Djeljivost brojeva", "divisibility", "6",
     "Da li je 252 djeljiv sa 6?", "da"),
    (6, "6-04-031", "Razlomci", "fractions", "1/2",
     r"Proširi razlomak \( \frac{1}{2} \) brojem 3.", "3/6"),
    (6, "6-08-070", "Skupovi tačaka, kružnica i krug", "geometry", "duz",
     "Nacrtaj i imenuj jednu duž.", "duz-ab"),
    (7, "7-01-001", "Cijeli brojevi", "integers", "abs",
     "Odredi apsolutnu vrijednost broja -7.", "7"),
    (7, "7-06-060", "Trougao", "triangles", "vrste",
     "Imenuj jednu vrstu trougla prema stranicama.", "jednakostranican"),
    (7, "7-05-054", "Operacije sa uglovima", "angles", "sabiranje",
     "Saberi uglove 30° i 45°.", "75"),
    (8, "8-04-025", "Pitagorina teorema", "pythagoras", "hipotenuza",
     "Izračunaj hipotenuzu pravouglog trougla sa katetama 3 i 4.", "5"),
    (8, "8-09-082", "Razmjere i proporcije", "proportions", "razmjera",
     "Skrati razmjeru 8:12 na najmanje cijele brojeve.", "2:3"),
    (8, "8-05-038", "Cijeli racionalni izrazi (polinomi)", "polynomials",
     "stepen", "Odredi stepen polinoma 3x^2 + 2x - 1.", "2"),
    (9, "9-03-027", "LINEARNA FUNKCIJA", "linear_functions", "koordinate",
     "Odredi koordinate tačke A(2, 3) u koordinatnom sistemu.", "(2,3)"),
    (9, "9-04-039", "LINEARNE JEDNAČINE SA JEDNOM NEPOZNATOM", "equations",
     "rjesenje", r"Riješi jednačinu \( 2x + 3 = 11 \).", "4"),
    (9, "9-06-060", "SISTEM LINEARNIH JEDNAČINA SA 2 NEPOZNATE", "systems",
     "sistem", "Riješi sistem: x + y = 5, x - y = 1.", "x=3,y=2"),
]

# --------------------------------------------------------------------------- #
# Reusable quality assertions — generic, never lesson-specific                #
# --------------------------------------------------------------------------- #
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_HTML_RE = re.compile(r"<(?!br\s*/?>)[a-zA-Z][^>]*>")


def assert_no_undelimited_latex(text: str) -> None:
    from matbot.ai_tutor_v3.rendering import normalize_math_for_display
    assert normalize_math_for_display(text) == text, (
        f"text still has undelimited LaTeX after normalization: {text!r}")


def assert_no_internal_terms(text: str) -> None:
    lowered = text.lower()
    for term in quality_gate._INTERNAL_TERMS:
        assert term not in lowered, f"internal term {term!r} leaked in: {text!r}"


def assert_no_raw_html(text: str) -> None:
    assert not _HTML_RE.search(text), f"unsupported HTML in: {text!r}"


def assert_task_sentence_count_within_policy(text: str, grade: int) -> None:
    sentences = [s for s in _SENTENCE_RE.split(text.strip()) if s.strip()]
    assert len(sentences) <= quality_gate._TASK_MAX_SENTENCES, (
        f"grade {grade} task has too many sentences: {text!r}")


def assert_no_duplicate_sentence(text: str) -> None:
    sentences = [s.strip().lower() for s in _SENTENCE_RE.split(text.strip()) if s.strip()]
    assert len(sentences) == len(set(sentences)), f"duplicated sentence in: {text!r}"


def assert_required_frontend_fields_present(resp: dict) -> None:
    for field in ("answer", "status", "last_tutor_task", "next_state",
                 "engine", "session_mode", "verification_status"):
        assert field in resp, f"missing required frontend field {field!r}"
    assert resp["status"] == "ready"
    assert resp["engine"] == "v3_practice"


def assert_no_progress_mutation(before: dict, after: dict) -> None:
    for key in ("attempts", "wrong_attempts", "solved_independent", "tasks_completed"):
        assert before[key] == after[key], (
            f"counter {key!r} mutated on a non-grading turn: {before} -> {after}")


def assert_task_id_preserved(before_task_id, after_task_id) -> None:
    assert before_task_id == after_task_id, "active task identity changed unexpectedly"


def assert_assisted_solution_not_independent(resp: dict) -> None:
    v3 = resp["next_state"]["v3_state"]
    assert v3["counters"]["solved_independent"] == 0 or v3["mastery"]["provisional"] is True


def assert_call_count_within_target(call_count: int, max_calls: int) -> None:
    assert call_count <= max_calls, f"too many model calls: {call_count} > {max_calls}"


# --------------------------------------------------------------------------- #
# Per-lesson FakeClient fixture builder                                       #
# --------------------------------------------------------------------------- #
def _make_fake(question: str, answer: str, target: str) -> FakeClient:
    client = FakeClient()
    client.set(orchestrator.PURPOSE_BLUEPRINT, blueprint_parsed(targets=[target]))
    client.set(orchestrator.PURPOSE_TASK, task_parsed(
        target=target, concept=f"concept-{target}", question=question))
    return client


@pytest.fixture()
def matrix_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_PRACTICE", "on")
    monkeypatch.setenv("MATBOT_AI_TUTOR_V3_LESSONS", "*")   # wildcard — broad support
    monkeypatch.setenv("MATBOT_V3_VERIFICATION", "off")
    monkeypatch.setenv("MATBOT_V3_DB_PATH", str(tmp_path / "v3.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "off")
    from matbot import topic_resolver as tr
    tr.reset_cache()
    yield
    dispatcher.set_model_client(None)
    tr.reset_cache()


@pytest.mark.parametrize(
    "grade,topic_id,oblast,area,target,question,answer", REPRESENTATIVE_LESSONS,
    ids=[f"g{g}-{area}" for (g, _t, _o, area, *_r) in REPRESENTATIVE_LESSONS])
def test_representative_lesson_full_flow(matrix_env, grade, topic_id, oblast,
                                         area, target, question, answer):
    """Drives initial task → new task → correct → incorrect → partial →
    typo'd answer → comment-with-a-number → concept question → hint (first,
    later) → solution reveal → easier/harder → ambiguous, asserting the
    REUSABLE generic checks at each step. One real curriculum lesson per
    grade/area — no lesson-specific Python."""
    fake = _make_fake(question, answer, target)
    dispatcher.set_model_client(fake)

    def payload(**over):
        base = {"session_id": f"mx-{area}", "grade": grade, "mode": "practice",
               "selected_topic": topic_id, "selected_oblast": oblast,
               "student_message": "Daj mi jedan zadatak za vježbu iz ove teme."}
        base.update(over)
        return base

    def dispatch(p, model="gpt-5-mini"):
        return dispatcher.v3_practice_dispatch(p, model=model, timeout=5)

    # 1. Initial task (bootstrap turn — no turn_interpretation call).
    resp = dispatch(payload(client_turn_id="t1"))
    assert resp is not None
    assert_required_frontend_fields_present(resp)
    assert_no_undelimited_latex(resp["last_tutor_task"])
    assert_no_internal_terms(resp["last_tutor_task"])
    assert_no_raw_html(resp["last_tutor_task"])
    assert_task_sentence_count_within_policy(resp["last_tutor_task"], grade)
    assert_no_duplicate_sentence(resp["last_tutor_task"])
    assert_call_count_within_target(len(fake.calls), 2)   # blueprint + task
    task_id_1 = resp["task_id"]

    # 2. New (explicit) task request mid-session.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    resp2 = dispatch(payload(client_turn_id="t2", previous_next_state=resp["next_state"]))
    assert resp2 is not None
    assert resp2["task_id"] != task_id_1   # a genuinely new task was assigned
    assert_no_undelimited_latex(resp2["last_tutor_task"])

    # 3. Correct answer (with proposed narration — Phase 7 consolidation).
    fake.calls.clear()
    correct_interp = interp_parsed("answer", is_answer=True,
                                   assessment=assess_parsed("correct"))
    correct_interp["narration_proposal"] = narration_parsed("Tačno, bravo!", "feedback_correct")
    fake.set(orchestrator.PURPOSE_INTERPRET, correct_interp)
    resp3 = dispatch(payload(client_turn_id="t3", student_message=answer,
                             previous_next_state=resp2["next_state"]))
    assert resp3 is not None
    assert resp3["answer_verdict"] == "correct"
    assert_no_undelimited_latex(resp3["answer"])
    assert orchestrator.PURPOSE_NARRATION not in fake.calls  # proposal used directly
    assert_call_count_within_target(len(fake.calls), 1)      # interpret only (proposal reused)

    # A correct answer completes the task (active_task cleared) — the student
    # must explicitly ask for the next one before an incorrect/partial answer
    # has anything to grade against.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("task_request"))
    resp3b = dispatch(payload(client_turn_id="t3b", previous_next_state=resp3["next_state"]))
    assert resp3b is not None
    assert resp3b["task_id"] is not None

    # 4. Incorrect answer on a fresh task.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("incorrect")))
    resp4 = dispatch(payload(client_turn_id="t4", student_message="pogresno",
                             previous_next_state=resp3b["next_state"]))
    assert resp4 is not None
    assert resp4["answer_verdict"] == "incorrect"
    task_id_before_wrong = resp4["task_id"]

    # 5. Partial answer — task preserved, no completion.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, assessment=assess_parsed("partial")))
    resp5 = dispatch(payload(client_turn_id="t5", student_message="dio odgovora",
                             previous_next_state=resp4["next_state"]))
    assert resp5 is not None
    assert resp5["answer_verdict"] == "partial"
    assert_task_id_preserved(task_id_before_wrong, resp5["task_id"])

    # 6. Missing diacritics / typo — still interpreted normally, not punished
    #    beyond what the (fake) model's assessment says.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "answer", is_answer=True, confidence=0.6,
        assessment=assess_parsed("partial", confidence=0.6)))
    resp6 = dispatch(payload(client_turn_id="t6", student_message="nisam sigurn",
                             previous_next_state=resp5["next_state"]))
    assert resp6 is not None

    # 7. Comment containing a number — never auto-graded as an answer.
    counters_before = resp6["next_state"]["v3_state"]["counters"]
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("comment", meaning="90"))
    resp7 = dispatch(payload(client_turn_id="t7", student_message="90",
                             previous_next_state=resp6["next_state"]))
    assert resp7 is not None
    assert_no_progress_mutation(counters_before, resp7["next_state"]["v3_state"]["counters"])

    # 8. Related concept question — active task preserved.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("question"))
    fake.set(orchestrator.PURPOSE_CONCEPT, narration_parsed(
        "Evo objašnjenja koncepta. Vratimo se na tvoj zadatak.", "concept"))
    resp8 = dispatch(payload(client_turn_id="t8", student_message="sta ovo znaci?",
                             previous_next_state=resp7["next_state"]))
    assert resp8 is not None
    assert_task_id_preserved(resp7["task_id"], resp8["task_id"])
    assert_no_undelimited_latex(resp8["answer"])
    assert_no_internal_terms(resp8["answer"])

    # 9. First hint.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("help_request"))
    fake.set(orchestrator.PURPOSE_HINT, narration_parsed("Prvi savjet.", "hint"))
    resp9 = dispatch(payload(client_turn_id="t9", student_message="Ne znam.",
                             intent="hint_request",
                             previous_next_state=resp8["next_state"]))
    assert resp9 is not None
    assert orchestrator.PURPOSE_INTERPRET not in fake.calls  # trusted intent bypass
    assert_task_id_preserved(resp8["task_id"], resp9["task_id"])

    # 10. Later (more specific) hint.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_HINT, narration_parsed("Konkretniji savjet.", "hint"))
    resp10 = dispatch(payload(client_turn_id="t10", student_message="Jos uvijek ne znam.",
                              intent="hint_request",
                              previous_next_state=resp9["next_state"]))
    assert resp10 is not None
    assert resp10["next_state"]["hint_count"] == 2

    # 11. Full solution — assisted, never independent.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed("solution_request"))
    fake.set(orchestrator.PURPOSE_REVEAL, narration_parsed(
        f"Rješenje: {answer}. Prvi korak... Drugi korak...", "reveal"))
    resp11 = dispatch(payload(client_turn_id="t11", student_message="Pokazi mi rjesenje.",
                              previous_next_state=resp10["next_state"]))
    assert resp11 is not None
    assert_assisted_solution_not_independent(resp11)

    # 12. Easier request (trusted difficulty_request bypass).
    fake.calls.clear()
    level_before = resp11["next_state"]["difficulty_level"]
    resp12 = dispatch(payload(client_turn_id="t12", student_message="Daj mi laksi zadatak.",
                              difficulty_request="easier",
                              previous_next_state=resp11["next_state"]))
    assert resp12 is not None
    assert orchestrator.PURPOSE_INTERPRET not in fake.calls
    assert resp12["next_state"]["difficulty_level"] <= level_before

    # 13. Harder request.
    fake.calls.clear()
    resp13 = dispatch(payload(client_turn_id="t13", student_message="Daj mi tezi zadatak.",
                              difficulty_request="harder",
                              previous_next_state=resp12["next_state"]))
    assert resp13 is not None
    assert orchestrator.PURPOSE_INTERPRET not in fake.calls

    # 14. Ambiguous message — clarify, never punish, task preserved.
    fake.calls.clear()
    fake.set(orchestrator.PURPOSE_INTERPRET, interp_parsed(
        "ambiguous", clarification="Možeš li reći tačnije šta misliš?"))
    counters_before_ambig = resp13["next_state"]["v3_state"]["counters"]
    resp14 = dispatch(payload(client_turn_id="t14", student_message="ovaj to",
                              previous_next_state=resp13["next_state"]))
    assert resp14 is not None
    assert resp14["response_category"] == "clarification"
    assert_no_progress_mutation(counters_before_ambig,
                                resp14["next_state"]["v3_state"]["counters"])
    assert_task_id_preserved(resp13["task_id"], resp14["task_id"])


# --------------------------------------------------------------------------- #
# Manual-review corpus artifact (fake fixtures only, no live call)            #
# --------------------------------------------------------------------------- #
def test_generate_manual_review_corpus(matrix_env, tmp_path):
    """Produces a small JSON artifact of representative student-facing
    outputs, generated entirely from fake model fixtures, for HUMAN
    inspection. Written to a temp path — never asserted as proof of good
    pedagogy, only as a reviewable sample."""
    corpus = []
    for grade, topic_id, oblast, area, target, question, answer in REPRESENTATIVE_LESSONS:
        fake = _make_fake(question, answer, target)
        dispatcher.set_model_client(fake)
        payload = {"session_id": f"corpus-{area}", "grade": grade, "mode": "practice",
                  "selected_topic": topic_id, "selected_oblast": oblast,
                  "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
                  "client_turn_id": f"corpus-{area}-1"}
        resp = dispatcher.v3_practice_dispatch(payload, model="gpt-5-mini", timeout=5)
        assert resp is not None
        corpus.append({
            "grade": grade, "area": area, "topic_id": topic_id,
            "task_text": resp["last_tutor_task"],
        })
        dispatcher.set_model_client(None)

    out_path = tmp_path / "v3_manual_review_corpus.json"
    out_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
    assert out_path.exists()
    assert len(corpus) == len(REPRESENTATIVE_LESSONS)
    for entry in corpus:
        assert entry["task_text"]
