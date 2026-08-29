"""Faza 3C — saglasnost sa STVARNOM produkcijskom tabelom `monthly_reports`.

Ovaj fajl postoji zbog jedne već plaćene greške: v1→v2 migracija je pala u
produkciji jer je test potvrđivao IZMIŠLJEN fixture umjesto stvarne tabele
(`description TEXT NOT NULL`). Zato se ovdje ne provjerava naš DDL protiv sebe
samog, nego protiv DDL-a koji je 2026-08-27 PROČITAN s produkcijskog VPS-a i
prepisan ovdje doslovno.

DVIJE TVRDNJE:
  1. Izmjerena produkcijska tabela je READY — uključujući `generated_at`, koji
     je dodatna saglasna kolona, ne razlog za odbijanje.
  2. Provjera i dalje odbija tabelu kojoj nedostaje garancija na koju se upis
     oslanja (NOT NULL, DEFAULT, UNIQUE, strani ključ s CASCADE).

Bez (2) bi (1) bila samo popustljivost.
"""
import pytest

from matbot import parent_report, report_facts, reporting_db, reporting_schema
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_parent_report import good_narrative, payload
from tests.test_thinkific_progress_import import build_v1, migrate, rows

libsql = pytest.importorskip("libsql")

# DOSLOVNO prepisano iz čitajuće introspekcije produkcije (VPS, 2026-08-27).
# Ne uređivati bez novog mjerenja.
PRODUCTION_DDL = """
CREATE TABLE monthly_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    student_id INTEGER NOT NULL,

    report_month TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (
            status IN ('draft', 'final')
        ),

    metrics_json TEXT,

    ai_summary TEXT,

    instructor_comment TEXT,

    pdf_path TEXT,

    generated_at TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (student_id)
        REFERENCES students(id)
        ON DELETE CASCADE,

    UNIQUE (student_id, report_month)
)
"""
PRODUCTION_INDEX = ("CREATE INDEX idx_monthly_reports_student_month "
                    "ON monthly_reports (student_id, report_month)")


def table(tmp_path, ddl, *, index=None, name="probe.db"):
    """Baza sa `students` i jednom varijantom `monthly_reports`."""
    path = str(tmp_path / name)
    conn = libsql.connect(path)
    conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, "
                 " display_name TEXT, grade INTEGER)")
    conn.execute(ddl)
    if index:
        conn.execute(index)
    conn.commit()
    return path, conn


def _properties(conn):
    """Mjerena svojstva, NE tekst DDL-a (razmaci i prelomi nisu ugovor)."""
    return (reporting_schema._column_details(conn, "monthly_reports"),
            reporting_schema._unique_column_sets(conn, "monthly_reports"),
            reporting_schema._foreign_keys_with_actions(conn, "monthly_reports"))


# ---------------------------------------------------------------------------
# 1-2) Produkcija je READY
# ---------------------------------------------------------------------------
def test_measured_production_ddl_is_ready(tmp_path):
    _, conn = table(tmp_path, PRODUCTION_DDL, index=PRODUCTION_INDEX)
    try:
        assert reporting_schema.verify_monthly_reports_schema(conn) == []
    finally:
        conn.close()


def test_generated_at_is_recognised_not_merely_tolerated(tmp_path):
    _, conn = table(tmp_path, PRODUCTION_DDL)
    try:
        assert reporting_schema.monthly_reports_capabilities(conn) == {
            "present": True, "generated_at": True}
    finally:
        conn.close()


def test_extra_compatible_column_does_not_reject_the_table(tmp_path):
    """Dodatna kolona nije razlog za odbijanje — provjera je podskupovna."""
    ddl = PRODUCTION_DDL.replace("generated_at TEXT,",
                                 "generated_at TEXT,\n    sent_to_parent_at TEXT,")
    _, conn = table(tmp_path, ddl)
    try:
        assert reporting_schema.verify_monthly_reports_schema(conn) == []
    finally:
        conn.close()


def test_our_local_ddl_matches_measured_production_properties(tmp_path):
    """Lokalni DDL i produkcija moraju imati ISTA mjerena svojstva."""
    _, prod = table(tmp_path, PRODUCTION_DDL, index=PRODUCTION_INDEX,
                    name="prod.db")
    _, local = table(tmp_path, reporting_schema.MONTHLY_REPORTS_DDL,
                     index=reporting_schema.MONTHLY_REPORTS_INDEX_DDL,
                     name="local.db")
    try:
        assert _properties(local) == _properties(prod)
    finally:
        prod.close()
        local.close()


def test_monthly_reports_still_needs_no_migration_in_v3():
    """Faza 3D dodaje v3, ali `monthly_reports` OSTAJE netaknuta.

    Verzija 3 donosi SAMO `student_sessions`. Nijedna njena naredba ne dira
    tabelu izvještaja — stari sačuvani nacrti se ne prepisuju (Dio 35)."""
    from matbot import config

    assert reporting_schema.CURRENT_SCHEMA_VERSION == 3
    assert config.REPORTING_SCHEMA_VERSION == 3
    assert set(reporting_schema.MIGRATION_DESCRIPTIONS) == {2, 3}
    assert reporting_schema.V3_TABLES == ("student_sessions",)
    blob = " ".join(reporting_schema.SCHEMA_V3_STATEMENTS)
    assert "monthly_reports" not in blob
    for table in reporting_schema.V1_TABLES + reporting_schema.V2_TABLES:
        assert "ALTER TABLE %s" % table not in blob
        assert "DROP TABLE %s" % table not in blob


# ---------------------------------------------------------------------------
# 3-5) Nedostajuća garancija pada ZATVORENO
# ---------------------------------------------------------------------------
def test_missing_required_column_fails_closed(tmp_path):
    ddl = PRODUCTION_DDL.replace("    metrics_json TEXT,\n", "")
    _, conn = table(tmp_path, ddl)
    try:
        problems = reporting_schema.verify_monthly_reports_schema(conn)
        assert problems and "columns_missing" in problems[0]
        assert "metrics_json" in problems[0]
    finally:
        conn.close()


def test_missing_unique_constraint_fails_closed(tmp_path):
    ddl = PRODUCTION_DDL.replace(",\n\n    UNIQUE (student_id, report_month)\n", "\n")
    _, conn = table(tmp_path, ddl)
    try:
        assert "monthly_reports_unique_missing" in \
            reporting_schema.verify_monthly_reports_schema(conn)
    finally:
        conn.close()


def test_missing_foreign_key_fails_closed(tmp_path):
    ddl = PRODUCTION_DDL.replace(
        "    FOREIGN KEY (student_id)\n        REFERENCES students(id)\n"
        "        ON DELETE CASCADE,\n\n", "")
    _, conn = table(tmp_path, ddl)
    try:
        assert "monthly_reports_foreign_key_missing" in \
            reporting_schema.verify_monthly_reports_schema(conn)
    finally:
        conn.close()


def test_foreign_key_without_cascade_fails_closed(tmp_path):
    """Brisanje učenika ne smije ostaviti izvještaj bez vlasnika."""
    ddl = PRODUCTION_DDL.replace("\n        ON DELETE CASCADE,", ",")
    _, conn = table(tmp_path, ddl)
    try:
        problems = reporting_schema.verify_monthly_reports_schema(conn)
        assert any("foreign_key_action" in p for p in problems), problems
    finally:
        conn.close()


def test_foreign_key_to_the_wrong_table_fails_closed(tmp_path):
    ddl = PRODUCTION_DDL.replace("REFERENCES students(id)",
                                 "REFERENCES wrong_table(id)")
    path = str(tmp_path / "wrong.db")
    conn = libsql.connect(path)
    conn.execute("CREATE TABLE students (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE wrong_table (id INTEGER PRIMARY KEY)")
    conn.execute(ddl)
    conn.commit()
    try:
        assert "monthly_reports_foreign_key_missing" in \
            reporting_schema.verify_monthly_reports_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("column", ["student_id", "report_month", "status",
                                    "created_at", "updated_at"])
def test_nullable_required_column_fails_closed(tmp_path, column):
    ddl = PRODUCTION_DDL
    if column in ("created_at", "updated_at"):
        ddl = ddl.replace("%s TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP" % column,
                          "%s TEXT DEFAULT CURRENT_TIMESTAMP" % column)
    elif column == "status":
        ddl = ddl.replace("status TEXT NOT NULL DEFAULT 'draft'",
                          "status TEXT DEFAULT 'draft'")
    else:
        ddl = ddl.replace("%s INTEGER NOT NULL" % column, "%s INTEGER" % column)
        ddl = ddl.replace("%s TEXT NOT NULL" % column, "%s TEXT" % column)
    _, conn = table(tmp_path, ddl)
    try:
        assert "monthly_reports_nullable:" + column in \
            reporting_schema.verify_monthly_reports_schema(conn)
    finally:
        conn.close()


def test_missing_status_default_fails_closed(tmp_path):
    ddl = PRODUCTION_DDL.replace("status TEXT NOT NULL DEFAULT 'draft'",
                                 "status TEXT NOT NULL")
    _, conn = table(tmp_path, ddl)
    try:
        problems = reporting_schema.verify_monthly_reports_schema(conn)
        assert any("status_default" in p for p in problems), problems
    finally:
        conn.close()


def test_legacy_stub_table_still_fails_closed(tmp_path):
    _, conn = table(tmp_path,
                    "CREATE TABLE monthly_reports (id INTEGER PRIMARY KEY)")
    try:
        assert reporting_schema.verify_monthly_reports_schema(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6-10) Ponašanje upisa nad PRODUKCIJSKI OBLIKOVANOM tabelom
# ---------------------------------------------------------------------------
@pytest.fixture
def prod_db(tmp_path, monkeypatch):
    """Baza čija je `monthly_reports` TAČNO produkcijskog oblika."""
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    migrate(path)
    conn = libsql.connect(path)
    conn.execute("DROP TABLE IF EXISTS monthly_reports")
    conn.execute(PRODUCTION_DDL)
    conn.execute(PRODUCTION_INDEX)
    conn.commit()
    conn.close()
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    reporting_db.set_database(reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False)))
    yield path
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


@pytest.fixture
def student(prod_db):
    return reporting_db.get_database().get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "learner@example.com", grade=6)


def snapshot():
    return parent_report.metrics_snapshot(
        report_facts.build_ai_facts(payload()), model="m", prompt_version="v")


def generated_at(path):
    return rows(path, "SELECT generated_at FROM monthly_reports")[0][0]


def test_generation_populates_generated_at(prod_db, student):
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot())
    stamp = generated_at(prod_db)
    assert stamp and len(stamp) == 19          # isti oblik kao CURRENT_TIMESTAMP
    assert parent_report.load_saved(student, "2026-08")["generated_at"] == stamp


def test_regeneration_moves_generated_at_forward(prod_db, student):
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot(),
                                 generated_at="2026-08-01 08:00:00")
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot(),
                                 generated_at="2026-09-02 09:30:00")
    assert generated_at(prod_db) == "2026-09-02 09:30:00"


def test_ordinary_edit_preserves_generated_at(prod_db, student):
    """Ručna ispravka rečenice NIJE novo AI generisanje."""
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot(),
                                 generated_at="2026-08-01 08:00:00")
    parent_report.save_edits(student, "2026-08",
                             good_narrative(summary="Ručno uređeno."),
                             "Komentar instruktora.")
    assert generated_at(prod_db) == "2026-08-01 08:00:00"
    saved = parent_report.load_saved(student, "2026-08")
    assert saved["narrative"]["summary"] == "Ručno uređeno."
    assert saved["instructor_comment"] == "Komentar instruktora."


def test_pdf_generation_preserves_generated_at(prod_db, student):
    from matbot import report_pdf

    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot(),
                                 generated_at="2026-08-01 08:00:00")
    saved = parent_report.load_saved(student, "2026-08")
    for _ in range(2):
        report_pdf.render_report_pdf(saved["snapshot"]["facts"],
                                     saved["narrative"], "", "Učenik")
    assert generated_at(prod_db) == "2026-08-01 08:00:00"


def test_generated_at_is_not_duplicated_inside_metrics_json(prod_db, student):
    """Jedan žig, jedno mjesto — dva bi se prije ili kasnije razišla."""
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot())
    stored = rows(prod_db, "SELECT metrics_json FROM monthly_reports")[0][0]
    assert "generated_at" not in stored
    assert '"prompt_version"' in stored and '"model"' in stored


def test_one_student_month_stays_one_row_on_the_production_table(prod_db, student):
    for _ in range(5):
        parent_report.save_narrative(student, "2026-08", good_narrative(),
                                     snapshot())
        parent_report.save_edits(student, "2026-08", good_narrative(), "k")
    assert rows(prod_db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 1


def test_status_defaults_to_draft_on_the_production_table(prod_db, student):
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot())
    assert rows(prod_db, "SELECT status FROM monthly_reports")[0][0] == "draft"


def test_unique_constraint_is_actually_enforced(prod_db, student):
    """Ne vjeruje se PRAGMA-i na riječ — jedinstvenost se izazove."""
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot())
    conn = libsql.connect(prod_db)
    try:
        with pytest.raises(Exception):
            conn.execute("INSERT INTO monthly_reports "
                         "(student_id, report_month, status) "
                         "VALUES (?, ?, 'draft')", (student, "2026-08"))
            conn.commit()
    finally:
        conn.close()


def test_check_reports_monthly_reports_ready(prod_db):
    report = reporting_db.get_database().check()
    assert report["monthly_reports_ready"] is True
    assert report["monthly_reports_problems"] == []
    assert "generated_at" in report["columns"]["monthly_reports"]
    text = reporting_db._format_report(report)
    assert "monthly_reports: ready" in text
    assert "columns[monthly_reports]:" in text


def test_check_reports_unusable_for_the_legacy_stub(tmp_path, monkeypatch):
    path = str(tmp_path / "stub.db")
    build_v1(path)
    migrate(path)          # šema v1 nosi patrljak `monthly_reports`
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "t")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    report = database.check()
    assert report["monthly_reports_ready"] is False
    assert "monthly_reports: UNUSABLE" in reporting_db._format_report(report)
