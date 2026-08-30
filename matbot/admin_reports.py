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
import logging

from flask import (Blueprint, abort, redirect, render_template, request,
                   session, url_for)

from matbot import admin_auth, config, parent_report, report_input, reporting_db
from matbot import report_pdf, report_prompt, reporting_schema
from matbot import thinkific_progress as progress
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
    return render_template(
        "admin_reports.html",
        csrf_token=admin_auth.csrf_token(),
        course_fields=COURSE_FIELDS,
        month=_default_month(),
        schema_state=state,
        schema_message=message,
        summary=None,
        outcome=None,
        errors=[],
    )


def _default_month():
    """Samo UDOBNOST u UI-ju. Poslana vrijednost ostaje autoritativna i server
    je iznova validira — mjesec se NIKAD ne izvodi iz vremena uploada."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return "%04d-%02d" % (now.year, now.month)


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
    failed = blocked_count + sum(1 for f in summary.files if f["status"] != "imported")
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
    summary = report_input.import_progress_files(month, files)
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


# ---------------------------------------------------------------------------
# Populacija i pregled
# ---------------------------------------------------------------------------
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

    rows = []
    for student_id in report_input.report_population(month):
        payload = report_input.build_report_input(student_id, month)
        matbot = payload["matbot"]
        thinkific = payload["thinkific"]
        rows.append({
            "student_id": student_id,
            # IDENTITET U UI-ju je ime ili neutralna oznaka — NIKAD e-mail.
            "label": _student_label(payload["profile"], student_id),
            "grade": payload["profile"].get("grade"),
            "has_snapshot": not thinkific.get("snapshot_missing"),
            "percent_completed": thinkific.get("percent_completed"),
            "delta_percent_completed": thinkific.get("delta_percent_completed"),
            "practice_tasks": matbot["practice_tasks"],
            "kontrolni_attempts": matbot["kontrolni_attempts"],
        })
    return render_template("admin_students.html", month=month, rows=rows,
                           schema_state=state, schema_message=message, error="")


# NOV IZVJEŠTAJ TRAŽI POTVRĐEN TEKUĆI RAZRED (verzija 4). Poruka je za
# administratora i ne nosi interni kod (pravilo 7). Stari sačuvani izvještaji se
# ovim ne diraju — čitaju se i preuzimaju kao i do sada.
ERROR_GRADE_UNCONFIRMED = (
    "Trenutni razred učenika nije potvrđen. Potvrdite razred na profilu učenika "
    "prije generisanja novog izvještaja.")


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
    return render_template(
        "admin_student.html", month=month, payload=payload,
        label=_student_label(payload["profile"], student_id),
        previous_month=report_input.previous_month(month), schema_message="",
        saved=saved, csrf_token=admin_auth.csrf_token(),
        # Dugme za generisanje se ne nudi bez potvrđenog razreda; server to
        # svejedno provjerava ponovo u `generate_report` — predložak nije
        # zaštita nego objašnjenje.
        grade_confirmed=_grade_confirmed(payload),
        grade_unconfirmed_message=ERROR_GRADE_UNCONFIRMED,
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
    def lines(field):
        raw = request.form.get(field, "")
        return [line.strip() for line in raw.splitlines() if line.strip()]

    return parent_report.normalize_narrative({
        "summary": request.form.get("summary", ""),
        "strengths": lines("strengths"),
        "focus_areas": lines("focus_areas"),
        "next_month_recommendations": lines("next_month_recommendations"),
    })


@admin_reports_bp.route("/student/<int:student_id>/generate", methods=["POST"])
@require_admin
def generate_report(student_id):
    """TAČNO JEDAN plaćeni poziv. Komentar instruktora se ne dira (Dio 15)."""
    _require_csrf()
    month = _month_or_400()
    payload, facts = parent_report.build_facts(student_id, month)

    # BLOKADA PRIJE POZIVA, NE POSLIJE. Nepotvrđen razred znači da ni sam
    # sistem ne zna koji razred dijete pohađa — izvještaj koji to tvrdi
    # roditelju ne smije nastati, a plaćeni poziv se ne troši. Sačuvani nacrt
    # ostaje netaknut.
    if not _grade_confirmed(payload):
        logger.info("admin_report_generate_blocked code=grade_unconfirmed "
                    "student_id=%s", student_id)
        return _render_student(student_id, month, payload,
                               ai_error=ERROR_GRADE_UNCONFIRMED), 200

    from matbot import llm as llm_module

    try:
        narrative = parent_report.generate_narrative(
            facts, llm_module.OpenAIPracticeLLM())
    except parent_report.ReportGenerationError as error:
        # Interni kod SAMO u log (pravilo 7). Postojeći nacrt ostaje netaknut.
        logger.info("admin_report_generate_failed code=%s", error.code)
        return _render_student(student_id, month, payload,
                               ai_error=parent_report.SAFE_AI_ERROR), 200

    snapshot = parent_report.metrics_snapshot(
        facts, model=config.REPORTING_MODEL,
        prompt_version=report_prompt.REPORT_PROMPT_VERSION,
        # Zapažanja s časova IZ ISTOG trenutka kao i činjenice: snimak mora biti
        # jedna konzistentna slika mjeseca (Dio 36).
        parent_comments=(payload.get("instruction") or {}).get("parent_comments"))
    try:
        # Model je STVARNO zvan, pa `generated_at` dobija novu vrijednost.
        parent_report.save_narrative(student_id, month, narrative, snapshot,
                                     generated_at=parent_report.utc_now())
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_report_save_failed code=%s", error.code)
        return _render_student(student_id, month, payload,
                               ai_error=parent_report.SAFE_AI_ERROR), 200
    return redirect(url_for("admin_reports.student_preview",
                            student_id=student_id, month=month))


@admin_reports_bp.route("/student/<int:student_id>/save", methods=["POST"])
@require_admin
def save_report(student_id):
    """Snimanje izmjena NIKAD ne zove model (Dio 32)."""
    _require_csrf()
    month = _month_or_400()
    payload = report_input.build_report_input(student_id, month)
    try:
        parent_report.save_edits(student_id, month, _narrative_from_form(),
                                 request.form.get("instructor_comment", ""))
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
    payload = report_input.build_report_input(student_id, month)
    try:
        saved = parent_report.load_saved(student_id, month)
    except reporting_db.ReportingUnavailable:
        saved = None
    if saved is None:
        # Bez sačuvanog nacrta nema šta da se štampa — nikad se ne generiše
        # tekst „u letu" samo da bi PDF postojao.
        abort(404)

    # Činjenice dolaze IZ SNIMKA, ne iz današnje baze: dokument mora ostati ono
    # što je administrator odobrio, i kad se izvorni podaci kasnije promijene.
    facts = (saved.get("snapshot") or {}).get("facts")
    if not facts:
        _, facts = parent_report.build_facts(student_id, month)
    label = _student_label(payload["profile"], student_id)
    try:
        data = report_pdf.render_report_pdf(
            facts, saved["narrative"], saved["instructor_comment"], label,
            saved.get("parent_comments"))
    except report_pdf.PdfTooLong as error:
        logger.info("admin_report_pdf_too_long detail=%s", error)
        abort(500)

    from flask import Response

    return Response(data, mimetype="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="%s"'
                               % report_pdf.pdf_filename(label, month),
        "Cache-Control": "no-store",
    })
