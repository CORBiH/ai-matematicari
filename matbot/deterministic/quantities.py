"""Deterministički generator porodica postotka, aritmetičke sredine i
klasične vjerovatnoće.

Tri semantičke porodice, jedan modul — sve tri su „količinske“ primjene
egzaktnog racionalnog računa:

  • percent_basic — procenat broja, postotni zapis razlomka i (nivo 3)
    traženje cjeline iz poznatog procenta;
  • arithmetic_mean_direct — aritmetička sredina 3–5 brojeva s egzaktnim
    rezultatom;
  • classical_probability_basic — klasična vjerovatnoća: povoljni / svi
    ishodi, s komplementom na nivou 3.

ZAPIS POSTOTKA: znak `%` NIKAD ne ide unutar `$...$` — u LaTeX-u je `%`
komentar i MathJax bi ostatak reda progutao, a `\\%` nije u projektnoj
allowlisti komandi. Postotak zato uvijek stoji u prozi („20 %“), a matematika
u `$...$` nosi razlomke stotina.

Vjerovatnoća se NAMJERNO piše riječju („vjerovatnoća je $\\frac{3}{10}$“),
nikad simbolom `P` — `P` je u projektu rezervisan za površinu
(matbot/geometry_rules.py) i geometrijski verifikator ga nadzire.
"""
import random
from fractions import Fraction
from math import gcd

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("percent_basic", "arithmetic_mean_direct",
              "classical_probability_basic")
GENERATOR_VERSION = "detqty-1"

_PERCENT_CONCEPTS = frozenset({"percent_of_number", "fraction_to_percent"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    if not concepts:
        return False
    return (concepts <= _PERCENT_CONCEPTS
            or concepts == {"mean"}
            or concepts == {"classical_probability"})


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    for _ in range(60):
        try:
            concepts = set(parameters["concepts"])
            if concepts == {"mean"}:
                return _mean_package(rng, level, lesson_id, lesson_title)
            if concepts == {"classical_probability"}:
                return _probability_package(rng, level, lesson_id, lesson_title)
            concept = rng.choice(tuple(concepts))
            if concept == "percent_of_number":
                return _percent_of_number_package(rng, level, lesson_id,
                                                  lesson_title)
            return _fraction_to_percent_package(rng, level, lesson_id,
                                                lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


# ---------------------------------------------------------------------------
# PROCENAT BROJA — rezultat je uvijek egzaktan (cio ili konačan decimalan)
# ---------------------------------------------------------------------------

_PERCENTS = {1: (10, 20, 25, 50), 2: (5, 15, 30, 40, 60, 75), 3: (4, 8, 12, 35, 45, 65, 85)}


def _percent_of_number_package(rng, level, lesson_id, lesson_title):
    percent = rng.choice(_PERCENTS[level])
    ratio = Fraction(percent, 100)
    for _ in range(200):
        base = rng.choice((20, 40, 50, 60, 80, 120, 150, 200, 240, 250, 300,
                           360, 400, 480, 500, 600, 800))
        answer = ratio * base
        if answer.denominator == 1 and answer != base:
            break
    else:
        raise DeterministicGenerationError("nema cjelobrojnog rezultata")

    inverse = level == 3 and rng.random() < 0.5
    reduced = Fraction(percent, 100)
    if inverse:
        question = (f"{percent} % nekog broja iznosi ${answer.numerator}$. "
                    "Koji je to broj?")
        correct = Fraction(base)
        chain = (f"\\frac{{{percent}}}{{100}} \\cdot {base} = {answer.numerator}")
        hint2 = (f"Ako je {percent} % vrijednosti jednako ${answer.numerator}$, "
                 f"onda je cijela vrijednost ${answer.numerator} : "
                 f"{core.plain_fraction_display(reduced)}$.")
        solution = (f"Traženi broj je ${base}$, jer je "
                    f"${chain}$ — {percent} % od ${base}$ zaista iznosi "
                    f"${answer.numerator}$.")
        candidates = [answer, Fraction(base) * 2, Fraction(base) // 2 if base % 2 == 0
                      else Fraction(base) + 10, Fraction(base) + 10,
                      Fraction(base) - 10, answer * 2]
    else:
        question = f"Izračunaj {percent} % od broja ${base}$."
        correct = answer
        chain = (f"\\frac{{{percent}}}{{100}} \\cdot {base} "
                 f"= {core.plain_fraction_display(reduced)} \\cdot {base} "
                 f"= {answer.numerator}")
        hint2 = (f"Zapiši procenat kao razlomak: {percent} % je "
                 f"$\\frac{{{percent}}}{{100}}$, pa pomnoži s ${base}$.")
        solution = (f"{percent} % znači $\\frac{{{percent}}}{{100}}$. "
                    f"Računamo: ${chain}$. Rezultat je ${answer.numerator}$.")
        candidates = [Fraction(base) - answer, answer * 10, answer / 10,
                      Fraction(base + percent), answer + 10, answer - 10,
                      answer + 5]
    hint1 = ("Procenat znači stoti dio: p % od broja je p stotinki tog broja.")
    hint3 = ("Provjeri rezultat: vrati se na procenat množenjem i uporedi s "
             "podacima iz zadatka.")
    candidates = [value for value in candidates
                  if value > 0 and value.denominator == 1]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="percent_basic",
        operation="percent_whole" if inverse else "percent_of_number",
        level=level, question=question, answer_value=correct,
        answer_display=str(correct.numerator), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("percent", str(percent)), ("base", str(base)),
                              ("inverse", str(inverse))],
        required_conditions=["percent"], relevant_objects=["rational"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator))


_PERCENT_FRACTIONS = {
    1: ((1, 2), (1, 4), (3, 4), (1, 10), (3, 10), (7, 10), (1, 5)),
    2: ((2, 5), (3, 5), (4, 5), (1, 20), (3, 20), (7, 20), (9, 10)),
    3: ((1, 8), (3, 8), (5, 8), (7, 8), (11, 20), (13, 20), (1, 40)),
}


def _percent_text(value: Fraction) -> str:
    """„25 %“ ili „37,5 %“ — čista proza, bez $...$."""
    percent = value * 100
    if percent.denominator == 1:
        return f"{percent.numerator} %"
    return f"{core.decimal_display(percent)} %"


def _fraction_to_percent_package(rng, level, lesson_id, lesson_title):
    p, q = rng.choice(_PERCENT_FRACTIONS[level])
    value = Fraction(p, q)
    display = f"\\frac{{{p}}}{{{q}}}"
    question = f"Koliko procenata iznosi razlomak ${display}$?"
    answer_text = _percent_text(value)
    hundreds = value * 100

    wrong = {Fraction(p, 100), Fraction(q, 100), value / 100,
             hundreds / 100 / 100}
    wrong.update({hundreds / 100 + Fraction(k, 10) for k in (1, -1)})
    wrong.update({Fraction(p * 10, 100), Fraction(min(99, p * q), 100)})
    option_values = [value]
    option_texts = [answer_text]
    for candidate in sorted(wrong):
        if candidate <= 0 or candidate in option_values:
            continue
        if not core.is_terminating_decimal(candidate * 100):
            continue
        text = _percent_text(candidate)
        if text in option_texts:
            continue
        option_values.append(candidate)
        option_texts.append(text)
        if len(option_texts) == 4:
            break
    if len(option_texts) < 4:
        raise DeterministicGenerationError("nedovoljno postotnih opcija")

    scale = 100 // q if 100 % q == 0 else None
    if scale:
        chain = (f"{display} = \\frac{{{p * scale}}}{{100}}")
        explain = (f"Proširimo razlomak na stotinke: ${chain}$, "
                   f"a $\\frac{{{p * scale}}}{{100}}$ iznosi {answer_text}.")
        hint2 = (f"Proširi razlomak tako da imenilac bude $100$: "
                 f"pomnoži brojnik i imenilac sa ${scale}$.")
    else:
        decimal = core.decimal_display(value)
        chain = f"{display} = {decimal}"
        explain = (f"Pretvorimo u decimalni zapis: ${chain}$, "
                   f"a to je {answer_text}.")
        hint2 = ("Podijeli brojnik imeniocem pa decimalni zapis pretvori u "
                 "procenat množenjem sa $100$.")
    hint1 = "Procenat je razlomak sa imeniocem $100$: prevedi razlomak na stotinke."
    hint3 = "Broj stotinki je broj procenata — pročitaj brojnik razlomka sa imeniocem $100$."
    solution = explain
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="percent_basic", operation="fraction_to_percent",
        level=level, question=question, answer_value=value,
        answer_display=answer_text, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("fraction", f"{p}/{q}")],
        required_conditions=["percent"], relevant_objects=["rational"],
        generator_version=GENERATOR_VERSION, option_texts=tuple(option_texts),
        wrap="")


# ---------------------------------------------------------------------------
# ARITMETIČKA SREDINA
# ---------------------------------------------------------------------------

def _mean_package(rng, level, lesson_id, lesson_title):
    count = {1: 3, 2: 4, 3: 5}[level]
    for _ in range(300):
        values = [rng.randint(2, 40 if level == 1 else 90)
                  for _ in range(count)]
        total = sum(values)
        mean = Fraction(total, count)
        if len(set(values)) < count:
            continue
        if level < 3 and mean.denominator != 1:
            continue
        if level == 3 and not core.is_terminating_decimal(mean):
            continue
        break
    else:
        raise DeterministicGenerationError("nema pogodnog skupa")
    listing = ", ".join(f"${v}$" for v in values[:-1]) + f" i ${values[-1]}$"
    question = f"Izračunaj aritmetičku sredinu brojeva {listing}."
    total_display = " + ".join(str(v) for v in values)
    mean_text = (str(mean.numerator) if mean.denominator == 1
                 else core.decimal_display(mean))
    chain = f"({total_display}) : {count} = {total} : {count} = {mean_text}"
    hint1 = ("Aritmetička sredina je zbir svih brojeva podijeljen brojem "
             "podataka.")
    hint2 = f"Prvo saberi sve brojeve: ${total_display} = {total}$."
    hint3 = f"Sada podijeli zbir brojem podataka: ${total} : {count}$."
    solution = f"{hint1} Računamo: ${chain}$. Rezultat je ${mean_text}$."
    candidates = [Fraction(total), Fraction(max(values)), Fraction(min(values)),
                  mean + 1, mean - 1, Fraction(total, max(count - 1, 1)),
                  mean + Fraction(1, 2)]
    candidates = [value for value in candidates
                  if value > 0 and core.is_terminating_decimal(value)]
    display_of = (lambda value: str(value.numerator) if value.denominator == 1
                  else core.decimal_display(value))
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="arithmetic_mean_direct", operation="mean", level=level,
        question=question, answer_value=mean, answer_display=mean_text,
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("values", "+".join(str(v) for v in values))],
        required_conditions=["mean"], relevant_objects=["rational"],
        generator_version=GENERATOR_VERSION, display_of=display_of)


# ---------------------------------------------------------------------------
# KLASIČNA VJEROVATNOĆA
# ---------------------------------------------------------------------------

_COLOURS = (
    ("crvena", "crvene", "crvenih", "crvena"),
    ("plava", "plave", "plavih", "plava"),
    ("zelena", "zelene", "zelenih", "zelena"),
    ("žuta", "žute", "žutih", "žuta"),
)


def _colour_count(count, colour):
    """„3 crvene kuglice“ / „7 plavih kuglica“ — brojna kongruencija."""
    _nom, paucal, plural, _adj = colour
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} {paucal} kuglice"
    return f"{count} {plural} kuglica"


def _probability_package(rng, level, lesson_id, lesson_title):
    colour_count = 2 if level == 1 else 3
    colours = rng.sample(_COLOURS, colour_count)
    counts = [rng.randint(2, 9) for _ in colours]
    total = sum(counts)
    target_index = rng.randrange(colour_count)
    target = colours[target_index]
    favourable = counts[target_index]

    complement = level == 3 and rng.random() < 0.5
    if complement:
        favourable_count = total - counts[target_index]
        event = f"da izvučena kuglica NE bude {target[0]}"
    else:
        favourable_count = favourable
        event = f"da izvučena kuglica bude {target[0]}"
    probability = Fraction(favourable_count, total)

    contents = " i ".join(_colour_count(count, colour)
                          for count, colour in zip(counts, colours)) \
        if colour_count == 2 else \
        (", ".join(_colour_count(count, colour)
                   for count, colour in zip(counts[:-1], colours[:-1]))
         + f" i {_colour_count(counts[-1], colours[-1])}")
    question = (f"U vreći se nalazi {contents}. Kolika je vjerovatnoća "
                f"{event}?")
    answer_display = core.plain_fraction_display(probability)
    totals = " + ".join(str(c) for c in counts)
    reduced_note = (f", skraćeno ${answer_display}$."
                    if gcd(favourable_count, total) > 1 else ".")
    chain = f"{totals} = {total}"
    if complement:
        explain = (f"Svih ishoda je ${chain}$, a povoljnih je "
                   f"${total} - {counts[target_index]} = {favourable_count}$ "
                   f"(sve kuglice koje nisu {target[1]} boje). Vjerovatnoća je "
                   f"$\\frac{{{favourable_count}}}{{{total}}}$" + reduced_note)
    else:
        explain = (f"Svih ishoda je ${chain}$, a povoljnih je "
                   f"${favourable_count}$. Vjerovatnoća je "
                   f"$\\frac{{{favourable_count}}}{{{total}}}$" + reduced_note)
    hint1 = ("Klasična vjerovatnoća je broj povoljnih ishoda podijeljen "
             "brojem svih mogućih ishoda.")
    hint2 = f"Prvo prebroji SVE kuglice u vreći: ${chain}$."
    hint3 = ("Sada prebroji povoljne ishode za traženi događaj i zapiši "
             "razlomak povoljni kroz svi.")
    solution = explain
    candidates = [Fraction(counts[target_index], total),
                  Fraction(total - favourable_count, total),
                  Fraction(favourable_count, max(total - favourable_count, 1)),
                  Fraction(1, total), Fraction(favourable_count + 1, total),
                  Fraction(max(favourable_count - 1, 1), total)]
    candidates = [value for value in candidates if 0 < value <= 1]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="classical_probability_basic",
        operation="classical_probability", level=level, question=question,
        answer_value=probability, answer_display=answer_display,
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("counts", "+".join(str(c) for c in counts)),
                              ("target", target[0]),
                              ("complement", str(complement))],
        required_conditions=["classical_probability"],
        relevant_objects=["rational"], generator_version=GENERATOR_VERSION,
        display_of=core.plain_fraction_display)
