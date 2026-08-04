"""Univerzalni troslojni kontroler pedagoške težine — FakeLLM end-to-end,
oba puta (528 lekcija bez ugovora preko Recenzenta, i šest lekcija s
ugovorom preko determinističkog adaptera). Zastavica
MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled se postavlja SAMO unutar
pojedinačnih testova (monkeypatch, auto-cleanup) — produkcijski default
ostaje netaknut.

Živi nalaz koji je pokrenuo ovaj kontroler: svježa sesija na lekciji
„Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25“ (6-03-004) je
otvarala ostatkom dijeljenja 375:25 umjesto uvodnog pitanja.
"""
import copy

import pytest

from matbot import difficulty_level
from matbot.contracts import generator, registry
from matbot.practice import (SAFE_ERROR_MESSAGE, _ANOTHER_ADVANCED_TASK_INTRO,
                             _ANOTHER_INTRO_TASK_INTRO, _HARDER_TASK_INTRO,
                             _NEW_TASK_INTRO, _SAME_SUPPORTED_DIFFICULTY_INTRO,
                             run_practice_turn)
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_fidelity_review, make_options,
                            make_output, make_task, queue_generation)

DIVISIBILITY = ("6-03-004", 6, "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25")
INTEGER_ADD = ("7-02-008", 7, "Sabiranje cijelih brojeva različitih znakova")
DECIMAL_COMPARE = ("6-05-006", 6, "Upoređivanje decimalnih brojeva")
FRACTION_WORD_PROBLEM = ("6-04-015", 6, "Tekstualni zadaci s razlomcima")
RECTANGLE_AREA = ("7-05-019", 7, "Površina pravougaonika i kvadrata - obnova")
SYSTEM_WORD = ("9-05-013", 9, "Tekstualni zadatak sa sistemom")
CONTRACT_LESSON = ("6-04-009", 6, "add_subtract_like_denominators")


def _enable(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(topic, grade, **changes):
    payload = {
        "session_id": f"lvl-{topic}", "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


_TASK_COUNTER = [0]


def _task(text="Izračunaj $2+2$.", options=("$4$", "$3$", "$5$", "$6$")):
    return make_task(text=text, options=make_options(*options))


def _next_task():
    """Zadatak s JEDINSTVENIM tekstom (rastući brojevi) — sprječava lažno
    'ponovljen tekst zadatka u istoj sesiji' kad isti test generiše više
    zadataka zaredom u istoj sesiji."""
    _TASK_COUNTER[0] += 1
    n = _TASK_COUNTER[0]
    return _task(f"Izračunaj $2+2+0\\cdot{n}$.", ("$4$", "$3$", "$5$", "$6$"))


# ===========================================================================
# 528 lekcija BEZ ugovora — preko Recenzenta
# ===========================================================================

# Zadatak po lekciji koji ZADOVOLJAVA njen ugovor porodice (matbot/
# task_family_validation.py) — ova provjera je nepromijenjena i i dalje
# autoritativna, pa generički _next_task() ne odgovara svakoj od šest.
_FRESH_SESSION_TASK_BY_TOPIC = {
    INTEGER_ADD[0]: _task,
    DECIMAL_COMPARE[0]: lambda: _task(
        "Koji je broj veći: $0,7$ ili $0,68$?",
        ("$0,7$", "$0,68$", "Jednaki su.", "Ne zna se.")),
    FRACTION_WORD_PROBLEM[0]: lambda: _task(
        "Amar je pojeo $\\frac{2}{8}$ torte na rođendanskoj proslavi, a Lejla "
        "$\\frac{3}{8}$ iste torte. Koliko su torte ukupno pojeli zajedno?",
        ("$\\frac{5}{8}$", "$\\frac{5}{16}$", "$\\frac{6}{8}$", "$\\frac{1}{8}$")),
    RECTANGLE_AREA[0]: _task,
    SYSTEM_WORD[0]: lambda: _task(
        "Amar i Lejla zajedno imaju $10$ KM. Amar ima $2$ KM više od Lejle. "
        "Koliko KM ima svako?",
        ("$(6,4)$", "$(4,6)$", "$(5,5)$", "$(8,2)$")),
    DIVISIBILITY[0]: lambda: _task(
        "Je li broj $24$ djeljiv sa $3$?",
        ("Da", "Ne", "Samo sa $2$", "Ne može se odrediti."),
    ),
}


@pytest.mark.parametrize("topic,grade,_title", [
    INTEGER_ADD, DECIMAL_COMPARE, FRACTION_WORD_PROBLEM, RECTANGLE_AREA,
    SYSTEM_WORD, DIVISIBILITY,
])
def test_fresh_session_targets_level_1_across_all_six_categories(topic, grade, _title, monkeypatch):
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _FRESH_SESSION_TASK_BY_TOPIC[topic]())
    response = run_practice_turn(store, fake, _turn(topic, grade))

    assert response["status"] == "ready"
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1
    tutor_input = fake.practice_calls[-1][1]
    reviewer_input = fake.fidelity_calls[-1][1]
    assert "CILJANI NIVO TEŽINE: 1" in tutor_input
    assert "CILJANI NIVO TEŽINE: 1" in reviewer_input
    assert fake.call_count == 2


def test_reported_bug_lesson_advanced_level_1_draft_is_corrected_not_published(monkeypatch):
    """Jezgro reprodukcije: nacrt koji NIJE uvodni (npr. ostatak 375:25) na
    Nivou 1 mora biti ispravljen ili odbijen — nikad objavljen kao da je
    prihvatljiv."""
    topic, grade, _ = DIVISIBILITY
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    advanced_draft = _task("Koji je ostatak pri dijeljenju $375$ sa $25$?",
                          ("$0$", "$5$", "$10$", "$15$"))
    corrected = _task("Da li je broj $24$ djeljiv sa $3$?",
                      ("Da.", "Ne.", "Samo sa $2$.", "Nije moguće odrediti."))
    fake.queue(make_output(reply="Evo zadatka.", new_task=advanced_draft))
    fake.queue(make_fidelity_review(decision="correct", corrected_task=corrected,
                                    difficulty_level_appropriate=True))
    response = run_practice_turn(store, fake, _turn(topic, grade))

    assert response["status"] == "ready"
    assert "24" in store.peek(f"lvl-{topic}")["current_task"]
    assert "375" not in store.peek(f"lvl-{topic}")["current_task"]
    assert fake.call_count == 2


def test_plain_new_task_keeps_the_level_unchanged(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1

    queue_generation(fake, _next_task())
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi drugi zadatak."))
    assert response["status"] == "ready"
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1


def test_harder_increases_and_caps_at_three(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))

    for expected_level in (2, 3, 3):
        queue_generation(fake, _next_task())
        response = run_practice_turn(store, fake, _turn(
            topic, grade, student_message="Daj mi teži zadatak.", difficulty_request="harder"))
        assert response["status"] == "ready", response
        assert store.peek(f"lvl-{topic}")["difficulty_level"] == expected_level


def test_easier_decreases_and_floors_at_one(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi teži zadatak.", difficulty_request="harder"))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 2

    for expected_level in (1, 1):
        queue_generation(fake, _next_task())
        response = run_practice_turn(store, fake, _turn(
            topic, grade, student_message="Daj mi lakši zadatak.", difficulty_request="easier"))
        assert response["status"] == "ready"
        assert store.peek(f"lvl-{topic}")["difficulty_level"] == expected_level


def test_boundary_harder_at_level_3_does_not_require_relative_direction_and_uses_neutral_intro(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))
    for _ in range(2):  # 1 -> 2 -> 3
        queue_generation(fake, _next_task())
        run_practice_turn(store, fake, _turn(
            topic, grade, student_message="teže", difficulty_request="harder"))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 3

    # difficulty_direction_correct=False bi inače srušilo turn -- na PRAVOJ
    # granici se NE traži, jer se ništa relativno nije pomjerilo.
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    fake.queue(make_fidelity_review(difficulty_level_appropriate=True,
                                    difficulty_direction_correct=False))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi teži zadatak.", difficulty_request="harder"))

    assert response["status"] == "ready"
    assert response["answer"].startswith(_ANOTHER_ADVANCED_TASK_INTRO)
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 3


def test_boundary_easier_at_level_1_does_not_require_relative_direction_and_uses_neutral_intro(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1

    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    fake.queue(make_fidelity_review(difficulty_level_appropriate=True,
                                    difficulty_direction_correct=False))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi lakši zadatak.", difficulty_request="easier"))

    assert response["status"] == "ready"
    assert response["answer"].startswith(_ANOTHER_INTRO_TASK_INTRO)
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1


def test_normal_case_direction_requirement_still_enforced(monkeypatch):
    """1 -> 2 ("harder") JESTE stvarna promjena — difficulty_direction_correct
    ostaje obavezan i i dalje ruši turn kad ga recenzent obori."""
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))
    before = copy.deepcopy(store.peek(f"lvl-{topic}"))
    calls_before = fake.call_count

    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    fake.queue(make_fidelity_review(difficulty_level_appropriate=True,
                                    difficulty_direction_correct=False))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi teži zadatak.", difficulty_request="harder"))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count - calls_before == 2
    assert store.peek(f"lvl-{topic}") == before


def test_missing_difficulty_level_appropriate_fails_closed(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    fake.queue(make_fidelity_review(difficulty_level_appropriate=None))
    response = run_practice_turn(store, fake, _turn(topic, grade))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 2
    assert store.peek(f"lvl-{topic}") is None


def test_false_difficulty_level_appropriate_with_no_correction_fails_closed(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    fake.queue(make_fidelity_review(decision="approve", corrected_task=None,
                                    difficulty_level_appropriate=False))
    response = run_practice_turn(store, fake, _turn(topic, grade))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 2
    assert store.peek(f"lvl-{topic}") is None


def test_approve_with_false_difficulty_level_and_correction_is_normalized_and_published(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    draft = _task("Izračunaj $8\\cdot 7 \\cdot 6 \\cdot 5$.", ("$1680$", "$100$", "$56$", "$13$"))
    corrected = _task("Izračunaj $2+3$.", ("$5$", "$4$", "$6$", "$1$"))
    fake.queue(make_output(reply="Evo zadatka.", new_task=draft))
    fake.queue(make_fidelity_review(decision="approve", corrected_task=corrected,
                                    difficulty_level_appropriate=False))
    response = run_practice_turn(store, fake, _turn(topic, grade))

    assert response["status"] == "ready"
    assert "2+3" in store.peek(f"lvl-{topic}")["current_task"]
    assert fake.call_count == 2
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1


def test_server_label_overrides_the_models_declared_label(monkeypatch):
    """Nivo je server-owned: modelova new_task.difficulty se PREPISUJE
    mapiranom oznakom, nikad obrnuto."""
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    contradicting = make_task(text="Izračunaj $2+2$.", options=make_options("4", "3", "5", "6"),
                              difficulty="hard")  # nivo 1 == "easy", model tvrdi "hard"
    queue_generation(fake, contradicting)
    run_practice_turn(store, fake, _turn(topic, grade))
    assert store.peek(f"lvl-{topic}")["difficulty"] == "easy"


def test_stored_label_never_disagrees_with_the_server_level_in_duplicate_signature(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    contradicting = make_task(text="Izračunaj $2+2$.", options=make_options("4", "3", "5", "6"),
                              difficulty="hard")
    queue_generation(fake, contradicting)
    run_practice_turn(store, fake, _turn(topic, grade))
    session = store.peek(f"lvl-{topic}")
    signature = session["recent_task_signatures"][-1]
    assert signature["difficulty"] == difficulty_level.LEVEL_TO_LABEL[session["difficulty_level"]]


def test_intro_never_disagrees_with_the_server_level_regardless_of_declared_label(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    intros = []
    for label in ("easy", "hard"):
        store, fake = SessionStore(), FakeLLM()
        task = make_task(text="Izračunaj $2+2$.", options=make_options("4", "3", "5", "6"),
                         difficulty=label)
        queue_generation(fake, task)
        response = run_practice_turn(store, fake, _turn(topic, grade))
        intros.append(response["answer"].split("\n\n")[0])
    assert intros[0] == intros[1] == _NEW_TASK_INTRO


def test_lesson_form_invariant_at_higher_level_word_problem_stays_a_word_problem(monkeypatch):
    """Nivo NIKAD ne smije zaobići postojeći ugovor porodice — čak i uz
    'harder' i difficulty_level_appropriate=True, gola računska operacija bez
    životnog konteksta i dalje pada na task_family_validation."""
    topic, grade, _ = FRACTION_WORD_PROBLEM
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    real_word_problem = _task(
        "Amar je pojeo $\\frac{2}{8}$ torte na proslavi, a Lejla $\\frac{3}{8}$ iste torte. "
        "Koliko su torte ukupno pojeli zajedno?",
        ("$\\frac{5}{8}$", "$\\frac{5}{16}$", "$\\frac{6}{8}$", "$\\frac{1}{8}$"),
    )
    queue_generation(fake, real_word_problem)
    run_practice_turn(store, fake, _turn(topic, grade))

    bare_operation = _task(r"Izračunaj $\frac{2}{8} + \frac{3}{8}$.",
                          ("$\\frac{5}{8}$", "$\\frac{5}{16}$", "$\\frac{6}{8}$", "$\\frac{1}{8}$"))
    fake.queue(make_output(reply="Evo zadatka.", new_task=bare_operation))
    fake.queue(make_fidelity_review(decision="approve", difficulty_level_appropriate=True))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi teži zadatak.", difficulty_request="harder"))

    assert response["answer"] == SAFE_ERROR_MESSAGE  # family contract i dalje odbija


def test_hint_and_dont_know_never_change_the_level(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))

    fake.queue(make_output(reply="Pogledaj znak.", gave_hint=True))
    run_practice_turn(store, fake, _turn(topic, grade, student_message="ne znam"))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1


def test_incorrect_answer_click_never_changes_the_level(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))
    session = store.peek(f"lvl-{topic}")
    wrong = next(o["id"] for o in session["current_options"] if o["id"] != session["correct_option_id"])

    fake.queue(make_output(reply="", hint="Pazi na znak."))
    run_practice_turn(store, fake, _turn(
        topic, grade, interaction_type="choice_answer", selected_option_id=wrong,
        student_message="[klik]", client_turn_id="c1"))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1


def test_rejection_leaves_the_previous_level_untouched(monkeypatch):
    topic, grade, _ = INTEGER_ADD
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))
    before = copy.deepcopy(store.peek(f"lvl-{topic}"))

    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    fake.queue(make_fidelity_review(difficulty_level_appropriate=None))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi teži zadatak.", difficulty_request="harder"))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(f"lvl-{topic}") == before


def test_lesson_switch_resets_to_level_1(monkeypatch):
    _enable(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    topic_a, grade_a, _ = INTEGER_ADD
    session_id = "lvl-switch"
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, dict(_turn(topic_a, grade_a), session_id=session_id))
    for _ in range(2):
        queue_generation(fake, _next_task())
        run_practice_turn(store, fake, dict(
            _turn(topic_a, grade_a, student_message="teže", difficulty_request="harder"),
            session_id=session_id,
        ))
    assert store.peek(session_id)["difficulty_level"] == 3

    topic_b, grade_b, _ = DECIMAL_COMPARE
    queue_generation(fake, _task("Koji je broj veći: $0,7$ ili $0,68$?",
                                ("$0,7$", "$0,68$", "Jednaki su.", "Ne zna se.")))
    run_practice_turn(store, fake, dict(_turn(topic_b, grade_b), session_id=session_id))
    assert store.peek(session_id)["difficulty_level"] == 1


def test_feature_off_no_difficulty_block_and_no_mandatory_reviewer_field():
    """Bez monkeypatch (zastavica isključena) — prompt ne smije sadržavati
    difficulty blok, a nedostajući difficulty_level_appropriate ne smije ništa
    srušiti."""
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task(), review=make_fidelity_review(difficulty_level_appropriate=None))
    response = run_practice_turn(store, fake, _turn(topic, grade))

    assert response["status"] == "ready"
    tutor_input = fake.practice_calls[-1][1]
    reviewer_input = fake.fidelity_calls[-1][1]
    assert "CILJANI NIVO TEŽINE" not in tutor_input
    assert "CILJANI NIVO TEŽINE" not in reviewer_input
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1  # podrazumijevano, nikad promijenjeno


def test_feature_off_never_transitions_or_commits_the_level(monkeypatch):
    """Nedvosmisleno pravilo: dok je zastavica isključena, NIJEDAN turn ne
    računa niti commituje novu vrijednost — polje ostaje na podrazumijevanoj
    vrijednosti bez obzira na ponovljene harder/easier zahtjeve."""
    monkeypatch.delenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", raising=False)
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, _turn(topic, grade))
    for _ in range(3):
        queue_generation(fake, _next_task())
        run_practice_turn(store, fake, _turn(
            topic, grade, student_message="teže", difficulty_request="harder"))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1


# ===========================================================================
# Šest lekcija s ugovorom — deterministički adapter, BEZ recenzenta
# ===========================================================================

def test_contract_lesson_shares_the_same_state_machine_as_a_528_lesson(monkeypatch):
    _enable(monkeypatch)
    topic, grade, _ = CONTRACT_LESSON
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, _turn(topic, grade))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1
    assert fake.call_count == 1
    assert fake.fidelity_calls == []

    for expected_level in (2, 3, 3):
        fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
        run_practice_turn(store, fake, _turn(
            topic, grade, student_message="teže", difficulty_request="harder"))
        assert store.peek(f"lvl-{topic}")["difficulty_level"] == expected_level

    for expected_level in (2, 1, 1):
        fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
        run_practice_turn(store, fake, _turn(
            topic, grade, student_message="lakše", difficulty_request="easier"))
        assert store.peek(f"lvl-{topic}")["difficulty_level"] == expected_level

    assert fake.fidelity_calls == []  # Recenzent NIKAD pozvan za lekciju s ugovorom


def test_contract_lesson_1_to_2_step_is_capability_limited_and_uses_neutral_intro(monkeypatch):
    _enable(monkeypatch)
    topic, grade, _ = CONTRACT_LESSON
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, _turn(topic, grade))

    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="teže", difficulty_request="harder"))

    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 2  # sesija SE pomjerila
    assert response["answer"].startswith(_SAME_SUPPORTED_DIFFICULTY_INTRO)
    assert not response["answer"].startswith(_HARDER_TASK_INTRO)
    assert not response["answer"].startswith(_ANOTHER_ADVANCED_TASK_INTRO)


def test_contract_lesson_2_to_3_step_genuinely_changes_generation(monkeypatch):
    _enable(monkeypatch)
    topic, grade, _ = CONTRACT_LESSON
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, _turn(topic, grade))
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, _turn(
        topic, grade, student_message="teže", difficulty_request="harder"))

    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="teže", difficulty_request="harder"))

    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 3
    assert response["answer"].startswith(_HARDER_TASK_INTRO)
    contract = registry.contract_for(topic)
    session = store.peek(f"lvl-{topic}")
    assert "operand_magnitude" in contract.difficulty_dimensions


def test_contract_lesson_boundary_harder_at_level_3_uses_advanced_intro(monkeypatch):
    _enable(monkeypatch)
    topic, grade, _ = CONTRACT_LESSON
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, _turn(topic, grade))
    for _ in range(2):
        fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
        run_practice_turn(store, fake, _turn(
            topic, grade, student_message="teže", difficulty_request="harder"))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 3

    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="teže", difficulty_request="harder"))
    assert response["answer"].startswith(_ANOTHER_ADVANCED_TASK_INTRO)
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 3


def test_contract_lesson_flag_off_never_changes_level_or_generation(monkeypatch):
    monkeypatch.delenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", raising=False)
    topic, grade, _ = CONTRACT_LESSON
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, _turn(topic, grade))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1

    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, _turn(
        topic, grade, student_message="teže", difficulty_request="harder"))
    assert store.peek(f"lvl-{topic}")["difficulty_level"] == 1


def test_contract_lesson_rejected_turn_leaves_level_unchanged(monkeypatch):
    _enable(monkeypatch)
    topic, grade, _ = CONTRACT_LESSON
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, _turn(topic, grade))
    before = copy.deepcopy(store.peek(f"lvl-{topic}"))
    calls_before = fake.call_count

    original_generate = generator.generate

    def _always_fail(*args, **kwargs):
        raise generator.GenerationError("forced_failure_for_test")

    monkeypatch.setattr(generator, "generate", _always_fail)
    try:
        # Aktivan zadatak već postoji (iz bootstrapa), pa priprema kostura
        # koja padne ne prekida turn odmah — nastavlja se kao razgovor, TAČNO
        # JEDAN Tutor poziv se i dalje desi, a novi zadatak se NE izdaje
        # (prepared_skeleton je None) → fail closed poslije tog jednog poziva.
        fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
        response = run_practice_turn(store, fake, _turn(
            topic, grade, student_message="teže", difficulty_request="harder"))
    finally:
        monkeypatch.setattr(generator, "generate", original_generate)

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(f"lvl-{topic}") == before
    assert fake.call_count - calls_before == 1


def test_contract_lesson_switch_to_non_contract_lesson_resets_to_level_1(monkeypatch):
    _enable(monkeypatch)
    session_id = "lvl-contract-switch"
    store, fake = SessionStore(), FakeLLM()
    topic_a, grade_a, _ = CONTRACT_LESSON
    fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
    run_practice_turn(store, fake, dict(_turn(topic_a, grade_a), session_id=session_id))
    for _ in range(2):
        fake.queue(make_output(reply="Evo zadatka.", new_task=_next_task()))
        run_practice_turn(store, fake, dict(
            _turn(topic_a, grade_a, student_message="teže", difficulty_request="harder"),
            session_id=session_id,
        ))
    assert store.peek(session_id)["difficulty_level"] == 3

    topic_b, grade_b, _ = INTEGER_ADD
    queue_generation(fake, _next_task())
    run_practice_turn(store, fake, dict(_turn(topic_b, grade_b), session_id=session_id))
    assert store.peek(session_id)["difficulty_level"] == 1
