"""Uvoz Thinkific snimaka i DETERMINISTIČKI ulaz za mjesečni izvještaj.

Ovdje se spajaju dva izvora i ništa se ne izmišlja:

  MAT-BOT (Faza 2)   — šta je učenik RADIO: zadaci, tačnost, nagovještaji,
                       kontrolni i ishodi po pitanju. Autoritet za učinak.
  Thinkific (3A)     — koliko je kursa PROŠAO: % pregledano, % završeno,
                       napredak po sekciji, datumi. Autoritet za angažman.

NIJEDAN BROJ NE DOLAZI OD MODELA. Ovaj modul ne zove OpenAI, ne piše prozu i ne
pravi PDF. Budući AI sažetak smije dobiti SAMO gotov objekat iz
`build_report_input` — dakle brojeve koje je izračunao SQL, ne obrnuto.

GRANICE MJESECA SU UTC I ZATVORENO-OTVORENE (Dio 24):

    report_month "2026-09"  ->  >= '2026-09-01 00:00:00'
                                <  '2026-10-01 00:00:00'

Isti par granica važi za `learning_activity.occurred_at` i
`assessment_attempts.completed_at`, jer oba polja Faza 2 pamti u UTC-u u istom
obliku kao `CURRENT_TIMESTAMP`. Lokalno sarajevsko vrijeme se u ovoj fazi
NAMJERNO ne uvodi: jedna vremenska osnova kroz cijeli izvještajni sloj.
"""
import logging

from matbot import reporting_db
from matbot import student_identity
from matbot import thinkific_progress as progress
from matbot.thinkific_progress import ProgressFormatError

logger = logging.getLogger("matbot.report_input")

# Koliko pitanja iz kontrolnih mora postojati da bi se lekcija uopšte smjela
# opisati kao slaba. JEDNO promašeno pitanje je slučajnost, ne dokaz — pa svaki
# ishod nosi i `evidence_items`, a `low_evidence` govori sloju iznad da tvrdnja
# nije potkrijepljena. Prag NE briše podatak, samo ga poštneno označava.
MIN_EVIDENCE_ITEMS_FOR_WEAKNESS = 3


def month_bounds(report_month):
    """`YYYY-MM` → (`start` uključivo, `next` isključivo) u UTC tekstu."""
    month = progress.parse_report_month(report_month)
    year, number = int(month[:4]), int(month[5:7])
    next_year, next_number = (year + 1, 1) if number == 12 else (year, number + 1)
    return ("%04d-%02d-01 00:00:00" % (year, number),
            "%04d-%02d-01 00:00:00" % (next_year, next_number))


def previous_month(report_month):
    month = progress.parse_report_month(report_month)
    year, number = int(month[:4]), int(month[5:7])
    return "%04d-%02d" % ((year - 1, 12) if number == 1 else (year, number - 1))


def _delta(current, previous):
    """Razlika SAMO kad oba mjerenja postoje.

    `None` znači „ne znamo", i to nikad ne smije postati 0: nula bi u izvještaju
    roditelju značila izmjeren izostanak napretka, a mi jednostavno nemamo
    prošlomjesečni snimak."""
    if current is None or previous is None:
        return None
    return round(current - previous, 4)


# ---------------------------------------------------------------------------
# UVOZ
# ---------------------------------------------------------------------------
class ImportSummary:
    """Ne-PII sažetak za administratora."""

    FIELDS = ("files_received", "files_imported", "rows_seen", "students_created",
              "students_reused", "snapshots_inserted", "snapshots_updated",
              "sections_written", "invalid_rows", "grade_conflicts")

    def __init__(self, report_month):
        self.report_month = report_month
        for field in self.FIELDS:
            setattr(self, field, 0)
        self.files = []          # [{course_key, status, code?, row_count}]
        self.errors = []         # [{course_key, code, row?, column?}]

    def as_dict(self):
        data = {"report_month": self.report_month}
        for field in self.FIELDS:
            data[field] = getattr(self, field)
        data["files"] = list(self.files)
        data["errors"] = list(self.errors)
        return data


def import_progress_files(report_month, files, database=None):
    """Uvezi 1–4 Thinkific izvoza za JEDAN mjesec. Vraća `ImportSummary`.

    `files` je `{course_key: raw_bytes}` — administratorska stranica će imati
    četiri izričita slota (`grade_6`…`grade_9`) i nijedan nije obavezan.

    ATOMIČNO PO FAJLU, NE PO PAKETU (Dio 16, svjestan izbor):
    fajl koji ima ijedan neispravan red se ODBIJA U CIJELOSTI i ne upisuje ništa;
    ostali fajlovi se uvoze normalno. Razlog je predvidljivost za administratora:
    „6. razred je uvezen, 7. je odbijen zbog reda 12" je uputstvo za popravku,
    dok bi odbijanje cijelog paketa zbog jednog reda u jednom razredu značilo da
    tri ispravna izvoza treba ponavljati bez potrebe. Djelimično uvezen FAJL se
    ne dopušta nikad — to je jedini ishod koji izgleda potpuno a nije.

    Ovaj poziv je SINHRON i namjerno nije na tutorskom putu: administrator čeka
    ishod uvoza, za razliku od izvještajnih događaja koji idu u pozadinu."""
    month = progress.parse_report_month(report_month)
    summary = ImportSummary(month)
    target = database or reporting_db.get_database()

    for course_key in sorted(files or {}):
        raw = files[course_key]
        summary.files_received += 1
        try:
            parsed = progress.parse_progress_csv(raw, course_key, month)
        except ProgressFormatError as error:
            summary.invalid_rows += 1
            summary.files.append({"course_key": course_key, "status": "rejected",
                                  "code": error.code})
            summary.errors.append({"course_key": course_key, "code": error.code,
                                   "row": error.row, "column": error.column})
            logger.info("thinkific_import_rejected course=%s code=%s row=%s",
                        course_key, error.code, error.row)
            continue

        try:
            _import_parsed_file(target, parsed, summary)
        except reporting_db.ReportingUnavailable as error:
            summary.files.append({"course_key": course_key, "status": "failed",
                                  "code": error.code})
            summary.errors.append({"course_key": course_key, "code": error.code})
            logger.info("thinkific_import_failed course=%s code=%s",
                        course_key, error.code)
            continue

        summary.files_imported += 1
        summary.files.append({"course_key": course_key, "status": "imported",
                              "row_count": parsed.row_count})
    return summary


def _import_parsed_file(target, parsed, summary):
    import_id = target.record_progress_import(
        report_month=parsed.report_month, course_key=parsed.course_key,
        course_name=parsed.course_name, grade=parsed.grade,
        source_sha256=parsed.source_sha256, row_count=parsed.row_count)

    for row in parsed.rows:
        summary.rows_seen += 1
        email = student_identity.normalize_email(row.email)
        if email is None:
            # Do ovdje dolazi samo adresa koja je NEPRAZNA ali neispravna;
            # prazna je već oborila fajl u parseru.
            raise reporting_db.ReportingUnavailable("email_unusable")

        student_id, created = _resolve_student(target, email, parsed.grade)
        if created:
            summary.students_created += 1
        else:
            summary.students_reused += 1

        name_set, grade_set, conflict = target.update_student_profile(
            student_id, display_name=row.display_name, grade=parsed.grade)
        if conflict:
            summary.grade_conflicts += 1
            # SAMO otisak — nikad e-mail ni ime u logu administratora.
            logger.info("thinkific_grade_conflict course=%s subject=%s",
                        parsed.course_key, student_identity.fingerprint(email))

        outcome, _snapshot_id, written = target.upsert_progress_snapshot(
            import_id=import_id, student_id=student_id,
            report_month=parsed.report_month, course_key=parsed.course_key,
            course_name=parsed.course_name, grade=parsed.grade,
            percent_viewed=row.percent_viewed,
            percent_completed=row.percent_completed,
            started_at=row.started_at, completed_at=row.completed_at,
            activated_at=row.activated_at, expires_at=row.expires_at,
            last_sign_in=row.last_sign_in, sections=row.sections)
        if outcome == "inserted":
            summary.snapshots_inserted += 1
        else:
            summary.snapshots_updated += 1
        summary.sections_written += written


def _resolve_student(target, email, grade):
    """Isti identitet kao Faza 1 — nikad nov prostor imena.

    Učenik koji NIKAD nije koristio MAT-BOT se ovdje kreira: izvještaj mu i dalje
    pripada, jer napredak u kursu postoji nezavisno od tutora."""
    existing = target.find_student(student_identity.PROVIDER_THINKIFIC_EMAIL, email)
    student_id = target.get_or_create_student(
        student_identity.PROVIDER_THINKIFIC_EMAIL, email, grade=grade)
    return student_id, existing is None


# ---------------------------------------------------------------------------
# ČITANJE: mjesec naspram prethodnog
# ---------------------------------------------------------------------------
def build_thinkific_section(student_id, report_month, database=None):
    """Thinkific dio izvještaja: tekući snimak, prošli i razlike."""
    target = database or reporting_db.get_database()
    month = progress.parse_report_month(report_month)
    current = target.fetch_progress_snapshot(student_id, month)
    if current is None:
        # Učenik postoji u MAT-BOT-u, ali za ovaj mjesec nema izvoza. To je
        # činjenica koju izvještaj mora reći, a ne rupa koju treba popuniti.
        return {"snapshot_missing": True}

    prior = target.fetch_progress_snapshot(
        student_id, previous_month(month), course_key=current["course_key"])
    prior_sections = {}
    if prior:
        prior_sections = {s["section_name"]: s["progress_percent"]
                          for s in prior["sections"]}

    sections = []
    for section in current["sections"]:
        previous_value = prior_sections.get(section["section_name"])
        sections.append({
            "ordinal": section["ordinal"],
            "section_name": section["section_name"],
            "current_progress_percent": section["progress_percent"],
            # Sekcija koje prošlog mjeseca NIJE bilo nema prethodnu vrijednost.
            # Ne pravimo se da je narasla od nule — nismo to mjerili.
            "previous_progress_percent": previous_value,
            "delta_progress_percent": _delta(section["progress_percent"],
                                             previous_value),
        })

    return {
        "snapshot_missing": False,
        "course_key": current["course_key"],
        "course_name": current["course_name"],
        "grade": current["grade"],
        "percent_viewed": current["percent_viewed"],
        "previous_percent_viewed": prior["percent_viewed"] if prior else None,
        "delta_percent_viewed": _delta(current["percent_viewed"],
                                       prior["percent_viewed"] if prior else None),
        "percent_completed": current["percent_completed"],
        "previous_percent_completed": prior["percent_completed"] if prior else None,
        "delta_percent_completed": _delta(current["percent_completed"],
                                          prior["percent_completed"] if prior else None),
        "started_at": current["started_at"],
        "completed_at": current["completed_at"],
        "last_sign_in": current["last_sign_in"],
        "previous_report_month": previous_month(month) if prior else None,
        "sections": sections,
    }


def build_matbot_section(student_id, report_month, database=None):
    """MAT-BOT dio izvještaja — sve iz Faze 2, sve determinističko."""
    from matbot import activity

    target = database or reporting_db.get_database()
    start, end = month_bounds(report_month)
    raw = target.fetch_matbot_month(student_id, start, end)
    counts = raw["counts"]

    correct = counts.get(activity.PRACTICE_ANSWER_CORRECT, 0)
    incorrect = counts.get(activity.PRACTICE_ANSWER_INCORRECT, 0)
    answered = correct + incorrect
    attempts, average, sum_correct, sum_total = raw["exams"]

    outcomes = []
    for lesson_id, lesson_name, area_name, difficulty, wrong, asked in raw["lesson_outcomes"]:
        outcomes.append({
            "lesson_id": lesson_id, "lesson_name": lesson_name,
            "area_name": area_name, "difficulty": difficulty,
            "incorrect_items": wrong, "evidence_items": asked,
            # DOKAZNA SNAGA, NE ZAKLJUČAK: sloj iznad (i budući AI) mora znati
            # da jedno promašeno pitanje nije nalaz o znanju.
            "low_evidence": asked < MIN_EVIDENCE_ITEMS_FOR_WEAKNESS,
        })

    return {
        "active_days": raw["active_days"] or 0,
        "practice_tasks": counts.get(activity.PRACTICE_TASK_PRESENTED, 0),
        "practice_correct": correct,
        "practice_incorrect": incorrect,
        # Tačnost postoji SAMO kad postoji imenilac. Učenik koji nije odgovarao
        # nema 0 % — nema mjerenja.
        "practice_accuracy": (round(100.0 * correct / answered, 1)
                              if answered else None),
        "hints_used": counts.get(activity.PRACTICE_HINT_USED, 0),
        "full_solutions_shown": counts.get(activity.PRACTICE_FULL_SOLUTION_SHOWN, 0),
        "explain_count": counts.get(activity.EXPLAIN_COMPLETED, 0),
        "quick_count": counts.get(activity.QUICK_COMPLETED, 0),
        "kontrolni_generated": counts.get(activity.KONTROLNI_GENERATED, 0),
        "kontrolni_attempts": attempts or 0,
        "kontrolni_average": round(average, 1) if average is not None else None,
        "kontrolni_correct": sum_correct,
        "kontrolni_total": sum_total,
        "lesson_outcomes": outcomes,
    }


def build_report_input(student_id, report_month, database=None):
    """JEDAN determinističan objekat po (učenik, mjesec).

    Ovo je jedina stvar koju budući AI sažetak smije dobiti: svaki broj u njemu
    je već izračunat SQL-om i Pythonom, pa model nema šta ni da sabere ni da
    procijeni — samo da opiše."""
    target = database or reporting_db.get_database()
    month = progress.parse_report_month(report_month)
    profile = target.fetch_student_profile(student_id) or {}
    return {
        "student_id": student_id,
        "report_month": month,
        "profile": {"display_name": profile.get("display_name"),
                    "grade": profile.get("grade")},
        "thinkific": build_thinkific_section(student_id, month, database=target),
        "matbot": build_matbot_section(student_id, month, database=target),
    }


def report_population(report_month, database=None):
    """Svi učenici koji za taj mjesec zaslužuju izvještaj (unija oba izvora)."""
    target = database or reporting_db.get_database()
    month = progress.parse_report_month(report_month)
    start, end = month_bounds(month)
    return target.fetch_report_population(start, end, month)
