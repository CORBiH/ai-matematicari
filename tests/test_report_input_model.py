"""Faza 3A — migracija v1→v2 i SPOJENI determinističi ulaz za izvještaj.

Dokazuje se troje:
  1. MIGRACIJA je aditivna: v2 dodaje tri tabele i ne dira nijedan postojeći red.
  2. SPOJENI OBJEKAT sadrži samo brojeve koje je izračunao SQL/Python — nijedan
     ne dolazi od modela i nijedan se ne izmišlja kad podatak nedostaje.
  3. POPULACIJA je UNIJA: učenik samo iz Thinkifica i učenik samo iz MAT-BOT-a
     oba dobijaju izvještaj.
"""
import pytest

from matbot import activity, report_input, reporting_db, reporting_schema
from matbot.api import _kontrolni_attempt
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.fixtures.thinkific import build_csv, learner, with_sections
from tests.test_thinkific_progress_import import build_v1, migrate, rows, simple_csv

libsql = pytest.importorskip("libsql")

E1 = "student1@example.com"
E2 = "student2@example.com"


@pytest.fixture
def v1(tmp_path):
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    return path


@pytest.fixture
def db(v1, monkeypatch):
    migrate(v1)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(v1, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield v1
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


# ---------------------------------------------------------------------------
# Migracija v1 -> v2
# ---------------------------------------------------------------------------
def test_migration_adds_only_the_three_new_tables(v1):
    conn = libsql.connect(v1)
    before = reporting_schema.table_names(conn)
    assert not (set(reporting_schema.V2_TABLES) & before)

    assert reporting_schema.migrate_to_v2(conn) is True
    after = reporting_schema.table_names(conn)
    conn.close()

    assert set(reporting_schema.V2_TABLES) <= after
    assert before <= after, "postojeća tabela je nestala"
    assert after - before == set(reporting_schema.V2_TABLES)


def test_migration_preserves_existing_phase1_and_phase2_rows(v1):
    conn = libsql.connect(v1)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('Ana', 6)")
    conn.execute("INSERT INTO student_accounts (student_id, provider, "
                 "external_user_id) VALUES (1, ?, ?)", (PROVIDER_THINKIFIC_EMAIL, E1))
    conn.execute("INSERT INTO learning_activity (student_id, source, event_type, "
                 "event_key, occurred_at) VALUES (1,'matbot',?, 'k1', "
                 "'2026-09-05 10:00:00')", (activity.PRACTICE_TASK_PRESENTED,))
    conn.execute("INSERT INTO assessment_attempts (student_id, source, "
                 "assessment_type, external_attempt_id, score_percent, "
                 "correct_count, total_count, completed_at) "
                 "VALUES (1,'matbot','kontrolni','e1',80,4,5,'2026-09-06 11:00:00')")
    conn.commit()
    before = (conn.execute("SELECT * FROM students").fetchall(),
              conn.execute("SELECT * FROM student_accounts").fetchall(),
              conn.execute("SELECT * FROM learning_activity").fetchall(),
              conn.execute("SELECT * FROM assessment_attempts").fetchall())

    reporting_schema.migrate_to_v2(conn)

    after = (conn.execute("SELECT * FROM students").fetchall(),
             conn.execute("SELECT * FROM student_accounts").fetchall(),
             conn.execute("SELECT * FROM learning_activity").fetchall(),
             conn.execute("SELECT * FROM assessment_attempts").fetchall())
    conn.close()
    assert before == after


def test_migration_is_idempotent_and_records_version_once(v1):
    conn = libsql.connect(v1)
    assert reporting_schema.migrate_to_v2(conn) is True
    assert reporting_schema.migrate_to_v2(conn) is False
    assert reporting_schema.migrate_to_v2(conn) is False

    versions = conn.execute("SELECT version FROM schema_migrations "
                            "ORDER BY version").fetchall()
    conn.close()
    assert [v[0] for v in versions] == [1, 2]


def test_migration_refuses_a_database_that_is_not_v1(tmp_path):
    path = str(tmp_path / "foreign.db")
    conn = libsql.connect(path)
    conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                 "applied_at TEXT)")
    conn.commit()
    with pytest.raises(reporting_schema.MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    conn.close()
    assert "v1_schema_incomplete" in str(error.value)


def test_diagnostic_reports_version_two_after_migration(db):
    report = reporting_db.get_database().check()
    assert report["schema_version"] == 2
    assert "thinkific_progress_snapshots" in report["columns"]


# ---------------------------------------------------------------------------
# Pomoćnici za MAT-BOT podatke
# ---------------------------------------------------------------------------
def seed_matbot(db_path, student_id, month="2026-09"):
    lesson = dict(mode="practice", grade=6, area_name="Skupovi",
                  lesson_id="6-01-005", lesson_name="Presjek skupova")
    database = reporting_db.get_database()
    stamp = "%s-05 10:00:00" % month
    database.record_learning_activity(student_id, [
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "p1",
                               occurred_at=stamp, **lesson),
        activity.ActivityEvent(activity.PRACTICE_ANSWER_CORRECT, "a1",
                               occurred_at=stamp, **lesson),
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "p2",
                               occurred_at="%s-07 09:00:00" % month, **lesson),
        activity.ActivityEvent(activity.PRACTICE_ANSWER_INCORRECT, "a2",
                               occurred_at="%s-07 09:00:00" % month, **lesson),
        activity.ActivityEvent(activity.PRACTICE_HINT_USED, "h1",
                               occurred_at="%s-07 09:01:00" % month, **lesson),
        activity.ActivityEvent(activity.EXPLAIN_COMPLETED, "e1", mode="explain",
                               grade=6, occurred_at=stamp),
        activity.ActivityEvent(activity.QUICK_COMPLETED, "q1", mode="quick",
                               grade=6, occurred_at=stamp),
        # Produkcija UZ svaki ocijenjen kontrolni pise i dogadjaj aktivnosti;
        # bez njega bi dan predaje testa nedostajao u `active_days`.
        activity.ActivityEvent(activity.KONTROLNI_COMPLETED, "k1", mode="kontrolni",
                               grade=6, occurred_at="%s-20 12:00:00" % month),
    ])
    database.record_assessment_completed(
        student_id,
        _kontrolni_attempt("exam-1", grade=6, area_name="Razlomci", total_count=5,
                           correct_count=4, score_percent=80,
                           completed_at="%s-20 12:00:00" % month),
        [{"item_key": "q1", "ordinal": 1, "is_correct": True,
          "lesson_id": "6-04-001", "lesson_name": "Sabiranje", "difficulty": "standard"},
         {"item_key": "q2", "ordinal": 2, "is_correct": False,
          "lesson_id": "6-04-005", "lesson_name": "Složeni", "difficulty": "harder"},
         {"item_key": "q3", "ordinal": 3, "is_correct": False,
          "lesson_id": "6-04-005", "lesson_name": "Složeni", "difficulty": "harder"},
         {"item_key": "q4", "ordinal": 4, "is_correct": False,
          "lesson_id": "6-04-005", "lesson_name": "Složeni", "difficulty": "harder"},
         {"item_key": "q5", "ordinal": 5, "is_correct": True,
          "lesson_id": "6-04-001", "lesson_name": "Sabiranje", "difficulty": "standard"}])


# ---------------------------------------------------------------------------
# MAT-BOT mjesečni model
# ---------------------------------------------------------------------------
def test_matbot_month_metrics_are_deterministic(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    seed_matbot(db, student_id)

    view = report_input.build_matbot_section(student_id, "2026-09")

    assert view["active_days"] == 3
    assert view["practice_tasks"] == 2
    assert (view["practice_correct"], view["practice_incorrect"]) == (1, 1)
    assert view["practice_accuracy"] == 50.0
    assert view["hints_used"] == 1
    assert view["explain_count"] == 1 and view["quick_count"] == 1
    assert view["kontrolni_attempts"] == 1
    assert view["kontrolni_average"] == 80.0
    assert (view["kontrolni_correct"], view["kontrolni_total"]) == (4, 5)


def test_weak_lessons_come_from_item_results_with_evidence_strength(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    seed_matbot(db, student_id)

    outcomes = report_input.build_matbot_section(student_id, "2026-09")["lesson_outcomes"]
    worst = outcomes[0]

    assert worst["lesson_id"] == "6-04-005"
    assert worst["incorrect_items"] == 3 and worst["evidence_items"] == 3
    assert worst["low_evidence"] is False
    # Lekcija s malo dokaza NIJE proglašena slabom bez ograde.
    thin = [o for o in outcomes if o["lesson_id"] == "6-04-001"][0]
    assert thin["incorrect_items"] == 0 and thin["evidence_items"] == 2
    assert thin["low_evidence"] is True


def test_zero_practice_yields_counts_of_zero_and_null_accuracy(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})
    student_id = rows(db, "SELECT id FROM students")[0][0]

    view = report_input.build_matbot_section(student_id, "2026-09")
    assert view["practice_tasks"] == 0 and view["practice_correct"] == 0
    assert view["practice_accuracy"] is None, "bez imenioca nema 0 %, nego NULL"
    assert view["kontrolni_attempts"] == 0
    assert view["kontrolni_average"] is None


def test_month_boundaries_are_utc_and_half_open(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    database = reporting_db.get_database()
    lesson = dict(mode="practice", grade=6, lesson_id="6-01-005")
    database.record_learning_activity(student_id, [
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "before",
                               occurred_at="2026-08-31 23:59:59", **lesson),
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "first",
                               occurred_at="2026-09-01 00:00:00", **lesson),
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "last",
                               occurred_at="2026-09-30 23:59:59", **lesson),
        activity.ActivityEvent(activity.PRACTICE_TASK_PRESENTED, "after",
                               occurred_at="2026-10-01 00:00:00", **lesson),
    ])

    assert report_input.month_bounds("2026-09") == ("2026-09-01 00:00:00",
                                                    "2026-10-01 00:00:00")
    view = report_input.build_matbot_section(student_id, "2026-09")
    assert view["practice_tasks"] == 2, "granice nisu [start, next)"


# ---------------------------------------------------------------------------
# Spojeni objekat i populacija
# ---------------------------------------------------------------------------
def test_joined_report_input_has_both_sources(db):
    report_input.import_progress_files("2026-08", {"grade_6": build_csv(
        [with_sections(learner(E1, viewed=40, completed=31), {"SKUPOVI": 50})],
        sections=["SKUPOVI"])})
    report_input.import_progress_files("2026-09", {"grade_6": build_csv(
        [with_sections(learner(E1, viewed=62, completed=48), {"SKUPOVI": 75})],
        sections=["SKUPOVI"])})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    seed_matbot(db, student_id)

    payload = report_input.build_report_input(student_id, "2026-09")

    assert payload["student_id"] == student_id
    assert payload["report_month"] == "2026-09"
    assert payload["profile"]["grade"] == 6
    assert payload["thinkific"]["snapshot_missing"] is False
    assert payload["thinkific"]["course_name"] == "Matematika za 6. razred"
    assert payload["thinkific"]["delta_percent_completed"] == 17.0
    assert payload["thinkific"]["sections"][0]["delta_progress_percent"] == 25.0
    assert payload["matbot"]["practice_tasks"] == 2
    assert payload["matbot"]["kontrolni_average"] == 80.0


def test_matbot_only_learner_is_included_with_snapshot_missing(db):
    database = reporting_db.get_database()
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, E2)
    seed_matbot(db, student_id)

    payload = report_input.build_report_input(student_id, "2026-09")
    assert payload["thinkific"] == {"snapshot_missing": True}
    assert payload["matbot"]["practice_tasks"] == 2

    assert student_id in report_input.report_population("2026-09")


def test_thinkific_only_learner_is_included_with_real_zero_matbot(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv()})
    student_id = rows(db, "SELECT id FROM students")[0][0]

    payload = report_input.build_report_input(student_id, "2026-09")
    assert payload["thinkific"]["snapshot_missing"] is False
    assert payload["matbot"]["practice_tasks"] == 0
    assert payload["matbot"]["practice_accuracy"] is None
    assert student_id in report_input.report_population("2026-09")


def test_population_is_the_union_of_both_sources(db):
    report_input.import_progress_files("2026-09", {"grade_6": simple_csv(email=E1)})
    database = reporting_db.get_database()
    matbot_only = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, E2)
    seed_matbot(db, matbot_only)
    thinkific_only = rows(db, "SELECT id FROM students WHERE id != ?",
                          (matbot_only,))[0][0]

    population = report_input.report_population("2026-09")
    assert sorted(population) == sorted([thinkific_only, matbot_only])
    # Drugi mjesec bez ijednog izvora je prazan — nikad se ne izmišlja.
    assert report_input.report_population("2026-12") == []


def test_report_input_contains_no_email(db):
    report_input.import_progress_files(
        "2026-09", {"grade_6": simple_csv(first="Ana", last="Anić")})
    student_id = rows(db, "SELECT id FROM students")[0][0]
    seed_matbot(db, student_id)

    rendered = str(report_input.build_report_input(student_id, "2026-09"))
    assert "@" not in rendered and E1 not in rendered
    # Ime SMIJE postojati — ono je profil za izvještaj, ne identitet.
    assert "Ana Anić" in rendered
