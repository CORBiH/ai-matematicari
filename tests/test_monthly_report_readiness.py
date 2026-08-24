"""Faza 2, Dio 15 — DOKAZ da se mjesečni izvještaj može izvesti iz događaja.

Ovdje se NE generiše izvještaj i ne piše nijedan AI sažetak. Dokazuje se samo
jedno: da struktura `learning_activity` sadrži dovoljno da se tražene brojke
DOBIJU OBIČNIM SQL-om, bez rekonstrukcije razgovora i bez ijednog podatka koji
nismo smjeli sačuvati.

Podaci su LOKALNI i sintetički — nijedan red ne dolazi iz produkcije.

Metrike koje ovdje NISU dokazane su u izvještaju izričito označene kao
NIJE JOŠ MJERLJIVO (vrijeme na zadatku, napredak u procentima, i sve što bi
tražilo Thinkific sinhronizaciju).
"""
import json

import pytest

from matbot import activity, reporting_db
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_assessment_capture import build_schema

libsql = pytest.importorskip("libsql")


@pytest.fixture
def db(tmp_path):
    """Ista sema kao produkcijska v1 (identitet + aktivnost + procjena)."""
    path = str(tmp_path / "reporting.db")
    build_schema(path)
    return path


def query(path, sql, params=()):
    conn = libsql.connect(path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture
def seeded(db):
    """Jedan učenik, dva dana, svi modovi — dovoljno za sve tražene brojke."""
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(db, timeout=30.0,
                                               _check_same_thread=False))
    student_id = database.get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "ucenik@example.com", grade=6)

    def event(event_type, key, **kw):
        return activity.ActivityEvent(event_type, key, **kw)

    practice = dict(mode="practice", grade=6, area_name="Skupovi",
                    lesson_id="6-01-005", lesson_name="Presjek skupova")
    geometry = dict(mode="practice", grade=7, area_name="Geometrija",
                    lesson_id="7-03-002", lesson_name="Uglovi")

    database.record_learning_activity(student_id, [
        event(activity.PRACTICE_TASK_PRESENTED, "p:1", **practice),
        event(activity.PRACTICE_ANSWER_CORRECT, "p:1:a", **practice),
        event(activity.PRACTICE_TASK_PRESENTED, "p:2", **practice),
        event(activity.PRACTICE_ANSWER_INCORRECT, "p:2:a", **practice),
        event(activity.PRACTICE_HINT_USED, "p:2:h", metadata={"hint_level": 1}, **practice),
        event(activity.PRACTICE_TASK_PRESENTED, "p:3", **geometry),
        event(activity.PRACTICE_ANSWER_CORRECT, "p:3:a", **geometry),
        event(activity.PRACTICE_FULL_SOLUTION_SHOWN, "p:3:s", **geometry),
        event(activity.EXPLAIN_COMPLETED, "e:1", mode="explain", grade=6,
              lesson_id="6-01-005"),
        event(activity.EXPLAIN_COMPLETED, "e:2", mode="explain", grade=6,
              lesson_id="6-02-001"),
        event(activity.QUICK_COMPLETED, "q:1", mode="quick", grade=6),
        event(activity.KONTROLNI_GENERATED, "k:1", mode="kontrolni", grade=6,
              area_name="Razlomci"),
    ])
    # Drugi „dan" - aktivnost nosi SAMO "sta i kada", bez ocjene.
    conn = libsql.connect(db)
    conn.execute(
        "INSERT INTO learning_activity (student_id, source, event_type, event_key, "
        "grade, mode, occurred_at) "
        "VALUES (?, 'matbot', ?, 'k:2', 6, 'kontrolni', '2026-07-14 09:00:00')",
        (student_id, activity.KONTROLNI_COMPLETED))
    conn.commit()
    conn.close()

    # AUTORITATIVAN REZULTAT zivi u tabelama procjene.
    from matbot.api import _kontrolni_attempt
    database.record_assessment_completed(
        student_id,
        _kontrolni_attempt("exam-monthly-1", grade=6, area_name="Razlomci",
                           total_count=5, correct_count=4, score_percent=80,
                           started_at="2026-07-14 09:00:00",
                           completed_at="2026-07-14 09:12:00"),
        [{"item_key": "q1", "ordinal": 1, "is_correct": True,
          "lesson_id": "6-04-001", "lesson_name": "Sabiranje razlomaka",
          "area_name": "Razlomci", "difficulty": "standard"},
         {"item_key": "q2", "ordinal": 2, "is_correct": True,
          "lesson_id": "6-04-002", "lesson_name": "Oduzimanje razlomaka",
          "area_name": "Razlomci", "difficulty": "standard"},
         {"item_key": "q3", "ordinal": 3, "is_correct": True,
          "lesson_id": "6-04-003", "lesson_name": "Mnozenje razlomaka",
          "area_name": "Razlomci", "difficulty": "harder"},
         {"item_key": "q4", "ordinal": 4, "is_correct": True,
          "lesson_id": "6-04-004", "lesson_name": "Dijeljenje razlomaka",
          "area_name": "Razlomci", "difficulty": "harder"},
         {"item_key": "q5", "ordinal": 5, "is_correct": False,
          "lesson_id": "6-04-005", "lesson_name": "Slozeni razlomci",
          "area_name": "Razlomci", "difficulty": "harder"}])
    database.close()
    return db, student_id


# ---------------------------------------------------------------------------
# Metrike koje SU izvedive danas
# ---------------------------------------------------------------------------
def test_active_days(seeded):
    path, student_id = seeded
    result = query(path,
                   "SELECT COUNT(DISTINCT date(occurred_at)) FROM learning_activity "
                   "WHERE student_id = ? AND source = 'matbot'", (student_id,))
    assert result[0][0] == 2


def test_practice_counts_and_accuracy(seeded):
    path, student_id = seeded
    counts = dict(query(path,
                        "SELECT event_type, COUNT(*) FROM learning_activity "
                        "WHERE student_id = ? GROUP BY event_type", (student_id,)))

    assert counts[activity.PRACTICE_TASK_PRESENTED] == 3
    assert counts[activity.PRACTICE_ANSWER_CORRECT] == 2
    assert counts[activity.PRACTICE_ANSWER_INCORRECT] == 1
    assert counts[activity.PRACTICE_HINT_USED] == 1
    assert counts[activity.PRACTICE_FULL_SOLUTION_SHOWN] == 1

    # Tačnost SAMO kad imenilac stvarno postoji — nikad dijeljenje s nulom i
    # nikad „0%" za učenika koji nije ni odgovarao.
    accuracy = query(path,
                     "SELECT CASE WHEN COUNT(*) = 0 THEN NULL ELSE "
                     "ROUND(100.0 * SUM(CASE WHEN event_type = ? THEN 1 ELSE 0 END) "
                     "/ COUNT(*), 1) END "
                     "FROM learning_activity WHERE student_id = ? "
                     "AND event_type IN (?, ?)",
                     (activity.PRACTICE_ANSWER_CORRECT, student_id,
                      activity.PRACTICE_ANSWER_CORRECT,
                      activity.PRACTICE_ANSWER_INCORRECT))
    assert accuracy[0][0] == pytest.approx(66.7, abs=0.1)


def test_accuracy_denominator_is_null_when_no_answers(db):
    """Učenik koji je samo čitao objašnjenja NEMA tačnost — ne 0 %."""
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(db, timeout=30.0,
                                               _check_same_thread=False))
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                                "citalac@example.com")
    database.record_learning_activity(student_id, [
        activity.ActivityEvent(activity.EXPLAIN_COMPLETED, "e:x", mode="explain")])
    database.close()

    accuracy = query(db,
                     "SELECT CASE WHEN COUNT(*) = 0 THEN NULL ELSE 1 END "
                     "FROM learning_activity WHERE student_id = ? "
                     "AND event_type IN (?, ?)",
                     (student_id, activity.PRACTICE_ANSWER_CORRECT,
                      activity.PRACTICE_ANSWER_INCORRECT))
    assert accuracy[0][0] is None


def test_explain_and_quick_usage(seeded):
    path, student_id = seeded
    counts = dict(query(path,
                        "SELECT event_type, COUNT(*) FROM learning_activity "
                        "WHERE student_id = ? GROUP BY event_type", (student_id,)))
    assert counts[activity.EXPLAIN_COMPLETED] == 2
    assert counts[activity.QUICK_COMPLETED] == 1


def test_lessons_and_areas_used(seeded):
    path, student_id = seeded
    lessons = query(path,
                    "SELECT lesson_id, COUNT(*) FROM learning_activity "
                    "WHERE student_id = ? AND lesson_id IS NOT NULL "
                    "GROUP BY lesson_id ORDER BY lesson_id", (student_id,))
    areas = query(path,
                  "SELECT area_name, COUNT(*) FROM learning_activity "
                  "WHERE student_id = ? AND area_name IS NOT NULL "
                  "GROUP BY area_name ORDER BY area_name", (student_id,))

    assert [row[0] for row in lessons] == ["6-01-005", "6-02-001", "7-03-002"]
    assert [row[0] for row in areas] == ["Geometrija", "Razlomci", "Skupovi"]


def test_activity_grouped_by_grade(seeded):
    path, student_id = seeded
    by_grade = query(path,
                     "SELECT grade, COUNT(*) FROM learning_activity "
                     "WHERE student_id = ? GROUP BY grade ORDER BY grade",
                     (student_id,))
    # 6. razred: 5 practice + 2 explain + 1 quick + 2 kontrolni = 10
    # 7. razred: zadatak + odgovor + rjesenje = 3
    assert by_grade == [(6, 10), (7, 3)]


def test_kontrolni_generations(seeded):
    path, student_id = seeded
    generated = query(path,
                      "SELECT COUNT(*) FROM learning_activity "
                      "WHERE student_id = ? AND event_type = ?",
                      (student_id, activity.KONTROLNI_GENERATED))
    assert generated[0][0] == 1


def test_kontrolni_results_come_from_assessment_attempts(seeded):
    """DIO 15: ocjena je vlasnistvo `assessment_attempts`, ne aktivnosti."""
    path, student_id = seeded
    row = query(path,
                "SELECT COUNT(*), AVG(score_percent), SUM(correct_count), "
                "SUM(total_count) FROM assessment_attempts "
                "WHERE student_id = ? AND source = 'matbot' "
                "AND assessment_type = 'kontrolni' AND completed_at IS NOT NULL",
                (student_id,))
    attempts, average, correct, total = row[0]

    assert attempts == 1
    assert average == 80.0
    assert (correct, total) == (4, 5)

    # I dokaz da ocjena vise NIJE u aktivnosti.
    dump = str(query(path, "SELECT metadata_json FROM learning_activity"))
    assert '"score"' not in dump and '"percentage"' not in dump


def test_incorrect_questions_group_by_lesson_area_and_difficulty(seeded):
    """Dimenzije postoje STVARNO: `lesson_id`, `area_name` i `difficulty` dolaze
    iz pohranjenog testa, po pitanju. Nista se ne izmislja."""
    path, student_id = seeded
    wrong = query(path, """
        SELECT i.lesson_id, i.lesson_name, i.area_name, i.difficulty
        FROM assessment_item_results i
        JOIN assessment_attempts a ON a.id = i.attempt_id
        WHERE a.student_id = ? AND i.is_correct = 0
        ORDER BY i.ordinal
    """, (student_id,))

    assert wrong == [("6-04-005", "Slozeni razlomci", "Razlomci", "harder")]

    by_difficulty = query(path, """
        SELECT i.difficulty,
               SUM(CASE WHEN i.is_correct = 0 THEN 1 ELSE 0 END) AS wrong,
               COUNT(*) AS asked
        FROM assessment_item_results i
        JOIN assessment_attempts a ON a.id = i.attempt_id
        WHERE a.student_id = ?
        GROUP BY i.difficulty ORDER BY i.difficulty
    """, (student_id,))
    assert by_difficulty == [("harder", 1, 3), ("standard", 0, 2)]


def test_per_item_outcomes_are_queryable(seeded):
    path, student_id = seeded
    outcomes = query(path,
                     "SELECT i.item_key, i.ordinal, i.is_correct, i.hints_used "
                     "FROM assessment_item_results i "
                     "JOIN assessment_attempts a ON a.id = i.attempt_id "
                     "WHERE a.student_id = ? ORDER BY i.ordinal", (student_id,))

    assert [o[0] for o in outcomes] == ["q1", "q2", "q3", "q4", "q5"]
    assert [o[2] for o in outcomes] == [1, 1, 1, 1, 0]
    assert {o[3] for o in outcomes} == {0}


def test_one_query_produces_the_whole_monthly_shape(seeded):
    """Cijeli skelet izvještaja iz JEDNOG upita — dokaz da rekonstrukcija
    razgovora nije potrebna ni za jednu traženu brojku."""
    path, student_id = seeded
    summary = query(path, """
        SELECT
          COUNT(DISTINCT date(occurred_at))                                  AS active_days,
          SUM(event_type = 'practice_task_presented')                        AS tasks,
          SUM(event_type = 'practice_answer_correct')                        AS correct,
          SUM(event_type = 'practice_answer_incorrect')                      AS incorrect,
          SUM(event_type = 'practice_hint_used')                             AS hints,
          SUM(event_type = 'practice_full_solution_shown')                   AS solutions,
          SUM(event_type = 'explain_completed')                              AS explains,
          SUM(event_type = 'quick_completed')                                AS quicks,
          SUM(event_type = 'kontrolni_generated')                            AS exams,
          SUM(event_type = 'kontrolni_completed')                            AS exams_done,
          COUNT(DISTINCT lesson_id)                                          AS lessons
        FROM learning_activity
        WHERE student_id = ? AND source = 'matbot'
    """, (student_id,))

    (active_days, tasks, correct, incorrect, hints, solutions,
     explains, quicks, exams, exams_done, lessons) = summary[0]
    assert (active_days, tasks, correct, incorrect) == (2, 3, 2, 1)
    assert (hints, solutions, explains, quicks) == (1, 1, 2, 1)
    assert (exams, exams_done, lessons) == (1, 1, 3)


def test_no_conversation_content_is_needed_or_present(seeded):
    """Ključna tvrdnja faze: izvještaj se izvodi BEZ ijednog traga razgovora."""
    path, _ = seeded
    dump = str(query(path, "SELECT * FROM learning_activity"))

    for forbidden in ("@", "Objasni", "Koliko je", "Zadatak:", "sk-", "Bearer"):
        assert forbidden not in dump
    assessment = str(query(path, "SELECT * FROM assessment_attempts")) +         str(query(path, "SELECT * FROM assessment_item_results"))
    for forbidden in ("@", "sk-", "Bearer", "Zadatak:"):
        assert forbidden not in assessment
