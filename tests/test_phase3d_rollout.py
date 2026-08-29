"""Faza 3D — otvrdnjavanje pred izdanje: identitet, kurikulum, rollout.

TRI TVRDNJE:

  1. Sudar Thinkific naloga NE SMIJE ostaviti duplikat učenika. Upis učenika i
     upis naloga su JEDNA logička operacija.
  2. Gradivo časa je KANONSKO (`data/topics.json`), ne slobodan tekst — mjesečno
     grupisanje po oblastima ne smije rasti iz tipfelera.
  3. Produkcija ide s v2 na v3 PRIJE nego što je aplikacija zamijenjena, a pad
     migracije ostavlja staru aplikaciju da poslužuje.

PII: svi učenici su sintetički.
"""
import io
import re
from pathlib import Path

import pytest

from matbot import (report_input, reporting_db, reporting_schema, student_sessions,
                    topics)
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_thinkific_progress_import import build_v1, migrate, migrate_v2_only

libsql = pytest.importorskip("libsql")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-vps.yml"

# Kanonske vrijednosti iz stvarnog kurikuluma — ne izmišljene.
G6_AREA = "Djeljivost brojeva"
G6_LESSON = "Djeljivost zbira, razlike i proizvoda"
G7_AREA = "Cijeli brojevi"
G7_LESSON = "Skup cijelih brojeva Z"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    migrate(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield database
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


def _student_count(database):
    conn = database._connection()
    return conn.execute("SELECT COUNT(*) FROM students").fetchall()[0][0]


def _account_count(database):
    conn = database._connection()
    return conn.execute("SELECT COUNT(*) FROM student_accounts").fetchall()[0][0]


# ===========================================================================
# 1) ATOMIČNO KREIRANJE IDENTITETA
# ===========================================================================
def test_student_without_email_creates_no_account(db):
    student_id = db.create_student("Bez Naloga", 6)
    assert _student_count(db) == 1
    assert _account_count(db) == 0
    assert db.student_has_thinkific(student_id) is False


def test_student_with_fresh_email_creates_both(db):
    db.create_student("S Nalogom", 6, external_user_id="novi@example.com")
    assert _student_count(db) == 1
    assert _account_count(db) == 1


def test_conflicting_email_creates_no_student_row(db):
    """ŽIVI DEFEKT: ranije je učenik ostajao iako nalog nije povezan."""
    owner = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL,
                                     "zauzet@example.com", grade=6)
    before_students, before_accounts = _student_count(db), _account_count(db)

    with pytest.raises(reporting_db.ReportingUnavailable) as caught:
        db.create_student("Duplikat", 6, external_user_id="zauzet@example.com")

    assert caught.value.code == "student_account_taken:%d" % owner
    # NIŠTA nije upisano — ni učenik, ni nalog.
    assert _student_count(db) == before_students
    assert _account_count(db) == before_accounts


def test_no_orphan_student_remains_after_a_conflict(db):
    db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, "zauzet@example.com",
                             grade=6)
    for _ in range(3):
        with pytest.raises(reporting_db.ReportingUnavailable):
            db.create_student("Duplikat", 6, external_user_id="zauzet@example.com")
    # Tri pokušaja, nijedan siroče.
    assert _student_count(db) == 1
    assert [s["display_name"] for s in db.list_students()] != ["Duplikat"]


def test_account_is_never_reassigned(db):
    owner = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, "zauzet@example.com",
                                     grade=6)
    other = db.create_student("Drugi", 6)
    with pytest.raises(reporting_db.ReportingUnavailable):
        db.link_thinkific_account(other, "zauzet@example.com")
    conn = db._connection()
    rows = conn.execute(
        "SELECT student_id FROM student_accounts WHERE external_user_id = ?",
        ("zauzet@example.com",)).fetchall()
    assert [r[0] for r in rows] == [owner], "nalog je promijenio vlasnika"


def test_normalized_variants_follow_the_existing_contract(db):
    """NORMALIZACIJA JE UGOVOR POZIVAOCA, kao i u Fazi 1.

    `_clean_external_id` samo skraćuje i trimuje; malo slovo pravi
    `student_identity.normalize_email`, koji zovu i `resolve_student` i
    administratorska ruta. Ovdje se dokazuje da kroz taj put dvije varijante
    ISTE adrese daju jedan identitet."""
    from matbot import student_identity

    first = student_identity.normalize_email("Ucenik@Example.COM")
    second = student_identity.normalize_email("  ucenik@example.com  ")
    assert first == second

    db.create_student("Prvi", 6, external_user_id=first)
    with pytest.raises(reporting_db.ReportingUnavailable):
        db.create_student("Drugi", 6, external_user_id=second)
    assert _student_count(db) == 1


def test_concurrent_duplicate_attempts_never_create_two_students(db):
    """BAZA JE ARBITAR. `UNIQUE(provider, external_user_id)` odlučuje, a
    gubitnik utrke povlači i svoj spekulativni red u `students`."""
    import threading

    errors, created = [], []
    barrier = threading.Barrier(4)

    def attempt(index):
        barrier.wait()
        try:
            created.append(db.create_student("Trkač %d" % index, 6,
                                             external_user_id="utrka@example.com"))
        except reporting_db.ReportingUnavailable as error:
            errors.append(error.code)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(created) == 1, "dva identiteta za isti nalog"
    assert len(errors) == 3
    assert all(code.startswith("student_account_taken") for code in errors), errors
    assert _student_count(db) == 1
    assert _account_count(db) == 1


def test_existing_thinkific_student_remains_reusable(db):
    first = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, "stalni@example.com",
                                     grade=6)
    again = db.get_or_create_student(PROVIDER_THINKIFIC_EMAIL, "stalni@example.com",
                                     grade=6)
    assert first == again
    assert _student_count(db) == 1


def test_registry_still_hides_the_email(db):
    import json

    db.create_student("Tajni", 6, external_user_id="tajna@example.com")
    assert "@" not in json.dumps(db.list_students(), ensure_ascii=False)


# ===========================================================================
# 2) KANONSKI KURIKULUM
# ===========================================================================
def test_no_second_curriculum_source_was_introduced():
    """Kurikulum ima TAČNO jedan izvor — `data/topics.json` kroz `topics.py`."""
    source = (ROOT / "matbot" / "student_sessions.py").read_text(encoding="utf-8")
    admin = (ROOT / "matbot" / "admin_students.py").read_text(encoding="utf-8")
    for blob in (source, admin):
        # Nijedan od ova dva modula NE OTVARA kurikulum sam — ide kroz `topics`.
        assert "json.load" not in blob, "drugi put do kurikuluma"
        assert 'open("data' not in blob and "open('data" not in blob
    assert "from matbot import topics" in source
    # Jedini modul koji stvarno čita fajl je `topics.py`.
    curriculum_readers = [
        path.name for path in (ROOT / "matbot").glob("*.py")
        if "data" in path.read_text(encoding="utf-8")
        and "topics.json" in path.read_text(encoding="utf-8")
        and "DATA_PATH" in path.read_text(encoding="utf-8")]
    assert curriculum_readers == ["topics.py"], curriculum_readers


@pytest.mark.parametrize("grade, area, lesson", [
    (6, G6_AREA, G6_LESSON),
    (7, G7_AREA, G7_LESSON),
])
def test_each_grade_exposes_its_own_curriculum(grade, area, lesson):
    areas = topics.curriculum_areas(grade)
    assert area in areas
    assert lesson in topics.curriculum_lessons(grade, area)
    assert topics.curriculum_pair_valid(grade, area, lesson) is True


@pytest.mark.parametrize("grade", [6, 7, 8, 9])
def test_every_supported_grade_has_curriculum(grade):
    areas = topics.curriculum_areas(grade)
    assert areas, "razred bez oblasti"
    assert all(topics.curriculum_lessons(grade, area) for area in areas)


def test_area_exposes_only_its_own_lessons():
    lessons = topics.curriculum_lessons(6, G6_AREA)
    assert G6_LESSON in lessons
    other = topics.curriculum_lessons(6, "Razlomci")
    assert set(lessons).isdisjoint(other), "lekcija pripada dvjema oblastima"


def _session(grade, area, lesson):
    return student_sessions.validate_session(
        session_date="2026-08-05", attendance="present", activity_rating=4,
        homework_status="done", area_name=area, lesson_name=lesson, grade=grade)


def test_canonical_pair_is_accepted():
    record = _session(6, G6_AREA, G6_LESSON)
    assert record["area_name"] == G6_AREA
    assert record["lesson_name"] == G6_LESSON


def test_lesson_from_another_area_is_rejected():
    with pytest.raises(student_sessions.SessionValidationError) as caught:
        _session(6, "Razlomci", G6_LESSON)
    assert caught.value.code == "session_curriculum_unknown"


def test_lesson_from_another_grade_is_rejected():
    with pytest.raises(student_sessions.SessionValidationError):
        _session(6, G7_AREA, G7_LESSON)


def test_invented_area_and_lesson_are_rejected():
    with pytest.raises(student_sessions.SessionValidationError):
        _session(6, "Izmišljena oblast", G6_LESSON)
    with pytest.raises(student_sessions.SessionValidationError):
        _session(6, G6_AREA, "Izmišljena lekcija")


def test_half_a_pair_is_rejected():
    with pytest.raises(student_sessions.SessionValidationError) as caught:
        _session(6, G6_AREA, None)
    assert caught.value.code == "session_curriculum_incomplete"


def test_empty_curriculum_stays_allowed():
    """Čas bez upisanog gradiva je legitiman (npr. samo izostanak)."""
    record = _session(6, None, None)
    assert record["area_name"] is None and record["lesson_name"] is None


def test_arbitrary_form_values_cannot_be_persisted():
    for area, lesson in (("<script>x</script>", G6_LESSON),
                         (G6_AREA, "<b>x</b>"),
                         ("' OR 1=1 --", "x")):
        with pytest.raises(student_sessions.SessionValidationError):
            _session(6, area, lesson)


def test_legacy_value_is_not_rewritten_merely_by_reading():
    """Bez `grade` se zatečena vrijednost NE dira — defanzivno za stare redove."""
    record = student_sessions.validate_session(
        session_date="2026-08-05", attendance="present", activity_rating=4,
        homework_status="done", area_name="Stara oblast",
        lesson_name="Stara lekcija")
    assert record["area_name"] == "Stara oblast"


def test_report_grouping_sees_canonical_labels(db):
    student_id = db.create_student("Kanonski", 6)
    db.insert_session(student_id, _session(6, G6_AREA, G6_LESSON))
    summary = report_input.build_instruction_section(student_id, "2026-08",
                                                     database=db)
    assert summary["areas_worked"] == [G6_AREA]
    assert summary["lessons_worked"] == [G6_LESSON]


# ===========================================================================
# 3) SIGURAN ROLLOUT V2 → V3
# ===========================================================================
def _v2_database(tmp_path, monkeypatch, name="v2.db"):
    path = str(tmp_path / name)
    build_v1(path)
    migrate_v2_only(path)
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    return path, database


def test_migrate_command_moves_a_clean_v2_to_v3(tmp_path, monkeypatch):
    path, database = _v2_database(tmp_path, monkeypatch)
    try:
        assert database.migrate() == [reporting_schema.SCHEMA_VERSION_V3]
        report = database.check()
        assert report["schema_version"] == 3
        assert report["v3_schema_verified"] is True
    finally:
        database.close()


def test_migrate_command_is_idempotent(tmp_path, monkeypatch):
    path, database = _v2_database(tmp_path, monkeypatch)
    try:
        database.migrate()
        assert database.migrate() == [], "druga migracija je nešto ponovo radila"
    finally:
        database.close()


def test_migrate_command_exits_nonzero_on_malformed_partial_schema(
        tmp_path, monkeypatch, capsys):
    path, database = _v2_database(tmp_path, monkeypatch)
    conn = libsql.connect(path)
    conn.execute("CREATE TABLE student_sessions (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    reporting_db.set_database(database)
    try:
        assert reporting_db.main(["--migrate"]) == 1
    finally:
        reporting_db.set_database(None)
        database.close()
    out = capsys.readouterr().out
    assert "migration: FAILED" in out
    assert "v3_" in out
    # Nikad tajna, nikad URL.
    for forbidden in ("libsql://", "TURSO", "token"):
        assert forbidden not in out


def test_migrate_command_prints_no_secret(tmp_path, monkeypatch, capsys):
    path, database = _v2_database(tmp_path, monkeypatch)
    reporting_db.set_database(database)
    try:
        reporting_db.main(["--migrate"])
    finally:
        reporting_db.set_database(None)
        database.close()
    out = capsys.readouterr().out
    assert "migration: applied v3" in out
    for forbidden in ("libsql://", "test-token-not-real", "TURSO_AUTH_TOKEN"):
        assert forbidden not in out


def test_v3_is_additive_for_the_currently_running_app(tmp_path, monkeypatch):
    """DOKAZ KOMPATIBILNOSTI: stara aplikacija smije nastaviti da radi.

    Poslije migracije, sve što stara verzija koristi mora ostati netaknuto —
    tabele i kolone v1/v2, ugovor v2 i zapis verzije 2. Jedina razlika je
    DODATA tabela koju stara verzija ne čita i zapis verzije 3 koji joj mijenja
    samo dijagnostiku, ne rad."""
    path, database = _v2_database(tmp_path, monkeypatch)
    conn = libsql.connect(path)
    before = {}
    for table in reporting_schema.V1_TABLES + reporting_schema.V2_TABLES:
        before[table] = [row[1] for row
                         in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
    conn.close()

    try:
        database.migrate()
    finally:
        database.close()

    conn = libsql.connect(path)
    try:
        for table, columns in before.items():
            after = [row[1] for row
                     in conn.execute("PRAGMA table_info(%s)" % table).fetchall()]
            assert after == columns, "v3 je promijenila %s" % table
        # Ugovor verzije 2 i dalje vrijedi.
        assert reporting_schema.verify_v2_schema(conn) == []
        # Zapis v2 nije izgubljen; v3 je DODAT.
        assert reporting_schema.applied_versions(conn) >= {1, 2, 3}
        # Jedina nova tabela.
        tables = reporting_schema.table_names(conn)
        assert "student_sessions" in tables
    finally:
        conn.close()


def test_old_app_startup_never_consults_the_reporting_schema_version():
    """Stara aplikacija ne pada zbog zapisa verzije 3 jer ga i ne gleda.

    Start zove `release_config.require_release_configuration`, ne izvještajnu
    dijagnostiku — pa migracija ne može oboriti proces koji već poslužuje."""
    startup = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "reporting_db" not in startup or "check()" not in startup
    assert "require_release_configuration" in startup


# ---------------------------------------------------------------------------
# Deploy workflow — redoslijed je dio ugovora, pa se i provjerava
# ---------------------------------------------------------------------------
def _workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_migrates_before_replacing_the_app():
    text = _workflow()
    migrate_at = text.index("python -m matbot.reporting_db --migrate")
    replace_at = text.index("docker compose up -d")
    build_at = text.index("docker compose build")
    assert build_at < migrate_at < replace_at, (
        "migracija mora biti POSLIJE builda i PRIJE zamjene aplikacije")


def test_workflow_migration_uses_the_new_image_without_serving_traffic():
    """`run --rm --no-deps` je kratkotrajan kontejner — živa usluga se ne dira."""
    text = _workflow()
    index = text.index("python -m matbot.reporting_db --migrate")
    preceding = text[:index]
    command = preceding[preceding.rindex("docker compose"):]
    assert "run --rm --no-deps -T matbot" in command


def test_workflow_migration_redirects_stdin():
    """Bez `< /dev/null` docker pojede ostatak skripte (izmjereni kvar)."""
    text = _workflow()
    line = [l for l in text.splitlines()
            if "matbot.reporting_db --migrate" in l][0]
    assert "< /dev/null" in line


def test_workflow_fails_closed_before_replacement():
    """`set -e` znači: pad migracije obara deploy prije `up -d`."""
    text = _workflow()
    assert "set -e" in text
    assert text.index("set -e") < text.index("matbot.reporting_db --migrate")


def test_workflow_keeps_health_and_version_verification_after_replacement():
    text = _workflow()
    replace_at = text.index("docker compose up -d")
    assert text.index("/healthz", replace_at) > replace_at
    assert text.index("RUNNING_VERSION", replace_at) > replace_at


def test_workflow_contains_no_ad_hoc_migration_sql():
    """Migracijsko znanje ostaje u Pythonu, ne u GitHub Actionsu."""
    text = _workflow()
    for forbidden in ("CREATE TABLE", "ALTER TABLE", "INSERT INTO schema_migrations"):
        assert forbidden not in text


def test_workflow_prints_no_secret():
    text = _workflow()
    assert "TURSO_AUTH_TOKEN" not in text
    assert "OPENAI_API_KEY" not in text


def test_ordinary_import_never_migrates():
    """Uvoz modula ne smije dirati šemu — migracija je izričit CLI korak."""
    source = (ROOT / "matbot" / "reporting_db.py").read_text(encoding="utf-8")
    module_level = [line for line in source.splitlines()
                    if line and not line[0].isspace()
                    and re.search(r"migrate_to_v[23]\s*\(", line)]
    assert module_level == []
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "migrate" not in app_source
