# -*- coding: utf-8 -*-
"""One activation authority for the V2 task lifecycle.

The 13-branch ``task_text`` ladder in ``_finalize_response`` used to decide, on
its own, what became the active task. It now only PRODUCES labelled candidates;
with V2 enabled nothing reaches active state except through
``task_activation.activate``. These tests pin that authority down — including the
negative cases (praise, invitations, off-topic, ungradeable) that used to slip
through because a regex found a question in model prose.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot import exam_engine as ee
from matbot import solution_plan
from matbot import task_activation as ta
from matbot import topic_resolver as tr
from matbot import turn_intent as ti

ARC_TASK = ("Poluprečnik kružnice je 8 cm, centralni ugao je 90°. "
            "Izračunaj dužinu kružnog luka.")
CIRCLES_NPP = "6-08-079"
EXPAND = {"selected_oblast": "Razlomci", "selected_topic": "6-04-035"}

V2_ON = {"MATBOT_ENGINE_V2": "shadow", "MATBOT_ENGINE_V2_GRADING": "on",
         "MATBOT_ENGINE_V2_PRACTICE": "on", "MATBOT_ENGINE_V2_EXAM": "on"}
V2_OFF = {k: "off" for k in V2_ON}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "a.sqlite3"))
    for k, v in V2_OFF.items():
        monkeypatch.setenv(k, v)
    tr.reset_cache()
    yield
    tr.reset_cache()


@pytest.fixture()
def v2(monkeypatch):
    for k, v in V2_ON.items():
        monkeypatch.setenv(k, v)


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _model(reply="U redu."):
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=reply))])
    return chat


def _chat(master, tmap, payload, reply="U redu."):
    return svc.handle_chat(dict(payload), _model(reply), master, tmap,
                           model="m", timeout=1)


def _identity(grade=6, topic="6-04-035", oblast="Razlomci"):
    return tr.identify(grade, topic, oblast=oblast)


def _ok(q):
    return {"validation_status": "validated"}


# =========================================================================== #
# 1-6: every explicit source activates through the one gate                   #
# =========================================================================== #
def test_1_deterministic_practice_task_activates(v2, master, tmap):
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a1",
                               **EXPAND, "student_message": "daj mi zadatak"})
    assert out["last_tutor_task"].lower().startswith("proširi")
    assert out["next_state"]["task"]["source"] == "template"
    assert out["next_state"]["task_id"]


def test_2_explanation_micro_task_activates(v2, master, tmap):
    out = _chat(master, tmap, {"grade": 6, "mode": "explain", "session_id": "a2",
                               "student_message": "objasni djeljivost sa 4"},
                "Pravilo je jednostavno.\nProbaj ti: da li je broj 24 djeljiv sa 4?")
    micro = out["next_state"]["micro_task"]
    assert micro is not None and micro["task_id"] and micro["kind"] == "micro"


def test_3_student_supplied_task_activates(v2, master, tmap):
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a3",
                               "student_message": "evo moj zadatak: 3/4 + 5/6"},
                "Idemo redom.")
    assert "3/4" in (out["last_tutor_task"] or "")
    assert out["next_state"]["task"]["source"] == "student_task"


def test_4_exam_items_activate_through_the_gate(v2):
    state = ee.start_exam(seed="a4", count=3, grade=6, oblast="Djeljivost brojeva")
    assert state.items
    for it in state.items:
        assert it.task_id, it.question           # each item was ACTIVATED


def test_4b_exam_item_that_cannot_activate_is_dropped(v2, monkeypatch):
    monkeypatch.setattr(ta, "activate",
                        lambda **kw: ta.ActivationDecision(activated=False,
                                                           reason="forced"))
    state = ee.start_exam(seed="a4b", count=3, grade=6, oblast="Djeljivost brojeva")
    assert state.items == []                     # never an unactivated item


def test_5_image_task_activates_and_persists(v2, master, tmap):
    ocr = "1. 3/10 + 4/10\n2. 7/12 - 5/12"
    first = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a5",
                                 "image_ocr_text": ocr, "student_message": ""},
                  "Zadatak 1: 3/10 + 4/10.")
    assert first["last_tutor_task"]
    assert first["next_state"]["active_task_kind"] == "image_test"


def test_6_model_candidate_is_validated_then_activated(v2, master, tmap):
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a6",
                               "selected_oblast": "Razlomci",
                               "student_message": "daj mi zadatak"},
                "Zadatak: Izračunaj 1/4 + 1/4.")
    assert out["last_tutor_task"]
    assert out["task_validation"]["validation_status"] == "validated"


# =========================================================================== #
# 7-10: candidates that must NOT activate                                     #
# =========================================================================== #
def test_7_off_topic_candidate_is_rejected(v2):
    ident = tr.identify(6, CIRCLES_NPP, oblast="Skupovi tačaka, kružnica i krug")
    d = ta.activate(question=ARC_TASK, source=ta.SOURCE_MODEL, topic=ident,
                    validator=_ok)
    assert d.activated is False and d.reason.startswith("off_topic")


def test_8_ungradeable_candidate_is_rejected(v2):
    d = ta.activate(question="Izračunaj nešto zanimljivo sa 5.",
                    source=ta.SOURCE_MODEL, topic=None,
                    validator=lambda q: {"validation_status": "rejected",
                                         "reason": "ungradeable"})
    assert d.activated is False and d.reason.startswith("invalid")


@pytest.mark.parametrize("prose", [
    "Bravo! Odlično si to uradio.",
    "Tačno.",
    "Dobro je što si pokušao.",
    "Idemo dalje.",
])
def test_9_praise_and_feedback_never_activate(v2, prose):
    d = ta.activate(question=prose, source=ta.SOURCE_MODEL, topic=None,
                    validator=_ok)
    assert d.activated is False, prose


@pytest.mark.parametrize("prose", [
    "Želiš li novi zadatak?",
    "Hoćeš li još jedan?",
    "Jesi li spreman?",
    "Zadatak:",
])
def test_10_invitations_and_headings_never_activate(v2, prose):
    d = ta.activate(question=prose, source=ta.SOURCE_MODEL, topic=None,
                    validator=_ok)
    assert d.activated is False, prose


def test_9b_a_real_task_still_activates(v2):
    """The prose gate must not reject genuine tasks."""
    d = ta.activate(question="Izračunaj: 3/4 + 1/4.", source=ta.SOURCE_MODEL,
                    topic=None, validator=_ok)
    assert d.activated is True


# =========================================================================== #
# 11-13: task identity across the lifecycle                                   #
# =========================================================================== #
def test_11_hint_does_not_replace_the_parent_task(v2, master, tmap):
    first = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a11",
                                 **EXPAND, "student_message": "daj mi zadatak"})
    task, ns = first["last_tutor_task"], first["next_state"]
    hinted = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a11",
                                  **EXPAND, "last_tutor_task": task,
                                  "interaction_phase": "answering_practice_task",
                                  "previous_next_state": ns,
                                  "student_message": "daj mi hint"},
                   "Sjeti se: brojnik i nazivnik množiš istim brojem. "
                   "Probaj ti: koliko je 2·3?")
    assert hinted["last_tutor_task"] == task     # parent survives the hint


def test_12_task_id_is_stable_across_help_and_retries(v2, master, tmap):
    first = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a12",
                                 **EXPAND, "student_message": "daj mi zadatak"})
    task, ns = first["last_tutor_task"], first["next_state"]
    tid = ns["task_id"]
    assert tid
    for msg in ("ne znam", "daj mi hint", "999"):
        out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a12",
                                   **EXPAND, "last_tutor_task": task,
                                   "interaction_phase": "answering_practice_task",
                                   "previous_next_state": ns,
                                   "student_message": msg}, "Idemo korak po korak.")
        if out["last_tutor_task"] == task:
            assert out["next_state"]["task_id"] == tid, msg


def test_13_an_explicit_new_task_gets_a_new_task_id(v2, master, tmap):
    first = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a13",
                                 **EXPAND, "message_index": 0,
                                 "student_message": "daj mi zadatak"})
    second = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a13",
                                  **EXPAND, "message_index": 1,
                                  "last_tutor_task": first["last_tutor_task"],
                                  "recent_tasks": [first["last_tutor_task"]],
                                  "previous_next_state": first["next_state"],
                                  "student_message": "daj mi novi zadatak"})
    assert second["last_tutor_task"] != first["last_tutor_task"]
    assert second["next_state"]["task_id"] != first["next_state"]["task_id"]


# =========================================================================== #
# 14-16: identity, mirror, and no prose dependence                            #
# =========================================================================== #
def test_14_exact_topic_identity_stays_attached(v2, master, tmap):
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a14",
                               **EXPAND, "student_message": "daj mi zadatak"})
    td = out["next_state"]["task"]
    assert td["tema_id"] == "6-04-035"
    assert td["tema_title"] == "Proširivanje razlomaka"
    assert td["skill_id"] == "fraction_expand"


def test_15_last_tutor_task_mirrors_the_activated_definition(v2, master, tmap):
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a15",
                               **EXPAND, "student_message": "daj mi zadatak"})
    assert out["next_state"]["task"]["question"] == out["last_tutor_task"]


def test_16_v2_state_does_not_depend_on_prose_extraction(v2, master, tmap):
    """A template task is presented by a direct answer; the model says something
    else entirely, and the active task is STILL the structured one."""
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a16",
                               **EXPAND, "student_message": "daj mi zadatak"},
                "Zadatak: Riješi jednačinu: 9x + 1 = 10.")
    assert out["last_tutor_task"].lower().startswith("proširi")
    assert "jednačin" not in out["last_tutor_task"].lower()


def test_16b_the_ladder_produces_candidates_not_activations():
    """Structural: the producer returns a candidate and never a decision."""
    import inspect
    src = inspect.getsource(svc._task_candidate)
    assert "TaskCandidate" in src or "TC(" in src
    # The producer never calls the gate — it only labels proposals.
    assert "activate(" not in src and "activate_candidate(" not in src


# =========================================================================== #
# 17-18: one help-intent classifier                                           #
# =========================================================================== #
@pytest.mark.parametrize("msg", [
    "ne znam", "pomozi", "daj hint", "objasni", "objasni ti", "kako",
    "kako se radi", "nemam pojma", "zapeo sam", "ne razumijem", "savjet", "help",
])
def test_17_all_help_phrases_share_one_intent(msg):
    assert ti.wants_support(msg) is True, msg


def test_17b_step_engine_uses_the_shared_classifier():
    src = open("matbot/solution_plan.py", encoding="utf-8").read()
    assert "_HELP_RE" not in src
    assert "turn_intent.wants_support" in src


def test_17c_exam_and_step_engine_agree_on_help():
    for msg in ("ne znam", "pomozi", "objasni ti", "nemam pojma"):
        assert ti.classify(msg).is_non_answer is True, msg
        assert ti.wants_support(msg) is True, msg


@pytest.mark.parametrize("msg", [
    "da, sa 5 jer se završava nulom",
    "da jer je zbir cifara 6",
    "da",
    "ne",
    "4pi cm",
    "12.57 cm",
    "zajednički nazivnik je 6",
])
def test_18_answers_containing_da_are_not_help(msg):
    assert ti.wants_support(msg) is False, msg


def test_18b_bare_da_is_a_confirmation_not_help():
    assert ti.classify("da").intent is ti.Intent.CONFIRMATION
    assert ti.classify("da", expects_boolean=True).intent is ti.Intent.ANSWER


def test_18c_step_engine_help_parity():
    """The migrated detector keeps the pre-migration verdict for 'zašto?'."""
    plan = solution_plan.build_plan_for_task(
        "Provjeri da li je broj 246 djeljiv sa 6. Obrazloži svoj odgovor.")
    assert plan is not None
    first = plan.steps[0]
    assert solution_plan.check_step(first, "ne znam") == solution_plan.HELP
    assert solution_plan.check_step(first, "zašto?") != solution_plan.HELP


# =========================================================================== #
# 19-20: flag-off parity and all-V2 integration                               #
# =========================================================================== #
def test_19_flag_off_keeps_the_legacy_ladder(master, tmap):
    """With V2 off the ladder still decides — rollback path untouched."""
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a19",
                               **EXPAND, "student_message": "daj mi novi zadatak"},
                "Zadatak: LEGACY-MODEL 2/3 + 1/3.")
    assert "LEGACY-MODEL" in out["answer"]
    assert out["next_state"].get("task") is None      # no V2 TaskDefinition
    assert "_activation" not in out


def test_19b_flag_off_prose_gate_does_not_apply(master, tmap):
    """The prose gate is a V2 behavior; legacy activation is unchanged."""
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a19b",
                               "selected_oblast": "Razlomci",
                               "student_message": "daj mi zadatak"},
                "Zadatak: Izračunaj 1/4 + 1/4.")
    assert "1/4" in (out["last_tutor_task"] or "")


def test_20_all_v2_grading_counters_and_streak(v2, master, tmap):
    first = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a20",
                                 **EXPAND, "student_message": "daj mi zadatak"})
    task, ns = first["last_tutor_task"], first["next_state"]
    wrong = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a20",
                                 **EXPAND, "last_tutor_task": task,
                                 "interaction_phase": "answering_practice_task",
                                 "previous_next_state": ns,
                                 "student_message": "999/999"}, "Nije tačno.")
    assert wrong["answer_verdict"] == "incorrect"
    assert wrong["next_state"]["correct_streak"] == 0
    assert wrong["next_state"]["wrong_attempt_count"] >= 1
    assert wrong["next_state"]["task_id"] == ns["task_id"]     # same task


def test_20b_all_v2_exam_progression(v2, master, tmap):
    start = _chat(master, tmap, {"grade": 6, "mode": "exam", "session_id": "a20b",
                                 "selected_oblast": "Djeljivost brojeva",
                                 "student_message": "daj mi kontrolni"})
    ns = start["next_state"]
    assert start["exam_state"]["exam_status"] == "active"
    out = _chat(master, tmap, {"grade": 6, "mode": "exam", "session_id": "a20b",
                               "selected_oblast": "Djeljivost brojeva",
                               "previous_next_state": ns, "student_message": "ne znam"})
    assert out["exam_state"]["current_item_index"] == 0       # help never advances
    assert out["exam_state"]["items"][0]["student_answer"] is None


def test_20c_one_sheets_row_per_turn(v2, master, tmap, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(svc, "log_transcript_to_sheet",
                        lambda p, r: calls.__setitem__("n", calls["n"] + 1))
    for msg in ("daj mi zadatak", "ne znam", "1/2"):
        calls["n"] = 0
        _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a20c",
                             **EXPAND, "student_message": msg})
        assert calls["n"] == 1, msg


def test_20d_activation_decision_drives_the_response(v2, master, tmap, monkeypatch):
    seen = {}
    real = ta.activate_candidate

    def spy(candidate, **kw):
        decision = real(candidate, **kw)
        seen["decision"] = decision
        return decision

    monkeypatch.setattr(svc.task_activation, "activate_candidate", spy)
    out = _chat(master, tmap, {"grade": 6, "mode": "practice", "session_id": "a20d",
                               **EXPAND, "student_message": "daj mi zadatak"})
    decision = seen.get("decision")
    assert decision is not None and decision.activated
    assert decision.question == out["last_tutor_task"]   # response mirrors it
    assert decision.topic.npp_id == "6-04-035"
