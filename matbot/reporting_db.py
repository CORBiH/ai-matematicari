"""Izvještajna baza (Turso/libSQL) — ISKLJUČIVO identitet učenika.

ZAŠTO POSTOJI ODVOJEN MODUL: MAT-BOT namjerno nema bazu i ne čuva ništa o
učeniku (docs/ARCHITECTURE.md). Izvještavanje je PRVI podsistem koji piše van
procesa, pa dobija vlastitu granicu — SQL ne smije nikad izaći iz ovog fajla, a
nijedan HTTP handler ne smije znati kako baza izgleda.

TVRDI INVARIJANT: dostupnost izvještajne baze NIKAD ne odlučuje da li učenik
dobije odgovor. Zato ovaj modul ima DVA sloja:

  • strogi sloj (`ReportingDatabase.*`) — baca `ReportingUnavailable`; koristi
    ga dijagnostika i testovi, jer oni HOĆE da vide uzrok;
  • siguran sloj (`resolve_student`, `touch_last_seen`) — nikad ne baca, nikad
    ne blokira duže od `config.REPORTING_DB_TIMEOUT_S` i na svaki kvar vraća
    `None`. Pad, timeout, pogrešna konfiguracija i operativna greška izgledaju
    pozivaocu POTPUNO isto. Tutorski tokovi (Practice, Explain, Quick,
    Kontrolni) zato ne mogu pasti zbog izvještavanja.

JEDAN POKUŠAJ, BEZ RETRYJA. Isti princip kao granica modelskih poziva iz
CLAUDE.md: neuspjeh se prijavljuje, ne ponavlja. Ponavljanje bi sporu bazu
pretvorilo u kašnjenje tutorskog turna — tačno ono što je zabranjeno.

ŠTA SE NIKAD NE LOGUJE: `TURSO_AUTH_TOKEN`, URL baze, puno ime učenika, sirovi
potpisani token. Vanjski ID se u dijagnostičkom logu pojavljuje samo kao HMAC
otisak (`_fingerprint`), dakle nepovratno bez `FLASK_SECRET_KEY`.

OVAJ MODUL JOŠ NEMA POZIVAOCA U ZAHTJEVNOM PUTU, i to je namjerno: MAT-BOT
trenutno NEMA autentikovan identitet učenika (`matbot/auth.py` kuje anoniman
token bez korisnika, a `session_id` je localStorage UUID koji klijent bira sam).
`get_or_create_student` zato PRIMA identitet kao argument i ne pretpostavlja
odakle dolazi — spajanje na stvarni izvor (Thinkific SSO/JWT) je zasebna faza.
"""
import hashlib
import hmac
import logging
import threading

from matbot import config

logger = logging.getLogger("matbot.reporting_db")

# Jedini podržan provajder identiteta za sada. Postoji kao konstanta da se
# string ne bi prepisivao po pozivnim mjestima kad ih bude.
PROVIDER_THINKIFIC = "thinkific"

# Tabele koje izvještajna šema mora imati. Dijagnostika ih SAMO provjerava —
# ovaj modul nikad ne kreira ni ne migrira šemu.
REQUIRED_TABLES = (
    "students",
    "student_accounts",
    "learning_activity",
    "assessment_attempts",
    "assessment_item_results",
    "matbot_sessions",
    "instructor_notes",
    "monthly_reports",
    "sync_state",
    "schema_migrations",
)

# Gornje granice ulaza. Nisu sigurnosna kapija baze nego zaštita od toga da
# neispravan poziv upiše smeće u identitetsku tabelu koju kasnije niko ne može
# razriješiti.
# SQLite busy-timeout (sekunde). Vidi `_default_connect_factory`.
REPORTING_DB_BUSY_TIMEOUT_S = 5.0

MAX_PROVIDER_CHARS = 40
MAX_EXTERNAL_ID_CHARS = 200
MAX_DISPLAY_NAME_CHARS = 120
MIN_GRADE = 1
MAX_GRADE = 12


class ReportingUnavailable(RuntimeError):
    """Izvještajna operacija nije uspjela. `code` je INTERNI dijagnostički kod
    (isti princip kao validator kodovi iz CLAUDE.md, tačka 7) — nikad ne ide
    učeniku i nikad ne nosi vrijednost tajne."""

    def __init__(self, code, cause=None):
        super().__init__(code)
        self.code = code
        self.cause = cause


def _fingerprint(provider, external_user_id):
    """Nepovratan otisak vanjskog ID-ja za logove (tačka „ne loguj PII“).

    Obican SHA-256 ne bi bio dovoljan: Thinkific ID je mali cio broj, pa bi se
    heš trivijalno razbio nabrajanjem. HMAC s `FLASK_SECRET_KEY` to sprječava, a
    sam ključ nikad ne izlazi iz funkcije. Bez secreta (lokalni run) vraća se
    neutralan marker umjesto slabog heša."""
    secret = config.SECRET_KEY
    if not secret:
        return "nokey"
    material = f"{provider}\x00{external_user_id}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
    return digest[:12]


def fingerprint_subject(provider, external_user_id):
    """Javni ulaz u `_fingerprint` — drugi moduli logušu identitet ISTIM
    nepovratnim otiskom, da se isti učenik može pratiti kroz logove bez ijednog
    zapisa sirovog e-maila."""
    return _fingerprint(provider, external_user_id)


def _clean_provider(value):
    provider = (value or "").strip().lower()[:MAX_PROVIDER_CHARS]
    if not provider:
        raise ReportingUnavailable("invalid_provider")
    return provider


def _clean_external_id(value):
    # Namjerno `str(value)`: Thinkific ID stiže i kao broj i kao string, a u
    # bazi je jedan te isti nalog. Bez normalizacije bi 123 i "123" napravili
    # DVA identiteta uprkos UNIQUE ogranicenju.
    if value is None:
        raise ReportingUnavailable("invalid_external_user_id")
    external = str(value).strip()[:MAX_EXTERNAL_ID_CHARS]
    if not external:
        raise ReportingUnavailable("invalid_external_user_id")
    return external


def _clean_display_name(value):
    """Opciono. Ime NIJE identitet (ono se mijenja, ponavlja i piše različito) —
    služi samo da izvještaj bude čitljiv."""
    if not isinstance(value, str):
        return None
    name = value.strip()[:MAX_DISPLAY_NAME_CHARS]
    return name or None


def _clean_grade(value):
    """Opciono. Van razumnog raspona se TIHO odbacuje umjesto da se upiše —
    pogrešan razred u izvještaju je gori od nepoznatog."""
    try:
        grade = int(value)
    except (TypeError, ValueError):
        return None
    if grade < MIN_GRADE or grade > MAX_GRADE:
        return None
    return grade


def _rows(cursor):
    """Uvijek `fetchall()`, nikad `fetchone()`.

    IZMJERENO na libsql 0.1.11: nedovršeno pročitan `SELECT`/`RETURNING` ostavi
    izjavu „in progress“, pa sljedeći `commit()` padne s
    „cannot commit transaction - SQL statements in progress“. Potpuno
    ispražnjen kursor to ne može izazvati."""
    return cursor.fetchall()


class ReportingDatabase:
    """Tanak omotač oko jedne libSQL konekcije. Bez ORM-a, bez keširanja
    identiteta, bez ijednog upita van ovog fajla.

    `connect_factory` postoji radi testova (Dio 10): testovi ubace fabriku koja
    otvara LOKALNU datoteku/`:memory:` bazu istim klijentom, pa se ispituje
    stvarno ponašanje klijenta bez ijednog dodira sa živim Turso serverom.
    """

    def __init__(self, connect_factory=None):
        self._connect_factory = connect_factory or _default_connect_factory
        self._conn = None
        self._lock = threading.Lock()

    # -- konekcija ---------------------------------------------------------
    def _connection(self):
        if self._conn is None:
            try:
                conn = self._connect_factory()
            except ReportingUnavailable:
                raise
            except Exception as exc:
                raise ReportingUnavailable("reporting_db_connect_failed", exc) from None
            # DIO 9 — strano ključno ograničenje. Izmjereno: libsql 0.1.11 ga
            # drži UKLJUČENIM podrazumijevano (za razliku od stdlib `sqlite3`,
            # gdje je isključeno). Svejedno se postavlja izričito, jer se na
            # podrazumijevanu vrijednost tuđe biblioteke ne smije oslanjati
            # tabela koja veže `student_accounts.student_id` na `students.id`.
            try:
                conn.execute("PRAGMA foreign_keys = ON")
            except Exception as exc:
                self._discard(conn)
                raise ReportingUnavailable("reporting_db_pragma_failed", exc) from None
            self._conn = conn
        return self._conn

    @staticmethod
    def _discard(conn):
        try:
            conn.close()
        except Exception:
            pass

    def _drop_connection(self):
        """Prekinuta mreža ne smije trajno otrovati proces. `connect()` je kod
        libsql-a LIJEN (izmjereno: ne dira mrežu), pa je ponovno otvaranje
        jeftino i sljedeći poziv kreće čist."""
        conn, self._conn = self._conn, None
        if conn is not None:
            self._discard(conn)

    def close(self):
        with self._lock:
            self._drop_connection()

    # -- javni strogi sloj -------------------------------------------------
    def get_or_create_student(self, provider, external_user_id,
                              display_name=None, grade=None):
        """Vrati `students.id` za (provider, external_user_id); kreiraj pri prvom
        susretu. Baca `ReportingUnavailable`.

        ALGORITAM (Dio 5) i zašto baš ovaj:

          1. `SELECT` po nalogu — daleko najčešći slučaj (svaki zahtjev osim
             prvog), pa ide prvi i ne otvara transakciju upisa.
          2. Nema naloga → upiši `students`, pa `student_accounts` sa
             `ON CONFLICT(provider, external_user_id) DO NOTHING`.
          3. `rowcount == 1` → mi smo kreirali identitet, `COMMIT`.
          4. `rowcount == 0` → paralelni zahtjev je stigao prvi. `ROLLBACK`
             poništava i NAŠ spekulativni `students` red, pa ne ostaje siroče
             bez naloga; zatim se pročita pobjednikov `student_id`.

        ZAŠTO NE „provjeri pa upiši“: između provjere i upisa stane drugi
        zahtjev, a `students` nema nijedan UNIQUE ključ koji bi to zaustavio —
        dobila bi se DVA identiteta za istog učenika. Jedini pouzdan arbitar je
        UNIQUE(provider, external_user_id) u bazi, pa se odluka prepušta njemu.

        NAMJERNO SE NE PREPISUJU `display_name` i `grade` na postojećem redu:
        ovo je identitetska putanja, ne sinhronizacija profila (to je zasebna
        faza), a tiho prepisivanje bi napravilo upis pri SVAKOM zahtjevu."""
        provider = _clean_provider(provider)
        external = _clean_external_id(external_user_id)
        name = _clean_display_name(display_name)
        student_grade = _clean_grade(grade)

        with self._lock:
            try:
                return self._get_or_create_locked(provider, external, name, student_grade)
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                # TIP GREŠKE ULAZI U KOD. Goli „student_resolution_failed" je
                # jednom pao u punoj sviti i nije se imalo šta pogledati — log
                # je rekao DA je palo, ne ZAŠTO. Ime tipa nije tajna ni PII.
                raise ReportingUnavailable(
                    "student_resolution_failed:" + type(exc).__name__, exc) from None

    def _get_or_create_locked(self, provider, external, name, grade):
        conn = self._connection()

        existing = self._lookup(conn, provider, external)
        if existing is not None:
            self._touch_locked(conn, existing, provider, external)
            return existing

        cursor = conn.execute(
            "INSERT INTO students (display_name, grade, created_at, updated_at, last_seen_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (name, grade),
        )
        student_id = cursor.lastrowid

        linked = conn.execute(
            "INSERT INTO student_accounts "
            "(student_id, provider, external_user_id, created_at, last_seen_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT (provider, external_user_id) DO NOTHING",
            (student_id, provider, external),
        )
        # `lastrowid` se OVDJE ne smije čitati: izmjereno na libsql 0.1.11, kod
        # `DO NOTHING` grane ostaje zatečena (tuđa) vrijednost, pa bi se vratio
        # pogrešan identitet. `rowcount` je jedini pouzdan signal.
        if linked.rowcount == 1:
            conn.commit()
            return student_id

        conn.rollback()
        winner = self._lookup(conn, provider, external)
        if winner is None:
            # Nalog nije upisan, a ni ne postoji — ograničenje nije ono što
            # mislimo (npr. šema bez UNIQUE(provider, external_user_id)).
            raise ReportingUnavailable("student_identity_unresolved")
        self._touch_locked(conn, winner, provider, external)
        return winner

    def touch_last_seen(self, student_id, provider=None, external_user_id=None):
        """Osvježi `last_seen_at`. Baca `ReportingUnavailable`."""
        with self._lock:
            try:
                conn = self._connection()
                clean_provider = _clean_provider(provider) if provider else None
                clean_external = _clean_external_id(external_user_id) if external_user_id else None
                self._touch_locked(conn, int(student_id), clean_provider, clean_external)
                return True
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "student_touch_failed:" + type(exc).__name__, exc) from None

    def check(self):
        """SAMO ČITANJE — dijagnostika za CLI (Dio 11). Nikad ne piše, nikad ne
        migrira i nikad ne vraća URL ni token.

        Vraća rječnik s: dostupnošću, verzijom šeme, spiskom tabela koje
        nedostaju i stvarnim kolonama identitetskih tabela. Kolone se ispisuju
        namjerno: kod ne smije nagađati da li `students.status` ima DEFAULT —
        to se PROVJERI prije nego što se upis uključi."""
        with self._lock:
            try:
                conn = self._connection()
                _rows(conn.execute("SELECT 1"))
                tables = {row[0] for row in _rows(
                    conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"))}
                report = {
                    "configured": True,
                    "connected": True,
                    "foreign_keys_on": self._foreign_keys_on(conn),
                    "missing_tables": [t for t in REQUIRED_TABLES if t not in tables],
                    "schema_version": None,
                    "schema_version_error": None,
                    "expected_schema_version": config.REPORTING_SCHEMA_VERSION,
                    "columns": {},
                    "error": None,
                }
                for table in ("students", "student_accounts", "schema_migrations"):
                    if table in tables:
                        report["columns"][table] = [
                            row[1] for row in _rows(conn.execute(f"PRAGMA table_info({table})"))
                        ]
                if "schema_migrations" in tables:
                    try:
                        rows = _rows(conn.execute("SELECT MAX(version) FROM schema_migrations"))
                        report["schema_version"] = rows[0][0] if rows else None
                    except Exception as exc:
                        # Kolona se možda ne zove `version`. Ne nagađa se —
                        # prijavi se, a stvarne kolone su već gore.
                        report["schema_version_error"] = type(exc).__name__
                report["schema_version_matches"] = (
                    report["schema_version"] == config.REPORTING_SCHEMA_VERSION)
                return report
            except ReportingUnavailable as exc:
                self._drop_connection()
                return {"configured": True, "connected": False, "error": exc.code}
            except Exception as exc:
                self._drop_connection()
                return {"configured": True, "connected": False,
                        "error": "reporting_db_check_failed:" + type(exc).__name__}

    # -- interno -----------------------------------------------------------
    @staticmethod
    def _lookup(conn, provider, external):
        rows = _rows(conn.execute(
            "SELECT student_id FROM student_accounts "
            "WHERE provider = ? AND external_user_id = ?",
            (provider, external),
        ))
        return rows[0][0] if rows else None

    @staticmethod
    def _touch_locked(conn, student_id, provider, external):
        conn.execute(
            "UPDATE students SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?",
            (student_id,),
        )
        if provider and external:
            conn.execute(
                "UPDATE student_accounts SET last_seen_at = CURRENT_TIMESTAMP "
                "WHERE provider = ? AND external_user_id = ?",
                (provider, external),
            )
        conn.commit()

    @staticmethod
    def _foreign_keys_on(conn):
        rows = _rows(conn.execute("PRAGMA foreign_keys"))
        return bool(rows and rows[0][0])

    def _safe_rollback(self):
        conn = self._conn
        if conn is None:
            return
        try:
            conn.rollback()
        except Exception:
            pass


def _default_connect_factory():
    """Produkcijska konekcija na Turso. Uvoz `libsql`-a je LOKALAN da odsustvo
    paketa (ili neuspio uvoz) ne obori uvoz `matbot`-a i time cijelu aplikaciju
    — izvještavanje ne smije biti uslov za tutorski odgovor."""
    if not config.reporting_db_configured():
        raise ReportingUnavailable("reporting_db_not_configured")
    try:
        import libsql
    except Exception as exc:
        raise ReportingUnavailable("reporting_db_client_missing", exc) from None
    # `_check_same_thread=False` jer se poziv izvršava na radnom nitu s rokom
    # (vidi `_call_bounded`), a ne na nitu koja je konekciju otvorila. Istovremen
    # pristup je serijalizovan `ReportingDatabase._lock`-om.
    # `timeout` je SQLite busy-timeout (čekanje na zaključan lokalni fajl), NE
    # mrežni rok — mrežni rok drži `_call_bounded`. Za udaljeni Turso je bez
    # značaja jer istovremenost rješava server; navodi se IZRIČITO da se ne
    # oslanjamo na nedokumentovanu podrazumijevanu vrijednost tuđe biblioteke.
    return libsql.connect(
        config.turso_database_url(),
        auth_token=config.turso_auth_token(),
        timeout=REPORTING_DB_BUSY_TIMEOUT_S,
        _check_same_thread=False,
    )


# --- proces-globalni singleton + siguran sloj -------------------------------
_database = None
_database_lock = threading.Lock()
_inflight = None
_inflight_limit = None


def get_database():
    global _database
    with _database_lock:
        if _database is None:
            _database = ReportingDatabase()
        return _database


def set_database(database):
    """Ubaci bazu (testovi, dijagnostika). `None` vraća na podrazumijevanu."""
    global _database
    with _database_lock:
        if _database is not None and _database is not database:
            _database.close()
        _database = database


def shutdown():
    """Uredno gašenje (Dio 2). Nije obavezno — proces smije pasti i bez ovoga:
    radne niti su `daemon`, pa nijedna ne može zadržati izlaz interpretera."""
    global _inflight, _inflight_limit
    with _database_lock:
        if _database is not None:
            _database.close()
        _inflight = None
        _inflight_limit = None


def _get_inflight():
    """Semafor koji ograničava broj ISTOVREMENIH izvještajnih niti.

    Ponovo se gradi kad se promijeni granica (test ili izmjena konfiguracije),
    inače bi stara vrijednost tiho ostala na snazi do restarta."""
    global _inflight, _inflight_limit
    limit = max(1, config.REPORTING_DB_MAX_INFLIGHT)
    with _database_lock:
        if _inflight is None or _inflight_limit != limit:
            _inflight = threading.BoundedSemaphore(limit)
            _inflight_limit = limit
        return _inflight


def _call_bounded(operation, timeout_s):
    """Izvrši `operation` s TVRDIM rokom po pozivu (Dio 4).

    Rok se ne može tražiti od klijenta: libsql 0.1.11 ima `timeout` samo kao
    SQLite busy-timeout, ne kao mrežni rok. Zato se poziv izvršava na radnoj
    niti, a pozivalac čeka najviše `timeout_s`. Zaglavljen poziv NE zadržava
    tutorski turn — samo drži svoje mjesto dok ga sam ne oslobodi.

    DVIJE OGRANIČENE RESURSNE GRANICE, obje namjerne:

      • BROJ NITI — semafor se uzima NEBLOKIRAJUĆE, pa preopterećenje pada na
        `reporting_db_busy` umjesto da se stane u red. Zbog toga u procesu
        nikad ne može živjeti više od `REPORTING_DB_MAX_INFLIGHT` izvještajnih
        niti, ma koliko zahtjeva stiglo i ma koliko poziva visjelo.

      • GAŠENJE PROCESA — niti su `daemon=True` i namjerno se NE koristi
        `ThreadPoolExecutor`. Od Pythona 3.9 njegove niti nisu daemon i spaja
        ih atexit kuka, pa bi JEDAN trajno zaglavljen libsql poziv blokirao
        izlaz interpretera i pretvorio uredan gunicorn restart u SIGKILL nakon
        `--graceful-timeout`. Izvještavanje ne smije produžiti gašenje servisa
        ništa više nego što smije produžiti tutorski turn.

    Nema ponovnog pokušaja — tačno jedan. Vraća `(True, rezultat)` ili
    `(False, kod)`."""
    inflight = _get_inflight()
    if not inflight.acquire(blocking=False):
        return False, "reporting_db_busy"

    outcome = {}
    finished = threading.Event()

    def runner():
        try:
            outcome["value"] = operation()
        except ReportingUnavailable as exc:
            outcome["code"] = exc.code
        except Exception as exc:
            outcome["code"] = "reporting_db_error:" + type(exc).__name__
        finally:
            inflight.release()
            finished.set()

    threading.Thread(target=runner, name="matbot-reporting", daemon=True).start()

    if not finished.wait(timeout_s):
        return False, "reporting_db_timeout"
    if "code" in outcome:
        return False, outcome["code"]
    return True, outcome.get("value")


def resolve_student(provider, external_user_id, display_name=None, grade=None,
                    database=None):
    """SIGURAN ulaz: `students.id` ili `None`. NIKAD ne baca i nikad ne čeka
    duže od `config.REPORTING_DB_TIMEOUT_S`.

    `None` znači samo „izvještavanje trenutno ne radi“ — nikad da je učeniku
    nešto uskraćeno. Pozivalac (kad ga bude) mora nastaviti normalno."""
    if not config.reporting_db_configured() and database is None:
        # Bez konfiguracije nema ni loga po zahtjevu: to nije kvar nego stanje
        # (lokalni run, testovi, produkcija prije uključenja).
        return None
    target = database or get_database()
    try:
        provider_code = _clean_provider(provider)
    except ReportingUnavailable:
        logger.info("student_resolution_failed code=invalid_provider")
        return None

    ok, value = _call_bounded(
        lambda: target.get_or_create_student(provider, external_user_id,
                                             display_name=display_name, grade=grade),
        config.REPORTING_DB_TIMEOUT_S,
    )
    if ok:
        return value
    # Log nosi SAMO kod, provajder i nepovratan otisak — nikad ime, nikad ID,
    # nikad token ni URL.
    logger.info("student_resolution_failed code=%s provider=%s subject=%s",
                value, provider_code, _fingerprint(provider_code, external_user_id))
    return None


def touch_last_seen(student_id, provider=None, external_user_id=None, database=None):
    """SIGURAN ulaz: `True` ako je zabilježeno, inače `False`. Nikad ne baca."""
    if not config.reporting_db_configured() and database is None:
        return False
    target = database or get_database()
    ok, value = _call_bounded(
        lambda: target.touch_last_seen(student_id, provider, external_user_id),
        config.REPORTING_DB_TIMEOUT_S,
    )
    if ok:
        return bool(value)
    logger.info("reporting_db_unavailable code=%s operation=touch_last_seen", value)
    return False


def diagnose(database=None):
    """Dijagnostika za CLI. Bez konfiguracije ne pokušava konekciju.

    NIKAD NE BACA, iako je CLI ulaz: neuhvaćen izuzetak bi ispisao traceback, a
    poruka greške klijenta smije sadržati URL baze. Zato se i ovdje vraća samo
    IME tipa greške — nikad njen tekst."""
    if not config.reporting_db_configured() and database is None:
        return {"configured": False, "connected": False,
                "error": "reporting_db_not_configured"}
    try:
        return (database or get_database()).check()
    except ReportingUnavailable as exc:
        return {"configured": True, "connected": False, "error": exc.code}
    except Exception as exc:
        return {"configured": True, "connected": False,
                "error": "reporting_db_check_failed:" + type(exc).__name__}


def _format_report(report):
    lines = []
    lines.append("credentials_configured: %s" % ("yes" if report.get("configured") else "no"))
    lines.append("connection: %s" % ("ok" if report.get("connected") else "FAILED"))
    if report.get("error"):
        lines.append("error: %s" % report["error"])
        return "\n".join(lines)
    lines.append("foreign_keys: %s" % ("ON" if report.get("foreign_keys_on") else "OFF"))
    lines.append("schema_version: %s (expected %s) -> %s" % (
        report.get("schema_version"),
        report.get("expected_schema_version"),
        "OK" if report.get("schema_version_matches") else "MISMATCH",
    ))
    if report.get("schema_version_error"):
        lines.append("schema_version_error: %s" % report["schema_version_error"])
    missing = report.get("missing_tables") or []
    lines.append("missing_tables: %s" % (", ".join(missing) if missing else "none"))
    for table, columns in sorted((report.get("columns") or {}).items()):
        lines.append("columns[%s]: %s" % (table, ", ".join(columns)))
    return "\n".join(lines)


def main(argv=None):
    """`python -m matbot.reporting_db --check`

    SAMO ČITANJE i SAMO CLI — dijagnostika se namjerno NE izlaže kroz
    `/healthz` (javni endpoint ne smije otkrivati stanje baze). Izlazni kod 0
    znači: konekcija radi, sve tabele postoje i verzija šeme se poklapa."""
    import argparse
    import sys

    # TEKST OVOG POMOĆNIKA JE NAMJERNO BEZ DIJAKRITIKA (isti izbor kao
    # deploy/apply_release_env.sh): argparse ga piše na stdout, a konzola koja
    # nije UTF-8 (Windows cp1252) na `č` baca UnicodeEncodeError. Dijagnostika
    # koja pukne pri ispisu pomoći nije dijagnostika. Komentari i docstringovi
    # ostaju s dijakriticima — oni se nikad ne ispisuju.
    parser = argparse.ArgumentParser(
        prog="python -m matbot.reporting_db",
        description="Provjera izvjestajne baze (samo citanje, ne mijenja podatke).")
    parser.add_argument("--check", action="store_true",
                        help="provjeri kredencijale, konekciju, tabele i verziju seme")
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 0

    report = diagnose()
    print(_format_report(report))
    healthy = (
        report.get("connected")
        and not report.get("missing_tables")
        and report.get("schema_version_matches")
    )
    return 0 if healthy else 1


if __name__ == "__main__":  # pragma: no cover - CLI ulaz
    import sys

    sys.exit(main())
