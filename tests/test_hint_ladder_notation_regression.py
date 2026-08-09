r"""LJESTVICA NAGOVJEŠTAJA — jedan poziv po koraku, 0→1→2, vrh daje rješenje.

ŽIVI FINAL-40 NALAZ (lekcija 9-02-006, tri uzastopna zahtjeva za pomoć):

    hint 1  →  objavljen, hint_level 0 → 1
    hint 2  →  ODBIJEN u objavi: `unknown_mathjax_command:tdot`
               server je vratio kaniranu sigurnu poruku (HTTP 200),
               `hint_level` je ISPRAVNO ostao 1
    hint 3  →  zato je dao još jedan MEĐUKORAK umjesto punog rješenja

`\tdot` ne postoji ni u jednoj MathJax bazi. Šta je model njime MISLIO nije
dokazivo: sirov izlaz modela se nigdje ne čuva (CLAUDE.md pravilo 7), a
artefakt kampanje nosi samo kod odbijanja. Zato se komanda NE dodaje na
bijelu listu i NE prepravlja se u neku drugu — taj put ostaje zatvoren, i ovaj
modul ga zaključava. Popravka je na strani UGOVORA ZAPISA za polja pomoći
(matbot/tutor/prompts.py::_HELP_NOTATION_RULE) plus dokaz da ispravna ljestvica
zaista ispunjava traženi ugovor.

ZERO poziva modela: sve ide kroz FakeLLM.
"""
import pytest

from matbot import config
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from tests.conftest import (FakeLLM, make_reviewer_final, make_task_payload,
                            make_tutor_draft)

LESSON, GRADE = "9-02-006", 9
SESSION = "x03-ladder"

# DOSLOVAN živi zadatak i opcije iz
# scratchpad/practice_eval/final40_d049a56_retry1/results.jsonl
LIVE_TASK = (
    r"Prava $p$ prolazi kroz tačku $A(2,1,4)$ i ima smjer vektora $v=(3,0,0)$. "
    r"Ravan $\alpha$ zadana je jednačinom $z=4$. Koji je međusobni položaj "
    r"prave $p$ i ravni $\alpha$?")
LIVE_OPTIONS = (
    r"Prava $p$ leži u ravni $\alpha$.",
    r"Prava $p$ siječe ravan $\alpha$ u tački.",
    r"Prava $p$ je kosa u odnosu na ravan $\alpha$ (ne leži u njoj, nije paralelna).",
    r"Prava $p$ je paralelna ravni $\alpha$ i nema zajedničkih tačaka.",
)

HINT_1 = (r"Provjeri $z$-komponentu vektora $v$ i $z$-koordinatu tačke $A$: "
          r"ako je $z$-komponenta jednaka nuli, prava ne mijenja visinu.")
# REKONSTRUISAN živi defekt: nepostojeća komanda unutar $...$ u polju `hint`.
HINT_2_BROKEN = r"Izračunaj $v\tdot n$ za normalu ravni $n=(0,0,1)$."
HINT_2_SAFE = (r"Napiši parametarski $z(t)=4+0\cdot t$ i uporedi ga s "
               r"jednačinom ravni $z=4$.")
HINT_3 = (r"Svaka tačka prave ima $z(t)=4$, a ravan traži $z=4$, pa su sve "
          r"tačke prave u ravni: prava $p$ leži u ravni $\alpha$.")


@pytest.fixture(autouse=True)
def _universal_runtime(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")


def _turn(message, **changes):
    payload = {
        "session_id": SESSION, "grade": GRADE, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def _publish(store, fake):
    draft = make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=LIVE_TASK, options=LIVE_OPTIONS,
                                   correct_option_index=0,
                                   expected=LIVE_OPTIONS[0]))
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))
    response = run_practice_turn(
        store, fake, _turn("Daj mi jedan samostalan MCQ zadatak iz ove lekcije."))
    assert response["status"] == "ready", response
    return response


def _ask_for_help(store, fake, hint, message, turn_id):
    """Jedan help turn: tačno JEDAN pripremljen odgovor modela."""
    fake.queue(make_tutor_draft(intent="hint_request", new_task=None,
                               reply="Idemo korak po korak.", hint=hint))
    before = fake.call_count
    response = run_practice_turn(store, fake, _turn(
        message, intent="hint_request", interaction_phase="practice_help",
        client_turn_id=turn_id))
    assert fake.call_count - before == 1, "help turn smije potrošiti TAČNO jedan poziv"
    return response


def _task_state(session):
    return {
        "task": session["current_task"],
        "identity": session["current_task_identity"],
        "options": [dict(option) for option in session["current_options"]],
        "correct": session["correct_option_id"],
        "expected": session["expected_answer_summary"],
    }


# ---------------------------------------------------------------------------
# 1) TRAŽENI UGOVOR LJESTVICE: 0 → 1 → 2, treći nagovještaj daje rezultat
# ---------------------------------------------------------------------------

def test_three_step_hint_ladder_meets_the_required_contract():
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake)
    published = _task_state(store.peek(SESSION))
    assert store.peek(SESSION)["hint_level"] == 0

    first = _ask_for_help(store, fake, HINT_1, "Ne znam, daj mi prvi hint.", "h1")
    assert first["status"] == "ready"
    assert first["answer"] != SAFE_ERROR_MESSAGE
    assert store.peek(SESSION)["hint_level"] == 1
    assert LIVE_OPTIONS[0] not in first["answer"], "nivo 1 ne smije dati odgovor"

    second = _ask_for_help(store, fake, HINT_2_SAFE, "Daj mi drugi hint.", "h2")
    assert second["status"] == "ready"
    assert second["answer"] != SAFE_ERROR_MESSAGE
    assert store.peek(SESSION)["hint_level"] == 2
    assert second["answer"] != first["answer"], "drugi hint mora biti različit"
    assert LIVE_OPTIONS[0] not in second["answer"], "nivo 2 ne smije dati odgovor"

    third = _ask_for_help(store, fake, HINT_3, "Daj mi treći hint.", "h3")
    assert third["status"] == "ready"
    assert third["answer"] != SAFE_ERROR_MESSAGE
    # Vrh ljestvice SMIJE (i mora) dati konačan rezultat.
    assert "leži u ravni" in third["answer"]
    assert store.peek(SESSION)["hint_level"] == config.MAX_HINT_LEVEL

    # Zadatak je kroz cijelu ljestvicu netaknut.
    assert _task_state(store.peek(SESSION)) == published
    assert third["last_tutor_task"] == published["task"]
    assert store.peek(SESSION)["task_completed"] is False
    # 2 (objava) + 3 (po jedan po nagovještaju)
    assert fake.call_count == 5


def test_prompt_asks_the_third_hint_for_the_full_procedure_and_result():
    guidance = tutor_prompts._HINT_LEVEL_GUIDANCE
    assert "NIVO 3" in guidance[2] and "konačan rezultat" in guidance[2]
    assert "BEZ" in guidance[0] and "BEZ" in guidance[1]


# ---------------------------------------------------------------------------
# 2) ISTORIJSKI DEFEKT ZAPISA — i dalje pada zatvoreno, stanje preživljava
# ---------------------------------------------------------------------------

def test_unknown_command_in_a_hint_fails_closed_without_advancing_the_ladder():
    """Odbijen nagovještaj NIKAD se ne prikazuje kao uspješna pomoć, ne troši
    više od jednog poziva i ne mijenja ni zadatak ni nivo ljestvice."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake)
    _ask_for_help(store, fake, HINT_1, "Ne znam, daj mi prvi hint.", "h1")
    before = _task_state(store.peek(SESSION))

    rejected = _ask_for_help(store, fake, HINT_2_BROKEN, "Daj mi drugi hint.", "h2")

    assert rejected["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in rejected, "kanirana poruka nije uspješan turn"
    assert "next_state" not in rejected
    assert rejected["task_preserved"] is True
    assert rejected["last_tutor_task"] == before["task"]
    session = store.peek(SESSION)
    assert _task_state(session) == before
    assert session["hint_level"] == 1, "odbijen hint ne smije podići nivo"


def test_the_rejection_is_logged_with_the_internal_code_only(caplog):
    """Interni kod ide u log, nikad u odgovor učeniku (pravilo 7)."""
    import logging

    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake)
    with caplog.at_level(logging.INFO, logger="matbot.tutor.pipeline"):
        rejected = _ask_for_help(store, fake, HINT_2_BROKEN, "Daj mi hint.", "h1")

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "unknown_mathjax_command:tdot" in logged
    assert "tdot" not in rejected["answer"]


# ---------------------------------------------------------------------------
# 3) NOTACIJA OSTAJE ZATVORENA — komanda se NE pogađa i NE dodaje na listu
# ---------------------------------------------------------------------------

def test_the_historical_command_is_not_on_the_allowlist():
    from matbot.mathsafe import MATHJAX_COMMAND_ALLOWLIST

    assert "tdot" not in MATHJAX_COMMAND_ALLOWLIST


@pytest.mark.parametrize("broken", [
    r"$v\tdot n$", r"$2\tdot3=6$", r"$x \ty 5$", r"$\foo{2}$", r"$a \bogus b$",
])
def test_unknown_commands_in_general_remain_rejected(broken):
    from matbot.mathsafe import sanitize_and_validate_math_text

    _cleaned, safe = sanitize_and_validate_math_text(broken)
    assert not safe, broken


def test_help_notation_contract_is_actually_sent_to_the_tutor():
    """Novi tekst prompta mora STVARNO ići u poziv (CLAUDE.md „kako mijenjati“)."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake)
    _ask_for_help(store, fake, HINT_1, "Ne znam.", "h1")

    instructions, _input_text = fake.tutor_calls[-1]
    assert "ZAPIS U `hint` I `worked_solution`" in instructions
    assert "NE IZMIŠLJAJ" in instructions
    assert "\\cdot" in instructions
