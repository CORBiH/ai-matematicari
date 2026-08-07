r"""Izričit „novi zadatak“ mora dati STVARNO drugačiji zadatak.

PRODUKCIJSKI NALAZ (lekcija 6-03-004):

    objavljeno : „Koji od sljedećih brojeva je djeljiv sa 25?“
                 opcije 322 · 390 · 349 · 375   (tačno: 375)
    učenik     : klikne 390 → crveno, ocijenjeno netačno — SVE ISPRAVNO
    učenik     : „Daj mi novi zadatak.“
    objavljeno : ISTI zadatak, ISTE četiri opcije
    a jednom i : „Evo lakšeg zadatka.“ uz istu matematiku

UZROK: zaštita od ponavljanja poredi `task.task_signature` — strukturu koju
MODEL sam deklariše o sebi. Kad model za vizuelno identičan zadatak deklariše
makar malo drugačije `normalized_parameters`, digest se razlikuje i provjera
propušta duplikat. Server nikad nije poredio ono što učenik STVARNO VIDI.

Popravka je serverski izveden kanonski potpis vidljivog paketa (tekst zadatka +
vrijednosti opcija), nezavisan od svega što model deklariše.

GRANICA (izričita): ovo hvata identičan tekst, promijenjen redoslijed opcija,
promijenjene ID-jeve uz iste vrijednosti i kozmetiku (razmaci, veličina slova,
`$…$`, interpunkcija). Puni parafrazni identitet nije odlučiv i ovdje se NE
tvrdi da je pokriven.
"""
import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import task_identity
from matbot.tutor.schema import UnifiedOutputError
from tests.conftest import (FakeLLM, make_task_payload, make_tutor_draft,
                            queue_two_call)

LESSON, GRADE = "6-03-004", 6

TASK = "Koji od sljedećih brojeva je djeljiv sa 25?"
OPTIONS = ("322", "390", "349", "375")
CORRECT = 3                                   # 375

OTHER_TASK = "Koji od sljedećih brojeva je djeljiv sa 10?"
OTHER_OPTIONS = ("41", "70", "33", "58")


@pytest.fixture(autouse=True)
def _universal_runtime(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(session_id, message, **changes):
    turn = {
        "session_id": session_id, "grade": GRADE, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    turn.update(changes)
    return turn


def _payload(text=TASK, options=OPTIONS, marked=CORRECT):
    return make_task_payload(text=text, options=options,
                             correct_option_index=marked, expected=options[marked])


def _publish_first(store, fake, session_id):
    queue_two_call(fake, draft=make_tutor_draft(intent="generate_task", new_task=_payload()))
    response = run_practice_turn(store, fake, _turn(session_id, "Daj mi zadatak."))
    assert response["status"] == "ready"
    return response


# ---------------------------------------------------------------------------
# 1. KANONSKI POTPIS — ŠTA JE ISTI ZADATAK
# ---------------------------------------------------------------------------

def _sig(text, options):
    return task_identity.canonical_signature(text, options)


def test_identical_package_has_the_same_signature():
    assert _sig(TASK, OPTIONS) == _sig(TASK, OPTIONS)


def test_reordered_options_are_the_same_task():
    assert _sig(TASK, ("375", "322", "349", "390")) == _sig(TASK, OPTIONS)


def test_cosmetic_text_differences_are_the_same_task():
    for variant in (
        "koji od sljedećih brojeva je djeljiv sa 25?",
        "Koji od sljedećih  brojeva  je djeljiv sa 25 ?",
        "Koji od sljedećih brojeva je djeljiv sa $25$?",
    ):
        assert _sig(variant, OPTIONS) == _sig(TASK, OPTIONS), variant


def test_math_wrapped_options_are_the_same_task():
    assert _sig(TASK, ("$322$", "$390$", "$349$", "$375$")) == _sig(TASK, OPTIONS)


def test_a_different_divisor_is_a_different_task():
    assert _sig("Koji od sljedećih brojeva je djeljiv sa 5?", OPTIONS) != _sig(TASK, OPTIONS)


def test_different_option_values_are_a_different_task():
    assert _sig(TASK, ("322", "390", "349", "350")) != _sig(TASK, OPTIONS)


def test_signature_is_independent_of_option_ids():
    """ID-jevi su serverska prezentacija — nikad dio matematičkog identiteta."""
    assert _sig(TASK, OPTIONS) == _sig(TASK, list(OPTIONS))


# ---------------------------------------------------------------------------
# 2. SERVER NE VJERUJE MODELOVOM POTPISU
# ---------------------------------------------------------------------------

def test_duplicate_is_caught_even_when_the_model_declares_a_new_signature():
    store, fake = SessionStore(), FakeLLM()
    _publish_first(store, fake, "dup-1")
    before = dict(store.peek("dup-1"))

    duplicate = _payload()
    duplicate = duplicate.model_copy(update={
        "task_signature": duplicate.task_signature.model_copy(update={
            "operation_or_relation": "potpuno druga deklaracija",
            "required_conditions": ["izmišljen uslov"],
        }),
    })
    queue_two_call(fake, draft=make_tutor_draft(intent="next_task", new_task=duplicate))
    response = run_practice_turn(store, fake, _turn("dup-1", "Daj mi novi zadatak."))

    assert "status" not in response, "duplikat je objavljen"
    after = store.peek("dup-1")
    assert after["current_task"] == before["current_task"]
    assert after["current_options"] == before["current_options"]
    assert after["correct_option_id"] == before["correct_option_id"]
    assert fake.call_count <= 4


def test_reordered_option_duplicate_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    _publish_first(store, fake, "dup-2")
    reordered = _payload(options=("375", "322", "349", "390"), marked=0)
    queue_two_call(fake, draft=make_tutor_draft(intent="next_task", new_task=reordered))
    response = run_practice_turn(store, fake, _turn("dup-2", "Daj mi novi zadatak."))
    assert "status" not in response


def test_cosmetic_reword_duplicate_is_rejected():
    store, fake = SessionStore(), FakeLLM()
    _publish_first(store, fake, "dup-3")
    cosmetic = _payload(text="Koji od sljedećih  brojeva je djeljiv sa $25$ ?")
    queue_two_call(fake, draft=make_tutor_draft(intent="next_task", new_task=cosmetic))
    response = run_practice_turn(store, fake, _turn("dup-3", "Daj mi novi zadatak."))
    assert "status" not in response


def test_a_genuinely_different_task_publishes():
    store, fake = SessionStore(), FakeLLM()
    _publish_first(store, fake, "dup-4")
    queue_two_call(fake, draft=make_tutor_draft(
        intent="next_task", new_task=_payload(text=OTHER_TASK, options=OTHER_OPTIONS, marked=1)))
    response = run_practice_turn(store, fake, _turn("dup-4", "Daj mi novi zadatak."))

    assert response["status"] == "ready"
    session = store.peek("dup-4")
    assert session["current_task"] == OTHER_TASK
    assert session["current_task_identity"] == _sig(OTHER_TASK, OTHER_OPTIONS)


# ---------------------------------------------------------------------------
# 3. PREFLIGHT, RECENZENT I OBJAVA
# ---------------------------------------------------------------------------

def test_preflight_reports_a_duplicate_of_the_active_task():
    issues = package_preflight.collect_package_issues(
        _payload(), previous_signature=_sig(TASK, OPTIONS))
    assert "duplicate_active_task" in package_preflight.describe_issues(issues)


def test_preflight_stays_silent_for_a_different_task():
    issues = package_preflight.collect_package_issues(
        _payload(text=OTHER_TASK, options=OTHER_OPTIONS, marked=1),
        previous_signature=_sig(TASK, OPTIONS))
    assert "duplicate_active_task" not in package_preflight.describe_issues(issues)


def test_reviewer_block_explains_how_to_replace_a_duplicate():
    block = package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(
            _payload(), previous_signature=_sig(TASK, OPTIONS)))
    assert "duplicate_active_task" in block
    assert "different" in block.lower()


def test_publication_rechecks_the_invariant_immediately_before_mutation():
    context = lesson_context_module.build(GRADE, LESSON)
    with pytest.raises(UnifiedOutputError) as error:
        tutor_pipeline._validate_task_server_side(
            _payload(), context, previous_signature=_sig(TASK, OPTIONS))
    assert "duplicate_active_task" in str(error.value)


def test_reviewer_may_correct_a_duplicate_into_a_different_task():
    """Ispravka u ISTOM drugom pozivu — bez trećeg poziva."""
    store, fake = SessionStore(), FakeLLM()
    _publish_first(store, fake, "dup-5")
    calls_before = fake.call_count

    duplicate_draft = make_tutor_draft(intent="next_task", new_task=_payload())
    corrected = make_tutor_draft(
        intent="next_task",
        new_task=_payload(text=OTHER_TASK, options=OTHER_OPTIONS, marked=1))
    from tests.conftest import make_reviewer_final
    fake.queue(duplicate_draft)
    fake.queue(make_reviewer_final(decision="correct", final=corrected))
    response = run_practice_turn(store, fake, _turn("dup-5", "Daj mi novi zadatak."))

    assert response["status"] == "ready"
    assert store.peek("dup-5")["current_task"] == OTHER_TASK
    assert fake.call_count - calls_before == 2


def test_signature_is_exposed_to_the_client_for_the_published_task():
    store, fake = SessionStore(), FakeLLM()
    response = _publish_first(store, fake, "dup-6")
    task_state = response["next_state"]["task"]
    assert task_state["identity"] == _sig(TASK, OPTIONS)


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: ovi testovi ispituju MODEL-strategiju (Tutor +
# Recenzent) i na lekcijama koje produkcija sada rutira deterministički
# (blocking ugovor + potpun generator). Izričito isključenje je ISTI mehanizam
# koji služi i kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=
# disabled) — model-put time ostaje trajno testiran, bajt za bajt kakav je bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
