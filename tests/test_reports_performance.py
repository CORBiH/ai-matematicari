"""Reports performance contracts over local, synthetic libSQL only."""
from pathlib import Path

import pytest

from matbot import report_input, reporting_db, reporting_schema
from tests.test_thinkific_progress_import import build_v1, migrate

libsql = pytest.importorskip("libsql")


class CountingConnection:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        return self.delegate.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.delegate, name)


@pytest.fixture
def measured_db(tmp_path):
    path = str(tmp_path / "reports-performance.db")
    build_v1(path)
    migrate(path)
    raw = libsql.connect(path)
    raw.execute("DROP TABLE monthly_reports")
    raw.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    raw.execute(reporting_schema.MONTHLY_REPORTS_INDEX_DDL)
    for index in range(3):
        student = raw.execute(
            "INSERT INTO students "
            "(display_name, grade, status, grade_confirmed_at, grade_source, "
            " account_type) VALUES (?, 6, 'active', CURRENT_TIMESTAMP, "
            " 'admin', 'STUDENT')",
            ("Benchmark %s" % index,)).lastrowid
        raw.execute(
            "INSERT INTO student_current_grades "
            "(student_id, grade, confirmed_at, source) "
            "VALUES (?, 6, CURRENT_TIMESTAMP, 'admin')", (student,))
    raw.commit()
    raw.close()

    wrapped = CountingConnection(libsql.connect(
        path, timeout=10.0, _check_same_thread=False))
    database = reporting_db.ReportingDatabase(connect_factory=lambda: wrapped)
    yield database, wrapped, path
    database.close()


def test_web_readiness_is_one_query_after_connection_warmup(measured_db):
    database, connection, _path = measured_db
    database._connection()
    connection.calls = 0

    state = database.check_reports_readiness()

    assert state["connected"] is True
    assert state["schema_version"] == reporting_schema.CURRENT_SCHEMA_VERSION
    assert state["missing_tables"] == []
    assert connection.calls == 1


def test_roster_summary_is_one_query_for_a_batch(measured_db):
    database, connection, _path = measured_db
    student_ids = [row["student_id"] for row in database.list_students()]
    connection.calls = 0
    start, end = report_input.month_bounds("2026-09")

    metrics = database.fetch_report_roster_metrics(
        student_ids, "2026-09", "2026-08", start, end)

    assert set(metrics) == set(student_ids)
    assert all(item["practice_tasks"] == 0 for item in metrics.values())
    assert connection.calls == 1


def test_warm_full_monthly_input_uses_five_queries(measured_db):
    database, connection, _path = measured_db
    # First pass resolves and caches connection-level schema capabilities.
    report_input.build_report_input(1, "2026-09", database=database)
    connection.calls = 0

    report_input.build_report_input(1, "2026-09", database=database)

    # profile + normalized grades, sessions, current/prior Thinkific pair,
    # and the combined MAT-BOT aggregate.
    assert connection.calls == 5


def test_successful_monthly_report_schema_check_is_cached_per_connection(
        measured_db):
    database, connection, _path = measured_db
    assert database.fetch_monthly_report(1, "2026-09") is None
    connection.calls = 0

    assert database.fetch_monthly_report(1, "2026-09") is None

    assert connection.calls == 1


def test_proposed_indexes_change_the_representative_query_plans(measured_db):
    _database, _connection, path = measured_db
    connection = libsql.connect(path)
    migration = (Path(__file__).parents[1] / "scripts" / "migrations"
                 / "reporting_performance_indexes.sql")
    statements = []
    current = []
    for line in migration.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("--"):
            continue
        current.append(line)
        if ";" in line:
            statements.append("\n".join(current).strip().rstrip(";"))
            current = []
    for statement in statements:
        connection.execute(statement)

    activity_plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT event_type, COUNT(*) "
        "FROM learning_activity WHERE student_id = ? AND source = ? "
        "AND occurred_at >= ? AND occurred_at < ? GROUP BY event_type",
        (1, "matbot", "2026-09-01", "2026-10-01")).fetchall()
    assessment_plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*), AVG(score_percent) "
        "FROM assessment_attempts WHERE student_id = ? AND source = ? "
        "AND completed_at IS NOT NULL AND completed_at >= ? "
        "AND completed_at < ?",
        (1, "matbot", "2026-09-01", "2026-10-01")).fetchall()
    account_plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM student_accounts "
        "WHERE student_id = ? AND provider = ?",
        (1, "thinkific_email")).fetchall()
    connection.close()

    assert any("idx_learning_activity_source_month_student" in row[3]
               for row in activity_plan)
    assert any("idx_assessment_attempts_source_month_student" in row[3]
               for row in assessment_plan)
    assert any("idx_student_accounts_student_provider" in row[3]
               for row in account_plan)
