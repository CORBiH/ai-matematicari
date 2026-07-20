# -*- coding: utf-8 -*-
"""Phase 3 — SolutionPlan / StepCursor engine (pure, deterministic).

The flagship acceptance is the 240÷6 multi-turn flow: it must advance
div2 → div3 → final → complete deterministically, regardless of tutor prose, and
a correct intermediate step must NOT complete the task.
"""
import pytest

from matbot import solution_plan as sp


# --------------------------------------------------------------------------- #
# Plan detection                                                              #
# --------------------------------------------------------------------------- #
def test_build_plan_for_divisibility_by_6():
    plan = sp.build_plan_for_task(
        "Provjeri da li je broj 240 djeljiv sa 6. Obrazloži svoj odgovor.")
    assert plan is not None
    assert plan.skill_id == "divisibility_by_6"
    assert [s.id for s in plan.steps] == ["div2", "div3", "final"]
    assert plan.step("div2").params == {"n": 240, "k": 2}


def test_no_plan_without_explanation_request():
    # No "obrazloži"/"objasni" → not a guided multi-step task.
    assert sp.build_plan_for_task("Je li 240 djeljiv sa 6?") is None


def test_no_plan_for_unsupported_task():
    assert sp.build_plan_for_task("Nacrtaj trougao i izmjeri uglove.") is None


def test_no_plan_for_atomic_tasks():
    # Atomic: same-denominator add, multiplication, a=1 equation, prime number.
    assert sp.build_plan_for_task("Izračunaj: 1/4 + 1/4.") is None
    assert sp.build_plan_for_task("Izračunaj: 1/2 · 5/9.") is None
    assert sp.build_plan_for_task("Riješi: x + 3 = 7.") is None
    assert sp.build_plan_for_task("Rastavi 7 na proste faktore.") is None


# --------------------------------------------------------------------------- #
# Per-step checks                                                             #
# --------------------------------------------------------------------------- #
def test_check_div2_step():
    plan = sp.build_plan_for_task("Je li 240 djeljiv sa 6? Obrazloži.")
    div2 = plan.step("div2")
    assert sp.check_step(div2, "da je djeljiv sa 2") == sp.CORRECT_STEP
    assert sp.check_step(div2, "nije djeljiv sa 2") == sp.WRONG_STEP
    assert sp.check_step(div2, "ne znam") == sp.HELP
    assert sp.check_step(div2, "možda") == sp.UNCLEAR


def test_check_digit_sum_step_accepts_value_or_yesno():
    plan = sp.build_plan_for_task("Je li 240 djeljiv sa 6? Obrazloži.")
    div3 = plan.step("div3")
    assert sp.check_step(div3, "da") == sp.CORRECT_STEP
    assert sp.check_step(div3, "2+4+0 = 6, jeste") == sp.CORRECT_STEP
    assert sp.check_step(div3, "6") == sp.CORRECT_STEP        # correct digit sum, divisible by 3


def test_check_digit_sum_step_wrong_when_negating_truth():
    # digit sum 6 IS divisible by 3; claiming "nije" is wrong.
    plan = sp.build_plan_for_task("Je li 240 djeljiv sa 6? Obrazloži.")
    assert sp.check_step(plan.step("div3"), "nije") == sp.WRONG_STEP


# --------------------------------------------------------------------------- #
# THE FLAGSHIP: 240 ÷ 6 multi-turn cursor advancement (deterministic)         #
# --------------------------------------------------------------------------- #
def test_240_div_6_full_guided_flow():
    task = "Provjeri da li je broj 240 djeljiv sa 6. Obrazloži svoj odgovor."
    plan, cursor = sp.cursor_for_task(task)
    assert cursor.active_step_id == "div2"
    assert cursor.refers_to(plan) == "substep"

    # Turn 1: student confirms divisibility by 2 → advance to div3, NOT complete.
    c1 = sp.classify_turn(plan, cursor, "da je djeljiv sa 2")
    assert c1 == sp.CORRECT_STEP
    cursor = sp.advance(plan, cursor, c1)
    assert cursor.active_step_id == "div3"
    assert cursor.completed_step_ids == ["div2"]
    assert cursor.is_complete is False        # correct intermediate must NOT finish the task

    # Turn 2: student handles divisibility by 3 → advance to final.
    c2 = sp.classify_turn(plan, cursor, "2+4+0 = 6, i 6 je djeljivo sa 3")
    assert c2 == sp.CORRECT_STEP
    cursor = sp.advance(plan, cursor, c2)
    assert cursor.active_step_id == "final"
    assert cursor.completed_step_ids == ["div2", "div3"]
    assert cursor.is_complete is False
    assert cursor.refers_to(plan) == "whole_task"

    # Turn 3: student states the final conclusion → complete.
    c3 = sp.classify_turn(plan, cursor, "da, djeljiv je sa 6 jer je djeljiv i sa 2 i sa 3")
    assert c3 == sp.FINAL_CORRECT
    cursor = sp.advance(plan, cursor, c3)
    assert cursor.is_complete is True
    assert cursor.active_step_id is None
    assert cursor.completed_step_ids == ["div2", "div3", "final"]


def test_wrong_intermediate_does_not_advance():
    task = "Je li 240 djeljiv sa 6? Obrazloži."
    plan, cursor = sp.cursor_for_task(task)
    c = sp.classify_turn(plan, cursor, "nije djeljiv sa 2")   # false claim
    assert c == sp.WRONG_STEP
    cursor2 = sp.advance(plan, cursor, c)
    assert cursor2.active_step_id == "div2"                   # stays put
    assert cursor2.completed_step_ids == []


def test_help_on_step_does_not_advance():
    task = "Je li 240 djeljiv sa 6? Obrazloži."
    plan, cursor = sp.cursor_for_task(task)
    c = sp.classify_turn(plan, cursor, "ne znam kako")
    assert c == sp.HELP
    assert sp.advance(plan, cursor, c).active_step_id == "div2"


def test_prose_independent_progression():
    """Progression depends ONLY on the student message + step params — never on
    any tutor prose (there is no prose input to the engine)."""
    task = "Je li 240 djeljiv sa 6? Obrazloži."
    plan, cursor = sp.cursor_for_task(task)
    cursor = sp.advance(plan, cursor, sp.classify_turn(plan, cursor, "da"))
    assert cursor.active_step_id == "div3"        # advanced on a bare "da", no prose needed


# --------------------------------------------------------------------------- #
# Serialization / round-trip                                                  #
# --------------------------------------------------------------------------- #
def test_cursor_roundtrip():
    task = "Je li 240 djeljiv sa 6? Obrazloži."
    plan, cursor = sp.cursor_for_task(task)
    cursor = sp.advance(plan, cursor, sp.CORRECT_STEP)
    d = cursor.to_dict()
    back = sp.normalize_cursor(d)
    assert back.skill_id == "divisibility_by_6"
    assert back.active_step_id == cursor.active_step_id
    assert back.completed_step_ids == cursor.completed_step_ids


def test_cursor_for_task_reuses_prior_same_skill():
    task = "Je li 240 djeljiv sa 6? Obrazloži."
    prior = {"skill_id": "divisibility_by_6", "active_step_id": "div3",
             "completed_step_ids": ["div2"], "is_complete": False}
    plan, cursor = sp.cursor_for_task(task, prior)
    assert cursor.active_step_id == "div3"
    assert cursor.completed_step_ids == ["div2"]


def test_cursor_for_task_resets_on_skill_mismatch():
    task = "Je li 240 djeljiv sa 6? Obrazloži."
    prior = {"skill_id": "something_else", "active_step_id": "x"}
    plan, cursor = sp.cursor_for_task(task, prior)
    assert cursor.active_step_id == "div2"        # fresh start


def test_normalize_cursor_rejects_junk():
    assert sp.normalize_cursor(None) is None
    assert sp.normalize_cursor("x") is None
    assert sp.normalize_cursor({}) is None


# --------------------------------------------------------------------------- #
# Prime factorization                                                         #
# --------------------------------------------------------------------------- #
def test_prime_factorization_plan_ladder():
    plan = sp.build_plan_for_task("Rastavi 60 na proste faktore.")
    assert plan.skill_id == "prime_factorization"
    assert [s.id for s in plan.steps] == ["p1", "p2", "p3", "p4", "final"]
    assert plan.step("p1").params["values"] == [2, 30]     # prime or quotient
    assert plan.step("final").kind == "final_delegate"


def test_prime_factorization_full_flow():
    plan, cur = sp.cursor_for_task("Rastavi 12 na proste faktore.")  # 2·2·3
    for ans, nxt in [("2", "p2"), ("2", "p3"), ("3", "final")]:
        c = sp.classify_turn(plan, cur, ans)
        assert c == sp.CORRECT_STEP
        cur = sp.advance(plan, cur, c)
        assert cur.active_step_id == nxt
    c = sp.classify_turn(plan, cur, "2*2*3")
    assert c == sp.FINAL_CORRECT
    assert sp.advance(plan, cur, c).is_complete is True


def test_prime_wrong_factor_rejected():
    plan, cur = sp.cursor_for_task("Rastavi 12 na proste faktore.")
    assert sp.classify_turn(plan, cur, "5") == sp.WRONG_STEP   # 12 not divisible by 5


# --------------------------------------------------------------------------- #
# Linear equations                                                            #
# --------------------------------------------------------------------------- #
def test_linear_equation_plan_two_steps():
    plan = sp.build_plan_for_task("Riješi jednačinu 2x + 3 = 11.")
    assert plan.skill_id == "linear_equation"
    assert [s.id for s in plan.steps] == ["isolate", "final"]
    assert plan.step("isolate").params["values"] == [8]       # 2x = 11 - 3


def test_linear_equation_full_flow():
    plan, cur = sp.cursor_for_task("Riješi jednačinu 2x + 3 = 11.")
    c = sp.classify_turn(plan, cur, "8"); cur = sp.advance(plan, cur, c)
    assert c == sp.CORRECT_STEP and cur.active_step_id == "final"
    c = sp.classify_turn(plan, cur, "x=4")
    assert c == sp.FINAL_CORRECT


def test_linear_wrong_final_not_accepted():
    plan, cur = sp.cursor_for_task("Riješi jednačinu 2x + 3 = 11.")
    cur = sp.advance(plan, cur, sp.CORRECT_STEP)          # at final
    assert sp.classify_turn(plan, cur, "x=5") == sp.FINAL_WRONG


def test_linear_atomic_no_plan():
    assert sp.build_plan_for_task("Riješi: x + 3 = 7.") is None      # a=1
    assert sp.build_plan_for_task("Riješi: 2x = 8.") is None         # b=0


# --------------------------------------------------------------------------- #
# Fraction add/sub                                                            #
# --------------------------------------------------------------------------- #
def test_fraction_plan_unlike_denominators():
    plan = sp.build_plan_for_task("Izračunaj: 1/2 + 1/3.")
    assert plan.skill_id == "fraction_add_sub"
    assert [s.id for s in plan.steps] == ["common_denom", "final"]
    assert plan.step("common_denom").params["values"] == [6]


def test_fraction_full_flow():
    plan, cur = sp.cursor_for_task("Izračunaj: 1/2 + 1/3.")
    c = sp.classify_turn(plan, cur, "zajednički je 6"); cur = sp.advance(plan, cur, c)
    assert c == sp.CORRECT_STEP and cur.active_step_id == "final"
    assert sp.classify_turn(plan, cur, "5/6") == sp.FINAL_CORRECT


def test_fraction_wrong_final_value_rejected():
    plan, cur = sp.cursor_for_task("Izračunaj: 1/2 + 1/3.")
    cur = sp.advance(plan, cur, sp.CORRECT_STEP)          # at final
    # 1/2 + 1/3 = 5/6; "7/6" is wrong even though the student "finished".
    assert sp.classify_turn(plan, cur, "7/6") == sp.FINAL_WRONG


def test_fraction_same_denominator_atomic_no_plan():
    assert sp.build_plan_for_task("Izračunaj: 1/4 + 1/4.") is None
    assert sp.build_plan_for_task("Izračunaj: 1/2 · 5/9.") is None   # multiplication


# --------------------------------------------------------------------------- #
# Help handling (engine level)                                                #
# --------------------------------------------------------------------------- #
def test_help_variants_classified_as_help():
    plan, cur = sp.cursor_for_task("Riješi jednačinu 2x + 3 = 11.")
    for msg in ("ne znam", "pomozi", "daj hint", "ne razumijem", "kako da počnem"):
        assert sp.classify_turn(plan, cur, msg) == sp.HELP


def test_active_hint_targets_current_step_only():
    plan, cur = sp.cursor_for_task("Rastavi 60 na proste faktore.")
    assert sp.active_hint(plan, cur)                       # p1 has a hint
    # advancing to p2 changes the hint target — later steps never leak early
    cur = sp.advance(plan, cur, sp.CORRECT_STEP)
    assert sp.active_prompt(plan, cur) == plan.step("p2").prompt
