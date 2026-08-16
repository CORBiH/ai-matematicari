# -*- coding: utf-8 -*-
"""Uski deterministički orakl za ORTOGONALNU PROJEKCIJU DUŽI NA RAVAN.

ZAŠTO POSTOJI (živi nalaz N-6, fokusirana kampanja uz N-4, oblast 9-02
„Tačka, prava i ravan", lekcija „Ortogonalna projekcija duži na ravan"):

    „Tačke $A$ i $B$ nalaze se s iste strane ravni $\\alpha$. Njihove
     udaljenosti od ravni su $9$ cm i $12$ cm, a duž $AB$ ima dužinu $15$ cm.
     Kolika je dužina ortogonalne projekcije duži $AB$ na ravan $\\alpha$?"
    Ponuđeno: $9$ / $18$ / $6$ / $12$ cm. Označeno: $12$ cm.

Tačno je: tačke su S ISTE STRANE, pa je normalna razlika $\\Delta h=|12-9|=3$, a
projekcija $p=\\sqrt{15^2-3^2}=\\sqrt{216}=6\\sqrt6\\approx14{,}70$ — dakle
NIJEDNA ponuđena opcija nije tačna. Označenih $12$ dobije se samo ako se $9$
uzme KAO normalna razlika ($\\sqrt{225-81}$), tj. kao da jedna tačka leži u
ravni.

ZAŠTO GA POSTOJEĆI VALIDATORI NISU MOGLI UHVATITI: modelov lanac
$225-81=144$ je ARITMETIČKI TAČAN, pa `mathcheck` nema šta prijaviti; oznake su
uredne, pa ni `geometrycheck`; `mcq_integrity`, `exactly_one`, orakl sistema i
orakl piramide nisu primjenjivi. Greška je u ODABIRU normalne razlike — dakle
semantička, kao i N-4.

GEOMETRIJA: duž $AB$ se rastavlja na komponentu U RAVNI (to je upravo dužina
ortogonalne projekcije $p$) i komponentu NORMALNU na ravan ($\\Delta h$):

    L^2 = p^2 + \\Delta h^2

    ista strana      ->  \\Delta h = |d_A - d_B|
    suprotne strane  ->  \\Delta h = d_A + d_B

Ta razlika je KOREKTNOSNO KRITIČNA i nikad se ne pogađa: ako zadatak ne kaže
izričito s koje su strane, orakl ćuti.

Sav račun ide nad KVADRATIMA dužina kao `Fraction` (projekcija je često
iracionalna), a poređenje opcija koristi ISTI egzaktni čitač kao orakl piramide
(`matbot/square_pyramid_mcq.py`), pa su `\\sqrt{216}` i `6\\sqrt6` ista
vrijednost.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Optional

from matbot.mathsegments import TEXT as _TEXT_SEGMENT
from matbot.mathsegments import tokenize_math
# Namjerno se DIJELI egzaktni čitač opcija s oraklom piramide — isti posao,
# jedan izvor istine (isti obrazac kao `linear_system_mcq` koji koristi
# `mcq_integrity._option_numeric_value`).
from matbot.square_pyramid_mcq import _option_squares

PROJECTION, SEGMENT, NORMAL_GAP = "p", "L", "dh"

_PLANE_RE = re.compile(r"\bravn(?:i|u|a|om)\b|\bravan\b", re.IGNORECASE)
_PROJECTION_RE = re.compile(r"projekcij", re.IGNORECASE)
# Porodica se prepoznaje po DUŽI i njenoj projekciji; projekcija TAČKE na ravan
# je drugi (trivijalan) oblik i ne dira se.
_SEGMENT_WORD_RE = re.compile(r"\bduž(?:i|u|ima)?\b", re.IGNORECASE)

_SAME_SIDE_RE = re.compile(
    r"s[ae]?\s+iste\s+strane|iste\s+strane\s+ravn|istoj\s+strani", re.IGNORECASE)
_OPPOSITE_SIDE_RE = re.compile(
    r"s[ae]?\s+(?:različitih|razlicitih|suprotnih)\s+strana|"
    r"(?:različitim|razlicitim|suprotnim)\s+stranama|"
    r"(?:različite|razlicite|suprotne)\s+strane\s+ravn", re.IGNORECASE)

_NUM = r"\$?\s*(\d+(?:[.,]\d+)?)\s*\$?"
_UNIT = r"(?:\s*\\?,?\s*(?:\\text\{)?\s*([a-zA-Z]{1,3})\s*\}?)?"

# „udaljenosti od ravni su $9$ cm i $12$ cm"
_DISTANCES_RE = re.compile(
    r"(?:udaljenost|rastojanj)\w*\s+od\s+ravni\s+(?:su|iznose|jesu)\s*"
    + _NUM + _UNIT + r"\s*(?:cm|m|dm|mm)?\s*i\s*" + _NUM + _UNIT,
    re.IGNORECASE)
# „duž $AB$ ima dužinu $15$ cm" / „duž $AB$ duga je $15$ cm" / „$AB=15$"
_SEGMENT_LEN_RE = re.compile(
    r"duž\s*\$?\s*[A-Z]{2}\s*\$?[^.?!]{0,40}?"
    r"(?:dužinu|dužine|duga\s+je|duga|iznosi)\s*" + _NUM + _UNIT,
    re.IGNORECASE)
_SEGMENT_EQ_RE = re.compile(r"\$\s*\|?\s*[A-Z]{2}\s*\|?\s*=\s*(\d+(?:[.,]\d+)?)\s*\$")
# „projekcija … ima dužinu $12$ cm"
_PROJECTION_LEN_RE = re.compile(
    r"projekcij\w*[^.?!]{0,60}?(?:dužinu|dužine|duga\s+je|duga|iznosi)\s*"
    + _NUM + _UNIT, re.IGNORECASE)


@dataclass(frozen=True)
class ProjectionMCQResult:
    applicable: bool
    valid: bool
    reason_code: str = ""
    target: str = ""
    truth_squared: Optional[Fraction] = None
    option_squares: tuple = ()
    correct_indices: tuple = ()


def _literal(raw) -> Optional[Fraction]:
    try:
        return Fraction(str(raw).strip().replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None


def _prose(question: str) -> str:
    return " ".join(content for kind, content in tokenize_math(question or "")
                    if kind == _TEXT_SEGMENT)


def _flat(question: str) -> str:
    """Zadatak s matematikom „razmotanom" u tekst, radi fraznog čitanja."""
    parts = []
    for kind, content in tokenize_math(question or ""):
        parts.append(content if kind == _TEXT_SEGMENT else f"${content}$")
    return "".join(parts)


def _side_relation(question: str) -> str:
    """'same' / 'opposite' / '' — nikad se ne pogađa."""
    text = _prose(question)
    same = bool(_SAME_SIDE_RE.search(text))
    opposite = bool(_OPPOSITE_SIDE_RE.search(text))
    if same and not opposite:
        return "same"
    if opposite and not same:
        return "opposite"
    return ""


def _units_consistent(*unit_groups) -> bool:
    seen = {unit for unit in unit_groups if unit}
    return len(seen) <= 1


def _givens(question: str):
    """({role: Fraction}, jedinica) iz VIDLJIVOG zadatka, ili ({}, '')."""
    flat = _flat(question)
    found: dict = {}
    units = []

    distances = _DISTANCES_RE.search(flat)
    if distances:
        first, second = _literal(distances.group(1)), _literal(distances.group(3))
        if first is None or second is None or first < 0 or second < 0:
            return {}, ""
        found["d"] = (first, second)
        units.extend([distances.group(2), distances.group(4)])

    # PROJEKCIJA SE ČITA PRVA I NJEN SPAN SE UKLANJA. Bez toga bi „projekcija
    # duži $AB$ … ima dužinu $8$ cm“ pogodila i obrazac za dužinu SAME duži,
    # pa bi $L$ i $p$ dobili istu vrijednost.
    projection = _PROJECTION_LEN_RE.search(flat)
    remainder = flat
    if projection:
        value = _literal(projection.group(1))
        if value is not None and value >= 0:
            found[PROJECTION] = value
            units.append(projection.group(2))
        remainder = flat[:projection.start()] + " " + flat[projection.end():]

    segment = _SEGMENT_LEN_RE.search(remainder)
    if segment:
        value = _literal(segment.group(1))
        if value is not None and value > 0:
            found[SEGMENT] = value
            units.append(segment.group(2))
    elif _SEGMENT_EQ_RE.search(remainder):
        value = _literal(_SEGMENT_EQ_RE.search(remainder).group(1))
        if value is not None and value > 0:
            found[SEGMENT] = value

    if not _units_consistent(*units):
        return {}, ""
    unit = next((u for u in units if u), "")
    return found, unit


def _target(question: str) -> str:
    """Tražena veličina — samo iz rečenice s upitnikom, i samo jednoznačna."""
    prose = _prose(question)
    mark = prose.rfind("?")
    sentence = prose if mark == -1 else prose[
        max(prose.rfind(".", 0, mark), prose.rfind("!", 0, mark)) + 1:mark + 1]
    asks_projection = bool(_PROJECTION_RE.search(sentence))
    asks_gap = bool(re.search(r"razlik\w*\s+udaljenost|normaln\w*\s+razlik",
                              sentence, re.IGNORECASE))
    asks_segment = bool(re.search(r"dužin\w*\s+duži|duljin\w*\s+duži",
                                  sentence, re.IGNORECASE)) and not asks_projection
    hits = [role for role, flag in ((PROJECTION, asks_projection),
                                    (NORMAL_GAP, asks_gap),
                                    (SEGMENT, asks_segment)) if flag]
    return hits[0] if len(hits) == 1 else ""


def _normal_gap(distances, side) -> Optional[Fraction]:
    first, second = distances
    if side == "same":
        return abs(first - second)
    if side == "opposite":
        return first + second
    return None


def _truth_squared(target, givens, side):
    """(kvadrat tražene dužine, kod_greške). Sve egzaktno."""
    distances = givens.get("d")
    length = givens.get(SEGMENT)
    projection = givens.get(PROJECTION)

    if target == PROJECTION:
        if distances is None or length is None:
            return None, ""
        gap = _normal_gap(distances, side)
        if gap is None:
            return None, ""
        value = length * length - gap * gap
        if value < 0:
            return None, "impossible_geometry"
        return value, ""
    if target == SEGMENT:
        if distances is None or projection is None:
            return None, ""
        gap = _normal_gap(distances, side)
        if gap is None:
            return None, ""
        return projection * projection + gap * gap, ""
    if target == NORMAL_GAP:
        if length is None or projection is None:
            return None, ""
        value = length * length - projection * projection
        if value < 0:
            return None, "impossible_geometry"
        return value, ""
    return None, ""


def evaluate_projection_mcq(question: str,
                            option_texts: Iterable[str]) -> ProjectionMCQResult:
    """Ocijeni SAMO MCQ o ortogonalnoj projekciji duži na ravan."""
    options = tuple(option_texts or ())
    text = question or ""
    if len(options) < 2:
        return ProjectionMCQResult(False, False)
    if not (_PLANE_RE.search(text) and _PROJECTION_RE.search(text)
            and _SEGMENT_WORD_RE.search(text)):
        return ProjectionMCQResult(False, False)

    target = _target(text)
    if not target:
        return ProjectionMCQResult(False, False)
    givens, _unit = _givens(text)
    if not givens:
        return ProjectionMCQResult(False, False)
    if target == PROJECTION and PROJECTION in givens:
        return ProjectionMCQResult(False, False)

    side = _side_relation(text)
    truth_sq, failure = _truth_squared(target, givens, side)
    if failure:
        return ProjectionMCQResult(True, False, failure, target)
    if truth_sq is None:
        return ProjectionMCQResult(False, False)

    parsed = _option_squares(options)
    if parsed is None:
        return ProjectionMCQResult(False, False)
    squares, exact_forms = parsed
    if not exact_forms:
        return ProjectionMCQResult(False, False)

    matching = tuple(index for index, square in enumerate(squares)
                     if square == truth_sq)
    if not matching:
        return ProjectionMCQResult(True, False, "no_correct_option", target,
                                   truth_sq, tuple(squares), matching)
    if len(matching) > 1:
        return ProjectionMCQResult(True, False, "multiple_correct_options",
                                   target, truth_sq, tuple(squares), matching)
    return ProjectionMCQResult(True, True, "", target, truth_sq, tuple(squares),
                               matching)


def publication_failure(question: str, option_texts: Iterable[str],
                        marked_index: int) -> str:
    """Kod odbijanja objave, ili "" kad orakl ne prigovara / ne primjenjuje se."""
    result = evaluate_projection_mcq(question, option_texts)
    if not result.applicable:
        return ""
    if not result.valid:
        return result.reason_code
    if marked_index not in result.correct_indices:
        return "marked_option_math_mismatch"
    return ""
