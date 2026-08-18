"""Determinističa provjera odgovora na ZADATAK SA SLIKE — nezavisno od onoga
što je model tvrdio u `reply`.

ZAŠTO POSTOJI (živi nalaz D35-5, poziv 33 kampanje od 35): slika je pokazivala
pravougaonik s $a=8$ cm i $b=5$ cm i tražila površinu; model je vratio
„$P=26\\,\\text{cm}$“ — vrijednost obima, s linearnom jedinicom. Postojeće
provjere to nisu mogle uhvatiti: mathcheck.py poredi članove LANCA jednakosti
(ovdje lanca nema — samo jedna dodjela), a geometrycheck.py provjerava OZNAKE
(a `P` jeste ispravna oznaka za površinu; pogrešna je bila vrijednost).

EKSPLICITNA STANJA UMJESTO PRAZNE LISTE (živi nalaz D35T-2, pozivi 12 i 13
kampanje od 14): ranija verzija je vraćala listu problema, pa je PRAZNA LISTA
značila DVIJE potpuno različite stvari — „provjereno i ispravno“ i „nisam imao
šta provjeriti“. Model je u polje s izrazom stavljao naslov zadatka
(„Rijesi jednacinu:“), provjera je tiho preskakala, a pozivalac je prazan
rezultat čitao kao uspjeh. Ponovna simulacija je pokazala da bi na taj način i
POGREŠAN odgovor („$x=99$“ za $3x+5=20$) bio objavljen.

Zato svaka provjera sada vraća ImageVerification s razdvojenim poljima:
  • supported — porodica zadatka je na listi determinističkih provjera,
  • engaged   — provjera je STVARNO imala upotrebljiv dokaz i izvršila se,
  • verified  — račun se slaže s nezavisno izračunatom istinom.
Za podržanu porodicu objavljivanje je dozvoljeno SAMO uz supported ∧ engaged ∧
verified. „Nije se izvršilo“ nikad više ne znači „provjereno“.

ŠTA OVAJ MODUL JESTE: uzak provjeravač za nekoliko prepoznatih porodica. Iz
strukturnog izlaza (matbot/schema.py: QuickImageTurnOutput) uzima vrijednosti
koje je model prijavio kao VIDLJIVE na slici, sam izračuna tačan rezultat,
izvuče rezultat koji je model napisao učeniku i uporedi ih.

ŠTA OVAJ MODUL NIJE: razumijevanje slike. Server sliku nikad ne vidi — vidi
samo ono što je model prijavio da je na njoj. Ovo NIJE dokaz da je model
ispravno pročitao sliku; dokazuje samo da je račun DOSLJEDAN s prijavljenim
dokazom. Protiv pogrešno pročitane vrijednosti radi stroga kapija čitljivosti u
matbot/quick.py, ne ovaj modul.

Istina za poređenje NIKAD se ne izvodi iz javnog odgovora modela, iz
predloženog rezultata, iz očekivanih udžbeničkih obrazaca ni iz toga što neka
vrijednost „čini jednačinu rješivom“ — isključivo iz validiranog strukturnog
vizuelnog dokaza.
"""
import re
from dataclasses import dataclass

from matbot.mathcheck import _MathError, _Unsupported, evaluate_candidates, math_segments

# Relativna tolerancija za poređenje serverski izračunate istine s brojem koji
# je model napisao. Namjerno mala: ove porodice daju egzaktne školske brojeve.
_REL_TOL = 1e-6

# Jedinice dužine koje umijemo porediti. Miješane jedinice (npr. $a$ u cm, $b$ u
# m) se NE preračunavaju nego odbijaju — u osnovnoj školi zadatak s pomiješanim
# jedinicama je gotovo uvijek pogrešno pročitan.
_LENGTH_UNITS = ("mm", "cm", "dm", "m", "km")

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
_ASSIGNMENT_RE = re.compile(r"([A-Za-z][A-Za-z_0-9]{0,3})\s*=\s*(-?\d+(?:[.,]\d+)?)")
_UNIT_RE = re.compile(
    r"\\(?:text|mathrm)\s*\{\s*(" + "|".join(_LENGTH_UNITS) + r")\s*\}\s*(\^\s*\{?\s*([23])\s*\}?)?"
    r"|(?<![A-Za-z])(" + "|".join(_LENGTH_UNITS) + r")\s*(\^\s*\{?\s*([23])\s*\}?)?(?![A-Za-z])"
)

AREA_TASKS = ("rectangle_area", "square_area")
PERIMETER_TASKS = ("rectangle_perimeter", "square_perimeter")
SQUARE_TASKS = ("square_area", "square_perimeter")
EXPRESSION_TASKS = ("arithmetic", "fraction_expression")
EQUATION_TASKS = ("linear_equation",)

#: Porodice za koje postoji NEZAVISNA deterministička provjera. Sve ostalo je
#: nepodržano i nikad se ne smije prikazati kao „provjereno“.
SUPPORTED_TASK_TYPES = frozenset(
    AREA_TASKS + PERIMETER_TASKS + EXPRESSION_TASKS + EQUATION_TASKS
)

# Riječi kojima počinje NASLOV zadatka, ne sam izraz. Model je u živom nalazu
# baš njih stavljao u polje s izrazom.
_HEADING_WORDS = (
    "rijesi", "riješi", "izracunaj", "izračunaj", "zadatak", "odredi",
    "izracunati", "izračunati", "koliko", "nadji", "nađi", "napisi", "napiši",
)


@dataclass(frozen=True)
class ImageVerification:
    """Ishod determinističe provjere jednog odgovora sa slike.

    supported — porodica je na listi determinističkih provjera;
    engaged   — provjera je imala upotrebljiv dokaz i stvarno se izvršila;
    verified  — nezavisno izračunata istina se slaže s predloženim odgovorom;
    code      — ograničen dijagnostički kod (bez sadržaja slike/odgovora).
    """

    supported: bool
    engaged: bool
    verified: bool
    code: str

    @property
    def may_publish(self):
        """Za PODRŽANU porodicu objavljivanje traži i izvršenu i uspješnu
        provjeru. Nepodržana porodica ne prolazi ovuda — o njoj odlučuje
        pozivalac (kapija čitljivosti + opšte provjere)."""
        return self.supported and self.engaged and self.verified


def unsupported(code="task_family_not_supported"):
    return ImageVerification(supported=False, engaged=False, verified=False, code=code)


def not_engaged(code):
    return ImageVerification(supported=True, engaged=False, verified=False, code=code)


def failed(code):
    return ImageVerification(supported=True, engaged=True, verified=False, code=code)


def passed(code="verified"):
    return ImageVerification(supported=True, engaged=True, verified=True, code=code)


def _to_number(token):
    try:
        return float(token.replace(",", "."))
    except (AttributeError, ValueError):
        return None


def looks_like_heading(text):
    """True kad polje sadrži naslov/instrukciju umjesto samog izraza."""
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    return any(re.match(r"^\W*" + word + r"\b", lowered) for word in _HEADING_WORDS)


# ---------------------------------------------------------------------------
# NORMALIZACIJA DOKAZNOG ZAPISA (živi audit 2026-08-18, turnovi B-04 i C-09)
# ---------------------------------------------------------------------------
# Model isti sadržaj zapisuje u više STILOVA, a stil je tiho gasio provjeru:
#
#   B-04  `$(x-1\frac{1}{6})+3,2=1\frac{2}{5}$`   → `image_equation_unparsable`
#   C-09  `(4 5/8 + 2 2/5) - (3 1/2 + 1 1/6)`     → `image_math_source_unparsable`
#
# Ista slika kao C-09 je u turnu B-01 stigla kao gol LaTeX i uredno se
# provjerila. Dakle porodica JESTE pokrivena — otkazivala je isključivo
# interpunkcija zapisa. Posljedica je bila najgora moguća za ovaj modul: kod
# `unparsable` znači „nisam se izvršio“, pa je po doktrini odgovor legitimno
# išao dalje NEPROVJEREN, a `verify_image_answer` je izgledao kao da radi.
#
# Obje normalizacije su DOKAZIVE i uske: skidanje `$…$` granica ne mijenja
# vrijednost izraza, a `a b/c` je jednoznačan školski zapis mješovitog broja.
# Ništa se ne pogađa — nepoznat zapis i dalje pada na `unparsable`.
_MATH_DELIMITER_RE = re.compile(r"^\s*\$\$?(?P<body>.*?)\$\$?\s*$", re.DOTALL)
#: `4 5/8` → `(4+5/8)`. Traži se CIO oblik cijeli-razmak-razlomak; `4 + 5/8`
#: nema poklapanja (operator razdvaja), pa se ispravan zapis nikad ne dira.
_ASCII_MIXED_NUMBER_RE = re.compile(
    r"(?<![\d./])(\d+)\s+(\d+)\s*/\s*(\d+)(?![\d./])")


def normalize_visible_math(value):
    """Skini `$…$` granice i prevedi školski `a b/c` u parsabilan oblik."""
    text = (value or "").strip()
    if not text:
        return text
    match = _MATH_DELIMITER_RE.match(text)
    if match:
        text = match.group("body").strip()
    return _ASCII_MIXED_NUMBER_RE.sub(r"(\1+\2/\3)", text)


def _visible_math(out):
    """Dokazni matematički zapis sa slike, ili None kad ga nema/nije upotrebljiv.

    NIKAD ne pada nazad na visible_problem_text: to je slobodan opis koji je u
    živom nalazu sadržavao naslov zadatka, pa bi takav fallback vratio upravo
    onu tihu rupu zbog koje ovo polje i postoji."""
    value = (getattr(out, "visible_math", "") or "").strip()
    if not value:
        return None
    if looks_like_heading(value):
        return None
    return normalize_visible_math(value)


def _visible_lengths(visible_values):
    """Vrati (duzine, jedinica) ili (None, None) kad podaci nisu upotrebljivi."""
    lengths = []
    units = set()
    for item in visible_values:
        number = _to_number(item.value)
        if number is None or number <= 0:
            return None, None
        unit = (item.unit or "").strip().lower()
        if unit not in _LENGTH_UNITS:
            return None, None
        units.add(unit)
        lengths.append(number)
    if len(units) != 1:
        return None, None
    return lengths, units.pop()


def _segment_value(segment):
    """Brojčana vrijednost JEDNOG matematičkog segmenta, ili None.

    Uzima se DESNA strana posljednje jednakosti („$O=2(a+b)=26$“ → „26“), pa se
    izraz vrednuje istim AST evaluatorom iz mathcheck.py (bijela lista, nikad
    eval). Zato prolazi i odgovor zapisan kao razlomak („$\\frac{5}{6}$“)."""
    expression = segment.rsplit("=", 1)[-1].strip() if "=" in segment else segment.strip()
    if not expression:
        return None
    try:
        candidates = evaluate_candidates(expression)
    except (_Unsupported, _MathError):
        candidates = []
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return None  # višeznačno (npr. π obje konvencije) — ne pogađaj

    numbers = [_to_number(m.group(0)) for m in _NUMBER_RE.finditer(expression)]
    numbers = [n for n in numbers if n is not None]
    return numbers[0] if len(numbers) == 1 else None


def claimed_result(reply):
    """Vrati (vrijednost, jedinica, eksponent_jedinice) iz teksta koji je model
    napisao učeniku, ili (None, None, None). Uzima se POSLJEDNJI segment koji
    daje vrijednost — zaključak je na kraju odgovora."""
    value = None
    for segment in math_segments(reply or ""):
        candidate = _segment_value(segment)
        if candidate is not None:
            value = candidate
    if value is None:
        return None, None, None

    unit_match = _UNIT_RE.search(reply or "")
    if unit_match is None:
        return value, "", 0
    unit = (unit_match.group(1) or unit_match.group(4) or "").lower()
    exponent = unit_match.group(3) or unit_match.group(6)
    return value, unit, int(exponent) if exponent else 1


def _compare(expected, value):
    return abs(expected - value) <= max(_REL_TOL * abs(expected), _REL_TOL)


# ---------------------------------------------------------------------------
# Pravougaonik / kvadrat — istina iz visible_values (strukturni vizuelni dokaz)
# ---------------------------------------------------------------------------
def _check_rectangle(out):
    lengths, unit = _visible_lengths(out.visible_values)
    if lengths is None:
        return not_engaged("image_rectangle_values_unusable")

    is_square = out.task_type in SQUARE_TASKS
    if is_square:
        if len(lengths) != 1:
            return not_engaged("image_rectangle_wrong_side_count")
        side_a = side_b = lengths[0]
    else:
        if len(lengths) != 2:
            return not_engaged("image_rectangle_wrong_side_count")
        side_a, side_b = lengths

    wants_area = out.task_type in AREA_TASKS
    if wants_area:
        if out.requested_quantity != "area":
            return failed("image_requested_quantity_mismatch")
        expected, expected_exponent = side_a * side_b, 2
    else:
        if out.requested_quantity != "perimeter":
            return failed("image_requested_quantity_mismatch")
        expected, expected_exponent = 2 * (side_a + side_b), 1

    value, claimed_unit, exponent = claimed_result(out.reply)
    if value is None:
        return not_engaged("image_result_not_parsable")
    if not _compare(expected, value):
        return failed("image_rectangle_value_mismatch")
    if claimed_unit and claimed_unit != unit:
        return failed("image_unit_mismatch")
    if exponent != expected_exponent:
        return failed("image_unit_exponent_mismatch")
    return passed()


# ---------------------------------------------------------------------------
# Aritmetika / razlomci — istina iz visible_math
# ---------------------------------------------------------------------------
# ŠKOLSKI OBLIK „a/b od n“ (živa dijagnostika Sol migracije, slučaj „2/3 od
# 27“): udžbenik dio cjeline piše riječju „od“, model to VJERNO prepiše u
# visible_math, a AST evaluator prozu ne razumije — provjera se nije izvršila
# i tačan odgovor je padao. Kurikularno „od“ ovdje znači TAČNO množenje.
# Namjerno usko: normalizuje se ISKLJUČIVO cio zapis oblika
# `<izraz> od <broj>` (jedno „od“, desno gol broj) → `(<izraz>)*(<broj>)`;
# lijeva strana i dalje ide kroz postojeći AST s bijelom listom — nikakva
# opšta obrada prirodnog jezika.
_OD_FORM_RE = re.compile(
    r"^\s*(?P<expr>[^\s].*?)\s+od\s+(?P<n>-?\d+(?:[.,]\d+)?)\s*$",
    re.IGNORECASE,
)


def _normalize_od_form(source):
    match = _OD_FORM_RE.match(source or "")
    if match is None or " od " in match.group("expr").lower():
        return source
    return "(%s)*(%s)" % (match.group("expr"), match.group("n"))


def _check_expression(out):
    source = _visible_math(out)
    if source is None:
        return not_engaged("image_math_source_missing")
    source = _normalize_od_form(source)
    try:
        candidates = evaluate_candidates(source.rstrip("=").strip())
    except (_Unsupported, _MathError):
        return not_engaged("image_math_source_unparsable")
    if len(candidates) != 1:
        return not_engaged("image_math_source_ambiguous")

    value, _unit, _exponent = claimed_result(out.reply)
    if value is None:
        return not_engaged("image_result_not_parsable")
    if not _compare(candidates[0], value):
        return failed("image_expression_value_mismatch")
    return passed()


# ---------------------------------------------------------------------------
# Prosta linearna jednačina — istina iz visible_math, provjera uvrštavanjem
# ---------------------------------------------------------------------------
_EQUATION_UNKNOWN_RE = re.compile(r"(?<![A-Za-z])([a-z])(?![A-Za-z])")


def _check_linear_equation(out):
    source = _visible_math(out)
    if source is None:
        return not_engaged("image_math_source_missing")

    sides = source.split("=")
    if len(sides) != 2 or not all(side.strip() for side in sides):
        return not_engaged("image_equation_missing_equality")

    unknowns = set(_EQUATION_UNKNOWN_RE.findall(source))
    if len(unknowns) != 1:
        return not_engaged("image_equation_unknown_ambiguous")
    unknown = unknowns.pop()

    value, _unit, _exponent = claimed_result(out.reply)
    if value is None:
        return not_engaged("image_result_not_parsable")

    substituted = [
        re.sub(r"(?<![A-Za-z])" + unknown + r"(?![A-Za-z])", "(%r)" % value, side)
        for side in sides
    ]
    try:
        left = evaluate_candidates(substituted[0])
        right = evaluate_candidates(substituted[1])
    except (_Unsupported, _MathError):
        return not_engaged("image_equation_unparsable")
    if not (left and right):
        return not_engaged("image_equation_unparsable")
    if any(_compare(a, b) for a in left for b in right):
        return passed()
    return failed("image_equation_substitution_failed")


def verify_image_answer(out):
    """Glavna ulazna tačka. Vrati ImageVerification. Nikad ne mijenja tekst,
    nikad ne poziva model."""
    task_type = out.task_type
    if task_type in AREA_TASKS + PERIMETER_TASKS:
        return _check_rectangle(out)
    if task_type in EXPRESSION_TASKS:
        return _check_expression(out)
    if task_type in EQUATION_TASKS:
        return _check_linear_equation(out)
    return unsupported()
