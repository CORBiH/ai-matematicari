# -*- coding: utf-8 -*-
"""The conversation DECISION must be visible in telemetry, not just the outcome.

Adds to ``minimal_routing`` (an existing Sheets column — nothing moves):
turn_intent, intent_source, concept_fact_kind, concept_facts_resolved,
pending_confirmation_before/after, confirmation_choice.

Also pins two audit rules: ``student_answer`` stays empty on non-grading turns,
and ``student_message`` is always the exact raw text.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr

STREAM_URL = "/api/ai-tutor/chat/stream"
PROD_TOPIC = "12880"


def prod_payload(**overrides):
    payload = {
        "session_id": "tele-1", "grade": 6, "mode": "practice",
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


@pytest.fixture()
def sheets(monkeypatch):
    rows = []
    monkeypatch.setattr(svc, "log_transcript_to_sheet",
                        lambda p, r: rows.append((p, r)))
    return rows


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


def routing(body):
    return body["minimal_routing"]


FIELDS = ("turn_intent", "intent_source", "concept_fact_kind",
          "concept_facts_resolved", "pending_confirmation_before",
          "pending_confirmation_after", "confirmation_choice")


# =========================================================================== #
# The three production cases                                                  #
# =========================================================================== #
def test_explicit_factor_question(client, fake_openai):
    """"Proširi 3/5 brojem 7." — a demonstration request, not an answer."""
    fake_openai.state["reply"] = "Parafraza."
    r = routing(sse(client, prod_payload(student_message="Proširi 3/5 brojem 7.")))
    assert r["turn_intent"] == "CONCEPT_QUESTION"
    assert r["intent_source"] == "deterministic"
    assert r["concept_fact_kind"] == "explicit_factor"
    assert r["concept_facts_resolved"] is True


def test_why_same_factor_question(client, fake_openai):
    fake_openai.state["reply"] = "Parafraza."
    r = routing(sse(client, prod_payload(
        student_message="Zašto množimo i brojnik i nazivnik istim brojem?")))
    assert r["turn_intent"] == "CONCEPT_QUESTION"
    assert r["concept_fact_kind"] == "why_same_factor"
    assert r["concept_facts_resolved"] is True


def test_task_or_explanation_confirmation_flow(client, fake_openai):
    """An ambiguous turn asks the question; "objasnjenje" answers it."""
    fake_openai.state["reply"] = "OTHER"
    first = sse(client, prod_payload(student_message="asdf"))
    assert routing(first)["pending_confirmation_after"] == "task_or_explanation"

    fake_openai.state["reply"] = "Parafraza."
    second = sse(client, prod_payload(student_message="objasnjenje",
                                      previous_next_state=first["next_state"]))
    r = routing(second)
    assert r["pending_confirmation_before"] == "task_or_explanation"
    assert r["pending_confirmation_after"] == ""
    assert r["confirmation_choice"] == "explanation"
    assert r["turn_intent"] == "CONCEPT_QUESTION"


def test_task_choice_creates_a_task(client, fake_openai):
    fake_openai.state["reply"] = "OTHER"
    first = sse(client, prod_payload(student_message="asdf"))
    second = sse(client, prod_payload(student_message="zadatak",
                                      previous_next_state=first["next_state"]))
    assert routing(second)["confirmation_choice"] == "task"
    assert second["last_tutor_task"]


# =========================================================================== #
# Field coverage and correctness                                              #
# =========================================================================== #
@pytest.mark.parametrize("message", ["Daj mi zadatak", "ne znam", "4/12",
                                     "Daj mi teži zadatak", "asdf"])
def test_every_turn_reports_all_fields(client, fake_openai, message):
    fake_openai.state["reply"] = "OTHER"
    first = sse(client, prod_payload())
    body = sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    r = routing(body)
    for field in FIELDS:
        assert field in r, (message, field)
    assert r["turn_intent"], message


def test_intent_source_is_deterministic_for_known_phrases(client):
    body = sse(client, prod_payload(student_message="daj mi zadatak"))
    r = routing(body)
    assert r["turn_intent"] == "NEW_TASK"
    assert r["intent_source"] == "deterministic"


def test_intent_source_is_model_when_rules_are_undecided(client, fake_openai):
    fake_openai.state["reply"] = "CONCEPT_QUESTION"
    body = sse(client, prod_payload(student_message="hmm a to vrijedi uvijek"))
    r = routing(body)
    assert r["intent_source"] == "model"
    assert r["turn_intent"] == "CONCEPT_QUESTION"


def test_intent_source_is_confirmation_after_a_yes(client):
    first = sse(client, prod_payload())
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(first["last_tutor_task"])
    solved = sse(client, prod_payload(
        student_message=getattr(exp, "expected_display", "") or _fmt_expected(exp),
        interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    assert routing(solved)["pending_confirmation_after"] == "new_task"
    body = sse(client, prod_payload(student_message="da",
                                    previous_next_state=solved["next_state"]))
    r = routing(body)
    assert r["pending_confirmation_before"] == "new_task"
    assert r["pending_confirmation_after"] == ""
    assert r["confirmation_choice"] == "task"
    assert r["intent_source"] == "confirmation"


def test_declining_is_reported(client):
    first = sse(client, prod_payload())
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(first["last_tutor_task"])
    solved = sse(client, prod_payload(
        student_message=getattr(exp, "expected_display", "") or _fmt_expected(exp),
        interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    body = sse(client, prod_payload(student_message="ne",
                                    previous_next_state=solved["next_state"]))
    assert routing(body)["confirmation_choice"] == "decline"


def test_grading_turns_report_no_concept_facts(client):
    first = sse(client, prod_payload())
    body = sse(client, prod_payload(
        student_message="4/12", interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    r = routing(body)
    assert r["turn_intent"] == "ANSWER"
    assert r["concept_facts_resolved"] is False
    assert r["concept_fact_kind"] == ""


def test_unresolvable_concept_question_is_reported_as_unresolved(client, fake_openai):
    fake_openai.state["reply"] = "Nazivnik ne smije biti nula."
    body = sse(client, prod_payload(student_message="a sta ako je nazivnik nula"))
    r = routing(body)
    assert r["turn_intent"] == "CONCEPT_QUESTION"
    assert r["concept_facts_resolved"] is False
    assert r["concept_fact_kind"] == ""


@pytest.mark.parametrize("question,kind", [
    ("sta ako imamo 2/13 i treba prosiriti na nazivnik 24", "target_not_multiple"),
    ("sta ako imamo 2/13 i treba prosiriti na nazivnik 26", "target_denominator"),
    ("sta ako imamo 3/5 i prosirimo sa 7", "explicit_factor"),
    ("sta ako imamo isti brojnik i nazivnik i prosirimo sa 10",
     "same_numerator_denominator"),
    ("zasto mnozimo i brojnik i nazivnik", "why_same_factor"),
])
def test_each_concept_fact_kind_is_reported(client, fake_openai, question, kind):
    fake_openai.state["reply"] = "Parafraza."
    r = routing(sse(client, prod_payload(student_message=question)))
    assert r["concept_fact_kind"] == kind, question
    assert r["concept_facts_resolved"] is True


# =========================================================================== #
# Telemetry reaches Sheets without moving a column                            #
# =========================================================================== #
def test_trace_reaches_the_minimal_routing_column(client, fake_openai, sheets):
    fake_openai.state["reply"] = "Parafraza."
    sse(client, prod_payload(student_message="Proširi 3/5 brojem 7."))
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    stored = json.loads(row[sheets_log.SHEET_HEADERS.index("minimal_routing")])
    assert stored["turn_intent"] == "CONCEPT_QUESTION"
    assert stored["intent_source"] == "deterministic"
    assert stored["concept_fact_kind"] == "explicit_factor"
    assert stored["concept_facts_resolved"] is True
    # the routing fields from earlier rounds are still there
    assert stored["runtime_topic"] == PROD_TOPIC
    assert stored["resolved_skill"] == "fraction_expand"


def test_no_sheets_column_moved():
    headers = sheets_log.SHEET_HEADERS
    assert len(headers) == 62
    assert headers.index("student_message") == 16
    assert headers.index("student_answer") == 25
    assert headers.index("engine_canary") == 59
    assert headers[-2:] == ["internal_instruction", "minimal_routing"]


# =========================================================================== #
# Audit rules                                                                 #
# =========================================================================== #
@pytest.mark.parametrize("message", ["Daj mi zadatak", "ne znam",
                                     "Zašto množimo i brojnik i nazivnik?",
                                     "asdf"])
def test_student_answer_is_empty_on_non_grading_turns(client, fake_openai,
                                                      sheets, message):
    fake_openai.state["reply"] = "Parafraza."
    first = sse(client, prod_payload())
    sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    assert row[sheets_log.SHEET_HEADERS.index("student_answer")] == "", message


def test_student_answer_is_populated_on_grading_turns(client, sheets):
    first = sse(client, prod_payload())
    sse(client, prod_payload(
        student_message="4/12", interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    assert row[sheets_log.SHEET_HEADERS.index("student_answer")] == "4/12"


@pytest.mark.parametrize("message", [
    "  Daj mi ZADATAK  ",
    "Zašto množimo i brojnik i nazivnik istim brojem?",
    "a sta ako imamo 2/13 i treba prosiriti na nazivnik 24",
    "4/12",
])
def test_student_message_is_the_exact_raw_text(client, fake_openai, sheets, message):
    fake_openai.state["reply"] = "Parafraza."
    sse(client, prod_payload(student_message=message))
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    assert row[sheets_log.SHEET_HEADERS.index("student_message")] == message
