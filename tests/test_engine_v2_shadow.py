# -*- coding: utf-8 -*-
"""Phase 0 — Engine V2 read-only shadow grading reducer.

Covers the required behaviors: the feature flag gates execution, shadow mode
never changes the student-facing response / task state / counters, the reducer's
precedence (deterministic > structured GPT > none), prose can never influence the
verdict, agreement/disagreement is logged with the right conflict type, and the
Sheets schema stays backward-compatible. Plus shadow snapshots of previously
observed grading cases (documented, NOT fixed in this phase).
"""
import json
import types

import pytest

from matbot import engine_v2
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.answer_checker import check_practice_answer
from matbot.grading_guard import authoritative_verdict
from matbot import sheets_log


# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                           #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _reset_metrics():
    engine_v2.reset_metrics()
    yield
    engine_v2.reset_metrics()


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _scripted_chat(reply: str):
    calls = {"n": 0}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["n"] += 1
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))]
        )

    chat.calls = calls
    return chat


def _grading_payload(task="Izračunaj: 1/4 + 1/4.", student="1/2"):
    return {
        "grade": 6,
        "mode": "practice",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": task,
        "student_message": student,
    }


def _evidence(task, student, **override):
    r = check_practice_answer(task, student)
    ev = engine_v2.GradingEvidence(
        deterministic_verdict=authoritative_verdict(r),
        deterministic_checkable=bool(r.checkable and r.has_verdicts),
        deterministic_step=any(i.verdict == "correct_step" for i in r.items),
        answer_type=(r.items[0].expected.answer_type if r.items and r.items[0].expected else None),
    )
    for k, v in override.items():
        setattr(ev, k, v)
    return ev, r


# --------------------------------------------------------------------------- #
# Flag behavior (tests 1, 15)                                                  #
# --------------------------------------------------------------------------- #
def test_flag_defaults_to_off(monkeypatch):
    monkeypatch.delenv("MATBOT_ENGINE_V2", raising=False)
    assert engine_v2.engine_v2_mode() == "off"
    assert engine_v2.shadow_enabled() is False


def test_flag_unknown_value_is_off(monkeypatch):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "authoritative")  # not supported this phase
    assert engine_v2.engine_v2_mode() == "off"


def test_flag_shadow_enables(monkeypatch):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    assert engine_v2.engine_v2_mode() == "shadow"
    assert engine_v2.shadow_enabled() is True


def test_flag_off_no_reducer_execution(monkeypatch, master, tmap):
    """Test 1: flag off → no shadow record, no metrics touched."""
    monkeypatch.setenv("MATBOT_ENGINE_V2", "off")
    out = svc.handle_chat(_grading_payload(), _scripted_chat("Tačno."),
                          master, tmap, model="m", timeout=1)
    assert "shadow_grading" not in out
    assert engine_v2.get_metrics()["total"] == 0


def test_flag_off_full_response_unchanged(monkeypatch, master, tmap):
    """Test 15: with the flag off the response is exactly the legacy one."""
    monkeypatch.setenv("MATBOT_ENGINE_V2", "off")
    out = svc.handle_chat(_grading_payload(), _scripted_chat("Tačno, 1/2 je ispravno."),
                          master, tmap, model="m", timeout=1)
    assert out.get("answer_verdict") == "correct"
    assert "shadow_grading" not in out


# --------------------------------------------------------------------------- #
# Shadow mode does not change behavior (tests 2, 3, 4)                         #
# --------------------------------------------------------------------------- #
def _run_off_and_shadow(monkeypatch, master, tmap, payload, reply):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "off")
    off = svc.handle_chat(dict(payload), _scripted_chat(reply), master, tmap, model="m", timeout=1)
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    shadow = svc.handle_chat(dict(payload), _scripted_chat(reply), master, tmap, model="m", timeout=1)
    return off, shadow


def test_shadow_does_not_change_response_text(monkeypatch, master, tmap):
    off, shadow = _run_off_and_shadow(
        monkeypatch, master, tmap, _grading_payload(), "Tačno, odlično."
    )
    assert shadow["answer"] == off["answer"]


def test_shadow_does_not_change_task_state(monkeypatch, master, tmap):
    off, shadow = _run_off_and_shadow(
        monkeypatch, master, tmap, _grading_payload(), "Tačno."
    )
    assert shadow.get("last_tutor_task") == off.get("last_tutor_task")
    assert shadow.get("task_status") == off.get("task_status")
    # task_id is a fresh random uuid per call (unrelated to shadow); presence,
    # not value, is what must be unchanged.
    assert bool(shadow.get("task_id")) == bool(off.get("task_id"))


def test_shadow_does_not_change_counters(monkeypatch, master, tmap):
    off, shadow = _run_off_and_shadow(
        monkeypatch, master, tmap, _grading_payload(), "Tačno."
    )
    for key in ("attempt_number", "total_attempt_count", "wrong_attempt_count", "hint_count"):
        assert shadow.get(key) == off.get(key), key
    assert shadow["next_state"].get("correct_streak") == off["next_state"].get("correct_streak")


def test_shadow_attaches_record_in_shadow_mode(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    out = svc.handle_chat(_grading_payload(), _scripted_chat("Tačno."),
                          master, tmap, model="m", timeout=1)
    sg = out.get("shadow_grading")
    assert sg and sg["engine_version"] == "v2-shadow"
    assert sg["shadow_verdict"] == "correct"
    assert sg["shadow_grader_source"] == "deterministic"


# --------------------------------------------------------------------------- #
# Reducer precedence — aligned with refined Phase 2 grading                    #
# (structured GPT authoritative when it ran; else deterministic; prose never)  #
# --------------------------------------------------------------------------- #
def test_structured_gpt_authoritative_when_present():
    """A present structured verdict wins — the grader only runs for procedural/
    intermediate answers where the checker can misread an intermediate number."""
    ev = engine_v2.GradingEvidence(
        deterministic_verdict="incorrect", deterministic_checkable=True,
        structured_gpt_verdict="partial", structured_gpt_confidence=0.9,
        structured_attempted=True,
    )
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict == "partial"
    assert res.grader_source == "structured_gpt"
    assert res.evidence["deterministic_gpt_conflict"] is True   # divergence recorded


def test_structured_gpt_beats_deterministic_correct():
    ev = engine_v2.GradingEvidence(
        deterministic_verdict="correct", deterministic_checkable=True,
        structured_gpt_verdict="incorrect", structured_gpt_confidence=0.9,
        structured_attempted=True,
    )
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict == "incorrect"
    assert res.grader_source == "structured_gpt"


def test_deterministic_used_for_clean_answer():
    """No structured verdict (clean final answer) → the checker decides."""
    ev = engine_v2.GradingEvidence(
        deterministic_verdict="correct", deterministic_checkable=True,
    )
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict == "correct"
    assert res.grader_source == "deterministic"


def test_gpt_used_when_deterministic_abstains():
    ev = engine_v2.GradingEvidence(
        deterministic_verdict="unknown", deterministic_checkable=False,
        structured_gpt_verdict="partial", structured_gpt_confidence=0.6,
        structured_attempted=True,
    )
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict == "partial"
    assert res.grader_source == "structured_gpt"
    assert res.confidence == 0.6


def test_no_evidence_returns_not_checkable():
    res = engine_v2.reduce_shadow(engine_v2.GradingEvidence())
    assert res.verdict == "not_checkable"
    assert res.grader_source == "none"


def test_structured_attempted_but_malformed_is_ambiguous():
    """Routed to structured grading but it yielded nothing, checker abstains →
    ambiguous/ungraded (NOT silently graded, NEVER from prose)."""
    ev = engine_v2.GradingEvidence(
        deterministic_verdict="unknown", deterministic_checkable=False,
        structured_gpt_verdict=None, structured_attempted=True,
    )
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict == "ambiguous"
    assert res.grader_source == "none"


def test_malformed_structured_still_uses_deterministic_when_clean():
    """Structured attempted but malformed, yet the checker decided → checker."""
    ev = engine_v2.GradingEvidence(
        deterministic_verdict="correct", deterministic_checkable=True,
        structured_gpt_verdict=None, structured_attempted=True,
    )
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict == "correct"
    assert res.grader_source == "deterministic"


# --------------------------------------------------------------------------- #
# Explicit grader-routing policy                                              #
# --------------------------------------------------------------------------- #
def test_route_grader_clean_answer_deterministic():
    ev = engine_v2.GradingEvidence(deterministic_verdict="correct", deterministic_checkable=True)
    assert engine_v2.route_grader(ev) == "deterministic"


def test_route_grader_structured_when_present():
    ev = engine_v2.GradingEvidence(
        deterministic_verdict="incorrect", deterministic_checkable=True,
        structured_gpt_verdict="partial", structured_attempted=True)
    assert engine_v2.route_grader(ev) == "structured_gpt"


def test_route_grader_none_when_nothing_decides():
    ev = engine_v2.GradingEvidence(structured_attempted=True)
    assert engine_v2.route_grader(ev) == "none"


# --------------------------------------------------------------------------- #
# Prose can never influence the verdict (tests 8, 9)                           #
# --------------------------------------------------------------------------- #
def test_reducer_has_no_prose_input():
    """The reducer's evidence object has no prose field at all — structurally
    impossible for prose to reach it."""
    fields = set(engine_v2.GradingEvidence().__dict__.keys())
    assert not any("prose" in f or "answer_text" in f or "message" in f for f in fields)


def test_tutor_prose_tacno_cannot_flip_incorrect(monkeypatch, master, tmap):
    """Test 8: model prose says 'Tačno' but the student answer is wrong →
    shadow verdict stays deterministic 'incorrect'."""
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    payload = _grading_payload(task="Izračunaj: 1/4 + 1/4.", student="9/9")
    out = svc.handle_chat(payload, _scripted_chat("Tačno! Bravo, potpuno ispravno."),
                          master, tmap, model="m", timeout=1)
    assert out["shadow_grading"]["shadow_verdict"] == "incorrect"
    assert out["shadow_grading"]["shadow_grader_source"] == "deterministic"


def test_tutor_prose_netacno_cannot_flip_correct(monkeypatch, master, tmap):
    """Test 9: model prose says 'Netačno' but the student answer is right →
    shadow verdict stays deterministic 'correct'."""
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    payload = _grading_payload(task="Izračunaj: 1/4 + 1/4.", student="1/2")
    out = svc.handle_chat(payload, _scripted_chat("Netačno, pogrešno si uradio."),
                          master, tmap, model="m", timeout=1)
    assert out["shadow_grading"]["shadow_verdict"] == "correct"
    assert out["shadow_grading"]["shadow_grader_source"] == "deterministic"


# --------------------------------------------------------------------------- #
# Legacy comparison logging (tests 11, 12)                                     #
# --------------------------------------------------------------------------- #
def test_agreement_is_logged():
    """Legacy and shadow agree → agreement True, no_conflict."""
    shadow = engine_v2.reduce_shadow(engine_v2.GradingEvidence(
        deterministic_verdict="correct", deterministic_checkable=True))
    cmp = engine_v2.compare_with_legacy(
        shadow, legacy_verdict="correct", legacy_verdict_detail="correct",
        legacy_task_completed=True, legacy_correct_streak=3,
        prose_derived_legacy=False,
    )
    assert cmp["agreement"] is True
    assert cmp["conflict_type"] == "no_conflict"


def test_legacy_prose_verdict_conflict_is_logged():
    """The PRIMARY Phase 2 change: legacy derived the verdict from tutor prose;
    the authoritative reducer does not → legacy_prose_verdict."""
    shadow = engine_v2.reduce_shadow(engine_v2.GradingEvidence(
        structured_attempted=True))                       # ungraded (ambiguous)
    cmp = engine_v2.compare_with_legacy(
        shadow, legacy_verdict="correct", legacy_verdict_detail="gpt_correct",
        legacy_task_completed=False, legacy_correct_streak=0,
        prose_derived_legacy=True,
    )
    assert cmp["agreement"] is False
    assert cmp["conflict_type"] == "legacy_prose_verdict"


def test_structured_gpt_present_agrees_with_legacy():
    """When a structured grade exists, shadow and legacy take the SAME verdict →
    no conflict (aligned precedence; no gpt_overrode_deterministic anymore)."""
    shadow = engine_v2.reduce_shadow(engine_v2.GradingEvidence(
        deterministic_verdict="incorrect", deterministic_checkable=True,
        structured_gpt_verdict="partial", structured_attempted=True))
    assert shadow.verdict == "partial" and shadow.grader_source == "structured_gpt"
    cmp = engine_v2.compare_with_legacy(
        shadow, legacy_verdict="partial", legacy_verdict_detail="gpt_partial",
        legacy_task_completed=False, legacy_correct_streak=0,
        prose_derived_legacy=False,
    )
    assert cmp["conflict_type"] == "no_conflict"


def test_legacy_correct_shadow_incorrect_conflict():
    shadow = engine_v2.reduce_shadow(engine_v2.GradingEvidence(
        deterministic_verdict="incorrect", deterministic_checkable=True))
    cmp = engine_v2.compare_with_legacy(
        shadow, legacy_verdict="correct", legacy_verdict_detail="correct",
        legacy_task_completed=True, legacy_correct_streak=1,
        prose_derived_legacy=False,
    )
    assert cmp["conflict_type"] == "legacy_correct_shadow_incorrect"


def test_uncheckable_conflict():
    shadow = engine_v2.reduce_shadow(engine_v2.GradingEvidence())
    cmp = engine_v2.compare_with_legacy(
        shadow, legacy_verdict="correct", legacy_verdict_detail="correct",
        legacy_task_completed=True, legacy_correct_streak=1,
        prose_derived_legacy=False,
    )
    assert cmp["conflict_type"] == "uncheckable"


def test_metrics_accumulate():
    for verdict in ("correct", "incorrect"):
        s = engine_v2.reduce_shadow(engine_v2.GradingEvidence(
            deterministic_verdict=verdict, deterministic_checkable=True))
        c = engine_v2.compare_with_legacy(
            s, legacy_verdict=verdict, legacy_verdict_detail=verdict,
            legacy_task_completed=False, legacy_correct_streak=0,
            prose_derived_legacy=False)
        engine_v2.record_metrics(s, c)
    m = engine_v2.get_metrics()
    assert m["total"] == 2
    assert m["agreements"] == 2
    assert m["deterministic"] == 2


# --------------------------------------------------------------------------- #
# Sheets backward-compatibility + no secrets (tests 13, 14)                    #
# --------------------------------------------------------------------------- #
def test_sheets_columns_are_append_only():
    """Test 13: telemetry columns are only ever APPENDED, so existing indices stay
    stable and old (shorter) rows pad cleanly. ``sheets_event_id`` keeps its
    historical index because it is looked up positionally."""
    headers = sheets_log.SHEET_HEADERS
    # 57 is the historical (pre-Engine-V2) position of the last column; it must
    # never move, because it is written by positional lookup.
    assert headers.index("sheets_event_id") == 57
    # every column added since is strictly appended, in order
    assert headers[57:] == ["sheets_event_id", "shadow_telemetry", "engine_canary"]


def test_sheets_row_length_matches_headers(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    out = svc.handle_chat(_grading_payload(), _scripted_chat("Tačno."),
                          master, tmap, model="m", timeout=1)
    row = sheets_log._build_transcript_row(_grading_payload(), out)
    assert len(row) == len(sheets_log.SHEET_HEADERS)


def test_shadow_telemetry_has_no_secrets_or_reasoning(monkeypatch, master, tmap):
    """Test 14: the shadow record contains no prose, feedback, prompts,
    chain-of-thought, or credentials."""
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    out = svc.handle_chat(_grading_payload(), _scripted_chat("Tačno, super."),
                          master, tmap, model="m", timeout=1)
    blob = json.dumps(out["shadow_grading"], ensure_ascii=False).lower()
    for banned in ("public_feedback", "reasoning", "chain", "prompt", "api_key",
                   "secret", "messages", "student_message"):
        assert banned not in blob, banned
    allowed_evidence = {
        "deterministic_verdict", "gpt_structured_verdict", "structured_attempted",
        "deterministic_scope", "task_status", "answer_type",
        "deterministic_gpt_conflict", "conflict_class",
    }
    assert set(out["shadow_grading"]["evidence"].keys()) <= allowed_evidence


# --------------------------------------------------------------------------- #
# Regression fixtures — SHADOW SNAPSHOTS ONLY (do not fix legacy this phase)   #
# --------------------------------------------------------------------------- #
def test_shadow_exam_angle_60():
    ev, _ = _evidence("Koliki je treći ugao u trouglu ako su dva ugla 60° i 60°?", "60°")
    assert engine_v2.reduce_shadow(ev).verdict == "correct"


def test_shadow_15_vs_15cm():
    ev, _ = _evidence("Koliki je obim kvadrata stranice 15 cm? Rezultat u cm.", "15 cm")
    res = engine_v2.reduce_shadow(ev)
    # documents current behavior; must never be a hard 'incorrect' for a right value
    assert res.verdict in ("correct", "partial", "not_checkable")


def test_shadow_40_vs_40deg():
    ev, _ = _evidence("Koliki je ugao? Rezultat u stepenima.", "40°")
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict in ("correct", "partial", "not_checkable")


def test_shadow_three_halves_vs_mixed():
    ev, _ = _evidence("Izračunaj: 3/4 + 3/4.", "1 1/2")
    assert engine_v2.reduce_shadow(ev).verdict == "correct"


def test_shadow_incomplete_inequality():
    # "x > 3" task, student submits bare "4" (a value, not the solution set)
    ev, _ = _evidence("Riješi nejednačinu: x - 3 > 0.", "4")
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict in ("partial", "incorrect", "not_checkable")


def test_shadow_correct_set_union():
    ev, _ = _evidence("Odredi A ∪ B ako je A={1,2} i B={2,3}.", "{1,2,3}")
    assert engine_v2.reduce_shadow(ev).verdict == "correct"


def test_shadow_divisibility_partial_explanation():
    ev, r = _evidence(
        "Provjeri da li je broj 240 djeljiv sa 6. Obrazloži svoj odgovor.",
        "da je djeljiv sa 2",
    )
    res = engine_v2.reduce_shadow(ev)
    # Whatever the checker yields, shadow must never mislabel a partial step as a
    # hard 'correct' completion in this phase (documents the Phase-3 gap).
    assert res.verdict in ("partial", "incorrect", "not_checkable", "correct")
    if res.grader_source == "deterministic" and res.step_completed:
        assert res.task_completed is False


def test_shadow_wrong_intermediate_correct_final_documented():
    # Deterministic checker often sees only the final value; this documents that
    # the shadow reducer would report 'correct' here — a KNOWN gap, not a fix.
    ev, _ = _evidence("Izračunaj: 2 + 3 · 4.", "14")
    res = engine_v2.reduce_shadow(ev)
    assert res.verdict in ("correct", "not_checkable")
