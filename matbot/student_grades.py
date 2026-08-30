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


def evidence_month(when):
    """Bilo koji vremenski trag → kanonski `YYYY-MM`, ili `None`.

    ZAŠTO POSTOJI (živi kvar u produkciji): Thinkific pamti `report_month`
    („2026-08"), a kontrolni i aktivnost pune vremenske žigove
    („2026-08-25 18:06:49"). Prethodna verzija je sukob između izvora tražila
    poređenjem tih SIROVIH nizova na jednakost — a oni se nikad ne mogu
    izjednačiti. Grana za sukob zato nije mogla da se okine NIJEDNOM, pa je
    učenik s Thinkific 6 i kontrolnim 9 u istom mjesecu prijavljivan kao
    CONSISTENT. Revizija je vratila 34/34 CONSISTENT i bila bezvrijedna.

    Mjesec je najgrublja zajednička rezolucija koju SVI izvori stvarno imaju —
    finija ne postoji, jer Thinkific dan uopšte ne bilježi."""
    text = str(when or "").strip()
    if (len(text) >= 7 and text[4] == "-"
            and text[:4].isdigit() and text[5:7].isdigit()):
        return text[:7]
    return None


def _pick_latest(rows):
    """[(vrijeme, razred)] → dokaz o NAJSVJEŽIJEM MJESECU jednog izvora.

    Vraća `(grade, when, month, ambiguous)`. `ambiguous` je True kad u istom
    najsvježijem MJESECU isti izvor nosi dva različita razreda — tada se ne
    bira nasumično nego se prijavljuje sukob.

    ZAŠTO NAJSVJEŽIJI, A NE SVI: učenik legitimno ima šesti razred lani i sedmi
    sada. Skup „svih ikad viđenih razreda" bi svakog naprednog učenika prijavio
    kao sukob."""
    usable = []
    for when, grade in (rows or []):
        month = evidence_month(when)
        if month and grade is not None:
            usable.append((month, when, int(grade)))
    if not usable:
        return None, None, None, False
    newest_month = max(month for month, _, _ in usable)
    in_month = [(when, grade) for month, when, grade in usable
                if month == newest_month]
    grades = {grade for _, grade in in_month}
    newest_when = max(when for when, _ in in_month)
    if len(grades) > 1:
        return None, newest_when, newest_month, True
    return grades.pop(), newest_when, newest_month, False


def evidence_from_rows(thinkific_rows=None, assessment_rows=None,
                       matbot_rows=None):
    """Sirovi redovi → uredan dokazni objekat. Bez baze, pa je testabilno."""
    tk_grade, tk_when, tk_month, tk_ambiguous = _pick_latest(thinkific_rows)
    as_grade, as_when, as_month, as_ambiguous = _pick_latest(assessment_rows)
    mb_grade, mb_when, mb_month, mb_ambiguous = _pick_latest(matbot_rows)
    # `when` ostaje IZVORNI trag (za prikaz operatoru), a `month` je jedina
    # vrijednost po kojoj se različiti izvori smiju porediti.
    return {
        "thinkific": {"grade": tk_grade, "when": tk_when, "month": tk_month,
                      "ambiguous": tk_ambiguous, "source": SOURCE_THINKIFIC},
        "assessment": {"grade": as_grade, "when": as_when, "month": as_month,
                       "ambiguous": as_ambiguous, "source": SOURCE_ASSESSMENT},
        "matbot": {"grade": mb_grade, "when": mb_when, "month": mb_month,
                   "ambiguous": mb_ambiguous, "source": SOURCE_MATBOT},
    }


def _by_authority(evidence):
    """Upotrebljivi dokazi po redu autoriteta. Dvosmisleni se preskaču."""
    ordered = []
    for key in ("thinkific", "assessment", "matbot"):
        item = evidence.get(key) or {}
        if item.get("grade") is not None and not item.get("ambiguous"):
            ordered.append(item)
    return ordered


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
    generacija — zato se oslanja na najsvježiji MJESEC, a ne na kalendar."""
    if any((evidence.get(key) or {}).get("ambiguous")
           for key in ("thinkific", "assessment", "matbot")):
        return STATUS_CONFLICTING, None

    strongest = strongest_evidence(evidence)
    if strongest is None:
        return STATUS_INSUFFICIENT, None

    # SUKOB SE MJERI PO MJESECU, NE PO SIROVOM ŽIGU. Ranije se poredilo
    # `when == when`, a Thinkific nosi „2026-08" dok kontrolni nosi
    # „2026-08-25 18:06:49" — jednaki nikad, pa se grana nije okidala nijednom.
    # Zato se gleda NAJSVJEŽIJI MJESEC preko SVIH izvora i svi dokazi U NJEMU.
    current_month = max(item["month"] for item in evidence.values()
                        if item.get("month"))
    in_current_month = [item for item in evidence.values()
                        if item.get("grade") is not None
                        and item.get("month") == current_month]

    # AUTORITET NE BRIŠE NESLAGANJE. Slabiji izvor ne smije sam preporučiti
    # razred, ali SMIJE oboriti tvrdnju da je sve u redu: dva različita razreda
    # u istom tekućem mjesecu traže čovjeka, ne automatski izbor jačeg.
    if len({item["grade"] for item in in_current_month}) > 1:
        return STATUS_CONFLICTING, None

    # Preporuka dolazi od NAJJAČEG izvora, ali samo iz tekućeg mjeseca; stariji
    # dokaz ne smije ni preporučiti ni oboriti tekuće stanje (Dio 4).
    current = [item for item in _by_authority(evidence)
               if item in in_current_month]
    recommended = (current[0]["grade"] if current else strongest["grade"])

    if stored_grade is not None and int(stored_grade) == recommended:
        return STATUS_CONSISTENT, recommended
    return STATUS_LIKELY_STALE, recommended


def needs_review(stored_grade, evidence):
    """Kratka zastavica za administratorski prikaz (registar i profil)."""
    status, _ = classify(stored_grade, evidence)
    return status in (STATUS_LIKELY_STALE, STATUS_CONFLICTING)
