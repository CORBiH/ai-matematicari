"""Regresija za ŽIVI INCIDENT migracije v1 → v2 (produkcija, 2026-08-25).

ŠTA SE DESILO: `migrate_to_v2` je upisivala samo `version` u `schema_migrations`,
a produkcijska tabela ima i `description TEXT NOT NULL` bez default-a. libsql je
podigao `ValueError: NOT NULL constraint failed`, kod je to sveo na
`migration_failed:ValueError`, a `rollback()` NIJE uklonio već kreirane tabele —
pa je produkcija ostala u stanju „sve tri v2 tabele postoje, zapisa verzije 2
nema".

TRI ODVOJENE TVRDNJE OVOG FAJLA:
  1. UGOVOR — zapis migracije se upisuje sa SVIM obaveznim kolonama.
  2. NASTAVLJIVOST — svako djelimično stanje je dozvoljen ULAZ, uključujući
     tačno ono koje je incident napravio.
  3. PADA ZATVORENO — zapis verzije 2 ne smije postojati dok ne postoji cijela
     DOKAZANA šema v2; tuđa ili pokvarena tabela istog imena se ne blagosilja.

Sve je lokalno i sintetičko. Produkcijska baza se ovdje ne dodiruje.
"""
import pytest

from matbot import config, reporting_db, reporting_schema
from matbot.reporting_schema import MigrationError

from tests.test_assessment_capture import ASSESSMENT_SCHEMA
from tests.test_learning_activity_capture import ACTIVITY_SCHEMA
from tests.test_reporting_db_identity import SCHEMA as IDENTITY_SCHEMA

libsql = pytest.importorskip("libsql")

V1_DESCRIPTION = "Initial Matematicari reporting schema"


def build_v1(path):
    """Baza TAČNO kakva je produkcija bila prije migracije."""
    conn = libsql.connect(path)
    for block in (IDENTITY_SCHEMA, ACTIVITY_SCHEMA, ASSESSMENT_SCHEMA):
        for statement in block.strip().split(";"):
            if not statement.strip():
                continue
            if block is IDENTITY_SCHEMA and any(
                    statement.lstrip().startswith("CREATE TABLE " + name)
                    for name in ("learning_activity", "assessment_attempts",
                                 "assessment_item_results")):
                continue
            conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations (version, description) VALUES (1, ?)",
                 (V1_DESCRIPTION,))
    conn.commit()
    return conn


@pytest.fixture
def v1(tmp_path):
    path = str(tmp_path / "reporting.db")
    conn = build_v1(path)
    yield conn, path
    try:
        conn.close()
    except Exception:
        pass


def migrations(conn):
    return conn.execute("SELECT version, description, applied_at "
                        "FROM schema_migrations ORDER BY version").fetchall()


def tables(conn):
    return reporting_schema.table_names(conn)


def seed_phase12(conn):
    """Nekoliko Faza 1/2 redova — moraju preživjeti migraciju netaknuti."""
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('Ana', 6)")
    conn.execute("INSERT INTO student_accounts (student_id, provider, external_user_id) "
                 "VALUES (1, 'thinkific_email', 'ana@example.com')")
    conn.execute("INSERT INTO learning_activity (student_id, source, event_type, "
                 "event_key, occurred_at) VALUES "
                 "(1,'matbot','practice_task_presented','k1','2026-09-05 10:00:00')")
    conn.execute("INSERT INTO assessment_attempts (student_id, source, assessment_type, "
                 "external_attempt_id, score_percent, correct_count, total_count, "
                 "completed_at) VALUES "
                 "(1,'matbot','kontrolni','e1',80,4,5,'2026-09-06 11:00:00')")
    conn.commit()
    return (conn.execute("SELECT * FROM students").fetchall(),
            conn.execute("SELECT * FROM student_accounts").fetchall(),
            conn.execute("SELECT * FROM learning_activity").fetchall(),
            conn.execute("SELECT * FROM assessment_attempts").fetchall())


def phase12_state(conn):
    return (conn.execute("SELECT * FROM students").fetchall(),
            conn.execute("SELECT * FROM student_accounts").fetchall(),
            conn.execute("SELECT * FROM learning_activity").fetchall(),
            conn.execute("SELECT * FROM assessment_attempts").fetchall())


# ---------------------------------------------------------------------------
# 1) UGOVOR: sta je produkcija stvarno trazila
# ---------------------------------------------------------------------------
def test_production_schema_migrations_requires_a_description(v1):
    """Sam korijen incidenta, zakljucan kao test."""
    conn, _ = v1
    columns = {row[1]: row for row in
               conn.execute("PRAGMA table_info(schema_migrations)").fetchall()}

    assert set(columns) == {"version", "description", "applied_at"}
    assert columns["description"][3] == 1, "description mora biti NOT NULL"
    assert columns["description"][4] is None, "description nema default"
    # Upis samo `version` je TACNO ono sto je puklo u produkciji.
    with pytest.raises(Exception):
        conn.execute("INSERT INTO schema_migrations (version) VALUES (99)")
        conn.commit()
    conn.rollback()


def test_migration_record_has_version_description_and_applied_at(v1):
    conn, _ = v1
    assert reporting_schema.migrate_to_v2(conn) is True

    recorded = migrations(conn)
    assert [r[0] for r in recorded] == [1, 2]
    version2 = recorded[1]
    assert version2[1] and version2[1].strip(), "opis migracije je prazan"
    assert version2[1] == reporting_schema.MIGRATION_DESCRIPTIONS[2]
    assert version2[2], "applied_at nije popunjen bazinim default-om"


def test_expected_version_in_config_tracks_the_schema_module():
    """Da dvije vrijednosti ne mogu odlutati (uzrok laznog „-> OK")."""
    assert config.REPORTING_SCHEMA_VERSION == reporting_schema.CURRENT_SCHEMA_VERSION == 4


# ---------------------------------------------------------------------------
# 2) NASTAVLJIVOST: svako djelimicno stanje je dozvoljen ulaz
# ---------------------------------------------------------------------------
def test_clean_v1_migrates(v1):
    conn, _ = v1
    before = seed_phase12(conn)

    assert reporting_schema.migrate_to_v2(conn) is True

    assert set(reporting_schema.V2_TABLES) <= tables(conn)
    assert reporting_schema.verify_v2_schema(conn) == []
    assert phase12_state(conn) == before


def _partial(conn, count):
    """Simuliraj prekinutu migraciju: prvih `count` DDL naredbi je prosло."""
    for statement in reporting_schema.SCHEMA_V2_STATEMENTS[:count]:
        conn.execute(statement)
    conn.commit()


def test_only_imports_table_exists_recovers(v1):
    conn, _ = v1
    _partial(conn, 1)
    assert "thinkific_progress_imports" in tables(conn)

    assert reporting_schema.migrate_to_v2(conn) is True
    assert reporting_schema.verify_v2_schema(conn) == []
    assert [r[0] for r in migrations(conn)] == [1, 2]


def test_imports_and_snapshots_exist_recovers(v1):
    conn, _ = v1
    _partial(conn, 2)

    assert reporting_schema.migrate_to_v2(conn) is True
    assert reporting_schema.verify_v2_schema(conn) == []


def test_exact_production_partial_state_recovers(v1):
    """DIO 12 — NAJVAZNIJI TEST.

    Reprodukuje TACNO stanje koje je neuspjela produkcijska migracija ostavila:
    sve tri v2 tabele postoje i prazne su, indeksi postoje, ali zapisa verzije 2
    NEMA jer je upis pukao na `description`."""
    conn, _ = v1
    before = seed_phase12(conn)
    for statement in reporting_schema.SCHEMA_V2_STATEMENTS:
        conn.execute(statement)
    conn.commit()

    # --- stanje PRIJE, kakvo je izmjereno u produkciji ---
    assert set(reporting_schema.V2_TABLES) <= tables(conn)
    assert [r[0] for r in migrations(conn)] == [1]
    assert reporting_schema.current_version(conn) == 1
    for table in reporting_schema.V2_TABLES:
        assert conn.execute("SELECT COUNT(*) FROM %s" % table).fetchall()[0][0] == 0

    # --- oporavak ---
    assert reporting_schema.migrate_to_v2(conn) is True

    # --- stanje POSLIJE ---
    assert reporting_schema.current_version(conn) == 2
    assert [r[0] for r in migrations(conn)] == [1, 2]
    assert migrations(conn)[1][1] == reporting_schema.MIGRATION_DESCRIPTIONS[2]
    assert reporting_schema.verify_v2_schema(conn) == []
    assert set(reporting_schema.V2_TABLES) <= tables(conn), "tabela je izgubljena"
    for table in reporting_schema.V2_TABLES:
        assert conn.execute("SELECT COUNT(*) FROM %s" % table).fetchall()[0][0] == 0
    assert phase12_state(conn) == before, "Faza 1/2 podaci su promijenjeni"

    # --- ponovno pokretanje ne mijenja nista ---
    assert reporting_schema.migrate_to_v2(conn) is False
    assert [r[0] for r in migrations(conn)] == [1, 2]
    assert phase12_state(conn) == before


def test_missing_index_is_created_on_recovery(v1):
    conn, _ = v1
    for statement in reporting_schema.SCHEMA_V2_STATEMENTS:
        conn.execute(statement)
    conn.execute("DROP INDEX idx_progress_snapshots_student_month")
    conn.commit()
    assert any("index_missing" in p for p in reporting_schema.verify_v2_schema(conn))

    assert reporting_schema.migrate_to_v2(conn) is True
    assert reporting_schema.verify_v2_schema(conn) == []


def test_rerun_after_success_is_a_no_op(v1):
    conn, _ = v1
    assert reporting_schema.migrate_to_v2(conn) is True
    snapshot = migrations(conn)

    assert reporting_schema.migrate_to_v2(conn) is False
    assert reporting_schema.migrate_to_v2(conn) is False
    assert migrations(conn) == snapshot


# ---------------------------------------------------------------------------
# 3) PADA ZATVORENO
# ---------------------------------------------------------------------------
def _replace_table(conn, name, ddl):
    conn.execute("DROP TABLE IF EXISTS %s" % name)
    conn.execute(ddl)
    conn.commit()


def test_incompatible_imports_table_fails_closed(v1):
    conn, _ = v1
    _replace_table(conn, "thinkific_progress_imports",
                   "CREATE TABLE thinkific_progress_imports (id INTEGER PRIMARY KEY)")

    with pytest.raises(MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    assert "thinkific_progress_imports" in error.value.code
    assert 2 not in reporting_schema.applied_versions(conn)


def test_incompatible_snapshots_table_fails_closed(v1):
    conn, _ = v1
    _replace_table(conn, "thinkific_progress_snapshots",
                   "CREATE TABLE thinkific_progress_snapshots (id INTEGER PRIMARY KEY)")

    with pytest.raises(MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    assert "thinkific_progress_snapshots" in error.value.code
    assert 2 not in reporting_schema.applied_versions(conn)


def test_incompatible_sections_table_fails_closed(v1):
    conn, _ = v1
    _replace_table(conn, "thinkific_progress_sections",
                   "CREATE TABLE thinkific_progress_sections "
                   "(id INTEGER PRIMARY KEY, snapshot_id INTEGER)")

    with pytest.raises(MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    assert "thinkific_progress_sections" in error.value.code
    assert 2 not in reporting_schema.applied_versions(conn)


def test_missing_unique_constraint_fails_closed(v1):
    """`CREATE TABLE IF NOT EXISTS` tiho NE URADI NISTA kad tabela postoji —
    zato tabela s ispravnim kolonama ali BEZ jedinstvenog kljuca mora pasti."""
    conn, _ = v1
    _replace_table(conn, "thinkific_progress_snapshots", """
        CREATE TABLE thinkific_progress_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, import_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL, report_month TEXT NOT NULL,
            course_key TEXT NOT NULL, course_name TEXT NOT NULL, grade INTEGER NOT NULL,
            percent_viewed REAL, percent_completed REAL, started_at TEXT,
            completed_at TEXT, activated_at TEXT, expires_at TEXT, last_sign_in TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (import_id) REFERENCES thinkific_progress_imports(id),
            FOREIGN KEY (student_id) REFERENCES students(id))""")

    with pytest.raises(MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    assert error.value.code.startswith("v2_unique_missing")
    assert 2 not in reporting_schema.applied_versions(conn)


def test_missing_foreign_key_fails_closed(v1):
    conn, _ = v1
    _replace_table(conn, "thinkific_progress_sections", """
        CREATE TABLE thinkific_progress_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id INTEGER NOT NULL,
            ordinal INTEGER, section_name TEXT NOT NULL, progress_percent REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (snapshot_id, section_name))""")

    with pytest.raises(MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    assert error.value.code.startswith("v2_foreign_key_missing")
    assert 2 not in reporting_schema.applied_versions(conn)


def test_recorded_v2_with_incomplete_schema_fails_closed(v1):
    """Baza koja TVRDI v2 a nema tabelu ne smije se prikazati kao zdrava."""
    conn, _ = v1
    conn.execute("INSERT INTO schema_migrations (version, description) VALUES (2, ?)",
                 ("Add Thinkific progress reporting schema",))
    conn.commit()

    with pytest.raises(MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    # Nedostaju i tabele i indeksi; bitno je da je pad zatvoren i strukturan.
    assert error.value.code.startswith("v2_")
    problems = reporting_schema.verify_v2_schema(conn)
    assert any(p.startswith("v2_table_missing") for p in problems)


def test_complete_v2_with_record_returns_false(v1):
    conn, _ = v1
    for statement in reporting_schema.SCHEMA_V2_STATEMENTS:
        conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations (version, description) VALUES (2, ?)",
                 ("Add Thinkific progress reporting schema",))
    conn.commit()

    assert reporting_schema.migrate_to_v2(conn) is False


def test_migration_refuses_a_database_that_is_not_v1(tmp_path):
    path = str(tmp_path / "foreign.db")
    conn = libsql.connect(path)
    conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                 "description TEXT NOT NULL, applied_at TEXT)")
    conn.commit()

    with pytest.raises(MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    conn.close()
    assert error.value.code == "v1_schema_incomplete"


def test_failure_never_records_version_two(v1):
    """Najvazniji invarijant cijele ispravke."""
    conn, _ = v1
    _replace_table(conn, "thinkific_progress_imports",
                   "CREATE TABLE thinkific_progress_imports (id INTEGER PRIMARY KEY)")

    for _ in range(3):
        with pytest.raises(MigrationError):
            reporting_schema.migrate_to_v2(conn)
    assert 2 not in reporting_schema.applied_versions(conn)
    assert reporting_schema.current_version(conn) == 1


# ---------------------------------------------------------------------------
# 4) Dijagnostika i dokaz
# ---------------------------------------------------------------------------
def test_diagnostics_carry_structural_codes_not_raw_db_text(v1):
    conn, _ = v1
    _replace_table(conn, "thinkific_progress_snapshots",
                   "CREATE TABLE thinkific_progress_snapshots (id INTEGER PRIMARY KEY)")

    with pytest.raises(MigrationError) as error:
        reporting_schema.migrate_to_v2(conn)
    text = str(error.value)

    assert error.value.code.startswith("v2_schema_incompatible")
    # Bez PII-ja, bez kredencijala, bez sirovog teksta izuzetka baze.
    for forbidden in ("@", "libsql://", "sk-", "Traceback", "NOT NULL constraint",
                      "auth_token"):
        assert forbidden not in text


def test_checker_proves_v2_after_migration(v1, monkeypatch):
    """DIO 10: JEDNA komanda mora dokazati TEKUĆU šemu operateru (sada v4)."""
    conn, path = v1
    reporting_schema.migrate_to_v2(conn)
    reporting_schema.migrate_to_v3(conn)
    reporting_schema.migrate_to_v4(conn)
    conn.close()

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0))
    try:
        report = database.check()
        rendered = reporting_db._format_report(report)
    finally:
        database.close()

    assert report["schema_version"] == 4
    assert report["schema_version_matches"] is True
    assert report["missing_tables"] == []
    assert report["v3_schema_verified"] is True
    assert "v3_schema: verified" in rendered
    assert report["v2_schema_verified"] is True
    assert report["v4_schema_verified"] is True
    assert "v4_schema: verified" in rendered
    assert "schema_version: 4 (expected 4) -> OK" in rendered
    assert "v2_schema: verified" in rendered
    for table in reporting_schema.V2_TABLES:
        assert table in report["columns"]


def test_checker_reports_v2_incomplete_before_migration(v1, monkeypatch):
    conn, path = v1
    conn.close()
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0))
    try:
        report = database.check()
        rendered = reporting_db._format_report(report)
    finally:
        database.close()

    assert report["v2_schema_verified"] is False
    assert "v2_schema: INCOMPLETE" in rendered
    assert "schema_version: 1 (expected 4) -> MISMATCH" in rendered


def test_ddl_survives_rollback_which_is_why_migration_is_resumable(tmp_path):
    """DOKAZ ZA DIO 3: oslanjanje na povlacenje DDL-a je pogresno.

    Ovo je izmjereno ponasanje libsql 0.1.11, ne pretpostavka — i upravo zbog
    njega je produkcija ostala s tri tabele bez zapisa verzije."""
    path = str(tmp_path / "ddl.db")
    conn = libsql.connect(path)
    conn.execute("CREATE TABLE base (id INTEGER PRIMARY KEY)")
    conn.commit()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS leftover (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO base (id, missing_column) VALUES (1, 2)")
        conn.commit()
    except Exception:
        conn.rollback()

    assert "leftover" in reporting_schema.table_names(conn), \
        "ako DDL vise NE prezivi rollback, migracija se smije pojednostaviti"
    conn.close()
