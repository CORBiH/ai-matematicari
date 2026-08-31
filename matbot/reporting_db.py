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
import time

from matbot import config

logger = logging.getLogger("matbot.reporting_db")

# Jedini podržan provajder identiteta za sada. Postoji kao konstanta da se
# string ne bi prepisivao po pozivnim mjestima kad ih bude.
PROVIDER_THINKIFIC = "thinkific"

# Vrijednost `learning_activity.source` za sve događaje koje piše MAT-BOT.
# Drži se ovdje jer je dio UNIQUE(source, event_key) ugovora baze.
SOURCE = "matbot"

# Faza 3D: registar i povezivanje naloga rade nad ISTIM providerom kao Faza 1 —
# nikad nov prostor imena. Drži se ovdje da se string ne bi prepisivao.
_THINKIFIC_PROVIDER = "thinkific_email"

# Razredi koje ručni upis smije primiti. Osnovna škola 6–9 (BiH).
VALID_MANUAL_GRADES = (6, 7, 8, 9)

# IZVOR POTVRDE RAZREDA (šema v4). Vrijednosti se preuzimaju iz
# `student_grades`, gdje živi i pravilo šta se smije čitati kao potvrđeno —
# dvije kopije istog stringa bi se razišle prvom izmjenom. Uvoz je lokalan u
# ovom smjeru namjerno: `student_grades` ne uvozi `reporting_db`, pa nema kruga.
from matbot.student_grades import (GRADE_SOURCE_ADMIN,          # noqa: E402
                                   GRADE_SOURCE_MANUAL_CREATION)

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


def _batches(values, size):
    """Podijeli na komade — `IN (...)` i višeredni `VALUES` dijele istu granicu
    broja parametara koju SQLite postavlja (999)."""
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


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
        # Ima li baza kolone potvrde razreda (šema v4)? `None` = još nije
        # provjereno. Vidi `_grade_confirmation_available`.
        self._grade_confirmation = None

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
        # Zapamćena sposobnost pripada KONEKCIJI, ne procesu: poslije migracije
        # se konekcija ionako obnavlja, pa se i odgovor mora ponovo izmjeriti.
        self._grade_confirmation = None
        if conn is not None:
            self._discard(conn)

    def close(self):
        with self._lock:
            self._drop_connection()

    # -- potvrda tekućeg razreda (šema v4) ---------------------------------
    def _grade_confirmation_available(self, conn):
        """Postoje li `grade_confirmed_at` i `grade_source` na `students`?

        ZAŠTO SE UOPŠTE PITA, umjesto da se pretpostavi: raspoređivanje pušta
        migraciju u zasebnom kontejneru PRIJE zamjene aplikacije, ali baza koja
        iz bilo kog razloga zaostane ne smije proizvesti neuhvaćen izuzetak
        usred administratorske stranice. Odgovor `False` je BEZBJEDAN ISHOD:
        nijedan razred se tada ne čita kao potvrđen, pa unos časa i novi
        izvještaj padaju zatvoreno — tačno ono što želimo od nepoznatog stanja.

        Mjeri se JEDNOM po konekciji: `PRAGMA table_info` je jeftin, ali ne po
        svakom redu registra."""
        if self._grade_confirmation is None:
            try:
                columns = {row[1] for row in _rows(
                    conn.execute("PRAGMA table_info(students)"))}
            except Exception:
                return False
            self._grade_confirmation = ("grade_confirmed_at" in columns
                                        and "grade_source" in columns)
        return self._grade_confirmation

    # -- javni strogi sloj -------------------------------------------------
    def get_or_create_student(self, provider, external_user_id,
                              display_name=None):
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

        NAMJERNO SE NE PREPISUJE `display_name` na postojećem redu: ovo je
        identitetska putanja, ne sinhronizacija profila (to je zasebna faza), a
        tiho prepisivanje bi napravilo upis pri SVAKOM zahtjevu.

        RAZRED SE OVDJE NE UPISUJE UOPŠTE, ni pri prvom susretu (verzija 4).
        Ranije je identitet primao `grade` i upisivao ga u `students.grade` —
        a jedini izvor te vrijednosti je bio padajući meni tutora, u kojem je
        `6` bila unaprijed izabrana opcija. Tako je 34 učenika trajno dobilo
        šesti razred, a Faza 3D je po tom polju birala kurikulum. Nov učenik
        zato nastaje s `grade = NULL` i čeka da ga administrator POTVRDI."""
        provider = _clean_provider(provider)
        external = _clean_external_id(external_user_id)
        name = _clean_display_name(display_name)

        with self._lock:
            try:
                return self._get_or_create_locked(provider, external, name)
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

    def _get_or_create_locked(self, provider, external, name):
        conn = self._connection()

        existing = self._lookup(conn, provider, external)
        if existing is not None:
            self._touch_locked(conn, existing, provider, external)
            return existing

        # `grade` se NE navodi: nov identitet nema potvrđen tekući razred i
        # kolona ostaje NULL dok administrator ne odluči.
        cursor = conn.execute(
            "INSERT INTO students (display_name, created_at, updated_at, last_seen_at) "
            "VALUES (?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (name,),
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

    def record_learning_activity(self, student_id, events):
        """Upiši dokazane događaje u `learning_activity`. Baca `ReportingUnavailable`.

        IDEMPOTENTNO PO KONSTRUKCIJI: `ON CONFLICT (source, event_key) DO NOTHING`
        prepušta odluku BAZI, a ne provjeri u Pythonu. Dvostruka HTTP isporuka,
        retry pregledača ili ponovljena predaja testa zato ne mogu napraviti dva
        reda — isti ključ, isti red.

        `duration_seconds` i `progress_percent` se NAMJERNO ne upisuju: MAT-BOT
        ih trenutno ne mjeri, a izmišljena nula bi u izvještaju izgledala kao
        mjerenje. Ostaju NULL dok stvarno mjerenje ne postoji.

        Vraća broj STVARNO upisanih redova (duplikat je 0)."""
        if not events:
            return 0
        with self._lock:
            try:
                conn = self._connection()
                written = 0
                for event in events:
                    cursor = conn.execute(
                        "INSERT INTO learning_activity "
                        "(student_id, source, event_type, event_key, grade, "
                        " area_name, lesson_id, lesson_name, mode, occurred_at, "
                        " metadata_json) "
                        # `occurred_at` dolazi IZ DOGAĐAJA (uhvaćen kad je
                        # činjenica dokazana), ne iz `CURRENT_TIMESTAMP`:
                        # asinhroni upis ne smije određivati kad se nešto desilo.
                        # `COALESCE` je samo pojas za slučaj da pozivalac ne
                        # pošalje vrijeme — tada je vrijeme upisa jedino što
                        # imamo, i to je poštenije od NULL-a.
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                        "        COALESCE(?, CURRENT_TIMESTAMP), ?) "
                        "ON CONFLICT (source, event_key) DO NOTHING",
                        (int(student_id), SOURCE, event.event_type, event.event_key,
                         event.grade, event.area_name or None,
                         event.lesson_id or None, event.lesson_name or None,
                         event.mode or None,
                         getattr(event, "occurred_at", None),
                         event.metadata_json()),
                    )
                    written += 1 if cursor.rowcount == 1 else 0
                conn.commit()
                return written
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "activity_write_failed:" + type(exc).__name__, exc) from None

    def record_assessment_generated(self, student_id, attempt):
        """Zabilježi da je test GENERISAN i predat učeniku. Nikad ne briše rezultat.

        NEDESTRUKTIVNO PO KONSTRUKCIJI (Dio 5): upis dolazi asinhrono, pa se
        teorijski može desiti POSLIJE upisa ocjene istog testa. Zato `DO UPDATE`
        dira samo polja generisanja i to kroz `COALESCE` — `score_percent`,
        `correct_count` i `completed_at` NISU ni navedeni, pa ih ova putanja ne
        može dodirnuti ni slučajno.

        Obrnut smjer je time takođe pokriven: ako je ocjena stigla prva i
        napravila red bez `started_at`, ovaj upis ga naknadno POPUNI umjesto da
        ostane prazan zauvijek."""
        with self._lock:
            try:
                conn = self._connection()
                conn.execute(
                    "INSERT INTO assessment_attempts "
                    "(student_id, source, assessment_type, external_attempt_id, "
                    " grade, area_name, total_count, started_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source, external_attempt_id) DO UPDATE SET "
                    # NAJRANIJE VRIJEME POBJEĐUJE. `MIN` (a ne samo `COALESCE`)
                    # znači da ponovljena isporuka istog generisanja ne može
                    # pomjeriti početak unaprijed, a zakašnjeli upis popunjava
                    # `started_at` koji ocjena nije znala.
                    "  started_at  = MIN(COALESCE(assessment_attempts.started_at, "
                    "                             excluded.started_at), "
                    "                    excluded.started_at), "
                    "  grade       = COALESCE(assessment_attempts.grade, excluded.grade), "
                    "  area_name   = COALESCE(assessment_attempts.area_name, "
                    "                         excluded.area_name), "
                    "  total_count = COALESCE(assessment_attempts.total_count, "
                    "                         excluded.total_count)",
                    (int(student_id), SOURCE, attempt["assessment_type"],
                     attempt["external_attempt_id"], attempt.get("grade"),
                     attempt.get("area_name") or None, attempt.get("total_count"),
                     attempt.get("started_at")),
                )
                conn.commit()
                return True
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "assessment_generated_failed:" + type(exc).__name__, exc) from None

    def record_assessment_completed(self, student_id, attempt, items):
        """Ocijenjen test: JEDAN `assessment_attempts` red + N `assessment_item_results`.

        ATOMIČNOST (Dio 9): sve izjave idu kroz JEDNU implicitnu transakciju ove
        konekcije i tek onda `commit()`. Izmjereno na libsql 0.1.11: prvi DML
        otvara transakciju, a `rollback()` stvarno poništava upis — pa pad na
        petom pitanju ne ostavlja polovično upisan test. Eksplicitni
        `BEGIN IMMEDIATE` se NAMJERNO ne piše: klijent već vodi transakciju i
        ručni `BEGIN` bi se sudario s njom.

        UPSERT po `(source, external_attempt_id)` znači da ponovljena predaja ne
        pravi drugi pokušaj, a `COALESCE` na `completed_at` čuva PRVO vrijeme
        završetka umjesto da ga pomjera pri svakoj ponovljenoj isporuci."""
        with self._lock:
            try:
                conn = self._connection()
                conn.execute(
                    "INSERT INTO assessment_attempts "
                    "(student_id, source, assessment_type, external_attempt_id, "
                    " grade, area_name, score_percent, correct_count, total_count, "
                    " completed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source, external_attempt_id) DO UPDATE SET "
                    "  score_percent = excluded.score_percent, "
                    "  correct_count = excluded.correct_count, "
                    "  total_count   = excluded.total_count, "
                    "  grade         = COALESCE(assessment_attempts.grade, excluded.grade), "
                    "  area_name     = COALESCE(assessment_attempts.area_name, "
                    "                           excluded.area_name), "
                    "  completed_at  = COALESCE(assessment_attempts.completed_at, "
                    "                           excluded.completed_at)",
                    (int(student_id), SOURCE, attempt["assessment_type"],
                     attempt["external_attempt_id"], attempt.get("grade"),
                     attempt.get("area_name") or None, attempt.get("score_percent"),
                     attempt.get("correct_count"), attempt.get("total_count"),
                     attempt.get("completed_at")),
                )
                # `lastrowid` se NE čita: izmjereno na libsql 0.1.11, na
                # konfliktnoj grani ostaje zatečena tuđa vrijednost. Jedini
                # pouzdan put do ID-ja je čitanje po prirodnom ključu.
                found = _rows(conn.execute(
                    "SELECT id FROM assessment_attempts "
                    "WHERE source = ? AND external_attempt_id = ?",
                    (SOURCE, attempt["external_attempt_id"])))
                if not found:
                    raise ReportingUnavailable("assessment_attempt_unresolved")
                attempt_id = found[0][0]

                for item in items or ():
                    conn.execute(
                        "INSERT INTO assessment_item_results "
                        "(attempt_id, item_key, ordinal, area_name, lesson_id, "
                        " lesson_name, difficulty, is_correct, hints_used) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0) "
                        "ON CONFLICT (attempt_id, item_key) DO NOTHING",
                        (attempt_id, item["item_key"], item.get("ordinal"),
                         item.get("area_name") or None, item.get("lesson_id") or None,
                         item.get("lesson_name") or None,
                         item.get("difficulty") or None,
                         1 if item.get("is_correct") else 0),
                    )
                conn.commit()
                return attempt_id
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "assessment_completed_failed:" + type(exc).__name__, exc) from None

    def find_student(self, provider, external_user_id):
        """`students.id` za postojeci nalog, ili `None`. NE KREIRA nista.

        Postoji da uvoz moze razlikovati NOV od POSTOJECEG ucenika bez drugog
        upisa: `get_or_create_student` sam ne kaze koji je slucaj bio."""
        with self._lock:
            try:
                conn = self._connection()
                return self._lookup(conn, _clean_provider(provider),
                                    _clean_external_id(external_user_id))
            except ReportingUnavailable:
                self._drop_connection()
                raise
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "student_lookup_failed:" + type(exc).__name__, exc) from None

    # --- FAZA 3A: Thinkific snimci napretka --------------------------------
    def update_student_profile(self, student_id, display_name=None):
        """Konzervativno dopuni IME u profilu. Vrati `name_set`.

        PROFIL NIJE IDENTITET. `students.display_name` i `students.grade` su
        pogodnost za izvještaj; identitet je i dalje isključivo
        `student_accounts(provider, external_user_id)`.

        PRAVILA (Dio 7 i 8):
          • ime se upisuje SAMO ako je novo neprazno i staro je prazno/NULL —
            prazno ime iz izvoza nikad ne smije obrisati korisnu vrijednost;
          • RAZRED SE NE DIRA. Parametar `grade` je od verzije 4 UKLONJEN, a ne
            ignorisan: dok je postojao, sinhronizacija profila je smjela upisati
            razred kursa kao tekući školski razred. Tekući razred mijenja
            isključivo `set_student_grade`, iz administratorske akcije."""
        with self._lock:
            try:
                conn = self._connection()
                rows = _rows(conn.execute(
                    "SELECT display_name FROM students WHERE id = ?",
                    (int(student_id),)))
                if not rows:
                    raise ReportingUnavailable("student_missing")
                existing_name = rows[0][0]

                name_set = False
                if display_name and not (existing_name or "").strip():
                    conn.execute(
                        "UPDATE students SET display_name = ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (display_name, int(student_id)))
                    name_set = True
                conn.commit()
                return name_set
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "profile_update_failed:" + type(exc).__name__, exc) from None

    def record_progress_import(self, *, report_month, course_key, course_name,
                               grade, source_sha256, row_count):
        """Revizijski red o UČITANOM FAJLU. Vraća `import_id`.

        Pamti se SAMO hash bajtova, nikad sam CSV: fajl je ulazni materijal, a
        ne podatak koji izvještaj treba."""
        with self._lock:
            try:
                conn = self._connection()
                cursor = conn.execute(
                    "INSERT INTO thinkific_progress_imports "
                    "(report_month, course_key, course_name, grade, source_sha256, "
                    " row_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (report_month, course_key, course_name, int(grade),
                     source_sha256, int(row_count)))
                import_id = cursor.lastrowid
                conn.commit()
                return import_id
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "progress_import_failed:" + type(exc).__name__, exc) from None

    def upsert_progress_snapshot(self, *, import_id, student_id, report_month,
                                 course_key, course_name, grade, percent_viewed,
                                 percent_completed, started_at, completed_at,
                                 activated_at, expires_at, last_sign_in, sections):
        """Jedno STANJE po (učenik, kurs, mjesec) + njegove sekcije. ATOMIČNO.

        Vraća `("inserted"|"updated", snapshot_id, broj_sekcija)`.

        IDEMPOTENTNOST JE NA STANJU, NE NA HASHU (Dio 14): ključ je
        `UNIQUE(student_id, course_key, report_month)`. Isti fajl dva puta ne
        pravi drugi red; NOVIJI izvoz ISTOG mjeseca deterministički prepisuje
        stanje, jer je mjesečni izvještaj slika najsvježijeg poznatog napretka.

        SEKCIJE SE ZAMJENJUJU U CJELINI, ne spajaju: kurikulum se mijenja
        (sekcija se doda ili ukloni), pa bi spajanje ostavilo duhove sekcija
        kojih u kursu više nema. Brisanje + upis idu u ISTOJ transakciji sa
        snimkom, pa pad usred zamjene vraća prethodno valjano stanje umjesto da
        ostavi test bez sekcija."""
        with self._lock:
            try:
                conn = self._connection()
                existing = _rows(conn.execute(
                    "SELECT id FROM thinkific_progress_snapshots "
                    "WHERE student_id = ? AND course_key = ? AND report_month = ?",
                    (int(student_id), course_key, report_month)))
                outcome = "updated" if existing else "inserted"

                conn.execute(
                    "INSERT INTO thinkific_progress_snapshots "
                    "(import_id, student_id, report_month, course_key, course_name, "
                    " grade, percent_viewed, percent_completed, started_at, "
                    " completed_at, activated_at, expires_at, last_sign_in) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (student_id, course_key, report_month) DO UPDATE SET "
                    "  import_id = excluded.import_id, "
                    "  course_name = excluded.course_name, "
                    "  grade = excluded.grade, "
                    "  percent_viewed = excluded.percent_viewed, "
                    "  percent_completed = excluded.percent_completed, "
                    "  started_at = excluded.started_at, "
                    "  completed_at = excluded.completed_at, "
                    "  activated_at = excluded.activated_at, "
                    "  expires_at = excluded.expires_at, "
                    "  last_sign_in = excluded.last_sign_in",
                    (int(import_id), int(student_id), report_month, course_key,
                     course_name, int(grade), percent_viewed, percent_completed,
                     started_at, completed_at, activated_at, expires_at, last_sign_in))

                found = _rows(conn.execute(
                    "SELECT id FROM thinkific_progress_snapshots "
                    "WHERE student_id = ? AND course_key = ? AND report_month = ?",
                    (int(student_id), course_key, report_month)))
                if not found:
                    raise ReportingUnavailable("snapshot_unresolved")
                snapshot_id = found[0][0]

                conn.execute(
                    "DELETE FROM thinkific_progress_sections WHERE snapshot_id = ?",
                    (snapshot_id,))
                written = 0
                for ordinal, name, percent in sections or ():
                    conn.execute(
                        "INSERT INTO thinkific_progress_sections "
                        "(snapshot_id, ordinal, section_name, progress_percent) "
                        "VALUES (?, ?, ?, ?)",
                        (snapshot_id, ordinal, name, percent))
                    written += 1
                conn.commit()
                return outcome, snapshot_id, written
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "snapshot_upsert_failed:" + type(exc).__name__, exc) from None

    # --- PAKETNI UVOZ JEDNOG THINKIFIC FAJLA -------------------------------
    # IZMJERENO (34 učenika, 7 sekcija, sintetički fajl oblika stvarnog izvoza):
    # red-po-red put je radio ~580 SQL izjava i **103 commita**, dakle ~683
    # mrežna obrta po fajlu. Na udaljenom Tursu svaki obrt je puna mrežna tura, a
    # commit je najskuplji jer mora trajno potvrditi upis — otud „uvoz traje
    # vječno" iako u njemu nema nijednog modela.
    #
    # Uzrok NIJE bio jedan spor upit nego ARHITEKTURA POZIVA: `find_student`,
    # `get_or_create_student`, `update_student_profile` i `upsert_progress_snapshot`
    # su četiri odvojene javne metode, svaka sa svojim `_lock`-om i svojim
    # `commit()`-om, pa je jedan učenik plaćao tri commita.
    #
    # Ova metoda radi ISTI posao u JEDNOJ transakciji i s JEDNIM commitom:
    #   • unaprijed dovuče postojeće naloge, profile i snimke za CIJELI fajl
    #     (tri `IN (...)` upita umjesto po jednog po učeniku);
    #   • upiše samo ono što stvarno nedostaje ili se stvarno mijenja;
    #   • sekcije briše JEDNIM `DELETE ... IN (...)` i upisuje jednim
    #     `executemany`.
    #
    # SEMANTIKA JE NEPROMIJENJENA: isti identitet, ista pravila profila, isti
    # `UNIQUE(student_id, course_key, report_month)`, ista potpuna zamjena skupa
    # sekcija, isto ponašanje kod sukoba razreda.
    #
    # JEDNA GARANCIJA JE ČAK JAČA: ranije je pad baze na 20. redu ostavljao
    # prvih 19 učenika UPISANIH (svaki je imao svoj commit). Sada je cijeli fajl
    # jedna transakcija, pa pad ne ostavlja ništa — što je tačno ono što ugovor
    # „jedan neispravan red odbija cijeli fajl" i traži.
    def import_progress_file(self, *, report_month, course_key, course_name, grade,
                             source_sha256, provider, rows):
        """Uvezi CIJELI provjereni fajl u jednoj transakciji. Vrati brojače.

        `rows` su već isparsirani i validirani redovi (`matbot/thinkific_progress.py`);
        ovdje se ne parsira ništa i ne donosi nijedna kurikularna odluka."""
        counters = {"students_created": 0, "students_reused": 0,
                    "snapshots_inserted": 0, "snapshots_updated": 0,
                    "sections_written": 0, "grade_conflicts": 0}
        if not rows:
            return counters

        with self._lock:
            try:
                conn = self._connection()
                cursor = conn.execute(
                    "INSERT INTO thinkific_progress_imports "
                    "(report_month, course_key, course_name, grade, source_sha256, "
                    " row_count) VALUES (?, ?, ?, ?, ?, ?)",
                    (report_month, course_key, course_name, int(grade),
                     source_sha256, len(rows)))
                import_id = cursor.lastrowid

                emails = [row["email"] for row in rows]
                accounts = self._existing_accounts(conn, provider, emails)

                # Novi učenici: `students` pa `student_accounts`. Ne može se
                # grupno jer svakom treba njegov `id`, ali NEMA commita po
                # učeniku — sve ostaje u istoj transakciji.
                for row in rows:
                    if row["email"] in accounts:
                        counters["students_reused"] += 1
                        continue
                    # RAZRED KURSA NIJE TEKUĆI RAZRED UČENIKA (verzija 4).
                    # Šesti razred u ovom fajlu znači „koristi gradivo šestog
                    # razreda", a ne „pohađa šesti razred" — forenzika je
                    # dokazala da u izvozu kursa 6. razreda legitimno rade i
                    # sedmaci. Nov učenik zato nastaje s `grade = NULL`; razred
                    # sadržaja i dalje ide u snimak, gdje mu je i mjesto.
                    created = conn.execute(
                        "INSERT INTO students (display_name, created_at, "
                        " updated_at, last_seen_at) VALUES (?, CURRENT_TIMESTAMP, "
                        " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                        (row["display_name"],))
                    student_id = created.lastrowid
                    conn.execute(
                        "INSERT INTO student_accounts (student_id, provider, "
                        " external_user_id, created_at, last_seen_at) "
                        "VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                        (student_id, provider, row["email"]))
                    accounts[row["email"]] = student_id
                    counters["students_created"] += 1

                student_ids = [accounts[row["email"]] for row in rows]
                # `last_seen_at` se osvježava kao i do sada — samo grupno.
                self._touch_students(conn, provider, student_ids, emails)

                profiles = self._existing_profiles(conn, student_ids)
                self._apply_profiles(conn, rows, accounts, profiles, grade, counters)

                snapshots = self._existing_snapshots(conn, course_key, report_month,
                                                     student_ids)
                sections = self._apply_snapshots(conn, rows, accounts, snapshots,
                                                 import_id, report_month, course_key,
                                                 course_name, grade, counters)
                self._replace_sections(conn, sections, counters)

                conn.commit()          # JEDINI commit za cijeli fajl
                return counters
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "progress_import_failed:" + type(exc).__name__, exc) from None

    # `IN (...)` se dijeli na komade: SQLite ima gornju granicu broja parametara,
    # a jedan razred u BiH školi je daleko ispod nje — komadanje postoji da uvoz
    # cijele škole ne pukne na granici, ne zato što je danas potrebno.
    _IN_CHUNK = 200
    # Četiri parametra po sekciji: 200 × 4 = 800 < 999.
    _ROWS_PER_INSERT = 200

    @staticmethod
    def _chunks(values):
        return _batches(values, ReportingDatabase._IN_CHUNK)

    def _existing_accounts(self, conn, provider, emails):
        """{e-mail: students.id} za sve adrese iz fajla — nekoliko upita ukupno."""
        found = {}
        for chunk in self._chunks(emails):
            placeholders = ",".join("?" * len(chunk))
            rows = _rows(conn.execute(
                "SELECT external_user_id, student_id FROM student_accounts "
                "WHERE provider = ? AND external_user_id IN (%s)" % placeholders,
                (provider, *chunk)))
            for email, student_id in rows:
                found[email] = student_id
        return found

    def _touch_students(self, conn, provider, student_ids, emails):
        for chunk in self._chunks(student_ids):
            placeholders = ",".join("?" * len(chunk))
            conn.execute(
                "UPDATE students SET last_seen_at = CURRENT_TIMESTAMP "
                "WHERE id IN (%s)" % placeholders, tuple(chunk))
        for chunk in self._chunks(emails):
            placeholders = ",".join("?" * len(chunk))
            conn.execute(
                "UPDATE student_accounts SET last_seen_at = CURRENT_TIMESTAMP "
                "WHERE provider = ? AND external_user_id IN (%s)" % placeholders,
                (provider, *chunk))

    def _existing_profiles(self, conn, student_ids):
        profiles = {}
        for chunk in self._chunks(student_ids):
            placeholders = ",".join("?" * len(chunk))
            for student_id, name, grade in _rows(conn.execute(
                    "SELECT id, display_name, grade FROM students "
                    "WHERE id IN (%s)" % placeholders, tuple(chunk))):
                profiles[student_id] = (name, grade)
        return profiles

    def _apply_profiles(self, conn, rows, accounts, profiles, grade, counters):
        """Dopuni SAMO ime. Razred se od verzije 4 NE dira ni pod kojim uslovom.

        ŠTA JE UKLONJENO I ZAŠTO: ranije se `grade` kursa upisivao u
        `students.grade` kad je zatečena vrijednost bila NULL. To je bila tiha
        tvrdnja da je razred KURSA ujedno i tekući školski razred učenika —
        a produkcijska forenzika je dokazala suprotno: augustovski izvoz je
        stvarno kurs šestog razreda, a u njemu legitimno rade i sedmaci koji
        obnavljaju gradivo. Tekući razred od sada potvrđuje isključivo čovjek.

        `grade_conflicts` OSTAJE, ali mu je značenje sada RAZLIKA SADRŽAJA:
        koliko učenika ima potvrđen profil različit od razreda uvezenog kursa.
        To je korisna informacija (može otkriti pogrešno izabran slot), ali NIJE
        greška i ne pokreće nikakvu izmjenu.

        Ime se i dalje upisuje samo preko praznog — prazno ime iz izvoza nikad
        ne smije obrisati korisnu vrijednost."""
        for row in rows:
            student_id = accounts[row["email"]]
            existing_name, existing_grade = profiles.get(student_id, (None, None))
            if row["display_name"] and not (existing_name or "").strip():
                conn.execute(
                    "UPDATE students SET display_name = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["display_name"], student_id))
                profiles[student_id] = (row["display_name"], existing_grade)
            if (grade is not None and existing_grade is not None
                    and int(existing_grade) != int(grade)):
                counters["grade_conflicts"] += 1

    def _existing_snapshots(self, conn, course_key, report_month, student_ids):
        found = {}
        for chunk in self._chunks(student_ids):
            placeholders = ",".join("?" * len(chunk))
            for student_id, snapshot_id in _rows(conn.execute(
                    "SELECT student_id, id FROM thinkific_progress_snapshots "
                    "WHERE course_key = ? AND report_month = ? "
                    "AND student_id IN (%s)" % placeholders,
                    (course_key, report_month, *chunk))):
                found[student_id] = snapshot_id
        return found

    def _apply_snapshots(self, conn, rows, accounts, snapshots, import_id,
                         report_month, course_key, course_name, grade, counters):
        """Vrati [(snapshot_id, ordinal, naziv, procenat)] za sve sekcije fajla.

        `lastrowid` se čita SAMO poslije stvarnog `INSERT`-a (nema konfliktne
        grane), jer je na konfliktu izmjereno nepouzdan.

        DVA REDA S ISTOM ADRESOM U ISTOM FAJLU: skup sekcija se DRŽI PO SNIMKU,
        pa POSLJEDNJI red pobjeđuje u cjelini — tačno kao raniji red-po-red put,
        koji je za svaki red brisao pa ponovo upisivao sekcije tog snimka
        (izmjereno na roditeljskom komitu). Ravna lista bi ovdje nagomilala oba
        reda i oborila `UNIQUE(snapshot_id, ordinal)`, dakle promijenila bi
        ponašanje iz „prihvaćeno" u „fajl odbijen"."""
        pending = {}
        for row in rows:
            student_id = accounts[row["email"]]
            values = (row["percent_viewed"], row["percent_completed"],
                      row["started_at"], row["completed_at"], row["activated_at"],
                      row["expires_at"], row["last_sign_in"])
            snapshot_id = snapshots.get(student_id)
            if snapshot_id is None:
                created = conn.execute(
                    "INSERT INTO thinkific_progress_snapshots "
                    "(import_id, student_id, report_month, course_key, course_name, "
                    " grade, percent_viewed, percent_completed, started_at, "
                    " completed_at, activated_at, expires_at, last_sign_in) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (import_id, student_id, report_month, course_key, course_name,
                     int(grade), *values))
                snapshot_id = created.lastrowid
                snapshots[student_id] = snapshot_id
                counters["snapshots_inserted"] += 1
            else:
                conn.execute(
                    "UPDATE thinkific_progress_snapshots SET import_id = ?, "
                    " course_name = ?, grade = ?, percent_viewed = ?, "
                    " percent_completed = ?, started_at = ?, completed_at = ?, "
                    " activated_at = ?, expires_at = ?, last_sign_in = ? "
                    "WHERE id = ?",
                    (import_id, course_name, int(grade), *values, snapshot_id))
                counters["snapshots_updated"] += 1
            pending[snapshot_id] = [(snapshot_id, ordinal, name, percent)
                                    for ordinal, name, percent in row["sections"]]
        return [entry for group in pending.values() for entry in group]

    def _replace_sections(self, conn, sections, counters):
        """Potpuna zamjena skupa sekcija: JEDAN `DELETE` i JEDAN `executemany`.

        Djelimičan skup ne može postati vidljiv jer je sve u istoj transakciji
        kao i snimak."""
        snapshot_ids = sorted({row[0] for row in sections})
        for chunk in self._chunks(snapshot_ids):
            placeholders = ",".join("?" * len(chunk))
            conn.execute(
                "DELETE FROM thinkific_progress_sections "
                "WHERE snapshot_id IN (%s)" % placeholders, tuple(chunk))
        # VIŠEREDNI `VALUES`, NE `executemany`: oba rade, ali `executemany` ne
        # garantuje da je na Hrani jedna mrežna tura — mogao bi poslati 238
        # zasebnih izjava. Ovako je broj izjava DOKAZIV: 238 sekcija = 2 izjave.
        # `_ROWS_PER_INSERT` × 4 parametra ostaje ispod SQLite granice od 999.
        for chunk in _batches(sections, self._ROWS_PER_INSERT):
            values = ",".join(["(?, ?, ?, ?)"] * len(chunk))
            conn.execute(
                "INSERT INTO thinkific_progress_sections "
                "(snapshot_id, ordinal, section_name, progress_percent) "
                "VALUES " + values,
                tuple(field for row in chunk for field in row))
        counters["sections_written"] = len(sections)

    # --- FAZA 3C: sačuvani mjesečni izvještaj -------------------------------
    # JEDAN red po (učenik, mjesec). Upsert je SELECT pa INSERT/UPDATE u istoj
    # transakciji, a ne `ON CONFLICT`, jer se na produkcijskoj `monthly_reports`
    # ne smije pretpostaviti da UNIQUE(student_id, report_month) postoji —
    # tabelu ovaj repo nikad nije kreirao (vidi `reporting_schema`).
    #
    # `metrics_json` je SNIMAK činjenica od kojih je izvještaj nastao, pa
    # kasnija promjena izvornih podataka ne mijenja ono što je već sačuvano
    # (Dio 14). `ai_summary` nosi cijeli narativ kao JSON — četiri polja u
    # jednoj TEXT koloni, da Faza 3C ne bi tražila nove kolone i time nametnula
    # migraciju šeme.
    def save_monthly_report(self, *, student_id, report_month, metrics_json=None,
                            ai_summary=None, instructor_comment=None,
                            status="draft", generated_at=None):
        """Upiši ili osvježi nacrt. `None` polje znači „ne diraj postojeće".

        Time je sačuvano da snimanje komentara instruktora ne pregazi AI tekst,
        a ponovno generisanje AI teksta ne pregazi komentar (Dio 15/17).

        `generated_at` je vrijeme POSLJEDNJEG AI generisanja i šalje se SAMO kad
        je model stvarno pozvan. Obično snimanje izmjena i pravljenje PDF-a ga
        namjerno ostavljaju na miru — inače bi ručna ispravka zareza izgledala
        kao nov AI nacrt. Kolona postoji u izmjerenoj produkcijskoj tabeli; ako
        je zatečena tabela nema, upis se tiho odvija bez nje."""
        with self._lock:
            try:
                conn = self._connection()
                self._require_monthly_reports(conn)
                existing = _rows(conn.execute(
                    "SELECT id FROM monthly_reports "
                    "WHERE student_id = ? AND report_month = ?",
                    (int(student_id), report_month)))
                has_generated_at = self._monthly_reports_has_generated_at(conn)
                if existing:
                    report_id = existing[0][0]
                    sets, params = [], []
                    columns = [("metrics_json", metrics_json),
                               ("ai_summary", ai_summary),
                               ("instructor_comment", instructor_comment),
                               ("status", status)]
                    if has_generated_at:
                        columns.append(("generated_at", generated_at))
                    for column, value in columns:
                        if value is not None:
                            sets.append(column + " = ?")
                            params.append(value)
                    sets.append("updated_at = CURRENT_TIMESTAMP")
                    conn.execute(
                        "UPDATE monthly_reports SET " + ", ".join(sets)
                        + " WHERE id = ?", (*params, report_id))
                else:
                    names = ["student_id", "report_month", "status",
                             "metrics_json", "ai_summary", "instructor_comment"]
                    values = [int(student_id), report_month, status, metrics_json,
                              ai_summary, instructor_comment or ""]
                    if has_generated_at:
                        names.append("generated_at")
                        values.append(generated_at)
                    cursor = conn.execute(
                        "INSERT INTO monthly_reports (%s, created_at, updated_at) "
                        "VALUES (%s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                        % (", ".join(names), ", ".join("?" * len(values))),
                        tuple(values))
                    report_id = cursor.lastrowid
                conn.commit()
                return report_id
            except ReportingUnavailable:
                self._safe_rollback()
                self._drop_connection()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "monthly_report_save_failed:" + type(exc).__name__, exc) from None

    def fetch_monthly_report(self, student_id, report_month):
        """Sačuvani nacrt ili None. Čisto čitanje — nikad ne kreira red."""
        with self._lock:
            try:
                conn = self._connection()
                self._require_monthly_reports(conn)
                generated = ("generated_at"
                             if self._monthly_reports_has_generated_at(conn)
                             else "NULL AS generated_at")
                rows = _rows(conn.execute(
                    "SELECT id, student_id, report_month, status, metrics_json, "
                    " ai_summary, instructor_comment, pdf_path, created_at, "
                    " updated_at, " + generated + " FROM monthly_reports "
                    "WHERE student_id = ? AND report_month = ?",
                    (int(student_id), report_month)))
            except ReportingUnavailable:
                self._drop_connection()
                raise
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "monthly_report_read_failed:" + type(exc).__name__, exc) from None
        if not rows:
            return None
        columns = ("id", "student_id", "report_month", "status", "metrics_json",
                   "ai_summary", "instructor_comment", "pdf_path", "created_at",
                   "updated_at", "generated_at")
        return dict(zip(columns, rows[0]))

    def _require_monthly_reports(self, conn):
        """Padni ZATVORENO ako produkcijska tabela ne podnosi Fazu 3C.

        Bolje je vidljiva greška nego upis u tabelu čiji oblik ne poznajemo."""
        # Lokalni import kao i u `check()` — izbjegava kružnu zavisnost.
        from matbot import reporting_schema

        problems = reporting_schema.verify_monthly_reports_schema(conn)
        if problems:
            raise ReportingUnavailable("monthly_reports_unusable:" + problems[0])

    def _monthly_reports_has_generated_at(self, conn):
        """Ima li zatečena tabela `generated_at`. Izmjerena produkcija ima."""
        from matbot import reporting_schema

        return bool(reporting_schema.monthly_reports_capabilities(conn)
                    .get("generated_at"))

    # --- ČITANJE (izvještajni model) ---------------------------------------
    def fetch_progress_snapshot(self, student_id, report_month, course_key=None):
        """Snimak + sekcije za jedan mjesec, ili `None`."""
        with self._lock:
            try:
                conn = self._connection()
                sql = ("SELECT id, course_key, course_name, grade, percent_viewed, "
                       "percent_completed, started_at, completed_at, activated_at, "
                       "expires_at, last_sign_in FROM thinkific_progress_snapshots "
                       "WHERE student_id = ? AND report_month = ?")
                params = [int(student_id), report_month]
                if course_key:
                    sql += " AND course_key = ?"
                    params.append(course_key)
                sql += " ORDER BY id DESC LIMIT 1"
                rows = _rows(conn.execute(sql, tuple(params)))
                if not rows:
                    return None
                row = rows[0]
                sections = _rows(conn.execute(
                    "SELECT ordinal, section_name, progress_percent "
                    "FROM thinkific_progress_sections WHERE snapshot_id = ? "
                    "ORDER BY ordinal, id", (row[0],)))
                return {
                    "snapshot_id": row[0], "course_key": row[1], "course_name": row[2],
                    "grade": row[3], "percent_viewed": row[4],
                    "percent_completed": row[5], "started_at": row[6],
                    "completed_at": row[7], "activated_at": row[8],
                    "expires_at": row[9], "last_sign_in": row[10],
                    "sections": [{"ordinal": s[0], "section_name": s[1],
                                  "progress_percent": s[2]} for s in sections],
                }
            except ReportingUnavailable:
                self._drop_connection()
                raise
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "snapshot_read_failed:" + type(exc).__name__, exc) from None

    def fetch_student_profile(self, student_id):
        """Profil + STANJE POTVRDE tekućeg razreda.

        `grade_confirmed_at`/`grade_source` su `None` i kad kolone postoje ali
        su prazne (zatečeni učenik) i kad ih baza još nema — oba stanja znače
        NEPOTVRĐENO, pa se pozivaocu ne razlikuju."""
        with self._lock:
            try:
                conn = self._connection()
                if self._grade_confirmation_available(conn):
                    rows = _rows(conn.execute(
                        "SELECT display_name, grade, grade_confirmed_at, grade_source "
                        "FROM students WHERE id = ?", (int(student_id),)))
                else:
                    # Bez v4 kolona: isti oblik reda, potvrda je uvijek prazna.
                    rows = [(row[0], row[1], None, None) for row in _rows(
                        conn.execute("SELECT display_name, grade FROM students "
                                     "WHERE id = ?", (int(student_id),)))]
                if not rows:
                    return None
                return {"display_name": rows[0][0], "grade": rows[0][1],
                        "grade_confirmed_at": rows[0][2],
                        "grade_source": rows[0][3]}
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "profile_read_failed:" + type(exc).__name__, exc) from None

    # --- FAZA 3D: registar učenika i evidencija časova ----------------------
    # NEMA DRUGOG PROSTORA IMENA. Registar je pogled na POSTOJEĆU `students`
    # tabelu; učenik koji je nastao kroz Thinkific i učenik kojeg je
    # administrator upisao ručno su isti tip zapisa i vide se na istoj listi.
    # STATUS UČENIKA: ovaj repozitorij kolonu `students.status` nikad nije ni
    # čitao ni pisao — postoji u zatečenoj šemi verzije 1. Zato se NE tvrdi da
    # znamo njen zatvoreni skup vrijednosti, nego se isključuju samo IZRIČITI
    # markeri neaktivnosti. Nepoznata vrijednost pada OTVORENO (učenik se vidi):
    # spisak časa je ionako ručni izbor instruktora, pa je suvišan red bezopasan,
    # dok bi nestao učenik značio da se čas ne može evidentirati.
    _INACTIVE_STATUSES = ("inactive", "archived", "deleted", "disabled")

    def list_students(self, search=None, grade=None, confirmed=None,
                      active=None):
        """Registar: svi učenici, stanje potvrde razreda i Thinkific veza.

        E-MAIL SE NE VRAĆA. Administratoru je dovoljno „povezan/nije povezan";
        adresa nema šta da radi u listi, URL-u ni logu (Dio 40).

        `confirmed` je `True`/`False`/`None` (bez filtera). Filtriranje ide u
        SQL-u samo kad baza ima v4 kolone; bez njih je odgovor „nijedan nije
        potvrđen", pa se `confirmed=True` svodi na praznu listu — što je tačno,
        a ne prazno iz kvara."""
        with self._lock:
            try:
                conn = self._connection()
                has_v4 = self._grade_confirmation_available(conn)
                columns = ("s.grade_confirmed_at, s.grade_source" if has_v4
                           else "NULL, NULL")
                sql = ("SELECT s.id, s.display_name, s.grade, "
                       "       (SELECT COUNT(*) FROM student_accounts a "
                       "        WHERE a.student_id = s.id AND a.provider = ?), "
                       "       " + columns + " "
                       "FROM students s")
                params = [_THINKIFIC_PROVIDER]
                where = []
                if (search or "").strip():
                    where.append("LOWER(COALESCE(s.display_name, '')) LIKE ?")
                    params.append("%" + search.strip().lower() + "%")
                if grade is not None:
                    where.append("s.grade = ?")
                    params.append(int(grade))
                if active:
                    placeholders = ",".join("?" * len(self._INACTIVE_STATUSES))
                    where.append(
                        "LOWER(COALESCE(s.status, 'active')) NOT IN (%s)"
                        % placeholders)
                    params.extend(self._INACTIVE_STATUSES)
                if confirmed is not None and has_v4:
                    # POTVRDA JE TROJKA (razred + vrijeme + izvor), pa filter
                    # gleda vrijeme — izvor se provjerava u Pythonu, jer skup
                    # dopuštenih izvora živi tamo.
                    where.append("s.grade_confirmed_at IS %s NULL"
                                 % ("NOT" if confirmed else ""))
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY COALESCE(s.display_name, ''), s.id"
                rows = _rows(conn.execute(sql, tuple(params)))
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "student_list_failed:" + type(exc).__name__, exc) from None
        listed = [{"student_id": r[0], "display_name": r[1], "grade": r[2],
                   "thinkific_linked": bool(r[3]),
                   "grade_confirmed_at": r[4], "grade_source": r[5]}
                  for r in rows]
        if confirmed is not None and not has_v4:
            # Bez v4 kolona nijedan razred nije potvrđen (vidi docstring).
            listed = [] if confirmed else listed
        return listed

    def create_student(self, display_name, grade, external_user_id=None):
        """Ručno upisan učenik. JEDNA LOGIČKA OPERACIJA, i kad nosi nalog.

        ŽIVI DEFEKT KOJI OVO ISPRAVLJA: ranije se učenik upisivao i KOMITOVAO,
        pa se tek onda pokušavao nalog. Kad je e-mail već pripadao drugom
        učeniku, nalog bi pao a NOVI UČENIK BI OSTAO — duplikat bez ijedne veze,
        koji se poslije ručno traži i briše. Zato se sada oboje dešava u jednoj
        transakciji: ili prođu i učenik i nalog, ili nijedno.

        BAZA JE KONAČNI ARBITAR NAD ISTOVREMENOŠĆU. Između provjere i upisa
        stane drugi zahtjev, a `students` nema nijedan UNIQUE ključ koji bi to
        zaustavio — jedini pouzdan sud je `UNIQUE(provider, external_user_id)`
        na `student_accounts`. Zato se ne oslanjamo na prethodni `SELECT` nego
        na `rowcount` upisa: izgubljena utrka povlači i spekulativni red u
        `students`, pa siroče ne nastane ni tada.

        Učenik BEZ naloga ostaje potpuno legitiman: časove dobija od
        instruktora, a izvještaj mu pripada i kad nikad nije otvorio nijednu
        platformu. Tada se `student_accounts` ne dira."""
        name = _clean_display_name(display_name)
        if not name:
            raise ReportingUnavailable("student_name_required")
        student_grade = _clean_grade(grade)
        # RUČNI UPIS TRAŽI TAČAN RAZRED. `_clean_grade` je namjerno širok (put
        # identiteta smije primiti i nepoznat razred), ali učenik kojeg upisuje
        # administrator ide u izvještaj s razredom na naslovnici — pa je ovdje
        # skup zatvoren na 6–9, tačno kao u formularu.
        if student_grade not in VALID_MANUAL_GRADES:
            raise ReportingUnavailable("student_grade_required")
        external = (_clean_external_id(external_user_id)
                    if external_user_id else None)

        with self._lock:
            try:
                conn = self._connection()
                if external:
                    # Rani, jeftin i INFORMATIVAN pregled: daje administratoru
                    # ID postojećeg učenika. NIJE zaštita od utrke — to je
                    # `rowcount` niže.
                    owner = _rows(conn.execute(
                        "SELECT student_id FROM student_accounts "
                        "WHERE provider = ? AND external_user_id = ?",
                        (_THINKIFIC_PROVIDER, external)))
                    if owner:
                        raise ReportingUnavailable(
                            "student_account_taken:%s" % owner[0][0])

                # RUČNI UPIS JE POTVRDA. Razred je obavezno polje formulara i
                # bira ga čovjek, pa se odmah bilježi i KO ga je potvrdio —
                # inače bi ručno upisan učenik bio nerazlučiv od zatečenog,
                # kojem je razred upisala stara automatika.
                if not self._grade_confirmation_available(conn):
                    raise ReportingUnavailable("grade_confirmation_unavailable")
                cursor = conn.execute(
                    "INSERT INTO students (display_name, grade, grade_confirmed_at, "
                    " grade_source, created_at, updated_at, last_seen_at) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP, ?, CURRENT_TIMESTAMP, "
                    " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (name, student_grade, GRADE_SOURCE_MANUAL_CREATION))
                student_id = cursor.lastrowid

                if external:
                    linked = conn.execute(
                        "INSERT INTO student_accounts (student_id, provider, "
                        " external_user_id, created_at, last_seen_at) "
                        "VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                        "ON CONFLICT (provider, external_user_id) DO NOTHING",
                        (student_id, _THINKIFIC_PROVIDER, external))
                    # `lastrowid` se OVDJE ne smije čitati (izmjereno na libsql
                    # 0.1.11: na `DO NOTHING` grani ostaje tuđa vrijednost).
                    # `rowcount` je jedini pouzdan signal.
                    if linked.rowcount != 1:
                        # Paralelni zahtjev je stigao prvi. `rollback` poništava
                        # i NAŠ red u `students` — bez njega bi ostalo siroče.
                        conn.rollback()
                        winner = _rows(conn.execute(
                            "SELECT student_id FROM student_accounts "
                            "WHERE provider = ? AND external_user_id = ?",
                            (_THINKIFIC_PROVIDER, external)))
                        raise ReportingUnavailable(
                            "student_account_taken:%s"
                            % (winner[0][0] if winner else "unknown"))

                conn.commit()
                return student_id
            except ReportingUnavailable:
                self._safe_rollback()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "student_create_failed:" + type(exc).__name__, exc) from None

    def link_thinkific_account(self, student_id, external_user_id):
        """Poveži Thinkific nalog s POSTOJEĆIM učenikom. Pada zatvoreno.

        NIKAD NE PREUZIMA TUĐI NALOG. Ako adresa već pripada drugom učeniku,
        `UNIQUE(provider, external_user_id)` to zaustavlja, a mi vraćamo
        `student_account_taken` s ID-em postojećeg učenika da administrator zna
        KOJI zapis da pogleda. Spajanje identiteta je destruktivno i nije
        predmet ove faze."""
        external = _clean_external_id(external_user_id)
        with self._lock:
            try:
                conn = self._connection()
                owner = _rows(conn.execute(
                    "SELECT student_id FROM student_accounts "
                    "WHERE provider = ? AND external_user_id = ?",
                    (_THINKIFIC_PROVIDER, external)))
                if owner:
                    existing = owner[0][0]
                    if int(existing) == int(student_id):
                        return False              # već povezan s OVIM učenikom
                    raise ReportingUnavailable(
                        "student_account_taken:%s" % existing)
                conn.execute(
                    "INSERT INTO student_accounts (student_id, provider, "
                    " external_user_id, created_at, last_seen_at) "
                    "VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (int(student_id), _THINKIFIC_PROVIDER, external))
                conn.commit()
                return True
            except ReportingUnavailable:
                self._safe_rollback()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "student_link_failed:" + type(exc).__name__, exc) from None

    def set_student_grade(self, student_id, grade):
        """POTVRDI tekući razred učenika. Mijenja SAMO profilne kolone.

        Upisuje razred, VRIJEME POTVRDE i IZVOR `admin`. Ista funkcija služi i
        za promjenu razreda i za potvrdu zatečene vrijednosti — u oba slučaja je
        radnja ista: čovjek je pogledao i odlučio.

        Istorija se ne dira: `learning_activity.grade`,
        `assessment_attempts.grade`, `thinkific_progress_snapshots.grade` i
        `student_sessions` su OPAŽANJA i ostaju kakva jesu. Učenik koji pređe iz
        šestog u sedmi razred ne smije izgubiti niti izmijeniti svoj lanjski čas.

        Poziva se ISKLJUČIVO iz administratorske akcije — nema automatske
        promocije, nema masovne ispravke i nema poziva iz uvoza ni iz tutora.

        PADA ZATVORENO bez šeme v4: potvrda koja se ne može ZAPISATI ne smije se
        ni tvrditi, jer bi sljedeće čitanje razred i dalje vidjelo kao
        nepotvrđen a administrator bi mislio da je gotov."""
        if int(grade) not in (6, 7, 8, 9):
            raise ReportingUnavailable("student_grade_invalid")
        with self._lock:
            try:
                conn = self._connection()
                if not self._grade_confirmation_available(conn):
                    raise ReportingUnavailable("grade_confirmation_unavailable")
                cursor = conn.execute(
                    "UPDATE students SET grade = ?, "
                    " grade_confirmed_at = CURRENT_TIMESTAMP, grade_source = ?, "
                    " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (int(grade), GRADE_SOURCE_ADMIN, int(student_id)))
                changed = cursor.rowcount
                conn.commit()
                return changed == 1
            except ReportingUnavailable:
                self._safe_rollback()
                raise
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "student_grade_update_failed:" + type(exc).__name__, exc) from None

    def fetch_grade_evidence(self, student_id):
        """SAMO ČITANJE: datirani tragovi razreda iz sva tri strukturna izvora.

        Ne vraća e-mail, `external_user_id` ni ijedan slobodan tekst. Redovi su
        `(vrijeme, razred)` parovi koje `student_grades` pretvara u dokaz."""
        with self._lock:
            try:
                conn = self._connection()
                thinkific = _rows(conn.execute(
                    "SELECT report_month, grade FROM thinkific_progress_snapshots "
                    "WHERE student_id = ? AND grade IS NOT NULL",
                    (int(student_id),)))
                assessment = _rows(conn.execute(
                    "SELECT MAX(completed_at), grade FROM assessment_attempts "
                    "WHERE student_id = ? AND grade IS NOT NULL "
                    "AND completed_at IS NOT NULL GROUP BY grade",
                    (int(student_id),)))
                matbot = _rows(conn.execute(
                    "SELECT MAX(occurred_at), grade FROM learning_activity "
                    "WHERE student_id = ? AND grade IS NOT NULL GROUP BY grade",
                    (int(student_id),)))
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "grade_evidence_read_failed:" + type(exc).__name__, exc) from None
        from matbot import student_grades

        return student_grades.evidence_from_rows(
            thinkific_rows=[(m, g) for m, g in thinkific],
            assessment_rows=[(w, g) for w, g in assessment],
            matbot_rows=[(w, g) for w, g in matbot])

    def fetch_progress_imports(self):
        """SAMO ČITANJE: svi Thinkific uvozi + broj snimaka po razredu.

        `grade` i `course_name` su ovdje ono što je administrator IZABRAO kao
        slot, ne ono što je bilo u fajlu — vidi `thinkific_grade_forensics`."""
        with self._lock:
            try:
                conn = self._connection()
                rows = _rows(conn.execute(
                    "SELECT id, report_month, course_key, course_name, grade, "
                    " row_count, imported_at, source_sha256 "
                    "FROM thinkific_progress_imports ORDER BY imported_at, id"))
                counts = _rows(conn.execute(
                    "SELECT import_id, grade, COUNT(*) "
                    "FROM thinkific_progress_snapshots GROUP BY import_id, grade"))
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "progress_imports_read_failed:" + type(exc).__name__, exc) from None
        by_import = {}
        for import_id, grade, count in counts:
            by_import.setdefault(import_id, {})[grade] = count
        columns = ("id", "report_month", "course_key", "course_name", "grade",
                   "row_count", "imported_at", "source_sha256")
        result = []
        for row in rows:
            item = dict(zip(columns, row))
            item["snapshot_grades"] = by_import.get(item["id"], {})
            result.append(item)
        return result

    def fetch_import_sections(self):
        """SAMO ČITANJE: {import_id: [(ordinal, naziv_sekcije)]}.

        Nazivi dolaze iz ZAGLAVLJA izvoza (`section_columns`), pa su jedini
        podatak u cijelom lancu koji ne zavisi od izabranog slota."""
        with self._lock:
            try:
                conn = self._connection()
                rows = _rows(conn.execute(
                    "SELECT DISTINCT p.import_id, s.ordinal, s.section_name "
                    "FROM thinkific_progress_sections s "
                    "JOIN thinkific_progress_snapshots p ON p.id = s.snapshot_id "
                    "ORDER BY p.import_id, s.ordinal"))
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "import_sections_read_failed:" + type(exc).__name__, exc) from None
        grouped = {}
        for import_id, ordinal, name in rows:
            grouped.setdefault(import_id, []).append((ordinal, name))
        return grouped

    def fetch_student_thinkific_history(self, student_id):
        """SAMO ČITANJE: svi snimci jednog učenika, najstariji prvi."""
        with self._lock:
            try:
                conn = self._connection()
                rows = _rows(conn.execute(
                    "SELECT report_month, course_key, course_name, grade, "
                    " percent_viewed, percent_completed "
                    "FROM thinkific_progress_snapshots WHERE student_id = ? "
                    "ORDER BY report_month, id", (int(student_id),)))
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "student_history_read_failed:" + type(exc).__name__, exc) from None
        columns = ("report_month", "course_key", "course_name", "grade",
                   "percent_viewed", "percent_completed")
        return [dict(zip(columns, row)) for row in rows]

    def student_has_thinkific(self, student_id):
        with self._lock:
            try:
                conn = self._connection()
                rows = _rows(conn.execute(
                    "SELECT 1 FROM student_accounts WHERE student_id = ? "
                    "AND provider = ?", (int(student_id), _THINKIFIC_PROVIDER)))
                return bool(rows)
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "student_account_read_failed:" + type(exc).__name__, exc) from None

    _SESSION_COLUMNS = ("id", "student_id", "session_date", "attendance",
                        "activity_rating", "homework_status", "area_name",
                        "lesson_name", "comment", "created_at", "updated_at")

    def insert_session(self, student_id, record):
        """Upiši jedan čas. `record` je već PROŠAO `student_sessions.validate_session`."""
        with self._lock:
            try:
                conn = self._connection()
                cursor = conn.execute(
                    "INSERT INTO student_sessions (student_id, session_date, "
                    " attendance, activity_rating, homework_status, area_name, "
                    " lesson_name, comment, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, "
                    " CURRENT_TIMESTAMP)",
                    (int(student_id), record["session_date"], record["attendance"],
                     record["activity_rating"], record["homework_status"],
                     record["area_name"], record["lesson_name"], record["comment"]))
                session_id = cursor.lastrowid
                conn.commit()
                return session_id
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "session_insert_failed:" + type(exc).__name__, exc) from None

    def update_session(self, session_id, student_id, record):
        """Izmijeni čas. VLASNIŠTVO JE USLOV, ne pretpostavka.

        `student_id` u `WHERE` je zaštita od IDOR-a: zapis tuđeg učenika se ne
        može izmijeniti ni kad se pogodi tačan `session_id`."""
        with self._lock:
            try:
                conn = self._connection()
                cursor = conn.execute(
                    "UPDATE student_sessions SET session_date = ?, attendance = ?, "
                    " activity_rating = ?, homework_status = ?, area_name = ?, "
                    " lesson_name = ?, comment = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND student_id = ?",
                    (record["session_date"], record["attendance"],
                     record["activity_rating"], record["homework_status"],
                     record["area_name"], record["lesson_name"], record["comment"],
                     int(session_id), int(student_id)))
                changed = cursor.rowcount
                conn.commit()
                return changed == 1
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "session_update_failed:" + type(exc).__name__, exc) from None

    def delete_session(self, session_id, student_id):
        with self._lock:
            try:
                conn = self._connection()
                cursor = conn.execute(
                    "DELETE FROM student_sessions WHERE id = ? AND student_id = ?",
                    (int(session_id), int(student_id)))
                changed = cursor.rowcount
                conn.commit()
                return changed == 1
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "session_delete_failed:" + type(exc).__name__, exc) from None

    def fetch_session(self, session_id, student_id):
        return self._fetch_sessions(
            "WHERE id = ? AND student_id = ?",
            (int(session_id), int(student_id)), single=True)

    def fetch_sessions(self, student_id, date_start=None, date_end=None):
        """Časovi jednog učenika. Opseg je [start, end) — isti oblik kao mjesec.

        Poredak je DETERMINISTIČAN (datum, pa `id`): dva časa istog dana ne
        smiju mijenjati redoslijed između dva čitanja."""
        clause = "WHERE student_id = ?"
        params = [int(student_id)]
        if date_start is not None:
            clause += " AND session_date >= ?"
            params.append(date_start)
        if date_end is not None:
            clause += " AND session_date < ?"
            params.append(date_end)
        clause += " ORDER BY session_date, id"
        return self._fetch_sessions(clause, tuple(params))

    def _fetch_sessions(self, clause, params, single=False):
        columns = ", ".join(self._SESSION_COLUMNS)
        with self._lock:
            try:
                conn = self._connection()
                rows = _rows(conn.execute(
                    "SELECT %s FROM student_sessions %s" % (columns, clause),
                    params))
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "session_read_failed:" + type(exc).__name__, exc) from None
        records = [dict(zip(self._SESSION_COLUMNS, row)) for row in rows]
        if single:
            return records[0] if records else None
        return records

    def save_class_sessions(self, records):
        """CIJELI ČAS U JEDNOJ TRANSAKCIJI. `records` je [(student_id, zapis)].

        Vraća `{"inserted": n, "updated": m}`.

        ZAŠTO JEDAN COMMIT: djelimično sačuvan čas je gori od nesačuvanog — pola
        odjeljenja bi imalo evidenciju, a instruktor bi vidio poruku o grešci i
        ponovo poslao isti čas. Ili prođu svi redovi, ili nijedan.

        ZAŠTITA OD DVOSTRUKOG SLANJA, BEZ ŠEME v5. Osvježena stranica i dvoklik
        na „Sačuvaj" ne smiju napraviti drugi red za isti logički čas. Ključ je
        `(student_id, session_date, area_name, lesson_name)`: postojeći red se
        AŽURIRA, a novi se upisuje samo ako ga nema. Isti čas poslan dvaput zato
        daje isto stanje kao poslan jednom.

        GRANICA KOJU OVO NE PREĐE, izričito: `student_sessions` NEMA jedinstveni
        indeks nad tim ključem (dodavanje bi bilo šema v5, a nad zatečenim
        produkcijskim redovima bi moglo i pasti). Zaštita je zato „pročitaj pa
        upiši" unutar JEDNE transakcije, serijalizovana bravom ove instance —
        dovoljno za jednog administratora, ali ne i strukturna garancija protiv
        dva istovremena procesa. Ako to ikad postane stvaran zahtjev, ispravka je
        jedinstveni indeks, ne još jedna provjera u Pythonu."""
        counters = {"inserted": 0, "updated": 0}
        if not records:
            return counters

        with self._lock:
            try:
                conn = self._connection()
                for student_id, record in records:
                    existing = _rows(conn.execute(
                        "SELECT id FROM student_sessions "
                        "WHERE student_id = ? AND session_date = ? "
                        " AND COALESCE(area_name, '') = COALESCE(?, '') "
                        " AND COALESCE(lesson_name, '') = COALESCE(?, '') "
                        "ORDER BY id LIMIT 1",
                        (int(student_id), record["session_date"],
                         record["area_name"], record["lesson_name"])))
                    if existing:
                        conn.execute(
                            "UPDATE student_sessions SET attendance = ?, "
                            " activity_rating = ?, homework_status = ?, "
                            " comment = ?, updated_at = CURRENT_TIMESTAMP "
                            "WHERE id = ? AND student_id = ?",
                            (record["attendance"], record["activity_rating"],
                             record["homework_status"], record["comment"],
                             int(existing[0][0]), int(student_id)))
                        counters["updated"] += 1
                    else:
                        conn.execute(
                            "INSERT INTO student_sessions (student_id, "
                            " session_date, attendance, activity_rating, "
                            " homework_status, area_name, lesson_name, comment, "
                            " created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, "
                            " CURRENT_TIMESTAMP)",
                            (int(student_id), record["session_date"],
                             record["attendance"], record["activity_rating"],
                             record["homework_status"], record["area_name"],
                             record["lesson_name"], record["comment"]))
                        counters["inserted"] += 1
                conn.commit()          # JEDINI commit za cijeli čas
                return counters
            except Exception as exc:
                self._safe_rollback()
                self._drop_connection()
                raise ReportingUnavailable(
                    "class_save_failed:" + type(exc).__name__, exc) from None

    def fetch_class_sessions(self, session_date, area_name, lesson_name,
                             student_ids):
        """Već upisani redovi JEDNOG logičkog časa. Samo čitanje.

        Služi da se stranica otvori kao IZMJENA: instruktor vidi šta je ranije
        unio umjesto praznog formulara, i ispravlja bez otvaranja profila."""
        found = {}
        ids = [int(sid) for sid in (student_ids or [])]
        if not ids:
            return found
        columns = ", ".join(self._SESSION_COLUMNS)
        with self._lock:
            try:
                conn = self._connection()
                for chunk in self._chunks(ids):
                    placeholders = ",".join("?" * len(chunk))
                    rows = _rows(conn.execute(
                        "SELECT %s FROM student_sessions "
                        "WHERE session_date = ? "
                        " AND COALESCE(area_name, '') = COALESCE(?, '') "
                        " AND COALESCE(lesson_name, '') = COALESCE(?, '') "
                        " AND student_id IN (%s) ORDER BY id"
                        % (columns, placeholders),
                        (session_date, area_name, lesson_name, *chunk)))
                    for row in rows:
                        record = dict(zip(self._SESSION_COLUMNS, row))
                        found.setdefault(record["student_id"], record)
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "class_read_failed:" + type(exc).__name__, exc) from None
        return found

    def fetch_matbot_month(self, student_id, month_start, next_month_start):
        """Determinističke MAT-BOT brojke za mjesec. Granice su UTC, [start, next).

        Sve dolazi iz Faze 2: `learning_activity` za brojanje i `assessment_*`
        za rezultate kontrolnih. Nijedan broj se ne procjenjuje."""
        with self._lock:
            try:
                conn = self._connection()
                counts = dict(_rows(conn.execute(
                    "SELECT event_type, COUNT(*) FROM learning_activity "
                    "WHERE student_id = ? AND source = ? "
                    "AND occurred_at >= ? AND occurred_at < ? GROUP BY event_type",
                    (int(student_id), SOURCE, month_start, next_month_start))))
                active_days = _rows(conn.execute(
                    "SELECT COUNT(DISTINCT date(occurred_at)) FROM learning_activity "
                    "WHERE student_id = ? AND source = ? "
                    "AND occurred_at >= ? AND occurred_at < ?",
                    (int(student_id), SOURCE, month_start, next_month_start)))[0][0]
                exams = _rows(conn.execute(
                    "SELECT COUNT(*), AVG(score_percent), SUM(correct_count), "
                    "SUM(total_count) FROM assessment_attempts "
                    "WHERE student_id = ? AND source = ? AND completed_at IS NOT NULL "
                    "AND completed_at >= ? AND completed_at < ?",
                    (int(student_id), SOURCE, month_start, next_month_start)))[0]
                outcomes = _rows(conn.execute(
                    "SELECT i.lesson_id, i.lesson_name, i.area_name, i.difficulty, "
                    "       SUM(CASE WHEN i.is_correct = 0 THEN 1 ELSE 0 END), "
                    "       COUNT(*) "
                    "FROM assessment_item_results i "
                    "JOIN assessment_attempts a ON a.id = i.attempt_id "
                    "WHERE a.student_id = ? AND a.source = ? "
                    "AND a.completed_at IS NOT NULL "
                    "AND a.completed_at >= ? AND a.completed_at < ? "
                    "AND i.lesson_id IS NOT NULL "
                    "GROUP BY i.lesson_id, i.lesson_name, i.area_name, i.difficulty "
                    "ORDER BY 5 DESC, i.lesson_id",
                    (int(student_id), SOURCE, month_start, next_month_start)))
                return {"counts": counts, "active_days": active_days,
                        "exams": exams, "lesson_outcomes": outcomes}
            except ReportingUnavailable:
                self._drop_connection()
                raise
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "matbot_month_read_failed:" + type(exc).__name__, exc) from None

    def fetch_report_population(self, month_start, next_month_start, report_month):
        """UNIJA učenika koji zaslužuju izvještaj (Dio 23): oni sa Thinkific
        snimkom TOG mjeseca I oni sa pripisanom MAT-BOT aktivnošću tog mjeseca.

        Nijedna grupa se ne ispušta: učenik koji ne koristi MAT-BOT i dalje ima
        napredak u kursu, a učenik bez snimka i dalje ima MAT-BOT rad."""
        with self._lock:
            try:
                conn = self._connection()
                rows = _rows(conn.execute(
                    "SELECT student_id FROM thinkific_progress_snapshots "
                    "WHERE report_month = ? "
                    "UNION "
                    "SELECT student_id FROM learning_activity "
                    "WHERE source = ? AND occurred_at >= ? AND occurred_at < ? "
                    "UNION "
                    "SELECT student_id FROM assessment_attempts "
                    "WHERE source = ? AND completed_at IS NOT NULL "
                    "AND completed_at >= ? AND completed_at < ? "
                    "UNION "
                    # Faza 3D: čas je RAVNOPRAVAN izvor. Učenik koji nema
                    # nijedan nalog ni jedan MAT-BOT događaj, a bio je na času,
                    # zaslužuje izvještaj — to je i cijela poenta registra.
                    # Granice su datumske ([start, end)), jer `session_date` je
                    # `YYYY-MM-DD`, a ne vremenski žig.
                    "SELECT student_id FROM student_sessions "
                    "WHERE session_date >= ? AND session_date < ? "
                    "ORDER BY 1",
                    (report_month, SOURCE, month_start, next_month_start,
                     SOURCE, month_start, next_month_start,
                     month_start[:10], next_month_start[:10])))
                return [row[0] for row in rows]
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "population_read_failed:" + type(exc).__name__, exc) from None

    def migrate(self):
        """Dovedi izvještajnu bazu do TEKUĆE verzije. Idempotentno.

        JEDINO MJESTO NA KOJEM SE ŠEMA MIJENJA, i namjerno je CLI/deploy korak,
        a ne nusproizvod web zahtjeva. Migracijsko znanje ostaje u
        `reporting_schema`; ovdje je samo redoslijed i konekcija — ad-hoc SQL u
        GitHub Actionsu bi bio druga, neprovjerena implementacija istog pravila.

        Svaki korak sam provjerava strukturu prije nego što upiše svoju verziju
        (vidi `migrate_to_v2` / `migrate_to_v3`), pa prekid ostavlja bazu bez
        zapisa te verzije i sljedeće pokretanje je dovrši. Vraća listu
        primijenjenih verzija; prazna znači „već sve na mjestu"."""
        from matbot import reporting_schema

        applied = []
        with self._lock:
            try:
                conn = self._connection()
                versions = reporting_schema.applied_versions(conn)
                if reporting_schema.SCHEMA_VERSION_V2 not in versions:
                    if reporting_schema.migrate_to_v2(conn):
                        applied.append(reporting_schema.SCHEMA_VERSION_V2)
                if reporting_schema.migrate_to_v3(conn):
                    applied.append(reporting_schema.SCHEMA_VERSION_V3)
                if reporting_schema.migrate_to_v4(conn):
                    applied.append(reporting_schema.SCHEMA_VERSION_V4)
                # Nove kolone mijenjaju ono što je konekcija zapamtila.
                self._grade_confirmation = None
            except reporting_schema.MigrationError:
                self._drop_connection()
                raise
            except Exception as exc:
                self._drop_connection()
                raise ReportingUnavailable(
                    "migration_failed:" + type(exc).__name__, exc) from None
        return applied

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
                for table in ("students", "student_accounts", "schema_migrations",
                              "learning_activity", "assessment_attempts",
                              "assessment_item_results",
                              "thinkific_progress_imports",
                              "thinkific_progress_snapshots",
                              "thinkific_progress_sections",
                              # Faza 3D: kolone evidencije časova moraju se
                              # vidjeti iz iste komande kao i sve ostalo.
                              "student_sessions",
                              # Faza 3C: kolone se ISPISUJU jer ovaj repo tabelu
                              # nikad nije kreirao — njen produkcijski oblik se
                              # mora vidjeti, a ne pretpostaviti.
                              "monthly_reports"):
                    if table in tables:
                        report["columns"][table] = [
                            row[1] for row in _rows(conn.execute(f"PRAGMA table_info({table})"))
                        ]
                # Faza 3C: podnosi li `monthly_reports` izvještaje za roditelje.
                try:
                    from matbot import reporting_schema as _schema

                    problems = _schema.verify_monthly_reports_schema(conn)
                    report["monthly_reports_ready"] = not problems
                    report["monthly_reports_problems"] = problems
                except Exception as exc:
                    report["monthly_reports_ready"] = False
                    report["monthly_reports_problems"] = [
                        "monthly_reports_check_failed:" + type(exc).__name__]

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
                # STRUKTURNI DOKAZ VERZIJE 2, ne samo broj u tabeli. Zivi
                # incident je pokazao da baza moze imati tabele bez zapisa
                # verzije (prekinuta migracija) ili zapis bez tabela; operator
                # mora vidjeti OBOJE iz jedne komande.
                try:
                    from matbot import reporting_schema

                    report["v2_schema_problems"] = reporting_schema.verify_v2_schema(conn)
                except Exception:
                    report["v2_schema_problems"] = ["v2_verification_unavailable"]
                report["v2_schema_verified"] = not report["v2_schema_problems"]
                # Faza 3D: ista logika za v3. Baza koja tvrdi verziju 3 a nema
                # ispravnu `student_sessions` mora se VIDJETI kao pokvarena, ne
                # prijaviti „OK" (živi incident v1→v2 je bio upravo to).
                try:
                    from matbot import reporting_schema as _v3schema

                    report["v3_schema_problems"] = _v3schema.verify_v3_schema(conn)
                except Exception:
                    report["v3_schema_problems"] = ["v3_verification_unavailable"]
                report["v3_schema_verified"] = not report["v3_schema_problems"]
                # Faza 3D+: ista logika za v4. Baza koja tvrdi verziju 4 a nema
                # kolone potvrde razreda mora se VIDJETI kao pokvarena.
                try:
                    from matbot import reporting_schema as _v4schema

                    report["v4_schema_problems"] = _v4schema.verify_v4_schema(conn)
                except Exception:
                    report["v4_schema_problems"] = ["v4_verification_unavailable"]
                report["v4_schema_verified"] = not report["v4_schema_problems"]
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

# --- KEŠ RAZRIJEŠENOG IDENTITETA -------------------------------------------
# IZMJERENI RAZLOG (Faza 2): `resolve_student` je SINHRON — turn ga čeka jer mu
# treba `students.id` da bi događaji dobili vlasnika. Dok baza radi, to je jedan
# brz upit; kad Turso VISI, svaki identifikovan turn plaća pun rok od
# `REPORTING_DB_TIMEOUT_S` (2 s), i to iznova. Izmjereno na zaglavljenoj bazi:
# turn je trajao 2,02 s umjesto ~0 s.
#
# Mapiranje (provider, external_user_id) -> students.id je NEPROMJENJIVO: red u
# `student_accounts` se nikad ne prevezuje na drugog učenika. Zato se smije
# keširati u procesu, i tada se cijena plaća NAJVIŠE JEDNOM po učeniku po
# procesu umjesto na svakom turnu.
#
# Granica je tvrda (`_IDENTITY_CACHE_MAX`) da keš ne raste neograničeno — isti
# princip kao `MAX_SESSIONS_IN_MEMORY`. Prazni se pri svakoj zamjeni baze
# (`set_database`), jer druga baza znači i druge ID-jeve.
_IDENTITY_CACHE_MAX = 2000
_identity_cache = {}


def _identity_cache_get(provider, external_user_id):
    with _database_lock:
        return _identity_cache.get((provider, external_user_id))


def _identity_cache_put(provider, external_user_id, student_id):
    with _database_lock:
        if len(_identity_cache) >= _IDENTITY_CACHE_MAX:
            # Jednostavno FIFO odbacivanje — keš je ubrzanje, ne evidencija.
            _identity_cache.pop(next(iter(_identity_cache)), None)
        _identity_cache[(provider, external_user_id)] = student_id


def clear_identity_cache():
    with _database_lock:
        _identity_cache.clear()


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
        # Druga baza = drugi ID-jevi. Keš iz prethodne baze bi bio pogrešan.
        _identity_cache.clear()


def shutdown():
    """Uredno gašenje (Dio 2). Nije obavezno — proces smije pasti i bez ovoga:
    radne niti su `daemon`, pa nijedna ne može zadržati izlaz interpretera."""
    global _inflight, _inflight_limit
    with _database_lock:
        if _database is not None:
            _database.close()
        _inflight = None
        _inflight_limit = None
        _identity_cache.clear()


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


def resolve_student(provider, external_user_id, display_name=None,
                    database=None):
    """SIGURAN ulaz: `students.id` ili `None`. NIKAD ne baca i nikad ne čeka
    duže od `config.REPORTING_DB_TIMEOUT_S`.

    RAZRED SE NE PRIMA (verzija 4). Parametar je uklonjen namjerno, a ne
    ignorisan: dok je postojao, svaki pozivalac je mogao — i jedan jeste —
    proslijediti razred iz klijentskog menija u `students.grade`.

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

    try:
        external = _clean_external_id(external_user_id)
    except ReportingUnavailable:
        logger.info("student_resolution_failed code=invalid_external_user_id")
        return None

    cached = _identity_cache_get(provider_code, external)
    if cached is not None:
        # Nema mreže, nema niti, nema roka — najčešći slučaj poslije prvog turna.
        return cached

    ok, value = _call_bounded(
        lambda: target.get_or_create_student(provider, external_user_id,
                                             display_name=display_name),
        config.REPORTING_DB_TIMEOUT_S,
    )
    if ok:
        if value:
            _identity_cache_put(provider_code, external, value)
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


def _run_detached(operation, label):
    """Pokreni izvještajni upis NA STRANU i odmah se vrati. Nikad ne baca.

    JEDAN mehanizam za sve asinhrone upise (događaji učenja, generisan test,
    ocijenjen test) — tri kopije iste petlje bi se razišle. Ograničenja su ista
    kao svuda u modulu: najviše `REPORTING_DB_MAX_INFLIGHT` niti, semafor se
    uzima NEBLOKIRAJUĆE (preopterećenje se odbacuje, ne staje u red), niti su
    `daemon` pa zaglavljen upis ne drži gašenje procesa, i nema retryja.

    Vraća `True` samo ako je posao stvarno predat niti."""
    inflight = _get_inflight()
    if not inflight.acquire(blocking=False):
        logger.info("reporting_db_unavailable code=reporting_db_busy operation=%s", label)
        return False

    def runner():
        try:
            operation()
        except ReportingUnavailable as exc:
            logger.info("%s_failed code=%s", label, exc.code)
        except Exception as exc:
            logger.info("%s_failed code=%s", label, "unexpected:" + type(exc).__name__)
        finally:
            inflight.release()

    threading.Thread(target=runner, name="matbot-reporting", daemon=True).start()
    return True


def record_activity(student_id, events, database=None):
    """SIGURAN ulaz za događaje učenja. Nikad ne baca i — kritično — NIKAD NE ČEKA.

    ZAŠTO JE OVAJ POZIV ASINHRON, ZA RAZLIKU OD `resolve_student`: razrješenje
    identiteta se dešava JEDNOM po turnu i njegov rezultat treba ostatku turna,
    pa se na njega smije kratko čekati. Događaji učenja ne trebaju NIKOME u tom
    zahtjevu — oni su čisto izvještajni. Sinhroni upis bi svakom turnu dodao
    mrežni put do Turso servera bez ijedne koristi za učenika.

    PRIHVAĆEN KOMPROMIS TRAJNOSTI: događaj predat niti koja još nije završila se
    GUBI ako se proces sruši ili restartuje (deploy). Za MVP je to svjesno
    izabrano — dostupnost tutora je važnija od garantovane isporuke jednog
    analitičkog događaja, a mjesečni izvještaj mjeri navike kroz sedmice, gdje
    izgubljen pojedinačan događaj ne mijenja zaključak. Ako to jednom postane
    nedovoljno, lijek je perzistentan red PRIJE mreže, ne čekanje u zahtjevu."""
    if not events or not student_id:
        return False
    if not config.reporting_db_configured() and database is None:
        return False
    target = database or get_database()
    return _run_detached(
        lambda: target.record_learning_activity(student_id, events),
        "activity_write")


ASSESSMENT_GENERATED = "generated"
ASSESSMENT_COMPLETED = "completed"


def record_turn(student_id, events=None, assessment=None, database=None):
    """JEDAN odvojen posao po zahtjevu: događaji učenja + (opciono) procjena.

    ZAŠTO SPOJENO, A NE DVA POZIVA — IZMJERENO: dok su upis aktivnosti i upis
    procjene bili dva `_run_detached` posla, jedan zahtjev je tražio DVA slota
    semafora. Pod opterećenjem (puna testna svita) prvi bi uzeo slot, a drugi
    pao na `reporting_db_busy` — i to je u praksi značilo da se ODBACI baš upis
    procjene, dakle autoritativan rezultat kontrolnog, dok je sporedni događaj
    aktivnosti prošao. Odbacivanje pod opterećenjem je namjerno, ali mora
    pogoditi CIJELI zahtjev ili ništa, nikad polovinu koja više vrijedi.

    Dva upisa unutar posla imaju ODVOJEN `try`: pad aktivnosti ne smije spriječiti
    upis procjene, ni obrnuto. Nijedan ne baca prema pozivaocu."""
    if not student_id or (not events and not assessment):
        return False
    if not config.reporting_db_configured() and database is None:
        return False
    target = database or get_database()

    def work():
        if events:
            try:
                target.record_learning_activity(student_id, events)
            except ReportingUnavailable as exc:
                logger.info("activity_write_failed code=%s", exc.code)
            except Exception as exc:
                logger.info("activity_write_failed code=unexpected:%s",
                            type(exc).__name__)
        if assessment:
            kind = assessment.get("kind")
            try:
                if kind == ASSESSMENT_GENERATED:
                    target.record_assessment_generated(
                        student_id, assessment["attempt"])
                elif kind == ASSESSMENT_COMPLETED:
                    target.record_assessment_completed(
                        student_id, assessment["attempt"],
                        assessment.get("items"))
            except ReportingUnavailable as exc:
                logger.info("assessment_%s_failed code=%s", kind, exc.code)
            except Exception as exc:
                logger.info("assessment_%s_failed code=unexpected:%s",
                            kind, type(exc).__name__)

    return _run_detached(work, "reporting_write")


def wait_for_pending_writes(timeout=5.0):
    """Cekaj da svi odvojeni izvjestajni upisi zavrse. Vraca True ako jesu.

    POSTOJI ZBOG IZMJERENE POJAVE: odvojene niti su `daemon` i prezive kraj
    zahtjeva (i kraj testa). U punoj testnoj sviti su zaostale niti dva puta
    oborile TUDJE testove - jednom utrku identiteta nad datotecnom bazom
    (SQLITE_BUSY), jednom vremenski osjetljiv tutorski test. Nije rijec o gresci
    u produkcijskoj logici nego o curenju resursa preko granice testa.

    Mehanizam je namjerno trivijalan: pokusaj zauzeti SVE slotove semafora. Kad
    uspije, po definiciji nijedan upis nije u letu. Nista se ne prekida i
    nijedan upis se ne gubi - samo se ceka.

    Za testove i uredno gasenje; zahtjevni put ovo NIKAD ne zove."""
    inflight = _get_inflight()
    limit = max(1, _inflight_limit or config.REPORTING_DB_MAX_INFLIGHT)
    acquired = 0
    deadline = time.monotonic() + timeout
    while acquired < limit and time.monotonic() < deadline:
        if inflight.acquire(blocking=False):
            acquired += 1
        else:
            time.sleep(0.01)
    for _ in range(acquired):
        inflight.release()
    return acquired == limit


def record_activity_blocking(student_id, events, database=None):
    """Isti upis, ali se ČEKA na ishod. Postoji SAMO za testove i dijagnostiku —
    nijedan zahtjevni put ga ne smije zvati, jer bi vratio mrežni put u turn."""
    if not events or not student_id:
        return False
    if not config.reporting_db_configured() and database is None:
        return False
    target = database or get_database()
    ok, value = _call_bounded(
        lambda: target.record_learning_activity(student_id, events),
        config.REPORTING_DB_TIMEOUT_S,
    )
    if ok:
        return True
    logger.info("activity_write_failed code=%s", value)
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
    if "v2_schema_verified" in report:
        problems = report.get("v2_schema_problems") or []
        lines.append("v2_schema: %s" % ("verified" if report["v2_schema_verified"]
                                        else "INCOMPLETE -> " + ", ".join(problems)))
    if "v3_schema_verified" in report:
        problems = report.get("v3_schema_problems") or []
        lines.append("v3_schema: %s" % ("verified" if report["v3_schema_verified"]
                                        else "INCOMPLETE -> " + ", ".join(problems)))
    if "v4_schema_verified" in report:
        problems = report.get("v4_schema_problems") or []
        lines.append("v4_schema: %s" % ("verified" if report["v4_schema_verified"]
                                        else "INCOMPLETE -> " + ", ".join(problems)))
    if "monthly_reports_ready" in report:
        problems = report.get("monthly_reports_problems") or []
        lines.append("monthly_reports: %s"
                     % ("ready" if report["monthly_reports_ready"]
                        else "UNUSABLE -> " + ", ".join(problems)))
    for table, columns in sorted((report.get("columns") or {}).items()):
        lines.append("columns[%s]: %s" % (table, ", ".join(columns)))
    return "\n".join(lines)


def main(argv=None):
    """`python -m matbot.reporting_db [--check] [--migrate]`

    SAMO CLI — dijagnostika se namjerno NE izlaže kroz `/healthz` (javni
    endpoint ne smije otkrivati stanje baze), a migracija se NIKAD ne pokreće iz
    web zahtjeva. Izlazni kod 0 znači: konekcija radi, sve tabele postoje i
    verzija šeme se poklapa.

    `--migrate` prvo primijeni nedostajuće verzije pa provjeri ishod, pa deploy
    ima jednu komandu koja ili dokaže ispravno stanje ili padne ne-nultim
    izlazom PRIJE nego što se živa usluga zamijeni."""
    import argparse
    import sys

    # TEKST OVOG POMOĆNIKA JE NAMJERNO BEZ DIJAKRITIKA (isti izbor kao
    # deploy/apply_release_env.sh): argparse ga piše na stdout, a konzola koja
    # nije UTF-8 (Windows cp1252) na `č` baca UnicodeEncodeError. Dijagnostika
    # koja pukne pri ispisu pomoći nije dijagnostika. Komentari i docstringovi
    # ostaju s dijakriticima — oni se nikad ne ispisuju.
    parser = argparse.ArgumentParser(
        prog="python -m matbot.reporting_db",
        description="Provjera i migracija izvjestajne baze. "
                    "--check samo cita; --migrate mijenja SEMU (nikad podatke).")
    parser.add_argument("--check", action="store_true",
                        help="provjeri kredencijale, konekciju, tabele i verziju seme")
    parser.add_argument("--migrate", action="store_true",
                        help="primijeni nedostajuce migracije seme, pa provjeri")
    args = parser.parse_args(argv)
    if not (args.check or args.migrate):
        parser.print_help()
        return 0

    if args.migrate:
        # MIGRACIJA PRIJE PROVJERE, u istom pozivu: deploy tako ima JEDNU
        # komandu koja ili dovede bazu u ispravno stanje i to dokaze, ili padne
        # ne-nultim izlazom prije nego sto se ziva usluga zamijeni.
        from matbot import reporting_schema

        try:
            applied = get_database().migrate()
        except reporting_schema.MigrationError as error:
            # STRUKTURNI kod, nikad sirovi tekst baze i nikad URL ni token.
            print("migration: FAILED -> %s" % error.code)
            return 1
        except ReportingUnavailable as error:
            print("migration: FAILED -> %s" % error.code)
            return 1
        print("migration: applied %s" % (", ".join("v%d" % v for v in applied)
                                         if applied else "nothing (already current)"))

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
