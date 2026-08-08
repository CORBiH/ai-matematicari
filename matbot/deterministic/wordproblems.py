"""Deterministički strukturisani tekstualni zadaci (Batch #4, Prioritet 2).

PRINCIP: ČINJENICE PRIJE PROZE. Svaki zadatak nastaje ovim redom:

    1. server izvuče egzaktne brojeve (WordProblemFacts);
    2. jezgro (matbot/mathkernel/wordfacts.py) izračuna kanonski odgovor;
    3. TEK TADA se sastavlja bosanska proza, nagovještaji, rješenje i opcije.

Proza se nikad ne parsira radi odgovora. Deterministički RENDER-AUDIT ipak
dokazuje suprotan smjer: svaki matematički segment proze mora biti ili prikaz
neke IR veličine ili izričito dozvoljena strukturna vrijednost — proza ne
smije sadržavati nijedan broj kojeg nema u činjenicama, niti smije izostaviti
ijednu poznatu veličinu. Neuspjeh audita OBARA generisanje (fail-closed).

Varijacija je isključivo KOZMETIČKA (imena, predmeti); semantika ostaje
identična IR-u. Jedna semantička porodica ``structured_word_problem``;
razlike među lekcijama nose parametri (``problem_types``) — nikad ID lekcije.
"""
import random
import re
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.mathkernel import wordfacts
from matbot.mathkernel.wordfacts import (Quantity, WordProblemError,
                                         WordProblemFacts)
from matbot.tutor.schema import DifficultyEvidence

FAMILY_IDS = ("structured_word_problem",)
GENERATOR_VERSION = "detwords-1"

_SUPPORTED_TYPES = frozenset(wordfacts.SUPPORTED_TYPES)


def supports(parameters) -> bool:
    parameters = parameters or {}
    problem_types = set(parameters.get("problem_types") or ())
    return bool(problem_types) and problem_types <= _SUPPORTED_TYPES


_NAMES = ("Amar", "Lejla", "Emir", "Sara", "Tarik", "Amina", "Vedad", "Hana")
_OBJECTS = ("olovaka", "klikera", "sličica", "jabuka", "bombona", "knjiga")
_GROUP_WORDS = ("kutije", "grupe", "police", "korpe")


def _ev(steps, cond, ops, repr_changes=0):
    return DifficultyEvidence(
        reasoning_steps=steps, condition_count=cond, operation_count=ops,
        representation_change_count=repr_changes, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


# Iskren dokaz težine po (tipu, nivou) — sistemske i formulske priče nose
# lekcijski-relativan profil (dva uslova / tri povezane operacije su SAMA
# vještina tih lekcija), ostale ostaju u globalnoj rubrici.
_EVIDENCE = {
    "default": {1: _ev(1, 1, 1), 2: _ev(2, 1, 2), 3: _ev(3, 2, 3)},
    "system": {1: _ev(2, 2, 2, 1), 2: _ev(3, 3, 4, 1), 3: _ev(4, 3, 5, 2)},
    "formula": {1: _ev(1, 1, 3), 2: _ev(2, 1, 3), 3: _ev(3, 2, 5, 1)},
}
_EVIDENCE_KIND = {
    "sum_difference_system": "system", "sum_multiple_system": "system",
    "pythagoras_distance": "formula", "pythagoras_leg": "formula",
    "box_volume": "formula", "cube_surface": "formula",
}


_MATH_SEGMENT_RE = re.compile(r"\$([^$]+)\$")


def _assert_prose_matches(question, required_displays, allowed_extra=()):
    """RENDER-AUDIT: proza ⇄ IR. Svaki segment mora biti dozvoljen, svaka
    poznata veličina mora biti prisutna."""
    segments = _MATH_SEGMENT_RE.findall(question)
    allowed = set(required_displays) | set(allowed_extra)
    for segment in segments:
        if segment not in allowed:
            raise DeterministicGenerationError(
                f"proza sadrži vrijednost izvan činjenica: {segment!r}")
    for display in required_displays:
        if display not in segments:
            raise DeterministicGenerationError(
                f"proza ne sadrži poznatu veličinu: {display!r}")


def _int_display(value: Fraction) -> str:
    if value.denominator != 1:
        raise DeterministicGenerationError("očekivan cio broj")
    return str(value.numerator)


def _money_display(value: Fraction) -> str:
    return core.decimal_display(value)


def _build(lesson_id, lesson_title, problem_type, level, question, facts,
           answer_value, answer_display, distractors, hints, solution,
           display_of=None, accepted=()):
    evidence_kind = _EVIDENCE_KIND.get(problem_type, "default")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="structured_word_problem", operation=problem_type,
        level=level, question=question, answer_value=answer_value,
        answer_display=answer_display, distractor_values=distractors,
        hints=hints, solution=solution,
        signature_parameters=[("type", problem_type)] + [
            (quantity.name, str(quantity.value)) for quantity in facts.known],
        required_conditions=list(facts.relationships) or [problem_type],
        relevant_objects=list(facts.entities),
        generator_version=GENERATOR_VERSION, display_of=display_of,
        accepted_answers=accepted,
        evidence=_EVIDENCE[evidence_kind][level])


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    builders = {
        "equal_sharing": _equal_sharing,
        "sharing_remainder": _sharing_remainder,
        "fraction_of_quantity": _fraction_of_quantity,
        "fraction_remainder": _fraction_remainder,
        "money_total": _money_total,
        "money_change": _money_change,
        "signed_change": _signed_change,
        "number_equation": _number_equation,
        "sum_difference_system": _sum_difference_system,
        "sum_multiple_system": _sum_multiple_system,
        "box_volume": _box_volume,
        "cube_surface": _cube_surface,
        "pythagoras_distance": _pythagoras_distance,
        "pythagoras_leg": _pythagoras_leg,
    }
    for _ in range(80):
        try:
            problem_type = rng.choice(tuple(parameters["problem_types"]))
            return builders[problem_type](rng, level, lesson_id, lesson_title,
                                          problem_type)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


# ---------------------------------------------------------------------------
# DIJELJENJE NA JEDNAKE DIJELOVE / OSTATAK (djeljivost)
# ---------------------------------------------------------------------------

def _equal_sharing(rng, level, lesson_id, lesson_title, problem_type):
    groups = rng.randint(3, 6 if level == 1 else 9)
    per_group = rng.randint(2, 9 if level == 1 else 15 if level == 2 else 40)
    total = groups * per_group
    facts = WordProblemFacts(
        semantic_type="equal_sharing",
        entities=(rng.choice(_NAMES), rng.choice(_OBJECTS)),
        known=(Quantity("total", Fraction(total)),
               Quantity("groups", Fraction(groups))),
        unknown="per_group", relationships=("total = groups · per_group",))
    solved = wordfacts.solve(facts)
    name, objects = facts.entities
    group_word = rng.choice(_GROUP_WORDS)
    question = (f"{name} ima ${total}$ {objects} i želi ih rasporediti u "
                f"${groups}$ jednakih {group_word} bez ostatka. Koliko "
                f"{objects} ide u svaku?")
    _assert_prose_matches(question, [str(total), str(groups)])
    answer = solved.answer.value
    hints = (
        "Kad se cjelina dijeli na jednake dijelove, traženi broj je količnik.",
        f"Podijeli: ${total} : {groups}$.",
        f"Provjeri množenjem: rezultat puta ${groups}$ mora dati ${total}$.",
    )
    solution = (f"${total} : {groups} = {_int_display(answer)}$, pa u svaku "
                f"ide ${_int_display(answer)}$ {objects}. Provjera: "
                f"${_int_display(answer)} \\cdot {groups} = {total}$.")
    distractors = [answer + 1, answer - 1, Fraction(total - groups),
                   Fraction(groups), answer + 2]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


def _sharing_remainder(rng, level, lesson_id, lesson_title, problem_type):
    groups = rng.randint(3, 6 if level == 1 else 9)
    per_group = rng.randint(2, 9 if level < 3 else 25)
    remainder = rng.randint(1, groups - 1)
    total = groups * per_group + remainder
    facts = WordProblemFacts(
        semantic_type="sharing_remainder",
        entities=(rng.choice(_NAMES), rng.choice(_OBJECTS)),
        known=(Quantity("total", Fraction(total)),
               Quantity("groups", Fraction(groups))),
        unknown="remainder",
        relationships=("total = groups · per_group + remainder",))
    solved = wordfacts.solve(facts)
    name, objects = facts.entities
    question = (f"{name} dijeli ${total}$ {objects} na ${groups}$ jednakih "
                f"dijelova. Koliko {objects} OSTANE nepodijeljeno?")
    _assert_prose_matches(question, [str(total), str(groups)])
    answer = solved.answer.value
    quotient = solved.auxiliary["quotient"]
    hints = (
        "Podijeli s ostatkom: ostatak je ono što ne stane u jednake dijelove.",
        f"Koliko puta ${groups}$ stane u ${total}$?",
        f"Stane ${_int_display(quotient)}$ puta — ostatak je razlika do "
        f"${total}$.",
    )
    solution = (f"${total} : {groups}$ daje količnik "
                f"${_int_display(quotient)}$ i ostatak "
                f"${_int_display(answer)}$, jer je "
                f"${groups} \\cdot {_int_display(quotient)} + "
                f"{_int_display(answer)} = {total}$.")
    distractors = [answer + 1, Fraction(groups - int(answer)), quotient,
                   answer + 2, Fraction(0)]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


# ---------------------------------------------------------------------------
# RAZLOMAK OD VELIČINE
# ---------------------------------------------------------------------------

def _fraction_pool(rng, level):
    if level == 1:
        q = rng.choice((2, 3, 4, 5))
        p = 1
    else:
        q = rng.choice((3, 4, 5, 6, 8))
        p = rng.randint(1, q - 1)
        while Fraction(p, q).denominator == 1:
            p = rng.randint(1, q - 1)
    return Fraction(p, q)


def _fraction_of_quantity(rng, level, lesson_id, lesson_title, problem_type):
    fraction = _fraction_pool(rng, level)
    per_unit = rng.randint(2, 8 if level < 3 else 20)
    total = fraction.denominator * per_unit
    facts = WordProblemFacts(
        semantic_type="fraction_of_quantity",
        entities=(rng.choice(_NAMES), rng.choice(_OBJECTS)),
        known=(Quantity("total", Fraction(total)),
               Quantity("fraction", fraction)),
        unknown="part", relationships=("part = fraction · total",))
    solved = wordfacts.solve(facts)
    name, objects = facts.entities
    fraction_display = core.plain_fraction_display(fraction)
    question = (f"{name} ima ${total}$ {objects} i pokloni "
                f"${fraction_display}$ od toga. Koliko {objects} je "
                "poklonjeno?")
    _assert_prose_matches(question, [str(total), fraction_display])
    answer = solved.answer.value
    hints = (
        "Dio cjeline se računa množenjem razlomka i cjeline.",
        f"Izračunaj ${fraction_display} \\cdot {total}$.",
        f"Podijeli ${total}$ imeniocem pa pomnoži brojnikom.",
    )
    solution = (f"${fraction_display} \\cdot {total} = "
                f"{_int_display(answer)}$, pa je poklonjeno "
                f"${_int_display(answer)}$ {objects}.")
    distractors = [Fraction(total) - answer, answer + fraction.denominator,
                   Fraction(total // fraction.denominator), answer + 1]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


def _fraction_remainder(rng, level, lesson_id, lesson_title, problem_type):
    fraction = _fraction_pool(rng, max(level, 2))
    per_unit = rng.randint(2, 8 if level < 3 else 15)
    total = fraction.denominator * per_unit
    facts = WordProblemFacts(
        semantic_type="fraction_remainder",
        entities=(rng.choice(_NAMES), rng.choice(_OBJECTS)),
        known=(Quantity("total", Fraction(total)),
               Quantity("fraction", fraction)),
        unknown="remainder",
        relationships=("part = fraction · total", "remainder = total - part"))
    solved = wordfacts.solve(facts)
    name, objects = facts.entities
    fraction_display = core.plain_fraction_display(fraction)
    question = (f"{name} je potrošio ${fraction_display}$ od svojih "
                f"${total}$ {objects}. Koliko {objects} mu je OSTALO?")
    _assert_prose_matches(question, [str(total), fraction_display])
    answer = solved.answer.value
    part = solved.auxiliary["part"]
    hints = (
        "Prvo izračunaj potrošeni dio, pa ga oduzmi od cjeline.",
        f"Potrošeno: ${fraction_display} \\cdot {total} = "
        f"{_int_display(part)}$.",
        f"Ostalo je ${total}$ minus potrošeno.",
    )
    solution = (f"Potrošeno je ${fraction_display} \\cdot {total} = "
                f"{_int_display(part)}$, pa je ostalo "
                f"${total} - {_int_display(part)} = {_int_display(answer)}$ "
                f"{objects}.")
    distractors = [part, answer + 1, answer - 1, Fraction(total)]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


# ---------------------------------------------------------------------------
# NOVAC (decimalni brojevi, egzaktno)
# ---------------------------------------------------------------------------

def _price(rng, level):
    whole = rng.randint(1, 9 if level == 1 else 30)
    cents = rng.choice((0, 25, 50, 75) if level < 3 else
                       (10, 20, 25, 30, 40, 50, 60, 75, 80, 90))
    return Fraction(whole * 100 + cents, 100)


def _money_total(rng, level, lesson_id, lesson_title, problem_type):
    price_a = _price(rng, level)
    price_b = _price(rng, level)
    count_a = Fraction(rng.randint(2, 4 if level < 3 else 7))
    facts = WordProblemFacts(
        semantic_type="money_total",
        entities=(rng.choice(_NAMES), "sveska", "olovka"),
        known=(Quantity("price_a", price_a, "KM"),
               Quantity("price_b", price_b, "KM"),
               Quantity("count_a", count_a)),
        unknown="total",
        relationships=("total = price_a · count_a + price_b",))
    solved = wordfacts.solve(facts)
    name = facts.entities[0]
    price_a_text = _money_display(price_a)
    price_b_text = _money_display(price_b)
    count_text = _int_display(count_a)
    question = (f"{name} kupuje ${count_text}$ svesaka po cijeni od "
                f"${price_a_text}$ KM i jednu olovku od ${price_b_text}$ KM. "
                "Koliko ukupno plaća?")
    _assert_prose_matches(question, [count_text, price_a_text, price_b_text])
    answer = solved.answer.value
    subtotal = solved.auxiliary["subtotal"]
    hints = (
        "Ukupan iznos je zbir svih kupljenih artikala.",
        f"Sveske: ${count_text} \\cdot {price_a_text}$ KM.",
        f"Na to dodaj cijenu olovke: ${price_b_text}$ KM.",
    )
    solution = (f"Sveske koštaju ${count_text} \\cdot {price_a_text} = "
                f"{_money_display(subtotal)}$ KM, pa je ukupno "
                f"${_money_display(subtotal)} + {price_b_text} = "
                f"{_money_display(answer)}$ KM.")
    distractors = [price_a + price_b, subtotal,
                   answer + Fraction(1, 2), answer - Fraction(1, 4),
                   (price_a + price_b) * count_a]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _money_display(answer), distractors, hints,
                  solution, display_of=_money_display)


def _money_change(rng, level, lesson_id, lesson_title, problem_type):
    price_a = _price(rng, level)
    price_b = _price(rng, level)
    spent = price_a + price_b
    paid_base = int(spent) + rng.randint(1, 3)
    paid = Fraction(paid_base * (10 if level >= 2 and paid_base <= 5 else 1))
    if paid <= spent:
        paid = Fraction(int(spent) + 5)
    facts = WordProblemFacts(
        semantic_type="money_change",
        entities=(rng.choice(_NAMES), "hljeb", "mlijeko"),
        known=(Quantity("paid", paid, "KM"),
               Quantity("price_a", price_a, "KM"),
               Quantity("price_b", price_b, "KM")),
        unknown="change",
        relationships=("change = paid - (price_a + price_b)",))
    solved = wordfacts.solve(facts)
    name = facts.entities[0]
    paid_text = _money_display(paid)
    price_a_text = _money_display(price_a)
    price_b_text = _money_display(price_b)
    question = (f"{name} plaća hljeb od ${price_a_text}$ KM i mlijeko od "
                f"${price_b_text}$ KM novčanicom od ${paid_text}$ KM. "
                "Koliko kusura dobija?")
    _assert_prose_matches(question, [paid_text, price_a_text, price_b_text])
    answer = solved.answer.value
    spent_value = solved.auxiliary["spent"]
    hints = (
        "Kusur je razlika između plaćenog iznosa i ukupne cijene.",
        f"Ukupna cijena: ${price_a_text} + {price_b_text}$ KM.",
        f"Oduzmi ukupnu cijenu od ${paid_text}$ KM.",
    )
    solution = (f"Ukupno je potrošeno ${price_a_text} + {price_b_text} = "
                f"{_money_display(spent_value)}$ KM, pa je kusur "
                f"${paid_text} - {_money_display(spent_value)} = "
                f"{_money_display(answer)}$ KM.")
    distractors = [spent_value, answer + Fraction(1, 2),
                   answer - Fraction(1, 4), paid - price_a]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _money_display(answer), distractors, hints,
                  solution, display_of=_money_display)


# ---------------------------------------------------------------------------
# CIJELI BROJEVI — promjene s predznakom (temperatura)
# ---------------------------------------------------------------------------

def _signed_change(rng, level, lesson_id, lesson_title, problem_type):
    start = rng.randint(-10, 10)
    change_count = 1 if level == 1 else 2
    changes = []
    for _ in range(change_count):
        delta = 0
        while delta == 0:
            delta = rng.randint(-9, 9)
        changes.append(delta)
    known = [Quantity("start", Fraction(start), "°C")]
    known.extend(Quantity(f"change_{index}", Fraction(delta), "°C")
                 for index, delta in enumerate(changes))
    facts = WordProblemFacts(
        semantic_type="signed_change", entities=("temperatura",),
        known=tuple(known), unknown="final",
        relationships=("final = start + sve promjene",))
    solved = wordfacts.solve(facts)
    start_text = str(start)
    piece_texts = []
    prose_parts = []
    for delta in changes:
        magnitude = abs(delta)
        piece_texts.append(str(magnitude))
        prose_parts.append(
            (f"porasla za ${magnitude}$" if delta > 0
             else f"pala za ${magnitude}$") + " stepeni")
    question = (f"Jutarnja temperatura bila je ${start_text}$ °C. Tokom dana "
                f"je najprije {prose_parts[0]}"
                + (f", a zatim {prose_parts[1]}" if len(prose_parts) > 1 else "")
                + ". Kolika je temperatura na kraju?")
    _assert_prose_matches(question, [start_text] + piece_texts)
    answer = solved.answer.value
    chain = f"{core.parenthesized(str(start))}"
    for delta in changes:
        chain += f" + {core.parenthesized(str(delta))}"
    hints = (
        "Porast dodaje, pad oduzima — radi redom, promjenu po promjenu.",
        f"Zapiši izraz: ${chain}$.",
        "Saberi cijele brojeve poštujući predznake.",
    )
    solution = (f"${chain} = {_int_display(answer)}$, pa je na kraju "
                f"${_int_display(answer)}$ °C.")
    distractors = [answer + 1, answer - 1, -answer,
                   Fraction(start + sum(abs(d) for d in changes))]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


# ---------------------------------------------------------------------------
# LINEARNA JEDNAČINA IZ PRIČE
# ---------------------------------------------------------------------------

def _number_equation(rng, level, lesson_id, lesson_title, problem_type):
    a = rng.randint(2, 4 if level == 1 else 9)
    x = rng.randint(2, 9 if level < 3 else 20)
    b = rng.randint(1, 9 if level == 1 else 30)
    if level >= 2 and rng.random() < 0.5:
        b = -b
    c = a * x + b
    facts = WordProblemFacts(
        semantic_type="number_equation", entities=("zamišljeni broj",),
        known=(Quantity("a", Fraction(a)), Quantity("b", Fraction(b)),
               Quantity("c", Fraction(c))),
        unknown="x", relationships=("a · x + b = c",))
    solved = wordfacts.solve(facts)
    b_word = "dodaš" if b >= 0 else "oduzmeš"
    b_text = str(abs(b))
    question = (f"Kad zamišljeni broj pomnožiš sa ${a}$ i {b_word} "
                f"${b_text}$, dobiješ ${c}$. Koji je to broj?")
    _assert_prose_matches(question, [str(a), b_text, str(c)])
    answer = solved.answer.value
    sign = "+" if b >= 0 else "-"
    hints = (
        "Označi traženi broj sa x i zapiši priču kao jednačinu.",
        f"Jednačina glasi: ${a}x {sign} {b_text} = {c}$.",
        f"Prebaci ${b_text}$ na desnu stranu pa podijeli sa ${a}$.",
    )
    solution = (f"Iz ${a}x {sign} {b_text} = {c}$ slijedi "
                f"${a}x = {c - b}$, pa je $x = {_int_display(answer)}$. "
                f"Provjera: ${a} \\cdot {_int_display(answer)} "
                f"{sign} {b_text} = {c}$.")
    distractors = [answer + 1, answer - 1, Fraction(c - b),
                   Fraction(c + b, a) if (c + b) % a == 0 else answer + 2]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


# ---------------------------------------------------------------------------
# SISTEM IZ PRIČE — zbir/razlika i zbir/višekratnik
# ---------------------------------------------------------------------------

def _sum_difference_system(rng, level, lesson_id, lesson_title, problem_type):
    smaller = rng.randint(2, 15 if level < 3 else 40)
    difference = rng.randint(1, 9 if level < 3 else 25)
    larger = smaller + difference
    total = smaller + larger
    facts = WordProblemFacts(
        semantic_type="sum_difference_system", entities=("dva broja",),
        known=(Quantity("sum", Fraction(total)),
               Quantity("difference", Fraction(difference))),
        unknown="larger",
        relationships=("x + y = sum", "x - y = difference"))
    solved = wordfacts.solve(facts)
    question = (f"Zbir dva broja je ${total}$, a njihova razlika je "
                f"${difference}$. Koliki je VEĆI od ta dva broja?")
    _assert_prose_matches(question, [str(total), str(difference)])
    answer = solved.answer.value
    smaller_value = solved.auxiliary["smaller"]
    hints = (
        "Postavi sistem: zbir daje jednu, razlika drugu jednačinu.",
        f"Zapiši: $x + y = {total}$ i $x - y = {difference}$.",
        "Saberi jednačine — y nestaje, ostaje jednačina po x.",
    )
    solution = (f"Iz $x + y = {total}$ i $x - y = {difference}$ sabiranjem "
                f"slijedi $2x = {total + difference}$, pa je veći broj "
                f"$x = {_int_display(answer)}$, a manji "
                f"$y = {_int_display(smaller_value)}$. Provjera: "
                f"${_int_display(answer)} + {_int_display(smaller_value)} "
                f"= {total}$.")
    distractors = [smaller_value, Fraction(total - difference), answer + 1,
                   Fraction(total) / 2 if total % 2 == 0 else answer - 1]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


def _sum_multiple_system(rng, level, lesson_id, lesson_title, problem_type):
    factor = rng.randint(2, 4 if level < 3 else 6)
    smaller = rng.randint(2, 12 if level < 3 else 30)
    total = smaller * (factor + 1)
    facts = WordProblemFacts(
        semantic_type="sum_multiple_system", entities=("dva broja",),
        known=(Quantity("sum", Fraction(total)),
               Quantity("factor", Fraction(factor))),
        unknown="smaller",
        relationships=("x + y = sum", "y = factor · x"))
    solved = wordfacts.solve(facts)
    question = (f"Zbir dva broja je ${total}$, a jedan od njih je ${factor}$ "
                "puta veći od drugog. Koliki je MANJI broj?")
    _assert_prose_matches(question, [str(total), str(factor)])
    answer = solved.answer.value
    larger_value = solved.auxiliary["larger"]
    hints = (
        "Označi manji broj sa x — veći je onda njegov višekratnik.",
        f"Zapiši: $x + {factor}x = {total}$.",
        f"Saberi članove pa podijeli sa ${factor + 1}$.",
    )
    solution = (f"Iz $x + {factor}x = {total}$ slijedi "
                f"${factor + 1}x = {total}$, pa je manji broj "
                f"$x = {_int_display(answer)}$, a veći "
                f"${_int_display(larger_value)}$. Provjera: "
                f"${_int_display(answer)} + {_int_display(larger_value)} = "
                f"{total}$.")
    distractors = [larger_value, answer + 1, answer - 1,
                   Fraction(total - factor)]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


# ---------------------------------------------------------------------------
# GEOMETRIJSKA PRIČA — zapremina/površina (egzaktno, cijeli brojevi)
# ---------------------------------------------------------------------------

def _box_volume(rng, level, lesson_id, lesson_title, problem_type):
    a = rng.randint(2, 6 if level == 1 else 12)
    b = rng.randint(2, 6 if level == 1 else 12)
    c = rng.randint(2, 6 if level < 3 else 15)
    facts = WordProblemFacts(
        semantic_type="box_volume", entities=("akvarij",),
        known=(Quantity("a", Fraction(a), "dm"), Quantity("b", Fraction(b), "dm"),
               Quantity("c", Fraction(c), "dm")),
        unknown="volume", relationships=("V = a · b · c",))
    solved = wordfacts.solve(facts)
    question = (f"Akvarij oblika kvadra dug je ${a}$ dm, širok ${b}$ dm i "
                f"visok ${c}$ dm. Koliko decimetara kubnih vode može "
                "primiti kad je pun?")
    _assert_prose_matches(question, [str(a), str(b), str(c)])
    answer = solved.answer.value
    base = solved.auxiliary["base"]
    hints = (
        "Zapremina kvadra je proizvod njegove tri dimenzije.",
        f"Prvo pomnoži: ${a} \\cdot {b} = {_int_display(base)}$.",
        f"Rezultat pomnoži visinom ${c}$.",
    )
    solution = (f"$V = {a} \\cdot {b} \\cdot {c} = "
                f"{_int_display(base)} \\cdot {c} = {_int_display(answer)}$, "
                f"pa akvarij prima ${_int_display(answer)}$ dm³ vode.")
    distractors = [Fraction(a + b + c), Fraction((a + b) * c),
                   answer + a, Fraction(2 * (a * b + b * c + a * c))]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


def _cube_surface(rng, level, lesson_id, lesson_title, problem_type):
    a = rng.randint(2, 6 if level == 1 else 12 if level == 2 else 20)
    facts = WordProblemFacts(
        semantic_type="cube_surface", entities=("kutija",),
        known=(Quantity("a", Fraction(a), "cm"),),
        unknown="surface", relationships=("P = 6 · a · a",))
    solved = wordfacts.solve(facts)
    question = (f"Kutija oblika kocke ima ivicu dužine ${a}$ cm. Koliko "
                "kvadratnih centimetara kartona treba za svih šest strana?")
    _assert_prose_matches(question, [str(a)])
    answer = solved.answer.value
    face = solved.auxiliary["face"]
    hints = (
        "Kocka ima šest jednakih strana — svaka je kvadrat.",
        f"Jedna strana ima površinu ${a} \\cdot {a} = {_int_display(face)}$.",
        "Pomnoži površinu jedne strane sa šest.",
    )
    solution = (f"Jedna strana: ${a} \\cdot {a} = {_int_display(face)}$, pa "
                f"je ukupno $6 \\cdot {_int_display(face)} = "
                f"{_int_display(answer)}$ cm² kartona.")
    distractors = [face, Fraction(4 * a * a), Fraction(a * a * a),
                   Fraction(12 * a)]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


# ---------------------------------------------------------------------------
# PRAKTIČNA PITAGORA — merdevine / dijagonala (Pitagorine trojke)
# ---------------------------------------------------------------------------

_TRIPLES_SMALL = ((3, 4, 5), (6, 8, 10), (5, 12, 13), (9, 12, 15))
_TRIPLES_LARGE = ((8, 15, 17), (12, 16, 20), (7, 24, 25), (20, 21, 29),
                  (18, 24, 30), (10, 24, 26))


def _triple(rng, level):
    pool = _TRIPLES_SMALL if level == 1 else _TRIPLES_SMALL + _TRIPLES_LARGE
    return rng.choice(pool)


def _pythagoras_distance(rng, level, lesson_id, lesson_title, problem_type):
    a, b, c = _triple(rng, level)
    facts = WordProblemFacts(
        semantic_type="pythagoras_distance", entities=("igralište",),
        known=(Quantity("leg_a", Fraction(a), "m"),
               Quantity("leg_b", Fraction(b), "m")),
        unknown="hypotenuse", relationships=("c² = a² + b²",))
    solved = wordfacts.solve(facts)
    question = (f"Pravougaono igralište dugo je ${a}$ m i široko ${b}$ m. "
                "Kolika je dužina staze koja ga presijeca po dijagonali?")
    _assert_prose_matches(question, [str(a), str(b)])
    answer = solved.answer.value
    square = solved.auxiliary["square"]
    hints = (
        "Dijagonala pravougaonika je hipotenuza pravouglog trougla čije su "
        "katete stranice igrališta.",
        f"Primijeni Pitagorinu teoremu: $d^{{2}} = {a}^{{2}} + {b}^{{2}}$.",
        f"Izračunaj zbir kvadrata pa potraži broj čiji je kvadrat "
        f"${_int_display(square)}$.",
    )
    solution = (f"$d^{{2}} = {a}^{{2}} + {b}^{{2}} = {a * a} + {b * b} = "
                f"{_int_display(square)}$, pa je "
                f"$d = {_int_display(answer)}$ m, jer je "
                f"${_int_display(answer)}^{{2}} = {_int_display(square)}$.")
    distractors = [Fraction(a + b), answer + 1, answer - 1,
                   Fraction(a * b)]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)


def _pythagoras_leg(rng, level, lesson_id, lesson_title, problem_type):
    a, b, c = _triple(rng, max(level, 2))
    facts = WordProblemFacts(
        semantic_type="pythagoras_leg", entities=("merdevine", "zid"),
        known=(Quantity("hypotenuse", Fraction(c), "m"),
               Quantity("leg_a", Fraction(a), "m")),
        unknown="leg_b", relationships=("b² = c² - a²",))
    solved = wordfacts.solve(facts)
    question = (f"Merdevine duge ${c}$ m naslonjene su na zid tako da im je "
                f"podnožje udaljeno ${a}$ m od zida. Do koje visine na zidu "
                "dosežu?")
    _assert_prose_matches(question, [str(c), str(a)])
    answer = solved.answer.value
    square = solved.auxiliary["square"]
    hints = (
        "Merdevine, zid i tlo čine pravougli trougao — merdevine su "
        "hipotenuza.",
        f"Iz Pitagorine teoreme: $h^{{2}} = {c}^{{2}} - {a}^{{2}}$.",
        f"Izračunaj razliku kvadrata pa potraži broj čiji je kvadrat "
        f"${_int_display(square)}$.",
    )
    solution = (f"$h^{{2}} = {c}^{{2}} - {a}^{{2}} = {c * c} - {a * a} = "
                f"{_int_display(square)}$, pa merdevine dosežu do "
                f"$h = {_int_display(answer)}$ m, jer je "
                f"${_int_display(answer)}^{{2}} = {_int_display(square)}$.")
    distractors = [Fraction(c - a), Fraction(c + a), answer + 1,
                   answer - 1]
    return _build(lesson_id, lesson_title, problem_type, level, question,
                  facts, answer, _int_display(answer), distractors, hints,
                  solution, display_of=_int_display)
