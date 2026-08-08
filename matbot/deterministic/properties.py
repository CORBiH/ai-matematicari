"""Deterministička svojstva računskih operacija (Batch #4, Prioritet 7).

Jedna semantička porodica: ``operation_property_recognition``.

  • ``recognize_example`` — koja jednakost prikazuje IMENOVANO svojstvo;
  • ``name_property``     — koje svojstvo prikazuje data jednakost.

MATEMATIČKI AUTORITET: svaka prikazana jednakost je NUMERIČKI TAČNA (server
je izračunao obje strane), pa nijedan distraktor nije lažna jednakost —
razlika među opcijama je ISKLJUČIVO svojstvo koje jednakost ilustruje.
Parametar ``number_domain`` bira brojeve lekcije (prirodni/cijeli/razlomci).
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("operation_property_recognition",)
GENERATOR_VERSION = "detprops-1"

_PROPERTIES = frozenset({
    "commutativity_add", "commutativity_mul", "associativity_add",
    "associativity_mul", "distributivity",
})
_DOMAINS = frozenset({"natural", "integer", "fraction"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    properties = set(parameters.get("properties") or ())
    domain = parameters.get("number_domain")
    return (bool(properties) and properties <= _PROPERTIES
            and domain in _DOMAINS)


_PROPERTY_NAMES = {
    "commutativity_add": "komutativnost sabiranja",
    "commutativity_mul": "komutativnost množenja",
    "associativity_add": "asocijativnost sabiranja",
    "associativity_mul": "asocijativnost množenja",
    "distributivity": "distributivnost množenja prema sabiranju",
}


def _operand(rng, domain, level):
    if domain == "natural":
        return Fraction(rng.randint(1, 9 if level == 1 else 20))
    if domain == "integer":
        value = 0
        while value == 0:
            value = rng.randint(-9 if level > 1 else -5, 9)
        return Fraction(value)
    denominator = rng.choice((2, 3, 4, 5))
    return Fraction(rng.randint(1, 2 * denominator), denominator)


def _display(value: Fraction) -> str:
    return core.parenthesized(core.plain_fraction_display(value))


def _equality_for(property_name, a, b, c):
    da, db, dc = _display(a), _display(b), _display(c)
    if property_name == "commutativity_add":
        return f"{da} + {db} = {db} + {da}"
    if property_name == "commutativity_mul":
        return f"{da} \\cdot {db} = {db} \\cdot {da}"
    if property_name == "associativity_add":
        return f"({da} + {db}) + {dc} = {da} + ({db} + {dc})"
    if property_name == "associativity_mul":
        return f"({da} \\cdot {db}) \\cdot {dc} = " \
               f"{da} \\cdot ({db} \\cdot {dc})"
    return (f"{da} \\cdot ({db} + {dc}) = "
            f"{da} \\cdot {db} + {da} \\cdot {dc}")


_PROPERTY_RULES = {
    "commutativity_add": "zamjena mjesta sabiraka ne mijenja zbir",
    "commutativity_mul": "zamjena mjesta faktora ne mijenja proizvod",
    "associativity_add": "grupisanje sabiraka zagradama ne mijenja zbir",
    "associativity_mul": "grupisanje faktora zagradama ne mijenja proizvod",
    "distributivity": "množenje zbira brojem raspoređuje se na svaki sabirak",
}


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    domain = parameters["number_domain"]
    allowed = tuple(parameters["properties"])
    for _ in range(60):
        try:
            target = rng.choice(allowed)
            ask_example = rng.random() < 0.5
            return (_recognize_example(rng, level, lesson_id, lesson_title,
                                       domain, target)
                    if ask_example else
                    _name_property(rng, level, lesson_id, lesson_title,
                                   domain, target, allowed))
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _package(lesson_id, lesson_title, concept, level, question, option_texts,
             hints, solution, answer_display, signature):
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="operation_property_recognition", operation=concept,
        level=level, question=question, answer_value=None,
        answer_display=answer_display, distractor_values=(), hints=hints,
        solution=solution, signature_parameters=signature,
        required_conditions=[concept], relevant_objects=["svojstvo operacije"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")


def _recognize_example(rng, level, lesson_id, lesson_title, domain, target):
    a = _operand(rng, domain, level)
    b = _operand(rng, domain, level)
    c = _operand(rng, domain, level)
    if len({a, b, c}) < 3:
        raise DeterministicGenerationError("operandi nisu različiti")
    correct = _equality_for(target, a, b, c)
    others = [name for name in _PROPERTY_NAMES if name != target]
    rng.shuffle(others)
    wrong = [_equality_for(name, a, b, c) for name in others[:3]]
    option_texts = (f"${correct}$", *(f"${w}$" for w in wrong))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("jednakosti nisu jedinstvene")
    property_word = _PROPERTY_NAMES[target]
    question = (f"Koja jednakost prikazuje svojstvo: {property_word}?")
    hints = (
        f"Podsjeti se: {property_word} znači da "
        f"{_PROPERTY_RULES[target]}.",
        "Sve ponuđene jednakosti su numerički tačne — traži ONU čiji oblik "
        "odgovara imenovanom svojstvu.",
        "Uporedi lijevu i desnu stranu: šta se tačno promijenilo?",
    )
    solution = (f"Svojstvo „{property_word}“ ({_PROPERTY_RULES[target]}) "
                f"prikazuje jednakost ${correct}$. Ostale jednakosti su "
                "tačne, ali ilustruju druga svojstva.")
    return _package(lesson_id, lesson_title, "recognize_example", level,
                    question, option_texts, hints, solution, correct,
                    [("property", target), ("a", str(a)), ("b", str(b)),
                     ("c", str(c))])


def _name_property(rng, level, lesson_id, lesson_title, domain, target,
                   allowed):
    a = _operand(rng, domain, level)
    b = _operand(rng, domain, level)
    c = _operand(rng, domain, level)
    if len({a, b, c}) < 3:
        raise DeterministicGenerationError("operandi nisu različiti")
    equality = _equality_for(target, a, b, c)
    correct = _PROPERTY_NAMES[target]
    others = [name for key, name in _PROPERTY_NAMES.items() if key != target]
    rng.shuffle(others)
    option_texts = (correct, *others[:3])
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("svojstva nisu jedinstvena")
    question = (f"Koje svojstvo računskih operacija prikazuje jednakost "
                f"${equality}$?")
    hints = (
        "Uporedi lijevu i desnu stranu jednakosti: mijenja li se poredak, "
        "grupisanje ili se množenje raspoređuje na sabirke?",
        "Promjena PORETKA je komutativnost; promjena ZAGRADA je "
        "asocijativnost; raspoređivanje množenja na zbir je distributivnost.",
        f"Ovdje: {_PROPERTY_RULES[target]}.",
    )
    solution = (f"Jednakost ${equality}$ prikazuje svojstvo „{correct}“ — "
                f"{_PROPERTY_RULES[target]}.")
    return _package(lesson_id, lesson_title, "name_property", level,
                    question, option_texts, hints, solution, correct,
                    [("property", target), ("a", str(a)), ("b", str(b)),
                     ("c", str(c))])
