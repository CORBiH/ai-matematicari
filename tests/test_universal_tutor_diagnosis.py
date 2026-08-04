"""Popravke univerzalnog puta nakon pada na ručnom testu (2026-08-03).

Put NIJE aktivan u produkciji — uključuje ga samo
`MATBOT_PRACTICE_PIPELINE=universal_two_call` (conftest ga pripne za ovaj modul).
Ovi testovi zaključavaju tri konkretna kvara koja je ručni test pokazao, da se
ne vrate kad se put jednom bude uključivao.

Ne pokrivaju cijeli put — to radi tests/test_universal_tutor_pipeline.py.
"""
import copy

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import lesson_context, prompts
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import schema as tutor_schema
from tests.conftest import (FakeLLM, make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft, queue_two_call)

SAFE = tutor_pipeline.SAFE_ERROR_MESSAGE
TOPIC = "6-03-001"          # uvodna lekcija djeljivosti (6. razred)


def _turn(**changes):
    payload = {
        "session_id": "diag", "grade": 6, "selected_topic": TOPIC,
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


# ---------------------------------------------------------------------------
# A. generate_task je padao zbog VIŠKA polja, ne zbog matematike
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("extra", [
    {"grading": "correct"},                 # recenzent ostavio ocjenu na zadatku
    {"grading": "incorrect"},
])
def test_a_irrelevant_field_from_the_reviewer_no_longer_fails_a_valid_task(extra):
    """Uredan zadatak ne smije propasti zato što se Tutor i Reviewer razilaze u
    polju koje ta namjera uopšte ne čita."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_tutor_draft(intent="generate_task"))
    fake.queue(make_reviewer_final(
        decision="approve", final=make_tutor_draft(intent="generate_task", **extra)))
    response = run_practice_turn(store, fake, _turn())
    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert store.peek("diag")["current_task"]


def test_a_irrelevant_field_from_the_tutor_is_also_tolerated():
    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(intent="generate_task", grading="correct")
    fake.queue(draft)
    fake.queue(make_reviewer_final(decision="approve", final=draft))
    assert run_practice_turn(store, fake, _turn())["status"] == "ready"


def test_a_normalization_only_clears_never_invents():
    """Normalizacija smije SAMO prazniti — nikad dodati sadržaj ni promijeniti
    namjeru (inače bi tiho mijenjala značenje odgovora)."""
    draft = make_tutor_draft(intent="generate_task", grading="correct",
                             hint="Ovo je koristan hint.")
    cleaned = tutor_schema.normalize_for_intent(draft)
    assert cleaned.intent == draft.intent
    assert cleaned.grading is None                      # višak obrisan
    assert cleaned.hint == "Ovo je koristan hint."      # koristan sadržaj ostaje
    assert cleaned.new_task == draft.new_task
    assert cleaned.reply == draft.reply


def test_a_missing_required_field_still_fails_closed():
    """Tolerancija na višak NE SMIJE oslabiti provjeru onoga što nedostaje."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_tutor_draft(intent="generate_task", new_task=None))
    response = run_practice_turn(store, fake, _turn())
    assert response["answer"] == SAFE
    assert fake.call_count == 1                 # nema šta recenzirati
    assert store.peek("diag") is None


def test_a_reviewer_must_still_independently_solve():
    """Ovo OSTAJE strogo: odobrenje bez nezavisnog rješavanja je nesigurno."""
    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(intent="generate_task")
    fake.queue(draft)
    fake.queue(make_reviewer_final(decision="approve", final=draft,
                                   checks=make_reviewer_checks(independently_solved=False)))
    assert run_practice_turn(store, fake, _turn())["answer"] == SAFE


# ---------------------------------------------------------------------------
# B. „Ne znam“ — hint mora biti VIDLJIV, i mora napredovati
# ---------------------------------------------------------------------------

def _bootstrap(store, fake, session_id="diag"):
    queue_two_call(fake)
    run_practice_turn(store, fake, _turn(session_id=session_id))


def _ask_hint(store, fake, hint, reply="Naravno, evo pomoći.", session_id="diag"):
    draft = make_tutor_draft(intent="hint_request", reply=reply, hint=hint,
                             new_task=None)
    fake.queue(draft)
    return run_practice_turn(store, fake, _turn(
        session_id=session_id, student_message="Ne znam"))


def test_b_hint_text_actually_reaches_the_student():
    """Kvar s ručnog testa: korisna pomoć je ostajala u polju `hint`, a učenik
    je u `answer` dobijao samo najavu."""
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake)
    hint = "Podijeli $12$ sa $4$ i provjeri ima li ostatka."
    response = _ask_hint(store, fake, hint)
    assert response["status"] == "ready"
    assert hint in response["answer"], response["answer"]


def test_b_frontend_visible_field_is_the_one_carrying_the_hint():
    """Frontend prikazuje isključivo `answer` — hint zato mora biti u njemu, a
    ne u odvojenom polju koje se nikad ne renderuje."""
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake)
    hint = "Sjeti se pravila djeljivosti sa $3$."
    response = _ask_hint(store, fake, hint)
    assert "hint" not in response          # nema zasebnog polja u odgovoru
    assert hint in response["answer"]


def test_b_repeated_ne_znam_progresses_through_hint_levels():
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake)

    first = _ask_hint(store, fake, "Pogledaj koja pravila djeljivosti znaš.")
    assert store.peek("diag")["hint_level"] == 1
    second = _ask_hint(store, fake, "Izračunaj $12 : 4$ i pogledaj ostatak.")
    assert store.peek("diag")["hint_level"] == 2
    assert first["answer"] != second["answer"]

    # Ni jedan „Ne znam“ ne pravi zadatak i ne ocjenjuje učenika.
    for response in (first, second):
        assert response["answer_verdict"] is None
        assert "Zadatak:" not in response["answer"]


def test_b_prompt_tells_the_model_what_the_next_hint_level_must_add():
    """Uzrok ponavljanja: prompt je slao samo BROJ hintova, bez ljestvice."""
    context = lesson_context.build(6, TOPIC)
    session = {"current_task": "Koji broj je djelilac broja $12$?",
               "current_options": [], "expected_answer_summary": "$4$",
               "difficulty": "standard", "hint_level": 0,
               "recent_tasks": [], "recent_turns": []}
    for level, marker in ((0, "NIVO 1"), (1, "NIVO 2"), (2, "NIVO 3")):
        session["hint_level"] = level
        block = prompts.build_tutor_input(context, session, "Ne znam")
        assert marker in block, (level, marker)
    assert "nikad ne ponavljaj raniji hint" in prompts.build_tutor_input(
        context, session, "Ne znam").lower()


def test_b_full_solution_text_also_reaches_the_student():
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake)
    solution = "$12 : 4 = 3$, ostatak je $0$, pa je $4$ djelilac broja $12$."
    draft = make_tutor_draft(intent="full_solution_request",
                             reply="Evo cijelog postupka.",
                             worked_solution=solution, new_task=None)
    fake.queue(draft)
    fake.queue(make_reviewer_final(decision="approve", final=draft))
    response = run_practice_turn(store, fake, _turn(student_message="Uradi ga ti."))
    assert solution in response["answer"]


def test_b_hint_is_not_duplicated_when_the_reply_already_contains_it():
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake)
    hint = "Provjeri ostatak pri dijeljenju."
    response = _ask_hint(store, fake, hint, reply=f"Evo pomoći. {hint}")
    assert response["answer"].count(hint) == 1


def test_b_unsafe_hint_fails_closed_without_mutating_state():
    """Hint prolazi ISTU sanitizaciju kao svaki vidljivi tekst."""
    store, fake = SessionStore(), FakeLLM()
    _bootstrap(store, fake)
    before = copy.deepcopy(store.peek("diag"))
    response = _ask_hint(store, fake, "Koristi \\ty{3}{4} ovdje.")
    assert response["answer"] == SAFE
    assert store.peek("diag") == before


# ---------------------------------------------------------------------------
# C. polazna složenost — apsolutno pravilo, ne relativna procjena
# ---------------------------------------------------------------------------

def test_c_tutor_prompt_receives_grade_and_lesson_context():
    context = lesson_context.build(6, TOPIC)
    session = {"current_task": "", "current_options": [],
               "expected_answer_summary": "", "difficulty": "standard",
               "hint_level": 0, "recent_tasks": [], "recent_turns": []}
    block = prompts.build_tutor_input(context, session, "Daj mi zadatak.")
    assert "razred: 6" in block
    assert context.title in block
    assert TOPIC in block
    assert context.oblast in block


def test_c_starting_complexity_rule_is_sent_and_is_absolute():
    """Ne oslanja se na relativnu procjenu recenzenta: na PRVOM zadatku nema s
    čim porediti, pa prag mora biti apsolutan."""
    context = lesson_context.build(6, TOPIC)
    instructions = prompts.build_tutor_instructions(context)
    assert "POLAZNA SLOŽENOST" in instructions
    assert "do 20" in instructions
    assert "harder_task" in instructions
    lowered = instructions.lower()
    assert "uzmi manje" in lowered


def test_c_rule_reaches_every_grade_and_lesson_without_lesson_id_branches():
    for grade, topic in ((6, TOPIC), (7, "7-02-008"), (8, "8-05-001"), (9, "9-05-004")):
        context = lesson_context.build(grade, topic)
        instructions = prompts.build_tutor_instructions(context)
        assert "POLAZNA SLOŽENOST" in instructions, topic
        assert f"razred: {grade}" in prompts.build_tutor_input(
            context,
            {"current_task": "", "current_options": [], "expected_answer_summary": "",
             "difficulty": "standard", "hint_level": 0, "recent_tasks": [],
             "recent_turns": []},
            "Daj mi zadatak.")


def test_c_no_lesson_id_branching_was_introduced():
    import re
    from pathlib import Path

    engine = Path(__file__).resolve().parent.parent / "matbot" / "tutor"
    topic_re = re.compile(r"\b\d-\d{2}-\d{3}\b")
    offenders = [
        f"{path.name}:{number}"
        for path in engine.glob("*.py")
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if topic_re.search(line)
    ]
    assert not offenders, offenders
