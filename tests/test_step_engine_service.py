# -*- coding: utf-8 -*-
"""Phase 3 — Practice Step Engine wired into handle_chat (flag-gated).

Flag off (default) → legacy prose-timed hints, no step_cursor, byte-identical.
Flag on → the deterministic engine drives the 240÷6 guided flow: correct
intermediate steps do NOT complete the task or bump the whole-task streak; the
task completes only when the final step is solved; progression is independent of
tutor prose; the cursor rides forward through next_state.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    # Engine flags off unless a test opts in.
    monkeypatch.setenv("MATBOT_ENGINE_V2", "off")
    monkeypatch.setenv("MATBOT_ENGINE_V2_GRADING", "off")
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "off")
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _fake(reply="U redu."):
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=reply))])
    return chat


DIV6_TASK = "Provjeri da li je broj 240 djeljiv sa 6. Obrazloži svoj odgovor."


def _turn(master, tmap, student, prev, reply="U redu."):
    payload = {"grade": 6, "mode": "practice",
               "interaction_phase": "answering_practice_task",
               "last_tutor_task": DIV6_TASK, "student_message": student}
    if prev:
        payload["previous_next_state"] = prev
    return svc.handle_chat(payload, _fake(reply), master, tmap, model="m", timeout=1)


# --------------------------------------------------------------------------- #
# Flag off → legacy (no engine)                                               #
# --------------------------------------------------------------------------- #
def test_flag_off_no_step_cursor(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "off")
    out = _turn(master, tmap, "da je djeljiv sa 2", None)
    assert out["next_state"].get("step_cursor") is None


# --------------------------------------------------------------------------- #
# Flag on → the flagship 240÷6 guided flow                                    #
# --------------------------------------------------------------------------- #
def test_full_guided_flow_on(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")

    # Turn 1: divisible by 2 → intermediate correct.
    t1 = _turn(master, tmap, "da je djeljiv sa 2", None)
    sc1 = t1["next_state"]["step_cursor"]
    assert t1["answer_verdict"] == "partial"
    assert t1["answer_verdict_detail"] == "step_correct_step"
    assert sc1["active_step_id"] == "div3"
    assert sc1["is_complete"] is False
    assert t1["task_status"] == "active"
    assert t1["last_tutor_task"] == DIV6_TASK          # parent preserved
    assert t1["next_state"]["correct_streak"] == 0     # NO whole-task bump

    # Turn 2: digit-sum divisibility by 3 → still intermediate.
    t2 = _turn(master, tmap, "2+4+0 = 6, i 6 je djeljivo sa 3", t1["next_state"])
    sc2 = t2["next_state"]["step_cursor"]
    assert t2["answer_verdict"] == "partial"
    assert sc2["active_step_id"] == "final"
    assert sc2["is_complete"] is False
    assert t2["task_status"] == "active"
    assert t2["next_state"]["correct_streak"] == 0

    # Turn 3: final conclusion → complete.
    t3 = _turn(master, tmap, "da, djeljiv je sa 6 jer je djeljiv i sa 2 i sa 3", t2["next_state"])
    sc3 = t3["next_state"]["step_cursor"]
    assert t3["answer_verdict"] == "correct"
    assert t3["answer_verdict_detail"] == "step_final_correct"
    assert sc3["is_complete"] is True
    assert sc3["active_step_id"] is None
    assert t3["task_status"] == "completed"
    assert t3["last_tutor_task"] == ""
    assert t3["next_state"]["correct_streak"] == 1     # whole task solved → bump


def test_help_turn_preserves_cursor(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    t1 = _turn(master, tmap, "da je djeljiv sa 2", None)
    assert t1["next_state"]["step_cursor"]["active_step_id"] == "div3"
    # "ne znam" is handled by legacy help contracts; the cursor must survive.
    t2 = _turn(master, tmap, "ne znam", t1["next_state"], reply="Evo hint...")
    assert t2["next_state"]["step_cursor"]["active_step_id"] == "div3"
    assert t2["task_status"] == "active"
    # Resuming with the digit-sum answer advances to final.
    t3 = _turn(master, tmap, "6, djeljivo je sa 3", t2["next_state"])
    assert t3["next_state"]["step_cursor"]["active_step_id"] == "final"


def test_wrong_intermediate_stays_and_resets_streak(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    # Prior good streak, then a WRONG step.
    prev = {"correct_streak": 3, "step_cursor": {
        "skill_id": "divisibility_by_6", "active_step_id": "div2",
        "completed_step_ids": [], "is_complete": False}}
    out = _turn(master, tmap, "nije djeljiv sa 2", prev)
    assert out["answer_verdict"] == "incorrect"
    assert out["next_state"]["step_cursor"]["active_step_id"] == "div2"   # stays
    assert out["next_state"]["correct_streak"] == 0                        # reset
    assert out["task_status"] == "active"


def test_step_directive_injected_into_prompt(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    captured = {}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        captured["system"] = messages[0]["content"]
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="ok"))])

    svc.handle_chat({"grade": 6, "mode": "practice",
                     "interaction_phase": "answering_practice_task",
                     "last_tutor_task": DIV6_TASK, "student_message": "da je djeljiv sa 2"},
                    chat, master, tmap, model="m", timeout=1)
    assert "VOĐENJE KROZ ZADATAK" in captured["system"]
    assert "djeljiv sa 3" in captured["system"]         # next step's prompt steered


def test_unsupported_task_no_engine(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    out = svc.handle_chat({"grade": 6, "mode": "practice",
                           "interaction_phase": "answering_practice_task",
                           "last_tutor_task": "Izračunaj: 1/4 + 1/4.",
                           "student_message": "1/2"},
                          _fake(), master, tmap, model="m", timeout=1)
    assert out["next_state"].get("step_cursor") is None   # atomic → legacy path
    assert out["answer_verdict"] == "correct"             # legacy checker still works


# --------------------------------------------------------------------------- #
# New skills — multi-turn, task-id stability, streak/counter semantics         #
# --------------------------------------------------------------------------- #
def _run_flow(master, tmap, task, turns, monkeypatch):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    prev, out = None, None
    task_ids = set()
    for student in turns:
        payload = {"grade": 6, "mode": "practice",
                   "interaction_phase": "answering_practice_task",
                   "last_tutor_task": task, "student_message": student}
        if prev:
            payload["previous_next_state"] = prev
        out = svc.handle_chat(payload, _fake(), master, tmap, model="m", timeout=1)
        prev = out["next_state"]
        if out.get("task_id"):
            task_ids.add(out["task_id"])
    return out, task_ids


def test_prime_factorization_guided(monkeypatch, master, tmap):
    out, ids = _run_flow(master, tmap, "Rastavi 12 na proste faktore.",
                         ["2", "2", "3", "2*2*3"], monkeypatch)
    assert out["answer_verdict"] == "correct"
    assert out["next_state"]["step_cursor"]["is_complete"] is True
    assert out["task_status"] == "completed"
    assert out["next_state"]["correct_streak"] == 1        # only after final
    assert len(ids) == 1                                    # SAME task_id throughout


def test_linear_equation_guided(monkeypatch, master, tmap):
    out, ids = _run_flow(master, tmap, "Riješi jednačinu 2x + 3 = 11.",
                         ["8", "x=4"], monkeypatch)
    assert out["answer_verdict"] == "correct"
    assert out["next_state"]["correct_streak"] == 1
    assert len(ids) == 1


def test_fraction_guided(monkeypatch, master, tmap):
    out, ids = _run_flow(master, tmap, "Izračunaj: 1/2 + 1/3.",
                         ["6", "5/6"], monkeypatch)
    assert out["answer_verdict"] == "correct"
    assert out["next_state"]["correct_streak"] == 1
    assert len(ids) == 1


def test_wrong_final_value_not_silently_accepted(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    t1 = _turn_for(master, tmap, "Izračunaj: 1/2 + 1/3.", "6", None)
    # correct common denom, then a WRONG final value (5/6 is correct, 7/6 is not).
    t2 = _turn_for(master, tmap, "Izračunaj: 1/2 + 1/3.", "7/6", t1["next_state"])
    assert t2["answer_verdict"] == "incorrect"
    assert t2["next_state"]["step_cursor"]["active_step_id"] == "final"   # stays
    assert t2["task_status"] == "active"                                   # not completed


# --------------------------------------------------------------------------- #
# Help refinement — cursor preserved, hint only, no wrong-count, no complete   #
# --------------------------------------------------------------------------- #
def test_help_preserves_cursor_and_hints(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    captured = {}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        captured["system"] = messages[0]["content"]
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="Evo mali savjet."))])

    out = svc.handle_chat({"grade": 6, "mode": "practice",
                           "interaction_phase": "answering_practice_task",
                           "last_tutor_task": "Riješi jednačinu 2x + 3 = 11.",
                           "student_message": "ne znam"},
                          chat, master, tmap, model="m", timeout=1)
    sc = out["next_state"]["step_cursor"]
    assert sc["active_step_id"] == "isolate"               # cursor preserved
    assert sc["is_complete"] is False                       # NOT completed
    assert out["answer_verdict"] is None                    # help is not a verdict
    assert out["wrong_attempt_count"] == 0                  # help never counts wrong
    assert out["hint_count"] == 1
    assert out["task_status"] == "active"
    # Directive is the HELP variant, steering a hint for the CURRENT step only.
    assert "VOĐENJE KROZ ZADATAK" in captured["system"]
    assert "SAMO JEDAN mali hint" in captured["system"]
    # The current step's prompt is present; the later 'final' step's prompt is not.
    assert "Koliko je onda 2x" in captured["system"]           # isolate step prompt
    assert "podijeli obje strane sa 2" not in captured["system"].lower()   # final step


def test_help_then_resume(monkeypatch, master, tmap):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "on")
    t1 = _turn_for(master, tmap, "Riješi jednačinu 2x + 3 = 11.", "ne znam", None)
    assert t1["next_state"]["step_cursor"]["active_step_id"] == "isolate"
    t2 = _turn_for(master, tmap, "Riješi jednačinu 2x + 3 = 11.", "8", t1["next_state"])
    assert t2["answer_verdict"] == "partial"
    assert t2["next_state"]["step_cursor"]["active_step_id"] == "final"


# --------------------------------------------------------------------------- #
# Flag-off parity for the new skills                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("task,student", [
    ("Rastavi 12 na proste faktore.", "2"),
    ("Riješi jednačinu 2x + 3 = 11.", "8"),
    ("Izračunaj: 1/2 + 1/3.", "6"),
])
def test_flag_off_no_engine_new_skills(monkeypatch, master, tmap, task, student):
    monkeypatch.setenv("MATBOT_ENGINE_V2_PRACTICE", "off")
    out = svc.handle_chat({"grade": 6, "mode": "practice",
                           "interaction_phase": "answering_practice_task",
                           "last_tutor_task": task, "student_message": student},
                          _fake(), master, tmap, model="m", timeout=1)
    assert out["next_state"].get("step_cursor") is None


def _turn_for(master, tmap, task, student, prev, reply="U redu."):
    payload = {"grade": 6, "mode": "practice",
               "interaction_phase": "answering_practice_task",
               "last_tutor_task": task, "student_message": student}
    if prev:
        payload["previous_next_state"] = prev
    return svc.handle_chat(payload, _fake(reply), master, tmap, model="m", timeout=1)
