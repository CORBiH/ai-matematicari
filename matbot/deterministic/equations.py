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
# PP-1: relacije nepoznatog člana — ISTE rečenice koje rules.py renderuje u
# oba prompta (jedna istina, vidi matbot/practice_policy.py).
from matbot.practice_policy import UNKNOWN_ROLE_RELATIONS as _ROLE_RELATIONS

FAMILY_IDS = ("linear_equation_direct", "simple_quadratic_equation")
GENERATOR_VERSION = "deteq-1"

_SUPPORTED_SHAPES = frozenset({
    "one_step_additive", "one_step_multiplicative", "parentheses",
    "check_solution", "check_inequality",
    # Batch #2: rješavanje nejednačina, apsolutna vrijednost, razlomački i
    # kombinovani oblici, klasifikacije i ekvivalencije.
    "subtract_from", "fraction_form", "parentheses_combine",
    "solve_inequality_additive", "solve_inequality_multiplicative",
    "solve_inequality_sign_flip", "solve_inequality_parentheses",
    "absolute_value_equation", "absolute_value_inequality",
    "classification", "solution_count", "equivalence_choice",
})
_QUADRATIC_SHAPES = frozenset({
    "x_squared_equals_a", "factor_out_x", "perfect_square_trinomial",
})
# `rational_nonneg` (Q+, 6. razred): svi koeficijenti i rješenja nenegativni;
# `decimal`: koeficijenti i rješenja s konačnim decimalnim zapisom.
_SUPPORTED_DOMAINS = frozenset({"integer", "rational", "rational_nonneg",
                                "decimal"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    shapes = set(parameters.get("shapes") or ())
    if not shapes:
        return False
    if shapes <= _QUADRATIC_SHAPES:
        return True
    if not shapes <= _SUPPORTED_SHAPES:
        return False
    return parameters.get("number_domain") in _SUPPORTED_DOMAINS


def generate_package(lesson_id, lesson_title, parameters, level, rng=None,
                     policy=None):
    """`policy` je razriješena PP-1 politika (matbot/practice_policy.py) ili
    None. Kad politika traži metodu nepoznatog člana (6. razred), oblici se
    grade iz ULOGE nepoznatog člana — transpoziciona proza za te oblike NIKAD
    ne nastaje. Oblik bez varijante nepoznatog člana pod tom politikom pada
    zatvoreno (DET-G6: buduće vezivanje oblika na niži razred ne smije tiho
    isporučiti pogrešnu metodu); model-put tada preuzima lekciju."""
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    shapes = tuple(parameters["shapes"])
    # Kvadratna porodica ne nosi number_domain — radi nad cijelim brojevima.
    domain = parameters.get("number_domain") or "integer"
    unknown_member = (policy is not None and getattr(
        policy, "equation_method", "") == "unknown_member")
    for _ in range(60):
        try:
            shape = rng.choice(shapes)
            if unknown_member:
                return _unknown_member_package(rng, level, domain, shape,
                                               lesson_id, lesson_title)
            builder = {
                "one_step_additive": _additive_package,
                "one_step_multiplicative": _multiplicative_package,
                "parentheses": _parentheses_package,
                "check_solution": _check_solution_package,
                "check_inequality": _check_inequality_package,
                "subtract_from": _subtract_from_package,
                "fraction_form": _fraction_form_package,
                "parentheses_combine": _parentheses_combine_package,
                "solve_inequality_additive": _solve_inequality_package,
                "solve_inequality_multiplicative": _solve_inequality_package,
                "solve_inequality_sign_flip": _solve_inequality_package,
                "solve_inequality_parentheses": _solve_inequality_package,
                "absolute_value_equation": _absolute_equation_package,
                "absolute_value_inequality": _absolute_inequality_package,
                "classification": _classification_package,
                "solution_count": _solution_count_package,
                "equivalence_choice": _equivalence_package,
                "x_squared_equals_a": _quadratic_package,
                "factor_out_x": _quadratic_package,
                "perfect_square_trinomial": _quadratic_package,
            }[shape]
            if shape in _QUADRATIC_SHAPES:
                return _quadratic_package(rng, level, shape, lesson_id,
                                          lesson_title)
            if shape.startswith("solve_inequality"):
                return _solve_inequality_package(rng, level, domain, shape,
                                                 lesson_id, lesson_title)
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
    if domain == "decimal":
        value = Fraction(rng.randint(2, 99 if not small else 30), 10)
        return value
    den = rng.randint(2, 6 if level == 1 else 9)
    num = rng.randint(1, den + (0 if level == 1 else 3))
    value = Fraction(num, den)
    if domain == "rational" and level > 1 and rng.random() < 0.4:
        value = -value
    return value


def _domain_show(domain, value: Fraction) -> str:
    if domain == "decimal" and core.is_terminating_decimal(value):
        return core.decimal_display(value)
    return core.plain_fraction_display(value)


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
                     question, equation, solution, hints, solution_text, scale,
                     method_id="transposition", distractors=None,
                     evidence=None, answer_display=None, display_of=None):
    """`method_id` je PP-1 provenijencija: zatečeni graditelji uče
    prebacivanje/balansiranje ("transposition"), graditelji nepoznatog člana
    prosljeđuju "unknown_member". Server je poredi sa zabranama politike."""
    candidates = distractors if distractors is not None \
        else _distractor_pool(solution, scale)
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation=operation, level=level,
        question=question, answer_value=solution,
        answer_display=answer_display or _final(solution),
        distractor_values=candidates,
        hints=hints, solution=solution_text,
        signature_parameters=[("equation", equation)],
        required_conditions=["linear_equation"],
        relevant_objects=["equation", domain],
        generator_version=GENERATOR_VERSION,
        display_of=display_of or core.fraction_display,
        method_id=method_id, evidence=evidence)


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


# ---------------------------------------------------------------------------
# a - x = b  (Batch #2) — nepoznata se oduzima
# ---------------------------------------------------------------------------

def _subtract_from_package(rng, level, domain, lesson_id, lesson_title):
    solution = abs(_value(rng, domain, level))
    difference = abs(_value(rng, domain, level, small=True))
    left = solution + difference
    show = lambda v: _domain_show(domain, v)
    equation = f"{show(left)} - x = {show(difference)}"
    question = f"Riješi jednačinu: ${equation}$"
    isolate = f"x = {show(left)} - {show(difference)} = {show(solution)}"
    check = f"{show(left)} - {core.parenthesized(show(solution))} = {show(difference)}"
    hint1 = ("Kad se nepoznata oduzima, prebaci je na jednu stranu a poznati "
             "član na drugu: x je razlika lijeve i desne strane.")
    hint2 = f"Zapiši: $x = {show(left)} - {show(difference)}$."
    hint3 = f"Izračunaj razliku ${show(left)} - {show(difference)}$ i provjeri uvrštavanjem."
    solution_text = (f"Prebacimo: ${isolate}$. Provjera: ${check}$ — tačno. "
                     f"Rješenje je $x = {_final(solution)}$.")
    scale = Fraction(1, solution.denominator) if solution.denominator > 1 \
        else Fraction(1)
    return _solving_package(rng, level, domain, lesson_id, lesson_title,
                            "subtract_from", question, equation, solution,
                            (hint1, hint2, hint3), solution_text, scale)


# ---------------------------------------------------------------------------
# RAZLOMAČKI OBLIK: x/a ± b = c  i  (x ± a)/b = c  (Batch #2)
# ---------------------------------------------------------------------------

def _fraction_form_package(rng, level, domain, lesson_id, lesson_title):
    divisor = rng.randint(2, 6 if level == 1 else 9)
    solution = Fraction(divisor * rng.randint(1, 8 if level < 3 else 12))
    if level > 1 and rng.random() < 0.5:
        solution = -solution
    if level < 3:
        offset = Fraction(rng.randint(1, 9))
        sign = rng.random() < 0.5
        rhs = solution / divisor + (offset if sign else -offset)
        equation = (f"\\frac{{x}}{{{divisor}}} + {_term(offset)} = {_show(rhs)}"
                    if sign else
                    f"\\frac{{x}}{{{divisor}}} - {_term(offset)} = {_show(rhs)}")
        step1 = (f"\\frac{{x}}{{{divisor}}} = {_show(rhs)} "
                 f"{'-' if sign else '+'} {_term(offset)} = "
                 f"{_show(solution / divisor)}")
        step2 = (f"x = {_show(solution / divisor)} \\cdot {divisor} "
                 f"= {_show(solution)}")
        hint2 = f"Prvo prebaci slobodni član: ${step1.split('=')[0].strip()} = {_show(solution / divisor)}$."
    else:
        offset = Fraction(divisor * rng.randint(1, 3))
        sign = rng.random() < 0.5
        inner = solution + (offset if sign else -offset)
        quotient = inner / divisor
        if quotient.denominator != 1:
            raise DeterministicGenerationError("količnik nije cio")
        equation = (f"\\frac{{x + {_show(offset)}}}{{{divisor}}} = {_show(quotient)}"
                    if sign else
                    f"\\frac{{x - {_show(offset)}}}{{{divisor}}} = {_show(quotient)}")
        step1 = (f"x {'+' if sign else '-'} {_show(offset)} = "
                 f"{_show(quotient)} \\cdot {divisor} = {_show(inner)}")
        step2 = (f"x = {_show(inner)} {'-' if sign else '+'} {_show(offset)} "
                 f"= {_show(solution)}")
        hint2 = f"Pomnoži obje strane sa ${divisor}$: ${step1.split('=')[0].strip()} = {_show(inner)}$."
    question = f"Riješi jednačinu: ${equation}$"
    hint1 = ("Razlomak uz nepoznatu ukloni množenjem obje strane nazivnikom, "
             "a slobodne članove prebacuj sa suprotnim predznakom.")
    hint3 = f"Sljedeći korak: ${step2.split('=')[0].strip()} = {step2.split('=', 1)[1].strip()}$."
    solution_text = (f"Rješavamo korak po korak: ${step1}$, zatim ${step2}$. "
                     f"Rješenje je $x = {_final(solution)}$.")
    return _solving_package(rng, level, domain, lesson_id, lesson_title,
                            "fraction_form", question, equation, solution,
                            (hint1, hint2, hint3), solution_text, Fraction(divisor))


# ---------------------------------------------------------------------------
# ZAGRADA + SVOĐENJE SLIČNIH ČLANOVA: a(x + b) + cx = d  (Batch #2)
# ---------------------------------------------------------------------------

def _parentheses_combine_package(rng, level, domain, lesson_id, lesson_title):
    solution = Fraction(rng.randint(-9, 9))
    factor = rng.randint(2, 4 if level == 1 else 6)
    inner = rng.randint(1, 9)
    if rng.random() < 0.5:
        inner = -inner
    extra = rng.randint(1, 5 if level < 3 else 9)
    if rng.random() < 0.5:
        extra = -extra
    combined = factor + extra
    if combined == 0:
        raise DeterministicGenerationError("nepoznata nestaje")
    rhs = combined * solution + factor * inner
    extra_term = f"+ {extra}x" if extra > 0 else f"- {abs(extra)}x"
    equation = f"{factor}(x + {_term(Fraction(inner))}) {extra_term} = {rhs}"
    question = f"Riješi jednačinu: ${equation}$"
    expanded = (f"{factor}x + {_term(Fraction(factor * inner))} {extra_term} "
                f"= {rhs}")
    combined_display = f"{combined}x + {_term(Fraction(factor * inner))} = {rhs}"
    step_isolate = (f"{combined}x = {rhs} - {_term(Fraction(factor * inner))} "
                    f"= {combined * solution}")
    step_solve = (f"x = {combined * solution} : {_term(Fraction(combined))} "
                  f"= {_show(solution)}")
    hint1 = ("Prvo oslobodi zagradu množenjem, zatim saberi slične članove "
             "(sve članove s nepoznatom), pa tek onda rješavaj.")
    hint2 = f"Poslije množenja i svođenja: ${combined_display}$."
    hint3 = f"Sada: ${step_isolate}$ — još samo podijeli koeficijentom."
    check_inner = solution + inner
    check = (f"{factor} \\cdot {_term(check_inner)} "
             f"{'+' if extra > 0 else '-'} {abs(extra)} \\cdot "
             f"{_term(solution)} = {rhs}")
    solution_text = (f"Oslobodimo zagradu: ${expanded}$, svedemo slične "
                     f"članove: ${combined_display}$, pa ${step_isolate}$ i "
                     f"${step_solve}$. Provjera: ${check}$ — tačno. "
                     f"Rješenje je $x = {_final(solution)}$.")
    return _solving_package(rng, level, domain, lesson_id, lesson_title,
                            "parentheses_combine", question, equation, solution,
                            (hint1, hint2, hint3), solution_text, Fraction(1))


# ---------------------------------------------------------------------------
# RJEŠAVANJE NEJEDNAČINA (Batch #2) — opcije su SKUPOVI RJEŠENJA
# ---------------------------------------------------------------------------

def _inequality_text(symbol, bound_display):
    return f"$x {symbol} {bound_display}$"


def _solve_inequality_package(rng, level, domain, shape, lesson_id,
                              lesson_title):
    show = lambda v: _domain_show(domain, v)
    less = rng.random() < 0.5
    symbol = "<" if less else ">"
    flipped = {"<": ">", ">": "<"}[symbol]

    if shape == "solve_inequality_additive":
        bound = _value(rng, domain, level)
        offset = abs(_value(rng, domain, level, small=True))
        sign = rng.random() < 0.5
        lhs_rhs = bound + (offset if sign else -offset)
        inequality = (f"x + {_term(offset)} {symbol} {show(lhs_rhs)}" if sign
                      else f"x - {_term(offset)} {symbol} {show(lhs_rhs)}")
        answer_symbol = symbol
        steps = (f"x {symbol} {show(lhs_rhs)} {'-' if sign else '+'} "
                 f"{_term(offset)}")
        rule = ("Sabiranje i oduzimanje ne mijenjaju smjer nejednakosti — "
                "prebaci poznati član sa suprotnim predznakom.")
    elif shape == "solve_inequality_parentheses":
        factor = rng.randint(2, 4)
        inner = rng.randint(1, 6)
        if rng.random() < 0.5:
            inner = -inner
        bound = Fraction(rng.randint(-9, 9))
        rhs = factor * (bound + inner)
        inequality = f"{factor}(x + {_term(Fraction(inner))}) {symbol} {rhs}"
        answer_symbol = symbol
        steps = f"x + {_term(Fraction(inner))} {symbol} {rhs} : {factor}"
        rule = ("Podijeli obje strane pozitivnim koeficijentom (smjer se ne "
                "mijenja), pa prebaci slobodni član.")
    else:
        # multiplicative / sign_flip
        force_negative = shape == "solve_inequality_sign_flip" or (
            level >= 2 and rng.random() < 0.5)
        coefficient = rng.randint(2, 6)
        if force_negative:
            coefficient = -coefficient
        bound = _value(rng, domain, level, small=True)
        if domain in ("rational_nonneg", "decimal") and coefficient < 0:
            coefficient = abs(coefficient)     # Q+/decimal: bez negativnih
            force_negative = False
        rhs = coefficient * bound
        inequality = f"{coefficient}x {symbol} {show(rhs)}"
        answer_symbol = flipped if coefficient < 0 else symbol
        steps = (f"x {answer_symbol} {show(rhs)} : "
                 f"{core.parenthesized(str(coefficient))}")
        rule = ("Dijeljenje POZITIVNIM brojem čuva smjer nejednakosti, a "
                "dijeljenje NEGATIVNIM brojem ga OBRĆE.")

    bound_display = show(bound)
    correct = _inequality_text(answer_symbol, bound_display)
    wrong_symbol = {"<": ">", ">": "<"}[answer_symbol]
    wrong_options = [_inequality_text(wrong_symbol, bound_display)]
    shifted = bound + 1 if bound.denominator == 1 else bound + Fraction(1, bound.denominator)
    wrong_options.append(_inequality_text(answer_symbol, show(shifted)))
    wrong_options.append(_inequality_text(wrong_symbol, show(shifted)))
    option_texts = (correct, *wrong_options)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("skupovi rješenja nisu jedinstveni")

    question = f"Riješi nejednačinu: ${inequality}$"
    hint2 = f"Postupak: ${steps}$."
    hint3 = ("Rješenje čitaš kao skup: svi brojevi koji zadovoljavaju "
             "dobijenu nejednakost.")
    # Provjera probnim brojem drži rješenje vezanim za PRAVI skup.
    probe = bound + (1 if answer_symbol == ">" else -1)
    solution_text = (f"{rule} Dakle: ${steps}$, pa je rješenje "
                     f"$x {answer_symbol} {bound_display}$. Probni broj "
                     f"${show(probe)}$ zadovoljava polaznu nejednačinu, što "
                     "potvrđuje smjer.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation=shape, level=level,
        question=question, answer_value=(answer_symbol, str(bound)),
        answer_display=f"x {answer_symbol} {bound_display}",
        distractor_values=(), hints=(rule, hint2, hint3),
        solution=solution_text,
        signature_parameters=[("inequality", inequality)],
        required_conditions=[shape], relevant_objects=["inequality", domain],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", accepted_answers=(f"x {answer_symbol} {bound_display}",),
        # Zatečena proza uči prebacivanje/balansiranje — provenijencija to
        # čini provjerljivim (u 7-9. razredu dozvoljeno, u 6. strukturno pada).
        method_id="transposition")


# ---------------------------------------------------------------------------
# APSOLUTNA VRIJEDNOST (Batch #2): |x| = a, |x - b| = a; |x| < a, |x| > a
# ---------------------------------------------------------------------------

def _absolute_equation_package(rng, level, domain, lesson_id, lesson_title):
    a = rng.randint(1, 9 if level == 1 else 15)
    if level == 1:
        equation = f"|x| = {a}"
        first, second = Fraction(a), Fraction(-a)
        explain = (f"Apsolutnu vrijednost ${a}$ imaju tačno dva broja: "
                   f"${a}$ i ${-a}$.")
    elif level == 2:
        shift = rng.randint(1, 9)
        equation = f"|x - {shift}| = {a}"
        first, second = Fraction(shift + a), Fraction(shift - a)
        explain = (f"Izraz u apsolutnoj vrijednosti je ${a}$ ili ${-a}$: "
                   f"$x - {shift} = {a}$ daje $x = {shift + a}$, a "
                   f"$x - {shift} = {-a}$ daje $x = {shift - a}$.")
    else:
        coefficient = rng.randint(2, 4)
        shift = rng.randint(1, 6)
        value = coefficient * rng.randint(1, 5)
        a = value + shift if rng.random() < 0.5 else value
        first_num = a - shift
        second_num = -a - shift
        if first_num % coefficient or second_num % coefficient:
            raise DeterministicGenerationError("rješenja nisu cijela")
        equation = f"|{coefficient}x + {shift}| = {a}"
        first = Fraction(first_num, coefficient)
        second = Fraction(second_num, coefficient)
        explain = (f"Izraz je ${a}$ ili ${-a}$: iz "
                   f"${coefficient}x + {shift} = {a}$ slijedi "
                   f"$x = {_show(first)}$, a iz "
                   f"${coefficient}x + {shift} = {-a}$ slijedi "
                   f"$x = {_show(second)}$.")
    if first == second:
        raise DeterministicGenerationError("dvostruko rješenje")
    low, high = sorted((first, second))
    correct = f"$x = {_show(low)}$ ili $x = {_show(high)}$"
    wrong = [f"$x = {_show(high)}$",
             f"$x = {_show(-high)}$ ili $x = {_show(high + 1)}$",
             f"$x = {_show(low - 1)}$ ili $x = {_show(high + 1)}$"]
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = f"Riješi jednačinu: ${equation}$"
    hint1 = ("Apsolutna vrijednost je udaljenost od nule: |A| = a znači "
             "A = a ILI A = -a — jednačina se raspada na dva slučaja.")
    hint2 = "Postavi oba slučaja: izraz jednak pozitivnoj i negativnoj vrijednosti."
    hint3 = "Riješi svaki slučaj posebno — očekuj DVA rješenja."
    solution_text = (f"{explain} Rješenja su $x = {_show(low)}$ i "
                     f"$x = {_show(high)}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation="absolute_value_equation",
        level=level, question=question,
        answer_value=(str(low), str(high)),
        answer_display=f"x = {_show(low)} ili x = {_show(high)}",
        distractor_values=(), hints=(hint1, hint2, hint3),
        solution=solution_text,
        signature_parameters=[("equation", equation)],
        required_conditions=["absolute_value_equation"],
        relevant_objects=["equation", "integer"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", accepted_answers=(f"x = {_show(low)} ili x = {_show(high)}",))


def _absolute_inequality_package(rng, level, domain, lesson_id, lesson_title):
    a = rng.randint(2, 9 if level < 3 else 15)
    less = rng.random() < 0.5 if level > 1 else True
    if less:
        inequality = f"|x| < {a}"
        correct = f"${-a} < x < {a}$"
        wrong = [f"$x < {a}$", f"$x < {-a}$ ili $x > {a}$",
                 f"${-a - 1} < x < {a + 1}$"]
        explain = (f"Udaljenost od nule manja od ${a}$ znači da je $x$ "
                   f"IZMEĐU ${-a}$ i ${a}$: ${-a} < x < {a}$.")
        answer_display = f"{-a} < x < {a}"
    else:
        inequality = f"|x| > {a}"
        correct = f"$x < {-a}$ ili $x > {a}$"
        wrong = [f"$x > {a}$", f"${-a} < x < {a}$",
                 f"$x < {-a - 1}$ ili $x > {a + 1}$"]
        explain = (f"Udaljenost od nule veća od ${a}$ znači da je $x$ IZVAN "
                   f"segmenta: $x < {-a}$ ili $x > {a}$.")
        answer_display = f"x < {-a} ili x > {a}"
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = f"Riješi nejednačinu: ${inequality}$"
    hint1 = ("Apsolutna vrijednost je udaljenost od nule: |x| < a daje "
             "interval između -a i a, a |x| > a dva kraka izvan njega.")
    hint2 = f"Nacrtaj brojevnu pravu i označi tačke ${-a}$ i ${a}$."
    hint3 = "Provjeri probnim brojem iz svakog dijela prave koji dio zadovoljava."
    solution_text = (f"{explain} Probni broj potvrđuje izbor skupa.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct",
        operation="absolute_value_inequality", level=level, question=question,
        answer_value=("abs", str(a), "<" if less else ">"),
        answer_display=answer_display, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution_text,
        signature_parameters=[("inequality", inequality)],
        required_conditions=["absolute_value_inequality"],
        relevant_objects=["inequality", "integer"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", accepted_answers=(answer_display,))


# ---------------------------------------------------------------------------
# KLASIFIKACIJA ZAPISA (Batch #2): jednakost / jednačina / nejednakost /
# nejednačina; linearna jednačina/nejednačina s jednom nepoznatom
# ---------------------------------------------------------------------------

def _classification_samples(rng):
    a, b = rng.randint(2, 9), rng.randint(1, 9)
    c = rng.randint(10, 30)
    equation = f"{a}x + {b} = {c}"
    inequality = f"{a}x + {b} > {c}" if rng.random() < 0.5 else \
        f"{a}x + {b} < {c}"
    total = a + b
    equality = f"{a} + {b} = {total}"
    expression = f"{a}x + {b}"
    quadratic = f"x^{{2}} + {b} = {c}"
    two_unknowns = f"x + y = {c}"
    return {"equation": equation, "inequality": inequality,
            "equality": equality, "expression": expression,
            "quadratic": quadratic, "two_unknowns": two_unknowns}


_KIND_LABELS = {
    "equation": "linearna jednačina s jednom nepoznatom",
    "inequality": "linearna nejednačina s jednom nepoznatom",
    "equality": "brojevna jednakost (bez nepoznate)",
    "expression": "izraz (nema znaka jednakosti ni nejednakosti)",
}


def _classification_package(rng, level, domain, lesson_id, lesson_title):
    samples = _classification_samples(rng)
    ask = rng.choice(("equation", "inequality"))
    if level == 1:
        wrong_kinds = ("equality", "expression",
                       "inequality" if ask == "equation" else "equation")
    else:
        wrong_kinds = ("quadratic" if ask == "equation" else "equality",
                       "two_unknowns" if ask == "equation" else "expression",
                       "inequality" if ask == "equation" else "equation")
    correct = f"${samples[ask]}$"
    option_texts = (correct, *(f"${samples[kind]}$" for kind in wrong_kinds))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("zapisi nisu jedinstveni")
    question = f"Koji od ponuđenih zapisa je {_KIND_LABELS[ask]}?"
    hint1 = ("Jednačina ima znak jednakosti i nepoznatu; nejednačina znak "
             "nejednakosti i nepoznatu; jednakost nema nepoznate, a izraz "
             "nema ni jednakosti ni nejednakosti.")
    hint2 = "Za svaki zapis provjeri: ima li nepoznatu? Koji znak povezuje strane?"
    hint3 = ("Linearna s JEDNOM nepoznatom: nepoznata na prvi stepen i samo "
             "jedno slovo.")
    solution_text = (f"Zapis ${samples[ask]}$ je {_KIND_LABELS[ask]}. Ostali "
                     "zapisi to nisu (pogrešan znak, bez nepoznate, kvadrat "
                     "ili dvije nepoznate).")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation="classification",
        level=level, question=question, answer_value=samples[ask],
        answer_display=samples[ask], distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution_text,
        signature_parameters=[("sample", samples[ask]), ("ask", ask)],
        required_conditions=["classification"],
        relevant_objects=["equation"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


# ---------------------------------------------------------------------------
# BROJ RJEŠENJA (Batch #2): nijedno / tačno jedno / beskonačno mnogo
# ---------------------------------------------------------------------------

_COUNT_OPTIONS = ("nijedno", "tačno jedno", "beskonačno mnogo", "tačno dva")


def _solution_count_package(rng, level, domain, lesson_id, lesson_title):
    a = rng.randint(2, 9)
    b = rng.randint(1, 9)
    c = b + rng.randint(1, 9)
    case = rng.choice(("none", "one", "infinite"))
    if case == "none":
        equation = f"{a}x + {b} = {a}x + {c}"
        correct = "nijedno"
        explain = (f"Oduzimanjem ${a}x$ s obje strane ostaje ${b} = {c}$, "
                   "što nije tačno — jednačina nema rješenja.")
    elif case == "infinite":
        equation = f"{a}x + {b} = {a}x + {b}"
        correct = "beskonačno mnogo"
        explain = (f"Obje strane su identične — svaka vrijednost $x$ "
                   "zadovoljava jednačinu (identitet).")
    else:
        solution = rng.randint(-9, 9)
        rhs = a * solution + b
        equation = f"{a}x + {b} = {rhs}"
        correct = "tačno jedno"
        explain = (f"Jednačina se svodi na ${a}x = {rhs - b}$, pa je "
                   f"$x = {solution}$ jedino rješenje.")
    option_texts = (correct,
                    *(option for option in _COUNT_OPTIONS if option != correct))
    question = f"Koliko rješenja ima jednačina ${equation}$?"
    hint1 = ("Uporedi koeficijente uz nepoznatu i slobodne članove: isti "
             "koeficijenti a različiti slobodni članovi znače kontradikciju, "
             "identične strane znače identitet.")
    hint2 = f"Oduzmi ${a}x$ od obje strane i pogledaj šta ostane."
    hint3 = ("Kontradikcija (npr. $3 = 5$, što nije tačno) znači da rješenja "
             "nema; identitet znači da je svako $x$ rješenje.")
    solution_text = explain
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation="solution_count",
        level=level, question=question, answer_value=correct,
        answer_display=correct, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution_text,
        signature_parameters=[("equation", equation), ("case", case)],
        required_conditions=["solution_count"],
        relevant_objects=["equation"], generator_version=GENERATOR_VERSION,
        option_texts=tuple(option_texts[:4]), wrap="",
        # „Oduzmi ax od obje strane“ — balansna metoda; legitimna u 9. razredu
        # gdje ovaj oblik jedino živi, ali provenijencija to čini provjerljivim.
        method_id="balance_both_sides")



# ---------------------------------------------------------------------------
# EKVIVALENTNE JEDNAČINE / NEJEDNAČINE (Batch #2)
# ---------------------------------------------------------------------------

def _equivalence_package(rng, level, domain, lesson_id, lesson_title):
    solution = rng.randint(-9, 9)
    a = rng.randint(1, 5)
    b = rng.randint(1, 9)
    rhs = a * solution + b
    base = f"{a}x + {b} = {rhs}" if a > 1 else f"x + {b} = {rhs}"
    factor = rng.choice((2, 3))
    shift = rng.randint(1, 5)
    transforms = [
        (f"{a * factor}x + {b * factor} = {rhs * factor}", True),
        (f"{a}x + {b + shift} = {rhs + shift}", True),
        (f"{a}x = {rhs - b}", True),
    ]
    correct_equation = transforms[rng.randrange(len(transforms))][0]
    wrong = [f"{a}x + {b} = {rhs + shift}",
             f"{a * factor}x + {b} = {rhs * factor}",
             f"{a}x + {b + shift} = {rhs}"]
    option_texts = (f"${correct_equation}$", *(f"${w}$" for w in wrong))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("jednačine nisu jedinstvene")
    question = (f"Koja je od ponuđenih jednačina ekvivalentna jednačini "
                f"${base}$?")
    hint1 = ("Ekvivalentne jednačine imaju ISTO rješenje — nastaju sabiranjem "
             "istog broja na obje strane ili množenjem obje strane istim "
             "brojem različitim od nule.")
    hint2 = f"Riješi polaznu jednačinu: $x = {solution}$ — pa provjeri koja opcija ima isto rješenje."
    hint3 = "Uvrsti rješenje polazne jednačine u svaku opciju i traži tačnu jednakost."
    check = (f"{a} \\cdot {core.parenthesized(str(solution))} + {b} = {rhs}"
             if a > 1 else
             f"{core.parenthesized(str(solution))} + {b} = {rhs}")
    solution_text = (f"Polazna jednačina ima rješenje $x = {solution}$ "
                     f"(provjera: ${check}$). Jednačina "
                     f"${correct_equation}$ nastaje dozvoljenom "
                     "transformacijom i ima isto rješenje; ostale opcije "
                     "imaju drukčija rješenja.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct", operation="equivalence_choice",
        level=level, question=question, answer_value=correct_equation,
        answer_display=correct_equation, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution_text,
        signature_parameters=[("base", base), ("chosen", correct_equation)],
        required_conditions=["equivalence"], relevant_objects=["equation"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", method_id="balance_both_sides")


# ---------------------------------------------------------------------------
# METODA NEPOZNATOG ČLANA (PP-1, 6. razred) — oblici se grade iz ULOGE
# nepoznatog člana (sabirak/umanjenik/umanjilac/činilac/djeljenik/djelilac).
#
# ZAŠTO POSTOJI (audit ovlašćenja pravila): zatečeni graditelji uče
# prebacivanje preko znaka jednakosti — metodu koju kurikulum 6. razreda
# izričito ne poznaje (živi produkcijski slučaj: nejednačina s razlomcima u
# Q+ s vidljivom negativnom desnom stranom). Ovi graditelji NE
# preimenuju transpozicionu prozu: samo izvođenje ide kroz relaciju veze među
# članovima, a SVAKA prikazana vrijednost se KONSTRUIŠE nenegativno (odgovor i
# poznati članovi se biraju, prikazane strane se RAČUNAJU iz njih), pa klasa
# `granica - pomak → negativna desna strana` ne može nastati.
#
# Relacije i formulacije dolaze iz matbot/practice_policy.UNKNOWN_ROLE_RELATIONS
# — iste rečenice koje rules.py renderuje u oba prompta.
# ---------------------------------------------------------------------------

def _role_sentence(role):
    """„nepoznati sabirak = zbir minus poznati sabirak“ — iz PP-1 tabele."""
    return _ROLE_RELATIONS[role][1]


def _role_value(rng, domain, level, small=False, nonzero=True):
    """Nenegativna vrijednost domena; složenost raste s nivoom.

    Nivo NE dodaje nove članove (oblik lekcije je x ± a = b) nego složenost
    operanada: isti nazivnici → različiti nazivnici → mješoviti/dvodecimalni
    zapis. Dokaz težine ispod to iskreno prati."""
    if domain == "decimal":
        places = 1 if level < 3 else 2
        scale = 10 ** places
        high = {1: 9, 2: 40, 3: 60}[level]
        numerator = rng.randint(1 if nonzero else 0, high * scale)
        if numerator and numerator % scale == 0:
            numerator += rng.randint(1, scale - 1)
        return Fraction(numerator, scale)
    if domain in ("rational", "rational_nonneg"):
        den = rng.randint(2, 6 if level == 1 else 9)
        high = den - 1 if (small or level == 1) else den + den  # nivo 3: mješovit
        return Fraction(rng.randint(1 if nonzero else 0, max(high, 1)), den)
    magnitude = {1: 12, 2: 20, 3: 30}[level]
    return Fraction(rng.randint(1 if nonzero else 0, 9 if small else magnitude))


def _role_pair(rng, domain, level):
    """(rješenje, poznati član) — na nivou 1 razlomci dijele nazivnik."""
    solution = _role_value(rng, domain, level)
    known = _role_value(rng, domain, level, small=True)
    if domain in ("rational", "rational_nonneg"):
        if level == 1:
            den = solution.denominator
            known = Fraction(rng.randint(1, max(den - 1, 1)), den)
        elif known.denominator == solution.denominator:
            known = Fraction(known.numerator,
                             known.denominator + 1 + rng.randint(0, 2))
    return solution, known


def _role_distractors(solution, scale):
    """Distraktori koji NIKAD ne napuštaju nenegativni vidljivi domen."""
    step = scale if scale > 0 else Fraction(1)
    pool = [solution + step, solution + 2 * step, solution * 2]
    if solution - step > 0:
        pool.insert(1, solution - step)
    if solution - 2 * step > 0:
        pool.append(solution - 2 * step)
    if solution != 0:
        pool.append(solution / 2)
    pool.append(solution + 3 * step)
    return pool


def _role_scale(solution):
    return (Fraction(1, solution.denominator)
            if solution.denominator > 1 else Fraction(1))


def _role_final(domain, value):
    """Konačan prikaz PO DOMENU lekcije: decimalna lekcija prikazuje decimalu
    (nikad mješovit broj $32\\frac{1}{10}$ za $32,1$)."""
    if domain == "decimal" and core.is_terminating_decimal(value):
        return core.decimal_display(value)
    return core.fraction_display(value)


def _role_display_of(domain):
    return (core.decimal_display if domain == "decimal"
            else core.fraction_display)


def _unknown_member_package(rng, level, domain, shape, lesson_id,
                            lesson_title):
    """Ruta metode nepoznatog člana za JEDAN oblik ugovora, ili fail closed."""
    builders = {
        "one_step_additive": _role_additive_package,
        "subtract_from": _role_subtract_from_package,
        "one_step_multiplicative": _role_multiplicative_package,
        "solve_inequality_additive": _role_inequality_additive_package,
        "solve_inequality_multiplicative": _role_inequality_multiplicative_package,
    }
    builder = builders.get(shape)
    if builder is not None:
        return builder(rng, level, domain, lesson_id, lesson_title)
    # Metodski NEUTRALNI oblici (uvrštavanje/prepoznavanje — bez postupka
    # rješavanja u prozi) smiju proći nepromijenjeni i pod ovom politikom.
    neutral = {
        "check_solution": _check_solution_package,
        "check_inequality": _check_inequality_package,
        "classification": _classification_package,
    }
    builder = neutral.get(shape)
    if builder is not None:
        return builder(rng, level, domain, lesson_id, lesson_title)
    # DET-G6: oblik bez varijante nepoznatog člana pod ovom politikom pada
    # zatvoreno — nikad tiho s pogrešnom metodom. Model-put preuzima lekciju.
    raise DeterministicGenerationError(
        f"oblik {shape} nema varijantu metode nepoznatog člana")


def _role_additive_package(rng, level, domain, lesson_id, lesson_title):
    """x + a = b, a + x = b (sabirak) i x - a = b (umanjenik)."""
    show = lambda v: _domain_show(domain, v)
    solution, known = _role_pair(rng, domain, level)
    if solution == 0 or known == 0:
        raise DeterministicGenerationError("degenerisan član")
    form = rng.choice(("x_plus", "a_plus_x", "x_minus"))
    if form in ("x_plus", "a_plus_x"):
        role = "unknown_addend"
        total = solution + known
        equation = (f"x + {show(known)} = {show(total)}" if form == "x_plus"
                    else f"{show(known)} + x = {show(total)}")
        role_line = (f"U ovoj jednačini $x$ je sabirak: {_role_sentence(role)}.")
        compute = f"x = {show(total)} - {show(known)}"
        result_chain = f"{compute} = {show(solution)}"
        check = f"{show(solution)} + {show(known)} = {show(total)}"
        hint2 = (f"Zbir je ${show(total)}$, poznati sabirak je ${show(known)}$: "
                 f"dakle ${compute}$.")
    else:
        role = "unknown_minuend"
        difference = _role_value(rng, domain, level, small=True)
        if difference == 0:
            raise DeterministicGenerationError("degenerisana razlika")
        solution = known + difference          # umanjenik = razlika + umanjilac
        equation = f"x - {show(known)} = {show(difference)}"
        role_line = (f"U ovoj jednačini $x$ je umanjenik: {_role_sentence(role)}.")
        compute = f"x = {show(difference)} + {show(known)}"
        result_chain = f"{compute} = {show(solution)}"
        check = f"{show(solution)} - {show(known)} = {show(difference)}"
        hint2 = (f"Razlika je ${show(difference)}$, umanjilac je "
                 f"${show(known)}$: dakle ${compute}$.")
    fraction_note = (" (svedi na zajednički nazivnik)"
                     if domain in ("rational", "rational_nonneg")
                     and level > 1 else "")
    hint3 = f"Izračunaj: ${result_chain.split('=')[1].strip()}$" \
            f"{fraction_note} — to je vrijednost $x$."
    solution_text = (f"{role_line} Računamo: ${result_chain}$. "
                     f"Provjera uvrštavanjem: ${check}$ — tačno. "
                     f"Rješenje je $x = {_role_final(domain, solution)}$.")
    return _solving_package(
        rng, level, domain, lesson_id, lesson_title, "one_step_additive",
        f"Riješi jednačinu: ${equation}$", equation, solution,
        (role_line, hint2, hint3), solution_text, _role_scale(solution),
        method_id="unknown_member",
        distractors=_role_distractors(solution, _role_scale(solution)),
        evidence=core.evidence_for_level(level),
        answer_display=_role_final(domain, solution),
        display_of=_role_display_of(domain))


def _role_subtract_from_package(rng, level, domain, lesson_id, lesson_title):
    """a - x = b — nepoznati umanjilac: x = a - b."""
    show = lambda v: _domain_show(domain, v)
    solution, difference = _role_pair(rng, domain, level)
    if solution == 0 or difference == 0:
        raise DeterministicGenerationError("degenerisan član")
    minuend = solution + difference           # umanjenik = umanjilac + razlika
    equation = f"{show(minuend)} - x = {show(difference)}"
    role = "unknown_subtrahend"
    role_line = f"U ovoj jednačini $x$ je umanjilac: {_role_sentence(role)}."
    compute = f"x = {show(minuend)} - {show(difference)}"
    check = f"{show(minuend)} - {show(solution)} = {show(difference)}"
    hint2 = (f"Umanjenik je ${show(minuend)}$, razlika je "
             f"${show(difference)}$: dakle ${compute}$.")
    hint3 = (f"Izračunaj razliku ${show(minuend)} - {show(difference)}$ — "
             "to je vrijednost $x$.")
    solution_text = (f"{role_line} Računamo: ${compute} = {show(solution)}$. "
                     f"Provjera uvrštavanjem: ${check}$ — tačno. "
                     f"Rješenje je $x = {_role_final(domain, solution)}$.")
    return _solving_package(
        rng, level, domain, lesson_id, lesson_title, "subtract_from",
        f"Riješi jednačinu: ${equation}$", equation, solution,
        (role_line, hint2, hint3), solution_text, _role_scale(solution),
        method_id="unknown_member",
        distractors=_role_distractors(solution, _role_scale(solution)),
        evidence=core.evidence_for_level(level),
        answer_display=_role_final(domain, solution),
        display_of=_role_display_of(domain))


def _role_multiplicative_package(rng, level, domain, lesson_id, lesson_title):
    """a·x = b (činilac), x : a = b (djeljenik), a : x = b (djelilac)."""
    show = lambda v: _domain_show(domain, v)
    solution = _role_value(rng, domain, level)
    if solution == 0:
        raise DeterministicGenerationError("trivijalno rješenje")
    forms = ["factor", "dividend"] if level < 3 else ["factor", "dividend",
                                                      "divisor"]
    form = rng.choice(forms)
    if domain in ("rational", "rational_nonneg") and level >= 2:
        multiplier = _role_value(rng, domain, 1, small=True)
        if multiplier == 0:
            raise DeterministicGenerationError("nula množilac")
    else:
        multiplier = Fraction(rng.randint(2, 9))
    if form == "factor":
        role = "unknown_factor"
        product = multiplier * solution
        coefficient_display = (show(multiplier) if multiplier.denominator > 1
                               else show(multiplier))
        equation = (f"{coefficient_display} \\cdot x = {show(product)}"
                    if multiplier.denominator > 1
                    else f"{show(multiplier)}x = {show(product)}")
        role_line = f"U ovoj jednačini $x$ je činilac: {_role_sentence(role)}."
        compute = f"x = {show(product)} : {core.parenthesized(show(multiplier))}"
        check = (f"{show(multiplier)} \\cdot {show(solution)} = {show(product)}"
                 if multiplier.denominator == 1 else
                 f"{show(multiplier)} \\cdot {show(solution)} = {show(product)}")
        hint2 = (f"Proizvod je ${show(product)}$, poznati činilac je "
                 f"${show(multiplier)}$: dakle ${compute}$.")
    elif form == "dividend":
        role = "unknown_dividend"
        quotient = _role_value(rng, domain, level, small=True)
        if quotient == 0:
            raise DeterministicGenerationError("nula količnik")
        solution = quotient * multiplier      # djeljenik = količnik · djelilac
        equation = f"x : {core.parenthesized(show(multiplier))} = {show(quotient)}"
        role_line = f"U ovoj jednačini $x$ je djeljenik: {_role_sentence(role)}."
        compute = f"x = {show(quotient)} \\cdot {core.parenthesized(show(multiplier))}"
        check = f"{show(solution)} : {core.parenthesized(show(multiplier))} = {show(quotient)}"
        hint2 = (f"Količnik je ${show(quotient)}$, djelilac je "
                 f"${show(multiplier)}$: dakle ${compute}$.")
    else:
        role = "unknown_divisor"
        quotient = _role_value(rng, domain, 1, small=True)
        if quotient == 0:
            raise DeterministicGenerationError("nula količnik")
        dividend = quotient * solution        # djeljenik = količnik · djelilac
        equation = f"{show(dividend)} : x = {show(quotient)}"
        role_line = f"U ovoj jednačini $x$ je djelilac: {_role_sentence(role)}."
        compute = f"x = {show(dividend)} : {core.parenthesized(show(quotient))}"
        check = f"{show(dividend)} : {core.parenthesized(show(solution))} = {show(quotient)}"
        hint2 = (f"Djeljenik je ${show(dividend)}$, količnik je "
                 f"${show(quotient)}$: dakle ${compute}$.")
    reciprocal_note = (" (dijeljenje razlomkom je množenje recipročnom "
                       "vrijednošću)" if domain in ("rational",
                                                    "rational_nonneg")
                       and level >= 2 else "")
    hint3 = f"Izračunaj{reciprocal_note}: ${compute.split('=', 1)[1].strip()}$."
    solution_text = (f"{role_line} Računamo: ${compute} = {show(solution)}$. "
                     f"Provjera uvrštavanjem: ${check}$ — tačno. "
                     f"Rješenje je $x = {_role_final(domain, solution)}$.")
    return _solving_package(
        rng, level, domain, lesson_id, lesson_title, "one_step_multiplicative",
        f"Riješi jednačinu: ${equation}$", equation, solution,
        (role_line, hint2, hint3), solution_text, _role_scale(solution),
        method_id="unknown_member",
        distractors=_role_distractors(solution, _role_scale(solution)),
        evidence=core.evidence_for_level(level),
        answer_display=_role_final(domain, solution),
        display_of=_role_display_of(domain))


def _role_inequality_options(answer_symbol, bound_display, show, bound):
    """Opcije-skupovi rješenja; pomak uvijek NAGORE (Q+ ostaje nenegativan)."""
    correct = _inequality_text(answer_symbol, bound_display)
    wrong_symbol = {"<": ">", ">": "<"}[answer_symbol]
    shifted = bound + (Fraction(1) if bound.denominator == 1
                       else Fraction(1, bound.denominator))
    options = (correct,
               _inequality_text(wrong_symbol, bound_display),
               _inequality_text(answer_symbol, show(shifted)),
               _inequality_text(wrong_symbol, show(shifted)))
    if len(set(options)) != 4:
        raise DeterministicGenerationError("skupovi rješenja nisu jedinstveni")
    return correct, options


def _role_inequality_additive_package(rng, level, domain, lesson_id,
                                      lesson_title):
    """x + a ≶ b i x - a > b — smjer se NE mijenja (sabiranje/oduzimanje)."""
    show = lambda v: _domain_show(domain, v)
    bound, known = _role_pair(rng, domain, level)
    if bound == 0 or known == 0:
        raise DeterministicGenerationError("degenerisan član")
    symbol = "<" if rng.random() < 0.5 else ">"
    form = rng.choice(("x_plus", "x_minus" if symbol == ">" else "x_plus"))
    if form == "x_plus":
        rhs = bound + known                   # granica se KONSTRUIŠE unaprijed
        inequality = f"x + {show(known)} {symbol} {show(rhs)}"
        role_line = ("Zamisli pridruženu jednačinu $x + "
                     f"{show(known)} = {show(rhs)}$: $x$ je sabirak, pa je "
                     f"granica {_role_sentence('unknown_addend').split('=')[1].strip()}"
                     f" — ${show(rhs)} - {show(known)} = {show(bound)}$.")
        steps = f"x {symbol} {show(rhs)} - {show(known)}"
    else:
        rhs = _role_value(rng, domain, level, small=True)
        if rhs == 0:
            raise DeterministicGenerationError("degenerisana desna strana")
        bound = rhs + known                   # umanjenik = razlika + umanjilac
        inequality = f"x - {show(known)} {symbol} {show(rhs)}"
        role_line = ("Zamisli pridruženu jednačinu $x - "
                     f"{show(known)} = {show(rhs)}$: $x$ je umanjenik, pa je "
                     f"granica {_role_sentence('unknown_minuend').split('=')[1].strip()}"
                     f" — ${show(rhs)} + {show(known)} = {show(bound)}$.")
        steps = f"x {symbol} {show(rhs)} + {show(known)}"
    answer_symbol = symbol                    # sabiranje/oduzimanje čuva smjer
    bound_display = show(bound)
    correct, options = _role_inequality_options(answer_symbol, bound_display,
                                                show, bound)
    hint2 = (f"Postupak: ${steps}$ — sabiranje i oduzimanje istog broja "
             "ne mijenjaju smjer nejednakosti.")
    hint3 = ("Rješenje čitaš kao skup: svi brojevi koji zadovoljavaju "
             "dobijenu nejednakost.")
    probe = bound + (Fraction(1) if answer_symbol == ">" else Fraction(0))
    if answer_symbol == "<":
        probe = bound / 2
    solution_text = (f"{role_line} Smjer nejednakosti se ne mijenja, pa je "
                     f"rješenje $x {answer_symbol} {bound_display}$. Probni "
                     f"broj ${show(probe)}$ zadovoljava polaznu nejednačinu, "
                     "što potvrđuje smjer.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct",
        operation="solve_inequality_additive", level=level,
        question=f"Riješi nejednačinu: ${inequality}$",
        answer_value=(answer_symbol, str(bound)),
        answer_display=f"x {answer_symbol} {bound_display}",
        distractor_values=(), hints=(role_line, hint2, hint3),
        solution=solution_text,
        signature_parameters=[("inequality", inequality)],
        required_conditions=["solve_inequality_additive"],
        relevant_objects=["inequality", domain],
        generator_version=GENERATOR_VERSION, option_texts=options,
        wrap="", accepted_answers=(f"x {answer_symbol} {bound_display}",),
        method_id="unknown_member", evidence=core.evidence_for_level(level))


def _role_inequality_multiplicative_package(rng, level, domain, lesson_id,
                                            lesson_title):
    """a·x ≶ b i x : a ≶ b s POZITIVNIM a — smjer se ne mijenja.

    Matematički autoritet PP-1: smjer nejednakosti mijenja SAMO množenje/
    dijeljenje negativnim brojem; u Q+ negativan množilac ne postoji, pa se
    pitanje okretanja znaka u ovom razredu uopšte ne javlja (i to rješenje
    izričito kaže)."""
    show = lambda v: _domain_show(domain, v)
    bound = _role_value(rng, domain, level)
    if bound == 0:
        raise DeterministicGenerationError("degenerisana granica")
    if domain in ("rational", "rational_nonneg") and level >= 2:
        multiplier = _role_value(rng, domain, 1, small=True)
        if multiplier == 0:
            raise DeterministicGenerationError("nula množilac")
    else:
        multiplier = Fraction(rng.randint(2, 9))
    symbol = "<" if rng.random() < 0.5 else ">"
    form = rng.choice(("factor", "dividend"))
    if form == "factor":
        rhs = multiplier * bound
        coefficient = (f"{show(multiplier)} \\cdot x"
                       if multiplier.denominator > 1
                       else f"{show(multiplier)}x")
        inequality = f"{coefficient} {symbol} {show(rhs)}"
        role_line = (f"Zamisli pridruženu jednačinu: $x$ je činilac, pa je "
                     f"granica {_role_sentence('unknown_factor').split('=')[1].strip()}"
                     f" — ${show(rhs)} : {core.parenthesized(show(multiplier))} = {show(bound)}$.")
        steps = f"x {symbol} {show(rhs)} : {core.parenthesized(show(multiplier))}"
    else:
        rhs = _role_value(rng, domain, level, small=True)
        if rhs == 0:
            raise DeterministicGenerationError("degenerisana desna strana")
        bound = rhs * multiplier              # djeljenik = količnik · djelilac
        inequality = (f"x : {core.parenthesized(show(multiplier))} {symbol} "
                      f"{show(rhs)}")
        role_line = (f"Zamisli pridruženu jednačinu: $x$ je djeljenik, pa je "
                     f"granica {_role_sentence('unknown_dividend').split('=')[1].strip()}"
                     f" — ${show(rhs)} \\cdot {core.parenthesized(show(multiplier))} = {show(bound)}$.")
        steps = f"x {symbol} {show(rhs)} \\cdot {core.parenthesized(show(multiplier))}"
    bound_display = show(bound)
    correct, options = _role_inequality_options(symbol, bound_display, show,
                                                bound)
    hint2 = (f"Postupak: ${steps}$ — množimo/dijelimo POZITIVNIM brojem "
             f"${show(multiplier)}$, pa se smjer nejednakosti NE mijenja.")
    hint3 = ("Rješenje čitaš kao skup: svi brojevi koji zadovoljavaju "
             "dobijenu nejednakost.")
    probe = bound + Fraction(1) if symbol == ">" else bound / 2
    # Q+ proza NE spominje negativne brojeve ni kontrast s njima — po pravilu
    # razreda to pitanje se u 6. razredu uopšte ne javlja.
    solution_text = (f"{role_line} Množenje i dijeljenje pozitivnim brojem "
                     f"čuvaju smjer nejednakosti, pa je rješenje $x {symbol} "
                     f"{bound_display}$. Probni broj ${show(probe)}$ "
                     "zadovoljava polaznu nejednačinu, što potvrđuje smjer.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_equation_direct",
        operation="solve_inequality_multiplicative", level=level,
        question=f"Riješi nejednačinu: ${inequality}$",
        answer_value=(symbol, str(bound)),
        answer_display=f"x {symbol} {bound_display}",
        distractor_values=(), hints=(role_line, hint2, hint3),
        solution=solution_text,
        signature_parameters=[("inequality", inequality)],
        required_conditions=["solve_inequality_multiplicative"],
        relevant_objects=["inequality", domain],
        generator_version=GENERATOR_VERSION, option_texts=options,
        wrap="", accepted_answers=(f"x {symbol} {bound_display}",),
        method_id="unknown_member", evidence=core.evidence_for_level(level))


# ---------------------------------------------------------------------------
# JEDNOSTAVNE KVADRATNE JEDNAČINE (Batch #2) — porodica
# simple_quadratic_equation: x²=a, ax²+bx=0, x²±2ax+a²=0
# ---------------------------------------------------------------------------

def _quadratic_package(rng, level, shape, lesson_id, lesson_title):
    if shape == "x_squared_equals_a":
        root = rng.randint(1, 9 if level == 1 else 15)
        a = root * root
        equation = f"x^{{2}} = {a}"
        low, high = -root, root
        correct = f"$x = {root}$ ili $x = {-root}$"
        wrong = [f"$x = {root}$",
                 f"$x = {a}$ ili $x = {-a}$",
                 f"$x = {root + 1}$ ili $x = {-(root + 1)}$"]
        explain = (f"Kvadrat i pozitivnog i negativnog broja je pozitivan: "
                   f"${root}^{{2}} = {a}$ i $({-root})^{{2}} = {a}$, pa su "
                   f"rješenja $x = {root}$ i $x = {-root}$.")
        answer_display = f"x = {root} ili x = {-root}"
        hint2 = f"Potraži broj čiji je kvadrat ${a}$ — i ne zaboravi negativan slučaj."
    elif shape == "factor_out_x":
        a = rng.randint(1, 4 if level < 3 else 6)
        b = a * rng.randint(1, 6)
        sign = rng.random() < 0.5
        second = Fraction(-b, a) if sign else Fraction(b, a)
        coefficient_term = f"{a}x^{{2}}" if a > 1 else "x^{2}"
        equation = (f"{coefficient_term} + {b}x = 0" if sign
                    else f"{coefficient_term} - {b}x = 0")
        factored = (f"x({a}x + {b}) = 0" if sign else f"x({a}x - {b}) = 0") \
            if a > 1 else (f"x(x + {b}) = 0" if sign else f"x(x - {b}) = 0")
        correct = f"$x = 0$ ili $x = {_show(second)}$"
        wrong = [f"$x = {_show(second)}$",
                 f"$x = 0$ ili $x = {_show(-second)}$",
                 f"$x = 0$"]
        explain = (f"Izlučimo x: ${factored}$ — proizvod je nula kad je bilo "
                   f"koji faktor nula, pa je $x = 0$ ili $x = {_show(second)}$.")
        answer_display = f"x = 0 ili x = {_show(second)}"
        hint2 = f"Izluči zajednički faktor: ${factored}$."
    else:  # perfect_square_trinomial
        a = rng.randint(1, 9 if level < 3 else 12)
        sign = rng.random() < 0.5
        root = Fraction(-a) if sign else Fraction(a)
        middle = 2 * a
        equation = (f"x^{{2}} + {middle}x + {a * a} = 0" if sign
                    else f"x^{{2}} - {middle}x + {a * a} = 0")
        square = (f"(x + {a})^{{2}} = 0" if sign else f"(x - {a})^{{2}} = 0")
        correct = f"$x = {_show(root)}$ (dvostruko rješenje)"
        wrong = [f"$x = {_show(-root)}$ (dvostruko rješenje)",
                 f"$x = {_show(root)}$ ili $x = {_show(-root)}$",
                 f"$x = {a * a}$"]
        explain = (f"Trinom je potpun kvadrat: ${square}$, pa je jedino "
                   f"(dvostruko) rješenje $x = {_show(root)}$.")
        answer_display = f"x = {_show(root)}"
        hint2 = f"Prepoznaj potpun kvadrat: ${square}$."
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = f"Riješi jednačinu: ${equation}$"
    hint1 = ("Kvadratnu jednačinu ovog oblika rješavaj bez formule: "
             "korjenovanjem, izlučivanjem x ili prepoznavanjem potpunog "
             "kvadrata.")
    hint3 = "Provjeri svako rješenje uvrštavanjem u polaznu jednačinu."
    solution_text = f"{explain}"
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="simple_quadratic_equation", operation=shape, level=level,
        question=question, answer_value=answer_display,
        answer_display=answer_display, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution_text,
        signature_parameters=[("equation", equation)],
        required_conditions=[shape], relevant_objects=["equation"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", accepted_answers=(answer_display,))
