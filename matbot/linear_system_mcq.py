# -*- coding: utf-8 -*-
"""Uski deterministički orakl za MCQ nad SISTEMOM DVIJE LINEARNE JEDNAČINE.

ZAŠTO POSTOJI (živi nalaz P0-1, finalna prijemna kampanja 2026-08-16,
kandidat e767cac, test K5 pitanje q5):

    „Cijena jedne sveske je $x$ KM, a cijena jedne olovke je $y$ KM. Za $2$
     sveske i $3$ olovke plaćeno je $9$ KM, a za $4$ sveske i $1$ olovku
     plaćeno je $11$ KM. Koliko iznosi cijena jedne sveske?“
     Ponuđeno: $4$ KM, $3$ KM, $1$ KM, $2$ KM — označeno $2$ KM.

Tačno je $x=\\frac{12}{5}$ (i $y=\\frac{7}{5}$), pa NIJEDNA opcija nije tačna, a
označena je pogrešna. Objavljeno je jer ga nijedan validator nije umio riješiti:
`mcq_integrity.publication_failure` je vratio prazno uz `applicable=False`
(djeljivost / direktan račun / poređenje / jednačina s JEDNOM nepoznatom ne
pokrivaju sistem), a `exactly_one` po konstrukciji ne dira pitanja koja traže
konkretan rezultat. Ostala je samo provjera `expected_answer == označena
opcija` — dosljednost modela sa samim sobom, nikad dokaz istine.

DOKTRINA: za ovu porodicu objava NE SMIJE zavisiti od modelove tvrdnje. Server
sam riješi sistem egzaktno (`Fraction`, bez ijednog `float`) i tek onda ocijeni
SVE ČETIRI opcije. Kad ne može dokazati — ćuti ili pada zatvoreno; popravka
smije zamijeniti slot provjerljivim pitanjem.

TRI ODLUKE KOJE ČUVAJU I TAČNOST I DOSTUPNOST:

1. ODAKLE JEDNAČINE. Iz zadatka se uzimaju SVE (uključujući dati podatak poput
   `$x=3$`). Iz modelovog `solution` uzimaju se SAMO jednačine s DVIJE
   nepoznate, i to samo ako je svaki koeficijent i slobodni član potvrđen
   brojem koji stvarno stoji u zadatku. Modelova tvrdnja o REZULTATU (`$x=2$`)
   nikad nije ulaz — nju upravo provjeravamo.

2. KAD SE ORAKL UOPŠTE JAVLJA. Traži se bar jedna SPREGNUTA jednačina (dvije
   nepoznate u istoj jednačini). Zadatak koji samo imenuje `$a=3$` i `$b=4$` pa
   pita za površinu nije ovaj oblik i orakl ćuti — inače bi obarao ispravna
   geometrijska pitanja koja traže IZVEDENU veličinu.

3. KOJA JE NEPOZNATA TRAŽENA. Ne pogađa se iz proze: zadatak sam veže pojam za
   slovo („Cijena jedne sveske je $x$ KM“), pa se traženo slovo dobija
   DOSLOVNIM poklapanjem te fraze s pitanjem. Ako se sistem dokaže, a traženo
   slovo se ne može vezati, pitanje pada zatvoreno (`system_target_unresolved`)
   umjesto da se objavi neprovjereno.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import combinations
from typing import Iterable, Optional

from matbot.mathsegments import TEXT as _TEXT_SEGMENT
from matbot.mathsegments import tokenize_math, math_contents
from matbot.mcq_integrity import _option_numeric_value

# Jedan član linearnog izraza: [znak] [broj] [nepoznata]. Namjerno BEZ zagrada,
# stepena, korijena i implicitnog množenja — svaki neprepoznat zapis obara
# cijelo čitanje (fail closed), nikad se ne pogađa.
_TERM_RE = re.compile(
    r"(?P<sign>[+-])?\s*"
    r"(?:(?P<num>\d+(?:[.,]\d+)?)\s*(?:\\cdot|·|\*)?\s*)?"
    r"(?P<var>[a-zA-Z])?"
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_UNSUPPORTED_RE = re.compile(r"\\frac|\\sqrt|\\left|\\right|[\^()\[\]{}<>≤≥]|\\le|\\ge")
# „<fraza> je $x$“ — zadatak sam veže pojam za slovo.
_DECLARATION_RE = re.compile(
    r"(?P<phrase>[^.,;!?]{3,60}?)\s+(?:je|iznosi)\s+\$(?P<var>[a-zA-Z])\$")

VARIABLE_COUNT = 2


@dataclass(frozen=True)
class LinearEquation:
    """`sum(coeffs[v] * v) = constant` — svi članovi egzaktni."""

    coeffs: dict = field(default_factory=dict)
    constant: Fraction = Fraction(0)

    def numbers(self) -> tuple:
        return tuple(abs(v) for v in self.coeffs.values()) + (abs(self.constant),)

    def satisfied_by(self, values: dict) -> bool:
        total = sum(coeff * values[name] for name, coeff in self.coeffs.items())
        return total == self.constant


@dataclass(frozen=True)
class SystemMCQResult:
    applicable: bool
    valid: bool
    reason_code: str = ""
    solution: tuple = ()          # ((ime, Fraction), …)
    target: str = ""
    option_values: tuple = ()
    correct_indices: tuple = ()


def _literal(raw: str) -> Optional[Fraction]:
    try:
        return Fraction(raw.replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None


def _side_terms(text: str) -> Optional[tuple]:
    """Jedna strana jednačine → ({nepoznata: koeficijent}, slobodni član)."""
    text = (text or "").strip()
    if not text or _UNSUPPORTED_RE.search(text):
        return None
    coeffs: dict = {}
    constant = Fraction(0)
    position = 0
    first = True
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = _TERM_RE.match(text, position)
        if match is None or match.end() == position:
            return None
        if not first and not match.group("sign"):
            return None            # dva člana bez znaka između — dvosmisleno
        if not match.group("num") and not match.group("var"):
            return None
        value = Fraction(1) if match.group("num") is None else _literal(match.group("num"))
        if value is None:
            return None
        if match.group("sign") == "-":
            value = -value
        letter = match.group("var")
        if letter:
            coeffs[letter] = coeffs.get(letter, Fraction(0)) + value
        else:
            constant += value
        position = match.end()
        first = False
    return coeffs, constant


def _parse_equation(segment: str) -> Optional[LinearEquation]:
    """`2x+3y=9` → LinearEquation. Sve što nije čista linearna jednakost → None."""
    segment = (segment or "").strip()
    if segment.count("=") != 1 or _UNSUPPORTED_RE.search(segment):
        return None
    left_raw, right_raw = segment.split("=")
    left = _side_terms(left_raw)
    right = _side_terms(right_raw)
    if left is None or right is None:
        return None
    coeffs: dict = dict(left[0])
    for name, value in right[0].items():
        coeffs[name] = coeffs.get(name, Fraction(0)) - value
    coeffs = {name: value for name, value in coeffs.items() if value != 0}
    if not coeffs:
        return None
    return LinearEquation(coeffs=coeffs, constant=right[1] - left[1])


def _equations_in(text: str) -> list:
    """Sve čiste linearne jednačine iz matematičkih segmenata teksta."""
    found = []
    for segment in math_contents(tokenize_math(text or "")):
        for part in re.split(r"\s*(?:,|;|\s+i\s+|\\quad|\\;)\s*", segment):
            equation = _parse_equation(part)
            if equation is not None:
                found.append(equation)
    return found


def _stem_numbers(text: str) -> set:
    return {Fraction(raw.replace(",", ".")) for raw in _NUMBER_RE.findall(text or "")}


def _corroborated(equation: LinearEquation, stem_values: set) -> bool:
    """Svaki koeficijent/slobodni član mora stvarno stajati u zadatku."""
    return all(value in stem_values for value in equation.numbers())


def _usable_equations(question: str, solution_text: str) -> list:
    """Jednačine kojima se SMIJE dokazivati istina (vidi odluku 1 u docstringu)."""
    equations = list(_equations_in(question))
    if solution_text:
        stem_values = _stem_numbers(question)
        for equation in _equations_in(solution_text):
            if len(equation.coeffs) < VARIABLE_COUNT:
                continue          # modelova tvrdnja o rezultatu nije ulaz
            if _corroborated(equation, stem_values) and equation not in equations:
                equations.append(equation)
    return equations


def _solve(equations: list) -> tuple:
    """(status, {nepoznata: vrijednost}); status: unique/singular/unsupported."""
    names = sorted({name for equation in equations for name in equation.coeffs})
    if len(names) != VARIABLE_COUNT or len(equations) < VARIABLE_COUNT:
        return "unsupported", {}
    if not any(len(equation.coeffs) == VARIABLE_COUNT for equation in equations):
        # Nema nijedne SPREGNUTE jednačine — nije ovaj oblik (odluka 2).
        return "unsupported", {}
    v1, v2 = names
    solutions = []
    for first, second in combinations(equations, 2):
        a1, b1 = first.coeffs.get(v1, Fraction(0)), first.coeffs.get(v2, Fraction(0))
        a2, b2 = second.coeffs.get(v1, Fraction(0)), second.coeffs.get(v2, Fraction(0))
        det = a1 * b2 - a2 * b1
        if det == 0:
            continue
        candidate = {v1: (first.constant * b2 - second.constant * b1) / det,
                     v2: (a1 * second.constant - a2 * first.constant) / det}
        if all(equation.satisfied_by(candidate) for equation in equations):
            if candidate not in solutions:
                solutions.append(candidate)
    if len(solutions) != 1:
        # Nijedan par ne daje rješenje koje zadovoljava SVE pročitane jednačine
        # (protivrječan sistem), ili ih ima više (zavisan sistem bez dodatnog
        # podatka). Ni u jednom slučaju jedna brojčana vrijednost nije
        # jednoznačno određena.
        return "singular", {}
    return "unique", solutions[0]


def _target_variable(question: str, names: Iterable[str]) -> str:
    """Slovo koje pitanje traži, dobijeno DOSLOVNIM poklapanjem fraze."""
    names = set(names)
    prose = " ".join(content for kind, content in tokenize_math(question or "")
                     if kind == _TEXT_SEGMENT)
    declarations = []
    for match in _DECLARATION_RE.finditer(question or ""):
        variable = match.group("var")
        phrase = " ".join(match.group("phrase").split()).strip().lower()
        if variable in names and len(phrase) >= 3:
            declarations.append((phrase, variable))
    if not declarations:
        return ""
    tail = prose.lower()
    question_mark = tail.rfind("?")
    if question_mark != -1:
        start = max(tail.rfind(".", 0, question_mark), tail.rfind("!", 0, question_mark))
        tail = tail[start + 1:question_mark + 1]
    hits = {variable for phrase, variable in declarations if phrase in tail}
    return hits.pop() if len(hits) == 1 else ""


def _option_scalars(options: tuple) -> Optional[list]:
    """Sve opcije kao egzaktne vrijednosti, ili None kad oblik nije brojčan.

    Opcije ove porodice nose mjernu jedinicu kao prozu (`$2$ KM`), a dijeljeni
    `mcq_integrity._option_numeric_value` takav zapis odbija. Jedinica se skida
    SAMO ako je kod SVIH opcija doslovno ista — inače „$2$ cm“ i „$2$ m“ ne
    smiju postati isti broj, pa orakl ćuti umjesto da poredi jabuke i kruške."""
    values, units = [], set()
    for option in options:
        tokens = tokenize_math(option or "")
        maths = [content for kind, content in tokens if kind != _TEXT_SEGMENT]
        prose = "".join(content for kind, content in tokens
                        if kind == _TEXT_SEGMENT).strip()
        if len(maths) != 1:
            return None
        status, value, _expr = _option_numeric_value(f"${maths[0]}$")
        if status != "value":
            return None
        units.add(prose)
        values.append(Fraction(value).limit_denominator(10 ** 6))
    if len(units) != 1:
        return None
    return values


def evaluate_system_mcq(question: str, option_texts: Iterable[str],
                        solution_text: str = "") -> SystemMCQResult:
    """Ocijeni SAMO MCQ nad sistemom dvije linearne jednačine; ostalo ćuti."""
    options = tuple(option_texts or ())
    if len(options) < 2:
        return SystemMCQResult(False, False)

    equations = _usable_equations(question, solution_text)
    status, solution = _solve(equations)
    if status == "unsupported":
        return SystemMCQResult(False, False)

    values = _option_scalars(options)
    if values is None:
        return SystemMCQResult(False, False)

    if status == "singular":
        return SystemMCQResult(True, False, "system_not_uniquely_solvable",
                               (), "", tuple(values), ())

    truth = tuple(sorted(solution.items()))
    target = _target_variable(question, solution)
    if not target:
        # Sistem je dokazan, ali se tražena nepoznata ne može vezati za pitanje:
        # objava bi opet zavisila od modelove riječi. Radije zatvoreno.
        return SystemMCQResult(True, False, "system_target_unresolved", truth, "",
                               tuple(values), ())

    canonical = solution[target]
    matching = tuple(index for index, value in enumerate(values) if value == canonical)
    if not matching:
        return SystemMCQResult(True, False, "no_correct_option", truth, target,
                               tuple(values), matching)
    if len(matching) > 1:
        return SystemMCQResult(True, False, "multiple_correct_options", truth, target,
                               tuple(values), matching)
    return SystemMCQResult(True, True, "", truth, target, tuple(values), matching)


def publication_failure(question: str, option_texts: Iterable[str],
                        marked_index: int, solution_text: str = "") -> str:
    """Kod odbijanja objave, ili "" kad orakl ne prigovara / ne primjenjuje se."""
    result = evaluate_system_mcq(question, option_texts, solution_text)
    if not result.applicable:
        return ""
    if not result.valid:
        return result.reason_code
    if marked_index not in result.correct_indices:
        return "marked_option_math_mismatch"
    return ""
