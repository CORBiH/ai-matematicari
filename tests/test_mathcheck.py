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
    # Podrška za školsko dijeljenje ':' ne smije pretvoriti Python dict
    # sintaksu "{1:2}" u aritmetiku "(1/2)". Samostalne vitičaste zagrade s
    # dvotačkom ostaju nepodržane; obično matematičko grupisanje koristi ().
    for expr in ["__import__", "open(1)", "[1,2]", "(1,2)", "{1:2}", "a.b"]:
        with pytest.raises(Exception):
            evaluate_candidates(expr)


def test_colon_inside_recognized_latex_argument_still_works():
    """Uska code-syntax zabrana ne smije blokirati pravi LaTeX argument."""
    from matbot.mathcheck import evaluate_candidates

    assert evaluate_candidates(r"\frac{60:15}{2}") == [2.0]


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
    base = {"session_id": "sess-mathcheck", "grade": 6, "selected_topic": "6-04-007",
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
    assert fake.practice_call_count == 1
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
    assert fake.practice_call_count == 3  # bootstrap + 2 klika, bez popravnog poziva
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
    assert fake.practice_call_count == 1


# ---------------------------------------------------------------------------
# Faza A2 (docs/CURRENT_STATE.md C-4): bosansko školsko dijeljenje "a:b" —
# ranije nije bilo podržano ("60:15=5" je bio tiho preskočen, ne provjeren).
# ---------------------------------------------------------------------------

def test_case11_simple_colon_division_correct_passes():
    assert find_numeric_inconsistencies("$60:15=4$") == []


def test_case12_simple_colon_division_wrong_rejects():
    issues = find_numeric_inconsistencies("$60:15=5$")
    assert issues
    assert "60:15" in issues[0]


def test_case13_another_colon_division_correct_passes():
    assert find_numeric_inconsistencies("$72:9=8$") == []


def test_case13b_another_colon_division_wrong_rejects():
    assert find_numeric_inconsistencies("$72:9=7$")


def test_case14_decimal_comma_colon_division_correct_passes():
    assert find_numeric_inconsistencies("$3,5:0,5=7$") == []


def test_case15_decimal_comma_colon_division_wrong_rejects():
    assert find_numeric_inconsistencies("$3,5:0,5=8$")


def test_case16_prose_time_outside_math_is_ignored():
    # mathcheck SAMO ikad gleda sadržaj unutar $...$/$$...$$ (vidi
    # math_segments) — "12:30" u običnoj prozi nikad i ne ulazi u provjeru.
    assert find_numeric_inconsistencies("Sastanak je zakazan za 12:30 popodne.") == []


def test_case17_colon_in_non_math_prose_is_ignored():
    assert find_numeric_inconsistencies("Napomena: ovo je važno, provjeri dvaput.") == []


def test_case18_no_unrestricted_eval_colon_division_by_zero_is_math_error_not_crash():
    # dijeljenje nulom kroz ':' mora proći isti siguran put kao '/' — _MathError,
    # NIKAD eval() i NIKAD nekontrolisan izuzetak koji izlazi iz check_segment.
    issues = find_numeric_inconsistencies("$60:0=0$")
    assert issues
    assert "dijeljenje nulom" in issues[0]


def test_colon_division_with_parentheses():
    assert find_numeric_inconsistencies("$(24+6):5=6$") == []
    assert find_numeric_inconsistencies("$(24+6):5=5$")


def test_colon_ratio_without_equality_is_not_falsely_rejected():
    # bez relacije (=/≈) nema šta da se uporedi — čist odnos ostaje neprovjeren,
    # ne pogrešno odbijen.
    assert find_numeric_inconsistencies("$3:4$ je razmjer.") == []


def test_colon_equivalent_ratios_with_equality_checked_consistently():
    assert find_numeric_inconsistencies("$3:4=6:8$") == []
    assert find_numeric_inconsistencies("$3:4=6:9$")


# ---------------------------------------------------------------------------
# ANOTACIJA „broj: zbir njegovih cifara“ (živi gate b7025e4, lekcija 6-03-004
# „Pravila djeljivosti…“). Dvotačka je tu OZNAKA broja, ne dijeljenje: tačan
# odgovor `$12:\;1+2=3$` je bio odbijen jer je lijeva strana računata kao
# 12/1+2 = 14.
# ---------------------------------------------------------------------------

def test_live_digit_sum_annotation_is_not_read_as_division():
    """Tačan živi string koji je pao na gateu mora proći."""
    assert find_numeric_inconsistencies("$12:\\;1+2=3$") == []


@pytest.mark.parametrize("text", [
    "$12:\\;1+2=3$",
    "$135:\\;1+3+5=9$",
    "$405:\\;4+0+5=9$",
    "$10:\\;1+0=1$",            # cifra 0 među sabircima
    "$999:\\;9+9+9=27$",        # ponovljene cifre
    "$12: 1+2=3$",              # bez LaTeX razmaka
    "$12:1+2=3$",               # bez ijednog razmaka
    "$12:\\,1+2=3$",            # druga LaTeX komanda za razmak
    "$12:\\quad 1+2=3$",
])
def test_valid_digit_sum_annotation_passes(text):
    assert find_numeric_inconsistencies(text) == []


@pytest.mark.parametrize("text", [
    "$12:\\;1+2=4$",            # zbir cifara je 3, ne 4
    "$135:\\;1+3+5=10$",        # zbir cifara je 9, ne 10
    "$405:\\;4+0+5=10$",
])
def test_wrong_digit_sum_annotation_is_still_rejected(text):
    assert find_numeric_inconsistencies(text)


def test_sum_that_is_not_the_prefix_digits_stays_division():
    """`1+3` NISU cifre broja 12 → nema anotacije, dvotačka ostaje dijeljenje.

    Bez ovoga bi „obriši sve prije dvotačke“ proglasilo `12:1+3=4` tačnim samo
    zato što je 1+3=4."""
    issues = find_numeric_inconsistencies("$12:\\;1+3=4$")
    assert issues
    assert "(15)" in issues[0]   # 12/1+3 — stvarno pročitano kao dijeljenje


def test_same_digits_in_wrong_order_stay_division():
    """Redoslijed cifara je dio dokaza: `2+1` nije dekompozicija broja 12."""
    assert find_numeric_inconsistencies("$12:\\;2+1=3$")


def test_single_digit_prefix_stays_division():
    """`$5:5=1$` je ispravno dijeljenje — prag od dvije cifre ga čuva.

    Da je prag jedna cifra, cifre `[5]` bi se poklopile sa sabircima `[5]`,
    izraz bi postao zbir 5 i tačno dijeljenje bi bilo lažno odbijeno."""
    assert find_numeric_inconsistencies("$5:5=1$") == []
    assert find_numeric_inconsistencies("$5:5=2$")


@pytest.mark.parametrize("text,expected_ok", [
    ("$12:3=4$", True), ("$12:3=5$", False),
    ("$12 : 3 = 4$", True), ("$12 : 3 = 5$", False),
    ("$20:5=4$", True), ("$20:5=5$", False),
    ("$60:15=4$", True), ("$60:15=5$", False),
    ("$3,5:0,5=7$", True), ("$3,5:0,5=8$", False),
    ("$(24+6):5=6$", True), ("$(24+6):5=5$", False),
])
def test_genuine_colon_division_is_unaffected(text, expected_ok):
    assert (find_numeric_inconsistencies(text) == []) is expected_ok


def test_annotation_inside_surrounding_prose():
    text = ("Broj $12$ nije djeljiv sa $9$ jer je zbir cifara "
            "$12:\\;1+2=3$, a $3$ nije djeljivo sa $9$.")
    assert find_numeric_inconsistencies(text) == []


def test_multiple_annotated_checks_in_one_solution_all_pass():
    text = ("Provjeri zbirove cifara: $135:\\;1+3+5=9$, zatim $405:\\;4+0+5=9$ "
            "i na kraju $12:\\;1+2=3$.")
    assert find_numeric_inconsistencies(text) == []


def test_one_wrong_equality_among_several_valid_annotations_is_caught():
    text = ("Provjeri: $135:\\;1+3+5=9$, pa $405:\\;4+0+5=8$, pa $12:\\;1+2=3$.")
    issues = find_numeric_inconsistencies(text)
    assert len(issues) == 1
    assert "405" in issues[0]


@pytest.mark.parametrize("text", [
    "$12:\\\\;1+2=3$",     # ZAOSTALA dvostruka kosa crta prije komande
    "$12\\\\quad+3=15$",
    "$12:\\ty 1+2=3$",     # nepoznata kontrolna riječ
])
def test_unknown_or_doubled_backslash_still_skips_as_before(text):
    """Prepoznavanje anotacije NE SMIJE nikom drugom promijeniti put.

    Sonda za razmake radi samo unutar `_strip_digit_sum_annotation`; izraz koji
    nije anotacija stiže u `_latex_to_python` doslovno kakav je i dosad stizao,
    pa zaostala dvostruka kosa crta i nepoznata komanda ostaju „nepodržano“ i
    tiho se preskaču (a u produkciji ih `mathsafe` odbije prije ove provjere).
    Ranija verzija ove popravke je razmake skidala PRIJE poziva i time je
    `\\\\quad` postajao običan razmak — tihi gubitak zatečenog ponašanja."""
    assert find_numeric_inconsistencies(text) == []


def test_annotation_does_not_disturb_ordinary_arithmetic():
    """Ostale operacije moraju ostati tačno onakve kakve su bile."""
    for ok in ("$2+3=5$", "$7-4=3$", "$6\\cdot7=42$", "$\\frac{3}{4}=0,75$",
               "$2^3=8$", "$1,5+2,5=4$", "$-3+(-4)=-7$", "$\\sqrt{16}=4$"):
        assert find_numeric_inconsistencies(ok) == [], ok
    # `$\frac{3}{4}=0,9$`, ne `0,8`: tolerancija se izvodi iz preciznosti
    # decimalnog literala, pa je 0,8 legitimno zaokruženje broja 0,75 na jednu
    # decimalu (zatečeno ponašanje `_tolerance`, nedirnuto ovom izmjenom).
    for bad in ("$2+3=6$", "$7-4=4$", "$6\\cdot7=41$", "$\\frac{3}{4}=0,9$",
                "$2^3=9$", "$1,5+2,5=5$", "$-3+(-4)=-1$", "$\\sqrt{16}=5$"):
        assert find_numeric_inconsistencies(bad), bad
