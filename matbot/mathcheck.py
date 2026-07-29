"""Deterministička provjera NUMERIČKE DOSLJEDNOSTI jednakosti u vidljivom tekstu.

ZAŠTO POSTOJI (živi nalaz, Explain, „Pravilni mnogougao“): model je naveo
ISPRAVNU formulu $P=\\frac{3a^2\\sqrt{3}}{2}$, a zatim za $a=4$ napisao lanac

    $P=\\frac{3\\cdot16\\sqrt{3}}{2}=48\\sqrt{3}\\approx83,14$

Tačno je $24\\sqrt{3}\\approx41,57$ — model je „zaboravio“ podijeliti s 2.
Formula je bila tačna, aritmetički lanac nije. Nijedna postojeća provjera
(MathJax sigurnost, terminologija, ugovor porodice) to ne može uhvatiti.

ŠTA OVAJ MODUL JESTE: uski, siguran *čuvar dosljednosti*. Uzima svaki $...$
segment, razbije ga na lanac dijelova razdvojenih znakom `=` ili `\\approx`,
numerički izračuna SAMO one dijelove koje sigurno može izračunati, i uporedi
susjedne izračunate vrijednosti. Neslaganje = odbijanje cijelog odgovora.

ŠTA OVAJ MODUL NIJE: dokazivač matematike. Izraz s promjenljivom
($c^2=a^2+b^2$, $x+7=15$) se PRESKAČE, ne „dokazuje“. Preskočen izraz nije
dokaz ispravnosti — samo odsustvo dokaza greške.

SIGURNOST: nikad `eval()`. Izraz se prevodi u ograničen Python podskup i
izvršava ručnim obilaskom AST-a uz strogu bijelu listu čvorova (samo
aritmetika, `sqrt(...)` i konstanta `PI`). Sve ostalo → nepodržano → preskoči.
"""
import ast
import math
import re

# Ludolfov broj: škola u BiH računa s π ≈ 3,14 (vidi referentni PDF), pa svaki
# izraz s π vrednujemo OBJEMA vrijednostima i prihvatamo ako se poklopi bilo
# koja — inače bi „$9\\pi \\approx 28,26$“ (tačno uz 3,14) bilo lažno odbijeno.
_PI_VALUES = (math.pi, 3.14)

# Relativna tolerancija za EGZAKTNO poređenje (oba dijela racionalna, bez
# decimalnog literala) — samo da apsorbuje šum binarnog zapisa.
_EXACT_REL_TOL = 1e-9

# Kad je relacija `\approx`, a nijedan dio nema decimalni literal iz kojeg bi
# se izvela preciznost, koristi se ova relativna toleranicija.
_APPROX_REL_TOL = 0.01


class _Unsupported(Exception):
    """Izraz sadrži nešto što ne umijemo sigurno izračunati — preskoči ga."""


class _MathError(Exception):
    """Izraz je matematički nevaljan (dijeljenje nulom, korijen negativnog)."""


# ---------------------------------------------------------------------------
# 1) IZDVAJANJE $...$ SEGMENATA
# ---------------------------------------------------------------------------

_DOLLAR_SPLIT = re.compile(r"(?<!\\)\$")


def math_segments(text):
    """Vrati sadržaj svakog $...$ segmenta (bez delimitera)."""
    if not text or "$" not in text:
        return []
    parts = _DOLLAR_SPLIT.split(text)
    if len(parts) % 2 == 0:
        return []  # neparan broj '$' — nije naš posao (mathsafe to već rješava)
    return [parts[i] for i in range(1, len(parts), 2)]


# ---------------------------------------------------------------------------
# 2) LaTeX → ograničen Python izraz
# ---------------------------------------------------------------------------

# Uređeni par „(3,2)“ — zarez tu NIJE decimalni separator. Takav dio se ne
# vrednuje da ne bismo „(3,2)“ pretvorili u broj 3,2.
_ORDERED_PAIR_RE = re.compile(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)")

# Konstrukcije koje ne umijemo (ili ne želimo) tumačiti numerički.
_UNSUPPORTED_RE = re.compile(
    r"\\circ|\\degree|°"          # uglovi
    r"|\\%|%"                     # procenti
    r"|\\sum|\\int|\\lim"         # analiza
    r"|\\begin|\\end"             # okruženja
    r"|\\sqrt\s*\["               # n-ti korijen
    r"|\\log|\\ln|\\sin|\\cos|\\tan"
    r"|\.\.\.|\\dots|\\ldots"
    r"|[<>≤≥≠]|\\le|\\ge|\\ne|\\neq|\\leq|\\geq"  # nejednakosti nisu jednakosti
)


def _strip_units_and_spacing(expr):
    """Ukloni jedinice i razmake koji ne utiču na vrijednost."""
    expr = re.sub(r"\\,|\\;|\\!|\\quad|\\qquad|\\ ", " ", expr)
    expr = re.sub(r"\\left|\\right", "", expr)
    # \text{cm}, \text{cm}^2, \mathrm{...} — jedinica; uklanjamo i eksponent
    # koji joj pripada (npr. \text{cm}^2 cijelo nestaje).
    expr = re.sub(r"\\(?:text|mathrm|mathit)\s*\{[^{}]*\}\s*(?:\^\s*(?:\{[^{}]*\}|\w))?", " ", expr)
    return expr


def _extract_argument(expr, start):
    """Vrati (sadržaj_argumenta, indeks_poslije) za argument LaTeX komande.

    Podržava OBA oblika koja LaTeX dozvoljava:
      • `{...}` s ugniježdenim zagradama — npr. \\sqrt{3}, \\frac{12}{5}
      • JEDAN token bez zagrada — npr. \\sqrt3, \\frac12
    Drugi oblik je bitan: živi model je pisao baš `\\sqrt3`, pa bi bez ovoga
    cijeli izraz bio „nepodržan“ i tiho preskočen umjesto provjeren.
    """
    while start < len(expr) and expr[start].isspace():
        start += 1
    if start >= len(expr):
        raise _Unsupported("nedostaje argument")
    if expr[start] == "{":
        depth = 0
        for i in range(start, len(expr)):
            if expr[i] == "{":
                depth += 1
            elif expr[i] == "}":
                depth -= 1
                if depth == 0:
                    return expr[start + 1:i], i + 1
        raise _Unsupported("nezatvorena '{'")
    if expr[start].isdigit() or expr[start].isalpha():
        return expr[start], start + 1
    raise _Unsupported("neprepoznat argument")


def _latex_to_python(expr):
    """Prevedi ograničen LaTeX podskup u Python izraz (string).

    Podržano: cijeli i decimalni brojevi (zarez ili tačka), zagrade, + - * /,
    stepen `^`, \\frac{}{}, \\sqrt{}, \\cdot, \\times, \\pi, implicitno množenje.
    """
    expr = _strip_units_and_spacing(expr)

    out = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "\\":
            if expr.startswith("\\frac", i):
                num, j = _extract_argument(expr, i + len("\\frac"))
                den, k = _extract_argument(expr, j)
                fraction = "((" + _latex_to_python(num) + ")/(" + _latex_to_python(den) + "))"
                # MJEŠOVITI BROJ: „$1\\frac{3}{20}$“ znači $1+\\frac{3}{20}$, NE
                # $1\\cdot\\frac{3}{20}$ (vidi pravilo zapisa u matbot/rules.py).
                # Cijeli broj neposredno ispred \\frac se zato „povuče“ nazad i
                # sabere. Množenje se u ovom projektu uvijek piše s \\cdot, pa
                # nema dvosmislenosti. (Ovo NE važi za \\sqrt: „$24\\sqrt{3}$“
                # jeste množenje.)
                digits = []
                while out and out[-1].isdigit():
                    digits.append(out.pop())
                if digits:
                    whole = "".join(reversed(digits))
                    out.append("((" + whole + ")+" + fraction + ")")
                else:
                    out.append(fraction)
                i = k
                continue
            if expr.startswith("\\sqrt", i):
                arg, j = _extract_argument(expr, i + len("\\sqrt"))
                out.append("sqrt((" + _latex_to_python(arg) + "))")
                i = j
                continue
            if expr.startswith("\\cdot", i):
                out.append("*")
                i += len("\\cdot")
                continue
            if expr.startswith("\\times", i):
                out.append("*")
                i += len("\\times")
                continue
            if expr.startswith("\\pi", i):
                out.append("PI")
                i += len("\\pi")
                continue
            raise _Unsupported("nepoznata LaTeX komanda")
        if ch == "^":
            out.append("**")
            i += 1
            if i < len(expr) and expr[i] == "{":
                arg, j = _extract_argument(expr, i)
                out.append("(" + _latex_to_python(arg) + ")")
                i = j
            continue
        if ch == "{" or ch == "}":
            out.append("(" if ch == "{" else ")")
            i += 1
            continue
        out.append(ch)
        i += 1

    text = "".join(out)
    # Decimalni zarez → tačka (samo između cifara).
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    return text


_IMPLICIT_PATTERNS = (
    (re.compile(r"(\d)\s*\("), r"\1*("),
    (re.compile(r"\)\s*\("), r")*("),
    (re.compile(r"\)\s*(\d)"), r")*\1"),
    (re.compile(r"(\d)\s*(sqrt|PI)"), r"\1*\2"),
    (re.compile(r"\)\s*(sqrt|PI)"), r")*\1"),
    (re.compile(r"(PI)\s*(\d)"), r"\1*\2"),
    (re.compile(r"(PI)\s*(sqrt)"), r"\1*\2"),
    (re.compile(r"(PI)\s*\("), r"\1*("),
)


def _insert_implicit_multiplication(text):
    previous = None
    while text != previous:
        previous = text
        for pattern, replacement in _IMPLICIT_PATTERNS:
            text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# 3) SIGURNO VREDNOVANJE (AST bijela lista — NIKAD eval)
# ---------------------------------------------------------------------------

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)


def _eval_node(node, pi_value):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, pi_value)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise _Unsupported("nedozvoljena konstanta")
        return float(node.value)
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise _Unsupported("nedozvoljena operacija")
        left = _eval_node(node.left, pi_value)
        right = _eval_node(node.right, pi_value)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise _MathError("dijeljenje nulom")
            return left / right
        # Pow — ograniči eksponent da spriječimo eksploziju (npr. 9**9**9).
        if abs(right) > 64 or abs(left) > 1e12:
            raise _Unsupported("prevelik stepen")
        try:
            result = left ** right
        except (OverflowError, ValueError, ZeroDivisionError):
            raise _MathError("nevaljan stepen")
        if isinstance(result, complex):
            raise _MathError("kompleksan rezultat")
        return float(result)
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            raise _Unsupported("nedozvoljen unarni operator")
        value = _eval_node(node.operand, pi_value)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Call):
        if (not isinstance(node.func, ast.Name) or node.func.id != "sqrt"
                or len(node.args) != 1 or node.keywords):
            raise _Unsupported("nedozvoljen poziv funkcije")
        value = _eval_node(node.args[0], pi_value)
        if value < 0:
            raise _MathError("korijen negativnog broja")
        return math.sqrt(value)
    if isinstance(node, ast.Name):
        if node.id == "PI":
            return pi_value
        raise _Unsupported("promjenljiva u izrazu")
    raise _Unsupported(f"nedozvoljen AST čvor: {type(node).__name__}")


def evaluate_candidates(latex_expr):
    """Vrati listu mogućih numeričkih vrijednosti izraza (jedna po vrijednosti
    π kad se π pojavljuje, inače jedna). Baca _Unsupported ili _MathError."""
    if _UNSUPPORTED_RE.search(latex_expr) or _ORDERED_PAIR_RE.search(latex_expr):
        raise _Unsupported("nepodržana konstrukcija")

    python_expr = _latex_to_python(latex_expr)
    python_expr = _insert_implicit_multiplication(python_expr).strip()
    if not python_expr:
        raise _Unsupported("prazan izraz")
    # Brza odbrana: sve što nije iz očekivanog skupa znakova → preskoči.
    if not re.fullmatch(r"[0-9+\-*/().\s]|[0-9+\-*/().\sA-Za-z]*", python_expr):
        raise _Unsupported("neočekivani znakovi")
    if re.search(r"[A-Za-z]+", python_expr.replace("sqrt", "").replace("PI", "")):
        raise _Unsupported("promjenljiva u izrazu")

    try:
        tree = ast.parse(python_expr, mode="eval")
    except SyntaxError:
        raise _Unsupported("neparsabilan izraz")

    uses_pi = "PI" in python_expr
    pi_values = _PI_VALUES if uses_pi else (math.pi,)
    return [_eval_node(tree, pi) for pi in pi_values]


# ---------------------------------------------------------------------------
# 4) POREĐENJE LANCA JEDNAKOSTI
# ---------------------------------------------------------------------------

_CHAIN_SPLIT = re.compile(r"(\\approx|≈|=)")
_DECIMAL_LITERAL_RE = re.compile(r"\d+[.,](\d+)")
_IRRATIONAL_RE = re.compile(r"\\sqrt|\\pi")


def _decimal_places(*expressions):
    places = 0
    for expr in expressions:
        for match in _DECIMAL_LITERAL_RE.finditer(expr):
            places = max(places, len(match.group(1)))
    return places


def _tolerance(left_expr, right_expr, relation, magnitude):
    """Tolerancija poređenja.

    • Oba dijela racionalna i bez decimalnog literala, relacija „=“ → egzaktno
      (samo šum float-a). Tako „$7/2=3$“ pada, kako i treba.
    • Postoji decimalni literal → tolerancija zaokruživanja iz njegove
      preciznosti (npr. 2 decimale → 0,005). Tako „$\\sqrt{2}=1,41$“ prolazi,
      a „$24\\sqrt{3}\\approx83,14$“ i dalje pada.
    • Iracionalno bez decimala uz „\\approx“ → mala relativna tolerancija.
    """
    places = _decimal_places(left_expr, right_expr)
    if places:
        return 0.5 * (10 ** -places) * 1.1
    has_irrational = bool(_IRRATIONAL_RE.search(left_expr) or _IRRATIONAL_RE.search(right_expr))
    if relation == "approx":
        return max(_APPROX_REL_TOL * abs(magnitude), 1e-9)
    if has_irrational:
        return max(_EXACT_REL_TOL * abs(magnitude), 1e-9)
    return max(_EXACT_REL_TOL * abs(magnitude), 1e-9)


def check_segment(segment):
    """Vrati listu poruka o nedosljednosti unutar JEDNOG $...$ segmenta."""
    tokens = _CHAIN_SPLIT.split(segment)
    if len(tokens) < 3:
        return []

    parts = tokens[0::2]
    separators = tokens[1::2]

    evaluated = []  # (index, candidates, raw_expr)
    for index, part in enumerate(parts):
        if not part.strip():
            continue
        try:
            evaluated.append((index, evaluate_candidates(part), part))
        except _MathError as e:
            return [f"numeric_equality_mismatch: nevaljan izraz ({e})"]
        except _Unsupported:
            continue  # sigurno preskakanje — nije dokaz ispravnosti

    issues = []
    for (left_i, left_vals, left_expr), (right_i, right_vals, right_expr) in zip(evaluated, evaluated[1:]):
        between = separators[left_i:right_i]
        relation = "approx" if any(s in ("\\approx", "≈") for s in between) else "eq"
        magnitude = max(abs(v) for v in left_vals + right_vals) or 1.0
        tol = _tolerance(left_expr, right_expr, relation, magnitude)
        if not any(abs(a - b) <= tol for a in left_vals for b in right_vals):
            issues.append(
                "numeric_equality_mismatch: "
                f"{left_expr.strip()!r} ({left_vals[0]:.6g}) != {right_expr.strip()!r} ({right_vals[0]:.6g})"
            )
    return issues


def find_numeric_inconsistencies(text):
    """Glavna ulazna tačka. Vrati listu INTERNIH razloga (prazno = nema
    dokazane nedosljednosti). Nikad ne mijenja tekst i nikad ne poziva model."""
    issues = []
    for segment in math_segments(text or ""):
        issues.extend(check_segment(segment))
    return issues


def is_numerically_consistent(text):
    return not find_numeric_inconsistencies(text)
