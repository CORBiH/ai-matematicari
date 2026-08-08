"""Deterministički generator događaja, vjerovatnosnih odluka i uzorkovanja.

Jedna semantička porodica (Batch #4, Prioritet 4): ``event_probability_facts``.

  • ``elementary_outcomes``   — elementarni ishodi ogleda (kocka, novčić,
    izvlačenje iz kutije poznatog sastava);
  • ``event_classification``  — siguran / nemoguć / slučajan događaj nad
    ogledom čiji sastav server u potpunosti poznaje;
  • ``probability_decision``  — poređenje dvije EGZAKTNE vjerovatnoće i
    izbor povoljnije opcije;
  • ``population_sample``     — populacija naspram uzorka nad server-vlasnički
    generisanim scenarijem (svi brojevi poznati prije proze).

MATEMATIČKI AUTORITET: sastav svakog ogleda generiše server PRIJE proze, pa
je klasifikacija svakog događaja i svaka vjerovatnoća egzaktna činjenica
(`Fraction`), nikad procjena. Vizuelni dijagrami nisu potrebni ni za jedan
koncept — lekcije o čitanju/crtanju dijagrama ostaju vizuelne.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("event_probability_facts",)
GENERATOR_VERSION = "detstats-1"

_SUPPORTED_CONCEPTS = frozenset({
    "elementary_outcomes", "event_classification", "probability_decision",
    "population_sample",
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
        "elementary_outcomes": _outcomes_package,
        "event_classification": _classification_package,
        "probability_decision": _decision_package,
        "population_sample": _population_package,
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
        family_id="event_probability_facts", operation=concept, level=level,
        question=question, answer_value=None, answer_display=answer_display,
        distractor_values=(), hints=hints, solution=solution,
        signature_parameters=signature, required_conditions=[concept],
        relevant_objects=["događaj"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


_COLORS = (("crvena", "crvenih"), ("plava", "plavih"), ("bijela", "bijelih"),
           ("zelena", "zelenih"), ("žuta", "žutih"))


def _outcomes_package(rng, level, lesson_id, lesson_title, concept):
    experiment = rng.choice(("die", "coin", "box") if level > 1 else
                            ("die", "coin"))
    if experiment == "die":
        question = ("Bacamo pravilnu kockicu za igru (strane označene "
                    "brojevima od $1$ do $6$). Koliko ELEMENTARNIH ishoda "
                    "ima ovaj ogled?")
        answer = 6
        wrong = (3, 12, 2)
        explanation = ("Svaka od šest strana kockice je po jedan elementaran "
                       "ishod, pa ih ima tačno šest.")
        signature = [("experiment", "die")]
    elif experiment == "coin":
        question = ("Bacamo novčić jedanput. Koliko ELEMENTARNIH ishoda ima "
                    "ovaj ogled?")
        answer = 2
        wrong = (1, 4, 3)
        explanation = ("Novčić može pasti na pismo ili na glavu — dva "
                       "elementarna ishoda.")
        signature = [("experiment", "coin")]
    else:
        first, second = rng.sample(_COLORS, 2)
        count_a = rng.randint(2, 6)
        count_b = rng.randint(2, 6)
        total = count_a + count_b
        question = (f"U kutiji je ${count_a}$ {first[1]} i ${count_b}$ "
                    f"{second[1]} kuglica. Izvlačimo jednu kuglicu. Koliko "
                    "kuglica može biti izvučeno, tj. koliko ima mogućih "
                    "ishoda?")
        answer = total
        wrong = (2, count_a, count_b)
        explanation = (f"Svaka od ${total}$ kuglica jednako je moguć ishod "
                       "izvlačenja.")
        signature = [("experiment", "box"), ("a", str(count_a)),
                     ("b", str(count_b))]
    options = [str(answer)]
    for value in wrong:
        if str(value) not in options:
            options.append(str(value))
        if len(options) == 4:
            break
    if len(options) != 4:
        raise DeterministicGenerationError("nedovoljno ishoda")
    hints = (
        "Elementaran ishod je jedan pojedinačan, dalje nedjeljiv rezultat "
        "ogleda.",
        "Prebroj sve pojedinačne rezultate koji se mogu desiti.",
        "Ne grupiši ishode — svaki pojedinačni rezultat broji se posebno.",
    )
    return _package(lesson_id, lesson_title, concept, level, question,
                    tuple(options), hints, explanation, str(answer),
                    signature)


def _classification_package(rng, level, lesson_id, lesson_title, concept):
    first, second = rng.sample(_COLORS, 2)
    third = rng.choice([c for c in _COLORS if c not in (first, second)])
    count_a = rng.randint(2, 7)
    count_b = rng.randint(2, 7)
    asked = rng.choice(("siguran", "nemoguć", "slučajan"))
    box = (f"U kutiji su ${count_a}$ {first[1]} i ${count_b}$ {second[1]} "
           "kuglica i iz nje izvlačimo jednu kuglicu.")
    certain = f"izvučena kuglica je {first[0]} ili {second[0]}"
    impossible = f"izvučena kuglica je {third[0]}"
    random_event = f"izvučena kuglica je {first[0]}"
    random_event_2 = f"izvučena kuglica je {second[0]}"
    if asked == "siguran":
        correct, wrong = certain, (impossible, random_event, random_event_2)
        reason = ("obuhvata SVE kuglice u kutiji, pa se dešava pri svakom "
                  "izvlačenju")
    elif asked == "nemoguć":
        correct, wrong = impossible, (certain, random_event, random_event_2)
        reason = (f"u kutiji nema {third[1]} kuglica, pa se taj događaj ne "
                  "može desiti")
    else:
        correct = random_event
        wrong = (certain, impossible,
                 f"izvučena kuglica je {first[0]} i {second[0]} istovremeno")
        reason = ("može, ali ne mora da se desi — zavisi od izvučene "
                  "kuglice")
    question = f"{box} Koji od ponuđenih događaja je {asked.upper()}?"
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("događaji nisu jedinstveni")
    hints = (
        "Siguran događaj se dešava uvijek, nemoguć nikad, a slučajan nekad "
        "da a nekad ne.",
        "Uporedi svaki događaj sa stvarnim sastavom kutije.",
        "Provjeri: postoji li ishod koji događaj čini mogućim i ishod koji "
        "ga sprječava?",
    )
    solution = (f"Događaj „{correct}“ je {asked}: {reason}.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("box", f"{count_a}+{count_b}"), ("asked", asked)])


def _decision_package(rng, level, lesson_id, lesson_title, concept):
    color = rng.choice(_COLORS)
    total_a = rng.randint(4, 8 if level == 1 else 12)
    favorable_a = rng.randint(1, total_a - 1)
    total_b = rng.randint(4, 8 if level == 1 else 12)
    favorable_b = rng.randint(1, total_b - 1)
    p_a = Fraction(favorable_a, total_a)
    p_b = Fraction(favorable_b, total_b)
    if p_a == p_b:
        raise DeterministicGenerationError("vjerovatnoće su jednake")
    better = "A" if p_a > p_b else "B"
    question = (f"U kutiji A je ${favorable_a}$ {color[1]} od ukupno "
                f"${total_a}$ kuglica, a u kutiji B ${favorable_b}$ "
                f"{color[1]} od ukupno ${total_b}$ kuglica. Iz koje kutije "
                f"je POVOLJNIJE izvlačiti da bi kuglica bila {color[0]}?")
    correct = f"iz kutije {better}"
    other = "B" if better == "A" else "A"
    option_texts = (correct, f"iz kutije {other}",
                    "svejedno je — šanse su jednake",
                    "ne može se odrediti bez izvlačenja")
    p_a_text = core.plain_fraction_display(p_a)
    p_b_text = core.plain_fraction_display(p_b)
    hints = (
        "Vjerovatnoća je količnik broja povoljnih i broja svih ishoda.",
        f"Kutija A: ${favorable_a}$ od ${total_a}$; kutija B: "
        f"${favorable_b}$ od ${total_b}$ — zapiši oba razlomka.",
        "Uporedi razlomke svođenjem na zajednički nazivnik.",
    )
    bigger = p_a_text if better == "A" else p_b_text
    solution = (f"Vjerovatnoće su $P(A) = {p_a_text}$ i $P(B) = {p_b_text}$. "
                f"Veća je ${bigger}$, pa je povoljnije izvlačiti iz kutije "
                f"{better}.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("pa", str(p_a)), ("pb", str(p_b))])


_SURVEY_TOPICS = ("omiljenom sportu", "načinu dolaska u školu",
                  "omiljenoj knjizi", "broju sati spavanja")


def _population_package(rng, level, lesson_id, lesson_title, concept):
    population = rng.choice((240, 360, 480, 520, 600))
    sample = rng.choice((30, 40, 50, 60, 80))
    if sample >= population:
        raise DeterministicGenerationError("uzorak veći od populacije")
    topic = rng.choice(_SURVEY_TOPICS)
    question = (f"Škola ima ${population}$ učenika. Za istraživanje o "
                f"{topic} anketirano je ${sample}$ nasumično odabranih "
                "učenika. Koja tvrdnja ISPRAVNO imenuje populaciju i "
                "uzorak?")
    correct = (f"populacija je svih {population} učenika škole, a uzorak "
               f"{sample} anketiranih")
    option_texts = (
        correct,
        f"populacija je {sample} anketiranih, a uzorak svih {population} "
        "učenika škole",
        f"i populacija i uzorak su svih {population} učenika škole",
        f"i populacija i uzorak su {sample} anketiranih učenika",
    )
    hints = (
        "Populacija je CIJELI skup koji istražujemo; uzorak je njegov "
        "odabrani dio.",
        "Zapitaj se: o kome želimo zaključiti? To je populacija.",
        "Anketirani učenici su dio od kojeg prikupljamo podatke — to je "
        "uzorak.",
    )
    solution = (f"Zaključke želimo o SVIH ${population}$ učenika — to je "
                f"populacija; podatke smo prikupili od ${sample}$ "
                "anketiranih — to je uzorak.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("population", str(population)), ("sample", str(sample))])
