"""Deterministički generator porodice razmjere i proporcije.

Jedna semantička porodica (`ratio_proportion_direct`) s konceptima:

  • ratio_simplification      — razmjera u najjednostavnijem obliku;
  • proportion_property       — prepoznavanje TAČNE proporcije (jednaki
                                unakrsni proizvodi);
  • missing_term              — nepoznati član proporcije a : b = c : x;
  • proportionality_recognition — direktna/obrnuta proporcionalnost iz
                                tabele parova;
  • proportional_division     — podjela veličine u datoj razmjeri.

MATEMATIČKI AUTORITET: egzaktna racionalna aritmetika. Kod skraćivanja
razmjere distraktori su razmjere DRUGOG količnika — dvije razmjere istog
količnika (npr. 2:3 i 4:6) bile bi dokazani semantički duplikat opcija, jer
se `a : b` numerički čita kao dijeljenje. Tabele parova u prepoznavanju
proporcionalnosti pišu se PROZNO (x: 2, 3, 5; y: 6, 9, 15), bez `$...$`,
da nijedan numerički parser ne pokuša čitati listu kao izraz.
"""
import random
from fractions import Fraction
from math import gcd

from matbot import proportion_arrows
from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("ratio_proportion_direct",)
GENERATOR_VERSION = "detratio-1"

_SUPPORTED_CONCEPTS = frozenset({
    "ratio_simplification", "proportion_property", "missing_term",
    "proportionality_recognition", "proportional_division",
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
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            builder = {
                "ratio_simplification": _simplify_package,
                "proportion_property": _property_package,
                "missing_term": _missing_term_package,
                "proportionality_recognition": _recognition_package,
                "proportional_division": _division_package,
            }[concept]
            return builder(rng, level, lesson_id, lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _ratio_text(a, b):
    return f"{a} : {b}"


def _simplify_package(rng, level, lesson_id, lesson_title):
    p = rng.randint(1, 9)
    q = rng.randint(1, 9)
    if gcd(p, q) != 1 or p == q:
        raise DeterministicGenerationError("osnova nije nesvodiva")
    factor = rng.randint(2, 6 if level == 1 else 12)
    a, b = p * factor, q * factor
    question = (f"Zapiši razmjeru ${_ratio_text(a, b)}$ u najjednostavnijem "
                "obliku.")
    correct_value = Fraction(p, q)
    # Distraktori DRUGOG količnika (isti količnik = dokazani duplikat).
    wrong_pairs = [(q, p), (p + 1, q), (p, q + 1), (p * 2, q), (p, q * 2)]
    option_texts = [f"${_ratio_text(p, q)}$"]
    seen_values = {correct_value}
    for wa, wb in wrong_pairs:
        if wb == 0:
            continue
        value = Fraction(wa, wb)
        if value in seen_values:
            continue
        seen_values.add(value)
        option_texts.append(f"${_ratio_text(wa, wb)}$")
        if len(option_texts) == 4:
            break
    if len(option_texts) != 4:
        raise DeterministicGenerationError("nedovoljno razmjera")
    hint1 = ("Razmjera se skraćuje kao razlomak: podijeli oba člana njihovim "
             "najvećim zajedničkim djeliocem.")
    hint2 = f"Najveći zajednički djelilac brojeva ${a}$ i ${b}$ je ${factor * gcd(p, q)}$."
    hint3 = f"Podijeli: ${a} : {factor} = {p}$ i ${b} : {factor} = {q}$."
    solution = (f"Oba člana dijelimo sa ${factor}$: "
                f"${a} : {factor} = {p}$ i ${b} : {factor} = {q}$, pa je "
                f"najjednostavniji oblik ${_ratio_text(p, q)}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="ratio_proportion_direct", operation="ratio_simplification",
        level=level, question=question, answer_value=correct_value,
        answer_display=_ratio_text(p, q), distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("ratio", f"{a}:{b}")],
        required_conditions=["ratio_simplification"],
        relevant_objects=["ratio"], generator_version=GENERATOR_VERSION,
        option_texts=tuple(option_texts), wrap="")


def _property_package(rng, level, lesson_id, lesson_title):
    a = rng.randint(2, 6 if level == 1 else 9)
    b = rng.randint(2, 6 if level == 1 else 9)
    factor = rng.randint(2, 4 if level < 3 else 6)
    c, d = a * factor, b * factor
    correct = f"{a} : {b} = {c} : {d}"
    correct_products = (a * d, b * c)

    # Količnici desnih strana moraju biti MEĐUSOBNO različiti: opcije
    # „17 : 17“ i „16 : 16“ su različiti stringovi, a ista vrijednost (1) —
    # dokazani semantički duplikat (živi nalaz smoke kampanje ove porodice).
    used_quotients = {Fraction(c, d)}

    def broken():
        for _ in range(200):
            da = rng.randint(1, 3)
            db = rng.randint(0, 3)
            wc, wd = c + da, d + db
            quotient = Fraction(wc, wd)
            if a * wd != b * wc and quotient not in used_quotients:
                used_quotients.add(quotient)
                return f"{a} : {b} = {wc} : {wd}"
        raise DeterministicGenerationError("nema pokvarene proporcije")

    wrong = []
    while len(wrong) < 3:
        candidate = broken()
        if candidate not in wrong and candidate != correct:
            wrong.append(candidate)
    option_texts = (f"${correct}$", *(f"${w}$" for w in wrong))
    question = "Koja od ponuđenih jednakosti je TAČNA proporcija?"
    hint1 = ("Osnovno svojstvo proporcije: proizvod spoljašnjih članova "
             "jednak je proizvodu unutrašnjih.")
    hint2 = "Za svaku jednakost pomnoži unakrsno pa uporedi proizvode."
    hint3 = "Samo jedna jednakost ima jednake unakrsne proizvode."
    solution = (f"Provjera unakrsnih proizvoda: ${a} \\cdot {d} = "
                f"{correct_products[0]}$ i ${b} \\cdot {c} = "
                f"{correct_products[1]}$ — jednaki su, pa je ${correct}$ "
                "tačna proporcija. Kod ostalih se proizvodi razlikuju.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="ratio_proportion_direct", operation="proportion_property",
        level=level, question=question, answer_value=correct,
        answer_display=correct, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("proportion", correct)],
        required_conditions=["proportion_property"],
        relevant_objects=["ratio"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _missing_term_package(rng, level, lesson_id, lesson_title):
    a = rng.randint(2, 9)
    b = rng.randint(2, 9)
    if a == b:
        raise DeterministicGenerationError("trivijalna razmjera")
    factor = rng.randint(2, 6 if level < 3 else 12)
    c = a * factor
    x = b * factor
    question = (f"Odredi nepoznati član proporcije: "
                f"${a} : {b} = {c} : x$")
    chain = (f"x = {b} \\cdot {c} : {a} = {b * c} : {a} = {x}")
    hint1 = ("Iskoristi osnovno svojstvo proporcije: proizvod spoljašnjih "
             "jednak je proizvodu unutrašnjih članova.")
    hint2 = f"Zapiši: ${a} \\cdot x = {b} \\cdot {c}$."
    hint3 = f"Izrazi: $x = {b * c} : {a}$."
    solution = (f"Iz ${a} \\cdot x = {b} \\cdot {c}$ slijedi ${chain}$. "
                f"Provjera: ${a} \\cdot {x} = {a * x}$ i "
                f"${b} \\cdot {c} = {b * c}$ — jednako.")
    answer = Fraction(x)
    candidates = [Fraction(a * factor), Fraction(x + b), Fraction(x - b),
                  Fraction(a * c, b) if (a * c) % b == 0 else answer + 1,
                  answer + factor]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="ratio_proportion_direct", operation="missing_term",
        level=level, question=question, answer_value=answer,
        answer_display=str(x), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("proportion", f"{a}:{b}={c}:x")],
        required_conditions=["missing_term"], relevant_objects=["ratio"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator))


def _table_text(xs, ys):
    x_list = ", ".join(str(v) for v in xs)
    y_list = ", ".join(str(v) for v in ys)
    return f"x: {x_list}; y: {y_list}"


def _recognition_package(rng, level, lesson_id, lesson_title):
    ask_direct = rng.random() < 0.5
    xs = sorted(rng.sample(range(1, 9), 3))
    k = rng.randint(2, 6)
    direct_table = _table_text(xs, [k * x for x in xs])
    base = xs[0] * xs[1] * xs[2] * rng.choice((1, 2))
    inverse_xs = [x for x in xs if base % x == 0][:3]
    while len(inverse_xs) < 3:
        candidate = rng.randint(1, 12)
        if base % candidate == 0 and candidate not in inverse_xs:
            inverse_xs.append(candidate)
    inverse_xs = sorted(inverse_xs)
    inverse_table = _table_text(inverse_xs, [base // x for x in inverse_xs])
    neither_table = _table_text(xs, [k * x + rng.randint(1, 3) for x in xs])
    neither_table2 = _table_text(xs, [x * x for x in xs])

    # Odluka F (audit ovlašćenja pravila): školska metoda strelica — smjer
    # promjene veličina se imenuje PRIJE računa; količnik/proizvod je zatim
    # dokazna provjera. Rečenice dolaze iz matbot/proportion_arrows.py (ista
    # formulacija koju rules.py renderuje u oba prompta).
    if ask_direct:
        correct, kind_word = direct_table, "direktno proporcionalne"
        wrong = [inverse_table, neither_table, neither_table2]
        rule = (proportion_arrows.direction_sentence(
                    proportion_arrows.KIND_DIRECT)
                + " Provjera: KOLIČNIK y : x je stalan za svaki par.")
        witness = (proportion_arrows.direction_sentence(
                       proportion_arrows.KIND_DIRECT)
                   + f" Količnici su redom "
                   + ", ".join(f"${k * x} : {x} = {k}$" for x in xs)
                   + " — stalno " + f"${k}$")
    else:
        correct, kind_word = inverse_table, "obrnuto proporcionalne"
        wrong = [direct_table, neither_table, neither_table2]
        rule = (proportion_arrows.direction_sentence(
                    proportion_arrows.KIND_INVERSE)
                + " Provjera: PROIZVOD x·y je stalan za svaki par.")
        witness = (proportion_arrows.direction_sentence(
                       proportion_arrows.KIND_INVERSE)
                   + f" Proizvodi su redom "
                   + ", ".join(f"${x} \\cdot {base // x} = {base}$"
                               for x in inverse_xs)
                   + " — stalno " + f"${base}$")
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("tabele nisu jedinstvene")
    question = (f"U kojoj su tabeli veličine x i y {kind_word}?")
    hint1 = rule
    hint2 = ("Za svaku tabelu izračunaj količnike y : x i proizvode x·y — "
             "traži onu u kojoj je odgovarajuća vrijednost stalna.")
    hint3 = "Dovoljan je jedan par koji odstupa da tabela otpadne."
    solution = (f"U tabeli ({correct}): kad prva veličina raste, druga "
                f"{'raste' if ask_direct else 'opada'}. {witness}. Veličine "
                f"su zato {kind_word}; u ostalim tabelama provjerena "
                "vrijednost nije stalna.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="ratio_proportion_direct",
        operation="proportionality_recognition", level=level,
        question=question, answer_value=correct, answer_display=correct,
        distractor_values=(), hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("table", correct), ("kind", kind_word)],
        required_conditions=["proportionality_recognition"],
        relevant_objects=["ratio"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _division_package(rng, level, lesson_id, lesson_title):
    p = rng.randint(1, 5)
    q = rng.randint(p + 1, 9)
    if gcd(p, q) != 1:
        raise DeterministicGenerationError("razmjera nije nesvodiva")
    unit = rng.randint(2, 9 if level < 3 else 20)
    total = (p + q) * unit
    larger = q * unit
    smaller = p * unit
    question = (f"Duž dužine ${total}$ cm podijeljena je u razmjeri "
                f"${p} : {q}$. Kolika je dužina VEĆEG dijela?")
    hint1 = ("Kod podjele u razmjeri prvo saberi članove razmjere — toliko "
             "jednakih dijelova ima cjelina.")
    hint2 = (f"Zbir članova je ${p} + {q} = {p + q}$, pa je jedan dio "
             f"${total} : {p + q} = {unit}$ cm.")
    hint3 = f"Veći dio ima ${q}$ takvih dijelova."
    solution = (f"Jedan dio iznosi ${total} : {p + q} = {unit}$ cm, pa je "
                f"veći dio ${q} \\cdot {unit} = {larger}$ cm, a manji "
                f"${p} \\cdot {unit} = {smaller}$ cm. Provjera: "
                f"${larger} + {smaller} = {total}$.")
    answer = Fraction(larger)
    candidates = [Fraction(smaller), Fraction(unit), Fraction(total - unit),
                  Fraction(larger + unit), Fraction(larger - unit)]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="ratio_proportion_direct", operation="proportional_division",
        level=level, question=question, answer_value=answer,
        answer_display=str(larger), distractor_values=candidates,
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("total", str(total)), ("ratio", f"{p}:{q}")],
        required_conditions=["proportional_division"],
        relevant_objects=["ratio"], generator_version=GENERATOR_VERSION,
        display_of=lambda value: str(value.numerator))
