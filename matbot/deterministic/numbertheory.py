"""Deterministički generator porodica djeljivosti, NZD/NZS i prostih brojeva.

Tri semantičke porodice, jedan modul — sve tri rade nad ISTOM cjelobrojnom
teorijom brojeva i dijele konstrukciju opcija:

  • divisibility_predicate_application — primjena pravila djeljivosti na dat
    broj (tačno jedna opcija zadovoljava SVA navedena pravila); objavljeni MCQ
    nezavisno dokazuje postojeći uski orakl djeljivosti (mcq_integrity);
  • common_divisors_multiples — djelilac/sadržilac, zajednički djelilac i
    sadržilac, NZD i NZS;
  • prime_structure — prost/složen, relativno prosti parovi, rastavljanje na
    proste faktore.

MATEMATIČKI AUTORITET: cjelobrojna aritmetika (`int`, `math.gcd`/`lcm`).
Nijedan ID lekcije; razlike među lekcijama nose parametri ugovora
(`divisors`, `concepts`).

VAŽNO ZA DISTRAKTORE FAKTORIZACIJE: dvije opcije NIKAD ne smiju imati istu
brojevnu vrijednost (option_equivalence bi ih dokazao kao semantičke
duplikate), pa je „pogrešan zapis iste vrijednosti“ namjerno isključen kao
distraktor — koriste se faktorizacije DRUGE vrijednosti.
"""
import random
from math import gcd, lcm

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = (
    "divisibility_predicate_application",
    "common_divisors_multiples",
    "prime_structure",
    # Batch #2: djeljivost vrijednosti izraza i dekadske jedinice — ODVOJENA
    # porodica, jer njen prompt NE nameće formulaciju »djeljiv sa N«.
    "divisibility_value_properties",
)
GENERATOR_VERSION = "detnum-1"

# Isti zatvoren skup pravila kao uski orakl djeljivosti (mcq_integrity):
# generator ne smije obećati pravilo koje orakl ne umije nezavisno dokazati.
_SUPPORTED_DIVISORS = (2, 3, 4, 5, 6, 9, 10, 15, 25)

_SUPPORTED_CONCEPTS = frozenset({
    "divisor_membership", "multiple_membership", "gcd", "lcm",
    "prime_classification", "coprime_pairs", "prime_factorization",
    # Batch #2: djeljivost VRIJEDNOSTI izraza (zbir/razlika/proizvod) i
    # najveća dekadska jedinica koja dijeli broj.
    "expression_divisibility", "decade_unit_divisibility",
})

_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53,
           59, 61, 67, 71, 73, 79, 83, 89, 97]
# Složeni brojevi koji „izgledaju prosto“ (bez male očite cifre-djelitelja).
_TRICKY_COMPOSITES = [49, 51, 57, 63, 77, 87, 91, 93, 119, 121, 133, 143]


def _is_prime(value):
    if value < 2:
        return False
    factor = 2
    while factor * factor <= value:
        if value % factor == 0:
            return False
        factor += 1
    return True


def supports(parameters) -> bool:
    parameters = parameters or {}
    divisors = parameters.get("divisors")
    concepts = parameters.get("concepts")
    if divisors:
        return set(divisors) <= set(_SUPPORTED_DIVISORS) and not concepts
    if concepts:
        return bool(set(concepts)) and set(concepts) <= _SUPPORTED_CONCEPTS
    return False


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    common = dict(lesson_id=lesson_id, lesson_title=lesson_title)

    for _ in range(60):
        try:
            if parameters.get("divisors"):
                return _divisibility_package(
                    rng, level, tuple(int(d) for d in parameters["divisors"]),
                    **common)
            concept = rng.choice(tuple(parameters["concepts"]))
            builder = {
                "divisor_membership": _divisor_membership_package,
                "multiple_membership": _multiple_membership_package,
                "gcd": _gcd_package,
                "lcm": _lcm_package,
                "prime_classification": _prime_classification_package,
                "coprime_pairs": _coprime_package,
                "prime_factorization": _factorization_package,
                "expression_divisibility": _expression_divisibility_package,
                "decade_unit_divisibility": _decade_unit_package,
            }[concept]
            return builder(rng, level, **common)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


# ---------------------------------------------------------------------------
# PRIMJENA PRAVILA DJELJIVOSTI — nivo = broj pravila koja se istovremeno traže
# ---------------------------------------------------------------------------

def _incomparable_pairs(divisors):
    pairs = []
    for first in divisors:
        for second in divisors:
            if first < second and second % first and first % second:
                pairs.append((first, second))
    return pairs


def _divisibility_package(rng, level, divisors, lesson_id, lesson_title):
    if level == 1 or len(divisors) == 1:
        chosen = (rng.choice(divisors),)
    else:
        pairs = _incomparable_pairs(divisors)
        if not pairs:
            chosen = (rng.choice(divisors),)
        elif level == 2:
            chosen = rng.choice(pairs)
        else:
            triples = [(a, b, c) for (a, b) in pairs for c in divisors
                       if c > b and b % c and c % b and a % c and c % a]
            chosen = rng.choice(triples) if triples else rng.choice(pairs)
    base = lcm(*chosen) if len(chosen) > 1 else chosen[0]
    low, high = (10, 200) if level == 1 else (50, 900)
    correct = base * rng.randint(max(2, low // base), max(3, high // base))

    # Distraktori: broj koji pada TAČNO na jednom pravilu (ili na svim).
    options_pool = []
    for _ in range(400):
        candidate = rng.randint(low, high + 60)
        if all(candidate % d == 0 for d in chosen):
            continue
        options_pool.append(candidate)
        if len(chosen) > 1:
            partial = base // rng.choice(chosen)
            near = partial * rng.randint(2, max(3, high // max(partial, 1)))
            if near > 0 and not all(near % d == 0 for d in chosen):
                options_pool.append(near)
        if len(options_pool) > 24:
            break
    distinct = []
    for candidate in options_pool:
        if candidate != correct and candidate not in distinct:
            distinct.append(candidate)
    if len(distinct) < 3:
        raise DeterministicGenerationError("nedovoljno kandidata")
    distractors = distinct[:3]

    condition = " i ".join(f"sa ${d}$" for d in chosen)
    question = f"Koji od ponuđenih brojeva je djeljiv {condition}?"
    rules = ", ".join(str(d) for d in chosen)
    hint1 = ("Primijeni pravilo djeljivosti za svaki navedeni broj — gledaj "
             "posljednju cifru, zbir cifara ili posljednje dvije cifre.")
    hint2 = (f"Provjeri svaku opciju redom: broj mora zadovoljiti pravilo "
             f"djeljivosti sa {rules}"
             + (" — svako od navedenih pravila istovremeno." if len(chosen) > 1
                else "."))
    hint3 = (f"Samo jedan od ponuđenih brojeva prolazi "
             + ("sva navedena pravila" if len(chosen) > 1 else "navedeno pravilo")
             + " — izračunaj ostatke i uporedi.")
    checks = " i ".join(f"${correct} : {d} = {correct // d}$" for d in chosen)
    solution = (f"Broj ${correct}$ je djeljiv {condition}: {checks}, "
                f"bez ostatka. Ostali ponuđeni brojevi ne zadovoljavaju "
                + ("sva navedena pravila." if len(chosen) > 1
                   else "navedeno pravilo."))
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="divisibility_predicate_application",
        operation="divisibility_predicate", level=level, question=question,
        answer_value=correct, answer_display=str(correct),
        distractor_values=distractors, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("correct", str(correct)),
                              ("divisors", "+".join(str(d) for d in chosen))],
        required_conditions=[f"divisible_by_{d}" for d in chosen],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value))


# ---------------------------------------------------------------------------
# DJELILAC / SADRŽILAC / NZD / NZS
# ---------------------------------------------------------------------------

def _proper_divisors(value):
    return [d for d in range(2, value) if value % d == 0]


def _divisor_membership_package(rng, level, lesson_id, lesson_title):
    if level == 1:
        target = rng.choice([n for n in range(12, 60)
                             if len(_proper_divisors(n)) >= 2])
        divisors = _proper_divisors(target)
        correct = rng.choice(divisors)
        pool = [n for n in range(2, target + 15)
                if target % n != 0 and n != correct]
        question = f"Koji od ponuđenih brojeva je djelilac broja ${target}$?"
        subject = f"broja ${target}$"
        witness = f"${target} : {correct} = {target // correct}$"
    else:
        for _ in range(300):
            a = rng.randint(12, 60 if level == 2 else 96)
            b = rng.randint(12, 60 if level == 2 else 96)
            common = [d for d in range(2, min(a, b) + 1)
                      if a % d == 0 and b % d == 0]
            if a != b and common:
                break
        else:
            raise DeterministicGenerationError("nema zajedničkog djelioca")
        target = (a, b)
        correct = rng.choice(common)
        pool = [n for n in range(2, min(a, b) + 10)
                if (a % n != 0 or b % n != 0) and n != correct]
        question = (f"Koji od ponuđenih brojeva je zajednički djelilac "
                    f"brojeva ${a}$ i ${b}$?")
        subject = f"brojeva ${a}$ i ${b}$"
        witness = f"${a} : {correct} = {a // correct}$ i ${b} : {correct} = {b // correct}$"
    rng.shuffle(pool)
    distractors = pool[:3]
    if len(distractors) < 3:
        raise DeterministicGenerationError("nedovoljno kandidata")
    hint1 = "Djelilac dijeli broj bez ostatka — provjeri dijeljenjem svaku opciju."
    hint2 = f"Podijeli {subject} svakom od ponuđenih opcija i traži ostatak nula."
    hint3 = "Samo jedna opcija daje ostatak nula pri svakom traženom dijeljenju."
    solution = (f"Broj ${correct}$ je djelilac: {witness}, bez ostatka. "
                "Ostale opcije ostavljaju ostatak.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="common_divisors_multiples", operation="divisor_membership",
        level=level, question=question, answer_value=correct,
        answer_display=str(correct), distractor_values=distractors,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("target", str(target)), ("correct", str(correct))],
        required_conditions=["divisor_membership"],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value))


def _multiple_membership_package(rng, level, lesson_id, lesson_title):
    if level == 1:
        base = rng.randint(3, 12)
        correct = base * rng.randint(3, 12)
        pool = [n for n in range(base + 1, base * 14)
                if n % base != 0]
        question = f"Koji od ponuđenih brojeva je sadržilac broja ${base}$?"
        witness = f"${correct} = {base} \\cdot {correct // base}$"
        subject = (base,)
    else:
        for _ in range(200):
            a = rng.randint(3, 9 if level == 2 else 12)
            b = rng.randint(3, 9 if level == 2 else 12)
            if a != b and a % b and b % a:
                break
        else:
            raise DeterministicGenerationError("nema para")
        base = lcm(a, b)
        correct = base * rng.randint(1, 3 if level == 2 else 4)
        pool = [n for n in range(min(a, b) + 1, base * 4 + 10)
                if n % base != 0]
        question = (f"Koji od ponuđenih brojeva je zajednički sadržilac "
                    f"brojeva ${a}$ i ${b}$?")
        witness = (f"${correct} = {a} \\cdot {correct // a}$ i "
                   f"${correct} = {b} \\cdot {correct // b}$")
        subject = (a, b)
    rng.shuffle(pool)
    distractors = pool[:3]
    if len(distractors) < 3:
        raise DeterministicGenerationError("nedovoljno kandidata")
    hint1 = ("Sadržilac je broj koji sadrži dati broj cio broj puta — "
             "dobija se množenjem datog broja prirodnim brojem.")
    hint2 = "Provjeri za svaku opciju: da li je djeljiva svakim od datih brojeva?"
    hint3 = "Samo jedna opcija je djeljiva svim datim brojevima bez ostatka."
    solution = (f"Broj ${correct}$ jeste sadržilac: {witness}. "
                "Ostale opcije nisu djeljive bez ostatka.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="common_divisors_multiples", operation="multiple_membership",
        level=level, question=question, answer_value=correct,
        answer_display=str(correct), distractor_values=distractors,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("base", "+".join(str(s) for s in subject)),
                              ("correct", str(correct))],
        required_conditions=["multiple_membership"],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value))


def _gcd_pair(rng, level):
    if level == 1:
        target_gcd = rng.choice((2, 3, 4, 5, 6))
        m, n = rng.sample(range(2, 8), 2)
    elif level == 2:
        target_gcd = rng.choice((4, 6, 8, 9, 12))
        m, n = rng.sample(range(2, 10), 2)
    else:
        target_gcd = rng.choice((6, 8, 12, 15, 18))
        m, n = rng.sample(range(3, 12), 2)
    if gcd(m, n) != 1:
        raise DeterministicGenerationError("faktori nisu uzajamno prosti")
    return target_gcd * m, target_gcd * n, target_gcd


def _gcd_package(rng, level, lesson_id, lesson_title):
    a, b, answer = _gcd_pair(rng, level)
    third = ""
    values = (a, b)
    if level == 3 and rng.random() < 0.5:
        extra_factor = rng.randint(2, 6)
        c = answer * extra_factor
        if gcd(a // answer, extra_factor) == 1 and gcd(b // answer, extra_factor) == 1:
            values = (a, b, c)
    numbers = " i ".join(f"${v}$" for v in values) if len(values) == 2 else \
        f"${values[0]}$, ${values[1]}$ i ${values[2]}$"
    question = f"Koliki je najveći zajednički djelilac brojeva {numbers}?"
    candidates = [answer * 2, answer // 2 if answer % 2 == 0 else answer * 3,
                  lcm(a, b), min(values), answer + 1, answer - 1, 1]
    distractors = [v for v in candidates if v and v > 0]
    hint1 = ("Rastavi svaki broj na proste faktore — najveći zajednički "
             "djelilac čine zajednički faktori.")
    hint2 = f"Nabroji djelioce manjeg broja pa provjeri koji od njih dijele i ostale brojeve."
    hint3 = "Najveći broj koji bez ostatka dijeli sve date brojeve je traženi rezultat."
    checks = " i ".join(f"${v} : {answer} = {v // answer}$" for v in values)
    solution = (f"Najveći zajednički djelilac je ${answer}$: {checks}, "
                "bez ostatka, a nijedan veći broj ne dijeli sve date brojeve.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="common_divisors_multiples", operation="gcd", level=level,
        question=question, answer_value=answer, answer_display=str(answer),
        distractor_values=distractors, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("numbers", "+".join(str(v) for v in values))],
        required_conditions=["gcd"], relevant_objects=["natural"],
        generator_version=GENERATOR_VERSION, display_of=lambda value: str(value))


def _lcm_package(rng, level, lesson_id, lesson_title):
    for _ in range(200):
        if level == 1:
            a, b = rng.randint(2, 9), rng.randint(2, 9)
        elif level == 2:
            a, b = rng.randint(4, 15), rng.randint(4, 15)
        else:
            a, b = rng.randint(6, 20), rng.randint(6, 20)
        if a != b and gcd(a, b) > 1:
            break
    else:
        raise DeterministicGenerationError("nema para")
    values = (a, b)
    answer = lcm(a, b)
    if level == 3 and rng.random() < 0.5:
        c = rng.randint(4, 15)
        if c not in (a, b):
            values = (a, b, c)
            answer = lcm(a, b, c)
    numbers = " i ".join(f"${v}$" for v in values) if len(values) == 2 else \
        f"${values[0]}$, ${values[1]}$ i ${values[2]}$"
    question = f"Koliki je najmanji zajednički sadržilac brojeva {numbers}?"
    product = 1
    for v in values:
        product *= v
    candidates = [product, answer * 2, max(values), sum(values), gcd(*values),
                  answer + min(values)]
    distractors = [v for v in candidates if v > 0]
    hint1 = ("Najmanji zajednički sadržilac je najmanji broj djeljiv svim "
             "datim brojevima — rastavi brojeve na proste faktore.")
    hint2 = f"Ispisuj sadržioce najvećeg od datih brojeva dok ne nađeš prvi djeljiv i ostalima."
    hint3 = "Provjeri za kandidata: mora biti djeljiv SVAKIM od datih brojeva, i to najmanji takav."
    checks = " i ".join(f"${answer} : {v} = {answer // v}$" for v in values)
    solution = (f"Najmanji zajednički sadržilac je ${answer}$: {checks}, "
                "bez ostatka, a nijedan manji broj nije djeljiv svim datim brojevima.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="common_divisors_multiples", operation="lcm", level=level,
        question=question, answer_value=answer, answer_display=str(answer),
        distractor_values=distractors, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("numbers", "+".join(str(v) for v in values))],
        required_conditions=["lcm"], relevant_objects=["natural"],
        generator_version=GENERATOR_VERSION, display_of=lambda value: str(value))


# ---------------------------------------------------------------------------
# PROSTI BROJEVI
# ---------------------------------------------------------------------------

def _prime_classification_package(rng, level, lesson_id, lesson_title):
    ask_prime = level == 1 or rng.random() < 0.5
    if level == 1:
        primes = [p for p in _PRIMES if p < 50]
        composites = [n for n in range(4, 50) if not _is_prime(n)]
    else:
        primes = [p for p in _PRIMES if 20 < p < 100]
        composites = _TRICKY_COMPOSITES + [n for n in range(50, 100)
                                           if not _is_prime(n)]
    if ask_prime:
        correct = rng.choice(primes)
        pool = composites if level == 1 else _TRICKY_COMPOSITES
        distractors = rng.sample([c for c in pool if c != correct], 6)
        question = "Koji od ponuđenih brojeva je prost?"
        explain = (f"Broj ${correct}$ nije djeljiv nijednim brojem osim $1$ i "
                   f"samog sebe, pa je prost.")
    else:
        pool = _TRICKY_COMPOSITES if level >= 2 else composites
        correct = rng.choice(pool)
        distractors = rng.sample([p for p in primes if p != correct], 6)
        factor = next(d for d in range(2, correct) if correct % d == 0)
        question = "Koji od ponuđenih brojeva je složen?"
        explain = (f"Broj ${correct}$ je složen: "
                   f"${correct} = {factor} \\cdot {correct // factor}$. "
                   "Ostali ponuđeni brojevi su prosti.")
    hint1 = ("Prost broj ima tačno dva djelioca: jedinicu i samog sebe; "
             "složen broj ima i neki treći djelilac.")
    hint2 = ("Provjeri redom djeljivost malim prostim brojevima: "
             "$2$, $3$, $5$, $7$, $11$, $13$.")
    hint3 = ("Pazi na brojeve koji „izgledaju prosto“ — provjeri i djeljivost "
             "sa $7$, $11$ i $13$ prije zaključka.")
    solution = explain
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="prime_structure", operation="prime_classification",
        level=level, question=question, answer_value=correct,
        answer_display=str(correct), distractor_values=distractors,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("correct", str(correct)),
                              ("ask", "prime" if ask_prime else "composite")],
        required_conditions=["prime_classification"],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value))


def _coprime_package(rng, level, lesson_id, lesson_title):
    high = 20 if level == 1 else (40 if level == 2 else 60)
    for _ in range(400):
        a, b = rng.randint(4, high), rng.randint(4, high)
        if a != b and gcd(a, b) == 1 and not (_is_prime(a) and _is_prime(b)):
            break
    else:
        raise DeterministicGenerationError("nema relativno prostog para")
    wrong = []
    for _ in range(400):
        c, d = rng.randint(4, high), rng.randint(4, high)
        if c != d and gcd(c, d) > 1:
            pair = (min(c, d), max(c, d))
            if pair not in wrong:
                wrong.append(pair)
        if len(wrong) == 3:
            break
    if len(wrong) < 3:
        raise DeterministicGenerationError("nedovoljno parova")
    correct_text = f"${min(a, b)}$ i ${max(a, b)}$"
    option_texts = (correct_text,
                    *(f"${x}$ i ${y}$" for x, y in wrong))
    question = "Koji par brojeva je relativno prost?"
    hint1 = ("Dva broja su relativno prosta kad im je najveći zajednički "
             "djelilac jednak $1$.")
    hint2 = "Za svaki par potraži zajednički djelilac veći od $1$."
    hint3 = ("Tri para imaju zajednički djelilac veći od $1$ — samo jedan par "
             "nema nijedan zajednički djelilac osim jedinice.")
    shared = [f"brojeve ${x}$ i ${y}$ dijeli ${gcd(x, y)}$" for x, y in wrong]
    solution = (f"Najveći zajednički djelilac brojeva ${min(a, b)}$ i "
                f"${max(a, b)}$ je $1$, pa su relativno prosti. "
                f"Ostali parovi nisu: {'; '.join(shared)}.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="prime_structure", operation="coprime_pairs", level=level,
        question=question, answer_value=(a, b), answer_display=correct_text,
        distractor_values=(), hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("pair", f"{min(a, b)}+{max(a, b)}")],
        required_conditions=["coprime_pairs"], relevant_objects=["natural"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="", accepted_answers=(f"{min(a, b)} i {max(a, b)}",))


def _factorization(value):
    factors = {}
    remaining = value
    for prime in _PRIMES:
        while remaining % prime == 0:
            factors[prime] = factors.get(prime, 0) + 1
            remaining //= prime
        if remaining == 1:
            break
    if remaining != 1:
        raise DeterministicGenerationError("faktorizacija van skupa prostih")
    return factors


def _factor_display(factors):
    parts = []
    for prime in sorted(factors):
        exponent = factors[prime]
        parts.append(f"{prime}^{{{exponent}}}" if exponent > 1 else str(prime))
    return " \\cdot ".join(parts)


def _factor_value(factors):
    value = 1
    for prime, exponent in factors.items():
        value *= prime ** exponent
    return value


def _factorization_package(rng, level, lesson_id, lesson_title):
    if level == 1:
        primes, max_exp, count = (2, 3, 5), 2, 2
    elif level == 2:
        primes, max_exp, count = (2, 3, 5, 7), 2, 2
    else:
        primes, max_exp, count = (2, 3, 5, 7), 2, 3
    chosen = rng.sample(primes, count)
    factors = {p: rng.randint(1, max_exp) for p in chosen}
    value = _factor_value(factors)
    if not 12 <= value <= (100 if level == 1 else 400):
        raise DeterministicGenerationError("vrijednost van opsega")
    display = _factor_display(factors)

    # Distraktori: faktorizacije DRUGE vrijednosti (tipične greške u eksponentu
    # ili faktoru) — nikad drugi zapis iste vrijednosti.
    variants = []
    for prime in list(factors):
        changed = dict(factors)
        changed[prime] = factors[prime] + 1
        variants.append(changed)
        if factors[prime] > 1:
            changed = dict(factors)
            changed[prime] = factors[prime] - 1
            variants.append(changed)
    for replacement in primes:
        if replacement not in factors:
            changed = dict(factors)
            first = sorted(factors)[0]
            changed.pop(first)
            changed[replacement] = factors[first]
            variants.append(changed)
    option_values, option_displays = [value], [f"${display}$"]
    for variant in variants:
        variant_value = _factor_value(variant)
        variant_display = f"${_factor_display(variant)}$"
        if variant_value == value or variant_value in option_values:
            continue
        if variant_display in option_displays:
            continue
        option_values.append(variant_value)
        option_displays.append(variant_display)
        if len(option_displays) == 4:
            break
    if len(option_displays) < 4:
        raise DeterministicGenerationError("nedovoljno faktorizacija")

    question = f"Kako glasi rastavljanje broja ${value}$ na proste faktore?"
    hint1 = ("Dijeli broj redom najmanjim prostim brojevima: prvo $2$, zatim "
             "$3$, $5$, $7$ — dok ne ostane $1$.")
    first_prime = min(factors)
    hint2 = (f"Počni: ${value} : {first_prime} = {value // first_prime}$, "
             "pa nastavi dijeliti prostim brojevima.")
    hint3 = ("Kad ispišeš sve proste djelioce, grupiši jednake faktore u "
             "stepene i provjeri množenjem.")
    solution = (f"Redom dijelimo prostim brojevima: ${value} = {display}$. "
                "Provjera množenjem vraća polazni broj.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="prime_structure", operation="prime_factorization",
        level=level, question=question, answer_value=value,
        answer_display=display, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("value", str(value))],
        required_conditions=["prime_factorization"],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        option_texts=tuple(option_displays), wrap="")


# ---------------------------------------------------------------------------
# DJELJIVOST VRIJEDNOSTI IZRAZA (zbir/razlika/proizvod) — Batch #2
# ---------------------------------------------------------------------------
# Riječ „djeljiv“ u pitanju je bezbjedna: uski orakl djeljivosti angažuje se
# samo kad su SVE opcije goli cijeli brojevi, a ovdje su opcije izrazi.

def _expression_divisibility_package(rng, level, lesson_id, lesson_title):
    divisor = rng.choice((2, 3, 4, 5, 6, 9, 10))
    symbol_by_kind = {"sum": "+", "difference": "-", "product": "\cdot"}
    kinds = ("sum",) if level == 1 else tuple(symbol_by_kind)

    def expression(divisible):
        kind = rng.choice(kinds)
        for _ in range(300):
            if kind == "product":
                a = rng.randint(2, 12)
                b = rng.randint(2, 12)
                value = a * b
            else:
                a = rng.randint(6, 60 if level < 3 else 200)
                b = rng.randint(2, a - 1)
                value = a + b if kind == "sum" else a - b
            if value <= 0:
                continue
            if (value % divisor == 0) == divisible:
                label = f"{a} {symbol_by_kind[kind]} {b}"
                return label, value, kind
        raise DeterministicGenerationError("nema izraza tražene djeljivosti")

    correct_label, correct_value, correct_kind = expression(True)
    wrong = []
    seen_values = {correct_value}
    for _ in range(200):
        label, value, _kind = expression(False)
        if value in seen_values:
            continue
        seen_values.add(value)
        wrong.append(label)
        if len(wrong) == 3:
            break
    if len(wrong) < 3:
        raise DeterministicGenerationError("nedovoljno izraza")

    option_texts = (f"${correct_label}$", *(f"${label}$" for label in wrong))
    question = (f"Vrijednost kojeg od ponuđenih izraza je djeljiva "
                f"sa ${divisor}$?")
    kind_word = {"sum": "zbir", "difference": "razlika", "product": "proizvod"}
    hint1 = ("Prvo izračunaj vrijednost svakog izraza, pa provjeri pravilo "
             "djeljivosti na rezultatu.")
    hint2 = (f"Za djeljivost sa ${divisor}$ primijeni odgovarajuće pravilo "
             "na IZRAČUNATU vrijednost, ne na pojedinačne članove.")
    hint3 = ("Samo jedna vrijednost prolazi pravilo — kod proizvoda je "
             "dovoljno da jedan faktor bude djeljiv datim brojem.")
    solution = (f"Vrijednost izraza ${correct_label}$ je ${correct_value}$: "
                f"${correct_value} : {divisor} = {correct_value // divisor}$, "
                f"bez ostatka — taj {kind_word[correct_kind]} je djeljiv sa "
                f"${divisor}$. Vrijednosti ostalih izraza nisu.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="divisibility_value_properties",
        operation="expression_divisibility", level=level, question=question,
        answer_value=correct_value, answer_display=correct_label,
        distractor_values=(), hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("expression", correct_label),
                              ("divisor", str(divisor))],
        required_conditions=["expression_divisibility"],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


# ---------------------------------------------------------------------------
# NAJVEĆA DEKADSKA JEDINICA — Batch #2
# ---------------------------------------------------------------------------
# Formulacija NAMJERNO izbjegava „djeljiv sa N“: uski orakl bi „sa 100“
# pročitao kao nepodržan djelilac i oborio ispravan paket kao nedokaziv uslov.

def _decade_unit_package(rng, level, lesson_id, lesson_title):
    exponent = rng.randint(1, 2 if level == 1 else (3 if level == 2 else 4))
    core_value = rng.randint(2, 90 if level < 3 else 900)
    if core_value % 10 == 0:
        core_value += rng.randint(1, 9)
    number = core_value * 10 ** exponent
    decade = 10 ** exponent
    units = [10, 100, 1000, 10000]
    option_values = [decade] + [u for u in units if u != decade][:3]
    option_texts = tuple(f"${value}$" for value in option_values)
    question = (f"Koja je najveća dekadska jedinica kojom se broj ${number}$ "
                "može podijeliti bez ostatka?")
    # Bez nabrajanja brojki: svaka opcija je dekadska jedinica, pa bi
    # nabrojane vrijednosti doslovno sadržavale i tačan odgovor.
    hint1 = ("Dekadske jedinice su desetica, stotica, hiljada... — broj se "
             "njima dijeli bez ostatka prema broju nula na svom kraju.")
    hint2 = f"Prebroji nule na kraju broja ${number}$."
    hint3 = (f"Broj ${number}$ završava tačno sa {exponent} "
             f"{'nulom' if exponent == 1 else 'nule' if exponent < 5 else 'nula'}"
             " — toliko nula ima i tražena dekadska jedinica.")
    solution = (f"Broj ${number}$ se može zapisati kao "
                f"${number} = {core_value} \cdot {decade}$, a ${core_value}$ "
                f"se ne završava nulom — najveća dekadska jedinica je ${decade}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="divisibility_value_properties",
        operation="decade_unit_divisibility", level=level, question=question,
        answer_value=decade, answer_display=str(decade),
        distractor_values=(), hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("number", str(number))],
        required_conditions=["decade_unit"],
        relevant_objects=["natural"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")
