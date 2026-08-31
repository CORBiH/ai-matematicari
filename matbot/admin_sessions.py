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

from matbot import (class_entry, reporting_db, student_grades, student_sessions,
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
ERROR_ROSTER = "Neki učenik ne pripada izabranom razredu."
ERROR_EMPTY = "Označite bar jednog učenika kao prisutnog ili odsutnog."
ERROR_SESSION = "Podaci o času nisu ispravni."

# Interni kod → poruka za ekran. Kodovi ostaju u logu (pravilo 7).
_MESSAGES = {
    "class_grade_invalid": ERROR_GRADE,
    "class_curriculum_incomplete": ERROR_CURRICULUM,
    "class_curriculum_unknown": ERROR_CURRICULUM,
    "class_student_not_in_roster": ERROR_ROSTER,
    "class_activity_required": ERROR_ACTIVITY,
    "session_date_format": ERROR_DATE,
    "session_date_invalid": ERROR_DATE,
    "session_curriculum_unknown": ERROR_CURRICULUM,
    "session_curriculum_incomplete": ERROR_CURRICULUM,
}


def _db():
    return reporting_db.get_database()


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
    """Zajednička polja časa iz `args` ili `form`. Ne validira — samo čita."""
    return {
        "session_date": (source.get("session_date") or "").strip() or _today(),
        "grade": class_entry.clean_grade(source.get("grade")),
        "area_name": (source.get("area_name") or "").strip(),
        "lesson_name": (source.get("lesson_name") or "").strip(),
    }


@admin_sessions_bp.route("/new", methods=["GET"])
@require_admin
def new_class():
    """Formular. GET NIŠTA NE MIJENJA — ni kad nosi sve parametre časa.

    S punom četvorkom (datum, razred, oblast, lekcija) stranica se ponaša kao
    IZMJENA: već upisani redovi se učitavaju u polja."""
    from matbot import admin_auth

    chosen = _selection(request.args)
    grade = chosen["grade"]
    roster, unconfirmed, saved = [], 0, {}
    try:
        database = _db()
        if grade is not None:
            roster, unconfirmed = class_roster(database, grade)
            if chosen["area_name"] and chosen["lesson_name"]:
                saved = database.fetch_class_sessions(
                    chosen["session_date"], chosen["area_name"],
                    chosen["lesson_name"], [s["student_id"] for s in roster])
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_roster_failed code=%s", error.code)
        return render_template(
            "admin_class_entry.html", chosen=chosen, roster=[], saved={},
            curriculum={}, areas=[], unconfirmed=0,
            grades=class_entry.VALID_GRADES,
            activity_labels=student_sessions.ACTIVITY_LABELS,
            homework_labels=student_sessions.HOMEWORK_LABELS,
            homework_default=class_entry.PRESENT_HOMEWORK_DEFAULT,
            participation_labels=class_entry.PARTICIPATION_LABELS,
            participation_default=class_entry.PARTICIPATION_DEFAULT,
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
                            grade=chosen["grade"], area_name=chosen["area_name"],
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
            session_date=chosen["session_date"], grade=chosen["grade"],
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
        counters = _db().save_class_sessions(records)
    except reporting_db.ReportingUnavailable as error:
        logger.info("admin_class_save_failed code=%s", error.code)
        return _back(chosen, ERROR_UNAVAILABLE)

    totals = class_entry.summarize_saved(records)
    # Bez PII: brojevi, datum i razred — nikad ime, komentar ni e-mail.
    logger.info("admin_class_saved date=%s grade=%s rows=%s present=%s "
                "absent=%s inserted=%s updated=%s",
                chosen["session_date"], chosen["grade"], totals["rows"],
                totals["present"], totals["absent"], counters["inserted"],
                counters["updated"])
    return redirect(url_for("admin_sessions.saved_class",
                            session_date=chosen["session_date"],
                            grade=chosen["grade"], area_name=chosen["area_name"],
                            lesson_name=chosen["lesson_name"],
                            present=totals["present"], absent=totals["absent"]))


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
