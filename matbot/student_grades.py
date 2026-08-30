"""Faza 3D+ — TEKUĆI školski razred: potvrda, dokazi o sadržaju, revizija.

DVIJE RAZLIČITE STVARI KOJE SU SE DO SADA ZVALE ISTO:

  TEKUĆI ŠKOLSKI RAZRED  = razred koji učenik POHAĐA. Živi u `students.grade` i
                           vrijedi SAMO ako ga je administrator POTVRDIO.
  RAZRED SADRŽAJA        = razred gradiva koje je učenik KORISTIO. Živi u
                           `thinkific_progress_snapshots.grade`,
                           `assessment_attempts.grade` i `learning_activity.grade`.

ZAŠTO SU RAZDVOJENI (produkcijska forenzika, 2026-08-29): augustovski Thinkific
uvoz je DOKAZANO izvoz kursa šestog razreda — sekcije iz samog fajla su SKUPOVI,
DJELJIVOST BROJEVA, RAZLOMCI, DECIMALNI BROJEVI. U njemu potpuno legitimno
učestvuju učenici koje čovjek prepoznaje kao sedmi, osmi i deveti razred.
Sedmak koji obnavlja gradivo šestog razreda je NORMALAN slučaj, ne kvar.

POSLJEDICA, IZRIČITO: nijedan sadržajni trag ne smije ni upisati ni PREDLOŽITI
tekući razred. Ovaj modul zato više NE proizvodi `recommended_grade`. Prethodna
verzija ga je proizvodila po „autoritetu izvora" (Thinkific > kontrolni >
MAT-BOT) i time bi sedmaka koji obnavlja šesti razred gurala nazad u šesti.

DOKAZ NIJE ISPRAVKA. Ovdje se ništa ne mijenja i ništa ne upisuje: modul čita
strukturne tragove s datumima i kaže administratoru šta zaslužuje pogled. Odluku
donosi čovjek (`admin_students.update_grade` / `confirm_grade`).

IME UČENIKA NIJE DOKAZ. „Amar 7 Septembar" može biti grupa, generacija ili
popravni — broj u imenu ide isključivo u zasebnu kolonu za ljudski pregled i
NIKAD ne ulazi ni u status ni u ijednu preporuku.
"""
import re

VALID_GRADES = (6, 7, 8, 9)

# --- KO SMIJE POTVRDITI RAZRED ---------------------------------------------
# Zatvoren skup, i namjerno kratak. Obje vrijednosti znače „čovjek je odlučio":
# `admin` je izmjena ili potvrda na profilu, `manual_creation` je ručni upis
# učenika u kojem je razred obavezno polje formulara. Sadržajni izvori NISU na
# ovom spisku i neće biti — to je cijela poenta verzije 4.
#
# Ograničenje se drži u Pythonu, ne u bazi: SQLite ne može dodati `CHECK` na
# postojeću tabelu bez prepisivanja cijele tabele, a prepisivanje `students` u
# produkciji nije prihvatljiv rizik za dvije opcione kolone.
GRADE_SOURCE_ADMIN = "admin"
GRADE_SOURCE_MANUAL_CREATION = "manual_creation"
VALID_GRADE_SOURCES = (GRADE_SOURCE_ADMIN, GRADE_SOURCE_MANUAL_CREATION)

# --- STATUSI REVIZIJE -------------------------------------------------------
# Pitanje revizije više NIJE „koji je razred tačan" nego „je li ga čovjek
# potvrdio". Zato su i statusi drugačiji nego u prethodnoj verziji.
STATUS_CONFIRMED = "CONFIRMED"
STATUS_UNCONFIRMED = "UNCONFIRMED"
STATUS_CONTENT_MISMATCH = "CONTENT_GRADE_MISMATCH"

# CONTENT_GRADE_MISMATCH NIJE GREŠKA. Znači samo: potvrđeni tekući razred se
# razlikuje od razreda gradiva koje je učenik nedavno koristio. To je najčešće
# obnavljanje ili priprema, i profil se zbog toga NE dira. Riječi „zastarjelo",
# „netačno" i „popravi" se ovdje svjesno ne koriste.

SOURCE_THINKIFIC = "thinkific_progress"
SOURCE_ASSESSMENT = "assessment"
SOURCE_MATBOT = "matbot_activity"

EVIDENCE_KEYS = ("thinkific", "assessment", "matbot")

# Broj u imenu — SAMO za ljudski pregled. Jedan jedini broj 6–9 u imenu daje
# nagovještaj; dva ili više različitih ne daju ništa (npr. „7/8 grupa").
_NAME_GRADE_RE = re.compile(r"(?<!\d)([6-9])(?!\d)")


def name_grade_hint(display_name):
    """Nagovještaj iz imena. NIKAD ne ulazi u status ni u ijednu preporuku."""
    found = {int(match) for match in _NAME_GRADE_RE.findall(display_name or "")}
    return found.pop() if len(found) == 1 else None


def evidence_month(when):
    """Bilo koji vremenski trag → kanonski `YYYY-MM`, ili `None`.

    ZAŠTO POSTOJI (živi kvar u produkciji): Thinkific pamti `report_month`
    („2026-08"), a kontrolni i aktivnost pune vremenske žigove
    („2026-08-25 18:06:49"). Prethodna verzija je razliku između izvora tražila
    poređenjem tih SIROVIH nizova na jednakost — a oni se nikad ne mogu
    izjednačiti, pa se grana nije okinula NIJEDNOM.

    Mjesec je najgrublja zajednička rezolucija koju SVI izvori stvarno imaju —
    finija ne postoji, jer Thinkific dan uopšte ne bilježi."""
    text = str(when or "").strip()
    if (len(text) >= 7 and text[4] == "-"
            and text[:4].isdigit() and text[5:7].isdigit()):
        return text[:7]
    return None


def _pick_latest(rows):
    """[(vrijeme, razred)] → sadržaj NAJSVJEŽIJEG MJESECA jednog izvora.

    Vraća `(grade, when, month, ambiguous)`. `ambiguous` je True kad u istom
    najsvježijem MJESECU isti izvor nosi dva različita razreda — učenik je tada
    u istom mjesecu radio gradivo dva razreda, što je i dalje samo KONTEKST.

    ZAŠTO NAJSVJEŽIJI, A NE SVI: skup „svih ikad korištenih razreda" bi za
    svakog učenika s istorijom bio šum, a pitanje je šta se koristi SADA."""
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


def is_confirmed(grade, grade_confirmed_at, grade_source):
    """Je li tekući razred SVJESNO potvrđen? Sva tri uslova, bez izuzetka.

    POSTOJANJE VRIJEDNOSTI NIJE POTVRDA. Zatečenih 34 učenika ima
    `students.grade = 6` koji je upisala stara automatika (Thinkific uvoz i
    unaprijed izabrana opcija u padajućem meniju tutora). Bez ove funkcije bi
    svi izgledali potvrđeno, a nijedan to nije."""
    return (grade in VALID_GRADES
            and bool(str(grade_confirmed_at or "").strip())
            and (grade_source or "") in VALID_GRADE_SOURCES)


def content_grades(evidence):
    """{izvor: dokaz} — SADRŽAJ koji je učenik koristio, ne tekući razred.

    Kontekst za administratora („u augustu je koristio gradivo 6. razreda"),
    nikad ulaz u odluku. Izvori bez upotrebljivog traga se izostavljaju."""
    found = {}
    for key in EVIDENCE_KEYS:
        item = evidence.get(key) or {}
        if item.get("month") and (item.get("grade") is not None
                                  or item.get("ambiguous")):
            found[key] = item
    return found


def recent_content_grades(evidence):
    """Sadržaj korišten u NAJSVJEŽIJEM mjesecu preko svih izvora.

    Poređenje ide po MJESECU, nikad po sirovom žigu: Thinkific nosi „2026-08", a
    kontrolni „2026-08-25 18:06:49" — jednaki ne mogu biti nikad."""
    usable = content_grades(evidence)
    if not usable:
        return {}
    newest = max(item["month"] for item in usable.values())
    return {key: item for key, item in usable.items()
            if item["month"] == newest}


def classify(grade, grade_confirmed_at, grade_source, evidence):
    """(status, sadržaj_iz_najsvježijeg_mjeseca). BEZ IJEDNE PREPORUKE.

    PRAVILA, izričito:
      • razred nije potvrđen (ili ga nema)            → UNCONFIRMED
      • potvrđen, a nedavni sadržaj je drugog razreda → CONTENT_GRADE_MISMATCH
      • potvrđen i bez razlike                        → CONFIRMED

    ŠTA OVA FUNKCIJA NAMJERNO NE RADI: ne vraća predloženi razred, ne rangira
    izvore po autoritetu i ne gleda ime učenika. Sadržaj se PRIKAZUJE, a razred
    mijenja isključivo čovjek.

    CONTENT_GRADE_MISMATCH nije kvar nego zapažanje: sedmak koji obnavlja
    gradivo šestog razreda dobija upravo ovaj status, a njegov profil ostaje
    netaknut."""
    recent = recent_content_grades(evidence)
    if not is_confirmed(grade, grade_confirmed_at, grade_source):
        return STATUS_UNCONFIRMED, recent
    differing = [item for item in recent.values()
                 if item.get("ambiguous") or int(item["grade"]) != int(grade)]
    if differing:
        return STATUS_CONTENT_MISMATCH, recent
    return STATUS_CONFIRMED, recent


def needs_confirmation(grade, grade_confirmed_at, grade_source):
    """Kratka zastavica za administratorski prikaz i za blokade unosa."""
    return not is_confirmed(grade, grade_confirmed_at, grade_source)
