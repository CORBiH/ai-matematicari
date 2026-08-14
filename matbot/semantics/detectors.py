"""Višekratni parametarski detektori semantike lekcije.

JEDAN detektor po PORODICI, nikad po lekciji: `fraction_arithmetic` opslužuje
sve četiri lekcije direktnog računa s razlomcima, a razlikuje ih isključivo
parametrima ugovora (dozvoljene operacije, odnos nazivnika, zabranjene
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

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
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
                           reason="manje od dva brojevna nazivnika — odnos se ne može dokazati",
                           operation=tuple(sorted(operations)),
                           denominators=tuple(denominators))
        actual = "equal" if len(set(denominators)) == 1 else "unlike"
        if actual != required_relation:
            return _result(STATUS_FAIL, CODE_DENOMINATOR_MISMATCH,
                           f"nazivnici su „{actual}“, a lekcija traži „{required_relation}“",
                           operation=tuple(sorted(operations)),
                           denominators=tuple(denominators),
                           denominator_relation=actual,
                           required_denominator_relation=required_relation)

    return _result(STATUS_PASS,
                   reason="vidljiva glavna operacija odgovara ugovoru lekcije",
                   operation=tuple(sorted(operations)),
                   denominators=tuple(denominators),
                   denominator_relation=required_relation)


# ---------------------------------------------------------------------------
# DETEKTOR PORODICE: polynomial_basic — STEPEN PROMJENLJIVE
# ---------------------------------------------------------------------------
# ŽIVI QA NALAZ (direktor škole, „Izrazi s promjenljivim i brojna vrijednost
# izraza“): traženjem sve težih zadataka lekcija je dolazila do
# izraza s $x^2$. Ugovor lekcije to već zabranjuje (`max_variable_degree: 1`,
# a njen `_scope_note` nosi dokaz: stepenovanje u 6. razredu ne postoji ni u
# jednoj NPP stavki i uvodi se tek u 8. razredu), i DETERMINISTIČKI generator
# ga poštuje — ali ta granica je bila SAMO savjetodavna: porodični detektor
# nije postojao, pa je `detect` vraćao UNSUPPORTED i model-put nije imao ništa
# što bi prekršaj oborilo. Ovdje se ta granica dokazuje.
#
# LEKCIJSKI, NE RAZREDNI OPSEG: mjeri se ISKLJUČIVO `max_variable_degree` iz
# ugovora TE lekcije. Lekcija bez tog parametra (npr. one čiji JESTE predmet
# stepenovanje) prolazi kroz ovaj detektor netaknuta — zabrana stepenovanja po
# razredu bila bi arhitektonski pogrešna.
#
# GRANICA DOKAZA: dokazuje se samo VIDLJIVA notacija stepena nad
# PROMJENLJIVOM unutar `$...$` — `x^2`, `x^{2}`, `x²`, `x**2` i izričit
# proizvod iste promjenljive sa samom sobom (`x \cdot x`). Brojevni stepen
# (`2^3`) i sve što se ne može dokazati ostaju UNSUPPORTED, nikad odbijeni.
CODE_VARIABLE_DEGREE_EXCEEDED = "semantic_variable_degree_exceeded"

# Baza mora biti SAMOSTALNO slovo: `cm^2` (jedinica) nije promjenljiva, pa
# lookbehind odbija slovo prije baze.
_VARIABLE_POWER_RE = re.compile(
    r"(?<![A-Za-zčćžšđČĆŽŠĐ\\])([A-Za-z])\s*(?:\^\s*\{?\s*(\d+)\s*\}?"
    r"|\*\*\s*(\d+)|([²³⁴⁵⁶⁷⁸⁹]))")
_SUPERSCRIPT_DIGITS = {"²": 2, "³": 3, "⁴": 4, "⁵": 5, "⁶": 6, "⁷": 7,
                       "⁸": 8, "⁹": 9}
# Ista promjenljiva pomnožena sama sobom je stepen bez oznake stepena.
_VARIABLE_SELF_PRODUCT_RE = re.compile(
    r"(?<![A-Za-zčćžšđČĆŽŠĐ\\])([A-Za-z])\s*(?:\\cdot|\\times|\*|·)\s*\1"
    r"(?![A-Za-zčćžšđČĆŽŠĐ])")


def _contract_max_variable_degree(contract):
    """Deklarisana granica stepena promjenljive, ili None kad je lekcija nema."""
    raw = contract.parameters.get("max_variable_degree")
    if raw in (None, ""):
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _detect_polynomial_basic(contract, text):
    limit = _contract_max_variable_degree(contract)
    if limit is None:
        return _result(STATUS_UNSUPPORTED,
                       reason="lekcija ne deklariše najveći stepen promjenljive")
    for content in math_contents(tokenize_math(text or "")):
        for match in _VARIABLE_POWER_RE.finditer(content):
            base = match.group(1)
            raw = match.group(2) or match.group(3)
            degree = int(raw) if raw else _SUPERSCRIPT_DIGITS[match.group(4)]
            if degree > limit:
                return _result(
                    STATUS_FAIL, CODE_VARIABLE_DEGREE_EXCEEDED,
                    f"promjenljiva „{base}“ je na stepen {degree}, a lekcija "
                    f"dozvoljava najviše {limit}",
                    variable=base, degree=degree, allowed_degree=limit,
                    segment=content)
        if limit < 2:
            product = _VARIABLE_SELF_PRODUCT_RE.search(content)
            if product:
                base = product.group(1)
                return _result(
                    STATUS_FAIL, CODE_VARIABLE_DEGREE_EXCEEDED,
                    f"promjenljiva „{base}“ je pomnožena sama sobom, što je "
                    f"stepen 2, a lekcija dozvoljava najviše {limit}",
                    variable=base, degree=2, allowed_degree=limit,
                    segment=content)
    return _result(STATUS_UNSUPPORTED,
                   reason="nije nađen dokaziv prekršaj stepena promjenljive",
                   allowed_degree=limit)


# ---------------------------------------------------------------------------
# PRIMITIV: DIMENZIJA TRAŽENE VELIČINE (dužina / površina / zapremina)
# ---------------------------------------------------------------------------
# ŽIVI NALAZ, DVA PUTA: „Mreža prizme“ je odgovarana FORMULOM ZAPREMINE (F5K,
# 150-turn real-state audit), a pravougaonik sa traženom POVRŠINOM odgovoren je
# obimom — `$P=26\,\text{cm}$` umjesto `cm^2` (D35-5, slikovni turn). Oba su
# ista klasa greške: zadatak ostaje matematički uredan, ali mjeri DRUGU
# veličinu nego što lekcija ispituje.
#
# To se ne mora nagađati iz proze. Eksponent mjerne jedinice u OZNAČENOM
# odgovoru je server-vlasnički, objektivan dokaz: `cm` je dužina, `cm^2`
# površina, `cm^3` zapremina. Vrsta zadatka koju ugovor deklariše (`kinds`)
# određuje koja je dimenzija dozvoljena.
#
# MAPA JE IZVEDENA MJERENJEM, NE RUČNO: `data/semantic_measure_dimensions.json`
# gradi `scripts/build_measure_dimensions.py` iz determinističkih generatora
# istih porodica — vrsta kod koje eksponent nije bio jedinstven kroz cio uzorak
# se NE upisuje i time se nikad ne blokira.
#
# GRANICA DOKAZA: dokazuje se samo kad označeni odgovor NOSI mjernu jedinicu i
# kad deklarisane vrste dopuštaju uži skup dimenzija od svih. Bez jedinice,
# uz vrstu bez jedinice (broj ivica, zbir uglova) ili uz vrste koje pokrivaju
# sve tri dimenzije — UNSUPPORTED, nikad odbijanje.
CODE_MEASURE_DIMENSION_MISMATCH = "semantic_measure_dimension_mismatch"

_DIMENSIONS_PATH = (Path(__file__).resolve().parent.parent.parent
                    / "data" / "semantic_measure_dimensions.json")

_DIMENSION_NAMES = {1: "dužina (cm)", 2: "površina (cm$^2$)",
                    3: "zapremina (cm$^3$)"}

# ZAPIS JEDINICE IMA VIŠE OBLIKA I SVI MORAJU BITI PROČITANI JEDNAKO:
#   `cm²` / `cm³`            — deterministički generator (Unicode)
#   `cm^2` / `cm^{2}`        — obični MathJax
#   `\text{cm}^2`            — jedinica kao tekst unutar matematike
#   `cm$^2$`                 — jedinica u prozi, eksponent u vlastitom `$...$`
# Posljednji oblik je mutacijski dokaz uhvatio kao PROPUST: bez tolerancije na
# `$` između jedinice i eksponenta, `cm$^2$` se čitalo kao dužina, pa bi
# površina prošla kao dužina — tiho lažno negativan nalaz.
_UNIT_RE = re.compile(
    r"(?:\\text\s*\{)?\s*(mm|cm|dm|km|m)\s*\}?\s*"
    r"(?:\$?\s*\^\s*\{?\s*([23])\s*\}?\s*\$?|([²³]))?")
_SUPERSCRIPT = {"²": 2, "³": 3}


@lru_cache(maxsize=1)
def _measure_dimensions():
    """(dimenzija_po_vrsti, vrste_bez_jedinice). Odsustvo artefakta =
    nijedna vrsta nije dokaziva — detektor tada samo vraća UNSUPPORTED."""
    try:
        payload = json.loads(_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, frozenset()
    by_kind = {str(k): int(v) for k, v in
               (payload.get("dimension_by_kind") or {}).items()}
    return by_kind, frozenset(payload.get("unitless_kinds") or ())


def unit_exponents(text):
    """Skup eksponenata mjernih jedinica; prazan skup = nema jedinice."""
    found = set()
    for match in _UNIT_RE.finditer(text or ""):
        if match.group(2):
            found.add(int(match.group(2)))
        elif match.group(3):
            found.add(_SUPERSCRIPT[match.group(3)])
        else:
            found.add(1)
    return found


def _detect_measure_dimension(contract, text, answer_text=""):
    by_kind, unitless = _measure_dimensions()
    kinds = tuple(contract.parameters.get("kinds") or ())
    if not kinds or not by_kind:
        return _result(STATUS_UNSUPPORTED,
                       reason="lekcija ne deklariše vrstu tražene veličine")
    if any(kind in unitless for kind in kinds):
        # Lekcija smije tražiti broj (ivice, dijagonale, zbir uglova) —
        # tada mjerna jedinica uopšte ne mora postojati.
        return _result(STATUS_UNSUPPORTED,
                       reason="lekcija dopušta i veličinu bez mjerne jedinice",
                       kinds=kinds)
    allowed = {by_kind.get(kind) for kind in kinds}
    if None in allowed:
        return _result(STATUS_UNSUPPORTED,
                       reason="bar jedna vrsta nema dokazanu dimenziju",
                       kinds=kinds)
    if allowed == {1, 2, 3}:
        return _result(STATUS_UNSUPPORTED,
                       reason="lekcija dopušta sve tri dimenzije — nema šta da se dokaže",
                       kinds=kinds)
    observed = unit_exponents(answer_text)
    if not observed:
        return _result(STATUS_UNSUPPORTED,
                       reason="označeni odgovor ne nosi mjernu jedinicu",
                       allowed_dimensions=tuple(sorted(allowed)))
    if len(observed) > 1:
        # Više različitih jedinica u jednom odgovoru — višeznačno, ne dokazuje se.
        return _result(STATUS_UNSUPPORTED,
                       reason="označeni odgovor nosi više različitih jedinica",
                       observed_dimensions=tuple(sorted(observed)),
                       allowed_dimensions=tuple(sorted(allowed)))
    actual = next(iter(observed))
    if actual in allowed:
        return _result(STATUS_PASS,
                       reason="dimenzija tražene veličine odgovara vrsti zadatka",
                       dimension=actual, allowed_dimensions=tuple(sorted(allowed)))
    wanted = ", ".join(_DIMENSION_NAMES.get(dim, str(dim))
                       for dim in sorted(allowed))
    return _result(
        STATUS_FAIL, CODE_MEASURE_DIMENSION_MISMATCH,
        f"označeni odgovor mjeri {_DIMENSION_NAMES.get(actual, actual)}, a ova "
        f"lekcija traži {wanted} — preformuliši zadatak tako da tražena "
        f"veličina bude {wanted}, ne {_DIMENSION_NAMES.get(actual, actual)}",
        dimension=actual, allowed_dimensions=tuple(sorted(allowed)), kinds=kinds)


# ---------------------------------------------------------------------------
# PRIMITIV: KLASA TRAŽENOG ODGOVORA (rezultat vs prepoznavanje)
# ---------------------------------------------------------------------------
# Lekcija „Vrste uglova“ traži da učenik ugao IMENUJE, a ne da mu izračuna
# mjeru; „Obrat Pitagorine teoreme“ traži ODLUKU je li trougao pravougli, a ne
# dužinu hipotenuze. Kad model na takvoj lekciji vrati broj, zadatak je
# matematički uredan a ispituje DRUGU vještinu — to je isti drift koji je F5K
# revizija našla kao „dokaz identiteta sveden na brojevni razlomak“.
#
# KLASU NE IZMIŠLJAMO I NE PARSIRAMO PONOVO: čita je `hint_policy.value_shaped`,
# već dokazan klasifikator kojim ljestvica pomoći razlikuje računski od
# tvrdnjskog zadatka (četiri nužna uslova, živo provjeren). Nema drugog parsera.
#
# MAPA JE IZVEDENA MJERENJEM: `data/semantic_answer_classes.json` gradi
# `scripts/build_answer_classes.py` nad determinističkim generatorima. Pojam
# kod kojeg klasa nije jednoglasna se NE upisuje (mjereno: 200 pojmova
# dokazano, 2 odbačena kao neodlučiva).
#
# ============================ MJEREN I ODBIJEN ============================
# OVAJ PRIMITIV NIJE ZAKAČEN U `DETECTORS` I NE SMIJE BITI, dok se ne pojavi
# dokaz kakvog danas nema. Replika je pokazala:
#
#     determinstički korpus : 21.120 paketa, 0 lažnih blokada
#     ŽIVI korpus (model)   :  3.287 paketa, 48 lažnih blokada (1,46 %)
#
# Svih 48 su ručno pregledane i sve su LAŽNE: sistem kao odgovor na lekciji o
# ekvivalentnim sistemima, odluka s obrazloženjem („Da, jer svaki činilac…“),
# simbolička tvrdnja („$A=N$“), i „Tačno“ na zadatku provjere.
#
# POUKA KOJA VRIJEDI ŠIRE OD OVOG PRIMITIVA: metoda izvođenja mape iz
# determinističkog generatora radi za MJERNU JEDINICU (dimenzija tražene
# veličine je fizičko svojstvo onoga što se pita), ali NE radi za KLASU
# ODGOVORA — oblik odgovora je autorski izbor, a model legitimno bira drukčiji
# nego generator. Determinstički korpus zato NIJE valjan zamjenik za model-
# autorski korpus kad se dokazuje svojstvo koje autor bira.
#
# Kod i podaci ostaju kao dokaz mjerenja i da bi se provjera mogla jeftino
# ponoviti kad se sakupi dovoljno živih paketa po porodici (danas: 13 uzoraka
# na 111 lekcija, 13 od 18 porodica bez ijednog). `tests/` čuva odluku.
# ==========================================================================
CODE_ANSWER_CLASS_MISMATCH = "semantic_answer_class_mismatch"

_ANSWER_CLASSES_PATH = (Path(__file__).resolve().parent.parent.parent
                        / "data" / "semantic_answer_classes.json")

# Polja ugovora koja imenuju VRSTU zadatka; ime polja se razlikuje po porodici.
_TOKEN_FIELDS = ("kinds", "concepts", "shapes", "problem_types")

_CLASS_LABELS = {
    "value": "REZULTAT (izračunata vrijednost)",
    "prose": "PREPOZNAVANJE (naziv, vrsta ili tvrdnja, bez računanja)",
}


@lru_cache(maxsize=1)
def _answer_classes():
    try:
        payload = json.loads(_ANSWER_CLASSES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in (payload.get("class_by_token") or {}).items()}


def declared_tokens(contract):
    tokens = []
    for field in _TOKEN_FIELDS:
        for item in contract.parameters.get(field) or ():
            tokens.append(str(item))
    return tuple(tokens)


def _detect_answer_class(contract, text, answer_text=""):
    by_token = _answer_classes()
    tokens = declared_tokens(contract)
    if not tokens or not by_token:
        return _result(STATUS_UNSUPPORTED,
                       reason="lekcija ne deklariše vrstu zadatka")
    required = {by_token.get(token) for token in tokens}
    if None in required:
        return _result(STATUS_UNSUPPORTED,
                       reason="bar jedan deklarisan pojam nema dokazanu klasu odgovora",
                       tokens=tokens)
    if len(required) != 1:
        return _result(STATUS_UNSUPPORTED,
                       reason="lekcija legitimno traži i prepoznavanje i račun",
                       tokens=tokens)
    wanted = next(iter(required))
    if not (answer_text or "").strip():
        return _result(STATUS_UNSUPPORTED, reason="nema označenog odgovora",
                       required_class=wanted)
    from matbot import hint_policy
    actual = "value" if hint_policy.value_shaped(answer_text) else "prose"
    if actual == wanted:
        return _result(STATUS_PASS,
                       reason="klasa odgovora odgovara vrsti zadatka",
                       required_class=wanted, answer_class=actual)
    return _result(
        STATUS_FAIL, CODE_ANSWER_CLASS_MISMATCH,
        f"ova lekcija traži odgovor tipa {_CLASS_LABELS[wanted]}, a označeni "
        f"odgovor je tipa {_CLASS_LABELS[actual]} — preformuliši zadatak i SVE "
        f"opcije tako da se od učenika traži "
        f"{'izračunata vrijednost' if wanted == 'value' else 'prepoznavanje/naziv, bez računanja'}",
        required_class=wanted, answer_class=actual, tokens=tokens)


DETECTORS = {
    "fraction_arithmetic": _detect_fraction_arithmetic,
    "polynomial_basic": _detect_polynomial_basic,
    "geometry_formula_2d": _detect_measure_dimension,
    "solid_geometry_direct": _detect_measure_dimension,
}

# Detektori kojima je dokaz OZNAČEN ODGOVOR, a ne tekst zadatka. Tekst nosi
# ULAZNE veličine (`a = 16` cm i u zadatku o površini), pa bi mjerenje nad njim
# bilo sistematski pogrešno.
_ANSWER_EVIDENCE_DETECTORS = frozenset({"geometry_formula_2d",
                                        "solid_geometry_direct"})


def detect(contract, text, answer_text=""):
    """Pokreni detektor porodice.

    `answer_text` je OZNAČENA opcija (server-vlasnička, poslije shuffle-a).
    Detektori koji je ne traže ponašaju se bajt za bajt kao ranije.

    Porodica bez vlastitog detektora i dalje vraća UNSUPPORTED. Primitiv klase
    odgovora (`_detect_answer_class`) NIJE zakačen ovdje — mjereno je odbijen,
    vidi njegov komentar."""
    if contract is None:
        return _result(STATUS_UNSUPPORTED, reason="lekcija nema semantički ugovor")
    handler = DETECTORS.get(contract.detector)
    if handler is None:
        return _result(STATUS_UNSUPPORTED,
                       reason=f"detektor '{contract.detector}' nije implementiran")
    if contract.detector in _ANSWER_EVIDENCE_DETECTORS:
        return handler(contract, text, answer_text)
    return handler(contract, text)
