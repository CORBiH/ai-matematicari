"""Deterministički generator porodice pretvaranja razlomak ↔ decimalni zapis.

Jedna semantička porodica (`fraction_decimal_conversion`) s konceptima koje
nose parametri ugovora:

  • fraction_to_decimal — razlomak s KONAČNIM decimalnim zapisom → decimala;
  • decimal_to_fraction — decimala → SKRAĆEN razlomak;
  • decimal_place_value — cifra na datom decimalnom mjestu.

MATEMATIČKI AUTORITET: egzaktni `fractions.Fraction`; decimalni prikaz
isključivo core.decimal_display (dekadski imenilac), nikad binarni float.
Razlomak bez konačnog decimalnog zapisa NE ULAZI u zadatak pretvaranja —
generator ga odbija pri konstrukciji.

OPCIJE NIKAD NE MIJEŠAJU ZAPISE: kod pretvaranja u decimalu sve četiri
opcije su decimale, kod pretvaranja u razlomak sve četiri su razlomci —
razlomačka i decimalna opcija iste vrijednosti bile bi dokazani semantički
duplikat. Iz istog razloga „neskraćeni ekvivalent“ ($\\frac{75}{100}$ uz
$\\frac{3}{4}$) NE SMIJE biti distraktor: to je ista vrijednost u dva zapisa
i objava bi paket odbila. Neskraćivanje se ispituje kroz rješenje i
nagovještaje, a distraktori su uvijek DRUGE vrijednosti (pogrešno pomjeren
zarez, zamijenjen brojnik/imenilac, pogrešan predznak).
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("fraction_decimal_conversion",)
GENERATOR_VERSION = "detconv-1"

_SUPPORTED_CONCEPTS = frozenset({"fraction_to_decimal", "decimal_to_fraction",
                                 "decimal_place_value"})
_SUPPORTED_SCOPES = frozenset({"nonneg", "signed"})

# Imenioci s konačnim decimalnim zapisom, po nivou složenosti.
_DENOMINATORS = {1: (2, 4, 5, 10), 2: (4, 5, 8, 20, 25, 50), 3: (8, 16, 40, 125)}


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    if not concepts or not concepts <= _SUPPORTED_CONCEPTS:
        return False
    scope = parameters.get("number_scope") or "nonneg"
    return scope in _SUPPORTED_SCOPES


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    scope = parameters.get("number_scope") or "nonneg"
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            builder = {
                "fraction_to_decimal": _fraction_to_decimal_package,
                "decimal_to_fraction": _decimal_to_fraction_package,
                "decimal_place_value": _decimal_place_value_package,
            }[concept]
            return builder(rng, level, scope, lesson_id, lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _conversion_value(rng, level, scope):
    denominator = rng.choice(_DENOMINATORS[level])
    numerator = rng.randint(1, denominator - 1)
    value = Fraction(numerator, denominator)
    if value.denominator == 1:
        raise DeterministicGenerationError("vrijednost je cio broj")
    if level == 3 and rng.random() < 0.5:
        value += rng.randint(1, 3)          # i mješovita vrijednost > 1
    if scope == "signed" and rng.random() < 0.5:
        value = -value
    if not core.is_terminating_decimal(value):
        raise DeterministicGenerationError("nema konačan decimalan zapis")
    return value


def _distinct_decimal_options(correct, candidates):
    option_values, option_texts = [correct], [f"${core.decimal_display(correct)}$"]
    for candidate in candidates:
        if not core.is_terminating_decimal(candidate):
            continue
        if any(candidate == seen for seen in option_values):
            continue
        text = f"${core.decimal_display(candidate)}$"
        if text in option_texts:
            continue
        option_values.append(candidate)
        option_texts.append(text)
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno decimalnih opcija")
    return tuple(option_texts)


def _fraction_to_decimal_package(rng, level, scope, lesson_id, lesson_title):
    value = _conversion_value(rng, level, scope)
    fraction_display = core.plain_fraction_display(value)
    decimal_display = core.decimal_display(value)
    scale = 10 ** core.decimal_places(value)
    expanded_numerator = (value * scale).numerator
    question = (f"Koji decimalni zapis odgovara razlomku ${fraction_display}$?")
    # Pogrešno pomjeren zarez, obrnut razlomak, pogrešan predznak — sve DRUGE
    # vrijednosti.
    candidates = [value * 10, value / 10, -value,
                  Fraction(value.denominator, value.numerator)
                  if value.numerator not in (0, value.denominator) else value * 100,
                  value + Fraction(1, 10)]
    option_texts = _distinct_decimal_options(value, candidates)
    hint1 = ("Razlomak se pretvara u decimalni zapis proširivanjem na "
             "dekadski imenilac (10, 100, 1000...) ili dijeljenjem brojnika "
             "imeniocem.")
    hint2 = (f"Proširi razlomak na imenilac ${scale}$: "
             f"${fraction_display} = \\frac{{{expanded_numerator}}}{{{scale}}}$."
             if value > 0 else
             f"Radi s apsolutnom vrijednošću pa vrati predznak: proširi na "
             f"imenilac ${scale}$.")
    hint3 = (f"Brojnik proširenog razlomka čitaj kao decimale: imenilac "
             f"${scale}$ znači {core.decimal_places(value)} "
             f"decimalna mjesta.")
    solution = (f"Proširimo na dekadski imenilac: ${fraction_display} = "
                f"\\frac{{{expanded_numerator}}}{{{scale}}} = {decimal_display}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="fraction_decimal_conversion", operation="fraction_to_decimal",
        level=level, question=question, answer_value=value,
        answer_display=decimal_display, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("value", str(value))],
        required_conditions=["fraction_to_decimal"],
        relevant_objects=["rational"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _decimal_to_fraction_package(rng, level, scope, lesson_id, lesson_title):
    value = _conversion_value(rng, level, scope)
    decimal_display = core.decimal_display(value)
    reduced_display = core.plain_fraction_display(value)
    scale = 10 ** core.decimal_places(value)
    raw_numerator = (value * scale).numerator
    question = (f"Koji skraćeni razlomak je jednak decimalnom broju "
                f"${decimal_display}$?")
    swapped = (Fraction(value.denominator, abs(value.numerator))
               if value.numerator not in (0,) else value * 10)
    candidates = [value * 10, value / 10, -value, swapped,
                  value + Fraction(1, value.denominator)]
    option_values = [value]
    option_texts = [f"${reduced_display}$"]
    for candidate in candidates:
        if any(candidate == seen for seen in option_values):
            continue
        text = f"${core.plain_fraction_display(candidate)}$"
        if text in option_texts:
            continue
        option_values.append(candidate)
        option_texts.append(text)
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno razlomačkih opcija")
    hint1 = ("Decimalni broj se piše kao razlomak s dekadskim imeniocem: "
             "broj decimala određuje broj nula u imeniocu.")
    hint2 = (f"Zapiši: ${decimal_display} = "
             f"\\frac{{{raw_numerator}}}{{{scale}}}$, pa skrati.")
    hint3 = ("Skrati razlomak najvećim zajedničkim djeliocem brojnika i "
             "imenioca — rezultat mora biti nesvodiv.")
    solution = (f"Zapišemo preko dekadskog imenioca pa skratimo: "
                f"${decimal_display} = \\frac{{{raw_numerator}}}{{{scale}}} = "
                f"{reduced_display}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="fraction_decimal_conversion", operation="decimal_to_fraction",
        level=level, question=question, answer_value=value,
        answer_display=reduced_display, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("value", str(value))],
        required_conditions=["decimal_to_fraction"],
        relevant_objects=["rational"], generator_version=GENERATOR_VERSION,
        option_texts=tuple(option_texts), wrap="")


_DECIMAL_PLACES = (("desetinki", 1), ("stotinki", 2), ("hiljaditki", 3))


def _decimal_place_value_package(rng, level, scope, lesson_id, lesson_title):
    place_count = {1: 2, 2: 3, 3: 3}[level]
    # Uvijek bar 4 RAZLIČITE cifre u broju: tri pogrešne opcije su upravo
    # cifre s DRUGIH mjesta istog broja.
    whole_digits = 2
    digits = rng.sample(range(10), whole_digits + place_count)
    if digits[0] == 0:
        digits[0], digits[-1] = digits[-1], digits[0]
    whole = int("".join(map(str, digits[:whole_digits])))
    decimals = digits[whole_digits:]
    value_text = f"{whole},{''.join(map(str, decimals))}"
    place_name, position = _DECIMAL_PLACES[rng.randrange(min(place_count, 3))]
    correct_digit = decimals[position - 1]

    ask_whole = level >= 2 and rng.random() < 0.3
    if ask_whole:
        question = f"Koliki je cijeli dio decimalnog broja ${value_text}$?"
        answer_value, answer_display = whole, str(whole)
        wrong = [int("".join(map(str, decimals))), whole + 1,
                 max(whole - 1, 0), decimals[0]]
        option_texts = [f"${whole}$"]
        seen = {whole}
        for candidate in wrong:
            if candidate in seen:
                continue
            seen.add(candidate)
            option_texts.append(f"${candidate}$")
            if len(option_texts) == 4:
                break
        if len(option_texts) != 4:
            raise DeterministicGenerationError("nedovoljno opcija")
        operation = "whole_part"
        explain = (f"Cijeli dio je dio ispred decimalnog zareza: kod "
                   f"${value_text}$ to je ${whole}$.")
    else:
        question = (f"Koja cifra se nalazi na mjestu {place_name} u "
                    f"decimalnom broju ${value_text}$?")
        answer_value, answer_display = correct_digit, str(correct_digit)
        others = [d for d in digits if d != correct_digit]
        option_texts = [f"${correct_digit}$"] + [f"${d}$" for d in others[:3]]
        if len(option_texts) != 4 or len(set(option_texts)) != 4:
            raise DeterministicGenerationError("cifre nisu jedinstvene")
        operation = "decimal_place_value"
        explain = (f"Iza decimalnog zareza mjesta su redom desetinke, "
                   f"stotinke, hiljaditke — u broju ${value_text}$ na mjestu "
                   f"{place_name} stoji cifra ${correct_digit}$.")
    hint1 = ("Mjesta iza decimalnog zareza su redom: desetinke, stotinke, "
             "hiljaditke.")
    hint2 = f"Kreni od zareza udesno i broji mjesta u broju ${value_text}$."
    hint3 = "Cijeli dio je lijevo od zareza, decimalni dio desno od njega."
    solution = explain
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="fraction_decimal_conversion", operation=operation,
        level=level, question=question, answer_value=answer_value,
        answer_display=answer_display, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("value", value_text), ("place", place_name)],
        required_conditions=["decimal_place_value"],
        relevant_objects=["decimal"], generator_version=GENERATOR_VERSION,
        option_texts=tuple(option_texts), wrap="")
