"""Determinističke „porodice zadataka“ (task families) za Practice mod.

PROBLEM koji rješava: model je, tražen više puta zaredom, davao pedagoški ISTI
zadatak s drugim brojevima („Proširi $\\frac{3}{8}$ na nazivnik 24.“ →
„Proširi $\\frac{5}{7}$ na nazivnik 28.“ → ...). Brojevi se razlikuju, vještina
se ne mijenja.

RJEŠENJE: porodica opisuje PEDAGOŠKU OPERACIJU, ne brojeve. SERVER (ne model)
bira porodicu prije jedinog AI poziva u turnu i šalje je u prompt kao obavezu.
Model ne smije preimenovati dodijeljenu porodicu.

Katalog NIJE po lekciji (534 lekcije) nego po prepoznatom domenu — routing se
oslanja na isti pouzdan (oblast, lesson_title) izvor kao rules.py, plus
geometrijski router. Lekcija dobije samo porodice koje za nju imaju smisla.
"""
import json
import re
from pathlib import Path

from matbot import geometry_rules
from matbot.legacy import practice_routing as legacy_routing
from matbot.rules import route_topic_rules

# Koliko zadnjih porodica pamtimo po sesiji (za LRU izbor i prompt kontekst).
MAX_RECENT_FAMILIES = 6

# Deklarativna tabela izuzetaka routinga (podaci, ne grana u kodu).
_OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "data" / "routing_overrides.json"
_OVERRIDES_CACHE = None


# ---------------------------------------------------------------------------
# KATALOG — id → kratak opis koji ide u prompt (bosanski, jer ga čita model
# zajedno s ostatkom prompta).
# ---------------------------------------------------------------------------

FAMILY_DESCRIPTIONS = {
    # --- razlomci ---
    "expand_to_given_denominator": "proširi razlomak na zadani nazivnik",
    "find_missing_numerator": "pronađi nedostajući brojnik ili nazivnik u jednakosti razlomaka",
    "find_expansion_factor": "odredi kojim brojem je razlomak proširen ili skraćen",
    "recognize_equivalent_fraction": "prepoznaj koji je razlomak jednak zadanom",
    "compare_fractions": "uporedi ili poređaj razlomke po veličini",
    "fraction_operation": "izvedi računsku operaciju s razlomcima",
    # Pet porodica ispod opslužuju SAMO legacy put (vidi matbot/legacy/).
    # `fraction_expression` još ima nemigriranog potrošača; ostale četiri
    # zadržane su da bi zatečeno routiranje ostalo doslovno isto. Koja lekcija
    # koristi koju — vidi matbot/legacy/practice_routing.py i kapiju parnosti
    # tests/test_legacy_routing_parity.py.
    "fraction_add_subtract_equal": "saberi ili oduzmi razlomke jednakih nazivnika",
    "fraction_add_subtract_unlike": "saberi ili oduzmi razlomke različitih nazivnika",
    "fraction_multiplication": "pomnoži razlomke",
    "fraction_division": "podijeli razlomke",
    "fraction_expression": "izračunaj brojevni izraz s više operacija nad razlomcima",
    "fraction_word_problem": "tekstualni (životni) zadatak s razlomcima",

    # --- sistemi jednačina ---
    "solve_system": "riješi sistem jednačina zadanom ili prikladnom metodom",
    "verify_ordered_pair": "provjeri da li je dati uređeni par rješenje sistema",
    "choose_method": "izaberi najprikladniju metodu rješavanja i obrazloži izbor",
    "determine_number_of_solutions": "odredi koliko rješenja sistem ima",
    "identify_equivalent_system": "prepoznaj koji je sistem ekvivalentan zadanom",
    "system_word_problem": "tekstualni zadatak koji se modelira sistemom",

    # --- jednačine i nejednačine ---
    "solve_equation": "riješi jednačinu",
    "verify_solution": "provjeri da li je dati broj rješenje jednačine/nejednačine",
    "identify_next_step": "prepoznaj koji je sljedeći ispravan korak u postupku",
    "translate_to_equation": "prevedi rečenicu u jednačinu ili nejednačinu",

    # --- geometrija ---
    "direct_formula_application": "direktna primjena formule na zadate podatke",
    "find_missing_dimension": "iz poznatog rezultata izračunaj nepoznatu dimenziju",
    "inverse_formula_problem": "obrnuti zadatak — iz površine/zapremine nazad do ivice ili poluprečnika",
    "choose_correct_formula": "izaberi ispravnu formulu za zadanu figuru ili tijelo",
    "unit_conversion": "pretvaranje mjernih jedinica uz geometrijsku veličinu",
    "compare_figures": "uporedi dvije figure ili tijela po obimu, površini ili zapremini",
    "detect_formula_error": "pronađi grešku u ponuđenom postupku ili formuli",
    "practical_geometry_problem": "praktičan (životni) geometrijski zadatak",

    # --- opšte (važe za skoro svaku lekciju kad domen nije prepoznat) ---
    "direct_computation": "direktan račun po pravilu iz lekcije",
    "find_missing_value": "pronađi nedostajuću vrijednost u zadanoj vezi",
    "recognize_correct_statement": "prepoznaj koja je tvrdnja o pojmu iz lekcije tačna",
    "detect_student_error": "pronađi grešku u tuđem riješenom postupku",
    "compare_or_order": "uporedi ili poređaj zadane vrijednosti",
    "word_problem": "tekstualni (životni) zadatak iz ove lekcije",
}


# Skupovi porodica po domenu, redoslijedom kojim ih je pedagoški prirodno
# obrađivati (prvi ciklus ide ovim redom kad ništa još nije završeno).
_FRACTION_FAMILIES = [
    "expand_to_given_denominator",
    "find_expansion_factor",
    "find_missing_numerator",
    "recognize_equivalent_fraction",
    "fraction_operation",
    "compare_fractions",
    "detect_student_error",
    "fraction_word_problem",
]

_SYSTEM_FAMILIES = [
    "solve_system",
    "verify_ordered_pair",
    "choose_method",
    "determine_number_of_solutions",
    "identify_equivalent_system",
    "detect_student_error",
    "system_word_problem",
]

_EQUATION_FAMILIES = [
    "solve_equation",
    "verify_solution",
    "identify_next_step",
    "detect_student_error",
    "translate_to_equation",
    "word_problem",
]

_GEOMETRY_FAMILIES = [
    "direct_formula_application",
    "choose_correct_formula",
    "find_missing_dimension",
    "inverse_formula_problem",
    "detect_formula_error",
    "compare_figures",
    "unit_conversion",
    "practical_geometry_problem",
]

_GENERAL_FAMILIES = [
    "direct_computation",
    "find_missing_value",
    "recognize_correct_statement",
    "detect_student_error",
    "compare_or_order",
    "word_problem",
]

# Konstrukcijske lekcije: „izračunaj“ porodice nemaju smisla — zadatak je uvijek
# o ispravnom koraku/postupku (i ostaje multiple-choice, vidi rules.py).
_CONSTRUCTION_FAMILIES = [
    "identify_next_step",
    "choose_correct_formula",
    "recognize_correct_statement",
    "detect_student_error",
]

_CONSTRUCTION_RE = re.compile(r"konstruk", re.IGNORECASE)


def applicable_families(grade, oblast, lesson_title, lesson_id=""):
    """Deterministički vrati listu porodica primjenjivih na ovu lekciju.

    Routing se oslanja na iste pouzdane server-side izvore kao ostatak sistema:
    route_topic_rules() (oblast/lekcija → topic-rule ID-jevi) i geometrijski
    router. Nikad ne gleda učenikovu poruku.

    OVO JE LEGACY PUT. Koristi se SAMO za lekcije koje nemaju uključen ugovor
    (matbot/contracts/). Ponašanje je NEPROMIJENJENO u odnosu na stanje prije
    uvođenja motora — uključujući ručno određen redoslijed porodica za lekcije
    razlomaka 6. razreda, koji sada živi u izolovanoj granici
    `matbot/legacy/practice_routing.py`. Nova lekcija se NE dodaje tamo nego
    ugovorom (vidi docs/LESSON_CONTRACTS.md).
    """
    haystack = f"{oblast or ''} {lesson_title or ''}"

    if _CONSTRUCTION_RE.search(haystack):
        return _apply_routing_override(list(_CONSTRUCTION_FAMILIES), lesson_id)

    topic_ids = route_topic_rules(oblast, lesson_title)
    geometry_scope, _ = geometry_rules.route_geometry_topic(oblast, lesson_title)

    if "sistemi" in topic_ids:
        families = _promote_declared_task_form(list(_SYSTEM_FAMILIES), lesson_title)
    elif "razlomci" in topic_ids:
        legacy_families = legacy_routing.grade6_fraction_families(grade, lesson_id)
        if legacy_families is None:
            legacy_families = list(_FRACTION_FAMILIES)
        families = _promote_declared_task_form(legacy_families, lesson_title)
    elif geometry_scope:
        families = _promote_declared_task_form(list(_GEOMETRY_FAMILIES), lesson_title)
    elif "jednacine" in topic_ids or "nejednacine" in topic_ids:
        families = _promote_declared_task_form(list(_EQUATION_FAMILIES), lesson_title)
    else:
        families = _promote_declared_task_form(list(_GENERAL_FAMILIES), lesson_title)

    # Tabela izuzetaka ide POSLJEDNJA: generičko pravilo iz naslova je dobra
    # pretpostavka, ali za dvije dokazano pogrešne lekcije podaci imaju zadnju
    # riječ (vidi data/routing_overrides.json).
    return _apply_routing_override(families, lesson_id)


# ---------------------------------------------------------------------------
# KAD NASLOV LEKCIJE SAM IMENUJE OBLIK ZADATKA
# ---------------------------------------------------------------------------
# ISPRAVKA MAPIRANJA (živi nalaz, 5 prijavljenih slučajeva): routing bira
# porodice po OBLASTI, pa je lekcija „Upoređivanje decimalnih brojeva“ dobijala
# `fraction_operation` (izvedi računsku operaciju) kao PRVU porodicu, a
# „Tekstualni zadatak sa sistemom“ je dobijao `solve_system` umjesto zadatka s
# pričom. Naslov lekcije je pri tome doslovno imenovao traženi oblik.
#
# Ovo NIJE grananje po lekciji i NE uvodi nijednu novu porodicu: samo podiže
# porodicu koja je VEĆ u listi te oblasti na prvo mjesto kad naslov eksplicitno
# imenuje oblik. Lekcija bez takve porodice ostaje nepromijenjena.
#
# Namjerno usko: pokriva samo dva oblika koje naslovi u kurikulumu stvarno
# imenuju (upoređivanje/uređivanje i tekstualni zadatak). Sve ostalo je posao
# recenzenta vjernosti lekciji, ne routinga.
_COMPARISON_TITLE_RE = re.compile(
    r"upoređivanj|upoređiv|uporedi|poređenj|uređenost|uređenje", re.IGNORECASE
)
_WORD_PROBLEM_TITLE_RE = re.compile(r"tekstualn", re.IGNORECASE)

_WORD_PROBLEM_FAMILIES_BY_PRIORITY = (
    "system_word_problem", "fraction_word_problem", "word_problem",
)

# Koja je porodica poređenja ISPRAVNA zavisi od ZAPISA koji lekcija poredi:
# `compare_fractions` ima validator specifičan za razlomke i odbija poređenje
# decimalnih brojeva („Koji je broj veći: $0,7$ ili $0,68$?“), dok je
# `compare_or_order` reprezentacijski neutralan. Zato lekcija o poređenju
# RAZLOMAKA dobija prvu, a svaka druga lekcija o poređenju drugu.
_FRACTION_TITLE_RE = re.compile(r"razlom", re.IGNORECASE)


def _comparison_family_for(lesson_title):
    return "compare_fractions" if _FRACTION_TITLE_RE.search(lesson_title or "") \
        else "compare_or_order"


def _routing_overrides():
    """{topic_id: primary_family} iz `data/routing_overrides.json` (keširano).

    Podaci, ne grana: ovaj modul ne zna nijedan ID lekcije — samo čita tabelu.
    Vidi docstring u samom JSON-u za pravila i obrazloženja."""
    global _OVERRIDES_CACHE
    if _OVERRIDES_CACHE is None:
        try:
            with _OVERRIDES_PATH.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            # Nedostupna/neispravna tabela ne smije oboriti Practice — bez
            # override-a važi generičko pravilo, tačno kao i ranije.
            _OVERRIDES_CACHE = {}
        else:
            _OVERRIDES_CACHE = {
                str(row["canonical_topic_id"]): str(row["primary_family"])
                for row in payload.get("overrides", [])
                if row.get("canonical_topic_id") and row.get("primary_family")
            }
    return _OVERRIDES_CACHE


def _apply_routing_override(families, lesson_id):
    """Podigni porodicu koju tabela izuzetaka propisuje za OVU lekciju.

    Override koji imenuje nepoznatu porodicu se IGNORIŠE (generičko pravilo
    ostaje) — pogrešan red u podacima ne smije proizvesti porodicu koju katalog
    ne poznaje."""
    family = _routing_overrides().get(lesson_id or "")
    if not family or family not in FAMILY_DESCRIPTIONS:
        return families
    remaining = [item for item in families if item != family]
    return [family] + remaining


def _promote_declared_task_form(families, lesson_title):
    """Podigni na prvo mjesto porodicu koju NASLOV lekcije izričito imenuje.

    Kod tekstualnih zadataka bira se porodica koja je VEĆ u listi te oblasti.
    Kod poređenja se traži porodica koja odgovara ZAPISU lekcije; ako je nema u
    listi (npr. lekcija o decimalnim brojevima je u „razlomci“ kanti), dodaje se
    — inače bi lekcija dobila porodicu čiji validator odbija njen vlastiti
    zapis, što je gore nego zatečeno stanje."""
    title = lesson_title or ""
    if _WORD_PROBLEM_TITLE_RE.search(title):
        for family in _WORD_PROBLEM_FAMILIES_BY_PRIORITY:
            if family in families:
                families.remove(family)
                return [family] + families
        return families

    if _COMPARISON_TITLE_RE.search(title):
        family = _comparison_family_for(title)
        if family in families:
            families.remove(family)
        return [family] + families
    return families


def _least_recently_used(candidates, recently_used):
    """Iz `candidates` izaberi onu koja je NAJDUŽE neupotrijebljena.

    `recently_used` je hronološka lista (najstarija → najnovija). Porodica koja
    se uopšte ne pojavljuje u historiji ima prioritet (rang -1). Neriješeno se
    razrješava redoslijedom iz `candidates` — izbor je potpuno determinističan.
    """
    def rank(family):
        try:
            return recently_used.index(family)
        except ValueError:
            return -1

    return min(candidates, key=lambda f: (rank(f), candidates.index(f)))


def select_family(applicable, recently_used=None, completed_families=None,
                  retry_required=False, current_family="", difficulty_request=""):
    """Izaberi porodicu za SLJEDEĆI generisani zadatak.

    Pravila (server je jedini koji ovo odlučuje, prije AI poziva):
      1. retry poslije netačnog odgovora → ISTA porodica (provjera iste vještine)
      2. inače → porodica koja još nije uspješno završena, različita od trenutne
      3. kad su sve završene → drugi ciklus: najdulje neupotrijebljena, ali
         nikad odmah ponovo ista kao prethodna
    """
    recently_used = list(recently_used or [])
    completed_families = list(completed_families or [])
    if not applicable:
        return ""

    if retry_required and current_family in applicable:
        return current_family

    # Teži/lakši je promjena nivoa ISTE izabrane lekcije, ne poziv da se pređe
    # na sporednu zajedničku porodicu.
    #
    # NAMJERNO ZADRŽANO ZA LEGACY PUT. Ranije je ovo značilo „prva porodica iz
    # ručno složene liste TE lekcije“, pa je težina zavisila od toga šta je neko
    # slučajno napisao prvo; te liste po lekciji su uklonjene, pa je applicable[0]
    # sada primarni oblik DOMENA (razlomci/sistemi/geometrija/…), što i dalje
    # čuva identitet lekcije kod zahtjeva za težim/lakšim.
    #
    # Lekcije s uključenim ugovorom OVO NE KORISTE: tamo težinu nosi
    # matbot/contracts/difficulty.py (deklarisane dimenzije i granice), pa
    # „teže“ ne bira drugi zadatak nego druge brojeve iste vještine.
    if difficulty_request in ("harder", "easier"):
        return applicable[0]

    remaining = [f for f in applicable if f not in completed_families]
    if remaining:
        candidates = [f for f in remaining if f != current_family] or remaining
        return _least_recently_used(candidates, recently_used)

    # Svi su završeni → razmaknuti drugi ciklus (spaced repetition).
    cycle = [f for f in applicable if f != current_family] or list(applicable)
    return _least_recently_used(cycle, recently_used)


def describe(family_id):
    """Kratak opis porodice za prompt; prazan string za nepoznat id."""
    return FAMILY_DESCRIPTIONS.get(family_id, "")


# ---------------------------------------------------------------------------
# POTPIS ZADATKA — lagana zaštita od doslovnog ponavljanja unutar sesije.
# Namjerno NIJE matematički parser: normalizuje se samo oblik teksta, pa
# poklapanje znači „isti tekst pitanja“, ne „ekvivalentan zadatak“.
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[.,;:!?„“\"'()]+")


def normalize_question(text):
    """Mala, sigurna normalizacija teksta pitanja: mala slova, sažeti razmaci,
    uklonjena interpunkcija. Brojevi se NE diraju — dva zadatka s različitim
    brojevima ostaju različiti potpisi (to i jeste željeno ponašanje: rotaciju
    porodica radi select_family, a ovaj potpis hvata samo doslovno ponavljanje).
    """
    lowered = (text or "").strip().lower()
    lowered = _PUNCTUATION_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", lowered).strip()


# ---------------------------------------------------------------------------
# PEDAGOŠKI POTPIS OBLIKA — sekundarna zaštita.
#
# Živi nalaz: tri uzastopna zadatka iz TRI različite porodice imala su isti
# pedagoški oblik, samo s drugim brojevima:
#     „Proširi razlomak 3/8 tako da nazivnik bude 24.“
#     „Proširi razlomak 2/5 tako da nazivnik bude 20.“
#     „Proširi razlomak 3/7 tako da nazivnik bude 35.“
# Doslovni potpis (normalize_question) ih vidi kao TRI RAZLIČITA zadatka jer
# čuva brojeve. Ovaj potpis zamjenjuje površne vrijednosti placeholderima pa
# sva tri daju IDENTIČAN oblik.
#
# NAMJERNO USKO: operatori ($+$, $-$, $\cdot$, $:$), imena promjenljivih i sve
# riječi ostaju netaknuti. Zato „$x+5=12$“ i „$x-3=8$“ NISU isti oblik — ne
# želimo proglasiti svaku algebarsku jednačinu istom samo zato što su se
# brojevi promijenili.
# ---------------------------------------------------------------------------

_SHAPE_FRAC_LATEX_RE = re.compile(r"\\frac\s*\{\s*-?\d+\s*\}\s*\{\s*-?\d+\s*\}")
_SHAPE_FRAC_SLASH_RE = re.compile(r"(?<!\d)-?\d+\s*/\s*-?\d+(?!\d)")
_SHAPE_DECIMAL_RE = re.compile(r"(?<!\d)-?\d+[.,]\d+(?!\d)")
_SHAPE_INT_RE = re.compile(r"(?<![\w<])-?\d+(?![\w>])")
_SHAPE_STRIP_RE = re.compile(r"[$.,;:!?„“\"'()\[\]]+")


def pedagogical_shape(question_text):
    """Oblik pitanja bez površnih vrijednosti: razlomci → <frac>, decimale →
    <dec>, cijeli brojevi → <num>. Vraća normalizovan mali tekst."""
    text = (question_text or "").strip()
    # Redoslijed je bitan: razlomci PRIJE golih cijelih brojeva.
    text = _SHAPE_FRAC_LATEX_RE.sub(" <frac> ", text)
    text = _SHAPE_FRAC_SLASH_RE.sub(" <frac> ", text)
    text = _SHAPE_DECIMAL_RE.sub(" <dec> ", text)
    text = _SHAPE_INT_RE.sub(" <num> ", text)
    text = _SHAPE_STRIP_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def task_signature(task_family, question_text, lesson_id, difficulty):
    """Potpis zadatka koji se pamti u sesiji radi otkrivanja ponavljanja.

    Nosi DVA potpisa: doslovni (`question`, čuva brojeve — hvata identičan
    tekst) i pedagoški (`shape`, bez brojeva — hvata „isti zadatak, drugi
    brojevi“ kroz različite porodice)."""
    return {
        "task_family": task_family or "",
        "question": normalize_question(question_text),
        "shape": pedagogical_shape(question_text),
        "lesson_id": lesson_id or "",
        "difficulty": difficulty or "",
    }


def is_duplicate_signature(signature, recent_signatures):
    """True ako je IDENTIČAN normalizovan tekst pitanja već viđen u ovoj sesiji
    za istu lekciju. Porodica i težina se namjerno NE traže u poklapanju —
    isti tekst pitanja je ponavljanje bez obzira na to kako je označen."""
    for previous in recent_signatures or []:
        if not previous.get("question"):
            continue
        if (previous.get("question") == signature.get("question")
                and previous.get("lesson_id") == signature.get("lesson_id")):
            return True
    return False


def is_duplicate_shape(signature, recent_signatures, retry_required=False):
    """True kad zadatak DRUGE porodice ponavlja pedagoški oblik nekog nedavnog
    zadatka — tačno onaj propust koji je uhvaćen uživo.

    Pravila:
      • poklapanje oblika unutar ISTE porodice je DOZVOLJENO (retry namjerno
        vježba istu vještinu s novim vrijednostima; doslovno ponavljanje i dalje
        hvata is_duplicate_signature)
      • poklapanje oblika kroz RAZLIČITE porodice se odbija — dvije različite
        vještine ne smiju izgledati kao isti zadatak
    """
    shape = signature.get("shape")
    if not shape:
        return False
    family = signature.get("task_family") or ""
    for previous in recent_signatures or []:
        if previous.get("shape") != shape:
            continue
        if previous.get("lesson_id") != signature.get("lesson_id"):
            continue
        previous_family = previous.get("task_family") or ""
        if previous_family and family and previous_family != family:
            return True
        if retry_required or previous_family == family:
            continue
        return True
    return False


def is_duplicate_mathematical_task(signature, recent_signatures):
    """True for a repeated server-derived mathematical MCQ fingerprint.

    The normal text/shape safeguards stay in force.  This third, optional
    signal exists only when a narrow deterministic oracle could derive a
    fingerprint (currently parseable divisibility MCQs), so blank values never
    turn ordinary creative tasks into false duplicates.
    """
    fingerprint = signature.get("mathematical_fingerprint") or ""
    if not fingerprint:
        return False
    return any(
        previous.get("lesson_id") == signature.get("lesson_id")
        and previous.get("mathematical_fingerprint") == fingerprint
        for previous in (recent_signatures or [])
    )
