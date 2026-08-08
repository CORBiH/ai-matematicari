"""Determinističke činjenice o pravoj i linearnoj funkciji — TEKSTUALNO.

Jedna semantička porodica (Batch #4, Prioritet 7): ``coordinate_line_direct``.

  • ``point_distance``    — udaljenost dvije tačke zadate KOORDINATAMA U
    TEKSTU (Pitagorine trojke → cio rezultat); crtanje nije potrebno;
  • ``slope_meaning``     — značenje koeficijenta k (rastuća/opadajuća,
    promjena y po jediničnom porastu x);
  • ``intercept_meaning`` — značenje slobodnog člana n (presjek s y-osom);
  • ``implicit_explicit`` — prevođenje implicitnog oblika u eksplicitni;
  • ``parallel_lines``    — paralelne/podudarne prave preko k i n;
  • ``line_intersection`` — presjek dvije prave (egzaktan cjelobrojni par);
  • ``dependency_type``   — direktna, obrnuta ili linearna zavisnost iz
    JEDNAČINE (ne iz grafika).

Lekcije koje stvarno traže CRTANJE ili ČITANJE grafika ostaju vizuelne i
nisu u obimu ove porodice. MATEMATIČKI AUTORITET: egzaktni razlomci; svaka
tvrdnja (paralelnost, presjek, rast/pad) dokazana je uvrštavanjem.
"""
import random
from fractions import Fraction
from math import isqrt

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.tutor.schema import DifficultyEvidence

FAMILY_IDS = ("coordinate_line_direct",)
GENERATOR_VERSION = "detlinefacts-1"

_SUPPORTED_CONCEPTS = frozenset({
    "point_distance", "slope_meaning", "intercept_meaning",
    "implicit_explicit", "parallel_lines", "line_intersection",
    "dependency_type",
})


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    return bool(concepts) and concepts <= _SUPPORTED_CONCEPTS


def _ev(steps, cond, ops, repr_changes=0):
    return DifficultyEvidence(
        reasoning_steps=steps, condition_count=cond, operation_count=ops,
        representation_change_count=repr_changes, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False)


# Formula udaljenosti iskreno nosi tri povezane operacije već na nivou 1 —
# lekcija te vještine nosi lekcijski-relativni profil direktne primjene
# formule (data/routing_overrides.json + data/difficulty_profiles.json).
_DISTANCE_EVIDENCE = {1: _ev(1, 1, 3), 2: _ev(2, 1, 3), 3: _ev(3, 1, 4)}


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    builders = {
        "point_distance": _distance_package,
        "slope_meaning": _slope_package,
        "intercept_meaning": _intercept_package,
        "implicit_explicit": _implicit_package,
        "parallel_lines": _parallel_package,
        "line_intersection": _intersection_package,
        "dependency_type": _dependency_package,
    }
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            return builders[concept](rng, level, lesson_id, lesson_title, concept)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


def _package(lesson_id, lesson_title, concept, level, question, option_texts,
             hints, solution, answer_display, signature, evidence=None):
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="coordinate_line_direct", operation=concept, level=level,
        question=question, answer_value=None, answer_display=answer_display,
        distractor_values=(), hints=hints, solution=solution,
        signature_parameters=signature, required_conditions=[concept],
        relevant_objects=["prava"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="", evidence=evidence)


def _nonzero(rng, low, high):
    value = 0
    while value == 0:
        value = rng.randint(low, high)
    return value


_TRIPLE_LEGS = ((3, 4, 5), (6, 8, 10), (5, 12, 13), (8, 15, 17),
                (9, 12, 15), (12, 16, 20), (7, 24, 25))


def _distance_package(rng, level, lesson_id, lesson_title, concept):
    dx, dy, d = rng.choice(_TRIPLE_LEGS[:2] if level == 1 else _TRIPLE_LEGS)
    if rng.random() < 0.5:
        dx, dy = dy, dx
    x1 = rng.randint(-5, 5)
    y1 = rng.randint(-5, 5)
    x2, y2 = x1 + dx, y1 + dy
    question = (f"Date su tačke $A({x1}, {y1})$ i $B({x2}, {y2})$. Kolika je "
                "udaljenost između tačaka $A$ i $B$?")
    answer = d
    options = [f"${d}$"]
    for wrong in (dx + dy, abs(dx - dy) or d + 2, d + 1, d - 1):
        text = f"${wrong}$"
        if wrong > 0 and text not in options:
            options.append(text)
        if len(options) == 4:
            break
    if len(options) != 4:
        raise DeterministicGenerationError("nedovoljno udaljenosti")
    hints = (
        "Udaljenost tačaka računa se preko razlika koordinata i Pitagorine "
        "teoreme.",
        f"Razlike su ${x2} - {core.parenthesized(str(x1))} = {dx}$ i "
        f"${y2} - {core.parenthesized(str(y1))} = {dy}$.",
        f"Udaljenost je korijen zbira kvadrata: $d^{{2}} = {dx}^{{2}} + "
        f"{dy}^{{2}}$.",
    )
    solution = (f"$d^{{2}} = {dx}^{{2}} + {dy}^{{2}} = {dx * dx} + "
                f"{dy * dy} = {d * d}$, pa je $d = {d}$, jer je "
                f"${d}^{{2}} = {d * d}$.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    tuple(options), hints, solution, str(d),
                    [("A", f"({x1},{y1})"), ("B", f"({x2},{y2})")],
                    evidence=_DISTANCE_EVIDENCE[level])


def _line_display(k: Fraction, n: Fraction) -> str:
    parts = []
    if k != 0:
        if k == 1:
            parts.append("x")
        elif k == -1:
            parts.append("-x")
        else:
            parts.append(f"{core.fraction_display(k)}x")
    if n != 0 or not parts:
        sign = "+" if n >= 0 and parts else ""
        parts.append(f"{sign}{core.fraction_display(n)}")
    return "y = " + "".join(parts)


def _slope_package(rng, level, lesson_id, lesson_title, concept):
    ask_growth = rng.random() < 0.5
    if ask_growth:
        k = Fraction(_nonzero(rng, -5, 5))
        n = Fraction(rng.randint(-5, 5))
        line = _line_display(k, n)
        rising = k > 0
        correct = ("funkcija je rastuća jer je koeficijent uz x pozitivan"
                   if rising else
                   "funkcija je opadajuća jer je koeficijent uz x negativan")
        wrong = [
            ("funkcija je opadajuća jer je koeficijent uz x pozitivan"
             if rising else
             "funkcija je rastuća jer je koeficijent uz x negativan"),
            "rast ili pad funkcije određuje slobodni član",
            "funkcija nije ni rastuća ni opadajuća",
        ]
        question = (f"Data je linearna funkcija ${line}$. Koja tvrdnja o "
                    "njenom rastu je tačna?")
        explanation = (f"Koeficijent uz $x$ iznosi ${core.fraction_display(k)}$ "
                       f"i {'pozitivan je, pa funkcija raste' if rising else 'negativan je, pa funkcija opada'}: "
                       f"povećanjem $x$ za $1$ vrijednost $y$ se "
                       f"{'poveća' if rising else 'smanji'} za "
                       f"${core.fraction_display(abs(k))}$.")
        signature = [("k", str(k)), ("kind", "growth")]
    else:
        k = Fraction(_nonzero(rng, -6, 6))
        n = Fraction(rng.randint(-5, 5))
        line = _line_display(k, n)
        correct = (f"kad se x poveća za 1, y se promijeni za "
                   f"{core.fraction_display(k)}")
        wrong = [
            f"kad se x poveća za 1, y se promijeni za "
            f"{core.fraction_display(n)}",
            f"kad se x poveća za 1, y se promijeni za "
            f"{core.fraction_display(-k)}",
            "koeficijent k ne utiče na promjenu vrijednosti y",
        ]
        question = (f"Data je linearna funkcija ${line}$. Šta kazuje "
                    "koeficijent $k$ ove funkcije?")
        explanation = (f"Koeficijent $k = {core.fraction_display(k)}$ je "
                       "promjena vrijednosti $y$ pri porastu $x$ za "
                       f"jedan: iz $k \\cdot (x + 1) = kx + k$ vidi se da se "
                       f"$y$ promijeni tačno za ${core.fraction_display(k)}$.")
        signature = [("k", str(k)), ("kind", "unit_change")]
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("tvrdnje nisu jedinstvene")
    hints = (
        "Koeficijent uz x (nagib) određuje rast: pozitivan raste, negativan "
        "opada.",
        "Uporedi vrijednosti funkcije za dva uzastopna cijela x.",
        "Slobodni član ne utiče na rast — on samo pomjera grafik.",
    )
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, explanation, correct, signature)


def _intercept_package(rng, level, lesson_id, lesson_title, concept):
    k = Fraction(_nonzero(rng, -5, 5))
    n = Fraction(_nonzero(rng, -8, 8))
    line = _line_display(k, n)
    correct = f"u tački $(0, {core.fraction_display(n)})$"
    wrong = [f"u tački $({core.fraction_display(n)}, 0)$",
             f"u tački $(0, {core.fraction_display(k)})$",
             f"u tački $(0, {core.fraction_display(-n)})$"]
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("tačke nisu jedinstvene")
    question = (f"U kojoj tački grafik funkcije ${line}$ siječe $y$-osu?")
    hints = (
        "Na y-osi je uvijek x = 0.",
        f"Uvrsti $x = 0$ u ${line}$.",
        "Dobijeni y je druga koordinata tražene tačke.",
    )
    solution = (f"Za $x = 0$ vrijedi $y = {core.fraction_display(n)}$, pa "
                f"grafik siječe $y$-osu u tački "
                f"$(0, {core.fraction_display(n)})$ — slobodni član $n$ je "
                "upravo taj presjek.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("k", str(k)), ("n", str(n))])


def _implicit_package(rng, level, lesson_id, lesson_title, concept):
    b = rng.choice((1, 2, 3) if level > 1 else (1,))
    k_num = _nonzero(rng, -4, 4)
    n_num = _nonzero(rng, -6, 6)
    a = -k_num
    c = -n_num * b
    k = Fraction(k_num, b)
    n = Fraction(n_num)
    a_text = f"{a}x" if a not in (1, -1) else ("x" if a == 1 else "-x")
    b_text = f"{b}y" if b != 1 else "y"
    c_text = f"+ {c}" if c >= 0 else f"- {abs(c)}"
    implicit = f"{a_text} + {b_text} {c_text} = 0"
    correct = _line_display(k, n)
    wrong = [_line_display(-k, n), _line_display(k, -n),
             _line_display(Fraction(a), Fraction(c))]
    option_texts = (f"${correct}$", *(f"${w}$" for w in wrong))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("oblici nisu jedinstveni")
    question = (f"Prevedi pravu ${implicit}$ iz implicitnog u EKSPLICITNI "
                "oblik.")
    hints = (
        "Eksplicitni oblik je y = kx + n — izrazi y.",
        f"Prebaci sve osim ${b_text}$ na desnu stranu.",
        f"Podijeli obje strane sa ${b}$." if b != 1 else
        "Sredi znakove na desnoj strani.",
    )
    solution = (f"Iz ${implicit}$ slijedi ${b_text} = "
                f"{-a if a < 0 else f'-{a}' if a != 0 else ''}x + {n_num * b}$"
                .replace("--", "") +
                (f", pa dijeljenjem sa ${b}$" if b != 1 else "") +
                f" dobijamo ${correct}$.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("implicit", implicit)])


def _parallel_package(rng, level, lesson_id, lesson_title, concept):
    k = Fraction(_nonzero(rng, -4, 4))
    n = Fraction(_nonzero(rng, -6, 6))
    base = _line_display(k, n)
    ask_identical = level >= 2 and rng.random() < 0.4
    parallel = _line_display(k, n + _nonzero(rng, 1, 5))
    identical = base
    different_k = _line_display(k + _nonzero(rng, 1, 3), n)
    negative_k = _line_display(-k, n + 1)
    if ask_identical:
        question = (f"Koja je prava PODUDARNA (poklapa se) s pravom "
                    f"${base}$?")
        correct_line = identical
        wrong_lines = [parallel, different_k, negative_k]
        rule = ("podudarne prave imaju ISTI koeficijent k i ISTI slobodni "
                "član n")
    else:
        question = (f"Koja je prava PARALELNA s pravom ${base}$, a "
                    "različita od nje?")
        correct_line = parallel
        wrong_lines = [different_k, negative_k, identical]
        rule = ("paralelne prave imaju ISTI koeficijent k, a RAZLIČIT "
                "slobodni član n")
    option_texts = (f"${correct_line}$", *(f"${w}$" for w in wrong_lines))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("prave nisu jedinstvene")
    hints = (
        "Uporedi koeficijente uz x: o paralelnosti odlučuje k.",
        "Zatim uporedi slobodne članove n.",
        f"Pravilo: {rule}.",
    )
    solution = (f"Pravilo: {rule}. Tražena prava je ${correct_line}$; prava "
                "s drugačijim k nije paralelna, a "
                + ("prava s drugim n nije podudarna."
                   if ask_identical else
                   "prava s istim k i istim n je ista prava, ne paralelna."))
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct_line,
                    [("base", base),
                     ("kind", "identical" if ask_identical else "parallel")])


def _intersection_package(rng, level, lesson_id, lesson_title, concept):
    x = _nonzero(rng, -5, 5)
    k1 = Fraction(_nonzero(rng, -3, 3))
    k2 = Fraction(_nonzero(rng, -3, 3))
    if k1 == k2:
        raise DeterministicGenerationError("prave su paralelne")
    n1 = Fraction(rng.randint(-6, 6))
    y = k1 * x + n1
    n2 = y - k2 * x
    if y.denominator != 1 or n2.denominator != 1 or abs(n2) > 12:
        raise DeterministicGenerationError("presjek nije školski")
    line1 = _line_display(k1, n1)
    line2 = _line_display(k2, n2)
    correct = f"$({x}, {core.fraction_display(y)})$"
    wrong = [f"$({core.fraction_display(y)}, {x})$",
             f"$({-x}, {core.fraction_display(y)})$",
             f"$({x}, {core.fraction_display(y + 1)})$"]
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("tačke nisu jedinstvene")
    question = (f"Odredi tačku presjeka pravih ${line1}$ i ${line2}$.")
    hints = (
        "U presjeku prave imaju istu vrijednost i za x i za y — izjednači "
        "desne strane.",
        f"Riješi jednačinu ${core.fraction_display(k1)}x + "
        f"{core.fraction_display(n1)} = {core.fraction_display(k2)}x + "
        f"{core.fraction_display(n2)}$.",
        "Dobijeni x uvrsti u bilo koju od pravih da dobiješ y.",
    )
    solution = (f"Izjednačavanjem desnih strana dobija se $x = {x}$, a "
                f"uvrštavanjem $y = {core.fraction_display(y)}$. Provjera: "
                "obje prave za taj $x$ daju isti $y$, pa je presjek "
                f"$({x}, {core.fraction_display(y)})$.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("line1", line1), ("line2", line2)])


def _dependency_package(rng, level, lesson_id, lesson_title, concept):
    k = rng.randint(2, 9)
    n = _nonzero(rng, 1, 8)
    kinds = {
        "direct": (f"y = {k}x", "direktna proporcionalnost",
                   "količnik y : x je stalan"),
        "inverse": (f"y = \\frac{{{k}}}{{x}}", "obrnuta proporcionalnost",
                    "proizvod x · y je stalan"),
        "linear": (f"y = {k}x + {n}", "linearna zavisnost koja NIJE "
                   "proporcionalnost", "grafik ne prolazi kroz koordinatni "
                   "početak"),
    }
    asked = rng.choice(tuple(kinds))
    equation, correct, reason = kinds[asked]
    wrong = [kinds[key][1] for key in kinds if key != asked]
    wrong.append("nijedna od navedenih zavisnosti")
    option_texts = (correct, *wrong[:3])
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("tvrdnje nisu jedinstvene")
    question = (f"Kakvu zavisnost veličina opisuje jednačina ${equation}$?")
    hints = (
        "Direktna: y = kx (količnik stalan). Obrnuta: y = k/x (proizvod "
        "stalan). Linearna s n ≠ 0 nije proporcionalnost.",
        "Provjeri prolazi li zavisnost kroz (0, 0) i kako se y mijenja s x.",
        f"Ovdje: {reason}.",
    )
    solution = (f"Jednačina ${equation}$ opisuje: {correct} — {reason}.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, correct,
                    [("equation", equation), ("kind", asked)])
