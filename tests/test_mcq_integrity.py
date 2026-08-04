"""Offline regressions for the narrow, server-derived divisibility MCQ oracle."""
import copy

from matbot import mcq_integrity
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_fidelity_review, make_options, make_output,
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


def test_pipeline_rejects_all_valid_divisibility_options_without_state_mutation():
    store, fake = SessionStore(), FakeLLM()
    bad = _task(
        "Koji od sljedećih brojeva je djeljiv sa 25?", ("725", "550", "600", "375"),
    )
    queue_generation(fake, bad)

    response = run_practice_turn(store, fake, _turn("all-divisible"))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("all-divisible") is None
    assert fake.call_count == 2


def test_publication_log_is_safe_and_records_server_derived_mcq_facts(caplog):
    store, fake = SessionStore(), FakeLLM()
    valid = _task("Koji broj je djeljiv sa 25?", ("725", "714", "738", "741"))
    queue_generation(fake, valid)
    with caplog.at_level("INFO", logger="matbot.practice"):
        assert run_practice_turn(store, fake, _turn("safe-publication-log"))["status"] == "ready"
    message = "\n".join(record.getMessage() for record in caplog.records)
    assert "practice_task_publication" in message
    assert "publication_result=published_valid_unique_option" in message
    assert "detected_correct_option_count=1" in message
    assert "math_fingerprint=" in message
    assert "expected_answer" not in message
    assert "725" not in message


def test_stale_feedback_candidate_is_rejected_without_commit():
    store, fake = SessionStore(), FakeLLM()
    task = _task(
        "Koji broj je djeljiv sa 25?", ("550", "714", "738", "741"),
    )
    queue_generation(fake, task)
    assert run_practice_turn(store, fake, _turn("stale-feedback"))["status"] == "ready"
    before = copy.deepcopy(store.peek("stale-feedback"))
    correct_id = before["correct_option_id"]
    fake.queue(make_output(reply="Tačno: zato je 150 djeljiv sa 25."))

    response = run_practice_turn(store, fake, _turn(
        "stale-feedback", interaction_type="choice_answer", selected_option_id=correct_id,
        student_message="[klik]", client_turn_id="stale-feedback-click",
    ))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("stale-feedback") == before
    assert fake.call_count == 3


def test_reviewer_corrected_options_replace_tutor_draft_for_committed_grading():
    store, fake = SessionStore(), FakeLLM()
    tutor_draft = _task("Koji broj je djeljiv sa 25?", ("150", "714", "738", "741"))
    corrected = _task("Koji broj je djeljiv sa 25?", ("550", "714", "738", "741"))
    queue_generation(fake, tutor_draft, review=make_fidelity_review(
        decision="correct", corrected_task=corrected,
    ))

    response = run_practice_turn(store, fake, _turn("reviewer-replaces-draft"))
    session = store.peek("reviewer-replaces-draft")
    visible = {option["id"]: option["text"] for option in response["next_state"]["task"]["options"]}

    assert response["status"] == "ready"
    assert "150" not in visible.values()
    assert visible[session["correct_option_id"]] == "550"
    fake.queue(make_output(reply="Tačno: 550 je djeljiv sa 25."))
    correct = run_practice_turn(store, fake, _turn(
        "reviewer-replaces-draft", interaction_type="choice_answer",
        selected_option_id=session["correct_option_id"], student_message="[klik]",
        client_turn_id="correct-corrected-value",
    ))
    assert correct["answer_verdict"] == "correct"


def test_mathematical_fingerprint_rejects_paraphrased_same_integer_mcq():
    store, fake = SessionStore(), FakeLLM()
    first = _task("Koji broj je djeljiv sa 25?", ("725", "714", "738", "741"))
    paraphrase = _task(
        "Izaberi ponuđeni broj koji je djeljiv sa 25.", ("741", "738", "714", "725"),
        correct_index=3,
    )
    queue_generation(fake, first)
    assert run_practice_turn(store, fake, _turn("math-duplicate"))["status"] == "ready"
    before = copy.deepcopy(store.peek("math-duplicate"))
    queue_generation(fake, paraphrase)

    response = run_practice_turn(store, fake, _turn("math-duplicate", student_message="Novi."))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("math-duplicate") == before
    assert fake.call_count == 4


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


def test_easier_divisibility_transition_requires_a_measurable_decrease(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    store, fake = SessionStore(), FakeLLM()
    session_id = "measurable-easier"
    level_one = _direct_task("Je li broj 47 djeljiv sa 5?")
    level_two = _task(
        "Koji broj je djeljiv i sa 2 i sa 3?", ("138", "139", "140", "141"),
    )
    queue_generation(fake, level_one)
    assert run_practice_turn(store, fake, _turn(session_id))["status"] == "ready"
    queue_generation(fake, level_two, review=make_fidelity_review(
        decision="approve", difficulty_level_appropriate=True, difficulty_direction_correct=True,
    ))
    assert run_practice_turn(store, fake, _turn(
        session_id, difficulty_request="harder", student_message="Teži.",
    ))["status"] == "ready"
    before = copy.deepcopy(store.peek(session_id))

    queue_generation(fake, level_two, review=make_fidelity_review(
        decision="approve", difficulty_level_appropriate=True, difficulty_direction_correct=True,
    ))
    rejected = run_practice_turn(store, fake, _turn(
        session_id, difficulty_request="easier", student_message="Lakši.",
    ))
    assert rejected["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) == before

    genuine_level_one = _direct_task("Je li broj 48 djeljiv sa 4?")
    queue_generation(fake, genuine_level_one, review=make_fidelity_review(
        decision="approve", difficulty_level_appropriate=True, difficulty_direction_correct=True,
    ))
    published = run_practice_turn(store, fake, _turn(
        session_id, difficulty_request="easier", student_message="Lakši.",
    ))
    assert published["status"] == "ready"
    assert store.peek(session_id)["difficulty_level"] == 1
