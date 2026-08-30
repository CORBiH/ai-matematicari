"""Faza 3D — SAMO ČITANJE: odakle je došao razred iz Thinkific uvoza.

    python -m matbot.thinkific_grade_forensics

ZAŠTO OVAJ ALAT POSTOJI. Revizija razreda (`student_grade_audit`) gleda
UČENIKE. Ovaj alat gleda IZVOR: koji su fajlovi uvezeni, pod kojim slotom, i
šta je STVARNO bilo u njima.

KLJUČNA ČINJENICA ZA ČITANJE IZLAZA:

  `thinkific_progress_imports.grade` i `.course_name` NISU dokaz o sadržaju
  fajla. Oba se izvode iz SLOTA koji je administrator izabrao u formularu
  (`COURSE_SLOTS` u `matbot/thinkific_progress.py`). Izvoz „Student Progress"
  nema kolonu s nazivom kursa — modul to i kaže — pa sistem NEMA način da
  primijeti pogrešno izabran slot. Uvoz označen kao 6. razred pisaće „grade 6"
  i „Matematika za 6. razred" bez obzira šta je u fajlu.

  JEDINI PODATAK KOJI DOLAZI IZ SAMOG FAJLA su NAZIVI SEKCIJA
  (`thinkific_progress_sections.section_name`) — dinamičke kolone zaglavlja.
  `section_columns()` ih namjerno ne ugrađuje u kod: „izvoz 7/8/9. razreda ima
  druge nazive". Zato su oni jedini nezavisan trag o tome koji je kurikulum
  zaista uvezen.

  ALAT NE ZAKLJUČUJE IZ NJIH. Ispisuje ih, a čovjek gleda. Automatsko
  „prepoznavanje razreda po nazivu sekcije" bilo bi nagađanje iste vrste kao
  čitanje razreda iz imena učenika.

GARANCIJE: isključivo `SELECT`. Nema `INSERT`, `UPDATE`, `DELETE`, DDL-a,
`commit`-a, migracije ni `--apply` opcije. Ne ispisuje e-mail,
`external_user_id`, token, komentar s časa ni tekst izvještaja.
"""
import sys

from matbot import reporting_db, student_grades

SHA_PREFIX = 12


def _short(value):
    return "-" if value in (None, "") else str(value)


def collect(database=None):
    """Sve što forenzika treba, u jednom čitanju. Ne mijenja ništa."""
    target = database or reporting_db.get_database()
    return {
        "imports": target.fetch_progress_imports(),
        "sections": target.fetch_import_sections(),
        "students": _students(target),
    }


def _students(target):
    rows = []
    for student in target.list_students():
        student_id = student["student_id"]
        evidence = target.fetch_grade_evidence(student_id)
        status, recommended = student_grades.classify(student["grade"], evidence)
        rows.append({
            "student_id": student_id,
            "display_name": student["display_name"] or "",
            "stored_grade": student["grade"],
            "thinkific_connected": "yes" if student["thinkific_linked"] else "no",
            "latest_thinkific_grade": evidence["thinkific"]["grade"],
            "latest_thinkific_month": evidence["thinkific"]["month"],
            "latest_assessment_grade": evidence["assessment"]["grade"],
            "latest_assessment_date": evidence["assessment"]["when"],
            "latest_matbot_grade": evidence["matbot"]["grade"],
            "latest_matbot_date": evidence["matbot"]["when"],
            # SAMO za ljudski pregled — ne ulazi u klasifikaciju.
            "name_grade_hint": student_grades.name_grade_hint(
                student["display_name"]),
            "status_after_fixed_classifier": status,
            "recommended_grade": recommended,
            "history": target.fetch_student_thinkific_history(student_id),
        })
    return rows


def format_report(data):
    lines = []
    imports = data["imports"]

    lines.append("=== IMPORTS ===")
    lines.append("%-4s %-9s %-9s %-28s %-6s %-6s %-21s %s"
                 % ("id", "month", "slot", "course_name(derived)", "grade",
                    "rows", "imported_at", "sha[:12]"))
    for row in imports:
        lines.append("%-4s %-9s %-9s %-28s %-6s %-6s %-21s %s"
                     % (row["id"], row["report_month"], row["course_key"],
                        (row["course_name"] or "")[:28], row["grade"],
                        row["row_count"], row["imported_at"],
                        (row["source_sha256"] or "")[:SHA_PREFIX]))
    lines.append("THINKIFIC_IMPORTS_TOTAL: %d" % len(imports))

    by_grade = {}
    for row in imports:
        by_grade[row["grade"]] = by_grade.get(row["grade"], 0) + 1
    lines.append("THINKIFIC_IMPORTS_BY_GRADE: %s" % dict(sorted(by_grade.items())))

    snapshots_by_grade = {}
    for row in imports:
        for grade, count in (row["snapshot_grades"] or {}).items():
            snapshots_by_grade[grade] = snapshots_by_grade.get(grade, 0) + count
    lines.append("SNAPSHOTS_BY_GRADE: %s" % dict(sorted(snapshots_by_grade.items())))

    lines.append("")
    lines.append("=== SNAPSHOTS PER IMPORT ===")
    lines.append("%-10s %-9s %-8s %-10s %s"
                 % ("import_id", "slot", "cfg_grade", "snapshots", "snapshot_grades"))
    for row in imports:
        grades = row["snapshot_grades"] or {}
        lines.append("%-10s %-9s %-8s %-10s %s"
                     % (row["id"], row["course_key"], row["grade"],
                        sum(grades.values()),
                        ", ".join("%s:%s" % kv for kv in sorted(grades.items()))
                        or "-"))

    # --- ISTI FAJL U VIŠE SLOTOVA -----------------------------------------
    lines.append("")
    lines.append("=== DISTINCT SOURCE FILES ===")
    by_hash = {}
    for row in imports:
        by_hash.setdefault((row["source_sha256"] or "")[:SHA_PREFIX], []).append(row)
    lines.append("DISTINCT_SOURCE_FILES: %d (medju %d uvoza)"
                 % (len(by_hash), len(imports)))
    for sha, group in sorted(by_hash.items()):
        slots = sorted({r["course_key"] for r in group})
        marker = "  <-- ISTI FAJL U VISE SLOTOVA" if len(slots) > 1 else ""
        lines.append("  %s  imports=%s slots=%s%s"
                     % (sha, [r["id"] for r in group], slots, marker))

    # --- JEDINI DOKAZ IZ SAMOG FAJLA ---------------------------------------
    lines.append("")
    lines.append("=== SECTION NAMES (the only CSV-derived evidence) ===")
    lines.append("Nazivi dolaze iz zaglavlja izvoza, ne iz izabranog slota.")
    for row in imports:
        lines.append("  IMPORT %s  slot=%s  configured_grade=%s  month=%s"
                     % (row["id"], row["course_key"], row["grade"],
                        row["report_month"]))
        names = data["sections"].get(row["id"]) or []
        for ordinal, name in names:
            lines.append("      %2s. %s" % (ordinal, name))
        if not names:
            lines.append("      (nema sekcija)")

    # --- UČENICI ------------------------------------------------------------
    students = data["students"]
    disagreeing = [s for s in students
                   if s["status_after_fixed_classifier"] != student_grades.STATUS_CONSISTENT]
    lines.append("")
    lines.append("=== STUDENTS (corrected classifier) ===")
    tally = {}
    for s in students:
        tally[s["status_after_fixed_classifier"]] = tally.get(
            s["status_after_fixed_classifier"], 0) + 1
    lines.append("TOTAL: %d" % len(students))
    for status in (student_grades.STATUS_CONSISTENT,
                   student_grades.STATUS_LIKELY_STALE,
                   student_grades.STATUS_CONFLICTING,
                   student_grades.STATUS_INSUFFICIENT):
        lines.append("%-22s %d" % (status + ":", tally.get(status, 0)))

    lines.append("")
    lines.append("=== NON-CONSISTENT STUDENTS (%d) ===" % len(disagreeing))
    lines.append("%-4s %-22s %-6s %-4s %-9s %-5s %-20s %-6s %-20s %-5s %-22s %s"
                 % ("id", "display_name", "stored", "tk", "tk_month", "exam",
                    "exam_date", "matbot", "matbot_date", "hint", "status",
                    "recommended"))
    for s in disagreeing:
        lines.append("%-4s %-22s %-6s %-4s %-9s %-5s %-20s %-6s %-20s %-5s %-22s %s"
                     % (s["student_id"], (s["display_name"] or "")[:22],
                        _short(s["stored_grade"]),
                        _short(s["latest_thinkific_grade"]),
                        _short(s["latest_thinkific_month"]),
                        _short(s["latest_assessment_grade"]),
                        _short(s["latest_assessment_date"]),
                        _short(s["latest_matbot_grade"]),
                        _short(s["latest_matbot_date"]),
                        _short(s["name_grade_hint"]),
                        s["status_after_fixed_classifier"],
                        _short(s["recommended_grade"])))

    lines.append("")
    lines.append("=== THINKIFIC HISTORY FOR NON-CONSISTENT STUDENTS ===")
    for s in disagreeing:
        lines.append("  student_id=%s %s (stored=%s)"
                     % (s["student_id"], (s["display_name"] or "")[:22],
                        _short(s["stored_grade"])))
        for h in s["history"]:
            lines.append("      %-9s %-9s %-26s grade=%-4s viewed=%-6s completed=%s"
                         % (h["report_month"], h["course_key"],
                            (h["course_name"] or "")[:26], h["grade"],
                            _short(h["percent_viewed"]),
                            _short(h["percent_completed"])))
        if not s["history"]:
            lines.append("      (nema Thinkific snimaka)")

    lines.append("")
    lines.append("READ_ONLY: nijedan podatak nije promijenjen.")
    return "\n".join(lines)


def main(argv=None):
    """Izlaz 0 kad je forenzika obavljena; 1 kad se baza ne moze procitati.

    NEMA `--apply`: alat prijavljuje, ne popravlja."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m matbot.thinkific_grade_forensics",
        description="Porijeklo razreda iz Thinkific uvoza (SAMO CITANJE).")
    parser.parse_args(argv)

    try:
        data = collect()
    except reporting_db.ReportingUnavailable as error:
        print("forensics: FAILED -> %s" % error.code)
        return 1
    print(format_report(data))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
