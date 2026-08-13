"""PHASE A — pomoćni tekstovi paketa prolaze ISTU kapiju kao objavljen zadatak.

ŽIVI NALAZ (audit ovlašćenja pravila, 2026-08-09): `DeterministicPackage.hints`
nikad nisu bili dio `TaskPayload`-a, pa ih objavna validacija nije ni vidjela.
U sesiju je išao SIROV string generatora, a help-turn ga je slao učeniku
doslovno — bez mathsafe provjere, bez normalizacije terminologije, bez ijednog
validatora. Rješenje je bilo objavno provjereno, ali se u dodatak upisivala
PRETHODNA (sirova) kopija, pa je objava mogla prihvatiti jedan tekst, a učenik
kasnije dobiti drugi. Povratna poruka na klik je sirov `display_answer` slijepo
umotavala u `$…$`.

Ovi testovi zaključavaju tri stvari: (1) sve što se pohrani je već prošlo
serversku kapiju, (2) objavljena i poslata kopija su ISTI tekst, (3) tekst koji
kapiju ne prođe obara KANDIDATA prije objave — stanje ostaje netaknuto, i to bez
ijednog poziva modela.
"""
import dataclasses
import logging



import pytest

from matbot import terminology
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import pipeline
from tests.conftest import FakeLLM

GRADE, LESSON, SESSION = 6, "6-04-009", "det-help-safe"

# Zabranjeni oblik se IZVODI iz samog normalizatora u vrijeme izvođenja:
# doslovan zapis u ovom fajlu pao bi na repo-skenu iz tests/test_terminology.py.
CROATIAN_FORM = next(term for term in terminology._TRIGGER_SUBSTRINGS
                     if terminology.normalize_terminology(term) != term)
BOSNIAN_FORM = terminology.normalize_terminology(CROATIAN_FORM)

UNSAFE_HINT = r"Pogledaj $\ty{5}$ pa nastavi."


def turn(message="Daj mi jedan zadatak za vježbu iz ove teme.", lesson=LESSON,
         **changes):
    payload = {
        "session_id": SESSION, "grade": GRADE, "selected_topic": lesson,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


@pytest.fixture
def universal(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


class _TransformedGenerator:
    """Pravi generator porodice, ali s izmijenjenim pomoćnim tekstovima.

    Ruta, parametri i sam zadatak ostaju netaknuti — mijenja se ISKLJUČIVO ono
    što ovaj phase štiti."""

    def __init__(self, real, transform):
        self._real, self._transform = real, transform

    def supports(self, parameters):
        return self._real.supports(parameters)

    def generate_package(self, **kwargs):
        return self._transform(self._real.generate_package(**kwargs))


def patch_help_texts(monkeypatch, transform, lesson=LESSON, grade=GRADE):
    family = lesson_context_module.build(grade, lesson).semantic_contract.family_id
    real = pipeline._DETERMINISTIC_GENERATORS[family]
    monkeypatch.setitem(pipeline._DETERMINISTIC_GENERATORS, family,
                        _TransformedGenerator(real, transform))


def with_hints(*hints):
    return lambda package: dataclasses.replace(package, hints=tuple(hints))


def append_to_help(text):
    def transform(package):
        return dataclasses.replace(
            package,
            hints=tuple(f"{hint} {text}" for hint in package.hints),
            solution=f"{package.solution} {text}")
    return transform


def publish(store, fake, lesson=LESSON):
    response = run_practice_turn(store, fake, turn(lesson=lesson))
    assert response["status"] == "ready" and fake.call_count == 0
    return store.peek(SESSION)


def hint_turn(turn_id, lesson=LESSON):
    return turn(message="Ne znam.", intent="hint_request", lesson=lesson,
                interaction_phase="practice_help", client_turn_id=turn_id)


def solution_turn(turn_id, lesson=LESSON):
    return turn(message="Uradi ga ti.", intent="solution_request", lesson=lesson,
                interaction_phase="practice_help", client_turn_id=turn_id)


# ---------------------------------------------------------------------------
# 1) SANITIZACIJA PRIJE POHRANE
# ---------------------------------------------------------------------------

def test_stored_hints_are_sanitized_before_they_reach_the_session(
        universal, monkeypatch):
    patch_help_texts(monkeypatch, append_to_help(
        f"Podsjetnik: {CROATIAN_FORM} je ovdje samo za provjeru."))
    store, fake = SessionStore(), FakeLLM()

    session = publish(store, fake)

    stored = session["deterministic_task"]["hints"]
    assert len(stored) == 3
    assert all(CROATIAN_FORM not in hint for hint in stored)
    assert all(BOSNIAN_FORM in hint for hint in stored)


def test_stored_solution_is_the_published_copy_not_the_raw_package_string(
        universal, monkeypatch):
    """Objava validira JEDAN tekst — u dodatak mora ući TAJ, ne raniji sirov."""
    patch_help_texts(monkeypatch, append_to_help(
        f"Napomena: {CROATIAN_FORM} nije dio računa."))
    store, fake = SessionStore(), FakeLLM()

    session = publish(store, fake)

    stored = session["deterministic_task"]["solution"]
    assert stored == session["solution_summary"]
    assert CROATIAN_FORM not in stored
    assert BOSNIAN_FORM in stored


def test_annex_solution_matches_the_published_solution_for_a_plain_package(
        universal):
    """Bez ijedne izmjene: dodatak i objava su ISTI string, uvijek."""
    store, fake = SessionStore(), FakeLLM()

    session = publish(store, fake)

    assert session["deterministic_task"]["solution"] == session["solution_summary"]
    assert session["deterministic_task"]["answer_reply"] == \
        session["expected_answer_summary"]


# ---------------------------------------------------------------------------
# 2) NEPROLAZAN POMOĆNI TEKST OBARA KANDIDATA — STANJE OSTAJE NETAKNUTO
# ---------------------------------------------------------------------------

def test_unsafe_hint_notation_never_reaches_the_student(
        universal, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="matbot.tutor")
    patch_help_texts(monkeypatch, with_hints(*(UNSAFE_HINT,) * 3))
    store, fake = SessionStore(), FakeLLM()

    response = run_practice_turn(store, fake, turn())

    assert fake.call_count == 0
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    # Nijedna mutacija nije commitovana: svježa sesija nikad nije ni sačuvana.
    assert store.peek(SESSION) is None
    assert "stage=deterministic_help_text" in caplog.text
    assert "unknown_mathjax_command" in caplog.text


def test_active_task_survives_a_replacement_with_an_unusable_hint(
        universal, monkeypatch):
    store, fake = SessionStore(), FakeLLM()
    before = publish(store, fake)
    patch_help_texts(monkeypatch, with_hints(*(UNSAFE_HINT,) * 3))

    response = run_practice_turn(store, fake, turn(message="Daj mi novi zadatak."))

    assert fake.call_count == 0
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert response["task_preserved"] is True
    assert store.peek(SESSION) == before


def test_numerically_false_hint_cannot_be_published(universal, monkeypatch):
    """Ista numerička kapija koju objava vodi nad tekstom zadatka."""
    patch_help_texts(monkeypatch, with_hints(*("Znamo da je $2 + 2 = 5$.",) * 3))
    store, fake = SessionStore(), FakeLLM()

    response = run_practice_turn(store, fake, turn())

    assert fake.call_count == 0
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None


# ---------------------------------------------------------------------------
# 3) SVAKA POMOĆNA POVRŠINA SLUŽI POHRANJEN, SIGURAN TEKST
# ---------------------------------------------------------------------------

def test_hint_ladder_serves_the_normalized_stored_text(universal, monkeypatch):
    patch_help_texts(monkeypatch, append_to_help(
        f"Podsjetnik: {CROATIAN_FORM} je ovdje samo za provjeru."))
    store, fake = SessionStore(), FakeLLM()
    stored = publish(store, fake)["deterministic_task"]["hints"]

    for index, turn_id in enumerate(("h1", "h2", "h3")):
        response = run_practice_turn(store, fake, hint_turn(turn_id))
        assert fake.call_count == 0
        assert stored[index] in response["answer"]
        assert CROATIAN_FORM not in response["answer"]
        assert BOSNIAN_FORM in response["answer"]


def test_first_wrong_click_serves_the_safe_stored_hint(universal, monkeypatch):
    patch_help_texts(monkeypatch, append_to_help(
        f"Podsjetnik: {CROATIAN_FORM} je ovdje samo za provjeru."))
    store, fake = SessionStore(), FakeLLM()
    session = publish(store, fake)
    wrong = next(option["id"] for option in session["current_options"]
                 if option["id"] != session["correct_option_id"])

    response = run_practice_turn(store, fake, turn(
        message="[odgovor]", interaction_type="choice_answer",
        selected_option_id=wrong, client_turn_id="c1"))

    assert fake.call_count == 0
    assert response["answer_verdict"] == "incorrect"
    assert session["deterministic_task"]["hints"][0] in response["answer"]
    assert CROATIAN_FORM not in response["answer"]


def test_full_solution_serves_the_safe_stored_solution(universal, monkeypatch):
    patch_help_texts(monkeypatch, append_to_help(
        f"Napomena: {CROATIAN_FORM} nije dio računa."))
    store, fake = SessionStore(), FakeLLM()
    session = publish(store, fake)

    response = run_practice_turn(store, fake, solution_turn("s1"))

    assert fake.call_count == 0
    assert session["deterministic_task"]["solution"] in response["answer"]
    assert response["answer"].count(CROATIAN_FORM) == 0
    assert BOSNIAN_FORM in response["answer"]


def test_deterministic_help_surfaces_stay_at_zero_calls(universal):
    store, fake = SessionStore(), FakeLLM()
    publish(store, fake)
    run_practice_turn(store, fake, hint_turn("h1"))
    run_practice_turn(store, fake, solution_turn("s1"))
    assert fake.call_count == 0


# ---------------------------------------------------------------------------
# 4) POVRATNA PORUKA NA KLIK — OBJAVLJENA OPCIJA, NE SIROV PRIKAZ
# ---------------------------------------------------------------------------

def test_choice_reply_uses_the_published_option_text(universal):
    """Porodica čiji odgovor SAM nosi `$…$` ranije je davala `$$…$$`.

    Lekcija 6-03-006 („dva broja“) ima `wrap=""`, pa je slijepo umotavanje
    sirovog `display_answer` proizvodilo pokvaren MathJax u poruci koju učenik
    dobije na tačan klik."""
    store, fake = SessionStore(), FakeLLM()
    session = publish(store, fake, lesson="6-03-006")
    correct = session["correct_option_id"]

    response = run_practice_turn(store, fake, turn(
        lesson="6-03-006", message="[odgovor]", interaction_type="choice_answer",
        selected_option_id=correct, client_turn_id="c1"))

    assert fake.call_count == 0
    assert response["answer_verdict"] == "correct"
    assert "$$" not in response["answer"]
    published_option = next(option["text"] for option in session["current_options"]
                            if option["id"] == correct)
    assert published_option in response["answer"]


@pytest.fixture(autouse=True)
def _single_hint_rollback(monkeypatch):
    """Ljestvica nagovještaja je od ovog izdanja ROLLBACK put."""
    # LJESTVICA NAGOVJEŠTAJA JE ROLLBACK PUT. Produkcija služi JEDAN
    # nagovještaj po zadatku; ovi testovi čuvaju da ljestvica ostane
    # ispravna kad se vrati (MATBOT_PRACTICE_SINGLE_HINT=disabled).
    monkeypatch.setenv("MATBOT_PRACTICE_SINGLE_HINT", "disabled")
