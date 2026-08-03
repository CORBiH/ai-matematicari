"""Recenzent vjernosti lekciji — kontradikcija 'approve uz oborene obavezne
provjere' (živi nalaz VPS):

    lesson_fidelity: odobreno uprkos oborenim provjerama:
    ['math_correct', 'tests_exact_lesson', 'answer_correct',
     'marked_option_correct']

Server je ispravno odbio (fail closed), ali oba poziva su potrošena bez
ijednog objavljenog zadatka iako je recenzent u istom odgovoru nosio potpun
`corrected_task`. Ovaj fajl dokazuje normalizacijsko pravilo:

  • `approve` uz sve obavezne provjere tačne -> objavljuje se nacrt;
  • `approve` uz oborenu obaveznu provjeru i BEZ `corrected_task` -> fail
    closed (postojeće ponašanje, nepromijenjeno);
  • `approve` uz oborenu obaveznu provjeru i SA kompletnim `corrected_task`
    -> odluka se preklapa u `correct`, a objava se dešava TEK kad ta zamjena
    prođe svu postojeću determinističku provjeru (schema, mathsafe, mathcheck,
    ugovor porodice, jedinstvenost opcija);
  • nepotpun `corrected_task` (bez obzira na odluku) -> fail closed;
  • nikad treći poziv, nikad mutacija sesije na odbijanje, nikad promjena
    izabrane lekcije.
"""
import copy

from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.topics import lesson_info
from tests.conftest import (FakeLLM, make_fidelity_review, make_options,
                            make_output, make_task, queue_generation)

INTEGER_ADD = ("7-02-008", 7, "Sabiranje cijelih brojeva različitih znakova")


def _turn(topic, grade, **changes):
    payload = {
        "session_id": f"rdc-{topic}", "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def _task(text, options, expected="", correct_index=0):
    return make_task(text=text, options=make_options(*options),
                     correct_option_index=correct_index,
                     expected=expected or options[correct_index])


# Prijavljene obavezne provjere iz VPS log-a — sve četiri oborene odjednom.
_PRODUCTION_FAILED_CHECKS = dict(
    math_correct=False, tests_exact_lesson=False,
    answer_correct=False, marked_option_correct=False,
)


def test_approve_with_all_hard_checks_true_publishes():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["status"] == "ready"
    assert "-7 + 12" in store.peek(f"rdc-{topic}")["current_task"]
    assert fake.call_count == 2


def test_approve_with_failed_hard_checks_and_no_corrected_task_fails_closed():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(
        fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")),
        review=make_fidelity_review(
            decision="approve", corrected_task=None, **_PRODUCTION_FAILED_CHECKS,
        ),
    )
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert fake.call_count == 2                       # nikad treći poziv
    assert store.peek(f"rdc-{topic}") is None          # bez mutacije stanja


def test_approve_with_failed_hard_checks_and_complete_corrected_task_is_normalized_and_publishes():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    corrected = _task("Izračunaj $-3 + 8$.", ("$5$", "$-5$", "$11$", "$-11$"))
    queue_generation(
        fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")),
        review=make_fidelity_review(
            decision="approve", corrected_task=corrected, **_PRODUCTION_FAILED_CHECKS,
        ),
    )
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["status"] == "ready"
    session = store.peek(f"rdc-{topic}")
    assert "-3 + 8" in session["current_task"]          # objavljena je ZAMJENA
    assert "-7 + 12" not in session["current_task"]      # ne originalni nacrt
    assert fake.call_count == 2                          # i dalje tačno dva poziva


def test_normalized_correction_still_fails_closed_when_it_fails_deterministic_validation():
    """Normalizacija NIJE bezuslovno objavljivanje: zamjenski zadatak i dalje
    mora proći POSTOJEĆU determinističku provjeru (ovdje: duple opcije)."""
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    corrected = _task("Izračunaj $-3 + 8$.", ("$5$", "$5$", "$11$", "$-11$"))
    queue_generation(
        fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")),
        review=make_fidelity_review(
            decision="approve", corrected_task=corrected, **_PRODUCTION_FAILED_CHECKS,
        ),
    )
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 2
    assert store.peek(f"rdc-{topic}") is None


def test_incomplete_corrected_final_fails_closed():
    """`correct` (ovdje IZVORNO, ne preklopljeno) bez zamjenskog zadatka je pad,
    bez obzira na sve ostalo."""
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(
        fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")),
        review=make_fidelity_review(decision="correct", corrected_task=None),
    )
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 2
    assert store.peek(f"rdc-{topic}") is None


def test_maximum_two_calls_on_every_path():
    topic, grade, _ = INTEGER_ADD
    for review in (
        make_fidelity_review(),  # approve, all true
        make_fidelity_review(decision="approve", corrected_task=None,
                             **_PRODUCTION_FAILED_CHECKS),
        make_fidelity_review(decision="approve",
                             corrected_task=_task("Izračunaj $-3 + 8$.",
                                                   ("$5$", "$-5$", "$11$", "$-11$")),
                             **_PRODUCTION_FAILED_CHECKS),
    ):
        store, fake = SessionStore(), FakeLLM()
        queue_generation(
            fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")),
            review=review,
        )
        run_practice_turn(store, fake, _turn(topic, grade))
        assert fake.call_count == 2


def test_rejection_never_mutates_session_state():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")))
    run_practice_turn(store, fake, _turn(topic, grade))
    before = copy.deepcopy(store.peek(f"rdc-{topic}"))

    queue_generation(
        fake, _task("Izračunaj $2+2$.", ("$4$", "$3$", "$5$", "$6$")),
        review=make_fidelity_review(decision="approve", corrected_task=None,
                                    **_PRODUCTION_FAILED_CHECKS),
    )
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi drugi zadatak."))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(f"rdc-{topic}") == before


def test_selected_lesson_cannot_change_even_when_normalized():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    corrected = _task("Izračunaj $-3 + 8$.", ("$5$", "$-5$", "$11$", "$-11$"))
    queue_generation(
        fake, _task("Izračunaj $-7 + 12$.", ("$5$", "$-5$", "$19$", "$-19$")),
        review=make_fidelity_review(
            decision="approve", corrected_task=corrected, **_PRODUCTION_FAILED_CHECKS,
        ),
    )
    response = run_practice_turn(store, fake, _turn(topic, grade))
    session = store.peek(f"rdc-{topic}")
    assert response["effective_topic"] == topic
    assert session["lesson_id"] == topic
    assert session["grade"] == grade
    assert session["oblast"] == lesson_info(grade, topic)["oblast"]


def test_resolve_reports_which_checks_failed_and_normalization_flag():
    """Jedinični test za lesson_fidelity.resolve() direktno — dokazuje da
    ResolvedReview.normalized_from_approve i dalje razlikuje čist 'correct'
    od preklopljenog 'approve'."""
    from matbot.lesson_fidelity import resolve

    corrected = _task("Izračunaj $-3 + 8$.", ("$5$", "$-5$", "$11$", "$-11$"))

    normalized = resolve(make_fidelity_review(
        decision="approve", corrected_task=corrected, **_PRODUCTION_FAILED_CHECKS))
    assert normalized.normalized_from_approve is True
    assert normalized.task is corrected

    genuine_correct = resolve(make_fidelity_review(
        decision="correct", corrected_task=corrected))
    assert genuine_correct.normalized_from_approve is False
    assert genuine_correct.task is corrected

    genuine_approve = resolve(make_fidelity_review(decision="approve"))
    assert genuine_approve.normalized_from_approve is False
    assert genuine_approve.task is None


def test_approve_with_failed_checks_and_no_corrected_task_raises_with_failed_checks_listed():
    import pytest

    from matbot.lesson_fidelity import FidelityRejected, resolve

    with pytest.raises(FidelityRejected) as excinfo:
        resolve(make_fidelity_review(decision="approve", corrected_task=None,
                                     **_PRODUCTION_FAILED_CHECKS))
    failed = set(excinfo.value.failed_checks)
    assert failed == set(_PRODUCTION_FAILED_CHECKS)
