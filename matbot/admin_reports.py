"""Privatna administratorska stranica mjesečnih izvještaja (Faza 3B).

ŠTA JE OVDJE, A ŠTA NIJE: ovo je TANAK kontroler. Provjeri oblik zahtjeva,
pokupi bajtove, pozovi već dokazan Faza 3A sloj i prikaži rezultat. Nijedno
poslovno pravilo ne živi u pogledima — parsiranje CSV-a, normalizacija e-maila,
procenti, datumi, dinamičke sekcije, idempotentnost, razrješenje učenika, upsert
snimka i poređenje mjeseci ostaju u `matbot/thinkific_progress.py`,
`matbot/report_input.py` i `matbot/reporting_db.py`. Duplirati ih ovdje značilo
bi drugu, neprovjerenu implementaciju istog pravila.

IZOLACIJA: blueprint je potpuno odvojen od tutorskih ruta. Nijedan izuzetak
odavde ne može promijeniti Practice, Explain, Quick ni Kontrolni, jer s njima ne
dijeli ni stanje ni put izvršavanja.

MIGRACIJA SE NIKAD NE POKREĆE IZ WEB ZAHTJEVA. Ako je baza još na šemi v1,
stranica to KAŽE i onemogući uvoz; nadogradnja šeme ostaje svjesna operacija
pri deployu, ne nusproizvod prvog uploada.
"""
import csv
import hmac
import io
import logging
import os
import re
import threading
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor

from flask import (Blueprint, Response, abort, jsonify, redirect,
                   render_template, request, url_for)
from werkzeug.utils import secure_filename

from matbot import admin_auth, config, parent_report, report_input, reporting_db
from matbot import report_facts, student_grades
from matbot import report_pdf, report_prompt, reporting_schema
from matbot import thinkific_progress as progress
from matbot import thinkific_roster
from matbot import thinkific_upload
from matbot.admin_auth import CSRF_FORM_FIELD, require_admin
from matbot.ratelimit import RateLimiter

logger = logging.getLogger("matbot.admin_reports")

admin_reports_bp = Blueprint("admin_reports", __name__, url_prefix="/admin/reports")

# Kanonski interni slotovi ostaju isti. Novi upload ih popunjava sigurnim
# prepoznavanjem kursne strukture, a stara eksplicitna polja se još prihvataju
# radi kompatibilnosti direktnog legacy importa i postojećih klijenata.
COURSE_FIELDS = (
    ("grade_6", "6. razred", "Matematika za 6. razred"),
    ("grade_7", "7. razred", "Matematika za 7. razred"),
    ("grade_8", "8. razred", "Matematika za 8. razred"),
    ("grade_9", "9. razred", "Matematika za 9. razred"),
)

# Konzervativne granice. Stvarni izvoz 6. razreda je ~5,6 KB za 34 učenika, pa
# 2 MiB po fajlu pokriva i najveću školu s ogromnom rezervom, a spriječi da
# jedan pogrešan upload pojede memoriju procesa. Ukupna granica postoji jer se
# šalju do četiri fajla odjednom; iznad nje ionako udara `MAX_CONTENT_LENGTH`.
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_TOTAL_UPLOAD_BYTES = 4 * MAX_CSV_BYTES
ALLOWED_EXTENSIONS = (".csv",)
MULTI_FILE_FIELD = "thinkific_files"
MANUAL_COURSE_FIELD = "file_course_keys"

# Granice su namjerno niže od tehničkih mogućnosti servera. Pregledač šalje
# generisanje redom, jednog učenika po zahtjevu, a server svejedno odbija
# prevelik ili ručno sastavljen paket.
MAX_BULK_GENERATE = 50
MAX_BULK_EXPORT = 100
VALID_REPORT_GRADES = (6, 7, 8, 9)
ACCOUNT_TYPE_LABELS = {
    reporting_db.ACCOUNT_TYPE_STUDENT: "Redovni učenik",
    reporting_db.ACCOUNT_TYPE_SUPPORT: "Podrška",
    reporting_db.ACCOUNT_TYPE_TEST: "Test",
}

STATUS_FULL = "full"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

LOGIN_LIMITER_KEY = "MATBOT_ADMIN_LOGIN_LIMITER"

# Produkcija namjerno koristi jedan Gunicorn proces (vidi DEPLOYMENT.md), pa je
# procesni in-flight skup dovoljna granica protiv dva istovremena placena
# generisanja istog (ucenik, mjesec). Skup nosi samo interne brojeve i prazni se
# u `finally`; ne cuva profil, izvjestaj ni drugi studentski podatak.
_REPORT_GENERATION_INFLIGHT = set()
_REPORT_GENERATION_INFLIGHT_LOCK = threading.Lock()

# JEDNA procesna granica za SVE izvjestajne modelske pozive. Uzimanje je
# neblokirajuce: osam Gunicorn niti nikad ne stoji u neogranicenom redu cekajuci
# OpenAI. Bulk dodatno koristi jedan zajednicki executor i dva admission mjesta,
# pa istovremeni HTTP zahtjevi ne mogu svaki napraviti vlastiti pool/red niti
# zaobici granicu. Oba semafora se oslobadjaju iskljucivo u `finally` blokovima.
_REPORT_MODEL_SLOTS = threading.BoundedSemaphore(
    config.REPORTING_MAX_ACTIVE_MODEL_CALLS)
_REPORT_BULK_ADMISSION = threading.BoundedSemaphore(
    config.REPORTING_MAX_ACTIVE_MODEL_CALLS)
_REPORT_BULK_EXECUTOR = ThreadPoolExecutor(
    max_workers=config.REPORTING_MAX_ACTIVE_MODEL_CALLS,
    thread_name_prefix="matbot-report-bulk")

_ROSTER_FAILURE_PHASES = frozenset((
    "build_plan", "select_actions", "schema_preflight", "validate_targets",
    "validate_action", "freeze_historical_names", "create_students",
    "update_name_or_status", "confirm_grade", "commit",
))
_ROSTER_ACTION_TYPES = frozenset((
    "name", "add", "archive", "reactivate", "grade",
))


def _safe_diagnostic_token(value, allowed, fallback):
    """Zatvorena lista za log polja koja nikad ne smiju nositi korisnički tekst."""
    return value if value in allowed else fallback


def _safe_exception_type(error):
    """Samo Python ime klase; proizvoljna poruka izuzetka ostaje odbačena."""
    name = type(error).__name__
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:80]
    return cleaned or "Exception"


def _safe_traceback_frames(error):
    """Traceback bez poruke, lokalnih vrijednosti, putanje i izvornog reda.

    Standardni ``logger.exception`` na DB uzroku nije siguran: završni red
    uključuje sirovu poruku biblioteke, koja može sadržati URL, token ili PII.
    Ovdje ostaju samo basename fajla, funkcija i broj reda.
    """
    frames = traceback.extract_tb(getattr(error, "__traceback__", None))
    if not frames:
        return "none"
    return " > ".join(
        "%s:%s:%s" % (os.path.basename(frame.filename), frame.name, frame.lineno)
        for frame in frames[-12:]
    )


def _limiter():
    """Prijava se ograničava po stopi — lozinka ne smije biti brzo pogodiva.

    Limiter živi u `current_app.config`, isti obrazac kao tutorski limiteri u
    `matbot/api.py`. Modul-globalni singleton bi dijelio brojače kroz cijeli
    proces, pa bi jedan test iscrpio kvotu svim ostalima (izmjereno)."""
    from flask import current_app

    limiter = current_app.config.get(LOGIN_LIMITER_KEY)
    if limiter is None:
        limiter = RateLimiter(per_minute=5, per_hour=30)
        current_app.config[LOGIN_LIMITER_KEY] = limiter
    return limiter


def _client_ip():
    return request.remote_addr or "unknown"


# ---------------------------------------------------------------------------
# Stanje šeme
# ---------------------------------------------------------------------------
def schema_state():
    """`("ready"|"upgrade_required"|"unavailable", poruka)` — bez detalja baze.

    Administrator mora znati MOŽE li uvoziti, ali ne smije dobiti tekst
    izuzetka baze (CLAUDE.md, tačka 7: interni kodovi idu samo u log)."""
    if not config.reporting_db_configured():
        return "unavailable", "Izvještajna baza nije konfigurisana na serveru."
    try:
        report = reporting_db.get_database().check_reports_readiness()
    except Exception:
        logger.info("admin_schema_check_failed")
        return "unavailable", "Izvještajna baza trenutno nije dostupna."
    if not report.get("connected"):
        return "unavailable", "Izvještajna baza trenutno nije dostupna."
    missing = [name for name in reporting_schema.V2_TABLES
               if name in (report.get("missing_tables") or [])]
    version = report.get("schema_version")
    if missing or (version is not None and version < reporting_schema.SCHEMA_VERSION_V2):
        return ("upgrade_required",
                "Izvještajna baza je na šemi v%s. Potrebna je nadogradnja na v%s "
                "prije uvoza." % (version, reporting_schema.SCHEMA_VERSION_V2))
    return "ready", ""


# ---------------------------------------------------------------------------
# Prijava
# ---------------------------------------------------------------------------
@admin_reports_bp.route("/login", methods=["GET", "POST"])
def login():
    if not admin_auth.admin_enabled():
        # Bez konfigurisane lozinke stranica se ponaša kao da ne postoji.
        abort(404)
    if request.method == "GET":
        if admin_auth.is_authenticated():
            return redirect(url_for("admin_reports.index"))
        return render_template("admin_login.html",
                               csrf_token=admin_auth.csrf_token(), error="")

    allowed, retry_after = _limiter().check("admin_login:" + _client_ip())
    if not allowed:
        logger.info("admin_login_rate_limited retry_after=%s", retry_after)
        return render_template("admin_login.html",
                               csrf_token=admin_auth.csrf_token(),
                               error="Previše pokušaja. Sačekaj pa pokušaj ponovo."), 429

    if not admin_auth.csrf_valid(request.form.get(CSRF_FORM_FIELD)):
        logger.info("admin_login_csrf_rejected")
        return render_template("admin_login.html",
                               csrf_token=admin_auth.csrf_token(),
                               error="Sigurnosna provjera nije prošla. Pokušaj ponovo."), 400

    if not admin_auth.verify_password(request.form.get("password", "")):
        # Log NIKAD ne nosi ni pokušanu lozinku ni njen dio.
        logger.info("admin_login_failed ip_bucket=set")
        return render_template("admin_login.html",
                               csrf_token=admin_auth.csrf_token(),
                               error="Pogrešna lozinka."), 401

    admin_auth.start_session()
    logger.info("admin_login_ok")
    return redirect(url_for("admin_reports.index"))


@admin_reports_bp.route("/logout", methods=["POST"])
@require_admin
def logout():
    admin_auth.end_session()
    return redirect(url_for("admin_reports.login"))


# ---------------------------------------------------------------------------
# Stranica
# ---------------------------------------------------------------------------
@admin_reports_bp.route("", methods=["GET"])
@admin_reports_bp.route("/", methods=["GET"])
@require_admin
def index():
    started = time.perf_counter()
    state, message = schema_state()
    month = _default_month()
    rendered = render_template(
        "admin_reports.html",
        csrf_token=admin_auth.csrf_token(),
        course_fields=COURSE_FIELDS,
        month=month,
        overview=_overview(month),
        schema_state=state,
        schema_message=message,
        summary=None,
        outcome=None,
        errors=[],
    )
    logger.info(
        "reports_timing operation=dashboard status=ok total_ms=%s "
        "payload_bytes=%s",
        int((time.perf_counter() - started) * 1000),
        len(rendered.encode("utf-8")))
    return rendered


def _overview(month):
    """Brojevi za nadzornu ploču. SAMO ono što je već jeftino dostupno.

    Namjerno bez ijednog ukrasnog upita: svaka stavka odgovara na pitanje „šta
    treba uraditi", a ne „koje tabele postoje". Kad baza nije dostupna, ploča
    prikazuje prazno stanje umjesto da sruši stranicu — dijagnostika ima svoju
    komandu."""
    overview = {"classes": None, "students": None, "unconfirmed": None,
                "report_total": None, "reports_saved": None,
                "reports_missing": None, "available": False}
    try:
        database = reporting_db.get_database()
        start, end = report_input.month_bounds(month)
        overview["classes"] = database.count_classes_in_range(start[:10],
                                                              end[:10])
        listed = database.list_students(
            active=True, account_type=reporting_db.ACCOUNT_TYPE_STUDENT)
        overview["students"] = len(listed)
        overview["unconfirmed"] = sum(
            1 for row in listed
            if not student_grades.is_confirmed_grades(row.get("grades")))
        population = set(report_input.report_population(month, database=database))
        overview["report_total"] = len(population)
        try:
            saved = database.fetch_monthly_report_student_ids(month)
        except reporting_db.ReportingUnavailable:
            saved = None
        if saved is not None:
            overview["reports_saved"] = len(population & saved)
            overview["reports_missing"] = (
                overview["report_total"] - overview["reports_saved"])
        overview["available"] = True
    except Exception:
        logger.info("admin_overview_unavailable")
    return overview


def _default_month():
    """Samo UDOBNOST u UI-ju. Poslana vrijednost ostaje autoritativna i server
    je iznova validira — mjesec se NIKAD ne izvodi iz vremena uploada."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return "%04d-%02d" % (now.year, now.month)


def _month_label(month):
    """Kratka lokalizovana oznaka mjeseca isključivo za prikaz."""
    names = ("Januar", "Februar", "Mart", "April", "Maj", "Juni",
             "Juli", "August", "Septembar", "Oktobar", "Novembar", "Decembar")
    try:
        year, number = month.split("-", 1)
        return "%s %s" % (names[int(number) - 1], year)
    except (AttributeError, IndexError, ValueError):
        return month


# ---------------------------------------------------------------------------
# Uvoz
# ---------------------------------------------------------------------------
def _collect_files(files_storage):
    """Legacy eksplicitna polja → bajtovi. Vraća `(files, errors)`.

    Ime fajla se NE gleda ni za razred ni za putanju — koristi se samo kao
    prikaz i to sanitizovano. Time otpadaju i „path traversal" i podmetanje
    razreda kroz naziv."""
    files, errors = {}, []
    total = 0
    for course_key, label, _course_name in COURSE_FIELDS:
        storage = files_storage.get(course_key)
        if storage is None or not (storage.filename or "").strip():
            continue
        filename = (storage.filename or "").strip()
        if not filename.lower().endswith(ALLOWED_EXTENSIONS):
            errors.append({"course_key": course_key, "label": label,
                           "code": "extension_not_csv"})
            continue
        raw = storage.read(MAX_CSV_BYTES + 1)
        if not raw or not raw.strip():
            errors.append({"course_key": course_key, "label": label,
                           "code": "file_empty"})
            continue
        if len(raw) > MAX_CSV_BYTES:
            errors.append({"course_key": course_key, "label": label,
                           "code": "file_too_large"})
            continue
        total += len(raw)
        if total > MAX_TOTAL_UPLOAD_BYTES:
            errors.append({"course_key": course_key, "label": label,
                           "code": "upload_total_too_large"})
            continue
        files[course_key] = raw
    return files, errors


def _display_filename(value, index):
    """Naziv samo za prikaz; putanja i kontrolni znakovi nikad ne izlaze."""
    cleaned = secure_filename((value or "").strip())
    return (cleaned or "fajl-%d.csv" % (index + 1))[:180]


def _multi_upload_items(files_storage):
    """Jedno ``multiple`` polje → ograničeni bajtovi sa stabilnim indeksom."""
    storages = files_storage.getlist(MULTI_FILE_FIELD)
    storages = [storage for storage in storages
                if storage is not None and (storage.filename or "").strip()]
    if not storages:
        return [], [{"message": "Odaberi Thinkific CSV fajlove."}]
    if len(storages) > len(COURSE_FIELDS):
        return [], [{
            "message": "Možeš učitati najviše četiri CSV fajla.",
        }]

    items, errors = [], []
    total = 0
    for index, storage in enumerate(storages):
        filename = _display_filename(storage.filename, index)
        item = {"index": index, "filename": filename, "raw": None,
                "error": None}
        if not (storage.filename or "").lower().endswith(ALLOWED_EXTENSIONS):
            item["error"] = "Fajl mora biti u CSV formatu."
        else:
            raw = storage.read(MAX_CSV_BYTES + 1)
            if not raw or not raw.strip():
                item["error"] = "Fajl je prazan."
            elif len(raw) > MAX_CSV_BYTES:
                item["error"] = "Fajl je prevelik."
            else:
                total += len(raw)
                if total > MAX_TOTAL_UPLOAD_BYTES:
                    item["error"] = "Ukupan upload je prevelik."
                else:
                    item["raw"] = raw
        if item["error"]:
            errors.append({"label": filename, "message": item["error"]})
        items.append(item)
    return items, errors


def _classify_multi_uploads(month, files_storage, manual_keys=(), *,
                            require_all):
    """Prepoznaj fajlove pa ih vrati u POSTOJEĆEM ``{grade_N: bytes}`` obliku.

    Ručni izbor se smije koristiti samo kad puni kursni potpis nije poznat.
    Automatsko podudaranje se ne može prepisati klijentskom vrijednošću.
    """
    items, errors = _multi_upload_items(files_storage)
    choices = list(manual_keys or ())
    if choices and len(choices) != len(items):
        errors.append({
            "message": "Mapiranje fajlova nije potpuno. Ponovi odabir fajlova.",
        })
    choices += [""] * max(0, len(items) - len(choices))

    entries = []
    resolved = []
    parsed_by_index = {}
    for item in items:
        index, filename, raw = item["index"], item["filename"], item["raw"]
        entry = {"index": index, "filename": filename, "status": "invalid",
                 "course_key": None, "grade": None}
        if raw is None:
            entries.append(entry)
            continue
        try:
            detected = thinkific_upload.detect_course_key(raw, month)
        except progress.ProgressFormatError:
            message = "Fajl nije ispravan Thinkific Student Progress CSV."
            entry["message"] = message
            errors.append({"label": filename, "message": message})
            entries.append(entry)
            continue

        choice = choices[index].strip() if index < len(choices) else ""
        if choice and choice not in progress.COURSE_SLOTS:
            message = "Odabrani razred nije ispravan."
            entry["message"] = message
            errors.append({"label": filename, "message": message})
            entries.append(entry)
            continue
        if detected and choice and choice != detected:
            message = "Automatski prepoznat razred se ne može prepisati."
            entry["message"] = message
            errors.append({"label": filename, "message": message})
            entries.append(entry)
            continue

        course_key = detected or choice or None
        if course_key is None:
            message = "Nije moguće automatski odrediti razred za ovaj fajl."
            entry.update(status="ambiguous", message=message)
            errors.append({"label": filename, "message": message})
            entries.append(entry)
            continue

        try:
            parsed_by_index[index] = progress.parse_progress_csv(
                raw, course_key, month)
        except progress.ProgressFormatError:
            message = "Fajl nije ispravan Thinkific Student Progress CSV."
            entry["message"] = message
            errors.append({"label": filename, "message": message})
            entries.append(entry)
            continue
        entry.update(status="detected" if detected else "manual",
                     course_key=course_key,
                     grade=progress.COURSE_SLOTS[course_key]["grade"])
        entries.append(entry)
        resolved.append((course_key, item, entry))

    grouped = {}
    for course_key, item, entry in resolved:
        grouped.setdefault(course_key, []).append((item, entry))
    for course_key, matches in grouped.items():
        if len(matches) > 1:
            grade = progress.COURSE_SLOTS[course_key]["grade"]
            errors.append({
                "message": "Za %d. razred pronađena su dva fajla." % grade,
            })
            for _item, entry in matches:
                entry["status"] = "duplicate"

    if require_all and items:
        for course_key, _label, _name in COURSE_FIELDS:
            if course_key not in grouped:
                grade = progress.COURSE_SLOTS[course_key]["grade"]
                errors.append({
                    "message": "Nedostaje CSV za %d. razred." % grade,
                })

    files, parsed = {}, {}
    for course_key, matches in grouped.items():
        if len(matches) != 1:
            continue
        item, _entry = matches[0]
        files[course_key] = item["raw"]
        parsed[course_key] = parsed_by_index[item["index"]]

    grades = []
    for course_key, _label, _name in COURSE_FIELDS:
        matches = grouped.get(course_key, ())
        grade = progress.COURSE_SLOTS[course_key]["grade"]
        grades.append({
            "course_key": course_key,
            "grade": grade,
            "status": ("found" if len(matches) == 1 else
                       "duplicate" if len(matches) > 1 else "missing"),
            "filename": matches[0][0]["filename"] if len(matches) == 1 else "",
        })

    unique_errors = []
    seen_messages = set()
    for error in errors:
        key = (error.get("label", ""), error.get("message", ""))
        if key not in seen_messages:
            seen_messages.add(key)
            unique_errors.append(error)
    ready = bool(files) and not unique_errors
    if require_all:
        ready = ready and set(files) == set(thinkific_roster.REQUIRED_COURSES)
    return {
        "files": files, "parsed": parsed, "entries": entries,
        "grades": grades, "errors": unique_errors, "ready": ready,
    }


def _outcome(summary, blocked_count):
    """Djelimičan uspjeh se NIKAD ne smije prikazati kao uspjeh (Dio 10)."""
    failed = (blocked_count + summary.students_skipped
              + sum(1 for f in summary.files if f["status"] != "imported"))
    if summary.files_imported and not failed:
        return STATUS_FULL
    if summary.files_imported and failed:
        return STATUS_PARTIAL
    return STATUS_FAILED


@admin_reports_bp.route("/import", methods=["POST"])
@require_admin
def import_files():
    state, message = schema_state()
    if state != "ready":
        # PADA ZATVORENO: nikad se ne kreira tabela iz web zahtjeva.
        logger.info("admin_import_blocked reason=%s", state)
        return _render_result(None, STATUS_FAILED, [{"code": state,
                                                     "message": message}],
                              month=request.form.get("report_month", ""),
                              schema=(state, message)), 409

    raw_month = request.form.get("report_month", "")
    try:
        month = progress.parse_report_month(raw_month)
    except progress.ProgressFormatError:
        return _render_result(None, STATUS_FAILED,
                              [{"code": "report_month_invalid",
                                "message": "Mjesec mora biti u obliku YYYY-MM."}],
                              month=raw_month, schema=(state, message)), 400

    if request.files.getlist(MULTI_FILE_FIELD):
        mapped = _classify_multi_uploads(
            month, request.files, request.form.getlist(MANUAL_COURSE_FIELD),
            require_all=False)
        files, upload_errors = mapped["files"], mapped["errors"]
    else:
        files, upload_errors = _collect_files(request.files)
    if not files:
        code = upload_errors[0].get("code", "upload_invalid") \
            if upload_errors else "no_file_selected"
        return _render_result(None, STATUS_FAILED,
                              upload_errors or [{"code": code,
                                                 "message": "Odaberi bar jedan CSV."}],
                              month=month, schema=(state, message)), 400

    # SVA logika je u Fazi 3A. Ovdje se ne parsira nijedan red.
    # Legacy put smije samo dopuniti napredak VEĆ ODOBRENIH identiteta.
    # Novi učenik nastaje isključivo kroz roster preview/apply izbor osoblja.
    summary = report_input.import_progress_files(
        month, files, create_missing=False)
    outcome = _outcome(summary, len(upload_errors))
    logger.info("admin_import month=%s files=%s imported=%s rows=%s outcome=%s",
                month, summary.files_received, summary.files_imported,
                summary.rows_seen, outcome)
    return _render_result(summary, outcome, upload_errors, month=month,
                          schema=(state, message))


def _render_result(summary, outcome, upload_errors, *, month, schema):
    state, schema_message = schema
    return render_template(
        "admin_reports.html",
        csrf_token=admin_auth.csrf_token(),
        course_fields=COURSE_FIELDS,
        month=month or _default_month(),
        schema_state=state,
        schema_message=schema_message,
        summary=summary.as_dict() if summary is not None else None,
        outcome=outcome,
        errors=upload_errors or [],
        labels={key: label for key, label, _ in COURSE_FIELDS},
    )


def _roster_sources():
    """Četiri uploada → sirovi fajlovi i četiri potpuno validirana objekta."""
    try:
        month = progress.parse_report_month(request.form.get("report_month", ""))
    except progress.ProgressFormatError:
        return None, None, "Mjesec mora biti u obliku YYYY-MM."

    if request.files.getlist(MULTI_FILE_FIELD):
        mapped = _classify_multi_uploads(
            month, request.files, request.form.getlist(MANUAL_COURSE_FIELD),
            require_all=True)
        if not mapped["ready"]:
            messages = [error.get("message", "")
                        for error in mapped["errors"] if error.get("message")]
            return None, None, " ".join(messages[:3]) or (
                "Mapiranje Thinkific fajlova nije potpuno.")
        return mapped["files"], mapped["parsed"], None

    files, upload_errors = _collect_files(request.files)
    missing = [key for key, _label, _name in COURSE_FIELDS if key not in files]
    if upload_errors or missing:
        return None, None, (
            "Za pregled registra odaberi sva četiri važeća CSV fajla.")
    try:
        parsed = {key: progress.parse_progress_csv(files[key], key, month)
                  for key, _label, _name in COURSE_FIELDS}
    except progress.ProgressFormatError as error:
        logger.info("thinkific_roster_rejected code=%s row=%s",
                    error.code, error.row)
        return None, None, (
            "Jedan CSV nije ispravan. Provjeri fajlove i pokušaj ponovo.")
    return files, parsed, None


@admin_reports_bp.route("/roster/classify", methods=["POST"])
@require_admin
def classify_roster_files():
    """Read-only provjera jednog višestrukog uploada prije roster pregleda."""
    _require_csrf()
    try:
        month = progress.parse_report_month(request.form.get("report_month", ""))
    except progress.ProgressFormatError:
        return jsonify({"status": "blocked", "ready": False,
                        "message": "Mjesec mora biti u obliku YYYY-MM.",
                        "files": [], "grades": [], "errors": [
                            {"message": "Mjesec mora biti u obliku YYYY-MM."}]}), 400

    mapped = _classify_multi_uploads(
        month, request.files, (), require_all=True)
    needs_mapping = any(entry["status"] == "ambiguous"
                        for entry in mapped["entries"])
    status = ("ready" if mapped["ready"] else
              "needs_mapping" if needs_mapping else "blocked")
    return jsonify({
        "status": status,
        "ready": mapped["ready"],
        "files": mapped["entries"],
        "grades": mapped["grades"],
        "errors": mapped["errors"],
        "message": (
            "Sva četiri razreda su sigurno prepoznata."
            if mapped["ready"] else
            "Dopuni ili ispravi mapiranje fajlova prije pregleda promjena."
        ),
    })


@admin_reports_bp.route("/roster/preview", methods=["POST"])
@require_admin
def preview_roster():
    """Read-only diff četiri trenutna izvoza prema MAT-BOT registru."""
    _require_csrf()
    state, _message = schema_state()
    if state != "ready":
        return jsonify({"status": "error",
                        "message": "Izvještajna baza trenutno nije spremna."}), 409
    _files, parsed, error = _roster_sources()
    if error:
        return jsonify({"status": "error", "message": error}), 400
    try:
        plan = thinkific_roster.build_plan(parsed, reporting_db.get_database())
    except (reporting_db.ReportingUnavailable,
            thinkific_roster.RosterPlanError) as caught:
        logger.info("thinkific_roster_preview_failed code=%s",
                    getattr(caught, "code", type(caught).__name__))
        return jsonify({"status": "error",
                        "message": "Pregled promjena trenutno nije dostupan."}), 503
    payload = plan.public()
    payload["status"] = "ready" if plan.apply_allowed else "blocked"
    return jsonify(payload)


@admin_reports_bp.route("/roster/apply", methods=["POST"])
@require_admin
def apply_roster():
    """Ponovo pročitaj izvore, potvrdi plan i primijeni samo označene akcije."""
    _require_csrf()
    state, _message = schema_state()
    if state != "ready":
        return jsonify({"status": "error",
                        "message": "Izvještajna baza trenutno nije spremna."}), 409
    files, parsed, error = _roster_sources()
    if error:
        return jsonify({"status": "error", "message": error}), 400
    database = reporting_db.get_database()
    phase = "build_plan"
    try:
        plan = thinkific_roster.build_plan(parsed, database)
        supplied = request.form.get("plan_id", "")
        if (not plan.apply_allowed or not supplied
                or not hmac.compare_digest(supplied, plan.plan_id or "")):
            logger.info("thinkific_roster_apply_refused code=plan_mismatch")
            return jsonify({
                "status": "error",
                "message": (
                    "Podaci su se promijenili nakon pregleda. Napravi novi "
                    "pregled prije primjene."),
            }), 409
        phase = "select_actions"
        actions = thinkific_roster.selected_actions(
            plan, request.form.getlist("action_keys"))
        phase = "schema_preflight"
        applied = database.apply_roster_reconciliation(actions)
    except thinkific_roster.RosterPlanError as caught:
        logger.info("thinkific_roster_apply_refused code=%s", caught.code)
        return jsonify({"status": "error",
                        "message": "Izbor promjena nije ispravan."}), 400
    except reporting_db.ReportingUnavailable as caught:
        cause = caught.cause or caught
        failure_phase = _safe_diagnostic_token(
            caught.phase or phase, _ROSTER_FAILURE_PHASES, "unknown")
        action_type = _safe_diagnostic_token(
            caught.action_type, _ROSTER_ACTION_TYPES, "none")
        logger.error(
            "thinkific_roster_apply_failed code=%s phase=%s action_type=%s "
            "exception_type=%s traceback=%s",
            caught.code, failure_phase, action_type,
            _safe_exception_type(cause), _safe_traceback_frames(cause))
        return jsonify({"status": "error",
                        "message": "Promjene nisu sačuvane. Pokušaj ponovo."}), 503

    # Fajlovi su upravo drugi put potpuno validirani. Uvoz napretka ne smije
    # ponovo kreirati isključene nove učenike niti prepisati neoznačena imena.
    imported = report_input.import_progress_files(
        plan.report_month, files, database=database, create_missing=False,
        refresh_existing_names=False)
    fully_imported = imported.files_imported == len(thinkific_roster.REQUIRED_COURSES)
    if not fully_imported:
        logger.info("thinkific_roster_progress_partial imported=%s",
                    imported.files_imported)
    return jsonify({
        "status": "applied" if fully_imported else "partial",
        "message": (
            "Registar je osvježen i napredak je uvezen."
            if fully_imported else
            "Registar je osvježen, ali dio napretka nije uvezen."),
        "applied": applied,
        "import": imported.as_dict(),
    })


# ---------------------------------------------------------------------------
# Populacija i pregled
# ---------------------------------------------------------------------------
def _student_filters(args):
    """Jedan provjeren skup filtera za HTML listu i CSV izvoz."""
    raw_grade = (args.get("grade") or "").strip()
    if raw_grade:
        try:
            grade = int(raw_grade)
        except ValueError:
            raise ValueError("grade") from None
        if grade not in VALID_REPORT_GRADES:
            raise ValueError("grade")
    else:
        grade = None

    raw_confirmed = (args.get("grade_status") or "all").strip().lower()
    if raw_confirmed not in ("all", "confirmed", "unconfirmed"):
        raise ValueError("grade_status")
    confirmed = {"all": None, "confirmed": True,
                 "unconfirmed": False}[raw_confirmed]
    search = (args.get("search") or "").strip()
    if len(search) > 120:
        raise ValueError("search")
    return {
        "grade": grade,
        "grade_status": raw_confirmed,
        "confirmed": confirmed,
        "search": search,
        # Redovni učenici su uvijek uključeni. Posebni nalozi se prikazuju
        # samo na izričit zahtjev i ostaju nepodobni za novi izvještaj.
        "include_support": args.get("include_support") == "1",
        "include_test": args.get("include_test") == "1",
    }


def _filter_account_types(filters):
    values = [reporting_db.ACCOUNT_TYPE_STUDENT]
    if filters["include_support"]:
        values.append(reporting_db.ACCOUNT_TYPE_SUPPORT)
    if filters["include_test"]:
        values.append(reporting_db.ACCOUNT_TYPE_TEST)
    return tuple(values)


def _filtered_report_rows(month, filters, database):
    """Isti redovi hrane tabelu, CSV i izbor u pregledaču."""
    account_types = _filter_account_types(filters)
    population = set(report_input.report_population(
        month, database=database, account_types=account_types))
    registry = database.list_students(
        search=filters["search"], grade=filters["grade"],
        confirmed=filters["confirmed"], active=True)
    registry = [row for row in registry
                if row["student_id"] in population
                and row.get("account_type") in account_types]
    try:
        summaries = database.fetch_monthly_report_summaries(month)
    except reporting_db.ReportingUnavailable:
        summaries = None

    start, end = report_input.month_bounds(month)
    metrics = database.fetch_report_roster_metrics(
        [row["student_id"] for row in registry], month,
        report_input.previous_month(month), start, end)

    rows = []
    for student in registry:
        student_id = student["student_id"]
        summary_metrics = metrics[student_id]
        report_summary = (None if summaries is None
                          else summaries.get(student_id))
        account_type = (student.get("account_type")
                        or reporting_db.ACCOUNT_TYPE_STUDENT)
        rows.append({
            "student_id": student_id,
            "label": _student_label(student, student_id),
            "grade": student.get("grade"),
            "grades": student.get("grades") or [],
            "grade_confirmed": student_grades.is_confirmed_grades(
                student.get("grades")),
            "account_type": account_type,
            "reporting_enabled": account_type == reporting_db.ACCOUNT_TYPE_STUDENT,
            "has_snapshot": summary_metrics["has_snapshot"],
            "percent_completed": summary_metrics["percent_completed"],
            "delta_percent_completed": summary_metrics[
                "delta_percent_completed"],
            "practice_tasks": summary_metrics["practice_tasks"],
            "kontrolni_attempts": summary_metrics["kontrolni_attempts"],
            "has_report": (None if summaries is None
                           else report_summary is not None),
            "generated_at": (report_summary or {}).get("generated_at"),
            "updated_at": (report_summary or {}).get("updated_at"),
        })
    return rows


@admin_reports_bp.route("/students", methods=["GET"])
@require_admin
def students():
    started = time.perf_counter()
    state, message = schema_state()
    raw_month = request.args.get("month", "")
    try:
        month = progress.parse_report_month(raw_month)
    except progress.ProgressFormatError:
        return render_template("admin_students.html", month=raw_month, rows=[],
                               schema_state=state, schema_message=message,
                               error="Mjesec mora biti u obliku YYYY-MM."), 400
    if state != "ready":
        return render_template("admin_students.html", month=month, rows=[],
                               schema_state=state, schema_message=message,
                               error=message), 409

    database = reporting_db.get_database()
    try:
        filters = _student_filters(request.args)
    except ValueError:
        return render_template("admin_students.html", month=month, rows=[],
                               filters={}, csrf_token=admin_auth.csrf_token(),
                               schema_state=state, schema_message=message,
                               error="Filter nije ispravan."), 400
    rows = _filtered_report_rows(month, filters, database)
    rendered = render_template(
        "admin_students.html", month=month, rows=rows,
        filters=filters, csrf_token=admin_auth.csrf_token(),
        max_bulk_generate=MAX_BULK_GENERATE,
        bulk_request_size=config.REPORTING_BULK_REQUEST_SIZE,
        schema_state=state, schema_message=message, error="")
    logger.info(
        "reports_timing operation=roster status=ok rows=%s total_ms=%s "
        "payload_bytes=%s",
        len(rows), int((time.perf_counter() - started) * 1000),
        len(rendered.encode("utf-8")))
    return rendered


@admin_reports_bp.route("/students.csv", methods=["GET"])
@require_admin
def students_csv():
    """CSV koristi potpuno isti mjesec i filtere kao radna lista."""
    state, _message = schema_state()
    if state != "ready":
        abort(409)
    try:
        month = progress.parse_report_month(request.args.get("month", ""))
        filters = _student_filters(request.args)
    except (progress.ProgressFormatError, ValueError):
        abort(400)
    rows = _filtered_report_rows(month, filters, reporting_db.get_database())

    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(("Učenik", "Razred", "Vrsta naloga", "Status razreda",
                     "Thinkific napredak", "MAT-BOT zadaci", "Kontrolni",
                     "Status izvještaja", "Period"))
    for row in rows:
        grades = "+".join(str(value) for value in row["grades"])
        writer.writerow((
            row["label"], grades,
            ACCOUNT_TYPE_LABELS.get(row["account_type"], row["account_type"]),
            "Potvrđen" if row["grade_confirmed"] else "Nije potvrđen",
            ("" if row["percent_completed"] is None
             else "%g%%" % row["percent_completed"]),
            row["practice_tasks"], row["kontrolni_attempts"],
            "Sačuvan" if row["has_report"] else "Nema izvještaja", month))
    data = ("\ufeff" + stream.getvalue()).encode("utf-8")
    return Response(data, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition":
            'attachment; filename="ucenici-izvjestaji-%s.csv"' % month,
    })


# NOV IZVJEŠTAJ TRAŽI POTVRĐEN TEKUĆI RAZRED (verzija 4). Poruka je za
# administratora i ne nosi interni kod (pravilo 7). Stari sačuvani izvještaji se
# ovim ne diraju — čitaju se i preuzimaju kao i do sada.
ERROR_GRADE_UNCONFIRMED = (
    "Trenutni razred učenika nije potvrđen. Potvrdite razred na profilu učenika "
    "prije generisanja novog izvještaja.")
ERROR_REPORT_DISABLED = "Izvještaji su isključeni za PODRŠKA i TEST naloge."
ERROR_GENERATION_IN_PROGRESS = (
    "Izvještaj za ovog učenika se već generiše. Sačekajte završetak prije "
    "novog pokušaja.")
ERROR_GENERATION_BUSY = (
    "Generisanje izvještaja je trenutno zauzeto. Sačekajte da se aktivna "
    "generisanja završe pa pokušajte ponovo.")


def _grade_confirmed(payload):
    """Je li tekući razred POTVRĐEN? Čita se iz profila, nikad iz sadržaja.

    Razred kursa (Thinkific), razred kontrolnog i razred iz MAT-BOT aktivnosti
    su OPAŽANJA o gradivu i ovdje se svjesno ne gledaju — izvještaj za roditelja
    na naslovnici tvrdi koji razred dijete pohađa."""
    return bool((payload.get("profile") or {}).get("grade_confirmed"))


def _student_label(profile, student_id):
    name = (profile.get("display_name") or "").strip()
    # Bez imena se NIKAD ne pada nazad na e-mail — koristi se neutralna oznaka.
    return name or ("Učenik #%d" % student_id)


@admin_reports_bp.route("/student/<int:student_id>", methods=["GET"])
@require_admin
def student_preview(student_id):
    state, message = schema_state()
    raw_month = request.args.get("month", "")
    try:
        month = progress.parse_report_month(raw_month)
    except progress.ProgressFormatError:
        abort(400)
    if state != "ready":
        return render_template("admin_student.html", month=month, payload=None,
                               label="", schema_message=message), 409

    payload = report_input.build_report_input(student_id, month)
    return _render_student(student_id, month, payload)


def _render_student(student_id, month, payload, *, ai_error="", notice=""):
    """Jedan predložak za sve ishode — pregled, generisanje, snimanje.

    OTVARANJE STRANICE NE ZOVE MODEL (Dio 32). Ovdje se sačuvani nacrt samo
    ČITA; ako ga nema, prikazuju se determinističke činjenice i dugme."""
    try:
        saved = parent_report.load_saved(student_id, month)
    except reporting_db.ReportingUnavailable as error:
        # Tabela `monthly_reports` ne podnosi Fazu 3C ili je baza pala.
        # Činjenice ostaju upotrebljive — izvještaj je taj koji nije dostupan.
        logger.info("admin_report_load_failed code=%s", error.code)
        saved = None
        ai_error = ai_error or parent_report.SAFE_AI_ERROR
    current_label = _student_label(payload["profile"], student_id)
    saved_label = (((saved or {}).get("snapshot") or {}).get("student")
                   or {}).get("label")
    return render_template(
        "admin_student.html", month=month, payload=payload,
        label=saved_label or current_label,
        month_label=_month_label(month),
        previous_month=report_input.previous_month(month), schema_message="",
        saved=saved, csrf_token=admin_auth.csrf_token(),
        # Dugme za generisanje se ne nudi bez potvrđenog razreda; server to
        # svejedno provjerava ponovo u `generate_report` — predložak nije
        # zaštita nego objašnjenje.
        grade_confirmed=_grade_confirmed(payload),
        grade_unconfirmed_message=ERROR_GRADE_UNCONFIRMED,
        reporting_enabled=(payload.get("profile") or {}).get(
            "reporting_enabled", True),
        report_disabled_message=ERROR_REPORT_DISABLED,
        narrative_fields=parent_report.NARRATIVE_FIELD_SPECS,
        max_parent_comment_chars=(
            parent_report.MAX_EDITABLE_PARENT_COMMENT_CHARS),
        ai_error=ai_error, notice=notice)


def _require_csrf():
    if not admin_auth.csrf_valid(request.form.get(CSRF_FORM_FIELD)):
        logger.info("admin_report_csrf_rejected")
        abort(400)


def _month_or_400():
    try:
        return progress.parse_report_month(request.args.get("month", ""))
    except progress.ProgressFormatError:
        abort(400)


def _narrative_from_form():
    """Ono što je administrator otkucao. Server ne dopisuje ništa svoje."""
    raw = {}
    for field in parent_report.NARRATIVE_FIELD_SPECS:
        value = request.form.get(field["name"], "")
        if field["kind"] == "items":
            value = [line.strip() for line in value.splitlines()
                     if line.strip()]
        raw[field["name"]] = value
    return parent_report.normalize_narrative(raw)


def _parent_comments_from_form(student_id, month):
    """Tekst zapažanja iz forme uz datume iz sačuvanog izvještaja."""
    saved = parent_report.load_saved(student_id, month)
    comments = list((saved or {}).get("parent_comments") or [])
    edited = []
    for index, entry in enumerate(comments):
        edited.append({
            "date": entry.get("date"),
            "comment": request.form.get(
                "parent_comment_%d" % index, entry.get("comment") or ""),
        })
    return None if edited == comments else edited


def _generate_one_report(student_id, month, *, replace):
    """Generiši ili sigurno ponovo iskoristi jedan sačuvani izvještaj."""
    key = (int(student_id), month)
    with _REPORT_GENERATION_INFLIGHT_LOCK:
        if key in _REPORT_GENERATION_INFLIGHT:
            logger.info(
                "admin_report_generate_blocked code=already_in_progress")
            payload = report_input.build_report_input(student_id, month)
            return payload, "error", ERROR_GENERATION_IN_PROGRESS
        _REPORT_GENERATION_INFLIGHT.add(key)
    try:
        return _generate_one_report_unlocked(student_id, month, replace=replace)
    finally:
        with _REPORT_GENERATION_INFLIGHT_LOCK:
            _REPORT_GENERATION_INFLIGHT.discard(key)


def _generate_one_report_unlocked(student_id, month, *, replace):
    """Pripremi izvještaj i napravi najviše jedan modelski poziv.

    Baza i determinističke činjenice završavaju prije uzimanja modelskog
    mjesta. Zato se ni globalni DB lock ni modelski permit ne drže dok se čeka
    drugi resurs. Zauzet proces pada brzo i sigurno, bez reda i bez plaćenog
    poziva.
    """
    started = time.perf_counter()
    data_started = time.perf_counter()
    payload = report_input.build_report_input(student_id, month)
    data_ms = int((time.perf_counter() - data_started) * 1000)
    profile = payload.get("profile") or {}
    if not profile.get("reporting_enabled", True):
        logger.info("admin_report_generate_blocked code=report_disabled")
        return payload, "error", ERROR_REPORT_DISABLED

    try:
        saved_before = parent_report.load_saved(student_id, month)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_report_load_failed code=%s", error.code)
        return payload, "error", parent_report.SAFE_AI_ERROR
    if saved_before is not None and not replace:
        return payload, "reused", "Postojeći izvještaj je sačuvan."

    if not _grade_confirmed(payload):
        logger.info("admin_report_generate_blocked code=grade_unconfirmed")
        return payload, "error", ERROR_GRADE_UNCONFIRMED

    parent_comments = list(
        (payload.get("instruction") or {}).get("parent_comments") or [])
    comments_were_edited = bool(
        ((saved_before or {}).get("snapshot") or {}).get(
            "parent_comments_edited"))
    if comments_were_edited:
        parent_comments = list(saved_before.get("parent_comments") or [])

    # Model i sačuvani dokument moraju dobiti isti skup zapažanja. Kopija čuva
    # originalni deterministički payload netaknutim, a uređeni komentar iz
    # ranijeg izvještaja ne vraća se u izvornu evidenciju časa.
    facts_payload = dict(payload)
    facts_instruction = dict(payload.get("instruction") or {})
    facts_instruction["parent_comments"] = parent_comments
    facts_payload["instruction"] = facts_instruction

    facts_started = time.perf_counter()
    facts = report_facts.build_ai_facts(facts_payload)
    facts_ms = int((time.perf_counter() - facts_started) * 1000)
    from matbot import llm as llm_module

    if not _REPORT_MODEL_SLOTS.acquire(blocking=False):
        logger.info("admin_report_generate_overloaded code=model_slots_busy")
        return payload, "error", ERROR_GENERATION_BUSY

    ai_started = time.perf_counter()
    ai_calls = 1
    try:
        narrative = parent_report.generate_narrative(
            facts, llm_module.OpenAIPracticeLLM())
    except parent_report.ReportGenerationError as error:
        logger.info(
            "admin_report_generate_failed code=%s calls=1 total_ms=%s",
            error.code, int((time.perf_counter() - started) * 1000))
        return payload, "error", parent_report.SAFE_AI_ERROR
    except Exception as error:
        logger.error(
            "admin_report_generate_failed code=unexpected calls=1 "
            "exception=%s frames=%s",
            _safe_exception_type(error), _safe_traceback_frames(error))
        return payload, "error", parent_report.SAFE_AI_ERROR
    finally:
        _REPORT_MODEL_SLOTS.release()
    ai_ms = int((time.perf_counter() - ai_started) * 1000)

    snapshot = parent_report.metrics_snapshot(
        facts, model=config.REPORTING_MODEL,
        prompt_version=report_prompt.REPORT_PROMPT_VERSION,
        parent_comments=parent_comments,
        student_label=_student_label(payload["profile"], student_id))
    if comments_were_edited:
        snapshot["parent_comments_edited"] = True
    persistence_started = time.perf_counter()
    try:
        parent_report.save_narrative(student_id, month, narrative, snapshot,
                                     generated_at=parent_report.utc_now())
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_report_save_failed code=%s", error.code)
        return payload, "error", parent_report.SAFE_AI_ERROR
    persistence_ms = int((time.perf_counter() - persistence_started) * 1000)
    logger.info(
        "reports_timing operation=generate status=ready data_ms=%s "
        "facts_ms=%s external_wait_ms=%s persistence_ms=%s total_ms=%s "
        "openai_calls=%s",
        data_ms, facts_ms, ai_ms, persistence_ms,
        int((time.perf_counter() - started) * 1000), ai_calls)
    return payload, "ready", "Izvještaj je spreman."


@admin_reports_bp.route("/student/<int:student_id>/generate", methods=["POST"])
@require_admin
def generate_report(student_id):
    """TAČNO JEDAN plaćeni poziv. Komentar instruktora se ne dira (Dio 15)."""
    _require_csrf()
    month = _month_or_400()
    payload, status, message = _generate_one_report(
        student_id, month, replace=True)
    if status == "error":
        return _render_student(student_id, month, payload,
                               ai_error=message), 200
    return redirect(url_for("admin_reports.student_preview",
                            student_id=student_id, month=month))


def _selected_student_ids(limit):
    """Uređen, jedinstven i ograničen spisak ID-jeva iz POST forme."""
    raw_values = request.form.getlist("student_ids")
    if not raw_values or len(raw_values) > limit:
        abort(400)
    result = []
    for raw in raw_values:
        try:
            student_id = int(raw)
        except (TypeError, ValueError):
            abort(400)
        if student_id <= 0 or student_id in result:
            abort(400)
        result.append(student_id)
    return result


def _bulk_allowed_student_ids(month, database):
    """Aktivni redovni učenici koji pripadaju odabranom periodu."""
    population = set(report_input.report_population(
        month, database=database,
        account_types=(reporting_db.ACCOUNT_TYPE_STUDENT,)))
    active = database.list_students(
        active=True, account_type=reporting_db.ACCOUNT_TYPE_STUDENT)
    return population & {row["student_id"] for row in active}


@admin_reports_bp.route("/bulk/generate", methods=["POST"])
@require_admin
def bulk_generate_reports():
    """Ograničen paket; kvar jednog izvještaja ne prekida ostale."""
    _require_csrf()
    try:
        month = progress.parse_report_month(request.form.get("month", ""))
    except progress.ProgressFormatError:
        abort(400)
    student_ids = _selected_student_ids(config.REPORTING_BULK_REQUEST_SIZE)
    replace = request.form.get("replace") == "1"
    database = reporting_db.get_database()
    allowed = _bulk_allowed_student_ids(month, database)
    def generate(student_id):
        if student_id not in allowed:
            return {"student_id": student_id, "status": "error",
                    "message": "Učenik nije dostupan za ovaj period."}
        try:
            _payload, status, message = _generate_one_report(
                student_id, month, replace=replace)
        except reporting_db.ReportingUnavailable as error:
            logger.info("admin_bulk_report_failed code=%s", error.code)
            status, message = "error", parent_report.SAFE_AI_ERROR
        except Exception as error:
            logger.error(
                "admin_bulk_report_failed code=unexpected exception=%s "
                "frames=%s",
                _safe_exception_type(error), _safe_traceback_frames(error))
            status, message = "error", parent_report.SAFE_AI_ERROR
        return {"student_id": student_id, "status": status,
                "message": message}

    def admitted_generate(student_id):
        try:
            return generate(student_id)
        finally:
            _REPORT_BULK_ADMISSION.release()

    started = time.perf_counter()
    ordered = []
    for student_id in student_ids:
        if not _REPORT_BULK_ADMISSION.acquire(blocking=False):
            logger.info("admin_bulk_report_overloaded code=bulk_slots_busy")
            ordered.append({
                "student_id": student_id, "status": "error",
                "message": ERROR_GENERATION_BUSY,
            })
            continue
        try:
            ordered.append(_REPORT_BULK_EXECUTOR.submit(
                admitted_generate, student_id))
        except Exception as error:
            _REPORT_BULK_ADMISSION.release()
            logger.error(
                "admin_bulk_report_submit_failed exception=%s frames=%s",
                _safe_exception_type(error), _safe_traceback_frames(error))
            ordered.append({
                "student_id": student_id, "status": "error",
                "message": parent_report.SAFE_AI_ERROR,
            })

    # Future-i su samo dva i vec su globalno primljeni; redoslijed odgovora
    # ostaje isti kao u formi, bez pravljenja per-request executora.
    results = [item.result() if hasattr(item, "result") else item
               for item in ordered]
    completed = sum(1 for item in results
                    if item["status"] in ("ready", "reused"))
    logger.info(
        "reports_timing operation=bulk_generate status=ok students=%s "
        "completed=%s concurrency_limit=%s total_ms=%s",
        len(student_ids), completed, config.REPORTING_MAX_ACTIVE_MODEL_CALLS,
        int((time.perf_counter() - started) * 1000))
    return jsonify({"results": results, "completed": completed,
                    "total": len(results)})


def _saved_pdf(student_id, month):
    """Bajtovi i ime jednog već sačuvanog izvještaja, ili None."""
    payload = report_input.build_report_input(student_id, month)
    saved = parent_report.load_saved(student_id, month)
    if saved is None:
        return None
    facts = (saved.get("snapshot") or {}).get("facts")
    if not facts:
        facts = report_facts.build_ai_facts(payload)
    label = ((((saved.get("snapshot") or {}).get("student") or {}).get("label"))
             or _student_label(payload["profile"], student_id))
    data = report_pdf.render_report_pdf(
        facts, saved["narrative"], saved["instructor_comment"], label,
        saved.get("parent_comments"))
    return data, report_pdf.pdf_filename(label, month)


@admin_reports_bp.route("/bulk/download", methods=["POST"])
@require_admin
def bulk_download_reports():
    """Jedan ZIP sa svim dostupnim sačuvanim PDF izvještajima."""
    _require_csrf()
    try:
        month = progress.parse_report_month(request.form.get("month", ""))
    except progress.ProgressFormatError:
        abort(400)
    student_ids = _selected_student_ids(MAX_BULK_EXPORT)
    database = reporting_db.get_database()
    allowed = _bulk_allowed_student_ids(month, database)
    if any(student_id not in allowed for student_id in student_ids):
        abort(400)

    output = io.BytesIO()
    written = 0
    used_names = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for student_id in student_ids:
            try:
                rendered = _saved_pdf(student_id, month)
            except (reporting_db.ReportingUnavailable, report_pdf.PdfTooLong):
                logger.info("admin_bulk_pdf_skipped")
                continue
            if rendered is None:
                continue
            data, filename = rendered
            original = filename
            suffix = 2
            while filename in used_names:
                filename = original[:-4] + "-%d.pdf" % suffix
                suffix += 1
            used_names.add(filename)
            archive.writestr(filename, data)
            written += 1
    if not written:
        abort(404)
    response = Response(output.getvalue(), mimetype="application/zip", headers={
        "Content-Disposition":
            'attachment; filename="izvjestaji-%s.zip"' % month,
        "X-Reports-Included": str(written),
    })
    return response


@admin_reports_bp.route("/student/<int:student_id>/save", methods=["POST"])
@require_admin
def save_report(student_id):
    """Snimanje izmjena NIKAD ne zove model (Dio 32)."""
    _require_csrf()
    month = _month_or_400()
    payload = report_input.build_report_input(student_id, month)
    if not (payload.get("profile") or {}).get("reporting_enabled", True):
        logger.info("admin_report_save_blocked code=report_disabled")
        return _render_student(student_id, month, payload,
                               ai_error=ERROR_REPORT_DISABLED), 200
    try:
        parent_report.save_edits(student_id, month, _narrative_from_form(),
                                 request.form.get("instructor_comment", ""),
                                 parent_comments=_parent_comments_from_form(
                                     student_id, month))
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_report_save_failed code=%s", error.code)
        return _render_student(student_id, month, payload,
                               ai_error=parent_report.SAFE_AI_ERROR), 200
    return redirect(url_for("admin_reports.student_preview",
                            student_id=student_id, month=month))


@admin_reports_bp.route("/student/<int:student_id>/pdf", methods=["GET"])
@require_admin
def report_pdf_download(student_id):
    """PDF iz SAČUVANOG nacrta. Ne zove model i ne mijenja nijedan red."""
    month = _month_or_400()
    try:
        rendered = _saved_pdf(student_id, month)
    except reporting_db.ReportingUnavailable:
        rendered = None
    except report_pdf.PdfTooLong as error:
        logger.info("admin_report_pdf_too_long detail=%s", error)
        abort(500)
    if rendered is None:
        # Bez sačuvanog nacrta nema šta da se štampa — nikad se ne generiše
        # tekst „u letu" samo da bi PDF postojao.
        abort(404)
    data, filename = rendered

    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": '%s; filename="%s"'
                               % ("inline" if request.args.get("preview") == "1"
                                  else "attachment", filename),
        "Cache-Control": "no-store",
    })
