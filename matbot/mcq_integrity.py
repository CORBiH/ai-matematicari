"""Narrow, deterministic integrity checks for parseable divisibility MCQs.

This is deliberately *not* a general theorem prover.  It applies only when a
question explicitly asks about divisibility and every option is a bare integer.
All other lessons and less structured choices keep their existing validation
path.  Keeping this boundary small makes an ``unsupported`` result safe: it
does not claim mathematical proof where the server cannot derive one.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Optional

from matbot.mathcheck import safe_numeric_value
from matbot.mathsegments import TEXT, math_contents, tokenize_math


SUPPORTED_DIVISORS = frozenset({2, 3, 4, 5, 6, 9, 10, 15, 25})

_DIVISIBILITY_WORD_RE = re.compile(r"\bdjeljiv\w*\b", re.IGNORECASE)
# ZATVOREN skup priloga koji smiju stajati IZMEĐU „djeljiv“ i liste djelilaca.
# ŽIVI GATE 93ad85c: recenzentova ispravka „…je djeljiv ISTOVREMENO sa 25 i sa
# 6?“ je za čovjeka jednoznačna, ali je parser tražio „sa N“ odmah iza riječi
# „djeljiv“, pa je vratio prazan skup i cio ISPRAVAN paket je odbijen kao
# `divisibility_condition_ambiguous`.
# Skup je namjerno zatvoren i sitan: ovaj orakl odlučuje KOJA je opcija tačna,
# pa svako šire tumačenje nosi rizik pogrešno izvedenog skupa djelilaca. Bilo
# koja druga riječ između i dalje znači „ne mogu dokazati“ → fail closed.
_DIVISOR_LIST_FILLER = r"(?:\s+(?:istovremeno|ujedno|i\s+istovremeno))?"
# „sa brojem 25“ — imenica „brojem“ neposredno iza prijedloga je jednoznačno
# ista tvrdnja kao „sa 25“ (Faza 4G: varijanta je padala kao nedokazan uslov).
_DIVISOR_PREPOSITION = r"(?:sa|s)(?:\s+brojem)?"
_DIVISOR_LIST_START_RE = re.compile(
    _DIVISOR_LIST_FILLER + r"\s+(?:i\s+)?" + _DIVISOR_PREPOSITION
    + r"\s*\$?\s*(\d+)\s*\$?",
    re.IGNORECASE,
)
# Nastavak liste priznaje ISTI zatvoreni skup kao početak (Faza 4G): veznici
# „i“/„te“, pojačivač „istovremeno“/„ujedno“ i „, ali i“ su konjunktivni i
# jednoznačni. „ili“ se čita ali ga `_condition_is_ambiguous` odbija kao
# disjunkciju; svaka NEIMENOVANA riječ i dalje prekida listu, pa nepročitan
# broj u istoj rečenici znači nedokazan uslov → fail closed.
_DIVISOR_LIST_CONTINUATION_RE = re.compile(
    r"(?:\s*,\s*(?:(?:ali\s+)?(?:i\s+)?(?:istovremeno\s+|ujedno\s+)?"
    r"(?:" + _DIVISOR_PREPOSITION + r")\s*)?"
    r"|\s*(?:i|ili|te)\s+(?:istovremeno\s+|ujedno\s+)?"
    r"(?:(?:" + _DIVISOR_PREPOSITION + r")\s*)?)"
    r"\$?\s*(\d+)\s*\$?",
    re.IGNORECASE,
)
_INTEGER_RE = re.compile(r"^-?\d+$")
_NEGATED_RE = re.compile(r"\b(?:nije|nisu|nije\s+li)\s+djeljiv", re.IGNORECASE)
_OR_RE = re.compile(r"\bili\b", re.IGNORECASE)
_ANSWER_NUMBER_RE = re.compile(r"(?<!\d)-?\d+(?!\d)")
_AFFIRMATIVE_RE = re.compile(r"\b(?:da|jeste|jest|ta[čc]no|djeljiv\w*)\b", re.IGNORECASE)
_NEGATIVE_RE = re.compile(r"\b(?:ne|nije|nisu|neta[čc]no)\b", re.IGNORECASE)
_OPTION_ORDINAL_PATTERNS = (
    (0, re.compile(r"\b(?:prva|prvi|first|1\.)\s+(?:opcija|option)\b", re.IGNORECASE)),
    (1, re.compile(r"\b(?:druga|drugi|second|2\.)\s+(?:opcija|option)\b", re.IGNORECASE)),
    (2, re.compile(r"\b(?:treća|treci|treći|third|3\.)\s+(?:opcija|option)\b", re.IGNORECASE)),
    (3, re.compile(r"\b(?:četvrta|cetvrta|četvrti|fourth|4\.)\s+(?:opcija|option)\b", re.IGNORECASE)),
)
_OPTION_LABEL_RE = re.compile(r"\b(?:opcija|option)\s*([a-d])\b", re.IGNORECASE)


@dataclass(frozen=True)
class DivisibilityMCQResult:
    """A server-derived fact about an intentionally narrow question shape."""

    applicable: bool
    valid: bool
    reason_code: str = ""
    divisors: tuple[int, ...] = ()
    option_values: tuple[int, ...] = ()
    correct_indices: tuple[int, ...] = ()

    @property
    def correct_index(self) -> Optional[int]:
        return self.correct_indices[0] if len(self.correct_indices) == 1 else None

    @property
    def correct_value(self) -> Optional[int]:
        index = self.correct_index
        return self.option_values[index] if index is not None else None


@dataclass(frozen=True)
class DivisibilityDifficultyProfile:
    """Measurable task complexity for a title that promises divisibility rules."""

    measurable: bool
    level: Optional[int] = None
    reason_code: str = ""
    divisors: tuple[int, ...] = ()


def _bare_integer(value: str) -> Optional[int]:
    text = (value or "").strip()
    # A number may be rendered in a single inline-math wrapper, but no prose,
    # units, arithmetic or labels are accepted by this deliberately small oracle.
    if text.startswith("$") and text.endswith("$") and text.count("$") == 2:
        text = text[1:-1].strip()
    if not _INTEGER_RE.fullmatch(text):
        return None
    try:
        return int(text)
    except ValueError:  # defensive; regex already establishes parseability
        return None


def _read_divisor_list(question: str) -> tuple[tuple[int, ...], str]:
    """(pročitani djelioci, ostatak ISTE rečenice iza pročitane liste)."""
    text = question or ""
    # A task can introduce the topic with ``pravila djeljivosti`` before its
    # actual predicate.  Select the occurrence followed by a divisibility
    # condition, rather than blindly using the first word-form match.
    for divisible in _DIVISIBILITY_WORD_RE.finditer(text):
        tail = text[divisible.end():]
        first = _DIVISOR_LIST_START_RE.match(tail)
        if first is None:
            continue

        # Read only syntactic members of the divisor list.  This supports both
        # coordinated ``sa 6 i sa 25`` and compact ``sa 2, 3 i 5`` forms, but
        # never treats the tested number or unrelated later numbers as rules.
        raw_values = [first.group(1)]
        position = first.end()
        while continuation := _DIVISOR_LIST_CONTINUATION_RE.match(tail, position):
            raw_values.append(continuation.group(1))
            position = continuation.end()

        found: list[int] = []
        for raw in raw_values:
            divisor = int(raw)
            if divisor not in SUPPORTED_DIVISORS:
                return (), ""
            if divisor not in found:
                found.append(divisor)
        return tuple(found), _clause_remainder(tail[position:])
    return (), ""


def _explicit_divisors(question: str) -> tuple[int, ...]:
    """Djelioci koje je parser uspio pročitati — BEZ tvrdnje da su svi.

    Za odluku o tačnoj opciji NIJE dovoljno; tamo se koristi
    `_divisor_condition`, koja uz listu vraća i dokaz da je uslov potpun."""
    return _read_divisor_list(question)[0]


# ŽIVI PRODUKCIJSKI NALAZ (ručni smoke, lekcija o pravilima djeljivosti u 6.
# razredu): objavljen je MCQ
# „…koji od sljedećih brojeva je djeljiv i sa 6 i sa 25?“ s opcijama 8, 6, 7, 9
# i označenom opcijom 6. Broj djeljiv i sa 6 i sa 25 djeljiv je sa NZS(6,25)=150,
# pa zadatak nije imao NIJEDAN tačan odgovor.
#
# UZROK: `_read_divisor_list` je PARCIJALAN parser. Kad nastavak liste ne
# odgovara nijednom priznatom obliku („…sa 6 i istovremeno sa 25“, „…sa 6 i sa
# brojem 25“, „…sa 25, a ni sa 4“), on vrati ono što je do tada pročitao. Oracle
# je taj KRNJI uslov uzimao kao istinu i onda AKTIVNO POTVRĐIVAO da je 6 jedini
# tačan odgovor — dakle nije samo propustio grešku, nego ju je proizveo. To je
# gore od preskakanja: guard koji uslov ne može dokazati ne smije ga izmisliti.
#
# GRANICA, NE SLABLJENJE: kad iza pročitane liste u ISTOJ rečenici ostane cifra,
# uslov nije DOKAZANO potpun i oracle vraća `divisibility_condition_ambiguous` —
# postojeći kod koji preflight već šalje recenzentu (pa ga on smije preformulisati
# u istom drugom pozivu) i koji objavu odbija zatvoreno. Podržani oblici
# („…djeljiv sa 25?“, „…i sa 6 i sa 25?“, „…sa 2, 3 i 5?“) nemaju nepročitan
# broj u istoj rečenici i ostaju bajt za bajt netaknuti.
#
# Rečenica je namjerna granica: „…djeljiv sa 6 i sa 25? Odaberi jedan od 4
# ponuđena.“ je potpun uslov, a broj iz sljedeće rečenice nije njegov dio.
_CLAUSE_END_RE = re.compile(r"[.?!;:]")
_DIGIT_RE = re.compile(r"\d")


def _clause_remainder(tail: str) -> str:
    end = _CLAUSE_END_RE.search(tail)
    return tail if end is None else tail[:end.start()]


def _divisor_condition(question: str) -> tuple[tuple[int, ...], bool]:
    """(djelioci, uslov je DOKAZANO potpun) — jedini izvor za odluku o tačnosti."""
    divisors, remainder = _read_divisor_list(question)
    return divisors, not _DIGIT_RE.search(remainder)


# ŽIVI RELEASE GATE (commit baef3fd, scenario `harder_level2`, lekcija o
# pravilima djeljivosti u 6. razredu):
#     „Nađi cifru $x$ tako da je broj $3x5$ djeljiv sa 9. Koja cifra $x$ to
#      zadovoljava?“
# Ovaj oracle stoji na jednoj pretpostavci: OPCIJE su brojevi čija se djeljivost
# tvrdi. Ovdje se djeljivost tvrdi za `3x5` — numerik s mjestodržačem — a opcije
# su kandidati za cifru `x`. Nad potpuno ispravnim paketom (tačna cifra je $1$)
# oracle je vratio `no_correct_option`, recenzent je tu lažnu primjedbu
# „popravio“ uvođenjem broja djeljivog sa 9 (`18`) i tako pokvario paket, pa je
# turn pao na `marked_option_math_mismatch`.
# Granica, ne slabljenje: kad se u pitanju pojavi numerik koji miješa cifre i
# slovo, server ne može izvesti istinu bez uvrštavanja — a ovaj modul namjerno
# ništa ne rješava. Tada mora PRESKOČITI, ne pogađati (CLAUDE.md: guard koji ne
# može dokazati mora skipovati). Podržani oblik („Koji od brojeva je djeljiv sa
# 25?“) nema takav token i ostaje netaknut.
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
_ALNUM_RUN_RE = re.compile(r"[A-Za-z0-9]+")


def _has_placeholder_numeral(question: str) -> bool:
    """True kad pitanje sadrži numerik s mjestodržačem (`3x5`, `47a`, `b24`)."""
    stripped = _LATEX_COMMAND_RE.sub(" ", question or "")
    return any(any(character.isdigit() for character in run)
               and any(character.isalpha() for character in run)
               for run in _ALNUM_RUN_RE.findall(stripped))


# CILJANI TALAS F4D (scenario D10) — ISTI kvar kao baef3fd, drugi mjestodržač:
#     „U broju 47? treba upisati jednu cifru … djeljiv sa 9. Koja cifra
#      nedostaje?“   opcije 6, 7, 2, 9 · označeno 7 · 477 = 9·53, dakle TAČNO
# Oracle je ipak vratio `marked_option_math_mismatch` jer je opcije shvatio kao
# brojeve čija se djeljivost tvrdi i „našao“ da je 9 djeljivo sa 9.
#
# Granica iz baef3fd hvatala je mjestodržač po NOTACIJI (slovo u numeriku), pa
# `47?`, `47_` i `47□` prolaze ispod nje. Notacija je pogrešan kriterij — model
# bira znak slobodno. Mjerodavan je OBLIK ZADATKA: kad se traži CIFRA, a opcije
# su kandidati za tu cifru (jednocifreni brojevi), pretpostavka oracla („opcije
# su brojevi čija se djeljivost tvrdi“) ne vrijedi.
#
# Ovo je ISKLJUČIVO proširenje PRESKAKANJA: oracle tada ne tvrdi ništa, pa ne
# može objaviti pogrešan paket. Ostali deterministički validatori i dalje rade.
_DIGIT_REQUEST_RE = re.compile(r"\bcifr\w*", re.IGNORECASE)


def _asks_which_digit_is_missing(question: str, values: Iterable[Optional[int]]) -> bool:
    """True kad pitanje traži cifru, a sve opcije su kandidati za tu cifru."""
    if not _DIGIT_REQUEST_RE.search(question or ""):
        return False
    candidates = list(values)
    return bool(candidates) and all(
        value is not None and 0 <= value <= 9 for value in candidates)


def _condition_is_ambiguous(question: str, divisors: tuple[int, ...]) -> bool:
    text = question or ""
    if not divisors or _NEGATED_RE.search(text):
        return True
    # "sa 2 ili sa 3" requires a disjunction policy.  The supported shape is
    # one divisor or an explicit conjunction of all named divisors only.
    return len(divisors) > 1 and bool(_OR_RE.search(text))


def evaluate_divisibility_mcq(question: str, option_texts: Iterable[str]) -> DivisibilityMCQResult:
    """Evaluate only an unambiguous integer-option divisibility MCQ.

    ``applicable=False`` means this is not the narrow supported shape.  An
    applicable-but-unparseable condition fails closed with
    ``divisibility_condition_ambiguous``; a parseable condition has exactly
    the requested reason codes for zero or multiple mathematically true options.
    """
    options = tuple(option_texts or ())
    if not _DIVISIBILITY_WORD_RE.search(question or ""):
        return DivisibilityMCQResult(False, False)
    if _has_placeholder_numeral(question or ""):
        # Tvrdnja se odnosi na broj koji server ne vidi kao literal — opcije
        # nisu ono što se provjerava. Vidi komentar uz `_has_placeholder_numeral`.
        return DivisibilityMCQResult(False, False)
    values = tuple(_bare_integer(option) for option in options)
    if not options or any(value is None for value in values):
        # A direct yes/no question is validly handled by the existing pipeline;
        # this oracle makes no claim about prose options.
        return DivisibilityMCQResult(False, False)
    if _asks_which_digit_is_missing(question or "", values):
        # Opcije su kandidati za traženu cifru, ne brojevi čija se djeljivost
        # tvrdi. Vidi komentar uz `_asks_which_digit_is_missing`.
        return DivisibilityMCQResult(False, False)

    divisors, condition_complete = _divisor_condition(question)
    if not condition_complete or _condition_is_ambiguous(question, divisors):
        # Nedokazano potpuna lista je ISTO što i nečitljiv uslov: server ne zna
        # koje pravilo zadatak stvarno traži, pa ne smije proglasiti nijednu
        # opciju tačnom. Vidi komentar uz `_divisor_condition`.
        return DivisibilityMCQResult(
            True, False, "divisibility_condition_ambiguous", divisors,
            tuple(int(value) for value in values),
        )

    integer_values = tuple(int(value) for value in values)
    correct_indices = tuple(
        index for index, value in enumerate(integer_values)
        if all(value % divisor == 0 for divisor in divisors)
    )
    if not correct_indices:
        return DivisibilityMCQResult(
            True, False, "no_correct_option", divisors, integer_values, correct_indices,
        )
    if len(correct_indices) != 1:
        return DivisibilityMCQResult(
            True, False, "multiple_correct_options", divisors, integer_values, correct_indices,
        )
    return DivisibilityMCQResult(True, True, "", divisors, integer_values, correct_indices)


def expected_answer_matches_correct_option(expected_answer: str, result: DivisibilityMCQResult) -> bool:
    """Require the hidden expected answer to name the same visible integer.

    The task may contain a complete explanatory sentence, so equality would be
    too strict.  It must nevertheless mention the unique correct visible value
    and must not name another answer option as its asserted value.
    """
    if not result.applicable or not result.valid or result.correct_value is None:
        return True
    numbers = {int(value) for value in _ANSWER_NUMBER_RE.findall(expected_answer or "")}
    if result.correct_value not in numbers:
        return False
    alternatives = set(result.option_values) - {result.correct_value}
    return not bool(numbers & alternatives)


# ---------------------------------------------------------------------------
# USKI ORAKL DIREKTNOG RAČUNA (Faza 4G, Workstream E — lekcije o razlomcima)
# ---------------------------------------------------------------------------
# Produkcijski nalaz koji je otvorio cio program bio je MCQ bez ijedne tačne
# opcije (djeljivost). Za lekcije direktnog računa s razlomcima istu
# klasu je dosad držao SAMO recenzent: nijedan deterministički validator nije
# poredio VRIJEDNOST vidljivog izraza s ponuđenim opcijama, pa bi „Izračunaj
# $\frac{2}{7}+\frac{3}{7}$“ s pogrešno označenom opcijom prošao sve serverske
# kapije. Orakl je namjerno uzak (isti princip kao djeljivost):
#   • proza mora IZRIČITO tražiti račun (zatvoren skup direktiva) — inače bi
#     „Koji broj je suprotan broju $\frac{3}{4}$?“ bio lažno odbijen, jer
#     vrijednost tačne opcije tu NIJE vrijednost prikazanog izraza;
#   • TAČNO JEDAN segment je izračunljiv izraz s VIDLJIVIM operatorom — bez
#     operatora nema računa (lekcije ekvivalencije!), a dva kandidata znače
#     da server ne zna koji je zadatak;
#   • sve opcije moraju biti izračunljive vrijednosti; prozna opcija
#     isključuje cio orakl;
#   • računa ISKLJUČIVO postojeći restricted-AST evaluator (mathcheck) —
#     nikad eval(), nikad novi parser.
_COMPUTE_DIRECTIVE_RE = re.compile(
    r"\bizra[čc]unaj\w*\b|\bkoliko\s+je\b|\bkolika\s+je\s+vrijednost\b"
    r"|\bkoliko\s+iznosi\b|\bkoji\s+je\s+rezultat\b"
    r"|\bodredi\s+(?:vrijednost|rezultat)\b",
    re.IGNORECASE,
)
# Vidljiv binarni operator; `-` samo kad je DOKAZANO binaran (iza cifre/`}`).
_BINARY_OPERATOR_RE = re.compile(
    r"\\cdot|\\times|\\div|[+:·÷]|(?<=[\d}])\s*[-−]\s*(?=[\d\\(])")
# Dozvoljen zaostatak „= ?“ / „= \square“ na kraju izraza — mjesto rezultata.
_TRAILING_RESULT_RE = re.compile(r"=\s*(?:\?|\\square|□|_+)?\s*$")
_DECIMAL_PLACES_RE = re.compile(r"\d+[.,](\d+)")

DIVISION_BY_ZERO_CODE = "division_by_zero_in_task"


@dataclass(frozen=True)
class DirectComputationMCQResult:
    """Serverski izvedena činjenica o zadatku oblika „izračunaj izraz“."""

    applicable: bool
    valid: bool
    reason_code: str = ""
    computed_value: Optional[float] = None
    option_values: tuple = ()
    correct_indices: tuple = ()
    # Kompatibilnost s dijagnostikom koja za djeljivost čita `divisors`.
    divisors: tuple = ()

    @property
    def correct_index(self) -> Optional[int]:
        return self.correct_indices[0] if len(self.correct_indices) == 1 else None


def _option_numeric_value(option_text: str):
    text = (option_text or "").strip()
    if text.startswith("$") and text.endswith("$") and text.count("$") == 2:
        text = text[1:-1].strip()
    if not text:
        return "unsupported", None, ""
    status, value = safe_numeric_value(text)
    return status, value, text


def _option_tolerance(option_expr: str, value: float) -> float:
    """Decimalna opcija se poredi s tolerancijom SVOJE preciznosti (isti
    princip kao mathcheck._tolerance); egzaktan zapis egzaktno (šum float-a)."""
    places = max((len(match.group(1)) for match in
                  _DECIMAL_PLACES_RE.finditer(option_expr)), default=0)
    if places:
        return 0.5 * (10 ** -places) * 1.1
    return max(1e-9 * abs(value), 1e-12)


def evaluate_direct_computation_mcq(question: str,
                                    option_texts: Iterable[str]) -> DirectComputationMCQResult:
    """Ocijeni SAMO jednoznačan „izračunaj izraz“ MCQ; sve ostalo ćuti."""
    options = tuple(option_texts or ())
    prose = " ".join(content for kind, content in tokenize_math(question or "")
                     if kind == TEXT)
    if not options or not _COMPUTE_DIRECTIVE_RE.search(prose):
        return DirectComputationMCQResult(False, False)

    candidates = []  # (status, value) — samo segmenti s vidljivim operatorom
    for segment in math_contents(tokenize_math(question or "")):
        stripped = _TRAILING_RESULT_RE.sub("", segment).strip()
        if not stripped or not _BINARY_OPERATOR_RE.search(stripped):
            continue
        status, value = safe_numeric_value(stripped)
        if status == "unsupported":
            continue
        candidates.append((status, value))
    if len(candidates) != 1:
        return DirectComputationMCQResult(False, False)
    status, computed = candidates[0]
    if status == "invalid":
        # Pod direktivom računa dokazano nevaljana aritmetika (u praksi:
        # dijeljenje nulom) nema tačan odgovor — zadatak mora biti zamijenjen.
        return DirectComputationMCQResult(True, False, DIVISION_BY_ZERO_CODE)

    option_values = []
    for option in options:
        option_status, option_value, option_expr = _option_numeric_value(option)
        if option_status != "value":
            return DirectComputationMCQResult(False, False)
        option_values.append((option_value, option_expr))

    correct_indices = tuple(
        index for index, (value, expr) in enumerate(option_values)
        if abs(value - computed) <= _option_tolerance(expr, computed))
    values = tuple(value for value, _expr in option_values)
    if not correct_indices:
        return DirectComputationMCQResult(
            True, False, "no_correct_option", computed, values, correct_indices)
    if len(correct_indices) != 1:
        return DirectComputationMCQResult(
            True, False, "multiple_correct_options", computed, values, correct_indices)
    return DirectComputationMCQResult(True, True, "", computed, values, correct_indices)


# ---------------------------------------------------------------------------
# USKI ORAKL UPOREĐIVANJA (Faza 4G, Workstream F — upoređivanje racionalnih)
# ---------------------------------------------------------------------------
# Lekcija upoređivanja racionalnih brojeva nema semantički ugovor porodice i
# SAMU relaciju dosad nije provjeravao nijedan deterministički validator —
# „Koji znak stoji između $\frac{2}{3}$ i $\frac{3}{4}$?“ s pogrešno označenim
# znakom prolazio je sve serverske kapije. Dva dokaziva oblika:
#   • ZNAK: opcije su isključivo simboli <, >, = (svaki najviše jednom; svaki
#     drugi relacijski simbol, npr. ≥, diskvalifikuje cio orakl), pitanje ima
#     TAČNO DVA izračunljiva broja i zatvorenu proznu direktivu poređenja;
#   • SUPERLATIV: proza traži najveći ILI najmanji, sve opcije su izračunljive
#     vrijednosti — tačna je jedinstvena ekstremna vrijednost.
# „koliko“ bilo gdje u pitanju („Za koliko je veći…“) znači RAČUN razlike, ne
# relaciju — orakl se tada NIKAD ne angažuje.
_SIGN_QUESTION_RE = re.compile(
    r"koji\s+znak|upi[šs]i\s+znak|umetni\s+znak|stoji\s+izme[đd]u|\buporedi\w*\b"
    r"|\busporedi\w*\b",
    re.IGNORECASE,
)
_QUANTITY_BLOCKER_RE = re.compile(r"\bkoliko\b", re.IGNORECASE)
_SUPERLATIVE_MAX_RE = re.compile(r"\bnajve[ćc]\w*", re.IGNORECASE)
_SUPERLATIVE_MIN_RE = re.compile(r"\bnajmanj\w*", re.IGNORECASE)
_BASE_SIGNS = ("<", ">", "=")
_DISQUALIFYING_SIGNS = ("≤", "≥", "≠", "\\le", "\\ge", "\\ne", "\\leq", "\\geq", "\\neq")


@dataclass(frozen=True)
class ComparisonMCQResult:
    """Serverski izvedena relacija između dva vidljiva racionalna broja."""

    applicable: bool
    valid: bool
    reason_code: str = ""
    relation: str = ""            # "<" | ">" | "=" | "max" | "min"
    option_values: tuple = ()
    correct_indices: tuple = ()

    @property
    def correct_index(self) -> Optional[int]:
        return self.correct_indices[0] if len(self.correct_indices) == 1 else None


def _comparison_tolerance(*values: float) -> float:
    return max(1e-9 * max((abs(value) for value in values), default=1.0), 1e-12)


def _evaluate_sign_mcq(question, prose, options) -> ComparisonMCQResult:
    if not _SIGN_QUESTION_RE.search(prose):
        return ComparisonMCQResult(False, False)
    normalized = []
    for option in options:
        text = (option or "").strip()
        if text.startswith("$") and text.endswith("$") and text.count("$") == 2:
            text = text[1:-1]
        text = text.strip()
        if any(sign in text for sign in _DISQUALIFYING_SIGNS):
            return ComparisonMCQResult(False, False)
        normalized.append(text if text in _BASE_SIGNS else None)
    signs = [sign for sign in normalized if sign is not None]
    if not signs or len(signs) != len(set(signs)):
        return ComparisonMCQResult(False, False)

    values = []
    for segment in math_contents(tokenize_math(question or "")):
        if not segment.strip():
            continue
        status, value = safe_numeric_value(segment)
        if status != "value":
            return ComparisonMCQResult(False, False)
        values.append(value)
    if len(values) != 2:
        return ComparisonMCQResult(False, False)
    left, right = values
    if abs(left - right) <= _comparison_tolerance(left, right):
        relation = "="
    else:
        relation = "<" if left < right else ">"
    correct_indices = tuple(index for index, sign in enumerate(normalized)
                            if sign == relation)
    if not correct_indices:
        return ComparisonMCQResult(True, False, "no_correct_option", relation,
                                   tuple(values))
    return ComparisonMCQResult(True, True, "", relation, tuple(values),
                               correct_indices)


def _evaluate_superlative_mcq(prose, options) -> ComparisonMCQResult:
    wants_max = bool(_SUPERLATIVE_MAX_RE.search(prose))
    wants_min = bool(_SUPERLATIVE_MIN_RE.search(prose))
    if wants_max == wants_min:   # nijedan ili oba — nedokazivo
        return ComparisonMCQResult(False, False)
    values = []
    for option in options:
        status, value, _expr = _option_numeric_value(option)
        if status != "value":
            return ComparisonMCQResult(False, False)
        values.append(value)
    extreme = max(values) if wants_max else min(values)
    tolerance = _comparison_tolerance(*values)
    correct_indices = tuple(index for index, value in enumerate(values)
                            if abs(value - extreme) <= tolerance)
    relation = "max" if wants_max else "min"
    if len(correct_indices) != 1:
        return ComparisonMCQResult(True, False, "multiple_correct_options",
                                   relation, tuple(values), correct_indices)
    return ComparisonMCQResult(True, True, "", relation, tuple(values),
                               correct_indices)


def evaluate_comparison_mcq(question: str,
                            option_texts: Iterable[str]) -> ComparisonMCQResult:
    """Ocijeni SAMO jednoznačan MCQ poređenja; sve ostalo ćuti."""
    options = tuple(option_texts or ())
    if not options or _QUANTITY_BLOCKER_RE.search(question or ""):
        return ComparisonMCQResult(False, False)
    prose = " ".join(content for kind, content in tokenize_math(question or "")
                     if kind == TEXT)
    sign_result = _evaluate_sign_mcq(question, prose, options)
    if sign_result.applicable or _SIGN_QUESTION_RE.search(prose):
        return sign_result
    return _evaluate_superlative_mcq(prose, options)


def mathematical_publication_failure(question: str, option_texts: Iterable[str],
                                     marked_index: int) -> tuple[str, DivisibilityMCQResult]:
    """Return the server-provable MCQ math failure, before metadata checks."""
    result = evaluate_divisibility_mcq(question, option_texts)
    if not result.applicable:
        return "", result
    if not result.valid:
        return result.reason_code, result
    if marked_index != result.correct_index:
        return "marked_option_math_mismatch", result
    return "", result


def publication_failure(question: str, option_texts: Iterable[str], marked_index: int,
                        expected_answer: str) -> tuple[str, DivisibilityMCQResult]:
    """Return a narrow publication failure code, including expected-answer value checks."""
    failure, result = mathematical_publication_failure(question, option_texts, marked_index)
    if failure:
        return failure, result
    if not expected_answer_matches_correct_option(expected_answer, result):
        return "marked_option_math_mismatch", result
    if not result.applicable:
        # Faza 4G: kad oblik NIJE djeljivost, isti poziv pita i uske orakle
        # direktnog računa i poređenja. `expected_answer` se ovdje ne poredi
        # ponovo — jednakost s označenom opcijom već garantuju šema i preflight.
        computation = evaluate_direct_computation_mcq(question, option_texts)
        if computation.applicable:
            if not computation.valid:
                return computation.reason_code, computation
            if marked_index != computation.correct_index:
                return "marked_option_math_mismatch", computation
        else:
            comparison = evaluate_comparison_mcq(question, option_texts)
            if comparison.applicable:
                if not comparison.valid:
                    return comparison.reason_code, comparison
                if marked_index != comparison.correct_index:
                    return "marked_option_math_mismatch", comparison
    return "", result


def option_reference_failure(text: str, current_options: Iterable[dict],
                             correct_option_id: str) -> str:
    """Validate explicit option ordinals/labels against committed UI state.

    A bare correct value is intentionally allowed.  The gate activates only
    when prose explicitly claims an ordinal (``treća opcija``) or option label
    (``opcija B``), and compares that claim with the post-shuffle visible
    position and server-owned correct ID.  It therefore cannot trust a
    Tutor/Reviewer ordinal that was written before server shuffling.
    """
    options = tuple(current_options or ())
    ids = [str(option.get("id", "")) for option in options if isinstance(option, dict)]
    if len(ids) != len(options) or correct_option_id not in ids:
        return "expected_answer_option_reference_mismatch"
    correct_position = ids.index(correct_option_id)
    for position, pattern in _OPTION_ORDINAL_PATTERNS:
        if pattern.search(text or "") and position != correct_position:
            return "expected_answer_option_reference_mismatch"
    for match in _OPTION_LABEL_RE.finditer(text or ""):
        if match.group(1).lower() != correct_option_id.lower():
            return "expected_answer_option_reference_mismatch"

    # If prose both identifies an option and names one of the displayed bare
    # integer values, that value must be the value at the committed correct
    # option.  Other mathematical numbers (for example a divisor 25) are not
    # visible-option values and remain harmless.
    values = [_bare_integer(str(option.get("text", ""))) for option in options]
    correct_value = values[correct_position]
    mentioned_values = {int(value) for value in _ANSWER_NUMBER_RE.findall(text or "")}
    visible_alternatives = {value for value in values if value is not None and value != correct_value}
    if mentioned_values & visible_alternatives:
        return "expected_answer_option_reference_mismatch"
    return ""


def mathematical_fingerprint(result: DivisibilityMCQResult, task_family: str,
                             objective: str = "divisibility_selection") -> str:
    """A hash-only fingerprint for equivalent supported mathematical tasks."""
    if not result.applicable or not result.valid or result.correct_value is None:
        return ""
    payload = {
        "answer_form": "integer_mcq",
        "divisors": list(result.divisors),
        "family": task_family or "",
        "objective": objective,
        "option_values": sorted(result.option_values),
        "unique_correct_value": result.correct_value,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def difficulty_profile(question: str, option_texts: Iterable[str]) -> DivisibilityDifficultyProfile:
    """Derive the requested three-level profile from visible structure only."""
    text = question or ""
    divisors = _explicit_divisors(text)
    if not _DIVISIBILITY_WORD_RE.search(text) or _condition_is_ambiguous(text, divisors):
        return DivisibilityDifficultyProfile(False, reason_code="difficulty_direction_not_measurable")

    lowered = text.lower()
    construction = re.search(
        r"\b(?:dopuni|odredi\s+cifru|upiši\s+cifru|sastavi|formiraj|najmanj|najveć|najvec|"
        r"poredi|uporedi|usporedi)\b", lowered,
    )
    if construction or len(divisors) >= 3:
        return DivisibilityDifficultyProfile(True, 3, divisors=divisors)

    # A direct one-rule yes/no question *or direct selection among candidates*
    # is introductory.  Candidate comparison becomes standard only once it
    # combines conditions (or explicitly asks for a further justification).
    if len(divisors) == 1 and not re.search(r"\b(?:objasni|obrazloži|zašto|zasto)\b", lowered):
        return DivisibilityDifficultyProfile(True, 1, divisors=divisors)
    if len(divisors) >= 2:
        return DivisibilityDifficultyProfile(True, 2, divisors=divisors)
    if len(divisors) == 1:
        return DivisibilityDifficultyProfile(True, 2, divisors=divisors)
    return DivisibilityDifficultyProfile(False, reason_code="difficulty_direction_not_measurable")


def feedback_failure(question: str, current_options: Iterable[dict], correct_option_id: str,
                     response_text: str) -> str:
    """Reject only explicit stale/incorrect affirmative answer claims in feedback.

    Intermediate arithmetic remains allowed.  A first-wrong hint is also not
    forced to reveal the correct answer: this check fires only when the model
    positively calls an integer divisible/correct/selected answer.
    """
    options = tuple(current_options or ())
    texts = tuple(str(option.get("text", "")) for option in options)
    result = evaluate_divisibility_mcq(question, texts)
    if not result.applicable or not result.valid:
        return ""
    marked_index = next((i for i, option in enumerate(options)
                         if option.get("id") == correct_option_id), None)
    if marked_index != result.correct_index:
        return "marked_option_math_mismatch"

    correct_value = result.correct_value
    normalized = (response_text or "")
    patterns = (
        re.compile(r"(?<!\d)(-?\d+)(?!\d)\s+(?:je|jeste)\s+djeljiv\w*", re.IGNORECASE),
        re.compile(r"\bzato\s+je\s+(-?\d+)\s+djeljiv\w*", re.IGNORECASE),
        re.compile(
            r"(?:ta[čc]n\w*|ispravn\w*)\s+(?:odgovor|opcija|izbor)\s*(?:je|:)?\s*"
            r"(?:broj\s*)?(-?\d+)", re.IGNORECASE,
        ),
        re.compile(
            r"(?:izabrao\s+si|odabrao\s+si)\s+(?:broj\s*)?(-?\d+)\s+"
            r"(?:kao\s+)?(?:ta[čc]n\w*|ispravn\w*)", re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(normalized):
            if int(match.group(1)) != correct_value:
                return "stale_correct_option"
    return ""
