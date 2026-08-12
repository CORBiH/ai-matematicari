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


# ---------------------------------------------------------------------------
# KURIKULARNI PREDIKATI VRSTA RAZLOMAKA — jedini izvor istine ove porodice
# ---------------------------------------------------------------------------
# ŽIVI P0 NALAZ (QA nad OBJAVLJENIM Practice paketom vrsta razlomaka):
# objavljeno je „Koji od ponuđenih razlomaka je PRAVI?“ s opcijama
# $\frac{1}{9}$, $\frac{24}{8}$, $\frac{2}{8}$, $\frac{9}{8}$ — DVIJE su tačne
# ($\frac{1}{9}$ i $\frac{2}{8}$), a server je priznavao samo jednu, pa je
# matematički tačan odgovor učenika ocijenjen kao netačan.
#
# UZROK: distraktori su birani iz fiksnog skupa „po jedan primjer svake vrste
# + još jedan pravi razlomak“, dakle po ULOZI koju im je kod dodijelio, bez
# ijedne provjere da distraktor NE zadovoljava traženi pojam.
#
# Zato vrste ovdje postoje kao PREDIKATI nad (brojnik, nazivnik), a ne kao
# imena uloga u kodu. Definicije su kurikularne i doslovno one koje generator
# izgovara učeniku u nagovještaju i u rješenju:
#   pravi    — brojnik manji od nazivnika;
#   nepravi  — brojnik veći od nazivnika ILI jednak;
#   prividni — vrijednost je cio broj (brojnik je višekratnik nazivnika).
#
# PRIVIDNI JE PODSKUP NEPRAVIH ($\frac{24}{8}$ je i nepravi i prividni). Klase
# se NIKAD ne smiju tretirati kao međusobno isključive uloge — ta pretpostavka
# je drugo lice istog defekta: „Koji je NEPRAVI?“ s prividnim distraktorom je
# jednako imao dvije tačne opcije.

def is_proper_fraction(numerator: int, denominator: int) -> bool:
    """Pravi razlomak: brojnik manji od nazivnika."""
    return numerator < denominator


def is_improper_fraction(numerator: int, denominator: int) -> bool:
    """Nepravi razlomak: brojnik veći od nazivnika ili jednak njemu."""
    return numerator >= denominator


def is_apparent_fraction(numerator: int, denominator: int) -> bool:
    """Prividni razlomak: vrijednost je cio broj."""
    return numerator % denominator == 0


FRACTION_TYPE_PREDICATES = {
    "proper": is_proper_fraction,
    "improper": is_improper_fraction,
    "apparent": is_apparent_fraction,
}


def _type_candidate_pool(rng, denominator):
    """Bazen kandidata (brojnik, nazivnik) za klasifikacijski MCQ.

    Bazen namjerno NE ZNA koja je vrsta tražena: uloge (tačna opcija naspram
    distraktora) dodjeljuje isključivo predikat tražene vrste, pa se defekt
    „distraktor je slučajno i sam tačan“ ne može ponoviti ni za jednu vrstu.

    Nazivnici su susjedni ($d$ i $d+1$) da četiri opcije i dalje izgledaju kao
    jedan zadatak. Vrijednosti se dedupliciraju jer objava odbija dvije opcije
    iste vrijednosti (`option_equivalence`), a $\\frac{6}{3}$ i $\\frac{8}{4}$
    su ista vrijednost."""
    candidates, values = [], set()
    for den in (denominator, denominator + 1):
        numerators = [*rng.sample(range(1, den), min(3, den - 1)),
                      den + rng.randint(1, den - 1),
                      den * 2 + rng.randint(1, den - 1),
                      den * rng.randint(2, 4)]
        for numerator in numerators:
            value = Fraction(numerator, den)
            if value in values:
                continue
            values.add(value)
            candidates.append((numerator, den))
    return candidates


def _exactly_one_satisfies(predicate, pairs) -> bool:
    """Invarijanta arhetipa: tačno jedna ponuđena opcija zadovoljava traženi
    pojam. Generator predikat POSJEDUJE (bira ga iz strukturisanog `asked`), pa
    ovo nije nikakvo čitanje pitanja — samo zatvaranje kruga prije objave."""
    return sum(1 for pair in pairs if predicate(*pair)) == 1


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
    if asked == "mixed":
        improper_numerator = (denominator * rng.randint(1, 2)
                              + rng.randint(1, denominator - 1))
        improper = _frac_text(improper_numerator, denominator)
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
        words = {"proper": ("PRAVI", "brojnik mu je manji od nazivnika"),
                 "improper": ("NEPRAVI",
                              "brojnik mu je veći od nazivnika (ili jednak)"),
                 "apparent": ("PRIVIDNI",
                              "brojnik je višekratnik nazivnika, pa je "
                              "vrijednost cio broj")}
        word, rule = words[asked]
        # TAČNO JEDNA TAČNA PO KONSTRUKCIJI: tačna opcija se bira iz kandidata
        # koji predikat ZADOVOLJAVAJU, a sva tri distraktora iz onih koji ga NE
        # zadovoljavaju. Nijedna opcija nije „vrsta po ulozi“ — svaka je
        # klasifikovana istim predikatom kojim se pitanje postavlja.
        predicate = FRACTION_TYPE_PREDICATES[asked]
        candidates = _type_candidate_pool(rng, denominator)
        true_pool = [pair for pair in candidates if predicate(*pair)]
        false_pool = [pair for pair in candidates if not predicate(*pair)]
        if not true_pool or len(false_pool) < 3:
            raise DeterministicGenerationError(
                "bazen ne dozvoljava tačno jednu tačnu opciju")
        correct_pair = rng.choice(true_pool)
        wrong_pairs = rng.sample(false_pool, 3)
        if not _exactly_one_satisfies(predicate, (correct_pair, *wrong_pairs)):
            raise DeterministicGenerationError(
                "MCQ nema tačno jednu tačnu opciju")
        correct_body = _frac_text(*correct_pair)
        correct = f"${correct_body}$"
        wrong = [f"${_frac_text(*pair)}$" for pair in wrong_pairs]
        question = f"Koji od ponuđenih razlomaka je {word}?"
        explanation = (f"Razlomak ${correct_body}$ je {word.lower()}: "
                       f"{rule}.")
        signature_kind = asked
    option_texts = (correct, *wrong)
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
