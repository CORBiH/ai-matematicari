"""Tests for optional Google Sheets transcript logging.

All Google client objects are mocked; the test suite must never call the network.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime

import pytest

from matbot import sheets_log as sl


class FakeCredentials:
    service_account_email = "bot@example.test"

    @classmethod
    def from_service_account_info(cls, info, scopes=None):
        cls.info = info
        cls.scopes = scopes
        return cls()

    @classmethod
    def from_service_account_file(cls, filename, scopes=None):
        cls.filename = filename
        cls.scopes = scopes
        return cls()


class FakeWorksheet:
    title = "Sheet1"

    def __init__(self, *, fail=False):
        self.fail = fail
        self.appended = []
        self.updated = []
        self.formats = []
        self.frozen = []
        self.filters = []
        self.rows = {1: []}

    def append_row(self, values, value_input_option=None):
        if self.fail:
            raise RuntimeError("append failed")
        self.appended.append((values, value_input_option))

    def row_values(self, row):
        return self.rows.get(row, [])

    def update(self, rng, values):
        self.updated.append((rng, values))
        if rng.startswith("A1:"):
            self.rows[1] = list(values[0])

    def freeze(self, **kwargs):
        self.frozen.append(kwargs)

    def set_basic_filter(self, rng):
        self.filters.append(rng)

    def format(self, rng, fmt):
        self.formats.append((rng, fmt))


class FakeSpreadsheet:
    title = "Fake spreadsheet"
    id = "sheet-123"

    def __init__(self, worksheet):
        self.sheet1 = worksheet

    def get_worksheet(self, index):
        return self.sheet1


class FakeClient:
    def __init__(self, worksheet):
        self.worksheet = worksheet
        self.opened_by_key = []
        self.opened_by_name = []

    def open_by_key(self, key):
        self.opened_by_key.append(key)
        return FakeSpreadsheet(self.worksheet)

    def open(self, name):
        self.opened_by_name.append(name)
        return FakeSpreadsheet(self.worksheet)


class FakeGspread:
    def __init__(self, client):
        self.client = client
        self.authorized_with = []

    def authorize(self, creds):
        self.authorized_with.append(creds)
        return self.client


class FakeApiError(Exception):
    def __init__(self, status_code, message="api error"):
        super().__init__(message)
        self.status_code = status_code


class SequenceWorksheet(FakeWorksheet):
    def __init__(self, outcomes):
        super().__init__()
        self.outcomes = list(outcomes)
        self.calls = 0

    def append_row(self, values, value_input_option=None):
        self.calls += 1
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        self.appended.append((values, value_input_option))


@pytest.fixture(autouse=True)
def isolate_sheets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    sl._reset_async_state_for_tests()
    for name in (
        "GOOGLE_SHEETS_CREDENTIALS_B64",
        "GSHEET_ID",
        "GSHEET_NAME",
        "SHEETS_ASYNC_LOG",
        "SHEETS_ASYNC_MAX_RETRIES",
        "SHEETS_ASYNC_RETRY_BASE_S",
    ):
        monkeypatch.delenv(name, raising=False)
    sl.sheet = None
    sl._sheets_initialized = False
    sl._sheet_layout_prepared = False
    yield
    sl._reset_async_state_for_tests()
    sl.sheet = None
    sl._sheets_initialized = False
    sl._sheet_layout_prepared = False


def _b64_creds() -> str:
    raw = json.dumps({"client_email": "bot@example.test"}).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _payload_response(status="ready"):
    payload = {
        "session_id": "sess-1",
        "grade": 7,
        "student_message": "Koliko je 2+2?",
    }
    response = {
        "mode": "practice",
        "final_topic": "6-01-001",
        "effective_topic": "fallback-topic",
        "entry_source_used": "manual_topic_choice",
        "answer": "4",
        "status": status,
    }
    return payload, response


def _row_map(row):
    return dict(zip(sl.SHEET_HEADERS, row))


def _install_fake_sheets(monkeypatch, worksheet, *, async_enabled=True):
    client = FakeClient(worksheet)
    fake_gspread = FakeGspread(client)
    monkeypatch.setattr(sl, "gspread", fake_gspread)
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_B64", _b64_creds())
    monkeypatch.setenv("GSHEET_ID", "sheet-123")
    monkeypatch.setenv("SHEETS_ASYNC_LOG", "1" if async_enabled else "0")
    monkeypatch.setenv("SHEETS_ASYNC_RETRY_BASE_S", "0")
    return client


def test_log_transcript_appends_expected_row(monkeypatch):
    worksheet = FakeWorksheet()
    client = FakeClient(worksheet)
    fake_gspread = FakeGspread(client)
    monkeypatch.setattr(sl, "gspread", fake_gspread)
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_B64", _b64_creds())
    monkeypatch.setenv("GSHEET_ID", "sheet-123")
    monkeypatch.setenv("SHEETS_ASYNC_LOG", "0")

    payload, response = _payload_response()
    assert sl.log_transcript_to_sheet(payload, response) is True

    assert client.opened_by_key == ["sheet-123"]
    assert len(worksheet.appended) == 1
    row, option = worksheet.appended[0]
    datetime.fromisoformat(row[0])
    assert worksheet.updated == [(f"A1:{sl._sheet_col(len(sl.SHEET_HEADERS))}1", [sl.SHEET_HEADERS])]
    assert worksheet.frozen == [{"rows": 1}]
    assert worksheet.filters == [f"A1:{sl._sheet_col(len(sl.SHEET_HEADERS))}1"]
    assert len(row) == len(sl.SHEET_HEADERS)
    mapped = _row_map(row)
    assert mapped["event_type"] == "chat"
    assert mapped["session_id"] == "sess-1"
    assert mapped["grade"] == 7
    assert mapped["mode"] == "practice"
    assert mapped["topic"] == "6-01-001"
    assert mapped["entry_source"] == "manual_topic_choice"
    assert mapped["status"] == "ready"
    assert mapped["student_message"] == "Koliko je 2+2?"
    assert mapped["answer"] == "4"
    assert mapped["task_origin"] == ""
    assert mapped["hint_level"] == ""
    # RAW since 2026-07-21: USER_ENTERED made Sheets parse "4/12" as a date
    # serial (46360), destroying correctly-graded fraction answers.
    assert option == "RAW"


def test_transcript_row_includes_adaptive_hint_telemetry():
    payload, response = _payload_response()
    response.update({
        "task_id": "task-child",
        "task_status": "active",
        "hint_level": 3,
        "highest_hint_level": 3,
        "hint_reason": "repeated_stuck",
        "hint_history": [{"level": 3, "reason": "repeated_stuck", "signature": "sig"}],
        "repeated_hint_prevented": True,
        "solution_revealed": False,
        "parent_task_id": "task-parent",
        "followup_task_id": "task-child",
        "task_origin": "independent_followup",
        "completed_parent_task": {
            "task_id": "task-parent",
            "task_status": "completed",
            "attempt_number": 2,
            "wrong_attempt_count": 1,
            "hint_count": 5,
            "solution_revealed": True,
            "solved_with_hints": True,
            "followup_task_id": "task-child",
        },
        "requires_independent_solution": True,
        "multiple_choice_hint": {
            "question": "Koji korak?",
            "correct_id": "A",
            "options": [
                {"id": "A", "text": "Prvi korak", "correct": True},
                {"id": "B", "text": "Drugi korak", "correct": False},
                {"id": "C", "text": "Treci korak", "correct": False},
            ],
        },
        "multiple_choice_result": {"choice_id": "A", "choice_text": "Prvi korak", "correct": True},
    })

    row = sl._build_transcript_row(payload, response)
    mapped = _row_map(row)

    assert len(row) == len(sl.SHEET_HEADERS)
    assert mapped["parent_task_id"] == "task-parent"
    assert mapped["followup_task_id"] == "task-child"
    assert mapped["task_origin"] == "independent_followup"
    assert '"task_id": "task-parent"' in mapped["completed_parent_task"]
    assert '"hint_count": 5' in mapped["completed_parent_task"]
    assert mapped["hint_level"] == 3
    assert mapped["highest_hint_level"] == 3
    assert mapped["hint_reason"] == "repeated_stuck"
    assert mapped["repeated_hint_prevented"] is True
    assert mapped["requires_independent_solution"] is True
    assert '"level": 3' in mapped["hint_history"]
    assert '"choice_id": "A"' in mapped["multiple_choice_result"]


def test_log_transcript_without_env_is_noop(monkeypatch):
    class NoGspread:
        def authorize(self, creds):
            raise AssertionError("gspread must not be used")

    monkeypatch.setattr(sl, "gspread", NoGspread())
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is False


def test_log_transcript_uses_credentials_file_in_local_mode(monkeypatch):
    worksheet = FakeWorksheet()
    client = FakeClient(worksheet)
    fake_gspread = FakeGspread(client)
    monkeypatch.setattr(sl, "gspread", fake_gspread)
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    monkeypatch.setenv("LOCAL_MODE", "1")
    monkeypatch.setenv("GSHEET_NAME", "matematika-bot")
    monkeypatch.setenv("SHEETS_ASYNC_LOG", "0")
    sl._credentials_file().write_text("{}", encoding="utf-8")

    payload, response = _payload_response()
    assert sl.log_transcript_to_sheet(payload, response) is True

    assert FakeCredentials.filename == "credentials.json"
    assert client.opened_by_name == ["matematika-bot"]
    assert len(worksheet.appended) == 1


def test_log_transcript_append_failure_returns_false(monkeypatch):
    worksheet = FakeWorksheet(fail=True)
    fake_gspread = FakeGspread(FakeClient(worksheet))
    monkeypatch.setattr(sl, "gspread", fake_gspread)
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_B64", _b64_creds())
    monkeypatch.setenv("GSHEET_ID", "sheet-123")
    monkeypatch.setenv("SHEETS_ASYNC_LOG", "0")

    payload, response = _payload_response()
    assert sl.log_transcript_to_sheet(payload, response) is False


def test_log_transcript_skips_non_ready_status(monkeypatch):
    worksheet = FakeWorksheet()
    fake_gspread = FakeGspread(FakeClient(worksheet))
    monkeypatch.setattr(sl, "gspread", fake_gspread)
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_B64", _b64_creds())
    monkeypatch.setenv("GSHEET_ID", "sheet-123")
    monkeypatch.setenv("SHEETS_ASYNC_LOG", "0")

    payload, response = _payload_response(status="fallback")
    assert sl.log_transcript_to_sheet(payload, response) is False
    assert worksheet.appended == []


def test_log_feedback_appends_verdict_row(monkeypatch):
    worksheet = FakeWorksheet()
    client = FakeClient(worksheet)
    fake_gspread = FakeGspread(client)
    monkeypatch.setattr(sl, "gspread", fake_gspread)
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_B64", _b64_creds())
    monkeypatch.setenv("GSHEET_ID", "sheet-123")
    monkeypatch.setenv("SHEETS_ASYNC_LOG", "0")

    assert sl.log_feedback_to_sheet({
        "session_id": "sess-fb",
        "message_index": 4,
        "verdict": "down",
        "mode": "practice",
        "topic": "6-04-040",
    }) is True

    assert client.opened_by_key == ["sheet-123"]
    assert len(worksheet.appended) == 1
    row, option = worksheet.appended[0]
    datetime.fromisoformat(row[0])
    assert worksheet.updated == [(f"A1:{sl._sheet_col(len(sl.SHEET_HEADERS))}1", [sl.SHEET_HEADERS])]
    assert len(row) == len(sl.SHEET_HEADERS)
    mapped = _row_map(row)
    assert mapped["event_type"] == "feedback"
    assert mapped["session_id"] == "sess-fb"
    assert mapped["message_index"] == 4
    assert mapped["mode"] == "practice"
    assert mapped["topic"] == "6-04-040"
    assert mapped["entry_source"] == "feedback"
    assert mapped["status"] == "ready"
    assert mapped["feedback_verdict"] == "down"
    # RAW since 2026-07-21: USER_ENTERED made Sheets parse "4/12" as a date
    # serial (46360), destroying correctly-graded fraction answers.
    assert option == "RAW"


def test_log_feedback_invalid_verdict_noop(monkeypatch):
    worksheet = FakeWorksheet()
    fake_gspread = FakeGspread(FakeClient(worksheet))
    monkeypatch.setattr(sl, "gspread", fake_gspread)
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_B64", _b64_creds())
    monkeypatch.setenv("GSHEET_ID", "sheet-123")
    monkeypatch.setenv("SHEETS_ASYNC_LOG", "0")

    assert sl.log_feedback_to_sheet({
        "session_id": "sess-fb",
        "message_index": 4,
        "verdict": "maybe",
    }) is False
    assert worksheet.appended == []


def test_async_event_is_queued_and_delivered(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is True
    assert sl.get_delivery_stats()["queued"] == 1
    assert sl.flush(timeout=2) is True

    assert len(worksheet.appended) == 1
    mapped = _row_map(worksheet.appended[0][0])
    assert mapped["session_id"] == "sess-1"
    assert mapped["sheets_event_id"].startswith("sheet_")
    stats = sl.get_delivery_stats()
    assert stats["delivered"] == 1
    assert stats["pending"] == 0


def test_async_multiple_events_delivered_in_order(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    for idx in range(3):
        p = {**payload, "message_index": idx}
        assert sl.log_transcript_to_sheet(p, response) is True
    assert sl.flush(timeout=2) is True

    assert [_row_map(row)["message_index"] for row, _ in worksheet.appended] == [0, 1, 2]
    assert sl.get_delivery_stats()["delivered"] == 3


def test_async_flush_waits_until_queue_finished(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is True
    assert sl.flush(timeout=2) is True
    assert sl.get_delivery_stats()["pending"] == 0
    assert len(worksheet.appended) == 1


def test_async_shutdown_flush_preserves_queued_events(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    for idx in range(2):
        assert sl.log_transcript_to_sheet({**payload, "message_index": idx}, response) is True
    assert sl.shutdown(wait=True, timeout=2) is True

    assert len(worksheet.appended) == 2
    assert sl.log_transcript_to_sheet({**payload, "message_index": 99}, response) is False
    assert sl.get_delivery_stats()["dropped_on_shutdown"] == 1


def test_async_background_exception_is_observable_and_sanitized(monkeypatch, caplog):
    secret = "VERY_SECRET_PRIVATE_KEY"
    worksheet = SequenceWorksheet([RuntimeError(secret)])
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is True
    assert sl.flush(timeout=2) is True

    stats = sl.get_delivery_stats()
    assert stats["permanently_failed"] == 1
    assert stats["recent_statuses"][-1]["status"] == "permanently_failed"
    assert stats["recent_statuses"][-1]["error_category"] == "unexpected"
    assert secret not in caplog.text


def test_async_transient_failure_retries_then_succeeds(monkeypatch):
    worksheet = SequenceWorksheet([TimeoutError("temporary timeout"), "ok"])
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    monkeypatch.setenv("SHEETS_ASYNC_MAX_RETRIES", "2")
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is True
    assert sl.flush(timeout=2) is True

    assert worksheet.calls == 2
    assert len(worksheet.appended) == 1
    stats = sl.get_delivery_stats()
    assert stats["retried"] == 1
    assert stats["delivered"] == 1


def test_async_permanent_failure_does_not_retry_forever(monkeypatch):
    worksheet = SequenceWorksheet([FakeApiError(403, "forbidden permanent")])
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    monkeypatch.setenv("SHEETS_ASYNC_MAX_RETRIES", "5")
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is True
    assert sl.flush(timeout=2) is True

    assert worksheet.calls == 1
    assert len(worksheet.appended) == 0
    stats = sl.get_delivery_stats()
    assert stats["retried"] == 0
    assert stats["permanently_failed"] == 1
    assert stats["recent_statuses"][-1]["error_category"] == "auth_or_permission"


def test_sync_mode_writes_directly_without_async_queue(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=False)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is True

    stats = sl.get_delivery_stats()
    assert stats["queued"] == 0
    assert stats["delivered"] == 1
    assert len(worksheet.appended) == 1


def test_async_logging_failure_does_not_break_tutor_response(monkeypatch):
    worksheet = SequenceWorksheet([FakeApiError(403, "forbidden permanent")])
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is True
    assert sl.flush(timeout=2) is True
    assert sl.get_delivery_stats()["permanently_failed"] == 1


def test_parent_and_followup_rows_delivered_async(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()
    parent = {
        "task_id": "task-parent",
        "task_status": "completed",
        "attempt_number": 2,
        "wrong_attempt_count": 1,
        "hint_count": 5,
        "solution_revealed": True,
        "solved_with_hints": True,
        "followup_task_id": "task-child",
    }
    followup_response = {
        **response,
        "task_id": "task-child",
        "parent_task_id": "task-parent",
        "followup_task_id": "task-child",
        "task_origin": "independent_followup",
        "completed_parent_task": parent,
        "status": "ready",
    }

    assert sl.log_transcript_to_sheet({**payload, "message_index": "parent"}, {**response, "task_id": "task-parent"}) is True
    assert sl.log_transcript_to_sheet({**payload, "message_index": "followup"}, followup_response) is True
    assert sl.flush(timeout=2) is True

    rows = [_row_map(row) for row, _ in worksheet.appended]
    assert [r["message_index"] for r in rows] == ["parent", "followup"]
    assert '"task_id": "task-parent"' in rows[1]["completed_parent_task"]


def test_rapid_consecutive_turns_do_not_lose_rows(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    for idx in range(25):
        assert sl.log_transcript_to_sheet({**payload, "message_index": idx}, response) is True
    assert sl.flush(timeout=5) is True

    assert len(worksheet.appended) == 25
    assert sl.get_delivery_stats()["delivered"] == 25


def test_separate_sessions_not_merged(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet({**payload, "session_id": "sess-a", "message_index": 1}, response) is True
    assert sl.log_transcript_to_sheet({**payload, "session_id": "sess-b", "message_index": 1}, response) is True
    assert sl.flush(timeout=2) is True

    rows = [_row_map(row) for row, _ in worksheet.appended]
    assert {r["session_id"] for r in rows} == {"sess-a", "sess-b"}
    assert len({r["sheets_event_id"] for r in rows}) == 2


def test_new_process_initialization_after_shutdown_gets_fresh_worker(monkeypatch):
    worksheet = FakeWorksheet()
    _install_fake_sheets(monkeypatch, worksheet, async_enabled=True)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is True
    assert sl.shutdown(wait=True, timeout=2) is True
    sl._reset_async_state_for_tests()
    sl.sheet = None
    sl._sheets_initialized = False
    sl._sheet_layout_prepared = False

    assert sl.log_transcript_to_sheet({**payload, "message_index": "fresh"}, response) is True
    assert sl.flush(timeout=2) is True
    assert len(worksheet.appended) == 2
