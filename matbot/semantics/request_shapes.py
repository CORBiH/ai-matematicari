# -*- coding: utf-8 -*-
"""Prepoznavanje KANONSKOG OBLIKA ZAHTJEVA i operacije koju taj oblik traži.

ZAŠTO POSTOJI (živi nalaz L5, izdanje 6e785ad): šestaš je pitao „Pravougli
trougao ima katete 3 cm i 4 cm. Kolika je hipotenuza?" i dobio „hipotenuza je
5 cm" uz urednu rečenicu da se teorema uči kasnije. Formula nije upotrijebljena,
ali REZULTAT koji se bez nje ne može dobiti jeste objavljen.

ZAŠTO OVO NIJE MOGAO RIJEŠITI POSTOJEĆI SLOJ: `capability_requests` traži da
poruka IMENUJE pojam („korijen", „Pitagora") — L5 ne imenuje nijedan. A
semantička porodica lekcije prati IZABRANU LEKCIJU, ne zahtjev: ista poruka
daje `pythagoras_direct` u 8. i `angle_relationships_direct` u 6. razredu, pa
bi kao signal blokirala baš onaj razred koji smije računati.

ARHITEKTURA — DVIJE ODVOJENE STVARI, obje server-vlasničke:
  1. DEKLARACIJA (podatak): `data/semantic_families.json` →
     `shape_required_operations` kaže koju operaciju traži pojedini kanonski
     oblik. Autorski podatak, provjerava ga kompajler.
  2. PREPOZNAVAČ (kod): funkcija koja iz učenikove poruke izvede KANONSKI
     OBLIK iz iste porodice — nikad slobodnu prozu i nikad zabranu direktno.

ZAŠTO VLASTITI REGISTAR, a ne `detectors.DETECTORS`: taj registar ima drugi
ugovor (`detector(contract, text, answer_text)` provjerava VEĆ GENERISAN paket
prema ugovoru lekcije) i drugu svrhu. Upis ovamo bi usput aktivirao provjeru
paketa za 12 lekcija 8. razreda — promjenu ponašanja Vježbajma koja nije
tražena. Registar je zato paralelan, u istom paketu i s istom disciplinom.

GRANICA POUZDANOSTI, izmjerena a ne pretpostavljena: prepoznaje se samo ono
što `tests/test_pythagoras_request_shapes.py` mjeri. Oblik koji se ne prepozna
NIJE zabranjen — vraća se prazno i turn ide modelu (fail-open). Nedokazano
nikad ne znači zabranjeno.
"""
import json
import re
from matbot import textnorm
from functools import lru_cache
from pathlib import Path

FAMILIES_PATH = (Path(__file__).resolve().parent.parent.parent
                 / "data" / "semantic_families.json")



def _normalize(value):
    """Leksički ugovor + preslikavanje „đ".

    „đ" (U+0111) nema kanonsku dekompoziciju, pa preživi svaki NFKD-baziran
    normalizator; bez preslikavanja „Nađi hipotenuzu za katete 8 i 15." izmiče
    ovoj kapiji. Preslikavanje je IZRIČITO baš ovdje, jer ga drugi potrošači
    ne smiju dobiti — `quick_context` čita oznaku zadatka azbukom koja „đ"
    sadrži. Vidi `matbot/textnorm.py`."""
    return textnorm.normalize_lexical(value, collapse_whitespace=False,
                                      fold_dstroke=True)


# ---------------------------------------------------------------------------
# TRAŽENA VELIČINA — koja se veličina PITA, a ne koja se spominje
# ---------------------------------------------------------------------------
# Ključna preciznost cijelog modula. „Kolika je površina trougla čije su katete
# 3 i 4?" spominje katete, ali PITA površinu — i to je zadatak koji 6. razred
# smije riješiti. Zato se traži PRVA mjerljiva imenica IZA pitalice.
# Jedan regex umjesto rastuće liste doslovnih fraza: enklitika između upitne
# riječi i glagola („Kolika MU je dijagonala?") je obična bosanska konstrukcija
# koju je popis fraza propuštao — živi defekt nađen u pregledu. Glagoli se
# navode uže (`odredi`, ne `odred\w*`) da izjavna „ugao je određen…" ne bi
# glumila zahtjev.
_ASK_RE = re.compile(
    r"\bkolik[aeiou]\w*(?:\s+(?:mu|joj|im|nam|vam|ti|mi))?\s+(?:je|su)\b"
    r"|\bkoliko(?:\s+(?:mu|joj|im))?\s+iznosi\b"
    r"|\bizracunaj\b|\bizracunajte\b|\bizracunati\b|\bracunaj\b"
    r"|\bodredi\b|\bodredite\b"
    r"|\bnadi\b|\bnadji\b|\bnadite\b|\bnadjite\b"
)
# Imenice koje imenuju MJERLJIVU veličinu. Prva iza pitalice je ono što se pita.
_QUANTITY_NOUNS = (
    ("hypotenuse_noun", re.compile(r"\bhipotenuz\w*")),
    ("leg_noun", re.compile(r"\bkatet\w*")),
    ("diagonal", re.compile(r"\bdijagonal\w*")),
    ("area", re.compile(r"\bpovrsin\w*")),
    ("perimeter", re.compile(r"\bobim\w*|\bopseg\w*")),
    ("angle", re.compile(r"\bugao\b|\bugl\w*")),
    ("height", re.compile(r"\bvisin\w*")),
    ("side", re.compile(r"\bstranic\w*")),
    ("volume", re.compile(r"\bzapremin\w*|\bvolumen\w*")),
    ("radius", re.compile(r"\bpoluprecnik\w*|\bprecnik\w*|\bpolumjer\w*")),
)
# MJERNI KVALIFIKATORI („duzina", „duljina", „velicina", „mjera") NAMJERNO nisu
# na listi: oni ne imenuju vlastitu veličinu nego najavljuju onu koja slijedi —
# „Kolika je DUŽINA HIPOTENUZE" pita hipotenuzu. Živi defekt nađen u pregledu:
# dok je „duzin" stajao među veličinama, ta posve obična bosanska formulacija
# je izmicala kapiji i 6. razred je opet mogao dobiti „5 cm". Prva PRAVA
# veličina iza pitalice je ono što se pita.

# Figure. „hipotenuza" i „kateta" same po sebi znace pravougli trougao, pa
# figura za te oblike nije potreban dokaz; provjerava se samo tamo gdje je
# dijagonala dvosmislena (pravougaonik naspram kvadrata).
_RECTANGLE = re.compile(r"\bpravougaon\w*|\bpravokutnik\w*")
# `kvadrat` kao FIGURA ima imeničke oblike (kvadrat, kvadrata, kvadratu…), dok
# je `kvadratn-` PRIDJEV mjerne jedinice („20 kvadratnih centimetara"). Živi
# defekt nađen u pregledu: riječ jedinice je glumila figuru, pa je „Površina je
# 20 kvadratnih centimetara. Kolika je dijagonala?" bilo blokirano iako figura
# uopšte nije poznata. Pridjevski oblik se zato isključuje — radije propustiti
# nedokazano nego blokirati po riječi mjerne jedinice.
_SQUARE = re.compile(r"\bkvadrat(?!n)\w*")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# Pojmovna/definicijska pitanja — ova NIKAD ne traže izvršenje.
_CONCEPT_MARKERS = (
    "sta je", "sto je", "sta su", "sta znaci", "koja je definicija",
    "koje stranice", "koja stranica", "kada se uci", "kad se uci",
    "u kojem razredu", "zasto", "objasni", "kako se zove", "sta predstavlja",
)


def _asked_quantity(normalized):
    """Ime veličine koja se PITA, ili None."""
    best = None
    for ask in _ASK_RE.finditer(normalized):
        tail = normalized[ask.end():]
        hits = []
        for name, pattern in _QUANTITY_NOUNS:
            match = pattern.search(tail)
            if match:
                hits.append((match.start(), name))
        if not hits:
            continue
        hits.sort()
        position = ask.end() + hits[0][0]
        if best is None or position < best[0]:
            best = (position, hits[0][1])
    return best[1] if best else None


def _count_numbers(message):
    return len(_NUMBER.findall(message or ""))


# ---------------------------------------------------------------------------
# PREPOZNAVAČ PORODICE `pythagoras_direct`
# ---------------------------------------------------------------------------

def recognise_pythagoras_direct(message):
    """Vrati kanonski oblik iz `pythagoras_direct.kinds`, ili "".

    Svaki oblik traži TRI dokaza zajedno: pita se baš ta veličina, kontekst je
    prava figura, i data su brojčana mjerenja. Bilo koji izostane — vraća se
    prazno i zahtjev ide modelu."""
    raw = message or ""
    normalized = _normalize(raw)
    if not normalized:
        return ""
    asked = _asked_quantity(normalized)
    if asked is None:
        return ""                      # nema pitanja o veličini → pojam/proza
    numbers = _count_numbers(raw)

    if asked == "hypotenuse_noun" and numbers >= 2:
        return "hypotenuse"
    if asked == "leg_noun" and numbers >= 2 and re.search(r"\bhipotenuz\w*", normalized):
        return "leg"
    if asked == "diagonal":
        if _RECTANGLE.search(normalized) and numbers >= 2:
            return "rectangle_diagonal"
        if _SQUARE.search(normalized) and numbers >= 1 and not _RECTANGLE.search(normalized):
            return "square_diagonal"
    return ""


REQUEST_SHAPE_RECOGNISERS = {
    "pythagoras_direct": recognise_pythagoras_direct,
}


# ---------------------------------------------------------------------------
# DEKLARACIJA IZ PODATAKA
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _shape_requirements():
    """{porodica: {oblik: (operacije,)}} iz kanonskih podataka porodica."""
    try:
        payload = json.loads(FAMILIES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for family_id, family in (payload.get("families") or {}).items():
        declared = family.get("shape_required_operations")
        if not isinstance(declared, dict):
            continue
        out[family_id] = {str(shape): tuple(ops)
                          for shape, ops in declared.items()
                          if isinstance(ops, list) and ops}
    return out


def reset_cache():
    _shape_requirements.cache_clear()


def recognise_request_shape(message):
    """(porodica, oblik) za prvi prepoznavač koji tvrdi oblik, ili ("", "")."""
    for family_id, recogniser in sorted(REQUEST_SHAPE_RECOGNISERS.items()):
        shape = recogniser(message)
        if shape:
            return family_id, shape
    return "", ""


def required_operations_for_request(message):
    """Operacije koje ZAHTJEV dokazano traži — torka, obično prazna.

    ZAHTJEV-SKOPIRANO: gleda se isključivo učenikova poruka. Izabrana lekcija
    se NAMJERNO ne konsultuje; mjereno je da ona prati temu koju je učenik
    kliknuo, ne ono što je pitao."""
    requirements = _shape_requirements()
    operations = []
    # SVI prepoznavači, ne samo prvi koji nešto tvrdi: jedan zahtjev smije
    # tražiti više operacija (npr. i teoremu i korjenovanje), a buduća porodica
    # se dodaje SAMO upisom u registar — bez ijedne izmjene u `explain.py`.
    for family_id, recogniser in sorted(REQUEST_SHAPE_RECOGNISERS.items()):
        shape = recogniser(message)
        if not shape:
            continue
        for operation in requirements.get(family_id, {}).get(shape, ()):
            if operation not in operations:
                operations.append(operation)
    return tuple(operations)
