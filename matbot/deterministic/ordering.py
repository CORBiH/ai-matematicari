"""Deterministički generator porodica poređenja i suprotnog/apsolutnog broja.

Dvije semantičke porodice, jedan modul — obje rade nad uređenjem brojevne
prave i dijele prikaz vrijednosti po domenu:

  • number_comparison_order — najveći/najmanji od četiri vrijednosti i
    „koji broj je između“; superlativni oblik nezavisno dokazuje postojeći
    orakl poređenja (mcq_integrity.evaluate_comparison_mcq);
  • absolute_value_opposite — apsolutna vrijednost i suprotan broj, uz
    višekorakčne izraze na višim nivoima (nivo = broj operacija).

MATEMATIČKI AUTORITET: egzaktni `fractions.Fraction` za sve domene; decimalni
prikaz isključivo preko core.decimal_display. SVE četiri opcije uvijek nose
MEĐUSOBNO RAZLIČITE vrijednosti — dvije vrijednosno jednake opcije (npr.
$0,5$ i $\\frac{1}{2}$) objava dokazano odbija kao semantičke duplikate, pa
ih generator ne smije ni proizvesti.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.tutor.schema import DifficultyEvidence

FAMILY_IDS = ("number_comparison_order", "absolute_value_opposite")
GENERATOR_VERSION = "detord-1"

_COMPARISON_DOMAINS = frozenset({"natural", "fraction", "decimal", "integer",
                                 "rational"})
_ABS_DOMAINS = frozenset({"integer", "rational"})
_ABS_CONCEPTS = frozenset({"absolute_value", "opposite"})


# Parametri koji pripadaju DRUGIM porodicama: njihovo prisustvo znači da je
# ugovor stigao pogrešnom modulu — fail closed, nikad „pa proradiće nekako“.
_FOREIGN_KEYS = frozenset({"allowed_operations", "expression_shape",
                           "sign_scope", "shapes", "divisors",
                           "denominator_relation"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    if _FOREIGN_KEYS & set(parameters):
        return False
    domain = parameters.get("number_domain")
    concepts = parameters.get("concepts")
    if concepts:
        return (set(concepts) <= _ABS_CONCEPTS and bool(concepts)
                and domain in _ABS_DOMAINS)
    return domain in _COMPARISON_DOMAINS


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    for _ in range(60):
        try:
            if parameters.get("concepts"):
                concept = rng.choice(tuple(parameters["concepts"]))
                return _abs_package(rng, level, concept,
                                    parameters["number_domain"],
                                    lesson_id, lesson_title)
            return _comparison_package(rng, level, parameters["number_domain"],
                                       lesson_id, lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


# ---------------------------------------------------------------------------
# POREĐENJE — četiri različite vrijednosti, jedinstven ekstrem
# ---------------------------------------------------------------------------

def _comparison_values(rng, domain, level):
    """Četiri MEĐUSOBNO RAZLIČITE vrijednosti domena, poredive ali bliske."""
    values = set()
    for _ in range(400):
        if domain == "natural":
            spread = {1: (10, 99), 2: (100, 999), 3: (1000, 9999)}[level]
            candidate = Fraction(rng.randint(*spread))
        elif domain == "integer":
            magnitude = {1: 12, 2: 30, 3: 60}[level]
            candidate = Fraction(rng.randint(-magnitude, magnitude))
        elif domain == "decimal":
            places = 1 if level == 1 else rng.choice((1, 2))
            candidate = Fraction(rng.randint(1, 99 if level < 3 else 999),
                                 10 ** places)
        elif domain == "fraction":
            if level == 1:
                den = rng.randint(5, 12)
                candidate = Fraction(rng.randint(1, den - 1), den)
            else:
                candidate = Fraction(rng.randint(1, 11), rng.randint(2, 12))
        else:  # rational — predznačeni razlomci i decimale zajedno
            if rng.random() < 0.5:
                candidate = Fraction(rng.randint(1, 9), rng.randint(2, 9))
            else:
                candidate = Fraction(rng.randint(1, 99), 10 ** rng.choice((1, 2)))
            if rng.random() < 0.5:
                candidate = -candidate
        values.add(candidate)
        if len(values) == 4:
            break
    if len(values) < 4:
        raise DeterministicGenerationError("nema četiri različite vrijednosti")
    ordered = sorted(values)
    if level == 1 and domain == "fraction":
        # Nivo 1: jednaki imenioci — različiti brojnici su već različite vrijednosti.
        pass
    return ordered


def _comparison_display(domain, value: Fraction) -> str:
    if domain == "decimal":
        return core.decimal_display(value)
    if domain in ("natural", "integer"):
        return str(value.numerator)
    if domain == "rational" and core.is_terminating_decimal(value) \
            and value.denominator in (10, 100):
        return core.decimal_display(value)
    return core.plain_fraction_display(value)


def _rounding_confusable(displays):
    """True kad je razlomačka opcija unutar tolerancije ZAOKRUŽENJA decimalne.

    Živi nalaz fuzz kampanje (lekcija racionalnog domena, 6000 paketa):
    option_equivalence dokazano odbija par poput $\\frac{1}{3}$ i $0,3$ kao
    `numeric_exact_vs_rounded` duplikate — egzaktna vrijednost i njeno
    zaokruženje su ISTI odgovor u dva zapisa. Generator takav par ne smije ni
    ponuditi, pa se ovdje primjenjuje ista tolerancija (pola posljednjeg
    decimalnog mjesta, s istom rezervom kao mathcheck)."""
    pairs = list(displays.items())
    for index, (value, display) in enumerate(pairs):
        for other_value, other_display in pairs[index + 1:]:
            fraction_first = "\\frac" in display
            fraction_second = "\\frac" in other_display
            if fraction_first == fraction_second:
                continue
            decimal_display = other_display if fraction_first else display
            digits = decimal_display.split(",")[1] if "," in decimal_display else ""
            tolerance = Fraction(11, 20) * Fraction(1, 10 ** len(digits)) \
                if digits else Fraction(11, 20)
            if abs(value - other_value) <= tolerance:
                return True
    return False


def _comparison_package(rng, level, domain, lesson_id, lesson_title):
    ordered = _comparison_values(rng, domain, level)
    between = level == 3 and domain in ("natural", "integer", "decimal") \
        and rng.random() < 0.5
    displays = {value: _comparison_display(domain, value) for value in ordered}
    if len(set(displays.values())) < 4:
        raise DeterministicGenerationError("prikazi nisu jedinstveni")
    if domain == "rational" and _rounding_confusable(displays):
        raise DeterministicGenerationError("razlomak i decimalno zaokruženje preblizu")

    if between:
        low, correct, high, outsider = ordered
        question = (f"Koji od ponuđenih brojeva se nalazi između "
                    f"${displays[low]}$ i ${displays[high]}$?")
        option_values = [correct, low, high, outsider]
        explain = (f"Vrijedi ${displays[low]} < {displays[correct]} < "
                   f"{displays[high]}$, pa je između njih broj "
                   f"${displays[correct]}$.")
        operation = "between"
    else:
        wants_max = rng.random() < 0.5
        correct = ordered[-1] if wants_max else ordered[0]
        others = [value for value in ordered if value != correct]
        question = ("Koji je od ponuđenih brojeva najveći?" if wants_max
                    else "Koji je od ponuđenih brojeva najmanji?")
        option_values = [correct, *others]
        chain = " < ".join(displays[value] for value in ordered)
        explain = (f"Poredak od najmanjeg prema najvećem: ${chain}$ — "
                   f"{'najveći' if wants_max else 'najmanji'} je "
                   f"${displays[correct]}$.")
        operation = "max" if wants_max else "min"

    option_texts = tuple(f"${displays[value]}$" for value in option_values)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    hint1 = _comparison_rule(domain)
    hint2 = ("Svedi sve brojeve na isti oblik (isti imenilac ili decimalni "
             "zapis), pa ih poredaj." if domain in ("fraction", "rational")
             else "Poredaj brojeve po veličini — na brojevnoj pravoj veći je desno.")
    hint3 = "Poredaj sve četiri vrijednosti od najmanje prema najvećoj, pa izaberi traženu."
    solution = explain
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="number_comparison_order", operation=operation, level=level,
        question=question, answer_value=correct,
        answer_display=displays[correct], distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("values", "+".join(str(v) for v in ordered)),
                              ("form", operation)],
        required_conditions=["ordering"], relevant_objects=[domain],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", comparison_evidence=True)


def _comparison_rule(domain):
    if domain == "fraction":
        return ("Razlomci se porede svođenjem na zajednički imenilac ili "
                "pretvaranjem u decimalni zapis.")
    if domain == "decimal":
        return ("Decimalni brojevi se porede cifra po cifra: prvo cijeli dio, "
                "zatim desetinke, stotinke...")
    if domain == "integer":
        return ("Na brojevnoj pravoj veći broj je desno: svaki pozitivan broj "
                "veći je od svakog negativnog, a od dva negativna veći je onaj "
                "bliži nuli.")
    if domain == "rational":
        return ("Racionalni brojevi se porede kao razlomci ili decimalno; "
                "pazi na znak — pozitivan je uvijek veći od negativnog.")
    return "Prirodni brojevi se porede po broju cifara, pa cifra po cifra slijeva."


# ---------------------------------------------------------------------------
# APSOLUTNA VRIJEDNOST I SUPROTAN BROJ — nivo = broj operacija
# ---------------------------------------------------------------------------

def _abs_operand(rng, domain, level):
    if domain == "integer":
        magnitude = {1: 12, 2: 30, 3: 30}[level]
        return Fraction(rng.randint(2, magnitude))
    den = rng.randint(2, 9)
    num = rng.randint(1, den - 1 if level == 1 else den + 3)
    return Fraction(num, den)


def _abs_display(domain, value: Fraction) -> str:
    if domain == "integer":
        return str(value.numerator)
    return core.plain_fraction_display(value)


def _abs_package(rng, level, concept, domain, lesson_id, lesson_title):
    if concept == "opposite":
        return _opposite_package(rng, level, domain, lesson_id, lesson_title)
    magnitudes = [_abs_operand(rng, domain, level) for _ in range(level)]
    signs = [rng.random() < 0.7 for _ in range(level)]  # True = negativan operand
    terms = [-m if negative else m for m, negative in zip(magnitudes, signs)]
    plus_minus = [rng.random() < 0.75 or index == 0
                  for index in range(level)]  # True = sabiranje člana

    display_terms = []
    value = Fraction(0)
    for index, term in enumerate(terms):
        inner = _abs_display(domain, term)
        piece = f"|{inner}|"
        if index == 0:
            display_terms.append(piece)
            value += abs(term)
        elif plus_minus[index]:
            display_terms.append(f" + {piece}")
            value += abs(term)
        else:
            display_terms.append(f" - {piece}")
            value -= abs(term)
    expression = "".join(display_terms)

    if level == 1:
        question = (f"Kolika je apsolutna vrijednost broja "
                    f"${_abs_display(domain, terms[0])}$?")
    else:
        question = f"Izračunaj: ${expression}$"
    answer = value
    answer_text = core.fraction_display(answer) if domain != "integer" \
        else str(answer.numerator)

    candidates = [-answer, answer + 1, answer - 1, answer + 2, answer - 2]
    if level == 1:
        candidates.insert(0, terms[0])            # „apsolutna vrijednost čuva znak“
    if domain == "rational":
        step = Fraction(1, terms[0].denominator)
        candidates.extend([answer + step, answer - step])
    hint1 = ("Apsolutna vrijednost broja je njegova udaljenost od nule — "
             "nikad nije negativna.")
    absolutes = ", ".join(f"$|{_abs_display(domain, term)}| = "
                          f"{_abs_display(domain, abs(term))}$"
                          for term in terms)
    hint2 = f"Prvo skini apsolutne vrijednosti: {absolutes}."
    if level == 1:
        hint3 = ("Udaljenost od nule je uvijek pozitivna — samo makni predznak "
                 "i uporedi s opcijama.")
        solution = (f"Apsolutna vrijednost je udaljenost od nule: "
                    f"$|{_abs_display(domain, terms[0])}| = {answer_text}$.")
    else:
        simplified = " ".join(
            (_abs_display(domain, abs(term)) if index == 0 else
             f"+ {_abs_display(domain, abs(term))}" if plus_minus[index] else
             f"- {_abs_display(domain, abs(term))}")
            for index, term in enumerate(terms))
        hint3 = f"Sada izračunaj: ${simplified}$ — još samo saberi i oduzmi redom."
        solution = (f"Skinemo apsolutne vrijednosti pa računamo: "
                    f"${expression} = {simplified} = {answer_text}$.")
    evidence = _abs_evidence(level)
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="absolute_value_opposite", operation="absolute_value",
        level=level, question=question, answer_value=answer,
        answer_display=answer_text, distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("terms", "+".join(str(t) for t in terms))],
        required_conditions=["absolute_value"], relevant_objects=[domain],
        generator_version=GENERATOR_VERSION,
        display_of=(core.fraction_display if domain != "integer"
                    else lambda v: str(v.numerator if isinstance(v, Fraction) else v)),
        evidence=evidence)


def _opposite_package(rng, level, domain, lesson_id, lesson_title):
    magnitude = _abs_operand(rng, domain, level)
    base = -magnitude if rng.random() < 0.6 else magnitude
    display = _abs_display(domain, base)
    if level == 1:
        question = f"Koji broj je suprotan broju ${display}$?"
        answer = -base
        chain = f"-({display}) = {_abs_display(domain, answer)}"
        steps_note = "Suprotan broj ima istu apsolutnu vrijednost, a suprotan predznak."
    elif level == 2:
        question = f"Izračunaj: $-(-({display}))$"
        answer = base
        chain = (f"-(-({display})) = {_abs_display(domain, answer)}")
        steps_note = ("Dvostruka promjena predznaka vraća polazni broj: "
                      "suprotan broj suprotnog broja je sam broj.")
    else:
        # Nivo 3 je STVARNO troopracijski: vrijednost izraza (dvije operacije)
        # pa promjena predznaka — fiksni dokaz nivoa 3 time govori istinu.
        second = _abs_operand(rng, domain, level)
        third = _abs_operand(rng, domain, level)
        if rng.random() < 0.5:
            second = -second
        expression = (f"{display} + {core.parenthesized(_abs_display(domain, second))}"
                      f" - {core.parenthesized(_abs_display(domain, third))}")
        question = f"Koji broj je suprotan vrijednosti izraza ${expression}$?"
        total = base + second - third
        answer = -total
        chain = f"{expression} = {_abs_display(domain, total)}"
        steps_note = ("Prvo izračunaj vrijednost izraza, pa promijeni predznak "
                      "rezultata.")
    answer_text = core.fraction_display(answer) if domain != "integer" \
        else str(answer.numerator)
    candidates = [base, abs(answer) if answer < 0 else -abs(answer),
                  answer + 1, answer - 1, answer + 2, answer - 2]
    hint1 = "Suprotan broj ima istu apsolutnu vrijednost, a suprotan predznak."
    hint2 = f"Kreni od broja ${display}$ i pažljivo prati svaku promjenu predznaka."
    hint3 = f"Postupak: ${chain}$ — provjeri predznak konačnog rezultata."
    solution = f"{steps_note} Računamo: ${chain}$. Rezultat je ${answer_text}$."
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="absolute_value_opposite", operation="opposite", level=level,
        question=question, answer_value=answer, answer_display=answer_text,
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("base", str(base)), ("answer", str(answer)),
                              ("form", f"l{level}")],
        required_conditions=["opposite"], relevant_objects=[domain],
        generator_version=GENERATOR_VERSION,
        display_of=(core.fraction_display if domain != "integer"
                    else lambda v: str(v.numerator if isinstance(v, Fraction) else v)))


def _abs_evidence(level):
    """Nivo = broj članova izraza; nivo 1 je jedno direktno očitavanje."""
    if level == 1:
        return DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    return core.evidence_for_level(level)
