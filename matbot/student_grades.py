"""Faza 3D — TEKUĆI razred učenika: dokazi, klasifikacija, preporuka.

ŠTA `students.grade` ZNAČI OD OVE FAZE: razred koji učenik POHAĐA SADA. Koristi
se za registar, izbor kurikuluma pri evidenciji časa i kontekst izvještaja. NIJE
identitet, nije „prvi viđeni razred" i nije trajno svojstvo naloga.

ZAŠTO OVAJ MODUL POSTOJI (živi nalaz): `students.grade` je bio zapiši-jednom.
Nijedan put ga nije osvježavao, a tutorski padajući meni je u markupu imao `6`
kao unaprijed izabranu opciju — pa je učenik koji ga nikad nije dodirnuo trajno
dobijao šesti razred. Kad je Faza 3D počela birati kurikulum po tom polju,
instruktor je za sedmaka dobijao gradivo šestog razreda.

DOKAZ NIJE ISPRAVKA. Ovdje se ništa ne mijenja i ništa ne upisuje: modul samo
čita strukturne tragove s datumima i kaže administratoru šta zaslužuje pogled.
Odluku donosi čovjek (`admin_students.update_grade`).

IME UČENIKA NIJE DOKAZ. „Amar 7 Septembar" može biti grupa, generacija ili
popravni — broj u imenu ide isključivo u zasebnu kolonu za ljudski pregled i
NIKAD ne ulazi ni u `status` ni u `recommended_grade`.
"""
import re

VALID_GRADES = (6, 7, 8, 9)

STATUS_CONSISTENT = "CONSISTENT"
STATUS_LIKELY_STALE = "LIKELY_STALE"
STATUS_CONFLICTING = "CONFLICTING_EVIDENCE"
STATUS_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

SOURCE_THINKIFIC = "thinkific_progress"
SOURCE_ASSESSMENT = "assessment"
SOURCE_MATBOT = "matbot_activity"

# Broj u imenu — SAMO za ljudski pregled. Jedan jedini broj 6–9 u imenu daje
# nagovještaj; dva ili više različitih ne daju ništa (npr. „7/8 grupa").
_NAME_GRADE_RE = re.compile(r"(?<!\d)([6-9])(?!\d)")


def name_grade_hint(display_name):
    """Nagovještaj iz imena. NIKAD ne ulazi u klasifikaciju."""
    found = {int(match) for match in _NAME_GRADE_RE.findall(display_name or "")}
    return found.pop() if len(found) == 1 else None


def _pick_latest(rows):
    """[(vrijeme, razred)] → dokaz o NAJSVJEŽIJEM trenutku.

    Vraća `(grade, when, ambiguous)`. `ambiguous` je True kad u ISTOM najsvježijem
    trenutku stoje DVA različita razreda — tada se ne bira nasumično nego se
    prijavljuje sukob. Prazan ulaz daje `(None, None, False)`.

    ZAŠTO NAJSVJEŽIJI, A NE SVI: učenik legitimno ima šesti razred lani i sedmi
    sada. Skup „svih ikad viđenih razreda" bi svakog naprednog učenika prijavio
    kao sukob."""
    usable = [(when, int(grade)) for when, grade in (rows or [])
              if when and grade is not None]
    if not usable:
        return None, None, False
    newest = max(when for when, _ in usable)
    grades = {grade for when, grade in usable if when == newest}
    if len(grades) > 1:
        return None, newest, True
    return grades.pop(), newest, False


def evidence_from_rows(thinkific_rows=None, assessment_rows=None,
                       matbot_rows=None):
    """Sirovi redovi → uredan dokazni objekat. Bez baze, pa je testabilno."""
    tk_grade, tk_when, tk_ambiguous = _pick_latest(thinkific_rows)
    as_grade, as_when, as_ambiguous = _pick_latest(assessment_rows)
    mb_grade, mb_when, mb_ambiguous = _pick_latest(matbot_rows)
    return {
        "thinkific": {"grade": tk_grade, "when": tk_when,
                      "ambiguous": tk_ambiguous, "source": SOURCE_THINKIFIC},
        "assessment": {"grade": as_grade, "when": as_when,
                       "ambiguous": as_ambiguous, "source": SOURCE_ASSESSMENT},
        "matbot": {"grade": mb_grade, "when": mb_when,
                   "ambiguous": mb_ambiguous, "source": SOURCE_MATBOT},
    }


def strongest_evidence(evidence):
    """Najjači JEDNOZNAČAN dokaz, po redu autoriteta.

    1. Thinkific napredak — kurs u koji je učenik STVARNO upisan;
    2. kontrolni — radio je gradivo tog razreda;
    3. MAT-BOT aktivnost — najslabije, jer razred bira sam učenik iz menija
       (isti izvor koji je i napravio kvar).

    Dvosmislen izvor se PRESKAČE kao izvor preporuke, ali se pamti da bi
    klasifikacija mogla prijaviti sukob."""
    for key in ("thinkific", "assessment", "matbot"):
        item = evidence.get(key) or {}
        if item.get("grade") is not None and not item.get("ambiguous"):
            return item
    return None


def classify(stored_grade, evidence):
    """(status, recommended_grade). Isključivo iz strukturnih dokaza.

    PRAVILA, izričito:
      • nema nijednog upotrebljivog dokaza            → INSUFFICIENT_EVIDENCE
      • bilo koji izvor dvosmislen u svom najsvježijem trenutku → CONFLICTING
      • dva izvora jednake svježine tvrde različito   → CONFLICTING
      • najjači dokaz == sačuvani razred              → CONSISTENT
      • najjači dokaz != sačuvani (ili sačuvani NULL) → LIKELY_STALE
    Preporuka se daje SAMO uz LIKELY_STALE; sukob traži čovjeka.

    ŠKOLSKA GODINA SE NE POGAĐA. Modul ne zna kad počinje septembar niti koja je
    generacija — zato se oslanja na najsvježiji datum, a ne na kalendar."""
    if any((evidence.get(key) or {}).get("ambiguous")
           for key in ("thinkific", "assessment", "matbot")):
        return STATUS_CONFLICTING, None

    strongest = strongest_evidence(evidence)
    if strongest is None:
        return STATUS_INSUFFICIENT, None

    # Sukob jednako svježih izvora: isti dan/mjesec, različit razred.
    same_moment = [item for item in evidence.values()
                   if item.get("grade") is not None
                   and item.get("when") == strongest.get("when")]
    if len({item["grade"] for item in same_moment}) > 1:
        return STATUS_CONFLICTING, None

    recommended = strongest["grade"]
    if stored_grade is not None and int(stored_grade) == recommended:
        return STATUS_CONSISTENT, recommended
    return STATUS_LIKELY_STALE, recommended


def needs_review(stored_grade, evidence):
    """Kratka zastavica za administratorski prikaz (registar i profil)."""
    status, _ = classify(stored_grade, evidence)
    return status in (STATUS_LIKELY_STALE, STATUS_CONFLICTING)
