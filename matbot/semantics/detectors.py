"""Višekratni parametarski detektori semantike lekcije.

JEDAN detektor po PORODICI, nikad po lekciji: `fraction_arithmetic` opslužuje
sve četiri lekcije direktnog računa s razlomcima, a razlikuje ih isključivo
parametrima ugovora (dozvoljene operacije, odnos imenilaca, zabranjene
direktive).

TRI ISHODA I NIŠTA IZMEĐU:
    PASS         — vidljiva glavna operacija je dokazano u skladu s ugovorom
    FAIL         — prekršaj je DOKAZAN nad vidljivim tekstom
    UNSUPPORTED  — ne može se dokazati ni jedno ni drugo

`UNSUPPORTED` NIJE dokaz ispravnosti; to je izričito „ne znam“ i nikad ne
odbija paket. Isti princip nose svi serverski validatori (mathcheck,
option_equivalence, imagecheck) — vidi CLAUDE.md.

GRANICA DOKAZA (zapisuje se u svaki rezultat): dokazuje se samo VIDLJIVA
brojevna aritmetika razlomaka unutar `$...$`. Tekstualni zadaci, simbolički
izrazi, slike i višeznačne formulacije se NE ocjenjuju.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType

from matbot.mathsegments import math_contents, tokenize_math

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_UNSUPPORTED = "unsupported"

# Kodovi nalaza — stabilni, idu u logove i u recenzentov ulaz.
CODE_OPERATION_MISMATCH = "semantic_operation_mismatch"
CODE_DENOMINATOR_MISMATCH = "semantic_denominator_relation_mismatch"
CODE_FORBIDDEN_MAIN_SKILL = "semantic_forbidden_main_skill"

PROOF_BOUNDARY = (
    "dokazuje se samo vidljiva brojevna aritmetika razlomaka unutar $...$; "
    "tekstualni zadaci, simbolički izrazi i višeznačne formulacije ostaju "
    "nedokazani (UNSUPPORTED), nikad odbijeni"
)


@dataclass(frozen=True)
class Detection:
    status: str
    code: str = ""
    reason: str = ""
    evidence: MappingProxyType = field(
        default_factory=lambda: MappingProxyType({}))
    boundary: str = PROOF_BOUNDARY

    @property
    def blocking(self) -> bool:
        return self.status == STATUS_FAIL


def _result(status, code="", reason="", **evidence):
    return Detection(status=status, code=code, reason=reason,
                     evidence=MappingProxyType(dict(sorted(evidence.items()))))


# ---------------------------------------------------------------------------
# TOKENIZACIJA VIDLJIVOG IZRAZA
# ---------------------------------------------------------------------------

_SPACING_RE = re.compile(r"\\(?:,|;|!|quad|qquad|left|right|;|:(?=\s)|\s)")
_TEXT_RE = re.compile(r"\\(?:text|mathrm|mbox)\s*\{[^{}]*\}")

# Operatori → klasa operacije. `:` je projektni zapis dijeljenja.
_OPERATORS = (
    (r"\\cdot", "multiply"), (r"\\times", "multiply"), (r"·", "multiply"),
    (r"\*", "multiply"),
    (r"\\div", "divide"), (r":", "divide"), (r"÷", "divide"),
    (r"\+", "add"), (r"−", "subtract"), (r"-", "subtract"),
)
_OPERATOR_RE = re.compile("|".join(f"(?P<op{index}>{pattern})"
                                   for index, (pattern, _cls) in enumerate(_OPERATORS)))
_OPERATOR_CLASS = {f"op{index}": cls for index, (_p, cls) in enumerate(_OPERATORS)}

_PLACEHOLDERS = ("\\square", "□", "?", "_")

_FRAC_RE = re.compile(r"\\d?frac\s*\{")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_SLASH_FRACTION_RE = re.compile(r"(?<![\w}])(\d+)\s*/\s*(\d+)(?![\w{])")
_VARIABLE_RE = re.compile(r"[A-Za-z]")

_TOKEN_FRACTION = "fraction"
_TOKEN_NUMBER = "number"
_TOKEN_PLACEHOLDER = "placeholder"
_TOKEN_VARIABLE = "variable"
_TOKEN_OPERATOR = "operator"
_TOKEN_EQUALS = "equals"


class _Unparseable(Exception):
    """Sadržaj izlazi iz granice dokaza ovog detektora."""


def _balanced_group(text, start):
    """Vrati (sadržaj, indeks_poslije) za `{...}` koji počinje na `start`."""
    if start >= len(text) or text[start] != "{":
        raise _Unparseable("očekivana vitičasta zagrada")
    depth, index = 0, start
    while index < len(text):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
        index += 1
    raise _Unparseable("nezatvorena vitičasta zagrada")


def _tokenize(content):
    """Vrati listu tokena ili baci _Unparseable.

    Namjerno usko: sve što nije prepoznato kao broj, razlomak, mjestodržač,
    varijabla, operator ili `=` obara parsiranje — bolje UNSUPPORTED nego
    pogrešan nalaz."""
    text = _TEXT_RE.sub(" ", content)
    text = _SPACING_RE.sub(" ", text)
    text = _SLASH_FRACTION_RE.sub(lambda m: "\\frac{%s}{%s}" % (m.group(1), m.group(2)),
                                  text)
    tokens, index = [], 0
    while index < len(text):
        char = text[index]
        if char.isspace() or char in "()[]$":
            index += 1
            continue
        if char == "=":
            tokens.append((_TOKEN_EQUALS, "="))
            index += 1
            continue
        frac = _FRAC_RE.match(text, index)
        if frac:
            numerator, after = _balanced_group(text, frac.end() - 1)
            denominator, after = _balanced_group(text, after)
            tokens.append((_TOKEN_FRACTION, (numerator.strip(), denominator.strip())))
            index = after
            continue
        placeholder = next((p for p in _PLACEHOLDERS if text.startswith(p, index)), None)
        if placeholder:
            tokens.append((_TOKEN_PLACEHOLDER, placeholder))
            index += len(placeholder)
            continue
        number = _NUMBER_RE.match(text, index)
        if number:
            tokens.append((_TOKEN_NUMBER, number.group(0)))
            index = number.end()
            continue
        operator = _OPERATOR_RE.match(text, index)
        if operator:
            group = operator.lastgroup
            tokens.append((_TOKEN_OPERATOR, _OPERATOR_CLASS[group]))
            index = operator.end()
            continue
        if text.startswith("\\", index):
            # Nepoznata komanda unutar izraza — izvan granice dokaza.
            raise _Unparseable("nepoznata komanda")
        if _VARIABLE_RE.match(char):
            tokens.append((_TOKEN_VARIABLE, char))
            index += 1
            continue
        raise _Unparseable(f"neprepoznat znak {char!r}")
    return tokens


def _is_sign(tokens, position):
    """`-` na početku ili odmah iza operatora/`=` je predznak, ne operacija."""
    if position == 0:
        return True
    kind, _value = tokens[position - 1]
    return kind in (_TOKEN_OPERATOR, _TOKEN_EQUALS)


def _numeric(value):
    return bool(value) and bool(_NUMBER_RE.fullmatch(value.strip()))


# ---------------------------------------------------------------------------
# ZABRANJENE DIREKTIVE — prepoznaju se SAMO kad glavna operacija nije vidljiva.
# Obrasci su preuzeti iz postojeće provjerene logike porodica zadataka
# (matbot/task_family_validation.py) i ovdje samo parametrizovani.
# ---------------------------------------------------------------------------

_DIRECTIVE_PATTERNS = {
    "expand_reduce": re.compile(
        r"\bpro[šs]iri(?:te)?\b|\bskrati(?:te)?\b|\bpro[šs]irivanj\w*\b"
        r"|\bskra[ćc]ivanj\w*\b", re.IGNORECASE),
    "common_denominator": re.compile(
        r"\bsvedi(?:te)?\b|\bsvo[đd]enj\w*\b"
        r"|zajedni[čc]k\w*\s+(?:imenilac|imenioc\w*|nazivnik\w*)", re.IGNORECASE),
    "solve_equation": re.compile(
        r"\brije[šs]i(?:te)?\s+(?:jedna[čc]in\w*|nejedna[čc]in\w*)"
        r"|\bjedna[čc]in\w*\b", re.IGNORECASE),
    "percent": re.compile(r"%|\bprocen\w*|\bpostot\w*", re.IGNORECASE),
}


def _prose_of(text):
    """Samo tekst IZVAN `$...$` — direktive su uvijek prozne."""
    return " ".join(content for kind, content in tokenize_math(text or "")
                    if kind == "text")


def _forbidden_directive(text, forbidden):
    prose = _prose_of(text)
    for name in forbidden:
        pattern = _DIRECTIVE_PATTERNS.get(name)
        if pattern is not None and pattern.search(prose):
            return name
    return ""


# ---------------------------------------------------------------------------
# DETEKTOR PORODICE: fraction_arithmetic
# ---------------------------------------------------------------------------

_ADDITIVE = frozenset({"add", "subtract"})
_MULTIPLICATIVE = frozenset({"multiply", "divide"})


def _detect_fraction_arithmetic(contract, text):
    parameters = contract.parameters
    allowed = frozenset(parameters.get("allowed_operations", ()))
    required_relation = parameters.get("denominator_relation", "any")
    forbidden = tuple(parameters.get("forbidden_directives", ()))

    segments = [content for content in math_contents(tokenize_math(text or ""))
                if content.strip()]
    if not segments:
        directive = _forbidden_directive(text, forbidden)
        if directive:
            return _result(STATUS_FAIL, CODE_FORBIDDEN_MAIN_SKILL,
                           f"glavna radnja je „{directive}“, a lekcija je traži "
                           "samo kao pomoćni korak",
                           directive=directive, visible_math=False)
        return _result(STATUS_UNSUPPORTED, reason="nema vidljivog matematičkog zapisa",
                       visible_math=False)

    operations, denominators, symbolic, variable_operand = set(), [], False, False
    parsed_any = False
    for content in segments:
        try:
            tokens = _tokenize(content)
        except _Unparseable:
            continue
        parsed_any = True
        for position, (kind, value) in enumerate(tokens):
            if kind == _TOKEN_OPERATOR:
                if value == "subtract" and _is_sign(tokens, position):
                    continue
                operations.add(value)
            elif kind == _TOKEN_FRACTION:
                numerator, denominator = value
                if not _numeric(numerator) or not _numeric(denominator):
                    symbolic = True
                else:
                    denominators.append(denominator)
            elif kind == _TOKEN_VARIABLE:
                variable_operand = True

    if not parsed_any:
        return _result(STATUS_UNSUPPORTED, reason="izraz se ne može sigurno isparsirati",
                       visible_math=True)
    if symbolic:
        return _result(STATUS_UNSUPPORTED, reason="simbolički razlomak — nije brojevna aritmetika",
                       symbolic=True)

    if variable_operand:
        # Nepoznata u vidljivom izrazu znači jednačinu, a ne direktan račun.
        if "solve_equation" in forbidden:
            return _result(STATUS_FAIL, CODE_FORBIDDEN_MAIN_SKILL,
                           "vidljivi izraz sadrži nepoznatu — to je jednačina, "
                           "a lekcija traži direktan račun",
                           directive="solve_equation", variable_operand=True)
        return _result(STATUS_UNSUPPORTED, reason="izraz sadrži nepoznatu",
                       variable_operand=True)

    if not operations:
        directive = _forbidden_directive(text, forbidden)
        if directive:
            return _result(STATUS_FAIL, CODE_FORBIDDEN_MAIN_SKILL,
                           f"glavna radnja je „{directive}“, a nijedna dozvoljena "
                           "operacija nije vidljiva",
                           directive=directive, operations=())
        return _result(STATUS_UNSUPPORTED,
                       reason="nijedna binarna operacija nije vidljiva",
                       operations=())

    if operations & _ADDITIVE and operations & _MULTIPLICATIVE:
        return _result(STATUS_UNSUPPORTED,
                       reason="izraz miješa sabiranje/oduzimanje s množenjem/dijeljenjem",
                       operations=tuple(sorted(operations)))
    if operations >= _MULTIPLICATIVE:
        return _result(STATUS_UNSUPPORTED,
                       reason="izraz sadrži i množenje i dijeljenje",
                       operations=tuple(sorted(operations)))

    if not operations <= allowed:
        return _result(STATUS_FAIL, CODE_OPERATION_MISMATCH,
                       "vidljiva operacija nije operacija ove lekcije",
                       operation=tuple(sorted(operations)),
                       allowed_operations=tuple(sorted(allowed)))

    if required_relation in ("equal", "unlike"):
        if len(denominators) < 2:
            return _result(STATUS_UNSUPPORTED,
                           reason="manje od dva brojevna imenioca — odnos se ne može dokazati",
                           operation=tuple(sorted(operations)),
                           denominators=tuple(denominators))
        actual = "equal" if len(set(denominators)) == 1 else "unlike"
        if actual != required_relation:
            return _result(STATUS_FAIL, CODE_DENOMINATOR_MISMATCH,
                           f"imenioci su „{actual}“, a lekcija traži „{required_relation}“",
                           operation=tuple(sorted(operations)),
                           denominators=tuple(denominators),
                           denominator_relation=actual,
                           required_denominator_relation=required_relation)

    return _result(STATUS_PASS,
                   reason="vidljiva glavna operacija odgovara ugovoru lekcije",
                   operation=tuple(sorted(operations)),
                   denominators=tuple(denominators),
                   denominator_relation=required_relation)


DETECTORS = {
    "fraction_arithmetic": _detect_fraction_arithmetic,
}


def detect(contract, text):
    """Pokreni detektor porodice. Nepoznat detektor NIKAD ne odbija paket."""
    if contract is None:
        return _result(STATUS_UNSUPPORTED, reason="lekcija nema semantički ugovor")
    handler = DETECTORS.get(contract.detector)
    if handler is None:
        return _result(STATUS_UNSUPPORTED,
                       reason=f"detektor '{contract.detector}' nije implementiran")
    return handler(contract, text)
