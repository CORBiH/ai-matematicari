"""Deterministička provjera SEMANTIČKE (ne samo tekstualne) jednakosti dvije
multiple-choice opcije (Defekt 4, živi produkcijski nalaz — lekcija
„Dijagonala kvadrata“, 8. razred):

  • "$8\\sqrt{2}\\,\\text{cm}$" i "$11,3\\,\\text{cm}$" predstavljaju ISTU
    vrijednost ($8\\sqrt{2}\\approx11,3137$, zaokruženo na jednu decimalu daje
    $11,3$) — dvije "različite" opcije, ista tačna vrijednost.
  • "$d=a\\sqrt{2}$" i "$d=\\sqrt{2}a$" su ALGEBARSKI identični (komutativno
    množenje u drugom poretku).

Nijedna postojeća provjera (mathsafe, mathcheck, FamilyContract) ne hvata ovo:
mathcheck poredi LANAC jednakosti UNUTAR jednog izraza, ne DVIJE odvojene
opcije jedna s drugom; tekstualno poređenje ne prepoznaje ni zaokruživanje ni
komutativnost.

DVA nezavisna, sigurna testa (OR — bilo koji dovoljan da se opcije proglase
ekvivalentnim):
  1. NUMERIČKA jednakost obje vrijednosti (ponovo koristi restricted-AST
     evaluator iz matbot/mathcheck.py — NIKAD eval()), s tolerancijom
     izvedenom iz decimalne preciznosti (isti princip kao mathcheck._tolerance).
  2. SIMBOLIČKA (komutativna) jednakost: izraz se parsira u ograničeno
     kanonsko stablo (broj/promjenljiva/+/-/*//^/\\sqrt) gdje se djeca
     komutativnih operacija (+, *) SORTIRAJU prije poređenja — pa
     "a\\sqrt{2}" i "\\sqrt{2}a" postaju STRUKTURNO identični.

Kad NIJEDAN test ne može sigurno dokazati jednakost, opcije se smatraju
RAZLIČITIM — modul NIKAD ne "pogađa" da su dvije opcije iste; nedokazivost
nije dokaz jednakosti (isti princip kao mathcheck.py: preskoči, ne pogađaj).
"""
import re

from matbot import mathcheck

_UNIT_STRIP_RE = re.compile(
    r"\\(?:text|mathrm|mathit)\s*\{[^{}]*\}\s*(?:\^\s*(?:\{[^{}]*\}|\w))?"
)
_SPACING_STRIP_RE = re.compile(r"\\,|\\;|\\!|\\quad|\\qquad|\\ |\\left|\\right")
_DISALLOWED_RE = re.compile(
    r"\\circ|\\degree|°"
    r"|\\%|%"
    r"|\\sum|\\int|\\lim"
    r"|\\begin|\\end"
    r"|\\sqrt\s*\["
    r"|\\log|\\ln|\\sin|\\cos|\\tan"
    r"|\.\.\.|\\dots|\\ldots"
    r"|[<>≤≥≠]|\\le|\\ge|\\ne|\\neq|\\leq|\\geq"
)
_NUM_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?")


class _Unknown(Exception):
    """Izraz se ne može sigurno kanonikalizovati/tokenizovati — NIJE dokaz
    nejednakosti, samo signal da se pouzdano ne zna."""


def _strip_math_delimiters(text):
    t = (text or "").strip()
    if len(t) >= 2 and t.startswith("$") and t.endswith("$"):
        return t[1:-1]
    return t


def _value_expression(expr):
    """Ako izraz ima oblik 'nešto=vrijednost' (npr. 'd=a\\sqrt2'), poredi se
    SAMO dio poslije POSLJEDNJEG '=' — to je stvarna formula/vrijednost koju
    opcija predlaže; lijeva strana (ime tražene veličine) je ista u sve četiri
    opcije i nije predmet poređenja."""
    idx = expr.rfind("=")
    return expr[idx + 1:].strip() if idx != -1 else expr.strip()


_UNIT_CAPTURE_RE = re.compile(
    r"\\(?:text|mathrm|mathit)\s*\{([^{}]*)\}\s*(\^\s*(?:\{[^{}]*\}|\w))?"
)


def _extract_units(expr):
    """Vrati normalizovan tuple mjernih jedinica (s eksponentom, npr. 'cm^2')
    prisutnih u izrazu — koristi se da "16 cm" i "16 cm^2" NIKAD ne budu
    proglašeni ekvivalentnim samo zato što je brojčani dio isti."""
    units = []
    for m in _UNIT_CAPTURE_RE.finditer(expr):
        unit = m.group(1).strip()
        exp = (m.group(2) or "").replace(" ", "").replace("{", "").replace("}", "")
        units.append(unit + exp)
    return tuple(units)


def _clean_expression(expr):
    cleaned = _UNIT_STRIP_RE.sub(" ", expr)
    cleaned = _SPACING_STRIP_RE.sub(" ", cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# 1) NUMERIČKA jednakost — ponovo koristi mathcheck restricted-AST evaluator
# ---------------------------------------------------------------------------

def _numeric_candidates(expr):
    cleaned = _clean_expression(expr)
    if _DISALLOWED_RE.search(cleaned):
        return None
    try:
        return mathcheck.evaluate_candidates(cleaned)
    except Exception:
        return None


def _numeric_tolerance(expr_a, expr_b, magnitude):
    places = mathcheck._decimal_places(expr_a, expr_b)
    if places:
        return 0.5 * (10 ** -places) * 1.1
    return max(1e-9 * abs(magnitude), 1e-9)


# ---------------------------------------------------------------------------
# 2) SIMBOLIČKA (komutativna) jednakost — uzak, siguran tokenizator/parser
#    (NIKAD eval/ast.parse nad proizvoljnim kodom — samo naš vlastiti,
#    ograničen rekurzivni spust nad whitelist gramatikom).
# ---------------------------------------------------------------------------

def _tokenize(expr):
    tokens = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if expr.startswith("\\sqrt", i):
            tokens.append(("SQRT", None)); i += 5; continue
        if expr.startswith("\\frac", i):
            tokens.append(("FRAC", None)); i += 5; continue
        if expr.startswith("\\cdot", i):
            tokens.append(("OP", "*")); i += 5; continue
        if expr.startswith("\\times", i):
            tokens.append(("OP", "*")); i += 6; continue
        if expr.startswith("\\pi", i):
            tokens.append(("PI", None)); i += 3; continue
        if ch == "\\":
            raise _Unknown("nepoznata LaTeX komanda")
        if ch == "(":
            tokens.append(("LP", ch)); i += 1; continue
        if ch == ")":
            tokens.append(("RP", ch)); i += 1; continue
        if ch == "{":
            tokens.append(("LB", ch)); i += 1; continue
        if ch == "}":
            tokens.append(("RB", ch)); i += 1; continue
        if ch in "+-*/^":
            tokens.append(("OP", ch)); i += 1; continue
        m = _NUM_TOKEN_RE.match(expr, i)
        if m:
            tokens.append(("NUM", m.group(0).replace(",", "."))); i = m.end(); continue
        if ch.isalpha():
            tokens.append(("VAR", ch)); i += 1; continue
        raise _Unknown(f"neočekivan znak {ch!r}")
    return tokens


_FACTOR_START_KINDS = ("NUM", "VAR", "LP", "LB", "SQRT", "FRAC", "PI")


class _TokenParser:
    """Rekurzivni spust nad tokenima -> kanonsko (hashable) ugniježdeno
    tuple-stablo. + i * su komutativni: njihova djeca se SORTIRAJU (po
    repr-u kanonskog podstabla) prije poređenja, čime "a\\sqrt2" i "\\sqrt2a"
    (oba: mul(var(a), sqrt(num(2)))) postaju STRUKTURNO identični bez obzira
    na originalni poredak pisanja. - i / NISU komutativni — ostaju uređeni
    (lijevo-desno) binarni čvorovi."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def _advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, kind):
        tok = self._advance()
        if tok[0] != kind:
            raise _Unknown(f"očekivano {kind}, dobijeno {tok}")
        return tok

    def parse_expr(self):
        terms = [self.parse_term()]
        signs = []
        while self._peek()[0] == "OP" and self._peek()[1] in ("+", "-"):
            signs.append(self._advance()[1])
            terms.append(self.parse_term())
        if not signs:
            return terms[0]
        canon = [terms[0]]
        for sign, t in zip(signs, terms[1:]):
            canon.append(t if sign == "+" else ("neg", t))
        return ("add", tuple(sorted(canon, key=repr)))

    def _starts_factor(self):
        return self._peek()[0] in _FACTOR_START_KINDS

    def parse_term(self):
        factors = [self.parse_signed_factor()]
        ops = []
        while True:
            kind, val = self._peek()
            if kind == "OP" and val in ("*", "/"):
                ops.append(self._advance()[1])
                factors.append(self.parse_signed_factor())
            elif self._starts_factor():
                ops.append("*")  # implicitno množenje (npr. "2a", "a\\sqrt2")
                factors.append(self.parse_signed_factor())
            else:
                break
        if not ops:
            return factors[0]
        result = factors[0]
        mul_group = [factors[0]]
        for op, f in zip(ops, factors[1:]):
            if op == "*":
                mul_group.append(f)
            else:  # "/" — nekomutativno, isprazni prikupljenu * grupu prvo
                result = mul_group[0] if len(mul_group) == 1 else (
                    "mul", tuple(sorted(mul_group, key=repr)))
                result = ("div", result, f)
                mul_group = [result]
        if len(mul_group) == 1:
            return mul_group[0]
        return ("mul", tuple(sorted(mul_group, key=repr)))

    def parse_signed_factor(self):
        negate = False
        while self._peek()[0] == "OP" and self._peek()[1] in ("+", "-"):
            if self._advance()[1] == "-":
                negate = not negate
        base = self.parse_power()
        return ("neg", base) if negate else base

    def parse_power(self):
        base = self.parse_atom()
        if self._peek()[0] == "OP" and self._peek()[1] == "^":
            self._advance()
            exp = self.parse_atom()
            return ("pow", base, exp)
        return base

    def parse_atom(self):
        kind, val = self._advance()
        if kind == "NUM":
            return ("num", float(val))
        if kind == "VAR":
            return ("var", val)
        if kind == "PI":
            return ("var", "PI")
        if kind in ("LP", "LB"):
            inner = self.parse_expr()
            self._expect("RP" if kind == "LP" else "RB")
            return inner
        if kind == "SQRT":
            return ("sqrt", self.parse_atom())
        if kind == "FRAC":
            num = self.parse_atom()
            den = self.parse_atom()
            return ("div", num, den)
        raise _Unknown(f"neočekivan token {kind!r}")


def canonicalize_expression(latex_expr):
    """Vrati kanonski oblik izraza, ili None ako se ne može sigurno
    kanonikalizovati (nepodržana/nejednoznačna konstrukcija) — None znači
    "nepoznato", NIKAD "dokazano različito"."""
    try:
        cleaned = _clean_expression(latex_expr or "")
        if not cleaned.strip() or _DISALLOWED_RE.search(cleaned):
            return None
        tokens = _tokenize(cleaned)
        if not tokens:
            return None
        parser = _TokenParser(tokens)
        result = parser.parse_expr()
        if parser.pos != len(tokens):
            return None  # ostatak neparsiran — ne riskiraj pogrešnu kanonikalizaciju
        return result
    except _Unknown:
        return None
    except (ZeroDivisionError, RecursionError, IndexError):
        return None


# ---------------------------------------------------------------------------
# JAVNI API
# ---------------------------------------------------------------------------

def options_are_equivalent(text_a, text_b):
    """Vrati True SAMO kad je SIGURNO DOKAZANO da dvije MC opcije predstavljaju
    istu vrijednost/izraz (numerički ili simbolički). Vraća False i kad su
    dokazano različite i kad se ne može dokazati — nikad se ne "pogađa"
    jednakost (isti princip nesigurnosti kao matbot/mathcheck.py)."""
    val_a = _value_expression(_strip_math_delimiters(text_a))
    val_b = _value_expression(_strip_math_delimiters(text_b))
    if not val_a or not val_b:
        return False

    units_a = _extract_units(val_a)
    units_b = _extract_units(val_b)
    if units_a and units_b and units_a != units_b:
        return False  # različite jedinice (npr. cm vs cm^2) — nikad "ista vrijednost"

    cands_a = _numeric_candidates(val_a)
    cands_b = _numeric_candidates(val_b)
    if cands_a is not None and cands_b is not None and cands_a and cands_b:
        magnitude = max(abs(v) for v in cands_a + cands_b) or 1.0
        tol = _numeric_tolerance(val_a, val_b, magnitude)
        return any(abs(a - b) <= tol for a in cands_a for b in cands_b)

    canon_a = canonicalize_expression(val_a)
    canon_b = canonicalize_expression(val_b)
    if canon_a is not None and canon_b is not None:
        return canon_a == canon_b
    return False


def find_equivalent_option_pairs(option_texts):
    """Vrati listu (i, j) indeksa parova opcija za koje je DOKAZANO da su
    semantički ekvivalentne (i < j). Prazna lista = sve četiri opcije su
    dokazano međusobno različite (ili se ne mogu dokazati različitim, što se
    OVDJE tretira kao razlika — vidi options_are_equivalent)."""
    pairs = []
    n = len(option_texts)
    for i in range(n):
        for j in range(i + 1, n):
            if options_are_equivalent(option_texts[i], option_texts[j]):
                pairs.append((i, j))
    return pairs
