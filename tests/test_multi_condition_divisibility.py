# -*- coding: utf-8 -*-
"""Production defect: multi-condition divisibility tasks collapsed to one boolean.

"Provjeri da li je broj 30 djeljiv sa 5 i sa 3. Obrazloži…" was reduced to a
single divisor, so a bare "da" scored `correct`, completed the task and bumped
the streak while the tutor invented the missing explanation itself.
"""
import json
import types

import pytest

from matbot.answer_checker import (
    check_practice_answer, derive_expected, divisibility_coverage,
    divisibility_missing_summary, format_check_block,
)
from matbot.grading_guard import authoritative_verdict as V
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl

T30 = "Provjeri da li je broj 30 djeljiv sa 5 i sa 3. Obrazloži svoj odgovor za oba broja."
T240 = "Provjeri da li je broj 240 djeljiv sa 10 i sa 15. Obrazloži svoj odgovor za oba broja."
T144 = "Provjeri je li broj 144 djeljiv sa 3 i 4. Obrazloži svoje odgovore."
TMIX = "Provjeri da li je broj 30 djeljiv sa 5 i sa 4. Obrazloži svoj odgovor za oba broja."
TREV = "Provjeri da li je broj 30 djeljiv sa 3 i sa 5. Obrazloži svoj odgovor za oba broja."
T3 = "Provjeri da li je broj 30 djeljiv sa 2, sa 3 i sa 5. Obrazloži svoj odgovor."

R30_5 = "da, djeljiv je sa 5 jer se završava nulom"
R30_BOTH = "da, sa 5 jer se završava nulom, i sa 3 jer je zbir cifara 3 djeljiv sa 3"
R240_10 = "da, sa 10 jer je zadnja cifra 0"
R240_BOTH = "da, sa 10 jer je zadnja cifra 0, i sa 15 jer se dijeli bez ostatka"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "a.sqlite3"))
    monkeypatch.setenv("MATBOT_ENGINE_V2", "off")
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "off")
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "off")
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _model(gpt_verdict=None, tutor="U redu."):
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        sys_txt = "".join(str(m.get("content") or "") for m in messages
                          if m.get("role") == "system")
        if "answer grader" in sys_txt.lower() and gpt_verdict:
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=json.dumps(
                    {"verdict": gpt_verdict, "confidence": 0.95, "public_feedback": ""})))])
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=tutor))])
    return chat


def _grade(master, tmap, task, student, gpt=None, prev=None):
    payload = {"grade": 6, "mode": "practice",
               "interaction_phase": "answering_practice_task",
               "last_tutor_task": task, "student_message": student}
    if prev:
        payload["previous_next_state"] = prev
    return svc.handle_chat(payload, _model(gpt), master, tmap, model="m", timeout=1)


# --------------------------------------------------------------------------- #
# Structured schema                                                           #
# --------------------------------------------------------------------------- #
def test_schema_captures_all_divisors_and_concepts():
    e = derive_expected(T30)
    assert e.divisors == (5, 3)                      # second divisor no longer lost
    assert e.divisor_expected == (True, True)
    assert e.all_conditions_required is True
    assert e.requires_full_explanation is True
    assert len(e.divisor_concepts) == 2
    assert any("zbir cifara" in c for c in e.divisor_concepts[1])
    assert e.divisor == 5                             # scalar kept for compatibility


def test_schema_reversed_and_three_divisors():
    assert derive_expected(TREV).divisors == (3, 5)
    assert derive_expected(T3).divisors == (2, 3, 5)
    assert derive_expected(TMIX).divisor_expected == (True, False)   # 30 not div by 4


def test_task_definition_metadata_preserves_conditions():
    meta = svc._task_answer_metadata(T30)[0]
    assert meta["divisors"] == [5, 3]
    assert meta["divisor_expected"] == [True, True]
    assert meta["all_conditions_required"] is True
    assert meta["requires_full_explanation"] is True
    assert len(meta["divisor_concepts"]) == 2
    assert meta["validation_status"] == "validated"


# --------------------------------------------------------------------------- #
# Required regression matrix (checker level)                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("task,answer,expected", [
    (T30, "da", "incomplete"),                       # 1 — bare yes is NOT correct
    (T30, R30_5, "partial"),                         # 2 — only one divisor explained
    (T30, R30_BOTH, "correct"),                      # 3 — both explained
    (T240, "jeste djeljivo", "incomplete"),          # 4 — bare yes
    (T240, R240_10, "partial"),                      # 5 — only 10 explained
    # 6 — 15 is now recognised as COMPOUND (3x5): "se dijeli bez ostatka" is a
    # generic phrase, not evidence for EITHER factor, so this is no longer
    # "both explained". Tightened for the same reason a generic reason was
    # already rejected for 3/9 alone — see _compound_coverage / _COMPOUND_DIVISORS.
    (T240, R240_BOTH, "partial"),
    (T240, "da, sa 10 jer je zadnja cifra 0, i sa 15 jer je zbir cifara 6 djeljiv "
          "sa 3 i zadnja cifra 0", "correct"),        # 6b — 15 genuinely justified
    (TREV, "da, sa 3 jer je zbir cifara 3, i sa 5 jer zavrsava nulom", "correct"),  # 8
    (T3, "da, sa 2 jer je paran, sa 3 jer je zbir cifara 3, i sa 5 jer zavrsava nulom",
     "correct"),                                     # 9 — three divisors
    (T144, "Jest jer je zbir cifara djeljiv sa 3 i zadnja dva broja su djeljiva sa 4.",
     "correct"),                                     # prior 144 fix preserved
])
def test_checker_matrix(task, answer, expected):
    assert V(check_practice_answer(task, answer)) == expected


def test_7_mixed_case_one_divisor_false():
    """30 is NOT divisible by 4 — claiming it is must not be correct."""
    r = check_practice_answer(TMIX, "da, sa 5 jer zavrsava nulom, i sa 4 jer se dijeli bez ostatka")
    assert V(r) == "incorrect"


def test_single_divisor_without_explanation_request_unchanged():
    """Plain "Je li 144 djeljiv sa 3?" keeps its simple yes/no behavior."""
    assert V(check_practice_answer("Je li 144 djeljiv sa 3?", "da")) == "correct"


def test_6_help_gets_no_negative_verdict():
    r = check_practice_answer(T30, "ne znam")
    assert (not r.checkable) or V(r) not in ("incorrect",)


# --------------------------------------------------------------------------- #
# Coverage / feedback contract                                                #
# --------------------------------------------------------------------------- #
def test_coverage_reports_supplied_and_missing():
    e = derive_expected(T30)
    summary = divisibility_missing_summary(e, R30_5)
    assert summary["supplied_divisors"] == [5]
    assert summary["missing_divisors"] == [3]
    assert summary["next_divisor"] == 3
    assert summary["all_covered"] is False


def test_check_block_states_missing_and_forbids_self_answer():
    block = format_check_block(check_practice_answer(T30, "da"))
    assert "VIŠEUSLOVNI ZADATAK" in block
    assert "Nedostaje" in block
    assert "NE piši sam obrazloženje" in block


def test_tutor_prose_is_never_student_evidence():
    """Coverage looks ONLY at the student's text."""
    e = derive_expected(T30)
    assert divisibility_coverage(e, "")[0]["justified"] is False
    # the tutor's own explanation must not count for the student
    assert divisibility_missing_summary(e, "da")["supplied_divisors"] == []


# --------------------------------------------------------------------------- #
# Service level: reproductions, streak, GPT upgrade                           #
# --------------------------------------------------------------------------- #
def test_repro1_bare_da_not_correct_task_stays_active(master, tmap):
    out = _grade(master, tmap, T30, "da")
    assert out["answer_verdict"] == "partial"
    assert out["task_status"] == "active"
    assert out["last_tutor_task"] == T30               # task preserved


def test_repro2_jeste_djeljivo_not_correct(master, tmap):
    out = _grade(master, tmap, T240, "jeste djeljivo")
    assert out["answer_verdict"] == "partial"
    assert out["task_status"] == "active"


def test_10_streak_only_on_full_completion(master, tmap):
    prev = {"correct_streak": 3}
    partial = _grade(master, tmap, T30, "da", prev=prev)
    assert partial["next_state"]["correct_streak"] == 0        # no increase
    full = _grade(master, tmap, T30, R30_BOTH, prev={"correct_streak": 3})
    assert full["answer_verdict"] == "correct"
    assert full["next_state"]["correct_streak"] == 4           # only now


def test_12_structured_gpt_cannot_upgrade_incomplete(master, tmap):
    out = _grade(master, tmap, T30, "da", gpt="correct")
    assert out["answer_verdict"] == "partial"                  # NOT upgraded
    assert out["gpt_check_used"] is False


def test_12b_structured_gpt_cannot_downgrade_verified_correct(master, tmap):
    out = _grade(master, tmap, T30, R30_BOTH, gpt="partial")
    assert out["answer_verdict"] == "correct"


def test_13_flag_off_uses_same_corrected_checker(monkeypatch, master, tmap):
    """The checker fix is a correctness fix, not a V2 feature: with V2 grading
    off the deterministic verdict is the same (no V2 machinery required)."""
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "off")
    out = _grade(master, tmap, T30, "da")
    assert out["answer_verdict"] == "partial"
    assert "shadow_grading" not in out                          # no V2 state leaked


def test_shadow_scope_is_full_answer(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    out = _grade(master, tmap, T30, "da", gpt="correct")
    ev = out["shadow_grading"]["evidence"]
    assert ev["deterministic_scope"] == "full_answer"
    assert out["shadow_grading"]["shadow_verdict"] == "partial"
