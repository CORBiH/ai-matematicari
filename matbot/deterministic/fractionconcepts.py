"""Deterministički pojmovni koncepti razlomaka i decimalnih zapisa.

Jedna semantička porodica (Batch #4, Prioritet 7): ``fraction_concept_direct``.

  • ``part_of_whole``             — razlomak kao dio cjeline i kao količnik;
  • ``fraction_types``            — pravi/nepravi/prividni razlomci i
    mješoviti brojevi;
  • ``common_denominator_numeric``— svođenje BROJEVNIH razlomaka na najmanji
    zajednički nazivnik;
  • ``decimal_type``              — konačan naspram beskonačnog periodičnog
    decimalnog zapisa (nazivnik oblika 2^a·5^b).

MATEMATIČKI AUTORITET: egzaktni razlomci; klasifikacija zapisa se dokazuje
faktorizacijom nazivnika (core.is_terminating_decimal), nikad dijeljenjem
do „dovoljno“ decimala. Ne dira postojeće porodice razlomaka: ovo su
POJMOVNE lekcije bez računskih operacija nad dva razlomka.
"""
import random
from fractions import Fraction
from math import gcd, lcm

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("fraction_concept_direct",)
GENERATOR_VERSION = "detfraconcepts-1"

_SUPPORTED_CONCEPTS = frozenset({
    "part_of_whole", "fraction_types", "common_denominator_numeric",
    "decimal_type",
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
    builders = {
        "part_of_whole": _part_package,
        "fraction_types": _types_package,
        "common_denominator_numeric": _common_denominator_package,
        "decimal_type": _decimal_type_package,
    }
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            return builders[concept](rng, level, lesson_id, lesson_title, concept)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _package(lesson_id, lesson_title, concept, level, question, option_texts,
             hints, solution, answer_display, signature):
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="fraction_concept_direct", operation=concept, level=level,
        question=question, answer_value=None, answer_display=answer_display,
        distractor_values=(), hints=hints, solution=solution,
        signature_parameters=signature, required_conditions=[concept],
        relevant_objects=["razlomak"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _frac_text(numerator, denominator):
    return f"\\frac{{{numerator}}}{{{denominator}}}"


def _part_package(rng, level, lesson_id, lesson_title, concept):
    denominator = rng.randint(3, 8 if level == 1 else 12)
    numerator = rng.randint(1, denominator - 1)
    as_quotient = level >= 2 and rng.random() < 0.5
    if as_quotient:
        question = (f"Koji razlomak je jednak količniku "
                    f"${numerator} : {denominator}$?")
        explanation = (f"Količnik ${numerator} : {denominator}$ zapisuje se "
                       f"razlomkom ${_frac_text(numerator, denominator)}$ — "
                       "djeljenik je brojnik, djelilac je nazivnik.")
    else:
        question = (f"Pica je podijeljena na ${denominator}$ jednakih "
                    f"dijelova i uzeto je ${numerator}$ dijelova. Koji "
                    "razlomak predstavlja uzeti dio?")
        explanation = (f"Cjelina ima ${denominator}$ jednakih dijelova "
                       f"(nazivnik), uzeto je ${numerator}$ (brojnik), pa je "
                       f"uzeti dio ${_frac_text(numerator, denominator)}$.")
    correct = f"${_frac_text(numerator, denominator)}$"
    wrong = [f"${_frac_text(denominator, numerator)}$",
             f"${_frac_text(numerator, denominator + 1)}$",
             f"${_frac_text(numerator + 1, denominator)}$"]
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    hints = (
        "Nazivnik kazuje na koliko je jednakih dijelova cjelina "
        "podijeljena, brojnik koliko se dijelova uzima.",
        "Kod količnika: djeljenik ide u brojnik, djelilac u nazivnik.",
        "Pazi na poredak — zamijenjen brojnik i nazivnik daju drugi broj.",
    )
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, explanation, correct,
                    [("numerator", str(numerator)),
                     ("denominator", str(denominator)),
                     ("form", "quotient" if as_quotient else "part")])


def _types_package(rng, level, lesson_id, lesson_title, concept):
    asked = rng.choice(("proper", "improper", "apparent")
                       if level < 3 else ("improper", "apparent", "mixed"))
    denominator = rng.randint(3, 9)
    proper = _frac_text(rng.randint(1, denominator - 1), denominator)
    improper_numerator = denominator * rng.randint(1, 2) + rng.randint(1, denominator - 1)
    improper = _frac_text(improper_numerator, denominator)
    apparent_factor = rng.randint(2, 4)
    apparent = _frac_text(denominator * apparent_factor, denominator)
    another_proper = _frac_text(1, denominator + 1)
    if asked == "mixed":
        whole, remainder = divmod(improper_numerator, denominator)
        correct = f"${whole}{_frac_text(remainder, denominator)}$"
        wrong = [f"${whole + 1}{_frac_text(remainder, denominator)}$",
                 f"${remainder}{_frac_text(whole, denominator)}$",
                 f"${whole}{_frac_text(remainder + 1, denominator)}$"
                 if remainder + 1 < denominator else
                 f"${whole}{_frac_text(1, denominator + 1)}$"]
        question = (f"Koji mješoviti broj je jednak nepravom razlomku "
                    f"${improper}$?")
        explanation = (f"Dijeljenjem ${improper_numerator} : {denominator}$ "
                       f"dobijamo cijeli dio ${whole}$ i ostatak "
                       f"${remainder}$, pa je "
                       f"${improper} = {whole}{_frac_text(remainder, denominator)}$.")
        signature_kind = "mixed"
    else:
        kinds = {"proper": ("PRAVI", proper,
                            "brojnik mu je manji od nazivnika"),
                 "improper": ("NEPRAVI", improper,
                              "brojnik mu je veći od nazivnika (ili jednak)"),
                 "apparent": ("PRIVIDNI", apparent,
                              "brojnik je višekratnik nazivnika, pa je "
                              "vrijednost cio broj")}
        word, correct_body, rule = kinds[asked]
        pool = {proper, improper, apparent, another_proper} - {correct_body}
        wrong = sorted(pool)[:3]
        correct = f"${correct_body}$"
        question = f"Koji od ponuđenih razlomaka je {word}?"
        explanation = (f"Razlomak ${correct_body}$ je {word.lower()}: "
                       f"{rule}.")
        signature_kind = asked
    option_texts = (correct, *(f"${w}$" if not w.startswith("$") else w
                               for w in wrong))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    hints = (
        "Pravi razlomak: brojnik manji od nazivnika. Nepravi: veći ili "
        "jednak. Prividni: vrijednost je cio broj.",
        "Uporedi brojnik i nazivnik svakog ponuđenog razlomka.",
        "Za mješoviti broj podijeli brojnik nazivnikom — količnik je cijeli "
        "dio, ostatak novi brojnik.",
    )
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, explanation, correct,
                    [("kind", signature_kind),
                     ("denominator", str(denominator))])


def _common_denominator_package(rng, level, lesson_id, lesson_title, concept):
    d1 = rng.choice((2, 3, 4, 6) if level == 1 else (4, 6, 8, 9, 10, 12))
    d2 = rng.choice((3, 4, 5, 6) if level == 1 else (6, 8, 9, 10, 12, 15))
    if d1 == d2 or lcm(d1, d2) in (d1, d2):
        raise DeterministicGenerationError("nazivnici su trivijalni")
    n1 = rng.randint(1, d1 - 1)
    n2 = rng.randint(1, d2 - 1)
    if gcd(n1, d1) != 1 or gcd(n2, d2) != 1:
        raise DeterministicGenerationError("razlomci nisu nesvodivi")
    common = lcm(d1, d2)
    e1 = n1 * (common // d1)
    e2 = n2 * (common // d2)
    correct = (f"${_frac_text(e1, common)}$ i ${_frac_text(e2, common)}$")
    product = d1 * d2
    wrong1 = (f"${_frac_text(n1 * d2, product)}$ i "
              f"${_frac_text(n2 * d1, product)}$") if product != common else \
        (f"${_frac_text(e1 * 2, common * 2)}$ i "
         f"${_frac_text(e2 * 2, common * 2)}$")
    wrong2 = (f"${_frac_text(n1, common)}$ i ${_frac_text(n2, common)}$")
    wrong3 = (f"${_frac_text(e2, common)}$ i ${_frac_text(e1, common)}$")
    option_texts = (correct, wrong1, wrong2, wrong3)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = (f"Svedi razlomke ${_frac_text(n1, d1)}$ i "
                f"${_frac_text(n2, d2)}$ na NAJMANJI zajednički nazivnik.")
    hints = (
        "Najmanji zajednički nazivnik je najmanji zajednički sadržilac "
        "oba nazivnika.",
        f"NZS(${d1}$, ${d2}$) $= {common}$.",
        f"Prvi razlomak proširi sa ${common // d1}$, drugi sa "
        f"${common // d2}$ — i BROJNIK i nazivnik.",
    )
    solution = (f"NZS nazivnika je ${common}$. Proširivanjem: "
                f"${_frac_text(n1, d1)} = {_frac_text(e1, common)}$ i "
                f"${_frac_text(n2, d2)} = {_frac_text(e2, common)}$.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("f1", f"{n1}/{d1}"), ("f2", f"{n2}/{d2}")])


def _decimal_type_package(rng, level, lesson_id, lesson_title, concept):
    terminating_denominators = (2, 4, 5, 8, 10, 20, 25)
    periodic_denominators = (3, 6, 7, 9, 11, 12, 15)
    ask_terminating = rng.random() < 0.5
    if ask_terminating:
        denominator = rng.choice(terminating_denominators)
        others = rng.sample(periodic_denominators, 3)
        word = "KONAČAN"
        rule = ("konačan decimalni zapis ima razlomak čiji nazivnik (poslije "
                "skraćivanja) sadrži samo faktore 2 i 5")
    else:
        denominator = rng.choice(periodic_denominators)
        others = rng.sample(terminating_denominators, 3)
        word = "BESKONAČAN PERIODIČAN"
        rule = ("beskonačan periodičan zapis ima razlomak čiji nazivnik "
                "poslije skraćivanja sadrži i neki faktor različit od 2 i 5")

    def coprime_numerator(den):
        for _ in range(40):
            candidate = rng.randint(1, den - 1)
            if gcd(candidate, den) == 1:
                return candidate
        raise DeterministicGenerationError("nema nesvodivog brojnika")

    numerator = coprime_numerator(denominator)
    correct = f"${_frac_text(numerator, denominator)}$"
    wrong = []
    for other in others:
        wrong.append(f"${_frac_text(coprime_numerator(other), other)}$")
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    question = (f"Koji od ponuđenih razlomaka ima {word} decimalni zapis?")
    verified = core.is_terminating_decimal(Fraction(numerator, denominator))
    if verified != ask_terminating:
        raise DeterministicGenerationError("klasifikacija nije dosljedna")
    hints = (
        "Rastavi nazivnik skraćenog razlomka na proste faktore.",
        "Samo faktori 2 i 5 daju konačan zapis; svaki drugi faktor donosi "
        "period.",
        f"Provjeri nazivnik ${denominator}$: koje proste faktore sadrži?",
    )
    solution = (f"Pravilo: {rule}. Razlomak "
                f"${_frac_text(numerator, denominator)}$ zadovoljava upravo "
                "to, a ostala tri razlomka pripadaju suprotnoj vrsti.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("denominator", str(denominator)),
                     ("kind", "terminating" if ask_terminating else "periodic")])
