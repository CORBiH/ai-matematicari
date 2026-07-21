# -*- coding: utf-8 -*-
"""``student_answer`` must hold ONLY what was mathematically graded.

Production: "ja mislim da je 9/10" was graded correctly, but the audit recorded
``student_answer`` as the whole sentence. The generic rational checker parses a
single token straight out of prose, so the extraction retry never ran and the
graded value defaulted to the raw message.

The fix is reusable across every rational skill — the token comes from the
checker's own ``given.raw`` — so this file exercises several skills, not just
fraction_add_unlike.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr
from matbot.minimal.grading import grade
from matbot.minimal.state import ActiveTask

STREAM_URL = "/api/ai-tutor/chat/stream"


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


def task_for(skill_id, question, expected):
    return ActiveTask(task_id="mt_x", skill_id=skill_id, question=question,
                      expected_display=expected)


# =========================================================================== #
# Reusable across rational skills                                             #
# =========================================================================== #
RATIONAL_CASES = [
    # (skill_id, question, expected_display, message, graded token)
    ("fraction_add_unlike", "Izračunaj: 1/2 + 2/5.", "9/10",
     "ja mislim da je 9/10", "9/10"),
    ("fraction_add_unlike", "Izračunaj: 1/3 + 4/5.", "17/15",
     "mislim 1 2/15", "1 2/15"),                       # mixed number
    ("fraction_expand", "Proširi 1/4 na nazivnik 20.", "5/20",
     "pa valjda 5/20", "5/20"),
    ("linear_equation", "Riješi jednačinu: 2x + 3 = 11.", "x=4",
     "mislim da je x = 4", "4"),
    ("divisibility", "Koliko je 20% od 50?", "10",
     "mislim da je 10", "10"),
]


@pytest.mark.parametrize("skill_id,question,expected,message,token",
                         RATIONAL_CASES)
def test_graded_answer_is_only_the_token(skill_id, question, expected,
                                         message, token):
    result = grade(task_for(skill_id, question, expected), message)
    assert result.graded_answer == token, (skill_id, message)
    # the verbatim message is preserved separately
    assert result.student_raw == message
    assert result.evidence["student_raw"] == message
    assert result.evidence["graded_text"] == token


@pytest.mark.parametrize("skill_id,question,expected,message,token",
                         RATIONAL_CASES)
def test_verdicts_are_unchanged_by_the_audit_fix(skill_id, question, expected,
                                                 message, token):
    """The token must be reported, not used to change the decision."""
    from_prose = grade(task_for(skill_id, question, expected), message)
    from_token = grade(task_for(skill_id, question, expected), token)
    assert from_prose.verdict == from_token.verdict, (skill_id, message)
    assert from_prose.normalized_student == from_token.normalized_student


@pytest.mark.parametrize("message", ["9/10", "  9/10  "])
def test_bare_answers_are_unchanged(message):
    result = grade(task_for("fraction_add_unlike", "Izračunaj: 1/2 + 2/5.",
                            "9/10"), message)
    assert result.graded_answer == "9/10"
    assert result.student_raw == message           # including its whitespace
    assert result.verdict == "correct"


def test_normalized_student_is_the_mathematical_value():
    result = grade(task_for("fraction_expand", "Proširi 1/4 na nazivnik 20.",
                            "5/20"), "pa valjda 5/20")
    assert result.graded_answer == "5/20"          # what was written
    assert result.normalized_student == "1/4"      # what it means


def test_extraction_retry_still_wins_when_it_runs():
    """A declared answer inside multi-fraction prose keeps the candidate."""
    result = grade(task_for("fraction_add_unlike", "Izračunaj: 1/3 + 2/5.",
                            "11/15"),
                   "Mislim da je odgovor 11/15 jer je 1/3 = 5/15, a 2/5 = 6/15.")
    assert result.graded_answer == "11/15"
    assert result.evidence["extracted_candidate"] == "11/15"
    assert result.verdict == "correct"


def test_ambiguous_still_records_nothing():
    result = grade(task_for("fraction_add_unlike", "Izračunaj: 1/3 + 2/5.",
                            "11/15"), "1/3 = 5/15 i 2/5 = 6/15")
    assert result.graded_answer == ""
    assert result.normalized_student == ""
    assert result.evidence["graded_text"] == ""


# =========================================================================== #
# End to end over the SSE route                                               #
# =========================================================================== #
def _seed(client, topic, skill_id, question, expected):
    first = sse(client, {
        "session_id": "ga", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": topic, "selected_oblast": "Razlomci",
        "student_message": "daj mi zadatak", "conversation_history": []})
    state = first["next_state"]
    state["minimal_state"]["active_task"] = {
        "task_id": "mt_x", "skill_id": skill_id, "question": question,
        "expected_display": expected, "npp_id": topic,
        "tema_title": "t", "attempts": 0, "wrong_attempts": 0,
        "hints_given": 0, "solved": False, "solution_revealed": False}
    return state


def _answer(client, topic, state, question, message):
    return sse(client, {
        "session_id": "ga", "grade": 6, "mode": "practice",
        "session_mode": "practice", "entry_source": "manual_topic_choice",
        "selected_topic": topic, "selected_oblast": "Razlomci",
        "student_message": message, "conversation_history": [],
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": question, "previous_next_state": state})


def _cells(sheets_rows):
    payload, response = sheets_rows[-1]
    row = sheets_log._build_transcript_row(payload, response)
    headers = sheets_log.SHEET_HEADERS
    return {n: row[headers.index(n)] for n in (
        "student_message", "student_answer", "normalized_student",
        "deterministic_check", "answer_verdict")}


def test_production_case_over_the_wire(client, sheets):
    """The exact production message."""
    question, message = "Izračunaj: 1/2 + 2/5.", "ja mislim da je 9/10"
    state = _seed(client, "6-04-040", "fraction_add_unlike", question, "9/10")
    body = _answer(client, "6-04-040", state, question, message)

    cells = _cells(sheets)
    assert cells["student_message"] == message          # verbatim
    assert cells["student_answer"] == "9/10"            # ONLY the graded token
    assert cells["normalized_student"] == "9/10"
    assert cells["answer_verdict"] == "correct"
    evidence = json.loads(cells["deterministic_check"])
    assert evidence["student_raw"] == message
    assert evidence["graded_text"] == "9/10"
    assert body["answer_verdict"] == "correct"


def test_production_case_on_a_second_skill_over_the_wire(client, sheets):
    question, message = "Proširi 1/4 na nazivnik 20.", "pa valjda 5/20"
    state = _seed(client, "6-04-035", "fraction_expand", question, "5/20")
    _answer(client, "6-04-035", state, question, message)

    cells = _cells(sheets)
    assert cells["student_message"] == message
    assert cells["student_answer"] == "5/20"
    assert cells["answer_verdict"] == "correct"


def test_mixed_number_in_prose_over_the_wire(client, sheets):
    question, message = "Izračunaj: 1/3 + 4/5.", "mislim 1 2/15"
    state = _seed(client, "6-04-040", "fraction_add_unlike", question, "17/15")
    _answer(client, "6-04-040", state, question, message)

    cells = _cells(sheets)
    assert cells["student_message"] == message
    assert cells["student_answer"] == "1 2/15"
    assert cells["normalized_student"] == "17/15"       # normalized value
    assert cells["answer_verdict"] == "correct"


def test_attempts_and_columns_are_untouched(client, sheets):
    question = "Izračunaj: 1/2 + 2/5."
    state = _seed(client, "6-04-040", "fraction_add_unlike", question, "9/10")
    body = _answer(client, "6-04-040", state, question, "ja mislim da je 9/10")
    assert body["next_state"]["total_attempt_count"] == 1
    assert body["next_state"]["correct_streak"] == 1
    headers = sheets_log.SHEET_HEADERS
    assert len(headers) == 62
    assert headers.index("student_message") == 16
    assert headers.index("student_answer") == 25
    assert headers.index("normalized_student") == 26
