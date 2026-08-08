"""Deterministički uglovi mnogougla (Batch #4, Prioritet 7).

Jedna semantička porodica: ``polygon_angle_direct``.

  • ``exterior_sum``    — zbir vanjskih uglova konveksnog mnogougla (uvijek
    360°) i vanjski ugao PRAVILNOG mnogougla (360/n);
  • ``interior_sum``    — zbir unutrašnjih uglova (n-2)·180°.

MATEMATIČKI AUTORITET: egzaktna cjelobrojna aritmetika; za pravilan
mnogougao biraju se SAMO vrijednosti n koje dijele 360, pa je svaki vidljivi
ugao cio broj stepeni.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("polygon_angle_direct",)
GENERATOR_VERSION = "detpolygons-1"

_SUPPORTED_CONCEPTS = frozenset({"exterior_sum", "interior_sum"})

_POLYGON_NAMES = {3: "trougla", 4: "četverougla", 5: "petougla",
                  6: "šestougla", 8: "osmougla", 9: "devetougla",
                  10: "desetougla", 12: "dvanaestougla"}


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
        "exterior_sum": _exterior_package,
        "interior_sum": _interior_package,
    }
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            return builders[concept](rng, level, lesson_id, lesson_title, concept)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _degree_options(answer, candidates):
    texts = [f"${answer}^{{\\circ}}$"]
    for value in candidates:
        text = f"${value}^{{\\circ}}$"
        if value > 0 and text not in texts:
            texts.append(text)
        if len(texts) == 4:
            break
    if len(texts) != 4:
        raise DeterministicGenerationError("nedovoljno uglova")
    return tuple(texts)


def _exterior_package(rng, level, lesson_id, lesson_title, concept):
    regular = level >= 2 and rng.random() < 0.6
    n = rng.choice(tuple(sorted(_POLYGON_NAMES)))
    name = _POLYGON_NAMES[n]
    if regular:
        if 360 % n != 0:
            raise DeterministicGenerationError("ugao nije cio")
        answer = 360 // n
        question = (f"Koliki je JEDAN vanjski ugao pravilnog {name} "
                    f"(mnogougao sa ${n}$ jednakih stranica i uglova)?")
        option_texts = _degree_options(
            answer, (360, (n - 2) * 180 // n, answer + 10, answer - 10,
                     answer * 2))
        # Prvi hint bez cifara: ugao pravilnog šestougla („60°“) bio bi
        # podniz zapisa „360°“ (živi 100-seed fuzz nalaz).
        hints = (
            "Zbir SVIH vanjskih uglova konveksnog mnogougla jednak je punom "
            "okretu.",
            f"Pravilan mnogougao ima ${n}$ jednakih vanjskih uglova.",
            f"Podijeli: $360 : {n}$.",
        )
        solution = (f"Zbir vanjskih uglova je $360^{{\\circ}}$, a pravilan "
                    f"{name} ima ${n}$ jednakih vanjskih uglova, pa je jedan "
                    f"ugao $360 : {n} = {answer}$ stepeni.")
        signature = [("n", str(n)), ("kind", "regular")]
    else:
        answer = 360
        question = (f"Koliki je ZBIR vanjskih uglova konveksnog {name}?")
        option_texts = _degree_options(
            answer, ((n - 2) * 180, 180, n * 90, 720))
        hints = (
            "Vanjski uglovi mjere ukupan okret pri obilasku figure.",
            "Obilaskom oko cijelog mnogougla napraviš pun krug.",
            "Zbir ne zavisi od broja stranica.",
        )
        solution = (f"Obilaskom konveksnog mnogougla napravi se tačno jedan "
                    f"pun okret, pa je zbir vanjskih uglova uvijek "
                    f"$360^{{\\circ}}$ — nezavisno od broja stranica.")
        signature = [("n", str(n)), ("kind", "sum")]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polygon_angle_direct", operation=concept, level=level,
        question=question, answer_value=None,
        answer_display=f"{answer}°", distractor_values=(), hints=hints,
        solution=solution, signature_parameters=signature,
        required_conditions=[concept], relevant_objects=["mnogougao"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")


def _interior_package(rng, level, lesson_id, lesson_title, concept):
    n = rng.choice((4, 5, 6) if level == 1 else tuple(sorted(_POLYGON_NAMES)))
    name = _POLYGON_NAMES[n]
    answer = (n - 2) * 180
    question = (f"Koliki je zbir unutrašnjih uglova konveksnog {name} "
                f"(mnogougao sa ${n}$ stranica)?")
    option_texts = _degree_options(
        answer, (n * 180, 360, answer + 180, answer - 180))
    hints = (
        "Mnogougao se dijagonalama iz jednog vrha dijeli na trouglove.",
        f"Iz jednog vrha nastaje ${n - 2}$ trouglova.",
        "Zbir uglova svakog trougla je 180°.",
    )
    solution = (f"Dijagonale iz jednog vrha dijele {name.rstrip('a')} na "
                f"${n - 2}$ trouglova, pa je zbir unutrašnjih uglova "
                f"$({n} - 2) \\cdot 180 = {answer}$ stepeni.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="polygon_angle_direct", operation=concept, level=level,
        question=question, answer_value=None,
        answer_display=f"{answer}°", distractor_values=(), hints=hints,
        solution=solution, signature_parameters=[("n", str(n))],
        required_conditions=[concept], relevant_objects=["mnogougao"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")
