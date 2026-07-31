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
from typing import Optional, Tuple

# --- interni kodovi (NIKAD u browser) --------------------------------------
SYSTEM_QUESTION_PARSE_FAILED = "system_question_parse_failed"
ORDERED_PAIR_PARSE_FAILED = "ordered_pair_parse_failed"
NO_CORRECT_OPTION = "no_mathematically_correct_option"
MULTIPLE_CORRECT_OPTIONS = "multiple_mathematically_correct_options"
MARKED_OPTION_MISMATCH = "marked_correct_option_math_mismatch"
EXPECTED_ANSWER_MISMATCH = "expected_answer_math_mismatch"
UNSUPPORTED_NONLINEAR = "unsupported_nonlinear_system"
UNSUPPORTED_SHAPE = "unsupported_system_shape"

ALL_ISSUE_CODES = (
    SYSTEM_QUESTION_PARSE_FAILED, ORDERED_PAIR_PARSE_FAILED, NO_CORRECT_OPTION,
    MULTIPLE_CORRECT_OPTIONS, MARKED_OPTION_MISMATCH, EXPECTED_ANSWER_MISMATCH,
    UNSUPPORTED_NONLINEAR, UNSUPPORTED_SHAPE,
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
