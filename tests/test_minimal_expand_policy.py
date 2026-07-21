# -*- coding: utf-8 -*-
"""Four production defects from the 2026-07-21 13:xx session.

1. ``Proširi 1/2 na nazivnik 4.`` + "4/8"  → accepted as correct.
   ``Proširi 2/4 na nazivnik 20.`` + "2/4" → accepted as correct (unexpanded!).
   Cause: the generic checker reports ``correct_value_wrong_form`` for an
   equivalent fraction, and ``_SOLVED_WITH_NOTE`` mapped that to "correct".
2. "Da li želiš novi zadatak?" → "da" → "Nije mi jasno šta želiš."
3. Literal 511 in cells that should be empty.
4. The completion row lost task_id / task_status / counters because the active
   task was cleared before the audit values were read.

Driven through the SSE route the browser uses.
"""
import json

import pytest

from matbot import ai_tutor_service as svc
from matbot import sheets_log
from matbot import topic_resolver as tr
from matbot.minimal import grading as mgrading
from matbot.minimal.state import ActiveTask

STREAM_URL = "/api/ai-tutor/chat/stream"
PROD_TOPIC = "12880"


def prod_payload(**overrides):
    payload = {
        "session_id": "expand-1", "grade": 6, "mode": "practice",
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


def seeded_task(client, question, expected):
    """Start a session and force a specific task via round-tripped state."""
    first = sse(client, prod_payload())
    state = first["next_state"]
    ms = state["minimal_state"]
    ms["active_task"] = {
        "task_id": "mt_seed", "skill_id": "fraction_expand",
        "question": question, "expected_display": expected,
        "npp_id": "6-04-035", "tema_title": "Proširivanje razlomaka",
        "attempts": 0, "wrong_attempts": 0, "hints_given": 0, "solved": False,
    }
    state["task_id"] = "mt_seed"
    return state, question


def answer(client, state, question, text):
    return sse(client, prod_payload(
        student_message=text, interaction_phase="answering_practice_task",
        last_tutor_task=question, previous_next_state=state))


# =========================================================================== #
# 1. fraction_expand requires the target denominator                          #
# =========================================================================== #
@pytest.mark.parametrize("question,expected,student", [
    ("Proširi 1/2 na nazivnik 4.", "2/4", "4/8"),      # production case 1
    ("Proširi 2/4 na nazivnik 20.", "10/20", "2/4"),   # production case 2
    ("Proširi 1/3 na nazivnik 9.", "3/9", "2/6"),
])
def test_equivalent_but_wrong_denominator_is_not_correct(client, question,
                                                         expected, student):
    state, q = seeded_task(client, question, expected)
    body = answer(client, state, q, student)
    assert body["answer_verdict"] != "correct", body["answer"]
    assert body["answer_verdict_detail"] == "incorrect_target_denominator"
    # the same task stays active
    assert body["next_state"]["task_id"] == "mt_seed"
    assert body["last_tutor_task"] == question
    assert body["next_state"]["task_status"] == "active"
    # no progress is credited
    assert body["next_state"]["correct_streak"] == 0
    assert body["next_state"]["minimal_state"]["solved_count"] == 0


@pytest.mark.parametrize("question,expected", [
    ("Proširi 1/2 na nazivnik 4.", "2/4"),
    ("Proširi 2/4 na nazivnik 20.", "10/20"),
])
def test_the_required_form_is_accepted(client, question, expected):
    state, q = seeded_task(client, question, expected)
    body = answer(client, state, q, expected)
    assert body["answer_verdict"] == "correct"
    assert body["next_state"]["correct_streak"] == 1
    assert body["next_state"]["minimal_state"]["solved_count"] == 1
    assert body["last_tutor_task"] == ""


def test_wrong_denominator_feedback_names_the_requirement(client):
    state, q = seeded_task(client, "Proširi 2/4 na nazivnik 20.", "10/20")
    body = answer(client, state, q, "2/4")
    text = body["answer"]
    assert "20" in text
    assert "nazivnik" in text.lower()
    assert "10/20" not in text                # never reveals the answer


def test_a_genuinely_wrong_value_stays_incorrect(client):
    state, q = seeded_task(client, "Proširi 5/8 na nazivnik 56.", "35/56")
    body = answer(client, state, q, "36/56")
    assert body["answer_verdict"] == "incorrect"
    assert body["answer_verdict_detail"] != "incorrect_target_denominator"


def test_policy_is_scoped_to_fraction_expand():
    """Other skills still accept an equivalent value."""
    other = ActiveTask(task_id="t", skill_id="fraction_add_unlike",
                       question="Izračunaj: 1/2 + 1/3.", expected_display="5/6")
    verdict, solved, detail = mgrading._apply_skill_policy(
        other, "10/12", verdict="correct", solved=True,
        detail="correct_value_wrong_form")
    assert (verdict, solved) == ("correct", True)


def test_streak_only_advances_on_truly_correct_answers(client):
    state, q = seeded_task(client, "Proširi 1/2 na nazivnik 4.", "2/4")
    body = answer(client, state, q, "4/8")            # equivalent, wrong form
    assert body["next_state"]["correct_streak"] == 0
    body = answer(client, body["next_state"], q, "2/4")
    assert body["next_state"]["correct_streak"] == 1


# =========================================================================== #
# 2. Yes/no confirmation after the invitation                                 #
# =========================================================================== #
def _correct_turn(client):
    first = sse(client, prod_payload())
    question = first["last_tutor_task"]
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    expected = getattr(exp, "expected_display", "") or _fmt_expected(exp)
    body = answer(client, first["next_state"], question, expected)
    assert body["answer_verdict"] == "correct"
    return body, question


def test_correct_answer_arms_the_confirmation(client):
    body, _ = _correct_turn(client)
    assert "?" in body["answer"]
    assert body["next_state"]["minimal_state"]["pending_confirmation"] == "new_task"


def test_da_after_the_invitation_creates_a_new_task(client):
    body, question = _correct_turn(client)
    nxt = sse(client, prod_payload(student_message="da",
                                   previous_next_state=body["next_state"]))
    assert nxt["last_tutor_task"], nxt["answer"]
    assert nxt["last_tutor_task"] != question
    assert nxt["next_state"]["task_status"] == "active"
    assert "nije mi jasno" not in nxt["answer"].lower()


@pytest.mark.parametrize("reply", ["da", "može", "hoću", "hajde"])
def test_all_affirmations_are_accepted(client, reply):
    body, _ = _correct_turn(client)
    nxt = sse(client, prod_payload(student_message=reply,
                                   previous_next_state=body["next_state"]))
    assert nxt["last_tutor_task"], (reply, nxt["answer"])


@pytest.mark.parametrize("reply", ["ne", "neću"])
def test_declining_creates_no_task(client, reply):
    body, _ = _correct_turn(client)
    nxt = sse(client, prod_payload(student_message=reply,
                                   previous_next_state=body["next_state"]))
    assert nxt["last_tutor_task"] == ""
    assert nxt["next_state"]["minimal_state"]["pending_confirmation"] == ""


def test_confirmation_is_consumed_only_once(client):
    body, _ = _correct_turn(client)
    first_yes = sse(client, prod_payload(student_message="da",
                                         previous_next_state=body["next_state"]))
    assert first_yes["last_tutor_task"]
    assert first_yes["next_state"]["minimal_state"]["pending_confirmation"] == ""
    # a second bare "da" has nothing pending → ambiguous, no new task
    second = sse(client, prod_payload(
        student_message="da", previous_next_state=first_yes["next_state"]))
    assert second["next_state"]["task_id"] == first_yes["next_state"]["task_id"]


def test_da_without_a_pending_confirmation_is_not_a_new_task(client):
    body = sse(client, prod_payload(student_message="da"))
    assert body["last_tutor_task"] == ""
    assert body["next_state"]["task_status"] is None


def test_unrelated_message_bypasses_the_confirmation(client):
    body, _ = _correct_turn(client)
    nxt = sse(client, prod_payload(student_message="daj mi teži zadatak",
                                   previous_next_state=body["next_state"]))
    assert nxt["last_tutor_task"]
    assert nxt["next_state"]["difficulty_level"] == 2      # honoured as HARDER
    assert nxt["next_state"]["minimal_state"]["pending_confirmation"] == ""


def test_confirmation_needs_no_openai_call(client, fake_openai):
    body, _ = _correct_turn(client)
    before = len(fake_openai.calls.messages)
    nxt = sse(client, prod_payload(student_message="da",
                                   previous_next_state=body["next_state"]))
    assert nxt["last_tutor_task"]
    assert len(fake_openai.calls.messages) == before      # no classifier call


def test_confirmation_preserves_difficulty_and_skill(client):
    first = sse(client, prod_payload())
    harder = sse(client, prod_payload(student_message="daj mi teži zadatak",
                                      previous_next_state=first["next_state"]))
    assert harder["next_state"]["difficulty_level"] == 2
    from matbot.answer_checker import derive_expected, _fmt_expected
    q = harder["last_tutor_task"]
    exp = derive_expected(q)
    solved = answer(client, harder["next_state"], q,
                    getattr(exp, "expected_display", "") or _fmt_expected(exp))
    nxt = sse(client, prod_payload(student_message="da",
                                   previous_next_state=solved["next_state"]))
    assert nxt["next_state"]["difficulty_level"] == 2
    assert nxt["next_state"]["task"]["skill_id"] == "fraction_expand"


# =========================================================================== #
# 3. No sentinel value anywhere in a written row                              #
# =========================================================================== #
def _written_row(sheets_rows):
    payload, response = sheets_rows[-1]
    return sheets_log._sheets_safe_row(
        sheets_log._build_transcript_row(payload, response))


def test_non_grading_row_has_no_sentinel(client, sheets):
    sse(client, prod_payload())
    row = _written_row(sheets)
    assert len(row) == len(sheets_log.SHEET_HEADERS)
    for name, value in zip(sheets_log.SHEET_HEADERS, row):
        assert str(value) != "511", name
        assert value is not None, name
        assert isinstance(value, (str, int, float, bool)), (name, type(value))


def test_grading_row_has_no_sentinel(client, sheets):
    first = sse(client, prod_payload())
    answer(client, first["next_state"], first["last_tutor_task"], "4/12")
    row = _written_row(sheets)
    for name, value in zip(sheets_log.SHEET_HEADERS, row):
        assert str(value) != "511", name
        assert isinstance(value, (str, int, float, bool)), (name, type(value))


def test_empty_optional_fields_are_blank_strings(client, sheets):
    sse(client, prod_payload())
    row = _written_row(sheets)
    headers = sheets_log.SHEET_HEADERS
    for name in ("expected_answer", "normalized_expected", "deterministic_check",
                 "internal_instruction", "answer_verdict", "hint_history"):
        assert row[headers.index(name)] == "", name


def test_sanitizer_preserves_types_and_literal_fractions():
    safe = sheets_log._sheets_safe
    assert safe("4/12") == "4/12"          # literal fraction, unchanged
    assert safe("x=4") == "x=4"
    assert safe(None) == ""                # never a sentinel
    assert safe(3) == 3 and isinstance(safe(3), int)
    assert safe(False) is False            # boolean stays boolean
    assert safe(1.5) == 1.5
    assert json.loads(safe({"a": 1})) == {"a": 1}      # dict → JSON text


def test_sanitizer_enforces_exact_row_width():
    short = sheets_log._sheets_safe_row(["a", "b"])
    assert len(short) == len(sheets_log.SHEET_HEADERS)
    assert short[2:] == [""] * (len(sheets_log.SHEET_HEADERS) - 2)
    long = sheets_log._sheets_safe_row(["x"] * (len(sheets_log.SHEET_HEADERS) + 5))
    assert len(long) == len(sheets_log.SHEET_HEADERS)


def test_append_uses_raw_and_the_sanitized_row(monkeypatch):
    captured = {}

    class _WS:
        col_count = 100

        def append_row(self, values, value_input_option=None):
            captured["values"] = values
            captured["option"] = value_input_option

    monkeypatch.setattr(sheets_log, "_init_sheets", lambda: _WS())
    monkeypatch.setattr(sheets_log, "_ensure_sheet_layout", lambda ws: None)
    sheets_log._append_row_once(["4/12", None, {"a": 1}])
    assert captured["option"] == "RAW"
    assert captured["values"][0] == "4/12"
    assert captured["values"][1] == ""
    assert len(captured["values"]) == len(sheets_log.SHEET_HEADERS)


def test_sheet_is_widened_when_headers_grow():
    calls = {}

    class _WS:
        col_count = 40

        def resize(self, cols=None):
            calls["cols"] = cols

    sheets_log._ensure_width(_WS(), len(sheets_log.SHEET_HEADERS))
    assert calls["cols"] == len(sheets_log.SHEET_HEADERS)


# =========================================================================== #
# 4. Completed-task audit data                                                #
# =========================================================================== #
def _audit(sheets_rows):
    payload, response = sheets_rows[-1]
    row = sheets_log._build_transcript_row(payload, response)
    headers = sheets_log.SHEET_HEADERS
    return {n: row[headers.index(n)] for n in (
        "task_id", "task_status", "attempt_number", "total_attempt_count",
        "wrong_attempt_count", "hint_count", "answer_verdict")}


def test_correct_on_first_attempt_is_auditable(client, sheets):
    first = sse(client, prod_payload())
    tid, question = first["next_state"]["task_id"], first["last_tutor_task"]
    from matbot.answer_checker import derive_expected, _fmt_expected
    exp = derive_expected(question)
    answer(client, first["next_state"], question,
           getattr(exp, "expected_display", "") or _fmt_expected(exp))
    cells = _audit(sheets)
    assert cells["task_id"] == tid
    assert cells["task_status"] == "completed"
    assert cells["total_attempt_count"] == 1
    assert cells["wrong_attempt_count"] == 0
    assert cells["answer_verdict"] == "correct"


def test_wrong_then_correct_preserves_both_counters(client, sheets):
    """The production 5/8 → 56 case: 36/56 wrong, then 35/56 correct."""
    state, question = seeded_task(client, "Proširi 5/8 na nazivnik 56.", "35/56")
    wrong = answer(client, state, question, "36/56")
    assert wrong["answer_verdict"] == "incorrect"
    answer(client, wrong["next_state"], question, "35/56")
    cells = _audit(sheets)
    assert cells["task_id"] == "mt_seed"
    assert cells["task_status"] == "completed"
    assert cells["attempt_number"] == 2
    assert cells["total_attempt_count"] == 2
    assert cells["wrong_attempt_count"] == 1


def test_hint_then_correct_preserves_the_hint_count(client, sheets):
    state, question = seeded_task(client, "Proširi 5/8 na nazivnik 56.", "35/56")
    hinted = answer(client, state, question, "ne znam")
    assert hinted["next_state"]["hint_count"] == 1
    answer(client, hinted["next_state"], question, "35/56")
    cells = _audit(sheets)
    assert cells["task_status"] == "completed"
    assert cells["hint_count"] == 1
    assert cells["total_attempt_count"] == 1


def test_audit_survives_active_task_becoming_null(client, sheets):
    state, question = seeded_task(client, "Proširi 5/8 na nazivnik 56.", "35/56")
    body = answer(client, state, question, "35/56")
    assert body["next_state"]["minimal_state"]["active_task"] is None
    assert body["next_state"]["active_task_kind"] is None
    # …yet the audit values are all still present
    assert body["next_state"]["task_id"] == "mt_seed"
    assert body["next_state"]["task_status"] == "completed"
    assert body["next_state"]["total_attempt_count"] == 1
