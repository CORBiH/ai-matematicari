"""Faza 3D+ — „Upiši čas": jedan čas, više učenika, jedno čuvanje.

TANAK KONTROLER, kao `admin_reports` i `admin_students`: provjeri oblik zahtjeva,
pozovi već dokazan sloj, prikaži ishod. Semantika učešća živi u
`matbot/class_entry.py`, semantika časa u `matbot/student_sessions.py`, upis u
`matbot/reporting_db.py`.

NIJE NOV IZVJEŠTAJNI SISTEM. Piše u POSTOJEĆI `student_sessions`, kroz POSTOJEĆU
validaciju — red nastao ovdje je nerazlučiv od reda unesenog kroz profil
učenika, i mjesečni izvještaj ga čita bez ijedne izmjene.

SPISAK JE SERVERSKA ODLUKA. Ponuđeni su isključivo AKTIVNI učenici s
POTVRĐENIM tekućim razredom jednakim izabranom razredu časa. Ni ime, ni Thinkific
kurs, ni kontrolni, ni MAT-BOT aktivnost ne mogu nikoga staviti na spisak.

STRANICA JE UJEDNO I IZMJENA. Isti `GET` s istim (datum, razred, oblast, lekcija)
učitava već upisane redove tog časa, pa se greška ispravlja bez otvaranja osam
profila. Nema nove „class session" tabele — logički čas JE ta četvorka.

SVE RUTE SU ADMIN-ONLY, a jedina koja mijenja stanje traži CSRF i POST.
"""
import datetime
import logging
import re

from flask import (Blueprint, abort, redirect, render_template, request,
                   url_for)

from matbot import (class_entry, report_input, reporting_db, student_grades,
                    student_sessions,
                    topics)
from matbot.admin_auth import CSRF_FORM_FIELD, require_admin

logger = logging.getLogger("matbot.admin_sessions")

admin_sessions_bp = Blueprint("admin_sessions", __name__,
                              url_prefix="/admin/sessions")

# Poruke za administratora. Nikad interni kod i nikad e-mail (pravilo 7).
ERROR_UNAVAILABLE = "Izvještajna baza trenutno nije dostupna."
ERROR_CURRICULUM = "Izaberite oblast i lekciju iz gradiva tog razreda."
ERROR_GRADE = "Razred mora biti 6, 7, 8 ili 9."
ERROR_DATE = "Datum časa nije ispravan."
ERROR_ACTIVITY = "Za svakog prisutnog učenika izaberite aktivnost na času."
ERROR_TIME = "Vrijeme časa mora biti u obliku HH:MM (na primjer 14:00)."
ERROR_TOPIC = "Unesite temu časa."
ERROR_TOPIC_LONG = "Tema časa je predugačka."
ERROR_CONFLICT = ("Na tom terminu već postoji čas s istom temom. Izaberite drugo "
                  "vrijeme ili uredite postojeći čas.")
ERROR_CLASS_MISSING = "Taj čas više ne postoji."
ERROR_CLASS_UNAVAILABLE = "Evidencija časova trenutno nije dostupna."

# Koliko časova stane na jednu stranicu pregleda. Pregled je radni spisak, ne
# izvoz — cjeloživotni skup se nikad ne povlači u pregledač.
CLASSES_PER_PAGE = 25
ERROR_ROSTER = "Neki učenik ne pripada izabranom razredu."
ERROR_EMPTY = "Označite bar jednog učenika kao prisutnog ili odsutnog."
ERROR_SESSION = "Podaci o času nisu ispravni."

# Interni kod → poruka za ekran. Kodovi ostaju u logu (pravilo 7).
_MESSAGES = {
    "class_grade_invalid": ERROR_GRADE,
    "class_curriculum_incomplete": ERROR_CURRICULUM,
    "class_curriculum_unknown": ERROR_CURRICULUM,
    "class_student_not_in_roster": ERROR_ROSTER,
    "class_occurrence_conflict": ERROR_CONFLICT,
    "class_missing": ERROR_CLASS_MISSING,
    "class_entity_unavailable": ERROR_CLASS_UNAVAILABLE,
    "class_activity_required": ERROR_ACTIVITY,
    "class_topic_required": ERROR_TOPIC,
    "class_topic_too_long": ERROR_TOPIC_LONG,
    "class_area_too_long": ERROR_TOPIC_LONG,
    "session_time_format": ERROR_TIME,
    "session_time_required": ERROR_TIME,
    "session_topic_required": ERROR_TOPIC,
    "session_date_format": ERROR_DATE,
    "session_date_invalid": ERROR_DATE,
    "session_curriculum_unknown": ERROR_CURRICULUM,
    "session_curriculum_incomplete": ERROR_CURRICULUM,
}


def _db():
    return reporting_db.get_database()


def _this_month():
    """Tekući mjesec `YYYY-MM` — podrazumijevani opseg pregleda časova."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def _today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _require_csrf():
    from matbot import admin_auth

    if not admin_auth.csrf_valid(request.form.get(CSRF_FORM_FIELD)):
        logger.info("admin_sessions_csrf_rejected")
        abort(400)


def class_roster(database, grade):
    """(spisak, broj_nepotvrđenih) za jedan razred. SAMO ČITANJE.

    NA SPISAK ULAZI SAMO POTVRĐEN RAZRED. Zatečena vrijednost `students.grade`
    bez potvrde nije dokaz da učenik pohađa taj razred — 34 zatečena reda nose
    šesticu koju je upisala stara automatika. Takvi se NE nude kao učesnici, ali
    se BROJE, da administrator zna zašto neko nedostaje.

    Nepotvrđeni se broje nad ISTIM zatečenim razredom, jer je to jedino što se o
    njima zna; to je prikaz, ne tvrdnja o njihovom stvarnom razredu."""
    listed = database.list_students(grade=grade, active=True)
    roster, unconfirmed = [], 0
    for student in listed:
        if student_grades.is_confirmed(student.get("grade"),
                                       student.get("grade_confirmed_at"),
                                       student.get("grade_source")):
            roster.append(student)
        else:
            unconfirmed += 1
    return roster, unconfirmed


def _selection(source):
    """Zajednička polja časa iz `args` ili `form`. Ne validira — samo čita.

    `topic_mode` se ovdje samo NORMALIZUJE u poznat režim; provjeru gradiva radi
    `class_entry.build_class_records`, jer klijentu se ne vjeruje ni oko toga
    koji je režim tražio."""
    return {
        "session_date": (source.get("session_date") or "").strip() or _today(),
        "session_time": (source.get("session_time") or "").strip(),
        "grade": class_entry.clean_grade(source.get("grade")),
        "topic_mode": class_entry.clean_topic_mode(source.get("topic_mode")),
        "area_name": (source.get("area_name") or "").strip(),
        "lesson_name": (source.get("lesson_name") or "").strip(),
        "class_id": _clean_id(source.get("class_id")),
    }


def _clean_id(raw):
    """Pozitivan cijeli broj, ili `None`. Klijentu se ne vjeruje ni ovdje."""
    try:
        value = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@admin_sessions_bp.route("/new", methods=["GET"])
@require_admin
def new_class():
    """Formular. GET NIŠTA NE MIJENJA — ni kad nosi sve parametre časa.

    S punom četvorkom (datum, razred, oblast, lekcija) stranica se ponaša kao
    IZMJENA: već upisani redovi se učitavaju u polja."""
    from matbot import admin_auth

    chosen = _selection(request.args)
    roster, unconfirmed, saved = [], 0, {}
    try:
        database = _db()
        # Otvaranje postojećeg časa popunjava zajednička polja IZ BAZE, pa se
        # istorijski razred čita iz zapisa časa — nikad iz tekućeg profila.
        if chosen["class_id"]:
            existing = database.fetch_class(chosen["class_id"])
            if existing is None:
                return redirect(url_for("admin_sessions.class_list",
                                        error=ERROR_CLASS_MISSING))
            chosen.update({
                "session_date": existing["session_date"],
                "session_time": existing["session_time"],
                "grade": existing["grade"],
                "topic_mode": existing["topic_source"],
                "area_name": existing["area_name"] or "",
                "lesson_name": existing["lesson_name"],
            })
        grade = chosen["grade"]
        if grade is not None:
            roster, unconfirmed = class_roster(database, grade)
            # IZMJENA IDE PO IDENTITETU ČASA, ne po skupu polja: `class_id` je
            # jedino što ostaje isto i kad se datum, vrijeme ili tema promijene.
            if chosen["class_id"]:
                saved = {row["student_id"]: row for row
                         in database.fetch_class_students(chosen["class_id"])}
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_roster_failed code=%s", error.code)
        chosen.setdefault("class_id", None)
        return render_template(
            "admin_class_entry.html", chosen=chosen, roster=[], saved={},
            curriculum={}, areas=[], unconfirmed=0,
            grades=class_entry.VALID_GRADES,
            activity_labels=student_sessions.ACTIVITY_LABELS,
            homework_labels=student_sessions.HOMEWORK_LABELS,
            homework_default=class_entry.PRESENT_HOMEWORK_DEFAULT,
            participation_labels=class_entry.PARTICIPATION_LABELS,
            participation_default=class_entry.PARTICIPATION_DEFAULT,
            topic_labels=class_entry.TOPIC_MODE_LABELS,
            topic_curriculum=student_sessions.TOPIC_CURRICULUM,
            topic_custom=student_sessions.TOPIC_CUSTOM,
            max_topic_chars=student_sessions.MAX_LABEL_CHARS,
            csrf_token=admin_auth.csrf_token(), today=_today(),
            error=ERROR_UNAVAILABLE), 503

    curriculum = topics.curriculum_choices(grade) if grade is not None else {}
    return render_template(
        "admin_class_entry.html", chosen=chosen, roster=roster, saved=saved,
        curriculum=curriculum, areas=list(curriculum), unconfirmed=unconfirmed,
        grades=class_entry.VALID_GRADES,
        activity_labels=student_sessions.ACTIVITY_LABELS,
        homework_labels=student_sessions.HOMEWORK_LABELS,
        homework_default=class_entry.PRESENT_HOMEWORK_DEFAULT,
        participation_labels=class_entry.PARTICIPATION_LABELS,
        participation_default=class_entry.PARTICIPATION_DEFAULT,
        topic_labels=class_entry.TOPIC_MODE_LABELS,
        topic_curriculum=student_sessions.TOPIC_CURRICULUM,
        topic_custom=student_sessions.TOPIC_CUSTOM,
        max_topic_chars=student_sessions.MAX_LABEL_CHARS,
        csrf_token=admin_auth.csrf_token(), today=_today(),
        error=request.args.get("error", ""))


_STUDENT_FIELD_RE = re.compile(r"\As(\d+)_participation\Z")


def _submissions():
    """Formular → `{student_id: polja}` za SVE poslane učenike.

    ČITA SE I ONO ŠTO NIJE NA SPISKU, i to je namjerno. Prva verzija je čitala
    samo članove serverskog spiska, pa je podmetnut osmoškolac u sedmi razred
    naprosto ISPADAO iz obrade: nije se upisao (dobro), ali ni prijavio (loše) —
    ostatak časa bi se sačuvao kao da je sve u redu, a to je tačno ono „tiho"
    koje se traži da ne postoji.

    Zato se ovdje uzima svaki `s<id>_participation`, a pripadnost spisku
    provjerava `class_entry.build_class_records` — i podmetnut učenik obara
    CIJELI čas umjesto da nestane. Isto pravilo hvata i bezazlen slučaj: učenik
    kojem je razred promijenjen između otvaranja stranice i čuvanja."""
    fields = {}
    for key in request.form:
        match = _STUDENT_FIELD_RE.match(key)
        if not match:
            continue
        student_id = int(match.group(1))
        prefix = "s%d_" % student_id
        fields[student_id] = {
            "participation": request.form.get(key),
            "activity_rating": request.form.get(prefix + "activity"),
            "homework_status": request.form.get(prefix + "homework"),
            "comment": request.form.get(prefix + "comment"),
        }
    return fields


def _back(chosen, message):
    return redirect(url_for("admin_sessions.new_class",
                            session_date=chosen["session_date"],
                            session_time=chosen["session_time"],
                            grade=chosen["grade"],
                            topic_mode=chosen["topic_mode"],
                            area_name=chosen["area_name"],
                            lesson_name=chosen["lesson_name"], error=message))


@admin_sessions_bp.route("/bulk", methods=["POST"])
@require_admin
def save_class():
    """JEDNO ČUVANJE ZA CIJELI ČAS. POST + CSRF + admin.

    Redoslijed je namjeran: CSRF, pa razred, pa SERVERSKI spisak, pa tek onda
    redovi. Spisak se izvodi IZ BAZE prije nego što se pogleda ijedno polje
    formulara — inače bi klijent mogao odrediti ko je učesnik."""
    _require_csrf()
    chosen = _selection(request.form)
    if chosen["grade"] is None:
        return _back(chosen, ERROR_GRADE)

    try:
        roster, _ = class_roster(_db(), chosen["grade"])
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_roster_failed code=%s", error.code)
        return _back(chosen, ERROR_UNAVAILABLE)

    try:
        records = class_entry.build_class_records(
            session_date=chosen["session_date"],
            session_time=chosen["session_time"], grade=chosen["grade"],
            topic_mode=chosen["topic_mode"],
            area_name=chosen["area_name"], lesson_name=chosen["lesson_name"],
            roster_ids=[s["student_id"] for s in roster],
            submissions=_submissions())
    except (class_entry.ClassEntryError,
            student_sessions.SessionValidationError) as error:
        # NIŠTA NIJE UPISANO: provjera je cijela obavljena prije prvog upisa.
        logger.info("admin_class_rejected code=%s", error.code)
        return _back(chosen, _MESSAGES.get(error.code, ERROR_SESSION))

    if not records:
        return _back(chosen, ERROR_EMPTY)

    try:
        class_id, counters = _db().save_class(
            class_id=chosen["class_id"],
            session_date=chosen["session_date"],
            session_time=records[0][1]["session_time"],
            grade=chosen["grade"], topic_source=chosen["topic_mode"],
            area_name=chosen["area_name"] or None,
            lesson_name=records[0][1]["lesson_name"], records=records)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_save_failed code=%s", error.code)
        return _back(chosen, _MESSAGES.get(error.code, ERROR_UNAVAILABLE))

    totals = class_entry.summarize_saved(records)
    # Bez PII: brojevi, datum i razred — nikad ime, komentar ni e-mail.
    # Bez PII: identitet časa i brojevi — nikad ime, komentar ni e-mail.
    logger.info("admin_class_saved class_id=%s date=%s time=%s grade=%s "
                "topic_source=%s rows=%s present=%s absent=%s inserted=%s "
                "updated=%s removed=%s",
                class_id, chosen["session_date"], chosen["session_time"],
                chosen["grade"], chosen["topic_mode"], totals["rows"],
                totals["present"], totals["absent"], counters["inserted"],
                counters["updated"], counters["removed"])
    return redirect(url_for("admin_sessions.class_detail", class_id=class_id,
                            saved=1))


@admin_sessions_bp.route("", methods=["GET"])
@admin_sessions_bp.route("/", methods=["GET"])
@require_admin
def class_list():
    """Svi časovi — JEDAN RED PO STVARNOM ČASU.

    Podrazumijevano tekući mjesec: pregled je radni spisak, a ne izvoz cijele
    istorije. Filtriranje i ograničenje idu u SQL."""
    month = (request.args.get("month") or "").strip() or _this_month()
    try:
        start, end = report_input.month_bounds(month)
    except Exception:
        month = _this_month()
        start, end = report_input.month_bounds(month)

    grade = class_entry.clean_grade(request.args.get("grade"))
    source = (request.args.get("topic_source") or "").strip()
    if source not in student_sessions.TOPIC_SOURCES:
        source = ""
    search = (request.args.get("q") or "").strip()[:120]
    page = max(1, _clean_id(request.args.get("page")) or 1)

    try:
        classes, total = _db().fetch_classes(
            date_start=start[:10], date_end=end[:10], grade=grade,
            topic_source=source or None, search=search or None,
            limit=CLASSES_PER_PAGE, offset=(page - 1) * CLASSES_PER_PAGE)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_list_failed code=%s", error.code)
        classes, total = [], 0

    pages = max(1, (total + CLASSES_PER_PAGE - 1) // CLASSES_PER_PAGE)
    return render_template(
        "admin_class_list.html", classes=classes, total=total, month=month,
        previous_month=report_input.previous_month(month),
        next_month=_next_month(month), grade=grade, topic_source=source,
        search=search, page=page, pages=pages,
        grades=class_entry.VALID_GRADES,
        topic_labels=class_entry.TOPIC_MODE_LABELS,
        topic_curriculum=student_sessions.TOPIC_CURRICULUM,
        topic_custom=student_sessions.TOPIC_CUSTOM,
        filtered=bool(grade or source or search),
        error=request.args.get("error", ""))


def _next_month(month):
    year, number = int(month[:4]), int(month[5:7])
    return "%04d-%02d" % ((year + 1, 1) if number == 12 else (year, number + 1))


@admin_sessions_bp.route("/<int:class_id>", methods=["GET"])
@require_admin
def class_detail(class_id):
    """Pregled jednog časa: zajedničke činjenice, sažetak i redovi učenika."""
    from matbot import admin_auth

    try:
        database = _db()
        found = database.fetch_class(class_id)
        if found is None:
            abort(404)
        students = database.fetch_class_students(class_id)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_detail_failed code=%s", error.code)
        abort(503)

    summary = student_sessions.build_monthly_summary(students)
    return render_template(
        "admin_class_detail.html", klass=found, students=students,
        summary=summary,
        activity_labels=student_sessions.ACTIVITY_LABELS,
        homework_labels=student_sessions.HOMEWORK_LABELS,
        attendance_labels=student_sessions.ATTENDANCE_LABELS,
        topic_labels=class_entry.TOPIC_MODE_LABELS,
        topic_custom=student_sessions.TOPIC_CUSTOM,
        csrf_token=admin_auth.csrf_token(),
        saved=bool(request.args.get("saved")),
        error=request.args.get("error", ""))


@admin_sessions_bp.route("/<int:class_id>/delete", methods=["GET"])
@require_admin
def confirm_delete(class_id):
    """Potvrda brisanja. GET SAMO PRIKAZUJE — ništa se ne mijenja."""
    from matbot import admin_auth

    try:
        database = _db()
        found = database.fetch_class(class_id)
        if found is None:
            abort(404)
        students = database.fetch_class_students(class_id)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_detail_failed code=%s", error.code)
        abort(503)
    return render_template(
        "admin_class_delete.html", klass=found, row_count=len(students),
        topic_custom=student_sessions.TOPIC_CUSTOM,
        csrf_token=admin_auth.csrf_token())


@admin_sessions_bp.route("/<int:class_id>/delete", methods=["POST"])
@require_admin
def delete_class(class_id):
    """Obriši TAČNO taj čas i njegove redove. POST + CSRF + admin."""
    _require_csrf()
    try:
        result = _db().delete_class(class_id)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_delete_failed code=%s", error.code)
        return redirect(url_for("admin_sessions.class_detail",
                                class_id=class_id,
                                error=ERROR_CLASS_UNAVAILABLE))
    if result is None:
        abort(404)
    # Bez PII: identitet časa i broj redova.
    logger.info("admin_class_deleted class_id=%s removed_rows=%s",
                result["class_id"], result["removed_rows"])
    return redirect(url_for("admin_sessions.class_list", deleted=1))


@admin_sessions_bp.route("/saved", methods=["GET"])
@require_admin
def saved_class():
    """Potvrda poslije čuvanja. Čisto prikaz — nijedan upit ne mijenja stanje.

    Brojevi dolaze iz URL-a jer su već izračunati pri čuvanju; ponovno čitanje
    baze ovdje ne bi dodalo garanciju, a dodalo bi upit na svako osvježavanje."""
    chosen = _selection(request.args)

    def count(name):
        try:
            return max(0, int(request.args.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    return render_template(
        "admin_class_saved.html", chosen=chosen,
        present=count("present"), absent=count("absent"))
