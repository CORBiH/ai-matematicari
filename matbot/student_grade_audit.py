"""Faza 3D — SAMO ČITANJE: revizija tekućeg razreda svih učenika.

    python -m matbot.student_grade_audit

Postoji zbog živog nalaza: `students.grade` je bio zapiši-jednom, a tutorski
padajući meni je imao `6` kao unaprijed izabranu opciju — pa učenik koji ga nikad
nije dodirnuo nosi šesti razred zauvijek. Faza 3D po tom polju bira kurikulum, pa
zastarjela vrijednost daje instruktoru gradivo pogrešnog razreda.

GARANCIJE:
  • ISKLJUČIVO `SELECT`. Nema `UPDATE`, `INSERT`, `DELETE`, DDL-a ni migracije,
    i NEMA `--apply` opcije — ispravku radi čovjek kroz administratorsku akciju.
  • Ne ispisuje e-mail, `external_user_id`, token, komentar s časa ni tekst
    izvještaja. Thinkific je samo „da/ne".
  • Ime se ispisuje da bi ga administrator prepoznao, ali broj u imenu ide u
    ZASEBNU kolonu i ne utiče ni na status ni na preporuku.

PRAVILA SVJEŽINE I NERIJEŠENOG: vidi `matbot/student_grades.py`. Ukratko —
gleda se NAJSVJEŽIJI datirani dokaz po izvoru, a ne skup svih ikad viđenih
razreda (učenik legitimno ima šesti lani i sedmi sada). Dva različita razreda u
istom najsvježijem trenutku su SUKOB, ne izbor.
"""
import sys

from matbot import reporting_db, student_grades

HEADER = ("id", "display_name", "stored", "tk", "tk_month", "exam",
          "exam_date", "matbot", "matbot_date", "hint", "status", "recommended")


def _row_values(student, evidence, status, recommended, linked):
    thinkific = evidence["thinkific"]
    assessment = evidence["assessment"]
    matbot = evidence["matbot"]
    return {
        "student_id": student["student_id"],
        "display_name": student["display_name"] or "",
        "stored_grade": student["grade"],
        "thinkific_connected": "yes" if linked else "no",
        "latest_thinkific_grade": thinkific["grade"],
        "latest_thinkific_month": thinkific["when"],
        "latest_assessment_grade": assessment["grade"],
        "latest_assessment_date": assessment["when"],
        "latest_matbot_grade": matbot["grade"],
        "latest_matbot_date": matbot["when"],
        # SAMO za ljudski pregled (vidi docstring modula).
        "name_grade_hint": student_grades.name_grade_hint(student["display_name"]),
        "status": status,
        "recommended_grade": recommended,
    }


def collect(database=None):
    """Svi učenici s dokazima i klasifikacijom. Ne mijenja ništa."""
    target = database or reporting_db.get_database()
    results = []
    for student in target.list_students():
        student_id = student["student_id"]
        evidence = target.fetch_grade_evidence(student_id)
        status, recommended = student_grades.classify(student["grade"], evidence)
        results.append(_row_values(student, evidence, status, recommended,
                                   student["thinkific_linked"]))
    return results


def _short(value):
    return "-" if value in (None, "") else str(value)


def format_report(rows):
    lines = []
    lines.append("%-4s %-24s %-6s %-4s %-9s %-5s %-20s %-6s %-20s %-5s %-22s %s"
                 % HEADER)
    lines.append("-" * 150)
    for row in rows:
        lines.append(
            "%-4s %-24s %-6s %-4s %-9s %-5s %-20s %-6s %-20s %-5s %-22s %s" % (
                row["student_id"], (row["display_name"] or "")[:24],
                _short(row["stored_grade"]),
                _short(row["latest_thinkific_grade"]),
                _short(row["latest_thinkific_month"]),
                _short(row["latest_assessment_grade"]),
                _short(row["latest_assessment_date"]),
                _short(row["latest_matbot_grade"]),
                _short(row["latest_matbot_date"]),
                _short(row["name_grade_hint"]),
                row["status"], _short(row["recommended_grade"])))

    tally = summarize(rows)
    lines.append("")
    lines.append("=== SUMMARY ===")
    lines.append("TOTAL:                 %d" % tally["TOTAL"])
    for status in (student_grades.STATUS_CONSISTENT,
                   student_grades.STATUS_LIKELY_STALE,
                   student_grades.STATUS_CONFLICTING,
                   student_grades.STATUS_INSUFFICIENT):
        lines.append("%-22s %d" % (status + ":", tally[status]))

    # SAMO LIKELY_STALE: jednoznačan dokaz, pa je pogled brz. Sukobi se
    # NAMJERNO ne stavljaju ovdje — oni traže odluku, ne potvrdu.
    candidates = [r for r in rows
                  if r["status"] == student_grades.STATUS_LIKELY_STALE]
    lines.append("")
    lines.append("=== SAFE_REVIEW_CANDIDATES (%d) ===" % len(candidates))
    if candidates:
        for row in candidates:
            lines.append("  id=%-4s %-24s stored=%-5s -> predlozeno=%-4s (izvor: %s %s)"
                         % (row["student_id"], (row["display_name"] or "")[:24],
                            _short(row["stored_grade"]),
                            _short(row["recommended_grade"]),
                            "thinkific" if row["latest_thinkific_grade"] is not None
                            else "assessment/matbot",
                            _short(row["latest_thinkific_month"])))
    else:
        lines.append("  nema")
    lines.append("")
    lines.append("READ_ONLY: nijedan podatak nije promijenjen.")
    return "\n".join(lines)


def summarize(rows):
    tally = {"TOTAL": len(rows),
             student_grades.STATUS_CONSISTENT: 0,
             student_grades.STATUS_LIKELY_STALE: 0,
             student_grades.STATUS_CONFLICTING: 0,
             student_grades.STATUS_INSUFFICIENT: 0}
    for row in rows:
        tally[row["status"]] += 1
    return tally


def main(argv=None):
    """Izlaz 0 kad je revizija obavljena; 1 kad se baza ne može pročitati.

    NEPOSTOJANJE `--apply` je namjerno: ovaj alat prijavljuje, ne popravlja."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m matbot.student_grade_audit",
        description="Revizija tekuceg razreda ucenika (SAMO CITANJE).")
    parser.parse_args(argv)

    try:
        rows = collect()
    except reporting_db.ReportingUnavailable as error:
        # Strukturni kod, nikad URL ni token.
        print("audit: FAILED -> %s" % error.code)
        return 1
    print(format_report(rows))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
