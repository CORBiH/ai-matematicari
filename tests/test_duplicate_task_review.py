"""Recenzent vjernosti lekciji — ponavljanje teksta zadatka u istoj sesiji
(živi nalaz VPS):

    practice_turn category=invalid_output
    detail=ponovljen tekst zadatka u istoj sesiji

Zadatak je bio odbijen TEK NAKON Tutora i recenzenta jer je doslovno ponovio
raniji tekst iz iste sesije — oba poziva potrošena, učenik nije dobio ništa,
iako je recenzent mogao ispraviti da je unaprijed znao za sudar. Ovaj fajl
dokazuje popravku:

  • sirov Tutorov nacrt se provjerava PRIJE recenzenta (bez odbijanja turna
    tu — samo signal za recenzenta) — vidi practice._duplicate_precheck;
  • recenzent dobija TAČAN razlog i tekst koji mora izbjeći;
  • popravka sa stvarno drugačijim brojevima se objavljuje;
  • sitne razlike u interpunkciji/razmacima i dalje broje kao isti zadatak —
    postojeća zaštita (task_families.is_duplicate_signature) se NE slabi;
  • konačna provjera se PONAVLJA nad ispravljenim zadatkom — i dalje ga
    odbija ako je matematički identičan originalu;
  • nikad treći poziv, nikad mutacija sesije na odbijanje.
"""
import copy

from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_fidelity_review, make_options,
                            make_output, make_task, queue_generation)

INTEGER_ADD = ("7-02-008", 7, "Sabiranje cijelih brojeva različitih znakova")

ORIGINAL_TEXT = "Izračunaj $-7 + 12$."
ORIGINAL_OPTIONS = ("$5$", "$-5$", "$19$", "$-19$")


def _turn(topic, grade, **changes):
    payload = {
        "session_id": f"dup-{topic}", "grade": grade, "selected_topic": topic,
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


def _bootstrap(store, fake, topic, grade):
    """Prvi turn: uspostavi originalan zadatak u sesiji — priprema koliziju
    za DRUGI turn, koji je stvarno ono što svaki test ispod ispituje."""
    queue_generation(fake, _task(ORIGINAL_TEXT, ORIGINAL_OPTIONS))
    response = run_practice_turn(store, fake, _turn(topic, grade))
    assert response["status"] == "ready"
    return response


def test_duplicate_tutor_draft_reaches_the_reviewer():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake, topic, grade)
    calls_before = fake.call_count
    fidelity_calls_before = len(fake.fidelity_calls)

    duplicate_draft = _task(ORIGINAL_TEXT, ORIGINAL_OPTIONS)
    fake.queue(make_output(reply="Evo zadatka.", new_task=duplicate_draft))
    fake.queue(make_fidelity_review())  # approve, nacrt neizmijenjen
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi drugi zadatak."))

    assert len(fake.fidelity_calls) == fidelity_calls_before + 1  # recenzent JE pozvan
    reviewer_input = fake.fidelity_calls[-1][1]
    assert "DETERMINISTIČKA PROVJERA PONAVLJANJA JE ODBILA NACRT" in reviewer_input
    assert "ponovljen tekst zadatka u istoj sesiji" in reviewer_input
    assert ORIGINAL_TEXT in reviewer_input
    # 'approve' nad neizmijenjenim duplikatom i dalje pada na determinističkoj
    # provjeri POSLIJE recenzenta — dolazak do recenzenta nije isto što i prolaz.
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count - calls_before == 2


def test_reviewer_correction_with_different_numbers_publishes():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake, topic, grade)
    calls_before = fake.call_count

    duplicate_draft = _task(ORIGINAL_TEXT, ORIGINAL_OPTIONS)
    # NAMJERNO drugačija brojevi I formulacija (ne samo brojevi) — inače isti
    # pedagoški oblik ("<num> + <num>") sudara se sa signaturom prve porodice
    # kroz vlastitu, zasebnu zaštitu (is_duplicate_shape), koja se ovim
    # fixom NAMJERNO ne slabi (pravilo 6).
    corrected = _task("Koliko je zbir brojeva $-3$ i $8$?",
                      ("$5$", "$-5$", "$11$", "$-11$"))
    fake.queue(make_output(reply="Evo zadatka.", new_task=duplicate_draft))
    fake.queue(make_fidelity_review(decision="correct", corrected_task=corrected))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi drugi zadatak."))

    assert response["status"] == "ready"
    session = store.peek(f"dup-{topic}")
    assert "zbir brojeva" in session["current_task"]
    assert "-7 + 12" not in session["current_task"]
    assert fake.call_count - calls_before == 2


def test_trivial_punctuation_or_whitespace_changes_still_treated_as_duplicate():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake, topic, grade)
    calls_before = fake.call_count

    trivial_variant = _task("izračunaj   $-7 + 12$", ORIGINAL_OPTIONS)  # samo velika/mala slova i razmaci
    fake.queue(make_output(reply="Evo zadatka.", new_task=trivial_variant))
    fake.queue(make_fidelity_review())  # recenzent odobrava BEZ ispravke
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi drugi zadatak."))

    reviewer_input = fake.fidelity_calls[-1][1]
    assert "DETERMINISTIČKA PROVJERA PONAVLJANJA JE ODBILA NACRT" in reviewer_input
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count - calls_before == 2
    assert store.peek(f"dup-{topic}")["current_task"] == ORIGINAL_TEXT  # bez mutacije


def test_corrected_task_identical_in_mathematical_content_is_rejected():
    """Recenzent vrati `correct`, ali zamjenski zadatak je SAMO površinski
    drugačiji (ista normalizovana matematika/tekst) — postojeća politika
    ponavljanja ga i dalje odbija POSLIJE recenzenta."""
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake, topic, grade)
    calls_before = fake.call_count

    duplicate_draft = _task(ORIGINAL_TEXT, ORIGINAL_OPTIONS)
    superficial_fix = _task("Izračunaj, $-7 + 12$", ORIGINAL_OPTIONS)  # ista matematika
    fake.queue(make_output(reply="Evo zadatka.", new_task=duplicate_draft))
    fake.queue(make_fidelity_review(decision="correct", corrected_task=superficial_fix))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi drugi zadatak."))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count - calls_before == 2
    assert store.peek(f"dup-{topic}")["current_task"] == ORIGINAL_TEXT  # bez mutacije


def test_maximum_two_calls_for_the_colliding_turn_on_every_path():
    topic, grade, _ = INTEGER_ADD
    reviews = (
        make_fidelity_review(),  # approve, i dalje duplikat
        make_fidelity_review(
            decision="correct",
            corrected_task=_task("Koliko je zbir brojeva $-3$ i $8$?",
                                 ("$5$", "$-5$", "$11$", "$-11$")),
        ),
    )
    for review in reviews:
        store, fake = SessionStore(), FakeLLM()
        _bootstrap(store, fake, topic, grade)
        calls_before = fake.call_count
        fake.queue(make_output(reply="Evo zadatka.",
                               new_task=_task(ORIGINAL_TEXT, ORIGINAL_OPTIONS)))
        fake.queue(review)
        run_practice_turn(store, fake, _turn(
            topic, grade, student_message="Daj mi drugi zadatak."))
        assert fake.call_count - calls_before == 2  # nikad treći poziv


def test_rejection_never_mutates_session_state():
    topic, grade, _ = INTEGER_ADD
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake, topic, grade)
    before = copy.deepcopy(store.peek(f"dup-{topic}"))

    fake.queue(make_output(reply="Evo zadatka.",
                           new_task=_task(ORIGINAL_TEXT, ORIGINAL_OPTIONS)))
    fake.queue(make_fidelity_review())
    response = run_practice_turn(store, fake, _turn(
        topic, grade, student_message="Daj mi drugi zadatak."))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(f"dup-{topic}") == before
