# -*- coding: utf-8 -*-
"""Follow-up to the 2026-07-21 session: wording honesty + Sheets audit fields.

1. Correct-answer feedback claimed the next task was starting ("Odlično! Idemo
   na sljedeći!") while ``active_task`` became null and no task was sent.
2. ``expected_answer`` / ``normalized_*`` / ``deterministic_check`` were blank
   in Sheets even though the evidence existed inside the GradingResult.
3. Fresh-session check that ``runtime_topic`` stays 12880 once the browser
   starts echoing the canonical id back.

All driven through the SSE route the browser uses.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr
from matbot.minimal import renderer

STREAM_URL = "/api/ai-tutor/chat/stream"
PROD_TOPIC = "12880"
CANONICAL_NPP = "6-04-035"
PROD_MESSAGE = "Daj mi jedan zadatak za vježbu iz ove teme."


def prod_payload(**overrides):
    payload = {
        "session_id": "audit-1", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": PROD_TOPIC, "selected_oblast": "Razlomci",
        "student_message": PROD_MESSAGE, "conversation_history": [],
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


def solve(question):
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    return getattr(exp, "expected_display", "") or _fmt_expected(exp)


def audit_cells(sheets_rows):
    """The Sheets audit columns of the most recently logged turn."""
    payload, response = sheets_rows[-1]
    row = sheets_log._build_transcript_row(payload, response)
    headers = sheets_log.SHEET_HEADERS
    return {name: row[headers.index(name)] for name in (
        "expected_answer", "normalized_expected", "student_answer",
        "normalized_student", "deterministic_check", "answer_type",
        "answer_verdict", "answer_verdict_detail", "gpt_check_used")}


# =========================================================================== #
# 1. Correct-answer wording must not announce a task that was not created     #
# =========================================================================== #
def _answer_correctly(client, first):
    return sse(client, prod_payload(
        student_message=solve(first["last_tutor_task"]),
        interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))


def test_correct_feedback_does_not_claim_a_next_task_started(client):
    first = sse(client, prod_payload())
    body = _answer_correctly(client, first)
    assert body["answer_verdict"] == "correct"
    # nothing new was created — the id that remains is the COMPLETED task's,
    # kept for audit (task_status=completed, nothing active)
    assert body["next_state"]["task_status"] == "completed"
    assert body["next_state"]["active_task_kind"] is None
    assert body["last_tutor_task"] == ""
    # …so the wording must not say otherwise
    folded = renderer._fold(body["answer"])
    assert not renderer._IMPLIES_NEXT_TASK_RE.search(folded), body["answer"]
    assert "?" in body["answer"]           # it OFFERS rather than announces


def test_correct_feedback_is_short_and_unpraising(client):
    first = sse(client, prod_payload())
    body = _answer_correctly(client, first)
    answer = body["answer"]
    assert answer.startswith("Tačno")
    assert not renderer._PRAISE_RE.search(renderer._fold(answer)), answer
    assert len(answer) <= 80, answer


def test_next_invite_pool_never_announces_a_task():
    for phrase in renderer._NEXT_INVITE:
        assert not renderer._IMPLIES_NEXT_TASK_RE.search(renderer._fold(phrase)), phrase
        assert phrase.endswith("?"), phrase


@pytest.mark.parametrize("drift", [
    "Odlično! Idemo na sljedeći!",
    "Bravo, tačno je!",
    "Tačno. Prelazimo na sljedeći zadatak.",
    "Super! Evo novog zadatka.",
    "Tačno, nastavljamo sa sljedećim.",
])
def test_model_wording_that_praises_or_announces_is_rejected(drift):
    class _Resp:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    original = "Tačno. Želiš li novi zadatak?"
    out = renderer.phrase_with_model(
        original, openai_chat=lambda *a, **kw: _Resp(drift),
        model="m", timeout=1, allow_verdict_words=True)
    assert out == original, drift


def test_a_real_new_task_turn_may_announce_it(client):
    """The guard applies to FEEDBACK; presenting a task is not affected."""
    body = sse(client, prod_payload())
    assert body["last_tutor_task"]
    assert body["next_state"]["task_id"]


# =========================================================================== #
# 2. Sheets audit fields                                                      #
# =========================================================================== #
def test_audit_fields_for_an_incorrect_answer(client, sheets):
    """Production example: Proširi 2/6 na nazivnik 48 → student answers 4/48."""
    first = sse(client, prod_payload())
    question = first["last_tutor_task"]
    expected = solve(question)
    sse(client, prod_payload(
        student_message="4/48", interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))
    cells = audit_cells(sheets)

    assert cells["expected_answer"] == expected
    assert cells["student_answer"] == "4/48"
    assert cells["answer_verdict_detail"]
    assert cells["gpt_check_used"] is False
    assert cells["answer_type"]
    assert cells["normalized_expected"]
    assert cells["normalized_student"]
    evidence = json.loads(cells["deterministic_check"])
    assert evidence["method"] == "deterministic"
    assert evidence["gpt_check_used"] is False
    assert evidence["expected_display"] == expected
    assert evidence["student_raw"] == "4/48"


def test_audit_fields_for_a_correct_answer(client, sheets):
    first = sse(client, prod_payload())
    question = first["last_tutor_task"]
    expected = solve(question)
    sse(client, prod_payload(
        student_message=expected, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))
    cells = audit_cells(sheets)

    assert cells["expected_answer"] == expected
    assert cells["student_answer"] == expected
    assert cells["answer_verdict"] == "correct"
    assert cells["gpt_check_used"] is False
    evidence = json.loads(cells["deterministic_check"])
    assert evidence["match"] is True
    assert evidence["expected_normalized"] == evidence["student_normalized"]


def test_audit_fields_for_an_answer_after_a_hint(client, sheets):
    first = sse(client, prod_payload())
    question = first["last_tutor_task"]
    hinted = sse(client, prod_payload(
        student_message="ne znam", interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=first["next_state"]))

    # a hint is not a grading turn → no grading evidence is invented for it
    hint_cells = audit_cells(sheets)
    assert hint_cells["expected_answer"] == ""
    assert hint_cells["deterministic_check"] == ""

    expected = solve(question)
    sse(client, prod_payload(
        student_message=expected, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=hinted["next_state"]))
    cells = audit_cells(sheets)
    assert cells["expected_answer"] == expected
    assert cells["student_answer"] == expected
    assert cells["answer_verdict"] == "correct"
    assert json.loads(cells["deterministic_check"])["match"] is True


def test_fraction_audit_values_stay_literal_text(client, sheets):
    """RAW storage plus the new fields: no date coercion anywhere."""
    first = sse(client, prod_payload())
    sse(client, prod_payload(
        student_message="4/12", interaction_phase="answering_practice_task",
        last_tutor_task=first["last_tutor_task"],
        previous_next_state=first["next_state"]))
    cells = audit_cells(sheets)
    assert cells["student_answer"] == "4/12"
    assert isinstance(cells["expected_answer"], str)
    assert "/" in cells["expected_answer"]


def test_audit_columns_did_not_move():
    headers = sheets_log.SHEET_HEADERS
    assert headers.index("student_message") == 16
    assert headers.index("answer_type") == 22
    assert headers.index("expected_answer") == 23
    assert headers.index("normalized_expected") == 24
    assert headers.index("student_answer") == 25
    assert headers.index("normalized_student") == 26
    assert headers.index("deterministic_check") == 27
    assert headers.index("engine_canary") == 59
    assert headers[-2:] == ["internal_instruction", "minimal_routing"]


def test_non_grading_turns_carry_no_answer_check(client):
    body = sse(client, prod_payload())
    assert body.get("answer_check") is None
    assert body.get("gpt_check_used") is None


# =========================================================================== #
# 3. Fresh-session runtime topic                                              #
# =========================================================================== #
def test_fresh_session_keeps_runtime_topic_when_client_echoes_canonical(client):
    """A FRESH session starting at 12880 keeps it once the browser echoes back.

    The earlier manual check reused a session whose frontend topic was already
    canonicalized, so it never exercised ``origin_runtime_id``.
    """
    fresh = prod_payload(session_id="fresh-origin")
    assert "previous_next_state" not in fresh
    first = sse(client, fresh)
    assert first["minimal_routing"]["runtime_topic"] == PROD_TOPIC
    assert first["next_state"]["minimal_state"]["origin_runtime_id"] == PROD_TOPIC

    state = first["next_state"]
    # index.html's adoptResponseTopic() replaces state.topic with
    # effective_topic, so every later turn sends the CANONICAL id
    for message in ("daj mi novi zadatak", "ne znam", "daj mi novi zadatak"):
        body = sse(client, prod_payload(
            session_id="fresh-origin", selected_topic=CANONICAL_NPP,
            student_message=message, previous_next_state=state))
        state = body["next_state"]
        routing = body["minimal_routing"]
        assert routing["runtime_topic"] == PROD_TOPIC, message
        assert routing["canonical_topic"] == CANONICAL_NPP, message
        assert routing["resolved_skill"] == "fraction_expand", message
        assert state["minimal_state"]["origin_runtime_id"] == PROD_TOPIC, message


def test_session_starting_canonical_reports_canonical(client):
    """No fabrication: a session that never saw 12880 must not claim it."""
    body = sse(client, prod_payload(session_id="canon-start",
                                    selected_topic=CANONICAL_NPP))
    assert body["minimal_routing"]["runtime_topic"] == CANONICAL_NPP
    assert body["next_state"]["minimal_state"]["origin_runtime_id"] == CANONICAL_NPP


def test_sheets_receives_the_preserved_runtime_topic(client, sheets):
    first = sse(client, prod_payload(session_id="fresh-sheets"))
    sse(client, prod_payload(
        session_id="fresh-sheets", selected_topic=CANONICAL_NPP,
        student_message="daj mi novi zadatak",
        previous_next_state=first["next_state"]))
    payload, response = sheets[-1]
    row = sheets_log._build_transcript_row(payload, response)
    routing = json.loads(row[sheets_log.SHEET_HEADERS.index("minimal_routing")])
    assert routing["runtime_topic"] == PROD_TOPIC
    assert routing["canonical_topic"] == CANONICAL_NPP
