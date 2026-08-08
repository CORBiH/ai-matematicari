"""Egzaktno jezgro racionalnih izraza — dokazi identiteta, domena i operacija.

Simbolički autoritet je PRIMARAN dokaz (kanonski oblik); uzorkovanje
vrijednosti van isključenja je SEKUNDARNA odbrana — nikad jedini dokaz.
"""
import random
from fractions import Fraction

import pytest

from matbot.mathkernel.rationalexpr import (Polynomial, RationalExpression,
                                            RationalExpressionError,
                                            solve_linear_rational_equation)


def poly(*coeffs):
    """poly(a0, a1, a2, ...) → a0 + a1·x + a2·x² + ..."""
    return Polynomial.from_coefficients(coeffs)


# ---------------------------------------------------------------------------
# POLINOM
# ---------------------------------------------------------------------------

def test_polynomial_normalizes_trailing_zeros():
    assert poly(1, 2, 0, 0) == poly(1, 2)
    assert poly(0).is_zero and poly().is_zero
    assert poly(0, 0).degree == -1


def test_polynomial_arithmetic_agrees_with_evaluation():
    rng = random.Random(4)
    for _ in range(200):
        a = poly(*[rng.randint(-6, 6) for _ in range(rng.randint(1, 4))])
        b = poly(*[rng.randint(-6, 6) for _ in range(rng.randint(1, 4))])
        x = Fraction(rng.randint(-9, 9), rng.randint(1, 5))
        assert (a + b).evaluate(x) == a.evaluate(x) + b.evaluate(x)
        assert (a - b).evaluate(x) == a.evaluate(x) - b.evaluate(x)
        assert (a * b).evaluate(x) == a.evaluate(x) * b.evaluate(x)


def test_polynomial_division_is_exact():
    rng = random.Random(11)
    for _ in range(200):
        divisor = poly(*[rng.randint(-5, 5) for _ in range(rng.randint(1, 3))])
        if divisor.is_zero:
            continue
        quotient = poly(*[rng.randint(-5, 5) for _ in range(rng.randint(1, 3))])
        remainder = poly(*[rng.randint(-4, 4)
                           for _ in range(max(divisor.degree, 0))])
        dividend = divisor * quotient + remainder
        q, r = dividend.divmod_by(divisor)
        assert divisor * q + r == dividend
        assert r.is_zero or r.degree < divisor.degree


def test_gcd_divides_both_and_is_monic():
    a = Polynomial.linear(1, -1) * Polynomial.linear(1, 2)   # (x-1)(x+2)
    b = Polynomial.linear(1, -1) * Polynomial.linear(1, 3)   # (x-1)(x+3)
    g = Polynomial.gcd_of(a, b)
    assert g == Polynomial.linear(1, -1)
    assert g.leading == 1


def test_rational_roots_full_split_and_incomplete():
    split = Polynomial.linear(2, -3) * Polynomial.linear(1, 1)  # (2x-3)(x+1)
    roots, complete = split.rational_roots()
    assert complete and roots == (Fraction(-1), Fraction(3, 2))
    irreducible = poly(1, 0, 1)                                  # x² + 1
    roots, complete = irreducible.rational_roots()
    assert not complete and roots == ()


def test_display_school_notation():
    assert poly(-3, 2).display() == "2x-3"
    assert poly(0, 0, 1).display() == "x^{2}"
    assert poly(1, -1).display() == "-x+1"
    assert poly(Fraction(1, 2)).display() == "\\frac{1}{2}"
    assert Polynomial.zero().display() == "0"


# ---------------------------------------------------------------------------
# DOMEN JE DIO IDENTITETA
# ---------------------------------------------------------------------------

def _expr(numerator, denominator, extra=()):
    return RationalExpression.build(numerator, denominator, extra_excluded=extra)


def test_simplification_preserves_excluded_values():
    # (x²-1)/(x-1): kanonski x+1, ali x=1 OSTAJE isključeno.
    expression = _expr(poly(-1, 0, 1), Polynomial.linear(1, -1))
    canonical = expression.canonical()
    assert canonical.numerator == poly(1, 1)
    assert canonical.denominator == Polynomial.one()
    assert canonical.excluded == (Fraction(1),)


def test_equivalence_requires_identical_domain():
    reduced = _expr(poly(-1, 0, 1), Polynomial.linear(1, -1))       # (x²-1)/(x-1)
    plain = _expr(poly(1, 1), Polynomial.one())                     # x+1, bez isključenja
    with_domain = _expr(poly(1, 1), Polynomial.one(), extra=(1,))   # x+1, x≠1
    assert not reduced.equivalent(plain)
    assert reduced.equivalent(with_domain)
    assert reduced.value_equivalent(plain)


def test_incomplete_domain_fails_closed():
    incomplete = _expr(poly(1), poly(1, 0, 1))   # 1/(x²+1) — bez racionalnih nula
    assert not incomplete.domain_complete
    same = _expr(poly(1), poly(1, 0, 1))
    assert not incomplete.equivalent(same)       # ne tvrdi se ništa


def test_zero_denominator_is_rejected():
    with pytest.raises(RationalExpressionError):
        RationalExpression.build(poly(1), Polynomial.zero())


# ---------------------------------------------------------------------------
# OPERACIJE
# ---------------------------------------------------------------------------

def test_multiplication_carries_both_domains():
    left = _expr(poly(1), Polynomial.linear(1, -2))    # 1/(x-2)
    right = _expr(poly(1), Polynomial.linear(1, 3))    # 1/(x+3)
    product = left.multiply(right)
    assert product.excluded == (Fraction(-3), Fraction(2))


def test_division_excludes_divisor_numerator_zeros():
    left = _expr(poly(1), Polynomial.linear(1, -2))                 # 1/(x-2)
    right = _expr(Polynomial.linear(1, -5), Polynomial.linear(1, 3))  # (x-5)/(x+3)
    quotient = left.divide(right)
    assert Fraction(5) in quotient.excluded      # nula brojnika djelioca
    assert Fraction(2) in quotient.excluded
    assert Fraction(-3) in quotient.excluded


def test_addition_uses_true_lcd():
    # 1/(x(x-1)) + 1/((x-1)(x+2)) — NZD imenilaca je (x-1).
    d1 = Polynomial.linear(1, 0) * Polynomial.linear(1, -1)
    d2 = Polynomial.linear(1, -1) * Polynomial.linear(1, 2)
    left, right = _expr(poly(1), d1), _expr(poly(1), d2)
    lcd = left.lcd_with(right)
    expected = (Polynomial.linear(1, 0) * Polynomial.linear(1, -1)
                * Polynomial.linear(1, 2))
    assert lcd == expected
    total = left.add(right)
    x = Fraction(3)
    assert total.evaluate(x) == left.evaluate(x) + right.evaluate(x)


def test_operations_agree_with_sampled_values():
    rng = random.Random(23)
    for _ in range(150):
        a_num = poly(*[rng.randint(-4, 4) for _ in range(2)])
        b_num = poly(*[rng.randint(-4, 4) for _ in range(2)])
        a_den = Polynomial.linear(1, rng.randint(-4, 4))
        b_den = Polynomial.linear(1, rng.randint(-4, 4))
        left, right = _expr(a_num, a_den), _expr(b_num, b_den)
        operations = [
            (left.add(right), lambda x: left.evaluate(x) + right.evaluate(x)),
            (left.subtract(right), lambda x: left.evaluate(x) - right.evaluate(x)),
            (left.multiply(right), lambda x: left.evaluate(x) * right.evaluate(x)),
        ]
        if not right.numerator.is_zero:
            quotient = left.divide(right)
            operations.append(
                (quotient, lambda x: left.evaluate(x) / right.evaluate(x)))
        for result, oracle in operations:
            for candidate in range(-8, 9):
                x = Fraction(candidate)
                if x in result.excluded:
                    continue
                assert result.evaluate(x) == oracle(x)


def test_every_excluded_value_breaks_an_original_denominator():
    rng = random.Random(31)
    for _ in range(100):
        a_den = Polynomial.linear(1, rng.randint(-5, 5))
        b_den = Polynomial.linear(1, rng.randint(-5, 5))
        left = _expr(poly(rng.randint(-4, 4), 1), a_den)
        right = _expr(poly(rng.randint(-4, 4), 1), b_den)
        result = left.multiply(right)
        for value in result.excluded:
            assert a_den.evaluate(value) == 0 or b_den.evaluate(value) == 0


# ---------------------------------------------------------------------------
# JEDNAČINE
# ---------------------------------------------------------------------------

def test_unique_solution_checks_against_domain():
    # 6/(x-1) = 3  →  x = 3.
    left = _expr(poly(6), Polynomial.linear(1, -1))
    right = _expr(poly(3), Polynomial.one())
    outcome = solve_linear_rational_equation(left, right)
    assert outcome.status == "unique" and outcome.solution == Fraction(3)
    assert Fraction(1) in outcome.excluded


def test_excluded_root_is_not_a_solution():
    # (x-2)/(x-2)... umjesto toga: 4/(x-2) = (2x)/(x-2) → kandidat x = 2 otpada.
    denominator = Polynomial.linear(1, -2)
    left = _expr(poly(4), denominator)
    right = _expr(poly(0, 2), denominator)
    outcome = solve_linear_rational_equation(left, right)
    assert outcome.status == "excluded_root"
    assert outcome.solution == Fraction(2)


def test_identity_and_no_solution_classification():
    denominator = Polynomial.linear(1, 1)
    same = _expr(poly(0, 3), denominator)
    outcome = solve_linear_rational_equation(same, same)
    assert outcome.status == "identity"
    shifted = _expr(poly(1, 3), denominator)
    outcome = solve_linear_rational_equation(same, shifted)
    assert outcome.status == "no_solution"


def test_nonlinear_reduction_fails_closed():
    left = _expr(poly(0, 0, 1), Polynomial.one())    # x²
    right = _expr(poly(4), Polynomial.one())
    with pytest.raises(RationalExpressionError):
        solve_linear_rational_equation(left, right)
