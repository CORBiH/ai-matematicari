# -*- coding: utf-8 -*-
"""Production defect fixes.

BUG 1 — structured GPT must not downgrade a VERIFIED full-answer deterministic
        result (144 divisible by 3 and 4), while an intermediate statement that
        the checker mis-scored (common denominator) must still yield partial.
BUG 2 — a selected tema must never collapse into its broader oblast
        ("Proširivanje razlomaka" → fraction multiplication).
"""
import json
import types

import pytest

from matbot import task_templates as tt
from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.answer_checker import check_practice_answer
from matbot.grading_guard import authoritative_verdict

T144 = "Provjeri je li broj 144 djeljiv sa 3 i 4. Obrazloži svoje odgovore."
A144 = "Jest jer je zbir cifara djeljiv sa 3 i zadnja dva broja su djeljiva sa 4."
COMMON_DENOM = ("Izračunaj: 1/2 + 1/3.", "Zajednički nazivnik je 6")


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
            body = json.dumps({"verdict": gpt_verdict, "confidence": 0.9,
                               "public_feedback": ""})
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=body))])
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=tutor))])
    return chat


def _grade(master, tmap, task, student, gpt_verdict=None, tutor="U redu."):
    return svc.handle_chat({"grade": 6, "mode": "practice",
                            "interaction_phase": "answering_practice_task",
                            "last_tutor_task": task, "student_message": student},
                           _model(gpt_verdict, tutor), master, tmap,
                           model="m", timeout=1)


# --------------------------------------------------------------------------- #
# BUG 1 — evidence model                                                      #
# --------------------------------------------------------------------------- #
def test_scope_full_answer_for_explanation_task():
    """144 expects required concepts → a positive verdict verified the WHOLE
    answer, so it is full-task decisive."""
    check = check_practice_answer(T144, A144)
    assert authoritative_verdict(check) == "correct"
    assert svc._deterministic_scope(check) == "full_answer"
    assert svc._deterministic_full_task_decisive({"answer_check": check}) is True


def test_scope_value_only_for_bare_numeric_task():
    check = check_practice_answer("Izračunaj: 1/2 · 5/9.", "5/18")
    assert svc._deterministic_scope(check) == "value_only"
    # correct, but only a VALUE was verified → not full-task decisive
    assert svc._deterministic_full_task_decisive({"answer_check": check}) is False


def test_negative_deterministic_is_never_decisive():
    """The checker may mistake an intermediate for a final answer."""
    check = check_practice_answer(*COMMON_DENOM)
    assert authoritative_verdict(check) == "incorrect"
    assert svc._deterministic_full_task_decisive({"answer_check": check}) is False


# --------------------------------------------------------------------------- #
# BUG 1 — required conflict-resolution matrix                                 #
# --------------------------------------------------------------------------- #
def test_1_verified_full_answer_beats_gpt_partial(master, tmap):
    out = _grade(master, tmap, T144, A144, gpt_verdict="partial")
    assert out["answer_verdict"] == "correct"
    assert out["gpt_check_used"] is False


def test_2_common_denominator_stays_partial(master, tmap):
    out = _grade(master, tmap, *COMMON_DENOM, gpt_verdict="partial")
    assert out["answer_verdict"] == "partial"          # NOT deterministic incorrect


def test_3_clean_numeric_final_deterministic_authoritative(master, tmap):
    out = _grade(master, tmap, "Izračunaj: 1/4 + 1/4.", "1/2")
    assert out["answer_verdict"] == "correct"


def test_4_procedural_missing_reasoning_may_stay_partial(master, tmap):
    out = _grade(master, tmap, "Izračunaj: 1/2 · 5/9.",
                 "prvo sam pomnozio brojnike pa je 5/18", gpt_verdict="partial")
    assert out["answer_verdict"] == "partial"


def test_5_both_correct(master, tmap):
    out = _grade(master, tmap, T144, A144, gpt_verdict="correct")
    assert out["answer_verdict"] == "correct"


def test_6_gpt_factually_wrong_logged(master, tmap, caplog):
    out = _grade(master, tmap, T144, A144, gpt_verdict="incorrect")
    assert out["answer_verdict"] == "correct"
    # the feedback must not claim the student was wrong
    assert not out["answer"].lower().startswith("netačno")


def test_7_checker_abstains_uses_structured_gpt(master, tmap):
    out = _grade(master, tmap, "Objasni šta je prost broj.",
                 "broj djeljiv samo sa 1 i sobom jer nema drugih djelilaca",
                 gpt_verdict="partial")
    if out["gpt_check_used"]:
        assert out["answer_verdict"] == "partial"


def test_8_both_unavailable_never_prose_derived(master, tmap):
    out = _grade(master, tmap, "Objasni šta je prost broj.", "onako nešto",
                 gpt_verdict=None, tutor="Tačno! Odlično.")
    assert out["answer_verdict"] != "correct"           # prose is never evidence


def test_conflict_class_in_shadow_telemetry(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    out = _grade(master, tmap, T144, A144, gpt_verdict="partial")
    ev = out["shadow_grading"]["evidence"]
    assert ev["deterministic_scope"] == "full_answer"
    assert ev["conflict_class"] == "gpt_contradicted_verified_deterministic"
    assert out["shadow_grading"]["shadow_verdict"] == "correct"


def test_flag_off_grading_unchanged(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "off")
    out = _grade(master, tmap, T144, A144, gpt_verdict="partial")
    assert out["answer_verdict"] == "partial"           # legacy behavior preserved


# --------------------------------------------------------------------------- #
# BUG 2 — exact tema selection                                                #
# --------------------------------------------------------------------------- #
EXPAND = {"selected_oblast": "Razlomci", "selected_topic": "6-04-035",
          "lesson_title": "Proširivanje razlomaka"}


def _practice(master, tmap, message, extra, prev=None):
    payload = {"grade": 6, "mode": "practice", "session_id": "t", **extra,
               "student_message": message}
    if prev:
        payload["previous_next_state"] = prev
    return svc.handle_chat(payload, _model(tutor="Zadatak: LEGACY-MODEL"),
                           master, tmap, model="m", timeout=1)


def test_tema_selects_exactly_one_skill():
    assert [t.skill_id for t in tt.select_templates(6, "Razlomci", "Proširivanje razlomaka")] \
        == ["fraction_expand"]
    assert [t.skill_id for t in tt.select_templates(6, "Razlomci", "6-04-035")] \
        == ["fraction_expand"]
    assert [t.skill_id for t in tt.select_templates(6, "Razlomci", "6-04-041")] \
        == ["fraction_mul"]


def test_tema_never_collapses_to_oblast():
    """An uncovered tema must yield NO templates, never the oblast's skills."""
    assert tt.select_templates(6, "Razlomci", "Dvojni razlomci") == []
    assert tt.has_coverage(6, "Razlomci", "Dvojni razlomci") is False
    # oblast alone still matches the oblast's skills
    assert len(tt.select_templates(6, "Razlomci")) >= 2


@pytest.mark.parametrize("message", [
    "daj mi novi zadatak",
    "Daj mi teži zadatak iz iste teme.",
    "Daj mi lakši zadatak iz iste teme.",
])
def test_1_2_3_repeated_and_difficulty_requests_stay_expansion(
        monkeypatch, master, tmap, message):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    out = _practice(master, tmap, message, EXPAND)
    assert out["last_tutor_task"].lower().startswith("proširi")
    assert "·" not in out["last_tutor_task"]            # never multiplication


def test_4_multi_turn_never_drifts_to_generic_razlomci(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    prev, tasks = None, []
    for msg in ("daj mi zadatak", "Daj mi teži zadatak iz iste teme.",
                "daj mi novi zadatak", "Daj mi lakši zadatak iz iste teme."):
        out = _practice(master, tmap, msg, EXPAND, prev)
        prev = out["next_state"]
        tasks.append(out["last_tutor_task"])
    assert all(t.lower().startswith("proširi") for t in tasks), tasks


def test_5_uncovered_tema_uses_explicit_fallback(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    out = _practice(master, tmap, "daj mi zadatak",
                    {"selected_oblast": "Razlomci", "selected_topic": "6-04-043",
                     "lesson_title": "Dvojni razlomci"})
    # falls through to the legacy model path — never an unrelated template task
    assert "LEGACY-MODEL" in out["answer"]


def test_6_exam_generation_follows_exact_tema(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_EXAM", "on")
    out = svc.handle_chat({"grade": 6, "mode": "exam", "session_id": "ex",
                           **EXPAND, "student_message": "daj mi kontrolni"},
                          _model(), master, tmap, model="m", timeout=1)
    es = out["exam_state"]
    assert es["topic_covered"] is True
    assert all(it["question"].lower().startswith("proširi") for it in es["items"])


def test_7_task_definition_preserves_identity(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2", "shadow")
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    out = _practice(master, tmap, "daj mi zadatak", EXPAND)
    td = out["next_state"]["task"]
    assert td["skill_id"] == "fraction_expand"          # stable template skill
    assert td["grade"] == 6
    assert "razlomci" in td["oblast_id"]
    assert "6-04-035" in td["tema_id"]
    assert td["source"] == "template"
    assert td["validation_status"] == "validated"
    assert td["task_id"] == out["task_id"] and td["task_id"]


def test_8_flag_off_legacy_generation_unchanged(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "off")
    out = _practice(master, tmap, "daj mi novi zadatak", EXPAND)
    assert "LEGACY-MODEL" in out["answer"]              # model path, no template


def test_expansion_template_self_validates():
    import random
    for s in range(120):
        q, a = tt._BY_ID["fraction_expand"].generate(random.Random(s))
        assert tt._validates(q, a), (q, a)
