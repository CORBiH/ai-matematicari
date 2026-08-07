"""Faza 4H, Workstream A — sigurna dijagnostika latencije i tokena.

Nalaz forenzike: per-stage vrijeme nije postojalo (samo SDK latency_ms), pa se
iz loga nije moglo pročitati gdje turn stvarno troši vrijeme, a `cached_tokens`
iz Responses API usage-a se u potpunosti odbacivao — stopa pogotka prompt
keša je bila nemjerljiva.

Dijagnostika NE SMIJE promijeniti ponašanje i NE SMIJE nositi sadržaj:
ni prompt, ni tekst zadatka, ni poruku učenika, ni tajne.
"""
import pytest

from matbot import llm as llm_module
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import (FakeLLM, make_reviewer_final, make_task_payload,
                            make_tutor_draft)

GRADE, TOPIC = 6, "6-03-004"


def turn(message="Daj mi zadatak.", **changes):
    payload = {
        "session_id": "diag-1", "grade": GRADE, "selected_topic": TOPIC,
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


def publish_one(store, fake):
    task = make_task_payload(
        text="Koji od ponuđenih brojeva je djeljiv sa 25?",
        options=("725", "714", "738", "741"), correct_option_index=0,
        expected="725")
    draft = make_tutor_draft(intent="generate_task", new_task=task)
    fake.queue(draft)
    fake.queue(make_reviewer_final(final=draft))
    return run_practice_turn(store, fake, turn())


# ---------------------------------------------------------------------------
# 1) CACHED TOKENI IZ USAGE-A
# ---------------------------------------------------------------------------

class _Details:
    def __init__(self, cached):
        self.cached_tokens = cached


class _Usage:
    input_tokens = 6000
    output_tokens = 1200

    def __init__(self):
        self.input_tokens_details = _Details(4096)
        self.output_tokens_details = _Details.__new__(_Details)
        self.output_tokens_details.reasoning_tokens = 448


class _Resp:
    usage = None

    def __init__(self):
        self.usage = _Usage()


def test_usage_dict_surfaces_cached_input_tokens():
    usage = llm_module._usage_dict(_Resp())
    assert usage["input_tokens"] == 6000
    assert usage["cached_input_tokens"] == 4096
    assert usage["reasoning_tokens"] == 448


def test_usage_dict_survives_missing_details():
    class Bare:
        class usage:
            input_tokens = 10
            output_tokens = 5
    usage = llm_module._usage_dict(Bare())
    assert usage["input_tokens"] == 10
    assert "cached_input_tokens" not in usage or usage["cached_input_tokens"] is None


# ---------------------------------------------------------------------------
# 2) TURN DIJAGNOSTIKA: ruta, faze, ukupno vrijeme — bez sadržaja
# ---------------------------------------------------------------------------

def test_text_turn_diagnostics_carry_route_stages_and_total(universal, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    store, fake = SessionStore(), FakeLLM()
    response = publish_one(store, fake)
    assert response["status"] == "ready"

    line = next(l for l in caplog.text.splitlines()
                if "tutor_turn_diagnostics" in l)
    assert "route=model_tutor_reviewer" in line
    assert "total_ms=" in line
    assert "stage_ms=" in line
    assert "family=" in line
    # Faze koje forenzika traži: prompt, tutor api, validacije, objava.
    for stage in ("tutor_api", "publish"):
        assert stage + ":" in line, line
    # Bez sadržaja: ni poruka učenika ni tekst zadatka ni prompt.
    assert "Daj mi zadatak" not in line
    assert "djeljiv" not in line
    assert "nastavnik" not in line          # riječ iz instrukcija prompta


def test_choice_turn_diagnostics_carry_route_and_total(universal, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    store, fake = SessionStore(), FakeLLM()
    publish_one(store, fake)
    session = store.peek("diag-1")
    correct_id = session["correct_option_id"]
    fake.queue(make_tutor_draft(intent="answer_attempt", reply="Tačno!",
                                grading="correct"))

    response = run_practice_turn(store, fake, turn(
        message="[odgovor]", interaction_type="choice_answer",
        selected_option_id=correct_id, client_turn_id="c1"))

    assert response["answer_verdict"] == "correct"
    line = next(l for l in caplog.text.splitlines() if "tutor_choice" in l)
    assert "route=model_tutor_reviewer" in line
    assert "total_ms=" in line


def test_rejected_turn_diagnostics_still_have_route_and_total(universal, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="matbot.tutor")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(llm_module.LLMTimeout("APITimeoutError"))

    response = run_practice_turn(store, fake, turn())

    assert "status" not in response
    line = next(l for l in caplog.text.splitlines()
                if "tutor_turn_diagnostics" in l)
    assert "route=model_tutor_reviewer" in line
    assert "total_ms=" in line


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
