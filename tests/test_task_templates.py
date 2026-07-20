# -*- coding: utf-8 -*-
"""Phase 5 — shared deterministic task-generation layer + engine integration.

Every generated task must be VALIDATED (the code-computed answer accepted by the
checker). Guidable skills must be guidable by the Practice Step Engine. Topic
selection must return only on-topic templates and NO coverage for unsupported
temas — callers must fall back EXPLICITLY, never silently substitute.
"""
import random
import types

import pytest

from matbot import task_templates as tt
from matbot import solution_plan as sp
from matbot import exam_engine as ee
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl


# --------------------------------------------------------------------------- #
# Pure generation layer                                                       #
# --------------------------------------------------------------------------- #
def test_every_template_self_validates():
    """Across many draws, each template's computed answer is checker-accepted."""
    failures = {}
    for t in tt._TEMPLATES:
        for s in range(60):
            q, a = t.generate(random.Random(s))
            if not tt._validates(q, a):
                failures.setdefault(t.skill_id, []).append((q, a))
    assert not failures, failures


def test_guidable_generated_tasks_are_guidable():
    for skill in tt.GUIDABLE_SKILLS:
        task = tt._generate_from(tt._BY_ID[skill], random.Random(3),
                                 grade=6, oblast="", tema="")
        assert task is not None
        assert sp.build_plan_for_task(task.question) is not None


def test_generate_one_is_validated():
    task = tt.generate_one(6, "Razlomci", seed="x")
    assert task is not None
    assert task.validation_status == "validated"
    assert tt._validates(task.question, task.expected_display)


def test_generate_batch_distinct_and_validated():
    tasks = tt.generate_batch(6, "Djeljivost brojeva", count=3, seed="y")
    assert len(tasks) == 3
    assert len({t.question for t in tasks}) == 3          # distinct
    for t in tasks:
        assert tt._validates(t.question, t.expected_display)


def test_generate_one_respects_avoid():
    first = tt.generate_one(6, "Razlomci", seed="z")
    again = tt.generate_one(6, "Razlomci", seed="z", avoid={first.question})
    assert again is None or again.question != first.question


def test_no_coverage_returns_none_and_empty():
    assert tt.generate_one(7, "Vektori") is None          # no template for vektori
    assert tt.generate_batch(7, "Vektori", count=3) == []
    assert tt.has_coverage(7, "Vektori") is False
    assert tt.has_coverage(6, "Djeljivost brojeva") is True


def test_topic_selection_is_on_topic():
    dj = {t.skill_id for t in tt.select_templates(6, "Djeljivost brojeva")}
    assert "divisibility_by_6" in dj and "prime_factorization" in dj
    assert "fraction_add_sub" not in dj                    # razlomci not in djeljivost
    # Oblast-level selection returns every Razlomci skill; TEMA-level selection
    # narrows to exactly one (see test_tema_selects_exactly_one_skill).
    ra = {t.skill_id for t in tt.select_templates(6, "Razlomci")}
    assert ra == {"fraction_expand", "fraction_add_sub", "fraction_mul"}


def test_grade_gating():
    # divisibility_by_6 is grade 6 only.
    assert any(t.skill_id == "divisibility_by_6" for t in tt.select_templates(6, "Djeljivost"))
    assert not any(t.skill_id == "divisibility_by_6" for t in tt.select_templates(7, "Djeljivost"))


def test_stable_skill_ids():
    ids = {t.skill_id for t in tt._TEMPLATES}
    assert {"divisibility_by_6", "prime_factorization", "gcd", "lcm", "fraction_add_sub",
            "fraction_mul", "percent_of", "unit_conversion", "linear_equation",
            "triangle_angle", "set_union"} <= ids


# --------------------------------------------------------------------------- #
# Exam Engine topic-selection integration                                     #
# --------------------------------------------------------------------------- #
def test_exam_covered_oblast_is_on_topic():
    state = ee.start_exam(seed="e", count=3, grade=6, oblast="Razlomci")
    assert state.topic_covered is True
    for it in state.items:
        assert "/" in it.question                          # fraction items


def test_exam_uncovered_oblast_explicit_generic_fallback():
    state = ee.start_exam(seed="e", count=3, grade=7, oblast="Vektori")
    assert state.topic_covered is False                    # NOT silently on-topic
    assert state.oblast == "Vektori"
    assert len(state.items) == 3                           # still a usable exam


def test_exam_no_topic_is_generic_covered():
    state = ee.start_exam(seed="e", count=3, grade=6, oblast="", tema="")
    assert state.topic_covered is True                     # generic is what was asked


# --------------------------------------------------------------------------- #
# Service integration                                                         #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "a.sqlite3"))
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
        raise AssertionError("model must not be called for a deterministic template task")
    return chat


def _rec_model(reply="Zadatak: model-generated."):
    calls = {"n": 0}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["n"] += 1
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=reply))])
    chat.calls = calls
    return chat


def test_exam_engine_uses_templates_for_selected_topic(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = svc.handle_chat({"grade": 6, "mode": "exam", "selected_oblast": "Razlomci",
                           "session_id": "s", "student_message": "daj mi kontrolni"},
                          _no_model(), master, tmap, model="m", timeout=1)
    es = out["exam_state"]
    assert es["topic_covered"] is True
    assert all("/" in it["question"] for it in es["items"])


def test_exam_engine_explicit_fallback_for_uncovered(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = svc.handle_chat({"grade": 7, "mode": "exam", "selected_oblast": "Vektori",
                           "session_id": "s", "student_message": "daj mi kontrolni"},
                          _no_model(), master, tmap, model="m", timeout=1)
    es = out["exam_state"]
    assert es["topic_covered"] is False
    assert "OPŠTI" in out["answer"] or "opšti" in out["answer"].lower()   # explicit label


def test_practice_generates_deterministic_task(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    out = svc.handle_chat({"grade": 6, "mode": "practice",
                           "selected_oblast": "Djeljivost brojeva",
                           "session_id": "p", "student_message": "daj mi novi zadatak"},
                          _no_model(), master, tmap, model="m", timeout=1)   # model MUST NOT be called
    assert out["answer"].startswith("Zadatak:")
    assert out["last_tutor_task"]
    assert out["task_status"] == "active"
    assert out["next_state"]["active_task_kind"] == "practice"


def test_practice_generated_task_is_gradeable_next_turn(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    first = svc.handle_chat({"grade": 6, "mode": "practice",
                             "selected_oblast": "Razlomci",
                             "session_id": "p2", "student_message": "daj mi zadatak"},
                            _no_model(), master, tmap, model="m", timeout=1)
    task = first["last_tutor_task"]
    from matbot.answer_checker import derive_expected, _fmt_expected
    correct = _fmt_expected(derive_expected(task))

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="Tačno."))])
    out = svc.handle_chat({"grade": 6, "mode": "practice",
                           "interaction_phase": "answering_practice_task",
                           "last_tutor_task": task, "student_message": correct,
                           "previous_next_state": first["next_state"]},
                          chat, master, tmap, model="m", timeout=1)
    assert out["answer_verdict"] in ("correct", "partial")   # deterministically graded


def test_practice_uncovered_tema_uses_legacy_model(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    chat = _rec_model()
    out = svc.handle_chat({"grade": 7, "mode": "practice", "selected_oblast": "Vektori",
                           "session_id": "p3", "student_message": "daj mi novi zadatak"},
                          chat, master, tmap, model="m", timeout=1)
    assert chat.calls["n"] >= 1                              # legacy model path used


def test_practice_flag_off_no_generation(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "off")
    chat = _rec_model()
    svc.handle_chat({"grade": 6, "mode": "practice", "selected_oblast": "Djeljivost brojeva",
                     "session_id": "p4", "student_message": "daj mi novi zadatak"},
                    chat, master, tmap, model="m", timeout=1)
    assert chat.calls["n"] >= 1                              # legacy path (model), no template
