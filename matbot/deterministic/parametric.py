"""Deterministička diskusija linearnih jednačina i sistema s parametrom.

Jedna semantička porodica (Batch #4, Prioritet 6):
``parametric_linear_discussion``.

  • ``parameter_case``            — za koju vrijednost parametra jednačina
    oblika $(a-k)x = c$ nema rješenja / ima beskonačno mnogo / ima
    jedinstveno rješenje;
  • ``parameter_value_solve``     — riješi jednačinu čiji je koeficijent
    zadan vrijednošću parametra;
  • ``parametric_system_classification`` — sistem s parametrom: jedinstveno /
    nema / beskonačno rješenja preko odnosa koeficijenata.

POTPUNOST SLUČAJEVA JE DOKAZANA KONSTRUKCIJOM: oblik $(a-k)x = c$ ima TAČNO
tri međusobno isključiva slučaja (a ≠ k; a = k uz c ≠ 0; a = k uz c = 0) i
rješenje ih sve navodi. Za sistem $x + y = s$, $kx + y = t$ determinantni
kriterij ($k \\neq 1$ jedinstveno; $k = 1$ pa poređenje $s$ i $t$) je
kompletan i egzaktan. Ne generišu se oblici čiju podjelu slučajeva server ne
može algebarski dokazati.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("parametric_linear_discussion",)
GENERATOR_VERSION = "detparam-1"

_SUPPORTED_CONCEPTS = frozenset({
    "parameter_case", "parameter_value_solve",
    "parametric_system_classification",
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
        "parameter_case": _case_package,
        "parameter_value_solve": _value_solve_package,
        "parametric_system_classification": _system_package,
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
        family_id="parametric_linear_discussion", operation=concept,
        level=level, question=question, answer_value=None,
        answer_display=answer_display, distractor_values=(), hints=hints,
        solution=solution, signature_parameters=signature,
        required_conditions=[concept], relevant_objects=["parametar"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")


def _nonzero(rng, low, high):
    value = 0
    while value == 0:
        value = rng.randint(low, high)
    return value


# ---------------------------------------------------------------------------
# (a - k)·x = c — tri potpuna, međusobno isključiva slučaja
# ---------------------------------------------------------------------------

_CASES = ("nema rješenja", "beskonačno mnogo rješenja",
          "tačno jedno rješenje")


def _case_package(rng, level, lesson_id, lesson_title, concept):
    k = _nonzero(rng, -6, 6)
    asked = rng.choice(("none", "infinite") if level < 3
                       else ("none", "infinite", "unique_check"))
    coefficient = f"(a - {k})" if k > 0 else f"(a + {abs(k)})"
    if asked == "none":
        c = _nonzero(rng, -9, 9)
        equation = f"{coefficient}x = {c}"
        question = (f"Za koju vrijednost parametra $a$ jednačina "
                    f"${equation}$ NEMA rješenja?")
        answer = f"a = {k}"
        wrong = [f"a = {-k}", f"a = {k + 1}", f"a = 0"]
        case_reason = (f"za $a = {k}$ jednačina glasi $0 \\cdot x = {c}$, "
                       f"a nula puta bilo koji broj nikad nije ${c}$")
    elif asked == "infinite":
        equation = f"{coefficient}x = 0"
        question = (f"Za koju vrijednost parametra $a$ jednačina "
                    f"${equation}$ ima BESKONAČNO MNOGO rješenja?")
        answer = f"a = {k}"
        wrong = [f"a = {-k}", f"a = {k - 1}", "nijednu — to je nemoguće"]
        case_reason = (f"za $a = {k}$ jednačina glasi $0 \\cdot x = 0$, "
                       "što je tačno za svaki $x$")
    else:
        c = _nonzero(rng, -9, 9)
        equation = f"{coefficient}x = {c}"
        a_value = k + _nonzero(rng, -4, 4)
        question = (f"Koliko rješenja ima jednačina ${equation}$ za "
                    f"$a = {a_value}$?")
        answer = "tačno jedno rješenje"
        wrong = ["nema rješenja", "beskonačno mnogo rješenja",
                 "dva rješenja"]
        case_reason = (f"za $a = {a_value}$ koeficijent uz $x$ iznosi "
                       f"${a_value - k}$ i različit je od nule, pa se "
                       "jednačina dijeli koeficijentom")
    option_texts = (answer, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    hints = (
        "Ponašanje jednačine određuje koeficijent uz x: različit od nule, "
        "ili nula.",
        f"Koeficijent uz $x$ je ${coefficient.strip('()')}$ — kada je on "
        "jednak nuli?",
        "Kad je koeficijent nula, uporedi lijevu i desnu stranu: 0 = 0 daje "
        "beskonačno mnogo, 0 = broj različit od nule nijedno rješenje.",
    )
    solution = (f"Tri slučaja u potpunosti opisuju jednačinu: za "
                f"$a \\neq {k}$ koeficijent je različit od nule i rješenje "
                "je jedinstveno; za $a = {k}$ s desnom stranom različitom "
                "od nule rješenja nema; za $a = {k}$ s desnom stranom nula "
                f"rješenja je beskonačno mnogo. Ovdje: {case_reason}, pa je "
                f"odgovor „{answer}“.".replace("{k}", str(k)))
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, answer,
                    [("k", str(k)), ("asked", asked)])


def _value_solve_package(rng, level, lesson_id, lesson_title, concept):
    a = _nonzero(rng, -5, 5)
    if level == 1 and a < 0:
        a = -a
    x = _nonzero(rng, -9, 9)
    b = rng.randint(1, 12)
    c = a * x + b
    equation = f"ax + {b} = {c}"
    question = (f"Data je jednačina ${equation}$ u kojoj je koeficijent "
                f"$a$ promjenljiv. Riješi jednačinu za $a = {a}$.")
    answer_value = Fraction(x)
    hints = (
        "Uvrsti zadanu vrijednost parametra umjesto a — dobijaš običnu "
        "linearnu jednačinu.",
        f"Jednačina postaje ${a}x + {b} = {c}$.",
        f"Prebaci ${b}$ na desnu stranu pa podijeli sa ${a}$.",
    )
    solution = (f"Za $a = {a}$: ${a}x + {b} = {c}$, pa je "
                f"${a}x = {c - b}$ i $x = {x}$. Provjera: "
                f"${a} \\cdot {core.parenthesized(str(x))} + {b} = {c}$.")
    distractors = [answer_value + 1, answer_value - 1, -answer_value,
                   Fraction(c - b)]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="parametric_linear_discussion", operation=concept,
        level=level, question=question, answer_value=answer_value,
        answer_display=f"x = {x}",
        distractor_values=distractors, hints=hints, solution=solution,
        signature_parameters=[("a", str(a)), ("b", str(b)), ("c", str(c))],
        required_conditions=[concept], relevant_objects=["parametar"],
        generator_version=GENERATOR_VERSION,
        display_of=lambda value: f"x = {core.fraction_display(value)}")


# ---------------------------------------------------------------------------
# SISTEM S PARAMETROM — x + y = s, kx + y = t
# ---------------------------------------------------------------------------

def _system_package(rng, level, lesson_id, lesson_title, concept):
    s = rng.randint(2, 12)
    asked = rng.choice(("no_solution", "infinite") if level < 3
                       else ("no_solution", "infinite", "unique"))
    if asked == "no_solution":
        t = s + _nonzero(rng, 1, 6)
        system = f"x + y = {s} \\quad i \\quad kx + y = {t}"
        question = (f"Za koju vrijednost parametra $k$ sistem "
                    f"${system}$ NEMA rješenja?")
        answer = "k = 1"
        wrong = [f"k = {t}", f"k = {s}", "k = 0"]
        reason = (f"za $k = 1$ lijeve strane postaju jednake, a desne su "
                  f"${s}$ i ${t}$ — različite, pa rješenja nema")
    elif asked == "infinite":
        t = s
        system = f"x + y = {s} \\quad i \\quad kx + y = {t}"
        question = (f"Za koju vrijednost parametra $k$ sistem "
                    f"${system}$ ima BESKONAČNO MNOGO rješenja?")
        answer = "k = 1"
        wrong = [f"k = {s}", "k = 0", "nijednu — to je nemoguće"]
        reason = ("za $k = 1$ obje jednačine postaju identične, pa je svako "
                  "rješenje prve ujedno i rješenje druge")
    else:
        t = s + _nonzero(rng, 1, 6)
        k_value = 1 + _nonzero(rng, 1, 4)
        system = f"x + y = {s} \\quad i \\quad kx + y = {t}"
        question = (f"Koliko rješenja ima sistem ${system}$ za "
                    f"$k = {k_value}$?")
        answer = "tačno jedno rješenje"
        wrong = ["nema rješenja", "beskonačno mnogo rješenja",
                 "dva rješenja"]
        reason = (f"za $k = {k_value}$ koeficijenti uz $x$ se razlikuju "
                  "(determinanta je različita od nule), pa se oduzimanjem "
                  "jednačina dobija jedinstven $x$")
    option_texts = (answer, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("opcije nisu jedinstvene")
    hints = (
        "Uporedi koeficijente uz x: sistem gubi jedinstvenost tek kad "
        "jednačine imaju ISTE koeficijente uz obje nepoznate.",
        "Oduzmi prvu jednačinu od druge — šta ostaje?",
        "Kad lijeve strane postanu jednake, o broju rješenja odlučuju desne "
        "strane: jednake daju beskonačno mnogo, različite nijedno.",
    )
    solution = (f"Oduzimanjem prve jednačine od druge ostaje "
                f"$(k - 1)x = {t - s}$. Slučajevi su potpuni i međusobno "
                f"isključivi: $k \\neq 1$ daje jedinstveno rješenje; "
                f"$k = 1$ uz različite desne strane nijedno; $k = 1$ uz "
                f"jednake desne strane beskonačno mnogo. Ovdje: {reason}, "
                f"pa je odgovor „{answer}“.")
    return _package(lesson_id, lesson_title, concept, level, question,
                    option_texts, hints, solution, answer,
                    [("s", str(s)), ("t", str(t)), ("asked", asked)])
