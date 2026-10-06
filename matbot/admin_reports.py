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
import zipfile

from flask import (Blueprint, Response, abort, jsonify, redirect,
                   render_template, request, url_for)

from matbot import admin_auth, config, parent_report, report_input, reporting_db
from matbot import report_facts, student_grades
from matbot import report_pdf, report_prompt, reporting_schema
from matbot import thinkific_progress as progress
from matbot import thinkific_roster
from matbot.admin_auth import CSRF_FORM_FIELD, require_admin
from matbot.ratelimit import RateLimiter

logger = logging.getLogger("matbot.admin_reports")

admin_reports_bp = Blueprint("admin_reports", __name__, url_prefix="/admin/reports")

# Četiri IZRIČITA slota. Razred NIKAD ne dolazi iz imena fajla ni iz sadržaja —
# administrator bira polje, a polje je vezano za kurs.
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
        report = reporting_db.get_database().check()
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
    state, message = schema_state()
    month = _default_month()
    return render_template(
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
    """Skupi bajtove iz ČETIRI IZRIČITA polja. Vraća `(files, errors)`.

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

    files, upload_errors = _collect_files(request.files)
    if not files:
        code = upload_errors[0]["code"] if upload_errors else "no_file_selected"
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
    files, upload_errors = _collect_files(request.files)
    missing = [key for key, _label, _name in COURSE_FIELDS if key not in files]
    if upload_errors or missing:
        return None, None, (
            "Za pregled registra odaberi sva četiri važeća CSV fajla.")
    try:
        month = progress.parse_report_month(request.form.get("report_month", ""))
        parsed = {key: progress.parse_progress_csv(files[key], key, month)
                  for key, _label, _name in COURSE_FIELDS}
    except progress.ProgressFormatError as error:
        logger.info("thinkific_roster_rejected code=%s row=%s",
                    error.code, error.row)
        return None, None, (
            "Jedan CSV nije ispravan. Provjeri fajlove i pokušaj ponovo.")
    return files, parsed, None


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
        actions = thinkific_roster.selected_actions(
            plan, request.form.getlist("action_keys"))
        applied = database.apply_roster_reconciliation(actions)
    except thinkific_roster.RosterPlanError as caught:
        logger.info("thinkific_roster_apply_refused code=%s", caught.code)
        return jsonify({"status": "error",
                        "message": "Izbor promjena nije ispravan."}), 400
    except reporting_db.ReportingUnavailable as caught:
        logger.info("thinkific_roster_apply_failed code=%s", caught.code)
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

    rows = []
    for student in registry:
        student_id = student["student_id"]
        payload = report_input.build_report_input(student_id, month,
                                                  database=database)
        matbot = payload["matbot"]
        thinkific = payload["thinkific"]
        report_summary = (None if summaries is None
                          else summaries.get(student_id))
        account_type = (student.get("account_type")
                        or reporting_db.ACCOUNT_TYPE_STUDENT)
        rows.append({
            "student_id": student_id,
            "label": _student_label(payload["profile"], student_id),
            "grade": payload["profile"].get("grade"),
            "grades": payload["profile"].get("grades") or [],
            "grade_confirmed": payload["profile"].get("grade_confirmed", False),
            "account_type": account_type,
            "reporting_enabled": account_type == reporting_db.ACCOUNT_TYPE_STUDENT,
            "has_snapshot": not thinkific.get("snapshot_missing"),
            "percent_completed": thinkific.get("percent_completed"),
            "delta_percent_completed": thinkific.get("delta_percent_completed"),
            "practice_tasks": matbot["practice_tasks"],
            "kontrolni_attempts": matbot["kontrolni_attempts"],
            "has_report": (None if summaries is None
                           else report_summary is not None),
            "generated_at": (report_summary or {}).get("generated_at"),
            "updated_at": (report_summary or {}).get("updated_at"),
        })
    return rows


@admin_reports_bp.route("/students", methods=["GET"])
@require_admin
def students():
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
    return render_template("admin_students.html", month=month, rows=rows,
                           filters=filters, csrf_token=admin_auth.csrf_token(),
                           max_bulk_generate=MAX_BULK_GENERATE,
                           schema_state=state, schema_message=message, error="")


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
    payload = report_input.build_report_input(student_id, month)
    profile = payload.get("profile") or {}
    if not profile.get("reporting_enabled", True):
        logger.info("admin_report_generate_blocked code=report_disabled "
                    "student_id=%s", student_id)
        return payload, "error", ERROR_REPORT_DISABLED

    try:
        saved_before = parent_report.load_saved(student_id, month)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_report_load_failed code=%s", error.code)
        return payload, "error", parent_report.SAFE_AI_ERROR
    if saved_before is not None and not replace:
        return payload, "reused", "Postojeći izvještaj je sačuvan."

    if not _grade_confirmed(payload):
        logger.info("admin_report_generate_blocked code=grade_unconfirmed "
                    "student_id=%s", student_id)
        return payload, "error", ERROR_GRADE_UNCONFIRMED

    facts = report_facts.build_ai_facts(payload)
    from matbot import llm as llm_module

    try:
        narrative = parent_report.generate_narrative(
            facts, llm_module.OpenAIPracticeLLM())
    except parent_report.ReportGenerationError as error:
        logger.info("admin_report_generate_failed code=%s", error.code)
        return payload, "error", parent_report.SAFE_AI_ERROR

    parent_comments = (payload.get("instruction") or {}).get("parent_comments")
    comments_were_edited = bool(
        ((saved_before or {}).get("snapshot") or {}).get(
            "parent_comments_edited"))
    if comments_were_edited:
        parent_comments = saved_before.get("parent_comments") or []
    snapshot = parent_report.metrics_snapshot(
        facts, model=config.REPORTING_MODEL,
        prompt_version=report_prompt.REPORT_PROMPT_VERSION,
        parent_comments=parent_comments,
        student_label=_student_label(payload["profile"], student_id))
    if comments_were_edited:
        snapshot["parent_comments_edited"] = True
    try:
        parent_report.save_narrative(student_id, month, narrative, snapshot,
                                     generated_at=parent_report.utc_now())
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_report_save_failed code=%s", error.code)
        return payload, "error", parent_report.SAFE_AI_ERROR
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
    student_ids = _selected_student_ids(MAX_BULK_GENERATE)
    replace = request.form.get("replace") == "1"
    database = reporting_db.get_database()
    allowed = _bulk_allowed_student_ids(month, database)
    results = []
    for student_id in student_ids:
        if student_id not in allowed:
            results.append({"student_id": student_id, "status": "error",
                            "message": "Učenik nije dostupan za ovaj period."})
            continue
        try:
            _payload, status, message = _generate_one_report(
                student_id, month, replace=replace)
        except reporting_db.ReportingUnavailable as error:
            logger.info("admin_bulk_report_failed code=%s", error.code)
            status, message = "error", parent_report.SAFE_AI_ERROR
        except Exception:
            logger.exception("admin_bulk_report_failed code=unexpected")
            status, message = "error", parent_report.SAFE_AI_ERROR
        results.append({"student_id": student_id, "status": status,
                        "message": message})
    completed = sum(1 for item in results
                    if item["status"] in ("ready", "reused"))
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
                logger.info("admin_bulk_pdf_skipped student_id=%s", student_id)
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
        logger.info("admin_report_save_blocked code=report_disabled "
                    "student_id=%s", student_id)
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
