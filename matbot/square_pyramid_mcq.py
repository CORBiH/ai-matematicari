# -*- coding: utf-8 -*-
"""Uski deterministički orakl za MCQ o PRAVILNOJ ČETVOROSTRANOJ PIRAMIDI.

ZAŠTO POSTOJI (živi nalaz N-4, post-deploy provjera 2026-08-16, kandidat
70bb514, oblast „Geometrijska tijela", pitanje q5):

    „Pravilna četvorostrana piramida ima stranicu kvadratne baze $a=10$ cm i
     apotemu $h_a=13$ cm. Kolika je dužina bočne ivice $s$?"
    Ponuđeno: $\\sqrt{219}$, $\\sqrt{194}$, $\\sqrt{169}$, $\\sqrt{269}$ cm.
    Označeno: $\\sqrt{219}$.

Tačno je $s^2 = h_a^2 + (a/2)^2 = 169 + 25 = 194$, dakle $\\sqrt{194}$ — a ta
opcija je BILA ponuđena, samo nije bila označena. $\\sqrt{219} = \\sqrt{169+50}$
dobije se SAMO ako se apotema pogrešno upotrijebi kao VISINA piramide
($s^2 = H^2 + a^2/2$). To je zamjena uloga veličina, ne greška u računu.

ZATO GA NIJEDAN POSTOJEĆI VALIDATOR NIJE MOGAO UHVATITI: `mathcheck` provjerava
lanac jednakosti, a modelov lanac ($13^2+50=219$) je ARITMETIČKI TAČAN;
`geometrycheck` provjerava oznake, a sve oznake su uredne; `mcq_integrity`,
`exactly_one` i orakl sistema nisu primjenjivi na ovaj oblik. Ostalo je
slaganje modela sa samim sobom.

DOKTRINA: kad su iz VIDLJIVOG zadatka poznate stranica baze i tačno jedna od
{H, h_a, s}, server sam izračuna traženu veličinu EGZAKTNO i tek onda ocijeni
sve četiri opcije. Kad ne može dokazati — ćuti.

ODNOSI (pravilna četvorostrana piramida, baza stranice `a`):
    poluosnovica            a/2          (centar baze → sredina ivice baze)
    poludijagonala          a√2/2        (centar baze → tjeme baze)
    h_a^2 = H^2 + (a/2)^2
    s^2   = h_a^2 + (a/2)^2
    s^2   = H^2 + (a√2/2)^2 = H^2 + a^2/2

Sav račun ide nad KVADRATIMA dužina kao `Fraction`, pa iracionalan rezultat
(`\\sqrt{194}`) nikad ne prolazi kroz `float`. Poređenje opcija je poređenje
egzaktnih kvadrata: `5` i `\\sqrt{25}` su ISTA vrijednost, a `\\sqrt{194}` i
`\\sqrt{219}` različite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Optional

from matbot.mathsegments import TEXT as _TEXT_SEGMENT
from matbot.mathsegments import math_contents, tokenize_math

BASE_SIDE, HEIGHT, APOTHEM, LATERAL_EDGE = "a", "H", "h_a", "s"

# Zadatak mora biti o PRAVILNOJ ČETVOROSTRANOJ piramidi — svaki drugi oblik
# (trostrana, šestostrana, zarubljena, kupa, prizma) ovaj orakl ne dira.
_SQUARE_PYRAMID_RE = re.compile(
    r"pravilna\s+(?:četvorostrana|četverostrana|cetvorostrana|cetverostrana)\s+piramida",
    re.IGNORECASE)
_EXCLUDED_SOLID_RE = re.compile(
    r"zarubljen|kupa|kupe|valjak|valjka|prizma|prizme|lopta|lopte|"
    r"trostrana|šestostrana|sestostrana|pet(?:o|ero)strana", re.IGNORECASE)

# `a=10`, `h_a=13`, `h_{a}=13`, `H=5`, `s=12` u matematičkom segmentu.
_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z\\])(?P<sym>h_\{?a\}?|[aHs])\s*=\s*"
    r"(?P<num>\d+(?:[.,]\d+)?)(?![\d.,])")

# Tražena veličina — riječi kojima kurikulum imenuje uloge (data/topics.json:
# „Apotema pravilne piramide", „Primjena Pitagorine teoreme na piramidu").
_TARGET_WORDS = (
    (LATERAL_EDGE, re.compile(r"bočn\w*\s+ivic\w*|bocn\w*\s+ivic\w*", re.IGNORECASE)),
    (APOTHEM, re.compile(r"apotem\w*", re.IGNORECASE)),
    (HEIGHT, re.compile(r"visin\w*\s+piramide|visina\s+tijela", re.IGNORECASE)),
    (BASE_SIDE, re.compile(r"stranic\w*\s+(?:kvadratne\s+)?baze", re.IGNORECASE)),
)
_TARGET_SYMBOLS = ((LATERAL_EDGE, "s"), (APOTHEM, "h_a"), (HEIGHT, "H"),
                   (BASE_SIDE, "a"))

# Opcija kao EGZAKTAN kvadrat: cijeli/decimalni broj, razlomak, ili korijen.
_PLAIN_NUMBER_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_FRAC_RE = re.compile(r"^\\d?frac\s*\{(?P<num>[^{}]+)\}\s*\{(?P<den>[^{}]+)\}$")
_SQRT_RE = re.compile(
    r"^(?P<coef>\d+(?:[.,]\d+)?)?\s*\\sqrt\s*\{(?P<rad>[^{}]+)\}$")


@dataclass(frozen=True)
class PyramidMCQResult:
    applicable: bool
    valid: bool
    reason_code: str = ""
    target: str = ""
    truth_squared: Optional[Fraction] = None
    option_squares: tuple = ()
    correct_indices: tuple = ()


def _literal(raw: str) -> Optional[Fraction]:
    try:
        return Fraction(str(raw).strip().replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None


def _exact_square(expression: str) -> Optional[Fraction]:
    """Kvadrat vrijednosti izraza kao egzaktan `Fraction`, ili None.

    Radi nad KVADRATIMA baš zato što su tražene dužine često iracionalne:
    `\\sqrt{194}` ima kvadrat 194, pa poređenje ostaje u racionalnim brojevima."""
    text = (expression or "").strip()
    if not text:
        return None
    if _PLAIN_NUMBER_RE.match(text):
        value = _literal(text)
        return None if value is None else value * value
    frac = _FRAC_RE.match(text)
    if frac:
        num, den = _literal(frac.group("num")), _literal(frac.group("den"))
        if num is None or den in (None, 0):
            return None
        value = num / den
        return value * value
    root = _SQRT_RE.match(text)
    if root:
        radicand = _literal(root.group("rad"))
        if radicand is None or radicand < 0:
            return None
        coefficient = (_literal(root.group("coef"))
                       if root.group("coef") else Fraction(1))
        if coefficient is None:
            return None
        return coefficient * coefficient * radicand
    return None


def _option_squares(options: tuple):
    """([kvadrat po opciji], ima_li_egzaktan_oblik) ili None kad oblik nije ovaj.

    Mjerna jedinica se skida samo kad je kod SVIH opcija ista — inače „$5$ cm"
    i „$5$ m" ne smiju postati ista vrijednost."""
    squares, units, exact_forms = [], set(), 0
    for option in options:
        tokens = tokenize_math(option or "")
        maths = [content for kind, content in tokens if kind != _TEXT_SEGMENT]
        prose = "".join(content for kind, content in tokens
                        if kind == _TEXT_SEGMENT).strip()
        if len(maths) != 1:
            return None
        body = maths[0]
        # Jedinica smije stajati i UNUTAR matematike (`5\,\text{cm}`).
        body = re.sub(r"\\[,;:!]|\\ ", " ", body)
        body = re.sub(r"\\(?:text|mathrm)\s*\{[^{}]*\}", " ", body)
        inline_unit = ""
        unit_match = re.search(r"\\(?:text|mathrm)\s*\{([^{}]*)\}", maths[0])
        if unit_match:
            inline_unit = unit_match.group(1).strip()
        square = _exact_square(body.strip())
        if square is None:
            return None
        if not _PLAIN_NUMBER_RE.match(body.strip()) or "\\sqrt" in maths[0]:
            exact_forms += 1
        elif _literal(body.strip()) is not None and "," not in body:
            exact_forms += 1          # cijeli broj je takođe egzaktan oblik
        squares.append(square)
        units.add(prose or inline_unit)
    if len(units) != 1:
        return None
    return squares, exact_forms


def _givens(question: str) -> dict:
    """Poznate veličine iz VIDLJIVOG zadatka (nikad iz modelovog rješenja)."""
    found: dict = {}
    for segment in math_contents(tokenize_math(question or "")):
        for match in _ASSIGNMENT_RE.finditer(segment):
            symbol = match.group("sym").replace("{", "").replace("}", "")
            value = _literal(match.group("num"))
            if value is None or value <= 0:
                continue
            role = APOTHEM if symbol.startswith("h_") else symbol
            if role in found and found[role] != value:
                return {}            # protivrječni podaci — ne pogađa se
            found[role] = value
    return found


def _question_sentence(question: str) -> str:
    prose = " ".join(content for kind, content in tokenize_math(question or "")
                     if kind == _TEXT_SEGMENT)
    mark = prose.rfind("?")
    if mark == -1:
        return prose
    start = max(prose.rfind(".", 0, mark), prose.rfind("!", 0, mark))
    return prose[start + 1:mark + 1]


def _target(question: str) -> str:
    """Tražena veličina — SAMO iz pitanja, i samo kad je jednoznačna."""
    sentence = _question_sentence(question)
    hits = {role for role, pattern in _TARGET_WORDS if pattern.search(sentence)}
    if len(hits) != 1:
        return ""
    role = hits.pop()
    # Simbol u pitanju, kad postoji, mora se slagati s riječju.
    symbols = {r for r, sym in _TARGET_SYMBOLS
               if re.search(r"(?<![A-Za-z\\])" + re.escape(sym) + r"(?![A-Za-z_])",
                            question or "")}
    stated = {r for r, sym in _TARGET_SYMBOLS
              if re.search(r"\$[^$]*(?<![A-Za-z\\])" + re.escape(sym)
                           + r"(?![A-Za-z_])[^$]*\$", sentence)}
    if stated and role not in stated:
        return ""
    del symbols
    return role


def _truth_squared(target: str, givens: dict):
    """(kvadrat tražene dužine, kod_greške). Sve egzaktno, nad kvadratima."""
    side = givens.get(BASE_SIDE)
    if side is None:
        return None, ""              # bez stranice baze oblik nije dokaziv
    half_sq = (side / 2) ** 2        # (a/2)^2
    half_diag_sq = side * side / 2   # (a√2/2)^2 = a^2/2
    known = {role: value for role, value in givens.items() if role != BASE_SIDE}
    if len(known) != 1:
        return None, ""
    role, value = next(iter(known.items()))
    if role == target:
        return None, ""
    given_sq = value * value
    if role == APOTHEM:
        squares = {HEIGHT: given_sq - half_sq,
                   LATERAL_EDGE: given_sq + half_sq}
    elif role == HEIGHT:
        squares = {APOTHEM: given_sq + half_sq,
                   LATERAL_EDGE: given_sq + half_diag_sq}
    elif role == LATERAL_EDGE:
        squares = {APOTHEM: given_sq - half_sq,
                   HEIGHT: given_sq - half_diag_sq}
    else:
        return None, ""
    if target not in squares:
        return None, ""
    # Nemoguća geometrija: apotema mora nadmašiti poluosnovicu, bočna ivica
    # poludijagonalu. Takav zadatak nema realno rješenje i ne smije se objaviti.
    if any(value_sq <= 0 for value_sq in squares.values()):
        return None, "impossible_geometry"
    return squares[target], ""


def evaluate_square_pyramid_mcq(question: str,
                                option_texts: Iterable[str]) -> PyramidMCQResult:
    """Ocijeni SAMO brojčani MCQ o pravilnoj četvorostranoj piramidi."""
    options = tuple(option_texts or ())
    text = question or ""
    if len(options) < 2:
        return PyramidMCQResult(False, False)
    if not _SQUARE_PYRAMID_RE.search(text) or _EXCLUDED_SOLID_RE.search(text):
        return PyramidMCQResult(False, False)

    givens = _givens(text)
    target = _target(text)
    if not target or target in givens:
        return PyramidMCQResult(False, False)

    truth_sq, failure = _truth_squared(target, givens)
    if failure:
        return PyramidMCQResult(True, False, failure, target)
    if truth_sq is None:
        return PyramidMCQResult(False, False)

    parsed = _option_squares(options)
    if parsed is None:
        return PyramidMCQResult(False, False)
    squares, exact_forms = parsed
    if not exact_forms:
        # Sve opcije su decimalne aproksimacije: ovaj orakl ne uvodi vlastitu
        # toleranciju, pa ćuti i pušta postojeće provjere.
        return PyramidMCQResult(False, False)

    matching = tuple(index for index, square in enumerate(squares)
                     if square == truth_sq)
    if not matching:
        return PyramidMCQResult(True, False, "no_correct_option", target,
                                truth_sq, tuple(squares), matching)
    if len(matching) > 1:
        return PyramidMCQResult(True, False, "multiple_correct_options", target,
                                truth_sq, tuple(squares), matching)
    return PyramidMCQResult(True, True, "", target, truth_sq, tuple(squares),
                            matching)


def publication_failure(question: str, option_texts: Iterable[str],
                        marked_index: int) -> str:
    """Kod odbijanja objave, ili "" kad orakl ne prigovara / ne primjenjuje se."""
    result = evaluate_square_pyramid_mcq(question, option_texts)
    if not result.applicable:
        return ""
    if not result.valid:
        return result.reason_code
    if marked_index not in result.correct_indices:
        return "marked_option_math_mismatch"
    return ""
