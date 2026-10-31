"""Local-only durability, privacy and recovery tests for reporting facts."""
import json
import sqlite3
import time

import pytest

from matbot import (activity, auth, config, reporting_db, reporting_outbox,
                    student_identity)
from tests.conftest import make_quick_output


class FakeReportingDatabase:
    def __init__(self, failures=0, owner=None):
        self.failures = failures
        self.owner = owner
        self.events = []
        self.generated = []
        self.completed = []

    def close(self):
        pass

    def find_student(self, _provider, _external_user_id):
        return self.owner

    def record_learning_activity(self, student_id, events):
        if self.failures:
            self.failures -= 1
            raise reporting_db.ReportingUnavailable("temporary_test_failure")
        self.events.extend((student_id, event) for event in events)
        return len(events)

    def record_assessment_generated(self, student_id, attempt):
        self.generated.append((student_id, attempt))
        return True

    def record_assessment_completed(self, student_id, attempt, items):
        self.completed.append((student_id, attempt, items))
        return 1


@pytest.fixture
def durable(tmp_path, monkeypatch):
    reporting_db.shutdown()
    path = tmp_path / "reporting-outbox.sqlite3"
    monkeypatch.setenv("MATBOT_REPORTING_OUTBOX_PATH", str(path))
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-only")
    monkeypatch.setattr(config, "REPORTING_OUTBOX_POLL_S", 0.01)
    monkeypatch.setattr(config, "REPORTING_OUTBOX_RETRY_BASE_S", 0.01)
    monkeypatch.setattr(config, "REPORTING_OUTBOX_RETRY_MAX_S", 0.02)
    monkeypatch.setattr(config, "REPORTING_DB_TIMEOUT_S", 0.03)
    yield path
    reporting_db.shutdown()


def event(event_type=activity.QUICK_COMPLETED, *, grade=6, mode="quick",
          key="quick:test-session:test-turn", occurred_at="2026-11-05 12:00:00",
          metadata=None):
    return activity.ActivityEvent(
        event_type, key, grade=grade, mode=mode,
        lesson_id="%s-01-005" % grade,
        metadata=metadata or {}, occurred_at=occurred_at)


@pytest.mark.parametrize("grade", [6, 7, 8, 9])
@pytest.mark.parametrize("event_type,mode", [
    (activity.PRACTICE_TASK_PRESENTED, "practice"),
    (activity.EXPLAIN_COMPLETED, "explain"),
    (activity.QUICK_COMPLETED, "quick"),
    (activity.KONTROLNI_GENERATED, "kontrolni"),
])
def test_all_grades_and_modes_cross_the_same_durable_path(
        durable, grade, event_type, mode):
    database = FakeReportingDatabase()
    reporting_db.set_database(database)
    accepted = reporting_db.record_turn(
        41, [event(event_type, grade=grade, mode=mode,
                   key="%s:%s" % (mode, grade))])

    assert accepted is True
    assert reporting_db.wait_for_pending_writes(2.0) is True
    assert [(owner, item.event_type, item.grade, item.mode)
            for owner, item in database.events] == [
                (41, event_type, grade, mode)]


def test_transient_failure_retries_without_changing_tutor_acceptance(durable):
    database = FakeReportingDatabase(failures=1)
    reporting_db.set_database(database)

    assert reporting_db.record_turn(8, [event()]) is True
    assert reporting_db.wait_for_pending_writes(2.0) is True
    assert len(database.events) == 1
    health = reporting_db.outbox_health()
    assert health["pending"] == health["failed"] == 0
    assert health["delivered"] == 1


def test_turso_timeout_leaves_row_pending_then_retries(durable):
    class TimeoutOnceDatabase(FakeReportingDatabase):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def record_learning_activity(self, student_id, events):
            self.calls += 1
            if self.calls == 1:
                time.sleep(0.15)
                raise reporting_db.ReportingUnavailable("late_timeout_test")
            return super().record_learning_activity(student_id, events)

    database = TimeoutOnceDatabase()
    reporting_db.set_database(database)

    assert reporting_db.record_turn(18, [event()]) is True
    assert reporting_db.wait_for_pending_writes(2.0) is True
    assert database.calls >= 2
    assert len(database.events) == 1
    assert reporting_db.outbox_health()["delivered"] == 1


def test_duplicate_enqueue_and_delivery_is_idempotent(durable):
    database = FakeReportingDatabase()
    reporting_db.set_database(database)
    one = event()

    assert reporting_db.record_turn(9, [one]) is True
    assert reporting_db.record_turn(9, [one]) is True
    assert reporting_db.wait_for_pending_writes(2.0) is True

    assert len(database.events) == 1
    assert reporting_db.outbox_health()["delivered"] == 1


def test_generated_and_completed_assessments_use_durable_delivery(durable):
    database = FakeReportingDatabase()
    reporting_db.set_database(database)
    attempt = {
        "assessment_type": "kontrolni", "external_attempt_id": "exam-safe-id",
        "grade": 6, "area_name": "Brojevi", "total_count": 5,
        "started_at": "2026-11-05 12:00:00",
    }
    assert reporting_db.record_turn(
        10, [event(activity.KONTROLNI_GENERATED, mode="kontrolni")],
        {"kind": reporting_db.ASSESSMENT_GENERATED,
         "attempt": attempt}) is True
    assert reporting_db.wait_for_pending_writes(2.0) is True
    assert database.generated == [(10, attempt)]

    completed = dict(attempt, score_percent=80, correct_count=4,
                     completed_at="2026-11-05 12:10:00")
    items = [{"item_key": "q1", "ordinal": 1, "is_correct": True}]
    assert reporting_db.record_turn(
        10, [event(activity.KONTROLNI_COMPLETED, mode="kontrolni",
                   key="kontrolni:exam-safe-id:completed")],
        {"kind": reporting_db.ASSESSMENT_COMPLETED,
         "attempt": completed, "items": items}) is True
    assert reporting_db.wait_for_pending_writes(2.0) is True
    assert database.completed == [(10, completed, items)]


def test_unattributed_is_private_and_requires_explicit_approval(durable):
    database = FakeReportingDatabase()
    reporting_db.set_database(database)
    subject = "v1:" + ("a" * 64)
    raw_email = "private.student@example.com"
    private_message = "NE SMIJE BITI SACUVANO"
    unsafe = event(metadata={
        "difficulty": "standard", "student_message": private_message,
        "email": raw_email,
    })

    assert reporting_db.record_turn(
        None, [unsafe], subject_key=subject) is True
    assert reporting_db.outbox_health()["unattributed"] == 1
    assert database.events == []

    blobs = []
    for candidate in (durable, durable.with_name(durable.name + "-wal")):
        if candidate.exists():
            blobs.append(candidate.read_bytes())
    persisted = b"".join(blobs)
    assert raw_email.encode() not in persisted
    assert private_message.encode() not in persisted

    assert reporting_db.approve_unattributed(subject, 77) == 1
    assert reporting_db.wait_for_pending_writes(2.0) is True
    assert [(owner, item.grade) for owner, item in database.events] == [(77, 6)]
    assert reporting_db.outbox_health()["unattributed"] == 0

    # A request that resolved just before admin approval but commits just after
    # it cannot recreate an orphan pending row; the approved local mapping
    # routes it directly to the official queue.
    late = event(key="quick:late-race:turn")
    assert reporting_db.record_turn(None, [late], subject_key=subject) is True
    assert reporting_db.wait_for_pending_writes(2.0) is True
    assert [owner for owner, _item in database.events] == [77, 77]
    assert reporting_db.outbox_health()["unattributed"] == 0


def test_resolved_turn_does_not_approve_older_unsigned_activity(durable):
    database = FakeReportingDatabase()
    reporting_db.set_database(database)
    subject = "v1:" + ("b" * 64)
    older = event(key="quick:unsigned-history:turn")
    current = event(key="quick:linked-current:turn")

    assert reporting_db.record_turn(None, [older], subject_key=subject) is True
    assert reporting_db.record_turn(77, [current], subject_key=subject) is True
    assert reporting_db.wait_for_pending_writes(2.0) is True

    assert reporting_db.outbox_health()["unattributed"] == 1
    assert [(owner, item.event_key) for owner, item in database.events] == [
        (77, current.event_key)]


def test_missing_identity_is_not_accepted(durable):
    database = FakeReportingDatabase()
    reporting_db.set_database(database)
    assert reporting_db.record_turn(None, [event()]) is False
    assert reporting_db.outbox_health()["unattributed"] == 0


def test_unwritable_outbox_rejects_acceptance_and_is_observable(
        tmp_path, monkeypatch):
    reporting_db.shutdown()
    # A directory cannot be opened as a SQLite database file.
    monkeypatch.setenv("MATBOT_REPORTING_OUTBOX_PATH", str(tmp_path))
    reporting_db.set_database(FakeReportingDatabase())
    try:
        assert reporting_db.record_turn(19, [event()]) is False
        health = reporting_db.outbox_health()
        assert health["configured"] is True
        assert health["writable"] is False
        assert "error" in health
    finally:
        reporting_db.shutdown()


def _embed_token(client, email_marker=None):
    query = ({student_identity.QUERY_PARAM: email_marker}
             if email_marker is not None else {})
    body = client.get("/", query_string=query).get_data(as_text=True)
    marker = 'name="matbot-embed-token" content="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def _quick(client, token, turn_id):
    return client.post("/api/ai-tutor/chat", json={
        "session_id": "outbox-identity-" + turn_id,
        "client_turn_id": turn_id,
        "grade": 6,
        "mode": "quick",
        "entry_source": "manual_topic_choice",
        "selected_topic": "6-01-005",
        "selected_oblast": "",
        "conversation_history": [],
        "student_message": "Koliko je 2+2?",
    }, headers={auth.TOKEN_HEADER: token})


def test_valid_but_unlinked_embed_identity_is_pending_not_a_student(
        durable, client, fake_llm):
    database = FakeReportingDatabase(owner=None)
    reporting_db.set_database(database)
    fake_llm.queue(make_quick_output("Rezultat je $4$."))

    response = _quick(
        client, _embed_token(client, "new.student@example.com"), "valid-unlinked")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    assert reporting_db.outbox_health()["unattributed"] == 1
    assert database.events == []


@pytest.mark.parametrize("email_marker", [None, "", "{{email}}"])
def test_missing_empty_or_literal_embed_identity_stays_anonymous(
        durable, client, fake_llm, email_marker):
    database = FakeReportingDatabase(owner=None)
    reporting_db.set_database(database)
    fake_llm.queue(make_quick_output("Rezultat je $4$."))

    marker_name = ("literal" if email_marker == "{{email}}" else
                   "empty" if email_marker == "" else "missing")
    response = _quick(client, _embed_token(client, email_marker),
                      "anonymous-" + marker_name)

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    assert reporting_db.outbox_health()["unattributed"] == 0
    assert database.events == []


@pytest.mark.parametrize("account_type", ["STUDENT", "SUPPORT", "TEST"])
def test_existing_identity_types_keep_tracking(account_type, durable, client,
                                               fake_llm):
    # Account type is a report-population concern, not a tutor-path tracking
    # gate. A pre-approved mapping resolves identically for all three types.
    database = FakeReportingDatabase(owner=91)
    reporting_db.set_database(database)
    fake_llm.queue(make_quick_output("Rezultat je $4$."))

    response = _quick(
        client, _embed_token(client, "%s@example.com" % account_type.lower()),
        "linked-" + account_type.lower())

    assert response.status_code == 200
    assert reporting_db.wait_for_pending_writes(2.0) is True
    assert [owner for owner, _event in database.events] == [91]
    assert reporting_db.outbox_health()["unattributed"] == 0


def test_restart_recovers_in_flight_row(tmp_path):
    path = tmp_path / "restart.sqlite3"
    first = reporting_outbox.ReportingOutbox(path)
    first.enqueue(12, [event()])
    first.close()
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE reporting_outbox SET state = 'in_flight'")

    delivered = []
    second = reporting_outbox.ReportingOutbox(
        path, lambda payload: (
            delivered.append(payload) or reporting_outbox.DeliveryResult(True)))
    try:
        assert second.process_one() is True
        assert delivered[0]["student_id"] == 12
        assert second.health()["delivered"] == 1
    finally:
        second.close()


def test_month_state_only_blocks_the_affected_period(tmp_path):
    path = tmp_path / "months.sqlite3"
    outbox = reporting_outbox.ReportingOutbox(path)
    try:
        outbox.enqueue(15, [event(occurred_at="2026-10-31 23:59:59")])
        outbox.enqueue(15, [event(key="quick:november:2",
                                  occurred_at="2026-11-01 00:00:00")])
        october = outbox.report_state(
            15, "2026-10-01 00:00:00", "2026-11-01 00:00:00")
        november = outbox.report_state(
            15, "2026-11-01 00:00:00", "2026-12-01 00:00:00")
        december = outbox.report_state(
            15, "2026-12-01 00:00:00", "2027-01-01 00:00:00")
        assert october == {"pending": 1, "failed": 0, "version": 1}
        assert november == {"pending": 1, "failed": 0, "version": 2}
        assert december == {"pending": 0, "failed": 0, "version": 0}
    finally:
        outbox.close()


def test_write_preflight_proves_writable_without_retaining_probe(tmp_path):
    path = tmp_path / "preflight.sqlite3"
    outbox = reporting_outbox.ReportingOutbox(path)
    try:
        assert outbox.verify_writable() is True
        with sqlite3.connect(path) as conn:
            assert conn.execute(
                "SELECT value FROM outbox_meta WHERE key = '__write_probe__'"
            ).fetchone() is None
    finally:
        outbox.close()


def test_corrupt_payload_becomes_visible_failure_not_silent_loss(tmp_path):
    path = tmp_path / "invalid.sqlite3"
    outbox = reporting_outbox.ReportingOutbox(path)
    try:
        outbox.enqueue(16, [event()])
        outbox.close()
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE reporting_outbox SET payload_json = ?",
                         (json.dumps({"v": 999}),))
        restarted = reporting_outbox.ReportingOutbox(
            path, reporting_db._outbox_delivery)
        try:
            assert restarted.process_one() is True
            assert restarted.health()["failed"] == 1
        finally:
            restarted.close()
    finally:
        outbox.close()


def test_corrupt_database_is_rejected_at_startup(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"not-a-sqlite-database")
    with pytest.raises(reporting_outbox.OutboxError) as caught:
        reporting_outbox.ReportingOutbox(path)
    assert caught.value.code.startswith("outbox_initialize_failed:DatabaseError")
