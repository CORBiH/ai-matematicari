"""Deterministički generator porodice polinoma i algebarskih izraza.

Jedna semantička porodica (`polynomial_basic`) s konceptima:

  • expression_evaluation — brojna vrijednost izraza za datu vrijednost
                            promjenljive (domena natural ili integer);
  • structure_count       — broj članova izraza;
  • monomial_structure    — koeficijent i stepen monoma;
  • combine_like_terms    — sređivanje polinoma;
  • add_subtract          — zbir/razlika dva polinoma;
  • multiply              — proizvod dva binoma / monoma i binoma.

MATEMATIČKI AUTORITET: polinom je server-vlasnički RJEČNIK {stepen:
koeficijent} nad egzaktnim cijelim brojevima; kanonski prikaz iz rječnika je
injektivan, pa su dva različita rječnika uvijek dva različita vidljiva
zapisa. Distraktori su polinomi DRUGOG rječnika (klasične greške znaka i
sabiranja stepena) — nikad drugi zapis istog polinoma. Numerička provjera u
rješenju: vrijednost polinoma u konkretnoj tački, egzaktno.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("polynomial_basic",)
GENERATOR_VERSION = "detpoly-1"

_SUPPORTED_CONCEPTS = frozenset({
    "expression_evaluation", "structure_count", "monomial_structure",
    "combine_like_terms", "add_subtract", "multiply",
})
_SUPPORTED_DOMAINS = frozenset({"natural", "integer"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    if not concepts or not concepts <= _SUPPORTED_CONCEPTS:
        return False
    domain = parameters.get("number_domain") or "integer"
    return domain in _SUPPORTED_DOMAINS


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    domain = parameters.get("number_domain") or "integer"
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            builder = {
                "expression_evaluation": _evaluation_package,
                "structure_count": _structure_count_package,
                "monomial_structure": _monomial_package,
                "combine_like_terms": _combine_package,
                "add_subtract": _add_subtract_package,
                "multiply": _multiply_package,
            }[concept]
            return builder(rng, level, domain, lesson_id, lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


# ---------------------------------------------------------------------------
# KANONSKI PRIKAZ POLINOMA IZ RJEČNIKA {stepen: koeficijent}
# ---------------------------------------------------------------------------

def _monomial_display(coefficient, degree, leading):
    if degree == 0:
        body = str(abs(coefficient))
    else:
        variable = "x" if degree == 1 else f"x^{{{degree}}}"
        body = variable if abs(coefficient) == 1 else f"{abs(coefficient)}{variable}"
    if leading:
        return f"-{body}" if coefficient < 0 else body
    return f" - {body}" if coefficient < 0 else f" + {body}"


def poly_display(coefficients):
    """Kanonski zapis: opadajući stepeni, bez nultih koeficijenata."""
    terms = [(degree, coefficient)
             for degree, coefficient in sorted(coefficients.items(),
                                               reverse=True)
             if coefficient != 0]
    if not terms:
        return "0"
    parts = []
    for index, (degree, coefficient) in enumerate(terms):
        parts.append(_monomial_display(coefficient, degree, leading=index == 0))
    return "".join(parts)


def poly_value(coefficients, x):
    return sum(Fraction(c) * Fraction(x) ** d for d, c in coefficients.items())


def _poly_add(first, second, sign=1):
    result = dict(first)
    for degree, coefficient in second.items():
        result[degree] = result.get(degree, 0) + sign * coefficient
    return {d: c for d, c in result.items() if c != 0}


def _poly_mul(first, second):
    result = {}
    for d1, c1 in first.items():
        for d2, c2 in second.items():
            result[d1 + d2] = result.get(d1 + d2, 0) + c1 * c2
    return {d: c for d, c in result.items() if c != 0}


def _distinct_poly_options(correct, candidates):
    option_dicts = [dict(correct)]
    option_texts = [f"${poly_display(correct)}$"]
    for candidate in candidates:
        cleaned = {d: c for d, c in candidate.items() if c != 0}
        if any(cleaned == seen for seen in option_dicts):
            continue
        text = f"${poly_display(cleaned)}$"
        if text in option_texts:
            continue
        option_dicts.append(cleaned)
        option_texts.append(text)
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno polinoma")
    return tuple(option_texts)


def _coefficient(rng, level, domain, allow_zero=False):
    high = 5 if level == 1 else 9
    value = rng.randint(0 if allow_zero else 1, high)
    if domain == "integer" and rng.random() < 0.5:
        value = -value
    return value


# ---------------------------------------------------------------------------
# BROJNA VRIJEDNOST IZRAZA
# ---------------------------------------------------------------------------

def _evaluation_package(rng, level, domain, lesson_id, lesson_title):
    a = _coefficient(rng, level, domain)
    b = _coefficient(rng, level, domain)
    if a == 0:
        raise DeterministicGenerationError("nema promjenljive")
    use_square = level == 3
    c = _coefficient(rng, level, domain) if use_square else 0
    x = rng.randint(2, 6 if level == 1 else 9)
    if domain == "integer" and level > 1 and rng.random() < 0.4:
        x = -x
    coefficients = ({2: c, 1: a, 0: b} if use_square and c else {1: a, 0: b})
    display = poly_display(coefficients)
    value = poly_value(coefficients, x)
    question = (f"Izračunaj brojnu vrijednost izraza ${display}$ za "
                f"$x = {x}$.")
    x_term = core.parenthesized(str(x))
    pieces = []
    if use_square and c:
        pieces.append(f"{core.parenthesized(str(c))} \\cdot {x_term}^{{2}}"
                      if c != 1 else f"{x_term}^{{2}}")
    pieces.append(f"{core.parenthesized(str(a))} \\cdot {x_term}"
                  if a != 1 else x_term)
    substitution = " + ".join(pieces) + (f" + {core.parenthesized(str(b))}"
                                         if b else "")
    chain = f"{substitution} = {core.plain_fraction_display(value)}"
    hint1 = ("Brojna vrijednost izraza: uvrsti dati broj umjesto "
             "promjenljive pa izračunaj po redoslijedu operacija.")
    hint2 = f"Uvrsti $x = {x}$: ${substitution}$."
    hint3 = "Prvo izračunaj stepene i proizvode, na kraju saberi."
    solution = (f"Uvrstimo $x = {x}$: ${chain}$. Vrijednost izraza je "
                f"${core.fraction_display(value)}$.")
    candidates = [value + 1, value - 1, -value, value + x, value - x,
                  value + 2]
    if domain == "natural":
        candidates = [v for v in candidates if v >= 0]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="expression_evaluation",
        level=level, question=question, answer_value=value,
        answer_display=core.fraction_display(value),
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("expression", display), ("x", str(x))],
        required_conditions=["expression_evaluation"],
        relevant_objects=["expression"], generator_version=GENERATOR_VERSION,
        display_of=core.fraction_display)


# ---------------------------------------------------------------------------
# STRUKTURA: BROJ ČLANOVA I MONOM
# ---------------------------------------------------------------------------

def _structure_count_package(rng, level, domain, lesson_id, lesson_title):
    term_count = rng.randint(2, 3 if level == 1 else 4)
    degrees = rng.sample(range(0, 5), term_count)
    coefficients = {}
    for degree in degrees:
        coefficient = _coefficient(rng, level, "integer")
        if coefficient == 0:
            coefficient = 1
        coefficients[degree] = coefficient
    display = poly_display(coefficients)
    question = f"Koliko članova ima izraz ${display}$?"
    wrong = [term_count + 1, term_count - 1, term_count + 2, 1]
    option_texts = [f"${term_count}$"]
    seen = {term_count}
    for candidate in wrong:
        if candidate < 1 or candidate in seen:
            continue
        seen.add(candidate)
        option_texts.append(f"${candidate}$")
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno opcija")
    hint1 = ("Članovi izraza su sabirci razdvojeni znakovima + i - izvan "
             "zagrada.")
    hint2 = f"Prebroji sabirke u zapisu ${display}$."
    hint3 = "I slobodni član (broj bez promjenljive) računa se kao član."
    solution = (f"Izraz ${display}$ ima tačno ${term_count}$ "
                f"{'člana' if term_count in (2, 3, 4) else 'članova'}.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="structure_count",
        level=level, question=question, answer_value=term_count,
        answer_display=str(term_count), distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("expression", display)],
        required_conditions=["structure_count"],
        relevant_objects=["expression"], generator_version=GENERATOR_VERSION,
        option_texts=tuple(option_texts), wrap="")


def _monomial_package(rng, level, domain, lesson_id, lesson_title):
    coefficient = _coefficient(rng, level, "integer")
    if coefficient == 0:
        coefficient = -4
    degree = rng.randint(1, 3 if level == 1 else 5)
    display = poly_display({degree: coefficient})
    ask_coefficient = rng.random() < 0.5
    if ask_coefficient:
        question = f"Koliki je koeficijent monoma ${display}$?"
        answer = coefficient
        wrong = [-coefficient, degree, -degree if degree != coefficient else degree + 1]
        explain = (f"Koeficijent monoma je brojevni faktor ispred "
                   f"promjenljive: kod ${display}$ to je ${coefficient}$.")
    else:
        question = f"Koliki je stepen monoma ${display}$?"
        answer = degree
        wrong = [degree + 1, degree - 1, abs(coefficient)]
        explain = (f"Stepen monoma je izložilac promjenljive: kod "
                   f"${display}$ to je ${degree}$.")
    option_texts = [f"${answer}$"]
    seen = {answer}
    for candidate in wrong + [answer + 2, answer - 2]:
        if candidate in seen:
            continue
        seen.add(candidate)
        option_texts.append(f"${candidate}$")
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno opcija")
    hint1 = ("Monom čine koeficijent (broj) i promjenljiva sa svojim "
             "izložiocem — izložilac je stepen monoma.")
    hint2 = f"U zapisu ${display}$ razdvoj broj od promjenljive."
    hint3 = "Pazi na predznak: minus pripada koeficijentu."
    solution = explain
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="monomial_structure",
        level=level, question=question, answer_value=answer,
        answer_display=str(answer), distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("monomial", display),
                              ("ask", "coefficient" if ask_coefficient else "degree")],
        required_conditions=["monomial_structure"],
        relevant_objects=["expression"], generator_version=GENERATOR_VERSION,
        option_texts=tuple(option_texts), wrap="")


# ---------------------------------------------------------------------------
# SREĐIVANJE, ZBIR/RAZLIKA I PROIZVOD POLINOMA
# ---------------------------------------------------------------------------

def _raw_terms_display(terms):
    parts = []
    for index, (degree, coefficient) in enumerate(terms):
        parts.append(_monomial_display(coefficient, degree, leading=index == 0))
    return "".join(parts)


def _combine_package(rng, level, domain, lesson_id, lesson_title):
    term_count = {1: 3, 2: 4, 3: 5}[level]
    degrees_pool = (0, 1, 2) if level < 3 else (0, 1, 2, 3)
    terms = []
    for _ in range(term_count):
        degree = rng.choice(degrees_pool)
        coefficient = _coefficient(rng, level, "integer")
        if coefficient == 0:
            coefficient = 2
        terms.append((degree, coefficient))
    degrees_used = {d for d, _c in terms}
    if len(degrees_used) == len(terms):
        raise DeterministicGenerationError("nema sličnih članova")
    raw_display = _raw_terms_display(terms)
    combined = {}
    for degree, coefficient in terms:
        combined[degree] = combined.get(degree, 0) + coefficient
    combined = {d: c for d, c in combined.items() if c != 0}
    if not combined:
        raise DeterministicGenerationError("sve se poništilo")
    question = f"Sredi polinom: ${raw_display}$"
    # Distraktori: pogrešan znak pri svođenju, izostavljen član, sabrani
    # NEslični članovi — sve DRUGI rječnici.
    flip_degree = rng.choice(sorted(combined))
    wrong1 = dict(combined)
    wrong1[flip_degree] = -wrong1[flip_degree]
    wrong2 = dict(combined)
    wrong2.pop(sorted(wrong2)[0])
    wrong3 = dict(combined)
    wrong3[flip_degree] = wrong3[flip_degree] + 2
    option_texts = _distinct_poly_options(combined, [wrong1, wrong2, wrong3,
                                                     {0: 1, **combined}])
    check_x = 2
    raw_value = sum(Fraction(c) * Fraction(check_x) ** d for d, c in terms)
    combined_value = poly_value(combined, check_x)
    if raw_value != combined_value:
        raise DeterministicGenerationError("sređivanje nije očuvalo vrijednost")
    hint1 = ("Slični članovi imaju ISTU promjenljivu s istim izložiocem — "
             "samo se njihovi koeficijenti sabiraju.")
    hint2 = "Grupiši članove po stepenu pa saberi koeficijente unutar grupe."
    hint3 = f"Provjeri se uvrštavanjem: za $x = {check_x}$ polazni i sređeni zapis moraju dati isto."
    solution = (f"Grupišemo po stepenima i saberemo koeficijente: "
                f"${raw_display} = {poly_display(combined)}$. Provjera za "
                f"$x = {check_x}$: obje strane daju "
                f"${core.plain_fraction_display(combined_value)}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="combine_like_terms",
        level=level, question=question, answer_value=poly_display(combined),
        answer_display=poly_display(combined), distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("raw", raw_display)],
        required_conditions=["combine_like_terms"],
        relevant_objects=["expression"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _small_poly(rng, level, max_degree=2):
    coefficients = {}
    for degree in range(max_degree + 1):
        coefficient = _coefficient(rng, level, "integer", allow_zero=True)
        if coefficient:
            coefficients[degree] = coefficient
    if len(coefficients) < 2:
        raise DeterministicGenerationError("prekratak polinom")
    return coefficients


def _add_subtract_package(rng, level, domain, lesson_id, lesson_title):
    first = _small_poly(rng, level)
    second = _small_poly(rng, level)
    subtract = rng.random() < 0.5
    result = _poly_add(first, second, sign=-1 if subtract else 1)
    if not result:
        raise DeterministicGenerationError("rezultat je nula")
    symbol = "-" if subtract else "+"
    question = (f"Izračunaj: $({poly_display(first)}) {symbol} "
                f"({poly_display(second)})$")
    wrong1 = _poly_add(first, second, sign=1 if subtract else -1)
    flip = rng.choice(sorted(result))
    wrong2 = dict(result)
    wrong2[flip] = -wrong2[flip]
    wrong3 = dict(result)
    wrong3[flip] = wrong3[flip] + 1
    option_texts = _distinct_poly_options(result, [wrong1, wrong2, wrong3,
                                                   _poly_add(result, {0: 1})])
    check_x = 2
    check_value = poly_value(result, check_x)
    first_value = poly_value(first, check_x)
    second_value = poly_value(second, check_x)
    hint1 = ("Polinomi se sabiraju i oduzimaju po sličnim članovima; kod "
             "oduzimanja SVAKI član drugog polinoma mijenja predznak.")
    hint2 = ("Ukloni zagrade (pazi na predznake), pa saberi koeficijente "
             "istih stepena.")
    hint3 = f"Provjeri se za $x = {check_x}$: rezultat mora dati isto što i polazni izrazi."
    solution = (f"Saberemo po sličnim članovima: rezultat je "
                f"${poly_display(result)}$. Provjera za $x = {check_x}$: "
                f"${core.plain_fraction_display(first_value)} {symbol} "
                f"{core.parenthesized(core.plain_fraction_display(second_value))} "
                f"= {core.plain_fraction_display(check_value)}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="add_subtract", level=level,
        question=question, answer_value=poly_display(result),
        answer_display=poly_display(result), distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("first", poly_display(first)),
                              ("second", poly_display(second)),
                              ("op", symbol)],
        required_conditions=["add_subtract"],
        relevant_objects=["expression"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _multiply_package(rng, level, domain, lesson_id, lesson_title):
    if level == 1:
        a = _coefficient(rng, level, "integer")
        if a == 0:
            a = 2
        first = {1: a} if rng.random() < 0.5 else {0: a}
        second = _small_poly(rng, level, max_degree=1)
    else:
        first = _small_poly(rng, level, max_degree=1)
        second = _small_poly(rng, level, max_degree=1)
    result = _poly_mul(first, second)
    if len(result) < 2:
        raise DeterministicGenerationError("proizvod je premali")
    question = (f"Pomnoži: $({poly_display(first)}) \\cdot "
                f"({poly_display(second)})$")
    flip = rng.choice(sorted(result))
    wrong1 = dict(result)
    wrong1[flip] = -wrong1[flip]
    wrong2 = _poly_add(first, second)             # sabrao umjesto množio
    middle = sorted(result)[len(result) // 2]
    wrong3 = dict(result)
    wrong3.pop(middle, None)
    if not wrong3 or wrong3 == result:
        wrong3 = dict(result)
        wrong3[middle] = wrong3.get(middle, 0) + 2
    option_texts = _distinct_poly_options(result, [wrong1, wrong2, wrong3,
                                                   _poly_add(result, {0: 2})])
    check_x = 2
    first_value = poly_value(first, check_x)
    second_value = poly_value(second, check_x)
    result_value = poly_value(result, check_x)
    hint1 = ("Množi svaki član prvog polinoma sa SVAKIM članom drugog, pa "
             "saberi slične članove.")
    hint2 = "Rasporedi proizvode po stepenima i pažljivo prati predznake."
    hint3 = f"Provjeri se za $x = {check_x}$: proizvod vrijednosti mora biti vrijednost proizvoda."
    solution = (f"Množenjem člana po član i svođenjem dobijamo "
                f"${poly_display(result)}$. Provjera za $x = {check_x}$: "
                f"${core.plain_fraction_display(first_value)} \\cdot "
                f"{core.parenthesized(core.plain_fraction_display(second_value))} "
                f"= {core.plain_fraction_display(result_value)}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="multiply", level=level,
        question=question, answer_value=poly_display(result),
        answer_display=poly_display(result), distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("first", poly_display(first)),
                              ("second", poly_display(second))],
        required_conditions=["multiply"], relevant_objects=["expression"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")
