"""JEDAN nagovjestaj po zadatku — produkcijsko ponasanje od ovog izdanja.

Ucenik trazi pomoc jednom i dobija JEDAN koristan strateski nagovjestaj.
Ponovni klik vraca ISTI tekst i ne trosi novi poziv. „Uradi ga ti“ ostaje
zasebna radnja koja i dalje daje puno rjesenje.
"""
import pytest

from matbot import config
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import pipeline as tutor_pipeline
from tests.conftest import (FakeLLM, make_task_payload, make_tutor_draft,
                            make_reviewer_checks, make_reviewer_final)

LESSON = ("6-04-001", 6)          # opsta modelska lekcija


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    monkeypatch.delenv("MATBOT_PRACTICE_SINGLE_HINT", raising=False)


def _turn(sid, message, intent="", request=""):
    return {"session_id": sid, "grade": LESSON[1], "selected_topic": LESSON[0],
            "selected_oblast": "", "student_message": message, "intent": intent,
            "difficulty_request": request, "interaction_phase": "",
            "last_tutor_task": "", "interaction_type": "student_question",
            "selected_option_id": "", "client_turn_id": ""}


HINT_TEXT = ("Prvo saberi cijene svih proizvoda da dobiješ ukupan iznos. "
             "Zatim taj zbir oduzmi od iznosa kojim je plaćeno.")


def _task(text="Koliki je kusur ako je plaćeno $20,00$ KM?",
          options=("$9,50$ KM", "$10,50$ KM", "$11,50$ KM", "$8,00$ KM"),
          solution="Ukupno $10,50$ KM, pa je kusur $20,00 - 10,50 = 9,50$ KM.",
          family="fixture"):
    """`family` mijenja POTPIS zadatka — objava odbija dva ista potpisa."""
    from matbot.tutor.schema import TaskSignature

    payload = make_task_payload(
        text=text, options=list(options), correct_option_index=0,
        expected=options[0], solution=solution)
    return payload.model_copy(update={
        "selected_lesson_id": LESSON[0],
        "selected_lesson_title": "Pojam razlomka",
        "task_signature": TaskSignature(
            task_family=family, operation_or_relation=f"{family}_operation",
            normalized_parameters=[], required_conditions=[], relevant_objects=[],
            answer_type="multiple_choice"),
    })


def _publish(store, fake, sid, message="Daj mi zadatak.", request="", task=None):
    fake.queue(make_tutor_draft(intent="generate_task", reply="Evo zadatka.",
                                lesson_focus="razlomci", new_task=task or _task()))
    fake.queue(make_reviewer_final(decision="approve", checks=make_reviewer_checks()))
    run_practice_turn(store, fake, _turn(sid, message, request=request))


def _session_with_task(store, fake, sid):
    _publish(store, fake, sid)


def test_single_hint_is_served_once_and_repeats_without_a_new_call(production):
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "h1")
    calls_after_task = fake.call_count

    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo nagovještaja.",
                                hint=HINT_TEXT, lesson_focus="razlomci"))
    first = run_practice_turn(store, fake, _turn("h1", "Ne znam.", intent="hint_request"))
    calls_after_first = fake.call_count
    assert calls_after_first == calls_after_task + 1        # tacno jedan poziv

    # PONOVLJEN KLIK: isti tekst, NULA novih poziva.
    second = run_practice_turn(store, fake, _turn("h1", "Ne znam.", intent="hint_request"))
    assert fake.call_count == calls_after_first
    assert second["answer"] == first["answer"]
    assert second["status"] == "ready"

    # I treci klik — i dalje isti tekst, i dalje bez poziva.
    third = run_practice_turn(store, fake, _turn("h1", "Ne znam.", intent="hint_request"))
    assert fake.call_count == calls_after_first
    assert third["answer"] == first["answer"]


def test_repeated_hint_never_deepens_or_reaches_the_ladder_top(production):
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "h2")
    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo nagovještaja.",
                                hint=HINT_TEXT, lesson_focus="razlomci"))
    run_practice_turn(store, fake, _turn("h2", "Ne znam.", intent="hint_request"))
    for _ in range(4):
        run_practice_turn(store, fake, _turn("h2", "Ne znam.", intent="hint_request"))
    session = store.peek("h2")
    # Nivo ne raste ponavljanjem, pa vrh ljestvice (koji otkriva rezultat)
    # ponovljenim klikom nije dostizan.
    assert session["hint_level"] < config.MAX_HINT_LEVEL


def test_hint_never_reveals_the_marked_option(production):
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "h3")
    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo nagovještaja.",
                                hint=HINT_TEXT, lesson_focus="razlomci"))
    answer = run_practice_turn(
        store, fake, _turn("h3", "Ne znam.", intent="hint_request"))["answer"]
    assert "9,50" not in answer
    repeat = run_practice_turn(
        store, fake, _turn("h3", "Ne znam.", intent="hint_request"))["answer"]
    assert "9,50" not in repeat


@pytest.mark.parametrize("message,request_type", [
    ("Daj mi novi zadatak.", ""),
    ("Daj mi teži zadatak.", "harder"),
    ("Daj mi lakši zadatak.", "easier"),
])
def test_a_new_task_clears_the_stored_hint(message, request_type, production):
    """NOV zadatak — nov nagovjestaj: pohranjeni tekst pripada proslom zadatku."""
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "h4")
    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo nagovještaja.",
                                hint=HINT_TEXT, lesson_focus="razlomci"))
    run_practice_turn(store, fake, _turn("h4", "Ne znam.", intent="hint_request"))
    assert tutor_pipeline.stored_hint_for_active_task(store.peek("h4"))

    _publish(store, fake, "h4", message=message, request=request_type,
             task=_task(text="Koliko iznosi razlika u KM?",
                        options=("$3$ KM", "$4$ KM", "$5$ KM", "$6$ KM"),
                        solution="Razlika je $8 - 5 = 3$ KM.", family="druga"))
    assert not tutor_pipeline.stored_hint_for_active_task(store.peek("h4"))

    # Poslije novog zadatka nagovjestaj se PONOVO trazi od modela.
    calls_before = fake.call_count
    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo drugog.",
                                hint="Drugi nagovještaj.", lesson_focus="razlomci"))
    run_practice_turn(store, fake, _turn("h4", "Ne znam.", intent="hint_request"))
    assert fake.call_count == calls_before + 1


def test_full_solution_action_still_works_after_a_hint(production):
    """„Uradi ga ti“ ostaje zasebna radnja i i dalje daje puno rjesenje."""
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "h5")
    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo nagovještaja.",
                                hint=HINT_TEXT, lesson_focus="razlomci"))
    run_practice_turn(store, fake, _turn("h5", "Ne znam.", intent="hint_request"))
    response = run_practice_turn(
        store, fake, _turn("h5", "Uradi ga ti.", intent="solution_request"))
    assert response["status"] == "ready"
    assert response.get("revealed_correct_option_id")


def test_stored_hint_belongs_to_its_own_task_text(production):
    """Otisak je TEKST zadatka: nagovjestaj se nikad ne sluzi drugom zadatku."""
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "h6")
    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo nagovještaja.",
                                hint=HINT_TEXT, lesson_focus="razlomci"))
    run_practice_turn(store, fake, _turn("h6", "Ne znam.", intent="hint_request"))
    session = store.peek("h6")
    session["current_task"] = "Neki sasvim drugi zadatak?"
    assert tutor_pipeline.stored_hint_for_active_task(session) == ""


def test_rollback_flag_restores_the_progressive_ladder(monkeypatch, production):
    monkeypatch.setenv("MATBOT_PRACTICE_SINGLE_HINT", "disabled")
    store, fake = SessionStore(), FakeLLM()
    _session_with_task(store, fake, "h7")
    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo nagovještaja.",
                                hint=HINT_TEXT, lesson_focus="razlomci"))
    run_practice_turn(store, fake, _turn("h7", "Ne znam.", intent="hint_request"))
    calls_before = fake.call_count
    fake.queue(make_tutor_draft(intent="hint_request", reply="Evo drugog.",
                                hint="Drugi nivo.", lesson_focus="razlomci"))
    run_practice_turn(store, fake, _turn("h7", "Ne znam.", intent="hint_request"))
    assert fake.call_count == calls_before + 1        # ljestvica opet trosi poziv


def test_server_composed_hint_also_repeats_without_deepening(production, monkeypatch):
    """ZIVI NALAZ (ciljani QA): propozicioni nagovjestaj je serverski i vraca se
    RANIJE od modelske grane, pa je ponovni klik i dalje isao na sljedeci nivo —
    bez ijednog poziva, ali s NOVIM (dubljim) tekstom."""
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "enabled")
    store, fake = SessionStore(), FakeLLM()
    lesson = ("6-01-001", 6)          # determinsticka lekcija, 0 poziva
    payload = {"session_id": "sh", "grade": lesson[1], "selected_topic": lesson[0],
               "selected_oblast": "", "student_message": "Daj mi zadatak.",
               "intent": "", "difficulty_request": "", "interaction_phase": "",
               "last_tutor_task": "", "interaction_type": "student_question",
               "selected_option_id": "", "client_turn_id": ""}
    run_practice_turn(store, fake, payload)
    assert store.peek("sh")["current_task"]

    hint_payload = dict(payload, student_message="Ne znam.", intent="hint_request")
    first = run_practice_turn(store, fake, hint_payload)
    second = run_practice_turn(store, fake, dict(hint_payload))
    assert fake.call_count == 0                       # serverski, bez poziva
    assert second["answer"] == first["answer"]        # ISTI tekst, ne dublji
    assert store.peek("sh")["hint_level"] < config.MAX_HINT_LEVEL
