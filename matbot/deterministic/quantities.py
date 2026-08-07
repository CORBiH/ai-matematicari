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
              "classical_probability_basic",
              # Batch #2: frekvencije malih skupova podataka i zaokruživanje.
              "frequency_basic", "decimal_rounding")
GENERATOR_VERSION = "detqty-1"

_PERCENT_CONCEPTS = frozenset({"percent_of_number", "fraction_to_percent",
                               # Batch #2: iznos/osnovica/stopa.
                               "percent_amount", "percent_rate"})
_PROBABILITY_CONCEPTS = frozenset({"classical_probability",
                                   "complement_probability",
                                   "outcome_counting"})
_FREQUENCY_CONCEPTS = frozenset({"frequency", "relative_frequency",
                                 "frequency_table"})
_ROUNDING_CONCEPTS = frozenset({"round_decimal", "round_then_estimate"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    if not concepts:
        return False
    return (concepts <= _PERCENT_CONCEPTS
            or concepts == {"mean"}
            or concepts <= _PROBABILITY_CONCEPTS
            or concepts <= _FREQUENCY_CONCEPTS
            or concepts <= _ROUNDING_CONCEPTS)


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
            concept = rng.choice(tuple(concepts))
            builder = {
                "classical_probability": _probability_package,
                "complement_probability": _complement_probability_package,
                "outcome_counting": _outcome_counting_package,
                "percent_of_number": _percent_of_number_package,
                "fraction_to_percent": _fraction_to_percent_package,
                "percent_amount": _percent_amount_package,
                "percent_rate": _percent_rate_package,
                "frequency": _frequency_package,
                "relative_frequency": _relative_frequency_package,
                "frequency_table": _frequency_table_package,
                "round_decimal": _round_decimal_package,
                "round_then_estimate": _round_estimate_package,
            }[concept]
            return builder(rng, level, lesson_id, lesson_title)
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


# ---------------------------------------------------------------------------
# KOMPLEMENTARAN DOGAĐAJ I BROJ ISHODA (Batch #2)
# ---------------------------------------------------------------------------

def _complement_probability_package(rng, level, lesson_id, lesson_title):
    den = rng.randint(4, 10 if level == 1 else 20)
    num = rng.randint(1, den - 1)
    probability = Fraction(num, den)
    complement = 1 - probability
    display = core.plain_fraction_display(probability)
    answer_display = core.plain_fraction_display(complement)
    question = (f"Vjerovatnoća nekog događaja iznosi ${display}$. Kolika je "
                "vjerovatnoća njemu suprotnog (komplementarnog) događaja?")
    hint1 = ("Događaj i njemu suprotan događaj zajedno pokrivaju sve ishode: "
             "zbir njihovih vjerovatnoća je $1$.")
    hint2 = f"Izračunaj: $1 - {display}$."
    hint3 = f"Svedi na zajednički imenilac: $\\frac{{{den}}}{{{den}}} - {display}$."
    solution = (f"{hint1} Računamo: $1 - {display} = "
                f"\\frac{{{den}}}{{{den}}} - {display} = {answer_display}$.")
    candidates = [probability, complement + Fraction(1, den),
                  complement - Fraction(1, den), Fraction(1, den),
                  Fraction(num, den + 1)]
    candidates = [value for value in candidates if 0 < value < 1]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="classical_probability_basic",
        operation="complement_probability", level=level, question=question,
        answer_value=complement, answer_display=answer_display,
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("probability", str(probability))],
        required_conditions=["complement"], relevant_objects=["rational"],
        generator_version=GENERATOR_VERSION,
        display_of=core.plain_fraction_display)


_EXPERIMENTS = (
    ("bacanje jedne igraće kocke", 6),
    ("bacanje novčića", 2),
    ("izvlačenje jedne karte iz špila od 32 karte", 32),
    ("bacanje dva novčića (redoslijed se razlikuje)", 4),
    ("bacanje dvije igraće kocke (redoslijed se razlikuje)", 36),
)


def _outcome_counting_package(rng, level, lesson_id, lesson_title):
    if level == 1:
        pool = _EXPERIMENTS[:2]
    elif level == 2:
        pool = _EXPERIMENTS[:4]
    else:
        pool = _EXPERIMENTS
    description, count = pool[rng.randrange(len(pool))]
    if level >= 2 and rng.random() < 0.4:
        total = rng.randint(5, 12)
        description = (f"izvlačenje jedne kuglice iz vreće u kojoj je "
                       f"{total} različitih kuglica")
        count = total
    question = (f"Ogled je {description}. Koliko elementarnih ishoda ima "
                "ovaj ogled?")
    wrong = [count + 1, count - 1, count * 2, count + 2, max(1, count // 2)]
    hint1 = ("Elementarni ishod je jedan pojedinačan mogući rezultat ogleda "
             "— prebroji SVE različite rezultate.")
    hint2 = "Nabroji (ili sistematski prebroji) sve mogućnosti, bez ponavljanja."
    hint3 = ("Kod dva bacanja broj ishoda je proizvod broja ishoda "
             "pojedinačnih bacanja.")
    solution = (f"Ogled ({description}) ima tačno ${count}$ elementarnih "
                "ishoda.")
    candidates = [Fraction(v) for v in wrong if v > 0]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="classical_probability_basic", operation="outcome_counting",
        level=level, question=question, answer_value=Fraction(count),
        answer_display=str(count), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("experiment", description[:40]),
                              ("count", str(count))],
        required_conditions=["outcome_counting"],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator
                                     if isinstance(value, Fraction) else value))


# ---------------------------------------------------------------------------
# PROCENTNI IZNOS / OSNOVICA / STOPA (Batch #2)
# ---------------------------------------------------------------------------

def _percent_amount_package(rng, level, lesson_id, lesson_title):
    percent = rng.choice(_PERCENTS[level])
    for _ in range(200):
        base = rng.choice((200, 250, 300, 400, 500, 600, 800, 1200))
        amount = Fraction(percent, 100) * base
        if amount.denominator == 1:
            break
    else:
        raise DeterministicGenerationError("nema cjelobrojnog iznosa")
    question = (f"Osnovica iznosi ${base}$, a procentna stopa je {percent} %. "
                "Koliki je procentni iznos?")
    chain = (f"\\frac{{{percent}}}{{100}} \\cdot {base} = {amount.numerator}")
    hint1 = "Procentni iznos je stopa (kao razlomak sa imeniocem 100) puta osnovica."
    hint2 = f"Zapiši stopu kao razlomak: $\\frac{{{percent}}}{{100}}$, pa pomnoži sa ${base}$."
    hint3 = f"Računaj: ${chain.split('=')[0].strip()}$."
    solution = f"Procentni iznos je ${chain}$."
    candidates = [Fraction(base) - amount, amount * 10, amount / 10,
                  Fraction(base + percent), amount + 10, amount - 10]
    candidates = [v for v in candidates if v > 0 and v.denominator == 1]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="percent_basic", operation="percent_amount", level=level,
        question=question, answer_value=amount,
        answer_display=str(amount.numerator), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("percent", str(percent)), ("base", str(base))],
        required_conditions=["percent"], relevant_objects=["rational"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator))


def _percent_rate_package(rng, level, lesson_id, lesson_title):
    percent = rng.choice(_PERCENTS[level])
    for _ in range(200):
        base = rng.choice((200, 250, 300, 400, 500, 600, 800))
        amount = Fraction(percent, 100) * base
        if amount.denominator == 1:
            break
    else:
        raise DeterministicGenerationError("nema cjelobrojnog iznosa")
    ask_base = level >= 2 and rng.random() < 0.5
    if ask_base:
        question = (f"Procentni iznos je ${amount.numerator}$, a stopa je "
                    f"{percent} %. Kolika je osnovica?")
        answer = Fraction(base)
        answer_display = str(base)
        chain = (f"{amount.numerator} : \\frac{{{percent}}}{{100}} = "
                 f"{amount.numerator} \\cdot \\frac{{100}}{{{percent}}} = {base}")
        hint2 = (f"Osnovica je iznos podijeljen stopom: "
                 f"${amount.numerator} : \\frac{{{percent}}}{{100}}$.")
        solution = f"Osnovica je ${chain}$."
        operation = "percent_base"
        option_texts = None
        wrap = "$"
        candidates = [amount, Fraction(base) * 2, Fraction(base) + 50,
                      Fraction(base) - 50, amount * 2]
        candidates = [v for v in candidates if v > 0 and v.denominator == 1]
    else:
        question = (f"Osnovica iznosi ${base}$, a procentni iznos je "
                    f"${amount.numerator}$. Kolika je procentna stopa?")
        answer = Fraction(percent)
        answer_display = f"{percent} %"
        chain = (f"\\frac{{{amount.numerator}}}{{{base}}} = "
                 f"\\frac{{{percent}}}{{100}}")
        hint2 = (f"Stopa je iznos kroz osnovicu, proširen na stotinke: "
                 f"$\\frac{{{amount.numerator}}}{{{base}}}$.")
        solution = (f"Vrijedi ${chain}$, pa je stopa {percent} %.")
        operation = "percent_rate"
        wrong = []
        for candidate in (percent + 5, percent - 5, percent * 2,
                          max(1, percent // 2), percent + 10):
            if candidate > 0 and candidate != percent and candidate not in wrong:
                wrong.append(candidate)
        option_texts = (f"{percent} %", *(f"{w} %" for w in wrong[:3]))
        wrap = ""
        candidates = ()
    hint1 = ("Iznos, osnovica i stopa su vezani: iznos = stopa · osnovica "
             "(stopa kao razlomak sa imeniocem 100).")
    hint3 = "Provjeri rezultat: stopa puta osnovica mora dati iznos."
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="percent_basic", operation=operation, level=level,
        question=question, answer_value=answer, answer_display=answer_display,
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("percent", str(percent)), ("base", str(base)),
                              ("form", operation)],
        required_conditions=["percent"], relevant_objects=["rational"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator),
        option_texts=option_texts, wrap=wrap)


# ---------------------------------------------------------------------------
# FREKVENCIJE MALOG SKUPA PODATAKA (Batch #2)
# ---------------------------------------------------------------------------

def _data_sample(rng, level):
    """Mali niz ocjena/vrijednosti s poznatim frekvencijama."""
    size = {1: 7, 2: 10, 3: 12}[level]
    values = [rng.randint(1, 5) for _ in range(size)]
    # Bar dvije različite vrijednosti, i bar jedna ponovljena.
    if len(set(values)) < 2:
        values[0] = 1 if values[0] != 1 else 2
    values_sorted = sorted(set(values))
    target = rng.choice([v for v in values_sorted
                         if values.count(v) >= (2 if level > 1 else 1)]
                        or values_sorted)
    return values, target


def _listing(values):
    return ", ".join(f"${v}$" for v in values)


def _frequency_package(rng, level, lesson_id, lesson_title):
    values, target = _data_sample(rng, level)
    frequency = values.count(target)
    question = (f"Učenici su na testu dobili ocjene: {_listing(values)}. "
                f"Kolika je frekvencija ocjene ${target}$?")
    hint1 = "Frekvencija vrijednosti je broj njenih pojavljivanja u podacima."
    hint2 = f"Prebroji koliko se puta ocjena ${target}$ pojavljuje u nizu."
    hint3 = "Broji pažljivo redom, podatak po podatak — ništa ne preskači."
    solution = (f"Ocjena ${target}$ se u nizu pojavljuje tačno "
                f"${frequency}$ puta, pa je njena frekvencija ${frequency}$.")
    candidates = [Fraction(v) for v in
                  (frequency + 1, frequency - 1, len(values),
                   len(values) - frequency, frequency + 2) if v > 0]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="frequency_basic", operation="frequency", level=level,
        question=question, answer_value=Fraction(frequency),
        answer_display=str(frequency), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("data", "+".join(map(str, values))),
                              ("target", str(target))],
        required_conditions=["frequency"], relevant_objects=["natural"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator
                                     if isinstance(value, Fraction) else value))


def _relative_frequency_package(rng, level, lesson_id, lesson_title):
    values, target = _data_sample(rng, level)
    frequency = values.count(target)
    total = len(values)
    relative = Fraction(frequency, total)
    answer_display = core.plain_fraction_display(relative)
    question = (f"U uzorku su zabilježene vrijednosti: {_listing(values)}. "
                f"Kolika je relativna frekvencija vrijednosti ${target}$?")
    hint1 = ("Relativna frekvencija je frekvencija podijeljena ukupnim "
             "brojem podataka.")
    hint2 = (f"Frekvencija vrijednosti ${target}$ je ${frequency}$, a "
             f"podataka je ukupno ${total}$.")
    hint3 = f"Zapiši razlomak $\\frac{{{frequency}}}{{{total}}}$ i skrati ga ako se može."
    reduced_note = ("" if relative == Fraction(frequency, total) and
                    relative.denominator == total else
                    f", skraćeno ${answer_display}$")
    solution = (f"Relativna frekvencija je "
                f"$\\frac{{{frequency}}}{{{total}}}$" + reduced_note + ".")
    candidates = [Fraction(total - frequency, total), Fraction(frequency + 1, total),
                  Fraction(max(frequency - 1, 1), total), Fraction(1, total),
                  Fraction(frequency, max(total - 1, 1))]
    candidates = [value for value in candidates if 0 < value <= 1]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="frequency_basic", operation="relative_frequency",
        level=level, question=question, answer_value=relative,
        answer_display=answer_display, distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("data", "+".join(map(str, values))),
                              ("target", str(target))],
        required_conditions=["relative_frequency"],
        relevant_objects=["rational"], generator_version=GENERATOR_VERSION,
        display_of=core.plain_fraction_display)


def _frequency_table_package(rng, level, lesson_id, lesson_title):
    values, target = _data_sample(rng, level)
    distinct = sorted(set(values))
    rows = "; ".join(f"vrijednost ${v}$ ima frekvenciju ${values.count(v)}$"
                     for v in distinct)
    total = len(values)
    question = (f"Tabela frekvencija glasi: {rows}. Koliko podataka ukupno "
                "sadrži ovaj uzorak?")
    hint1 = "Ukupan broj podataka je ZBIR svih frekvencija iz tabele."
    hint2 = ("Saberi frekvencije: $"
             + " + ".join(str(values.count(v)) for v in distinct) + "$.")
    hint3 = "Provjeri da nisi preskočio nijedan red tabele."
    chain = (" + ".join(str(values.count(v)) for v in distinct)
             + f" = {total}")
    solution = f"Zbir svih frekvencija je ${chain}$ — uzorak ima ${total}$ podataka."
    candidates = [Fraction(v) for v in
                  (total + 1, total - 1, len(distinct),
                   total - values.count(target), total + 2) if v > 0]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="frequency_basic", operation="frequency_table", level=level,
        question=question, answer_value=Fraction(total),
        answer_display=str(total), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("data", "+".join(map(str, values)))],
        required_conditions=["frequency_table"], relevant_objects=["natural"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator
                                     if isinstance(value, Fraction) else value))


# ---------------------------------------------------------------------------
# ZAOKRUŽIVANJE DECIMALNIH BROJEVA (Batch #2)
# ---------------------------------------------------------------------------
# Politika zaokruživanja je ŠKOLSKA (half-up): cifra 5 i veće zaokružuju
# naviše. Sve je egzaktan racionalan račun — nikad binarni float ni podrazumi-
# jevano bankarsko zaokruživanje. Približenja se pišu znakom \approx, nikad
# znakom jednakosti (lažna jednakost bi bila dokazano odbijena).

_PLACE_NAMES = {0: "cio broj", 1: "jednu decimalu (desetinke)",
                2: "dvije decimale (stotinke)", 3: "tri decimale (hiljaditke)"}


def _round_half_up(value: Fraction, places: int) -> Fraction:
    scale = Fraction(10) ** places
    scaled = value * scale
    whole = scaled.numerator // scaled.denominator
    remainder = scaled - whole
    if remainder >= Fraction(1, 2):
        whole += 1
    return Fraction(whole, 1) / scale


def _rounding_value(rng, level):
    places_pool = {1: (0, 1), 2: (1, 2), 3: (2, 3)}[
        1 if level == 1 else (2 if level == 2 else 3)]
    target_places = rng.choice(places_pool)
    digits = target_places + rng.randint(1, 2)
    scale = 10 ** digits
    numerator = rng.randint(2 * scale // 10, 99 * scale // 10)
    value = Fraction(numerator, scale)
    if core.decimal_places(value) <= target_places:
        raise DeterministicGenerationError("nema šta da se zaokruži")
    return value, target_places


def _round_decimal_package(rng, level, lesson_id, lesson_title):
    value, places = _rounding_value(rng, level)
    rounded = _round_half_up(value, places)
    truncated = Fraction((value * 10 ** places).numerator
                         // (value * 10 ** places).denominator,
                         10 ** places)
    display = core.decimal_display(value)
    answer_display = core.decimal_display(rounded)
    question = (f"Zaokruži broj ${display}$ na {_PLACE_NAMES[places]}.")
    hint1 = ("Pogledaj PRVU cifru iza traženog mjesta: 0–4 zaokružuje "
             "naniže, a 5–9 naviše.")
    hint2 = f"Podvuci traženo mjesto u broju ${display}$ i pogledaj cifru odmah iza njega."
    hint3 = ("Ako je cifra iza traženog mjesta 5 ili veća, uvećaj zadnju "
             "zadržanu cifru za jedan; ostatak se odbacuje.")
    # BEZ "$a \\approx b$" u JEDNOM segmentu: mathcheck approx toleranciju
    # priznaje samo uz iracionalan izraz (\\sqrt/\\pi), a čisto decimalno
    # "6,419 ≈ 6,4" bi dokazano pao kao numerička protivrječnost.
    solution = (f"Cifra iza traženog mjesta odlučuje smjer: broj ${display}$ "
                f"zaokružen na {_PLACE_NAMES[places]} iznosi ${answer_display}$.")
    candidates = [truncated, rounded + Fraction(1, 10 ** places),
                  rounded - Fraction(1, 10 ** places),
                  _round_half_up(value, max(places - 1, 0)),
                  rounded + Fraction(2, 10 ** places)]
    candidates = [v for v in candidates
                  if v > 0 and core.is_terminating_decimal(v)]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="decimal_rounding", operation="round_decimal", level=level,
        question=question, answer_value=rounded, answer_display=answer_display,
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("value", str(value)), ("places", str(places))],
        required_conditions=["rounding"], relevant_objects=["decimal"],
        generator_version=GENERATOR_VERSION,
        display_of=core.decimal_display)


def _round_estimate_package(rng, level, lesson_id, lesson_title):
    # Procjena: zaokruži oba sabirka na cio broj pa saberi — egzaktno
    # definisana procjena, pa je tačno jedna opcija ispravna procjena.
    first = Fraction(rng.randint(21, 89 if level < 3 else 890), 10)
    second = Fraction(rng.randint(21, 89 if level < 3 else 890), 10)
    rounded_first = _round_half_up(first, 0)
    rounded_second = _round_half_up(second, 0)
    estimate = rounded_first + rounded_second
    display_first = core.decimal_display(first)
    display_second = core.decimal_display(second)
    question = (f"Procijeni zbir ${display_first} + {display_second}$ tako "
                "što oba sabirka prvo zaokružiš na cio broj.")
    hint1 = "Procjena: zaokruži svaki sabirak na cio broj, pa saberi zaokružene vrijednosti."
    hint2 = (f"Sabirak ${display_first}$ zaokruži na "
             f"${rounded_first.numerator}$, a ${display_second}$ na "
             f"${rounded_second.numerator}$.")
    hint3 = f"Saberi zaokružene brojeve: ${rounded_first.numerator} + {rounded_second.numerator}$."
    solution = (f"Sabirak ${display_first}$ zaokružujemo na "
                f"${rounded_first.numerator}$, a ${display_second}$ na "
                f"${rounded_second.numerator}$; procjena zbira je "
                f"${rounded_first.numerator} + {rounded_second.numerator} = "
                f"{estimate.numerator}$.")
    exact = first + second
    candidates = [estimate + 1, estimate - 1, estimate + 2,
                  _round_half_up(exact, 0) + 3]
    candidates = [v for v in candidates if v > 0 and v != estimate]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="decimal_rounding", operation="round_then_estimate",
        level=level, question=question, answer_value=estimate,
        answer_display=str(estimate.numerator), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("first", str(first)), ("second", str(second))],
        required_conditions=["estimation"], relevant_objects=["decimal"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator))
