"""Izvještajna baza — identitet učenika i granica kvara.

NIJEDAN TEST NE DIRA ŽIVI TURSO. Sve što ispituje SQL semantiku radi nad
LOKALNOM datotečnom bazom otvorenom ISTIM klijentom (`libsql`), pa se mjeri
stvarno ponašanje klijenta (rowcount kod `DO NOTHING`, rollback, strani
ključevi) a ne izmišljeni dvojnik. Testovi kvara ubacuju fabriku konekcija koja
puca ili visi — tako se timeout i nedostupnost dokazuju bez mreže.
"""
import threading

import pytest

from matbot import reporting_db
from matbot.reporting_db import PROVIDER_THINKIFIC, ReportingDatabase, ReportingUnavailable

libsql = pytest.importorskip("libsql")

# STVARNI PRODUKCIJSKI UGOVOR `schema_migrations` (izmjeren 2026-08-25, nakon
# zivog incidenta): `description` je NOT NULL BEZ default-a. Ranija verzija ove
# fixture imala je samo (version, applied_at), pa je test potvrdjivao NASU
# PRETPOSTAVKU umjesto produkcije -- migracija je zato pukla tek na stvarnoj bazi.
#
# Šema je NAMJERNO ista kao produkcijska u dijelu koji ovaj modul dodiruje —
# uključujući UNIQUE(provider, external_user_id) i strani ključ, jer se baš na
# njih oslanja algoritam. Ostale tabele postoje da dijagnostika ima šta naći.
SCHEMA = """
CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT,
    grade INTEGER,
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT,
    last_seen_at TEXT
);
CREATE TABLE student_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students(id),
    provider TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    created_at TEXT,
    last_seen_at TEXT,
    UNIQUE (provider, external_user_id)
);
CREATE TABLE learning_activity (id INTEGER PRIMARY KEY);
CREATE TABLE assessment_attempts (id INTEGER PRIMARY KEY);
CREATE TABLE assessment_item_results (id INTEGER PRIMARY KEY);
CREATE TABLE matbot_sessions (id INTEGER PRIMARY KEY);
CREATE TABLE instructor_notes (id INTEGER PRIMARY KEY);
CREATE TABLE monthly_reports (id INTEGER PRIMARY KEY);
CREATE TABLE sync_state (id INTEGER PRIMARY KEY);
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "reporting.db")
    conn = libsql.connect(path)
    for statement in SCHEMA.strip().split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations (version, description) "
                 "VALUES (1, 'Initial Matematicari reporting schema')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def database(db_path):
    db = ReportingDatabase(
        connect_factory=lambda: libsql.connect(db_path, timeout=10.0, _check_same_thread=False))
    yield db
    db.close()


def rows(db_path, sql, params=()):
    conn = libsql.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# --- 1) prvi zahtjev kreira TAČNO jednog učenika ----------------------------
def test_first_request_creates_exactly_one_student(database, db_path):
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC, "42")

    assert isinstance(student_id, int) and student_id > 0
    assert rows(db_path, "SELECT COUNT(*) FROM students")[0][0] == 1
    assert rows(db_path, "SELECT COUNT(*) FROM student_accounts")[0][0] == 1
    linked = rows(db_path,
                  "SELECT student_id, provider, external_user_id FROM student_accounts")
    assert linked == [(student_id, PROVIDER_THINKIFIC, "42")]


# --- 2) svaki sljedeći zahtjev vraća ISTI identitet -------------------------
def test_second_request_reuses_same_student(database, db_path):
    first = database.get_or_create_student(PROVIDER_THINKIFIC, "42", display_name="Amina")
    second = database.get_or_create_student(PROVIDER_THINKIFIC, "42")
    third = database.get_or_create_student(PROVIDER_THINKIFIC, "42", display_name="Amina H.")

    assert first == second == third
    assert rows(db_path, "SELECT COUNT(*) FROM students")[0][0] == 1
    # Ime se NE prepisuje na postojećem redu — ovo je identitetska putanja, ne
    # sinhronizacija profila.
    assert rows(db_path, "SELECT display_name FROM students")[0][0] == "Amina"


def test_numeric_and_string_external_id_are_the_same_identity(database, db_path):
    """Thinkific ID stiže i kao broj i kao string; UNIQUE ne bi sam po sebi
    spojio 123 i "123", pa ih normalizacija mora spojiti prije upisa."""
    first = database.get_or_create_student(PROVIDER_THINKIFIC, 123)
    second = database.get_or_create_student(PROVIDER_THINKIFIC, "123")

    assert first == second
    assert rows(db_path, "SELECT COUNT(*) FROM students")[0][0] == 1


# --- 3) isti vanjski ID kod DVA provajdera su DVA identiteta ----------------
def test_same_external_id_different_providers_stay_distinct(database, db_path):
    thinkific = database.get_or_create_student(PROVIDER_THINKIFIC, "7")
    other = database.get_or_create_student("moodle", "7")

    assert thinkific != other
    assert rows(db_path, "SELECT COUNT(*) FROM students")[0][0] == 2


# --- 4) dva različita vanjska ID-ja su dva identiteta -----------------------
def test_different_external_ids_stay_distinct(database, db_path):
    a = database.get_or_create_student(PROVIDER_THINKIFIC, "7")
    b = database.get_or_create_student(PROVIDER_THINKIFIC, "8")

    assert a != b
    assert rows(db_path, "SELECT COUNT(*) FROM students")[0][0] == 2


# --- 5) display_name i grade su OPCIONI -------------------------------------
def test_display_name_and_grade_are_optional(database, db_path):
    minimal = database.get_or_create_student(PROVIDER_THINKIFIC, "1")
    full = database.get_or_create_student(PROVIDER_THINKIFIC, "2",
                                          display_name="Emir", grade=7)

    assert rows(db_path, "SELECT display_name, grade FROM students WHERE id = ?",
                (minimal,)) == [(None, None)]
    assert rows(db_path, "SELECT display_name, grade FROM students WHERE id = ?",
                (full,)) == [("Emir", 7)]


def test_out_of_range_grade_is_dropped_not_stored(database, db_path):
    """Pogrešan razred u izvještaju je gori od nepoznatog."""
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC, "3",
                                                grade=99, display_name="   ")

    assert rows(db_path, "SELECT display_name, grade FROM students WHERE id = ?",
                (student_id,)) == [(None, None)]


def test_missing_identity_is_rejected_before_any_write(database, db_path):
    with pytest.raises(ReportingUnavailable) as first:
        database.get_or_create_student(PROVIDER_THINKIFIC, "")
    with pytest.raises(ReportingUnavailable) as second:
        database.get_or_create_student("", "42")

    assert first.value.code == "invalid_external_user_id"
    assert second.value.code == "invalid_provider"
    assert rows(db_path, "SELECT COUNT(*) FROM students")[0][0] == 0


# --- 6) last_seen se osvježava ---------------------------------------------
def test_last_seen_is_updated_on_reuse(database, db_path):
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC, "42")
    rows(db_path, "SELECT 1")
    conn = libsql.connect(db_path)
    conn.execute("UPDATE students SET last_seen_at = '2000-01-01 00:00:00' WHERE id = ?",
                 (student_id,))
    conn.execute("UPDATE student_accounts SET last_seen_at = '2000-01-01 00:00:00'")
    conn.commit()
    conn.close()

    database.get_or_create_student(PROVIDER_THINKIFIC, "42")

    student_seen = rows(db_path, "SELECT last_seen_at FROM students WHERE id = ?",
                        (student_id,))[0][0]
    account_seen = rows(db_path, "SELECT last_seen_at FROM student_accounts")[0][0]
    assert student_seen != "2000-01-01 00:00:00"
    assert account_seen != "2000-01-01 00:00:00"


def test_touch_last_seen_is_a_separate_operation(database, db_path):
    student_id = database.get_or_create_student(PROVIDER_THINKIFIC, "42")
    conn = libsql.connect(db_path)
    conn.execute("UPDATE students SET last_seen_at = '2000-01-01 00:00:00'")
    conn.commit()
    conn.close()

    assert database.touch_last_seen(student_id, PROVIDER_THINKIFIC, "42") is True
    assert rows(db_path, "SELECT last_seen_at FROM students")[0][0] != "2000-01-01 00:00:00"


# --- 7) istovremeno prvo kreiranje daje TAČNO jedan identitet ---------------
def test_concurrent_first_requests_resolve_to_one_identity(db_path):
    """Dvije ODVOJENE konekcije, dakle stvarna utrka na nivou baze — ne dvije
    niti koje ionako čeka isti proces-lokalni lock."""
    barrier = threading.Barrier(4)
    results = []
    errors = []

    def worker():
        db = ReportingDatabase(
            connect_factory=lambda: libsql.connect(db_path, timeout=10.0, _check_same_thread=False))
        try:
            barrier.wait(timeout=10)
            results.append(db.get_or_create_student(PROVIDER_THINKIFIC, "42",
                                                    display_name="Amina", grade=7))
        except Exception as exc:  # pragma: no cover - dijagnostika pri padu
            errors.append(repr(exc))
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    # ZASTO JE PROZOR POSMATRANJA VECI OD BUSY-TIMEOUTA KONEKCIJE (10 s):
    # dok su bili jednaki (30 s / 30 s), radnik koji legitimno ceka na zakljucan
    # fajl je nadzivio `join` i test bi pao na `len(results)` BEZ ijedne greske
    # u `errors` - dakle neuhvatljivo. Sada svako predugo cekanje padne kao
    # vidljiv SQLITE_BUSY unutar radnika, mnogo prije isteka `join`.
    still_running = [t.name for t in threads if t.is_alive()]
    assert not still_running, "radnik nije zavrsio: %s" % still_running
    assert errors == []
    assert len(results) == 4
    assert len(set(results)) == 1, "ista Thinkific osoba je dobila više identiteta"
    assert rows(db_path, "SELECT COUNT(*) FROM students")[0][0] == 1
    assert rows(db_path, "SELECT COUNT(*) FROM student_accounts")[0][0] == 1


def test_losing_racer_leaves_no_orphan_student_row(database, db_path):
    """Poražena strana mora poništiti i SVOJ `students` red.

    Utrka se pravi DETERMINISTIČKI: prvo traženje naloga se natjera da vrati
    „nema ga“ iako nalog postoji, pa kod uđe tačno u granu koju bi u stvarnoj
    utrci prošao gubitnik — `ON CONFLICT DO NOTHING` ne upiše ništa, transakcija
    se poništi, a identitet se pročita od pobjednika."""
    incumbent = database.get_or_create_student(PROVIDER_THINKIFIC, "42")
    before = rows(db_path, "SELECT COUNT(*) FROM students")[0][0]

    real_lookup = ReportingDatabase._lookup
    calls = {"n": 0}

    def blind_first_lookup(conn, provider, external):
        calls["n"] += 1
        if calls["n"] == 1:
            return None       # tako izgleda svijet gubitniku prije upisa
        return real_lookup(conn, provider, external)

    database._lookup = staticmethod(blind_first_lookup)
    try:
        resolved = database.get_or_create_student(PROVIDER_THINKIFIC, "42")
    finally:
        del database._lookup

    assert resolved == incumbent, "gubitnik nije preuzeo postojeći identitet"
    assert rows(db_path, "SELECT COUNT(*) FROM students")[0][0] == before,         "spekulativni students red nije poništen — ostalo je siroče"
    assert rows(db_path, "SELECT COUNT(*) FROM student_accounts")[0][0] == 1


# --- 9) strani ključevi (Dio 9) --------------------------------------------
def test_foreign_keys_are_enforced_on_the_canonical_connection(database, db_path):
    database.get_or_create_student(PROVIDER_THINKIFIC, "42")

    report = database.check()
    assert report["foreign_keys_on"] is True

    conn = libsql.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO student_accounts (student_id, provider, external_user_id) "
            "VALUES (999999, 'thinkific', 'orphan')")
        conn.commit()
    conn.close()


# --- 11/12) dijagnostika: konekcija, tabele, verzija šeme -------------------
def test_check_reports_schema_version_and_tables(database):
    """Baza na v1 se NE smije prijaviti kao ispravna.

    ZIVI INCIDENT: dok je ocekivana verzija bila zakucana na 1, `--check` nad
    NEMIGRIRANOM produkcijom je ispisivao „schema_version: 1 (expected 1) -> OK"
    iako je cijela verzija 2 nedostajala. Provjera je tvrdila da je sve u redu
    tacno kad nije."""
    report = database.check()

    assert report["connected"] is True
    assert report["missing_tables"] == []
    assert report["schema_version"] == 1
    assert report["expected_schema_version"] == 3
    assert report["schema_version_matches"] is False,         "nemigrirana baza se prijavljuje kao ispravna"
    assert report["v2_schema_verified"] is False
    assert "display_name" in report["columns"]["students"]
    assert "external_user_id" in report["columns"]["student_accounts"]


def test_check_reports_missing_tables_without_creating_them(tmp_path):
    path = str(tmp_path / "empty.db")
    conn = libsql.connect(path)
    conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    db = ReportingDatabase(connect_factory=lambda: libsql.connect(path))
    try:
        report = db.check()
    finally:
        db.close()

    assert "students" in report["missing_tables"]
    assert report["schema_version_matches"] is False
    # Dijagnostika NE SMIJE ništa kreirati.
    remaining = rows(path, "SELECT name FROM sqlite_master WHERE type = 'table'")
    assert [r[0] for r in remaining] == ["unrelated"]


def test_cli_check_returns_nonzero_when_not_configured(monkeypatch, capsys):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)

    assert reporting_db.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "reporting_db_not_configured" in out


def test_cli_check_refuses_an_unmigrated_v1_database(monkeypatch, database, capsys):
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://example.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "unused-because-database-is-injected")
    reporting_db.set_database(database)
    try:
        assert reporting_db.main(["--check"]) == 1, "v1 baza ne smije proci kao OK"
    finally:
        reporting_db.set_database(None)
    out = capsys.readouterr().out
    assert "connection: ok" in out
    assert "foreign_keys: ON" in out
    # v1 baza NIJE zdrava za ovo izdanje -- CLI to mora reci i vratiti != 0.
    assert "schema_version: 1 (expected 3) -> MISMATCH" in out
    assert "v2_schema: INCOMPLETE" in out
