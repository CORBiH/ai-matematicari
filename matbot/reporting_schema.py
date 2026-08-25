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
postojeća tabela se ne mijenja, ne preimenuje i ne briše.

MIGRACIJA JE NASTAVLJIVA, NE „SVE ILI NIŠTA". Prva verzija je tvrdila da su tri
tabele i zapis verzije jedna transakcija; Živi incident (2026-08-25) je pokazao da
to nije istina: upis zapisa je pukao, `rollback()` je pozvan, a sve tri tabele su
OSTALE. Izmjereno i lokalno na libsql 0.1.11 — `CREATE TABLE` preživi
`rollback()`. Zato se DDL izvodi `IF NOT EXISTS` (smije se ponoviti nad bilo
kojim djelimičnim stanjem), struktura se poslije toga DOKAZUJE introspekcijom, a
zapis verzije se upisuje TEK na kraju. Prekid u bilo kojoj tački ostavlja bazu
bez zapisa verzije 2, pa je sljedeće pokretanje jednostavno dovrši.

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
    """Migracija nije sigurna ili nije uspjela.

    `code` je STRUKTURNI dijagnostički kod (npr. `v2_schema_incompatible:
    thinkific_progress_snapshots`), nikad sirovi tekst izuzetka baze — a nikad
    ni URL, token, e-mail ni bilo koji podatak učenika."""

    def __init__(self, code, detail=""):
        super().__init__(code if not detail else "%s (%s)" % (code, detail))
        self.code = code
        self.detail = detail


# --- ZAPIS MIGRACIJE -------------------------------------------------------
# ŽIVI INCIDENT (produkcija, 2026-08-25): `schema_migrations` u produkciji ima
# TRI kolone, a srednja je OBAVEZNA:
#
#     version INTEGER PRIMARY KEY
#     description TEXT NOT NULL          <- bez DEFAULT-a
#     applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
#
# Migracija je upisivala samo `version`, pa je libsql podigao
# `ValueError: NOT NULL constraint failed: schema_migrations.description`.
# Zato opis migracije od sada NIJE opcion: svaka verzija ga mora imati, i to
# smislen — prazan ili razmakom popunjen opis se odbija prije upisa.
MIGRATION_DESCRIPTIONS = {
    SCHEMA_VERSION_V2: "Add Thinkific progress reporting schema",
}

# `applied_at` se NAMJERNO ne upisuje: ima bazin DEFAULT i njegovo značenje je
# „kad je red nastao", dakle vrijeme upisa. Isti princip kao `created_at`
# drugdje u izvještajnom sloju.

# --- OČEKIVANA STRUKTURA VERZIJE 2 -----------------------------------------
# Provjerava se STRUKTURA, ne tekst `CREATE TABLE` naredbe: bezazlena razlika u
# razmacima ili redoslijedu ograničenja ne smije oboriti migraciju, ali
# nedostajuća kolona, ključ ili indeks moraju.
#
# `required_columns` su kolone bez kojih upis ili čitanje ne bi radili.
# `unique` su skupovi kolona koji moraju imati JEDINSTVEN indeks — na njima
# počiva cijela idempotentnost Faze 3A.
# `foreign_keys` su (kolona, ciljna tabela) parovi.
EXPECTED_V2_SCHEMA = {
    "thinkific_progress_imports": {
        "required_columns": {"id", "report_month", "course_key", "course_name",
                             "grade", "source_sha256", "row_count", "imported_at"},
        "unique": [],
        "foreign_keys": [],
    },
    "thinkific_progress_snapshots": {
        "required_columns": {"id", "import_id", "student_id", "report_month",
                             "course_key", "course_name", "grade", "percent_viewed",
                             "percent_completed", "started_at", "completed_at",
                             "activated_at", "expires_at", "last_sign_in",
                             "created_at"},
        "unique": [("student_id", "course_key", "report_month")],
        "foreign_keys": [("student_id", "students"),
                         ("import_id", "thinkific_progress_imports")],
    },
    "thinkific_progress_sections": {
        "required_columns": {"id", "snapshot_id", "ordinal", "section_name",
                             "progress_percent", "created_at"},
        "unique": [("snapshot_id", "section_name")],
        "foreign_keys": [("snapshot_id", "thinkific_progress_snapshots")],
    },
}

EXPECTED_V2_INDEXES = {
    "idx_progress_snapshots_student_month": ("thinkific_progress_snapshots",
                                             ("student_id", "report_month")),
    "idx_progress_snapshots_month_course": ("thinkific_progress_snapshots",
                                            ("report_month", "course_key")),
}


def table_names(conn):
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in cursor.fetchall()}


def applied_versions(conn):
    cursor = conn.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def current_version(conn):
    rows = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchall()
    return rows[0][0] if rows and rows[0][0] is not None else None


def _columns(conn, table):
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table).fetchall()}


def _unique_column_sets(conn, table):
    """Skupovi kolona koje BAZA garantuje jedinstvenima (UNIQUE ili PK indeks)."""
    found = set()
    for row in conn.execute("PRAGMA index_list(%s)" % table).fetchall():
        name, is_unique = row[1], row[2]
        if not is_unique:
            continue
        columns = tuple(entry[2] for entry
                        in conn.execute("PRAGMA index_info(%s)" % name).fetchall())
        found.add(columns)
    return found


def _foreign_keys(conn, table):
    """{(kolona, ciljna_tabela)} — smjer koji nam je bitan za integritet."""
    return {(row[3], row[2]) for row
            in conn.execute("PRAGMA foreign_key_list(%s)" % table).fetchall()}


def _index_definitions(conn):
    return {row[0]: row[1] for row
            in conn.execute("SELECT name, tbl_name FROM sqlite_master "
                            "WHERE type = 'index'").fetchall()}


def verify_v2_schema(conn):
    """Strukturna provjera cijele verzije 2. Vraća listu STRUKTURNIH kodova.

    Prazna lista znači „dokazano kompatibilno". Svaki drugi ishod je razlog da
    se verzija 2 NE upiše — `CREATE TABLE IF NOT EXISTS` tiho ne uradi ništa kad
    tabela postoji, pa bi bez ove provjere tuđa ili pokvarena tabela istog imena
    bila blagoslovljena kao ispravna."""
    problems = []
    existing = table_names(conn)

    for table, expected in EXPECTED_V2_SCHEMA.items():
        if table not in existing:
            problems.append("v2_table_missing:%s" % table)
            continue
        try:
            columns = _columns(conn, table)
            uniques = _unique_column_sets(conn, table)
            keys = _foreign_keys(conn, table)
        except Exception:
            problems.append("v2_schema_unreadable:%s" % table)
            continue

        missing = expected["required_columns"] - columns
        if missing:
            problems.append("v2_schema_incompatible:%s" % table)
            continue
        for required in expected["unique"]:
            if tuple(required) not in uniques:
                problems.append("v2_unique_missing:%s" % table)
        for column, target in expected["foreign_keys"]:
            if (column, target) not in keys:
                problems.append("v2_foreign_key_missing:%s" % table)

    try:
        indexes = _index_definitions(conn)
    except Exception:
        problems.append("v2_index_unreadable")
        return sorted(set(problems))

    for name, (table, columns) in EXPECTED_V2_INDEXES.items():
        if indexes.get(name) != table:
            problems.append("v2_index_missing_or_invalid:%s" % name)
            continue
        try:
            actual = tuple(entry[2] for entry
                           in conn.execute("PRAGMA index_info(%s)" % name).fetchall())
        except Exception:
            problems.append("v2_index_missing_or_invalid:%s" % name)
            continue
        if actual != tuple(columns):
            problems.append("v2_index_missing_or_invalid:%s" % name)

    return sorted(set(problems))


def verify_existing_v2_tables(conn):
    """Provjeri SAMO one v2 tabele koje VEC postoje. Vrati strukturne kodove.

    Zove se PRIJE ijedne DDL naredbe. Razlog je dijagnostika: ako v2 tabela vec
    postoji ali je pokvarena ili tudja, `CREATE INDEX` nad njom pukne prvi i dao
    bi neupotrebljiv kod `v2_ddl_failed`. Ovako operator odmah dobije
    `v2_schema_incompatible:<tabela>` i zna sta da gleda.

    Odsutne tabele se NE prijavljuju — one su normalan ulaz za nastavljivu
    migraciju i DDL ce ih tek napraviti."""
    present = table_names(conn)
    problems = []
    for table, expected in EXPECTED_V2_SCHEMA.items():
        if table not in present:
            continue
        try:
            columns = _columns(conn, table)
            uniques = _unique_column_sets(conn, table)
            keys = _foreign_keys(conn, table)
        except Exception:
            problems.append("v2_schema_unreadable:%s" % table)
            continue
        if expected["required_columns"] - columns:
            problems.append("v2_schema_incompatible:%s" % table)
            continue
        for required in expected["unique"]:
            if tuple(required) not in uniques:
                problems.append("v2_unique_missing:%s" % table)
        for column, target in expected["foreign_keys"]:
            if (column, target) not in keys:
                problems.append("v2_foreign_key_missing:%s" % table)
    return sorted(set(problems))


def _apply_v2_ddl(conn):
    """Sve naredbe su `IF NOT EXISTS`, pa se smiju ponoviti nad djelimičnim
    stanjem. Ovdje se NIŠTA ne briše i ništa ne mijenja — samo dodaje."""
    for statement in SCHEMA_V2_STATEMENTS:
        try:
            conn.execute(statement)
        except Exception as exc:
            raise MigrationError("v2_ddl_failed", type(exc).__name__) from None
    try:
        conn.commit()
    except Exception as exc:
        raise MigrationError("v2_ddl_commit_failed", type(exc).__name__) from None


def _record_migration(conn, version):
    """Upiši red migracije SA SVIM OBAVEZNIM KOLONAMA.

    Ovo je tačka na kojoj je produkcija pukla: `description` je NOT NULL bez
    default-a, a upisivali smo samo `version`."""
    description = MIGRATION_DESCRIPTIONS.get(version, "")
    if not description.strip():
        raise MigrationError("migration_description_missing")
    try:
        conn.execute(
            "INSERT INTO schema_migrations (version, description) "
            "SELECT ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM schema_migrations WHERE version = ?)",
            (version, description, version))
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        raise MigrationError("migration_record_insert_failed",
                             type(exc).__name__) from None


def migrate_to_v2(conn):
    """v1 → v2. ADITIVNO, IDEMPOTENTNO i OTPORNO NA PREKID.

    ZAŠTO OVO NIJE „SVE ILI NIŠTA" (ispravka lažne tvrdnje iz prve verzije):
    IZMJERENO na libsql 0.1.11 — `CREATE TABLE` PREŽIVI `rollback()`. Prva
    verzija je tvrdila da su tri tabele i zapis verzije jedna transakcija; u
    produkciji je upis zapisa pukao, `rollback()` je pozvan, a sve tri tabele su
    OSTALE. Oslanjati se na povlačenje DDL-a je zato pogrešno.

    Umjesto toga migracija je NASTAVLJIVA: DDL je `IF NOT EXISTS` i smije se
    ponoviti nad bilo kojim djelimičnim stanjem, a red verzije se upisuje TEK
    kad je cijela struktura DOKAZANA. Prekid u bilo kojoj tački ostavlja bazu
    bez zapisa verzije 2, pa je sljedeći pokretanje jednostavno dovrši.

    TVRDI INVARIJANT: ZAPIS VERZIJE 2 NE SMIJE POSTOJATI DOK NE POSTOJI CIJELA
    PROVJERENA ŠEMA V2. Zato se i postojeći zapis verzije 2 provjerava — baza
    koja tvrdi v2 a nema tabelu pada zatvoreno umjesto da se prikaže zdravom.

    Vraća `True` ako je nešto primijenjeno, `False` ako je već sve na mjestu.
    Baca `MigrationError` sa STRUKTURNIM kodom."""
    existing = table_names(conn)
    missing_v1 = [name for name in V1_TABLES if name not in existing]
    if missing_v1:
        raise MigrationError("v1_schema_incomplete", ",".join(sorted(missing_v1)))

    already = applied_versions(conn)
    if SCHEMA_VERSION_V1 not in already:
        raise MigrationError("v1_migration_record_missing")

    if SCHEMA_VERSION_V2 in already:
        # Baza TVRDI da je na v2 — to se mora i dokazati, inače bi nepotpuna
        # šema zauvijek izgledala kao uspješno migrirana.
        problems = verify_v2_schema(conn)
        if problems:
            raise MigrationError(problems[0], "recorded v2 but schema incomplete")
        return False

    # Djelimično stanje je DOZVOLJEN ulaz: neke tabele već mogu postojati poslije
    # prekinutog pokušaja. Ali ako POSTOJE, moraju biti ISPRAVNE — provjera ide
    # PRIJE DDL-a da pokvarena tabela da precizan kod umjesto `v2_ddl_failed`.
    prior = verify_existing_v2_tables(conn)
    if prior:
        raise MigrationError(prior[0], "existing v2 object is incompatible")

    # `IF NOT EXISTS` preskače postojeće, a nedostajuće dodaje.
    _apply_v2_ddl(conn)

    problems = verify_v2_schema(conn)
    if problems:
        # DDL koji je već nastao ostaje (aditivan je i bezopasan), ali verzija se
        # NE upisuje. Sljedeći pokušaj kreće od istog mjesta.
        raise MigrationError(problems[0], "verification failed after ddl")

    _record_migration(conn, SCHEMA_VERSION_V2)
    logger.info("reporting_schema_migrated from=%s to=%s",
                SCHEMA_VERSION_V1, SCHEMA_VERSION_V2)
    return True
