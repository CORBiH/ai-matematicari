# -*- coding: utf-8 -*-
"""Narrow P1 regressions: equivalent systems, ordered pairs, translation scope."""
import copy
from fractions import Fraction as F

import pytest

from matbot import systemcheck as sc
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.schema import NewTask, Option
from matbot.session_store import SessionStore
from matbot.task_family_validation import (
    FamilyContractError,
    has_ambiguous_translation_scope,
    prompt_block,
    validate_task_family,
)
from matbot.topics import lesson_info
from tests.conftest import FakeLLM, make_output


CALL156_Q = "Koji od sljedećih sistema je ekvivalentan sistemu\n\n$3x-2y=4$\n\n$x+y=5$"
CALL156_OPTIONS = [
    "$6x-4y=8,\\;x+y=5$",
    "$6x-4y=10,\\;x+y=5$",
    "$3x-2y=4,\\;2x+2y=10$",
    "$9x-6y=12,\\;x+y=6$",
]
CALL172_Q = "Koji od sljedećih sistema je ekvivalentan sistemu\n\n$3x+4y=5$\n\n$2x-y=3$"
CALL172_OPTIONS = [
    "$6x+8y=10,\\;2x-y=3$",
    "$3x+4y=10,\\;2x-y=3$",
    "$3x+4y=5,\\;4x-2y=7$",
    "$-3x-4y=5,\\;2x-y=3$",
]

CALL32_Q = (
    "Provjeri da li dati uređeni par rješava sistem i odaberi tačnu tvrdnju:\n\n"
    "$2x+3y=8$\n\n$x-y=-1$\n\nDa li je par $(2,1)$ rješenje sistema?"
)
CALL32_OPTIONS = [
    "Ne, jer u prvoj jednačini dobijemo $7\\neq 8$.",
    "Da, par $(2,1)$ zadovoljava obje jednačine.",
    "Ne, jer u drugoj jednačini dobijemo $1\\neq -1$.",
    "Ne, jer u prvoj jednačini dobijemo $8$, ali u drugoj $0\\neq -1$.",
]
CANONICAL_PAIR_OPTIONS = [
    "Par zadovoljava obje jednačine.",
    "Par zadovoljava samo prvu jednačinu.",
    "Par zadovoljava samo drugu jednačinu.",
    "Par ne zadovoljava nijednu jednačinu.",
]


def _equivalent_result(question, correct, others=None, marked=0):
    options = [correct] + list(others or [
        "$x+y=4,\\;x-y=1$",
        "$x+y=3,\\;x-y=2$",
        "$2x+2y=8,\\;x-y=1$",
    ])
    return sc.verify_equivalent_system_options(question, options, marked)


def _task(text, options, correct_index, family, expected="Tačna označena opcija.", difficulty="standard"):
    return NewTask(
        text=text,
        expected_answer=expected,
        difficulty=difficulty,
        options=[Option(text=value) for value in options],
        correct_option_index=correct_index,
        task_family=family,
        student_must_find="statement",
        answer_kind="option_label",
        task_form="recognition",
    )


def _seed_family(store, family, sid):
    if family in ("identify_equivalent_system", "verify_ordered_pair"):
        grade, topic = 9, "9-05-003"
        completed = ["solve_system"]
        current = "solve_system"
        if family == "identify_equivalent_system":
            completed += ["verify_ordered_pair", "choose_method", "determine_number_of_solutions"]
            current = "determine_number_of_solutions"
    else:
        grade, topic = 7, "7-03-016"
        completed = ["solve_equation", "verify_solution", "identify_next_step", "detect_student_error"]
        current = "detect_student_error"
    lesson = lesson_info(grade, topic)
    session = store.load(
        session_id=sid,
        grade=grade,
        lesson_id=topic,
        lesson_title=lesson["title"],
        oblast=lesson["oblast"],
        mode="practice",
    )
    session["correctly_completed_families"] = completed
    session["recently_used_families"] = list(completed)
    session["current_family"] = current
    store.save(session)
    return grade, topic, lesson


def _turn(sid, grade, topic, lesson, msg="Daj mi novi zadatak."):
    return {
        "session_id": sid,
        "grade": grade,
        "selected_topic": topic,
        "selected_oblast": lesson["oblast"],
        "student_message": msg,
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "interaction_type": "",
        "selected_option_id": "",
        "client_turn_id": "",
    }


# --- FIX B: identify_equivalent_system -------------------------------------


def test_call156_finds_both_equivalent_options_and_is_invalid():
    result = sc.verify_equivalent_system_options(CALL156_Q, CALL156_OPTIONS, 0)
    assert result.status == sc.STATUS_INVALID
    assert result.equivalent_option_indices == (0, 2)
    assert result.issue_codes == (sc.MULTIPLE_EQUIVALENT_SYSTEM_OPTIONS,)
    assert result.original_rref == ((F(1), F(0), F(14, 5)), (F(0), F(1), F(11, 5)))


def test_call172_has_one_equivalent_option_and_verifies():
    result = sc.verify_equivalent_system_options(CALL172_Q, CALL172_OPTIONS, 0)
    assert result.status == sc.STATUS_VERIFIED
    assert result.equivalent_option_indices == (0,)


@pytest.mark.parametrize("correct", [
    "$2x+2y=6,\\;x-y=1$",            # scale first only
    "$x+y=3,\\;3x-3y=3$",            # scale second only
    "$2x+2y=6,\\;3x-3y=3$",          # scale both
    "$x-y=1,\\;x+y=3$",              # swap equation order
    "$3=x+y,\\;1=x-y$",              # swap equation sides
    "$2x=4,\\;x-y=1$",               # row1 + row2, then row2
])
def test_supported_row_operations_compare_by_exact_rref(correct):
    result = _equivalent_result("Ekvivalentan sistem: $x+y=3$ $x-y=1$", correct)
    assert result.status == sc.STATUS_VERIFIED
    assert result.equivalent_option_indices == (0,)


def test_unique_systems_with_equal_rref_pass():
    original = sc.rref_augmented_system(sc.parse_system("$2x+y=5$ $x-y=1$"))
    option = sc.rref_augmented_system(sc.parse_option_system("$3x=6,\\;x-y=1$"))
    assert original == option == ((F(1), F(0), F(2)), (F(0), F(1), F(1)))


def test_dependent_systems_match_only_when_row_spaces_equal():
    question = "Koji je ekvivalentan? $x+y=2$ $2x+2y=4$"
    options = [
        "$3x+3y=6,\\;-x-y=-2$",
        "$x-y=2,\\;2x-2y=4$",
        "$x+y=3,\\;2x+2y=6$",
        "$x+y=2,\\;2x+2y=5$",
    ]
    result = sc.verify_equivalent_system_options(question, options, 0)
    assert result.status == sc.STATUS_VERIFIED
    assert result.equivalent_option_indices == (0,)
    assert result.option_rrefs[1] != result.original_rref


def test_inconsistent_systems_use_row_equivalence_not_empty_solution_count():
    question = "Koji je ekvivalentan? $x+y=1$ $x+y=2$"
    options = [
        "$2x+2y=2,\\;-3x-3y=-6$",
        "$x-y=1,\\;x-y=2$",  # also inconsistent, but different row space
        "$x+y=1,\\;2x+2y=2$",
        "$x+2y=1,\\;x+2y=3$",
    ]
    result = sc.verify_equivalent_system_options(question, options, 0)
    assert result.status == sc.STATUS_VERIFIED
    assert result.equivalent_option_indices == (0,)
    assert result.option_rrefs[1] != result.original_rref


def test_non_equivalent_rhs_change_does_not_match():
    result = sc.verify_equivalent_system_options(CALL172_Q, CALL172_OPTIONS, 1)
    assert result.equivalent_option_indices == (0,)
    assert result.status == sc.STATUS_INVALID
    assert sc.MARKED_EQUIVALENT_SYSTEM_MISMATCH in result.issue_codes


@pytest.mark.parametrize("question,options", [
    ("Ekvivalentan? $x+y=3$ $x-y=1$", ["$0x+0y=0,\\;x-y=1$"] * 4),
    ("Ekvivalentan? $x+y=3$ $x-y=1$", ["$2x+2y=6,x-y=1$"] * 4),
    ("Ekvivalentan? $x+y=3$ $x-y=1$", ["$x^2+y=3,\\;x-y=1$"] * 4),
    ("Ekvivalentan? $xy=3$ $x-y=1$", CALL172_OPTIONS),
    ("Ekvivalentan? $x+y=3$", CALL172_OPTIONS),
    ("Ekvivalentan? $x+y+z=3$ $x-y=1$", CALL172_OPTIONS),
])
def test_unsupported_equivalent_system_shapes_are_never_guessed(question, options):
    result = sc.verify_equivalent_system_options(question, options, 0)
    assert result.status == sc.STATUS_UNSUPPORTED
    assert result.equivalent_option_indices == ()


def test_zero_equivalent_options_is_invalid():
    options = CALL172_OPTIONS[1:] + ["$x+y=0,\\;x-y=0$"]
    result = sc.verify_equivalent_system_options(CALL172_Q, options, 0)
    assert result.status == sc.STATUS_INVALID
    assert result.issue_codes == (sc.NO_EQUIVALENT_SYSTEM_OPTION,)


def test_call156_rejected_before_mutation_with_one_call_and_no_code_leak():
    store, fake, sid = SessionStore(), FakeLLM(), "p1-eq-reject"
    grade, topic, lesson = _seed_family(store, "identify_equivalent_system", sid)
    before = store.peek(sid)
    fake.queue(make_output(
        reply="Evo novog zadatka za vježbu.",
        new_task=_task(CALL156_Q, CALL156_OPTIONS, 0, "identify_equivalent_system", difficulty="easy"),
    ))
    response = run_practice_turn(store, fake, _turn(sid, grade, topic, lesson))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(sid) == before
    assert fake.call_count == 1
    assert "multiple_equivalent_system_options" not in str(response)


def test_rejected_equivalent_system_task_cannot_receive_feedback():
    store, fake, sid = SessionStore(), FakeLLM(), "p1-eq-no-feedback"
    grade, topic, lesson = _seed_family(store, "identify_equivalent_system", sid)
    fake.queue(make_output(new_task=_task(
        CALL156_Q, CALL156_OPTIONS, 0, "identify_equivalent_system", difficulty="easy"
    )))
    run_practice_turn(store, fake, _turn(sid, grade, topic, lesson))
    response = run_practice_turn(store, fake, {
        **_turn(sid, grade, topic, lesson, "[klik]"),
        "interaction_type": "choice_answer",
        "selected_option_id": "a",
        "client_turn_id": "p1-rejected-click",
    })
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1


# --- FIX A: verify_ordered_pair --------------------------------------------


@pytest.mark.parametrize("question,expected,truth", [
    ("Provjeri par $(2,1)$: $x+y=3$ $x-y=1$", sc.PAIR_SATISFIES_BOTH, (True, True)),
    ("Provjeri par $(2,1)$: $x+y=3$ $x-y=2$", sc.PAIR_SATISFIES_ONLY_FIRST, (True, False)),
    ("Provjeri par $(2,1)$: $x+y=4$ $x-y=1$", sc.PAIR_SATISFIES_ONLY_SECOND, (False, True)),
    (CALL32_Q, sc.PAIR_SATISFIES_NEITHER, (False, False)),
    ("Provjeri par $(\\frac{1}{2},1)$: $2x=1$ $y=1$", sc.PAIR_SATISFIES_BOTH, (True, True)),
    ("Provjeri par $(0,5;-1,25)$: $2x=1$ $4y=-5$", sc.PAIR_SATISFIES_BOTH, (True, True)),
    ("Provjeri par $(-2,-3)$: $x+y=-5$ $x-y=1$", sc.PAIR_SATISFIES_BOTH, (True, True)),
])
def test_four_state_classifier_uses_exact_substitution(question, expected, truth):
    assert sc.classify_ordered_pair_against_system(question) == expected
    result = sc.verify_ordered_pair_options(
        question, CANONICAL_PAIR_OPTIONS, sc.ORDERED_PAIR_STATUSES.index(expected)
    )
    assert result.status == sc.STATUS_VERIFIED
    assert result.equation_truth_values == truth


@pytest.mark.parametrize("pair", [
    r"$\left(\frac{1}{2},\frac{3}{2}\right)$",
    "$(0,5;1,5)$",
])
def test_verify_ordered_pair_contract_accepts_non_integer_pair(pair):
    question = f"Provjeri da li uređeni par {pair} zadovoljava sistem $2x=1$ i $2y=3$."
    assert validate_task_family(
        "verify_ordered_pair",
        question,
        CANONICAL_PAIR_OPTIONS,
        correct_option_index=0,
        declared={"task_family": "verify_ordered_pair", "answer_kind": "option_label"},
    ) is None


def test_canonical_option_mapper_covers_exactly_four_statuses():
    mapped = tuple(sc.map_ordered_pair_option_meaning(value) for value in CANONICAL_PAIR_OPTIONS)
    assert mapped == sc.ORDERED_PAIR_STATUSES
    assert sc.ordered_pair_options_are_mutually_exclusive(CANONICAL_PAIR_OPTIONS)


@pytest.mark.parametrize("text", [
    "Ne zadovoljava prvu jednačinu.",
    "Ne zadovoljava drugu jednačinu.",
    "Prva jednačina nije zadovoljena.",
    "Druga jednačina nije zadovoljena.",
])
def test_overlapping_negative_wording_is_ambiguous(text):
    assert sc.map_ordered_pair_option_meaning(text) == "ambiguous"


def test_exact_call32_options_are_rejected_as_ambiguous():
    result = sc.verify_ordered_pair_options(CALL32_Q, CALL32_OPTIONS, 0)
    assert result.status == sc.STATUS_INVALID
    assert result.computed_pair_status == sc.PAIR_SATISFIES_NEITHER
    assert result.equation_truth_values == (False, False)
    assert result.issue_codes == (sc.AMBIGUOUS_ORDERED_PAIR_OPTION,)


def test_duplicate_status_and_multiple_match_are_rejected():
    options = [
        CANONICAL_PAIR_OPTIONS[0], CANONICAL_PAIR_OPTIONS[1],
        CANONICAL_PAIR_OPTIONS[3], "Također ne zadovoljava nijednu jednačinu.",
    ]
    result = sc.verify_ordered_pair_options(CALL32_Q, options, 3)
    assert result.status == sc.STATUS_INVALID
    assert sc.OVERLAPPING_ORDERED_PAIR_OPTIONS in result.issue_codes
    assert sc.MULTIPLE_MATCHING_ORDERED_PAIR_STATUSES in result.issue_codes


def test_zero_matching_status_is_rejected():
    options = CANONICAL_PAIR_OPTIONS[:3] + ["Ponovo zadovoljava samo prvu jednačinu."]
    result = sc.verify_ordered_pair_options(CALL32_Q, options, 0)
    assert result.status == sc.STATUS_INVALID
    assert sc.NO_MATCHING_ORDERED_PAIR_STATUS in result.issue_codes


def test_wrong_marked_ordered_pair_status_is_rejected():
    result = sc.verify_ordered_pair_options(CALL32_Q, CANONICAL_PAIR_OPTIONS, 0)
    assert result.status == sc.STATUS_INVALID
    assert result.matching_option_indices == (3,)
    assert result.issue_codes == (sc.MARKED_ORDERED_PAIR_STATUS_MISMATCH,)


def test_arbitrary_prose_is_unsupported_not_guessed():
    options = ["Možda.", "Vjerovatno.", "Ponekad.", "Nije moguće reći."]
    result = sc.verify_ordered_pair_options(CALL32_Q, options, 0)
    assert result.status == sc.STATUS_UNSUPPORTED
    assert result.mapped_option_statuses == (None, None, None, None)


def test_call32_rejected_before_mutation_one_call_and_no_leak():
    store, fake, sid = SessionStore(), FakeLLM(), "p1-pair-reject"
    grade, topic, lesson = _seed_family(store, "verify_ordered_pair", sid)
    before = store.peek(sid)
    fake.queue(make_output(new_task=_task(
        CALL32_Q, CALL32_OPTIONS, 0, "verify_ordered_pair",
        expected="Par ne zadovoljava nijednu jednačinu.",
    )))
    response = run_practice_turn(store, fake, _turn(sid, grade, topic, lesson))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(sid) == before
    assert fake.call_count == 1
    assert "ambiguous_ordered_pair_option" not in str(response)


def test_verified_pair_generation_can_follow_correct_feedback_path():
    store, fake, sid = SessionStore(), FakeLLM(), "p1-pair-feedback"
    grade, topic, lesson = _seed_family(store, "verify_ordered_pair", sid)
    fake.queue(make_output(new_task=_task(
        CALL32_Q, CANONICAL_PAIR_OPTIONS, 3, "verify_ordered_pair",
        expected="Par ne zadovoljava nijednu jednačinu.",
    )))
    generated = run_practice_turn(store, fake, _turn(sid, grade, topic, lesson))
    assert generated["status"] == "ready"
    session = store.peek(sid)
    fake.queue(make_output(
        reply="Tačno. Uvrštavanjem se vidi da nijedna jednačina nije zadovoljena.",
        evaluation="correct",
    ))
    answered = run_practice_turn(store, fake, {
        **_turn(sid, grade, topic, lesson, "[klik]"),
        "interaction_type": "choice_answer",
        "selected_option_id": session["correct_option_id"],
        "client_turn_id": "p1-pair-correct",
    })
    assert answered["answer_verdict"] == "correct"
    assert store.peek(sid)["task_completed"] is True
    assert fake.call_count == 2  # exactly one call in each of two application turns


# --- FIX C: translate_to_equation ------------------------------------------


AMBIGUOUS_TRANSLATIONS = [
    'Prevedi u jednačinu: "Tri puta broj uvećan za 5 daje 20."',
    'Prevedi u jednačinu: "Četiri puta broj smanjen za 3 daje 20."',
    'Prevedi u jednačinu: "Trostruko broj uvećan za 5 daje 20."',
]
SCOPED_TRANSLATIONS = [
    "Trostrukom broju dodaj 5 i dobiješ 20.",
    "Tri puta broj, pa zatim dodaj 5, daje 20.",
    "Zbir trostrukog broja i broja 5 jednak je 20.",
    "Broj uvećaj za 5, pa rezultat pomnoži sa 3; dobiješ 20.",
    "Tri puta zbir broja i 5 jednak je 20.",
    "Trostruka vrijednost zbira broja i 5 je 20.",
    "Od četverostrukog broja oduzmi 3 i dobiješ 20.",
    "Od broja oduzmi 3, pa rezultat pomnoži sa 4; dobiješ 20.",
    "Četiri puta razlika broja i 3 jednaka je 20.",
]
TRANSLATION_OPTIONS = ["$3x+5=20$", "$3(x+5)=20$", "$3x-5=20$", "$x+5=20$"]


def _translation_contract_error(question):
    try:
        validate_task_family(
            "translate_to_equation",
            question=question,
            option_texts=TRANSLATION_OPTIONS,
            correct_option_index=0,
            expected_answer="$3x+5=20$",
            declared={"task_family": "translate_to_equation", "answer_kind": "formula"},
        )
    except FamilyContractError as error:
        return str(error)
    return None


@pytest.mark.parametrize("question", AMBIGUOUS_TRANSLATIONS)
def test_ambiguous_translation_scope_is_rejected(question):
    assert has_ambiguous_translation_scope(question)
    assert "dvosmislen_opseg_mnozenja" in _translation_contract_error(question)


@pytest.mark.parametrize("question", SCOPED_TRANSLATIONS)
def test_explicit_translation_scope_passes(question):
    assert not has_ambiguous_translation_scope(question)
    assert _translation_contract_error(question) is None


@pytest.mark.parametrize("question", [
    "Broj uvećan za 5 daje 20.",
    "Tri puta veća površina iznosi 20.",
    "Od broja oduzmi 3 i dobiješ 20.",
])
def test_nearby_unrelated_phrases_are_not_falsely_rejected(question):
    assert not has_ambiguous_translation_scope(question)


def test_translation_prompt_requires_explicit_scope():
    block = prompt_block("translate_to_equation")
    assert "N puta broj uvećan/smanjen" in block
    assert "pa rezultat pomnoži" in block


def test_ambiguous_translation_rejected_without_mutation_or_code_leak():
    store, fake, sid = SessionStore(), FakeLLM(), "p1-translation-reject"
    grade, topic, lesson = _seed_family(store, "translate_to_equation", sid)
    before = copy.deepcopy(store.peek(sid))
    fake.queue(make_output(new_task=_task(
        AMBIGUOUS_TRANSLATIONS[0], TRANSLATION_OPTIONS, 0, "translate_to_equation",
        expected="$3x+5=20$",
    )))
    response = run_practice_turn(store, fake, _turn(sid, grade, topic, lesson))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(sid) == before
    assert fake.call_count == 1
    assert "dvosmislen_opseg_mnozenja" not in str(response)


def test_all_internal_p1_codes_are_registered_for_leak_checks():
    for code in (
        sc.NO_EQUIVALENT_SYSTEM_OPTION,
        sc.MULTIPLE_EQUIVALENT_SYSTEM_OPTIONS,
        sc.MARKED_EQUIVALENT_SYSTEM_MISMATCH,
        sc.ORIGINAL_SYSTEM_PARSE_FAILED,
        sc.OPTION_SYSTEM_PARSE_FAILED,
        sc.UNSUPPORTED_EQUIVALENT_SYSTEM_SHAPE,
        sc.AMBIGUOUS_ORDERED_PAIR_OPTION,
        sc.OVERLAPPING_ORDERED_PAIR_OPTIONS,
        sc.NO_MATCHING_ORDERED_PAIR_STATUS,
        sc.MULTIPLE_MATCHING_ORDERED_PAIR_STATUSES,
        sc.MARKED_ORDERED_PAIR_STATUS_MISMATCH,
        sc.ORDERED_PAIR_QUESTION_PARSE_FAILED,
    ):
        assert code in sc.ALL_ISSUE_CODES
