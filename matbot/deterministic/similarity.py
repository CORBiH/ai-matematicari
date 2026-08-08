"""Deterministička sličnost i proporcionalne duži (Batch #4, Prioritet 7).

Jedna semantička porodica: ``similarity_direct``.

  • ``proportional_segments``  — nepoznata duž iz proporcije a : b = c : x;
  • ``similarity_coefficient`` — koeficijent sličnosti iz odgovarajućih
    stranica;
  • ``similar_perimeter``      — obim slične figure (skalira se sa k);
  • ``similar_area``           — površina slične figure (skalira se sa k²).

MATEMATIČKI AUTORITET: egzaktna racionalna aritmetika; svaka tvrdnja o
skaliranju dokazana je množenjem u rješenju. Konstrukcijske i dokazne
lekcije sličnosti (Talesova teorema kao dokaz, konstrukcije) NISU u obimu.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("similarity_direct",)
GENERATOR_VERSION = "detsimilarity-1"

_SUPPORTED_CONCEPTS = frozenset({
    "proportional_segments", "similarity_coefficient", "similar_perimeter",
    "similar_area",
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
        "proportional_segments": _segments_package,
        "similarity_coefficient": _coefficient_package,
        "similar_perimeter": _perimeter_package,
        "similar_area": _area_package,
    }
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            return builders[concept](rng, level, lesson_id, lesson_title, concept)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _int_display(value: Fraction) -> str:
    return core.fraction_display(value)


def _segments_package(rng, level, lesson_id, lesson_title, concept):
    a = rng.randint(2, 6 if level == 1 else 9)
    b = rng.randint(2, 6 if level == 1 else 9)
    if a == b:
        raise DeterministicGenerationError("trivijalna proporcija")
    factor = rng.randint(2, 4 if level < 3 else 8)
    c = a * factor
    x = b * factor
    question = (f"Duži su proporcionalne: $a : b = c : d$, pri čemu je "
                f"$a = {a}$ cm, $b = {b}$ cm i $c = {c}$ cm. Kolika je "
                "dužina $d$?")
    answer = Fraction(x)
    hints = (
        "Iz proporcije slijedi jednakost unakrsnih proizvoda.",
        f"Zapiši: ${a} \\cdot d = {b} \\cdot {c}$.",
        f"Izrazi: $d = {b * c} : {a}$.",
    )
    solution = (f"Iz ${a} \\cdot d = {b} \\cdot {c}$ slijedi "
                f"$d = {b * c} : {a} = {x}$, dakle $d = {x}$ cm. Provjera: "
                f"${a} : {b} = {c} : {x}$ jer su unakrsni proizvodi "
                f"${a * x}$ i ${b * c}$ jednaki.")
    distractors = [Fraction(a * factor), answer + 1, answer - 1,
                   Fraction(a * b), answer + factor]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="similarity_direct", operation=concept, level=level,
        question=question, answer_value=answer, answer_display=str(x),
        distractor_values=distractors, hints=hints, solution=solution,
        signature_parameters=[("a", str(a)), ("b", str(b)), ("c", str(c))],
        required_conditions=[concept], relevant_objects=["duž"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: core.fraction_display(value))


def _coefficient_pool(rng, level):
    if level == 1:
        return Fraction(rng.randint(2, 4))
    if level == 2:
        return Fraction(rng.choice((2, 3, 4, 5)))
    return Fraction(rng.choice((1, 3)), 2) + (1 if rng.random() < 0.5 else 0)


def _coefficient_package(rng, level, lesson_id, lesson_title, concept):
    k = _coefficient_pool(rng, level)
    a = rng.randint(3, 9) * (k.denominator)
    a_prime = Fraction(a) * k
    question = (f"Trouglovi su slični, a odgovarajuće stranice iznose "
                f"$a = {a}$ cm i $a' = {_int_display(a_prime)}$ cm. Koliki "
                "je koeficijent sličnosti $k = a' : a$?")
    answer = k
    hints = (
        "Koeficijent sličnosti je količnik odgovarajućih stranica.",
        f"Izračunaj $k = {_int_display(a_prime)} : {a}$.",
        "Skrati dobijeni razlomak do kraja.",
    )
    solution = (f"$k = {_int_display(a_prime)} : {a} = {_int_display(k)}$. "
                f"Provjera: ${a} \\cdot {_int_display(k)} = "
                f"{_int_display(a_prime)}$.")
    distractors = [Fraction(1) / k if k != 0 else k + 1, k + 1,
                   k - Fraction(1, 2), k * 2]
    distractors = [d for d in distractors if d > 0 and d != k]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="similarity_direct", operation=concept, level=level,
        question=question, answer_value=answer,
        answer_display=_int_display(k), distractor_values=distractors,
        hints=hints, solution=solution,
        signature_parameters=[("a", str(a)), ("k", str(k))],
        required_conditions=[concept], relevant_objects=["trougao"],
        generator_version=GENERATOR_VERSION,
        display_of=_int_display)


def _perimeter_package(rng, level, lesson_id, lesson_title, concept):
    k = Fraction(rng.randint(2, 3 if level == 1 else 5))
    perimeter = Fraction(rng.randint(6, 20 if level < 3 else 40))
    scaled = perimeter * k
    question = (f"Obim trougla iznosi $O = {_int_display(perimeter)}$ cm. "
                f"Njemu sličan trougao ima koeficijent sličnosti "
                f"$k = {_int_display(k)}$. Koliki je obim sličnog trougla?")
    answer = scaled
    hints = (
        "Kod sličnih figura obim se mijenja ISTIM koeficijentom kao "
        "stranice.",
        f"Izračunaj ${_int_display(perimeter)} \\cdot {_int_display(k)}$.",
        "Svaka stranica je k puta duža, pa je i zbir svih stranica k puta "
        "veći.",
    )
    solution = (f"Obim se skalira koeficijentom $k$: "
                f"$O' = {_int_display(perimeter)} \\cdot {_int_display(k)} "
                f"= {_int_display(scaled)}$ cm.")
    distractors = [perimeter * k * k, perimeter + k, scaled + 1,
                   perimeter]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="similarity_direct", operation=concept, level=level,
        question=question, answer_value=answer,
        answer_display=_int_display(scaled), distractor_values=distractors,
        hints=hints, solution=solution,
        signature_parameters=[("perimeter", str(perimeter)), ("k", str(k))],
        required_conditions=[concept], relevant_objects=["trougao"],
        generator_version=GENERATOR_VERSION, display_of=_int_display)


def _area_package(rng, level, lesson_id, lesson_title, concept):
    k = Fraction(rng.randint(2, 3 if level < 3 else 5))
    area = Fraction(rng.randint(4, 20 if level < 3 else 50))
    scaled = area * k * k
    question = (f"Površina trougla iznosi $P = {_int_display(area)}$ cm². "
                f"Njemu sličan trougao ima koeficijent sličnosti "
                f"$k = {_int_display(k)}$. Kolika je površina sličnog "
                "trougla?")
    answer = scaled
    hints = (
        "Kod sličnih figura površina se mijenja KVADRATOM koeficijenta "
        "sličnosti.",
        f"Izračunaj $k^{{2}} = {_int_display(k * k)}$.",
        f"Pomnoži: ${_int_display(area)} \\cdot {_int_display(k * k)}$.",
    )
    solution = (f"Površina se skalira sa $k^{{2}} = {_int_display(k * k)}$: "
                f"$P' = {_int_display(area)} \\cdot {_int_display(k * k)} = "
                f"{_int_display(scaled)}$ cm².")
    distractors = [area * k, scaled + area, area, scaled - k]
    distractors = [d for d in distractors if d > 0 and d != scaled]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="similarity_direct", operation=concept, level=level,
        question=question, answer_value=answer,
        answer_display=_int_display(scaled), distractor_values=distractors,
        hints=hints, solution=solution,
        signature_parameters=[("area", str(area)), ("k", str(k))],
        required_conditions=[concept], relevant_objects=["trougao"],
        generator_version=GENERATOR_VERSION, display_of=_int_display)
