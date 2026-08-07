"""Deterministički generator porodica direktnog brojevnog računa.

JEDAN modul za četiri semantičke porodice — prirodni brojevi, cijeli brojevi,
decimalni brojevi i predznačeni racionalni brojevi — jer je posao identičan, a
razlike nose ISKLJUČIVO parametri kompajliranog ugovora (`number_domain`,
`allowed_operations`, `expression_shape`, `sign_scope`). Nijedan ID lekcije,
nijedan naslov, nijedna grana po lekciji.

MATEMATIČKI AUTORITET: sve vrijednosti su egzaktni `fractions.Fraction`;
decimalni prikaz nastaje iz razlomka s dekadskim imeniocem
(core.decimal_display), pa je svaka vidljiva jednakost egzaktno tačna i
nezavisno je dokazuje postojeći mathcheck + orakl direktnog računa
(mcq_integrity) nad objavljenim paketom.

NIVO JE BROJ POVEZANIH OPERACIJA, ne prozna procjena: nivo 1 = jedna operacija
malim brojevima, nivo 2 = dvije povezane operacije, nivo 3 = tri povezane
operacije. Fiksni dokaz težine time doslovno opisuje vidljivi zadatak; šabloni
koji odstupaju (redoslijed operacija nivoa 1, proizvod više faktora) nose
VLASTITI iskren dokaz umjesto zajedničkog.

ZAPIS U LANCU RJEŠENJA: međurezultati su UVIJEK u „ravnom“ zapisu (cio broj,
nesvodivi razlomak, decimalni broj) — mješoviti broj `K\\frac{p}{q}` se u
sredini izraza čita kao K + p/q, pa bi `a - 2\\frac{1}{3}` značilo pogrešnu
vrijednost. Mješoviti zapis smije stajati samo kao samostalan konačan rezultat.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.tutor.schema import DifficultyEvidence

FAMILY_IDS = (
    "natural_arithmetic_direct",
    "integer_arithmetic_direct",
    "decimal_arithmetic_direct",
    "rational_arithmetic_direct",
)
GENERATOR_VERSION = "detarith-1"

_SUPPORTED_OPERATIONS = frozenset({"add", "subtract", "multiply", "divide"})
# `rational_nonneg` (Batch #2): pozitivni racionalni brojevi 6. razreda (Q+) —
# isti račun kao rational_signed, ali SVAKI operand i SVAKI međurezultat mora
# ostati nenegativan (kurikulum 6. razreda ne poznaje negativne razlomke).
_SUPPORTED_DOMAINS = frozenset({"natural", "integer", "decimal",
                                "rational_signed", "rational_nonneg"})
_SUPPORTED_SHAPES = frozenset({"single_operation", "multi_factor",
                               "order_of_operations", "division_with_remainder",
                               "complex_fraction"})
_SUPPORTED_SIGN_SCOPES = frozenset({"any", "same_signs", "different_signs"})

# Projektni zapis operacija (matbot/rules.py): množenje `\cdot`, dijeljenje `:`.
_SYMBOL = {"add": "+", "subtract": "-", "multiply": "\\cdot", "divide": ":"}

_RULE_HINT = {
    ("natural", "add"): "Saberi brojeve — kod višecifrenih pazi na prenos preko desetice.",
    ("natural", "subtract"): "Oduzmi drugi broj od prvog — pazi na pozajmicu preko desetice.",
    ("natural", "multiply"): "Pomnoži brojeve — kod višecifrenih množi mjesto po mjesto pa saberi.",
    ("natural", "divide"): "Podijeli brojeve — provjeri rezultat množenjem: količnik puta djelilac daje djeljenik.",
    ("integer", "add"): "Isti znakovi: saberi apsolutne vrijednosti i zadrži znak. Različiti znakovi: oduzmi manju apsolutnu vrijednost od veće i uzmi znak broja veće apsolutne vrijednosti.",
    ("integer", "subtract"): "Oduzimanje je sabiranje sa suprotnim brojem: umjesto oduzimanja dodaj suprotan broj.",
    ("integer", "multiply"): "Pravilo znakova: isti znakovi daju plus, različiti znakovi daju minus — zatim pomnoži apsolutne vrijednosti.",
    ("integer", "divide"): "Pravilo znakova važi i za dijeljenje: isti znakovi daju plus, različiti minus — zatim podijeli apsolutne vrijednosti.",
    ("decimal", "add"): "Potpiši decimalne brojeve tako da zarez bude ispod zareza, pa saberi mjesto po mjesto.",
    ("decimal", "subtract"): "Potpiši decimalne brojeve tako da zarez bude ispod zareza, pa oduzmi mjesto po mjesto.",
    ("decimal", "multiply"): "Pomnoži brojeve kao prirodne, pa u rezultatu odvoji onoliko decimala koliko ih imaju oba faktora zajedno.",
    ("decimal", "divide"): "Pomjeri zarez i u djeljeniku i u djeliocu za isti broj mjesta dok djelilac ne postane prirodan broj, pa podijeli.",
    ("rational_signed", "add"): "Racionalni brojevi se sabiraju kao razlomci: svedi na zajednički imenilac i pazi na znakove.",
    ("rational_signed", "subtract"): "Oduzimanje racionalnog broja je sabiranje sa suprotnim brojem — svedi na zajednički imenilac i pazi na znakove.",
    ("rational_signed", "multiply"): "Pomnoži brojnik s brojnikom i imenilac s imeniocem; različiti znakovi daju minus.",
    ("rational_signed", "divide"): "Dijeljenje racionalnim brojem je množenje njegovom recipročnom vrijednošću; pravilo znakova važi i ovdje.",
}

_PITFALL = {
    ("natural", "add"): "pazi na prenos",
    ("natural", "subtract"): "pazi na pozajmicu",
    ("natural", "multiply"): "pazi na mjesnu vrijednost",
    ("natural", "divide"): "provjeri množenjem",
    ("integer", "add"): "pazi na znak rezultata",
    ("integer", "subtract"): "prvo pretvori u sabiranje suprotnog broja",
    ("integer", "multiply"): "prvo odredi znak rezultata",
    ("integer", "divide"): "prvo odredi znak rezultata",
    ("decimal", "add"): "zarez ispod zareza",
    ("decimal", "subtract"): "zarez ispod zareza",
    ("decimal", "multiply"): "prebroj decimalna mjesta oba faktora",
    ("decimal", "divide"): "prvo ukloni zarez iz djelioca",
    ("rational_signed", "add"): "prvo nađi zajednički imenilac",
    ("rational_signed", "subtract"): "prvo nađi zajednički imenilac",
    ("rational_signed", "multiply"): "skrati prije množenja ako se može",
    ("rational_signed", "divide"): "pomnoži recipročnom vrijednošću djelioca",
}

_ORDER_RULE = ("Redoslijed operacija: prvo zagrade, zatim množenje i dijeljenje, "
               "na kraju sabiranje i oduzimanje.")


def supports(parameters) -> bool:
    """True SAMO kad parametri ugovora u potpunosti staju u ovaj generator."""
    parameters = parameters or {}
    operations = tuple(parameters.get("allowed_operations") or ())
    if not operations or not set(operations) <= _SUPPORTED_OPERATIONS:
        return False
    domain = parameters.get("number_domain")
    if domain not in _SUPPORTED_DOMAINS:
        return False
    shape = parameters.get("expression_shape") or "single_operation"
    if shape not in _SUPPORTED_SHAPES:
        return False
    if shape == "order_of_operations":
        # Batch #2: šabloni prioriteta pokrivaju i cijele, decimalne i
        # racionalne domene — svaka jednakost u koracima ostaje egzaktna.
        # Šabloni koriste sve četiri operacije, pa ih ugovor mora sve dozvoliti.
        if set(operations) != set(_SUPPORTED_OPERATIONS):
            return False
    if shape == "division_with_remainder" and domain != "natural":
        return False
    if shape == "complex_fraction" and domain != "rational_signed":
        return False
    scope = parameters.get("sign_scope") or "any"
    if scope not in _SUPPORTED_SIGN_SCOPES:
        return False
    if scope != "any" and domain != "integer":
        return False
    return True


# ---------------------------------------------------------------------------
# OPERANDI PO DOMENU I NIVOU
# ---------------------------------------------------------------------------

def _natural(rng, level, operation):
    if operation in ("add", "subtract"):
        high = {1: 90, 2: 400, 3: 400}[level]
        return Fraction(rng.randint(11, high))
    return Fraction(rng.randint(3, 9 if level == 1 else 12))


def _integer(rng, level, operation):
    if operation in ("multiply", "divide"):
        magnitude = 9 if level == 1 else 12
    else:
        magnitude = {1: 12, 2: 40, 3: 60}[level]
    value = rng.randint(2, magnitude)
    return Fraction(value if rng.random() < 0.5 else -value)


def _decimal(rng, level, operation):
    places = 1 if level == 1 else rng.choice((1, 2))
    scale = 10 ** places
    high = {1: 9, 2: 40, 3: 60}[level]
    if operation in ("multiply", "divide"):
        high = 9
    numerator = rng.randint(1, high * scale)
    if numerator % scale == 0:
        numerator += rng.randint(1, scale - 1)
    return Fraction(numerator, scale)


def _signed_fraction(rng, level, _operation):
    den = rng.randint(2, 6 if level == 1 else 9)
    num = rng.randint(1, den - 1 if level == 1 else 2 * den)
    value = Fraction(num, den)
    if rng.random() < 0.5:
        value = -value
    return value


def _nonneg_fraction(rng, level, operation):
    return abs(_signed_fraction(rng, level, operation))


_OPERAND_BUILDERS = {"natural": _natural, "integer": _integer,
                     "decimal": _decimal, "rational_signed": _signed_fraction,
                     "rational_nonneg": _nonneg_fraction}


def _hint_domain(domain):
    """Q+ dijeli ista pravila i zamke kao predznačeni racionalni brojevi."""
    return "rational_signed" if domain == "rational_nonneg" else domain


def _operand(rng, domain, level, operation):
    return _OPERAND_BUILDERS[domain](rng, level, operation)


def _display(domain, value: Fraction) -> str:
    """Ravan zapis operanda/međurezultata (nikad mješovit broj)."""
    if domain == "decimal":
        return core.decimal_display(value)
    if domain in ("rational_signed", "rational_nonneg"):
        return core.plain_fraction_display(value)
    return str(value.numerator)  # natural/integer su uvijek cijeli


def _final_display(domain, value: Fraction) -> str:
    """Kanonski zapis KONAČNOG rezultata (pozitivan razlomak smije biti mješovit)."""
    if domain == "decimal":
        return core.decimal_display(value)
    if domain in ("rational_signed", "rational_nonneg"):
        return core.fraction_display(value)
    return str(value.numerator)


def _apply(operation, left: Fraction, right: Fraction) -> Fraction:
    if operation == "add":
        return left + right
    if operation == "subtract":
        return left - right
    if operation == "multiply":
        return left * right
    if right == 0:
        raise DeterministicGenerationError("djelilac nula")
    return left / right


# ---------------------------------------------------------------------------
# LANAC OPERACIJA ISTE PRIORITETNE KLASE — nivo = broj operacija
# ---------------------------------------------------------------------------

def _chain_operations(rng, allowed, level, shape):
    if shape == "multi_factor":
        return ["multiply"] * (2 if level < 3 else 3)
    additive = [op for op in allowed if op in ("add", "subtract")]
    multiplicative = [op for op in allowed if op in ("multiply", "divide")]
    pool = additive if additive and (not multiplicative or rng.random() < 0.5) \
        else multiplicative
    return [rng.choice(pool) for _ in range(level)]


def _division_pair(rng, domain, level):
    """(djeljenik, djelilac) s EGZAKTNIM količnikom, konstruisano unazad."""
    if domain == "natural":
        quotient = rng.randint(2, 9 if level == 1 else 40)
        divisor = rng.randint(2, 9 if level < 3 else 24)
        return Fraction(quotient * divisor), Fraction(divisor)
    if domain == "integer":
        quotient = rng.randint(2, 9 if level == 1 else 12)
        divisor = rng.randint(2, 9 if level == 1 else 12)
        sign_q = 1 if rng.random() < 0.5 else -1
        sign_d = 1 if rng.random() < 0.5 else -1
        return Fraction(quotient * divisor * sign_q * sign_d), Fraction(divisor * sign_d)
    if domain == "decimal":
        places = 1 if level == 1 else rng.choice((1, 2))
        quotient = Fraction(rng.randint(11, 99), 10 ** places)
        divisor = Fraction(rng.choice((2, 4, 5)) if level < 3
                           else rng.choice((2, 4, 5, 8, 25)),
                           10 if level == 3 and rng.random() < 0.5 else 1)
        return quotient * divisor, divisor
    divisor = _signed_fraction(rng, level, "divide")
    quotient = _signed_fraction(rng, level, "divide")
    if divisor == 0:
        raise DeterministicGenerationError("djelilac nula")
    return quotient * divisor, divisor


def _later_divisor(rng, partial: Fraction, domain):
    """Djelilac za NASTAVAK lanca tako da količnik ostane u domenu."""
    if domain in ("natural", "integer"):
        if partial.denominator != 1:
            return None
        magnitude = abs(partial.numerator)
        options = [d for d in range(2, 13) if magnitude % d == 0 and magnitude // d > 0]
        if not options:
            return None
        divisor = rng.choice(options)
        if domain == "integer" and rng.random() < 0.5:
            divisor = -divisor
        return Fraction(divisor)
    if domain == "decimal":
        return Fraction(rng.choice((2, 4, 5)))
    value = _signed_fraction(rng, 2, "divide")
    return value if value != 0 else None


def _partial_value(values, operations):
    result = values[0]
    for index in range(len(values) - 1):
        result = _apply(operations[index], result, values[index + 1])
    return result


def _exact_in_domain(value: Fraction, domain) -> bool:
    if domain == "natural":
        return value.denominator == 1 and value >= 0
    if domain == "integer":
        return value.denominator == 1
    if domain == "decimal":
        return value >= 0 and core.is_terminating_decimal(value)
    if domain == "rational_nonneg":
        # Q+ (6. razred): nijedan međurezultat ne smije postati negativan.
        return value >= 0
    return True


def _sign_scope_ok(values, sign_scope):
    if sign_scope == "same_signs":
        return all(v > 0 for v in values) or all(v < 0 for v in values)
    if sign_scope == "different_signs":
        return any(v > 0 for v in values) and any(v < 0 for v in values)
    return True


def _build_chain(rng, domain, level, operations, sign_scope):
    """Vrati vrijednosti operanada tako da su SVI međurezultati u domenu."""
    for _ in range(300):
        if operations[0] == "divide":
            dividend, divisor = _division_pair(rng, domain, level)
            values = [dividend, divisor]
        else:
            values = [_operand(rng, domain, level, operations[0]),
                      _operand(rng, domain, level, operations[0])]
        ok = True
        for index, operation in enumerate(operations[1:], start=1):
            if operation == "divide":
                partial = _partial_value(values, operations[:index])
                divisor = _later_divisor(rng, partial, domain)
                if divisor is None:
                    ok = False
                    break
                values.append(divisor)
            else:
                values.append(_operand(rng, domain, level, operation))
        if not ok or any(value == 0 for value in values):
            continue
        if not _sign_scope_ok(values, sign_scope):
            continue
        result = values[0]
        for index, operation in enumerate(operations):
            result = _apply(operation, result, values[index + 1])
            if not _exact_in_domain(result, domain):
                ok = False
                break
        if not ok or result == values[0]:
            continue
        return values
    raise DeterministicGenerationError("lanac operanada nije nastao")


def _chain_display(domain, values, operations):
    parts = [_display(domain, values[0])]
    for index, operation in enumerate(operations):
        operand = _display(domain, values[index + 1])
        parts.append(f" {_SYMBOL[operation]} {core.parenthesized(operand)}")
    return "".join(parts)


def _chain_solution_steps(domain, values, operations):
    """Koraci lanca jednakosti — SVAKA jednakost egzaktna, ravan zapis."""
    steps = []
    result = values[0]
    for index, operation in enumerate(operations):
        result = _apply(operation, result, values[index + 1])
        remainder = ""
        for later in range(index + 1, len(operations)):
            operand = _display(domain, values[later + 1])
            remainder += f" {_SYMBOL[operations[later]]} {core.parenthesized(operand)}"
        step_value = _display(domain, result)
        if remainder:
            steps.append(f"{core.parenthesized(step_value)}{remainder}"
                         if step_value.startswith("-") and remainder
                         else f"{step_value}{remainder}")
        else:
            steps.append(step_value)
    return steps, result


# ---------------------------------------------------------------------------
# REDOSLIJED OPERACIJA — fiksni dokazivi šabloni nad prirodnim brojevima
# ---------------------------------------------------------------------------

def _order_operand(rng, domain):
    """Mali operand za šablone prioriteta — svaka domena ostaje zatvorena."""
    if domain == "natural":
        return Fraction(rng.randint(2, 9))
    if domain == "integer":
        value = rng.randint(2, 9)
        return Fraction(value if rng.random() < 0.5 else -value)
    if domain == "decimal":
        return Fraction(rng.randint(2, 99), 10)
    den = rng.randint(2, 6)
    value = Fraction(rng.randint(1, den + 2), den)
    if domain == "rational_signed" and rng.random() < 0.4:
        value = -value
    return value


def _order_term(domain, value):
    return core.parenthesized(_display(domain, value))


def _order_template(rng, level, domain):
    """(display, koraci, rezultat, sjeme potpisa, broj operacija).

    Šabloni su DOMENSKI generički (Batch #2): operandi dolaze iz domene, a
    dijeljenje se uvijek konstruiše unazad pa je svaka jednakost u koracima
    egzaktna i svaki međurezultat ostaje u domeni."""
    def pick():
        return _order_operand(rng, domain)

    if level == 1:
        # a ± b·c: jedna prioritetna odluka, dvije operacije.
        b, c = pick(), pick()
        product = b * c
        if rng.random() < 0.5 or domain == "rational_signed":
            a = pick()
            if domain in ("natural", "rational_nonneg", "decimal"):
                a = abs(a) + abs(product)
            display = f"{_display(domain, a)} + {_order_term(domain, b)} \\cdot {_order_term(domain, c)}"
            result = a + product
            steps = [f"{_display(domain, a)} + {_order_term(domain, product)}",
                     _display(domain, result)]
            seeds = (a, b, c)
        else:
            a = abs(product) + abs(pick())
            display = f"{_display(domain, a)} - {_order_term(domain, b)} \\cdot {_order_term(domain, c)}"
            result = a - product
            steps = [f"{_display(domain, a)} - {_order_term(domain, product)}",
                     _display(domain, result)]
            seeds = (a, b, c)
        return display, steps, result, seeds, 2

    if level == 2:
        # (a ± b) : c — zagrada mijenja prirodni redoslijed, dijeljenje egzaktno.
        if domain in ("natural", "integer"):
            c = Fraction(rng.randint(2, 9))
            quotient = Fraction(rng.randint(2, 12))
            if domain == "integer" and rng.random() < 0.5:
                quotient = -quotient
        elif domain == "decimal":
            c = Fraction(rng.choice((2, 4, 5)))
            quotient = Fraction(rng.randint(11, 99), 10)
        else:
            c = Fraction(rng.randint(2, 6))
            quotient = _order_operand(rng, domain)
        total = quotient * c
        b = abs(_order_operand(rng, domain))
        if b == 0:
            raise DeterministicGenerationError("prazan sabirak")
        if rng.random() < 0.5:
            a = total - b
            if domain in ("natural", "rational_nonneg", "decimal") and a < 0:
                raise DeterministicGenerationError("negativan član u Q+")
            display = (f"({_display(domain, a)} + {_order_term(domain, b)}) "
                       f": {_order_term(domain, c)}")
        else:
            a = total + b
            display = (f"({_display(domain, a)} - {_order_term(domain, b)}) "
                       f": {_order_term(domain, c)}")
        steps = [f"{_order_term(domain, total)} : {_order_term(domain, c)}",
                 _display(domain, quotient)]
        return display, steps, quotient, (a, b, c), 2

    if rng.random() < 0.5:
        a, b, c, d = pick(), pick(), pick(), pick()
        p1, p2 = a * b, c * d
        result = p1 + p2
        if domain in ("natural", "rational_nonneg", "decimal") and result < 0:
            raise DeterministicGenerationError("negativan zbir")
        display = (f"{_order_term(domain, a)} \\cdot {_order_term(domain, b)} + "
                   f"{_order_term(domain, c)} \\cdot {_order_term(domain, d)}")
        steps = [f"{_order_term(domain, p1)} + {_order_term(domain, c)} \\cdot {_order_term(domain, d)}",
                 f"{_order_term(domain, p1)} + {_order_term(domain, p2)}",
                 _display(domain, result)]
        return display, steps, result, (a, b, c, d), 3
    a, b, c = abs(pick()), abs(pick()), pick()
    total = (a + b) * c
    d = abs(pick())
    result = total - d
    if domain in ("natural", "rational_nonneg", "decimal") and result < 0:
        raise DeterministicGenerationError("negativna razlika")
    display = (f"({_display(domain, a)} + {_order_term(domain, b)}) \\cdot "
               f"{_order_term(domain, c)} - {_order_term(domain, d)}")
    steps = [f"{_order_term(domain, a + b)} \\cdot {_order_term(domain, c)} - {_order_term(domain, d)}",
             f"{_order_term(domain, total)} - {_order_term(domain, d)}",
             _display(domain, result)]
    return display, steps, result, (a, b, c, d), 3


def _order_evidence(level, operation_count):
    """Iskren dokaz: šablon nivoa 1 STVARNO ima dvije operacije."""
    if level == 1:
        return DifficultyEvidence(
            reasoning_steps=1, condition_count=1,
            operation_count=min(operation_count, 2),
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False)
    return core.evidence_for_level(level)


# ---------------------------------------------------------------------------
# DISTRAKTORI — poznate učeničke greške, egzaktno izračunate
# ---------------------------------------------------------------------------

def _distractors(domain, operations, values, answer):
    candidates = []
    primary = operations[0]
    left, right = values[0], values[1]
    if primary in ("add", "subtract") and len(operations) == 1:
        candidates.append(_apply("subtract" if primary == "add" else "add",
                                 left, right))              # pogrešna operacija
    if domain == "integer":
        candidates.append(-answer)                          # pogrešan znak
        if primary == "add" and len(operations) == 1:
            candidates.append(abs(left) + abs(right))       # zbir apsolutnih
    if domain == "decimal":
        candidates.append(answer * 10)                      # pomjeren zarez
        candidates.append(answer / 10)
    if domain == "rational_signed":
        candidates.append(-answer)
        if primary in ("add", "subtract") and (
                left.denominator != right.denominator):
            numerator = (left.numerator + right.numerator if primary == "add"
                         else left.numerator - right.numerator)
            denominator = left.denominator + right.denominator
            if denominator > 0:
                candidates.append(Fraction(numerator, denominator))
        if primary == "multiply" and right != 0:
            candidates.append(left / right)                 # podijelio umjesto množio
        if primary == "divide" and left != 0:
            candidates.append(left * right)                 # množio bez recipročnog
    if primary == "multiply" and domain in ("natural", "integer"):
        candidates.append(left + right)                     # sabrao umjesto množio
    if primary == "divide" and domain in ("natural", "integer", "decimal"):
        candidates.append(left * right)                     # množio umjesto dijelio
    step = Fraction(1) if domain in ("natural", "integer") else (
        Fraction(1, 10) if domain == "decimal"
        else Fraction(1, answer.denominator if answer.denominator > 1 else 2))
    for factor in (1, 2, 3, 4):
        candidates.append(answer + factor * step)
        candidates.append(answer - factor * step)
    if domain in ("natural", "decimal"):
        candidates = [value for value in candidates if value >= 0]
    if domain in ("natural", "integer"):
        candidates = [value for value in candidates if value.denominator == 1]
    if domain == "decimal":
        candidates = [value for value in candidates
                      if core.is_terminating_decimal(value)]
    return candidates


# ---------------------------------------------------------------------------
# GLAVNA ULAZNA TAČKA
# ---------------------------------------------------------------------------

def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    """Sastavi kompletan deterministički paket za JEDNU lekciju porodice.

    `parameters` su parametri kompajliranog semantičkog ugovora — generator
    NIKAD ne grana po identitetu lekcije."""
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    domain = parameters["number_domain"]
    allowed = tuple(parameters["allowed_operations"])
    shape = parameters.get("expression_shape") or "single_operation"
    sign_scope = parameters.get("sign_scope") or "any"

    for _ in range(60):
        try:
            if shape == "order_of_operations":
                return _order_package(rng, lesson_id, lesson_title, level,
                                      domain)
            if shape == "division_with_remainder":
                return _remainder_package(rng, lesson_id, lesson_title, level)
            if shape == "complex_fraction":
                return _complex_fraction_package(rng, lesson_id, lesson_title,
                                                 level)
            return _chain_package(rng, lesson_id, lesson_title, domain,
                                  allowed, shape, sign_scope, level)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _chain_package(rng, lesson_id, lesson_title, domain, allowed, shape,
                   sign_scope, level):
    operations = _chain_operations(rng, allowed, level, shape)
    values = _build_chain(rng, domain, level, operations, sign_scope)
    steps, answer = _chain_solution_steps(domain, values, operations)
    expression = _chain_display(domain, values, operations)
    question = f"Izračunaj: ${expression}$"
    answer_text = _final_display(domain, answer)
    flat_answer = _display(domain, answer)

    rule = _RULE_HINT[(_hint_domain(domain), operations[0])]
    pitfall = _PITFALL[(_hint_domain(domain), operations[0])]
    if len(operations) > 1:
        hint2 = f"Kreni redom: prvi korak daje ${steps[0]}$."
        hint3 = (f"Dakle: ${expression} = {steps[-2]}$ — još samo završi račun.")
    else:
        hint2 = f"Primijeni pravilo na brojeve iz zadatka — {pitfall}."
        hint3 = (f"Dakle: ${expression}$ — {pitfall}, pa uporedi svoj rezultat "
                 "s ponuđenim opcijama.")
    chain = " = ".join([expression] + steps)
    if answer_text != flat_answer:
        chain += f" = {answer_text}"
    solution = f"{rule} Računamo: ${chain}$. Rezultat je ${answer_text}$."

    distractors = _distractors(domain, operations, values, answer)
    display_of = (core.decimal_display if domain == "decimal"
                  else core.fraction_display)
    signature = [(f"operand_{index}", str(value))
                 for index, value in enumerate(values)]
    signature.append(("operations", "+".join(operations)))
    evidence = None
    if shape == "multi_factor" and level == 1:
        # Proizvod tri faktora ima dvije operacije i na nivou 1 — dokaz to kaže.
        evidence = _order_evidence(1, len(operations))
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id=_family_for(domain), operation=operations[0], level=level,
        question=question, answer_value=answer, answer_display=answer_text,
        distractor_values=distractors, hints=(rule, hint2, hint3),
        solution=solution, signature_parameters=signature,
        required_conditions=[f"domain_{domain}"],
        relevant_objects=[domain], generator_version=GENERATOR_VERSION,
        display_of=display_of, evidence=evidence)


def _order_package(rng, lesson_id, lesson_title, level, domain="natural"):
    display, steps, answer, seeds, operation_count = _order_template(rng, level,
                                                                     domain)
    question = f"Izračunaj: ${display}$"
    answer_text = _final_display(domain, answer)
    flat_answer = _display(domain, answer)
    hint2 = f"Prvi korak: ${display} = {steps[0]}$."
    hint3 = (f"Dakle: ${display} = {steps[-2] if len(steps) > 1 else steps[-1]}$"
             " — još samo završi račun.")
    chain = " = ".join([display] + steps)
    if answer_text != flat_answer:
        chain += f" = {answer_text}"
    solution = f"{_ORDER_RULE} Računamo: ${chain}$. Rezultat je ${answer_text}$."
    step_unit = Fraction(1) if domain in ("natural", "integer") else (
        Fraction(1, 10) if domain == "decimal"
        else Fraction(1, answer.denominator if answer.denominator > 1 else 2))
    distractors = [answer + factor * step_unit
                   for factor in (1, -1, 2, -2, 3, 5, 10, -3)]
    if domain in ("natural", "decimal", "rational_nonneg"):
        distractors = [value for value in distractors if value >= 0]
    display_of = (core.decimal_display if domain == "decimal"
                  else core.fraction_display)
    signature = [(f"seed_{index}", str(value))
                 for index, value in enumerate(seeds)]
    signature.append(("template", f"order_l{level}"))
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id=_family_for(domain), operation="order_of_operations",
        level=level, question=question, answer_value=answer,
        answer_display=answer_text, distractor_values=distractors,
        hints=(_ORDER_RULE, hint2, hint3), solution=solution,
        signature_parameters=signature,
        required_conditions=["order_of_operations"],
        relevant_objects=[domain], generator_version=GENERATOR_VERSION,
        display_of=display_of,
        evidence=_order_evidence(level, operation_count))


# ---------------------------------------------------------------------------
# DIJELJENJE S OSTATKOM: a = b·q + r  (Batch #2)
# ---------------------------------------------------------------------------

def _remainder_package(rng, lesson_id, lesson_title, level):
    divisor = rng.randint(3, 9 if level == 1 else 12)
    quotient = rng.randint(2, 12 if level < 3 else 40)
    remainder = rng.randint(1, divisor - 1)
    dividend = divisor * quotient + remainder
    identity = f"{dividend} = {divisor} \\cdot {quotient} + {remainder}"

    if level == 1:
        question = (f"Koliki je ostatak pri dijeljenju broja ${dividend}$ "
                    f"brojem ${divisor}$?")
        answer = Fraction(remainder)
        answer_text = str(remainder)
        candidates = [Fraction(v) for v in
                      (quotient, remainder + 1, remainder - 1, divisor,
                       divisor - remainder, remainder + 2) if v >= 0]
        operation = "remainder"
    elif level == 2:
        question = (f"Koliki je količnik pri dijeljenju broja ${dividend}$ "
                    f"brojem ${divisor}$ (dijeljenje s ostatkom)?")
        answer = Fraction(quotient)
        answer_text = str(quotient)
        candidates = [Fraction(v) for v in
                      (quotient + 1, quotient - 1, remainder, dividend - divisor,
                       quotient + 2) if v > 0]
        operation = "quotient"
    else:
        question = (f"Koji broj pri dijeljenju brojem ${divisor}$ daje "
                    f"količnik ${quotient}$ i ostatak ${remainder}$?")
        answer = Fraction(dividend)
        answer_text = str(dividend)
        candidates = [Fraction(v) for v in
                      (dividend + divisor, dividend - divisor,
                       divisor * quotient, divisor * quotient - remainder,
                       dividend + 1, dividend - 1) if v > 0]
        operation = "missing_dividend"

    hint1 = ("Dijeljenje s ostatkom zapisuje se kao a = b·q + r, gdje je "
             "ostatak r manji od djelioca b.")
    hint2 = (f"Potraži najveći sadržilac broja ${divisor}$ koji ne premašuje "
             f"djeljenik, pa pogledaj šta ostane." if level < 3 else
             f"Uvrsti u zapis a = b·q + r: $a = {divisor} \\cdot {quotient} + "
             f"{remainder}$.")
    hint3 = f"Provjeri zapisom: ${identity}$ — ostatak mora biti manji od djelioca."
    solution = (f"Vrijedi ${identity}$, a ostatak ${remainder}$ je manji od "
                f"djelioca ${divisor}$. Traženi odgovor je ${answer_text}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="natural_arithmetic_direct", operation=operation,
        level=level, question=question, answer_value=answer,
        answer_display=answer_text, distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("dividend", str(dividend)),
                              ("divisor", str(divisor))],
        required_conditions=["division_with_remainder"],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        display_of=core.fraction_display)


# ---------------------------------------------------------------------------
# DVOJNI RAZLOMAK  (Batch #2)
# ---------------------------------------------------------------------------

def _complex_fraction_package(rng, lesson_id, lesson_title, level):
    def simple(signed=True):
        den = rng.randint(2, 6)
        num = rng.randint(1, den + (2 if level > 1 else 0))
        value = Fraction(num, den)
        if signed and level > 1 and rng.random() < 0.4:
            value = -value
        return value

    numerator = simple()
    denominator = simple(signed=False)
    if level == 3:
        extra = Fraction(rng.randint(1, numerator.denominator - 1)
                         if numerator.denominator > 1 else 1,
                         numerator.denominator)
        numerator_display = (f"{core.plain_fraction_display(numerator)} + "
                             f"{core.plain_fraction_display(extra)}")
        numerator_value = numerator + extra
    else:
        numerator_display = core.plain_fraction_display(numerator)
        numerator_value = numerator
    if denominator == 0:
        raise DeterministicGenerationError("nulti imenilac dvojnog razlomka")
    answer = numerator_value / denominator
    display = (f"\\frac{{{numerator_display}}}"
               f"{{{core.plain_fraction_display(denominator)}}}")
    question = f"Izračunaj vrijednost dvojnog razlomka: ${display}$"
    answer_text = core.fraction_display(answer)
    flat = core.plain_fraction_display(answer)
    chain = (f"{display} = {core.plain_fraction_display(numerator_value)} : "
             f"{core.parenthesized(core.plain_fraction_display(denominator))} = {flat}")
    if answer_text != flat:
        chain += f" = {answer_text}"
    hint1 = ("Dvojni razlomak je dijeljenje: gornji razlomak se dijeli donjim, "
             "a dijeljenje razlomkom je množenje recipročnom vrijednošću.")
    hint2 = (f"Prepiši kao dijeljenje: "
             f"${core.plain_fraction_display(numerator_value)} : "
             f"{core.parenthesized(core.plain_fraction_display(denominator))}$.")
    hint3 = "Pomnoži gornji razlomak recipročnom vrijednošću donjeg, pa skrati."
    solution = (f"{hint1} Računamo: ${chain}$. Rezultat je ${answer_text}$.")
    candidates = [numerator_value * denominator, -answer,
                  denominator / numerator_value if numerator_value != 0 else answer + 1,
                  answer + 1, answer - 1,
                  answer + Fraction(1, answer.denominator if answer.denominator > 1 else 2)]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="rational_arithmetic_direct", operation="complex_fraction",
        level=level, question=question, answer_value=answer,
        answer_display=answer_text, distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("numerator", str(numerator_value)),
                              ("denominator", str(denominator))],
        required_conditions=["complex_fraction"],
        relevant_objects=["rational_signed"], generator_version=GENERATOR_VERSION,
        display_of=core.fraction_display)


def _family_for(domain):
    return {"natural": "natural_arithmetic_direct",
            "integer": "integer_arithmetic_direct",
            "decimal": "decimal_arithmetic_direct",
            "rational_signed": "rational_arithmetic_direct",
            "rational_nonneg": "rational_arithmetic_direct"}[domain]
