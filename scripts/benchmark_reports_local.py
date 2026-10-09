"""Temporary local-only benchmark for the Reports deployment review."""
import json
import os
import re
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for raw_line in (ROOT / "deploy" / "production_release.env").read_text(
        encoding="utf-8").splitlines():
    line = raw_line.strip()
    if line and not line.startswith("#"):
        name, value = line.split("=", 1)
        os.environ[name] = value

os.environ["FLASK_SECRET_KEY"] = "benchmark-only-secret-key-000000"
os.environ["MATBOT_ADMIN_PASSWORD"] = "benchmark-admin-password"
os.environ["MATBOT_ADMIN_COOKIE_SECURE"] = "disabled"
os.environ["TURSO_DATABASE_URL"] = "libsql://benchmark.invalid"
os.environ["TURSO_AUTH_TOKEN"] = "benchmark-token-not-real"
sys.path.insert(0, str(ROOT))

import libsql
from werkzeug.datastructures import MultiDict

import app as app_module
from matbot import (admin_reports, parent_report, report_facts, report_input,
                    report_prompt, reporting_db, reporting_schema)
from tests.test_parent_report import good_narrative
from tests.test_thinkific_progress_import import build_v1, migrate


class CountingConnection:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = 0
        self.execute_seconds = 0.0
        self.lock = threading.Lock()

    def execute(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return self.delegate.execute(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - started
            with self.lock:
                self.calls += 1
                self.execute_seconds += elapsed

    def reset(self):
        with self.lock:
            self.calls = 0
            self.execute_seconds = 0.0

    def snapshot(self):
        with self.lock:
            return self.calls, self.execute_seconds * 1000

    def __getattr__(self, name):
        return getattr(self.delegate, name)


def seed(path):
    build_v1(path)
    migrate(path)
    conn = libsql.connect(path)
    conn.execute("DROP TABLE IF EXISTS monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    conn.execute(reporting_schema.MONTHLY_REPORTS_INDEX_DDL)

    imports = {}
    for month in ("2026-07", "2026-08"):
        for grade in (6, 7, 8, 9):
            cursor = conn.execute(
                "INSERT INTO thinkific_progress_imports "
                "(report_month, course_key, course_name, grade, source_sha256, "
                " row_count) VALUES (?, ?, ?, ?, ?, 30)",
                (month, "grade_%d" % grade, "Grade %d" % grade, grade,
                 (month + "-%d" % grade).encode().hex().ljust(64, "0")[:64]))
            imports[(month, grade)] = cursor.lastrowid

    narrative = json.dumps(good_narrative(), ensure_ascii=False)
    for index in range(120):
        grade = 6 + (index % 4)
        email = "benchmark%03d@example.invalid" % index
        cursor = conn.execute(
            "INSERT INTO students "
            "(display_name, grade, status, grade_confirmed_at, grade_source, "
            " account_type, created_at, updated_at, last_seen_at) "
            "VALUES (?, ?, 'active', CURRENT_TIMESTAMP, 'admin', 'STUDENT', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("Benchmark %03d" % index, grade))
        student_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO student_accounts "
            "(student_id, provider, external_user_id, created_at, last_seen_at) "
            "VALUES (?, 'thinkific_email', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (student_id, email))
        conn.execute(
            "INSERT INTO student_current_grades "
            "(student_id, grade, confirmed_at, source) "
            "VALUES (?, ?, CURRENT_TIMESTAMP, 'admin')", (student_id, grade))

        for month_index, month in enumerate(("2026-07", "2026-08")):
            completed = float((index * 3 + month_index * 11) % 101)
            snapshot = conn.execute(
                "INSERT INTO thinkific_progress_snapshots "
                "(import_id, student_id, report_month, course_key, course_name, "
                " grade, percent_viewed, percent_completed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (imports[(month, grade)], student_id, month,
                 "grade_%d" % grade, "Grade %d" % grade, grade,
                 min(100.0, completed + 5.0), completed)).lastrowid
            for ordinal in range(6):
                conn.execute(
                    "INSERT INTO thinkific_progress_sections "
                    "(snapshot_id, ordinal, section_name, progress_percent) "
                    "VALUES (?, ?, ?, ?)",
                    (snapshot, ordinal, "Section %d" % ordinal,
                     float((index + ordinal * 7 + month_index * 5) % 101)))

        for session_index in range(4):
            conn.execute(
                "INSERT INTO student_sessions "
                "(student_id, session_date, attendance, activity_rating, "
                " homework_status, area_name, lesson_name, comment) "
                "VALUES (?, ?, 'present', 4, 'done', 'Algebra', ?, '')",
                (student_id, "2026-08-%02d" % (3 + session_index * 6),
                 "Lesson %d" % session_index))

        for event_index, event_type in enumerate((
                "practice_task_presented", "practice_answer_correct",
                "practice_task_presented", "practice_answer_incorrect")):
            conn.execute(
                "INSERT INTO learning_activity "
                "(student_id, source, event_type, event_key, grade, area_name, "
                " lesson_id, lesson_name, mode, occurred_at, metadata_json) "
                "VALUES (?, 'matbot', ?, ?, ?, 'Algebra', 'l1', 'Lesson', "
                "'practice', ?, '{}')",
                (student_id, event_type, "bench-%d-%d" % (index, event_index),
                 grade, "2026-08-%02d 10:00:00" % (5 + event_index)))

        conn.execute(
            "INSERT INTO assessment_attempts "
            "(student_id, source, assessment_type, external_attempt_id, grade, "
            " area_name, score_percent, correct_count, total_count, completed_at) "
            "VALUES (?, 'matbot', 'kontrolni', ?, ?, 'Algebra', 80, 4, 5, "
            "'2026-08-20 12:00:00')",
            (student_id, "assessment-%d" % index, grade))

        if index % 2 == 0:
            conn.execute(
                "INSERT INTO monthly_reports "
                "(student_id, report_month, status, metrics_json, ai_summary, "
                " instructor_comment, generated_at) "
                "VALUES (?, '2026-08', 'draft', '{}', ?, '', CURRENT_TIMESTAMP)",
                (student_id, narrative))
    conn.commit()
    conn.close()


def percentile(values, percent):
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def measure(name, operation, wrapper, runs):
    times, calls, db_times, sizes = [], [], [], []
    for _ in range(runs):
        wrapper.reset()
        started = time.perf_counter()
        value = operation()
        times.append((time.perf_counter() - started) * 1000)
        call_count, db_ms = wrapper.snapshot()
        calls.append(call_count)
        db_times.append(db_ms)
        if hasattr(value, "data"):
            sizes.append(len(value.data))
    result = {
        "operation": name,
        "runs": runs,
        "p50_ms": round(statistics.median(times), 2),
        "p95_ms": round(percentile(times, 0.95), 2),
        "sql_p50": statistics.median(calls),
        "db_execute_p50_ms": round(statistics.median(db_times), 2),
    }
    if sizes:
        result["response_bytes_p50"] = statistics.median(sizes)
    print(json.dumps(result, sort_keys=True))
    return result


def csrf(response):
    found = re.search(r'name="csrf_token" value="([^"]+)"',
                      response.get_data(as_text=True))
    return found.group(1)


def main():
    with tempfile.TemporaryDirectory(prefix="matbot-report-bench-") as temp:
        path = str(Path(temp) / "reporting.db")
        seed(path)
        wrapped = CountingConnection(libsql.connect(
            path, timeout=10.0, _check_same_thread=False))
        database = reporting_db.ReportingDatabase(
            connect_factory=lambda: wrapped)
        reporting_db.set_database(database)
        database._connection()

        client = app_module.app.test_client()
        token = csrf(client.get("/admin/reports/login"))
        response = client.post(
            "/admin/reports/login",
            data={"csrf_token": token,
                  "password": "benchmark-admin-password"})
        assert response.status_code == 302

        # Warm every connection-scoped capability cache before measurement.
        client.get("/admin/reports")
        client.get("/admin/reports/students?month=2026-08")
        report_input.build_report_input(1, "2026-08", database=database)
        parent_report.load_saved(1, "2026-08")

        measure("dashboard_warm", lambda: client.get("/admin/reports"),
                wrapped, 12)
        measure("roster_120_warm", lambda: client.get(
            "/admin/reports/students?month=2026-08"), wrapped, 8)
        measure("search_one", lambda: client.get(
            "/admin/reports/students?month=2026-08&search=Benchmark+011"),
            wrapped, 12)
        measure("grade_filter_30", lambda: client.get(
            "/admin/reports/students?month=2026-08&grade=6"), wrapped, 12)
        measure("existing_report", lambda: parent_report.load_saved(
            1, "2026-08"), wrapped, 20)
        measure("monthly_input", lambda: report_input.build_report_input(
            1, "2026-08", database=database), wrapped, 20)

        def preparation():
            payload = report_input.build_report_input(
                1, "2026-08", database=database)
            facts = report_facts.build_ai_facts(payload)
            report_prompt.build_input_text(facts)

        measure("individual_preparation", preparation, wrapped, 20)

        class FakeLLM:
            calls = 0
            lock = threading.Lock()

            def report_turn(self, instructions, input_text):
                with self.lock:
                    FakeLLM.calls += 1
                time.sleep(0.05)
                from matbot.llm import LLMResult
                return LLMResult(output=good_narrative(), latency_ms=50)

        from matbot import llm as llm_module
        original = llm_module.OpenAIPracticeLLM
        llm_module.OpenAIPracticeLLM = FakeLLM
        try:
            student_token = csrf(client.get(
                "/admin/reports/student/1?month=2026-08"))
            measure("individual_generation_fake_50ms", lambda: client.post(
                "/admin/reports/student/1/generate?month=2026-08",
                data={"csrf_token": student_token}), wrapped, 8)

            def sequential_ten():
                responses = [client.post(
                    "/admin/reports/student/%d/generate?month=2026-08"
                    % student_id, data={"csrf_token": student_token})
                    for student_id in range(1, 11)]
                assert all(response.status_code == 302 for response in responses)
                return responses[-1]

            sequential = measure(
                "sequential_10_fake_50ms", sequential_ten, wrapped, 5)
            sequential["throughput_students_per_s"] = round(
                1000 * 10 / sequential["p50_ms"], 2)
            print(json.dumps({"sequential_summary": sequential}, sort_keys=True))

            roster_token = csrf(client.get(
                "/admin/reports/students?month=2026-08"))

            def bulk_ten():
                responses = []
                for offset in range(0, 10, 2):
                    responses.append(client.post(
                        "/admin/reports/bulk/generate",
                        data=MultiDict([
                            ("csrf_token", roster_token),
                            ("month", "2026-08"), ("replace", "1"),
                            ("student_ids", str(offset + 1)),
                            ("student_ids", str(offset + 2)),
                        ])))
                assert all(response.status_code == 200 for response in responses)
                return responses[-1]

            bulk = measure("bulk_10_fake_50ms", bulk_ten, wrapped, 5)
            bulk["throughput_students_per_s"] = round(
                1000 * 10 / bulk["p50_ms"], 2)
            print(json.dumps({"bulk_summary": bulk}, sort_keys=True))
            print(json.dumps({"fake_model_calls": FakeLLM.calls}, sort_keys=True))
        finally:
            llm_module.OpenAIPracticeLLM = original
            reporting_db.set_database(None)
            database.close()


if __name__ == "__main__":
    main()
