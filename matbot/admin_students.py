"""Faza 3D — privatni registar učenika i evidencija časova.

TANAK KONTROLER, kao i `admin_reports`: provjeri oblik zahtjeva, pozovi već
dokazan sloj, prikaži ishod. Semantika časa (prisustvo, angažman, zadaća) živi
u `matbot/student_sessions.py`, upis u `matbot/reporting_db.py`.

REGISTAR NIJE NOVA TABELA. Radi nad postojećom `students`, istom onom u koju
Faza 1 upisuje učenike pristigle preko Thinkifica. Ručno upisan i automatski
nastao učenik su isti tip zapisa i vide se na istoj listi — drugi prostor imena
bi značio dva izvora istine o istom djetetu.

E-MAIL SE NE PRIKAZUJE I NE PUTUJE KROZ URL. Administratoru je dovoljno
„povezan / nije povezan"; adresa se unosi jednom, normalizuje istim pravilima
kao Faza 1, i poslije toga se ne ispisuje (Dio 40).

SVE RUTE SU ADMIN-ONLY, a sve koje mijenjaju stanje traže CSRF i POST.
"""
import logging

from flask import (Blueprint, abort, redirect, render_template, request,
                   url_for)

from matbot import report_input, reporting_db, student_identity, student_sessions
from matbot.admin_auth import CSRF_FORM_FIELD, require_admin

logger = logging.getLogger("matbot.admin_students")

admin_students_bp = Blueprint("admin_students", __name__,
                              url_prefix="/admin/students")

VALID_GRADES = (6, 7, 8, 9)
MAX_NAME_CHARS = 120

# Poruke za administratora. Nikad ne nose e-mail ni interni kod (pravilo 7).
ERROR_NAME = "Ime učenika je obavezno."
ERROR_GRADE = "Razred mora biti 6, 7, 8 ili 9."
ERROR_EMAIL = "Thinkific e-mail nije u ispravnom obliku."
ERROR_TAKEN = "Ovaj Thinkific nalog je već povezan sa drugim učenikom."
ERROR_SESSION = "Podaci o času nisu ispravni."
ERROR_UNAVAILABLE = "Izvještajna baza trenutno nije dostupna."


def _require_csrf():
    from matbot import admin_auth

    if not admin_auth.csrf_valid(request.form.get(CSRF_FORM_FIELD)):
        logger.info("admin_students_csrf_rejected")
        abort(400)


def _clean_name(raw):
    name = " ".join((raw or "").split())
    if not name or "<" in name or ">" in name:
        return None
    return name[:MAX_NAME_CHARS]


def _clean_grade(raw):
    try:
        grade = int((raw or "").strip())
    except (TypeError, ValueError):
        return None
    return grade if grade in VALID_GRADES else None


def _db():
    return reporting_db.get_database()


@admin_students_bp.route("", methods=["GET"])
@admin_students_bp.route("/", methods=["GET"])
@require_admin
def index():
    """Registar. Pretraga po imenu i filter po razredu — bez e-maila."""
    search = (request.args.get("q") or "").strip()[:MAX_NAME_CHARS]
    grade = _clean_grade(request.args.get("grade"))
    try:
        students = _db().list_students(search=search or None, grade=grade)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_students_list_failed code=%s", error.code)
        students = []
    from matbot import admin_auth

    return render_template("admin_registry.html", students=students,
                           search=search, grade=grade, grades=VALID_GRADES,
                           csrf_token=admin_auth.csrf_token(),
                           error=request.args.get("error", ""),
                           conflict=(request.args.get("conflict") or "").strip()[:20],
                           month=report_input.previous_month(_this_month()))


def _this_month():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


@admin_students_bp.route("/create", methods=["POST"])
@require_admin
def create_student():
    """Ručni upis. E-mail je NEOBAVEZAN i vezuje se zasebno, pa neuspjeh
    povezivanja ne smije poništiti već upisanog učenika."""
    _require_csrf()
    name = _clean_name(request.form.get("display_name"))
    grade = _clean_grade(request.form.get("grade"))
    if name is None:
        return redirect(url_for("admin_students.index", error=ERROR_NAME))
    if grade is None:
        return redirect(url_for("admin_students.index", error=ERROR_GRADE))

    raw_email = (request.form.get("thinkific_email") or "").strip()
    external = None
    if raw_email:
        external = student_identity.normalize_email(raw_email)
        if external is None:
            return redirect(url_for("admin_students.index", error=ERROR_EMAIL))

    # JEDAN POZIV, JEDNA TRANSAKCIJA. Ranije se učenik komitovao pa se tek onda
    # pokušavao nalog — sudar e-maila je tako ostavljao duplikat bez veze.
    try:
        student_id = _db().create_student(name, grade, external_user_id=external)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_student_create_failed code=%s", error.code)
        if str(error.code).startswith("student_account_taken"):
            # NIŠTA NIJE UPISANO. Administrator dobija ID postojećeg učenika i
            # vezu na njegov profil — bez ponavljanja adrese.
            other = str(error.code).split(":", 1)[-1]
            return redirect(url_for("admin_students.index",
                                    error=ERROR_TAKEN, conflict=other))
        return redirect(url_for("admin_students.index", error=ERROR_UNAVAILABLE))
    return redirect(url_for("admin_students.profile", student_id=student_id))


@admin_students_bp.route("/<int:student_id>", methods=["GET"])
@require_admin
def profile(student_id):
    """Profil: identitet, akcije i ISTORIJA ČASOVA."""
    from matbot import admin_auth

    try:
        database = _db()
        student = database.fetch_student_profile(student_id)
        if student is None:
            abort(404)
        linked = database.student_has_thinkific(student_id)
        sessions = database.fetch_sessions(student_id)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_student_profile_failed code=%s", error.code)
        abort(503)

    from matbot import topics

    # Kanonski izbor gradiva za razred OVOG učenika. Isti izvor kao Practice i
    # Kontrolni (`data/topics.json`) — nema druge kopije kurikuluma.
    curriculum = topics.curriculum_choices(student.get("grade"))
    return render_template(
        "admin_student_profile.html", student_id=student_id, student=student,
        curriculum=curriculum, areas=list(curriculum),
        thinkific_linked=linked, sessions=list(reversed(sessions)),
        activity_labels=student_sessions.ACTIVITY_LABELS,
        homework_labels=student_sessions.HOMEWORK_LABELS,
        attendance_labels=student_sessions.ATTENDANCE_LABELS,
        csrf_token=admin_auth.csrf_token(),
        error=request.args.get("error", ""),
        month=_this_month())


@admin_students_bp.route("/<int:student_id>/link", methods=["POST"])
@require_admin
def link_account(student_id):
    """Poveži Thinkific nalog s postojećim učenikom. PADA ZATVORENO na koliziju.

    Nikad ne preuzima tuđi nalog i nikad ne spaja identitete — spajanje je
    destruktivno i nije predmet ove faze."""
    _require_csrf()
    external = student_identity.normalize_email(
        request.form.get("thinkific_email") or "")
    if external is None:
        return redirect(url_for("admin_students.profile",
                                student_id=student_id, error=ERROR_EMAIL))
    try:
        _db().link_thinkific_account(student_id, external)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_student_link_failed code=%s", error.code)
        message = ERROR_UNAVAILABLE
        if str(error.code).startswith("student_account_taken"):
            other = str(error.code).split(":", 1)[-1]
            message = ERROR_TAKEN + " Pogledajte zapis učenika #%s." % other
        return redirect(url_for("admin_students.profile",
                                student_id=student_id, error=message))
    return redirect(url_for("admin_students.profile", student_id=student_id))


def _session_from_form(grade):
    """Formular → provjeren zapis. Server je autoritet, ne klijent.

    `grade` dolazi iz BAZE (profil učenika), nikad iz formulara — inače bi
    klijent mogao izabrati razred u kojem njegova izmišljena lekcija „postoji"."""
    return student_sessions.validate_session(
        session_date=request.form.get("session_date"),
        attendance=request.form.get("attendance"),
        activity_rating=request.form.get("activity_rating"),
        homework_status=request.form.get("homework_status"),
        area_name=request.form.get("area_name"),
        lesson_name=request.form.get("lesson_name"),
        comment=request.form.get("comment"),
        grade=grade)


def _student_grade(student_id):
    profile = _db().fetch_student_profile(student_id)
    if profile is None:
        abort(404)
    return profile.get("grade")


@admin_students_bp.route("/<int:student_id>/sessions", methods=["POST"])
@require_admin
def create_session(student_id):
    _require_csrf()
    try:
        record = _session_from_form(_student_grade(student_id))
    except student_sessions.SessionValidationError as error:
        logger.info("admin_session_rejected code=%s", error.code)
        return redirect(url_for("admin_students.profile",
                                student_id=student_id, error=ERROR_SESSION))
    try:
        _db().insert_session(student_id, record)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_session_insert_failed code=%s", error.code)
        return redirect(url_for("admin_students.profile",
                                student_id=student_id, error=ERROR_UNAVAILABLE))
    return redirect(url_for("admin_students.profile", student_id=student_id))


@admin_students_bp.route("/<int:student_id>/sessions/<int:session_id>",
                         methods=["POST"])
@require_admin
def update_session(student_id, session_id):
    """Izmjena. Instruktori griješe pri unosu, pa ispravka mora postojati.

    VLASNIŠTVO SE PROVJERAVA U UPITU (`WHERE id = ? AND student_id = ?`), pa
    tuđi zapis ne može biti izmijenjen ni s pogođenim `session_id`."""
    _require_csrf()
    try:
        record = _session_from_form(_student_grade(student_id))
    except student_sessions.SessionValidationError as error:
        logger.info("admin_session_rejected code=%s", error.code)
        return redirect(url_for("admin_students.profile",
                                student_id=student_id, error=ERROR_SESSION))
    try:
        if not _db().update_session(session_id, student_id, record):
            abort(404)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_session_update_failed code=%s", error.code)
        return redirect(url_for("admin_students.profile",
                                student_id=student_id, error=ERROR_UNAVAILABLE))
    return redirect(url_for("admin_students.profile", student_id=student_id))


@admin_students_bp.route("/<int:student_id>/sessions/<int:session_id>/delete",
                         methods=["POST"])
@require_admin
def delete_session(student_id, session_id):
    """Brisanje je POST + CSRF. GET nikad ne mijenja stanje."""
    _require_csrf()
    try:
        if not _db().delete_session(session_id, student_id):
            abort(404)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_session_delete_failed code=%s", error.code)
        return redirect(url_for("admin_students.profile",
                                student_id=student_id, error=ERROR_UNAVAILABLE))
    return redirect(url_for("admin_students.profile", student_id=student_id))
