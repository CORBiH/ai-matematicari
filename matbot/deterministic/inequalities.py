"""Determinističke linearne nejednačine (Batch #4, Prioritet 7).

Jedna semantička porodica: ``linear_inequality_direct``.

  • ``equivalent_inequality`` — ekvivalentne nejednačine (množenje/dijeljenje
    negativnim brojem OBRĆE znak);
  • ``fraction_inequality``   — nejednačina s razlomljenim koeficijentima,
    egzaktno rješiva;
  • ``interval_solution``     — skup rješenja zapisan intervalom.

MATEMATIČKI AUTORITET: egzaktna racionalna aritmetika; svaka transformacija
nejednačine čuva (ili dokazano obrće) znak, a rješenje se potvrđuje
uvrštavanjem probne vrijednosti sa svake strane granice.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("linear_inequality_direct",)
GENERATOR_VERSION = "detineq-1"

_SUPPORTED_CONCEPTS = frozenset({
    "equivalent_inequality", "fraction_inequality", "interval_solution",
})


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
        "equivalent_inequality": _equivalent_package,
        "fraction_inequality": _fraction_package,
        "interval_solution": _interval_package,
    }
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            return builders[concept](rng, level, lesson_id, lesson_title, concept)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _package(lesson_id, lesson_title, concept, level, question, option_texts,
             hints, solution, answer_display, signature):
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_inequality_direct", operation=concept, level=level,
        question=question, answer_value=None, answer_display=answer_display,
        distractor_values=(), hints=hints, solution=solution,
        signature_parameters=signature, required_conditions=[concept],
        relevant_objects=["nejednačina"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _nonzero(rng, low, high):
    value = 0
    while value == 0:
        value = rng.randint(low, high)
    return value


_RELATIONS = ("<", ">", "\\leq", "\\geq")
_FLIPPED = {"<": ">", ">": "<", "\\leq": "\\geq", "\\geq": "\\leq"}


def _equivalent_package(rng, level, lesson_id, lesson_title, concept):
    a = -rng.randint(2, 4 if level == 1 else 9)
    relation = rng.choice(_RELATIONS[:2] if level == 1 else _RELATIONS)
    bound = _nonzero(rng, -9, 9)
    product = a * bound
    inequality = f"{a}x {relation} {product}"
    flipped = _FLIPPED[relation]
    correct = f"x {flipped} {bound}"
    wrong = [f"x {relation} {bound}", f"x {flipped} {-bound}",
             f"x {relation} {-bound}"]
    option_texts = (f"${correct}$", *(f"${w}$" for w in wrong))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = (f"Koja je nejednačina EKVIVALENTNA nejednačini "
                f"${inequality}$?")
    hints = (
        "Ekvivalentne nejednačine imaju isti skup rješenja.",
        f"Podijeli obje strane sa ${a}$ — negativnim brojem.",
        "Dijeljenje (ili množenje) NEGATIVNIM brojem obrće znak "
        "nejednakosti.",
    )
    test_value = Fraction(bound) + (1 if flipped in (">", "\\geq") else -1)
    solution = (f"Dijeljenjem sa ${a}$ (negativan broj!) znak se obrće: "
                f"${inequality}$ postaje ${correct}$. Provjera za "
                f"$x = {core.fraction_display(test_value)}$: "
                "obje nejednačine su tada tačne, a za vrijednost s druge "
                "strane granice obje su netačne.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("inequality", inequality)])


def _fraction_package(rng, level, lesson_id, lesson_title, concept):
    denominator = rng.choice((2, 3, 4, 5))
    b = _nonzero(rng, -6, 6)
    solution_bound = _nonzero(rng, -9, 9)
    c = Fraction(solution_bound, denominator) + b
    if c.denominator not in (1, denominator):
        raise DeterministicGenerationError("granica nije školska")
    relation = rng.choice(("<", ">") if level < 3 else _RELATIONS)
    sign = "+" if b >= 0 else "-"
    c_text = core.fraction_display(c)
    inequality = f"\\frac{{x}}{{{denominator}}} {sign} {abs(b)} {relation} {c_text}"
    correct = f"x {relation} {solution_bound}"
    wrong = [f"x {_FLIPPED[relation]} {solution_bound}",
             f"x {relation} {-solution_bound}",
             f"x {relation} {solution_bound + denominator}"]
    option_texts = (f"${correct}$", *(f"${w}$" for w in wrong))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = f"Riješi nejednačinu: ${inequality}$."
    rhs = c - b
    hints = (
        f"Prebaci slobodni član: oduzmi ${core.fraction_display(Fraction(b))}$ "
        "s obje strane.",
        f"Ostaje $\\frac{{x}}{{{denominator}}} {relation} "
        f"{core.fraction_display(rhs)}$.",
        f"Pomnoži obje strane sa ${denominator}$ — pozitivan broj, znak "
        "ostaje isti.",
    )
    solution = (f"Poslije prebacivanja: $\\frac{{x}}{{{denominator}}} "
                f"{relation} {core.fraction_display(rhs)}$; množenjem sa "
                f"${denominator}$ (pozitivno, znak se NE mijenja) dobijamo "
                f"${correct}$.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("inequality", inequality)])


def _interval_package(rng, level, lesson_id, lesson_title, concept):
    a = rng.choice((1, 2, 3) if level == 1 else (2, 3, -2, 4, -3))
    bound = _nonzero(rng, -9, 9)
    b = _nonzero(rng, -9, 9)
    c = a * bound + b
    relation = rng.choice(("<", ">") if level < 3 else _RELATIONS)
    effective = relation if a > 0 else _FLIPPED[relation]
    inequality = f"{a}x + {b} {relation} {c}" if b >= 0 else \
        f"{a}x - {abs(b)} {relation} {c}"
    open_left = effective in (">", "\\geq")
    strict = effective in ("<", ">")
    if open_left:
        interval = f"({bound}, +\\infty)" if strict else f"[{bound}, +\\infty)"
        wrong_interval = f"(-\\infty, {bound})" if strict else \
            f"(-\\infty, {bound}]"
    else:
        interval = f"(-\\infty, {bound})" if strict else \
            f"(-\\infty, {bound}]"
        wrong_interval = f"({bound}, +\\infty)" if strict else \
            f"[{bound}, +\\infty)"
    flipped_strictness = (f"[{bound}, +\\infty)" if open_left and strict else
                          f"({bound}, +\\infty)" if open_left else
                          f"(-\\infty, {bound}]" if strict else
                          f"(-\\infty, {bound})")
    shifted = (f"({bound + 1}, +\\infty)" if open_left else
               f"(-\\infty, {bound + 1})")
    option_texts = (f"${interval}$", f"${wrong_interval}$",
                    f"${flipped_strictness}$", f"${shifted}$")
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("intervali nisu jedinstveni")
    question = (f"Riješi nejednačinu ${inequality}$ i zapiši skup rješenja "
                "intervalom.")
    negative_note = (" Pazi: dijeljenje negativnim brojem obrće znak."
                     if a < 0 else "")
    hints = (
        "Prvo riješi nejednačinu kao i obično, pa rješenje pretoči u "
        "interval." + negative_note,
        f"Slobodni član prebaci na desnu stranu pa podijeli sa ${a}$.",
        "Otvorena zagrada ide uz strogu nejednakost i uz beskonačnost; "
        "uglasta uz „manje ili jednako“ / „veće ili jednako“.",
    )
    solution = (f"Rješavanjem se dobija $x {effective} {bound}$"
                + (" (znak se obrnuo zbog dijeljenja negativnim brojem)"
                   if a < 0 else "")
                + f", što se intervalom zapisuje ${interval}$.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, interval,
                    [("inequality", inequality)])
