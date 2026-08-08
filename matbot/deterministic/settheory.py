"""Deterministički generator skupovne algebre i pripadnosti brojevnim skupovima.

Dvije semantičke porodice (Batch #4, Prioritet 3):

  • ``finite_set_direct``      — pripadnost, zadavanje skupa, brojnost,
    podskup/jednakost, unija, presjek, razlika, komplement (uz izričit
    univerzum), uređeni par i Dekartov proizvod;
  • ``number_set_membership``  — N/N0, klasifikacija u N/Z/Q/R,
    prepoznavanje iracionalnog broja i položaj korijena među uzastopnim
    cijelim brojevima.

MATEMATIČKI AUTORITET: ``matbot/mathkernel/finiteset.py`` — skup je
``frozenset``, pa {1,2,3} i {3,2,1} JESU isti objekat i jedinstvenost opcija
je skupovna, nikad tekstualna. Skupovi se prikazuju PROZNO (A = {2, 4, 6}),
bez ``$...$`` — nijedan numerički parser ne čita listu kao izraz. Vizuelni
Venn dijagram NIJE potreban ni za jedan koncept ove porodice; lekcija čija
je poenta crtanje/čitanje dijagrama ostaje vizuelna i NE aktivira se ovdje.
"""
import random
from math import isqrt

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.mathkernel import finiteset

FAMILY_IDS = ("finite_set_direct", "number_set_membership")
GENERATOR_VERSION = "detsets-1"

_SET_CONCEPTS = frozenset({
    "element_membership", "set_builder_match", "cardinality",
    "subset_equality", "union", "intersection", "difference", "complement",
    "ordered_pair", "cartesian_product",
})
_NUMBER_CONCEPTS = frozenset({
    "natural_sets", "set_classification", "irrational_recognition",
    "sqrt_between",
})
_SUPPORTED_CONCEPTS = _SET_CONCEPTS | _NUMBER_CONCEPTS


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
        "element_membership": _membership_package,
        "set_builder_match": _builder_package,
        "cardinality": _cardinality_package,
        "subset_equality": _subset_package,
        "union": _operation_package,
        "intersection": _operation_package,
        "difference": _operation_package,
        "complement": _complement_package,
        "ordered_pair": _ordered_pair_package,
        "cartesian_product": _product_package,
        "natural_sets": _natural_sets_package,
        "set_classification": _classification_package,
        "irrational_recognition": _irrational_package,
        "sqrt_between": _sqrt_between_package,
    }
    for _ in range(80):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            return builders[concept](rng, level, lesson_id, lesson_title, concept)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _family_for(concept):
    return ("finite_set_direct" if concept in _SET_CONCEPTS
            else "number_set_membership")


def _random_set(rng, level, low=1, high=None):
    high = high or (9 if level == 1 else 15 if level == 2 else 25)
    size = rng.randint(3, 4 if level == 1 else 5 if level == 2 else 6)
    if high - low + 1 <= size:
        raise DeterministicGenerationError("premalen raspon")
    return finiteset.canonical(rng.sample(range(low, high + 1), size))


def _set_package(lesson_id, lesson_title, concept, level, question,
                 option_texts, hints, solution, answer_display, signature):
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id=_family_for(concept), operation=concept, level=level,
        question=question, answer_value=None, answer_display=answer_display,
        distractor_values=(), hints=hints, solution=solution,
        signature_parameters=signature, required_conditions=[concept],
        relevant_objects=["skup"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _distinct_set_options(correct, candidates):
    """Opcije-skupovi: jedinstvenost je SKUPOVNA (frozenset), ne tekstualna."""
    texts = [finiteset.display(correct)]
    seen = {finiteset.canonical(correct)}
    for candidate in candidates:
        canonical = finiteset.canonical(candidate)
        if canonical in seen:
            continue
        seen.add(canonical)
        texts.append(finiteset.display(canonical))
        if len(texts) == 4:
            break
    if len(texts) != 4:
        raise DeterministicGenerationError("nedovoljno različitih skupova")
    return tuple(texts)


# ---------------------------------------------------------------------------
# OSNOVNI SKUPOVNI KONCEPTI
# ---------------------------------------------------------------------------

def _membership_package(rng, level, lesson_id, lesson_title, concept):
    base = _random_set(rng, level)
    inside = rng.choice(sorted(base))
    outside = [value for value in range(1, 30) if value not in base]
    wrong = rng.sample(outside, 3)
    question = (f"Dat je skup A = {finiteset.display(base)}. Koji od "
                "ponuđenih brojeva PRIPADA skupu A?")
    option_texts = (str(inside), *(str(value) for value in wrong))
    hints = (
        "Broj pripada skupu ako je naveden među njegovim elementima.",
        "Prođi kroz elemente skupa A jedan po jedan.",
        "Tri ponuđena broja se ne nalaze u skupu — samo jedan se nalazi.",
    )
    solution = (f"Elementi skupa A su {finiteset.display(base)}, pa skupu "
                f"pripada broj {inside}; ostali ponuđeni brojevi nisu "
                "elementi skupa A.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution, str(inside),
                        [("set", finiteset.display(base)),
                         ("member", str(inside))])


_BUILDER_RULES = (
    ("parni brojevi manji od {n}", lambda n: [v for v in range(2, n, 2)]),
    ("neparni brojevi manji od {n}", lambda n: [v for v in range(1, n, 2)]),
    ("djelioci broja {n}", lambda n: [v for v in range(1, n + 1) if n % v == 0]),
    ("prirodni brojevi veći od {m}, a manji od {n}",
     lambda n, m=None: None),  # popunjava se posebno
)


def _builder_package(rng, level, lesson_id, lesson_title, concept):
    kind = rng.choice(("even", "odd", "divisors", "between"))
    if kind == "even":
        n = rng.choice((9, 11, 13) if level == 1 else (15, 17, 19, 21))
        elements = list(range(2, n, 2))
        description = f"skup parnih brojeva manjih od {n}"
    elif kind == "odd":
        n = rng.choice((8, 10) if level == 1 else (12, 14, 16, 18))
        elements = list(range(1, n, 2))
        description = f"skup neparnih brojeva manjih od {n}"
    elif kind == "divisors":
        n = rng.choice((6, 8, 10) if level < 3 else (12, 18, 20, 24))
        elements = [v for v in range(1, n + 1) if n % v == 0]
        description = f"skup svih djelilaca broja {n}"
    else:
        m = rng.randint(1, 6)
        n = m + rng.randint(4, 5 if level < 3 else 7)
        elements = list(range(m + 1, n))
        description = (f"skup prirodnih brojeva većih od {m}, "
                       f"a manjih od {n}")
    correct = finiteset.canonical(elements)
    if not correct or len(correct) < 2:
        raise DeterministicGenerationError("opis daje premalen skup")
    with_extra = set(correct) | {max(correct) + rng.randint(1, 2)}
    without_one = set(correct) - {rng.choice(sorted(correct))}
    shifted = {value + 1 for value in correct}
    option_texts = _distinct_set_options(
        correct, [with_extra, without_one, shifted])
    question = (f"Koji zapis nabrajanjem elemenata ispravno zadaje "
                f"{description}?")
    hints = (
        "Provjeri svaki ponuđeni skup element po element prema opisu.",
        "Dovoljan je jedan element koji ne zadovoljava opis (ili jedan koji "
        "nedostaje) da zapis otpadne.",
        "Tačan zapis sadrži SVE brojeve iz opisa i nijedan drugi.",
    )
    solution = (f"Opisu odgovara tačno {finiteset.display(correct)} — svaki "
                "element zadovoljava opis, a nijedan broj koji opis "
                "zadovoljava nije izostavljen.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution,
                        finiteset.display(correct),
                        [("description", description)])


def _cardinality_package(rng, level, lesson_id, lesson_title, concept):
    if level >= 2 and rng.random() < 0.3:
        base = frozenset()
        question = ("Koliko elemenata ima PRAZAN skup?")
        answer = 0
    else:
        base = _random_set(rng, level)
        question = (f"Dat je skup A = {finiteset.display(base)}. "
                    "Koliko elemenata ima skup A?")
        answer = finiteset.cardinality(base)
    options = [str(answer)]
    for wrong in (answer + 1, answer - 1, answer + 2, answer + 3):
        if wrong >= 0 and str(wrong) not in options:
            options.append(str(wrong))
        if len(options) == 4:
            break
    if len(options) != 4:
        raise DeterministicGenerationError("nedovoljno brojnosti")
    hints = (
        "Brojnost skupa je broj njegovih (različitih) elemenata.",
        "Prebroj elemente jedan po jedan.",
        "Prazan skup nema nijedan element — brojnost mu je nula.",
    )
    solution = (f"Skup ima tačno {answer} elemenata."
                if base else "Prazan skup nema elemenata, pa je brojnost 0.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        tuple(options), hints, solution, str(answer),
                        [("set", finiteset.display(base))])


def _subset_package(rng, level, lesson_id, lesson_title, concept):
    base = _random_set(rng, max(level, 2))
    subset_size = rng.randint(2, len(base) - 1)
    subset = finiteset.canonical(rng.sample(sorted(base), subset_size))
    outside = [v for v in range(1, 30) if v not in base]
    not_subset_1 = set(subset) | {rng.choice(outside)}
    not_subset_2 = {rng.choice(outside)} | set(rng.sample(sorted(base), 1))
    not_subset_3 = set(rng.sample(outside, min(3, len(outside))))
    option_texts = _distinct_set_options(
        subset, [not_subset_1, not_subset_2, not_subset_3])
    question = (f"Dat je skup A = {finiteset.display(base)}. Koji od "
                "ponuđenih skupova je PODSKUP skupa A?")
    hints = (
        "Skup B je podskup skupa A kad je SVAKI element skupa B ujedno i "
        "element skupa A.",
        "Za svaki ponuđeni skup provjeri njegove elemente redom.",
        "Jedan jedini element izvan skupa A ruši podskup.",
    )
    solution = (f"Svi elementi skupa {finiteset.display(subset)} nalaze se "
                f"u skupu A, pa je on podskup. Ostali ponuđeni skupovi "
                "sadrže bar jedan element koji nije u A.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution,
                        finiteset.display(subset),
                        [("set", finiteset.display(base)),
                         ("subset", finiteset.display(subset))])


_OPERATION_WORDS = {
    "union": ("UNIJU", "unija sadrži svaki element koji je bar u jednom "
              "od skupova"),
    "intersection": ("PRESJEK", "presjek sadrži samo elemente koji su u OBA "
                     "skupa"),
    "difference": ("RAZLIKU A \\ B", "razlika A \\ B sadrži elemente skupa A "
                   "koji NISU u skupu B"),
}


def _operation_package(rng, level, lesson_id, lesson_title, concept):
    first = _random_set(rng, level)
    second = _random_set(rng, level)
    shared = finiteset.intersection(first, second)
    if concept == "intersection" and len(shared) < 1:
        second = finiteset.canonical(set(second) | {rng.choice(sorted(first))})
    if concept == "difference" and not finiteset.difference(first, second):
        raise DeterministicGenerationError("razlika je prazna")
    operations = {
        "union": finiteset.union,
        "intersection": finiteset.intersection,
        "difference": finiteset.difference,
    }
    correct = operations[concept](first, second)
    if not correct:
        raise DeterministicGenerationError("rezultat je prazan skup")
    word, rule = _OPERATION_WORDS[concept]
    others = {key: op(first, second) for key, op in operations.items()
              if key != concept}
    wrong = list(others.values())
    wrong.append(set(correct) | {max(max(first), max(second)) + 1})
    wrong.append(set(correct) - {min(correct)})
    option_texts = _distinct_set_options(correct, wrong)
    question = (f"Dati su skupovi A = {finiteset.display(first)} i "
                f"B = {finiteset.display(second)}. Odredi {word} ta dva "
                "skupa.")
    hints = (
        f"Podsjeti se: {rule}.",
        "Prolazi kroz elemente oba skupa i primjenjuj pravilo element po "
        "element.",
        "Elementi se u rezultatu ne ponavljaju, a poredak zapisivanja nije "
        "bitan.",
    )
    solution = (f"Prema pravilu ({rule}) rezultat je "
                f"{finiteset.display(correct)}.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution,
                        finiteset.display(correct),
                        [("A", finiteset.display(first)),
                         ("B", finiteset.display(second))])


def _complement_package(rng, level, lesson_id, lesson_title, concept):
    high = 8 if level == 1 else 12 if level == 2 else 16
    universe = finiteset.canonical(range(1, high + 1))
    subset = finiteset.canonical(
        rng.sample(sorted(universe), rng.randint(2, high - 3)))
    correct = finiteset.complement(subset, universe)
    if not correct:
        raise DeterministicGenerationError("komplement je prazan")
    wrong = [subset, set(correct) | {min(subset)},
             set(correct) - {min(correct)} if len(correct) > 1 else universe]
    option_texts = _distinct_set_options(correct, wrong)
    question = (f"Univerzalni skup je U = {finiteset.display(universe)}, a "
                f"skup A = {finiteset.display(subset)}. Odredi KOMPLEMENT "
                "skupa A u odnosu na U.")
    hints = (
        "Komplement skupa A čine SVI elementi univerzalnog skupa koji NISU "
        "u skupu A.",
        "Prođi kroz univerzalni skup element po element i zadrži one izvan A.",
        "Unija skupa i njegovog komplementa daje cijeli univerzalni skup.",
    )
    solution = (f"Elementi univerzuma koji nisu u A čine komplement: "
                f"{finiteset.display(correct)}. Provjera: unija s A daje "
                "cijeli U.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution,
                        finiteset.display(correct),
                        [("U", finiteset.display(universe)),
                         ("A", finiteset.display(subset))])


def _ordered_pair_package(rng, level, lesson_id, lesson_title, concept):
    a = rng.randint(1, 9)
    b = rng.randint(1, 9)
    if a == b:
        raise DeterministicGenerationError("trivijalan par")
    correct = f"({a}, {b})"
    wrong = [f"({b}, {a})", f"({a}, {b + 1})", f"({a + 1}, {b})"]
    question = (f"Uređeni par ima PRVU komponentu {a} i DRUGU komponentu "
                f"{b}. Koji zapis je ispravan?")
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("parovi nisu jedinstveni")
    hints = (
        "Kod uređenog para poredak komponenti je bitan: (a, b) i (b, a) su "
        "različiti parovi čim je a različito od b.",
        "Prva komponenta se piše na prvom mjestu, druga na drugom.",
        f"Traženi par ima {a} na prvom i {b} na drugom mjestu.",
    )
    solution = (f"Ispravan zapis je {correct}: prva komponenta je {a}, druga "
                f"{b}. Par ({b}, {a}) je DRUGAČIJI uređeni par jer je "
                "poredak bitan.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution, correct,
                        [("pair", correct)])


def _product_package(rng, level, lesson_id, lesson_title, concept):
    size_a = 2
    size_b = 2 if level == 1 else 3
    first = finiteset.canonical(rng.sample(range(1, 8), size_a))
    second = finiteset.canonical(rng.sample(range(1, 8), size_b))
    product = finiteset.cartesian_product(first, second)
    correct = finiteset.display_pairs(product)
    swapped = finiteset.display_pairs(finiteset.cartesian_product(second, first))
    missing = finiteset.display_pairs(sorted(product)[:-1])
    doubled = finiteset.display_pairs(
        finiteset.cartesian_product(first, first))
    option_texts = (correct, swapped, missing, doubled)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("proizvodi nisu jedinstveni")
    question = (f"Dati su skupovi A = {finiteset.display(first)} i "
                f"B = {finiteset.display(second)}. Koji zapis predstavlja "
                "Dekartov proizvod A × B?")
    hints = (
        "Dekartov proizvod A × B čine SVI uređeni parovi kojima je prva "
        "komponenta iz A, a druga iz B.",
        f"Skup A ima {len(first)} elemenata, B ima {len(second)} — proizvod "
        f"ima {len(first) * len(second)} parova.",
        "Pazi na poredak: parovi iz B × A nisu isti parovi.",
    )
    solution = (f"A × B = {correct} — svaki element skupa A uparen je sa "
                f"svakim elementom skupa B, ukupno "
                f"{len(first) * len(second)} parova.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution, correct,
                        [("A", finiteset.display(first)),
                         ("B", finiteset.display(second))])


# ---------------------------------------------------------------------------
# PRIPADNOST BROJEVNIM SKUPOVIMA
# ---------------------------------------------------------------------------

def _natural_sets_package(rng, level, lesson_id, lesson_title, concept):
    zero_question = rng.random() < 0.5
    if zero_question:
        question = ("Koji od ponuđenih brojeva pripada skupu N0, "
                    "a NE pripada skupu N?")
        correct = "0"
        wrong = [str(rng.randint(1, 9)), str(rng.randint(10, 20)),
                 str(rng.randint(21, 40))]
        explanation = ("Skup N0 sadrži nulu i sve prirodne brojeve, dok skup "
                       "N počinje od 1 — jedina razlika je broj 0.")
    else:
        inside = rng.randint(1, 30)
        question = "Koji od ponuđenih brojeva pripada skupu N?"
        correct = str(inside)
        wrong = ["0", str(-rng.randint(1, 9)), str(-rng.randint(10, 20))]
        explanation = (f"Prirodni brojevi su 1, 2, 3, … pa skupu N pripada "
                       f"{inside}; nula i negativni brojevi ne pripadaju.")
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    # Prvi hint bez ijedne cifre: odgovor („0“ ili konkretan broj) ne smije
    # se pojaviti ni kao podniz (živi fuzz nalaz: „0“ unutar „N0“).
    hints = (
        "Prirodni brojevi počinju od jedinice; skup s dodatom nulom sadrži "
        "i nulu.",
        "Provjeri svaki ponuđeni broj prema definiciji oba skupa.",
        "Razlika ta dva skupa je tačno jedan broj.",
    )
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, explanation, correct,
                        [("kind", "zero" if zero_question else "member")])


def _classification_package(rng, level, lesson_id, lesson_title, concept):
    n = rng.randint(2, 9)
    negative = -rng.randint(1, 12)
    numerator = rng.randint(1, 9)
    denominator = rng.choice((3, 7, 9, 11))
    statements = [
        (f"Broj {negative} pripada skupu Z, a ne pripada skupu N", True),
        (f"Broj {negative} pripada skupu N", False),
        (f"Broj {n} ne pripada skupu Q", False),
        (f"Broj 0 pripada skupu N", False),
    ]
    correct_text = statements[0][0]
    option_texts = tuple(text for text, _truth in statements)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("tvrdnje nisu jedinstvene")
    question = ("Posmatraj skupove brojeva N, Z, Q i R. "
                "Koja od ponuđenih tvrdnji je TAČNA?")
    hints = (
        "N su prirodni, Z cijeli, Q racionalni, a R realni brojevi — svaki "
        "sljedeći skup sadrži prethodni.",
        "Negativan cio broj jeste cio broj, ali nije prirodan.",
        "Svaki prirodan i svaki cio broj ujedno je i racionalan.",
    )
    solution = (f"Tačna je tvrdnja: „{correct_text}“ — negativni cijeli "
                "brojevi pripadaju skupu Z, a skup N sadrži samo brojeve "
                "1, 2, 3, …")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution, correct_text,
                        [("negative", str(negative)), ("n", str(n)),
                         ("q", f"{numerator}/{denominator}")])


_PERFECT_SQUARES = {n * n for n in range(1, 12)}


def _irrational_package(rng, level, lesson_id, lesson_title, concept):
    non_square = rng.choice([n for n in range(2, 30 if level < 3 else 60)
                             if n not in _PERFECT_SQUARES])
    square = rng.choice([n for n in (4, 9, 16, 25, 36) if n != non_square])
    # Nesvodiv razlomak → nikad cio broj → nikad ista vrijednost kao korijen
    # potpunog kvadrata (živi fuzz nalaz: $\sqrt{4}$ i $\frac{6}{3}$ su bili
    # dokazani semantički duplikat).
    from math import gcd as _gcd
    denominator = rng.choice((3, 7, 9))
    numerator = rng.randint(1, 9)
    while _gcd(numerator, denominator) != 1:
        numerator = rng.randint(1, 9)
    decimal = f"{rng.randint(0, 9)},{rng.randint(1, 9)}{rng.randint(0, 9)}"
    correct = f"$\\sqrt{{{non_square}}}$"
    wrong = [f"$\\sqrt{{{square}}}$",
             f"$\\frac{{{numerator}}}{{{denominator}}}$",
             f"${decimal}$"]
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = "Koji od ponuđenih brojeva je IRACIONALAN?"
    hints = (
        "Iracionalan broj ima beskonačan neperiodičan decimalni zapis — ne "
        "može se zapisati kao razlomak.",
        f"Provjeri je li {square} potpun kvadrat.",
        "Korijen prirodnog broja je racionalan SAMO kad je taj broj potpun "
        "kvadrat.",
    )
    solution = (f"Broj ${square}$ je potpun kvadrat, pa je "
                f"$\\sqrt{{{square}}}$ racionalan; razlomak i konačan "
                f"decimalni zapis su racionalni. Broj {non_square} nije "
                f"potpun kvadrat, pa je $\\sqrt{{{non_square}}}$ "
                "iracionalan.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution, correct,
                        [("value", str(non_square))])


def _sqrt_between_package(rng, level, lesson_id, lesson_title, concept):
    n = rng.choice([v for v in range(2, 40 if level == 1 else 120)
                    if v not in _PERFECT_SQUARES])
    lower = isqrt(n)
    upper = lower + 1
    correct = f"između {lower} i {upper}"
    wrong = [f"između {lower - 1} i {lower}" if lower > 1 else
             f"između {upper} i {upper + 1}",
             f"između {upper} i {upper + 1}",
             f"između {lower + 2} i {lower + 3}"]
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = (f"Između koja dva uzastopna cijela broja na brojevnoj osi "
                f"leži broj $\\sqrt{{{n}}}$?")
    hints = (
        "Potraži dva uzastopna potpuna kvadrata između kojih leži broj pod "
        "korijenom.",
        f"Vrijedi ${lower * lower} < {n} < {upper * upper}$.",
        "Korjenovanje čuva poredak pozitivnih brojeva.",
    )
    solution = (f"Iz ${lower * lower} < {n} < {upper * upper}$ slijedi "
                f"${lower} < \\sqrt{{{n}}} < {upper}$, pa broj leži između "
                f"{lower} i {upper}.")
    return _set_package(lesson_id, lesson_title, concept, level, question,
                        option_texts, hints, solution, correct,
                        [("n", str(n))])
