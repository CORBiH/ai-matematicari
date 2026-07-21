# -*- coding: utf-8 -*-
"""Conversational routing, 2026-07-21T12:54:17Z.

With no active task, a conceptual question became a task:

  student: "a reci mi sta ako imamo isti brojnik i isti nazvivnik i trebamo
            prosiriti brojem 7"
  engine : "Proširi 2/4 na nazivnik 20."      ← the question was never answered

Cause: ``engine.py`` created a task whenever no task was active and the intent
was anything other than HELP, so OTHER/unrecognised fell into task creation.

Also covered: the 12:53:10 / 12:53:20 pair that produced the identical task
"Proširi 1/2 na nazivnik 4." under two different task ids.

Driven through the SSE route the browser uses.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import topic_resolver as tr
from matbot.minimal import intent as mintent
from matbot.minimal.intent import TurnIntent, classify, classify_turn

STREAM_URL = "/api/ai-tutor/chat/stream"
PROD_TOPIC = "12880"
CANONICAL_NPP = "6-04-035"
CONCEPT_Q = ("a reci mi sta ako imamo isti brojnik i isti nazvivnik i trebamo "
             "prosiriti brojem 7")


def prod_payload(**overrides):
    payload = {
        "session_id": "conv-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": PROD_TOPIC, "selected_oblast": "Razlomci",
        "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
        "conversation_history": [],
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


def minimal_state(body):
    return body["next_state"]["minimal_state"]


# =========================================================================== #
# Intent classification                                                       #
# =========================================================================== #
@pytest.mark.parametrize("message", [
    CONCEPT_Q,                                     # the exact production text
    "šta ako je brojnik jednak nazivniku?",
    "zašto množimo i brojnik i nazivnik?",
    "šta znači proširiti razlomak?",
    "može li nazivnik biti nula?",
    "koja je razlika između proširivanja i skraćivanja?",
    "sta ako imamo isti nazvivnik?",               # typo-heavy
])
def test_conceptual_questions_are_recognised(message):
    assert classify(message).intent is TurnIntent.CONCEPT_QUESTION, message


@pytest.mark.parametrize("message,expected", [
    ("ne znam", TurnIntent.HELP),
    ("pomozi", TurnIntent.HELP),
    ("daj mi zadatak", TurnIntent.NEW_TASK),
    ("Daj mi teži zadatak", TurnIntent.HARDER),
    ("4/12", TurnIntent.ANSWER),
])
def test_existing_intents_are_unchanged(message, expected):
    assert classify(message).intent is expected, message


# =========================================================================== #
# The constrained fallback classifier                                         #
# =========================================================================== #
class _Resp:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


def test_classifier_is_only_consulted_when_rules_are_undecided():
    calls = []

    def spy(model, messages, **kw):
        calls.append(messages)
        return _Resp("NEW_TASK")

    # a decided message must NOT reach the model
    assert classify_turn("daj mi zadatak", openai_chat=spy).intent is TurnIntent.NEW_TASK
    assert calls == []
    # an undecided one may
    classify_turn("hmmm pa dobro", openai_chat=spy)
    assert len(calls) == 1


def test_classifier_output_is_restricted_to_the_allowlist():
    for bogus in ("GRADE_IT", "correct", "", "Proširi 1/2 na nazivnik 4.",
                  "ANSWER; and the result is 5/20"):
        result = mintent.classify_with_model(
            "hmmm", openai_chat=lambda *a, **kw: _Resp(bogus), model="m")
        assert result is None or result.name in mintent.CLASSIFIER_LABELS, bogus


def test_classifier_failure_falls_back_to_other():
    def boom(*a, **kw):
        raise RuntimeError("api down")

    assert classify_turn("hmmm pa dobro", openai_chat=boom).intent is TurnIntent.OTHER


def test_classifier_sees_only_the_student_message():
    captured = {}

    def spy(model, messages, **kw):
        captured["messages"] = messages
        return _Resp("CONCEPT_QUESTION")

    classify_turn("hmmm pa dobro", openai_chat=spy)
    blob = json.dumps(captured["messages"], ensure_ascii=False)
    for leak in ("expected", "task_id", "nazivnik 20", "correct"):
        assert leak not in blob, leak


def test_classifier_can_rescue_an_unrecognised_concept_question():
    result = classify_turn(
        "hmm a to vrijedi uvijek",
        openai_chat=lambda *a, **kw: _Resp("CONCEPT_QUESTION"))
    assert result.intent is TurnIntent.CONCEPT_QUESTION
    assert result.matched == "model_classifier"


# =========================================================================== #
# Case 1: no active task + the exact production question                      #
# =========================================================================== #
def test_concept_question_without_a_task_creates_nothing(client, fake_openai):
    fake_openai.state["reply"] = (
        "Kada su brojnik i nazivnik jednaki, razlomak je jednak 1. "
        "Ako proširiš sa 7, množiš oba broja sa 7, na primjer 3/3 postaje 21/21.")
    body = sse(client, prod_payload(student_message=CONCEPT_Q))

    assert body["engine"] == "minimal"
    assert body["session_mode"] == "practice"
    # the question is ANSWERED…
    assert "21/21" in body["answer"] or "jednak" in body["answer"].lower()
    # …and nothing was created or graded
    assert body["last_tutor_task"] == ""
    assert body["next_state"]["task_id"] is None
    assert body["answer_verdict"] is None
    assert body.get("answer_check") is None
    state = minimal_state(body)
    assert state["active_task"] is None
    assert state["correct_streak"] == 0
    assert state["solved_count"] == 0


def test_concept_question_does_not_produce_a_task_even_with_digits(client, fake_openai):
    """The production message contains "7" and topic words — it is still a question."""
    fake_openai.state["reply"] = "Razlomak sa istim brojnikom i nazivnikom jednak je 1."
    body = sse(client, prod_payload(student_message=CONCEPT_Q))
    assert "Proširi" not in body["answer"]
    assert body["last_tutor_task"] == ""


def test_concept_answer_obeys_the_language_policy(client, fake_openai):
    from matbot.minimal import renderer
    fake_openai.state["reply"] = "Тачно, ако je brojnik jednak nazivniku razlomak je 1."
    body = sse(client, prod_payload(student_message=CONCEPT_Q))
    assert not renderer.has_cyrillic(body["answer"]), body["answer"]


def test_concept_answer_falls_back_when_the_model_fails(client, fake_openai):
    fake_openai.state["raise_always"] = RuntimeError("api down")
    body = sse(client, prod_payload(student_message=CONCEPT_Q))
    assert body["last_tutor_task"] == ""           # still no task
    assert body["answer"].strip()                  # still a helpful reply


# =========================================================================== #
# Case 2: active task + a conceptual question                                 #
# =========================================================================== #
def test_concept_question_with_an_active_task_preserves_everything(client, fake_openai):
    first = sse(client, prod_payload())
    question, tid = first["last_tutor_task"], first["next_state"]["task_id"]
    before = minimal_state(first)["active_task"]

    fake_openai.state["reply"] = (
        "Množimo oba dijela istim brojem da vrijednost razlomka ostane ista.")
    body = sse(client, prod_payload(
        student_message="zašto množimo i brojnik i nazivnik?",
        interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))

    assert body["next_state"]["task_id"] == tid          # same task
    assert body["last_tutor_task"] == question
    assert body["answer_verdict"] is None                # not graded
    after = minimal_state(body)["active_task"]
    assert after["attempts"] == before["attempts"]
    assert after["wrong_attempts"] == before["wrong_attempts"]
    assert after["hints_given"] == before["hints_given"]
    assert minimal_state(body)["correct_streak"] == \
        minimal_state(first)["correct_streak"]
    # the reply reminds the student of the task (rendered as LaTeX) without
    # revealing the answer
    from matbot.minimal import mathfmt
    assert mathfmt.format_question(question) in body["answer"]
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    expected = getattr(exp, "expected_display", "") or _fmt_expected(exp)
    assert expected not in body["answer"]


# =========================================================================== #
# Case 3: an ambiguous message must never create a task                       #
# =========================================================================== #
@pytest.mark.parametrize("message", ["asdf", "hello there", "ok pa dobro",
                                     "hmmm", "😀"])
def test_ambiguous_message_asks_for_clarification(client, fake_openai, message):
    fake_openai.state["reply"] = "OTHER"
    body = sse(client, prod_payload(student_message=message))
    assert body["last_tutor_task"] == "", message
    assert body["next_state"]["task_id"] is None, message
    assert "želiš" in body["answer"].lower(), body["answer"]
    assert body["answer_verdict"] is None


def test_ambiguous_message_with_an_active_task_keeps_it(client, fake_openai):
    first = sse(client, prod_payload())
    question, tid = first["last_tutor_task"], first["next_state"]["task_id"]
    fake_openai.state["reply"] = "OTHER"
    body = sse(client, prod_payload(
        student_message="asdf", interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))
    assert body["next_state"]["task_id"] == tid
    assert body["last_tutor_task"] == question
    assert body["answer_verdict"] is None


# =========================================================================== #
# Case 4: repeated identical new-task request                                 #
# =========================================================================== #
def test_repeated_identical_request_returns_the_same_task(client):
    """The 12:53:10 / 12:53:20 pair: identical task, two different ids."""
    first = sse(client, prod_payload(student_message="daj mi zadatak"))
    question, tid = first["last_tutor_task"], first["next_state"]["task_id"]

    second = sse(client, prod_payload(
        student_message="daj mi zadatak",
        previous_next_state=first["next_state"]))
    assert second["next_state"]["task_id"] == tid        # not replaced
    assert second["last_tutor_task"] == question


def test_repeated_request_does_not_reset_progress(client):
    first = sse(client, prod_payload(student_message="daj mi zadatak"))
    question = first["last_tutor_task"]
    hinted = sse(client, prod_payload(
        student_message="ne znam", interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))
    repeated = sse(client, prod_payload(
        student_message="daj mi zadatak",
        previous_next_state=hinted["next_state"]))
    assert repeated["next_state"]["task_id"] == first["next_state"]["task_id"]
    assert repeated["next_state"]["hint_count"] == 1     # progress preserved


def test_a_different_new_task_request_does_replace_the_task(client):
    first = sse(client, prod_payload(student_message="daj mi zadatak"))
    second = sse(client, prod_payload(
        student_message="daj mi novi zadatak",
        previous_next_state=first["next_state"]))
    assert second["next_state"]["task_id"] != first["next_state"]["task_id"]
    assert second["last_tutor_task"] != first["last_tutor_task"]


# =========================================================================== #
# Case 5: a genuine new task after completion differs                         #
# =========================================================================== #
def test_new_task_after_completion_differs_from_the_previous(client):
    first = sse(client, prod_payload(student_message="daj mi zadatak"))
    question = first["last_tutor_task"]
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    expected = getattr(exp, "expected_display", "") or _fmt_expected(exp)

    solved = sse(client, prod_payload(
        student_message=expected, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))
    assert solved["answer_verdict"] == "correct"

    nxt = sse(client, prod_payload(
        student_message="daj mi novi zadatak",
        previous_next_state=solved["next_state"]))
    assert nxt["last_tutor_task"]
    assert nxt["last_tutor_task"] != question            # not the same signature
    assert nxt["next_state"]["task_id"] != first["next_state"]["task_id"]


def test_recent_signature_list_stays_bounded(client):
    body = sse(client, prod_payload(student_message="daj mi zadatak"))
    state = body["next_state"]
    for message in ("daj mi novi zadatak", "hoću još jedan zadatak",
                    "daj mi drugi zadatak", "daj mi zadatak za vježbu"):
        body = sse(client, prod_payload(student_message=message,
                                        previous_next_state=state))
        state = body["next_state"]
    assert len(minimal_state(body)["recent_questions"]) <= 8
    assert isinstance(minimal_state(body)["last_request_signature"], str)


# =========================================================================== #
# Grading ownership is unchanged                                              #
# =========================================================================== #
def test_concept_turns_never_produce_grading_evidence(client, fake_openai, sheets):
    first = sse(client, prod_payload())
    fake_openai.state["reply"] = "Objašnjenje."
    sse(client, prod_payload(
        student_message="zašto množimo i brojnik i nazivnik?",
        interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    from matbot import sheets_log
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    headers = sheets_log.SHEET_HEADERS
    assert row[headers.index("expected_answer")] == ""
    assert row[headers.index("deterministic_check")] == ""
    assert row[headers.index("answer_verdict")] == ""


def test_answers_are_still_graded_deterministically(client, fake_openai):
    first = sse(client, prod_payload())
    question = first["last_tutor_task"]
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    expected = getattr(exp, "expected_display", "") or _fmt_expected(exp)
    body = sse(client, prod_payload(
        student_message=expected, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))
    assert body["answer_verdict"] == "correct"
    assert body["gpt_check_used"] is False


def test_raw_student_message_is_preserved_for_concept_turns(client, fake_openai, sheets):
    fake_openai.state["reply"] = "Objašnjenje."
    sse(client, prod_payload(student_message=CONCEPT_Q))
    from matbot.sheets_log import _raw_student_message
    payload, _ = sheets[-1]
    assert _raw_student_message(payload) == CONCEPT_Q
