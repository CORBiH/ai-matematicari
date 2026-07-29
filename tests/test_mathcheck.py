"""Finding B: deterministička provjera numeričke dosljednosti (matbot/mathcheck.py).

Živi nalaz (Explain, „Pravilni mnogougao“): ispravna formula, pogrešan lanac —
$P=\\frac{3\\cdot16\\sqrt{3}}{2}=48\\sqrt{3}\\approx83,14$ umjesto $24\\sqrt{3}\\approx41,57$.
"""
import json

import pytest

from matbot import config
from matbot.mathcheck import (
    find_numeric_inconsistencies, is_numerically_consistent, math_segments,
)
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.quick import run_quick_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_explain_output, make_options, make_output,
                             make_quick_output, make_task, make_task_for_family)


def rejects(text):
    return bool(find_numeric_inconsistencies(text))


# ---------------------------------------------------------------------------
# Exact live failure / correction
# ---------------------------------------------------------------------------

LIVE_WRONG = "$P=\\frac{3\\cdot16\\sqrt{3}}{2}=48\\sqrt{3}\\approx83,14\\,\\text{cm}^2$"
LIVE_RIGHT = "$P=\\frac{3\\cdot16\\sqrt{3}}{2}=24\\sqrt{3}\\approx41,57\\,\\text{cm}^2$"


def test_exact_live_wrong_equality_chain_is_rejected():
    issues = find_numeric_inconsistencies(LIVE_WRONG)
    assert issues
    assert "numeric_equality_mismatch" in issues[0]


def test_exact_live_corrected_chain_is_accepted():
    assert not find_numeric_inconsistencies(LIVE_RIGHT)


# ---------------------------------------------------------------------------
# 1-11. Required numeric cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$3\\cdot16/2=24$",
    "$\\sqrt{100}=10$",
    "$\\frac{75}{3}=25$",
    "$4\\pi\\cdot9=36\\pi$",
    "$24\\sqrt3\\approx41,57$",
    "$24\\sqrt3\\approx41,6$",
])
def test_valid_numeric_chains_are_accepted(text):
    assert not rejects(text), text


@pytest.mark.parametrize("text", [
    "$3\\cdot16/2=48$",
    "$\\sqrt{100}=20$",
    "$\\frac{75}{3}=15$",
    "$4\\pi\\cdot9=18\\pi$",
    "$24\\sqrt3\\approx83,14$",
])
def test_invalid_numeric_chains_are_rejected(text):
    assert rejects(text), text


# ---------------------------------------------------------------------------
# 12-13. Decimal comma and units
# ---------------------------------------------------------------------------

def test_decimal_comma_is_handled():
    assert not rejects("$2,5\\cdot4=10$")
    assert rejects("$2,5\\cdot4=12$")


def test_units_are_ignored_for_computation():
    assert not rejects("$P=\\frac{10\\cdot 6}{2}=30\\,\\text{cm}^2$")
    assert not rejects("$V=\\frac{25\\cdot9}{3}=75\\,\\text{cm}^3$")
    assert rejects("$V=\\frac{25\\cdot9}{3}=70\\,\\text{cm}^3$")


def test_units_remain_byte_identical_in_output():
    """Checker NIKAD ne mijenja tekst — samo prijavljuje."""
    text = "$P=30\\,\\text{cm}^2$"
    find_numeric_inconsistencies(text)
    assert text == "$P=30\\,\\text{cm}^2$"


# ---------------------------------------------------------------------------
# 14-16. Safe skipping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$c^2=a^2+b^2$",
    "$P=\\frac{3a^2\\sqrt3}{2}$",
    "$x+7=15$",
    "$P=2\\pi r(r+H)$",
    "$M=O_BH$",
    "$V=\\frac{BH}{3}$",
    "$(x,y)=(3,2)$",
    "$\\log(100)=2$",
    "$35^\\circ+55^\\circ=90^\\circ$",
])
def test_symbolic_or_unsupported_expressions_are_skipped(text):
    assert not rejects(text), text


def test_skipping_is_not_proof_of_correctness():
    """Dokumentuje politiku: preskočen izraz vraća praznu listu, isto kao
    ispravan — checker je čuvar dosljednosti, ne dokazivač."""
    assert find_numeric_inconsistencies("$c^2=a^2+b^2$") == []
    assert find_numeric_inconsistencies("$2+2=4$") == []


# ---------------------------------------------------------------------------
# 17. Division by zero / invalid root
# ---------------------------------------------------------------------------

def test_division_by_zero_is_rejected():
    assert rejects("$\\frac{5}{0}=1$")


def test_invalid_square_root_is_rejected():
    assert rejects("$\\sqrt{-4}=2$")


# ---------------------------------------------------------------------------
# 18. No arbitrary code execution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "$__import__('os').system('echo hi')=1$",
    "$(1).__class__=2$",
    "$open('x')=1$",
    "$[1,2][0]=1$",
    "$lambda:1=1$",
    "$9**9**9=1$",
])
def test_no_arbitrary_code_execution(payload):
    """Sve mora biti ili sigurno preskočeno ili odbijeno — nikad izvršeno."""
    find_numeric_inconsistencies(payload)  # ne smije baciti niti išta izvršiti


def test_evaluator_rejects_non_whitelisted_ast_nodes():
    from matbot.mathcheck import evaluate_candidates, _Unsupported
    for expr in ["__import__", "open(1)", "[1,2]", "{1:2}", "a.b"]:
        with pytest.raises(Exception):
            evaluate_candidates(expr)


# ---------------------------------------------------------------------------
# School π convention (live-verified: model computes with 3,14)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "$B=\\pi\\cdot3^2=9\\pi\\approx28,26\\,\\text{cm}^2$",
    "$M=\\pi\\cdot3\\cdot5=15\\pi\\approx47,10\\,\\text{cm}^2$",
    "$P=\\pi\\cdot3(3+5)=24\\pi\\approx75,36\\,\\text{cm}^2$",
    "$P=4\\pi\\cdot6^2=452,16\\,\\text{cm}^2$",
])
def test_school_pi_314_approximations_are_accepted(text):
    assert not rejects(text), text


# ---------------------------------------------------------------------------
# Mixed numbers (regression: 1\frac{3}{20} is 1+3/20, not 1*3/20)
# ---------------------------------------------------------------------------

def test_mixed_number_is_addition_not_multiplication():
    assert not rejects("$\\frac{23}{20}=1\\frac{3}{20}$")
    assert not rejects("$2\\frac{1}{3}=\\frac{7}{3}$")
    assert rejects("$\\frac{23}{20}=1\\frac{5}{20}$")


def test_sqrt_after_digit_is_still_multiplication():
    assert not rejects("$24\\sqrt{3}\\approx41,57$")


# ---------------------------------------------------------------------------
# Chains and segments
# ---------------------------------------------------------------------------

def test_long_valid_chain_is_accepted():
    assert not rejects("$s=\\sqrt{3^2+4^2}=\\sqrt{9+16}=\\sqrt{25}=5\\,\\text{cm}$")


def test_rational_exact_mismatch_is_rejected():
    assert rejects("$7/2=3$")


def test_rounded_equality_with_irrational_is_tolerated():
    assert not rejects("$\\sqrt{2}=1,41$")


def test_math_segments_extraction():
    assert math_segments("Tekst $1+1=2$ i $3+3=6$.") == ["1+1=2", "3+3=6"]
    assert math_segments("Bez matematike.") == []


def test_is_numerically_consistent_helper():
    assert is_numerically_consistent("$2+2=4$")
    assert not is_numerically_consistent("$2+2=5$")


# ---------------------------------------------------------------------------
# 19-22. Integration through the three modes
# ---------------------------------------------------------------------------

def _explain_turn(grade=8, topic="8-08-005"):
    return {"grade": grade, "selected_topic": topic, "selected_oblast": "",
            "student_message": "Objasni mi.", "interaction_phase": "",
            "conversation_history": [], "last_tutor_message": ""}


def _quick_turn(grade=8, topic="8-08-005"):
    return {"grade": grade, "selected_topic": topic, "selected_oblast": "",
            "student_message": "Izračunaj.", "interaction_phase": "",
            "conversation_history": []}


def test_explain_exact_live_wrong_response_returns_safe_message():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply=f"Primjer: {LIVE_WRONG}"))
    r = run_explain_turn(fake, _explain_turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert fake.call_count == 1  # bez popravnog poziva


def test_explain_corrected_response_is_accepted_unchanged():
    fake = FakeLLM()
    reply = f"Primjer: {LIVE_RIGHT}"
    fake.queue(make_explain_output(reply=reply))
    r = run_explain_turn(fake, _explain_turn())
    assert r["answer"] == reply
    assert fake.call_count == 1


def test_quick_inconsistent_equality_is_rejected():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$3\\cdot16/2=48$"))
    r = run_quick_turn(fake, _quick_turn())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1


def test_quick_consistent_equality_is_accepted():
    fake = FakeLLM()
    reply = "$3\\cdot16/2=24$"
    fake.queue(make_quick_output(reply=reply))
    r = run_quick_turn(fake, _quick_turn())
    assert r["answer"] == reply


def _practice_payload(msg="Daj zadatak.", **kw):
    base = {"session_id": "sess-mathcheck", "grade": 6, "selected_topic": "6-04-005",
            "selected_oblast": "", "student_message": msg, "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "", "selected_option_id": "", "client_turn_id": ""}
    base.update(kw)
    return base


def test_practice_task_with_inconsistent_arithmetic_is_rejected_without_state_change():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$. Provjeri: $3\\cdot3=12$.",
        expected="$\\frac{9}{24}$",
        options=make_options("$\\frac{9}{24}$", "$\\frac{3}{24}$",
                              "$\\frac{9}{8}$", "$\\frac{6}{24}$"))))
    before = store.peek("sess-mathcheck")
    r = run_practice_turn(store, fake, _practice_payload())
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1
    assert store.peek("sess-mathcheck") == before  # oba None — ništa spremljeno


def test_practice_reveal_with_inconsistent_arithmetic_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.",
                            new_task=make_task_for_family("expand_to_given_denominator")))
    run_practice_turn(store, fake, _practice_payload())
    sess = store.peek("sess-mathcheck")
    wrong = next(o["id"] for o in sess["current_options"] if o["id"] != sess["correct_option_id"])

    fake.queue(make_output(reply="x", hint="Prvi hint."))
    run_practice_turn(store, fake, _practice_payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=wrong, client_turn_id="t1"))

    sess2 = store.peek("sess-mathcheck")
    second_wrong = next(o["id"] for o in sess2["current_options"]
                        if o["id"] not in (sess2["correct_option_id"], wrong))
    before = store.peek("sess-mathcheck")
    fake.queue(make_output(reply="Postupak: $3\\cdot3=12$, dakle rezultat."))
    r = run_practice_turn(store, fake, _practice_payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=second_wrong, client_turn_id="t2"))

    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 3  # bootstrap + 2 klika, bez popravnog poziva
    after = store.peek("sess-mathcheck")
    assert after["wrong_option_ids"] == before["wrong_option_ids"]
    assert after["task_completed"] == before["task_completed"]


def test_error_detection_family_may_show_wrong_arithmetic_on_purpose():
    """„Učenik je napisao $\\frac{1}{2}+\\frac{1}{3}=\\frac{2}{5}$. Šta je
    pogriješio?“ — netačna jednakost je SVRHA zadatka i ne smije se odbiti."""
    from matbot.mathcheck import find_numeric_inconsistencies as f
    from matbot.task_family_validation import question_numeric_policy
    task = make_task_for_family("detect_student_error")
    assert f(task.text), "test-template mora sadržavati namjerno pogrešnu jednakost"

    assert question_numeric_policy("detect_student_error") == "allow_intentional_mismatch"
    assert question_numeric_policy("detect_formula_error") == "allow_intentional_mismatch"


def test_practice_valid_task_still_accepted_after_integration():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.",
                            new_task=make_task_for_family("expand_to_given_denominator")))
    r = run_practice_turn(store, fake, _practice_payload())
    assert r["status"] == "ready"
    assert fake.call_count == 1
