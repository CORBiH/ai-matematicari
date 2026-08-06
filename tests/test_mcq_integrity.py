"""Offline regressions for the narrow, server-derived divisibility MCQ oracle."""
import copy

from matbot import mcq_integrity
from matbot.lesson_fidelity import deterministic_difficulty_failure
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


def test_coordinated_divisors_allow_measurable_level_one_to_two_transition():
    failed_text = (
        "Primijeni pravila djeljivosti: Da li je broj $1350$ djeljiv i sa $6$ "
        "i sa $25$? Odaberi jednu opciju."
    )
    assert deterministic_difficulty_failure(
        "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
        failed_text,
        ("Da", "Ne"),
        target_level=2,
        requested_difficulty="harder",
        level_changed=True,
        prior_task="Da li je broj $430$ djeljiv sa $5$?",
        prior_option_texts=("Da", "Ne"),
    ) is None


def test_pipeline_publishes_coordinated_divisibility_harder_task_without_retry(monkeypatch, caplog):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    store, fake = SessionStore(), FakeLLM()
    session_id = "coordinated-divisors-harder"
    level_one = _direct_task("Da li je broj $430$ djeljiv sa $5$?")
    level_two_text = (
        "Primijeni pravila djeljivosti: Da li je broj $1350$ djeljiv i sa $6$ "
        "i sa $25$? Odaberi jednu opciju."
    )
    level_two = _direct_task(level_two_text)

    queue_generation(fake, level_one)
    assert run_practice_turn(store, fake, _turn(session_id))["status"] == "ready"
    assert store.peek(session_id)["difficulty_level"] == 1
    calls_before_harder = fake.call_count

    queue_generation(fake, level_two, review=make_fidelity_review(
        decision="approve", difficulty_level_appropriate=True, difficulty_direction_correct=True,
    ))
    with caplog.at_level("WARNING", logger="matbot.practice"):
        response = run_practice_turn(store, fake, _turn(
            session_id, student_message="Daj mi teÅ¾i zadatak.", difficulty_request="harder",
        ))

    assert response["status"] == "ready"
    assert store.peek(session_id)["difficulty_level"] == 2
    assert store.peek(session_id)["current_task"] == level_two_text
    assert fake.call_count - calls_before_harder == 2
    assert fake.call_count == 4
    assert "difficulty_direction_not_measurable" not in "\n".join(
        record.getMessage() for record in caplog.records
    )


def test_nonmeasurable_harder_task_preserves_committed_divisibility_state(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    store, fake = SessionStore(), FakeLLM()
    session_id = "nonmeasurable-divisors-harder"
    level_one = _direct_task("Da li je broj $430$ djeljiv sa $5$?")
    queue_generation(fake, level_one)
    assert run_practice_turn(store, fake, _turn(session_id))["status"] == "ready"
    before = copy.deepcopy(store.peek(session_id))
    calls_before_harder = fake.call_count

    queue_generation(fake, _direct_task("Da li je broj $1350$ djeljiv?"), review=make_fidelity_review(
        decision="approve", difficulty_level_appropriate=True, difficulty_direction_correct=True,
    ))
    response = run_practice_turn(store, fake, _turn(
        session_id, student_message="Daj mi teÅ¾i zadatak.", difficulty_request="harder",
    ))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) == before
    assert store.peek(session_id)["difficulty_level"] == 1
    assert fake.call_count - calls_before_harder == 2


def test_explicit_expected_answer_option_references_must_match_committed_shuffle():
    options = [
        {"id": "a", "text": "330"}, {"id": "b", "text": "275"},
        {"id": "c", "text": "470"}, {"id": "d", "text": "182"},
    ]
    assert mcq_integrity.option_reference_failure(
        "Treća opcija (broj 275).", options, "b",
    ) == "expected_answer_option_reference_mismatch"
    assert mcq_integrity.option_reference_failure("Druga opcija (broj 275).", options, "b") == ""
    assert mcq_integrity.option_reference_failure("Opcija B, broj 275.", options, "b") == ""
    assert mcq_integrity.option_reference_failure("275", options, "b") == ""
    assert mcq_integrity.option_reference_failure(
        "Druga opcija (broj 470).", options, "b",
    ) == "expected_answer_option_reference_mismatch"
    assert mcq_integrity.option_reference_failure(
        "Opcija C, broj 275.", options, "b",
    ) == "expected_answer_option_reference_mismatch"


def test_publication_rejects_expected_answer_ordinal_mismatch_after_server_shuffle(monkeypatch, caplog):
    # Freeze the server shuffle only for this regression so positions map to
    # a/b/c/d exactly as the captured live failure documented.
    monkeypatch.setattr("matbot.practice.random.shuffle", lambda items: None)
    store, fake = SessionStore(), FakeLLM()
    task = _task(
        "Koji broj je djeljiv sa 25?", ("330", "275", "470", "182"),
        correct_index=1, expected="Treća opcija (broj 275).",
    )
    queue_generation(fake, task)
    with caplog.at_level("WARNING", logger="matbot.practice"):
        response = run_practice_turn(store, fake, _turn("ordinal-mismatch"))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("ordinal-mismatch") is None
    assert "expected_answer_option_reference_mismatch" in "\n".join(
        record.getMessage() for record in caplog.records
    )


def test_correct_click_feedback_rejects_a_stale_expected_answer_ordinal(monkeypatch):
    monkeypatch.setattr("matbot.practice.random.shuffle", lambda items: None)
    store, fake = SessionStore(), FakeLLM()
    task = _task("Koji broj je djeljiv sa 25?", ("330", "275", "470", "182"),
                 correct_index=1, expected="Druga opcija (broj 275).")
    queue_generation(fake, task)
    assert run_practice_turn(store, fake, _turn("ordinal-feedback"))["status"] == "ready"
    before = copy.deepcopy(store.peek("ordinal-feedback"))
    fake.queue(make_output(reply="Tačno. Treća opcija (broj 275) je ispravna."))
    response = run_practice_turn(store, fake, _turn(
        "ordinal-feedback", interaction_type="choice_answer", selected_option_id="b",
        student_message="[klik]", client_turn_id="ordinal-feedback-click",
    ))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("ordinal-feedback") == before


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


def test_easier_target_profile_is_shared_with_tutor_and_reviewer_and_rejects_wording_only_fix(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    store, fake = SessionStore(), FakeLLM()
    session_id = "live-easier-reproduction"
    level_one = _direct_task("Je li broj 47 djeljiv sa 5?")
    level_two = _task(
        "Koji od ovih brojeva je djeljiv i sa 25 i sa 10?",
        ("12650", "12640", "12560", "12345"),
    )
    wording_only_correction = _task(
        "Izaberite broj koji je djeljiv i sa 10 i sa 25:",
        ("12650", "12640", "12560", "12345"),
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
        decision="correct", corrected_task=wording_only_correction,
        difficulty_level_appropriate=True, difficulty_direction_correct=True,
    ))
    rejected = run_practice_turn(store, fake, _turn(
        session_id, difficulty_request="easier", student_message="Lakši.",
    ))
    tutor_prompt = fake.practice_calls[-1][1]
    reviewer_prompt = fake.fidelity_calls[-1][1]
    for prompt in (tutor_prompt, reviewer_prompt):
        assert "CILJ PROFILA 1" in prompt
        assert "profil=2; djelitelji=[25, 10]" in prompt
        assert "otisak=" in prompt
        assert "NE SMIJE ponoviti" in prompt
    assert rejected["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) == before
    assert fake.call_count == 6  # Tutor + Reviewer only; no retry/third call.

    genuine_level_one = _task(
        "Koji od ovih brojeva je djeljiv sa 9?", ("126", "127", "128", "130"),
    )
    queue_generation(fake, genuine_level_one, review=make_fidelity_review(
        decision="correct", corrected_task=genuine_level_one,
        difficulty_level_appropriate=True, difficulty_direction_correct=True,
    ))
    published = run_practice_turn(store, fake, _turn(
        session_id, difficulty_request="easier", student_message="Lakši.",
    ))
    assert published["status"] == "ready"
    after = store.peek(session_id)
    assert after["difficulty_level"] == 1
    previous_fingerprint = mcq_integrity.mathematical_fingerprint(
        mcq_integrity.evaluate_divisibility_mcq(level_two.text, [option.text for option in level_two.options]),
        "direct_computation",
    )
    new_fingerprint = mcq_integrity.mathematical_fingerprint(
        mcq_integrity.evaluate_divisibility_mcq(
            after["current_task"], [option["text"] for option in after["current_options"]],
        ),
        after["current_family"],
    )
    assert new_fingerprint and new_fingerprint != previous_fingerprint
    assert fake.call_count == 8  # Four generation turns, exactly Tutor + Reviewer each.


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
