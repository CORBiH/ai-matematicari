"""Server-owned introductions for newly generated Practice tasks."""

import copy

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_options, make_output, make_task, make_task_for_family


TOPIC = "6-04-007"
QUESTION = "Proširi razlomak $\\frac{7}{10}$ tako da nazivnik bude $50$."
GUIDED_REPLY = (
    "Evo zadatka. Prvo pronađi broj kojim treba pomnožiti nazivnik 10 da dobije 50."
)


def payload(session_id="intro-session", message="Daj zadatak.", **updates):
    value = {
        "session_id": session_id,
        "grade": 6,
        "selected_topic": TOPIC,
        "selected_oblast": "",
        "student_message": message,
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "interaction_type": "",
        "selected_option_id": "",
        "client_turn_id": "",
    }
    value.update(updates)
    return value


def fraction_task(difficulty="standard"):
    return make_task(
        text=QUESTION,
        expected="$\\frac{35}{50}$",
        difficulty=difficulty,
        options=make_options(
            "$\\frac{35}{50}$", "$\\frac{7}{50}$",
            "$\\frac{14}{50}$", "$\\frac{70}{50}$",
        ),
        correct_option_index=0,
        task_family="expand_to_given_denominator",
    )


def generate(store, fake, *, reply=GUIDED_REPLY, difficulty_request="", difficulty="standard",
             session_id="intro-session"):
    fake.queue(make_output(reply=reply, new_task=fraction_task(difficulty)))
    before_calls = fake.practice_call_count
    response = run_practice_turn(
        store,
        fake,
        payload(
            session_id=session_id,
            message="Daj zadatak.",
            difficulty_request=difficulty_request,
        ),
    )
    assert fake.practice_call_count == before_calls + 1
    return response


def test_model_hint_is_replaced_by_normal_intro_and_question_is_unchanged():
    store, fake = SessionStore(), FakeLLM()

    response = generate(store, fake)

    assert response["answer"] == f"Evo zadatka.\n\nZadatak: {QUESTION}"
    assert response["last_tutor_task"] == QUESTION
    assert response["next_state"]["task"]["question"] == QUESTION
    assert response["next_state"]["hint_level"] == 0
    intro = response["answer"].split("\n\nZadatak:", 1)[0]
    assert intro == "Evo zadatka."
    assert "Prvo pronađi" not in response["answer"]


def test_harder_task_uses_server_owned_harder_intro():
    store, fake = SessionStore(), FakeLLM()
    response = generate(
        store, fake, difficulty_request="harder", difficulty="hard",
        reply="Najprije podijeli 50 sa 10.",
    )
    assert response["answer"] == f"Evo težeg zadatka.\n\nZadatak: {QUESTION}"
    assert store.peek("intro-session")["difficulty"] == "hard"


def test_easier_task_uses_server_owned_easier_intro():
    store, fake = SessionStore(), FakeLLM()
    response = generate(
        store, fake, difficulty_request="easier", difficulty="easy",
        reply="Pomnoži brojnik i nazivnik istim brojem.",
    )
    assert response["answer"] == f"Evo lakšeg zadatka.\n\nZadatak: {QUESTION}"
    assert store.peek("intro-session")["difficulty"] == "easy"


def test_wrong_answer_keeps_one_hint_and_retry_gets_same_skill_intro():
    store, fake = SessionStore(), FakeLLM()
    generate(store, fake)
    active = store.peek("intro-session")
    wrong_id = next(
        option["id"] for option in active["current_options"]
        if option["id"] != active["correct_option_id"]
    )

    fake.queue(make_output(reply="Duga analiza.", hint="Provjeri odnos nazivnika."))
    before_wrong_calls = fake.practice_call_count
    wrong = run_practice_turn(
        store,
        fake,
        payload(
            message="[klik]",
            interaction_type="choice_answer",
            selected_option_id=wrong_id,
            client_turn_id="intro-wrong-1",
        ),
    )
    assert fake.practice_call_count == before_wrong_calls + 1
    assert wrong["answer"].startswith("Netačno.\n\nHint: ")
    assert wrong["answer"].count("Hint:") == 1
    assert store.peek("intro-session")["retry_required"] is True

    family = store.peek("intro-session")["current_family"]
    fake.queue(make_output(
        reply="Prvo izračunaj faktor proširivanja.",
        new_task=make_task_for_family(family, suffix=" (novi brojevi)"),
    ))
    before_retry_calls = fake.practice_call_count
    retry = run_practice_turn(store, fake, payload(message="Daj novi zadatak."))
    assert fake.practice_call_count == before_retry_calls + 1
    assert retry["answer"].startswith("Evo novog zadatka za istu vještinu.\n\nZadatak: ")
    assert "Prvo izračunaj" not in retry["answer"]
    assert retry["next_state"]["hint_level"] == 0


def test_explicit_hint_still_increases_hint_level_normally():
    store, fake = SessionStore(), FakeLLM()
    generate(store, fake)
    fake.queue(make_output(
        reply="Pogledaj kojim faktorom se nazivnik 10 pretvara u 50.",
        gave_hint=True,
    ))
    before_calls = fake.practice_call_count
    response = run_practice_turn(
        store,
        fake,
        payload(message="Ne znam — daj mi hint", intent="hint_request"),
    )
    assert fake.practice_call_count == before_calls + 1
    assert response["next_state"]["hint_level"] == 1
    assert "Pogledaj kojim faktorom" in response["answer"]


def test_correct_answer_explanation_is_unchanged():
    store, fake = SessionStore(), FakeLLM()
    generate(store, fake)
    active = store.peek("intro-session")
    explanation = "Tačno. Pomnožili smo brojnik i nazivnik sa 5."
    fake.queue(make_output(reply=explanation, evaluation="correct"))
    before_calls = fake.practice_call_count
    response = run_practice_turn(
        store,
        fake,
        payload(
            message="[klik]",
            interaction_type="choice_answer",
            selected_option_id=active["correct_option_id"],
            client_turn_id="intro-correct-1",
        ),
    )
    assert fake.practice_call_count == before_calls + 1
    assert response["answer"] == explanation
    assert response["answer_verdict"] == "correct"


def test_rejected_generation_preserves_state_and_uses_one_call():
    store, fake = SessionStore(), FakeLLM()
    generate(store, fake)
    before = store.peek("intro-session")
    broken_task = make_task(
        text="Izračunaj $\\frac{3}{24$.",
        expected="1/8",
        options=make_options("1/8", "1/4", "3/8", "8"),
    )
    fake.queue(make_output(reply="Prvo podijeli 24 sa 3.", new_task=broken_task))
    before_calls = fake.practice_call_count

    response = run_practice_turn(store, fake, payload(message="Daj novi zadatak."))

    assert fake.practice_call_count == before_calls + 1
    assert "status" not in response
    assert store.peek("intro-session") == copy.deepcopy(before)
