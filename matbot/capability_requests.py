# -*- coding: utf-8 -*-
"""Server-vlasničko prepoznavanje ZAHTJEVA za operaciju koju razred nema.

ZAŠTO POSTOJI (živi nalaz, produkcija 0a2f087 + mjerena kampanja 12 poziva):
šestaš je na „Koliko je $\\sqrt{36}$?" dobio „Kvadratni korijen nije gradivo
6. razreda" — i odmah zatim „$\\sqrt{36}=6$". Čišćenje protivrječnosti u
promptu (`rules.notation_rules_for_grade`) bilo je nužno, ali NIJE bilo
dovoljno: isti zadatak je poslije čišćenja opet izračunao korijen. Jedan od
mjerenih propusta je bio ČISTA PROZA — „Kvadratni korijen broja $49$ je $7$" —
bez ijednog znaka `\\sqrt`, pa ga izlazni validator notacije po konstrukciji
ne može uhvatiti.

ZATO SE ODLUKA PREMJEŠTA NA ULAZ. Kad učenik IZRIČITO traži operaciju koju
njegov razred nema, model se ne pita da odbije — server odgovori granicom
kurikuluma i model se uopšte ne zove (0 poziva). Ono što model ne dobije, ne
može ni izračunati.

ŠTA OVAJ MODUL NIJE: nije klasifikator matematičke namjere uopšte i ne
pokušava dokazati da neki zadatak „u pozadini traži" zabranjenu operaciju
(kvadrat površine $20$ → stranica). Ta klasa (`IMPLICIT_FORBIDDEN_OPERATION`)
ostaje na očišćenom promptu i izlaznoj kapiji, namjerno — dokazivanje bi
tražilo simbolički motor kojeg ovaj projekat po odluci nema.

SASTAVLJENO IZ DVIJE SERVER-VLASNIČKE ČINJENICE, nikad iz modelove prijave:
  1. POJAM — leksikon nad učenikovom porukom (proza I znak);
  2. NAMJERA — traži li poruka IZVRŠENJE (glagol računanja + operand) ili
     govori O POJMU („Šta je kvadratni korijen?", „Kada se to uči?");
plus 3. SPOSOBNOST RAZREDA iz `ResolvedPracticePolicy` — ista zastavica koju
koriste prompt i validatori, bez nove razredne tabele i bez ID-ja lekcije.
"""
import re

from matbot import practice_policy, textnorm

# ---------------------------------------------------------------------------
# NORMALIZACIJA — isti postupak kao uski prozni klasifikatori u `quick.py` i
# `lesson_relevance.py`: mala slova, bez dijakritika. Znakovi se traže nad
# SIROVIM tekstom, jer ih normalizacija briše.
# ---------------------------------------------------------------------------


def _normalize(value):
    """Leksicki ugovor bez sazimanja razmaka (vidi matbot/textnorm.py)."""
    return textnorm.normalize_lexical(value, collapse_whitespace=False)


# ---------------------------------------------------------------------------
# NAMJERA
# ---------------------------------------------------------------------------
# TRAŽI IZVRŠENJE. „koliko je"/„koliki je" pokrivaju i „Koliki je kvadratni
# korijen broja 49?" — mjereni prozni propust B3.
_EXECUTE_MARKERS = (
    "izracunaj", "izracunajte", "izracunati", "racunaj", "izracun",
    "koliko je", "koliko iznosi", "koliki je", "kolika je", "koliko su",
    "pojednostavi", "pojednostavite", "svedi", "sredi",
    "rezultat", "izvadi", "izvuci", "odredi", "nadji", "nadi",
    "reci mi koliko", "reci koliko", "samo rezultat", "samo mi reci",
)
# GOVORI O POJMU. Ove fraze same po sebi NE traže izvršenje; kad se pojave bez
# glagola računanja, pitanje ostaje modelu (vidi `is_conceptual`).
_CONCEPT_MARKERS = (
    "sta je", "sto je", "sta znaci", "sta su", "definicij",
    "kada se uci", "kad se uci", "kada ucimo", "kad ucimo",
    "zasto ne radimo", "zasto jos ne", "zasto se ne", "u kojem razredu",
    "objasni pojam", "sta predstavlja",
)
_OPERAND_RE = re.compile(r"\d")


# ---------------------------------------------------------------------------
# POJMOVI VEZANI ZA KURIKULARNU SPOSOBNOST
# ---------------------------------------------------------------------------
# Struktura je OPŠTA: novi pojam je novi red u tabeli, bez nove politike i bez
# grananja po razredu. `allows` čita POSTOJEĆU zastavicu razriješene politike.
_BS = "\\"

CAPABILITY_RADICAL = "radical_operation"
CAPABILITY_PYTHAGORAS = "pythagoras_operation"


def _radical_allowed(policy):
    return bool(getattr(policy, "radical_operation_allowed", True))


def _pythagoras_allowed(policy):
    return bool(getattr(policy, "pythagoras_operation_allowed", True))


_CAPABILITIES = (
    {
        "id": CAPABILITY_RADICAL,
        # Proza: „korijen", „korjenovanje", „korenovanje", „korijena iz".
        "prose": re.compile(r"\bkori?j?en\w*", re.UNICODE),
        # Znak: `\sqrt` u sirovoj poruci ili unicode korijen.
        "symbol": re.compile(re.escape(_BS + "sqrt") + r"|√"),
        "allows": _radical_allowed,
        "introduced": practice_policy.RADICAL_OPERATION_GRADE,
        "concept_name": "Kvadratni korijen",
        "operation_name": "korjenovanje",
        # Zamjenica u granicnoj poruci mora se slagati s rodom pojma:
        # „Kvadratni korijen ... pa GA", ali „Pitagorina teorema ... pa JE".
        "object_pronoun": "ga",
    },
    {
        "id": CAPABILITY_PYTHAGORAS,
        "prose": re.compile(r"\bpitagor\w*", re.UNICODE),
        "symbol": re.compile(r"(?!x)x"),          # nema vlastiti znak
        "allows": _pythagoras_allowed,
        "introduced": practice_policy.PYTHAGORAS_OPERATION_GRADE,
        "concept_name": "Pitagorina teorema",
        "operation_name": "računanje Pitagorinom teoremom",
        "object_pronoun": "je",
    },
)

_BY_ID = {spec["id"]: spec for spec in _CAPABILITIES}

# JEDAN RJECNIK SPOSOBNOSTI za cio sistem. Cita ga i eksplicitni detektor
# iznad, i semanticka deklaracija `shape_required_operations` u
# `data/semantic_families.json` (provjerava je kompajler), pa se imena ne mogu
# raziti izmedju podataka i koda.
KNOWN_OPERATIONS = frozenset(_BY_ID)


def operation_allowed(operation_id, policy):
    """Smije li razred izvesti ovu operaciju. Nepoznata operacija -> True
    (fail-open): nedokazano nikad ne znaci zabranjeno."""
    spec = _BY_ID.get(operation_id)
    if spec is None or policy is None:
        return True
    return spec["allows"](policy)


def named_capabilities(message):
    """Sposobnosti čiji je POJAM imenovan u poruci (proza ili znak)."""
    normalized = _normalize(message)
    raw = message or ""
    found = []
    for spec in _CAPABILITIES:
        if spec["prose"].search(normalized) or spec["symbol"].search(raw):
            found.append(spec["id"])
    return tuple(found)


def is_conceptual(message):
    """Pita li poruka O POJMU umjesto da traži izvršenje.

    Konzervativno: pojmovna fraza vrijedi SAMO ako poruka istovremeno ne nosi
    glagol računanja. „Šta je korijen i koliko je $\\sqrt{9}$?" je zahtjev za
    izvršenje, ne pojmovno pitanje."""
    normalized = _normalize(message)
    if any(marker in normalized for marker in _EXECUTE_MARKERS):
        return False
    return any(marker in normalized for marker in _CONCEPT_MARKERS)


def requests_execution(message):
    """Traži li poruka IZVRŠENJE operacije nad konkretnim brojem.

    Dva nužna uslova, oba server-vlasnička:
      • glagol/fraza računanja iz zatvorenog skupa;
      • OPERAND — bar jedna cifra. Bez operanda nema šta da se izračuna, pa
        „Kada se uči korjenovanje?" i „Šta je korijen?" ne mogu upasti.
    Operand je namjerno gruba, ali NEOBORIVA činjenica: ovdje se ne pokušava
    parsirati matematika, samo razlikovati pitanje o pojmu od naloga."""
    if is_conceptual(message):
        return False
    normalized = _normalize(message)
    if not any(marker in normalized for marker in _EXECUTE_MARKERS):
        return False
    return bool(_OPERAND_RE.search(message or ""))


def forbidden_operation_requests(message, policy):
    """Sposobnosti koje poruka IZRIČITO traži, a razred ih nema.

    Vraća torku ID-jeva (obično prazna). Prazna torka znači „nema dokaza" —
    nikad se ne pogađa; implicitni zadaci ostaju modelu."""
    if policy is None or not message:
        return ()
    if not requests_execution(message):
        return ()
    named = set(named_capabilities(message))
    return tuple(spec["id"] for spec in _CAPABILITIES
                 if spec["id"] in named and not spec["allows"](policy))


# ---------------------------------------------------------------------------
# ODGOVOR GRANICE KURIKULUMA
# ---------------------------------------------------------------------------
# NIJE greška i NIKAD ne smije biti `SAFE_ERROR_MESSAGE`: učenikovo pitanje je
# legitimno, samo je odgovor granica gradiva. Tekst poštuje razrednu
# sposobnost ZAPISA — razred koji znak ne smije vidjeti dobija čistu prozu.
def boundary_answer(capability_id, policy):
    spec = _BY_ID[capability_id]
    grade = getattr(policy, "grade", 0)
    introduced = spec["introduced"]
    concept = spec["concept_name"]
    if capability_id == CAPABILITY_RADICAL and getattr(
            policy, "radical_notation_allowed", False):
        # 7. razred: zapis smije vidjeti, samo ga ne računa.
        return (
            f"Zapis ${_BS}sqrt{{{_BS};}}$ smiješ prepoznati, ali korjenovanje "
            f"se uči u {introduced}. razredu, pa ga u {grade}. razredu još ne "
            "računamo. Reci mi šta iz ove lekcije da objasnimo umjesto toga."
        )
    pronoun = spec.get("object_pronoun", "ga")
    return (
        f"{concept} se uči u {introduced}. razredu, pa {pronoun} u {grade}. "
        "razredu još ne računamo. Kad dođeš do te lekcije, radit ćemo je korak "
        "po korak. Pitaj me nešto iz gradiva ovog razreda i rado ću objasniti."
    )
