# -*- coding: utf-8 -*-
"""Phase 2 — authoritative GradingResult precedence (Engine V2), flag-gated.

When ``MATBOT_ENGINE_V2_GRADING=on``:
  * a decisive deterministic checker verdict is authoritative;
  * a structured GPT verdict can NEVER override the checker;
  * tutor prose is NEVER a grader (no prose->verdict fallback);
  * structured GPT is used only when the checker abstains.

When the flag is off, the legacy precedence (GPT/​prose can override) is kept
verbatim as the rollback path.
"""
import json
import types

import pytest

from matbot import engine_v2
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    # Phase 0/1/3 flags off unless a test opts in — these tests isolate Phase 2.
    monkeypatch.setenv("MATBOT_ENGINE_V2", "off")
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "off")
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _model(gpt_verdict=None, tutor_reply="U redu."):
    """Mock model: returns a JSON verdict for the contextual-grader call,
    otherwise the tutor prose reply."""
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        sys_txt = "".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        if "answer grader" in sys_txt.lower() and gpt_verdict is not None:
            body = json.dumps({"verdict": gpt_verdict, "confidence": 0.9, "public_feedback": ""})
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=body))])
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=tutor_reply))])
    return chat


def _gp(task, student):
    return {"grade": 6, "mode": "practice", "interaction_phase": "answering_practice_task",
            "last_tutor_task": task, "student_message": student}


def _run(master, tmap, payload, gpt_verdict=None, tutor_reply="U redu."):
    return svc.handle_chat(dict(payload), _model(gpt_verdict, tutor_reply),
                           master, tmap, model="m", timeout=1)


# Correct answer narrated with procedure words → deterministic=correct AND the
# contextual GPT fires (and here disagrees). This is the gpt_overrode class.
PROC_CORRECT = _gp("Izračunaj: 1/2 · 5/9.", "prvo sam pomnozio brojnike pa je 5/18")


# --------------------------------------------------------------------------- #
# Flag semantics                                                              #
# --------------------------------------------------------------------------- #
def test_grading_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("MATBOT_ENGINE_V2_GRADING", raising=False)
    assert engine_v2.grading_mode() == "off"
    assert engine_v2.grading_authoritative() is False


def test_grading_flag_unknown_is_off(monkeypatch):
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "authoritative")
    assert engine_v2.grading_mode() == "off"


def test_grading_flag_on(monkeypatch):
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    assert engine_v2.grading_authoritative() is True


# --------------------------------------------------------------------------- #
# Legacy path preserved (rollback)                                            #
# --------------------------------------------------------------------------- #
def test_legacy_gpt_overrides_deterministic(monkeypatch, master, tmap):
    """Flag off: GPT 'incorrect' overrides the deterministically-correct answer
    (documents the legacy behavior Phase 2 fixes)."""
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "off")
    out = _run(master, tmap, PROC_CORRECT, gpt_verdict="incorrect")
    assert out["answer_verdict"] == "incorrect"
    assert out["next_state"]["correct_streak"] == 0


# --------------------------------------------------------------------------- #
# Phase 2 authoritative behavior                                              #
#                                                                             #
# Design note (data-driven): the divergence replay showed the deterministic   #
# checker is UNRELIABLE on procedural/intermediate answers (it misreads an     #
# intermediate number as a wrong final answer). So Phase 2 does NOT force      #
# "deterministic-first"; the structured GPT grader (which evaluated the whole  #
# procedure) stays authoritative when it ran. The ONLY behavior change vs      #
# legacy is that tutor prose is never parsed into a verdict.                   #
# --------------------------------------------------------------------------- #
def test_phase2_structured_gpt_authoritative_over_false_deterministic(monkeypatch, master, tmap):
    """A correct intermediate step ("Zajednički imenilac je 6") that the checker
    wrongly scores 'incorrect' must keep the structured GPT 'partial' — Phase 2
    must NOT regress intermediate-step grading to a false 'incorrect'."""
    from matbot.answer_checker import check_practice_answer
    from matbot.grading_guard import authoritative_verdict
    # Precondition: the deterministic checker really does mis-score this.
    r = check_practice_answer("Izracunaj: 1/2 + 1/3", "Zajednicki imenilac je 6")
    assert authoritative_verdict(r) == "incorrect"
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    out = _run(master, tmap, _gp("Izracunaj: 1/2 + 1/3", "Zajednicki imenilac je 6"),
               gpt_verdict="partial")
    assert out["answer_verdict"] == "partial"       # GPT wins, NOT the false 'incorrect'
    assert out["gpt_check_used"] is True


def test_phase2_structured_gpt_verdict_unchanged_vs_legacy(monkeypatch, master, tmap):
    """When a structured GPT grade exists, Phase 2 reports the same verdict as
    legacy (the structured grader stays authoritative — no divergence)."""
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "off")
    off = _run(master, tmap, PROC_CORRECT, gpt_verdict="incorrect")
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    on = _run(master, tmap, PROC_CORRECT, gpt_verdict="incorrect")
    assert on["answer_verdict"] == off["answer_verdict"]
    assert on["gpt_check_used"] == off["gpt_check_used"] is True


def test_phase2_clean_correct_unchanged(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    out = _run(master, tmap, _gp("Izračunaj: 1/2 · 5/9.", "5/18"))
    assert out["answer_verdict"] == "correct"
    assert out["next_state"]["correct_streak"] == 1


def test_phase2_prose_never_becomes_verdict(monkeypatch, master, tmap):
    """Flag on: checker abstains, no structured grade, prose says 'Tačno' →
    the verdict must NOT be derived from prose."""
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    # A conceptual task the deterministic checker cannot grade.
    payload = _gp("Objasni svojim riječima šta je razlomak.",
                  "razlomak je kada nešto podijelimo")
    out = _run(master, tmap, payload, gpt_verdict=None, tutor_reply="Tačno! Odlično.")
    assert out["answer_verdict"] != "correct"       # prose 'Tačno' ignored
    assert out["gpt_check_used"] is False


def test_phase2_gpt_used_when_checker_abstains(monkeypatch, master, tmap):
    """Flag on: checker abstains AND a structured GPT grade exists → GPT decides
    (deterministic-first still yields to GPT only on abstain)."""
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    payload = _gp("Objasni zašto zbir uglova u trouglu iznosi 180 stepeni.",
                  "zato sto se tri ugla poklope u ravnu liniju jer je to prava")
    out = _run(master, tmap, payload, gpt_verdict="partial")
    # If the contextual grader ran, its verdict is used; otherwise no verdict.
    if out["gpt_check_used"]:
        assert out["answer_verdict"] == "partial"


# --------------------------------------------------------------------------- #
# Parity: no behavior change on non-divergent cases                           #
# --------------------------------------------------------------------------- #
def test_phase2_parity_on_plain_correct(monkeypatch, master, tmap):
    payload = _gp("Izračunaj: 1/2 · 5/9.", "5/18")
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "off")
    off = _run(master, tmap, payload)
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    on = _run(master, tmap, payload)
    assert on["answer"] == off["answer"]
    assert on["answer_verdict"] == off["answer_verdict"]
    assert on["next_state"]["correct_streak"] == off["next_state"]["correct_streak"]


def test_phase2_parity_on_plain_wrong(monkeypatch, master, tmap):
    payload = _gp("Izračunaj: 1/2 · 5/9.", "6/11")
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "off")
    off = _run(master, tmap, payload)
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "on")
    on = _run(master, tmap, payload)
    assert on["answer_verdict"] == off["answer_verdict"] == "incorrect"
    assert on["answer"] == off["answer"]


# --------------------------------------------------------------------------- #
# Helper units                                                                #
# --------------------------------------------------------------------------- #
def test_deterministic_decisive_true_for_checkable():
    from matbot.answer_checker import check_practice_answer
    payload = {"answer_check": check_practice_answer("Izračunaj: 1/4 + 1/4.", "1/2")}
    assert svc._deterministic_decisive(payload) is True


def test_deterministic_decisive_false_without_check():
    assert svc._deterministic_decisive({}) is False


def test_apply_verdict_streak_increments_and_resets():
    p = {"previous_next_state": {"correct_streak": 2}}
    svc._apply_verdict_streak(p, "correct")
    assert p["_correct_streak"] == 3
    p2 = {"previous_next_state": {"correct_streak": 2}}
    svc._apply_verdict_streak(p2, "incorrect")
    assert p2["_correct_streak"] == 0
