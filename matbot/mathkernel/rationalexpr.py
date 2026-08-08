"""Egzaktni polinomi jedne promjenljive i razlomljeni racionalni izrazi.

KANONSKI IR (Batch #4, Prioritet 1):

    Polynomial          — koeficijenti su `Fraction`, indeks = stepen;
    RationalExpression  — brojnik/imenilac polinomi + ISKLJUČENE VRIJEDNOSTI.

DOMEN JE DIO IDENTITETA: ``(x^2-1)/(x-1)`` i ``x+1`` su ekvivalentni SAMO na
zajedničkom domenu bez ``x=1``. Skraćivanje zato NIKAD ne odbacuje isključene
vrijednosti — kanonski oblik nosi i redukovan razlomak i kompletan skup
isključenja, a ekvivalencija poredi OBOJE.

POTPUNOST DOMENA: isključene vrijednosti su racionalne nule imenioca. Jezgro
ih traži teoremom o racionalnim nulama s deflacijom; kad se imenilac NE
raspada potpuno na linearne faktore nad Q, izraz nosi ``domain_complete=False``
i ekvivalencija takva dva izraza ODBIJA da tvrdi bilo šta (fail-closed).
Generatori grade imenioce isključivo iz linearnih faktora, pa je u praksi
domen uvijek potpun — ali jezgro to dokazuje, ne pretpostavlja.

Nijedan dio ovog modula ne poznaje lekciju, MCQ ni Practice.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import gcd, lcm


class RationalExpressionError(ValueError):
    """Matematički nedozvoljena konstrukcija (npr. nulti imenilac)."""


def _fraction(value) -> Fraction:
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    raise RationalExpressionError(f"koeficijent mora biti egzaktan: {value!r}")


@dataclass(frozen=True)
class Polynomial:
    """Polinom jedne promjenljive s egzaktnim racionalnim koeficijentima.

    ``coefficients[i]`` je koeficijent uz x^i; završne nule su skinute, pa je
    nula-polinom prazna torka i jednakost koeficijenata je jednakost polinoma.
    """

    coefficients: tuple

    # ------------------------------------------------------------------
    # KONSTRUKCIJA
    # ------------------------------------------------------------------

    @staticmethod
    def from_coefficients(sequence) -> "Polynomial":
        coeffs = [_fraction(value) for value in sequence]
        while coeffs and coeffs[-1] == 0:
            coeffs.pop()
        return Polynomial(tuple(coeffs))

    @staticmethod
    def constant(value) -> "Polynomial":
        return Polynomial.from_coefficients([value])

    @staticmethod
    def monomial(coefficient, degree: int) -> "Polynomial":
        if degree < 0:
            raise RationalExpressionError("stepen monoma mora biti nenegativan")
        return Polynomial.from_coefficients([0] * degree + [coefficient])

    @staticmethod
    def linear(a, b) -> "Polynomial":
        """a·x + b."""
        return Polynomial.from_coefficients([b, a])

    @staticmethod
    def zero() -> "Polynomial":
        return Polynomial(())

    @staticmethod
    def one() -> "Polynomial":
        return Polynomial.constant(1)

    # ------------------------------------------------------------------
    # OSNOVNA SVOJSTVA
    # ------------------------------------------------------------------

    @property
    def degree(self) -> int:
        """Stepen; nula-polinom ima dogovorno -1."""
        return len(self.coefficients) - 1

    @property
    def is_zero(self) -> bool:
        return not self.coefficients

    @property
    def leading(self) -> Fraction:
        if self.is_zero:
            raise RationalExpressionError("nula-polinom nema vodeći koeficijent")
        return self.coefficients[-1]

    def coefficient(self, degree: int) -> Fraction:
        if 0 <= degree < len(self.coefficients):
            return self.coefficients[degree]
        return Fraction(0)

    # ------------------------------------------------------------------
    # ARITMETIKA
    # ------------------------------------------------------------------

    def __add__(self, other: "Polynomial") -> "Polynomial":
        size = max(len(self.coefficients), len(other.coefficients))
        return Polynomial.from_coefficients([
            self.coefficient(i) + other.coefficient(i) for i in range(size)])

    def __sub__(self, other: "Polynomial") -> "Polynomial":
        size = max(len(self.coefficients), len(other.coefficients))
        return Polynomial.from_coefficients([
            self.coefficient(i) - other.coefficient(i) for i in range(size)])

    def __neg__(self) -> "Polynomial":
        return Polynomial(tuple(-c for c in self.coefficients))

    def __mul__(self, other: "Polynomial") -> "Polynomial":
        if self.is_zero or other.is_zero:
            return Polynomial.zero()
        result = [Fraction(0)] * (self.degree + other.degree + 1)
        for i, a in enumerate(self.coefficients):
            if a == 0:
                continue
            for j, b in enumerate(other.coefficients):
                result[i + j] += a * b
        return Polynomial.from_coefficients(result)

    def scaled(self, factor) -> "Polynomial":
        factor = _fraction(factor)
        return Polynomial.from_coefficients(
            [c * factor for c in self.coefficients])

    def power(self, exponent: int) -> "Polynomial":
        if exponent < 0:
            raise RationalExpressionError("negativan stepen polinoma")
        result = Polynomial.one()
        for _ in range(exponent):
            result = result * self
        return result

    def divmod_by(self, divisor: "Polynomial"):
        """Egzaktno polinomsko dijeljenje s ostatkom."""
        if divisor.is_zero:
            raise RationalExpressionError("dijeljenje nula-polinomom")
        quotient = Polynomial.zero()
        remainder = self
        while not remainder.is_zero and remainder.degree >= divisor.degree:
            shift = remainder.degree - divisor.degree
            factor = remainder.leading / divisor.leading
            term = Polynomial.monomial(factor, shift)
            quotient = quotient + term
            remainder = remainder - term * divisor
        return quotient, remainder

    def evaluate(self, x) -> Fraction:
        x = _fraction(x)
        result = Fraction(0)
        for coefficient in reversed(self.coefficients):
            result = result * x + coefficient
        return result

    # ------------------------------------------------------------------
    # STRUKTURA: NZD, primitivna normalizacija, racionalne nule
    # ------------------------------------------------------------------

    def monic(self) -> "Polynomial":
        if self.is_zero:
            return self
        return self.scaled(Fraction(1) / self.leading)

    @staticmethod
    def gcd_of(first: "Polynomial", second: "Polynomial") -> "Polynomial":
        """Monični NZD (Euklid nad Q); gcd(0, p) = monic(p)."""
        a, b = first, second
        while not b.is_zero:
            _quotient, remainder = a.divmod_by(b)
            a, b = b, remainder
        if a.is_zero:
            return Polynomial.zero()
        return a.monic()

    def primitive_integer(self) -> tuple:
        """(sadržaj, primitivan cjelobrojni polinom pozitivnog vodećeg).

        p == sadržaj · primitivni; sadržaj je Fraction, primitivni polinom ima
        cjelobrojne koeficijente bez zajedničkog djelioca i pozitivan vodeći.
        """
        if self.is_zero:
            return Fraction(0), self
        denominators = lcm(*[c.denominator for c in self.coefficients])
        scaled = [c * denominators for c in self.coefficients]
        numerators = [int(c) for c in scaled]
        common = 0
        for value in numerators:
            common = gcd(common, abs(value))
        sign = -1 if numerators[-1] < 0 else 1
        primitive = Polynomial.from_coefficients(
            [Fraction(value, sign * common) for value in numerators])
        content = Fraction(sign * common, denominators)
        return content, primitive

    def rational_roots(self) -> tuple:
        """(sortirane racionalne nule s višestrukostima skupljenim u skup,
        potpuno_rastavljen) — nule preko teoreme o racionalnim nulama uz
        deflaciju; ``potpuno_rastavljen`` je True kad se polinom raspada na
        linearne faktore nad Q (poslije deflacije ostane konstanta)."""
        if self.is_zero:
            raise RationalExpressionError("nula-polinom ima sve vrijednosti kao nule")
        roots = []
        _content, current = self.primitive_integer()
        while current.degree >= 1:
            root = _one_rational_root(current)
            if root is None:
                return tuple(sorted(set(roots))), False
            roots.append(root)
            quotient, remainder = current.divmod_by(Polynomial.linear(1, -root))
            if not remainder.is_zero:
                raise RationalExpressionError("deflacija nije egzaktna")
            _content, current = quotient.primitive_integer()
        return tuple(sorted(set(roots))), True

    # ------------------------------------------------------------------
    # PRIKAZ (školski MathJax zapis, bez $...$)
    # ------------------------------------------------------------------

    def display(self, variable: str = "x") -> str:
        if self.is_zero:
            return "0"
        parts = []
        for degree in range(self.degree, -1, -1):
            coefficient = self.coefficient(degree)
            if coefficient == 0:
                continue
            parts.append(_term_display(coefficient, degree, variable,
                                       first=not parts))
        return "".join(parts)


def _one_rational_root(primitive: Polynomial):
    """Jedna racionalna nula primitivnog cjelobrojnog polinoma, ili None."""
    constant = primitive.coefficient(0)
    if constant == 0:
        return Fraction(0)
    lead = int(primitive.leading)
    tail = int(constant)
    for p in _divisors(abs(tail)):
        for q in _divisors(abs(lead)):
            for candidate in (Fraction(p, q), Fraction(-p, q)):
                if primitive.evaluate(candidate) == 0:
                    return candidate
    return None


def _divisors(value: int):
    result = []
    for candidate in range(1, int(value ** 0.5) + 1):
        if value % candidate == 0:
            result.append(candidate)
            if candidate != value // candidate:
                result.append(value // candidate)
    return sorted(result)


def _coefficient_display(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    sign = "-" if value < 0 else ""
    positive = abs(value)
    return f"{sign}\\frac{{{positive.numerator}}}{{{positive.denominator}}}"


def _term_display(coefficient: Fraction, degree: int, variable: str,
                  first: bool) -> str:
    sign = "-" if coefficient < 0 else ("" if first else "+")
    magnitude = abs(coefficient)
    if degree == 0:
        body = _coefficient_display(magnitude)
    else:
        variable_part = variable if degree == 1 else f"{variable}^{{{degree}}}"
        body = variable_part if magnitude == 1 else \
            f"{_coefficient_display(magnitude)}{variable_part}"
    return f"{sign}{body}"


# ---------------------------------------------------------------------------
# RAZLOMLJEN RACIONALAN IZRAZ
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RationalExpression:
    """Brojnik/imenilac + ISKLJUČENE VRIJEDNOSTI kao dio identiteta.

    ``excluded`` su SVE racionalne nule POLAZNOG imenioca (i eventualno
    naslijeđene iz ranijih koraka računa — npr. dijeljenje dodaje nule
    brojnika djelioca). ``domain_complete`` je False kad neka komponenta
    imenioca nije potpuno rastavljena nad Q — tada se ekvivalencija odbija.
    """

    numerator: Polynomial
    denominator: Polynomial
    excluded: tuple
    domain_complete: bool

    @staticmethod
    def build(numerator: Polynomial, denominator: Polynomial,
              extra_excluded=()) -> "RationalExpression":
        if denominator.is_zero:
            raise RationalExpressionError("imenilac je nula-polinom")
        roots, complete = denominator.rational_roots()
        excluded = tuple(sorted(set(roots) | {Fraction(v) for v in extra_excluded}))
        return RationalExpression(numerator=numerator, denominator=denominator,
                                  excluded=excluded, domain_complete=complete)

    # ------------------------------------------------------------------
    # KANONSKI OBLIK — redukovan razlomak, primitivan imenilac, isti domen
    # ------------------------------------------------------------------

    def canonical(self) -> "RationalExpression":
        """Skraćen oblik s NEPROMIJENJENIM skupom isključenja.

        Faktor koji se skrati NE briše svoju nulu iz domena — to je srž
        pravila „(x²-1)/(x-1) ≠ x+1 bez x≠1“."""
        common = Polynomial.gcd_of(self.numerator, self.denominator)
        if common.is_zero:
            # brojnik i imenilac oba nula — nemoguće (imenilac != 0)
            raise RationalExpressionError("nedozvoljen NZD")
        numerator, remainder_n = self.numerator.divmod_by(common)
        denominator, remainder_d = self.denominator.divmod_by(common)
        if not remainder_n.is_zero or not remainder_d.is_zero:
            raise RationalExpressionError("skraćivanje nije egzaktno")
        content, primitive = denominator.primitive_integer()
        numerator = numerator.scaled(Fraction(1) / content)
        return RationalExpression(
            numerator=numerator, denominator=primitive,
            excluded=self.excluded, domain_complete=self.domain_complete)

    def equivalent(self, other: "RationalExpression") -> bool:
        """Ekvivalencija = isti kanonski razlomak I isti domen.

        Nepotpun domen na bilo kojoj strani → False (fail-closed): ne tvrdi
        se ekvivalencija koja se ne može dokazati."""
        if not (self.domain_complete and other.domain_complete):
            return False
        a, b = self.canonical(), other.canonical()
        return (a.numerator == b.numerator
                and a.denominator == b.denominator
                and a.excluded == b.excluded)

    def value_equivalent(self, other: "RationalExpression") -> bool:
        """Ista racionalna funkcija (bez poređenja domena) — za distraktore
        kojima je RAZLIČIT DOMEN jedina razlika."""
        a, b = self.canonical(), other.canonical()
        return a.numerator == b.numerator and a.denominator == b.denominator

    # ------------------------------------------------------------------
    # OPERACIJE — svaka nosi ISPRAVAN skup isključenja rezultata
    # ------------------------------------------------------------------

    def multiply(self, other: "RationalExpression") -> "RationalExpression":
        return RationalExpression(
            numerator=self.numerator * other.numerator,
            denominator=self.denominator * other.denominator,
            excluded=tuple(sorted(set(self.excluded) | set(other.excluded))),
            domain_complete=self.domain_complete and other.domain_complete)

    def divide(self, other: "RationalExpression") -> "RationalExpression":
        """Dijeljenje dodatno isključuje i nule BROJNIKA djelioca."""
        if other.numerator.is_zero:
            raise RationalExpressionError("dijeljenje nulom (brojnik djelioca)")
        divisor_zeros, divisor_complete = other.numerator.rational_roots()
        return RationalExpression(
            numerator=self.numerator * other.denominator,
            denominator=self.denominator * other.numerator,
            excluded=tuple(sorted(set(self.excluded) | set(other.excluded)
                                  | set(divisor_zeros))),
            domain_complete=(self.domain_complete and other.domain_complete
                             and divisor_complete))

    def add(self, other: "RationalExpression") -> "RationalExpression":
        lcd = self.lcd_with(other)
        left_factor, r1 = lcd.divmod_by(self.denominator)
        right_factor, r2 = lcd.divmod_by(other.denominator)
        if not r1.is_zero or not r2.is_zero:
            raise RationalExpressionError("zajednički imenilac nije egzaktan")
        return RationalExpression(
            numerator=self.numerator * left_factor + other.numerator * right_factor,
            denominator=lcd,
            excluded=tuple(sorted(set(self.excluded) | set(other.excluded))),
            domain_complete=self.domain_complete and other.domain_complete)

    def subtract(self, other: "RationalExpression") -> "RationalExpression":
        negated = RationalExpression(
            numerator=-other.numerator, denominator=other.denominator,
            excluded=other.excluded, domain_complete=other.domain_complete)
        return self.add(negated)

    def lcd_with(self, other: "RationalExpression") -> Polynomial:
        """Najmanji zajednički imenilac: d1·d2 / NZD(d1, d2), primitivan."""
        common = Polynomial.gcd_of(self.denominator, other.denominator)
        quotient, remainder = self.denominator.divmod_by(common)
        if not remainder.is_zero:
            raise RationalExpressionError("NZD nije egzaktan djelilac")
        lcd = quotient * other.denominator
        _content, primitive = lcd.primitive_integer()
        return primitive

    def evaluate(self, x) -> Fraction:
        x = _fraction(x)
        if x in self.excluded:
            raise RationalExpressionError(
                "vrijednost je isključena iz domena izraza")
        denominator_value = self.denominator.evaluate(x)
        if denominator_value == 0:
            raise RationalExpressionError("imenilac je nula u datoj tački")
        return self.numerator.evaluate(x) / denominator_value

    # ------------------------------------------------------------------
    # PRIKAZ
    # ------------------------------------------------------------------

    def display(self, variable: str = "x") -> str:
        numerator = self.numerator.display(variable)
        if self.denominator.degree == 0 and self.denominator.leading == 1:
            return numerator
        return (f"\\frac{{{numerator}}}"
                f"{{{self.denominator.display(variable)}}}")


# ---------------------------------------------------------------------------
# RJEŠAVAČ — linearno rješive razlomljene jednačine (Result-mode reusable)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RationalEquationSolution:
    """Ishod egzaktnog rješavanja: rješenje, isključenja i klasifikacija."""

    status: str          # "unique" | "excluded_root" | "no_solution" | "identity"
    solution: object     # Fraction za "unique"/"excluded_root", inače None
    excluded: tuple      # sve vrijednosti isključene iz domena jednačine


def solve_linear_rational_equation(left: RationalExpression,
                                   right: RationalExpression) -> RationalEquationSolution:
    """Riješi ``left = right`` kad se svodi na linearnu jednačinu.

    Postupak: razlika svedena na zajednički imenilac; brojnik mora biti
    stepena <= 1 (inače izraz nije u podržanom obliku i poziv PADA — nikad
    tiho pogrešan odgovor). Korijen koji upada u isključene vrijednosti
    vraća se kao ``excluded_root`` — jednačina tada NEMA rješenje, a
    dijagnostika čuva kandidata radi potpunog rješenja."""
    difference = left.subtract(right)
    excluded = difference.excluded
    numerator = difference.numerator
    if numerator.degree > 1:
        raise RationalExpressionError(
            "jednačina se ne svodi na linearnu — nepodržan oblik")
    if numerator.is_zero:
        return RationalEquationSolution("identity", None, excluded)
    if numerator.degree == 0:
        return RationalEquationSolution("no_solution", None, excluded)
    root = -numerator.coefficient(0) / numerator.coefficient(1)
    if root in excluded:
        return RationalEquationSolution("excluded_root", root, excluded)
    return RationalEquationSolution("unique", root, excluded)
