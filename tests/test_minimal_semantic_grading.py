# -*- coding: utf-8 -*-
"""SemanticAnswerJudge, driven through the real SSE route.

Stop adding phrase-by-phrase regex patches: the divisibility regression
("da jer je zadnja cifra 0" rejected, "ne jer je neparan" rejected, "jer je
broj paran" routed as a concept question) proved that enumerating every
child's phrasing does not scale. This file proves the shared hybrid grading
layer (``matbot/minimal/semantic_grading.py``) end to end via the ACTUAL
browser route, using the existing ``fake_openai`` fixture to supply a canned
judgment per turn — never a real network call.

``MATBOT_SEMANTIC_GRADING`` is off by default; every test here sets it
explicitly (shadow or on) via monkeypatch, so the rest of the suite (which
does not set it) proves the feature is inert until deliberately enabled.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr

STREAM_URL = "/api/ai-tutor/chat/stream"
DIV_TOPIC = "6-03-024"
DIV_OBLAST = "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25"


def prod_payload(**overrides):
    payload = {
        "session_id": "sem-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": DIV_TOPIC, "selected_oblast": DIV_OBLAST,
        "student_message": "daj mi zadatak", "conversation_history": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "sem.sqlite3"))
    monkeypatch.setenv("MATBOT_MINIMAL_ENGINE", "on")
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "off")   # explicit default
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


def seeded(client, question, expected, turn_id="seed"):
    state = sse(client, prod_payload(client_turn_id=turn_id))["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_sem", "skill_id": "divisibility", "question": question,
        "expected_display": expected, "npp_id": DIV_TOPIC, "tema_title": "t",
        "attempts": 0, "wrong_attempts": 0, "hints_given": 0,
        "solved": False, "solution_revealed": False,
        "pending_evidence_prompt": False,
    }
    state["task_id"] = "mt_sem"
    return state


def turn(client, state, message, question, turn_id="t"):
    return sse(client, prod_payload(
        student_message=message, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state,
        client_turn_id=turn_id))


def judgment(**overrides):
    payload = {
        "understood": True, "response_kind": "answer", "decision": "yes",
        "decision_source": "explicit", "final_answer_text": "",
        "claims": [], "explanation_present": True, "relevance": "direct",
        "completeness": "complete", "ambiguity": "", "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


TASK_110_10 = "Provjeri da li je broj 110 djeljiv sa 10. Obrazloži svoj odgovor."
TASK_275_2 = "Provjeri da li je broj 275 djeljiv sa 2. Obrazloži svoj odgovor."
TASK_84_2 = "Provjeri da li je broj 84 djeljiv sa 2. Obrazloži svoj odgovor."
TASK_252_6 = "Provjeri da li je broj 252 djeljiv sa 6. Obrazloži svoj odgovor."


# =========================================================================== #
# 1/12. Deterministic bare answers use zero model calls, regardless of mode   #
# =========================================================================== #
def test_bare_answer_zero_model_calls_even_with_semantic_on(client, monkeypatch,
                                                             fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    state = seeded(client, TASK_110_10, "da")
    body = turn(client, state, "da", question=TASK_110_10)
    assert body["answer_verdict"] == "partial"
    assert len(fake_openai.calls.messages) == 0


def test_fully_matching_deterministic_answer_never_uses_the_judge(client,
                                                                  monkeypatch,
                                                                  fake_openai):
    """A "correct" verdict optionally triggers the PRE-EXISTING wording
    rephrase call (unrelated to this feature) — what must be zero here is
    specifically the SEMANTIC JUDGE, not every model call in the pipeline."""
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    state = seeded(client, TASK_110_10, "da")
    body = turn(client, state, "da, 110 je djeljiv sa 10 jer je zadnja cifra 0",
               question=TASK_110_10)
    assert body["answer_verdict"] == "correct"
    assert body["minimal_routing"].get("semantic_judge_used") is not True


def test_off_mode_never_calls_the_model_for_natural_phrasing(client, monkeypatch,
                                                              fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "off")
    state = seeded(client, TASK_110_10, "da")
    body = turn(client, state, "da jer je zadnja cifra 0", question=TASK_110_10)
    assert len(fake_openai.calls.messages) == 0
    assert body["answer_verdict"] == "partial"   # unchanged deterministic result


# =========================================================================== #
# 3. Exact production cases, end to end                                       #
# =========================================================================== #
def test_110_div_10_last_digit_zero_over_the_wire(client, monkeypatch, fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, TASK_110_10, "da")
    body = turn(client, state, "da jer je zadnja cifra 0", question=TASK_110_10)
    assert body["answer_verdict"] == "correct"
    assert body["task_status"] == "completed"
    assert body["minimal_routing"]["semantic_judge_used"] is True


def test_275_div_2_odd_over_the_wire(client, monkeypatch, fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(decision="no", claims=[
        {"type": "parity", "value": "neparan", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, TASK_275_2, "ne")
    body = turn(client, state, "ne jer je neparan", question=TASK_275_2)
    assert body["answer_verdict"] == "correct"


def test_84_div_2_implicit_yes_over_the_wire(client, monkeypatch, fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(
        decision_source="implicit", claims=[
            {"type": "parity", "value": "paran", "polarity": "positive",
             "confidence": 0.85}]))
    state = seeded(client, TASK_84_2, "da")
    body = turn(client, state, "jer je broj paran", question=TASK_84_2)
    assert body["answer_verdict"] == "correct"


def test_task_variety_comment_is_not_graded_over_the_wire(client, monkeypatch,
                                                           fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(
        response_kind="other", decision="unknown", decision_source="none",
        relevance="unrelated", completeness="absent"))
    state = seeded(client, TASK_252_6, "da")
    body = turn(client, state, "sto me pitas samo za 6", question=TASK_252_6)
    assert body["answer_verdict_detail"] == "not_checkable"
    assert body["wrong_attempt_count"] == 0
    assert body["total_attempt_count"] == 0
    assert body["next_state"]["task_id"] == "mt_sem"


# =========================================================================== #
# 4. False evidence is not accepted                                           #
# =========================================================================== #
def test_false_evidence_rejected_over_the_wire(client, monkeypatch, fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(claims=[
        {"type": "last_digit", "value": "5", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, TASK_110_10, "da")
    body = turn(client, state, "da jer je zadnja cifra 5", question=TASK_110_10)
    assert body["answer_verdict"] != "correct"
    assert body["answer_verdict_detail"] == "incorrect_evidence"
    assert body["wrong_attempt_count"] == 0      # decision was right, not wrong


# =========================================================================== #
# 5. Compound rules over the wire                                             #
# =========================================================================== #
def test_compound_6_yes_over_the_wire(client, monkeypatch, fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(claims=[
        {"type": "divisibility_factor", "value": "2", "polarity": "positive", "confidence": 0.9},
        {"type": "divisibility_factor", "value": "3", "polarity": "positive", "confidence": 0.9},
    ]))
    state = seeded(client, TASK_252_6, "da")
    body = turn(client, state, "da jer je djeljiv i sa 2 i sa 3", question=TASK_252_6)
    assert body["answer_verdict"] == "correct"


def test_compound_6_one_failing_condition_over_the_wire(client, monkeypatch,
                                                         fake_openai):
    question = "Provjeri da li je broj 155 djeljiv sa 6. Obrazloži svoj odgovor."
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(decision="no", claims=[
        {"type": "parity", "value": "neparan", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, question, "ne")
    body = turn(client, state, "ne jer nije paran", question=question)
    assert body["answer_verdict"] == "correct"


# =========================================================================== #
# 10. Counters and task lifecycle stay engine-owned                          #
# =========================================================================== #
def test_model_cannot_increment_counters_on_wrong_decision(client, monkeypatch,
                                                           fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(decision="no", claims=[
        {"type": "parity", "value": "paran", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, TASK_252_6, "da")
    body = turn(client, state, "ne, iako je paran", question=TASK_252_6)
    assert body["answer_verdict"] == "incorrect"
    assert body["wrong_attempt_count"] == 1
    assert body["next_state"]["task_id"] == "mt_sem"    # task not silently closed


def test_model_cannot_complete_the_task_on_non_answer(client, monkeypatch,
                                                       fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(
        response_kind="task_request", decision="unknown",
        decision_source="none", relevance="unrelated", completeness="absent"))
    state = seeded(client, TASK_252_6, "da")
    body = turn(client, state, "daj mi zadatak sa 25", question=TASK_252_6)
    assert body["task_status"] != "completed"
    assert body["next_state"]["minimal_state"]["solved_count"] == 0


# =========================================================================== #
# 11. Shadow mode: log differences, never change the verdict                  #
# =========================================================================== #
def test_shadow_mode_over_the_wire_leaves_verdict_unchanged(client, monkeypatch,
                                                             fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "shadow")
    fake_openai.state["reply"] = json.dumps(judgment(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, TASK_110_10, "da")
    body = turn(client, state, "da jer je zadnja cifra 0", question=TASK_110_10)
    # DETERMINISTIC verdict survives — shadow mode never changes it
    assert body["answer_verdict"] == "partial"
    assert body["answer_verdict_detail"] == "incomplete"
    assert len(fake_openai.calls.messages) == 1        # the judge DID run
    routing = body["minimal_routing"]
    assert routing.get("semantic_judge_used") is True
    assert routing.get("deterministic_claim_verification") == "correct"


def test_shadow_mode_does_not_break_streak_or_count_wrong(client, monkeypatch,
                                                           fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "shadow")
    fake_openai.state["reply"] = json.dumps(judgment(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, TASK_110_10, "da")
    body = turn(client, state, "da jer je zadnja cifra 0", question=TASK_110_10)
    assert body["wrong_attempt_count"] == 0
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["task_id"] == "mt_sem"


# =========================================================================== #
# Telemetry shape (requirement 8)                                             #
# =========================================================================== #
def test_telemetry_fields_present_in_minimal_routing(client, monkeypatch,
                                                     fake_openai):
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, TASK_110_10, "da")
    body = turn(client, state, "da jer je zadnja cifra 0", question=TASK_110_10)
    routing = body["minimal_routing"]
    for key in ("semantic_judge_used", "semantic_judge_model",
               "semantic_judge_confidence", "semantic_response_kind",
               "semantic_decision", "semantic_claims",
               "deterministic_claim_verification"):
        assert key in routing, key
    assert routing["semantic_judge_model"]


def test_sheets_columns_unchanged_with_semantic_grading_on(client, monkeypatch,
                                                            fake_openai, sheets):
    from matbot import sheets_log
    monkeypatch.setenv("MATBOT_SEMANTIC_GRADING", "on")
    fake_openai.state["reply"] = json.dumps(judgment(claims=[
        {"type": "last_digit", "value": "0", "polarity": "positive", "confidence": 0.9}]))
    state = seeded(client, TASK_110_10, "da")
    turn(client, state, "da jer je zadnja cifra 0", question=TASK_110_10)
    assert len(sheets_log.SHEET_HEADERS) == 62
    row = sheets_log._build_transcript_row(*sheets[-1])
    assert row[sheets_log.SHEET_HEADERS.index("student_message")] == \
        "da jer je zadnja cifra 0"
