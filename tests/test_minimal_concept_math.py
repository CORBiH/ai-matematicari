# -*- coding: utf-8 -*-
"""Conceptual explanations must not invent arithmetic (2026-07-21 13:4x).

Production question: "a sta ako imamo 2/13 i treba prosiriti na nazivnik 24".
The model answered: factor = 24/13, 2 × (24/13) = 48/24 — every step false.
2/13 cannot be expanded to denominator 24, because 24 is not a multiple of 13.

The numbers now come from ``concept_facts``; the model may only rephrase text
that already contains them. Also covers the "brojemnikom" spelling slip and the
live Sheets write diagnostic.
"""
import json
import logging

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr
from matbot.minimal import concept_facts, renderer

STREAM_URL = "/api/ai-tutor/chat/stream"
PROD_TOPIC = "12880"
PROD_Q = "a sta ako imamo 2/13 i treba prosiriti na nazivnik 24"


def prod_payload(**overrides):
    payload = {
        "session_id": "concept-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": PROD_TOPIC, "selected_oblast": "Razlomci",
        "student_message": "Daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "a.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "on")
    for f in ("MATBOT_ENGINE_V2", "MATBOT_ENGINE_V2_GRADING",
              "MATBOT_ENGINE_V2_PRACTICE", "MATBOT_ENGINE_V2_EXAM"):
        monkeypatch.setenv(f, "off")
    tr.reset_cache()
    yield
    tr.reset_cache()


def sse(client, payload):
    resp = client.post(STREAM_URL, json=payload)
    assert resp.status_code == 200, resp.data
    name = None
    for line in resp.get_data(as_text=True).splitlines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name == "done":
            return json.loads(line.split(":", 1)[1].strip())
    raise AssertionError("no done event")


# =========================================================================== #
# Deterministic facts                                                         #
# =========================================================================== #
def test_2_over_13_to_24_is_impossible():
    facts = concept_facts.resolve_expand_question(PROD_Q)
    assert facts is not None
    assert facts.possible is False
    assert facts.reason == "target_denominator_not_multiple"
    assert facts.original == "2/13"
    assert facts.target_denominator == 24
    assert facts.expanded == ""              # nothing is computed


def test_2_over_13_to_26_gives_factor_2():
    facts = concept_facts.resolve_expand_question(
        "sta ako imamo 2/13 i treba prosiriti na nazivnik 26")
    assert facts.possible is True
    assert facts.factor == 2
    assert facts.expanded == "4/26"


def test_3_over_5_expanded_by_7():
    facts = concept_facts.resolve_expand_question("sta ako imamo 3/5 i prosirimo sa 7")
    assert facts.possible is True
    assert facts.factor == 7
    assert facts.expanded == "21/35"
    assert facts.value_is_one is False


def test_5_over_5_expanded_by_10_stays_one():
    facts = concept_facts.resolve_expand_question("sta ako imamo 5/5 i prosirimo sa 10")
    assert facts.possible is True
    assert facts.expanded == "50/50"
    assert facts.value_is_one is True


def test_same_numerator_and_denominator_case():
    facts = concept_facts.resolve_expand_question(
        "a reci mi sta ako imamo isti brojnik i isti nazvivnik i trebamo prosiriti brojem 10")
    assert facts.possible is True
    assert facts.kind == "same_numerator_denominator"
    assert facts.factor == 10
    assert facts.expanded == "50/50"
    assert facts.value_is_one is True


@pytest.mark.parametrize("question", [
    "zasto se to tako radi",
    "sta ako je nazivnik nula",
    "a sta ako imamo 2/0 i nazivnik 10",
    "kako to mislis",
])
def test_unparseable_questions_yield_no_facts(question):
    assert concept_facts.resolve_expand_question(question) is None


def test_impossible_explanation_states_the_reason_and_no_result():
    facts = concept_facts.resolve_expand_question(PROD_Q)
    text = concept_facts.explain(facts)
    assert "2/13" in text and "24" in text
    assert "13" in text
    assert "48/24" not in text               # the production fabrication
    assert "48" not in text


# =========================================================================== #
# End to end over the SSE route                                               #
# =========================================================================== #
def test_prod_question_never_produces_48_over_24(client, fake_openai):
    """Even a hostile model reply cannot introduce the wrong numbers."""
    fake_openai.state["reply"] = (
        "Faktor je 24/13, pa je 2 puta 24/13 jednako 48/24.")
    body = sse(client, prod_payload(student_message=PROD_Q))
    answer = body["answer"]
    assert "48/24" not in answer, answer
    assert "48" not in answer, answer
    assert "2/13" in answer
    assert "24" in answer
    # nothing was created or graded
    assert body["last_tutor_task"] == ""
    assert body["answer_verdict"] is None


def test_prod_question_says_it_is_impossible(client, fake_openai):
    fake_openai.state["reply"] = "Neka parafraza."
    body = sse(client, prod_payload(student_message=PROD_Q))
    low = body["answer"].lower()
    assert "ne može" in low or "nije djeljiv" in low, body["answer"]


def test_valid_expansion_question_is_answered_correctly(client, fake_openai):
    fake_openai.state["reply"] = "Neka parafraza."
    body = sse(client, prod_payload(
        student_message="sta ako imamo 2/13 i treba prosiriti na nazivnik 26"))
    assert "4/26" in body["answer"], body["answer"]


def test_same_numerator_question_end_to_end(client, fake_openai):
    fake_openai.state["reply"] = "Neka parafraza."
    body = sse(client, prod_payload(
        student_message="sta ako imamo isti brojnik i nazivnik i prosirimo sa 10"))
    assert "50/50" in body["answer"], body["answer"]
    assert "1" in body["answer"]


def test_model_may_rephrase_but_not_change_numbers(client, fake_openai):
    fake_openai.state["reply"] = "Nazivnik 5 pomnožiš sa 7, pa je 3 · 7 = 21, dakle 21/35."
    body = sse(client, prod_payload(
        student_message="sta ako imamo 3/5 i prosirimo sa 7"))
    assert "21/35" in body["answer"]


@pytest.mark.parametrize("reply", [
    "Rezultat je 999/111.",
    "Faktor je 24/13, pa je 2 puta 24/13 = 48/24.",
    "To je 5 * 7 = 35.",
])
def test_unparseable_numeric_question_gets_no_calculation(client, fake_openai, reply):
    """No verified facts → the model may explain, but may NOT calculate."""
    fake_openai.state["reply"] = reply
    body = sse(client, prod_payload(student_message="a sta ako je nazivnik nula"))
    answer = body["answer"]
    assert "999/111" not in answer
    assert "48/24" not in answer
    assert not renderer._CALCULATION_RE.search(answer), answer
    assert body["last_tutor_task"] == ""
    assert body["answer_verdict"] is None


def test_non_numeric_model_explanation_is_still_allowed(client, fake_openai):
    fake_openai.state["reply"] = (
        "Nazivnik nikada ne smije biti nula jer dijeljenje nulom nije definisano.")
    body = sse(client, prod_payload(student_message="a sta ako je nazivnik nula"))
    assert "nula" in body["answer"].lower()


def test_concept_question_with_an_active_task_is_unchanged(client, fake_openai):
    first = sse(client, prod_payload(student_message="daj mi zadatak"))
    tid = first["next_state"]["task_id"]
    before = first["next_state"]["minimal_state"]["active_task"]
    fake_openai.state["reply"] = "Neka parafraza."
    body = sse(client, prod_payload(
        student_message=PROD_Q,
        interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    after = body["next_state"]["minimal_state"]["active_task"]
    assert body["next_state"]["task_id"] == tid
    assert after["attempts"] == before["attempts"]
    assert after["wrong_attempts"] == before["wrong_attempts"]
    assert body["next_state"]["correct_streak"] == first["next_state"]["correct_streak"]
    assert body["answer_verdict"] is None
    assert "48/24" not in body["answer"]


# =========================================================================== #
# Language quality                                                            #
# =========================================================================== #
@pytest.mark.parametrize("typo", ["brojemnikom", "brojemnik", "nazivnikom "])
def test_known_spelling_slips_are_not_produced(client, fake_openai, typo):
    """"brojemnikom" appeared in production; deterministic text must be clean."""
    fake_openai.state["reply"] = f"Pomnožiš sa {typo} i gotovo."
    body = sse(client, prod_payload(
        student_message="sta ako imamo 3/5 i prosirimo sa 7"))
    assert "brojemnik" not in body["answer"].lower(), body["answer"]


def test_deterministic_concept_text_is_policy_clean():
    for question in ("sta ako imamo 2/13 i treba prosiriti na nazivnik 24",
                     "sta ako imamo 2/13 i treba prosiriti na nazivnik 26",
                     "sta ako imamo 3/5 i prosirimo sa 7",
                     "sta ako imamo 5/5 i prosirimo sa 10",
                     "sta ako imamo isti brojnik i nazivnik i prosirimo sa 10"):
        facts = concept_facts.resolve_expand_question(question)
        text = concept_facts.explain(facts)
        assert not renderer.has_cyrillic(text), text
        assert not renderer.is_gendered(text), text
        assert "brojemnik" not in text.lower(), text
        assert "brojnik" in text or "nazivnik" in text


# =========================================================================== #
# Live Sheets write diagnostic                                                #
# =========================================================================== #
class _FakeSpreadsheet:
    def __init__(self, stored):
        self.stored = stored

    def values_get(self, rng, params=None):
        return {"values": [self.stored]}


class _FakeWorksheet:
    col_count = 62

    def __init__(self, corrupt=False):
        self.corrupt = corrupt
        self.spreadsheet = None
        self.sent = None

    def get_all_values(self):
        return [["h"] * 62, ["old"] * 62]

    def row_values(self, n):
        return []

    def append_row(self, values, value_input_option=None):
        self.sent = list(values)
        stored = [("511" if v == "" else v) for v in values] if self.corrupt \
            else list(values)
        self.spreadsheet = _FakeSpreadsheet(stored)
        return {"updates": {"updatedRange": "Sheet1!A3:BJ3",
                            "updatedColumns": 62, "updatedCells": 62}}


@pytest.fixture()
def diag(monkeypatch, caplog):
    monkeypatch.setenv(sheets_log.SHEETS_DIAGNOSTIC_ENV, "1")
    monkeypatch.setattr(sheets_log, "_ensure_sheet_layout", lambda ws: None)
    caplog.set_level(logging.INFO, logger="matbot.sheets")
    return caplog


def _row():
    return sheets_log._build_transcript_row(
        {"session_id": "s", "student_message": "4/12"},
        {"answer": "ok", "next_state": {}})


def test_diagnostic_is_off_by_default(monkeypatch):
    monkeypatch.delenv(sheets_log.SHEETS_DIAGNOSTIC_ENV, raising=False)
    assert sheets_log._diagnostic_enabled() is False


def test_diagnostic_reports_no_mismatch_on_a_faithful_write(diag, monkeypatch):
    ws = _FakeWorksheet(corrupt=False)
    monkeypatch.setattr(sheets_log, "_init_sheets", lambda: ws)
    sheets_log._append_row_once(_row())
    text = diag.text
    assert "SHEETS_DIAG pre_write cells=62" in text
    assert "first_mismatch=none" in text


def test_diagnostic_pinpoints_the_first_corrupted_cell(diag, monkeypatch):
    ws = _FakeWorksheet(corrupt=True)
    monkeypatch.setattr(sheets_log, "_init_sheets", lambda: ws)
    sheets_log._append_row_once(_row())
    line = [ln for ln in diag.text.splitlines() if "first_mismatch=" in ln][-1]
    payload = json.loads(line.split("first_mismatch=", 1)[1])
    assert payload["index"] == 3
    assert payload["header"] == "message_index"
    assert payload["sent"] == "''"
    assert payload["got"] == "'511'"


def test_diagnostic_logs_the_updated_range_and_target_row(diag, monkeypatch):
    ws = _FakeWorksheet()
    monkeypatch.setattr(sheets_log, "_init_sheets", lambda: ws)
    sheets_log._append_row_once(_row())
    assert "updatedRange=Sheet1!A3:BJ3" in diag.text
    assert "target rows_with_data=2 next_row=3" in diag.text
    assert "target_row_before_append non_empty=False" in diag.text


def test_diagnostic_describes_every_column(diag, monkeypatch):
    ws = _FakeWorksheet()
    monkeypatch.setattr(sheets_log, "_init_sheets", lambda: ws)
    sheets_log._append_row_once(_row())
    line = [ln for ln in diag.text.splitlines() if "pre_write" in ln][-1]
    described = json.loads(line.split("payload=", 1)[1])
    assert len(described) == 62
    assert described[0]["header"] == "timestamp_iso"
    assert described[61]["header"] == "minimal_routing"
    assert all({"index", "header", "type", "repr"} <= set(d) for d in described)


def test_the_written_row_still_has_no_local_sentinel(monkeypatch):
    ws = _FakeWorksheet()
    monkeypatch.setattr(sheets_log, "_init_sheets", lambda: ws)
    monkeypatch.setattr(sheets_log, "_ensure_sheet_layout", lambda w: None)
    sheets_log._append_row_once(_row())
    assert len(ws.sent) == 62
    assert all(str(v) != "511" for v in ws.sent)
    assert ws.sent[16] == "4/12"             # literal fraction survives RAW
