"""SINTETIČKI Thinkific izvozi, oblikovani po STRUKTURI stvarnog uzorka.

Nijedno ime, adresa ni vrijeme ovdje ne pripada stvarnom učeniku. Stvarni izvoz
(`scratchpad/thinkific/progress_grade6_sample.csv`) sadrži PII, gitignorisan je i
NIKAD se ne kopira u testove — iz njega je preuzeta samo STRUKTURA, izmjerena:

  • UTF-8 bez BOM-a, goli LF, zarez kao razdvajač
  • naziv sekcije koji SADRŽI zarez i mora biti u navodnicima
    (u stvarnom izvozu: „KRUŽNICA, KRUG, UGAO")
  • procenti kao GOLI CIJELI BROJEVI, bez znaka `%`
  • vremena kao `YYYY-MM-DD HH:MM:SS UTC`, prazno = prazan string
  • administrativna kolona `Company`
  • kolona `Matematički bot-MAT BOT` na kraju
"""

FIXED_HEADER = ["First Name", "Last Name", "Email", "Company", "Completed At",
                "Started At", "Activated At", "Expires At", "Last Sign In",
                "% Viewed", "% Completed"]

# Isti oblik kao stvarni izvoz 6. razreda, uključujući naziv sa zarezom.
GRADE6_SECTIONS = ["SKUPOVI", "KRUŽNICA, KRUG, UGAO", "N i No SKUPOVI",
                   "DJELJIVOST BROJEVA", "RAZLOMCI"]

MATBOT_COLUMN = "Matematički bot-MAT BOT"


def _quote(value):
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", '"', "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text


def build_csv(rows, sections=None, *, include_company=True,
              include_matbot_column=True, header_extra=None):
    """Sastavi izvoz. `rows` su rječnici; nedostajuće vrijednosti su prazne.

    Vraća BAJTOVE, jer uvoz radi nad bajtovima (hash se računa nad njima)."""
    sections = GRADE6_SECTIONS if sections is None else sections
    header = list(FIXED_HEADER)
    if not include_company:
        header.remove("Company")
    header += list(sections)
    if include_matbot_column:
        header.append(MATBOT_COLUMN)
    if header_extra:
        header += list(header_extra)

    lines = [",".join(_quote(name) for name in header)]
    for row in rows:
        cells = [_quote(row.get(name, "")) for name in header]
        lines.append(",".join(cells))
    return ("\n".join(lines) + "\n").encode("utf-8")


def learner(email, *, first="Ana", last="Anić", viewed=40, completed=30,
            started="2026-08-01 09:00:00 UTC", last_sign_in="2026-09-10 18:22:11 UTC",
            completed_at="", activated="2026-07-15 08:00:00 UTC", expires="",
            company="", matbot=55, **sections):
    """Jedan sintetički red. `sections` prima nazive sekcija kao kwargs alias."""
    row = {"First Name": first, "Last Name": last, "Email": email,
           "Company": company, "Completed At": completed_at, "Started At": started,
           "Activated At": activated, "Expires At": expires,
           "Last Sign In": last_sign_in, "% Viewed": viewed,
           "% Completed": completed, MATBOT_COLUMN: matbot}
    row.update(sections)
    return row


def with_sections(row, values):
    """Dodaj vrijednosti sekcija po nazivu: `{"SKUPOVI": 80, ...}`."""
    row = dict(row)
    row.update(values)
    return row
