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
    # Batch #3 — identiteti i faktorizacija (dokaz: RAZVOJ kandidata nazad).
    "square_of_binomial", "cube_of_binomial", "factor_difference_squares",
    "factor_common", "factor_grouping", "factor_identity", "sum_diff_cubes",
    "zero_product", "fraction_domain", "monomial_mul_div", "like_terms_select",
})
_SUPPORTED_DOMAINS = frozenset({"natural", "integer"})

# ---------------------------------------------------------------------------
# KURIKULARNA GRANICA STEPENA PROMJENLJIVE
# ---------------------------------------------------------------------------
# ŽIVI QA NALAZ (direktor škole, Practice): u lekciji o izrazima s promjenljivim
# traženje sve težih zadataka dovelo je do izraza s $x^2$. Uzrok NIJE bio bug u
# računu — nivo 3 je NAMJERNO dizao stepen, jer ova porodica opslužuje i lekcije
# u kojima je stepenovanje obrađeno gradivo, a generator o lekciji ne zna ništa
# osim parametara ugovora.
#
# Granica zato dolazi iz UGOVORA LEKCIJE (`max_variable_degree`), nikad iz
# razreda, naslova ni ID-ja lekcije: ista porodica u jednoj lekciji smije
# $x^2$, u drugoj ne smije, i to je razlika PODATAKA. Bez parametra vrijedi
# istorijsko ponašanje (stepen do 2) — nijedna postojeća lekcija se ne mijenja.
_DEFAULT_MAX_VARIABLE_DEGREE = 2
_DEGREE_BOUNDED_CONCEPTS = frozenset({"expression_evaluation"})


def max_variable_degree(parameters) -> int:
    """Najviši dozvoljeni stepen promjenljive iz ugovora lekcije."""
    raw = (parameters or {}).get("max_variable_degree")
    if raw is None:
        return _DEFAULT_MAX_VARIABLE_DEGREE
    return int(raw)


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
    max_degree = max_variable_degree(parameters)
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
                "square_of_binomial": _square_binomial_package,
                "cube_of_binomial": _cube_binomial_package,
                "factor_difference_squares": _factor_diff_squares_package,
                "factor_common": _factor_common_package,
                "factor_grouping": _factor_grouping_package,
                "factor_identity": _factor_identity_package,
                "sum_diff_cubes": _sum_diff_cubes_package,
                "zero_product": _zero_product_package,
                "fraction_domain": _fraction_domain_package,
                "monomial_mul_div": _monomial_mul_div_package,
                "like_terms_select": _like_terms_select_package,
            }[concept]
            # Granicu stepena prima SAMO koncept koji gradi slobodan izraz;
            # ostali koncepti su stepenom definisani sami po sebi (kvadrat
            # binoma, monom, razlika kubova) i lekcija ih ne smije „spuštati“.
            if concept in _DEGREE_BOUNDED_CONCEPTS:
                return builder(rng, level, domain, lesson_id, lesson_title,
                               max_degree=max_degree)
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

def _evaluation_package(rng, level, domain, lesson_id, lesson_title,
                        max_degree=_DEFAULT_MAX_VARIABLE_DEGREE):
    if max_degree < 2:
        # Lekcija u kojoj stepenovanje NIJE obrađeno: nivo 3 raste po dimenziji
        # koju kurikulum stvarno traži — „vrijednost izraza s promjenljivim za
        # date vrijednosti PROMJENLJIVIH“ — dakle druga promjenljiva i drugo
        # uvrštavanje, nikad viši stepen.
        return _linear_evaluation_package(rng, level, domain, lesson_id,
                                          lesson_title)
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
# LINEARNI IZRAZ (max_variable_degree = 1)
# ---------------------------------------------------------------------------
# Izraz je i ovdje SERVER-VLASNIČKA STRUKTURA — lista (koeficijent,
# promjenljiva) plus slobodan član — pa se stepen ne „provjerava u tekstu“
# nego po konstrukciji ne može ni nastati: nijedan član nema izlagač.
#
# NIVOI (jedina dimenzija koju dokaz lekcije nosi):
#   1 — jedna promjenljiva, mali koeficijenti, mala vrijednost;
#   2 — jedna promjenljiva, veći koeficijenti i veća vrijednost;
#   3 — DVIJE promjenljive u istom izrazu, dakle dva uvrštavanja i praćenje
#       koja vrijednost ide uz koju promjenljivu.

_LINEAR_VARIABLE_PAIRS = (("x", "y"), ("a", "b"), ("m", "n"), ("p", "q"))


def _linear_term_display(coefficient, variable, leading):
    body = variable if abs(coefficient) == 1 else f"{abs(coefficient)}{variable}"
    if leading:
        return f"-{body}" if coefficient < 0 else body
    return f" - {body}" if coefficient < 0 else f" + {body}"


def _linear_evaluation_package(rng, level, domain, lesson_id, lesson_title):
    # Nivoi 1 i 2 zadržavaju POSTOJEĆI oblik (jedna promjenljiva $x$); mijenja
    # se samo nivo 3, koji je i bio jedino mjesto gdje je nastajao stepen.
    names = ["x"] if level < 3 else list(rng.choice(_LINEAR_VARIABLE_PAIRS))
    terms = []
    for name in names:
        coefficient = _coefficient(rng, level, domain)
        if coefficient == 0:
            raise DeterministicGenerationError("nema promjenljive")
        terms.append((coefficient, name))
    constant = _coefficient(rng, level, domain)

    values = {}
    for name in names:
        value = rng.randint(2, 6 if level == 1 else 9)
        if domain == "integer" and level > 1 and rng.random() < 0.4:
            value = -value
        values[name] = value

    display = "".join(
        _linear_term_display(coefficient, name, leading=index == 0)
        for index, (coefficient, name) in enumerate(terms))
    if constant:
        display += (f" - {abs(constant)}" if constant < 0
                    else f" + {constant}")
    total = Fraction(constant) + sum(
        Fraction(coefficient) * Fraction(values[name])
        for coefficient, name in terms)

    given = " i ".join(f"${name} = {values[name]}$" for name in names)
    question = (f"Izračunaj brojnu vrijednost izraza ${display}$ za {given}.")
    pieces = []
    for coefficient, name in terms:
        factor = core.parenthesized(str(values[name]))
        pieces.append(factor if coefficient == 1 else
                      f"{core.parenthesized(str(coefficient))} \\cdot {factor}")
    substitution = " + ".join(pieces)
    if constant:
        substitution += f" + {core.parenthesized(str(constant))}"
    chain = f"{substitution} = {core.plain_fraction_display(total)}"
    hint1 = ("Brojna vrijednost izraza: umjesto svake promjenljive uvrsti "
             "njenu datu vrijednost pa izračunaj po redoslijedu operacija.")
    hint2 = f"Uvrsti {given}: ${substitution}$."
    hint3 = ("Prvo izračunaj proizvode, pa tek onda saberi (i oduzmi) "
             "dobijene brojeve.")
    solution = (f"Uvrstimo {given}: ${chain}$. Vrijednost izraza je "
                f"${core.fraction_display(total)}$.")
    shift = max(abs(values[name]) for name in names)
    candidates = [total + 1, total - 1, -total, total + shift, total - shift,
                  total + 2]
    if domain == "natural":
        candidates = [value for value in candidates if value >= 0]
    signature = [("expression", display)] + [
        (name, str(values[name])) for name in names]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="expression_evaluation",
        level=level, question=question, answer_value=total,
        answer_display=core.fraction_display(total),
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution, signature_parameters=signature,
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


# ---------------------------------------------------------------------------
# BATCH #3 — IDENTITETI I FAKTORIZACIJA
# ---------------------------------------------------------------------------
# Autoritet: kanonski rječnik {stepen: koeficijent}. Svaka faktorizacija se
# dokazuje RAZVOJEM kandidata nazad u polinom; dvije opcije nikad nemaju isti
# razvoj (dedup po kanonskom rječniku razvoja, ne po tekstu).

def _binom(sign, b, a=1):
    """(ax + sign*b) kao rjecnik."""
    return {1: a, 0: sign * b}


def _binom_display(sign, b, a=1):
    lead = "x" if a == 1 else f"{a}x"
    return f"({lead} {'+' if sign > 0 else '-'} {b})"


def _factored_options(original, candidates):
    """Opcije su FAKTORISANI tekstovi; jednakost se sudi po RAZVOJU."""
    expansions = [dict(original)]
    texts = [candidates[0][0]]
    for text_display, expansion in candidates[1:]:
        cleaned = {d: c for d, c in expansion.items() if c != 0}
        if any(cleaned == seen for seen in expansions):
            continue
        if text_display in texts:
            continue
        expansions.append(cleaned)
        texts.append(text_display)
        if len(texts) == 4:
            break
    if len(texts) != 4:
        raise DeterministicGenerationError("nedovoljno faktorizacija")
    return tuple(f"${display}$" for display in texts)


def _ev_capped(level, cap=2):
    return core.evidence_for_level(min(level, cap) if level < 3 else 3)


def _square_binomial_package(rng, level, domain, lesson_id, lesson_title):
    b = rng.randint(1, 6 if level == 1 else 9)
    a = 1 if level < 3 else rng.randint(2, 3)
    sign = 1 if rng.random() < 0.5 else -1
    binom = _binom(sign, b, a)
    correct = _poly_mul(binom, binom)
    display = _binom_display(sign, b, a)
    question = f"Kvadriraj binom: ${display}^2$"
    wrong_no_middle = {2: a * a, 0: b * b}
    wrong_sign = {2: correct.get(2, 0), 1: -correct.get(1, 0),
                  0: correct.get(0, 0)}
    wrong_half = {2: a * a, 1: sign * a * b, 0: b * b}
    option_texts = _distinct_poly_options(
        correct, [wrong_no_middle, wrong_sign, wrong_half,
                  {2: a * a, 1: correct.get(1, 0), 0: 2 * b}])
    lead = "x" if a == 1 else f"{a}x"
    solution = (f"Kvadrat binoma: prvi kvadrat, dvostruki proizvod pa drugi "
                f"kvadrat. Ovdje: ${display}^2 = {poly_display(correct)}$ — "
                f"srednji član je $2 \\cdot {lead} \\cdot {b}$ sa znakom "
                "binoma.")
    hints = ("Kvadrat binoma ima TRI člana: kvadrat prvog, dvostruki "
             "proizvod i kvadrat drugog člana.",
             f"Kvadrat prvog člana je ${poly_display({2: a * a})}$, a "
             f"kvadrat drugog ${b * b}$ — nedostaje dvostruki proizvod.",
             f"Dvostruki proizvod je $2 \\cdot {lead} \\cdot {b} = "
             f"{poly_display({1: abs(correct.get(1, 0))})}$ sa znakom binoma.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="square_of_binomial",
        level=level, question=question, answer_value=poly_display(correct),
        answer_display=poly_display(correct), distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("binom", f"{a}|{sign}|{b}")],
        required_conditions=["square_of_binomial"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="", evidence=_ev_capped(level))


def _cube_binomial_package(rng, level, domain, lesson_id, lesson_title):
    b = rng.randint(1, 4 if level < 3 else 6)
    sign = 1 if rng.random() < 0.5 else -1
    binom = _binom(sign, b)
    square = _poly_mul(binom, binom)
    correct = _poly_mul(square, binom)
    display = _binom_display(sign, b)
    question = f"Kubiraj binom: ${display}^3$"
    wrong_cubes_only = {3: 1, 0: sign * b ** 3}
    wrong_sign = {d: -c if d % 2 == 0 else c for d, c in correct.items()}
    wrong_middle = {3: 1, 2: sign * 2 * b, 1: 2 * b * b, 0: sign * b ** 3}
    option_texts = _distinct_poly_options(
        correct, [wrong_cubes_only, wrong_sign, wrong_middle,
                  {3: 1, 2: sign * 4 * b, 1: 4 * b * b, 0: sign * b ** 3}])
    solution = (f"Kub binoma ima koeficijente 1, 3, 3, 1: ${display}^3 = "
                f"{poly_display(correct)}$.")
    hints = ("Kub binoma ima CETIRI člana s koeficijentima 1, 3, 3, 1.",
             f"Prvi član je $x^3$, posljednji je kub broja ${b}$ sa znakom "
             "binoma — srednja dva nose trojku.",
             f"Razvij: ${display}^2 = {poly_display(square)}$, pa pomnoži "
             f"jos jednom sa ${display}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="cube_of_binomial",
        level=level, question=question, answer_value=poly_display(correct),
        answer_display=poly_display(correct), distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("binom", f"{sign}|{b}")],
        required_conditions=["cube_of_binomial"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="",
        evidence=_ev_capped(level))


def _factor_diff_squares_package(rng, level, domain, lesson_id, lesson_title):
    b = rng.randint(1, 6 if level == 1 else 9)
    a = 1 if level < 3 else rng.randint(2, 3)
    original = {2: a * a, 0: -b * b}
    correct_display = f"{_binom_display(-1, b, a)}{_binom_display(1, b, a)}"
    candidates = [
        (correct_display, _poly_mul(_binom(-1, b, a), _binom(1, b, a))),
        (f"{_binom_display(-1, b, a)}^2",
         _poly_mul(_binom(-1, b, a), _binom(-1, b, a))),
        (f"{_binom_display(1, b, a)}^2",
         _poly_mul(_binom(1, b, a), _binom(1, b, a))),
        # Za a = 1 varijanta s pomjerenim b: (x-(b+1))(x+(b+1)) — kandidat
        # razlike kvadrata mora imati RAZLIČIT razvoj od tačnog.
        (f"{_binom_display(-1, b + 1, a)}{_binom_display(1, b + 1, a)}",
         _poly_mul(_binom(-1, b + 1, a), _binom(1, b + 1, a))),
    ]
    assert candidates[0][1] == original
    option_texts = _factored_options(original, candidates)
    lead = "x" if a == 1 else f"{a}x"
    question = (f"Rastavi na faktore (razlika kvadrata): "
                f"${poly_display(original)}$")
    solution = (f"Razlika kvadrata se rastavlja kao proizvod razlike i "
                f"zbira: ${poly_display(original)} = {correct_display}$ — "
                "razvoj vraca polazni polinom.")
    hints = ("Razlika kvadrata se rastavlja kao proizvod razlike i zbira.",
             f"Prepoznaj kvadrate: prvi član je kvadrat od ${lead}$, a "
             f"drugi od ${b}$.",
             f"Dakle: $({lead} - {b})({lead} + {b})$ — provjeri razvojem.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="factor_difference_squares",
        level=level, question=question, answer_value=correct_display,
        answer_display=correct_display, distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("a", str(a)), ("b", str(b))],
        required_conditions=["factor_difference_squares"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="", evidence=_ev_capped(level))


def _factor_common_package(rng, level, domain, lesson_id, lesson_title):
    k = rng.randint(2, 5)
    p = rng.randint(1, 5)
    q = rng.randint(1, 6)
    if p == q:
        q += 1
    if level == 3:
        # Tri člana — tri izlučivanja, iskren dokaz nivoa 3.
        r = rng.randint(1, 6)
        while r in (p, q):
            r += 1
        original = {3: k * p, 2: k * q, 1: k * r}
        inner = {2: p, 1: q, 0: r}
    else:
        original = {2: k * p, 1: k * q}
        inner = {1: p, 0: q}
    correct_display = f"{k}x({poly_display(inner)})"
    assert _poly_mul({1: k}, inner) == original
    candidates = [(correct_display, original)]
    for delta in (1, 2, 3, 4):
        variant_inner = {1: p, 0: q + delta}
        candidates.append((f"{k}x({poly_display(variant_inner)})",
                           _poly_mul({1: k}, variant_inner)))
    candidates.append((f"{k}x({poly_display({1: p + 1, 0: q})})",
                       _poly_mul({1: k}, {1: p + 1, 0: q})))
    option_texts = _factored_options(original, candidates)
    question = (f"Izluči najveći zajednički faktor: ${poly_display(original)}$")
    solution = (f"Najveći zajednički faktor članova je ${k}x$: "
                f"${poly_display(original)} = {correct_display}$ — razvoj "
                "vraca polazni izraz.")
    hints = ("Nađi najveći broj i najveći stepen od x koji dijele SVE "
             "članove.",
             f"Koeficijenti su djeljivi sa ${k}$, a svaki član sadrži $x$.",
             f"Izluči ${k}x$ pa zapiši ostatak svakog člana u zagradi.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="factor_common",
        level=level, question=question, answer_value=correct_display,
        answer_display=correct_display, distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("k", str(k)), ("p", str(p)), ("q", str(q)),
                              ("terms", str(len(original)))],
        required_conditions=["factor_common"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="",
        evidence=core.evidence_for_level(level if level == 3 else
                                         min(level, 2)))


def _factor_grouping_package(rng, level, domain, lesson_id, lesson_title):
    b = rng.randint(1, 6)
    c = rng.randint(1, 6)
    if b == c:
        c += 1
    question_display = f"x^2 + {b}x + {c}x + {b * c}"
    original = {2: 1, 1: b + c, 0: b * c}
    correct_display = f"{_binom_display(1, b)}{_binom_display(1, c)}"
    candidates = [
        (correct_display, _poly_mul(_binom(1, b), _binom(1, c))),
        (f"{_binom_display(1, b + c)}{_binom_display(1, 1)}",
         _poly_mul(_binom(1, b + c), _binom(1, 1))),
        (f"{_binom_display(-1, b)}{_binom_display(-1, c)}",
         _poly_mul(_binom(-1, b), _binom(-1, c))),
        (f"{_binom_display(1, b)}{_binom_display(1, c + 1)}",
         _poly_mul(_binom(1, b), _binom(1, c + 1))),
    ]
    assert candidates[0][1] == original
    option_texts = _factored_options(original, candidates)
    question = f"Rastavi grupisanjem: ${question_display}$"
    solution = (f"Grupisemo: $x(x + {b}) + {c}(x + {b}) = {correct_display}$ "
                "— razvoj vraca polazni izraz.")
    hints = ("Grupiši po dva člana tako da svaka grupa ima zajednički "
             "faktor.",
             f"Iz prve grupe izluči $x$, a iz druge ${c}$ — u obje ostaje "
             f"$(x + {b})$.",
             f"Zajednički binom $(x + {b})$ izluči ispred zagrade.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="factor_grouping",
        level=level, question=question, answer_value=correct_display,
        answer_display=correct_display, distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("b", str(b)), ("c", str(c))],
        required_conditions=["factor_grouping"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="",
        evidence=_ev_capped(level))


def _factor_identity_package(rng, level, domain, lesson_id, lesson_title):
    b = rng.randint(1, 6 if level == 1 else 9)
    sign = 1 if rng.random() < 0.5 else -1
    original = _poly_mul(_binom(sign, b), _binom(sign, b))
    correct_display = f"{_binom_display(sign, b)}^2"
    candidates = [
        (correct_display, original),
        (f"{_binom_display(-sign, b)}^2",
         _poly_mul(_binom(-sign, b), _binom(-sign, b))),
        (f"{_binom_display(-1, b)}{_binom_display(1, b)}",
         _poly_mul(_binom(-1, b), _binom(1, b))),
        (f"{_binom_display(sign, b + 1)}^2",
         _poly_mul(_binom(sign, b + 1), _binom(sign, b + 1))),
    ]
    option_texts = _factored_options(original, candidates)
    sign_word = "plus" if sign > 0 else "minus"
    question = f"Rastavi primjenom identiteta: ${poly_display(original)}$"
    solution = (f"Trinom je kvadrat binoma: ${poly_display(original)} = "
                f"{correct_display}$ — razvoj vraca polazni polinom.")
    hints = ("Provjeri da li je trinom kvadrat binoma: prvi i posljednji "
             "član su kvadrati, a srednji dvostruki proizvod.",
             f"Kvadrati su $x^2$ i ${b * b}$; srednji član je "
             f"$2 \\cdot x \\cdot {b}$ sa znakom {sign_word}.",
             f"Dakle: $(x {'+' if sign > 0 else '-'} {b})^2$ — provjeri "
             "razvojem.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="factor_identity",
        level=level, question=question, answer_value=correct_display,
        answer_display=correct_display, distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("sign", str(sign)), ("b", str(b))],
        required_conditions=["factor_identity"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="", evidence=_ev_capped(level))


def _sum_diff_cubes_package(rng, level, domain, lesson_id, lesson_title):
    b = rng.randint(1, 4)
    sign = 1 if rng.random() < 0.5 else -1
    original = {3: 1, 0: sign * b ** 3}
    quad = {2: 1, 1: -sign * b, 0: b * b}
    correct_display = f"{_binom_display(sign, b)}({poly_display(quad)})"
    wrong_quad = {2: 1, 1: sign * b, 0: b * b}
    candidates = [
        (correct_display, _poly_mul(_binom(sign, b), quad)),
        (f"{_binom_display(sign, b)}^3",
         _poly_mul(_poly_mul(_binom(sign, b), _binom(sign, b)),
                   _binom(sign, b))),
        (f"{_binom_display(sign, b)}({poly_display(wrong_quad)})",
         _poly_mul(_binom(sign, b), wrong_quad)),
        (f"{_binom_display(-sign, b)}({poly_display(quad)})",
         _poly_mul(_binom(-sign, b), quad)),
    ]
    assert candidates[0][1] == original
    option_texts = _factored_options(original, candidates)
    kind_word = "zbir" if sign > 0 else "razliku"
    question = f"Rastavi {kind_word} kubova: ${poly_display(original)}$"
    solution = (f"Zbir/razlika kubova: binom puta trinom bez dvojke — "
                f"${poly_display(original)} = {correct_display}$.")
    hints = ("Zbir/razlika kubova se rastavlja na binom i trinom.",
             f"Kubovi su $x^3$ i ${b ** 3}$, pa je binom "
             f"$(x {'+' if sign > 0 else '-'} {b})$.",
             "U trinomu srednji član mijenja znak binoma i nema dvojke.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="sum_diff_cubes",
        level=level, question=question, answer_value=correct_display,
        answer_display=correct_display, distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("sign", str(sign)), ("b", str(b))],
        required_conditions=["sum_diff_cubes"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="",
        evidence=_ev_capped(level))


def _zero_product_package(rng, level, domain, lesson_id, lesson_title):
    p = rng.randint(1, 8)
    q = rng.randint(1, 8)
    while q == p:
        q = rng.randint(1, 8)
    root_p = p if rng.random() < 0.5 else -p
    root_q = -q if rng.random() < 0.5 else q
    display = (f"(x {'-' if root_p > 0 else '+'} {abs(root_p)})"
               f"(x {'-' if root_q > 0 else '+'} {abs(root_q)}) = 0")
    correct = f"x = {root_p} ili x = {root_q}"
    options = [correct,
               f"x = {-root_p} ili x = {-root_q}",
               f"x = {root_p} ili x = {-root_q}",
               f"x = {root_p + 1} ili x = {root_q}"]
    option_texts = tuple(f"${item}$" for item in options)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = f"Riješi jednačinu: ${display}$"
    solution = (f"Proizvod je nula kad je bar jedan faktor nula: prvi faktor "
                f"daje $x = {root_p}$, a drugi $x = {root_q}$ — oba broja "
                "poništavaju po jedan faktor polazne jednačine.")
    hints = ("Nula proizvoda: proizvod je nula tačno kad je neki faktor "
             "nula.",
             "Izjednači SVAKI faktor s nulom i riješi dvije male jednačine.",
             f"Prvi faktor daje $x = {root_p}$ — nađi i drugu vrijednost.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="zero_product",
        level=level, question=question, answer_value=correct,
        answer_display=correct, distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("roots", f"{root_p}|{root_q}")],
        required_conditions=["zero_product"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="", evidence=_ev_capped(level))


def _fraction_domain_package(rng, level, domain, lesson_id, lesson_title):
    p = rng.randint(1, 8)
    q = rng.randint(1, 8)
    while q == p:
        q = rng.randint(1, 8)
    numerator = rng.randint(1, 9)
    factored = f"(x - {p})(x + {q})"
    expansion = _poly_mul({1: 1, 0: -p}, {1: 1, 0: q})
    denominator_display = factored if level == 1 else poly_display(expansion)
    correct = f"x = {p} i x = {-q}"
    options = [correct,
               f"x = {-p} i x = {q}",
               f"x = {p} i x = {q}",
               f"x = {p * q} i x = {q - p}"]
    option_texts = tuple(f"${item}$" for item in options)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = ("Za koje vrijednosti promjenljive $x$ razlomak "
                f"$\\frac{{{numerator}}}{{{denominator_display}}}$ NIJE "
                "definisan?")
    solution = (f"Razlomak nije definisan kad je nazivnik nula: "
                f"${factored} = 0$ daje $x = {p}$ i $x = {-q}$.")
    hint2 = ("Rastavi nazivnik na faktore pa svaki faktor izjednači s nulom."
             if level > 1 else
             "Izjednači svaki faktor nazivnika s nulom.")
    hints = ("Razlomak nije definisan kad mu je nazivnik jednak nuli.",
             hint2,
             f"Faktori su $(x - {p})$ i $(x + {q})$ — nule su njihova "
             "rješenja.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="fraction_domain",
        level=level, question=question, answer_value=correct,
        answer_display=correct, distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("p", str(p)), ("q", str(q))],
        required_conditions=["fraction_domain"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="",
        evidence=_ev_capped(level))


def _monomial_mul_div_package(rng, level, domain, lesson_id, lesson_title):
    c1 = rng.randint(2, 6)
    c2 = rng.randint(2, 6)
    d1 = rng.randint(1, 3 if level == 1 else 4)
    d2 = rng.randint(1, 3 if level == 1 else 4)
    multiply = rng.random() < 0.5 or level == 1
    if level == 3:
        # c1*c2 ne mora imati djelioca u (2,3,4,6) — npr. 5*5=25 — pa je c1
        # uvijek siguran rezervni djelilac proizvoda (Batch #3 fuzz nalaz).
        pool = [c for c in (2, 3, 4, 6) if (c1 * c2) % c == 0] or [c1]
        c3 = rng.choice(pool)
        d3 = rng.randint(1, min(d1 + d2 - 1, 3))
        result = {d1 + d2 - d3: (c1 * c2) // c3}
        question = (f"Izračunaj: ${poly_display({d1: c1})} \cdot "
                    f"{poly_display({d2: c2})} : {poly_display({d3: c3})}$")
        rule = ("Pri množenju monoma izložioci se sabiraju, a pri "
                "dijeljenju oduzimaju")
        work = (f"({c1} \cdot {c2} : {c3})x^{{{d1}+{d2}-{d3}}} = "
                f"{poly_display(result)}")
        wrong = [{d1 + d2 + d3: (c1 * c2) // c3},
                 {d1 + d2 - d3: c1 * c2 * c3},
                 {d1 + d2 - d3: (c1 * c2) // c3 + 1}]
        operation = "monomial_chain"
        option_texts = _distinct_poly_options(result, wrong)
        solution = f"{rule}: ${work}$."
        hints = (f"{rule}.",
                 "Prvo sredi koeficijente, zatim stepene od $x$.",
                 f"Postupak: ${work}$.")
        return core.build_package(
            lesson_id=lesson_id, lesson_title=lesson_title,
            family_id="polynomial_basic", operation=operation, level=level,
            question=question, answer_value=poly_display(result),
            answer_display=poly_display(result), distractor_values=(),
            hints=hints, solution=solution,
            signature_parameters=[("c", f"{c1}|{c2}|{c3}"),
                                  ("d", f"{d1}|{d2}|{d3}")],
            required_conditions=[operation],
            relevant_objects=["polynomial"],
            generator_version=GENERATOR_VERSION, option_texts=option_texts,
            wrap="", evidence=core.evidence_for_level(3))
    if multiply:
        result = {d1 + d2: c1 * c2}
        question = (f"Pomnozi monome: ${poly_display({d1: c1})} \\cdot "
                    f"{poly_display({d2: c2})}$")
        rule = ("Monomi se mnoze tako da se pomnoze koeficijenti, a "
                "izlozioci SABERU")
        work = f"({c1} \\cdot {c2})x^{{{d1}+{d2}}} = {poly_display(result)}"
        wrong = [{d1 * d2: c1 * c2}, {d1 + d2: c1 + c2},
                 {d1 + d2: c1 * c2 + 1}]
        operation = "monomial_multiply"
    else:
        big = {d1 + d2: c1 * c2}
        result = {d2: c2}
        question = (f"Podijeli monome: ${poly_display(big)} : "
                    f"{poly_display({d1: c1})}$")
        rule = ("Monomi se dijele tako da se podijele koeficijenti, a "
                "izlozioci ODUZMU")
        work = (f"({c1 * c2} : {c1})x^{{{d1 + d2}-{d1}}} = "
                f"{poly_display(result)}")
        wrong = [{d1 + d2 + d1: c2}, {d2: c1 * c2 - c1}, {d2 + 1: c2}]
        operation = "monomial_divide"
    option_texts = _distinct_poly_options(result, wrong)
    solution = f"{rule}: ${work}$."
    hints = (f"{rule}.",
             "Prvo sredi koeficijente, zatim stepene od $x$.",
             f"Postupak: ${work}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation=operation, level=level,
        question=question, answer_value=poly_display(result),
        answer_display=poly_display(result), distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("c", f"{c1}|{c2}"), ("d", f"{d1}|{d2}"),
                              ("op", operation)],
        required_conditions=[operation], relevant_objects=["polynomial"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", evidence=core.evidence_for_level(min(level, 2)))


def _like_terms_select_package(rng, level, domain, lesson_id, lesson_title):
    if level == 3:
        raise DeterministicGenerationError(
            "prepoznavanje sličnih monoma nema trostepenu varijantu — "
            "nivo 3 nosi combine_like_terms")
    degree = rng.randint(1, 4)
    coefficient = rng.randint(2, 9)
    target = poly_display({degree: coefficient})
    like_coefficient = rng.choice([c for c in range(-9, 10)
                                   if c not in (0, coefficient)])
    like = poly_display({degree: like_coefficient})
    unlike = [poly_display({degree + 1: coefficient}),
              poly_display({max(degree - 1, 0): coefficient}),
              poly_display({degree + 2: rng.randint(2, 9)})]
    options = [f"${like}$"] + [f"${item}$" for item in unlike]
    if len(set(options)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = f"Koji je od ponudjenih monoma SLICAN monomu ${target}$?"
    solution = (f"Slicni monomi imaju ISTU promjenljivu s ISTIM izloziocem — "
                f"${like}$ ima isti stepen kao ${target}$, a koeficijent "
                "smije biti razlicit.")
    hints = ("Slicni monomi imaju istu promjenljivu s istim izloziocem.",
             f"Trazi monom ciji je stepen uz $x$ jednak stepenu datog monoma.",
             "Koeficijent NIJE bitan za slicnost — bitan je samo stepen.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polynomial_basic", operation="like_terms_select",
        level=level, question=question, answer_value=options[0],
        answer_display=like, distractor_values=(),
        hints=hints, solution=solution,
        signature_parameters=[("target", target)],
        required_conditions=["like_terms_select"],
        relevant_objects=["polynomial"], generator_version=GENERATOR_VERSION,
        option_texts=tuple(options), wrap="",
        evidence=core.evidence_for_level(min(level, 2)))
