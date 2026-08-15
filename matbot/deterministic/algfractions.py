"""Deterministički generator algebarskih (razlomljenih racionalnih) izraza.

Dvije semantičke porodice (Batch #4, Prioritet 1):

  • ``rational_expression_direct`` — domen, brojna vrijednost, proširivanje,
    skraćivanje, jednakost, zajednički nazivnik, sabiranje, oduzimanje,
    množenje, dijeljenje, dvojni razlomak i sređivanje izraza;
  • ``rational_equation_direct``  — jednačine s algebarskim razlomcima i
    jednačine s dvojnim razlomkom, linearno rješive.

MATEMATIČKI AUTORITET: isključivo ``matbot/mathkernel/rationalexpr.py`` —
egzaktni polinomi nad Q s domenom kao dijelom identiteta. Ovaj modul SAMO
renderuje zadatke i opcije; nijedna matematička istina ne nastaje ovdje.

DOMEN JE DIO IDENTITETA I U OPCIJAMA: kandidat-distraktor se odbacuje ako je
ekvivalentan tačnom odgovoru (ista funkcija I isti domen); distraktor kojem je
jedina razlika domen dozvoljen je SAMO u konceptima čija je poenta upravo
domen, i tada rješenje tu razliku izričito imenuje. Skraćeni oblik u rješenju
UVIJEK navodi uslov (x ≠ ...) — faktor se nikad ne krati prešutno.
"""
import random
from fractions import Fraction
from math import gcd, lcm

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.mathkernel.rationalexpr import (Polynomial, RationalExpression,
                                            RationalExpressionError,
                                            solve_linear_rational_equation)

FAMILY_IDS = ("rational_expression_direct", "rational_equation_direct")
GENERATOR_VERSION = "detalgfrac-1"

_EXPRESSION_CONCEPTS = frozenset({
    "domain_condition", "numeric_value", "expand", "reduce", "equal_fractions",
    "common_denominator", "add", "subtract", "multiply", "divide",
    "compound_fraction", "simplify_combined",
})
_EQUATION_CONCEPTS = frozenset({"fraction_equation", "double_fraction_equation"})
_SUPPORTED_CONCEPTS = _EXPRESSION_CONCEPTS | _EQUATION_CONCEPTS


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    return bool(concepts) and concepts <= _SUPPORTED_CONCEPTS


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    builders = {
        "domain_condition": _domain_package,
        "numeric_value": _numeric_value_package,
        "expand": _expand_package,
        "reduce": _reduce_package,
        "equal_fractions": _equal_fractions_package,
        "common_denominator": _common_denominator_package,
        "add": _add_subtract_package,
        "subtract": _add_subtract_package,
        "multiply": _multiply_divide_package,
        "divide": _multiply_divide_package,
        "compound_fraction": _compound_package,
        "simplify_combined": _simplify_combined_package,
        "fraction_equation": _fraction_equation_package,
        "double_fraction_equation": _double_fraction_equation_package,
    }
    for _ in range(80):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            return builders[concept](rng, level, lesson_id, lesson_title, concept)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


# ---------------------------------------------------------------------------
# GRADIVNI BLOKOVI — faktori s poznatim nulama (domen egzaktan konstrukcijom)
# ---------------------------------------------------------------------------

def _nonzero(rng, low, high):
    value = 0
    while value == 0:
        value = rng.randint(low, high)
    return value


def _linear_factor(rng, level):
    """Linearan faktor s malim koeficijentima; L1 preferira x i x±b."""
    b = _nonzero(rng, -6, 6)
    if level >= 3 and rng.random() < 0.4:
        a = rng.choice((2, 3))
        return Polynomial.linear(a, b)
    return Polynomial.linear(1, b)


def _denominator_for(rng, level):
    """Nazivnik po nivou: L1 monom, L2 binom, L3 proizvod/razlika kvadrata."""
    if level == 1:
        c = rng.choice((1, 1, 2, 3))
        return Polynomial.monomial(c, 1)
    if level == 2:
        return _linear_factor(rng, level)
    if rng.random() < 0.5:
        a = rng.randint(1, 4)
        return Polynomial.linear(1, -a) * Polynomial.linear(1, a)  # x²-a²
    return Polynomial.linear(1, _nonzero(rng, -4, 4)) * Polynomial.monomial(1, 1)


def _numerator_for(rng, level):
    if level == 1:
        return Polynomial.constant(_nonzero(rng, -9, 9))
    return Polynomial.linear(rng.choice((1, 1, 2)), _nonzero(rng, -6, 6))


def _display(expression: RationalExpression) -> str:
    return expression.display()


def _exclusion_text(excluded) -> str:
    """`$x \\neq -1$ i $x \\neq 2$` — vrijednosti sortirane, egzaktne."""
    if not excluded:
        raise DeterministicGenerationError("nema isključenih vrijednosti")
    parts = [f"$x \\neq {core.fraction_display(value)}$" for value in excluded]
    return " i ".join(parts)


def _distinct_expression_options(correct: RationalExpression, candidates,
                                 compare="equivalent"):
    """Četiri opcije-izraza, semantički dokazano različite (jezgro, ne string).

    ``compare="equivalent"`` poredi funkciju I domen; ``"value"`` poredi samo
    funkciju (za koncepte gdje bi „ista funkcija, drugi domen“ bila varka)."""
    def same(a, b):
        return a.value_equivalent(b) if compare == "value" else a.equivalent(b)

    chosen = []
    texts = [f"${_display(correct)}$"]
    for candidate in candidates:
        if candidate.denominator.is_zero:
            continue
        # Kanonski prikaz i za distraktore — bez ugniježdenih \frac u nazivniku.
        candidate = candidate.canonical()
        if same(candidate, correct) or any(same(candidate, seen) for seen in chosen):
            continue
        text = f"${_display(candidate)}$"
        if text in texts:
            continue
        chosen.append(candidate)
        texts.append(text)
        if len(chosen) == 3:
            break
    if len(chosen) != 3:
        raise DeterministicGenerationError("nedovoljno različitih izraza")
    return tuple(texts)


def _package(lesson_id, lesson_title, family_id, concept, level, question,
             option_texts, hints, solution, answer_display, signature,
             accepted=()):
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title, family_id=family_id,
        operation=concept, level=level, question=question,
        answer_value=None, answer_display=answer_display,
        distractor_values=(), hints=hints, solution=solution,
        signature_parameters=signature, required_conditions=[concept],
        relevant_objects=["algebarski razlomak"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", accepted_answers=accepted)


# ---------------------------------------------------------------------------
# 1) DOMEN — uslov definisanosti (nule nazivnika)
# ---------------------------------------------------------------------------

def _domain_package(rng, level, lesson_id, lesson_title, concept):
    denominator = _denominator_for(rng, max(level, 2) if level > 1 else 1)
    numerator = _numerator_for(rng, level)
    expression = RationalExpression.build(numerator, denominator)
    if not expression.domain_complete or not expression.excluded:
        raise DeterministicGenerationError("domen nije potpun")
    excluded = expression.excluded
    correct = _exclusion_text(excluded)

    wrong_sets = [tuple(-value for value in excluded)]
    numerator_roots, complete = numerator.rational_roots() \
        if not numerator.is_zero and numerator.degree >= 1 else ((), True)
    if numerator_roots:
        wrong_sets.append(tuple(numerator_roots))
    if len(excluded) > 1:
        wrong_sets.append(excluded[:1])
    wrong_sets.extend([
        tuple(value + 1 for value in excluded),
        tuple(value - 1 for value in excluded),
        tuple(value + 2 for value in excluded),
        tuple(value - 2 for value in excluded),
        tuple(value * 2 + 1 for value in excluded),
    ])
    option_texts, seen = [correct], {tuple(excluded)}
    for wrong in wrong_sets:
        normalized = tuple(sorted(set(Fraction(v) for v in wrong)))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        option_texts.append(_exclusion_text(normalized))
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno skupova isključenja")

    question = (f"Za koje vrijednosti promjenljive izraz "
                f"${_display(expression)}$ NIJE definisan?")
    hints = (
        "Razlomak nije definisan tamo gdje je nazivnik jednak nuli — "
        "brojnik na to ne utiče.",
        f"Riješi jednačinu ${denominator.display()} = 0$.",
        "Svaka nula nazivnika daje po jedan uslov oblika x ≠ vrijednost.",
    )
    solution = (f"Nazivnik ${denominator.display()}$ jednak je nuli za "
                + ", ".join(f"$x = {core.fraction_display(v)}$" for v in excluded)
                + f", pa izraz nije definisan upravo tamo: {correct}.")
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, tuple(option_texts), hints,
                    solution, correct,
                    [("denominator", denominator.display()),
                     ("numerator", numerator.display())])


# ---------------------------------------------------------------------------
# 2) BROJNA VRIJEDNOST
# ---------------------------------------------------------------------------

def _numeric_value_package(rng, level, lesson_id, lesson_title, concept):
    denominator = _denominator_for(rng, level)
    numerator = _numerator_for(rng, max(level, 2))
    expression = RationalExpression.build(numerator, denominator)
    for _ in range(30):
        x = Fraction(rng.randint(-6, 6))
        if x not in expression.excluded and denominator.evaluate(x) != 0:
            break
    else:
        raise DeterministicGenerationError("nema dozvoljene tačke")
    value = expression.evaluate(x)
    numerator_value = numerator.evaluate(x)
    denominator_value = denominator.evaluate(x)
    candidates = [value + 1, value - 1, -value,
                  Fraction(1) / value if value != 0 else value + 2,
                  numerator_value * denominator_value]
    question = (f"Izračunaj brojnu vrijednost izraza ${_display(expression)}$ "
                f"za $x = {core.fraction_display(x)}$.")
    hints = (
        "Uvrsti datu vrijednost promjenljive posebno u brojnik i posebno "
        "u nazivnik.",
        f"Brojnik: ${numerator.display()}$ za $x = {core.fraction_display(x)}$ "
        f"iznosi ${core.fraction_display(numerator_value)}$.",
        f"Nazivnik iznosi ${core.fraction_display(denominator_value)}$ — "
        "podijeli brojnik nazivnikom.",
    )
    solution = (f"Za $x = {core.fraction_display(x)}$: brojnik je "
                f"${core.fraction_display(numerator_value)}$, nazivnik "
                f"${core.fraction_display(denominator_value)}$, pa je "
                f"vrijednost ${core.fraction_display(numerator_value)} : "
                f"{core.parenthesized(core.fraction_display(denominator_value))}"
                f" = {core.fraction_display(value)}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="rational_expression_direct", operation=concept,
        level=level, question=question, answer_value=value,
        answer_display=core.fraction_display(value),
        distractor_values=candidates, hints=hints, solution=solution,
        signature_parameters=[("expression", _display(expression)),
                              ("x", str(x))],
        required_conditions=[concept],
        relevant_objects=["algebarski razlomak"],
        generator_version=GENERATOR_VERSION)


# ---------------------------------------------------------------------------
# 3) PROŠIRIVANJE
# ---------------------------------------------------------------------------

def _expand_package(rng, level, lesson_id, lesson_title, concept):
    base_den = _denominator_for(rng, min(level, 2))
    base_num = _numerator_for(rng, level)
    base = RationalExpression.build(base_num, base_den)
    if level == 1:
        factor = Polynomial.constant(rng.randint(2, 6))
    elif level == 2:
        factor = Polynomial.monomial(rng.randint(1, 3), 1)
    else:
        factor = _linear_factor(rng, 2)
    expanded = RationalExpression.build(base_num * factor, base_den * factor)
    factor_text = factor.display()

    wrong = [
        RationalExpression.build(base_num * factor, base_den),
        RationalExpression.build(base_num, base_den * factor),
        RationalExpression.build(base_num * factor * factor,
                                 base_den * factor),
        RationalExpression.build(base_num + factor, base_den + factor),
    ]
    option_texts = _distinct_expression_options(expanded, wrong, compare="value")
    question = (f"Proširi razlomak ${_display(base)}$ faktorom "
                f"${factor_text}$.")
    hints = (
        "Proširivanje množi ISTIM izrazom i brojnik i nazivnik — vrijednost "
        "razlomka se ne mijenja na zajedničkom domenu.",
        f"Pomnoži brojnik: $({base_num.display()}) \\cdot ({factor_text})$.",
        f"Pomnoži i nazivnik: $({base_den.display()}) \\cdot ({factor_text})$.",
    )
    solution = (f"Množimo brojnik i nazivnik faktorom ${factor_text}$: "
                f"${_display(base)} = {_display(expanded)}$ "
                "(vrijednost je ista za svaki $x$ iz zajedničkog domena).")
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, option_texts, hints, solution,
                    _display(expanded),
                    [("base", _display(base)), ("factor", factor_text)])


# ---------------------------------------------------------------------------
# 4) SKRAĆIVANJE — faktor se krati, USLOV OSTAJE
# ---------------------------------------------------------------------------

def _reduce_package(rng, level, lesson_id, lesson_title, concept):
    if level == 1:
        common = Polynomial.monomial(rng.randint(2, 5), 1)  # c·x
        reduced_num = Polynomial.constant(_nonzero(rng, -7, 7))
        reduced_den = Polynomial.constant(rng.randint(2, 7))
    elif level == 2:
        common = _linear_factor(rng, 1)
        reduced_num = Polynomial.constant(_nonzero(rng, -6, 6))
        reduced_den = Polynomial.linear(1, _nonzero(rng, -5, 5))
    else:
        a = rng.randint(1, 5)
        common = Polynomial.linear(1, a)                   # (x+a)
        reduced_num = Polynomial.linear(1, -a)             # (x-a) → x²-a² gore
        reduced_den = Polynomial.constant(rng.randint(2, 5))
    numerator = reduced_num * common
    denominator = reduced_den * common
    if Polynomial.gcd_of(reduced_num, reduced_den).degree > 0:
        raise DeterministicGenerationError("oblik nije skraćen do kraja")
    original = RationalExpression.build(numerator, denominator)
    if not original.domain_complete:
        raise DeterministicGenerationError("domen nije potpun")
    reduced = original.canonical()

    wrong = [
        RationalExpression.build(reduced.denominator, reduced.numerator)
        if not reduced.numerator.is_zero else
        RationalExpression.build(Polynomial.one(), reduced.denominator),
        RationalExpression.build(-reduced.numerator, reduced.denominator),
        RationalExpression.build(reduced.numerator + Polynomial.one(),
                                 reduced.denominator),
        RationalExpression.build(numerator, reduced.denominator),
    ]
    option_texts = _distinct_expression_options(reduced, wrong, compare="value")
    condition = _exclusion_text(original.excluded)
    question = f"Skrati razlomak ${_display(original)}$."
    hints = (
        "Rastavi brojnik i nazivnik na faktore pa potraži zajednički faktor.",
        f"Zajednički faktor je ${common.display()}$.",
        "Prekriži zajednički faktor, ali zapiši uslov: on ne smije biti nula.",
    )
    solution = (f"Brojnik je $({reduced.numerator.display()}) \\cdot "
                f"({common.display()})$, nazivnik "
                f"$({reduced.denominator.display()}) \\cdot ({common.display()})$. "
                f"Kraćenjem zajedničkog faktora ${common.display()}$ dobijamo "
                f"${_display(reduced)}$, uz uslov {condition} "
                "(vrijednosti za koje polazni nazivnik nije definisan).")
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, option_texts, hints, solution,
                    _display(reduced),
                    [("original", _display(original))])


# ---------------------------------------------------------------------------
# 5) JEDNAKI ALGEBARSKI RAZLOMCI — ista funkcija I ISTI DOMEN
# ---------------------------------------------------------------------------

def _equal_fractions_package(rng, level, lesson_id, lesson_title, concept):
    base_den = (Polynomial.monomial(1, 1) if level == 1
                else _linear_factor(rng, min(level, 2)))
    base_num = _numerator_for(rng, level)
    base = RationalExpression.build(base_num, base_den)
    constant = rng.randint(2, 6)
    equal = RationalExpression.build(base_num.scaled(constant),
                                     base_den.scaled(constant))
    if not base.equivalent(equal):
        raise DeterministicGenerationError("proširenje konstantom nije jednako")

    extra = _linear_factor(rng, 1)
    wrong = [
        # ista funkcija, ali SUŽEN domen — nije jednak izraz:
        RationalExpression.build(base_num * extra, base_den * extra),
        RationalExpression.build(base_num.scaled(constant), base_den),
        RationalExpression.build(base_num, base_den.scaled(constant)),
        RationalExpression.build(-base_num, base_den),
    ]
    option_texts = _distinct_expression_options(equal, wrong,
                                                compare="equivalent")
    question = (f"Koji je izraz JEDNAK izrazu ${_display(base)}$ "
                "(ista vrijednost i isti domen)?")
    domain_note = (_exclusion_text(base.excluded) if base.excluded
                   else "svaki $x$")
    hints = (
        "Jednaki algebarski razlomci imaju istu vrijednost za svaki x iz "
        "ISTOG domena.",
        "Proširivanje konstantom različitom od nule ne mijenja ni vrijednost "
        "ni domen.",
        "Proširivanje izrazom koji sadrži x SUŽAVA domen — takav razlomak "
        "nije jednak polaznom.",
    )
    solution = (f"Proširivanjem konstantom ${constant}$ dobijamo "
                f"${_display(equal)}$ — ista vrijednost i isti domen "
                f"({domain_note}). Proširivanje faktorom "
                f"${extra.display()}$ izbacilo bi iz domena i "
                f"$x = {core.fraction_display(-extra.coefficient(0))}$, pa "
                "takav razlomak NIJE jednak polaznom.")
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, option_texts, hints, solution,
                    _display(equal), [("base", _display(base))])


# ---------------------------------------------------------------------------
# 6) ZAJEDNIČKI NAZIVNIK
# ---------------------------------------------------------------------------

def _poly_content_lcm(d1: Polynomial, d2: Polynomial) -> Polynomial:
    """Školski NZI: NZS brojevnih sadržaja · primitivni NZI polinoma."""
    c1, p1 = d1.primitive_integer()
    c2, p2 = d2.primitive_integer()
    common = Polynomial.gcd_of(p1, p2)
    quotient, remainder = p1.divmod_by(common)
    if not remainder.is_zero:
        raise DeterministicGenerationError("NZI nije egzaktan")
    _content, primitive = (quotient * p2).primitive_integer()
    numeric = lcm(int(abs(c1) * 1), int(abs(c2) * 1)) \
        if c1.denominator == 1 and c2.denominator == 1 else 1
    return primitive.scaled(numeric)


def _common_denominator_package(rng, level, lesson_id, lesson_title, concept):
    if level == 1:
        c1, c2 = rng.randint(2, 6), rng.randint(2, 6)
        d1 = Polynomial.monomial(c1, 1)
        d2 = Polynomial.monomial(c2, rng.choice((1, 2)))
    elif level == 2:
        shared = _linear_factor(rng, 1)
        d1 = shared
        d2 = shared * Polynomial.constant(rng.randint(2, 4))
        if rng.random() < 0.5:
            d2 = Polynomial.monomial(rng.randint(2, 4), 1)
    else:
        shared = _linear_factor(rng, 1)
        other = _linear_factor(rng, 1)
        if Polynomial.gcd_of(shared, other).degree > 0:
            raise DeterministicGenerationError("faktori nisu različiti")
        d1 = shared * Polynomial.constant(rng.randint(1, 3))
        d2 = shared * other
    n1, n2 = _numerator_for(rng, 1), _numerator_for(rng, 1)
    left = RationalExpression.build(n1, d1)
    right = RationalExpression.build(n2, d2)
    lcd = _poly_content_lcm(d1, d2)
    product = d1 * d2
    lcd_text = lcd.display()

    candidates, texts_seen = [], {lcd_text}
    for wrong in (product, d1, d2, lcd.scaled(2),
                  Polynomial.gcd_of(d1, d2)):
        text = wrong.display()
        _q, r1 = wrong.divmod_by(d1.primitive_integer()[1]) \
            if not wrong.is_zero else (None, Polynomial.one())
        if text in texts_seen or wrong.is_zero:
            continue
        # NZI mora biti djeljiv s OBA nazivnika; distraktor smije i ne biti.
        texts_seen.add(text)
        candidates.append(text)
        if len(candidates) == 3:
            break
    if len(candidates) != 3:
        raise DeterministicGenerationError("nedovoljno nazivnika")
    option_texts = (f"${lcd_text}$", *(f"${t}$" for t in candidates))
    question = (f"Odredi najmanji zajednički nazivnik razlomaka "
                f"${_display(left)}$ i ${_display(right)}$.")
    hints = (
        "Najmanji zajednički nazivnik sadrži svaki faktor oba nazivnika u "
        "najvećem stepenu u kojem se pojavljuje.",
        f"Rastavi nazivnike: ${d1.display()}$ i ${d2.display()}$.",
        "Pomnoži sve različite faktore — zajednički faktor uzmi samo jednom.",
    )
    solution = (f"Nazivnici su ${d1.display()}$ i ${d2.display()}$. Najmanji "
                f"zajednički nazivnik je ${lcd_text}$ — djeljiv je s oba, a "
                "nijedan manji izraz nije.")
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, option_texts, hints, solution,
                    lcd_text,
                    [("d1", d1.display()), ("d2", d2.display())])


# ---------------------------------------------------------------------------
# 7) SABIRANJE / ODUZIMANJE
# ---------------------------------------------------------------------------

def _add_subtract_package(rng, level, lesson_id, lesson_title, concept):
    subtractive = concept == "subtract"
    if level == 1:
        shared = Polynomial.monomial(rng.choice((1, 1, 2)), 1)
        d1 = d2 = shared
    elif level == 2:
        shared = _linear_factor(rng, 1)
        d1 = d2 = shared
    else:
        d1 = Polynomial.monomial(1, 1)
        d2 = _linear_factor(rng, 1)
        if d2.coefficient(0) == 0:
            raise DeterministicGenerationError("nazivnici nisu različiti")
    n1, n2 = _numerator_for(rng, level), _numerator_for(rng, level)
    left = RationalExpression.build(n1, d1)
    right = RationalExpression.build(n2, d2)
    result = left.subtract(right) if subtractive else left.add(right)
    canonical = result.canonical()
    if canonical.numerator.is_zero:
        raise DeterministicGenerationError("rezultat je nula — neupečatljivo")

    numerator_naive = n1 + (-n2 if subtractive else n2)
    wrong = [
        RationalExpression.build(numerator_naive, d1 * d2),
        RationalExpression.build(n1 + n2 if subtractive else n1 - n2,
                                 canonical.denominator),
        RationalExpression.build(canonical.numerator + Polynomial.one(),
                                 canonical.denominator),
        RationalExpression.build(-canonical.numerator, canonical.denominator),
    ]
    option_texts = _distinct_expression_options(canonical, wrong,
                                                compare="value")
    word = "razliku" if subtractive else "zbir"
    sign = "-" if subtractive else "+"
    question = (f"Izračunaj {word}: ${_display(left)} {sign} "
                f"{_display(right)}$.")
    lcd = left.lcd_with(right)
    hints = (
        "Razlomci se sabiraju i oduzimaju tek kad imaju zajednički nazivnik.",
        f"Najmanji zajednički nazivnik je ${lcd.display()}$ — proširi oba "
        "razlomka na njega.",
        f"{'Oduzmi' if subtractive else 'Saberi'} brojnike, nazivnik prepiši.",
    )
    solution = (f"Zajednički nazivnik je ${lcd.display()}$, pa je "
                f"${_display(left)} {sign} {_display(right)} = "
                f"{_display(canonical)}$"
                + (f", uz uslov {_exclusion_text(canonical.excluded)}."
                   if canonical.excluded else "."))
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, option_texts, hints, solution,
                    _display(canonical),
                    [("left", _display(left)), ("right", _display(right)),
                     ("op", sign)])


# ---------------------------------------------------------------------------
# 8) MNOŽENJE / DIJELJENJE — s vidljivim kraćenjem
# ---------------------------------------------------------------------------

def _multiply_divide_package(rng, level, lesson_id, lesson_title, concept):
    division = concept == "divide"
    shared = (Polynomial.monomial(1, 1) if level == 1
              else _linear_factor(rng, min(level, 2)))
    a = _nonzero(rng, -6, 6)
    b = rng.randint(2, 6)
    left = RationalExpression.build(Polynomial.constant(a), shared)
    if division:
        # (a/f) : (b/f) = a/b — faktor se krati kroz recipročnu vrijednost.
        right = RationalExpression.build(Polynomial.constant(b), shared)
        result = left.divide(right)
    else:
        right = RationalExpression.build(shared,
                                         Polynomial.constant(b))
        result = left.multiply(right)
    canonical = result.canonical()

    wrong_exprs = [
        RationalExpression.build(canonical.denominator, canonical.numerator)
        if not canonical.numerator.is_zero else canonical,
        RationalExpression.build(-canonical.numerator, canonical.denominator),
        RationalExpression.build(Polynomial.constant(a * b), shared),
        RationalExpression.build(Polynomial.constant(a), shared * shared),
    ]
    option_texts = _distinct_expression_options(canonical, wrong_exprs,
                                                compare="value")
    sign = ":" if division else "\\cdot"
    question = (f"Izračunaj: ${_display(left)} {sign} {_display(right)}$.")
    extra_note = ""
    if division:
        extra_note = (" Dijeljenje dodatno traži da djelilac nije nula, pa "
                      f"vrijedi i {_exclusion_text(result.excluded)}."
                      if result.excluded else "")
    hints = (
        ("Dijeljenje razlomkom je množenje njegovom recipročnom vrijednošću."
         if division else
         "Razlomci se množe: brojnik brojnikom, nazivnik nazivnikom."),
        (f"Zapiši: ${_display(left)} \\cdot "
         f"\\frac{{{shared.display()}}}{{{b}}}$." if division else
         f"Pomnoži brojnike i nazivnike pa potraži zajednički faktor."),
        f"Zajednički faktor ${shared.display()}$ se krati.",
    )
    solution = ((f"${_display(left)} {sign} {_display(right)} = "
                 f"{_display(canonical)}$ — zajednički faktor "
                 f"${shared.display()}$ se krati.")
                + extra_note)
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, option_texts, hints, solution,
                    _display(canonical),
                    [("left", _display(left)), ("right", _display(right)),
                     ("op", sign)])


# ---------------------------------------------------------------------------
# 9) DVOJNI RAZLOMAK
# ---------------------------------------------------------------------------

def _compound_package(rng, level, lesson_id, lesson_title, concept):
    a = _nonzero(rng, -6, 6)
    b = rng.randint(2, 6)
    if level == 1:
        upper = RationalExpression.build(Polynomial.constant(a),
                                         Polynomial.monomial(1, 1))
        lower = RationalExpression.build(Polynomial.constant(b),
                                         Polynomial.monomial(1, 1))
    elif level == 2:
        shared = _linear_factor(rng, 1)
        upper = RationalExpression.build(Polynomial.constant(a), shared)
        lower = RationalExpression.build(Polynomial.constant(b), shared)
    else:
        shared = _linear_factor(rng, 1)
        upper = RationalExpression.build(Polynomial.linear(1, _nonzero(rng, -4, 4)),
                                         shared)
        lower = RationalExpression.build(Polynomial.constant(b), shared)
    result = upper.divide(lower)
    canonical = result.canonical()
    wrong = [
        upper.multiply(lower).canonical(),
        RationalExpression.build(canonical.denominator, canonical.numerator)
        if not canonical.numerator.is_zero else canonical,
        RationalExpression.build(-canonical.numerator, canonical.denominator),
        RationalExpression.build(canonical.numerator.scaled(b),
                                 canonical.denominator),
    ]
    option_texts = _distinct_expression_options(canonical, wrong,
                                                compare="value")
    question = (f"Pojednostavi dvojni razlomak: "
                f"$\\frac{{{_display(upper)}}}{{{_display(lower)}}}$.")
    hints = (
        "Dvojni razlomak je dijeljenje: gornji razlomak podijeljen donjim.",
        f"Zapiši: ${_display(upper)} : {_display(lower)}$.",
        "Dijeljenje pretvori u množenje recipročnom vrijednošću pa krati.",
    )
    solution = (f"$\\frac{{{_display(upper)}}}{{{_display(lower)}}} = "
                f"{_display(upper)} : {_display(lower)} = "
                f"{_display(canonical)}$.")
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, option_texts, hints, solution,
                    _display(canonical),
                    [("upper", _display(upper)), ("lower", _display(lower))])


# ---------------------------------------------------------------------------
# 10) SREĐIVANJE IZRAZA — kombinovan korak pa kanonski oblik
# ---------------------------------------------------------------------------

def _simplify_combined_package(rng, level, lesson_id, lesson_title, concept):
    x = Polynomial.monomial(1, 1)
    a = _nonzero(rng, -5, 5)
    b = _nonzero(rng, -5, 5)
    if level == 1:
        left = RationalExpression.build(Polynomial.constant(a), x)
        right = RationalExpression.build(Polynomial.constant(b), x)
    elif level == 2:
        left = RationalExpression.build(Polynomial.constant(a), x)
        right = RationalExpression.build(Polynomial.constant(b),
                                         Polynomial.monomial(1, 2))
    else:
        shared = _linear_factor(rng, 1)
        left = RationalExpression.build(Polynomial.constant(a), x)
        right = RationalExpression.build(Polynomial.constant(b), shared)
    result = left.add(right)
    canonical = result.canonical()
    if canonical.numerator.is_zero:
        raise DeterministicGenerationError("rezultat je nula")
    wrong = [
        RationalExpression.build(Polynomial.constant(a + b),
                                 left.denominator * right.denominator),
        RationalExpression.build(canonical.numerator + Polynomial.one(),
                                 canonical.denominator),
        RationalExpression.build(-canonical.numerator, canonical.denominator),
        RationalExpression.build(Polynomial.constant(a + b),
                                 canonical.denominator),
    ]
    option_texts = _distinct_expression_options(canonical, wrong,
                                                compare="value")
    question = (f"Sredi izraz ${_display(left)} + {_display(right)}$ u jedan "
                "razlomak.")
    lcd = left.lcd_with(right)
    hints = (
        "Sređivanje znači zapisati cio izraz kao JEDAN razlomak u "
        "najjednostavnijem obliku.",
        f"Zajednički nazivnik je ${lcd.display()}$.",
        "Poslije sabiranja provjeri može li se rezultat skratiti.",
    )
    solution = (f"$ {_display(left)} + {_display(right)} = "
                f"{_display(canonical)}$"
                + (f", uz {_exclusion_text(canonical.excluded)}."
                   if canonical.excluded else "."))
    return _package(lesson_id, lesson_title, "rational_expression_direct",
                    concept, level, question, option_texts, hints, solution,
                    _display(canonical),
                    [("left", _display(left)), ("right", _display(right))])


# ---------------------------------------------------------------------------
# 11) JEDNAČINE S ALGEBARSKIM RAZLOMCIMA
# ---------------------------------------------------------------------------

def _fraction_equation_package(rng, level, lesson_id, lesson_title, concept):
    x_poly = Polynomial.monomial(1, 1)
    if level == 1:
        # a/x = b  →  x = a/b
        a = _nonzero(rng, -12, 12)
        b = _nonzero(rng, -6, 6)
        left = RationalExpression.build(Polynomial.constant(a), x_poly)
        right = RationalExpression.build(Polynomial.constant(b),
                                         Polynomial.one())
        equation_text = f"\\frac{{{a}}}{{x}} = {b}"
    elif level == 2:
        # (x+a)/(x+b) = c, c != 1
        a = _nonzero(rng, -6, 6)
        b = _nonzero(rng, -6, 6)
        c = rng.choice((2, 3, -2, 4))
        if a == b:
            raise DeterministicGenerationError("degenerisana jednačina")
        left = RationalExpression.build(Polynomial.linear(1, a),
                                        Polynomial.linear(1, b))
        right = RationalExpression.build(Polynomial.constant(c),
                                         Polynomial.one())
        equation_text = (f"\\frac{{{Polynomial.linear(1, a).display()}}}"
                         f"{{{Polynomial.linear(1, b).display()}}} = {c}")
    else:
        # a/(x+b) = c/(x+d) — kandidat može biti i isključen (nema rješenja)
        a = _nonzero(rng, -8, 8)
        c = _nonzero(rng, -8, 8)
        b = _nonzero(rng, -5, 5)
        d = _nonzero(rng, -5, 5)
        if a == c or b == d:
            raise DeterministicGenerationError("degenerisana jednačina")
        left = RationalExpression.build(Polynomial.constant(a),
                                        Polynomial.linear(1, b))
        right = RationalExpression.build(Polynomial.constant(c),
                                         Polynomial.linear(1, d))
        equation_text = (f"\\frac{{{a}}}{{{Polynomial.linear(1, b).display()}}}"
                         f" = \\frac{{{c}}}{{{Polynomial.linear(1, d).display()}}}")
    outcome = solve_linear_rational_equation(left, right)
    domain_text = (_exclusion_text(outcome.excluded)
                   if outcome.excluded else "svaki $x$")

    if outcome.status == "unique":
        answer_display = f"x = {core.fraction_display(outcome.solution)}"
        answer_value = outcome.solution
        candidates = [answer_value + 1, answer_value - 1, -answer_value,
                      answer_value * 2]
        option_texts = None
        distractors = candidates
    elif outcome.status == "excluded_root":
        answer_display = "jednačina nema rješenja"
        answer_value = None
        excluded_candidate = outcome.solution
        option_texts = (
            "jednačina nema rješenja",
            f"$x = {core.fraction_display(excluded_candidate)}$",
            f"$x = {core.fraction_display(excluded_candidate + 1)}$",
            f"$x = {core.fraction_display(-excluded_candidate)}$"
            if excluded_candidate != 0 else
            f"$x = {core.fraction_display(excluded_candidate - 1)}$",
        )
        distractors = ()
    else:
        raise DeterministicGenerationError("neupečatljiva klasifikacija")

    question = (f"Riješi jednačinu: ${equation_text}$.")
    hints = (
        "Prvo zapiši uslove definisanosti — nazivnik ne smije biti nula.",
        f"Uslov: {domain_text}. Pomnoži obje strane zajedničkim nazivnikom.",
        "Riješi dobijenu linearnu jednačinu pa provjeri da rješenje ne "
        "upada u zabranjene vrijednosti.",
    )
    if outcome.status == "unique":
        check_left = left.evaluate(outcome.solution)
        solution = (f"Uslov definisanosti: {domain_text}. Množenjem "
                    "zajedničkim nazivnikom jednačina postaje linearna i "
                    f"daje $x = {core.fraction_display(outcome.solution)}$. "
                    f"Provjera uvrštavanjem: obje strane iznose "
                    f"${core.fraction_display(check_left)}$, a rješenje ne "
                    "upada u zabranjene vrijednosti.")
        return core.build_package(
            lesson_id=lesson_id, lesson_title=lesson_title,
            family_id="rational_equation_direct", operation=concept,
            level=level, question=question, answer_value=answer_value,
            answer_display=answer_display,
            distractor_values=distractors, hints=hints, solution=solution,
            signature_parameters=[("equation", equation_text)],
            required_conditions=[concept],
            relevant_objects=["razlomljena jednačina"],
            generator_version=GENERATOR_VERSION,
            display_of=lambda value: f"x = {core.fraction_display(value)}")
    solution = (f"Uslov definisanosti: {domain_text}. Linearni postupak daje "
                f"kandidata $x = {core.fraction_display(outcome.solution)}$, "
                "ali ta vrijednost NIJE u domenu jednačine (nazivnik bi bio "
                "nula), pa jednačina nema rješenja.")
    return _package(lesson_id, lesson_title, "rational_equation_direct",
                    concept, level, question, option_texts, hints, solution,
                    answer_display, [("equation", equation_text)],
                    accepted=("nema rješenja",))


def _double_fraction_equation_package(rng, level, lesson_id, lesson_title,
                                      concept):
    # \frac{x/a + b}{c} = d  →  x = a(cd - b); sve egzaktno.
    a = rng.randint(2, 5)
    b = _nonzero(rng, -6, 6)
    c = rng.randint(2, 4 + level)
    d = _nonzero(rng, -5, 5)
    x_value = Fraction(a) * (Fraction(c) * d - b)
    if level == 1 and x_value.denominator != 1:
        raise DeterministicGenerationError("nivo 1 traži cjelobrojno rješenje")
    equation_text = (f"\\frac{{\\frac{{x}}{{{a}}} + "
                     f"{core.parenthesized(str(b))}}}{{{c}}} = {d}")
    inner = Fraction(c) * d
    question = f"Riješi jednačinu: ${equation_text}$."
    hints = (
        "Oslobodi se vanjskog razlomka: pomnoži obje strane njegovim "
        "nazivnikom.",
        f"Dobijaš $\\frac{{x}}{{{a}}} + {core.parenthesized(str(b))} = "
        f"{core.fraction_display(inner)}$.",
        f"Prebaci ${b}$ na desnu stranu pa pomnoži sa ${a}$.",
    )
    solution = (f"Množenjem sa ${c}$: $\\frac{{x}}{{{a}}} + "
                f"{core.parenthesized(str(b))} = {core.fraction_display(inner)}$, "
                f"pa je $\\frac{{x}}{{{a}}} = "
                f"{core.fraction_display(inner - b)}$ i "
                f"$x = {core.fraction_display(x_value)}$. Provjera "
                "uvrštavanjem potvrđuje jednakost.")
    candidates = [x_value + a, x_value - a, -x_value, Fraction(c) * d - b,
                  x_value + 1]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="rational_equation_direct", operation=concept,
        level=level, question=question, answer_value=x_value,
        answer_display=f"x = {core.fraction_display(x_value)}",
        distractor_values=candidates, hints=hints, solution=solution,
        signature_parameters=[("equation", equation_text)],
        required_conditions=[concept],
        relevant_objects=["dvojni razlomak"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: f"x = {core.fraction_display(value)}")
