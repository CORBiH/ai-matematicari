"""Šema izvještajne baze i migracije. JEDINI vlasnik DDL-a.

ZAŠTO POSTOJI ODVOJEN MODUL: `matbot/reporting_db.py` je vlasnik UPITA nad
postojećom šemom; ovdje živi ono što šemu MIJENJA. Razdvojeno je namjerno —
migracija se pokreće rijetko, svjesno i izvan zahtjevnog puta, dok se upiti
izvršavaju na svakom turnu.

VERZIJA 1 (produkcija) je stvorena van ovog repozitorija i ovdje se NE dira:
students, student_accounts, learning_activity, assessment_attempts,
assessment_item_results, matbot_sessions, instructor_notes, monthly_reports,
sync_state, schema_migrations.

VERZIJA 2 SAMO DODAJE tri tabele za Thinkific „Student Progress" snimke. Nijedna
postojeća tabela se ne mijenja, ne preimenuje i ne briše — migracija je čisto
aditivna, pa je i najgori ishod (prekid usred izvršavanja) bezopasan: fale samo
nove tabele, a Faza 1 i Faza 2 rade netaknuto.

MODEL PODATAKA: UVOZ → SNIMAK → SEKCIJE.

  thinkific_progress_imports    jedan red po UČITANOM FAJLU (revizija: hash,
                                broj redova, kada je uvezen)
  thinkific_progress_snapshots  jedno STANJE po (učenik, kurs, mjesec)
  thinkific_progress_sections   napredak po kurikularnoj sekciji tog snimka

Sekcije su NORMALIZOVANE, ne JSON blob, i to je odluka s razlogom: cijela svrha
faze je poređenje mjeseci („koja sekcija je napredovala"), a to je nad tabelom
običan `JOIN`, dok bi nad JSON-om bilo raspakivanje u aplikaciji za svaki red.
Nazivi sekcija se k tome razlikuju po razredu i mijenjaju kroz vrijeme.
"""
import logging

logger = logging.getLogger("matbot.reporting_schema")

SCHEMA_VERSION_V1 = 1
SCHEMA_VERSION_V2 = 2
CURRENT_SCHEMA_VERSION = SCHEMA_VERSION_V2

# Tabele koje verzija 1 mora imati da bismo uopšte smjeli migrirati.
V1_TABLES = (
    "students", "student_accounts", "learning_activity", "assessment_attempts",
    "assessment_item_results", "matbot_sessions", "instructor_notes",
    "monthly_reports", "sync_state", "schema_migrations",
)

V2_TABLES = ("thinkific_progress_imports", "thinkific_progress_snapshots",
             "thinkific_progress_sections")

# --- DDL VERZIJE 2 ---------------------------------------------------------
# `IF NOT EXISTS` svuda: migracija mora biti ponovljiva bez štete.
#
# `ON DELETE` je biran NAMJERNO, ne po navici:
#   • snapshot.student_id  -> CASCADE  (brisanje učenika briše i njegove snimke;
#                             isti izbor kao Faza 1/2, GDPR-friendly)
#   • section.snapshot_id  -> CASCADE  (sekcija bez snimka je besmislena)
#   • snapshot.import_id   -> RESTRICT (uvoz je REVIZIJSKI trag: dok god neki
#                             snimak tvrdi „došao sam iz ovog fajla", taj red se
#                             ne smije izgubiti. Brisanje uvoza mora biti
#                             svjesna radnja, ne posljedica.)
SCHEMA_V2_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS thinkific_progress_imports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_month TEXT NOT NULL,
        course_key TEXT NOT NULL,
        course_name TEXT NOT NULL,
        grade INTEGER NOT NULL,
        source_sha256 TEXT NOT NULL,
        row_count INTEGER NOT NULL CHECK (row_count >= 0),
        imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS thinkific_progress_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        import_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        report_month TEXT NOT NULL,
        course_key TEXT NOT NULL,
        course_name TEXT NOT NULL,
        grade INTEGER NOT NULL,
        percent_viewed REAL CHECK (percent_viewed IS NULL
                                   OR (percent_viewed >= 0 AND percent_viewed <= 100)),
        percent_completed REAL CHECK (percent_completed IS NULL
                                      OR (percent_completed >= 0
                                          AND percent_completed <= 100)),
        started_at TEXT,
        completed_at TEXT,
        activated_at TEXT,
        expires_at TEXT,
        last_sign_in TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (import_id) REFERENCES thinkific_progress_imports(id)
            ON DELETE RESTRICT,
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
        UNIQUE (student_id, course_key, report_month)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS thinkific_progress_sections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL,
        ordinal INTEGER,
        section_name TEXT NOT NULL,
        progress_percent REAL CHECK (progress_percent IS NULL
                                     OR (progress_percent >= 0
                                         AND progress_percent <= 100)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (snapshot_id) REFERENCES thinkific_progress_snapshots(id)
            ON DELETE CASCADE,
        UNIQUE (snapshot_id, section_name)
    )
    """,
    # Mjesečni izvještaj UVIJEK čita po učeniku i mjesecu — indeks postoji da
    # poređenje dva mjeseca ostane jeftino i kad tabela naraste.
    "CREATE INDEX IF NOT EXISTS idx_progress_snapshots_student_month "
    "ON thinkific_progress_snapshots (student_id, report_month)",
    "CREATE INDEX IF NOT EXISTS idx_progress_snapshots_month_course "
    "ON thinkific_progress_snapshots (report_month, course_key)",
)


class MigrationError(RuntimeError):
    """Migracija nije sigurna ili nije uspjela. Nikad ne nosi PII."""


def table_names(conn):
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in cursor.fetchall()}


def applied_versions(conn):
    cursor = conn.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def current_version(conn):
    rows = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchall()
    return rows[0][0] if rows and rows[0][0] is not None else None


def migrate_to_v2(conn):
    """v1 → v2. IDEMPOTENTNO i ADITIVNO. Vraća `True` ako je nešto primijenjeno.

    Odbija da radi nad bazom koja nije prepoznatljiva v1 — bolje stati nego
    kreirati tabele u tuđoj šemi. Sve ide kroz JEDNU transakciju: ili postoje
    sve tri tabele i zapis verzije, ili ništa."""
    existing = table_names(conn)
    missing = [name for name in V1_TABLES if name not in existing]
    if missing:
        raise MigrationError("v1_schema_incomplete: " + ",".join(sorted(missing)))

    already = applied_versions(conn)
    if SCHEMA_VERSION_V1 not in already:
        raise MigrationError("v1_migration_record_missing")
    if SCHEMA_VERSION_V2 in already and all(name in existing for name in V2_TABLES):
        return False                      # već migrirano — bez izmjene

    try:
        for statement in SCHEMA_V2_STATEMENTS:
            conn.execute(statement)
        # Verzija se upisuje TEK kad su tabele stvarno tu, i to bez duplikata.
        conn.execute(
            "INSERT INTO schema_migrations (version) "
            "SELECT ? WHERE NOT EXISTS "
            "(SELECT 1 FROM schema_migrations WHERE version = ?)",
            (SCHEMA_VERSION_V2, SCHEMA_VERSION_V2))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        raise MigrationError("migration_failed:" + type(exc).__name__) from None

    logger.info("reporting_schema_migrated from=%s to=%s",
                SCHEMA_VERSION_V1, SCHEMA_VERSION_V2)
    return True
