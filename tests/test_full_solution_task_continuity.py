r"""„Uradi ga ti“ rješava AKTIVAN zadatak — nikad ga ne zamjenjuje novim.

PRODUKCIJSKI NALAZ (ručni smoke test, 6. razred, lekcija 6-03-004, zastavice
`MATBOT_PRACTICE_PIPELINE=universal_two_call` i
`MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled`):

    1. objavljen zadatak (opcije 8 · 6 · 7 · 9)
    2. „Ne znam — daj mi hint“  → hint, zadatak ostaje
    3. „Uradi ga ti“            → NOV ZADATAK („Dopuni: □50 …“) umjesto
                                  rješenja postojećeg

UZROK: univerzalni dvopozivni put (`matbot/tutor/pipeline.py`) nikad nije čitao
`turn["intent"]`. Frontend eksplicitno šalje `intent=solution_request`
(templates/index.html), ali je taj signal ostajao neiskorišten, pa je namjeru
odlučivao ISKLJUČIVO model iz slobodnog teksta „Uradi ga ti.“ Kad model vrati
`next_task`, `_run_text_turn` objavi nov paket i pregazi aktivan zadatak.

Klijentski `intent` se ovdje koristi SAMO da SUZI ono što server smije uraditi
(zabrana objave novog zadatka) — nikad da nešto odobri. Zato ostaje u skladu s
pravilom „klijentu se ne vjeruje“: lažna vrijednost može izazvati odbijanje,
nikad objavu koja inače ne bi prošla.
"""
import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_task_payload, make_tutor_draft,
                            queue_two_call)

LESSON, GRADE = "6-03-004", 6

ACTIVE_TASK = "Koji od sljedećih brojeva je djeljiv i sa 6 i sa 25?"
ACTIVE_OPTIONS = ("150", "60", "75", "90")        # tačna je 150
REPLACEMENT_TASK = ("Dopuni: $\\square 50$ tako da je broj djeljiv i sa 6 i sa "
                    "25. Koja cifra u $\\square$ to omogućava?")
REPLACEMENT_OPTIONS = ("8", "6", "7", "9")


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


def _publish_active_task(store, fake, session_id):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=ACTIVE_TASK, options=ACTIVE_OPTIONS,
                                   correct_option_index=0, expected="150")))
    response = run_practice_turn(
        store, fake, _turn(session_id, "Daj mi jedan zadatak za vježbu iz ove teme."))
    assert response["status"] == "ready"
    return response


def _task_state(session):
    """Sve što identifikuje AKTIVAN zadatak — potpis, opcije, označen odgovor."""
    return {
        "current_task": session["current_task"],
        "current_options": [dict(option) for option in session["current_options"]],
        "correct_option_id": session["correct_option_id"],
        "expected_answer_summary": session["expected_answer_summary"],
        "current_task_signature": session["current_task_signature"],
    }


# ---------------------------------------------------------------------------
# 1. TAČAN PRODUKCIJSKI NIZ: zadatak → hint → puno rješenje
# ---------------------------------------------------------------------------

def test_hint_leaves_the_active_task_untouched():
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-1")
    before = _task_state(store.peek("seq-1"))

    queue_two_call(fake, draft=make_tutor_draft(
        intent="hint_request", new_task=None,
        reply="Krenimo korak po korak.",
        hint="Prvo provjeri pravilo za djeljivost sa 25: pogledaj posljednje dvije cifre broja."))
    hint = run_practice_turn(
        store, fake,
        _turn("seq-1", "Ne znam.", intent="hint_request",
              interaction_phase="practice_help"))

    assert hint["status"] == "ready"
    session = store.peek("seq-1")
    assert _task_state(session) == before
    assert session["hint_level"] == 1


def test_full_solution_request_solves_the_active_task():
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-2")
    queue_two_call(fake, draft=make_tutor_draft(
        intent="hint_request", new_task=None, reply="Idemo redom.",
        hint="Pogledaj posljednje dvije cifre broja."))
    run_practice_turn(store, fake, _turn("seq-2", "Ne znam.", intent="hint_request",
                                         interaction_phase="practice_help"))
    before = _task_state(store.peek("seq-2"))
    calls_before = fake.call_count

    queue_two_call(fake, draft=make_tutor_draft(
        intent="full_solution_request", new_task=None,
        reply="Evo cijelog postupka.",
        worked_solution="Broj mora biti djeljiv sa 150, a to je jedino 150."))
    solution = run_practice_turn(
        store, fake,
        _turn("seq-2", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert solution["status"] == "ready"
    assert "150" in solution["answer"]
    # Zadatak se rješava, ne zamjenjuje.
    assert "Zadatak:" not in solution["answer"]
    assert solution["last_tutor_task"] == before["current_task"]
    assert solution["next_state"]["task"]["question"] == before["current_task"]
    assert solution["next_state"]["task"]["options"] == before["current_options"]
    assert solution["revealed_correct_option_id"] == before["correct_option_id"]

    session = store.peek("seq-2")
    assert _task_state(session) == before
    assert session["task_completed"] is True
    assert session["last_result"] == "full_solution"
    assert fake.call_count - calls_before <= 2      # nikad treći poziv


def test_full_solution_request_never_publishes_a_replacement_task():
    """Model vrati `next_task` — server mora odbiti, ne objaviti."""
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-3")
    before = _task_state(store.peek("seq-3"))
    calls_before = fake.call_count

    queue_two_call(fake, draft=make_tutor_draft(
        intent="next_task", reply="Evo sljedećeg zadatka.",
        new_task=make_task_payload(text=REPLACEMENT_TASK, options=REPLACEMENT_OPTIONS,
                                   correct_option_index=2, expected="7")))
    response = run_practice_turn(
        store, fake,
        _turn("seq-3", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert "status" not in response                 # sigurna poruka
    assert REPLACEMENT_TASK not in response["answer"]
    assert response["last_tutor_task"] == before["current_task"]
    assert _task_state(store.peek("seq-3")) == before
    # Nacrt s zabranjenom namjerom ne troši ni recenzenta ni treći poziv.
    assert fake.call_count - calls_before <= 2


def test_forbidden_task_intent_stops_before_the_reviewer_call():
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-4")
    reviewer_calls_before = len(fake.reviewer_calls)

    fake.queue(make_tutor_draft(
        intent="generate_task", reply="Evo zadatka.",
        new_task=make_task_payload(text=REPLACEMENT_TASK, options=REPLACEMENT_OPTIONS,
                                   correct_option_index=2, expected="7")))
    response = run_practice_turn(
        store, fake,
        _turn("seq-4", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert "status" not in response
    assert len(fake.reviewer_calls) == reviewer_calls_before


def test_hint_request_also_refuses_to_replace_the_active_task():
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-5")
    before = _task_state(store.peek("seq-5"))

    fake.queue(make_tutor_draft(
        intent="generate_task", reply="Evo zadatka.",
        new_task=make_task_payload(text=REPLACEMENT_TASK, options=REPLACEMENT_OPTIONS,
                                   correct_option_index=2, expected="7")))
    response = run_practice_turn(
        store, fake,
        _turn("seq-5", "Ne znam.", intent="hint_request",
              interaction_phase="practice_help"))

    assert "status" not in response
    assert _task_state(store.peek("seq-5")) == before


def test_explicit_new_task_request_still_publishes():
    """Zabrana važi SAMO za eksplicitnu UI akciju hint/rješenje."""
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-6")

    queue_two_call(fake, draft=make_tutor_draft(
        intent="next_task", reply="Evo sljedećeg zadatka.",
        new_task=make_task_payload(text=REPLACEMENT_TASK, options=REPLACEMENT_OPTIONS,
                                   correct_option_index=2, expected="7")))
    response = run_practice_turn(
        store, fake, _turn("seq-6", "Daj mi novi zadatak."))

    assert response["status"] == "ready"
    assert REPLACEMENT_TASK in response["answer"]
    assert store.peek("seq-6")["current_task"] == REPLACEMENT_TASK


# ---------------------------------------------------------------------------
# 2. SIGURAN PAD: rješenje ne uspije → aktivan zadatak preživi
# ---------------------------------------------------------------------------

def test_failed_full_solution_preserves_the_active_task():
    from matbot.llm import LLMUnavailable

    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-7")
    before = _task_state(store.peek("seq-7"))

    fake.queue(LLMUnavailable("boom"))
    response = run_practice_turn(
        store, fake,
        _turn("seq-7", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    assert "status" not in response
    assert response["last_tutor_task"] == before["current_task"]
    session = store.peek("seq-7")
    assert _task_state(session) == before
    assert session["task_completed"] is False


# ---------------------------------------------------------------------------
# 3. UGOVOR PROMPTA: server saopštava modelu što je već sam utvrdio
# ---------------------------------------------------------------------------

def test_tutor_prompt_carries_the_explicit_ui_action():
    store, fake = SessionStore(), FakeLLM()
    _publish_active_task(store, fake, "seq-8")

    queue_two_call(fake, draft=make_tutor_draft(
        intent="full_solution_request", new_task=None, reply="Evo postupka.",
        worked_solution="Traži se broj djeljiv sa 150."))
    run_practice_turn(
        store, fake,
        _turn("seq-8", "Uradi ga ti.", intent="solution_request",
              interaction_phase="practice_help"))

    _instructions, input_text = fake.tutor_calls[-1]
    assert "full_solution_request" in input_text
    assert "novi zadatak" in input_text.lower()


# ---------------------------------------------------------------------------
# 4. ŽIVI F5L NALAZ (I01–I03, produkcijski smoke 20260808T144302Z):
#    identitetska lekcija 9-01-017 — svjež → hint → puno rješenje.
#    Produkcija je objavila vjeran zadatak i hint, a rješenje odbila u objavi
#    porukom „nebezbjedan matematički zapis [full_solution_request]“ BEZ koda
#    defekta (help-turn nema preflight), pa je klasa (M06, M18, I03) bila
#    nedijagnostikljiva. Lokalna reprodukcija na istom kodu objavila je čisto
#    rješenje — validator NIJE lažno pozitivan; testovi pinuju oba ishoda.
# ---------------------------------------------------------------------------

IDENTITY_LESSON, IDENTITY_GRADE = "9-01-017", 9
IDENTITY_TASK = ("Pokaži da je identitet istinit tako što ćeš pojednostaviti "
                 "lijevu stranu i navesti uslov definisanosti: Pokaži da je "
                 "$\\frac{x^2-1}{x-1}=x+1$ za sve vrijednosti $x$ za koje je "
                 "izraz definisan.")
IDENTITY_OPTIONS = ("$x+1$", "$x^2+1$", "$x-1$", "1")   # tačna je $x+1$
IDENTITY_SOLUTION = ("Faktorišemo brojnik: $x^2-1=(x-1)(x+1)$, pa je "
                     "$\\frac{x^2-1}{x-1}=\\frac{(x-1)(x+1)}{x-1}=x+1$ za "
                     "svako $x \\neq 1$. Uslov definisanosti: $x \\neq 1$. "
                     "Tačna opcija je $x+1$.")


def _identity_turn(session_id, message, **changes):
    turn = _turn(session_id, message, grade=IDENTITY_GRADE,
                 selected_topic=IDENTITY_LESSON)
    turn.update(changes)
    return turn


def _publish_identity_task(store, fake, session_id):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=IDENTITY_TASK,
                                   options=IDENTITY_OPTIONS,
                                   correct_option_index=0, expected="$x+1$")))
    response = run_practice_turn(
        store, fake, _identity_turn(session_id, "Daj mi zadatak."))
    assert response["status"] == "ready", response.get("answer", "")
    return response


def test_identity_lifecycle_fresh_hint_solution_publishes_cleanly():
    """Vjeran tok I01–I03: čisto rješenje s uslovom definisanosti $x \\neq 1$
    MORA biti objavljeno, uz otkrivanje opcije i očuvano stanje."""
    store, fake = SessionStore(), FakeLLM()
    _publish_identity_task(store, fake, "f5l-id-1")

    queue_two_call(fake, draft=make_tutor_draft(
        intent="hint_request", new_task=None, reply="Idemo korak po korak.",
        hint="Faktoriši brojnik $x^2-1$ kao razliku kvadrata."))
    hint = run_practice_turn(
        store, fake, _identity_turn("f5l-id-1", "Ne znam.",
                                    intent="hint_request",
                                    interaction_phase="practice_help"))
    assert hint["status"] == "ready"
    before = _task_state(store.peek("f5l-id-1"))

    queue_two_call(fake, draft=make_tutor_draft(
        intent="full_solution_request", new_task=None,
        reply="Evo cijelog postupka.", worked_solution=IDENTITY_SOLUTION))
    solution = run_practice_turn(
        store, fake, _identity_turn("f5l-id-1", "Uradi ga ti.",
                                    intent="solution_request",
                                    interaction_phase="practice_help"))

    assert solution["status"] == "ready"
    assert "x+1" in solution["answer"]
    assert "\\neq 1" in solution["answer"]          # uslov definisanosti
    assert solution["revealed_correct_option_id"] == before["correct_option_id"]
    session = store.peek("f5l-id-1")
    assert _task_state(session) == before
    assert session["task_completed"] is True


def test_unsafe_solution_notation_rejection_logs_defect_codes(caplog):
    """ŽIVI F5L NALAZ (I03): kad model u rješenju upotrijebi komandu van
    allowlista (npr. \\cancel), objava i dalje pada zatvoreno i čuva zadatak
    — ali log sada nosi ograničen kod defekta (ime komande, nikad sadržaj),
    da produkcijska ponavljanja ove klase više ne budu nedijagnostikljiva."""
    import logging

    store, fake = SessionStore(), FakeLLM()
    _publish_identity_task(store, fake, "f5l-id-2")
    before = _task_state(store.peek("f5l-id-2"))

    unsafe = ("Skratimo: $\\frac{x^2-1}{x-1}"
              "=\\frac{\\cancel{(x-1)}(x+1)}{\\cancel{x-1}}=x+1$, "
              "uz $x \\neq 1$.")
    queue_two_call(fake, draft=make_tutor_draft(
        intent="full_solution_request", new_task=None,
        reply="Evo postupka.", worked_solution=unsafe))
    with caplog.at_level(logging.WARNING, logger="matbot.tutor.pipeline"):
        response = run_practice_turn(
            store, fake, _identity_turn("f5l-id-2", "Uradi ga ti.",
                                        intent="solution_request",
                                        interaction_phase="practice_help"))

    assert "status" not in response                  # sigurna poruka, ne objava
    assert response["last_tutor_task"] == before["current_task"]
    session = store.peek("f5l-id-2")
    assert _task_state(session) == before
    assert session["task_completed"] is False
    assert "nebezbjedan matematički zapis [full_solution_request]" in caplog.text
    assert "codes=" in caplog.text
    assert "unknown_mathjax_command" in caplog.text
    # Kod NIKAD ne nosi sadržaj rečenice — samo ime komande.
    assert "Skratimo" not in caplog.text


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
