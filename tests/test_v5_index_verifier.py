"""Provjera v5 indeksa mora gledati STRUKTURU, ne oblik ispisa.

ŽIVI PRODUKCIJSKI KVAR (izdanje 9225dd9): raspoređivanje je palo iako je indeks
bio POTPUNO ISPRAVAN. Turso je isti objekat kanonizovao kao

    COALESCE (area_name, '')

a lokalno generisani oblik glasi `COALESCE(area_name, '')`. Provjera je sažimala
uzastopne praznine, ali razmak IZMEĐU imena funkcije i otvorene zagrade time ne
nestaje — pa je `v5_index_shape` prijavljen nad strukturno tačnim indeksom.

LAŽNO NEGATIVNO, i skupo: migracija se nije upisala, `set -e` je oborio deploy
prije `up -d`, i produkcija je ostala na prethodnom izdanju. Baza je pritom već
imala i kolone i indeks — dakle sve osim zapisa verzije 5.

ISPRAVKA JE UŽA NEGO ŠTO IZGLEDA: uklanja se PRAZNINA priljepljena uz `(`, `)` i
`,`. Nijedan identifikator, literal ni znak interpunkcije se ne gubi, pa svaka
strukturna garancija ostaje — što ovaj fajl i mjeri, s jedanaest negativnih
slučajeva uz dva pozitivna.
"""
import pytest

from matbot import reporting_schema

from tests.test_thinkific_progress_import import build_v1, migrate_v4_only

libsql = pytest.importorskip("libsql")

INDEX = reporting_schema.V5_CLASS_INDEX

# TAČAN ispis izmjeren u produkciji (`sqlite_master.sql`), s prelomima redova i
# razmakom prije zagrade — prepisan, ne rekonstruisan.
PRODUCTION_SQL = (
    "CREATE UNIQUE INDEX idx_student_sessions_logical_class\n"
    "ON student_sessions\n"
    "(student_id, session_date, session_time,\n"
    " COALESCE (area_name, ''),\n"
    " COALESCE (lesson_name, ''))\n"
    "WHERE session_time IS NOT NULL"
)

# Oblik koji generiše sam repozitorij.
LOCAL_SQL = (
    "CREATE UNIQUE INDEX idx_student_sessions_logical_class "
    "ON student_sessions (student_id, session_date, session_time, "
    "COALESCE(area_name, ''), COALESCE(lesson_name, '')) "
    "WHERE session_time IS NOT NULL"
)


def v4_database(tmp_path, name="v4.db"):
    """Baza na v4 s kolonama v5 — oblik zatečen u produkciji."""
    path = str(tmp_path / name)
    build_v1(path)
    migrate_v4_only(path)
    conn = libsql.connect(path)
    conn.execute("ALTER TABLE student_sessions ADD COLUMN session_time TEXT")
    conn.execute("ALTER TABLE student_sessions ADD COLUMN topic_source TEXT")
    conn.commit()
    return conn


def with_index(conn, sql):
    conn.execute(sql)
    conn.commit()
    return conn


# ===========================================================================
# 3-4) OBA OBLIKA PROLAZE
# ===========================================================================
def test_3_the_exact_production_sql_passes(tmp_path):
    """Najvažniji test: ovo je STANJE PRODUKCIJE U OVOM TRENUTKU."""
    conn = with_index(v4_database(tmp_path), PRODUCTION_SQL)
    assert reporting_schema.verify_v5_schema(conn) == []
    conn.close()


def test_4_the_locally_generated_sql_still_passes(tmp_path):
    conn = with_index(v4_database(tmp_path), LOCAL_SQL)
    assert reporting_schema.verify_v5_schema(conn) == []
    conn.close()


def test_4b_both_representations_canonicalize_identically():
    canonical = reporting_schema.canonical_index_sql
    assert canonical(PRODUCTION_SQL) == canonical(LOCAL_SQL)
    canon = canonical(PRODUCTION_SQL)
    key, _, predicate = canon.partition("where")
    for fragment in reporting_schema._V5_INDEX_KEY_FRAGMENTS:
        assert fragment in key, fragment
    assert reporting_schema._V5_INDEX_PREDICATE in predicate


def test_4c_normalization_removes_only_whitespace():
    """Ništa osim praznine: svi znakovi ostaju, samo bez razmaka uz zagrade."""
    canonical = reporting_schema.canonical_index_sql(PRODUCTION_SQL)
    stripped = "".join(PRODUCTION_SQL.lower().split())
    assert canonical.replace(" ", "") == stripped


# ===========================================================================
# 5) NEGATIVNI SLUČAJEVI — provjera i dalje pada ZATVORENO
# ===========================================================================
NEGATIVE_CASES = {
    "A non-unique index":
        LOCAL_SQL.replace("CREATE UNIQUE INDEX", "CREATE INDEX"),
    "B missing session_time":
        LOCAL_SQL.replace("session_date, session_time,", "session_date,"),
    "C missing partial predicate":
        LOCAL_SQL.replace(" WHERE session_time IS NOT NULL", ""),
    "D wrong partial predicate":
        LOCAL_SQL.replace("IS NOT NULL", "IS NULL"),
    "E missing COALESCE(area_name)":
        LOCAL_SQL.replace("COALESCE(area_name, ''), ", ""),
    "F missing COALESCE(lesson_name)":
        LOCAL_SQL.replace(", COALESCE(lesson_name, '')", ""),
    "G plain area_name":
        LOCAL_SQL.replace("COALESCE(area_name, '')", "area_name"),
    "H plain lesson_name":
        LOCAL_SQL.replace("COALESCE(lesson_name, '')", "lesson_name"),
    "I non-empty default":
        LOCAL_SQL.replace("COALESCE(area_name, '')", "COALESCE(area_name, 'x')"),
    "J COALESCE on the wrong column":
        LOCAL_SQL.replace("COALESCE(area_name, '')", "COALESCE(comment, '')"),
    "K right name, incompatible shape":
        ("CREATE UNIQUE INDEX idx_student_sessions_logical_class "
         "ON student_sessions (student_id, session_date)"),
}


@pytest.mark.parametrize("label", sorted(NEGATIVE_CASES))
def test_5_malformed_indexes_are_still_rejected(tmp_path, label):
    sql = NEGATIVE_CASES[label]
    conn = with_index(v4_database(tmp_path, name=label[0] + ".db"), sql)
    problems = reporting_schema.verify_v5_schema(conn)
    assert problems, "prihvaćen pokvaren indeks: " + label
    assert any(p.startswith("v5_index") for p in problems), problems
    conn.close()


@pytest.mark.parametrize("label", sorted(NEGATIVE_CASES))
def test_5b_a_malformed_index_also_blocks_the_migration(tmp_path, label):
    """Pokvaren indeks ne smije samo „prijaviti" — mora zaustaviti migraciju."""
    conn = with_index(v4_database(tmp_path, name="m" + label[0] + ".db"),
                      NEGATIVE_CASES[label])
    with pytest.raises(reporting_schema.MigrationError):
        reporting_schema.migrate_to_v5(conn)
    assert 5 not in reporting_schema.applied_versions(conn)
    conn.close()


def test_5c_a_missing_index_is_still_reported(tmp_path):
    conn = v4_database(tmp_path, name="noindex.db")
    problems = reporting_schema.verify_v5_schema(conn)
    assert any(p.startswith("v5_index_missing") for p in problems), problems
    conn.close()


# ===========================================================================
# 6) NASTAVAK IZ TAČNOG PRODUKCIJSKOG STANJA
# ===========================================================================
def test_6_the_exact_production_partial_state_resumes(tmp_path):
    """schema_migrations = 1,2,3,4 · kolone postoje · Turso indeks postoji."""
    conn = with_index(v4_database(tmp_path, name="prod.db"), PRODUCTION_SQL)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('A', 7)")
    conn.execute(
        "INSERT INTO student_sessions (student_id, session_date, attendance, "
        " activity_rating, homework_status, area_name, lesson_name, comment) "
        "VALUES (1, '2026-05-01', 'present', 4, 'done', 'O', 'L', 'stari')")
    conn.commit()

    assert sorted(reporting_schema.applied_versions(conn)) == [1, 2, 3, 4]
    columns = {r[1] for r in conn.execute(
        "PRAGMA table_info(student_sessions)").fetchall()}
    assert {"session_time", "topic_source"} <= columns

    cols = ("id, student_id, session_date, attendance, activity_rating, "
            "homework_status, area_name, lesson_name, comment, session_time, "
            "topic_source")
    before = [tuple(r) for r in conn.execute(
        "SELECT %s FROM student_sessions ORDER BY id" % cols).fetchall()]
    index_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (INDEX,)).fetchall()[0][0]

    # Postojeći ispravan indeks se PRIHVATA kao ulaz u djelimično stanje.
    assert reporting_schema.verify_existing_v5_objects(conn) == []

    assert reporting_schema.migrate_to_v5(conn) is True
    assert reporting_schema.verify_v5_schema(conn) == []
    assert 5 in reporting_schema.applied_versions(conn)
    assert reporting_schema.current_version(conn) == 5

    after = [tuple(r) for r in conn.execute(
        "SELECT %s FROM student_sessions ORDER BY id" % cols).fetchall()]
    assert after == before, "postojeći red je promijenjen"
    index_after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (INDEX,)).fetchall()[0][0]
    assert index_after == index_before, "indeks je prepravljen"
    conn.close()


def test_6b_no_destructive_ddl_exists_in_the_v5_path():
    """`IF NOT EXISTS` + `ADD COLUMN`: nema `DROP`, `UPDATE` ni `DELETE`."""
    blob = " ".join(reporting_schema.SCHEMA_V5_STATEMENTS).upper()
    for verb in ("DROP", "UPDATE", "DELETE", "ALTER TABLE STUDENT_SESSIONS DROP"):
        assert verb not in blob, verb
    assert "IF NOT EXISTS" in blob

    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "matbot"
              / "reporting_schema.py").read_text(encoding="utf-8")
    body = source.split("def _apply_v5_ddl(")[1].split("\ndef ")[0]
    for verb in ("DROP ", "DELETE ", "UPDATE "):
        assert verb not in body.upper(), verb


def test_6c_existing_columns_are_not_recreated(tmp_path):
    """`ADD COLUMN` se ne ponavlja nad kolonom koja već postoji."""
    conn = with_index(v4_database(tmp_path, name="cols.db"), PRODUCTION_SQL)
    reporting_schema.migrate_to_v5(conn)
    columns = [r[1] for r in conn.execute(
        "PRAGMA table_info(student_sessions)").fetchall()]
    assert columns.count("session_time") == 1
    assert columns.count("topic_source") == 1
    conn.close()


# ===========================================================================
# 7) IDEMPOTENTNOST
# ===========================================================================
def test_7_a_second_run_is_a_no_op(tmp_path):
    conn = with_index(v4_database(tmp_path, name="idem.db"), PRODUCTION_SQL)
    conn.execute("INSERT INTO students (display_name, grade) VALUES ('A', 7)")
    conn.execute(
        "INSERT INTO student_sessions (student_id, session_date, attendance, "
        " activity_rating, homework_status) "
        "VALUES (1, '2026-05-01', 'present', 4, 'done')")
    conn.commit()
    assert reporting_schema.migrate_to_v5(conn) is True

    rows_before = [tuple(r) for r in conn.execute(
        "SELECT * FROM student_sessions ORDER BY id").fetchall()]
    index_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (INDEX,)).fetchall()[0][0]

    assert reporting_schema.migrate_to_v5(conn) is False
    assert reporting_schema.verify_v5_schema(conn) == []

    versions = [r[0] for r in conn.execute(
        "SELECT version FROM schema_migrations WHERE version = 5").fetchall()]
    assert versions == [5], "duplikat reda migracije"
    assert [tuple(r) for r in conn.execute(
        "SELECT * FROM student_sessions ORDER BY id").fetchall()] == rows_before
    assert conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        (INDEX,)).fetchall()[0][0] == index_before
    conn.close()


# ===========================================================================
# 8-9) NIŠTA DRUGO SE NIJE PROMIJENILO
# ===========================================================================
def test_8_the_data_model_is_untouched():
    assert [name for name, _ in reporting_schema.V5_SESSION_COLUMNS] == [
        "session_time", "topic_source"]
    assert reporting_schema.V5_CLASS_INDEX == "idx_student_sessions_logical_class"
    assert len(reporting_schema.SCHEMA_V5_STATEMENTS) == 1
    assert set(reporting_schema.EXPECTED_V5_SCHEMA) == {"student_sessions"}


def test_9_the_v5_contract_is_unchanged_by_later_versions():
    """v5 ostaje tačno ono što je bio; v6 je DODAT, nije ga prepisao."""
    from matbot import config

    assert config.REPORTING_SCHEMA_VERSION == reporting_schema.CURRENT_SCHEMA_VERSION
    assert reporting_schema.SCHEMA_VERSION_V5 == 5
    assert {2, 3, 4, 5} <= set(reporting_schema.MIGRATION_DESCRIPTIONS)
    assert [name for name, _ in reporting_schema.V5_SESSION_COLUMNS] == [
        "session_time", "topic_source"]
    assert reporting_schema.V5_CLASS_INDEX == "idx_student_sessions_logical_class"
