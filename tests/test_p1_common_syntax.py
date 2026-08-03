# -*- coding: utf-8 -*-
"""Follow-up P1 coverage: common system syntax and pair-option normalization."""
from fractions import Fraction as F

import pytest

from matbot import systemcheck as sc
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_output
from tests.test_p1_narrow_verifiers import (
    CALL156_Q,
    CALL172_OPTIONS,
    CALL172_Q,
    _equivalent_result,
    _seed_family,
    _task,
    _turn,
)


COMMON_ROWS = ((F(2), F(4), F(10)), (F(3), F(-1), F(4)))


@pytest.mark.parametrize("option", [
    r"$\left(2x+4y=10,\ 3x-y=4\right)$",
    r"$2x+4y=10,\ 3x-y=4$",
    "$2x+4y=10, 3x-y=4$",
    r"$2x+4y=10,\;3x-y=4$",
    r"$2x+4y=10;\ 3x-y=4$",
    "$2x+4y=10\n3x-y=4$",
    "$2x+4y=10$ i $3x-y=4$",
    r"2x+4y=10,\;3x-y=4",
])
def test_parse_option_system_accepts_common_live_formats(option):
    assert sc.parse_option_system(option) == COMMON_ROWS


def test_decimal_commas_do_not_become_system_separators():
    parsed = sc.parse_option_system(r"$0,5x+y=2, x-y=1$")
    assert parsed == ((F(1, 2), F(1), F(2)), (F(1), F(-1), F(1)))


def test_exactly_one_valid_candidate_split_is_accepted():
    assert sc.parse_option_system(r"$0,5x+y=2,\ 3x-y=4$") == (
        (F(1, 2), F(1), F(2)), (F(3), F(-1), F(4)),
    )


def test_zero_valid_candidate_splits_are_unsupported():
    with pytest.raises(sc._Unsupported):
        sc.parse_option_system("$2x+4y=10, ovo nije jednačina$")


def test_multiple_valid_candidate_splits_are_unsupported(monkeypatch):
    monkeypatch.setattr(sc, "_candidate_option_separator_spans", lambda _value: ((3, 5), (3, 5)))
    with pytest.raises(sc._Unsupported):
        sc.parse_option_system("$x=1, y=2$")


LIVE_EQUIVALENT_TASKS = [
    (
        "Koji je sistem ekvivalentan sistemu $x+2y=5$ i $3x-y=4$?",
        [
            r"$\left(2x+4y=10,\ 3x-y=4\right)$",
            r"$\left(x+2y=10,\ 3x-y=4\right)$",
            r"$\left(x+2y=5,\ 6x-2y=7\right)$",
            r"$\left(2x+4y=9,\ 3x-y=4\right)$",
        ],
    ),
    (
        "Koji je sistem ekvivalentan sistemu $x+2y=5$ i $3x-y=4$?",
        [r"$2x+4y=10,\ 3x-y=4$", r"$x+2y=6,\ 3x-y=4$",
         r"$x+2y=5,\ 3x-y=5$", r"$2x+4y=9,\ 3x-y=4$"],
    ),
    (
        "Koji je sistem ekvivalentan sistemu $x+y=4$ i $x-y=2$?",
        ["$3x+3y=12, x-y=2$", "$x+y=4, x-y=-2$",
         "$2x+3y=8, x-y=2$", "$x+y=5, x-y=2$"],
    ),
    (
        "Koji je sistem ekvivalentan sistemu $x+y=4$ i $x-y=2$?",
        [r"$2x+2y=8,\;2x-2y=4$", r"$x+y=4,\;2x-y=2$",
         r"$x+y=4,\;x-y=1$", r"x+y=5,\;x-y=2"],
    ),
]


@pytest.mark.parametrize("question,options", LIVE_EQUIVALENT_TASKS)
def test_exact_live_common_syntax_tasks_are_now_product_verified(question, options):
    result = sc.verify_equivalent_system_options(question, options, 0)
    assert result.status == sc.STATUS_VERIFIED
    assert result.equivalent_option_indices == (0,)
    assert all(rref is not None for rref in result.option_rrefs)


def _system_option(left, right, style):
    if style == "math_spacing":
        return f"${left},\\;{right}$"
    if style == "slash_space":
        return f"${left},\\ {right}$"
    if style == "plain_space":
        return f"${left}, {right}$"
    if style == "left_right":
        return f"$\\left({left},\\ {right}\\right)$"
    return f"{left}, {right}"


@pytest.mark.parametrize("style", ["math_spacing", "slash_space", "plain_space", "left_right", "plain"])
def test_multiple_equivalent_regression_rejects_in_every_supported_separator(style):
    systems = [
        ("6x-4y=8", "x+y=5"), ("6x-4y=10", "x+y=5"),
        ("3x-2y=4", "2x+2y=10"), ("9x-6y=12", "x+y=6"),
    ]
    options = [_system_option(left, right, style) for left, right in systems]
    result = sc.verify_equivalent_system_options(CALL156_Q, options, 0)
    assert result.status == sc.STATUS_INVALID
    assert result.equivalent_option_indices == (0, 2)


@pytest.mark.parametrize("correct", [
    "$x+y=3, 3x-3y=3$",       # scale second only
    "$x-y=1, x+y=3$",         # swapped order
    "$2x=4, x-y=1$",          # elimination combination
])
def test_common_syntax_detects_required_equivalent_transformations(correct):
    result = _equivalent_result("Ekvivalentan sistem: $x+y=3$ $x-y=1$", correct)
    assert result.status == sc.STATUS_VERIFIED
    assert result.equivalent_option_indices == (0,)


def test_remaining_unparseable_option_fails_closed_before_mutation():
    store, fake, sid = SessionStore(), FakeLLM(), "p1-eq-unsupported-fail-closed"
    grade, topic, lesson = _seed_family(store, "identify_equivalent_system", sid)
    before = store.peek(sid)
    options = list(CALL172_OPTIONS)
    options[3] = "$ovo nije linearna jednačina$"
    fake.queue(make_output(new_task=_task(
        CALL172_Q, options, 0, "identify_equivalent_system", difficulty="easy"
    )))
    response = run_practice_turn(store, fake, _turn(sid, grade, topic, lesson))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(sid) == before
    assert fake.practice_call_count == 1
    assert "option_system_parse_failed" not in str(response)


@pytest.mark.parametrize("word", ["OBJE", "OBE", "OBA"])
def test_both_equations_nearby_variants_map_to_satisfies_both(word):
    assert sc.map_ordered_pair_option_meaning(
        f"Par zadovoljava {word} jednačine."
    ) == sc.PAIR_SATISFIES_BOTH


LIVE_WRAPPED_PAIR_OPTIONS = [
    r"$\text{Par }(3,2)\ \text{zadovoljava OBE jednačine.}$",
    r"$\text{Par }(3,2)\ \text{zadovoljava SAMO PRVU jednačinu.}$",
    r"$\text{Par }(3,2)\ \text{zadovoljava SAMO DRUGU jednačinu.}$",
    r"$\text{Par }(3,2)\ \text{ne zadovoljava NIJEDNU jednačinu.}$",
]


def test_mathjax_text_wrappers_are_flattened_and_exact_call13_set_verifies():
    question = "Da li je uređeni par $(3,2)$ rješenje sistema $2x+3y=13$ $x-y=1$?"
    result = sc.verify_ordered_pair_options(question, LIVE_WRAPPED_PAIR_OPTIONS, 2)
    assert result.status == sc.STATUS_VERIFIED
    assert result.equation_truth_values == (False, True)
    assert result.computed_pair_status == sc.PAIR_SATISFIES_ONLY_SECOND
    assert result.matching_option_indices == (2,)
    assert result.mapped_option_statuses == sc.ORDERED_PAIR_STATUSES
    assert sc.ordered_pair_options_are_mutually_exclusive(LIVE_WRAPPED_PAIR_OPTIONS)


@pytest.mark.parametrize("text", [
    "Ne zadovoljava prvu jednačinu.",
    r"$\text{Ne zadovoljava drugu jednačinu.}$",
])
def test_wrapper_normalization_does_not_weaken_ambiguous_negative_rejection(text):
    assert sc.map_ordered_pair_option_meaning(text) == "ambiguous"
