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
import math
import re
from dataclasses import dataclass
from fractions import Fraction
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


def explicit_compound_divisor_request(message: str) -> bool:
    """True SAMO kad PORUKA UČENIKA sama deterministički traži složen uslov
    djeljivosti: čitljiva konjunktivna lista od najmanje dva podržana djelioca,
    dokazano potpuna, bez negacije i bez disjunkcije.

    ZAŠTO POSTOJI (živi F4G talas, G03/G05; ranije F4F F13–F15): server cilja
    svjež zadatak na nivo 1, a zadatak s DVA uslova djeljivosti je definiciono
    nivo 2 (`difficulty_profile` to već mjeri). Izričit zahtjev poput „djeljiv
    i sa 6 i sa 25“ je zato OBAVEZNO padao zatvoreno: recenzent je mogao ili
    vratiti `fail_closed` ili izdati jedno-pravilni zadatak koji ne odgovara
    zahtjevu. Ova funkcija daje pipeline-u deterministički osnov da za takav
    turn podigne cilj na nivo 2 — čita se ISKLJUČIVO učenikova poruka, istom
    zatvorenom gramatikom kojom se čita i tekst zadatka."""
    text = message or ""
    divisors, complete = _divisor_condition(text)
    if not complete or len(divisors) < 2:
        return False
    return not _condition_is_ambiguous(text, divisors)


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
# „NAJVEĆI zajednički djelilac“ / „NAJMANJI zajednički sadržilac“ imenuju
# FUNKCIJU (NZD/NZS), a ne ekstrem među ponuđenim opcijama — superlativ odmah
# ispred „zajednički“ zato diskvalifikuje cio superlativni orakl (kapacitetna
# ekspanzija: deterministički NZD paket s numeričkim opcijama bio bi inače
# dokazano POGREŠNO odbijen jer tačan NZD gotovo nikad nije najveća opcija).
# Batch #2: isto važi za „najveća DEKADSKA JEDINICA kojom se broj dijeli“ —
# superlativ imenuje USLOVLJEN objekat (najveću jedinicu koja dijeli broj),
# a ne najveću ponuđenu opciju.
_SUPERLATIVE_FUNCTION_RE = re.compile(
    r"\b(?:najve[ćc]\w*|najmanj\w*)\s+(?:zajedni[čc]k|dekadsk)", re.IGNORECASE)
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
    if _SUPERLATIVE_FUNCTION_RE.search(prose):
        # NZD/NZS pitanje — superlativ je dio imena funkcije, ne relacija.
        return ComparisonMCQResult(False, False)
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


# ---------------------------------------------------------------------------
# USKI ORAKL RJEŠAVANJA LINEARNE (NE)JEDNAČINE (PP-1 LIVE-150, nalaz F008)
# ---------------------------------------------------------------------------
# ŽIVI PRODUKCIJSKI NALAZ (PP1-F008, live talas od 150 scenarija): objavljen je MCQ
#     „Riješi nejednačinu: $-3< x+1 < -1$“
# s opcijama $x<-3$ / $x>-3$ / $x=-3$ / $x=-2$ i označenom opcijom $x=-3$.
# Skup rješenja je $-4<x<-2$; $x=-3$ je samo JEDAN član tog skupa, ne rješenje.
# Tutor je paket sastavio, recenzent ga je ODOBRIO, a nijedan deterministički
# sloj nije ni pokušao matematiku — orakli iznad pokrivaju djeljivost, direktan
# račun i poređenje, ali ne i „riješi (ne)jednačinu“.
#
# GRANICE (namjerno uske, isti princip kao svi orakli u modulu):
#   • proza mora IZRIČITO tražiti rješavanje („Riješi…“, „…rješenje…“) — bez
#     direktive orakl ćuti;
#   • superlativ („najveće rješenje“), „koliko“, negacija („koja NIJE…“) i
#     STVARNO ograničenje domena (dokaz suženja: „u skupu cijelih brojeva“,
#     „prirodni brojevi“, x ∈ ℤ…) isključuju cio orakl: tada tačan odgovor
#     NIJE nužno cio skup rješenja nad Q. Sama riječ „skup“ NIJE dokaz —
#     „skup rješenja“ je obična formulacija potpunog rješenja (vidi komentar
#     uz `_solve_domain_restricted`);
#   • SVAKI matematički segment pitanja mora biti pročitan zatvorenom
#     gramatikom ispod, a TAČNO JEDAN smije sadržavati relaciju — nepročitan
#     segment (npr. $x\in\mathbb{Z}$, $x^2>4$, $|x|<3$) znači ćutanje, nikad
#     pogađanje;
#   • podržana je isključivo LINEARNA relacija s jednom nepoznatom i egzaktno
#     čitljivim racionalnim konstantama (cio broj, decimalni zapis, \frac);
#     lančana nejednačina se rješava kao presjek dvaju linearnih uslova;
#   • opcije su relacije u ISTOJ nepoznatoj, gole vrijednosti, jednočlani
#     skupovi ({-1}) ili intervalni zapis ((-3,3), [2,+\infty)…); gola
#     vrijednost/jednočlan skup je KANDIDAT-TAČKA — cio skup rješenja samo
#     kod jednačine, a kod nejednačine dokazivo pogrešan;
#   • RAZDVOJENO JE „zadatak van dometa“ od „opcija koju ne znam pročitati“
#     (targeted live verifikacija, dva objavljena pogrešna paketa): kad je
#     zadatak DOKAZANO u dometu i skup rješenja izveden, nečitljiva opcija NE
#     gasi orakl nego pada zatvoreno (`unverifiable_solution_option`) — ćutanje
#     bi značilo „objavi bez ijedne matematičke provjere“. Mješovit broj
#     ($9\frac{4}{5}$ = 49/5) se sada čita EGZAKTNO po ugovoru uz
#     `_SOLVE_MIXED_NUMBER_RE`. Jedini namjerni izuzetak ćutanja na nivou
#     opcije: gola vrijednost/jednočlan skup na pitanju o ČLANSTVU;
#   • sva aritmetika je egzaktna (`Fraction`) — float nikad ne odlučuje istinu.
#
# Dvije relacije su „isti odgovor“ SAMO kad opisuju ISTI skup rješenja:
# tačka ≠ zrak ≠ interval, a $x\le 4$ ≠ $x<5$ i nad Q i pedagoški.
_SOLVE_DIRECTIVE_RE = re.compile(
    r"\brije[šs]i\w*\b|\brje[šs]enj\w*", re.IGNORECASE)
_SOLVE_NEGATION_RE = re.compile(r"\bnije\b|\bnisu\b", re.IGNORECASE)
# PITANJE O ČLANSTVU, ne o skupu rješenja („Koja vrijednost zadovoljava…“,
# „Koji broj je rješenje…“). Tu je gola brojčana opcija POTPUNO legitimna, pa
# se za takav tekst gola vrijednost NE tumači kao kandidat-skup. Blokira
# ISKLJUČIVO tu jednu mogućnost — opcije-relacije se i dalje presuđuju normalno,
# pa se nijedan već pokriven oblik ne gubi.
_SOLVE_MEMBERSHIP_RE = re.compile(
    r"\bzadovoljav\w*|\bpripada\w*"
    r"|\bkoj\w*\s+(?:je\s+)?(?:vrijednost|broj)\w*",
    re.IGNORECASE)
# ŽIVI FINALNI P0 TALAS (30 real-model scenarija; 8 FAIL iste klase: LSP0-A05…
# A08, B03, B04, B06, M05): raniji blokator domena hvatao je `\bskup\w*` i
# `\bcijel\w*` BILO GDJE u prozi, pa je i OBIČNA formulacija potpunog rješenja
# („skup rješenja“, „cijeli skup rješenja“, „tačan zapis skupa rješenja“…)
# gasila cio orakl (applicable=False) prije ijedne matematičke provjere. Još
# gore: TAČNI repliki ranije objavljenih pogrešnih paketa ({-5} uz interval
# -6<x<-4; {-1} uz interval -2<x<0) i dalje su prolazili bez ijednog
# determinističkog nalaza, jer su nosili „cijeli skup rješenja“. „Skup
# rješenja“ znači SKUP RJEŠENJA — ne suženje domena.
#
# Blokada je zato POZITIVNA: orakl se isključuje samo na stvaran dokaz suženja
# domena, nikad na riječ „skup“ samu:
#   • pridjev brojevnog skupa NEPOSREDNO uz imenicu koja domen stvarno nosi:
#     „cijelih BROJEVA“, „cijele VRIJEDNOSTI“, „cijelih RJEŠENJA“,
#     „racionalnih brojeva“… („cijeli SKUP rješenja“ NE — „skup“ je objekat
#     rješavanja, ne nosilac domena; „na realnoj OSI“ NE — osa nije domen);
#   • pridjevi koji su domen sami po sebi: prirodn-, cjelobrojn-,
#     (ne)negativn-, (ne)pozitivn- (ponašanje starog blokatora, zadržano);
#   • fraza „u skupu / nad skupom / iz skupa“ + pridjev brojevnog skupa;
#   • simbolika domena, isključivo u USKOM kontekstu (vidi
#     `_SOLVE_DOMAIN_SYMBOL_RE`).
# Domen zapisan unutar $…$ ($x\in\mathbb{Z}$) i dalje gasi orakl kroz postojeću
# kapiju nepročitanog segmenta — ovdje se čita isključivo proza.
_SOLVE_DOMAIN_NOUN = r"(?:broj\w*|vrijednost\w*|rje[šs]enj\w*)"
_SOLVE_DOMAIN_ADJECTIVE = (
    r"(?:prirodn|cijel|cjelobrojn|racionaln|iracionaln|realn|decimaln"
    r"|parn|neparn|prost|nenegativn|negativn|nepozitivn|pozitivn)")
_SOLVE_DOMAIN_WORD_RE = re.compile(
    r"\bprirodn\w*|\bcjelobrojn\w*|\b(?:ne)?negativn\w*|\b(?:ne)?pozitivn\w*"
    r"|\b" + _SOLVE_DOMAIN_ADJECTIVE + r"\w*\s+" + _SOLVE_DOMAIN_NOUN
    + r"|\b(?:u\s+skupu|nad\s+skupom|iz\s+skupa)\s+(?:svih\s+)?"
    + _SOLVE_DOMAIN_ADJECTIVE + r"\w*",
    re.IGNORECASE)
# NAMJERNO bez IGNORECASE: samo VELIKO N/Z/Q/R je oznaka brojevnog skupa, i to
# samo kao samostalan token u kontekstu pripadnosti ili prijedloga — „Riješi“
# ne smije postati domen R, ni „u Zenici“ domen Z (negativni lookahead).
_SOLVE_DOMAIN_LETTER = r"[NZQR](?![0-9A-Za-zčćžšđČĆŽŠĐ])"
_SOLVE_DOMAIN_SYMBOL_RE = re.compile(
    r"[ℕℤℚℝ]|\\mathbb\{\s*[NZQR]\s*\}"
    r"|(?:∈|\\in\b)\s*" + _SOLVE_DOMAIN_LETTER
    + r"|(?:\b[Uu]\s+|\b[Ss]kup(?:u|a|om)?\s+)" + _SOLVE_DOMAIN_LETTER)


# ---------------------------------------------------------------------------
# RAZRJEŠENJE DOMENA (LSP0/DISC talas, RC3): suženje domena više ne gasi orakl
# bezuslovno. Kad je domen DOKAZIVO tačno jedan od Q/R/Z/N/N0, orakl presuđuje
# pod tim domenom: Q/R zadržavaju kontinuiranu semantiku (nikad se ne
# diskretizuju), Z/N/N0 presijecaju egzaktno rješenje s cijelim brojevima
# (LSP0-E02: „(cijeli brojevi)“ + x=-1 i -2<x<0 su ISTI skup {-1} — objavljen
# je MCQ s dvije tačne opcije jer je orakl na domen ćutao). Svako suženje koje
# se ne može dokazati kao tačno jedan podržan domen (parni, prosti, pozitivni,
# protivrječni domeni, „prirodni … nula“ bez izričitog N0) i dalje znači
# ćutanje — nedokazivost nikad ne postaje presuda.
# ---------------------------------------------------------------------------

# N0 tokeni — ISTI uski kontekst kao golo slovo domena (vidi
# _SOLVE_DOMAIN_LETTER): „N0“/„N_0“ iza ∈/\in ili prijedloga/„skup“, plus
# jednoznačni simboli ℕ₀ / \mathbb{N}_0. Golo slovo N iza kog stoji cifra NE
# hvata _SOLVE_DOMAIN_LETTER (negativni lookahead), pa se ovi oblici čitaju
# ovdje — prije skeniranja običnih simbola.
_SOLVE_DOMAIN_N0_RE = re.compile(
    r"ℕ₀|ℕ_\{?0\}?|\\mathbb\{\s*N\s*\}_\{?0\}?"
    r"|(?:∈|\\in\b|\b[Uu]\s+|\b[Ss]kup(?:u|a|om)?\s+)\s*N_?0\b")

_SOLVE_DOMAIN_LETTER_MAP = {"N": "N", "Z": "Z", "Q": "Q", "R": "R",
                            "ℕ": "N", "ℤ": "Z", "ℚ": "Q", "ℝ": "R"}


def _classify_domain_adjective(matched_text: str) -> Optional[str]:
    """Podržan domen iz JEDNOG pogotka _SOLVE_DOMAIN_WORD_RE, ili None.

    Redoslijed je bitan: „iracionaln“ sadrži „racionaln“ kao podniz, pa se
    provjerava prije. Sve što nije N/Z/Q/R (parn, neparn, prost, decimaln,
    (ne)negativn, (ne)pozitivn…) vraća None — nepodržano suženje."""
    lowered = matched_text.lower()
    if "prirodn" in lowered:
        return "N"
    if "cjelobrojn" in lowered or "cijel" in lowered:
        return "Z"
    if "iracionaln" in lowered:
        return None
    if "racionaln" in lowered:
        return "Q"
    if "realn" in lowered:
        return "R"
    return None


def _domain_from_symbol_text(matched_text: str) -> Optional[str]:
    """Slovo domena iz JEDNOG pogotka _SOLVE_DOMAIN_SYMBOL_RE."""
    for token in ("ℕ", "ℤ", "ℚ", "ℝ"):
        if token in matched_text:
            return _SOLVE_DOMAIN_LETTER_MAP[token]
    blackboard = re.search(r"\\mathbb\{\s*([NZQR])\s*\}", matched_text)
    if blackboard:
        return blackboard.group(1)
    trailing = re.search(r"([NZQR])\s*$", matched_text.strip())
    return trailing.group(1) if trailing else None


def _resolve_solve_domain(prose: str) -> tuple:
    """(status, domen, deklarisane nepoznate) iz PROZE pitanja.

    status: "none" (nema suženja), "supported" (tačno jedan od Q/R/Z/N/N0),
    "unsupported" (suženje postoji ali se ne može dokazati kao tačno jedan
    podržan domen — orakl mora ćutati, kao i dosad)."""
    domains = set()
    declared_variables = set()
    unsupported = False
    # N0 tokeni PRIJE običnih simbola: „\mathbb{N}_0“ bi se inače pročitao i
    # kao N (simbol) i kao N0 → lažna protivrječnost.
    stripped = _SOLVE_DOMAIN_N0_RE.sub(" ", prose or "")
    if stripped != (prose or ""):
        domains.add("N0")
    for match in _SOLVE_DOMAIN_WORD_RE.finditer(stripped):
        domain = _classify_domain_adjective(match.group(0))
        if domain is None:
            unsupported = True
        else:
            domains.add(domain)
    for match in _SOLVE_DOMAIN_SYMBOL_RE.finditer(stripped):
        domain = _domain_from_symbol_text(match.group(0))
        if domain is None:
            unsupported = True
        else:
            domains.add(domain)
        # Slovo NEPOSREDNO ispred ∈/\in imenuje nepoznatu na koju se domen
        # odnosi („za x ∈ Z“) — pamti se i poredi s nepoznatom relacije.
        if match.group(0).lstrip().startswith(("∈", "\\in")):
            head = stripped[:match.start()].rstrip()
            if head and head[-1].isalpha() and (
                    len(head) == 1 or not head[-2].isalpha()):
                declared_variables.add(head[-1])
    if "N" in domains and re.search(r"\bnul\w*", stripped, re.IGNORECASE):
        # „prirodnih brojeva … nula/nulom“: možda N0, možda komentar o nuli —
        # bez izričitog N0 tokena se NE pogađa.
        unsupported = True
    if not domains and not unsupported:
        return "none", "", declared_variables
    if unsupported or len(domains) != 1:
        return "unsupported", "", declared_variables
    return "supported", next(iter(domains)), declared_variables


# Čista deklaracija domena kao ZASEBAN matematički segment ($x\in\mathbb{Z}$,
# $x ∈ Z$, $x\in\mathbb{N}_0$) — ranije je takav segment bio nečitljiv i gasio
# cio orakl; sada je domenska EVIDENCIJA. Sve ostalo s ∈ i dalje pada na
# postojećoj kapiji nepročitanog segmenta.
_SOLVE_SEGMENT_DOMAIN_RE = re.compile(
    r"^\s*(?:([A-Za-z])\s*)?(?:\\in\b|∈)\s*"
    r"(?:\\mathbb\{\s*([NZQR])\s*\}|([ℕℤℚℝ])|([NZQR]))\s*"
    r"(?:(_\{?0\}?|₀)\s*)?$")


def _segment_domain_declaration(segment: str) -> Optional[tuple]:
    """(nepoznata|None, domen) ili None kad segment NIJE čista deklaracija."""
    match = _SOLVE_SEGMENT_DOMAIN_RE.match(segment or "")
    if match is None:
        return None
    letter = match.group(2) or match.group(4) or ""
    if match.group(3):
        letter = _SOLVE_DOMAIN_LETTER_MAP[match.group(3)]
    if match.group(5):
        if letter != "N":
            return None            # Z_0 i sl. — nepoznata notacija, ne pogađa se
        letter = "N0"
    if letter not in _SOLVE_DOMAIN_LETTER_MAP and letter != "N0":
        return None
    return match.group(1), letter
_SOLVE_RELATION_TOKEN_RE = re.compile(r"<=|>=|<|>|=")
# \frac s egzaktno čitljivim argumentima (broj ili jedno slovo); „§“ je interni
# marker razlomačke crte — literal „/“ iz ulaza zadržava svoju dvosmislenost
# („3/4x“ se NE tumači), dok je \frac{3}{4}x jednoznačno (3/4)·x.
# DISC talas (B014): argument smije nositi i JEDAN već zamijenjen unutrašnji
# razlomak (`2§3`), pa se \frac{1}{\frac{2}{3}} čita iznutra prema vani —
# oba argumenta brojčana znače EGZAKTNO izračunatu vrijednost (Fraction),
# nikad tekstualno nadovezan `1§2§3` (dvosmislen za parser članova).
_SOLVE_FRAC_ARG = r"[+-]?(?:\d+(?:[.,]\d+)?(?:§[+-]?\d+(?:[.,]\d+)?)?|[A-Za-z])"
_SOLVE_FRAC_RE = re.compile(
    r"\\[dt]?frac\{(" + _SOLVE_FRAC_ARG + r")\}\{(" + _SOLVE_FRAC_ARG + r")\}")
# DISC talas (B012/B013): školski oblik „linearni brojnik / brojčani nazivnik“
# (\frac{2x+1}{3}) je dosad gasio orakl. Brojnik mora nositi slovo (čisto
# brojčani već pokriva _SOLVE_FRAC_RE), nazivnik je egzaktan broj ≠ 0;
# zamjena daje `(2x+1)§3` — grupu koju parser strane zna raspodijeliti.
# Nazivnik s promjenljivom se NE podržava (nelinearno) i dalje ćuti.
_SOLVE_LINEAR_FRAC_RE = re.compile(
    r"\\[dt]?frac\{([0-9A-Za-z+\-*.,§ ]+)\}"
    r"\{([+-]?\d+(?:[.,]\d+)?(?:§[+-]?\d+(?:[.,]\d+)?)?)\}")
_SOLVE_UNSUPPORTED_CHAR_RE = re.compile(r"[^0-9A-Za-z+\-*/=<>.,§()]")
_SOLVE_TERM_RE = re.compile(
    r"(?P<sign>[+-]?)"
    r"(?:(?P<num>\d+(?:[.,]\d+)?(?:§[+-]?\d+(?:[.,]\d+)?)?)(?:\*?(?P<var_after>[A-Za-z]))?"
    r"|(?P<var>[A-Za-z]))"
    r"(?:[/§](?P<den>[+-]?\d+(?:[.,]\d+)?))?")
_SOLVE_REVERSED_OP = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}
# MJEŠOVIT BROJ `K\frac{p}{q}` — EGZAKTNA vrijednost K + p/q (zatvaranje
# posljednje poznate rupe: ovaj zapis je ranije NAMJERNO ćutao, pa je „riješi“
# zadatak s mješovitim opcijama ostajao bez ijedne determinističke provjere).
# UGOVOR — zamjenjuje se isključivo dokazano jednoznačan školski oblik, tačno
# onaj koji `deterministic/core.py::fraction_display` stvarno emituje:
#   • cio dio K ≥ 1, nazivnik q ≥ 2, PRAVI razlomački dio 1 ≤ p < q;
#   • predznak NIJE dio zapisa: `-9\frac{4}{5}` znači -(9 + 4/5) = -49/5
#     (školska konvencija) i to garantuje postojeći parser članova, jer se
#     zamjena `9\frac{4}{5}` → `49§5` obavlja bez predznaka pa `-` ostaje
#     unarni/binarni znak CIJELOG člana — nikad -9 + 4/5;
#   • nepravi sufiks (9\frac{7}{5}), K=0, p=0, q<2, q=0, slovo/cifra odmah iza —
#     SVE ostaje nezamijenjeno i pada na postojećoj cifra-uz-\frac kapiji kao
#     nečitljivo (za opciju poznatog zadatka → fail closed, nikad tiho).
_SOLVE_MIXED_NUMBER_RE = re.compile(
    r"(?P<whole>\d+)\s*\\[dt]?frac\{(?P<num>\d+)\}\{(?P<den>\d+)\}")

# ŽIVA TARGETED VERIFIKACIJA (24 scenarija, 2 FAIL iste klase): objavljena su
# DVA pogrešna paketa u kojima je „riješi nejednačinu“ zadatak nosio opcije u
# zapisu jednočlanog skupa ({-5} za skup -6<x<-4; {-1} za skup -2<x<0). Parser
# opcija nije poznavao vitičaste zagrade, JEDNA nepročitljiva opcija je gasila
# CIO orakl (applicable=False), i deterministička zaštita je nestala baš na
# paketu koji ju je najviše trebao. Ovaj kod razdvaja te dvije stvari: zadatak
# u dometu + nečitljiva opcija = zatvoreno padanje, nikad tiho odustajanje.
UNVERIFIABLE_SOLUTION_OPTION_CODE = "unverifiable_solution_option"
# DISC talas (RC1): dva različita zapisa ISTOG skupa među pročitanim opcijama.
# NAMJERNO isti string kao publikacijski kod semantičkih duplikata
# (package_preflight.SEMANTIC_DUPLICATE_CODE) — recenzentski recept „zamijeni
# distraktor(e) da sve četiri opcije budu semantički različite“ već postoji.
EQUIVALENT_SOLUTION_OPTIONS_CODE = "semantically_duplicate_options"


@dataclass(frozen=True)
class _SolutionSet:
    """Kanonski zapis skupa rješenja; jednakost dataklasa = jednakost skupova.

    Konstrukcija ide isključivo kroz classmethod-e, pa nekorištena polja uvijek
    nose podrazumijevane vrijednosti i poređenje ostaje kanonsko.

    DISC/LSP0 talas (RC3): uz kontinuirane oblike (point/ray/interval) postoje
    i DISKRETNI kanonski oblici, izvedeni presjekom kontinuiranog rješenja s
    domenom Z/N/N0 (vidi `_discretize`):
      • "finite"    — konačan skup tačaka (sortirani, jedinstveni članovi);
      • "empty"     — prazan skup (npr. necjelobrojna tačka nad Z);
      • "int_ray"   — beskonačan diskretni zrak cijelih brojeva, granica
                      NORMALIZOVANA na uključivu (">=" ili "<=") — time
                      x>3 i x>=4 nad Z postaju ISTI kanonski zapis;
      • "int_range" — ograničen cjelobrojni raspon PREVELIK za materijalizaciju
                      (vidi _MAX_FINITE_SET_MEMBERS): granice se porede
                      simbolički, članovi se NIKAD ne alociraju iz modelovih
                      vrijednosti."""

    kind: str                      # "point"|"ray"|"interval"|"finite"|"empty"|"int_ray"|"int_range"
    op: str = ""                   # za ray/int_ray: "<" | "<=" | ">" | ">="
    value: Fraction = Fraction(0)  # tačka ili granica zraka
    lower: Fraction = Fraction(0)
    lower_included: bool = False
    upper: Fraction = Fraction(0)
    upper_included: bool = False
    members: tuple = ()            # samo za "finite"

    @classmethod
    def point(cls, value):
        return cls("point", value=value)

    @classmethod
    def ray(cls, op, bound):
        return cls("ray", op=op, value=bound)

    @classmethod
    def interval(cls, lower, lower_included, upper, upper_included):
        return cls("interval", lower=lower, lower_included=lower_included,
                   upper=upper, upper_included=upper_included)

    @classmethod
    def finite(cls, values):
        members = tuple(sorted(set(values)))
        if not members:
            return cls("empty")
        return cls("finite", members=members)

    @classmethod
    def empty_set(cls):
        return cls("empty")

    @classmethod
    def int_ray(cls, op, bound):
        return cls("int_ray", op=op, value=bound)

    @classmethod
    def int_range(cls, lower, upper):
        return cls("int_range", lower=lower, upper=upper)

    def display(self, variable: str) -> str:
        if self.kind == "point":
            return f"{variable} = {self.value}"
        if self.kind == "ray":
            return f"{variable} {self.op} {self.value}"
        if self.kind == "finite":
            return "{" + ", ".join(str(member) for member in self.members) + "}"
        if self.kind == "empty":
            return "∅"
        if self.kind == "int_ray":
            return f"{variable} {self.op} {self.value} [cijeli]"
        if self.kind == "int_range":
            return f"{{{self.lower}, …, {self.upper}}}"
        left = "<=" if self.lower_included else "<"
        right = "<=" if self.upper_included else "<"
        return f"{self.lower} {left} {variable} {right} {self.upper}"


# Stroga granica materijalizacije konačnog cjelobrojnog skupa: školski zadaci
# ne prelaze desetak članova, a modelove vrijednosti NIKAD ne smiju alocirati
# velik raspon (CLAUDE.md: MAX granice se ne šire bez razloga).
_MAX_FINITE_SET_MEMBERS = 24

# Donja granica diskretnog domena; None = bez donje granice (Z).
# KONVENCIJA PROJEKTA (ne mijenjati): N = {1,2,3,...}, N0 = {0,1,2,3,...}.
_SOLVE_DISCRETE_DOMAIN_MIN = {"Z": None, "N": 1, "N0": 0}


def _bounded_int_set(lower, upper) -> _SolutionSet:
    """Kanonski skup cijelih brojeva lower..upper (uključivo)."""
    if upper < lower:
        return _SolutionSet.empty_set()
    if upper - lower + 1 <= _MAX_FINITE_SET_MEMBERS:
        return _SolutionSet.finite(
            Fraction(value) for value in range(int(lower), int(upper) + 1))
    return _SolutionSet.int_range(Fraction(lower), Fraction(upper))


def _discretize(solution: _SolutionSet, domain: str) -> _SolutionSet:
    """Presjek kontinuiranog skupa rješenja s diskretnim domenom Z/N/N0.

    Sve granice se računaju egzaktno nad `Fraction` (floor/ceil su egzaktni
    nad racionalnim brojevima); rezultat je kanonski diskretni oblik, pa
    jednakost dataklasa ostaje jednakost SKUPOVA — x>3 i x>=4 nad Z su isti
    zapis, a -2<x<0 nad Z je tačno {-1} (LSP0-E02)."""
    minimum = _SOLVE_DISCRETE_DOMAIN_MIN[domain]
    if solution.kind == "point":
        value = solution.value
        if value.denominator != 1 or (minimum is not None and value < minimum):
            return _SolutionSet.empty_set()
        return _SolutionSet.finite((value,))
    if solution.kind == "ray":
        if solution.op in (">", ">="):
            low = (math.floor(solution.value) + 1 if solution.op == ">"
                   else math.ceil(solution.value))
            if minimum is not None:
                low = max(low, minimum)
            return _SolutionSet.int_ray(">=", Fraction(low))
        high = (math.ceil(solution.value) - 1 if solution.op == "<"
                else math.floor(solution.value))
        if minimum is None:
            return _SolutionSet.int_ray("<=", Fraction(high))
        return _bounded_int_set(minimum, high)
    if solution.kind == "interval":
        low = (math.ceil(solution.lower) if solution.lower_included
               else math.floor(solution.lower) + 1)
        high = (math.floor(solution.upper) if solution.upper_included
                else math.ceil(solution.upper) - 1)
        if minimum is not None:
            low = max(low, minimum)
        return _bounded_int_set(low, high)
    return solution                  # već diskretan kanonski zapis


@dataclass(frozen=True)
class LinearSolveMCQResult:
    """Serverski izveden skup rješenja podržane linearne (ne)jednačine."""

    applicable: bool
    valid: bool
    reason_code: str = ""
    solution_display: str = ""     # kanonski prikaz izvedenog skupa (dijagnostika)
    option_displays: tuple = ()    # kanonski prikazi pročitanih opcija
    correct_indices: tuple = ()
    # Kompatibilnost s dijagnostikom koja za djeljivost čita `divisors`.
    divisors: tuple = ()

    @property
    def correct_index(self) -> Optional[int]:
        return self.correct_indices[0] if len(self.correct_indices) == 1 else None


def _solve_literal(text: str) -> Fraction:
    """Egzaktna vrijednost literala; decimalni zapis ide kroz string (bez floata)."""
    if "§" in text:
        numerator, denominator = text.split("§", 1)
        bottom = _solve_literal(denominator)
        if bottom == 0:
            raise ZeroDivisionError(text)
        return _solve_literal(numerator) / bottom
    return Fraction(text.replace(",", "."))


def _replace_mixed_numbers(text: str) -> str:
    """Zamijeni svaki UGOVORNO valjan mješovit broj egzaktnim nepravim razlomkom.

    `9\\frac{4}{5}` → `49§5` (= (9·5+4)/5), bez ikakvog diranja predznaka.
    Oblik van ugovora (vidi `_SOLVE_MIXED_NUMBER_RE`) se NE zamjenjuje — ostaje
    cifra-uz-\\frac i pada na postojećoj kapiji u opštoj petlji ispod, pa
    malformiran zapis nikad ne postane pogođena vrijednost."""
    out = []
    position = 0
    for match in _SOLVE_MIXED_NUMBER_RE.finditer(text):
        if match.start() < position:
            continue
        whole = int(match.group("whole"))
        numerator = int(match.group("num"))
        denominator = int(match.group("den"))
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if (whole < 1 or denominator < 2 or not 1 <= numerator < denominator
                or before in (".", ",", "§") or after.isalnum()):
            continue                    # van ugovora — namjerno bez zamjene
        out.append(text[position:match.start()])
        out.append(f"{whole * denominator + numerator}§{denominator}")
        position = match.end()
    out.append(text[position:])
    return "".join(out)


def _normalize_solve_segment(segment: str) -> Optional[str]:
    """Zatvorena normalizacija LaTeX segmenta, ili None kad išta ostane nepročitano."""
    text = segment or ""
    for old, new in (("\\left", ""), ("\\right", ""),
                     ("\\displaystyle", " "), ("\\textstyle", " "),
                     ("\\leq", "<="), ("\\geq", ">="),
                     ("\\le", "<="), ("\\ge", ">="),
                     ("\\lt", "<"), ("\\gt", ">"),
                     ("\\cdot", "*"), ("\\times", "*"),
                     ("\\,", " "), ("\\;", " "), ("\\!", " "), ("\\ ", " ")):
        text = text.replace(old, new)
    text = (text.replace("·", "*").replace("−", "-")
            .replace("≤", "<=").replace("≥", ">="))
    # PRVO ugovorno valjani mješoviti brojevi (egzaktno, vidi helper), pa TEK
    # ONDA opšta \frac zamjena — svaka PREOSTALA cifra uz \frac je time
    # dokazano malformiran zapis i pada ispod.
    text = _replace_mixed_numbers(text)
    while match := _SOLVE_FRAC_RE.search(text):
        # Cifra ili zatvorena zagrada neposredno prije / cifra neposredno
        # poslije razlomka NAKON ugovorne zamjene mješovitih brojeva = zapis
        # koji se ne pogađa (nepravi sufiks 9\frac{7}{5}, K=0, p=0, q=0…):
        # prosta zamjena bi nadovezala cifre (94§5 = 94/5 ≠ 49/5).
        before = text[match.start() - 1] if match.start() > 0 else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if before.isdigit() or before == "}" or after.isdigit():
            return None
        numerator, denominator = match.group(1), match.group(2)
        if numerator[-1:].isalpha() or denominator[-1:].isalpha():
            # Slovo u argumentu: postojeće tekstualno ponašanje (x§2 i sl.).
            replacement = numerator + "§" + denominator
        else:
            # Oba argumenta brojčana (moguće s unutrašnjim §, DISC B014):
            # egzaktan Fraction račun, kanonski `p§q` — nikad `1§2§3`.
            try:
                bottom = _solve_literal(denominator)
                if bottom == 0:
                    return None
                value = _solve_literal(numerator) / bottom
            except (ValueError, ZeroDivisionError):
                return None
            replacement = (str(value.numerator) if value.denominator == 1
                           else f"{value.numerator}§{value.denominator}")
        text = text[:match.start()] + replacement + text[match.end():]
    # DISC talas (B012/B013): linearni brojnik / brojčani nazivnik. Zamjena u
    # grupu `(2x+1)§3` — parser strane je raspodjeljuje egzaktno. Brojnik bez
    # slova ovdje znači malformiran zapis (čisto brojčani je već morao biti
    # zamijenjen gore); cifra neposredno iza razlomka bi se slijepila s
    # nazivnikom, pa se ne pogađa.
    while match := _SOLVE_LINEAR_FRAC_RE.search(text):
        numerator = match.group(1)
        after = text[match.end()] if match.end() < len(text) else ""
        if not any(character.isalpha() for character in numerator):
            return None
        if after.isdigit() or (text[match.start() - 1:match.start()] == "}"):
            return None
        text = (text[:match.start()] + "(" + numerator + ")§" + match.group(2)
                + text[match.end():])
    if re.search(r"\d\s+\d", text):
        # „9 4/5“ (tekstualni mješovit broj) bi se brisanjem razmaka slijepio u
        # „94/5“ — pogođena vrijednost umjesto 49/5. Deterministički sadržaj
        # takav zapis ne emituje (uvijek \frac), pa se ne pogađa: nečitljivo.
        return None
    text = re.sub(r"\s+", "", text)
    text = text.replace(":", "/")
    if not text or _SOLVE_UNSUPPORTED_CHAR_RE.search(text):
        return None
    return text


# DISC talas (B007/B017): školska jednonivovska zagrada `k(ax+b)` — brojčani
# koeficijent (i eventualni brojčani nazivnik iza grupe: `(x+1)/2` odnosno
# `(x+1)§2` iz linearnog razlomka). Unutrašnjost NEMA zagrada (regex), pa
# gniježdenje, `(x+1)(x-2)` (grupa bez predznaka iza grupe) i `(x+1)^2`
# (`^` van dozvoljenog skupa znakova) i dalje dokazano ćute.
_SOLVE_GROUP_RE = re.compile(
    r"(?P<sign>[+-]?)"
    r"(?P<coef>\d+(?:[.,]\d+)?(?:§[+-]?\d+(?:[.,]\d+)?)?)?\*?"
    r"\((?P<inner>[^()]+)\)"
    r"(?:[/§](?P<den>[+-]?\d+(?:[.,]\d+)?(?:§[+-]?\d+(?:[.,]\d+)?)?))?")


def _solve_side_terms(text: str, variables: set,
                      allow_groups: bool = True) -> Optional[tuple]:
    """Jedna strana relacije kao (koeficijent uz nepoznatu, slobodni član)."""
    if not text:
        return None
    coeff = Fraction(0)
    constant = Fraction(0)
    position = 0
    first = True
    while position < len(text):
        group = _SOLVE_GROUP_RE.match(text, position) if allow_groups else None
        if group is not None:
            if not first and not group.group("sign"):
                # Grupa bez eksplicitnog +/- iza prethodnog člana: implicitno
                # množenje ((x+1)(x-2), x(x-1)) — nelinearno, ne tumači se.
                return None
            inner = group.group("inner")
            if _SOLVE_RELATION_TOKEN_RE.search(inner):
                return None
            if "," in inner and not any(ch.isalpha() for ch in inner):
                # `(5,2)` bez slova je nerazlučivo od (degenerisanog)
                # intervalnog zapisa — zarez se NE pogađa kao decimalni
                # (ista doktrina kao {2,5} kod jednočlanog skupa).
                return None
            inner_side = _solve_side_terms(inner, variables, allow_groups=False)
            if inner_side is None:
                return None
            try:
                factor = (_solve_literal(group.group("coef"))
                          if group.group("coef") else Fraction(1))
                denominator = (_solve_literal(group.group("den"))
                              if group.group("den") else None)
            except (ValueError, ZeroDivisionError):
                return None
            if group.group("sign") == "-":
                factor = -factor
            if denominator is not None:
                if denominator == 0:
                    return None
                factor /= denominator
            coeff += factor * inner_side[0]
            constant += factor * inner_side[1]
            position = group.end()
            first = False
            continue
        match = _SOLVE_TERM_RE.match(text, position)
        if match is None or match.end() == position:
            return None
        if not first and not match.group("sign"):
            # „3/4x“ i slično: bez eksplicitnog +/- između članova zapis je
            # dvosmislen i NE tumači se.
            return None
        try:
            base = (_solve_literal(match.group("num"))
                    if match.group("num") else Fraction(1))
            den = (_solve_literal(match.group("den"))
                   if match.group("den") else None)
        except (ValueError, ZeroDivisionError):
            return None
        if den == 0:
            return None
        value = -base if match.group("sign") == "-" else base
        if den is not None:
            value /= den
        letter = match.group("var_after") or match.group("var")
        if letter:
            variables.add(letter)
            coeff += value
        else:
            constant += value
        position = match.end()
        first = False
    return coeff, constant


def _solve_simple_relation(left, right, op) -> Optional[_SolutionSet]:
    """Riješi L op R po nepoznatoj; množenje negativnim obrće smjer."""
    slope = left[0] - right[0]
    if slope == 0:
        return None                      # nepoznata se skratila — ne dokazujemo
    bound = (right[1] - left[1]) / slope
    if op == "=":
        return _SolutionSet.point(bound)
    if slope < 0:
        op = _SOLVE_REVERSED_OP[op]
    return _SolutionSet.ray(op, bound)


def _solve_chain_relation(sides, ops) -> Optional[_SolutionSet]:
    """Lančana nejednačina = presjek dvaju linearnih uslova (dva zraka)."""
    first = _solve_simple_relation(sides[0], sides[1], ops[0])
    second = _solve_simple_relation(sides[1], sides[2], ops[1])
    if (first is None or second is None
            or first.kind != "ray" or second.kind != "ray"):
        return None
    lower = upper = None
    for ray in (first, second):
        if ray.op in ("<", "<="):
            if upper is not None:
                return None              # dvije gornje granice — nije interval
            upper = ray
        else:
            if lower is not None:
                return None
            lower = ray
    if lower is None or upper is None or lower.value >= upper.value:
        return None                      # prazan/degenerisan skup — ćutanje
    return _SolutionSet.interval(lower.value, lower.op == ">=",
                                 upper.value, upper.op == "<=")


def _solve_relation_text(text: str) -> Optional[tuple]:
    """(skup rješenja, nepoznata) za normalizovan relacijski zapis, ili None."""
    parts = []
    ops = []
    position = 0
    for match in _SOLVE_RELATION_TOKEN_RE.finditer(text):
        parts.append(text[position:match.start()])
        ops.append(match.group(0))
        position = match.end()
    parts.append(text[position:])
    if len(ops) not in (1, 2) or any(not part for part in parts):
        return None
    variables: set = set()
    sides = []
    for part in parts:
        side = _solve_side_terms(part, variables)
        if side is None:
            return None
        sides.append(side)
    if len(variables) != 1:
        return None
    if len(ops) == 1:
        solution = _solve_simple_relation(sides[0], sides[1], ops[0])
    else:
        solution = _solve_chain_relation(sides, ops)
    if solution is None:
        return None
    return solution, next(iter(variables))


def _solve_singleton_inner(text: str) -> Optional[str]:
    """Sadržaj JEDNOČLANOG skupa `{c}` / `\\{c\\}`, ili None kad zapis nije takav.

    ŽIVI NALAZ (targeted verifikacija): model tačku piše i kao skup — `{-1}`,
    `{ 5 }`, `\\{-5\\}`. Višečlani skup NIKAD nije tačka, pa zarez/tačka-zarez u
    sadržaju diskvalifikuje zapis: `{2,5}` je nerazlučiv od skupa {2, 5}
    (decimalni zarez se ovdje NAMJERNO žrtvuje — ne pogađa se)."""
    stripped = (text or "").strip()
    # Escaped vitičaste su ISTI zapis: \{-1\} == {-1}. `\frac{...}` nema
    # backslash neposredno ispred vitičaste, pa ga zamjena ne dira.
    stripped = stripped.replace("\\{", "{").replace("\\}", "}")
    if len(stripped) < 3 or not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    inner = stripped[1:-1].strip()
    if not inner or "," in inner or ";" in inner:
        return None
    return inner


def _solve_bare_point(text: str) -> Optional[_SolutionSet]:
    """Čista vrijednost (bez relacije i bez nepoznate) kao KANDIDAT-TAČKA."""
    normalized = _normalize_solve_segment(text)
    if normalized is None or _SOLVE_RELATION_TOKEN_RE.search(normalized):
        return None
    variables: set = set()
    side = _solve_side_terms(normalized, variables)
    if side is None or variables or side[0] != 0:
        return None
    return _SolutionSet.point(side[1])


# ŽIVI FINALNI P0 TALAS, druga posljedica: čim „skup rješenja“ više ne gasi
# orakl, u domet ulaze i živi paketi A05–A08/M05 i deterministička porodica
# `interval_solution` („…zapiši skup rješenja intervalom“) — svi nose opcije u
# intervalnom zapisu ((-3,3), [-11,-4), (9\frac{1}{2},\infty)…). Bez ovog
# čitanja bi DOKAZANO ISPRAVNI paketi padali kao `unverifiable_solution_option`.
# Zapis je ZATVOREN školski oblik i preslikava se na već postojeće kanonske
# skupove — nikakva nova aritmetika, granice čita ista gramatika kao gole
# vrijednosti (cio broj, decimala, \frac, ugovorni mješoviti broj):
#   • (a,b) / [a,b] / [a,b) / (a,b] uz a<b  → interval;
#   • (a,+\infty) / [a,+\infty)             → zrak > / >= a;
#   • (-\infty,b) / (-\infty,b]             → zrak < / <= b.
# SVE ostalo se NE pogađa i pada na postojećoj kapiji nečitljive opcije:
# uključena beskonačnost ([-∞,…), (-∞,∞), a>=b (prazan/degenerisan zapis),
# više od jednog zareza na vrhu zapisa (decimalni zarez u granici se time
# ŽRTVUJE — ista doktrina kao {2,5} kod jednočlanog skupa) i nečitljiv kraj.
_SOLVE_NEGATIVE_INFINITY = frozenset({"-\\infty", "−\\infty", "-∞", "−∞"})
_SOLVE_POSITIVE_INFINITY = frozenset({"+\\infty", "\\infty", "+∞", "∞"})


def _solve_interval_set(text: str) -> Optional[_SolutionSet]:
    """Intervalni zapis kao kanonski skup, ili None kad zapis nije dokazan."""
    stripped = (text or "").replace("\\left", "").replace("\\right", "")
    # DISC-B013: razmaknica `\,` u granici ($(-\infty,\,\frac{11}{3})$) nosi
    # ZAREZ koji bi presjekao listu na vrhu zapisa — razmaknice se skidaju
    # PRIJE brojanja zareza (nikad ne nose značenje).
    for spacing in ("\\,", "\\;", "\\!", "\\ "):
        stripped = stripped.replace(spacing, " ")
    stripped = stripped.strip()
    if len(stripped) < 3 or stripped[0] not in "([" or stripped[-1] not in ")]":
        return None
    # Zarez se broji samo na vrhu zapisa — `\frac{1,5}{2}` u granici ne smije
    # presjeći listu, ali ni postati pogođena vrijednost (padne na granici).
    parts = [""]
    depth = 0
    for character in stripped[1:-1]:
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(depth - 1, 0)
        if character == "," and depth == 0:
            parts.append("")
        else:
            parts[-1] += character
    if len(parts) != 2:
        return None
    left_text, right_text = parts
    left_compact = re.sub(r"\s+", "", left_text)
    right_compact = re.sub(r"\s+", "", right_text)
    lower_included = stripped[0] == "["
    upper_included = stripped[-1] == "]"
    left_infinite = left_compact in _SOLVE_NEGATIVE_INFINITY
    right_infinite = right_compact in _SOLVE_POSITIVE_INFINITY
    if left_infinite:
        if lower_included or right_infinite:
            # [-∞,… je malformiran zapis, a (-∞,∞) orakl nikad ne izvodi.
            return None
        bound = _solve_bare_point(right_text)
        if bound is None:
            return None
        return _SolutionSet.ray("<=" if upper_included else "<", bound.value)
    if right_infinite:
        if upper_included:
            return None
        bound = _solve_bare_point(left_text)
        if bound is None:
            return None
        return _SolutionSet.ray(">=" if lower_included else ">", bound.value)
    lower = _solve_bare_point(left_text)
    upper = _solve_bare_point(right_text)
    if lower is None or upper is None or lower.value >= upper.value:
        return None
    return _SolutionSet.interval(lower.value, lower_included,
                                 upper.value, upper_included)


# ---------------------------------------------------------------------------
# GRAMATIKA OPCIJA POD DOMENOM (DISC/LSP0 talas, RC3 + §7): prazan skup,
# set-builder i konačni cjelobrojni skupovi. Sve se tumači POD DOMENOM
# ZADATKA; nepodržan zapis na podržanom zadatku pada zatvoreno
# (`unverifiable_solution_option`), nikad tiho.
# ---------------------------------------------------------------------------

_SOLVE_EMPTY_SET_RE = re.compile(
    r"^\s*(?:∅|\\emptyset\b|\\varnothing\b|\{\s*\}|\\\{\s*\\\})\s*$")
# `x ∈ {…}` ispred zapisa skupa (LSP0-E02, opcija `x\in\{-2,-1\}`).
_SOLVE_MEMBER_PREFIX_RE = re.compile(r"^\s*([A-Za-z])\s*(?:\\in\b|∈)\s*")
_SOLVE_INT_MEMBER_RE = re.compile(r"[+-]?\d+")


def _strip_set_braces(text: str) -> Optional[str]:
    """Sadržaj vanjskog vitičastog para (`{…}` ili `\\{…\\}`), ili None."""
    stripped = (text or "").strip().replace("\\{", "{").replace("\\}", "}")
    if (len(stripped) >= 3 and stripped.startswith("{")
            and stripped.endswith("}")):
        inner = stripped[1:-1].strip()
        return inner or None
    return None


def _solve_finite_int_set(inner: str) -> Optional[_SolutionSet]:
    """Konačan skup CIJELIH literala `{-1,0,1}`, ili None.

    Članovi su isključivo cijeli brojevi: decimalni zarez u vitičastim
    zagradama ostaje žrtvovan (ista doktrina kao `_solve_singleton_inner` —
    `{2,5}` se pod kontinuiranim domenom nikad ne pogađa; pod cjelobrojnim
    domenom je jedino cjelobrojno čitanje i smisleno)."""
    members = []
    for part in re.split(r"[,;]", inner):
        part = part.strip()
        if not _SOLVE_INT_MEMBER_RE.fullmatch(part):
            return None
        members.append(Fraction(int(part)))
    return _SolutionSet.finite(members) if members else None


def _solve_set_builder(inner: str, variable: str) -> Optional[_SolutionSet]:
    """Set-builder `{x ∈ D : relacija}` / `{x∈D | relacija}` kao kanonski skup.

    DISC-A012: `(-2,\\infty)` i `{x ∈ Q : x>-2}` su ISTI skup rješenja nad Q —
    objavljen je MCQ s obje opcije tačne. Deklaracija lijevo koristi ISTU
    zatvorenu gramatiku kao segmentna deklaracija domena; relacija desno ISTU
    gramatiku kao opcija-relacija. Skup se tumači pod DEKLARISANIM domenom
    (diskretni se diskretizuje, Q/R ostaju kontinuirani) — poređenje je zatim
    poštena jednakost skupova brojeva, bez obzira na to kojim je domenom skup
    zapisan. Sve nedokazivo vraća None (fail closed na podržanom zadatku).

    ŽIVI FINAL-40 NALAZ: separator je do sada bio ISKLJUČIVO goli znak `:` ili
    `|`, a model školski set-builder u LaTeX-u piše komandom — `\\{x\\in\\mathbb{Q}
    \\mid x>-2\\}`. `\\mid` JESTE ta ista uspravna crta (i MathJax je tako
    renderuje), pa je oblik koji je ovaj čitač već trebao podržavati padao kao
    `unverifiable_solution_option`. Zamjena je zatvorena i doslovna: `\\mid` →
    `|`, plus skidanje LaTeX razmaknica koje nikad ne nose značenje. Nijedan
    drugi zapis se ne pogađa (`\\vert`, `\\Vert`, `\\colon` … i dalje ćute)."""
    inner = inner.replace("\\mid", "|")
    for spacing in ("\\,", "\\;", "\\!", "\\ "):
        inner = inner.replace(spacing, " ")
    separator_index = None
    depth = 0
    for index, character in enumerate(inner):
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(depth - 1, 0)
        elif character in ":|" and depth == 0:
            separator_index = index
            break
    if separator_index is None:
        return None
    declaration = _segment_domain_declaration(inner[:separator_index])
    if declaration is None:
        return None
    declared_variable, declared_domain = declaration
    if declared_variable is None or declared_variable != variable:
        return None
    normalized = _normalize_solve_segment(inner[separator_index + 1:])
    if normalized is None or not _SOLVE_RELATION_TOKEN_RE.search(normalized):
        return None
    solved = _solve_relation_text(normalized)
    if solved is None or solved[1] != variable:
        return None
    if declared_domain in _SOLVE_DISCRETE_DOMAIN_MIN:
        return _discretize(solved[0], declared_domain)
    return solved[0]


def _solve_option_set(option_text: str, variable: str,
                      allow_bare_value: bool,
                      domain: str = "") -> Optional[_SolutionSet]:
    text = (option_text or "").strip()
    if text.startswith("$") and text.endswith("$") and text.count("$") == 2:
        text = text[1:-1].strip()
    discrete = domain in _SOLVE_DISCRETE_DOMAIN_MIN
    if _SOLVE_EMPTY_SET_RE.match(text):
        # Prazan skup je PUNI zapis skupa: nad Z je npr. tačka 1/2 prazan
        # presjek, pa ∅ mora biti dokaziva tačna opcija — a nad kontinuiranim
        # rješenjem uvijek dokazivo pogrešna (čitljiva!) opcija.
        return _SolutionSet.empty_set()
    member_prefix = _SOLVE_MEMBER_PREFIX_RE.match(text)
    if member_prefix is not None:
        # `x ∈ {…}` — prefiks pripadnosti ispred zapisa skupa (LSP0-E02).
        remainder = text[member_prefix.end():].strip()
        if member_prefix.group(1) != variable:
            return None
        if _SOLVE_EMPTY_SET_RE.match(remainder):
            return _SolutionSet.empty_set()
        if _strip_set_braces(remainder) is None:
            return None            # `x ∈ 5` i sl. — nije zapis skupa
        text = remainder
    brace_inner = _strip_set_braces(text)
    if brace_inner is not None:
        if "\\in" in brace_inner or "∈" in brace_inner:
            return _solve_set_builder(brace_inner, variable)
        if "," in brace_inner or ";" in brace_inner:
            # Višečlan skup: pod DISKRETNIM domenom je legitiman zapis punog
            # rješenja; pod kontinuiranim se i dalje NE pogađa (decimalni
            # zarez) i pada zatvoreno na podržanom zadatku.
            if discrete:
                return _solve_finite_int_set(brace_inner)
            return None
        # Jednočlan skup je samo drugi ZAPIS vrijednosti — ista kapija kao gola
        # vrijednost: kod jednačine je to cio skup rješenja, kod nejednačine
        # dokazivo pogrešan kandidat (tačka ≠ zrak ≠ interval).
        if not allow_bare_value:
            return None
        point = _solve_bare_point(brace_inner)
        if point is None:
            return None
        return _discretize(point, domain) if discrete else point
    interval = _solve_interval_set(text)
    if interval is not None:
        # Intervalni zapis je PUNI zapis skupa (kao relacija) — presuđuje se
        # uvijek, nezavisno od `allow_bare_value`.
        return _discretize(interval, domain) if discrete else interval
    normalized = _normalize_solve_segment(text)
    if normalized is None:
        return None
    if not _SOLVE_RELATION_TOKEN_RE.search(normalized):
        # Gola vrijednost je KANDIDAT-TAČKA. Za jednačinu je to i cio skup
        # rješenja; za nejednačinu je to samo JEDAN član, pa poređenje s
        # izvedenim zrakom/intervalom dokazano pada — vidi `allow_bare_value`.
        if not allow_bare_value:
            return None
        point = _solve_bare_point(normalized)
        if point is None:
            return None
        return _discretize(point, domain) if discrete else point
    solved = _solve_relation_text(normalized)
    if solved is None:
        return None
    solution, option_variable = solved
    if option_variable != variable:
        return None
    return _discretize(solution, domain) if discrete else solution


def continuous_answer_set(option_text: str) -> Optional[tuple]:
    """(kanonski KONTINUIRANI skup, nepoznata|None) jedne MCQ opcije, ili None.

    JAVNA tačka za matbot/option_equivalence (DISC talas, RC1): dva zapisa s
    ISTIM kontinuiranim skupom su isti odgovor pod SVAKIM domenom — presjek
    istog skupa s bilo kojim domenom je isti skup — pa je ovo DOMEN-SLIJEPA,
    sigurna ekvivalencija (`11` ≡ `{11}` ≡ `x=11`; `(-2,\\infty)` ≡ `x>-2`).
    Domen-osjetljive jednakosti (x=-1 vs -2<x<0 nad Z; set-builder) ovdje
    NAMJERNO nisu dokazive — njih presuđuje evaluate_linear_solve_mcq, koji
    domen zadatka stvarno zna. None znači „nepoznato“, nikad „različito“."""
    text = (option_text or "").strip()
    if text.startswith("$") and text.endswith("$") and text.count("$") == 2:
        text = text[1:-1].strip()
    if _SOLVE_EMPTY_SET_RE.match(text):
        return _SolutionSet.empty_set(), None
    brace_inner = _strip_set_braces(text)
    if brace_inner is not None:
        if ("," in brace_inner or ";" in brace_inner
                or "\\in" in brace_inner or "∈" in brace_inner):
            return None
        point = _solve_bare_point(brace_inner)
        return (point, None) if point is not None else None
    interval = _solve_interval_set(text)
    if interval is not None:
        return interval, None
    normalized = _normalize_solve_segment(text)
    if normalized is None:
        return None
    if not _SOLVE_RELATION_TOKEN_RE.search(normalized):
        point = _solve_bare_point(normalized)
        return (point, None) if point is not None else None
    return _solve_relation_text(normalized)


# ---------------------------------------------------------------------------
# ČITANJE „RIJEŠI“ IZJAVE IZ SLOBODNOG TEKSTA (Task 3, DISC A009/A010/A020/A023)
# ---------------------------------------------------------------------------
# ZAŠTO POSTOJI: čuvar vjernosti zahtjevu (matbot/request_fidelity.py) mora
# uporediti ono što je UČENIK izričito tražio s onim što je zadatak STVARNO
# postavio. Da ne bi nastao DRUGI parser jednačina i domena, oba teksta čita
# ISTA zatvorena gramatika koju Task 1 već koristi za orakl rješavanja
# (`_normalize_solve_segment`, `_solve_relation_text`, `_resolve_solve_domain`,
# `_segment_domain_declaration`). Ova funkcija NE odlučuje o tačnosti i NE
# mijenja `evaluate_linear_solve_mcq` ni za bajt — ona samo ČITA izjavu.
#
# Razlika u odnosu na orakl je namjerna i ide u stranu OPREZA: orakl mora
# ćutati na svaki nepročitan segment (jer presuđuje tačnost), dok čitač izjave
# nepročitan segment preskače, ali proglašava izjavu NEJEDNOZNAČNOM čim vidi
# dvije RAZLIČITE relacije ili dva RAZLIČITA domena — a nejednoznačno nikad ne
# postaje nalaz.
#
# Učenikova poruka gotovo nikad ne nosi `$…$`: „riješi -2<x<2 u domenu Z“.
# Zato se relacija traži i u golom tekstu, ali SAMO kao NEPREKINUT niz znakova
# zatvorene abecede koji sadrži relacijski znak — proza se time ne može
# slijepiti u izraz, a svaki takav kandidat i dalje mora proći kroz istu
# gramatiku ili biti odbačen.
_REQUEST_BARE_RUN_RE = re.compile(r"[0-9A-Za-z+\-*/:=<>≤≥.,()\\{}^_]+")
_REQUEST_TRAILING_PUNCT = ".,;:!?"
# Definicija domena nabrajanjem (`N={1,2,3,...}`) — TAČAN oblik iz živog
# A020 zahtjeva. Prvi nabrojani član razlikuje N od N0, što je po konvenciji
# ovog projekta suštinska razlika (N počinje od 1, N0 od 0).
_REQUEST_DOMAIN_DEFINITION_RE = re.compile(
    r"\b([NZQR])\s*=\s*\\?\{\s*(-?\d+)\s*(?:,[^{}]*)?\\?\}")
# Goli simbol brojevnog skupa kao CIJELI matematički segment ($\mathbb{Z}$).
# Orakl takav segment vidi kao nepročitan i ćuti (ispravno za presudu); čitač
# izjave ga smije pročitati kao dokaz domena, jer ne presuđuje tačnost.
_REQUEST_BARE_DOMAIN_SEGMENT_RE = re.compile(
    r"^\s*(?:\\mathbb\s*\{\s*([NZQR])\s*\}|([ℕℤℚℝ]))\s*(_\{?0\}?|₀)?\s*$")
# Izričita riječ za vrstu zadatka. „NEjednačina“ sadrži „jednačina“ kao
# podniz, pa se negirani oblik MORA provjeriti prvi.
_REQUEST_INEQUALITY_WORD_RE = re.compile(
    r"\bnejedna[čc]\w*|\bnejednakost\w*", re.IGNORECASE)
_REQUEST_EQUATION_WORD_RE = re.compile(r"\bjedna[čc]in\w*", re.IGNORECASE)

RELATION_EQUATION = "equation"
RELATION_INEQUALITY = "inequality"


@dataclass(frozen=True)
class SolveStatement:
    """Šta jedan tekst DOKAZIVO tvrdi o „riješi“ zadatku.

    `domain_status`: "none" (nije spomenut), "supported" (tačno jedan od
    Q/R/Z/N/N0), "unsupported" (suženje postoji ali nije dokazivo jedan
    podržan domen — uključujući protivrječne domene).
    `solution` je KONTINUIRAN kanonski skup rješenja (prije diskretizacije)
    ili None kad relacija nije dokazivo pročitana; `relation_ambiguous` je
    True kad tekst nosi DVIJE različite relacije, pa se nijedna ne pripisuje."""

    domain: str = ""
    domain_status: str = "none"
    solution: object = None
    variable: str = ""
    relation_kind: str = ""        # "" | "equation" | "inequality"
    relation_ambiguous: bool = False
    stated_kind: str = ""          # vrsta izričito imenovana riječju

    @property
    def has_relation(self) -> bool:
        return self.solution is not None and not self.relation_ambiguous

    def solution_display(self) -> str:
        if self.solution is None:
            return ""
        return self.solution.display(self.variable or "x")


def _request_relation_candidates(text: str) -> list:
    """Nizovi znakova koji SMIJU biti relacija — iz $…$ i iz golog teksta."""
    candidates = []
    for kind, content in tokenize_math(text or ""):
        if kind != TEXT:
            candidates.append(content)
            continue
        for match in _REQUEST_BARE_RUN_RE.finditer(content):
            run = match.group(0).strip()
            while run and run[-1] in _REQUEST_TRAILING_PUNCT:
                run = run[:-1]
            # Rečenična zagrada oko izraza („(riješi x>3)“) nije dio izraza;
            # skida se SAMO kad je nesparena, pa `2(x-3)=x+5` ostaje netaknut.
            while run.endswith(")") and run.count(")") > run.count("("):
                run = run[:-1]
            while run.startswith("(") and run.count("(") > run.count(")"):
                run = run[1:]
            if run and _SOLVE_RELATION_TOKEN_RE.search(run):
                candidates.append(run)
    return candidates


def read_solve_statement(text: str) -> SolveStatement:
    """Pročitaj domen i relaciju iz slobodnog teksta zatvorenom gramatikom.

    Nikad ne pogađa: nepročitan kandidat se preskače, a dvije RAZLIČITE
    relacije/domena znače „nejednoznačno“ (nikad nalaz)."""
    raw = text or ""
    domains = set()
    unsupported = False

    # 1) DOMEN — ista prozna gramatika kao orakl, plus dva zapisa koja se u
    #    zahtjevima stvarno pojavljuju (nabrajanje i goli simbol segmenta).
    prose = " ".join(content for kind, content in tokenize_math(raw)
                     if kind == TEXT)
    status, domain, _declared = _resolve_solve_domain(prose)
    if status == "supported":
        domains.add(domain)
    elif status == "unsupported":
        unsupported = True
    for match in _REQUEST_DOMAIN_DEFINITION_RE.finditer(raw):
        letter, first_member = match.group(1), int(match.group(2))
        if letter != "N":
            domains.add(letter)
        elif first_member == 1:
            domains.add("N")
        elif first_member == 0:
            domains.add("N0")
        else:
            unsupported = True       # `N={2,3,...}` — ne pogađa se
    for _kind, content in tokenize_math(raw):
        if _kind == TEXT:
            continue
        declaration = _segment_domain_declaration(content)
        if declaration is not None:
            domains.add(declaration[1])
            continue
        bare = _REQUEST_BARE_DOMAIN_SEGMENT_RE.match(content)
        if bare is not None:
            letter = bare.group(1) or _SOLVE_DOMAIN_LETTER_MAP[bare.group(2)]
            if bare.group(3):
                if letter != "N":
                    unsupported = True
                    continue
                letter = "N0"
            domains.add(letter)
    if unsupported or len(domains) > 1:
        domain_status, resolved_domain = "unsupported", ""
    elif domains:
        domain_status, resolved_domain = "supported", next(iter(domains))
    else:
        domain_status, resolved_domain = "none", ""

    # 2) RELACIJA — svaki kandidat kroz ISTU gramatiku; različiti kanonski
    #    skupovi znače nejednoznačnost, isti skup zapisan dvaput ne znači.
    seen = []
    for candidate in _request_relation_candidates(raw):
        normalized = _normalize_solve_segment(candidate)
        if normalized is None or not _SOLVE_RELATION_TOKEN_RE.search(normalized):
            continue
        solved = _solve_relation_text(normalized)
        if solved is None:
            continue
        kind = (RELATION_EQUATION if _SOLVE_RELATION_TOKEN_RE.findall(normalized) == ["="]
                else RELATION_INEQUALITY)
        entry = (solved[0], solved[1], kind)
        if entry not in seen:
            seen.append(entry)
    if len(seen) == 1:
        solution, variable, relation_kind = seen[0]
        ambiguous = False
    else:
        solution, variable, relation_kind = None, "", ""
        ambiguous = len(seen) > 1

    # 3) IZRIČITO IMENOVANA VRSTA — „nejednačina“ se provjerava PRVA.
    if _REQUEST_INEQUALITY_WORD_RE.search(prose):
        stated_kind = RELATION_INEQUALITY
    elif _REQUEST_EQUATION_WORD_RE.search(prose):
        stated_kind = RELATION_EQUATION
    else:
        stated_kind = ""

    return SolveStatement(
        domain=resolved_domain, domain_status=domain_status,
        solution=solution, variable=variable, relation_kind=relation_kind,
        relation_ambiguous=ambiguous, stated_kind=stated_kind)


def solution_sets_match(first, second, domain: str = "") -> bool:
    """Isti skup rješenja: kontinuirano ili (uz dokazan diskretan domen) nakon
    presjeka s tim domenom. Preformulacija koja daje ISTI skup NIJE drift
    (globalni forenzički zaključak B003)."""
    if first is None or second is None:
        return False
    if first == second:
        return True
    if domain in _SOLVE_DISCRETE_DOMAIN_MIN:
        return _discretize(first, domain) == _discretize(second, domain)
    return False


def evaluate_linear_solve_mcq(question: str,
                              option_texts: Iterable[str]) -> LinearSolveMCQResult:
    """Ocijeni SAMO jednoznačan „riješi linearnu (ne)jednačinu“ MCQ; inače ćuti."""
    options = tuple(option_texts or ())
    if not options:
        return LinearSolveMCQResult(False, False)
    prose = " ".join(content for kind, content in tokenize_math(question or "")
                     if kind == TEXT)
    if not _SOLVE_DIRECTIVE_RE.search(prose):
        return LinearSolveMCQResult(False, False)
    if (_QUANTITY_BLOCKER_RE.search(prose) or _SOLVE_NEGATION_RE.search(prose)
            or _SUPERLATIVE_MAX_RE.search(prose)
            or _SUPERLATIVE_MIN_RE.search(prose)):
        # „najveće rješenje“, „koliko rješenja“, „koja NIJE rješenje“ mijenjaju
        # šta je tačan odgovor — tada cio skup nije mjerilo i orakl ćuti.
        return LinearSolveMCQResult(False, False)
    # DOMEN (LSP0/DISC talas, RC3): dokaziv Q/R/Z/N/N0 više ne gasi orakl —
    # presuđuje se POD domenom (Q/R kontinuirano, Z/N/N0 presjek s cijelim
    # brojevima). Svako drugo suženje (parni, prosti, pozitivni, protivrječno,
    # nedokazivo) i dalje znači ćutanje. Obična formulacija „skup rješenja“
    # NIJE domen (živi finalni P0 talas: 8 lažnih ćutanja).
    domain_status, domain, declared_variables = _resolve_solve_domain(prose)
    if domain_status == "unsupported":
        return LinearSolveMCQResult(False, False)

    candidates = []
    segment_domains = set()
    for segment in math_contents(tokenize_math(question or "")):
        declaration = _segment_domain_declaration(segment)
        if declaration is not None:
            # Čista deklaracija domena ($x\in\mathbb{Z}$) je EVIDENCIJA, ne
            # nečitljiv segment (ranije je gasila cio orakl).
            declared_variable, segment_domain = declaration
            if declared_variable is not None:
                declared_variables.add(declared_variable)
            segment_domains.add(segment_domain)
            continue
        normalized = _normalize_solve_segment(segment)
        if normalized is None:
            # Nepročitan segment ($x^2>4$, $|x|<3$…) može nositi uslov koji
            # mijenja rješenje — cio orakl tada ćuti.
            return LinearSolveMCQResult(False, False)
        if _SOLVE_RELATION_TOKEN_RE.search(normalized):
            candidates.append(normalized)
    if len(segment_domains) > 1:
        return LinearSolveMCQResult(False, False)
    if segment_domains:
        segment_domain = segment_domains.pop()
        if domain and segment_domain != domain:
            # Proza i segment tvrde RAZLIČITE domene — ne pogađa se.
            return LinearSolveMCQResult(False, False)
        domain = segment_domain
    if len(candidates) != 1:
        return LinearSolveMCQResult(False, False)
    solved = _solve_relation_text(candidates[0])
    if solved is None:
        return LinearSolveMCQResult(False, False)
    solution, variable = solved
    if declared_variables and declared_variables != {variable}:
        # Domen deklarisan za NEKU DRUGU nepoznatu — nedokazivo, ćutanje.
        return LinearSolveMCQResult(False, False)
    if domain in _SOLVE_DISCRETE_DOMAIN_MIN:
        solution = _discretize(solution, domain)

    # ŽIVI PP-1 LIVE-150 NALAZ (F008, druga pojava): objavljeno je
    #     „Riješi nejednačinu: $-1 < x+1 < 1$“  opcije 1 / -2 / -1 / 0, označeno -1
    # Skup rješenja je $-2<x<0$; $-1$ je samo JEDAN član. Orakl je tada ćutao jer
    # je golu vrijednost primao SAMO kad je izvedeno rješenje tačka (jednačina),
    # pa nijedan deterministički nalaz nije ni postojao i recenzent je odobrio.
    # Izričit zahtjev „Riješi nejednačinu“ traži CIO skup, pa je gola vrijednost
    # dokazivo nedovoljna — tačka nikad nije jednaka zraku ni intervalu. Pitanja
    # o članstvu zadržavaju staro ponašanje (vidi `_SOLVE_MEMBERSHIP_RE`).
    solution_is_pointlike = (solution.kind == "point"
                             or (solution.kind == "finite"
                                 and len(solution.members) == 1))
    allow_bare_value = (solution_is_pointlike
                        or not _SOLVE_MEMBERSHIP_RE.search(prose))
    option_sets = []
    unverifiable = False
    for option in options:
        parsed = _solve_option_set(option, variable,
                                   allow_bare_value=allow_bare_value,
                                   domain=domain)
        if parsed is None:
            # Pitanje o ČLANSTVU s golom vrijednošću/jednočlanim skupom: zadatak
            # semantički NIJE „riješi skup“ i orakl ostaje van njega (ćutanje),
            # kao i dosad. Proba s allow_bare_value=True razlikuje baš taj
            # slučaj od stvarno nečitljivog zapisa.
            if (not allow_bare_value
                    and _solve_option_set(option, variable,
                                          allow_bare_value=True,
                                          domain=domain) is not None):
                return LinearSolveMCQResult(False, False)
            # ZADATAK JE U DOMETU i skup rješenja je izveden: nečitljiva opcija
            # više NE gasi orakl. Ćutanje bi značilo „objavi bez ijedne
            # matematičke provjere“ — upravo klasa dva živa pogrešna paketa.
            unverifiable = True
            option_sets.append(None)
            continue
        option_sets.append(parsed)

    solution_display = solution.display(variable)
    option_displays = tuple("?" if candidate is None else candidate.display(variable)
                            for candidate in option_sets)
    if unverifiable:
        return LinearSolveMCQResult(True, False, UNVERIFIABLE_SOLUTION_OPTION_CODE,
                                    solution_display, option_displays, ())
    correct_indices = tuple(index for index, candidate in enumerate(option_sets)
                            if candidate == solution)
    if not correct_indices:
        return LinearSolveMCQResult(True, False, "no_correct_option",
                                    solution_display, option_displays,
                                    correct_indices)
    if len(correct_indices) != 1:
        return LinearSolveMCQResult(True, False, "multiple_correct_options",
                                    solution_display, option_displays,
                                    correct_indices)
    # DISC talas (RC1, dubinska odbrana): dva RAZLIČITA zapisa istog skupa među
    # PROČITANIM opcijama — čak i kad nijedan nije tačan odgovor — čine MCQ
    # dokazano neispravnim (učenik vidi „dvije iste“ opcije). Kod je namjerno
    # ISTI kao postojeći publikacijski kod za semantičke duplikate, pa
    # recenzentski recept i logovi ostaju jednoobrazni.
    for i in range(len(option_sets)):
        for j in range(i + 1, len(option_sets)):
            if option_sets[i] == option_sets[j]:
                return LinearSolveMCQResult(
                    True, False, EQUIVALENT_SOLUTION_OPTIONS_CODE,
                    solution_display, option_displays, correct_indices)
    return LinearSolveMCQResult(True, True, "", solution_display,
                                option_displays, correct_indices)


# ---------------------------------------------------------------------------
# USKI ORAKL FUNKCIJE ZADANE TABELOM PAROVA (DISC-C008, Task 2)
# ---------------------------------------------------------------------------
# ŽIVI DISC NALAZ (C008, lekcija o prikazu funkcije tabelom): objavljen je MCQ
#     „Funkcija $f$ je zadana tabelom parova: $\{(1,2),(2,3),(3,2),(4,5)\}$.
#      Je li slika elementa $1$ jedinstvena?“
# s OZNAČENOM opcijom „Ne — slika nije jedinstvena jer $f(1)=2$ i $f(3)=2$.“
# To je matematički pogrešno: ulaz 1 se u tabeli javlja TAČNO JEDNOM, pa je
# f(1)=2 jedinstveno određena. To što DVA RAZLIČITA ulaza dijele isti izlaz
# (f(1)=f(3)=2) narušava INJEKTIVNOST, ne jedinstvenost slike ulaza 1 —
# Tutor i recenzent su dijelili istu zabludu, a nijedan deterministički sloj
# nije provjeravao ovu matematiku.
#
# GRANICE (namjerno uske, isti princip kao svi orakli u modulu):
#   • angažuje se SAMO kad pitanje sadrži TAČNO JEDAN eksplicitan konačan
#     skup uređenih parova ($\{(1,2),(2,3),...\}$) s CJELOBROJNIM članovima
#     (decimalni zarez unutar para se NE pogađa — zarez je razdjelnik para);
#   • zatvorene direktive: vrijednost/slika elementa (f(k), „slika elementa
#     k“), jedinstvenost slike KONKRETNOG ulaza, „da li je ovo funkcija“;
#     sva ostala proza (proizvoljna svojstva, injektivnost, domen/kodomen…)
#     znači ćutanje;
#   • negacija („koja NIJE…“) isključuje orakl;
#   • verdikt-opcije se čitaju po zatvorenoj glavi „Da/Ne“; „Da“ opcija koja
#     imenuje vrijednost slike mora imenovati BAŠ serverski izvedenu (uz
#     toleranciju pominjanja samog ulaza k); kad NIJEDNA opcija nema čitljiv
#     verdikt, oblik nije dokazano ova klasa → ćutanje; kad je dio opcija
#     čitljiv a dio nije → zatvoreno padanje (Task 1 doktrina);
#   • FUNKCIJA ≠ INJEKCIJA: skup {(1,2),(3,2)} JESTE funkcija (različiti
#     ulazi smiju dijeliti izlaz); {(1,2),(1,3)} NIJE (jedan ulaz, dva
#     izlaza). Ponovljen IDENTIČAN par ima skupovnu semantiku i ne kvari
#     funkciju.
_FUNC_PAIR_RE = re.compile(r"\(\s*([+-]?\d+)\s*,\s*([+-]?\d+)\s*\)")
_FUNC_PAIR_SET_RE = re.compile(
    r"^\s*\\?\{\s*\(\s*[+-]?\d+\s*,\s*[+-]?\d+\s*\)"
    r"(?:\s*,\s*\(\s*[+-]?\d+\s*,\s*[+-]?\d+\s*\))*\s*,?\s*\\?\}\s*$")
_FUNC_ELEMENT_REF_RE = re.compile(
    r"\belement\w*\s+([+-]?\d+)\b|\bf\s*\(\s*([+-]?\d+)\s*\)")
_FUNC_UNIQUE_DIRECTIVE_RE = re.compile(
    r"\bjedinstven\w*", re.IGNORECASE)
_FUNC_IMAGE_WORD_RE = re.compile(r"\bslik\w*", re.IGNORECASE)
_FUNC_VALUE_DIRECTIVE_RE = re.compile(
    r"\bkolik[aoi]?\s+je\b|\bkoja\s+je\s+slika\b|\bodredi\b|\bizra[čc]unaj\w*\b",
    re.IGNORECASE)
_FUNC_IS_FUNCTION_RE = re.compile(
    r"(?:\bda\s+li\b|\bje\s+li\b|\bpredstavlja\s+li\b|\bjeste\s+li\b)"
    r"[^.?!]{0,80}?funkcij\w*",
    re.IGNORECASE)
_FUNC_NEGATION_RE = re.compile(r"\bnije\b|\bnisu\b", re.IGNORECASE)
_FUNC_VERDICT_RE = re.compile(r"^\s*(da|ne)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FunctionTableMCQResult:
    """Serverski izvedena činjenica o funkciji zadanoj tabelom parova."""

    applicable: bool
    valid: bool
    reason_code: str = ""
    solution_display: str = ""     # serverski izvedena istina (dijagnostika)
    option_displays: tuple = ()
    correct_indices: tuple = ()
    # Kompatibilnost s dijagnostikom koja za djeljivost čita `divisors`.
    divisors: tuple = ()

    @property
    def correct_index(self) -> Optional[int]:
        return self.correct_indices[0] if len(self.correct_indices) == 1 else None


def _function_pairs(question: str) -> Optional[tuple]:
    """Parovi iz TAČNO JEDNOG eksplicitnog skupa parova, ili None."""
    pair_segments = []
    for segment in math_contents(tokenize_math(question or "")):
        if _FUNC_PAIR_SET_RE.match(segment or ""):
            pair_segments.append(segment)
    if len(pair_segments) != 1:
        return None
    pairs = tuple((int(left), int(right))
                  for left, right in _FUNC_PAIR_RE.findall(pair_segments[0]))
    return pairs if pairs else None


def _function_flat_text(question: str) -> str:
    """Proza i matematika u jednom redu — element se pominje i kao proza
    („slika elementa“) i kao matematički segment ($1$, $f(1)$)."""
    return " ".join(content for _kind, content in tokenize_math(question or ""))


def _function_element(flat: str) -> Optional[int]:
    """TAČNO JEDAN pominjani element k, ili None (0 ili 2+ različita → None)."""
    values = set()
    for prose_number, call_number in _FUNC_ELEMENT_REF_RE.findall(flat):
        values.add(int(prose_number or call_number))
    return values.pop() if len(values) == 1 else None


def _function_option_verdict(option_text: str) -> Optional[bool]:
    """True=Da, False=Ne, None=verdikt nije čitljiv zatvorenom gramatikom."""
    text = (option_text or "").strip()
    if text.startswith("$") and text.endswith("$") and text.count("$") == 2:
        text = text[1:-1].strip()
    match = _FUNC_VERDICT_RE.match(text)
    if match is None:
        return None
    return match.group(1).lower() == "da"


def _adjudicate_verdict_options(options, correct_predicate,
                                solution_display) -> FunctionTableMCQResult:
    """Zajednička presuda za Da/Ne porodice (jedinstvenost, „je li funkcija“)."""
    verdicts = [_function_option_verdict(option) for option in options]
    if all(verdict is None for verdict in verdicts):
        # Nijedna opcija nema čitljiv verdikt — oblik NIJE dokazano ova
        # klasa (npr. opcije „jedinstvena“ / „nije jedinstvena“) → ćutanje.
        return FunctionTableMCQResult(False, False)
    option_displays = tuple("?" if verdict is None else ("da" if verdict else "ne")
                            for verdict in verdicts)
    if any(verdict is None for verdict in verdicts):
        # Zadatak je dokazano u dometu, dio opcija nečitljiv → zatvoreno
        # padanje (ista arhitektonska granica kao Task 1 solve orakl).
        return FunctionTableMCQResult(
            True, False, UNVERIFIABLE_SOLUTION_OPTION_CODE,
            solution_display, option_displays, ())
    correct_indices = tuple(index for index, verdict in enumerate(verdicts)
                            if correct_predicate(index, verdict))
    if not correct_indices:
        return FunctionTableMCQResult(True, False, "no_correct_option",
                                      solution_display, option_displays,
                                      correct_indices)
    if len(correct_indices) != 1:
        return FunctionTableMCQResult(True, False, "multiple_correct_options",
                                      solution_display, option_displays,
                                      correct_indices)
    return FunctionTableMCQResult(True, True, "", solution_display,
                                  option_displays, correct_indices)


def evaluate_function_table_mcq(question: str,
                                option_texts: Iterable[str]) -> FunctionTableMCQResult:
    """Ocijeni SAMO jednoznačan MCQ nad tabelom parova; sve ostalo ćuti."""
    options = tuple(option_texts or ())
    if not options:
        return FunctionTableMCQResult(False, False)
    prose = " ".join(content for kind, content in tokenize_math(question or "")
                     if kind == TEXT)
    if _FUNC_NEGATION_RE.search(prose):
        # „Koja tvrdnja NIJE tačna…“ obrće šta je tačan odgovor — ćutanje.
        return FunctionTableMCQResult(False, False)
    pairs = _function_pairs(question)
    if pairs is None:
        return FunctionTableMCQResult(False, False)
    outputs: dict = {}
    for left, right in pairs:
        outputs.setdefault(left, set()).add(right)

    flat = _function_flat_text(question)
    element = _function_element(flat)

    # 1) JEDINSTVENOST SLIKE KONKRETNOG ULAZA (tačna C008 klasa).
    if (_FUNC_UNIQUE_DIRECTIVE_RE.search(prose)
            and _FUNC_IMAGE_WORD_RE.search(prose)):
        if element is None or element not in outputs:
            return FunctionTableMCQResult(False, False)
        images = outputs[element]
        unique = len(images) == 1
        image_value = next(iter(images)) if unique else None
        display = (f"slika({element}) jedinstvena = da, f({element}) = {image_value}"
                   if unique else f"slika({element}) jedinstvena = ne")

        def correct(index, verdict):
            if not unique:
                return not verdict
            if not verdict:
                return False
            # „Da“ opcija koja imenuje vrijednost mora imenovati BAŠ izvedenu
            # sliku; pominjanje samog ulaza k je dozvoljeno (npr. „slika
            # elementa 1 iznosi 2“).
            mentioned = {int(value) for value in
                         _ANSWER_NUMBER_RE.findall(options[index] or "")}
            extras = mentioned - {element}
            return not extras or extras == {image_value}

        return _adjudicate_verdict_options(options, correct, display)

    # 2) VRIJEDNOST/SLIKA ELEMENTA — f(k) za ulaz koji se javlja tačno jednom.
    if (element is not None
            and (_FUNC_VALUE_DIRECTIVE_RE.search(prose)
                 or _FUNC_IMAGE_WORD_RE.search(prose))
            and element in outputs and len(outputs[element]) == 1):
        image_value = next(iter(outputs[element]))
        values = tuple(_bare_integer(option) for option in options)
        if all(value is None for value in values):
            return FunctionTableMCQResult(False, False)
        option_displays = tuple("?" if value is None else str(value)
                                for value in values)
        display = f"f({element}) = {image_value}"
        if any(value is None for value in values):
            return FunctionTableMCQResult(
                True, False, UNVERIFIABLE_SOLUTION_OPTION_CODE,
                display, option_displays, ())
        correct_indices = tuple(index for index, value in enumerate(values)
                                if value == image_value)
        if not correct_indices:
            return FunctionTableMCQResult(True, False, "no_correct_option",
                                          display, option_displays,
                                          correct_indices)
        if len(correct_indices) != 1:
            return FunctionTableMCQResult(True, False, "multiple_correct_options",
                                          display, option_displays,
                                          correct_indices)
        return FunctionTableMCQResult(True, True, "", display,
                                      option_displays, correct_indices)

    # 3) „DA LI JE OVO FUNKCIJA?“ — nijedan ulaz s dva različita izlaza.
    if _FUNC_IS_FUNCTION_RE.search(prose):
        is_function = all(len(images) == 1 for images in outputs.values())
        display = f"funkcija = {'da' if is_function else 'ne'}"

        def correct(_index, verdict):
            return verdict == is_function

        return _adjudicate_verdict_options(options, correct, display)

    return FunctionTableMCQResult(False, False)


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
            else:
                # PP-1 LIVE-150 (F008): „riješi (ne)jednačinu“ MCQ dosad nije
                # imao NIJEDAN matematički orakl, pa je pogrešno označeno
                # $x=-3$ (član skupa umjesto skupa $-4<x<-2$) objavljeno.
                # Redoslijed je namjeran: postojeći orakli zadržavaju prednost
                # bajt za bajt; ovaj se pita tek kad svi ostali ćute.
                solve = evaluate_linear_solve_mcq(question, option_texts)
                if solve.applicable:
                    if not solve.valid:
                        return solve.reason_code, solve
                    if marked_index != solve.correct_index:
                        return "marked_option_math_mismatch", solve
                else:
                    # DISC-C008 (Task 2): funkcija zadana tabelom parova —
                    # jedinstvenost slike, f(k) i „je li funkcija“. Redoslijed
                    # je namjeran: svi postojeći orakli zadržavaju prednost
                    # bajt za bajt; ovaj se pita tek kad svi ostali ćute.
                    table = evaluate_function_table_mcq(question, option_texts)
                    if table.applicable:
                        if not table.valid:
                            return table.reason_code, table
                        if marked_index != table.correct_index:
                            return "marked_option_math_mismatch", table
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
