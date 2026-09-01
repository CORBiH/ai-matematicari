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
import re

logger = logging.getLogger("matbot.reporting_schema")

SCHEMA_VERSION_V1 = 1
SCHEMA_VERSION_V2 = 2
SCHEMA_VERSION_V3 = 3
SCHEMA_VERSION_V4 = 4
SCHEMA_VERSION_V5 = 5
CURRENT_SCHEMA_VERSION = SCHEMA_VERSION_V5

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
    SCHEMA_VERSION_V3: "Add instructor student session records",
    SCHEMA_VERSION_V4: "Require explicit current-grade confirmation",
    SCHEMA_VERSION_V5: "Add class session time and topic source",
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


# --- FAZA 3D: evidencija časova (`student_sessions`) ------------------------
# JEDAN RED PO ODRŽANOM ČASU. Ovo je prvi izvor u izvještaju koji NE dolazi ni iz
# Thinkifica ni iz MAT-BOT-a nego od instruktora, pa je i jedini koji učenika bez
# ijedne platforme čini vrijednim izvještaja.
#
# `activity_rating` NIJE OCJENA IZ MATEMATIKE nego angažman na času (1–5), i
# CHECK ga veže uz prisustvo: odsutan učenik NE SMIJE imati ocjenu angažmana.
# Lažna jedinica za odsutnog bi mjesecima obarala prosjek i čitala bi se kao
# nezainteresovanost umjesto kao izostanak.
#
# `homework_status` ima TRI stanja, ne bulean: „nije zadana" ne smije ulaziti u
# imenilac i ne smije se čitati kao neurađena zadaća.
V3_TABLES = ("student_sessions",)

SCHEMA_V3_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS student_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        session_date TEXT NOT NULL,
        attendance TEXT NOT NULL CHECK (attendance IN ('present', 'absent')),
        activity_rating INTEGER CHECK (
            (attendance = 'absent' AND activity_rating IS NULL)
            OR (activity_rating IS NULL)
            OR (activity_rating BETWEEN 1 AND 5)),
        homework_status TEXT NOT NULL CHECK (
            homework_status IN ('done', 'not_done', 'not_assigned')),
        area_name TEXT,
        lesson_name TEXT,
        comment TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
    )
    """,
    # Izvještaj UVIJEK čita po učeniku i po mjesecu (opseg datuma).
    "CREATE INDEX IF NOT EXISTS idx_student_sessions_student_date "
    "ON student_sessions (student_id, session_date)",
)

EXPECTED_V3_SCHEMA = {
    "student_sessions": {
        "required_columns": {"id", "student_id", "session_date", "attendance",
                             "activity_rating", "homework_status", "area_name",
                             "lesson_name", "comment", "created_at", "updated_at"},
        "not_null": ("student_id", "session_date", "attendance",
                     "homework_status", "created_at", "updated_at"),
        "foreign_keys": {("student_id", "students"): "CASCADE"},
    },
}

EXPECTED_V3_INDEXES = {
    "idx_student_sessions_student_date": ("student_sessions",
                                          ("student_id", "session_date")),
}


def _index_columns(conn, index_name):
    return tuple(entry[2] for entry
                 in conn.execute("PRAGMA index_info(%s)" % index_name).fetchall())


def verify_v3_schema(conn):
    """Strukturna provjera verzije 3. Prazna lista znači „dokazano ispravno".

    PRAGMA introspekcija, ne poređenje teksta `CREATE TABLE` naredbe: razmak ili
    redoslijed ograničenja ne smiju oboriti migraciju, ali nedostajuća kolona,
    NOT NULL, strani ključ ili indeks moraju."""
    problems = []
    existing = table_names(conn)

    for table, expected in EXPECTED_V3_SCHEMA.items():
        if table not in existing:
            problems.append("v3_table_missing:%s" % table)
            continue
        try:
            details = _column_details(conn, table)
            keys = _foreign_keys_with_actions(conn, table)
        except Exception:
            problems.append("v3_table_unreadable:%s" % table)
            continue

        missing = expected["required_columns"] - set(details)
        if missing:
            problems.append("v3_columns_missing:%s:%s"
                            % (table, ",".join(sorted(missing))))
            continue

        for column in expected["not_null"]:
            if not details[column][0]:
                problems.append("v3_nullable:%s:%s" % (table, column))

        for (column, target), action in expected["foreign_keys"].items():
            found = keys.get((column, target))
            if found is None:
                problems.append("v3_foreign_key_missing:%s:%s" % (table, column))
            elif found != action:
                problems.append("v3_foreign_key_action:%s:%s:%s"
                                % (table, column, found or "none"))

    indexes = _index_definitions(conn)
    for name, (table, columns) in EXPECTED_V3_INDEXES.items():
        if indexes.get(name) != table:
            problems.append("v3_index_missing:%s" % name)
            continue
        try:
            if _index_columns(conn, name) != columns:
                problems.append("v3_index_columns:%s" % name)
        except Exception:
            problems.append("v3_index_unreadable:%s" % name)
    return problems


def verify_existing_v3_tables(conn):
    """Provjeri SAMO ono što već postoji — ulaz u djelimično stanje.

    Postoji iz istog razloga kao v2 blizanac: `CREATE TABLE IF NOT EXISTS` nad
    tuđom ili pokvarenom tabelom istog imena tiho ne uradi ništa, pa bi bez ove
    provjere takva tabela bila blagoslovljena kao ispravna."""
    existing = table_names(conn)
    if not any(table in existing for table in EXPECTED_V3_SCHEMA):
        return []
    return [problem for problem in verify_v3_schema(conn)
            if not problem.startswith(("v3_table_missing", "v3_index_missing"))]


def _apply_v3_ddl(conn):
    for statement in SCHEMA_V3_STATEMENTS:
        try:
            conn.execute(statement)
        except Exception as exc:
            raise MigrationError("v3_ddl_failed", type(exc).__name__) from None
    try:
        conn.commit()
    except Exception as exc:
        raise MigrationError("v3_ddl_commit_failed", type(exc).__name__) from None


def migrate_to_v3(conn):
    """v2 → v3. ADITIVNO, IDEMPOTENTNO i OTPORNO NA PREKID.

    ISTA DOKTRINA KAO v1→v2, i to nije stil nego IZMJERENO ponašanje: na
    libsql 0.1.11 `CREATE TABLE` PREŽIVI `rollback()`. Zato se ovdje NE tvrdi
    atomičnost. Umjesto toga:

      • DDL je `IF NOT EXISTS` i smije se ponoviti nad bilo kojim djelimičnim
        stanjem;
      • zapis verzije se upisuje TEK kad je cijela struktura DOKAZANA;
      • prekid u bilo kojoj tački ostavlja bazu bez zapisa verzije 3, pa je
        sljedeće pokretanje jednostavno dovrši.

    TVRDI INVARIJANT: ZAPIS VERZIJE 3 NE SMIJE POSTOJATI BEZ PROVJERENE ŠEME V3.
    Baza koja tvrdi v3 a nema ispravnu tabelu pada zatvoreno.

    FAZA 3C SE NE DIRA: nijedna naredba ne mijenja `monthly_reports` ni bilo
    koju v1/v2 tabelu — migracija samo dodaje."""
    already = applied_versions(conn)
    if SCHEMA_VERSION_V2 not in already:
        raise MigrationError("v2_migration_record_missing")

    if SCHEMA_VERSION_V3 in already:
        problems = verify_v3_schema(conn)
        if problems:
            raise MigrationError(problems[0], "recorded v3 but schema incomplete")
        return False

    prior = verify_existing_v3_tables(conn)
    if prior:
        raise MigrationError(prior[0], "existing v3 object is incompatible")

    _apply_v3_ddl(conn)

    problems = verify_v3_schema(conn)
    if problems:
        raise MigrationError(problems[0], "verification failed after ddl")

    _record_migration(conn, SCHEMA_VERSION_V3)
    logger.info("reporting_schema_migrated from=%s to=%s",
                SCHEMA_VERSION_V2, SCHEMA_VERSION_V3)
    return True


# --- FAZA 3D+: POTVRDA TEKUCEG RAZREDA (verzija 4) -------------------------
# ZASTO OVA VERZIJA POSTOJI (produkcijski nalaz, 2026-08-29). Forenzika je
# dokazala da je augustovski Thinkific uvoz ZAISTA izvoz kursa 6. razreda
# (sekcije: SKUPOVI, DJELJIVOST BROJEVA, RAZLOMCI, DECIMALNI BROJEVI...), a da
# u njemu potpuno legitimno ucestvuju ucenici koje covjek prepoznaje kao sedmi,
# osmi i deveti razred. Iz toga slijedi razlika koju sema do sada NIJE imala:
#
#   RAZRED SADRZAJA (koji kurs/gradivo je ucenik koristio)
#   NIJE
#   TEKUCI SKOLSKI RAZRED (koji razred ucenik pohadja).
#
# Sedmak koji obnavlja gradivo sestog razreda je NORMALAN slucaj, ne kvar. Zato
# nijedan sadrzajni trag (Thinkific, kontrolni, MAT-BOT aktivnost) vise ne smije
# ni napisati ni predloziti `students.grade`.
#
# STO SEMA MORA MOCI, A NIJE MOGLA: razlikovati razred koji je administrator
# SVJESNO POTVRDIO od razreda koji je stara automatika tiho upisala. Obje
# vrijednosti su danas obican `INTEGER` i nerazlucive su. Bez te razlike bi 34
# zatecena ucenika izgledala kao potvrdjena, a nijedan to nije.
#
# NAJMANJE SIGURNO RJESENJE: dvije NULL kolone na `students`.
#
#   grade_confirmed_at TEXT NULL   kad je covjek potvrdio (NULL = nikad)
#   grade_source       TEXT NULL   ko je potvrdio: 'admin' | 'manual_creation'
#
# ADITIVNO I NEDESTRUKTIVNO: `ALTER TABLE ... ADD COLUMN` bez DEFAULT-a ne
# prepisuje nijedan postojeci red. Zatecene vrijednosti `students.grade` OSTAJU
# (istorijski i administrativni kontekst se ne brise), ali dobijaju NULL potvrdu
# — dakle tacno ono sto jesu: NEPOTVRDJENE. Potvrda se NE IZMISLJA iz postojece
# vrijednosti; to bi bilo isto nagadjanje koje je i napravilo problem.
#
# ZASTO NEMA `CHECK` NA `grade_source`: SQLite ne moze dodati CHECK na postojecu
# tabelu bez prepisivanja cijele tabele, a prepisivanje `students` u produkciji
# nije prihvatljiv rizik za dvije opcione kolone. Skup vrijednosti se zato
# zatvara u Pythonu (`matbot/student_grades.py::VALID_GRADE_SOURCES`), a upis
# ide iskljucivo kroz dvije funkcije koje ga postuju.
#
# STARA APLIKACIJA I DALJE RADI: svaki upit nad `students` u ovom repozitoriju
# navodi kolone IZRICITO (nema `SELECT *`), pa dodatne kolone ne mijenjaju
# nijedan postojeci rezultat. Zato je redoslijed „migracija pa zamjena
# aplikacije" i dalje ispravan i ostaje nepromijenjen.
V4_STUDENT_COLUMNS = (
    ("grade_confirmed_at", "TEXT"),
    ("grade_source", "TEXT"),
)

EXPECTED_V4_SCHEMA = {
    "students": {"required_columns": {name for name, _ in V4_STUDENT_COLUMNS}},
}


def verify_v4_schema(conn):
    """Strukturna provjera verzije 4. Prazna lista znaci „dokazano ispravno".

    Provjerava se i da su nove kolone NULLABLE: kolona s NOT NULL bi znacila da
    ju je neko drugi napravio pod drugim pravilima, a `ALTER TABLE ADD COLUMN`
    bi nad postojecim redovima uz NOT NULL bez DEFAULT-a i inace pukao."""
    problems = []
    existing = table_names(conn)
    for table, expected in EXPECTED_V4_SCHEMA.items():
        if table not in existing:
            problems.append("v4_table_missing:%s" % table)
            continue
        try:
            details = _column_details(conn, table)
        except Exception:
            problems.append("v4_table_unreadable:%s" % table)
            continue
        missing = expected["required_columns"] - set(details)
        if missing:
            problems.append("v4_columns_missing:%s:%s"
                            % (table, ",".join(sorted(missing))))
            continue
        for column in sorted(expected["required_columns"]):
            if details[column][0]:
                problems.append("v4_not_nullable:%s:%s" % (table, column))
    return problems


def verify_existing_v4_columns(conn):
    """Provjeri SAMO ono sto vec postoji — ulaz u djelimicno stanje.

    Prekinuta migracija je mogla dodati prvu kolonu a ne i drugu. Odsutna
    kolona je zato NORMALAN ulaz i ne prijavljuje se; postojeca kolona s
    pogresnim svojstvima se prijavljuje i zaustavlja migraciju."""
    return [problem for problem in verify_v4_schema(conn)
            if not problem.startswith(("v4_table_missing", "v4_columns_missing"))]


def _apply_v4_ddl(conn):
    """Dodaj SAMO kolone kojih nema. `ADD COLUMN` nema `IF NOT EXISTS`.

    Ovo je razlog zasto se ovdje prvo cita `PRAGMA table_info`: v2 i v3 su bili
    `CREATE TABLE IF NOT EXISTS` i smjeli su se slijepo ponoviti, a `ALTER TABLE
    ADD COLUMN` nad postojecom kolonom pukne. Nastavljivost se zato dobija
    provjerom prije naredbe, a ne samom naredbom."""
    try:
        present = _columns(conn, "students")
    except Exception as exc:
        raise MigrationError("v4_students_unreadable", type(exc).__name__) from None
    for name, sql_type in V4_STUDENT_COLUMNS:
        if name in present:
            continue
        try:
            conn.execute("ALTER TABLE students ADD COLUMN %s %s" % (name, sql_type))
        except Exception as exc:
            raise MigrationError("v4_ddl_failed", type(exc).__name__) from None
    try:
        conn.commit()
    except Exception as exc:
        raise MigrationError("v4_ddl_commit_failed", type(exc).__name__) from None


def migrate_to_v4(conn):
    """v3 -> v4. ADITIVNO, IDEMPOTENTNO i OTPORNO NA PREKID.

    ISTA DOKTRINA KAO v1->v2 i v2->v3, iz istog izmjerenog razloga: na libsql
    0.1.11 DDL PREZIVI `rollback()`, pa se atomicnost ne tvrdi. Umjesto toga:

      • dodaju se samo kolone kojih NEMA, pa se korak smije ponoviti nad bilo
        kojim djelimicnim stanjem;
      • zapis verzije se upisuje TEK kad je struktura DOKAZANA;
      • prekid u bilo kojoj tacki ostavlja bazu bez zapisa verzije 4, pa je
        sljedece pokretanje jednostavno dovrsi.

    NIJEDAN POSTOJECI RED SE NE MIJENJA. `students.grade` ostaje kakav jeste,
    aktivnost, kontrolni, snimci, casovi i izvjestaji se ne diraju. Jedina
    posljedica je da zatecena vrijednost razreda od sada ima NULL potvrdu —
    dakle citala se kao NEPOTVRDJENA, sto je i istina."""
    already = applied_versions(conn)
    if SCHEMA_VERSION_V3 not in already:
        raise MigrationError("v3_migration_record_missing")

    if SCHEMA_VERSION_V4 in already:
        problems = verify_v4_schema(conn)
        if problems:
            raise MigrationError(problems[0], "recorded v4 but schema incomplete")
        return False

    prior = verify_existing_v4_columns(conn)
    if prior:
        raise MigrationError(prior[0], "existing v4 column is incompatible")

    _apply_v4_ddl(conn)

    problems = verify_v4_schema(conn)
    if problems:
        raise MigrationError(problems[0], "verification failed after ddl")

    _record_migration(conn, SCHEMA_VERSION_V4)
    logger.info("reporting_schema_migrated from=%s to=%s",
                SCHEMA_VERSION_V3, SCHEMA_VERSION_V4)
    return True


# --- VRIJEME I IZVOR TEME ČASA (verzija 5) ---------------------------------
# ZAŠTO OVA VERZIJA POSTOJI — DVA STVARNA OPERATIVNA ZAHTJEVA.
#
# 1. VIŠE GRUPA ISTOG RAZREDA ISTOG DANA. Instruktor drži sedmi razred u 10:00 i
#    opet u 14:00, ponekad NAD ISTOM lekcijom. Do sada je logički identitet časa
#    bio (učenik, datum, oblast, lekcija), pa su se ta dva stvarna časa
#    SUDARALA: drugo čuvanje je pregazilo prvo. Vrijeme je jedino što ih
#    razlikuje, pa mora postojati u podacima — nije kozmetika.
#
# 2. NIJE SVAKI ČAS LEKCIJA IZ PLANA. „Uvodni čas", „Ponavljanje", „Priprema za
#    kontrolni", „Konsultacije" su stvarni časovi koji u `topics.json` ne
#    postoje i ne smiju se izmišljati kao kurikularna lekcija. `topic_source`
#    čini razliku EKSPLICITNOM, umjesto da se kasnije nagađa pretragom naziva
#    po kurikulumu — nagađanje bi se mijenjalo svaki put kad se plan izmijeni.
#
# ADITIVNO I NEDESTRUKTIVNO: dvije NULL kolone. Nijedan zatečeni red se ne
# prepisuje i nijedno vrijeme se NE IZMIŠLJA. Stari red ostaje bez vremena, i to
# je istina o njemu — ne znamo kad je čas bio.
#
# ZNAČENJE `topic_source = NULL` NA ZATEČENIM REDOVIMA: čita se kao kurikularni
# čas, i to nije nagađanje nego činjenica o putu unosa — do ove verzije je
# `validate_session` SVAKI čas s gradivom provjeravao prema `topics.json`, pa
# drugačiji nije mogao ni nastati.
V5_SESSION_COLUMNS = (
    ("session_time", "TEXT"),
    ("topic_source", "TEXT"),
)

# --- STRUKTURNA ZAŠTITA OD DUPLIKATA ---------------------------------------
# Prethodno izdanje je SVJESNO izbjeglo jedinstveni indeks: nad zatečenim
# produkcijskim redovima se nije moglo dokazati da ga zadovoljavaju, pa bi
# migracija mogla pasti. Sada je taj problem NESTAO sam od sebe, i to je razlog
# zašto se indeks konačno smije uvesti:
#
#   svi ZATEČENI redovi imaju `session_time IS NULL`
#   svi NOVI redovi imaju vrijeme
#
# pa DJELIMIČAN indeks (`WHERE session_time IS NOT NULL`) ne dodiruje nijedan
# postojeći red i ne može pasti na zatečenim podacima. Izmjereno na libsql
# 0.1.11: djelimičan indeks nad izrazom je podržan, stvarno odbija duplikat
# vremenskog reda, a redove bez vremena uopšte ne gleda.
#
# `COALESCE(..., '')` je OBAVEZAN, ne stil: u SQLite-u su dva NULL-a u
# jedinstvenom indeksu RAZLIČITA, pa bi bez njega dva časa s praznom oblašću
# (što je legitiman slučaj kod ručne teme) prošla kao različita i indeks ne bi
# štitio ništa.
V5_CLASS_INDEX = "idx_student_sessions_logical_class"

SCHEMA_V5_STATEMENTS = (
    "CREATE UNIQUE INDEX IF NOT EXISTS " + V5_CLASS_INDEX + " "
    "ON student_sessions (student_id, session_date, session_time, "
    " COALESCE(area_name, ''), COALESCE(lesson_name, '')) "
    "WHERE session_time IS NOT NULL",
)

EXPECTED_V5_SCHEMA = {
    "student_sessions": {
        "required_columns": {name for name, _ in V5_SESSION_COLUMNS},
    },
}

# Dijelovi koje ISPIS indeksa mora sadržavati. Provjerava se `sqlite_master.sql`,
# a NE `PRAGMA index_info`: izmjereno — za kolonu koja je izraz PRAGMA vraća
# `-2` i ime `None`, pa bi provjera po kolonama bila slijepa upravo na dijelu
# koji nosi garanciju.
# KLJUČ I PREDIKAT SE PROVJERAVAJU ODVOJENO, i to nije uljepšavanje.
#
# Dok je provjera bila jedan spisak odlomaka nad CIJELIM ispisom, indeks kojem
# `session_time` NEDOSTAJE U KLJUČU prolazio je — jer se `session_time` i dalje
# pojavljuje u `WHERE session_time IS NOT NULL`. Takav indeks je strukturno
# pogrešan na najgori mogući način: zabranio bi dva časa iste lekcije istog dana
# u RAZLIČITO vrijeme, dakle upravo ono zbog čega verzija 5 i postoji.
#
# Zato se kanonski ispis dijeli na `where`: ključ mora nositi svih pet dijelova,
# a predikat mora biti tačno onaj koji ostavlja zatečene redove bez vremena van
# indeksa.
_V5_INDEX_KEY_FRAGMENTS = (
    "student_id", "session_date", "session_time",
    "coalesce(area_name,'')", "coalesce(lesson_name,'')",
)
_V5_INDEX_PREDICATE = "session_time is not null"

# RAZMAK UZ ZAGRADU I ZAREZ NIJE STRUKTURA.
#
# ŽIVI NALAZ (produkcija, izdanje 9225dd9): raspoređivanje je palo iako je
# indeks bio POTPUNO ISPRAVAN. Turso je isti objekat kanonizovao kao
#
#     COALESCE (area_name, '')
#
# a lokalno generisani oblik glasi `COALESCE(area_name, '')`. Sažimanje
# uzastopnih razmaka (`" ".join(sql.split())`) razmak IZMEĐU imena funkcije i
# otvorene zagrade ne dira, pa je provjera prijavila `v5_index_shape` nad
# strukturno tačnim indeksom — LAŽNO NEGATIVNO, i migracija se nije upisala.
#
# Zato se prije poređenja uklanja razmak PRILJEPLJEN uz `(`, `)` i `,`. Uklanja
# se isključivo PRAZNINA: nijedan identifikator, literal ni znak interpunkcije se
# ne gubi, pa `coalesce(area_name,'x')`, goli `area_name` ili `COALESCE` nad
# drugom kolonom i dalje padaju. Negativni testovi to i mjere — normalizacija
# skida osjetljivost na oblik, ne na strukturu.
_INSIGNIFICANT_SPACE_RE = re.compile(r"\s*([(),])\s*")


def canonical_index_sql(sql):
    """Ispis indeksa → oblik u kojem se smiju porediti STRUKTURNI dijelovi.

    Dvije koraka, oba determinisitička: sažmi praznine i spusti u mala slova, pa
    ukloni prazninu uz zagrade i zareze. Nema parsera, nema približnog
    poređenja."""
    collapsed = " ".join(str(sql or "").split()).lower()
    return _INSIGNIFICANT_SPACE_RE.sub(r"\1", collapsed)


def _normalized_index_sql(conn, name):
    rows = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'index' "
                        "AND name = ?", (name,)).fetchall()
    if not rows or not rows[0][0]:
        return None
    return canonical_index_sql(rows[0][0])


def verify_v5_schema(conn):
    """Strukturna provjera verzije 5. Prazna lista znači „dokazano ispravno"."""
    problems = []
    existing = table_names(conn)
    for table, expected in EXPECTED_V5_SCHEMA.items():
        if table not in existing:
            problems.append("v5_table_missing:%s" % table)
            continue
        try:
            details = _column_details(conn, table)
        except Exception:
            problems.append("v5_table_unreadable:%s" % table)
            continue
        missing = expected["required_columns"] - set(details)
        if missing:
            problems.append("v5_columns_missing:%s:%s"
                            % (table, ",".join(sorted(missing))))
            continue
        for column in sorted(expected["required_columns"]):
            if details[column][0]:
                # NOT NULL bi značilo da je kolonu napravio neko drugi pod
                # drugim pravilima — zatečeni redovi je nemaju.
                problems.append("v5_not_nullable:%s:%s" % (table, column))

    if "student_sessions" not in existing:
        return problems

    try:
        index_sql = _normalized_index_sql(conn, V5_CLASS_INDEX)
    except Exception:
        problems.append("v5_index_unreadable:%s" % V5_CLASS_INDEX)
        return problems
    if index_sql is None:
        problems.append("v5_index_missing:%s" % V5_CLASS_INDEX)
        return problems
    if "unique" not in index_sql:
        problems.append("v5_index_not_unique:%s" % V5_CLASS_INDEX)

    # Podjela na `where`: lijevo je KLJUČ, desno PREDIKAT. Odlomak nađen na
    # pogrešnoj strani ne dokazuje ništa (vidi komentar uz konstante).
    key, separator, predicate = index_sql.partition("where")
    if not separator or _V5_INDEX_PREDICATE not in predicate:
        problems.append("v5_index_predicate:%s" % V5_CLASS_INDEX)
    for fragment in _V5_INDEX_KEY_FRAGMENTS:
        if fragment not in key:
            problems.append("v5_index_shape:%s" % V5_CLASS_INDEX)
            break
    return problems


def verify_existing_v5_objects(conn):
    """Provjeri SAMO ono što već postoji — ulaz u djelimično stanje.

    Prekinuta migracija je mogla dodati prvu kolonu a ne i drugu, ili kolone a
    ne i indeks. Odsutno je NORMALAN ulaz i ne prijavljuje se; postojeće s
    pogrešnim svojstvima zaustavlja migraciju."""
    return [problem for problem in verify_v5_schema(conn)
            if not problem.startswith(("v5_table_missing", "v5_columns_missing",
                                       "v5_index_missing"))]


def _apply_v5_ddl(conn):
    """Dodaj SAMO ono čega nema. `ADD COLUMN` nema `IF NOT EXISTS`."""
    try:
        present = _columns(conn, "student_sessions")
    except Exception as exc:
        raise MigrationError("v5_sessions_unreadable", type(exc).__name__) from None
    for name, sql_type in V5_SESSION_COLUMNS:
        if name in present:
            continue
        try:
            conn.execute("ALTER TABLE student_sessions ADD COLUMN %s %s"
                         % (name, sql_type))
        except Exception as exc:
            raise MigrationError("v5_ddl_failed", type(exc).__name__) from None
    for statement in SCHEMA_V5_STATEMENTS:
        try:
            conn.execute(statement)
        except Exception as exc:
            # Ovdje bi pao jedinstveni indeks da zatečeni redovi imaju vrijeme.
            # Ne mogu ga imati (kolona je upravo dodata kao NULL), pa je ovo
            # zaštita od tuđeg objekta istog imena, ne od naših podataka.
            raise MigrationError("v5_index_failed", type(exc).__name__) from None
    try:
        conn.commit()
    except Exception as exc:
        raise MigrationError("v5_ddl_commit_failed", type(exc).__name__) from None


def migrate_to_v5(conn):
    """v4 → v5. ADITIVNO, IDEMPOTENTNO i OTPORNO NA PREKID.

    ISTA DOKTRINA KAO SVE RANIJE: DDL preživi `rollback()` (izmjereno na libsql
    0.1.11), pa se atomičnost ne tvrdi. Dodaje se samo ono čega nema, struktura
    se DOKAZUJE, a red verzije se upisuje TEK na kraju.

    NIJEDAN ZATEČENI RED SE NE MIJENJA: nema `UPDATE`-a, nema izmišljenog
    vremena i nema izmišljenog izvora teme. Djelimičan jedinstveni indeks gleda
    isključivo redove S vremenom, a zatečeni ga nemaju — zato migracija ne može
    pasti na produkcijskim podacima."""
    already = applied_versions(conn)
    if SCHEMA_VERSION_V4 not in already:
        raise MigrationError("v4_migration_record_missing")

    if SCHEMA_VERSION_V5 in already:
        problems = verify_v5_schema(conn)
        if problems:
            raise MigrationError(problems[0], "recorded v5 but schema incomplete")
        return False

    prior = verify_existing_v5_objects(conn)
    if prior:
        raise MigrationError(prior[0], "existing v5 object is incompatible")

    _apply_v5_ddl(conn)

    problems = verify_v5_schema(conn)
    if problems:
        raise MigrationError(problems[0], "verification failed after ddl")

    _record_migration(conn, SCHEMA_VERSION_V5)
    logger.info("reporting_schema_migrated from=%s to=%s",
                SCHEMA_VERSION_V4, SCHEMA_VERSION_V5)
    return True


# --- FAZA 3C: tabela `monthly_reports` -------------------------------------
# OVAJ REPO NIKAD NIJE KREIRAO `monthly_reports`. Tabela postoji u produkciji iz
# izvorne Matematičari šeme, a njen oblik je 2026-08-27 IZMJEREN čitajućom
# introspekcijom na VPS-u — nije pretpostavljen i nije prepisan iz testa.
# Izmišljen fixture je već jednom oborio migraciju u produkciji (v1→v2,
# `description TEXT NOT NULL`), pa je ovdje jedini prihvatljiv izvor istine
# stvarno mjerenje.
#
# IZMJERENO STANJE: svih devet kolona koje Faza 3C treba, PLUS `generated_at`,
# uz UNIQUE(student_id, report_month) i strani ključ na `students` s ON DELETE
# CASCADE. Migracija NIJE potrebna — nema šeme v3.
#
# PROVJERA JE PODSKUPOVNA, NE JEDNAKOSNA: traži se da tabela ima ono što Faza 3C
# koristi, a dodatne saglasne kolone se DOPUŠTAJU. Jednakost skupa kolona bi
# odbila upravo onu produkcijsku tabelu zbog koje provjera i postoji.
# „Dopušteno" ne znači „slabo": ispod se provjeravaju i NOT NULL, podrazumijevana
# vrijednost `status`-a, jedinstvenost i strani ključ — dakle svojstva na koja se
# upis stvarno oslanja.
MONTHLY_REPORTS_REQUIRED_COLUMNS = frozenset({
    "id", "student_id", "report_month", "status", "metrics_json", "ai_summary",
    "instructor_comment", "pdf_path", "created_at", "updated_at",
})

# Kolona koju produkcija ima i koju Faza 3C SVJESNO koristi: vrijeme posljednjeg
# AI generisanja. Nije obavezna za rad (stariji oblik tabele bez nje i dalje
# prolazi), ali kad postoji — puni se.
MONTHLY_REPORTS_GENERATED_AT = "generated_at"

# Kolone koje NE SMIJU primiti NULL, jer se upis na njih oslanja.
_MONTHLY_REPORTS_NOT_NULL = ("student_id", "report_month", "status",
                             "created_at", "updated_at")

# DDL koji lokalni razvoj i testovi koriste. BAJT ZA BAJT ista svojstva kao
# izmjerena produkcijska tabela (kolone, NOT NULL, CHECK, DEFAULT, UNIQUE, FK
# s ON DELETE CASCADE). NIJE migracija i ne izvršava se nad produkcijom.
MONTHLY_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS monthly_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    report_month TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'final')),
    metrics_json TEXT,
    ai_summary TEXT,
    instructor_comment TEXT,
    pdf_path TEXT,
    generated_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    UNIQUE (student_id, report_month)
)
"""

# Indeks koji produkcija ima uz autoindeks jedinstvenosti. Postojanje mu se
# provjerava informativno — jedinstvenost je ta koja je obavezna.
MONTHLY_REPORTS_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_monthly_reports_student_month "
    "ON monthly_reports (student_id, report_month)")


def _column_details(conn, table):
    """{ime: (notnull, podrazumijevana_vrijednost)} iz `PRAGMA table_info`."""
    return {row[1]: (bool(row[3]), row[4])
            for row in conn.execute("PRAGMA table_info(%s)" % table).fetchall()}


def _foreign_keys_with_actions(conn, table):
    """{(kolona, ciljna_tabela): on_delete} — CASCADE je dio ugovora, ne detalj."""
    return {(row[3], row[2]): (row[6] or "").upper() for row
            in conn.execute("PRAGMA foreign_key_list(%s)" % table).fetchall()}


def monthly_reports_capabilities(conn):
    """Šta zatečena tabela nudi. Čita, ne mijenja ništa."""
    if "monthly_reports" not in table_names(conn):
        return {"present": False, "generated_at": False}
    try:
        columns = _columns(conn, "monthly_reports")
    except Exception:
        return {"present": False, "generated_at": False}
    return {"present": True,
            "generated_at": MONTHLY_REPORTS_GENERATED_AT in columns}


def verify_monthly_reports_schema(conn):
    """Čitajuća provjera da `monthly_reports` podnosi Fazu 3C.

    Vraća listu strukturnih kodova; prazna znači „dokazano upotrebljivo". Ne
    kreira, ne mijenja i ne migrira ništa. Dodatne kolone se dopuštaju —
    nedostajuće garancije NE."""
    problems = []
    if "monthly_reports" not in table_names(conn):
        return ["monthly_reports_missing"]
    try:
        details = _column_details(conn, "monthly_reports")
        uniques = _unique_column_sets(conn, "monthly_reports")
        keys = _foreign_keys_with_actions(conn, "monthly_reports")
    except Exception:
        return ["monthly_reports_unreadable"]

    missing = MONTHLY_REPORTS_REQUIRED_COLUMNS - set(details)
    if missing:
        # Bez kolona nema smisla provjeravati ostalo — ostatak bi lagao.
        return ["monthly_reports_columns_missing:" + ",".join(sorted(missing))]

    for column in _MONTHLY_REPORTS_NOT_NULL:
        if not details[column][0]:
            problems.append("monthly_reports_nullable:" + column)

    # `status` mora imati podrazumijevanu vrijednost jer upis oslanja se na nju
    # kad je ne pošalje izričito. Poredi se OČIŠĆENA vrijednost, jer SQLite
    # podrazumijevanu vrijednost vraća s navodnicima ('draft').
    default_status = (details["status"][1] or "").strip().strip("'\"")
    if default_status != "draft":
        problems.append("monthly_reports_status_default:" + (default_status or "none"))

    if ("student_id", "report_month") not in uniques:
        # Bez ovoga bi jedan (učenik, mjesec) mogao dobiti dva izvještaja.
        problems.append("monthly_reports_unique_missing")

    on_delete = keys.get(("student_id", "students"))
    if on_delete is None:
        problems.append("monthly_reports_foreign_key_missing")
    elif on_delete != "CASCADE":
        # Brisanje učenika ne smije ostaviti izvještaj bez vlasnika.
        problems.append("monthly_reports_foreign_key_action:" + (on_delete or "none"))

    return problems
