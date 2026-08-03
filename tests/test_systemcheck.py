# -*- coding: utf-8 -*-
"""Deterministička supstitucijska provjera sistema (matbot/systemcheck.py).

ŽIVI NALAZ (poziv 8 fokusiranog testa, lekcija „Provjera uređenog para u
sistemu“): model je prikazao sistem $2x+4y=10$, $3x-y=2$, a kao tačan označio
par $(13/7, 11/7)$ — koji rješava sistem $2x+4y=10$, $3x-y=4$. Tačno rješenje
$(9/7, 13/7)$ nije bilo ni ponuđeno; NIJEDNA opcija ne zadovoljava obje
prikazane jednačine. `expected_answer` je ponovio istu grešku, pa slaganje
označene opcije i expected_answer NIJE dokaz.
"""
from fractions import Fraction as F

import pytest

from tests.conftest import FakeLLM, make_options, make_output, make_task
from matbot import systemcheck as sc
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore

LIVE_Q = 'Riješi sistem: $2x+4y=10$ \n$3x-y=2$'
LIVE_OPTS = [
    r'$\left(\frac{13}{7},\frac{11}{7}\right)$',
    r'$\left(\frac{13}{7},-\frac{11}{7}\right)$',
    r'$\left(-\frac{13}{7},\frac{11}{7}\right)$',
    r'$(3,1)$',
]
LIVE_EXPECTED = (r'Riješavanjem metoda supstitucije/elim.: $x=\frac{13}{7},\;y=\frac{11}{7}$; '
                 r'uređeni par $\left(\frac{13}{7},\frac{11}{7}\right)$')
TRUE_PAIR = r'$\left(\frac{9}{7},\frac{13}{7}\right)$'

SIMPLE_Q = 'Riješi sistem: $x+y=7$ i $x-y=1$.'
SIMPLE_OPTS = [r'$(4,3)$', r'$(3,4)$', r'$(5,2)$', r'$(2,5)$']   # (4,3) is the solution


def turn(topic="9-05-004", grade=9, sid="sys-sess"):
    return {"session_id": sid, "grade": grade, "selected_topic": topic,
            "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": ""}


def run_task(store, fake, question, opts, correct_index, expected, sid="sys-sess"):
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text=question, expected=expected, options=make_options(*opts),
        correct_option_index=correct_index, task_family="solve_system",
        answer_kind="ordered_pair", student_must_find="ordered_pair",
        task_form="direct_calculation")))
    return run_practice_turn(store, fake, turn(sid=sid))


# ---------------------------------------------------------------------------
# 1-8: tačna živa regresija
# ---------------------------------------------------------------------------

def test_1_parses_the_two_live_equations():
    eqs = sc.parse_system(LIVE_Q)
    assert eqs == ((F(2), F(4), F(10)), (F(3), F(-1), F(2)))


def test_2_parses_all_four_live_options():
    pairs = [sc.parse_ordered_pair(o) for o in LIVE_OPTS]
    assert pairs == [(F(13, 7), F(11, 7)), (F(13, 7), F(-11, 7)),
                     (F(-13, 7), F(11, 7)), (F(3), F(1))]


def test_3_substitution_truth_table_for_live_task():
    eqs = sc.parse_system(LIVE_Q)
    truth = [[sc.satisfies(e, sc.parse_ordered_pair(o)) for e in eqs] for o in LIVE_OPTS]
    assert truth[0] == [True, False]      # marked correct — first equation only
    assert truth[3] == [True, False]      # (3,1) — first equation only
    assert truth[1] == [False, False]
    assert truth[2] == [False, False]
    assert not any(all(t) for t in truth)


def test_4_and_5_live_task_rejected_despite_expected_answer_agreement():
    r = sc.verify_solve_system(LIVE_Q, LIVE_OPTS, 0, LIVE_EXPECTED)
    assert r.status == sc.STATUS_INVALID
    assert sc.NO_CORRECT_OPTION in r.issue_codes
    assert r.valid_option_indices == ()
    # expected_answer agrees with the marked option, and that must not rescue it
    assert sc.parse_ordered_pair(LIVE_EXPECTED) == (F(13, 7), F(11, 7))


def test_6_7_8_live_task_blocked_end_to_end_without_state_change():
    store, fake = SessionStore(), FakeLLM()
    before = store.peek("sys-sess")
    r = run_task(store, fake, LIVE_Q, LIVE_OPTS, 0, LIVE_EXPECTED)
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r and "next_state" not in r          # 6: nothing browser-visible
    assert store.peek("sys-sess") == before                     # 7: no state mutation
    assert fake.practice_call_count == 1                                 # 8: exactly one LLM call


def test_true_solution_verifies_against_the_live_system():
    eqs = sc.parse_system(LIVE_Q)
    pair = sc.parse_ordered_pair(TRUE_PAIR)
    assert pair == (F(9, 7), F(13, 7))
    assert all(sc.satisfies(e, pair) for e in eqs)


# ---------------------------------------------------------------------------
# 9-17: ispravni sistemi
# ---------------------------------------------------------------------------

def test_9_accepts_correct_system():
    r = sc.verify_solve_system(SIMPLE_Q, SIMPLE_OPTS, 0, r'$(4,3)$')
    assert r.status == sc.STATUS_VERIFIED
    assert r.valid_option_indices == (0,)


def test_9b_accepts_correct_system_end_to_end():
    store, fake = SessionStore(), FakeLLM()
    r = run_task(store, fake, SIMPLE_Q, SIMPLE_OPTS, 0, r'$(4,3)$')
    assert r["status"] == "ready", r["answer"]
    assert fake.practice_call_count == 1


def test_10_rejects_when_true_pair_present_but_wrong_option_marked():
    r = sc.verify_solve_system(SIMPLE_Q, SIMPLE_OPTS, 1, r'$(3,4)$')
    assert r.status == sc.STATUS_INVALID
    assert sc.MARKED_OPTION_MISMATCH in r.issue_codes
    assert r.valid_option_indices == (0,)


def test_11_rejects_when_no_option_is_correct():
    r = sc.verify_solve_system(SIMPLE_Q, [r'$(1,1)$', r'$(2,2)$', r'$(3,3)$', r'$(5,5)$'], 0)
    assert r.status == sc.STATUS_INVALID
    assert sc.NO_CORRECT_OPTION in r.issue_codes


def test_12_rejects_two_options_representing_the_same_correct_pair():
    r = sc.verify_solve_system(SIMPLE_Q, [r'$(4,3)$', r'$\left(4,3\right)$', r'$(5,2)$', r'$(2,5)$'], 0)
    assert r.status == sc.STATUS_INVALID
    assert sc.MULTIPLE_CORRECT_OPTIONS in r.issue_codes
    assert r.valid_option_indices == (0, 1)


def test_13_accepts_negative_solutions():
    q = 'Riješi sistem: $x+y=-5$ i $x-y=1$.'          # (-2,-3)
    r = sc.verify_solve_system(q, [r'$(-2,-3)$', r'$(2,3)$', r'$(-3,-2)$', r'$(0,-5)$'], 0)
    assert r.status == sc.STATUS_VERIFIED


def test_14_accepts_fractional_solutions():
    q = 'Riješi sistem: $2x+4y=10$ i $3x-y=2$.'        # (9/7,13/7)
    r = sc.verify_solve_system(q, [TRUE_PAIR, LIVE_OPTS[0], LIVE_OPTS[1], r'$(3,1)$'], 0)
    assert r.status == sc.STATUS_VERIFIED
    assert r.valid_option_indices == (0,)


def test_15_accepts_decimal_coefficients_exactly():
    q = 'Riješi sistem: $0,5x-y=2$ i $x+y=7$.'         # x=6, y=1
    assert sc.parse_system(q) == ((F(1, 2), F(-1), F(2)), (F(1), F(1), F(7)))
    r = sc.verify_solve_system(q, [r'$(6,1)$', r'$(1,6)$', r'$(5,2)$', r'$(2,5)$'], 0)
    assert r.status == sc.STATUS_VERIFIED


def test_16_accepts_reordered_terms():
    assert sc.parse_system('$3y+2x=7$ i $-2x+3y=5$') == \
        ((F(2), F(3), F(7)), (F(-2), F(3), F(5)))


def test_17_accepts_safely_expandable_parentheses():
    assert sc.parse_system('$2(x+y)=10$ i $x-y=1$') == \
        ((F(2), F(2), F(10)), (F(1), F(-1), F(1)))
    r = sc.verify_solve_system('$2(x+y)=10$ i $x-y=1$',
                               [r'$(3,2)$', r'$(2,3)$', r'$(4,1)$', r'$(1,4)$'], 0)
    assert r.status == sc.STATUS_VERIFIED


def test_fraction_coefficient_supported():
    assert sc.parse_system(r'$\frac{1}{2}x+y=4$ i $x-y=1$') == \
        ((F(1, 2), F(1), F(4)), (F(1), F(-1), F(1)))


# ---------------------------------------------------------------------------
# 18-22: nepodržani slučajevi (unsupported, NIKAD invalid)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    '$x^2+y=5$ i $x-y=1$',                    # 18 nonlinear
    '$xy=4$ i $x+y=5$',                       # 18 nonlinear (product)
    '$x+y+z=6$ i $x-y=1$',                    # 19 three variables
    '$x+=5$ i $x-y=1$',                       # 20 malformed
    '$x+y=7$',                                # only one equation
    '$x+y=7$ i $x-y=1$ i $2x=8$',             # three equations
    r'$\sqrt{x}+y=3$ i $x-y=1$',              # root
    'Riješi sistem bez formula.',             # no equations at all
])
def test_18_to_20_unsupported_systems(question):
    r = sc.verify_solve_system(question, SIMPLE_OPTS, 0)
    assert r.status == sc.STATUS_UNSUPPORTED, question
    assert r.valid_option_indices == ()


def test_21_ambiguous_or_unparseable_pair_is_unsupported():
    r = sc.verify_solve_system(SIMPLE_Q, [r'$(4,3)$', 'Sistem nema rješenja.',
                                          r'$(5,2)$', r'$(2,5)$'], 0)
    assert r.status == sc.STATUS_UNSUPPORTED
    assert sc.ORDERED_PAIR_PARSE_FAILED in r.issue_codes
    r2 = sc.verify_solve_system(SIMPLE_Q, [r'$(1,2)$ ili $(3,4)$', r'$(4,3)$',
                                           r'$(5,2)$', r'$(2,5)$'], 1)
    assert r2.status == sc.STATUS_UNSUPPORTED


def test_22_unsupported_is_never_labelled_verified():
    r = sc.verify_solve_system('$x^2+y=5$ i $x-y=1$', SIMPLE_OPTS, 0)
    assert r.status != sc.STATUS_VERIFIED
    assert r.status == sc.STATUS_UNSUPPORTED


def test_unsupported_task_still_passes_through_unchanged_behaviour():
    """U ovom prolazu `unsupported` NE mijenja postojeće ponašanje."""
    store, fake = SessionStore(), FakeLLM()
    q = 'Riješi sistem: $x^2+y=5$ i $x-y=1$.'
    r = run_task(store, fake, q, [r'$(1,0)$', r'$(2,1)$', r'$(0,-1)$', r'$(3,2)$'], 0, r'$(1,0)$')
    assert r["status"] == "ready", r["answer"]
    assert fake.practice_call_count == 1


# ---------------------------------------------------------------------------
# 23-28: prepoznavanje uređenih parova / answer_kind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (r'$(3,2)$', (F(3), F(2))),                                        # 23
    (r'$(-3,2)$', (F(-3), F(2))),
    (r'$\left(3,2\right)$', (F(3), F(2))),
    (r'$\left(\frac{9}{7},\frac{13}{7}\right)$', (F(9, 7), F(13, 7))),  # 24
    (r'$x=3,\ y=2$', (F(3), F(2))),                                     # 25
    (r'$(0,5;-1,25)$', (F(1, 2), F(-5, 4))),
    (r'Rješenje je $(4,3)$.', (F(4), F(3))),
])
def test_23_to_25_ordered_pair_formats(text, expected):
    assert sc.parse_ordered_pair(text) == expected


def test_26_coordinate_order_is_preserved():
    assert sc.parse_ordered_pair(r'$(3,2)$') == (F(3), F(2))
    assert sc.parse_ordered_pair(r'$(2,3)$') == (F(2), F(3))
    assert sc.parse_ordered_pair(r'$(3,2)$') != sc.parse_ordered_pair(r'$(2,3)$')


def test_27_decimal_comma_is_not_a_coordinate_separator():
    from matbot.task_family_validation import detected_answer_kind

    assert sc.parse_ordered_pair(r'$2,5$') is None      # no parentheses -> decimal
    assert sc.is_bare_ordered_pair(r'$2,5$') is False
    assert detected_answer_kind(r'$2,5$') == "decimal"


def test_28_answer_kind_recognises_fractional_ordered_pairs():
    from matbot.task_family_validation import detected_answer_kind, is_ordered_pair_option

    assert detected_answer_kind(r'$\left(\frac{9}{7},\frac{13}{7}\right)$') == "ordered_pair"
    assert detected_answer_kind(r'$(3,2)$') == "ordered_pair"
    assert detected_answer_kind(r'$x=3,\ y=2$') == "ordered_pair"
    assert is_ordered_pair_option(r'$\left(\frac{9}{7},\frac{13}{7}\right)$')
    # prose statements that MENTION a pair stay statements (live regression)
    assert detected_answer_kind("Par $(2,1)$ zadovoljava obje jednačine.") is None
    assert not sc.is_bare_ordered_pair("Par $(2,1)$ zadovoljava obje jednačine.")


# ---------------------------------------------------------------------------
# 29-33: sigurnost i stanje
# ---------------------------------------------------------------------------

def test_29_30_31_no_issue_code_or_internal_value_leaks():
    import json

    store, fake = SessionStore(), FakeLLM()
    r = run_task(store, fake, LIVE_Q, LIVE_OPTS, 0, LIVE_EXPECTED)
    blob = json.dumps(r, ensure_ascii=False)
    for code in sc.ALL_ISSUE_CODES:
        assert code not in blob
        assert code not in SAFE_ERROR_MESSAGE
    assert "expected_answer" not in blob
    assert "correct_option_id" not in blob
    assert "13" not in blob and "11" not in blob      # no coordinates leak either


def test_32_rejection_preserves_full_progression_state():
    store, fake = SessionStore(), FakeLLM()
    # establish a valid task first, then attempt an invalid one
    run_task(store, fake, SIMPLE_Q, SIMPLE_OPTS, 0, r'$(4,3)$')
    before = store.peek("sys-sess")
    r = run_task(store, fake, LIVE_Q, LIVE_OPTS, 0, LIVE_EXPECTED)
    after = store.peek("sys-sess")
    assert r["answer"] == SAFE_ERROR_MESSAGE
    for key in ("current_task", "current_family", "retry_required",
                "correctly_completed_families", "recent_task_signatures",
                "current_options", "correct_option_id", "expected_answer_summary"):
        assert after[key] == before[key], key
    assert fake.practice_call_count == 2                       # one call per turn


def test_33_shuffle_preserves_independently_verified_correct_option():
    for _ in range(12):
        store, fake = SessionStore(), FakeLLM()
        r = run_task(store, fake, SIMPLE_Q, SIMPLE_OPTS, 0, r'$(4,3)$')
        assert r["status"] == "ready"
        sess = store.peek("sys-sess")
        by_id = {o["id"]: o["text"] for o in sess["current_options"]}
        marked = by_id[sess["correct_option_id"]]
        pair = sc.parse_ordered_pair(marked)
        eqs = sc.parse_system(SIMPLE_Q)
        assert all(sc.satisfies(e, pair) for e in eqs), marked


# ---------------------------------------------------------------------------
# 34-39: postojeći invarijanti
# ---------------------------------------------------------------------------

def test_39_all_family_contracts_remain_covered():
    from matbot.task_family_validation import CONTRACTS, missing_contracts
    from matbot.task_families import FAMILY_DESCRIPTIONS

    assert missing_contracts() == []
    # 36 porodica: pet „fraction_*“ porodica opslužuje SAMO nemigrirane
    # lekcije kroz legacy granicu (matbot/legacy/practice_routing.py).
    # Brišu se tek kad njihovi potrošači dobiju ugovor — vidi
    # tests/test_legacy_routing_parity.py.
    assert len(CONTRACTS) == len(FAMILY_DESCRIPTIONS) == 36


def test_verifier_runs_only_for_solve_system():
    """Druga porodica s istim tekstom NE smije proći kroz sistemsku provjeru."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Provjeri da li je uređeni par $(2,1)$ rješenje sistema: $2x+y=5$\n$x-y=1$",
        expected="Da, zadovoljava obje jednačine.",
        options=make_options("Da, par zadovoljava obje jednačine.",
                             "Ne, ne zadovoljava prvu jednačinu.",
                             "Ne, ne zadovoljava drugu jednačinu.",
                             "Ne, sistem nema rješenja."),
        correct_option_index=0, task_family="verify_ordered_pair",
        answer_kind="short_text", student_must_find="statement",
        task_form="recognition")))
    # server picks solve_system for a fresh systems session, so the declared
    # family mismatch is what rejects it — never the system verifier.
    r = run_practice_turn(store, fake, turn(sid="other-fam"))
    assert fake.practice_call_count == 1


def test_prompt_block_contains_verification_instruction():
    from matbot.task_family_validation import prompt_block

    block = prompt_block("solve_system")
    assert "OBAVEZNA PROVJERA PRIJE SLANJA" in block
    assert "OBJE jednačine" in block
    # other families are untouched
    assert "OBAVEZNA PROVJERA PRIJE SLANJA" not in prompt_block("solve_equation")
