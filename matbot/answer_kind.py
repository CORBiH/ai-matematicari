"""VRSTA ODGOVORA — klasifikacija ZAPISA, bez ijedne porodice zadatka.

Ovaj modul je sve što je preživjelo dva fajla obrisana s povlačenjem starog
jednopozivnog Practice motora (2026-08-14):

  • `matbot/task_family_validation.py` (1.435 redova) — validacija porodica
    zadataka za taj motor;
  • `matbot/systemcheck.py` (934 reda) — supstitucijski verifikator sistema,
    koji AKTIVNI put nikad nije zvao (mjereno: nula referenci u `matbot/tutor/`).

Zašto je ovih ~200 redova ostalo: klasifikacija ZAPISA odgovora (razlomak /
cijeli broj / uređeni par / kratak tekst) ne zna nijedan motor ni porodicu —
gleda isključivo tekst. Koristi je zvanična kapija izdanja
(`scratchpad/run_difficulty_canary.py` kroz `tools/run_live_release_gate.py`)
da NEZAVISNO provjeri objavljen odgovor. Brisanje bi oslijepilo kapiju, a ne
pojednostavilo motor.

Ime je promijenjeno namjerno: modul koji više ne validira porodice zadataka ne
smije se zvati `task_family_validation`.
"""
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction


class _Unsupported(Exception):
    """Zapis se ne može SIGURNO pročitati — nikad se ne pogađa."""


_CLEAN_RE = re.compile(r"\\left|\\right|\\,|\\;|\\!|\\quad|\\qquad|\\ |\s+")


def _clean(expr):
    return _CLEAN_RE.sub("", expr or "")


def _to_fraction(token):
    """Egzaktna konverzija cijelog/decimalnog broja u Fraction.
    „0,5“ → 1/2 (preko Decimal, bez binarnog float šuma)."""
    text = token.replace(",", ".")
    try:
        return Fraction(Decimal(text))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        raise _Unsupported(f"neparsabilan broj {token!r}")


_SCALAR_RE = re.compile(
    r"^-?(?:\\frac\{-?\d+\}\{-?\d+\}|\d+/-?\d+|\d+(?:[.,]\d+)?)$"
)
_XY_FORM_RE = re.compile(
    r"x\s*=\s*(?P<x>-?(?:\\frac\{-?\d+\}\{-?\d+\}|\d+/-?\d+|\d+(?:[.,]\d+)?))"
    r"\s*[,;]\s*"
    r"y\s*=\s*(?P<y>-?(?:\\frac\{-?\d+\}\{-?\d+\}|\d+/-?\d+|\d+(?:[.,]\d+)?))"
)
_PAREN_RE = re.compile(r"\(([^()]*)\)")


def _parse_scalar(token):
    t = _clean(token)
    if not _SCALAR_RE.match(t):
        raise _Unsupported(f"neparsabilan skalar {token!r}")
    neg = t.startswith("-")
    if neg:
        t = t[1:]
    m = re.fullmatch(r"\\frac\{(-?\d+)\}\{(-?\d+)\}", t)
    if m:
        den = int(m.group(2))
        if den == 0:
            raise _Unsupported("nazivnik 0")
        value = Fraction(int(m.group(1)), den)
    else:
        m = re.fullmatch(r"(\d+)/(-?\d+)", t)
        if m:
            den = int(m.group(2))
            if den == 0:
                raise _Unsupported("nazivnik 0")
            value = Fraction(int(m.group(1)), den)
        else:
            value = _to_fraction(t)
    return -value if neg else value


def _split_pair_body(body):
    """Razdvoji sadržaj zagrade na dvije koordinate.

    Tačka-zarez je NEDVOSMISLEN separator (koristi se baš kad koordinate imaju
    decimalni zarez). Za zarez se traži da razdvajanje da TAČNO dva dijela —
    „0,5;-1,25“ ide preko „;“, a „0,5“ (jedan decimalni broj) daje dva dijela
    koja su oba cijeli brojevi i tretira se kao par SAMO uz zagrade (vidi
    parse_ordered_pair), što je i postojeća konvencija projekta."""
    if ";" in body:
        parts = body.split(";")
    else:
        parts = body.split(",")
    if len(parts) != 2:
        raise _Unsupported("koordinate nisu jednoznačne")
    return parts[0], parts[1]


def parse_ordered_pair(text):
    """Vrati (x, y) kao Fraction, ili None kad se ne može SIGURNO pročitati.

    Podržano: $(3,2)$, $(-3,2)$, $\\left(3,2\\right)$,
    $\\left(\\frac{9}{7},\\frac{13}{7}\\right)$, $(0,5;-1,25)$,
    $x=3,\\ y=2$, i prozni omotač koji sadrži TAČNO JEDAN takav par.
    Redoslijed koordinata se čuva."""
    if not text:
        return None
    raw = text.strip()
    cleaned = _clean(raw.replace("$", " "))

    m = _XY_FORM_RE.search(cleaned)
    if m:
        try:
            return (_parse_scalar(m.group("x")), _parse_scalar(m.group("y")))
        except _Unsupported:
            return None

    bodies = _PAREN_RE.findall(cleaned)
    if len(bodies) != 1:
        return None            # nula ili više parova → dvosmisleno, ne pogađaj
    try:
        left, right = _split_pair_body(bodies[0])
        return (_parse_scalar(left), _parse_scalar(right))
    except _Unsupported:
        return None


_BARE_PAIR_RE = re.compile(r"^\([^()]*\)$")


def is_bare_ordered_pair(text):
    """True SAMO kad je CIJELA opcija uređeni par — ne proza koja par spominje.

    Ovo je predikat za `answer_kind` (matbot/task_family_validation.py), gdje
    je razlika ključna: „Par $(2,1)$ zadovoljava obje jednačine.“ je TVRDNJA
    (option_label/short_text), a ne uređeni par. Živi nalaz koji ovo štiti je
    lažno odbijen `verify_ordered_pair` zadatak — vidi
    tests/test_student_must_find_trust.py.

    `parse_ordered_pair` je namjerno tolerantniji (vadi par i iz proze) jer se
    koristi za čitanje `expected_answer`; ovdje je potrebna stroga varijanta."""
    if not text:
        return False
    cleaned = _clean(str(text).replace("$", " "))
    if _BARE_PAIR_RE.match(cleaned):
        return parse_ordered_pair(text) is not None
    if _XY_FORM_RE.fullmatch(cleaned):
        return True
    return False


def _strip_math_wrapper(text):
    """Skini $...$, \\, i \\text{...} da bi ostala gola vrijednost opcije."""
    value = (text or "").strip()
    value = value.replace("\\,", " ").replace("\\;", " ").replace("\\!", "")
    value = re.sub(r"\\text\s*\{([^{}]*)\}", r"\1", value)
    value = value.strip().strip("$").strip()
    return value


_INTEGER_VALUE_RE = re.compile(r"^-?\d+$")
_DECIMAL_VALUE_RE = re.compile(r"^-?\d+[.,]\d+$")
_ORDERED_PAIR_RE = re.compile(r"^\(?\s*-?\d+\s*,\s*-?\d+\s*\)?$|^\(\s*x\s*,\s*y\s*\)\s*=")


def is_fraction_option(text):
    value = _strip_math_wrapper(text)
    if re.fullmatch(r"\\frac\s*\{[^{}]*\}\s*\{[^{}]*\}", value):
        return True
    if re.fullmatch(r"-?\d+\s*/\s*-?\d+", value):
        return True
    # mješoviti broj: 2\frac{1}{3}
    if re.fullmatch(r"-?\d+\s*\\frac\s*\{[^{}]*\}\s*\{[^{}]*\}", value):
        return True
    return False


def is_integer_option(text):
    return bool(_INTEGER_VALUE_RE.fullmatch(_strip_math_wrapper(text)))


def detected_answer_kind(text):
    """Objektivno prepoznaj TIP vrijednosti iz vidljivog teksta — vraća jedan
    od "fraction"/"integer"/"decimal"/"ordered_pair", ili None kad tip nije
    mehanički prepoznatljiv (proza, formula, izraz, oznaka opcije). Koristi se
    da se `answer_kind` provjeri protiv STVARNOG sadržaja, ne protiv statične
    liste po porodici — vidi napomenu uz `validate_task_family`."""
    if is_fraction_option(text):
        return "fraction"
    value = _strip_math_wrapper(text)
    # Decimal PRIJE ordered_pair: "2,5" (bosanski decimalni zarez) inače pogrešno
    # upada u ordered_pair regex jer su zagrade tamo opcionalne.
    if _DECIMAL_VALUE_RE.fullmatch(value):
        return "decimal"
    if is_ordered_pair_option(text):
        return "ordered_pair"
    if is_integer_option(text):
        return "integer"
    return None


def canonical_answer_kind(declared_answer_kind, correct_option_text):
    """SERVER-DERIVED `answer_kind` — vraća (canonical, normalized).

    ŽIVI NALAZ (canary s pravim modelom, lekcija o pravilima djeljivosti,
    prelaz Nivo 1→2): recenzent je vratio ISPRAVAN zadatak — tačna opcija
    „138“, i sve njegove provjere tačne (math_correct, answer_correct,
    marked_option_correct, tests_exact_lesson, difficulty_level_appropriate) —
    ali ga je deklarisao kao `answer_kind="option_label"`, misleći na to da
    učenik bira ponuđenu opciju. Zadatak je pao zatvoreno iako mu matematički
    ni pedagoški ništa nije falilo, potrošivši oba poziva.

    To je TREĆI slučaj iste klase propusta (prva dva su `task_form` i
    `student_must_find`, vidi FamilyContract docstring): opisna oznaka modela
    korištena kao dokaz. Pouka je sada dosljedno primijenjena: kad server tip
    može OBJEKTIVNO izmjeriti iz stvarnog teksta tačne opcije
    (`detected_answer_kind`), ta izmjerena vrijednost je JEDINA istina i
    deklaracija se tiho zamjenjuje — deklaracija ne nosi nijednu informaciju
    koju server već nema, pa može samo proizvesti lažno odbijanje.

    Kad tip NIJE mehanički prepoznatljiv (proza, formula, izraz, prava oznaka
    opcije poput „A“), kanonizacija se NE izvodi i deklaracija ostaje
    netaknuta — ali tada ionako nema objektivne osnove ni za odbijanje.

    Ovo NIKAD ne može sakriti pogrešan odgovor: tip vrijednosti nije tvrdnja
    o tačnosti. Ispravnost tačne opcije i dalje dokazuju nepromijenjeni
    slojevi — mathcheck nad tačnom opcijom i `expected_answer`, jedinstvenost
    i semantička ekvivalencija opcija, vidljivi ugovor porodice
    (required/forbidden), geometrijska notacija, systemcheck i nezavisno
    rješavanje recenzenta."""
    declared = (declared_answer_kind or "").strip()
    detected = detected_answer_kind(correct_option_text)
    if not detected:
        return declared, False
    if declared and declared != detected:
        return detected, True
    return detected, False


def is_ordered_pair_option(text):
    """Cijela opcija je uređeni par (a ne proza koja par spominje)."""
    return is_bare_ordered_pair(text)
