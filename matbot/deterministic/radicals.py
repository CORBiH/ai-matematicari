"""Egzaktne vrijednosti oblika q·√n (kapacitet: geometrija, Pitagora).

ZAŠTO POSTOJI: površine, visine i dijagonale školske geometrije žive u
kvadratnim iracionalnostima (d = a√2, h = a√3/2, P = a²√3/4). Decimalna
aproksimacija NIKAD nije autoritet — vrijednost se nosi egzaktno kao
racionalan koeficijent uz √n s kvadratno slobodnim n, pa su jednakost,
poređenje i dedup opcija egzaktni. mathcheck nezavisno dokazuje ovakve
prikaze (podržava \\sqrt), a orakl direktnog računa ih zna evaluirati.
"""
from dataclasses import dataclass
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError


def simplify_sqrt(n: int):
    """√n = k·√m s kvadratno slobodnim m. Vrati (k, m)."""
    if n < 0:
        raise DeterministicGenerationError("negativna potkorjena vrijednost")
    k, m, factor = 1, n, 2
    while factor * factor <= m:
        while m % (factor * factor) == 0:
            m //= factor * factor
            k *= factor
        factor += 1
    return k, m


@dataclass(frozen=True)
class RadicalValue:
    """Egzaktna vrijednost `coefficient · √radicand` (radicand kvadratno slobodan)."""

    coefficient: Fraction
    radicand: int = 1

    @staticmethod
    def of(coefficient, radicand=1):
        coefficient = Fraction(coefficient)
        k, m = simplify_sqrt(int(radicand))
        coefficient *= k
        if m == 1 or coefficient == 0:
            return RadicalValue(coefficient, 1)
        return RadicalValue(coefficient, m)

    @staticmethod
    def sqrt_of(value):
        """Kvadratni korijen NENEGATIVNOG racionalnog broja, egzaktno.

        √(p/q) = √(pq)/q — brojnik se zatim kvadratno oslobađa."""
        value = Fraction(value)
        if value < 0:
            raise DeterministicGenerationError("korijen negativnog broja")
        return RadicalValue.of(Fraction(1, value.denominator),
                               value.numerator * value.denominator)

    @property
    def is_rational(self):
        return self.radicand == 1

    def rational(self) -> Fraction:
        if not self.is_rational:
            raise DeterministicGenerationError("vrijednost nije racionalna")
        return self.coefficient

    def __mul__(self, other):
        if isinstance(other, RadicalValue):
            return RadicalValue.of(self.coefficient * other.coefficient,
                                   self.radicand * other.radicand)
        return RadicalValue(self.coefficient * Fraction(other), self.radicand)

    __rmul__ = __mul__

    def __add__(self, other):
        other = other if isinstance(other, RadicalValue) else \
            RadicalValue(Fraction(other), 1)
        if self.coefficient == 0:
            return other
        if other.coefficient == 0:
            return self
        if self.radicand != other.radicand:
            raise DeterministicGenerationError("zbir različitih korijena")
        return RadicalValue(self.coefficient + other.coefficient, self.radicand)

    def __sub__(self, other):
        other = other if isinstance(other, RadicalValue) else \
            RadicalValue(Fraction(other), 1)
        return self.__add__(RadicalValue(-other.coefficient, other.radicand))

    def __truediv__(self, other):
        return RadicalValue(self.coefficient / Fraction(other), self.radicand)

    def approx(self) -> float:
        return float(self.coefficient) * (self.radicand ** 0.5)

    def display(self) -> str:
        """Kanonski MathJax zapis: `18`, `\\frac{3}{4}`, `5\\sqrt{2}`,
        `\\frac{a...}` — koeficijent u projektnom zapisu, korijen iza njega."""
        if self.is_rational:
            return core.fraction_display(self.coefficient)
        root = f"\\sqrt{{{self.radicand}}}"
        if self.coefficient == 1:
            return root
        if self.coefficient == -1:
            return f"-{root}"
        if self.coefficient.denominator == 1:
            return f"{self.coefficient.numerator}{root}"
        if self.coefficient.numerator == 1:
            return f"\\frac{{{root}}}{{{self.coefficient.denominator}}}"
        return (f"\\frac{{{abs(self.coefficient.numerator)}{root}}}"
                f"{{{self.coefficient.denominator}}}"
                if self.coefficient > 0 else
                f"-\\frac{{{abs(self.coefficient.numerator)}{root}}}"
                f"{{{self.coefficient.denominator}}}")
