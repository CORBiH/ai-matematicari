"""Faza 3D+ — SAMO ČITANJE: je li tekući razred POTVRĐEN?

    python -m matbot.student_grade_audit

PITANJE SE PROMIJENILO. Ranije je revizija pitala „koji je razred tačan" i po
autoritetu izvora nudila `recommended_grade`. Produkcijska forenzika je pokazala
da je to pitanje pogrešno: augustovski Thinkific uvoz JESTE kurs šestog razreda,
a u njemu legitimno rade i sedmaci koji obnavljaju gradivo. Preporuka po sadržaju
bi ih gurala nazad u šesti razred.

Novo pitanje je: JE LI TEKUĆI RAZRED ADMINISTRATOR POTVRDIO.

  CONFIRMED               potvrđen razred i nema razlike prema korištenom gradivu
  UNCONFIRMED             zatečen profil koji niko nikad nije izričito potvrdio
  CONTENT_GRADE_MISMATCH  potvrđen razred, ali je nedavno gradivo drugog razreda

CONTENT_GRADE_MISMATCH NIJE GREŠKA i ne traži ispravku. Znači „obnavlja ili
priprema gradivo drugog razreda" — pojava koju administrator vidi, a ne kvar
koji se popravlja.

GARANCIJE:
  • ISKLJUČIVO `SELECT`. Nema `UPDATE`, `INSERT`, `DELETE`, DDL-a ni migracije,
    i NEMA `--apply` opcije — razred potvrđuje čovjek kroz administratorsku
    akciju, po jednom učeniku.
  • NEMA PREPORUČENOG RAZREDA. Nijedan sadržajni izvor, i nijedan broj iz imena,
    ne smije predložiti tekući razred.
  • Ne ispisuje e-mail, `external_user_id`, token, komentar s časa ni tekst
    izvještaja. Thinkific je samo „da/ne".
  • Ime se ispisuje da bi ga administrator prepoznao, ali broj u imenu ide u
    ZASEBNU kolonu i ne utiče ni na status ni na ijednu radnju.
"""
import sys

from matbot import reporting_db, student_grades

HEADER = ("id", "display_name", "grade", "potvrda", "izvor", "tk", "tk_month",
          "exam", "exam_date", "matbot", "matbot_date", "hint", "status")

_ROW = "%-4s %-24s %-6s %-19s %-16s %-4s %-9s %-5s %-20s %-6s %-20s %-5s %s"


def _row_values(student, evidence, status, linked):
    thinkific = evidence["thinkific"]
    assessment = evidence["assessment"]
    matbot = evidence["matbot"]
    return {
        "student_id": student["student_id"],
        "display_name": student["display_name"] or "",
        "stored_grade": student["grade"],
        "grade_confirmed_at": student.get("grade_confirmed_at"),
        "grade_source": student.get("grade_source"),
        "thinkific_connected": "yes" if linked else "no",
        # SADRŽAJ, ne tekući razred — kolone su namjerno tako i nazvane.
        "content_thinkific_grade": thinkific["grade"],
        "content_thinkific_month": thinkific["month"],
        "content_assessment_grade": assessment["grade"],
        "content_assessment_date": assessment["when"],
        "content_matbot_grade": matbot["grade"],
        "content_matbot_date": matbot["when"],
        # SAMO za ljudski pregled (vidi docstring modula).
        "name_grade_hint": student_grades.name_grade_hint(student["display_name"]),
        "status": status,
    }


def collect(database=None):
    """Svi učenici sa stanjem potvrde i korištenim gradivom. Ne mijenja ništa."""
    target = database or reporting_db.get_database()
    results = []
    for student in target.list_students():
        student_id = student["student_id"]
        evidence = target.fetch_grade_evidence(student_id)
        status, _ = student_grades.classify(
            student["grade"], student.get("grade_confirmed_at"),
            student.get("grade_source"), evidence)
        results.append(_row_values(student, evidence, status,
                                   student["thinkific_linked"]))
    return results


def _short(value):
    return "-" if value in (None, "") else str(value)


def format_report(rows):
    lines = [_ROW % HEADER, "-" * 160]
    for row in rows:
        lines.append(_ROW % (
            row["student_id"], (row["display_name"] or "")[:24],
            _short(row["stored_grade"]),
            _short(row["grade_confirmed_at"]),
            _short(row["grade_source"]),
            _short(row["content_thinkific_grade"]),
            _short(row["content_thinkific_month"]),
            _short(row["content_assessment_grade"]),
            _short(row["content_assessment_date"]),
            _short(row["content_matbot_grade"]),
            _short(row["content_matbot_date"]),
            _short(row["name_grade_hint"]),
            row["status"]))

    tally = summarize(rows)
    lines.append("")
    lines.append("=== SUMMARY ===")
    lines.append("TOTAL:                 %d" % tally["TOTAL"])
    for status in (student_grades.STATUS_CONFIRMED,
                   student_grades.STATUS_UNCONFIRMED,
                   student_grades.STATUS_CONTENT_MISMATCH):
        lines.append("%-23s %d" % (status + ":", tally[status]))

    # RADNI SPISAK, NE SPISAK GREŠAKA. Ovdje su učenici kojima razred treba
    # POTVRDITI — ne piše koji razred, jer to zna samo čovjek.
    pending = [r for r in rows
               if r["status"] == student_grades.STATUS_UNCONFIRMED]
    lines.append("")
    lines.append("=== NEEDS_CONFIRMATION (%d) ===" % len(pending))
    if pending:
        for row in pending:
            lines.append("  id=%-4s %-24s zatecen_razred=%-5s (potvrda: nema)"
                         % (row["student_id"], (row["display_name"] or "")[:24],
                            _short(row["stored_grade"])))
    else:
        lines.append("  nema")

    # POSEBNO, I BEZ RIJEČI „GREŠKA": razlika u gradivu je kontekst.
    differing = [r for r in rows
                 if r["status"] == student_grades.STATUS_CONTENT_MISMATCH]
    lines.append("")
    lines.append("=== CONTENT_DIFFERENCE_CONTEXT (%d) ===" % len(differing))
    lines.append("  Potvrdjen razred se razlikuje od nedavno koristenog gradiva.")
    lines.append("  To je uobicajeno kod obnavljanja i NE trazi izmjenu profila.")
    if differing:
        for row in differing:
            lines.append("  id=%-4s %-24s razred=%-5s gradivo: tk=%s exam=%s matbot=%s"
                         % (row["student_id"], (row["display_name"] or "")[:24],
                            _short(row["stored_grade"]),
                            _short(row["content_thinkific_grade"]),
                            _short(row["content_assessment_grade"]),
                            _short(row["content_matbot_grade"])))
    else:
        lines.append("  nema")

    lines.append("")
    lines.append("NO_RECOMMENDED_GRADE: revizija ne predlaze razred ni iz jednog izvora.")
    lines.append("READ_ONLY: nijedan podatak nije promijenjen.")
    return "\n".join(lines)


def summarize(rows):
    tally = {"TOTAL": len(rows),
             student_grades.STATUS_CONFIRMED: 0,
             student_grades.STATUS_UNCONFIRMED: 0,
             student_grades.STATUS_CONTENT_MISMATCH: 0}
    for row in rows:
        tally[row["status"]] += 1
    return tally


def main(argv=None):
    """Izlaz 0 kad je revizija obavljena; 1 kad se baza ne može pročitati.

    NEPOSTOJANJE `--apply` je namjerno: ovaj alat prijavljuje, ne popravlja."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m matbot.student_grade_audit",
        description="Revizija potvrde tekuceg razreda (SAMO CITANJE).")
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
