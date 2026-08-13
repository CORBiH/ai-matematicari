"""ALGEBARSKI OBLIK zadatka — na kojem MJESTU stoji nepoznata.

ZAŠTO POSTOJI (ručni nalaz nad lekcijom „Nejednačine s razlomcima oblika
x ± a < b / > b i a ± x < b / > b“): nastavni plan te lekcije izričito nabraja
OBA porodična oblika, a učenik je 20 od 20 puta dobijao samo `x`-prvi oblik.
Mjereno nad stvarnim serverskim putem, bez ijednog modelskog poziva.

    arhetip      = ŠTA učenik mora uraditi (riješiti, naći grešku, uporediti)
    oblik (ovdje) = GDJE stoji nepoznata unutar izraza

To su DVIJE različite ose i namjerno se ne miješaju: `a - x < b` i `x - a < b`
su isti arhetip (`DIRECT_COMPUTE`), a pedagoški bitno različita zadatka — kod
`a - x` izolacija nepoznate množi nejednakost s $-1$ i OBRĆE smjer.

GRANICE (iste kao kod svakog verifikatora u projektu):
  • ovo NIJE mjera tačnosti i nikad ne odlučuje o objavi zbog matematike;
  • zamjena STRANA cijele nejednačine NIJE nov oblik — `b > x - a` je i dalje
    `x`-prvi oblik, pa se gleda isključivo strana na kojoj stoji nepoznata;
  • ono što se ne može dokazati vraća `` (nepoznato), nikad se ne nagađa.
"""
import json
import os
import re
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# ZATVORENA LISTA OBLIKA
# ---------------------------------------------------------------------------
X_PLUS_A = "X_PLUS_A"
X_MINUS_A = "X_MINUS_A"
A_PLUS_X = "A_PLUS_X"
A_MINUS_X = "A_MINUS_X"

ALL_VARIANTS = (X_PLUS_A, X_MINUS_A, A_PLUS_X, A_MINUS_X)

# Opis ide U PROMPT kad server bira oblik za model-podržane lekcije.
DESCRIPTIONS = {
    X_PLUS_A: "nepoznata je PRVI član i sabira se: $x + a$",
    X_MINUS_A: "nepoznata je PRVI član i oduzima se od nje: $x - a$",
    A_PLUS_X: "nepoznata je DRUGI član i sabira se: $a + x$",
    A_MINUS_X: ("nepoznata je DRUGI član i ODUZIMA SE OD broja: $a - x$ "
                "(izolacija obrće smjer nejednakosti)"),
}

_ARTIFACT = Path(__file__).resolve().parent.parent / "data" / "task_form_variants.json"

# Relacija koja dijeli izraz na dvije strane.
_RELATION_RE = re.compile(r"\\leq|\\geq|\\le|\\ge|<=|>=|≤|≥|<|>|=")
# Samostalna nepoznata: `x`, ali ne `\frac`, ne `max`, ne `x^2`.
_UNKNOWN_RE = re.compile(r"(?<![a-zA-Z\\])x(?![a-zA-Z0-9^_])")
_MATH_DELIMITERS_RE = re.compile(r"\$+")


def _enabled() -> bool:
    """Rotacija oblika. `disabled` vraća ponašanje bez preferencije."""
    return os.environ.get("MATBOT_FORM_ROTATION", "enabled") != "disabled"


@lru_cache(maxsize=1)
def _payload() -> dict:
    try:
        return json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def supported(lesson_id) -> tuple:
    """Oblici koje lekcija DEKLARIŠE svojim gradivom; prazno = nema mjerila.

    Prazno je najčešći slučaj i znači „ne diraj ništa“: lekcija bez izričito
    nabrojanih oblika ponaša se bajt-identično zatečenom."""
    row = (_payload().get("lessons") or {}).get(lesson_id) or {}
    return tuple(row.get("supported") or ())


def _top_level_split(side):
    """Podijeli stranu na članove po `+`/`-` koji NISU unutar vitičastih zagrada."""
    terms, operators, depth, current = [], [], 0, []
    for character in side:
        if character in "{(":
            depth += 1
        elif character in "})":
            depth = max(0, depth - 1)
        if depth == 0 and character in "+-" and current:
            terms.append("".join(current))
            operators.append(character)
            current = []
            continue
        current.append(character)
    terms.append("".join(current))
    return [term.strip() for term in terms], operators


def classify(task_text):
    """Oblik izraza s nepoznatom, ili `` kad se ne može dokazati.

    Gleda ISKLJUČIVO stranu na kojoj stoji nepoznata, pa zamjena strana cijele
    nejednačine ne može lažirati drugi oblik."""
    text = _MATH_DELIMITERS_RE.sub(" ", str(task_text or ""))
    match = _RELATION_RE.search(text)
    if not match:
        return ""
    left, right = text[:match.start()], text[match.end():]
    # Strana s nepoznatom. Kad je nepoznata na obje strane, ništa se ne tvrdi.
    left_has, right_has = bool(_UNKNOWN_RE.search(left)), bool(_UNKNOWN_RE.search(right))
    if left_has == right_has:
        return ""
    side = left if left_has else right
    # Iz teksta zadatka („Riješi nejednačinu: x + 2/3“) uzima se samo izraz —
    # prozni uvod nema relaciju ni operatore na nultoj dubini uz nepoznatu.
    side = side.split(":")[-1]
    terms, operators = _top_level_split(side)
    if len(terms) != 2 or len(operators) != 1:
        return ""
    first_has = bool(_UNKNOWN_RE.search(terms[0]))
    second_has = bool(_UNKNOWN_RE.search(terms[1]))
    if first_has == second_has:
        return ""
    if first_has:
        return X_PLUS_A if operators[0] == "+" else X_MINUS_A
    return A_PLUS_X if operators[0] == "+" else A_MINUS_X


def preferred_variant(lesson_id, recent_variants=()) -> str:
    """Oblik koji server traži SLJEDEĆI, ili `` kad ne traži ništa.

    Prvo neiskorišten deklarisan oblik, pa NAJDAVNIJE korišten (LRU). Isti
    princip kao rotacija arhetipa — samo druga osa."""
    if not _enabled():
        return ""
    options = supported(lesson_id)
    if len(options) < 2:
        return ""
    recent = [variant for variant in (recent_variants or ()) if variant]
    unused = [variant for variant in options if variant not in recent]
    if unused:
        return unused[0]
    # STVARNI LRU: bira se oblik čija je POSLJEDNJA upotreba najstarija, ne
    # prvi zapis u prozoru. Bez toga bi niz `A B C D A` opet vratio `A` — oblik
    # koji je upravo viđen — i učenik bi dobio tri ista oblika zaredom.
    last_used = {variant: index for index, variant in enumerate(recent)}
    return min(options, key=lambda variant: last_used.get(variant, -1))


def describe(variant):
    return DESCRIPTIONS.get(variant, "")
