"""Deterministički generator porodica stepena i kvadratnog korijena.

Dvije semantičke porodice (8. razred):

  • power_arithmetic_direct — vrijednost stepena, kvadrat racionalnog broja,
    nulti/negativni izložilac i zakoni stepena jednakih osnova;
  • square_root_direct — kvadratni korijen savršenog kvadrata (i razlomka /
    decimalnog broja sa savršenim kvadratima) te prepoznavanje savršenog
    kvadrata.

MATEMATIČKI AUTORITET: egzaktni `fractions.Fraction`; svaki vidljivi lanac
jednakosti je egzaktan pa ga mathcheck nezavisno dokazuje, a „Izračunaj“
pitanja dodatno dokazuje orakl direktnog računa (safe_numeric_value razumije
`^`, `\\sqrt`, `\\frac` i decimalni zarez).

DISTRAKTORI ZAKONA STEPENA su stepeni DRUGE vrijednosti (pogrešno sabran,
pomnožen ili oduzet izložilac, pomnožene osnove) — nikad drugi zapis iste
vrijednosti, jer bi objava dokazala semantički duplikat opcija.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.tutor.schema import DifficultyEvidence

FAMILY_IDS = ("power_arithmetic_direct", "square_root_direct")
GENERATOR_VERSION = "detpow-1"

_POWER_CONCEPTS = frozenset({
    "square_value", "power_value", "zero_negative_exponent",
    "same_base_product_quotient", "power_of_power_product",
})
_ROOT_CONCEPTS = frozenset({"square_root_value", "perfect_square_recognition"})

_SQUARES = [n * n for n in range(2, 21)]


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    if not concepts:
        return False
    return concepts <= _POWER_CONCEPTS or concepts <= _ROOT_CONCEPTS


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            builder = {
                "square_value": _square_value_package,
                "power_value": _power_value_package,
                "zero_negative_exponent": _zero_negative_package,
                "same_base_product_quotient": _same_base_package,
                "power_of_power_product": _power_law_package,
                "square_root_value": _root_value_package,
                "perfect_square_recognition": _perfect_square_package,
            }[concept]
            return builder(rng, level, lesson_id, lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _value_display(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return core.plain_fraction_display(value)


def _power_display(base_display, exponent):
    return f"{base_display}^{{{exponent}}}"


# ---------------------------------------------------------------------------
# KVADRAT RACIONALNOG BROJA — nivo 1 prirodan, 2 negativan/razlomak, 3 zbir
# ---------------------------------------------------------------------------

def _square_base(rng, level):
    if level == 1:
        return Fraction(rng.randint(2, 12))
    kind = rng.random()
    if kind < 0.4:
        value = Fraction(rng.randint(2, 12))
        return -value
    if kind < 0.7:
        value = Fraction(rng.randint(1, 9), 10)
        return value if rng.random() < 0.5 else -value
    value = Fraction(rng.randint(1, 8), rng.randint(2, 9))
    return value if rng.random() < 0.5 else -value


def _square_term(value: Fraction) -> str:
    display = _value_display(value) if value.denominator > 1 or value < 0 \
        else str(value.numerator)
    if value < 0 or value.denominator > 1:
        return f"({display})^{{2}}"
    return f"{display}^{{2}}"


def _square_value_package(rng, level, lesson_id, lesson_title):
    if level < 3:
        base = _square_base(rng, level)
        answer = base * base
        expression = _square_term(base)
        question = f"Izračunaj: ${expression}$"
        chain = f"{expression} = {_value_display(base)} \\cdot {core.parenthesized(_value_display(base))} = {_value_display(answer)}"
        hint2 = f"Kvadrirati znači pomnožiti broj samim sobom: ${expression} = {_value_display(base)} \\cdot {core.parenthesized(_value_display(base))}$."
        candidates = [base * 2, -answer if base < 0 else answer * 2,
                      abs(base) * 2, answer + 1, answer - 1,
                      answer * 4, answer / 2]
        signature = [("base", str(base))]
    else:
        first = _square_base(rng, 2)
        second = _square_base(rng, 2)
        answer = first * first + second * second
        expression = f"{_square_term(first)} + {_square_term(second)}"
        question = f"Izračunaj: ${expression}$"
        chain = (f"{expression} = {_value_display(first * first)} + "
                 f"{_value_display(second * second)} = {_value_display(answer)}")
        hint2 = (f"Prvo kvadriraj svaki broj posebno: "
                 f"${_square_term(first)} = {_value_display(first * first)}$ i "
                 f"${_square_term(second)} = {_value_display(second * second)}$.")
        candidates = [first * first - second * second, answer + 1, answer - 1,
                      (first + second) * (first + second), answer * 2]
        signature = [("bases", f"{first}+{second}")]
    answer_text = core.fraction_display(answer)
    hint1 = ("Kvadrat broja je proizvod broja samim sobom; kvadrat negativnog "
             "broja je pozitivan.")
    hint3 = "Pazi na predznak: minus u zagradi nestaje kvadriranjem."
    solution = (f"Kvadriramo: ${chain}$. Rezultat je ${answer_text}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="power_arithmetic_direct", operation="square_value",
        level=level, question=question, answer_value=answer,
        answer_display=answer_text, distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=signature, required_conditions=["square"],
        relevant_objects=["rational"], generator_version=GENERATOR_VERSION,
        display_of=core.fraction_display)


# ---------------------------------------------------------------------------
# VRIJEDNOST STEPENA S PRIRODNIM IZLOŽIOCEM
# ---------------------------------------------------------------------------

def _power_value_package(rng, level, lesson_id, lesson_title):
    if level == 1:
        base, exponent = rng.randint(2, 6), rng.randint(2, 3)
        negative = False
    elif level == 2:
        base, exponent = rng.randint(2, 5), rng.randint(2, 4)
        negative = rng.random() < 0.7
    else:
        base, exponent = rng.randint(2, 4), rng.randint(3, 5)
        negative = rng.random() < 0.5
    value = Fraction((-base if negative else base) ** exponent)
    base_display = f"(-{base})" if negative else str(base)
    expression = _power_display(base_display, exponent)
    question = f"Izračunaj: ${expression}$"
    answer_text = str(value.numerator)
    factors = " \\cdot ".join([base_display] * exponent)
    chain = f"{expression} = {factors} = {answer_text}"
    hint1 = ("Stepen je skraćeno množenje: izložilac kaže koliko puta se "
             "osnova množi sama sobom.")
    hint2 = f"Raspiši stepen kao proizvod: ${expression} = {factors}$."
    hint3 = ("Neparan broj negativnih faktora daje negativan rezultat, paran "
             "pozitivan — prvo odredi predznak, pa množi.")
    solution = f"Raspišemo stepen: ${chain}$. Rezultat je ${answer_text}$."
    candidates = [Fraction(base * exponent * (-1 if negative else 1)),
                  -value, value + 1, value - 1,
                  Fraction((-base if not negative else base) ** exponent),
                  value * 2]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="power_arithmetic_direct", operation="power_value",
        level=level, question=question, answer_value=value,
        answer_display=answer_text, distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("base", base_display), ("exponent", str(exponent))],
        required_conditions=["power_value"], relevant_objects=["integer"],
        generator_version=GENERATOR_VERSION, display_of=core.fraction_display)


# ---------------------------------------------------------------------------
# NULTI I NEGATIVAN IZLOŽILAC
# ---------------------------------------------------------------------------

def _zero_negative_package(rng, level, lesson_id, lesson_title):
    if level == 1:
        base = rng.randint(2, 12)
        if rng.random() < 0.5:
            expression = _power_display(str(base), 0)
            value = Fraction(1)
            chain = f"{expression} = 1"
            law = "Svaki broj različit od nule na nulti stepen daje $1$."
        else:
            expression = _power_display(str(base), "-1")
            value = Fraction(1, base)
            chain = f"{expression} = \\frac{{1}}{{{base}}}"
            law = ("Negativan izložilac znači recipročnu vrijednost: osnova "
                   "odlazi u imenilac.")
    elif level == 2:
        base = rng.randint(2, 5)
        exponent = rng.randint(2, 3)
        expression = _power_display(str(base), f"-{exponent}")
        value = Fraction(1, base ** exponent)
        chain = (f"{expression} = \\frac{{1}}{{{_power_display(str(base), exponent)}}}"
                 f" = \\frac{{1}}{{{base ** exponent}}}")
        law = ("Negativan izložilac znači recipročnu vrijednost stepena: "
               "$a^{-n}$ je $1$ kroz $a^{n}$.")
    else:
        base = rng.randint(2, 4)
        exponent = rng.randint(1, 3)
        other = rng.randint(2, 9)
        expression = (f"{_power_display(str(base), f'-{exponent}')} + "
                      f"{_power_display(str(other), 0)}")
        value = Fraction(1, base ** exponent) + 1
        chain = (f"{expression} = \\frac{{1}}{{{base ** exponent}}} + 1 "
                 f"= {core.fraction_display(value)}")
        law = ("Nulti stepen daje $1$, a negativan izložilac recipročnu "
               "vrijednost stepena.")
    answer_text = core.fraction_display(value)
    question = f"Izračunaj: ${expression}$"
    # Nagovještaj 1 NE SMIJE nositi konkretnu vrijednost (nulti stepen bi njime
    # doslovno otkrio odgovor) — pravilo s vrijednošću ide u nagovještaj 2 i
    # rješenje.
    hint1 = ("Sjeti se posebnih pravila: nulti izložilac daje uvijek istu "
             "vrijednost, a negativan izložilac vodi na recipročnu vrijednost "
             "stepena.")
    hint2 = f"{law} Primijeni to na ${chain.split('=')[0].strip()}$."
    hint3 = f"Postupak: ${chain}$ — provjeri svaki korak."
    solution = f"{law} Računamo: ${chain}$. Rezultat je ${answer_text}$."
    candidates = [Fraction(0), -value, Fraction(base) * (-1),
                  value + 1, value - 1 if value > 1 else value + 2,
                  Fraction(base ** max(1, level))]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="power_arithmetic_direct", operation="zero_negative_exponent",
        level=level, question=question, answer_value=value,
        answer_display=answer_text, distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("expression", expression)],
        required_conditions=["zero_negative_exponent"],
        relevant_objects=["rational"], generator_version=GENERATOR_VERSION,
        display_of=core.fraction_display)


# ---------------------------------------------------------------------------
# ZAKONI STEPENA — opcije su STEPENI, sve četiri različite vrijednosti
# ---------------------------------------------------------------------------

def _exponent_options(base, correct_exponent, wrong_exponents, wrong_bases=()):
    """Opcije oblika stepena s MEĐUSOBNO različitim vrijednostima."""
    options = [(Fraction(base) ** correct_exponent,
                f"${_power_display(str(base), correct_exponent)}$")]
    for exponent in wrong_exponents:
        if exponent <= 0:
            continue
        value = Fraction(base) ** exponent
        display = f"${_power_display(str(base), exponent)}$"
        if all(value != seen for seen, _text in options):
            options.append((value, display))
    for wrong_base, exponent in wrong_bases:
        value = Fraction(wrong_base) ** exponent
        display = f"${_power_display(str(wrong_base), exponent)}$"
        if all(value != seen for seen, _text in options):
            options.append((value, display))
    if len(options) < 4:
        raise DeterministicGenerationError("nedovoljno različitih stepena")
    return tuple(text for _value, text in options[:4])


def _same_base_package(rng, level, lesson_id, lesson_title):
    base = rng.randint(2, 6)
    if level == 1:
        m, n = rng.randint(2, 5), rng.randint(2, 5)
        expression = (f"{_power_display(str(base), m)} \\cdot "
                      f"{_power_display(str(base), n)}")
        correct = m + n
        law = ("Stepeni jednakih osnova se množe tako da se izložioci "
               "SABERU, a osnova ostane ista.")
        derivation = f"{expression} = {_power_display(str(base), f'{m}+{n}')} = {_power_display(str(base), correct)}"
        wrong = [m * n, abs(m - n), m + n + 1]
        wrong_bases = [(base * base, m + n)]
    elif level == 2:
        n = rng.randint(2, 4)
        m = n + rng.randint(2, 4)
        expression = (f"{_power_display(str(base), m)} : "
                      f"{_power_display(str(base), n)}")
        correct = m - n
        law = ("Stepeni jednakih osnova se dijele tako da se izložioci "
               "ODUZMU, a osnova ostane ista.")
        derivation = f"{expression} = {_power_display(str(base), f'{m}-{n}')} = {_power_display(str(base), correct)}"
        wrong = [m + n, m * n, correct + 1]
        wrong_bases = []
    else:
        m, n = rng.randint(2, 4), rng.randint(2, 4)
        k = rng.randint(1, min(3, m + n - 1))
        expression = (f"{_power_display(str(base), m)} \\cdot "
                      f"{_power_display(str(base), n)} : "
                      f"{_power_display(str(base), k)}")
        correct = m + n - k
        law = ("Pri množenju stepena jednakih osnova izložioci se sabiraju, "
               "a pri dijeljenju oduzimaju.")
        derivation = (f"{expression} = {_power_display(str(base), f'{m}+{n}-{k}')}"
                      f" = {_power_display(str(base), correct)}")
        wrong = [m + n + k, m * n - k, correct + 1, correct + 2]
        wrong_bases = []
    option_texts = _exponent_options(base, correct, wrong, wrong_bases)
    question = f"Zapiši u obliku jednog stepena: ${expression}$"
    answer_value = Fraction(base) ** correct
    answer_display = _power_display(str(base), correct)
    hint1 = law
    hint2 = f"Osnova ostaje ${base}$ — računaj samo s izložiocima."
    hint3 = f"Postupak: ${derivation}$."
    solution = f"{law} Dakle: ${derivation}$."
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="power_arithmetic_direct", operation="same_base_law",
        level=level, question=question, answer_value=answer_value,
        answer_display=answer_display, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("base", str(base)),
                              ("exponents", f"{expression}")],
        required_conditions=["same_base_law"], relevant_objects=["natural"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", evidence=_law_evidence(level))


def _power_law_package(rng, level, lesson_id, lesson_title):
    base = rng.randint(2, 5)
    if level == 1:
        m, n = rng.randint(2, 3), rng.randint(2, 3)
        expression = f"({_power_display(str(base), m)})^{{{n}}}"
        correct = m * n
        law = ("Stepen stepena: izložioci se MNOŽE, a osnova ostaje ista.")
        product_exponent = f"{m} \\cdot {n}"
        derivation = (f"{expression} = {_power_display(str(base), product_exponent)}"
                      f" = {_power_display(str(base), correct)}")
        wrong = [m + n, m ** n if m ** n != m * n else m + n + 1, correct + 1]
    elif level == 2:
        other = rng.randint(2, 5)
        while other == base:
            other = rng.randint(2, 5)
        n = rng.randint(2, 3)
        expression = f"({base} \\cdot {other})^{{{n}}}"
        value = Fraction((base * other) ** n)
        question = f"Izračunaj: ${expression}$"
        derivation = (f"{expression} = {_power_display(str(base), n)} \\cdot "
                      f"{_power_display(str(other), n)} = "
                      f"{base ** n} \\cdot {other ** n} = {value.numerator}")
        law = ("Stepen proizvoda: svaki faktor se stepenuje istim izložiocem.")
        hint2 = f"Raspiši: ${expression} = {_power_display(str(base), n)} \\cdot {_power_display(str(other), n)}$."
        candidates = [Fraction(base * other * n), Fraction(base ** n * other),
                      value + 1, value - 1, value * 2]
        solution = f"{law} Dakle: ${derivation}$."
        return core.build_package(
            lesson_id=lesson_id, lesson_title=lesson_title,
            family_id="power_arithmetic_direct", operation="power_of_product",
            level=level, question=question, answer_value=value,
            answer_display=str(value.numerator), distractor_values=candidates,
            hints=(law, hint2, f"Postupak: ${derivation}$."), solution=solution,
            signature_parameters=[("base", f"{base}*{other}"), ("exponent", str(n))],
            required_conditions=["power_of_product"],
            relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
            display_of=core.fraction_display, evidence=_law_evidence(level))
    else:
        m = rng.randint(2, 3)
        n = rng.randint(2, 3)
        k = rng.randint(2, 4)
        expression = (f"({_power_display(str(base), m)})^{{{n}}} \\cdot "
                      f"{_power_display(str(base), k)}")
        correct = m * n + k
        law = ("Stepen stepena množi izložioce, a množenje stepena jednakih "
               "osnova ih sabira.")
        product_exponent = f"{m} \\cdot {n}"
        summed_exponent = f"{m * n}+{k}"
        derivation = (f"{expression} = {_power_display(str(base), product_exponent)}"
                      f" \\cdot {_power_display(str(base), k)} = "
                      f"{_power_display(str(base), summed_exponent)} = "
                      f"{_power_display(str(base), correct)}")
        wrong = [m * n * k, m + n + k, correct + 1, correct - 1]
    option_texts = _exponent_options(base, correct, wrong)
    question = f"Zapiši u obliku jednog stepena: ${expression}$"
    answer_value = Fraction(base) ** correct
    answer_display = _power_display(str(base), correct)
    hint2 = f"Osnova ostaje ${base}$ — pazi gdje se izložioci množe, a gdje sabiraju."
    solution = f"{law} Dakle: ${derivation}$."
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="power_arithmetic_direct", operation="power_of_power",
        level=level, question=question, answer_value=answer_value,
        answer_display=answer_display, distractor_values=(),
        hints=(law, hint2, f"Postupak: ${derivation}$."), solution=solution,
        signature_parameters=[("base", str(base)), ("expression", expression)],
        required_conditions=["power_of_power"], relevant_objects=["natural"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", evidence=_law_evidence(level))


def _law_evidence(level):
    """Zakon stepena: jedan primijenjen zakon = jedan korak + promjena zapisa."""
    if level == 1:
        return DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=1, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    return core.evidence_for_level(level)


# ---------------------------------------------------------------------------
# KVADRATNI KORIJEN
# ---------------------------------------------------------------------------

def _root_value_package(rng, level, lesson_id, lesson_title):
    if level == 1:
        root = rng.randint(2, 15)
        value = Fraction(root)
        radicand = f"{root * root}"
        expression = f"\\sqrt{{{radicand}}}"
        chain = f"{expression} = {root}"
        hint2 = f"Traži broj koji pomnožen samim sobom daje ${radicand}$."
        candidates = [Fraction(root * root // 2), Fraction(root + 1),
                      Fraction(root - 1), Fraction(root * 2),
                      Fraction(root * root)]
        signature = [("radicand", radicand)]
    elif level == 2:
        if rng.random() < 0.5:
            p, q = rng.randint(2, 9), rng.randint(2, 12)
            while p == q:
                q = rng.randint(2, 12)
            value = Fraction(p, q)
            radicand = f"\\frac{{{p * p}}}{{{q * q}}}"
            expression = f"\\sqrt{{{radicand}}}"
            chain = f"{expression} = \\frac{{{p}}}{{{q}}}"
            hint2 = (f"Korjenuj brojnik i imenilac posebno: "
                     f"$\\sqrt{{{p * p}}} = {p}$ i $\\sqrt{{{q * q}}} = {q}$.")
            candidates = [Fraction(p * p, q * q), Fraction(q, p),
                          value + 1, Fraction(p, q + 1)]
            signature = [("radicand", f"{p * p}/{q * q}")]
        else:
            root = Fraction(rng.randint(2, 9), 10)
            value = root
            radicand = core.decimal_display(root * root)
            expression = f"\\sqrt{{{radicand}}}"
            chain = f"{expression} = {core.decimal_display(root)}"
            hint2 = (f"Traži decimalni broj čiji je kvadrat ${radicand}$ — "
                     "pazi na broj decimala.")
            candidates = [root * root, root * 10, root / 10, root + Fraction(1, 10)]
            signature = [("radicand", str(root * root))]
    else:
        a, b = rng.sample(range(2, 13), 2)
        value = Fraction(a + b)
        expression = f"\\sqrt{{{a * a}}} + \\sqrt{{{b * b}}}"
        chain = f"{expression} = {a} + {b} = {a + b}"
        hint2 = (f"Izračunaj svaki korijen posebno: $\\sqrt{{{a * a}}} = {a}$ "
                 f"i $\\sqrt{{{b * b}}} = {b}$.")
        candidates = [Fraction(a * a + b * b), Fraction(abs(a - b)),
                      value + 1, value - 1, Fraction((a + b) * (a + b))]
        signature = [("radicands", f"{a * a}+{b * b}")]
    question = f"Izračunaj: ${expression}$"
    answer_text = core.fraction_display(value) if value.denominator > 1 \
        else (core.decimal_display(value) if level == 2 and value.denominator == 1
              and False else core.fraction_display(value))
    if level == 2 and value.denominator in (10, 100):
        answer_text = core.decimal_display(value)
    hint1 = ("Kvadratni korijen broja je nenegativan broj čiji je kvadrat "
             "jednak datom broju.")
    hint3 = "Provjeri kvadriranjem: rezultat pomnožen samim sobom mora dati potkorjenu vrijednost."
    solution = f"Korjenujemo: ${chain}$. Rezultat je ${answer_text}$."
    display_of = core.fraction_display
    if level == 2 and value.denominator in (10, 100):
        display_of = (lambda v: core.decimal_display(v)
                      if core.is_terminating_decimal(v) and v.denominator in (10, 100)
                      else core.fraction_display(v))
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="square_root_direct", operation="square_root_value",
        level=level, question=question, answer_value=value,
        answer_display=answer_text, distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=signature, required_conditions=["square_root"],
        relevant_objects=["rational"], generator_version=GENERATOR_VERSION,
        display_of=display_of)


def _perfect_square_package(rng, level, lesson_id, lesson_title):
    squares = [s for s in _SQUARES if (s < 100 if level == 1 else s >= 25)]
    correct = rng.choice(squares)
    low, high = (10, 120) if level == 1 else (30, 420)
    pool = [n for n in range(low, high)
            if n not in _SQUARES and n != correct]
    rng.shuffle(pool)
    distractors = pool[:3]
    if len(distractors) < 3:
        raise DeterministicGenerationError("nedovoljno kandidata")
    root = int(correct ** 0.5)
    question = "Koji od ponuđenih brojeva je savršeni kvadrat?"
    # Bez liste primjera: pri malim vrijednostima primjeri bi doslovno
    # sadržavali tačan odgovor.
    hint1 = ("Savršeni kvadrat je broj koji se može zapisati kao proizvod "
             "nekog prirodnog broja samog sa sobom.")
    hint2 = "Za svaku opciju provjeri postoji li prirodan broj čiji je kvadrat baš ta vrijednost."
    hint3 = f"Sjeti se kvadrata brojeva do $20$ i uporedi ih s ponuđenim opcijama."
    solution = (f"Broj ${correct}$ je savršeni kvadrat: "
                f"${correct} = {root}^{{2}}$. Ostale opcije nisu kvadrat "
                "nijednog prirodnog broja.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="square_root_direct", operation="perfect_square_recognition",
        level=level, question=question, answer_value=correct,
        answer_display=str(correct), distractor_values=distractors,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("correct", str(correct))],
        required_conditions=["perfect_square"], relevant_objects=["natural"],
        generator_version=GENERATOR_VERSION, display_of=lambda value: str(value))
