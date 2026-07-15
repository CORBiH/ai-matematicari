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

    def append_row(self, values, value_input_option=None):
        if self.fail:
            raise RuntimeError("append failed")
        self.appended.append((values, value_input_option))


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


@pytest.fixture(autouse=True)
def isolate_sheets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in (
        "GOOGLE_SHEETS_CREDENTIALS_B64",
        "GSHEET_ID",
        "GSHEET_NAME",
        "SHEETS_ASYNC_LOG",
    ):
        monkeypatch.delenv(name, raising=False)
    sl.sheet = None
    sl._sheets_initialized = False
    yield
    sl.sheet = None
    sl._sheets_initialized = False


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
    assert row[1:] == [
        "sess-1",
        7,
        "practice",
        "6-01-001",
        "manual_topic_choice",
        "Koliko je 2+2?",
        "4",
        "ready",
    ]
    assert option == "USER_ENTERED"


def test_log_transcript_without_env_is_noop(monkeypatch):
    class NoGspread:
        def authorize(self, creds):
            raise AssertionError("gspread must not be used")

    monkeypatch.setattr(sl, "gspread", NoGspread())
    monkeypatch.setattr(sl, "SACreds", FakeCredentials)
    payload, response = _payload_response()

    assert sl.log_transcript_to_sheet(payload, response) is False


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
