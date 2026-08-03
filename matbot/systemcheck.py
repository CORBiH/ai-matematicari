"""Deterministička provjera zadataka porodice `solve_system` — SUPSTITUCIJA,
ne rješavanje.

ZAŠTO POSTOJI (živi nalaz, poziv 8 fokusiranog testa, lekcija „Provjera
uređenog para u sistemu“). Model je prikazao sistem

    $2x+4y=10$
    $3x-y=2$

a kao tačan označio par $\\left(\\frac{13}{7},\\frac{11}{7}\\right)$ i isti taj
par ponovio u `expected_answer`. Taj par NE rješava prikazani sistem — on
rješava sistem $2x+4y=10$, $3x-y=4$ (model je interno koristio desnu stranu 4
umjesto 2 koju je sam ispisao). Tačno rješenje $\\left(\\frac{9}{7},
\\frac{13}{7}\\right)$ nije bilo ni ponuđeno: NIJEDNA od četiri opcije ne
zadovoljava obje jednačine.

Zadatak je prošao SVE postojeće validatore i stigao do učenika:
  • mathcheck preskače svaki izraz s promjenljivom („2x+4y“ → _Unsupported“)
    i svaki uređeni par („\\left(...,...\\right)“ → nepodržana konstrukcija“),
  • option_equivalence ne umije kanonikalizovati uređeni par (zarez nije token),
  • FamilyContract za `solve_system` ima `required=()` — dokazuje samo da
    pitanje NE traži metodu/broj rješenja/grešku, nikad da par rješava sistem,
  • answer_kind provjera je preskočena jer par s razlomcima nije bio prepoznat,
  • proizvod NIKAD ne poredi označenu opciju s `expected_answer`.

ŠTA OVAJ MODUL JESTE: uzak, egzaktan čuvar. Parsira TAČNO dvije linearne
jednačine s dvije nepoznate, parsira ponuđene uređene parove i SVAKI par
uvrsti u OBJE jednačine koristeći `fractions.Fraction` (bez zaokruživanja).
Traži da TAČNO JEDAN par zadovolji obje jednačine i da baš taj bude označen.

ŠTA OVAJ MODUL NIJE: simbolički rješavač. Ne rješava sistem, ne pogađa,
ne mijenja tekst i ne dira opcije. Sve što ne umije sigurno da parsira vraća
kao `unsupported` (kao mathcheck.py: preskočeno nije dokaz ispravnosti).

NE VJERUJE MODELU: ni `expected_answer` ni `correct_option_index` nisu dokaz —
oboje dolazi iz ISTOG poziva koji je i pogriješio. Jedini dokaz je supstitucija
u jednačine koje je učenik STVARNO vidio.

SIGURNOST: nikad `eval()`. Vlastiti tokenizer i rekurzivni spust nad zatvorenom
gramatikom; svaka nepoznata konstrukcija je greška parsiranja, ne pretpostavka.
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Optional, Sequence, Tuple

# --- interni kodovi (NIKAD u browser) --------------------------------------
SYSTEM_QUESTION_PARSE_FAILED = "system_question_parse_failed"
ORDERED_PAIR_PARSE_FAILED = "ordered_pair_parse_failed"
NO_CORRECT_OPTION = "no_mathematically_correct_option"
MULTIPLE_CORRECT_OPTIONS = "multiple_mathematically_correct_options"
MARKED_OPTION_MISMATCH = "marked_correct_option_math_mismatch"
EXPECTED_ANSWER_MISMATCH = "expected_answer_math_mismatch"
UNSUPPORTED_NONLINEAR = "unsupported_nonlinear_system"
UNSUPPORTED_SHAPE = "unsupported_system_shape"

# `identify_equivalent_system` — uzak verifier nad tačno dva reda sistema.
NO_EQUIVALENT_SYSTEM_OPTION = "no_equivalent_system_option"
MULTIPLE_EQUIVALENT_SYSTEM_OPTIONS = "multiple_equivalent_system_options"
MARKED_EQUIVALENT_SYSTEM_MISMATCH = "marked_equivalent_system_mismatch"
ORIGINAL_SYSTEM_PARSE_FAILED = "original_system_parse_failed"
OPTION_SYSTEM_PARSE_FAILED = "option_system_parse_failed"
UNSUPPORTED_EQUIVALENT_SYSTEM_SHAPE = "unsupported_equivalent_system_shape"

# `verify_ordered_pair` — četiri međusobno isključiva statusa.
AMBIGUOUS_ORDERED_PAIR_OPTION = "ambiguous_ordered_pair_option"
OVERLAPPING_ORDERED_PAIR_OPTIONS = "overlapping_ordered_pair_options"
NO_MATCHING_ORDERED_PAIR_STATUS = "no_matching_ordered_pair_status"
MULTIPLE_MATCHING_ORDERED_PAIR_STATUSES = "multiple_matching_ordered_pair_statuses"
MARKED_ORDERED_PAIR_STATUS_MISMATCH = "marked_ordered_pair_status_mismatch"
ORDERED_PAIR_QUESTION_PARSE_FAILED = "ordered_pair_question_parse_failed"

ALL_ISSUE_CODES = (
    SYSTEM_QUESTION_PARSE_FAILED, ORDERED_PAIR_PARSE_FAILED, NO_CORRECT_OPTION,
    MULTIPLE_CORRECT_OPTIONS, MARKED_OPTION_MISMATCH, EXPECTED_ANSWER_MISMATCH,
    UNSUPPORTED_NONLINEAR, UNSUPPORTED_SHAPE,
    NO_EQUIVALENT_SYSTEM_OPTION, MULTIPLE_EQUIVALENT_SYSTEM_OPTIONS,
    MARKED_EQUIVALENT_SYSTEM_MISMATCH, ORIGINAL_SYSTEM_PARSE_FAILED,
    OPTION_SYSTEM_PARSE_FAILED, UNSUPPORTED_EQUIVALENT_SYSTEM_SHAPE,
    AMBIGUOUS_ORDERED_PAIR_OPTION, OVERLAPPING_ORDERED_PAIR_OPTIONS,
    NO_MATCHING_ORDERED_PAIR_STATUS, MULTIPLE_MATCHING_ORDERED_PAIR_STATUSES,
    MARKED_ORDERED_PAIR_STATUS_MISMATCH, ORDERED_PAIR_QUESTION_PARSE_FAILED,
)

STATUS_VERIFIED = "verified"
STATUS_INVALID = "invalid"
STATUS_UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class SystemVerificationResult:
    status: str                       # "verified" | "invalid" | "unsupported"
    issue_codes: tuple = ()
    valid_option_indices: tuple = ()
    marked_option_index: int = -1
    parsed_equations: Optional[tuple] = None   # ((a,b,c), (a,b,c))
    parsed_options: Optional[tuple] = None     # ((x,y) | None, ...)


@dataclass(frozen=True)
class EquivalentSystemVerificationResult:
    status: str
    issue_codes: tuple = ()
    equivalent_option_indices: tuple = ()
    marked_option_index: int = -1
    original_rref: Optional[tuple] = None
    option_rrefs: Optional[tuple] = None


@dataclass(frozen=True)
class OrderedPairVerificationResult:
    status: str
    issue_codes: tuple = ()
    computed_pair_status: Optional[str] = None
    matching_option_indices: tuple = ()
    marked_option_index: int = -1
    equation_truth_values: Optional[tuple] = None
    mapped_option_statuses: Optional[tuple] = None


class _Unsupported(Exception):
    """Konstrukcija koju ne umijemo sigurno parsirati — NIKAD se ne pogađa."""


# ---------------------------------------------------------------------------
# 1) LINEARNA FORMA  a*x + b*y + k
# ---------------------------------------------------------------------------

class _Lin:
    __slots__ = ("a", "b", "k")

    def __init__(self, a=0, b=0, k=0):
        self.a, self.b, self.k = Fraction(a), Fraction(b), Fraction(k)

    @property
    def is_const(self):
        return self.a == 0 and self.b == 0

    def __add__(self, o):
        return _Lin(self.a + o.a, self.b + o.b, self.k + o.k)

    def __sub__(self, o):
        return _Lin(self.a - o.a, self.b - o.b, self.k - o.k)

    def __neg__(self):
        return _Lin(-self.a, -self.b, -self.k)

    def __mul__(self, o):
        # Linearnost: bar jedan faktor MORA biti konstanta.
        if self.is_const:
            return _Lin(o.a * self.k, o.b * self.k, o.k * self.k)
        if o.is_const:
            return _Lin(self.a * o.k, self.b * o.k, self.k * o.k)
        raise _Unsupported("nelinearan proizvod")

    def __truediv__(self, o):
        if not o.is_const or o.k == 0:
            raise _Unsupported("dijeljenje nekonstantom ili nulom")
        return _Lin(self.a / o.k, self.b / o.k, self.k / o.k)


# ---------------------------------------------------------------------------
# 2) TOKENIZER
# ---------------------------------------------------------------------------

_CLEAN_RE = re.compile(r"\\left|\\right|\\,|\\;|\\!|\\quad|\\qquad|\\ |\s+")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _clean(expr):
    return _CLEAN_RE.sub("", expr or "")


def _to_fraction(token):
    """Egzaktna konverzija cijelog/decimalnog broja u Fraction.
    „0,5“ → 1/2 (preko Decimal, bez binarnog float šuma)."""
    text = token.replace(",", ".")
    try:
        return Fraction(Decimal(text))
    except (InvalidOperation, ValueError, ZeroDivisionError):
        raise _Unsupported(f"neparsabilan broj {token!r}")


def _tokenize(expr):
    tokens = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if expr.startswith("\\frac", i):
            tokens.append(("FRAC", None)); i += 5; continue
        if expr.startswith("\\cdot", i):
            tokens.append(("OP", "*")); i += 5; continue
        if expr.startswith("\\times", i):
            tokens.append(("OP", "*")); i += 6; continue
        if ch == "\\":
            raise _Unsupported("nepoznata LaTeX komanda")
        if ch in "+-*/^":
            if ch == "^":
                raise _Unsupported("stepen — nelinearno")
            tokens.append(("OP", ch)); i += 1; continue
        if ch == "(":
            tokens.append(("LP", None)); i += 1; continue
        if ch == ")":
            tokens.append(("RP", None)); i += 1; continue
        if ch == "{":
            tokens.append(("LB", None)); i += 1; continue
        if ch == "}":
            tokens.append(("RB", None)); i += 1; continue
        m = _NUM_RE.match(expr, i)
        if m:
            tokens.append(("NUM", m.group(0))); i = m.end(); continue
        if ch.isalpha():
            if ch not in ("x", "y"):
                raise _Unsupported(f"nepodržana promjenljiva {ch!r}")
            tokens.append(("VAR", ch)); i += 1; continue
        raise _Unsupported(f"neočekivan znak {ch!r}")
    return tokens


# ---------------------------------------------------------------------------
# 3) REKURZIVNI SPUST → _Lin
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, tokens):
        self.t = tokens
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, kind):
        k, v = self.next()
        if k != kind:
            raise _Unsupported(f"očekivano {kind}, dobijeno {k}")
        return v

    def parse(self):
        value = self.expr()
        if self.i != len(self.t):
            raise _Unsupported("neparsiran ostatak izraza")
        return value

    def expr(self):
        if self.peek()[0] == "OP" and self.peek()[1] in "+-":
            sign = self.next()[1]
            value = self.term()
            if sign == "-":
                value = -value
        else:
            value = self.term()
        while self.peek()[0] == "OP" and self.peek()[1] in "+-":
            op = self.next()[1]
            rhs = self.term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def term(self):
        value = self.factor()
        while True:
            kind, op = self.peek()
            if kind == "OP" and op in "*/":
                self.next()
                rhs = self.factor()
                value = value * rhs if op == "*" else value / rhs
            elif kind in ("NUM", "VAR", "LP", "FRAC"):
                value = value * self.factor()      # implicitno množenje: 2x, 2(x+y)
            else:
                break
        return value

    def factor(self):
        kind, val = self.peek()
        if kind == "OP" and val == "-":
            self.next()
            return -self.factor()
        if kind == "NUM":
            self.next()
            return _Lin(k=_to_fraction(val))
        if kind == "VAR":
            self.next()
            return _Lin(a=1) if val == "x" else _Lin(b=1)
        if kind == "LP":
            self.next()
            inner = self.expr()
            self.expect("RP")
            return inner
        if kind == "FRAC":
            self.next()
            num = self.braced()
            den = self.braced()
            return num / den
        raise _Unsupported(f"neočekivan token {kind}")

    def braced(self):
        if self.peek()[0] != "LB":
            # \frac12 — jednoznakovni argumenti
            kind, val = self.next()
            if kind == "NUM":
                return _Lin(k=_to_fraction(val))
            if kind == "VAR":
                return _Lin(a=1) if val == "x" else _Lin(b=1)
            raise _Unsupported("neispravan argument \\frac")
        self.next()
        inner = self.expr()
        self.expect("RB")
        return inner


def _parse_linear(expr):
    return _Parser(_tokenize(_clean(expr))).parse()


def parse_equation(segment):
    """„2x+4y=10“ → (a, b, c) za a*x+b*y=c, egzaktno. Baca _Unsupported."""
    if segment.count("=") != 1:
        raise _Unsupported("jednačina mora imati tačno jedan znak jednakosti")
    left, right = segment.split("=")
    lin = _parse_linear(left) - _parse_linear(right)
    if lin.a == 0 and lin.b == 0:
        raise _Unsupported("jednačina bez nepoznatih")
    return (lin.a, lin.b, -lin.k)


_DOLLAR_SPLIT = re.compile(r"(?<!\\)\$")
_FORBIDDEN_RE = re.compile(r"\\begin|\\end|\\sqrt|\\log|\\ln|\\sin|\\cos|\\tan|\\int|\\sum|\^")


def parse_system(question):
    """Izdvoji TAČNO dvije linearne jednačine iz $...$ segmenata pitanja."""
    if not question:
        raise _Unsupported("prazno pitanje")
    parts = _DOLLAR_SPLIT.split(question)
    if len(parts) % 2 == 0:
        raise _Unsupported("neparan broj $ delimitera")
    segments = [p for i, p in enumerate(parts) if i % 2 == 1 and "=" in p]
    if _FORBIDDEN_RE.search(question):
        raise _Unsupported("nepodržana konstrukcija u pitanju")
    if len(segments) != 2:
        raise _Unsupported(f"očekivane tačno 2 jednačine, nađeno {len(segments)}")
    return tuple(parse_equation(s) for s in segments)


# ---------------------------------------------------------------------------
# 3a) EKVIVALENTNI SISTEMI — TAČNO 2x2, egzaktni prošireni redovi
# ---------------------------------------------------------------------------

_OPTION_SEPARATOR_SPACING_RE = re.compile(
    r"(?:\s+|\\;|\\,|\\!|\\quad|\\qquad|\\ )+"
)


def _strip_outer_system_wrapper(value):
    """Ukloni samo jedan par bezazlenih omotača oko CIJELOG sistema."""
    text = (value or "").strip()
    if text.startswith(r"\left(") and text.endswith(r"\right)"):
        return text[len(r"\left("):-len(r"\right)")].strip()
    if not (text.startswith("(") and text.endswith(")")):
        return text
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(text) - 1:
                return text
        if depth < 0:
            return text
    return text[1:-1].strip() if depth == 0 else text


def _candidate_option_separator_spans(value):
    """Vrati top-level kandidate; decimalni zarez bez razmaka nije kandidat."""
    spans = []
    paren_depth = brace_depth = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif paren_depth == 0 and brace_depth == 0:
            if char == ";":
                match = _OPTION_SEPARATOR_SPACING_RE.match(value, index + 1)
                spans.append((index, match.end() if match else index + 1))
            elif char == ",":
                match = _OPTION_SEPARATOR_SPACING_RE.match(value, index + 1)
                if match:
                    spans.append((index, match.end()))
            elif char in "\r\n":
                end = index + 1
                while end < len(value) and value[end] in " \t\r\n":
                    end += 1
                spans.append((index, end))
                index = end - 1
        index += 1
    return tuple(dict.fromkeys(spans))


def _parse_unique_option_system_split(value):
    """Prihvati samo kada TAČNO jedan kandidat daje dvije validne jednačine."""
    content = _strip_outer_system_wrapper(value)
    if content.count("=") != 2:
        raise _Unsupported("očekivane tačno dvije jednačine u opciji")
    valid = []
    for start, end in _candidate_option_separator_spans(content):
        left, right = content[:start].strip(), content[end:].strip()
        if not left or not right or left.count("=") != 1 or right.count("=") != 1:
            continue
        try:
            parsed = (parse_equation(left), parse_equation(right))
        except _Unsupported:
            continue
        valid.append(((start, end), parsed))
    if len(valid) != 1:
        raise _Unsupported(f"broj validnih razdvajanja sistema: {len(valid)}")
    return valid[0][1]


def parse_option_system(text):
    """Pročitaj TAČNO jedan sistem od dvije jednačine iz jedne opcije.

    Podržava dva ``$...$`` bloka te jedan blok ili čisti tekst s dvije
    jednačine. Separator se ne bira napamet: svaki top-level kandidat se
    provjeri postojećim restriktivnim parserom, a prihvata se samo jedinstveno
    validno razdvajanje. Zato decimalni zarez ostaje dio broja.
    """
    if not text:
        raise _Unsupported("prazna opcija sistema")
    parts = _DOLLAR_SPLIT.split(str(text))
    if len(parts) % 2 == 0:
        raise _Unsupported("neparan broj $ delimitera u opciji")
    outside = " ".join(parts[0::2])
    if re.search(r"\bili\b", outside, re.IGNORECASE):
        raise _Unsupported("opcija sadrži više alternativa")
    if "=" in outside and len(parts) > 1:
        raise _Unsupported("jednačina izvan MathJax bloka čini opciju dvosmislenom")
    math_segments = [part for i, part in enumerate(parts) if i % 2 == 1]

    if len(math_segments) == 2 and all(segment.count("=") == 1 for segment in math_segments):
        return tuple(parse_equation(_strip_outer_system_wrapper(equation))
                     for equation in math_segments)
    if len(math_segments) == 1:
        return _parse_unique_option_system_split(math_segments[0])
    if not math_segments:
        return _parse_unique_option_system_split(str(text))
    raise _Unsupported("očekivan tačno jedan sistem sa dvije jednačine")


def rref_augmented_system(rows):
    """Egzaktni RREF 2x3 proširene matrice koristeći samo ``Fraction``.

    RREF poređenje znači poređenje tačne redne ekvivalencije. Zato se dva
    proizvoljna kontradiktorna sistema NE proglašavaju jednakim samo zato što
    oba imaju prazan skup rješenja.
    """
    if len(rows) != 2 or any(len(row) != 3 for row in rows):
        raise _Unsupported("sistem mora dati proširenu matricu 2x3")
    matrix = [[Fraction(value) for value in row] for row in rows]
    pivot_row = 0
    for column in range(3):
        pivot = next(
            (row for row in range(pivot_row, len(matrix)) if matrix[row][column] != 0),
            None,
        )
        if pivot is None:
            continue
        matrix[pivot_row], matrix[pivot] = matrix[pivot], matrix[pivot_row]
        scale = matrix[pivot_row][column]
        matrix[pivot_row] = [value / scale for value in matrix[pivot_row]]
        for row in range(len(matrix)):
            if row == pivot_row:
                continue
            factor = matrix[row][column]
            if factor != 0:
                matrix[row] = [
                    value - factor * base
                    for value, base in zip(matrix[row], matrix[pivot_row])
                ]
        pivot_row += 1
        if pivot_row == len(matrix):
            break
    return tuple(tuple(row) for row in matrix)


def verify_equivalent_system_options(
        question: str, option_texts: Sequence[str], marked_option_index: int
):
    """Provjeri opcije SAMO porodice ``identify_equivalent_system``.

    Nepodržana sintaksa vraća ``unsupported``; matematički dokazano nula/više
    ekvivalentnih opcija ili pogrešan označeni indeks vraća ``invalid``.
    Funkcija nikad ne baca i ne rješava opštu simboličku algebru.
    """
    options = tuple(option_texts or ())
    if len(options) != 4 or not (0 <= marked_option_index < len(options)):
        return EquivalentSystemVerificationResult(
            status=STATUS_UNSUPPORTED,
            issue_codes=(UNSUPPORTED_EQUIVALENT_SYSTEM_SHAPE,),
            marked_option_index=marked_option_index,
        )
    try:
        original = parse_system(question)
        original_rref = rref_augmented_system(original)
    except _Unsupported:
        return EquivalentSystemVerificationResult(
            status=STATUS_UNSUPPORTED,
            issue_codes=(ORIGINAL_SYSTEM_PARSE_FAILED, UNSUPPORTED_EQUIVALENT_SYSTEM_SHAPE),
            marked_option_index=marked_option_index,
        )

    option_rrefs = []
    parse_failed = False
    for option in options:
        try:
            option_rrefs.append(rref_augmented_system(parse_option_system(option)))
        except _Unsupported:
            option_rrefs.append(None)
            parse_failed = True
    parsed_rrefs = tuple(option_rrefs)
    if parse_failed:
        return EquivalentSystemVerificationResult(
            status=STATUS_UNSUPPORTED,
            issue_codes=(OPTION_SYSTEM_PARSE_FAILED, UNSUPPORTED_EQUIVALENT_SYSTEM_SHAPE),
            marked_option_index=marked_option_index,
            original_rref=original_rref,
            option_rrefs=parsed_rrefs,
        )

    equivalent = tuple(
        index for index, option_rref in enumerate(parsed_rrefs)
        if option_rref == original_rref
    )
    issues = []
    if not equivalent:
        issues.append(NO_EQUIVALENT_SYSTEM_OPTION)
    elif len(equivalent) > 1:
        issues.append(MULTIPLE_EQUIVALENT_SYSTEM_OPTIONS)
    elif equivalent[0] != marked_option_index:
        issues.append(MARKED_EQUIVALENT_SYSTEM_MISMATCH)

    return EquivalentSystemVerificationResult(
        status=STATUS_INVALID if issues else STATUS_VERIFIED,
        issue_codes=tuple(issues),
        equivalent_option_indices=equivalent,
        marked_option_index=marked_option_index,
        original_rref=original_rref,
        option_rrefs=parsed_rrefs,
    )


# ---------------------------------------------------------------------------
# 4) UREĐENI PAROVI
# ---------------------------------------------------------------------------

_SCALAR_RE = re.compile(
    r"^-?(?:\\frac\{-?\d+\}\{-?\d+\}|\d+/-?\d+|\d+(?:[.,]\d+)?)$"
)
_XY_FORM_RE = re.compile(
    r"x\s*=\s*(?P<x>-?(?:\\frac\{-?\d+\}\{-?\d+\}|\d+/-?\d+|\d+(?:[.,]\d+)?))"
    r"\s*[,;]\s*"
    r"y\s*=\s*(?P<y>-?(?:\\frac\{-?\d+\}\{-?\d+\}|\d+/-?\d+|\d+(?:[.,]\d+)?))"
)
_PAREN_RE = re.compile(r"\(([^()]*)\)")


def _parse_scalar(token):
    t = _clean(token)
    if not _SCALAR_RE.match(t):
        raise _Unsupported(f"neparsabilan skalar {token!r}")
    neg = t.startswith("-")
    if neg:
        t = t[1:]
    m = re.fullmatch(r"\\frac\{(-?\d+)\}\{(-?\d+)\}", t)
    if m:
        den = int(m.group(2))
        if den == 0:
            raise _Unsupported("nazivnik 0")
        value = Fraction(int(m.group(1)), den)
    else:
        m = re.fullmatch(r"(\d+)/(-?\d+)", t)
        if m:
            den = int(m.group(2))
            if den == 0:
                raise _Unsupported("nazivnik 0")
            value = Fraction(int(m.group(1)), den)
        else:
            value = _to_fraction(t)
    return -value if neg else value


def parse_exact_scalar(text):
    """Parse a bare integer/decimal/fraction as :class:`Fraction`.

    Public, fail-closed facade over the exact scalar parser used by the system
    verifier.  ``None`` means unsupported or ambiguous; callers must never
    treat it as a valid value.  Math delimiters and harmless LaTeX spacing are
    accepted, but prose and compound expressions are intentionally rejected.
    """
    value = (text or "").strip()
    if len(value) >= 2 and value.startswith("$") and value.endswith("$"):
        value = value[1:-1].strip()
    try:
        return _parse_scalar(value)
    except (_Unsupported, ValueError, ZeroDivisionError):
        return None


def _split_pair_body(body):
    """Razdvoji sadržaj zagrade na dvije koordinate.

    Tačka-zarez je NEDVOSMISLEN separator (koristi se baš kad koordinate imaju
    decimalni zarez). Za zarez se traži da razdvajanje da TAČNO dva dijela —
    „0,5;-1,25“ ide preko „;“, a „0,5“ (jedan decimalni broj) daje dva dijela
    koja su oba cijeli brojevi i tretira se kao par SAMO uz zagrade (vidi
    parse_ordered_pair), što je i postojeća konvencija projekta."""
    if ";" in body:
        parts = body.split(";")
    else:
        parts = body.split(",")
    if len(parts) != 2:
        raise _Unsupported("koordinate nisu jednoznačne")
    return parts[0], parts[1]


def parse_ordered_pair(text):
    """Vrati (x, y) kao Fraction, ili None kad se ne može SIGURNO pročitati.

    Podržano: $(3,2)$, $(-3,2)$, $\\left(3,2\\right)$,
    $\\left(\\frac{9}{7},\\frac{13}{7}\\right)$, $(0,5;-1,25)$,
    $x=3,\\ y=2$, i prozni omotač koji sadrži TAČNO JEDAN takav par.
    Redoslijed koordinata se čuva."""
    if not text:
        return None
    raw = text.strip()
    cleaned = _clean(raw.replace("$", " "))

    m = _XY_FORM_RE.search(cleaned)
    if m:
        try:
            return (_parse_scalar(m.group("x")), _parse_scalar(m.group("y")))
        except _Unsupported:
            return None

    bodies = _PAREN_RE.findall(cleaned)
    if len(bodies) != 1:
        return None            # nula ili više parova → dvosmisleno, ne pogađaj
    try:
        left, right = _split_pair_body(bodies[0])
        return (_parse_scalar(left), _parse_scalar(right))
    except _Unsupported:
        return None


_BARE_PAIR_RE = re.compile(r"^\([^()]*\)$")


def is_bare_ordered_pair(text):
    """True SAMO kad je CIJELA opcija uređeni par — ne proza koja par spominje.

    Ovo je predikat za `answer_kind` (matbot/task_family_validation.py), gdje
    je razlika ključna: „Par $(2,1)$ zadovoljava obje jednačine.“ je TVRDNJA
    (option_label/short_text), a ne uređeni par. Živi nalaz koji ovo štiti je
    lažno odbijen `verify_ordered_pair` zadatak — vidi
    tests/test_student_must_find_trust.py.

    `parse_ordered_pair` je namjerno tolerantniji (vadi par i iz proze) jer se
    koristi za čitanje `expected_answer`; ovdje je potrebna stroga varijanta."""
    if not text:
        return False
    cleaned = _clean(str(text).replace("$", " "))
    if _BARE_PAIR_RE.match(cleaned):
        return parse_ordered_pair(text) is not None
    if _XY_FORM_RE.fullmatch(cleaned):
        return True
    return False


def looks_like_ordered_pair(text):
    """Tolerantno prepoznavanje (uključujući par u prozi) — koristi se tamo
    gdje je cilj IZVUĆI par, ne klasifikovati tip odgovora."""
    return parse_ordered_pair(text) is not None


# ---------------------------------------------------------------------------
# 5) SUPSTITUCIJA I VERDIKT
# ---------------------------------------------------------------------------

def satisfies(equation, pair):
    a, b, c = equation
    x, y = pair
    return a * x + b * y == c


PAIR_SATISFIES_BOTH = "satisfies_both"
PAIR_SATISFIES_ONLY_FIRST = "satisfies_only_first"
PAIR_SATISFIES_ONLY_SECOND = "satisfies_only_second"
PAIR_SATISFIES_NEITHER = "satisfies_neither"
PAIR_STATUS_UNSUPPORTED = "unsupported"
ORDERED_PAIR_STATUSES = (
    PAIR_SATISFIES_BOTH,
    PAIR_SATISFIES_ONLY_FIRST,
    PAIR_SATISFIES_ONLY_SECOND,
    PAIR_SATISFIES_NEITHER,
)
_AMBIGUOUS_OPTION = "ambiguous"


def _pair_from_question(question):
    """Izdvoji jedan par koji se provjerava, bez miješanja sa jednačinama."""
    parts = _DOLLAR_SPLIT.split(question or "")
    if len(parts) % 2 == 1:
        candidates = []
        for index, segment in enumerate(parts):
            if index % 2 == 0 or "=" in segment:
                continue
            pair = parse_ordered_pair(f"${segment}$")
            if pair is not None:
                candidates.append(pair)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return None
    return parse_ordered_pair(question)


def _ordered_pair_truth(question):
    equations = parse_system(question)
    pair = _pair_from_question(question)
    if pair is None:
        raise _Unsupported("nije pronađen tačno jedan uređeni par")
    truth = tuple(satisfies(equation, pair) for equation in equations)
    if len(truth) != 2:
        raise _Unsupported("očekivane dvije istinitosne vrijednosti")
    return equations, pair, truth


def classify_ordered_pair_against_system(question):
    """Vrati jedan od četiri statusa ili ``unsupported``; nikad ne baca."""
    try:
        _equations, _pair, truth = _ordered_pair_truth(question)
    except _Unsupported:
        return PAIR_STATUS_UNSUPPORTED
    return {
        (True, True): PAIR_SATISFIES_BOTH,
        (True, False): PAIR_SATISFIES_ONLY_FIRST,
        (False, True): PAIR_SATISFIES_ONLY_SECOND,
        (False, False): PAIR_SATISFIES_NEITHER,
    }[truth]


_PAIR_WORD = r"jednačin(?:a|e|i|u|om|ama)?"
_ONLY_FIRST_PATTERNS = (
    re.compile(rf"\bzadovoljava\s+samo\s+(?:prvu|1\.)(?:\s+{_PAIR_WORD})?\b", re.IGNORECASE),
    re.compile(rf"\bsamo\s+(?:prva|1\.)\s+{_PAIR_WORD}\s+(?:je\s+)?zadovoljena\b", re.IGNORECASE),
    re.compile(r"\bzadovoljava\s+prvu\b.*\b(?:ali|a)\s+ne\s+(?:zadovoljava\s+)?drugu\b", re.IGNORECASE),
    re.compile(r"\bprvu\s+zadovoljava\b.*\bdrugu\s+ne\b", re.IGNORECASE),
)
_ONLY_SECOND_PATTERNS = (
    re.compile(rf"\bzadovoljava\s+samo\s+(?:drugu|2\.)(?:\s+{_PAIR_WORD})?\b", re.IGNORECASE),
    re.compile(rf"\bsamo\s+(?:druga|2\.)\s+{_PAIR_WORD}\s+(?:je\s+)?zadovoljena\b", re.IGNORECASE),
    re.compile(r"\bzadovoljava\s+drugu\b.*\b(?:ali|a)\s+ne\s+(?:zadovoljava\s+)?prvu\b", re.IGNORECASE),
    re.compile(r"\bdrugu\s+zadovoljava\b.*\bprvu\s+ne\b", re.IGNORECASE),
)
_BOTH_PATTERNS = (
    re.compile(rf"\bzadovoljava\s+(?:obje|obe|oba)\s+{_PAIR_WORD}\b", re.IGNORECASE),
    re.compile(rf"\b(?:obje|obe|oba)\s+{_PAIR_WORD}\s+(?:su\s+)?zadovoljen", re.IGNORECASE),
)
_NEITHER_PATTERNS = (
    re.compile(rf"\bne\s+zadovoljava\s+nijednu\s+{_PAIR_WORD}\b", re.IGNORECASE),
    re.compile(rf"\bnijedna\s+{_PAIR_WORD}\s+(?:nije\s+)?zadovoljena\b", re.IGNORECASE),
    re.compile(r"\bne\s+zadovoljava\s+ni\s+prvu\s+ni\s+drugu\b", re.IGNORECASE),
)
_AMBIGUOUS_PAIR_OPTION_PATTERNS = (
    re.compile(rf"\bne\s+zadovoljava\s+(?:prvu|drugu)\s+{_PAIR_WORD}\b", re.IGNORECASE),
    re.compile(rf"\b(?:prva|druga)\s+{_PAIR_WORD}\s+nije\s+zadovoljena\b", re.IGNORECASE),
    re.compile(rf"\bu\s+(?:prvoj|drugoj)\s+{_PAIR_WORD}\b.*(?:\\neq|≠|nije|ne\s+vrijedi)", re.IGNORECASE),
    re.compile(rf"\bne\s+zadovoljava\s+(?:obje|obe|oba)\s+{_PAIR_WORD}\b", re.IGNORECASE),
)

_PAIR_TEXT_WRAPPER_RE = re.compile(r"\\text\s*\{([^{}]*)\}")
_PAIR_MATH_SPACING_RE = re.compile(r"\\(?:,|;|!|quad|qquad)|\\\s")


def _normalize_ordered_pair_option_text(text):
    """Spljošti samo bezazlene MathJax tekstualne omotače prije mapiranja."""
    value = str(text or "").replace("$", " ")
    previous = None
    while value != previous:
        previous = value
        value = _PAIR_TEXT_WRAPPER_RE.sub(r"\1", value)
    value = value.replace(r"\left", "").replace(r"\right", "")
    value = _PAIR_MATH_SPACING_RE.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def map_ordered_pair_option_meaning(text):
    """Usko mapiranje kanonske bosanske tvrdnje; proizvoljna proza → ``None``.

    Povratna vrijednost ``ambiguous`` označava poznatu preklapajuću formulaciju
    poput „ne zadovoljava prvu“, koja se mora odbiti, a ne tumačiti kao „samo
    druga“.
    """
    value = _normalize_ordered_pair_option_text(text)
    for status, patterns in (
        (PAIR_SATISFIES_ONLY_FIRST, _ONLY_FIRST_PATTERNS),
        (PAIR_SATISFIES_ONLY_SECOND, _ONLY_SECOND_PATTERNS),
        (PAIR_SATISFIES_BOTH, _BOTH_PATTERNS),
        (PAIR_SATISFIES_NEITHER, _NEITHER_PATTERNS),
    ):
        if any(pattern.search(value) for pattern in patterns):
            return status
    if any(pattern.search(value) for pattern in _AMBIGUOUS_PAIR_OPTION_PATTERNS):
        return _AMBIGUOUS_OPTION
    return None


def ordered_pair_options_are_mutually_exclusive(option_texts):
    """True samo za četiri prepoznata, međusobno različita kanonska statusa."""
    mapped = tuple(map_ordered_pair_option_meaning(text) for text in (option_texts or ()))
    return len(mapped) == 4 and set(mapped) == set(ORDERED_PAIR_STATUSES)


def verify_ordered_pair_options(
        question: str, option_texts: Sequence[str], marked_option_index: int
):
    """Provjeri četiri statusne opcije porodice ``verify_ordered_pair``."""
    options = tuple(option_texts or ())
    if len(options) != 4 or not (0 <= marked_option_index < len(options)):
        return OrderedPairVerificationResult(
            status=STATUS_UNSUPPORTED,
            issue_codes=(ORDERED_PAIR_QUESTION_PARSE_FAILED,),
            marked_option_index=marked_option_index,
        )
    try:
        _equations, _pair, truth = _ordered_pair_truth(question)
    except _Unsupported:
        return OrderedPairVerificationResult(
            status=STATUS_UNSUPPORTED,
            issue_codes=(ORDERED_PAIR_QUESTION_PARSE_FAILED,),
            marked_option_index=marked_option_index,
        )

    computed = {
        (True, True): PAIR_SATISFIES_BOTH,
        (True, False): PAIR_SATISFIES_ONLY_FIRST,
        (False, True): PAIR_SATISFIES_ONLY_SECOND,
        (False, False): PAIR_SATISFIES_NEITHER,
    }[truth]
    mapped = tuple(map_ordered_pair_option_meaning(text) for text in options)
    if _AMBIGUOUS_OPTION in mapped:
        return OrderedPairVerificationResult(
            status=STATUS_INVALID,
            issue_codes=(AMBIGUOUS_ORDERED_PAIR_OPTION,),
            computed_pair_status=computed,
            marked_option_index=marked_option_index,
            equation_truth_values=truth,
            mapped_option_statuses=mapped,
        )
    if any(status is None for status in mapped):
        return OrderedPairVerificationResult(
            status=STATUS_UNSUPPORTED,
            issue_codes=(AMBIGUOUS_ORDERED_PAIR_OPTION,),
            computed_pair_status=computed,
            marked_option_index=marked_option_index,
            equation_truth_values=truth,
            mapped_option_statuses=mapped,
        )

    matching = tuple(index for index, status in enumerate(mapped) if status == computed)
    issues = []
    if len(set(mapped)) != len(mapped) or set(mapped) != set(ORDERED_PAIR_STATUSES):
        issues.append(OVERLAPPING_ORDERED_PAIR_OPTIONS)
    if not matching:
        issues.append(NO_MATCHING_ORDERED_PAIR_STATUS)
    elif len(matching) > 1:
        issues.append(MULTIPLE_MATCHING_ORDERED_PAIR_STATUSES)
    elif matching[0] != marked_option_index:
        issues.append(MARKED_ORDERED_PAIR_STATUS_MISMATCH)

    return OrderedPairVerificationResult(
        status=STATUS_INVALID if issues else STATUS_VERIFIED,
        issue_codes=tuple(issues),
        computed_pair_status=computed,
        matching_option_indices=matching,
        marked_option_index=marked_option_index,
        equation_truth_values=truth,
        mapped_option_statuses=mapped,
    )


def verify_solve_system(question, option_texts, correct_option_index,
                        expected_answer=""):
    """Glavna ulazna tačka. NIKAD ne baca — vraća SystemVerificationResult."""
    options = tuple(option_texts or ())
    try:
        equations = parse_system(question)
    except _Unsupported as e:
        code = UNSUPPORTED_NONLINEAR if "nelinear" in str(e) or "stepen" in str(e) \
            else UNSUPPORTED_SHAPE
        return SystemVerificationResult(
            status=STATUS_UNSUPPORTED, issue_codes=(code, SYSTEM_QUESTION_PARSE_FAILED),
            marked_option_index=correct_option_index)

    pairs = tuple(parse_ordered_pair(t) for t in options)
    if not options or any(p is None for p in pairs):
        return SystemVerificationResult(
            status=STATUS_UNSUPPORTED, issue_codes=(ORDERED_PAIR_PARSE_FAILED,),
            marked_option_index=correct_option_index,
            parsed_equations=equations, parsed_options=pairs)

    valid = tuple(i for i, p in enumerate(pairs)
                  if all(satisfies(eq, p) for eq in equations))

    issues = []
    if len(valid) == 0:
        issues.append(NO_CORRECT_OPTION)
    elif len(valid) > 1:
        issues.append(MULTIPLE_CORRECT_OPTIONS)
    elif valid[0] != correct_option_index:
        issues.append(MARKED_OPTION_MISMATCH)

    # expected_answer je DODATNA provjera, nikad izvor istine: koristi se samo
    # da uhvati kontradikciju s već utvrđenim rješenjem.
    if not issues:
        expected_pair = parse_ordered_pair(expected_answer)
        if expected_pair is not None and expected_pair != pairs[valid[0]]:
            issues.append(EXPECTED_ANSWER_MISMATCH)

    if issues:
        return SystemVerificationResult(
            status=STATUS_INVALID, issue_codes=tuple(issues), valid_option_indices=valid,
            marked_option_index=correct_option_index,
            parsed_equations=equations, parsed_options=pairs)

    return SystemVerificationResult(
        status=STATUS_VERIFIED, valid_option_indices=valid,
        marked_option_index=correct_option_index,
        parsed_equations=equations, parsed_options=pairs)
