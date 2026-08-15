"""Offline regressions for the narrow, server-derived divisibility MCQ oracle."""
import copy

from matbot import mcq_integrity
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (queue_two_call, FakeLLM, make_fidelity_review, make_options, make_output,
                            make_task, queue_generation)


TOPIC = "6-03-004"
GRADE = 6


def _turn(session_id, **changes):
    turn = {
        "session_id": session_id,
        "grade": GRADE,
        "selected_topic": TOPIC,
        "selected_oblast": "",
        "student_message": "Daj mi zadatak.",
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "interaction_type": "student_question",
        "selected_option_id": "",
        "client_turn_id": "",
    }
    turn.update(changes)
    return turn


def _task(text, options, correct_index=0, expected=None):
    return make_task(
        text=text,
        options=make_options(*options),
        correct_option_index=correct_index,
        expected=expected or str(options[correct_index]),
        task_family="direct_computation",
        answer_kind="integer",
    )


def _direct_task(text, correct_index=0):
    options = ("Da", "Ne", "Samo sa 2", "Ne može se odrediti")
    return make_task(
        text=text, options=make_options(*options), correct_option_index=correct_index,
        expected=options[correct_index], task_family="direct_computation", answer_kind="short_text",
    )


def test_integer_divisibility_oracle_finds_the_only_correct_option():
    result = mcq_integrity.evaluate_divisibility_mcq(
        "Koji od ponuđenih brojeva je djeljiv sa 25?", ("725", "714", "738", "741"),
    )
    assert result.applicable and result.valid
    assert result.correct_index == 0
    assert result.correct_value == 725
    compact_conditions = mcq_integrity.evaluate_divisibility_mcq(
        "Koji broj je djeljiv sa 2, 3 i 5?", ("120", "122", "123", "125"),
    )
    assert compact_conditions.divisors == (2, 3, 5)
    assert compact_conditions.correct_value == 120


def test_integer_divisibility_oracle_rejects_zero_or_many_true_options():
    no_correct = mcq_integrity.evaluate_divisibility_mcq(
        "Koji broj je djeljiv sa 25?", ("721", "722", "723", "724"),
    )
    many_correct = mcq_integrity.evaluate_divisibility_mcq(
        "Koji broj je djeljiv sa 25?", ("725", "550", "600", "375"),
    )
    assert no_correct.reason_code == "no_correct_option"
    assert many_correct.reason_code == "multiple_correct_options"
    ambiguous = mcq_integrity.evaluate_divisibility_mcq(
        "Koji broj je djeljiv sa 2 ili sa 3?", ("2", "3", "5", "7"),
    )
    assert ambiguous.reason_code == "divisibility_condition_ambiguous"


def test_integer_divisibility_oracle_rejects_wrong_mark_and_expected_option_reference():
    failure, _ = mcq_integrity.publication_failure(
        "Koji broj je djeljiv sa 25?", ("725", "714", "738", "741"), 1, "725",
    )
    stale_expected, _ = mcq_integrity.publication_failure(
        "Koji broj je djeljiv sa 25?", ("725", "714", "738", "741"), 0, "714",
    )
    assert failure == "marked_option_math_mismatch"
    assert stale_expected == "marked_option_math_mismatch"


def test_measurable_divisibility_profiles_are_strictly_ordered():
    level_one = mcq_integrity.difficulty_profile(
        "Je li broj 47 djeljiv sa 5?", ("Da", "Ne", "Ne znam", "Samo sa 2"),
    )
    level_two = mcq_integrity.difficulty_profile(
        "Koji broj je djeljiv i sa 2 i sa 3?", ("138", "139", "140", "141"),
    )
    level_three = mcq_integrity.difficulty_profile(
        "Dopuni cifru tako da broj bude djeljiv sa 2, sa 3 i sa 5.",
        ("0", "1", "2", "3"),
    )
    assert (level_one.level, level_two.level, level_three.level) == (1, 2, 3)


def test_difficulty_profile_skips_topic_heading_and_reads_coordinated_divisors():
    failed_text = (
        "Primijeni pravila djeljivosti: Da li je broj $1350$ djeljiv i sa $6$ "
        "i sa $25$? Odaberi jednu opciju."
    )
    direct_variant = "Da li je broj $1350$ djeljiv sa $6$ i sa $25$?"
    one_rule = "Da li je broj $430$ djeljiv sa $5$?"

    for text in (failed_text, direct_variant):
        profile = mcq_integrity.difficulty_profile(text, ("Da", "Ne"))
        assert profile.measurable
        assert (profile.level, profile.divisors) == (2, (6, 25))
        assert 1350 not in profile.divisors
        assert mcq_integrity._explicit_divisors(text) == (6, 25)

    one_rule_profile = mcq_integrity.difficulty_profile(one_rule, ("Da", "Ne"))
    assert (one_rule_profile.level, one_rule_profile.divisors) == (1, (5,))
    assert 430 not in one_rule_profile.divisors
    assert mcq_integrity.difficulty_profile(
        "Da li je broj $430$ djeljiv sa $5$ uz napomenu da je $12$ broj bodova?",
        ("Da", "Ne"),
    ).divisors == (5,)
    assert mcq_integrity._explicit_divisors(
        "Da li je broj $1350$ djeljiv sa $6$ i sa $6$ i sa $25$?",
    ) == (6, 25)


# ---------------------------------------------------------------------------
# ŽIVI GATE 93ad85c — pridjev između „djeljiv“ i liste djelilaca
# ---------------------------------------------------------------------------
# Recenzentova ISPRAVKA je glasila:
#   „Koji od sljedećih brojeva je djeljiv istovremeno sa 25 i sa 6? …“
# Uslov je za čovjeka potpuno jednoznačan (25 I 6), ali je parser tražio da
# „sa N“ dođe ODMAH iza riječi „djeljiv“, pa je jedan prilog između njih dao
# prazan skup djelilaca → `divisibility_condition_ambiguous` → gate pao na
# ISPRAVNOM paketu. Oracle je bio u krivu, ne model.

GATE_TEXT = ("Koji od sljedećih brojeva je djeljiv istovremeno sa 25 i sa 6? "
             "Primijeni pravila djeljivosti za 25 i za 6 i izaberi tačnu opciju.")


def test_live_gate_adverb_between_divisible_and_divisor_list():
    assert mcq_integrity._explicit_divisors(GATE_TEXT) == (25, 6)
    result = mcq_integrity.evaluate_divisibility_mcq(
        GATE_TEXT, ["$150$", "$50$", "$75$", "$30$"])
    assert result.applicable and result.valid
    assert result.correct_value == 150          # 150 = 25·6, jedini djeljiv s oba


def test_adverb_tolerance_does_not_swallow_a_disjunction():
    """Prilog se tolerira, ali „ili“ i dalje znači dvosmisleno."""
    text = "Koji broj je djeljiv istovremeno sa 25 ili sa 6?"
    result = mcq_integrity.evaluate_divisibility_mcq(
        text, ["$150$", "$50$", "$75$", "$30$"])
    assert result.applicable and not result.valid
    assert result.reason_code == "divisibility_condition_ambiguous"


def test_adverb_tolerance_does_not_swallow_a_negation():
    text = "Koji broj NIJE djeljiv istovremeno sa 25 i sa 6?"
    result = mcq_integrity.evaluate_divisibility_mcq(
        text, ["$150$", "$50$", "$75$", "$30$"])
    assert result.applicable and not result.valid


def test_unknown_filler_words_still_fail_closed():
    """Samo zatvoren skup priloga se preskače — sve drugo ostaje nedokazivo."""
    assert mcq_integrity._explicit_divisors(
        "Koji broj je djeljiv nekim čudnim uslovom sa 25?") == ()
