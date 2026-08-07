"""Deterministički generator porodice pretvaranja mjernih jedinica.

Jedna semantička porodica (`unit_conversion_direct`); parametar `dimensions`
bira fizičke dimenzije lekcije:

  • length — mm, cm, dm, m, km;
  • mass   — mg, g, kg, t;
  • time   — s, min, h;
  • area   — mm², cm², dm², m²   (faktor 100 po koraku!);
  • volume — mm³, cm³, dm³, m³   (faktor 1000 po koraku!);
  • speed  — m/s ↔ km/h (egzaktan faktor 3,6 = 18/5);
  • angle  — stepen, minuta, sekunda (faktor 60).

STRUKTURNA DIMENZIONALNOST: faktori pretvaranja su navedeni PO DIMENZIJI i
nikad se ne miješaju (nema pretvaranja mase u dužinu). Kvadratne i kubne
jedinice nose kvadrirane odnosno kubirane faktore — 1 m² = 10 000 cm², a ne
100 cm². Sav račun je egzaktan `fractions.Fraction`; decimalni prikaz samo
preko core.decimal_display.

Jedinice se pišu u PROZI opcije („$450$ cm“), nikad unutar `$...$` — slova u
matematičkom segmentu ionako isključuju numeričke parsere, a ovako i prikaz
ostaje školski.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("unit_conversion_direct",)
GENERATOR_VERSION = "detunit-1"

# (ime dimenzije, [(jedinica, faktor prema SLJEDEĆOJ manjoj jedinici)...])
# Lista ide od NAJVEĆE prema najmanjoj jedinici; faktor je koliko manjih
# jedinica čini jednu veću.
_DIMENSIONS = {
    "length": (("km", 1000), ("m", 10), ("dm", 10), ("cm", 10), ("mm", None)),
    "mass": (("t", 1000), ("kg", 1000), ("g", 1000), ("mg", None)),
    "time": (("h", 60), ("min", 60), ("s", None)),
    "area": (("m²", 100), ("dm²", 100), ("cm²", 100), ("mm²", None)),
    "volume": (("m³", 1000), ("dm³", 1000), ("cm³", 1000), ("mm³", None)),
    "angle": (("°", 60), ("'", 60), ("''", None)),
}
_ANGLE_NAMES = {"°": "stepeni", "'": "minuta", "''": "sekundi"}


def _unit_names(dimension):
    return [unit for unit, _factor in _DIMENSIONS[dimension]]


def _conversion_factor(dimension, source, target):
    """Fraction: koliko TARGET jedinica čini jednu SOURCE jedinicu."""
    units = _unit_names(dimension)
    i, j = units.index(source), units.index(target)
    if i == j:
        raise DeterministicGenerationError("iste jedinice")
    factor = Fraction(1)
    if i < j:
        for position in range(i, j):
            factor *= _DIMENSIONS[dimension][position][1]
        return factor
    for position in range(j, i):
        factor /= _DIMENSIONS[dimension][position][1]
    return factor


_SUPPORTED_DIMENSIONS = frozenset(_DIMENSIONS) | {"speed"}


def supports(parameters) -> bool:
    parameters = parameters or {}
    dimensions = set(parameters.get("dimensions") or ())
    return bool(dimensions) and dimensions <= _SUPPORTED_DIMENSIONS


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    for _ in range(60):
        try:
            dimension = rng.choice(tuple(parameters["dimensions"]))
            if dimension == "speed":
                return _speed_package(rng, level, lesson_id, lesson_title)
            if dimension == "angle":
                return _angle_package(rng, level, lesson_id, lesson_title)
            return _linear_package(rng, level, dimension, lesson_id,
                                   lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _amount_display(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return core.decimal_display(value)


def _option(value: Fraction, unit: str) -> str:
    return f"${_amount_display(value)}$ {unit}"


_DIMENSION_WORDS = {
    "length": "dužine", "mass": "mase", "time": "vremena",
    "area": "površine", "volume": "zapremine",
}


def _linear_package(rng, level, dimension, lesson_id, lesson_title):
    units = _unit_names(dimension)
    if level == 1:
        i = rng.randrange(len(units) - 1)
        j = i + 1                                  # susjedne jedinice, naniže
    elif level == 2:
        i = rng.randrange(len(units))
        j = i - 1 if i == len(units) - 1 else i + 1
        if rng.random() < 0.5 and i > 0:
            j = i - 1                              # i naviše (manja → veća)
    else:
        pairs = [(a, b) for a in range(len(units)) for b in range(len(units))
                 if abs(a - b) == 2]
        if not pairs:
            pairs = [(a, b) for a in range(len(units))
                     for b in range(len(units)) if a != b]
        i, j = pairs[rng.randrange(len(pairs))]
    source, target = units[i], units[j]
    factor = _conversion_factor(dimension, source, target)

    if factor >= 1:
        amount = Fraction(rng.randint(2, 9))
        if level >= 2 and dimension not in ("time", "angle") and rng.random() < 0.5:
            amount = Fraction(rng.randint(11, 99), 10)   # npr. 4,5 m
    else:
        # Manja → veća jedinica: uzmi sadržilac da rezultat bude "lijep".
        amount = Fraction(1, factor) * rng.randint(2, 9)
    result = amount * factor
    if not core.is_terminating_decimal(result):
        raise DeterministicGenerationError("rezultat nije konačan")

    question = (f"Koliko je ${_amount_display(amount)}$ {source} izraženo u "
                f"jedinici {target}?")
    factor_display = (_amount_display(factor) if factor >= 1
                      else core.plain_fraction_display(factor))
    chain = (f"{_amount_display(amount)} \\cdot {factor_display} "
             f"= {_amount_display(result)}"
             if factor >= 1 else
             f"{_amount_display(amount)} : {_amount_display(Fraction(1, factor))} "
             f"= {_amount_display(result)}")
    squared_note = ""
    if dimension == "area":
        squared_note = (" Kvadratne jedinice nose KVADRIRAN faktor — "
                        "1 m² = 100 dm² = 10 000 cm².")
    if dimension == "volume":
        squared_note = (" Kubne jedinice nose KUBIRAN faktor — "
                        "1 m³ = 1000 dm³.")
    hint1 = (f"Pri pretvaranju jedinica {_DIMENSION_WORDS[dimension]} veća "
             "jedinica se množi faktorom, a manja dijeli." + squared_note)
    hint2 = (f"Jedna jedinica {source} ima "
             f"${factor_display}$ jedinica {target}."
             if factor >= 1 else
             f"Jedna jedinica {target} ima "
             f"${_amount_display(Fraction(1, factor))}$ jedinica {source}.")
    hint3 = f"Računaj: ${chain.split('=')[0].strip()}$."
    solution = (f"Vrijedi: ${chain}$, pa je "
                f"${_amount_display(amount)}$ {source} jednako "
                f"${_amount_display(result)}$ {target}." + squared_note)
    wrong_factors = [factor * 10, factor / 10, factor * 100, Fraction(1, 1) / factor]
    option_texts = [_option(result, target)]
    seen = {result}
    for wrong_factor in wrong_factors:
        candidate = amount * wrong_factor
        if candidate in seen or not core.is_terminating_decimal(candidate):
            continue
        seen.add(candidate)
        option_texts.append(_option(candidate, target))
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno opcija")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="unit_conversion_direct", operation=f"convert_{dimension}",
        level=level, question=question, answer_value=result,
        answer_display=f"{_amount_display(result)} {target}",
        distractor_values=(), hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("amount", str(amount)),
                              ("units", f"{source}->{target}")],
        required_conditions=[f"convert_{dimension}"],
        relevant_objects=[dimension], generator_version=GENERATOR_VERSION,
        option_texts=tuple(option_texts), wrap="",
        accepted_answers=(f"{_amount_display(result)} {target}",))


def _speed_package(rng, level, lesson_id, lesson_title):
    to_kmh = rng.random() < 0.5 if level > 1 else True
    if to_kmh:
        amount = Fraction(rng.choice((5, 10, 15, 20, 25, 30) if level < 3
                                     else (4, 6, 8, 12, 14, 16)))
        result = amount * Fraction(18, 5)
        question = (f"Brzina iznosi ${_amount_display(amount)}$ m/s. Kolika "
                    "je ta brzina u km/h?")
        chain = f"{_amount_display(amount)} \\cdot 3,6 = {_amount_display(result)}"
        rule = ("Iz m/s u km/h množi se sa 3,6 (jer sat ima 3600 sekundi, a "
                "kilometar 1000 metara).")
        source_unit, target_unit = "m/s", "km/h"
    else:
        amount = Fraction(rng.choice((18, 36, 54, 72, 90, 108)))
        result = amount * Fraction(5, 18)
        question = (f"Brzina iznosi ${_amount_display(amount)}$ km/h. Kolika "
                    "je ta brzina u m/s?")
        chain = f"{_amount_display(amount)} : 3,6 = {_amount_display(result)}"
        rule = "Iz km/h u m/s dijeli se sa 3,6."
        source_unit, target_unit = "km/h", "m/s"
    if not core.is_terminating_decimal(result):
        raise DeterministicGenerationError("rezultat nije konačan")
    hint2 = f"Računaj: ${chain.split('=')[0].strip()}$."
    hint3 = ("Provjeri smjer: brzina u km/h je uvijek BROJČANO veća od iste "
             "brzine u m/s.")
    solution = (f"{rule} Računamo: ${chain}$, pa je brzina "
                f"${_amount_display(result)}$ {target_unit}.")
    wrong = [amount * Fraction(5, 18) if to_kmh else amount * Fraction(18, 5),
             amount * 10, amount, result * 10, result / 10]
    option_texts = [_option(result, target_unit)]
    seen = {result}
    for candidate in wrong:
        if candidate in seen or not core.is_terminating_decimal(candidate):
            continue
        seen.add(candidate)
        option_texts.append(_option(candidate, target_unit))
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno opcija")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="unit_conversion_direct", operation="convert_speed",
        level=level, question=question, answer_value=result,
        answer_display=f"{_amount_display(result)} {target_unit}",
        distractor_values=(), hints=(rule, hint2, hint3), solution=solution,
        signature_parameters=[("amount", str(amount)),
                              ("direction", f"{source_unit}->{target_unit}")],
        required_conditions=["convert_speed"], relevant_objects=["speed"],
        generator_version=GENERATOR_VERSION, option_texts=tuple(option_texts),
        wrap="", accepted_answers=(f"{_amount_display(result)} {target_unit}",))


def _angle_package(rng, level, lesson_id, lesson_title):
    if level == 1:
        degrees = rng.randint(1, 9)
        minutes = 0
        total = degrees * 60
        question = (f"Koliko minuta ima ugao od ${degrees}^{{\\circ}}$?")
        chain = f"{degrees} \\cdot 60 = {total}"
        target_word = "minuta"
    elif level == 2:
        degrees = rng.randint(1, 9)
        minutes = rng.choice((10, 15, 20, 30, 40, 45, 50))
        total = degrees * 60 + minutes
        question = (f"Koliko minuta ima ugao od ${degrees}^{{\\circ}} "
                    f"{minutes}'$?")
        chain = f"{degrees} \\cdot 60 + {minutes} = {total}"
        target_word = "minuta"
    else:
        minutes = rng.randint(2, 9)
        seconds = rng.choice((10, 15, 20, 30, 40, 45))
        total = minutes * 60 + seconds
        question = (f"Koliko sekundi ima ugao od ${minutes}' {seconds}''$?")
        chain = f"{minutes} \\cdot 60 + {seconds} = {total}"
        target_word = "sekundi"
    hint1 = ("Ugaone jedinice idu po 60: jedan stepen ima 60 minuta, a "
             "jedna minuta 60 sekundi.")
    hint2 = f"Računaj: ${chain.split('=')[0].strip()}$."
    hint3 = "Prvo pretvori veću jedinicu, pa dodaj ostatak."
    solution = f"Računamo: ${chain}$ — ugao ima ${total}$ {target_word}."
    wrong = [total + 60, total - 60, total + 10,
             (total - minutes) if level > 1 else total // 2, total * 60]
    option_texts = [f"${total}$"]
    seen = {total}
    for candidate in wrong:
        if candidate <= 0 or candidate in seen:
            continue
        seen.add(candidate)
        option_texts.append(f"${candidate}$")
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno opcija")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="unit_conversion_direct", operation="convert_angle",
        level=level, question=question, answer_value=total,
        answer_display=str(total), distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("total", str(total)), ("level", str(level))],
        required_conditions=["convert_angle"], relevant_objects=["angle"],
        generator_version=GENERATOR_VERSION, option_texts=tuple(option_texts),
        wrap="")
