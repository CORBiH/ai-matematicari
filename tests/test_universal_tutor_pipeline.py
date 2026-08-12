"""Univerzalni dvopozivni Tutor+Reviewer put — JEDAN aktivni put za 534 lekcije.

Dokazuje ono što je pivot obećao: nema više dva aktivna Practice puta, granica
je TAČNO dva poziva, a stanje se ne dira dok oba poziva i sve serverske provjere
ne prođu.
"""
import copy
import json
import re
from pathlib import Path

import pytest

from matbot.contracts import registry as contract_registry
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.topics import _load as load_topics
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor import schema as tutor_schema
from tests.conftest import (FakeLLM, LLMTimeout, make_difficulty_diagnostics,
                            make_reviewer_checks, make_reviewer_final,
                            make_task_payload, make_tutor_draft, queue_two_call)

ROOT = Path(__file__).resolve().parent.parent
SAFE = tutor_pipeline.SAFE_ERROR_MESSAGE


def all_lessons():
    data = load_topics()
    out = []
    for grade, payload in data.get("grades", {}).items():
        for lesson in payload.get("lessons", []):
            out.append((int(grade), lesson["id"]))
    return out


LESSONS = all_lessons()


def turn_for(topic_id, grade, **changes):
    payload = {
        "session_id": f"uni-{topic_id}", "grade": grade,
        "selected_topic": topic_id, "selected_oblast": "nepouzdano",
        "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


# ---------------------------------------------------------------------------
# 1-2: svih 534 lekcija — jedan put, jedna šema
# ---------------------------------------------------------------------------

def test_curriculum_really_has_every_lesson():
    assert len(LESSONS) == 536


def test_all_534_lessons_resolve_to_the_universal_pipeline():
    """Nijedna lekcija ne smije ostati bez LessonContext-a — kad bi ostala,
    tiho bi otišla na drugi put ili na sigurnu poruku."""
    missing = [
        topic for grade, topic in LESSONS
        if lesson_context_module.build(grade, topic) is None
    ]
    assert not missing, f"lekcije bez univerzalnog konteksta: {missing[:10]}"


def test_every_lesson_uses_the_same_final_schema():
    """Ista šema, isti oblik odgovora — bez obzira ima li lekcija ugovor."""
    seen_contract, seen_legacy = 0, 0
    for grade, topic in LESSONS:
        context = lesson_context_module.build(grade, topic)
        if context.has_contract:
            seen_contract += 1
        else:
            seen_legacy += 1
        assert isinstance(context, lesson_context_module.LessonContext)
    # Dvije nove lekcije Skupova nemaju K1/K3 ugovor (semantički je
    # odvojen sloj), pa rastu SAMO u legacy brojaču.
    assert seen_contract == 6 and seen_legacy == 530


@pytest.mark.parametrize("grade,topic", [
    (6, "6-04-009"),   # lekcija S ugovorom
    (6, "6-04-014"),   # lekcija BEZ ugovora, ista oblast
    (7, "7-02-008"),   # drugi razred, drugi domen
    (9, "9-05-004"),   # sistemi
    (8, "8-05-001"),   # geometrija
])
def test_contracted_and_legacy_lessons_take_the_identical_path(grade, topic):
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    response = run_practice_turn(store, fake, turn_for(topic, grade))
    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1
    assert response["effective_topic"] == topic


def test_frontend_response_contract_is_unchanged():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert set(response) == {
        "status", "answer", "answer_verdict", "last_tutor_task",
        "next_state", "session_mode", "effective_topic",
    }
    state = response["next_state"]
    assert state["v"] == 1 and "task" in state
    assert {"id", "text"} == set(state["task"]["options"][0])
    # Interna polja NIKAD ne izlaze.
    blob = json.dumps(response, ensure_ascii=False)
    for leaked in ("lesson_focus", "difficulty_diagnostics", "independent_answer",
                   "correct_option_index", "expected_answer"):
        assert leaked not in blob


# ---------------------------------------------------------------------------
# 3-4: kontekst koji modeli stvarno dobiju
# ---------------------------------------------------------------------------

def test_tutor_and_reviewer_receive_the_selected_lesson():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))

    # Kanonski kurikularni naslov (data/topics.json) namjerno zadržava
    # „imenilaca“ — normalizacija važi za PROIZVEDENI tekst, ne za naslove.
    title = "Sabiranje i oduzimanje razlomaka jednakih imenilaca"
    for _instructions, input_text in (fake.tutor_calls[0], fake.reviewer_calls[0]):
        assert title in input_text
        assert "6-04-009" in input_text
        assert "Razlomci" in input_text


def test_reviewer_receives_the_tutor_draft():
    store, fake = SessionStore(), FakeLLM()
    draft, _ = queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    reviewer_input = fake.reviewer_calls[0][1]
    # Nacrt se prosljeđuje kao JSON, pa se backslash escapuje — poredi se s
    # JSON oblikom, ne sa sirovim Python stringom.
    assert draft.model_dump_json(exclude_none=True) in reviewer_input
    assert "NACRT" in reviewer_input
    assert draft.intent in reviewer_input


def test_easier_and_harder_carry_the_prior_task_context():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    first_task = store.peek("uni-6-04-009")["current_task"]

    for intent, message in (("easier_task", "Daj mi lakši zadatak."),
                            ("harder_task", "Daj mi teži zadatak.")):
        harder_task = make_task_payload(
            text="Izračunaj: $\\frac{11}{13} + \\frac{1}{13}$.",
            options=("$\\frac{12}{13}$", "$\\frac{12}{26}$", "$\\frac{10}{13}$", "$1$"),
            expected="$\\frac{12}{13}$",
        )
        draft = make_tutor_draft(
            intent=intent, new_task=harder_task,
            difficulty_diagnostics=make_difficulty_diagnostics(
                "lower" if intent == "easier_task" else "higher"),
        )
        queue_two_call(fake, draft=draft)
        run_practice_turn(store, fake, turn_for(
            "6-04-009", 6, student_message=message))
        # Prethodni zadatak MORA biti u promptu oba poziva da bi poređenje
        # težine uopšte bilo moguće.
        assert first_task in fake.tutor_calls[-1][1]
        assert first_task in fake.reviewer_calls[-1][1]


def test_difficulty_diagnostics_never_reach_the_student():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    draft = make_tutor_draft(
        intent="easier_task",
        new_task=make_task_payload(text="Izračunaj: $\\frac{1}{5} + \\frac{2}{5}$.",
                                   options=("$\\frac{3}{5}$", "$\\frac{3}{10}$",
                                            "$\\frac{2}{5}$", "$\\frac{4}{5}$"),
                                   expected="$\\frac{3}{5}$"),
        difficulty_diagnostics=make_difficulty_diagnostics(
            "lower", rationale="TAJNI INTERNI TRAG"),
    )
    queue_two_call(fake, draft=draft)
    response = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, student_message="Lakše, molim."))
    assert "TAJNI INTERNI TRAG" not in json.dumps(response, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 5-6: „ne znam“, hint, rješenje, ocjenjivanje
# ---------------------------------------------------------------------------

def test_ne_znam_produces_help_and_is_never_graded_as_wrong():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))

    draft = make_tutor_draft(
        intent="hint_request", reply="Bez brige — nazivnik ostaje isti.",
        hint="Saberi samo brojnike.", new_task=None)
    queue_two_call(fake, draft=draft)
    response = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, student_message="ne znam"))

    assert response["status"] == "ready"
    assert response["answer_verdict"] is None      # nikad „netačno“
    assert "nazivnik" in response["answer"]
    assert store.peek("uni-6-04-009")["hint_level"] == 1


def test_prompt_states_that_ne_znam_is_not_an_answer_attempt():
    context = lesson_context_module.build(6, "6-04-009")
    instructions = tutor_prompts.build_tutor_instructions(context)
    assert "NE ZNAM" in instructions.upper()
    assert "hint_request" in instructions
    for intent in tutor_schema.INTENTS:
        assert intent in instructions, intent


def test_answer_attempt_is_graded_against_the_active_task():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    session = store.peek("uni-6-04-009")

    draft = make_tutor_draft(
        intent="answer_attempt", reply="Tačno! Nazivnik ostaje isti.",
        grading="correct", new_task=None)
    queue_two_call(fake, draft=draft)
    response = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, student_message=session["expected_answer_summary"]))
    assert response["status"] == "ready"
    # Aktivni zadatak i njegov tačan odgovor moraju biti u promptu Tutora.
    assert session["current_task"] in fake.tutor_calls[-1][1]
    assert session["expected_answer_summary"] in fake.tutor_calls[-1][1]


def test_option_click_verdict_stays_server_owned():
    """Klik se ocjenjuje DETERMINISTIČKI; model samo piše feedback i ne smije
    osporiti serverski verdikt."""
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    session = store.peek("uni-6-04-009")
    wrong = next(o["id"] for o in session["current_options"]
                 if o["id"] != session["correct_option_id"])

    draft = make_tutor_draft(intent="answer_attempt", reply="Nije tačno, probaj ponovo.",
                            grading="incorrect", new_task=None)
    queue_two_call(fake, draft=draft)
    response = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, interaction_type="choice_answer", selected_option_id=wrong,
        student_message="[klik]", client_turn_id="c1"))

    assert response["answer_verdict"] == "incorrect"
    assert "SERVER JE UTVRDIO" in fake.tutor_calls[-1][1]
    assert "NETAČAN" in fake.tutor_calls[-1][1]
    assert store.peek("uni-6-04-009")["correct_streak"] == 0


def test_full_solution_request_reveals_the_correct_option():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    correct_id = store.peek("uni-6-04-009")["correct_option_id"]

    draft = make_tutor_draft(
        intent="full_solution_request", reply="Evo cijelog postupka.",
        worked_solution="Saberi brojnike: 2+3=5, nazivnik ostaje 7.", new_task=None)
    queue_two_call(fake, draft=draft)
    response = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, student_message="Uradi ga ti."))
    assert response["revealed_correct_option_id"] == correct_id
    assert store.peek("uni-6-04-009")["task_completed"] is True


def test_off_topic_and_clarification_do_not_create_a_task():
    store, fake = SessionStore(), FakeLLM()
    for intent, message in (("off_topic", "Voliš li fudbal?"),
                            ("clarification", "Šta znači nazivnik?")):
        draft = make_tutor_draft(
            intent=intent, reply="Vratimo se na zadatak iz ove lekcije.",
            new_task=None)
        queue_two_call(fake, draft=draft)
        response = run_practice_turn(store, fake, turn_for(
            "6-04-009", 6, student_message=message))
        assert response["status"] == "ready"
        assert response["answer_verdict"] is None
        assert store.peek("uni-6-04-009") is None or \
            not store.peek("uni-6-04-009")["current_task"]


# ---------------------------------------------------------------------------
# 7-10: recenzent kao semantička kapija
# ---------------------------------------------------------------------------

def test_reviewer_can_correct_an_off_scope_draft():
    """Nacrt izvan lekcije: recenzent vraća ISPRAVLJEN payload i to je konačan
    odgovor — bez trećeg poziva."""
    store, fake = SessionStore(), FakeLLM()
    off_scope = make_tutor_draft(new_task=make_task_payload(
        text="Izračunaj obim kruga poluprečnika $5$ cm.",
        options=("$31,4$ cm", "$15,7$ cm", "$78,5$ cm", "$10$ cm"),
        expected="$31,4$ cm"))
    corrected = make_tutor_draft()      # nazad unutar lekcije
    fake.queue(off_scope)
    fake.queue(make_reviewer_final(decision="correct", final=corrected))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))

    assert response["status"] == "ready"
    assert fake.call_count == 2                     # nema trećeg poziva
    assert "obim kruga" not in response["answer"]
    assert store.peek("uni-6-04-009")["current_task"] == corrected.new_task.text


def test_reviewer_fail_closed_publishes_nothing_and_keeps_state():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    before = copy.deepcopy(store.peek("uni-6-04-009"))
    calls_before = fake.call_count

    fake.queue(make_tutor_draft())
    fake.queue(make_reviewer_final(decision="fail_closed",
                                   fail_reason_code="ambiguous_task"))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))

    assert response["answer"] == SAFE
    assert "status" not in response
    assert fake.call_count == calls_before + 2      # tačno dva, bez trećeg
    assert store.peek("uni-6-04-009") == before     # bez mutacije


@pytest.mark.parametrize("reason", [
    "math_incorrect", "wrong_marked_option", "outside_lesson",
    "ambiguous_task", "unsolvable_task", "invalid_mathjax",
])
def test_every_fail_reason_fails_closed_safely(reason):
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_tutor_draft())
    fake.queue(make_reviewer_final(decision="fail_closed", fail_reason_code=reason))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE
    assert store.peek("uni-6-04-009") is None


def test_reviewer_cannot_approve_while_reporting_a_failed_check():
    """Kontradiktoran payload (approve + oborena provjera) je pad, ne odobrenje."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_tutor_draft())
    fake.queue(make_reviewer_final(
        decision="approve", checks=make_reviewer_checks(math_correct=False)))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE
    assert store.peek("uni-6-04-009") is None


def test_reviewer_must_independently_solve_before_approving_a_task():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_tutor_draft())
    fake.queue(make_reviewer_final(
        decision="approve", checks=make_reviewer_checks(independently_solved=False)))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE


def test_wrong_marked_answer_is_caught_by_server_side_option_checks():
    """Dvije opcije iste vrijednosti = nema jednog tačnog odgovora → fail closed
    čak i kad ih je recenzent odobrio."""
    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(new_task=make_task_payload(
        options=("$\\frac{5}{7}$", "$\\frac{10}{14}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$")))
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE
    assert store.peek("uni-6-04-009") is None


def test_numerically_inconsistent_task_text_fails_closed():
    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(
        new_task=make_task_payload(text="Izračunaj: $2+3=6$ pa nastavi."))
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE
    assert store.peek("uni-6-04-009") is None


def test_reviewer_corrects_a_wrong_marked_answer():
    """Nacrt označi pogrešnu opciju; recenzent vrati ISPRAVLJEN payload s
    tačnim indeksom — i to je ono što se objavljuje."""
    store, fake = SessionStore(), FakeLLM()
    wrong_marked = make_tutor_draft(new_task=make_task_payload(
        correct_option_index=2, expected="$\\frac{6}{7}$"))   # netačno označeno
    corrected = make_tutor_draft(new_task=make_task_payload(
        correct_option_index=0, expected="$\\frac{5}{7}$"))   # 2/7+3/7 = 5/7
    fake.queue(wrong_marked)
    fake.queue(make_reviewer_final(decision="correct", final=corrected))
    run_practice_turn(store, fake, turn_for("6-04-009", 6))

    session = store.peek("uni-6-04-009")
    correct_text = next(o["text"] for o in session["current_options"]
                        if o["id"] == session["correct_option_id"])
    assert correct_text == "$\\frac{5}{7}$"
    assert session["expected_answer_summary"] == "$\\frac{5}{7}$"
    assert fake.call_count == 2


def test_reviewer_corrects_mathematically_wrong_draft():
    store, fake = SessionStore(), FakeLLM()
    bad_math = make_tutor_draft(new_task=make_task_payload(
        text="Izračunaj: $\\frac{2}{7} + \\frac{3}{7}$.",
        options=("$\\frac{5}{14}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$", "$\\frac{4}{7}$"),
        expected="$\\frac{5}{14}$"))
    corrected = make_tutor_draft()
    fake.queue(bad_math)
    fake.queue(make_reviewer_final(decision="correct", final=corrected))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["status"] == "ready"
    assert "\\frac{5}{14}" not in store.peek("uni-6-04-009")["expected_answer_summary"]


def test_universal_path_normalizes_forbidden_terminology():
    """Projektna terminologija se primjenjuje i na novom putu — hrvatski oblik
    ne smije stići do učenika ni kroz zadatak ni kroz odgovor."""
    from matbot import terminology

    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(
        intent="explanation_request",
        reply="Prosti čimbenici broja su njegovi djelioci.", new_task=None)
    queue_two_call(fake, draft=draft)
    response = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, student_message="Objasni mi."))
    assert not terminology.contains_forbidden_term(response["answer"])
    assert "faktori" in response["answer"]


def test_universal_path_rejects_unsafe_mathjax_in_a_task():
    store, fake = SessionStore(), FakeLLM()
    draft = make_tutor_draft(new_task=make_task_payload(
        text="Izračunaj \\ty{5}{7} i nastavi."))
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE
    assert store.peek("uni-6-04-009") is None


# ---------------------------------------------------------------------------
# 11-14: granica poziva
# ---------------------------------------------------------------------------

def test_successful_turn_uses_exactly_two_calls():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["status"] == "ready"
    assert fake.call_count == 2


def test_unparsable_tutor_draft_never_reaches_the_reviewer():
    """Nacrt koji ne prolazi pravilo polja nema šta da se recenzira → 1 poziv."""
    store, fake = SessionStore(), FakeLLM()
    # hint_request BEZ hinta — strukturno validan pydantic, sadržajno neupotrebljiv.
    fake.queue(make_tutor_draft(intent="hint_request", hint=None, new_task=None))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE
    assert fake.call_count == 1
    assert len(fake.reviewer_calls) == 0
    assert store.peek("uni-6-04-009") is None


def test_tutor_timeout_costs_one_call_and_no_reviewer_call():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(LLMTimeout("APITimeoutError"))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE
    assert fake.call_count == 1
    assert len(fake.reviewer_calls) == 0


def test_reviewer_timeout_costs_two_calls_and_never_a_third():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_tutor_draft())
    fake.queue(LLMTimeout("APITimeoutError"))
    response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
    assert response["answer"] == SAFE
    assert fake.call_count == 2
    assert store.peek("uni-6-04-009") is None


def test_successful_turn_records_exactly_two_distinguishable_sdk_entries(caplog):
    """Računovodstvo troška: iz loga se mora moći prebrojati TAČNO dva poziva i
    razlikovati koji je Tutor (call=1), a koji Reviewer (call=2)."""
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    with caplog.at_level("INFO", logger="matbot.tutor"):
        run_practice_turn(store, fake, turn_for("6-04-009", 6))

    entries = [r.getMessage() for r in caplog.records if "tutor_sdk_call" in r.getMessage()]
    assert len(entries) == 2, entries
    assert "stage=tutor call=1" in entries[0]
    assert "stage=reviewer call=2" in entries[1]
    for entry in entries:
        assert "latency_ms=" in entry and "usage=" in entry
        # Nikad sadržaj — samo mjerni podaci.
        assert "Izračunaj" not in entry


def test_tutor_failure_records_one_or_zero_sdk_entries(caplog):
    store, fake = SessionStore(), FakeLLM()
    fake.queue(LLMTimeout("APITimeoutError"))
    with caplog.at_level("INFO", logger="matbot.tutor"):
        run_practice_turn(store, fake, turn_for("6-04-009", 6))
    entries = [r.getMessage() for r in caplog.records if "tutor_sdk_call" in r.getMessage()]
    assert entries == []          # poziv nije uspio → nema SDK zapisa
    assert fake.call_count == 1

    caplog.clear()
    store2, fake2 = SessionStore(), FakeLLM()
    fake2.queue(make_tutor_draft())
    fake2.queue(LLMTimeout("APITimeoutError"))
    with caplog.at_level("INFO", logger="matbot.tutor"):
        response = run_practice_turn(store2, fake2, turn_for(
            "6-04-009", 6, session_id="rev-timeout"))
    entries = [r.getMessage() for r in caplog.records if "tutor_sdk_call" in r.getMessage()]
    assert len(entries) == 1 and "stage=tutor" in entries[0]
    assert response["answer"] == SAFE
    assert store2.peek("rev-timeout") is None      # Reviewer timeout ne commituje


def test_blocked_before_the_model_costs_zero_calls():
    store, fake = SessionStore(), FakeLLM()
    # nepostojeća lekcija
    response = run_practice_turn(store, fake, turn_for("6-99-999", 6))
    assert response["answer"] == SAFE
    assert fake.call_count == 0

    # klik bez aktivnog zadatka
    response = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, interaction_type="choice_answer",
        selected_option_id="a", client_turn_id="x"))
    assert response["answer"] == SAFE
    assert fake.call_count == 0


def test_no_turn_ever_makes_a_third_call():
    """Svaki ishod (uspjeh, ispravka, fail_closed, serverska greška) staje na
    najviše dva poziva."""
    scenarios = [
        lambda f: queue_two_call(f),
        lambda f: (f.queue(make_tutor_draft()),
                   f.queue(make_reviewer_final(decision="fail_closed",
                                               fail_reason_code="math_incorrect"))),
        lambda f: (f.queue(make_tutor_draft()),
                   f.queue(make_reviewer_final(decision="correct"))),
        lambda f: (f.queue(make_tutor_draft(new_task=make_task_payload(
                       options=("$1$", "$1$", "$2$", "$3$")))),
                   f.queue(make_reviewer_final())),
    ]
    for index, prepare in enumerate(scenarios):
        store, fake = SessionStore(), FakeLLM()
        prepare(fake)
        run_practice_turn(store, fake, turn_for("6-04-009", 6,
                                                session_id=f"third-{index}"))
        assert fake.call_count <= 2, index


# ---------------------------------------------------------------------------
# 15-17: stanje
# ---------------------------------------------------------------------------

def test_rejection_never_mutates_session_state():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    before = copy.deepcopy(store.peek("uni-6-04-009"))

    duplicated = make_tutor_draft(new_task=make_task_payload(
        options=("$1$", "$1$", "$2$", "$3$"), expected="$1$"))
    for prepare in (
        lambda: (fake.queue(make_tutor_draft()),
                 fake.queue(make_reviewer_final(decision="fail_closed",
                                                fail_reason_code="math_incorrect"))),
        lambda: fake.queue(LLMTimeout("APITimeoutError")),
        # Recenzent odobrava payload koji SERVERSKA provjera opcija mora oboriti.
        lambda: (fake.queue(duplicated),
                 fake.queue(make_reviewer_final(final=duplicated))),
    ):
        prepare()
        response = run_practice_turn(store, fake, turn_for("6-04-009", 6))
        assert response["answer"] == SAFE
        assert store.peek("uni-6-04-009") == before


def test_switching_lesson_isolates_state_and_stale_clicks_cannot_overwrite():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    first = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, session_id="shared"))
    stale_task = first["last_tutor_task"]
    stale_option = store.peek("shared")["correct_option_id"]

    # Ista sesija, DRUGA lekcija → svjež kontekst.
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-014", 6, session_id="shared"))
    assert store.peek("shared")["lesson_id"] == "6-04-014"

    # Zakašnjeli klik iz stare lekcije ne smije ništa prepisati ni pozvati model.
    calls_before = fake.call_count
    response = run_practice_turn(store, fake, turn_for(
        "6-04-009", 6, session_id="shared", interaction_type="choice_answer",
        selected_option_id=stale_option, last_tutor_task=stale_task,
        client_turn_id="stale-1"))
    assert "status" not in response
    assert fake.call_count == calls_before
    assert store.peek("shared")["current_task"] == ""


def test_idempotent_click_retry_makes_no_extra_call():
    store, fake = SessionStore(), FakeLLM()
    queue_two_call(fake)
    run_practice_turn(store, fake, turn_for("6-04-009", 6))
    session = store.peek("uni-6-04-009")
    wrong = next(o["id"] for o in session["current_options"]
                 if o["id"] != session["correct_option_id"])

    queue_two_call(fake, draft=make_tutor_draft(
        intent="answer_attempt", reply="Nije tačno.", grading="incorrect", new_task=None))
    click = turn_for("6-04-009", 6, interaction_type="choice_answer",
                     selected_option_id=wrong, student_message="[klik]",
                     client_turn_id="same-id")
    first = run_practice_turn(store, fake, click)
    calls_after_first = fake.call_count
    second = run_practice_turn(store, fake, click)

    assert first == second
    assert fake.call_count == calls_after_first     # bez ijednog novog poziva


def test_no_lesson_silently_enters_another_execution_path(monkeypatch):
    """Ni jedna lekcija ne smije skliznuti na zamrznuti jednopozivni put."""
    from matbot import practice as practice_module

    def _explode(*args, **kwargs):
        raise AssertionError("zamrznuti jednopozivni put NE SMIJE biti pozvan")

    monkeypatch.setattr(practice_module, "_run_legacy_single_call_turn", _explode)
    for grade, topic in [(6, "6-04-009"), (6, "6-04-014"), (9, "9-05-004")]:
        store, fake = SessionStore(), FakeLLM()
        queue_two_call(fake)
        response = run_practice_turn(store, fake, turn_for(topic, grade))
        assert response["status"] == "ready", topic


# ---------------------------------------------------------------------------
# 18-19: parnost i odsustvo grananja po lekciji
# ---------------------------------------------------------------------------

def test_legacy_family_mapping_parity_is_still_intact():
    """Mapiranje bez ugovora ostaje netaknuto — sada kao KONTEKST, ne kao put.

    Broj raste kad kurikulum dobije lekciju bez K1/K3 ugovora (dvije nove
    lekcije Skupova), pa se čita iz zamrznutog baseline-a, ne iz konstante."""
    baseline = json.loads(
        (ROOT / "tests" / "fixtures" / "legacy_routing_baseline.json").read_text(
            encoding="utf-8")
    )
    rows = baseline["lessons"] if isinstance(baseline, dict) else baseline
    assert len(rows) == 530
    checked = 0
    for row in rows:
        topic = row["topic_id"]
        context = lesson_context_module.build(int(topic[0]), topic)
        assert context is not None, topic
        assert list(context.families) == list(row["families"]), topic
        checked += 1
    assert checked == 530


def test_no_lesson_id_branching_in_the_universal_engine():
    topic_re = re.compile(r"\b\d-\d{2}-\d{3}\b")
    offenders = []
    for path in sorted((ROOT / "matbot" / "tutor").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if topic_re.search(line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"ID lekcije u univerzalnom motoru: {offenders}"


def test_no_lesson_title_in_the_universal_engine():
    titles = {
        lesson["title"]
        for payload in load_topics()["grades"].values()
        for lesson in payload["lessons"]
        if len(lesson["title"]) >= 12
    }
    blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "matbot" / "tutor").glob("*.py")
    )
    found = sorted(title for title in titles if title in blob)
    assert not found, f"naziv lekcije u kodu motora: {found}"


def test_one_universal_prompt_serves_every_lesson():
    """Prompt se GRADI iz konteksta; nema 534 ručno pisana prompta."""
    sampled = [LESSONS[i] for i in range(0, len(LESSONS), 37)]
    shapes = set()
    for grade, topic in sampled:
        context = lesson_context_module.build(grade, topic)
        instructions = tutor_prompts.build_tutor_instructions(context)
        # Skelet je isti; razlikuje se samo umetnuti kontekst.
        shapes.add(instructions.count("ODREDI NAMJERU"))
        assert context.title in tutor_prompts.build_tutor_input(
            context,
            {"current_task": "", "current_options": [], "expected_answer_summary": "",
             "difficulty": "standard", "hint_level": 0, "recent_tasks": [],
             "recent_turns": []},
            "Daj mi zadatak.",
        )
    assert shapes == {1}


# ---------------------------------------------------------------------------
# K1/K3 sačuvan, ali ne blokira univerzalni put
# ---------------------------------------------------------------------------

def test_deterministic_k1_k3_generator_is_preserved_and_still_works():
    import random as _random

    from matbot.contracts import generator

    contract = contract_registry.contract_for("6-04-009")
    skeleton = generator.generate(contract, "direct_computation", rng=_random.Random(0))
    ok, code = generator.self_verify(contract, skeleton)
    assert ok, code


def test_universal_pipeline_is_opt_in_and_never_the_default(monkeypatch):
    """ROLLBACK 2026-08-03: podrazumijevano je STABILAN jednopozivni put; ovaj
    (univerzalni) put se uključuje SAMO eksplicitnom zastavicom. Puna kapija
    živi u tests/test_practice_pipeline_selection.py."""
    from matbot import practice as practice_module

    monkeypatch.delenv("MATBOT_PRACTICE_PIPELINE", raising=False)
    assert practice_module._universal_pipeline_enabled() is False
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE",
                       practice_module.UNIVERSAL_PIPELINE_FLAG)
    assert practice_module._universal_pipeline_enabled() is True


# ---------------------------------------------------------------------------
# Faza 4H: ovi testovi ispituju MODEL-strategiju (Tutor+Recenzent) i na
# porodičnim lekcijama koje produkcija sada rutira deterministički. Izričito
# isključenje je ISTI mehanizam koji služi i kao produkcijski rollback
# (MATBOT_DETERMINISTIC_PRACTICE=disabled) — model-put time ostaje trajno
# testiran, bajt za bajt kakav je i bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_f4h


@_pytest_f4h.fixture(autouse=True)
def _model_route_only_f4h(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
