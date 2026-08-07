"""Deterministički generator porodice sistema dvije linearne jednačine.

Jedna semantička porodica (`linear_system_direct`); vrste zadataka su podaci
ugovora:

  • solve            — riješi sistem (supstitucija/eliminacija — metoda je
                       pedagoška uputa, rješenje je isto); opcije su uređeni
                       parovi;
  • verify_pair      — koji od ponuđenih parova jeste rješenje sistema (ili
                       jedne jednačine s dvije nepoznate);
  • single_equation  — rješenja jednačine ax + by = c (izbor para);
  • classify         — sistem sa jednim / nijednim / beskonačno mnogo
                       rješenja, uključujući odnos koeficijenata i
                       geometrijsko tumačenje (prave se sijeku / paralelne /
                       poklapaju);
  • equivalent_system — koji je sistem EKVIVALENTAN datom (isto rješenje).

AUTORITET: egzaktno rješavanje Kramerovim pravilom nad `Fraction`
koeficijentima; svako konstruisano rješenje se UVRŠTAVA u obje polazne
jednačine, a klasifikacija se nezavisno provjerava odnosom koeficijenata
(determinanta i proporcionalnost). Sistem se uvijek KONSTRUIŠE iz unaprijed
izabranog rješenja — nikad se ne „nada“ da rješenje postoji.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError
from matbot.tutor.schema import DifficultyEvidence

FAMILY_IDS = ("linear_system_direct",)
GENERATOR_VERSION = "detsys-1"

_KIND_NAMES = frozenset({"solve", "verify_pair", "single_equation",
                         "classify", "equivalent_system"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    kinds = parameters.get("kinds") or ()
    return bool(kinds) and set(kinds) <= _KIND_NAMES


def _coef(rng, level, allow_fraction=False):
    if allow_fraction and level >= 2 and rng.random() < 0.35:
        return Fraction(rng.randint(1, 5), rng.choice((2, 3)))
    value = rng.randint(1, 6 if level == 1 else 9)
    return Fraction(-value if rng.random() < 0.4 and level > 1 else value)


def _term(coefficient, variable):
    """`3x`, `-x`, `\\frac{1}{2}y` — kanonski zapis člana."""
    if coefficient == 1:
        return variable
    if coefficient == -1:
        return f"-{variable}"
    return f"{core.plain_fraction_display(coefficient)}{variable}"


def _equation_display(a, b, c):
    left = _term(a, "x")
    if b > 0:
        left += f" + {_term(b, 'y')}"
    else:
        left += f" - {_term(-b, 'y')}"
    return f"{left} = {core.plain_fraction_display(c)}"


def _system_display(eq1, eq2):
    """Dvije inline jednačine u prozi — projektna konvencija je jednostruki
    `$...$`, a `\\begin{cases}` mathsafe s razlogom odbija (okruženje, ne
    komanda; frontend radi s inline zapisom)."""
    return f"${_equation_display(*eq1)}$ i ${_equation_display(*eq2)}$"


def _pair_display(x, y):
    return (f"({core.plain_fraction_display(x)}, "
            f"{core.plain_fraction_display(y)})")


_EVIDENCE_DIRECT_L1 = DifficultyEvidence(
    reasoning_steps=1, condition_count=1, operation_count=2,
    representation_change_count=0, requires_explanation=False,
    requires_comparison=False, requires_construction=False,
    requires_proof_or_justification=False, combines_concepts=False)


def _ev(level):
    """Nivo 1 = jedna direktna provjera/odluka (rubrika: 'primjena jednog
    pravila i izbor opcije je JEDAN korak'); više nivoe nosi zajednički dokaz."""
    return _EVIDENCE_DIRECT_L1 if level == 1 else core.evidence_for_level(level)


def _build_system(rng, level, fractions_allowed=False, simple=False):
    """Konstruiši sistem OKO izabranog rješenja; vrati (eq1, eq2, x, y).

    `simple=True` prisiljava koeficijent 1 uz y u prvoj jednačini — tada je
    supstitucija direktna (iskrena dva koraka, nivo 2)."""
    x = Fraction(rng.randint(-6, 6))
    y = Fraction(rng.randint(-6, 6))
    if x == 0 and y == 0:
        x = Fraction(1)
    for _ in range(200):
        a1, b1 = _coef(rng, level, fractions_allowed), _coef(rng, level, fractions_allowed)
        if simple:
            b1 = Fraction(1)
        a2, b2 = _coef(rng, level, fractions_allowed), _coef(rng, level, fractions_allowed)
        determinant = a1 * b2 - a2 * b1
        if determinant == 0:
            continue
        c1 = a1 * x + b1 * y
        c2 = a2 * x + b2 * y
        # Nezavisna provjera Kramerovim pravilom + uvrštavanje.
        solved_x = (c1 * b2 - c2 * b1) / determinant
        solved_y = (a1 * c2 - a2 * c1) / determinant
        assert solved_x == x and solved_y == y
        assert a1 * solved_x + b1 * solved_y == c1
        assert a2 * solved_x + b2 * solved_y == c2
        return (a1, b1, c1), (a2, b2, c2), x, y
    raise DeterministicGenerationError("sistem nije konstruisan")


def _pair_options(rng, x, y):
    candidates = [(y, x), (x + 1, y), (x, y - 1), (-x, -y), (x - 1, y + 1),
                  (x + 2, y + 2)]
    options = [(x, y)]
    for candidate in candidates:
        if candidate not in options:
            options.append(candidate)
        if len(options) == 4:
            break
    return options


def _k_solve(rng, level, fractions_allowed):
    eq1, eq2, x, y = _build_system(rng, level, fractions_allowed,
                                   simple=(level == 2))
    system = _system_display(eq1, eq2)
    options = _pair_options(rng, x, y)
    a1, b1, c1 = eq1
    return {
        "question": f"Riješi sistem jednačina: {system}",
        "option_texts": tuple(f"${_pair_display(px, py)}$" for px, py in options),
        "answer_display": f"{_pair_display(x, y)}",
        "rule": ("Sistem se rješava supstitucijom ili metodom suprotnih "
                 "koeficijenata; rješenje je uređeni par $(x, y)$ koji "
                 "zadovoljava OBJE jednačine"),
        "hint2": ("Izrazi jednu nepoznatu iz prve jednačine pa je uvrsti u "
                  "drugu (ili izjednači koeficijente pa oduzmi jednačine)."),
        "hint3": (f"Provjera kandidata: uvrsti par u prvu jednačinu — "
                  f"${_term(a1, 'x')}"
                  + (f" + {_term(b1, 'y')}" if b1 > 0 else
                     f" - {_term(-b1, 'y')}")
                  + f"$ mora dati ${core.plain_fraction_display(c1)}$."),
        "solution": (f"Rješenje sistema je $(x, y) = {_pair_display(x, y)}$. "
                     f"Provjera uvrštavanjem: "
                     f"${core.plain_fraction_display(eq1[0])} \\cdot "
                     f"{core.parenthesized(core.plain_fraction_display(x))} + "
                     f"{core.plain_fraction_display(eq1[1])} \\cdot "
                     f"{core.parenthesized(core.plain_fraction_display(y))} = "
                     f"{core.plain_fraction_display(eq1[2])}$ i "
                     f"${core.plain_fraction_display(eq2[0])} \\cdot "
                     f"{core.parenthesized(core.plain_fraction_display(x))} + "
                     f"{core.plain_fraction_display(eq2[1])} \\cdot "
                     f"{core.parenthesized(core.plain_fraction_display(y))} = "
                     f"{core.plain_fraction_display(eq2[2])}$ — obje "
                     "jednakosti važe."),
        "signature": [("eq1", f"{eq1[0]}|{eq1[1]}|{eq1[2]}"),
                      ("eq2", f"{eq2[0]}|{eq2[1]}|{eq2[2]}")],
        "operation": "solve_system",
        "evidence": _ev(level),
    }


def _k_verify_pair(rng, level, fractions_allowed):
    spec = _k_solve(rng, level, fractions_allowed)
    spec["question"] = spec["question"].replace(
        "Riješi sistem jednačina:",
        "Koji od ponuđenih uređenih parova JESTE rješenje sistema")
    spec["question"] += "?"
    spec["operation"] = "verify_pair"
    spec["evidence"] = _ev(level)
    return spec


def _k_single_equation(rng, level, fractions_allowed):
    x = Fraction(rng.randint(-5, 5))
    y = Fraction(rng.randint(-5, 5))
    a = _coef(rng, level)
    b = _coef(rng, level)
    c = a * x + b * y
    equation = _equation_display(a, b, c)
    options = _pair_options(rng, x, y)
    # Distraktori NE SMIJU slučajno zadovoljiti jednačinu (jednačina s dvije
    # nepoznate ima beskonačno rješenja — provjera je obavezna!).
    safe_options = [(x, y)]
    for px, py in options[1:]:
        if a * px + b * py != c:
            safe_options.append((px, py))
    attempt = 0
    while len(safe_options) < 4 and attempt < 100:
        attempt += 1
        px = x + rng.randint(-4, 4)
        py = y + rng.randint(-4, 4)
        if (px, py) not in safe_options and a * px + b * py != c:
            safe_options.append((px, py))
    if len(safe_options) < 4:
        raise DeterministicGenerationError("nedovoljno sigurnih parova")
    return {
        "question": (f"Koji od ponuđenih uređenih parova $(x, y)$ jeste "
                     f"rješenje jednačine ${equation}$?"),
        "option_texts": tuple(f"${_pair_display(px, py)}$"
                              for px, py in safe_options[:4]),
        "answer_display": f"{_pair_display(x, y)}",
        "rule": ("Linearna jednačina s dvije nepoznate ima beskonačno mnogo "
                 "rješenja — rješenje je svaki par koji uvrštavanjem daje "
                 "tačnu jednakost"),
        "hint2": "Uvrsti svaki ponuđeni par u jednačinu i izračunaj lijevu stranu.",
        "hint3": (f"Tačan par uvrštavanjem daje "
                  f"${core.plain_fraction_display(c)}$ na lijevoj strani."),
        "solution": (f"Uvrstimo par ${_pair_display(x, y)}$: "
                     f"${core.plain_fraction_display(a)} \\cdot "
                     f"{core.parenthesized(core.plain_fraction_display(x))} + "
                     f"{core.plain_fraction_display(b)} \\cdot "
                     f"{core.parenthesized(core.plain_fraction_display(y))} = "
                     f"{core.plain_fraction_display(c)}$ — jednakost važi, a "
                     "ostali parovi je ne zadovoljavaju."),
        "signature": [("eq", f"{a}|{b}|{c}")],
        "operation": "single_equation_pair",
        "evidence": _ev(level),
    }


_CLASSIFY_LABELS = ("tačno jedno rješenje", "nema rješenja",
                    "beskonačno mnogo rješenja")


def _k_classify(rng, level, fractions_allowed):
    outcome = rng.choice(_CLASSIFY_LABELS)
    for _ in range(200):
        a1 = rng.randint(1, 6)
        b1 = rng.choice((1, 2, 3, -1, -2))
        c1 = rng.randint(-8, 8)
        if outcome == "tačno jedno rješenje":
            a2, b2 = rng.randint(1, 6), rng.choice((1, 2, 3, -1, -2))
            if a1 * b2 - a2 * b1 == 0:
                continue
            c2 = rng.randint(-8, 8)
            relation = ("determinanta $a_1b_2 - a_2b_1 \\ne 0$ — prave se "
                        "SIJEKU u jednoj tački")
        else:
            k = rng.randint(2, 3)
            a2, b2 = k * a1, k * b1
            if outcome == "beskonačno mnogo rješenja":
                c2 = k * c1
                relation = ("svi koeficijenti su proporcionalni "
                            "($a_2 = k a_1$, $b_2 = k b_1$, $c_2 = k c_1$) — "
                            "prave se POKLAPAJU")
            else:
                c2 = k * c1 + rng.choice((1, 2, -1, -2))
                relation = ("koeficijenti uz nepoznate su proporcionalni, ali "
                            "slobodni članovi NISU — prave su PARALELNE")
        eq1, eq2 = (a1, Fraction(b1), Fraction(c1)), (a2, Fraction(b2), Fraction(c2))
        determinant = Fraction(a1) * b2 - Fraction(a2) * b1
        if outcome == "tačno jedno rješenje":
            assert determinant != 0
        else:
            assert determinant == 0
            proportional_c = Fraction(c2) * a1 == Fraction(c1) * a2
            assert proportional_c == (outcome == "beskonačno mnogo rješenja")
        system = _system_display(eq1, eq2)
        others = [label for label in _CLASSIFY_LABELS if label != outcome]
        geometric = {"tačno jedno rješenje": "prave se sijeku",
                     "nema rješenja": "prave su paralelne",
                     "beskonačno mnogo rješenja": "prave se poklapaju"}
        if level >= 2 and rng.random() < 0.5:
            question = (f"Grafici jednačina sistema {system} su dvije prave. "
                        "U kakvom su međusobnom položaju te prave?")
            option_texts = (geometric[outcome],
                            *[geometric[label] for label in others],
                            "prave su normalne")
            answer_display = geometric[outcome]
            operation = "system_geometry"
        else:
            question = f"Koliko rješenja ima sistem jednačina {system}?"
            option_texts = (outcome, *others, "tačno dva rješenja")
            answer_display = outcome
            operation = "classify_solutions"
        return {
            "question": question,
            "option_texts": option_texts,
            "answer_display": answer_display,
            "rule": ("Broj rješenja sistema čita se iz odnosa koeficijenata: "
                     "različiti omjeri daju jedno rješenje, proporcionalni "
                     "koeficijenti uz nepoznate bez proporcionalnih slobodnih "
                     "članova nijedno, a potpuna proporcionalnost beskonačno "
                     "mnogo"),
            "hint2": ("Uporedi omjere $\\frac{a_1}{a_2}$, $\\frac{b_1}{b_2}$ "
                      "i $\\frac{c_1}{c_2}$."),
            "hint3": f"U ovom sistemu: {relation}.",
            "solution": (f"{relation.capitalize()}, pa sistem ima: "
                         f"{outcome}."),
            "signature": [("eq1", f"{eq1[0]}|{eq1[1]}|{eq1[2]}"),
                          ("eq2", f"{eq2[0]}|{eq2[1]}|{eq2[2]}"),
                          ("outcome", outcome)],
            "operation": operation,
            "evidence": _ev(level),
        }
    raise DeterministicGenerationError("klasifikacija nije konstruisana")


def _k_equivalent_system(rng, level, fractions_allowed):
    eq1, eq2, x, y = _build_system(rng, level, False)
    system = _system_display(eq1, eq2)
    k = rng.choice((2, 3))
    equivalent = ((eq1[0] * k, eq1[1] * k, eq1[2] * k), eq2)
    wrong1 = ((eq1[0], eq1[1], eq1[2] + 1), eq2)
    wrong2 = (eq1, (eq2[0], eq2[1], eq2[2] - 2))
    wrong3 = ((eq1[0] * k, eq1[1] * k, eq1[2] * k + 1), eq2)

    def _solves(system_pair):
        (a1, b1, c1), (a2, b2, c2) = system_pair
        return a1 * x + b1 * y == c1 and a2 * x + b2 * y == c2

    assert _solves(equivalent)
    assert not any(_solves(candidate) for candidate in (wrong1, wrong2, wrong3))
    return {
        "question": (f"Sistem {system} ima rješenje "
                     f"$(x, y) = {_pair_display(x, y)}$. Koji je od ponuđenih "
                     "sistema EKVIVALENTAN datom sistemu?"),
        "option_texts": (_system_display(*equivalent),
                         _system_display(*wrong1), _system_display(*wrong2),
                         _system_display(*wrong3)),
        "answer_display": _system_display(*equivalent).strip("$"),
        "rule": ("Ekvivalentni sistemi imaju ISTO rješenje — jednačina se "
                 "smije pomnožiti brojem različitim od nule, a jednačine se "
                 "smiju sabirati"),
        "hint2": (f"Prva jednačina tačnog sistema je polazna prva jednačina "
                  f"pomnožena sa ${k}$."),
        "hint3": (f"Uvrsti ${_pair_display(x, y)}$ u svaki ponuđeni sistem — "
                  "samo jedan ga zadovoljava."),
        "solution": (f"Množenjem prve jednačine sa ${k}$ rješenje se ne "
                     f"mijenja, pa je ekvivalentan sistem "
                     f"{_system_display(*equivalent)}. Par "
                     f"${_pair_display(x, y)}$ zadovoljava obje njegove "
                     "jednačine, a nijedan drugi ponuđeni sistem."),
        "signature": [("eq1", f"{eq1[0]}|{eq1[1]}|{eq1[2]}"),
                      ("k", str(k))],
        "operation": "equivalent_system",
        "evidence": _ev(level),
    }


_KINDS = {
    "solve": _k_solve,
    "verify_pair": _k_verify_pair,
    "single_equation": _k_single_equation,
    "classify": _k_classify,
    "equivalent_system": _k_equivalent_system,
}


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    kinds = tuple(parameters["kinds"])
    fractions_allowed = bool(parameters.get("fraction_coefficients"))
    for _ in range(60):
        try:
            kind = rng.choice(kinds)
            spec = _KINDS[kind](rng, level, fractions_allowed)
        except DeterministicGenerationError:
            continue
        option_texts = spec["option_texts"]
        if len(set(option_texts)) != 4:
            continue
        hints = (f"{spec['rule']}.", spec["hint2"], spec["hint3"])
        if spec["answer_display"] in hints[0]:
            # Prikaz odgovora ne smije procuriti kroz prvu uputu — novi
            # pokušaj bira drugi zadatak (ista zaštita kao u geometry).
            continue
        return core.build_package(
            lesson_id=lesson_id, lesson_title=lesson_title,
            family_id="linear_system_direct", operation=spec["operation"],
            level=level, question=spec["question"],
            answer_value=spec["answer_display"],
            answer_display=spec["answer_display"], distractor_values=(),
            hints=hints, solution=spec["solution"],
            signature_parameters=list(spec["signature"])
            + [("kind", spec["operation"])],
            required_conditions=[spec["operation"]],
            relevant_objects=["system"], generator_version=GENERATOR_VERSION,
            option_texts=option_texts, wrap="", evidence=spec["evidence"])
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")
