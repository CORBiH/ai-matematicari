"""Deterministički generator porodice linearne funkcije.

Jedna semantička porodica (`linear_function_direct`) s konceptima koje nose
parametri ugovora (`concepts`, opciono `function_kind`):

  • evaluate / table       — vrijednost funkcije u tački (i „tabela parova“);
  • find_coefficient       — k (ili n) iz poznate tačke;
  • zero                   — nula funkcije kx + n = 0;
  • membership             — koja tačka pripada grafiku;
  • monotonicity           — koja je funkcija rastuća/opadajuća (znak od k);
  • sign_analysis          — za koje x je f(x) pozitivna/negativna;
  • from_two_points        — jednačina prave kroz dvije tačke;
  • implicit_to_explicit   — prelazak iz implicitnog u eksplicitni oblik.

`function_kind` bira oblik: `affine` (y = kx + n), `direct` (y = kx) ili
`inverse` (y = k/x, uvijek s egzaktnim djeliocima).

MATEMATIČKI AUTORITET: egzaktni `fractions.Fraction`; svaka vidljiva numerička
jednakost u rješenju je egzaktna. TAČKE SE UVIJEK PIŠU S IMENOM
(`T(2, 5)`, `T_1(1, 3)`): goli zapis `(2, 5)` bi numerički parser opcija
mogao pročitati kao decimalu „2,5“ i dvije različite tačke proglasiti istom
vrijednošću — slovo ispred zagrade drži zapis izvan numeričkog poređenja.
"""
import random
from fractions import Fraction

from matbot.deterministic import core
from matbot.deterministic.core import DeterministicGenerationError

FAMILY_IDS = ("linear_function_direct",)
GENERATOR_VERSION = "detfun-1"

_SUPPORTED_CONCEPTS = frozenset({
    "evaluate", "table", "find_coefficient", "zero", "membership",
    "monotonicity", "sign_analysis", "from_two_points",
    "implicit_to_explicit",
})
_SUPPORTED_KINDS = frozenset({"affine", "direct", "inverse"})


def supports(parameters) -> bool:
    parameters = parameters or {}
    concepts = set(parameters.get("concepts") or ())
    if not concepts or not concepts <= _SUPPORTED_CONCEPTS:
        return False
    kind = parameters.get("function_kind") or "affine"
    if kind not in _SUPPORTED_KINDS:
        return False
    if kind == "inverse" and not concepts <= {"evaluate", "table",
                                              "find_coefficient", "membership"}:
        # Obrnuta proporcionalnost nema nulu ni monotonost u školskom obimu.
        return False
    return True


def generate_package(lesson_id, lesson_title, parameters, level, rng=None):
    if not supports(parameters):
        raise DeterministicGenerationError("parametri ugovora nisu podržani")
    rng = rng or random.Random()
    level = core.clamp_level(level)
    kind = parameters.get("function_kind") or "affine"
    for _ in range(60):
        try:
            concept = rng.choice(tuple(parameters["concepts"]))
            builder = {
                "evaluate": _evaluate_package,
                "table": _evaluate_package,
                "find_coefficient": _find_coefficient_package,
                "zero": _zero_package,
                "membership": _membership_package,
                "monotonicity": _monotonicity_package,
                "sign_analysis": _sign_analysis_package,
                "from_two_points": _from_two_points_package,
                "implicit_to_explicit": _implicit_package,
            }[concept]
            return builder(rng, level, kind, concept, lesson_id, lesson_title)
        except DeterministicGenerationError:
            continue
    raise DeterministicGenerationError("paket nije nastao u ograničenom broju pokušaja")


# ---------------------------------------------------------------------------
# PRIKAZ FUNKCIJE I KOEFICIJENTI
# ---------------------------------------------------------------------------

def _coefficients(rng, level, kind):
    k = rng.randint(1, 5 if level == 1 else 9)
    if level > 1 and rng.random() < 0.5:
        k = -k
    if kind == "direct":
        return k, 0
    if kind == "inverse":
        return rng.randint(2, 9) * rng.randint(2, 6), 0   # k za y = k/x
    n = rng.randint(1, 9)
    if rng.random() < 0.5:
        n = -n
    return k, n


def _function_display(k, n, kind):
    if kind == "inverse":
        return f"f(x) = \\frac{{{k}}}{{x}}"
    k_part = "x" if k == 1 else ("-x" if k == -1 else f"{k}x")
    if kind == "direct" or n == 0:
        return f"f(x) = {k_part}"
    sign = "+" if n > 0 else "-"
    return f"f(x) = {k_part} {sign} {abs(n)}"


def _point(name, x, y):
    return f"{name}({_show(x)}, {_show(y)})"


def _show(value) -> str:
    value = Fraction(value)
    return core.plain_fraction_display(value)


def _evaluate_at(k, n, kind, x):
    x = Fraction(x)
    if kind == "inverse":
        if x == 0:
            raise DeterministicGenerationError("dijeljenje nulom")
        return Fraction(k) / x
    return Fraction(k) * x + n


# ---------------------------------------------------------------------------
# VRIJEDNOST FUNKCIJE (i tabela parova)
# ---------------------------------------------------------------------------

def _evaluate_package(rng, level, kind, concept, lesson_id, lesson_title):
    k, n = _coefficients(rng, level, kind)
    if kind == "inverse":
        divisors = [d for d in range(1, k + 1) if k % d == 0 and d > 1]
        if not divisors:
            raise DeterministicGenerationError("nema djelilaca")
        x = rng.choice(divisors)
    else:
        x = rng.randint(2, 6 if level == 1 else 9)
        if level > 1 and rng.random() < 0.4:
            x = -x
    value = _evaluate_at(k, n, kind, x)
    display = _function_display(k, n, kind)
    if concept == "table":
        question = (f"Za funkciju ${display}$ popunjava se tabela "
                    f"vrijednosti. Koja vrijednost $f(x)$ pripada koloni "
                    f"$x = {_show(x)}$?")
    else:
        question = (f"Data je funkcija ${display}$. Koliko iznosi "
                    f"$f({_show(x)})$?")
    if kind == "inverse":
        chain = f"\\frac{{{k}}}{{{_show(x)}}} = {_show(value)}"
        hint2 = f"Uvrsti: $f({_show(x)}) = \\frac{{{k}}}{{{_show(x)}}}$."
    else:
        chain = (f"{k} \\cdot {core.parenthesized(_show(x))}"
                 + (f" + {_show(n)}" if n > 0 else (f" - {abs(n)}" if n else ""))
                 + f" = {_show(value)}")
        hint2 = f"Uvrsti $x = {_show(x)}$ umjesto $x$ u zapis funkcije."
    hint1 = ("Vrijednost funkcije u tački dobijaš tako što broj uvrstiš "
             "umjesto $x$ i izračunaš.")
    hint3 = f"Računaj: ${chain.split('=')[0].strip()}$."
    solution = (f"Uvrstimo: $f({_show(x)}) = {chain}$. "
                f"Vrijednost je ${core.fraction_display(value)}$.")
    step = Fraction(1) if value.denominator == 1 else Fraction(1, value.denominator)
    candidates = [value + step, value - step, -value, value + 2 * step,
                  Fraction(k) * x - n if kind == "affine" and n else value + 3 * step]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_function_direct", operation=concept, level=level,
        question=question, answer_value=value,
        answer_display=core.fraction_display(value),
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("function", display), ("x", str(x))],
        required_conditions=[concept], relevant_objects=["function"],
        generator_version=GENERATOR_VERSION, display_of=core.fraction_display)


def _find_coefficient_package(rng, level, kind, concept, lesson_id,
                              lesson_title):
    k, n = _coefficients(rng, level, kind)
    if kind == "inverse":
        x = rng.choice([d for d in range(2, k + 1) if k % d == 0] or [1])
        y = Fraction(k) / x
        question = (f"Funkcija obrnute proporcionalnosti $f(x) = "
                    f"\\frac{{k}}{{x}}$ prolazi kroz tačku "
                    f"${_point('T', x, y)}$. Koliki je koeficijent $k$?")
        answer = Fraction(k)
        chain = f"k = {_show(x)} \\cdot {_show(y)} = {k}"
        rule = "Kod obrnute proporcionalnosti proizvod x·y je stalan i jednak k."
    else:
        x = rng.randint(2, 9)
        y = Fraction(k) * x + (n if kind == "affine" else 0)
        if kind == "direct":
            question = (f"Funkcija direktne proporcionalnosti $f(x) = kx$ "
                        f"prolazi kroz tačku ${_point('T', x, y)}$. Koliki je "
                        "koeficijent $k$?")
            chain = f"k = {_show(y)} : {_show(x)} = {_show(Fraction(y, x))}"
            answer = Fraction(y, x)
            rule = "Kod direktne proporcionalnosti k je količnik y : x."
        else:
            question = (f"Funkcija $f(x) = kx + {n}$ prolazi kroz tačku "
                        f"${_point('T', x, y)}$. Koliki je koeficijent $k$?"
                        if n > 0 else
                        f"Funkcija $f(x) = kx - {abs(n)}$ prolazi kroz tačku "
                        f"${_point('T', x, y)}$. Koliki je koeficijent $k$?")
            chain = (f"k = ({_show(y)} - {_term_show(n)}) : {_show(x)} "
                     f"= {_show(Fraction(y - n, x))}")
            answer = Fraction(y - n, x)
            rule = "Uvrsti koordinate tačke pa izrazi k iz jednačine."
    hint2 = f"Uvrsti koordinate tačke u zapis funkcije."
    hint3 = f"Izrazi k: ${chain.split('=')[0].strip()} = {chain.split('=', 1)[1].strip()}$."
    solution = (f"{rule} Računamo: ${chain}$. Koeficijent je "
                f"${core.fraction_display(answer)}$.")
    step = Fraction(1) if answer.denominator == 1 else Fraction(1, answer.denominator)
    candidates = [-answer, answer + step, answer - step, answer * 2,
                  Fraction(x)]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_function_direct", operation="find_coefficient",
        level=level, question=question, answer_value=answer,
        answer_display=core.fraction_display(answer),
        distractor_values=candidates, hints=(rule, hint2, hint3),
        solution=solution,
        signature_parameters=[("point", f"{x}:{y}"), ("kind", kind)],
        required_conditions=["find_coefficient"],
        relevant_objects=["function"], generator_version=GENERATOR_VERSION,
        display_of=core.fraction_display)


def _term_show(value):
    return core.parenthesized(_show(value))


# ---------------------------------------------------------------------------
# NULA FUNKCIJE
# ---------------------------------------------------------------------------

def _zero_package(rng, level, kind, concept, lesson_id, lesson_title):
    k = rng.randint(1, 5 if level == 1 else 9)
    if level > 1 and rng.random() < 0.5:
        k = -k
    zero = Fraction(rng.randint(1, 6))
    if rng.random() < 0.5:
        zero = -zero
    if level == 3:
        zero = Fraction(rng.randint(1, 9), rng.randint(2, 5))
        if rng.random() < 0.5:
            zero = -zero
    n = -Fraction(k) * zero
    if n.denominator != 1 and level < 3:
        raise DeterministicGenerationError("slobodni član nije cio")
    display = _function_display(k, n if n.denominator == 1 else n, "affine")
    if n == 0:
        raise DeterministicGenerationError("nula u nuli je trivijalna")
    question = f"Odredi nulu funkcije ${display}$."
    chain = (f"x = {_term_show(-n)} : {_term_show(Fraction(k))} "
             f"= {_show(zero)}")
    hint1 = ("Nula funkcije je vrijednost x za koju je f(x) = 0 — "
             "postavi jednačinu kx + n = 0.")
    hint2 = f"Riješi: postavi ${display.split('=', 1)[1].strip()} = 0$."
    hint3 = f"Prebaci slobodni član pa podijeli koeficijentom uz $x$."
    check_value = Fraction(k) * zero + n
    solution = (f"Iz $f(x) = 0$ slijedi ${chain}$. Provjera: "
                f"${k} \\cdot {core.parenthesized(_show(zero))}"
                + (f" + {_show(n)}" if n > 0 else f" - {abs(n)}")
                + f" = {_show(check_value)}$ — nula funkcije je "
                f"${core.fraction_display(zero)}$.")
    step = Fraction(1) if zero.denominator == 1 else Fraction(1, zero.denominator)
    candidates = [-zero, Fraction(n), zero + step, zero - step, Fraction(k)]
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_function_direct", operation="zero", level=level,
        question=question, answer_value=zero,
        answer_display=core.fraction_display(zero),
        distractor_values=candidates, hints=(hint1, hint2, hint3),
        solution=solution,
        signature_parameters=[("function", display)],
        required_conditions=["zero"], relevant_objects=["function"],
        generator_version=GENERATOR_VERSION, display_of=core.fraction_display)


# ---------------------------------------------------------------------------
# PRIPADNOST TAČKE GRAFIKU
# ---------------------------------------------------------------------------

def _membership_package(rng, level, kind, concept, lesson_id, lesson_title):
    k, n = _coefficients(rng, level, "affine" if kind != "inverse" else kind)
    display = _function_display(k, n, kind)
    x = rng.randint(1, 6)
    if kind == "inverse":
        divisors = [d for d in range(1, k + 1) if k % d == 0]
        x = rng.choice(divisors)
    y = _evaluate_at(k, n, kind, x)
    if y.denominator != 1:
        raise DeterministicGenerationError("tačka nije cjelobrojna")
    names = ("A", "B", "C", "D")
    correct = _point(names[0], x, y)
    wrong, seen = [], {(Fraction(x), y)}
    for _ in range(200):
        wx = rng.randint(1, 9)
        wy = Fraction(rng.randint(-15, 15))
        if (Fraction(wx), wy) in seen:
            continue
        if _evaluate_at(k, n, kind, wx) == wy:
            continue
        seen.add((Fraction(wx), wy))
        wrong.append(_point(names[len(wrong) + 1], wx, wy))
        if len(wrong) == 3:
            break
    if len(wrong) < 3:
        raise DeterministicGenerationError("nedovoljno tačaka")
    option_texts = (f"${correct}$", *(f"${w}$" for w in wrong))
    question = (f"Koja od ponuđenih tačaka pripada grafiku funkcije "
                f"${display}$?")
    hint1 = ("Tačka pripada grafiku ako uvrštavanjem njene prve koordinate "
             "funkcija daje tačno drugu koordinatu.")
    hint2 = "Uvrsti x-koordinatu svake tačke i uporedi rezultat s y-koordinatom."
    hint3 = "Samo jedna tačka daje tačnu jednakost — ostale odstupaju."
    if kind == "inverse":
        chain = f"f({_show(x)}) = \\frac{{{k}}}{{{_show(x)}}} = {_show(y)}"
    else:
        chain = (f"f({_show(x)}) = {k} \\cdot {_show(x)}"
                 + (f" + {_show(n)}" if n > 0 else (f" - {abs(n)}" if n else ""))
                 + f" = {_show(y)}")
    solution = (f"Provjerimo tačku ${correct}$: ${chain}$ — koordinate se "
                "slažu, pa ona pripada grafiku. Ostale tačke ne "
                "zadovoljavaju jednakost.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_function_direct", operation="membership",
        level=level, question=question, answer_value=correct,
        answer_display=correct, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("function", display), ("point", f"{x}:{y}")],
        required_conditions=["membership"], relevant_objects=["function"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")


# ---------------------------------------------------------------------------
# MONOTONOST I ZNAK
# ---------------------------------------------------------------------------

def _monotonicity_package(rng, level, kind, concept, lesson_id, lesson_title):
    wants_increasing = rng.random() < 0.5
    used = set()

    def fresh_function(increasing):
        for _ in range(100):
            k = rng.randint(1, 9) * (1 if increasing else -1)
            n = rng.randint(-9, 9)
            if (k, n) not in used:
                used.add((k, n))
                return _function_display(k, n, "affine")
        raise DeterministicGenerationError("nema svježe funkcije")

    correct = fresh_function(wants_increasing)
    wrong = [fresh_function(not wants_increasing) for _ in range(3)]
    option_texts = (f"${correct}$", *(f"${w}$" for w in wrong))
    word = "rastuća" if wants_increasing else "opadajuća"
    question = f"Koja je od ponuđenih linearnih funkcija {word}?"
    hint1 = ("Tok linearne funkcije određuje koeficijent uz $x$: pozitivan "
             "k znači rastuću, negativan opadajuću funkciju.")
    hint2 = "Pogledaj samo broj uz $x$ — slobodni član ne utiče na tok."
    hint3 = f"Tražiš funkciju čiji je koeficijent uz $x$ {'pozitivan' if wants_increasing else 'negativan'}."
    solution = (f"Funkcija ${correct}$ ima "
                f"{'pozitivan' if wants_increasing else 'negativan'} "
                f"koeficijent uz $x$, pa je {word}. Ostale imaju suprotan "
                "predznak koeficijenta.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_function_direct", operation="monotonicity",
        level=level, question=question, answer_value=correct,
        answer_display=correct, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("target", word), ("function", correct)],
        required_conditions=["monotonicity"], relevant_objects=["function"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")


def _sign_analysis_package(rng, level, kind, concept, lesson_id, lesson_title):
    k = rng.randint(1, 5 if level == 1 else 9)
    if level > 1 and rng.random() < 0.5:
        k = -k
    zero = Fraction(rng.randint(-6, 6))
    n = -Fraction(k) * zero
    display = _function_display(k, n, "affine")
    positive = rng.random() < 0.5
    word = "pozitivna" if positive else "negativna"
    # f(x) > 0: za k>0 x > nula; za k<0 x < nula (i obrnuto za < 0).
    symbol = ">" if (positive == (k > 0)) else "<"
    correct = f"$x {symbol} {_show(zero)}$"
    other = {"<": ">", ">": "<"}[symbol]
    wrong = [f"$x {other} {_show(zero)}$",
             f"$x {symbol} {_show(-zero if zero != 0 else zero + 1)}$",
             f"$x {other} {_show(zero + (1 if symbol == '>' else -1))}$"]
    option_texts = (correct, *wrong)
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("skupovi nisu jedinstveni")
    question = f"Za koje vrijednosti $x$ je funkcija ${display}$ {word}?"
    hint1 = ("Prvo odredi nulu funkcije — ona dijeli brojevnu pravu na dio "
             "gdje je funkcija pozitivna i dio gdje je negativna.")
    hint2 = f"Nula funkcije je $x = {_show(zero)}$; znak zavisi od predznaka koeficijenta uz $x$."
    hint3 = "Uvrsti probni broj s jedne strane nule i pogledaj znak rezultata."
    probe = zero + (1 if symbol == ">" else -1)
    probe_value = Fraction(k) * probe + n
    solution = (f"Nula funkcije je $x = {_show(zero)}$. Probni broj "
                f"${_show(probe)}$ daje $f({_show(probe)}) = "
                f"{_show(probe_value)}$, što je "
                f"{'pozitivno' if probe_value > 0 else 'negativno'} — "
                f"funkcija je {word} za $x {symbol} {_show(zero)}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_function_direct", operation="sign_analysis",
        level=level, question=question,
        answer_value=(symbol, str(zero), word),
        answer_display=f"x {symbol} {_show(zero)}", distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("function", display), ("sign", word)],
        required_conditions=["sign_analysis"], relevant_objects=["function"],
        generator_version=GENERATOR_VERSION, option_texts=option_texts,
        wrap="")


# ---------------------------------------------------------------------------
# PRAVA KROZ DVIJE TAČKE I EKSPLICITNI OBLIK
# ---------------------------------------------------------------------------

def _from_two_points_package(rng, level, kind, concept, lesson_id,
                             lesson_title):
    k = rng.randint(1, 4 if level == 1 else 6)
    if rng.random() < 0.5:
        k = -k
    n = rng.randint(-6, 6)
    x1 = rng.randint(-4, 4)
    x2 = x1 + rng.randint(1, 4)
    y1 = k * x1 + n
    y2 = k * x2 + n
    display = _function_display(k, n, "affine").replace("f(x)", "y")
    point1 = _point("T_1", x1, y1)
    point2 = _point("T_2", x2, y2)
    question = (f"Odredi jednačinu prave koja prolazi kroz tačke "
                f"${point1}$ i ${point2}$.")
    wrong_displays = [
        _function_display(-k, n, "affine").replace("f(x)", "y"),
        _function_display(k, n + 1, "affine").replace("f(x)", "y"),
        _function_display(k + (1 if k > 0 else -1), n, "affine").replace("f(x)", "y"),
    ]
    option_texts = (f"${display}$", *(f"${w}$" for w in wrong_displays))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("jednačine nisu jedinstvene")
    slope_chain = (f"k = ({_show(y2)} - {_term_show(y1)}) : "
                   f"({_show(x2)} - {_term_show(x1)}) = {_show(Fraction(k))}")
    intercept_chain = (f"n = {_show(y1)} - {_show(Fraction(k))} \\cdot "
                       f"{_term_show(x1)} = {_show(Fraction(n))}")
    hint1 = ("Koeficijent pravca je količnik razlike y-koordinata i razlike "
             "x-koordinata; n zatim odrediš uvrštavanjem jedne tačke.")
    hint2 = f"Prvo k: ${slope_chain}$."
    hint3 = f"Zatim n: ${intercept_chain}$."
    solution = (f"Računamo koeficijent pravca: ${slope_chain}$, pa slobodni "
                f"član: ${intercept_chain}$. Jednačina prave je ${display}$.")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_function_direct", operation="from_two_points",
        level=level, question=question, answer_value=display,
        answer_display=display, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("points", f"{x1}:{y1}|{x2}:{y2}")],
        required_conditions=["from_two_points"],
        relevant_objects=["function"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")


def _implicit_package(rng, level, kind, concept, lesson_id, lesson_title):
    k = rng.randint(1, 5 if level == 1 else 9)
    if rng.random() < 0.5:
        k = -k
    n = rng.randint(1, 9)
    if rng.random() < 0.5:
        n = -n
    # Implicitni oblik: ax + by + c = 0 s b = 1 (školski slučaj bez razlomaka):
    # y = -ax - c  →  a = -k, c = -n.
    a = -k
    c = -n
    a_term = "x" if a == 1 else ("-x" if a == -1 else f"{a}x")
    c_term = f" + {c}" if c > 0 else f" - {abs(c)}"
    implicit = f"{a_term} + y{c_term} = 0"
    explicit = _function_display(k, n, "affine").replace("f(x)", "y")
    question = (f"Zapiši jednačinu prave ${implicit}$ u eksplicitnom obliku.")
    wrong_displays = [
        _function_display(-k, n, "affine").replace("f(x)", "y"),
        _function_display(k, -n, "affine").replace("f(x)", "y"),
        _function_display(-k, -n, "affine").replace("f(x)", "y"),
    ]
    option_texts = (f"${explicit}$", *(f"${w}$" for w in wrong_displays))
    if len(set(option_texts)) != 4:
        raise DeterministicGenerationError("oblici nisu jedinstveni")
    hint1 = ("Eksplicitni oblik je y = kx + n: prebaci sve ostale članove na "
             "desnu stranu jednačine.")
    hint2 = f"Iz ${implicit}$ prebaci članove sa $x$ i slobodni član desno."
    hint3 = "Pazi na predznake pri prebacivanju — svaki član mijenja predznak."
    solution = (f"Prebacimo članove: iz ${implicit}$ slijedi ${explicit}$ "
                "(svaki prebačeni član mijenja predznak).")
    return core.build_package(
        lesson_id=lesson_id, lesson_title=lesson_title,
        family_id="linear_function_direct", operation="implicit_to_explicit",
        level=level, question=question, answer_value=explicit,
        answer_display=explicit, distractor_values=(),
        hints=(hint1, hint2, hint3), solution=solution,
        signature_parameters=[("implicit", implicit)],
        required_conditions=["implicit_to_explicit"],
        relevant_objects=["function"], generator_version=GENERATOR_VERSION,
        option_texts=option_texts, wrap="")
