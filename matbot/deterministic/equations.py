"""Deterministički generator porodice jednostavnih linearnih jednačina.

Jedna semantička porodica (`linear_equation_direct`) s oblicima koje nose
parametri ugovora (`shapes`, `number_domain`):

  • one_step_additive        — x ± a = b (na višim nivoima više konstanti);
  • one_step_multiplicative  — a·x = b i x : a = b, uvijek egzaktno rješivo;
  • parentheses              — a(x + b) = c s cjelobrojnim rješenjem;
  • check_solution           — „koji od ponuđenih brojeva je rješenje“;
  • check_inequality         — „koji od ponuđenih brojeva zadovoljava
                               nejednačinu“ (stroga nejednakost, tačno jedna
                               opcija u skupu rješenja).

MATEMATIČKI AUTORITET: rješenje se KONSTRUIŠE unaprijed (server bira x pa
gradi jednačinu oko njega), nikad ne „rješava“ model. Linearna jednačina ima
jedinstveno rješenje, pa je svaka opcija različita od konstruisanog rješenja
dokazano pogrešna; za nejednačinu se sve tri pogrešne opcije biraju IZVAN
skupa rješenja. Provjera uvrštavanjem u rješenju je čisto brojevni lanac koji
mathcheck nezavisno dokazuje.

NIVO = broj računskih koraka do rješenja (1, 2 ili 3) — dokaz težine time
opisuje stvarni postupak, ne proznu procjenu.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("linear_equation_direct",)
GENERATOR_VERSION = "deteq-1"

_SUPPORTED_SHAPES = frozenset({
    "one_step_additive", "one_step_multiplicative", "parentheses",
    "check_solution", "check_inequality",
})
_SUPPORTED_DOMAINS = frozenset({"integer", "rational"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    shapes = set(parameters.get("shapes") or ())
    if not shapes or not shapes <= _SUPPORTED_SHAPES:
        return False
    return parameters.get("number_domain") in _SUPPORTED_DOMAINS


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    shapes = tuple(parameters["shapes"])
    domain = parameters["number_domain"]
    for _ in range(60):
        try:
            shape = rng.choice(shapes)
            builder = {
                "one_step_additive": _additive_package,
                "one_step_multiplicative": _multiplicative_package,
                "parentheses": _parentheses_package,
                "check_solution": _check_solution_package,
                "check_inequality": _check_inequality_package,
            }[shape]
            return builder(rng, level, domain, lesson_id, lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


# ---------------------------------------------------------------------------
# VRIJEDNOSTI I PRIKAZ
# ---------------------------------------------------------------------------

def _value(rng, domain, level, small=False):
    if domain == "integer":
        magnitude = {1: 12, 2: 20, 3: 30}[level]
        if small:
            magnitude = 9
        value = Fraction(rng.randint(1, magnitude))
        if level > 1 and rng.random() < 0.5:
            value = -value
        return value
    den = rng.randint(2, 6 if level == 1 else 9)
    num = rng.randint(1, den + (0 if level == 1 else 3))
    value = Fraction(num, den)
    if level > 1 and rng.random() < 0.4:
        value = -value
    return value


def _show(value: Fraction) -> str:
    return core.plain_fraction_display(value)


def _final(value: Fraction) -> str:
    return core.fraction_display(value)


def _term(value: Fraction) -> str:
    """Operand u izrazu: negativan i razlomljen ide u zagrade po potrebi."""
    return core.parenthesized(_show(value))


def _distractor_pool(solution, scale=Fraction(1)):
    step = scale if scale > 0 else Fraction(1)
    pool = [-solution, solution + step, solution - step,
            solution + 2 * step, solution - 2 * step, solution * 2]
    if solution != 0:
        pool.append(solution / 2)
    return pool


# ---------------------------------------------------------------------------
# x ± a = b  (nivo = broj konstanti koje treba prebaciti/srediti)
# ---------------------------------------------------------------------------

def _additive_package(rng, level, domain, lesson_id, lesson_title):
    solution = _value(rng, domain, level)
    first = _value(rng, domain, level)
    sign_first = rng.random() < 0.5
    left_terms = [f"x + {_term(first)}" if sign_first else f"x - {_term(first)}"]
    value_sum = first if sign_first else -first
    steps = 1
    if level >= 2:
        second = _value(rng, domain, level, small=True)
        sign_second = rng.random() < 0.5
        left_terms.append(f" + {_term(second)}" if sign_second
                          else f" - {_term(second)}")
        value_sum += second if sign_second else -second
        steps += 1
    if value_sum == 0:
        raise DeterministicGenerationError("degenerisana jednačina")
    right = solution + value_sum
    if level == 3:
        # Desna strana je i sama zbir — još jedan korak sređivanja.
        part = _value(rng, domain, level, small=True)
        if part == right or part == 0:
            raise DeterministicGenerationError("degenerisana desna strana")
        rhs_display = f"{_term(right - part)} + {_term(part)}"
        steps += 1
    else:
        rhs_display = _show(right)
    equation = "".join(left_terms) + f" = {rhs_display}"
    question = f"Riješi jednačinu: ${equation}$"

    isolate_chain = f"x = {_show(right)} - {_term(value_sum)} = {_show(solution)}"
    check_value = solution + value_sum
    check_chain = f"{_show(check_value)}"
    hint1 = ("Nepoznatu ostavi na jednoj strani, a sve poznate članove prebaci "
             "na drugu stranu sa suprotnim predznakom.")
    hint2 = (f"Svedi poznate članove: svi zajedno iznose ${_show(value_sum)}$, "
             f"pa jednačina glasi $x + {_term(value_sum)} = {_show(right)}$.")
    hint3 = f"Sada prebaci: ${isolate_chain.split('=')[0].strip()} = {_show(right)} - {_term(value_sum)}$ — izračunaj razliku."
    solution_text = (
        f"Prebacimo poznate članove na desnu stranu: ${isolate_chain}$. "
        f"Provjera uvrštavanjem: ${_show(solution)} + {_term(value_sum)} "
        f"= {check_chain}$, što je tačno desna strana. "
        f"Rješenje je $x = {_final(solution)}$.")
    scale = Fraction(1, solution.denominator) if solution.denominator > 1 \
        else Fraction(1)
    return _solving_package(rng, level, domain, lesson_id, lesson_title,
                            "one_step_additive", question, equation, solution,
                            (hint1, hint2, hint3), solution_text, scale)


# ---------------------------------------------------------------------------
# a·x = b  i  x : a = b
# ---------------------------------------------------------------------------

def _multiplicative_package(rng, level, domain, lesson_id, lesson_title):
    solution = _value(rng, domain, level)
    use_division = level >= 2 and rng.random() < 0.4
    if use_division:
        divisor = _value(rng, "integer", 1, small=True)
        if divisor == 0:
            raise DeterministicGenerationError("djelilac nula")
        quotient = solution / divisor
        equation = f"x : {_term(divisor)} = {_show(quotient)}"
        question = f"Riješi jednačinu: ${equation}$"
        isolate = (f"x = {_show(quotient)} \\cdot {_term(divisor)} "
                   f"= {_show(solution)}")
        hint2 = ("Dijeljenje poništi množenjem: pomnoži obje strane "
                 f"djeliocem ${_show(divisor)}$.")
        check = (f"{_show(solution)} : {_term(divisor)} = {_show(quotient)}")
        operation = "solve_division"
    else:
        coefficient = _value(rng, "integer", min(level, 2), small=True)
        if abs(coefficient) <= 1:
            raise DeterministicGenerationError("koeficijent trivijalan")
        product = coefficient * solution
        equation = f"{_show(coefficient)}x = {_show(product)}"
        question = f"Riješi jednačinu: ${equation}$"
        isolate = (f"x = {_show(product)} : {_term(coefficient)} "
                   f"= {_show(solution)}")
        hint2 = ("Množenje poništi dijeljenjem: podijeli obje strane "
                 f"koeficijentom ${_show(coefficient)}$.")
        check = (f"{_show(coefficient)} \\cdot {_term(solution)} "
                 f"= {_show(product)}")
        operation = "solve_multiplication"
    hint1 = ("Nepoznata je pomnožena (ili podijeljena) poznatim brojem — "
             "primijeni suprotnu operaciju na OBJE strane jednačine.")
    hint3 = f"Postupak: ${isolate.split('=')[0].strip()} = {isolate.split('=', 1)[1].strip()}$."
    solution_text = (
        f"Primijenimo suprotnu operaciju: ${isolate}$. "
        f"Provjera uvrštavanjem: ${check}$, što se slaže sa zadatom "
        f"jednačinom. Rješenje je $x = {_final(solution)}$.")
    scale = Fraction(1, solution.denominator) if solution.denominator > 1 \
        else Fraction(1)
    return _solving_package(rng, level, domain, lesson_id, lesson_title,
                            operation, question, equation, solution,
                            (hint1, hint2, hint3), solution_text, scale)


# ---------------------------------------------------------------------------
# a(x + b) = c  — uvijek cjelobrojno rješenje
# ---------------------------------------------------------------------------

def _parentheses_package(rng, level, domain, lesson_id, lesson_title):
    solution = Fraction(rng.randint(-12, 12))
    inner = Fraction(rng.randint(1, 9))
    if rng.random() < 0.5:
        inner = -inner
    factor = Fraction(rng.choice((2, 3, 4, 5, -2, -3)[:4 if level == 1 else 6]))
    inner_sum = solution + inner
    rhs = factor * inner_sum
    tail = Fraction(0)
    if level == 3:
        tail = Fraction(rng.randint(2, 9))
        if rng.random() < 0.5:
            tail = -tail
        rhs = rhs + tail
        equation = (f"{_show(factor)}(x + {_term(inner)}) + {_term(tail)} "
                    f"= {_show(rhs)}")
    else:
        equation = f"{_show(factor)}(x + {_term(inner)}) = {_show(rhs)}"
    question = f"Riješi jednačinu: ${equation}$"
    without_tail = rhs - tail
    inner_value = without_tail / factor
    hint1 = ("Prvo oslobodi zagradu: podijeli obje strane koeficijentom uz "
             "zagradu (ili prebaci slobodni član pa podijeli).")
    step_chain = []
    if tail != 0:
        step_chain.append(
            f"{_show(factor)}(x + {_term(inner)}) = {_show(rhs)} - "
            f"{_term(tail)} = {_show(without_tail)}")
    step_chain.append(
        f"x + {_term(inner)} = {_show(without_tail)} : {_term(factor)} "
        f"= {_show(inner_value)}")
    step_chain.append(
        f"x = {_show(inner_value)} - {_term(inner)} = {_show(solution)}")
    hint2 = f"Prvi korak: ${step_chain[0]}$."
    hint3 = f"Zatim: ${step_chain[-1].split('=')[0].strip()} = {_show(inner_value)} - {_term(inner)}$ — izračunaj."
    check_inner = solution + inner
    check = (f"{_show(factor)} \\cdot {_term(check_inner)} "
             + (f"+ {_term(tail)} " if tail != 0 else "")
             + f"= {_show(rhs)}")
    solution_text = ("Rješavamo korak po korak: $"
                     + "$, zatim $".join(step_chain)
                     + f"$. Provjera: ${check}$. Rješenje je "
                     f"$x = {_final(solution)}$.")
    return _solving_package(rng, level, domain, lesson_id, lesson_title,
                            "solve_parentheses", question, equation, solution,
                            (hint1, hint2, hint3), solution_text, Fraction(1))


def _solving_package(rng, level, domain, lesson_id, lesson_title, operation,
                     question, equation, solution, hints, solution_text, scale):
    candidates = _distractor_pool(solution, scale)
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation=operation, level=level,
        question=question, answer_value=solution,
        answer_display=_final(solution), distractor_values=candidates,
        hints=hints, solution=solution_text,
        signature_parameters=[("equation", equation)],
        required_conditions=["linear_equation"],
        relevant_objects=["equation", domain],
        generator_version=GENERATOR_VERSION, display_of=core.fraction_display)


# ---------------------------------------------------------------------------
# PROVJERA RJEŠENJA — jednačina i (nivo 3) nejednačina
# ---------------------------------------------------------------------------

def _check_solution_package(rng, level, domain, lesson_id, lesson_title):
    solution = _value(rng, "integer", level)
    coefficient = Fraction(rng.randint(2, 5))
    constant = _value(rng, "integer", level, small=True)
    sign = rng.random() < 0.5
    rhs = coefficient * solution + (constant if sign else -constant)
    equation = (f"{_show(coefficient)}x + {_term(constant)} = {_show(rhs)}"
                if sign else
                f"{_show(coefficient)}x - {_term(constant)} = {_show(rhs)}")
    question = (f"Koji od ponuđenih brojeva je rješenje jednačine "
                f"${equation}$?")
    substitution = (f"{_show(coefficient)} \\cdot {_term(solution)} "
                    + (f"+ {_term(constant)}" if sign else f"- {_term(constant)}")
                    + f" = {_show(coefficient * solution)} "
                    + (f"+ {_term(constant)}" if sign else f"- {_term(constant)}")
                    + f" = {_show(rhs)}")
    hint1 = ("Rješenje jednačine je broj koji uvrštavanjem umjesto $x$ daje "
             "tačnu jednakost.")
    hint2 = "Uvrsti svaku opciju umjesto $x$ i izračunaj lijevu stranu."
    hint3 = ("Samo jedna opcija daje lijevu stranu jednaku desnoj — ostale "
             "daju drugu vrijednost.")
    solution_text = (f"Uvrstimo $x = {_show(solution)}$: ${substitution}$ — "
                     "jednakost je tačna, pa je to rješenje. Ostale opcije "
                     "ne zadovoljavaju jednačinu.")
    candidates = _distractor_pool(solution)
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation="check_solution",
        level=level, question=question, answer_value=solution,
        answer_display=_final(solution), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution_text,
        signature_parameters=[("equation", equation)],
        required_conditions=["check_solution"],
        relevant_objects=["equation", domain],
        generator_version=GENERATOR_VERSION, display_of=core.fraction_display)


def _check_inequality_package(rng, level, domain, lesson_id, lesson_title):
    coefficient = rng.randint(2, 5)
    bound = rng.randint(2, 12)
    less_than = rng.random() < 0.5
    rhs = coefficient * bound
    symbol = "<" if less_than else ">"
    inequality = f"{coefficient}x {symbol} {rhs}"
    question = (f"Koji od ponuđenih brojeva zadovoljava nejednačinu "
                f"${inequality}$?")
    offset = rng.randint(1, 4)
    correct = Fraction(bound - offset if less_than else bound + offset)
    outside = [bound, bound + 1 if less_than else bound - 1,
               bound + 4 if less_than else bound - 4]
    option_values = [correct, *(Fraction(v) for v in outside)]
    option_texts = tuple(f"${_show(v)}$" for v in option_values)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    check = (f"{coefficient} \\cdot {_term(correct)} "
             f"= {_show(coefficient * correct)}")
    hint1 = ("Broj zadovoljava nejednačinu ako uvrštavanjem umjesto $x$ "
             "nastane tačna nejednakost.")
    hint2 = "Uvrsti svaku opciju i uporedi lijevu stranu s desnom."
    hint3 = (f"Granica je $x {symbol} {bound}$ — provjeri koja opcija "
             "upada u taj skup rješenja.")
    solution_text = (f"Uvrstimo $x = {_show(correct)}$: ${check}$, a "
                     f"${_show(coefficient * correct)} {symbol} {rhs}$ je "
                     "tačna nejednakost. Ostale opcije ne zadovoljavaju "
                     "nejednačinu.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation="check_inequality",
        level=level, question=question, answer_value=correct,
        answer_display=_show(correct), distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution_text,
        signature_parameters=[("inequality", inequality)],
        required_conditions=["check_inequality"],
        relevant_objects=["inequality", domain],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")
