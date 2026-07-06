"""Deterministička provjera učenikovog odgovora na practice/exam zadatak.

Motiv (audit): ocjenjivanje je do sada bilo 100% na LLM-u, pa je model znao
tačan odgovor proglasiti netačnim (npr. "5/8" za "koji dio nije obojen ako je
obojano 3/8"). Ovaj modul u KODU rješava klase zadataka koje se daju riješiti
deterministički i vraća presudu po stavci; LLM onda samo formuliše pedagoški
odgovor i NE smije protivrječiti presudi.

Principi:
- ČIST modul: bez mreže, bez IO-a, bez importa app-a. ``fractions.Fraction``
  automatski pokriva ekvivalentne razlomke (3/5 == 6/10) i mješovite brojeve
  (2 1/4 == 9/4).
- KONZERVATIVNO: kada zadatak ili odgovor nije moguće pouzdano parsirati,
  presuda je ``unverified`` — NIKAD se ne izmišlja "netačno". Jedina smjela
  tvrdnja je ona koju kod stvarno može izračunati.
- OPŠTE klase zadataka (ne hardkodirani primjeri): komplement razlomka
  ("koji dio nije/ostaje"), pretvaranje mješoviti↔nepravi, direktan račun
  ("izračunaj 1/2 + 1/3"), skraćivanje razlomka.

Ulazne notacije: običan tekst ("3/5", "2 1/4", "2,5") i LaTeX koji tutor sam
piše po pravilima zapisa ("\\frac{3}{5}", "2\\frac{1}{4}", "\\(...\\)").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

__all__ = [
    "check_practice_answer",
    "derive_expected",
    "format_check_block",
    "parse_number_token",
    "parse_student_answers",
    "split_numbered_items",
]

_DIACRITIC_MAP = str.maketrans({
    "č": "c", "ć": "c", "đ": "d", "š": "s", "ž": "z",
    "Č": "c", "Ć": "c", "Đ": "d", "Š": "s", "Ž": "z",
})


def _fold(text: str) -> str:
    return (text or "").translate(_DIACRITIC_MAP).lower()


# --- Normalizacija LaTeX zapisa u običan tekst -----------------------------------

_FRAC_RE = re.compile(r"\\d?frac\s*\{\s*(-?\d+)\s*\}\s*\{\s*(-?\d+)\s*\}")
_LATEX_WRAP_RE = re.compile(r"\\[()\[\]]|\$\$?")
_CDOT_RE = re.compile(r"\\cdot|\\times|[·×]")
_DIV_RE = re.compile(r"\\div|÷")


def _normalize_math_text(text: str) -> str:
    """LaTeX/unicode zapis → oblik koji tokenizer razumije ("\\frac{3}{5}" → "3/5").

    Razmak oko zamjene je bitan: "2\\frac{1}{4}" (LaTeX zapis mješovitog broja)
    mora postati "2 1/4", ne "21/4"."""
    t = text or ""
    t = _FRAC_RE.sub(r" \1/\2 ", t)
    t = _LATEX_WRAP_RE.sub(" ", t)
    t = _CDOT_RE.sub("*", t)
    t = _DIV_RE.sub(":", t)
    t = t.replace("−", "-").replace("–", "-")
    return t


# --- Parsiranje jednog broja (razlomak / mješoviti / cijeli / decimalni) ---------

@dataclass
class NumberToken:
    value: Fraction
    form: str          # "fraction" | "mixed" | "integer" | "decimal"
    raw: str = ""

    @property
    def is_reduced_fraction(self) -> bool:
        if self.form != "fraction":
            return False
        num, den = _fraction_parts(self.raw)
        if den == 0:
            return False
        from math import gcd
        return gcd(abs(num), den) == 1


def _fraction_parts(raw: str) -> tuple[int, int]:
    m = re.search(r"(-?\d+)\s*/\s*(\d+)", raw)
    if not m:
        return 0, 0
    return int(m.group(1)), int(m.group(2))


# Mješoviti: "2 1/4" (razmak) — u LaTeX obliku "2\frac{1}{4}" poslije normalizacije
# postane "2 1/4" (frac → 1/4, wrap → razmak). Redoslijed pokušaja je bitan:
# mješoviti PRIJE običnog razlomka, razlomak PRIJE cijelog broja. Lookahead
# blokira samo NASTAVAK istog broja ("3/5/7", "3.5"), ne interpunkciju
# rečenice ("6/10.") niti sljedeći broj u listi ("3/5 1/4").
_MIXED_RE = re.compile(r"(?<![\d/,.])(-?\d+)\s+(\d+)\s*/\s*(\d+)(?!\s*/)(?![.,]\d)")
_SIMPLE_FRAC_RE = re.compile(r"(?<![\d/,.])(-?\d+)\s*/\s*(\d+)(?!\s*/)(?![.,]\d)")
_DECIMAL_RE = re.compile(r"(?<![\d/,.])(-?\d+),(\d+)(?![\d/])")
_INT_RE = re.compile(r"(?<![\d/,.])(-?\d+)(?!\s*/)(?![.,]\d)(?!\s+\d+\s*/)")


def parse_number_token(text: str) -> NumberToken | None:
    """Parsiraj TAČNO JEDAN broj iz kratkog teksta; None ako nije jednoznačno."""
    tokens = _scan_number_tokens(_normalize_math_text(text))
    return tokens[0] if len(tokens) == 1 else None


def _scan_number_tokens(norm: str) -> list[NumberToken]:
    """Svi brojevni tokeni u tekstu, s lijeva na desno, bez preklapanja."""
    found: list[tuple[int, int, NumberToken]] = []

    def _add(m: re.Match, tok: NumberToken):
        for s, e, _t in found:
            if m.start() < e and m.end() > s:
                return
        found.append((m.start(), m.end(), tok))

    for m in _MIXED_RE.finditer(norm):
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if den == 0:
            continue
        sign = -1 if whole < 0 else 1
        value = Fraction(whole) + sign * Fraction(num, den)
        _add(m, NumberToken(value=value, form="mixed", raw=m.group(0).strip()))
    for m in _SIMPLE_FRAC_RE.finditer(norm):
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            continue
        _add(m, NumberToken(value=Fraction(num, den), form="fraction", raw=m.group(0).strip()))
    for m in _DECIMAL_RE.finditer(norm):
        whole, dec = m.group(1), m.group(2)
        sign = -1 if whole.startswith("-") else 1
        value = Fraction(abs(int(whole))) + Fraction(int(dec), 10 ** len(dec))
        _add(m, NumberToken(value=sign * value, form="decimal", raw=m.group(0).strip()))
    for m in _INT_RE.finditer(norm):
        _add(m, NumberToken(value=Fraction(int(m.group(1))), form="integer", raw=m.group(0).strip()))

    found.sort(key=lambda x: x[0])
    return [t for _s, _e, t in found]


# --- Podjela zadatka na numerisane stavke -----------------------------------------

_ITEM_MARKER_RE = re.compile(r"(?:^|(?<=[.!?:]\s))\s*(\d{1,2})[.)]\s+", re.MULTILINE)


def split_numbered_items(task_text: str) -> list[tuple[int, str]]:
    """Vrati [(broj, tekst stavke), ...]; prazna lista = zadatak nije numerisan.

    Markeri se prihvataju samo na početku reda ili poslije kraja rečenice, da se
    "12. januar" usred rečenice ne protumači kao stavka 12."""
    text = task_text or ""
    marks = [(m.start(), m.end(), int(m.group(1))) for m in _ITEM_MARKER_RE.finditer(text)]
    if len(marks) < 2:
        return []
    numbers = [n for _s, _e, n in marks]
    # numerisana lista mora krenuti od 1 i rasti — sve drugo su brojevi u tekstu
    if numbers[0] != 1 or any(b <= a for a, b in zip(numbers, numbers[1:])):
        return []
    items: list[tuple[int, str]] = []
    for i, (_s, e, n) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        chunk = text[e:end].strip()
        if chunk:
            items.append((n, chunk))
    return items if len(items) >= 2 else []


# --- Izvođenje očekivanog rezultata za stavku (opšte klase, ne primjeri) ----------

@dataclass
class Expected:
    value: Fraction
    kind: str                     # "complement" | "to_improper" | "to_mixed" | "arithmetic" | "simplify"
    required_form: str | None = None   # "fraction" (nepravi) | "mixed" | None
    # "high" → smije se presuditi i TAČNO i NETAČNO; "positive_only" → samo
    # potvrda tačnog (kontekst bi mogao mijenjati jedinicu odgovora, pa se
    # različit odgovor NE proglašava netačnim nego unverified).
    confidence: str = "high"


_COMPLEMENT_SIGNAL_RE = re.compile(
    r"\b(nije|nisu|ne\s+bude)\b|\bosta(?:je|lo|la|o|ne)\b|\bpreosta\w*"
)
_QUESTION_SIGNAL_RE = re.compile(r"\b(koji|koja|koliki|kolika|koliko)\b")
_DIO_RE = re.compile(r"\b(dio|dijel\w*|deo|dela)\b")
_TO_IMPROPER_RE = re.compile(r"neprav\w*\s+razlom\w*")
_TO_MIXED_RE = re.compile(r"m[ij]e[sš]?ovit\w*\s+broj\w*")
_CONVERT_RE = re.compile(r"\bpretvori|\bzapisi|\bnapisi|\bpredstavi")
_SIMPLIFY_RE = re.compile(r"\bskrati")
_CALC_LEAD_RE = re.compile(r"\b(izracunaj|koliko\s+je|odredi\s+vrijednost)\b")


def _try_complement(folded: str, tokens: list[NumberToken]) -> Expected | None:
    """"Obojano je 3/8 — koji dio NIJE obojen / koliko OSTAJE?" → 1 − 3/8.

    Uslovi: razlomak 0 < f < 1 je JEDINI broj u stavci (dodatni brojevi poput
    "od 50 KM" mijenjaju traženu veličinu), plus signal komplementa
    (nije/ostaje/preostaje) i upitna riječ. Opšti obrazac, ne zavisi od
    konteksta (krug, pizza, novac, riža...). Ako pitanje ne spominje "dio",
    jedinica odgovora nije garantovano razlomak → samo potvrdna presuda."""
    if len(tokens) != 1:
        return None
    tok = tokens[0]
    if tok.form not in ("fraction", "mixed", "decimal") or not (0 < tok.value < 1):
        return None
    if not (_COMPLEMENT_SIGNAL_RE.search(folded) and _QUESTION_SIGNAL_RE.search(folded)):
        return None
    confidence = "high" if _DIO_RE.search(folded) else "positive_only"
    return Expected(value=Fraction(1) - tok.value, kind="complement", confidence=confidence)


def _try_conversion(folded: str, tokens: list[NumberToken]) -> Expected | None:
    if not _CONVERT_RE.search(folded):
        return None
    if _TO_IMPROPER_RE.search(folded):
        mixed = [t for t in tokens if t.form == "mixed"]
        if len(mixed) == 1:
            return Expected(value=mixed[0].value, kind="to_improper", required_form="fraction")
    if _TO_MIXED_RE.search(folded):
        improper = [t for t in tokens if t.form == "fraction" and abs(t.value) > 1]
        if len(improper) == 1:
            return Expected(value=improper[0].value, kind="to_mixed", required_form="mixed")
    return None


def _try_simplify(folded: str, tokens: list[NumberToken]) -> Expected | None:
    if not _SIMPLIFY_RE.search(folded):
        return None
    fracs = [t for t in tokens if t.form == "fraction"]
    if len(fracs) != 1:
        return None
    return Expected(value=fracs[0].value, kind="simplify", required_form="fraction")


_EXPR_PREFIX_RE = re.compile(
    r"[\s:=]*((?:-?\d+(?:\s+\d+\s*/\s*\d+|\s*/\s*\d+|,\d+)?\s*[+\-*:]\s*)+"
    r"-?\d+(?:\s+\d+\s*/\s*\d+|\s*/\s*\d+|,\d+)?)"
)
_TASK_LEAD_STRIP_RE = re.compile(r"^\s*(zadatak(\s+za\s+vjezbu)?|primjer)\s*[:.\-]?\s*", re.IGNORECASE)


def _try_arithmetic(folded: str, norm_text: str) -> Expected | None:
    """"Izračunaj: 1/2 + 1/3" → 5/6; isto i kada je zadatak SAMO izraz
    ("3/4 · 2/5"). Bez zagrada; množenje i dijeljenje prije sabiranja i
    oduzimanja (školski prioritet)."""
    m = _CALC_LEAD_RE.search(folded)
    if m:
        tail = norm_text[m.end():]
        expr_m = _EXPR_PREFIX_RE.match(tail)
    else:
        # bez "izračunaj": prihvati SAMO ako je cijela stavka čist izraz
        # (uz opcioni "Zadatak:" prefiks i završnu interpunkciju)
        stripped = _TASK_LEAD_STRIP_RE.sub("", norm_text).strip()
        expr_m = _EXPR_PREFIX_RE.match(stripped)
        if expr_m and stripped[expr_m.end():].strip(" .!?="):
            return None
    if not expr_m:
        return None
    value = _eval_expr(expr_m.group(1))
    if value is None:
        return None
    return Expected(value=value, kind="arithmetic")


_NUMBER_CHUNK_RE = re.compile(
    r"\s*(-?\d+\s+\d+\s*/\s*\d+|-?\d+\s*/\s*\d+|-?\d+,\d+|-?\d+)"
)
_OP_CHUNK_RE = re.compile(r"\s*([+\-*:])")


def _eval_expr(expr: str) -> Fraction | None:
    """Evaluiraj niz broj-operator-broj... sa prioritetom (*, :) pa (+, -)."""
    pos = 0
    items: list = []   # naizmjenično Fraction vrijednosti i operatori
    expect_number = True
    while pos < len(expr):
        if expect_number:
            m = _NUMBER_CHUNK_RE.match(expr, pos)
            if not m:
                break
            toks = _scan_number_tokens(m.group(1))
            if len(toks) != 1:
                return None
            items.append(toks[0].value)
            expect_number = False
        else:
            m = _OP_CHUNK_RE.match(expr, pos)
            if not m:
                break
            items.append(m.group(1))
            expect_number = True
        pos = m.end()
    if expect_number or len(items) < 3 or expr[pos:].strip():
        return None
    # prvo * i :
    try:
        stack: list = [items[0]]
        i = 1
        while i < len(items):
            op, val = items[i], items[i + 1]
            if op == "*":
                stack[-1] = stack[-1] * val
            elif op == ":":
                stack[-1] = stack[-1] / val
            else:
                stack.append(op)
                stack.append(val)
            i += 2
        result = stack[0]
        i = 1
        while i < len(stack):
            op, val = stack[i], stack[i + 1]
            result = result + val if op == "+" else result - val
            i += 2
        return result
    except ZeroDivisionError:
        return None


def derive_expected(item_text: str) -> Expected | None:
    """Pokušaj deterministički izračunati očekivani rezultat stavke; None = ne zna."""
    norm = _normalize_math_text(item_text or "")
    folded = _fold(norm)
    tokens = _scan_number_tokens(norm)
    for solver in (_try_conversion, _try_simplify, _try_complement):
        result = solver(folded, tokens)
        if result is not None:
            return result
    return _try_arithmetic(folded, norm)


# --- Parsiranje učenikovog odgovora ------------------------------------------------

# "1) 3/5 2) 1/4" — marker MORA imati interpunkciju iza broja, da se "2 1/4"
# (mješoviti broj) ne protumači kao "stavka 2, odgovor 1/4".
_ANSWER_MARKER_RE = re.compile(r"(?:^|[\s,;])(\d{1,2})\s*[).:]\s*")


def parse_student_answers(student_text: str) -> tuple[str, dict[int, NumberToken | None]]:
    """Vrati ("numbered", {n: token|None}) | ("ordered", {1: t1, 2: t2, ...}) |
    ("single", {1: token}) | ("none", {}).

    U "numbered" mapi ``None`` znači: učenik JESTE odgovorio na stavku n, ali
    odgovor nije jednoznačno parsiran (ne smije se ocijeniti kao "bez odgovora").
    Konzervativno: slobodan tekst sa više brojeva ("mislim 3/5 jer 8-3=5") NE
    mapiramo — bolje unverified nego pogrešno."""
    norm = _normalize_math_text(student_text or "").strip()
    if not norm:
        return "none", {}

    marks = [(m.start(1), m.end(), int(m.group(1))) for m in _ANSWER_MARKER_RE.finditer(norm)]
    if marks:
        numbered: dict[int, NumberToken | None] = {}
        for i, (_s, e, n) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(norm)
            toks = _scan_number_tokens(norm[e:end])
            if 0 < n <= 20:
                numbered[n] = toks[0] if len(toks) == 1 else None
        if any(t is not None for t in numbered.values()):
            return "numbered", numbered

    tokens = _scan_number_tokens(norm)
    if len(tokens) == 1:
        # jedan broj, ali samo ako je poruka suštinski taj broj (kratak odgovor),
        # ne rečenica u kojoj se broj slučajno spominje
        stripped = re.sub(r"[\s.,;!?]+$", "", norm)
        if len(stripped) <= 40:
            return "single", {1: tokens[0]}
        return "none", {}

    # više brojeva bez markera: prihvati SAMO ako je poruka čista lista brojeva
    leftover = norm
    for t in tokens:
        leftover = leftover.replace(t.raw, " ", 1)
    if re.fullmatch(r"[\s,;i]*", leftover or ""):
        return "ordered", {i + 1: t for i, t in enumerate(tokens)}
    return "none", {}


# --- Eksplicitno referenciranje stavki ("treće pitanje", "zadatak 2") ---------------

_ORDINAL_WORDS = (
    (1, r"prv(?:i|a|o|og|om|u)"),
    (2, r"drug(?:i|a|o|og|om|u)"),
    (3, r"trec(?:i|a|e|eg|em|u)"),
    (4, r"cetvrt(?:i|a|o|og|om|u)"),
    (5, r"pet(?:i|a|o|og|om|u)"),
    (6, r"sest(?:i|a|o|og|om|u)"),
    (7, r"sedm(?:i|a|o|og|om|u)"),
    (8, r"osm(?:i|a|o|og|om|u)"),
)
_ITEM_NOUN = r"(?:pitanj\w*|zadat\w*|zadac\w*|stavk\w*)"
_LAST_ITEM_RE = re.compile(rf"\b(?:zadnj|posljednj)\w*\s+{_ITEM_NOUN}")
_REFERENCED_NUMBER_RE = re.compile(
    r"\b(?:na|za|kod|u)\s+(\d{1,2})\s*[.)]?(?!\s*/)"
)


def detect_referenced_items(student_text: str, valid_numbers: list[int]) -> set[int]:
    """Stavke koje učenik EKSPLICITNO spominje: "odgovor na treće pitanje",
    "zadatak 2", "3. pitanje", "zadnji zadatak". Prazan skup = bez referenci.

    Referenca znači samo "učenik je POKUŠAO ovu stavku" — ostale se označavaju
    kao nepokušane (nikad kao netačne)."""
    folded = _fold(_normalize_math_text(student_text or ""))
    if not folded or not valid_numbers:
        return set()
    refs: set[int] = set()
    for n, pat in _ORDINAL_WORDS:
        if re.search(rf"\b{pat}\s+{_ITEM_NOUN}", folded) or re.search(rf"\bna\s+{pat}\b(?!\s*\d)", folded):
            refs.add(n)
    for m in re.finditer(rf"\b(\d{{1,2}})\s*[.)]?\s*{_ITEM_NOUN}", folded):
        refs.add(int(m.group(1)))
    for m in re.finditer(rf"\b{_ITEM_NOUN}\s+(?:broj\s+)?(\d{{1,2}})\b", folded):
        refs.add(int(m.group(1)))
    # "odgovor za 2.", "na 3. je..." — važno za konceptualne odgovore bez
    # numeričkog tokena, gdje parse_student_answers ne može mapirati stavku.
    for m in _REFERENCED_NUMBER_RE.finditer(folded):
        refs.add(int(m.group(1)))
    if _LAST_ITEM_RE.search(folded):
        refs.add(max(valid_numbers))
    return {n for n in refs if n in valid_numbers}


# --- Glavna provjera ----------------------------------------------------------------

@dataclass
class ItemCheck:
    n: int
    task: str
    expected: Expected | None
    given: NumberToken | None
    # "correct" | "correct_value_wrong_form" | "incorrect" | "missing"
    # | "not_attempted" | "unverified"
    # missing = odgovarao je na skup stavki, ali ovu izostavio;
    # not_attempted = eksplicitno je rješavao SAMO druge stavke.
    # Ni missing ni not_attempted se NIKAD ne opisuju kao "netačno".
    verdict: str


@dataclass
class CheckResult:
    checkable: bool
    items: list[ItemCheck] = field(default_factory=list)

    @property
    def has_verdicts(self) -> bool:
        return any(
            i.verdict in (
                "correct", "correct_value_wrong_form", "incorrect",
                "missing", "not_attempted",
            )
            for i in self.items
        )


def _judge(expected: Expected | None, given: NumberToken | None, answered: bool) -> str:
    if given is None:
        # answered=True znači: odgovor postoji, ali ga nismo znali pročitati
        return "unverified" if answered else "missing"
    if expected is None:
        return "unverified"
    if given.value != expected.value:
        # positive_only: različita vrijednost može biti druga jedinica/oblik —
        # ne smijemo tvrditi "netačno", model provjerava sam
        return "incorrect" if expected.confidence == "high" else "unverified"
    if expected.required_form == "fraction" and given.form != "fraction":
        return "correct_value_wrong_form"
    if expected.required_form == "mixed" and given.form not in ("mixed", "integer"):
        return "correct_value_wrong_form"
    if expected.kind == "simplify" and not given.is_reduced_fraction:
        return "correct_value_wrong_form"
    return "correct"


def check_practice_answer(task_text: str, student_text: str) -> CheckResult:
    """Uporedi učenikov odgovor sa zadatkom, po stavkama. Nikad ne baca izuzetak."""
    try:
        return _check(task_text or "", student_text or "")
    except Exception:
        return CheckResult(checkable=False)


def _check(task_text: str, student_text: str) -> CheckResult:
    numbered_items = split_numbered_items(task_text)
    if numbered_items:
        items = numbered_items
    else:
        items = [(1, task_text.strip())] if task_text.strip() else []
    if not items:
        return CheckResult(checkable=False)

    valid = [n for n, _t in items]
    mode, answers = parse_student_answers(student_text)
    # "odgovor na treće pitanje je ..." — eksplicitna referenca vrijedi i kada
    # sam odgovor nije numerički parsiran (konceptualne stavke)
    refs = detect_referenced_items(student_text, valid) if len(items) > 1 else set()
    if mode == "none" and not refs:
        return CheckResult(checkable=False)

    expected_by_n = {n: derive_expected(text) for n, text in items}

    given_by_n: dict[int, NumberToken] = {}
    answered_ns: set[int] = set()
    refs_mode = False
    if mode == "numbered":
        # prihvati samo brojeve stavki koje stvarno postoje u zadatku
        if not set(answers) & set(valid):
            return CheckResult(checkable=False)
        given_by_n = {n: t for n, t in answers.items() if n in valid and t is not None}
        answered_ns = {n for n in answers if n in valid}
    elif refs:
        # učenik rješava SAMO stavke koje spominje; ostale NIJE pokušao —
        # one su not_attempted i nikad se ne ocjenjuju kao netačne
        refs_mode = True
        answered_ns = set(refs)
        if len(refs) == 1 and mode == "single":
            given_by_n = {next(iter(refs)): answers[1]}
    elif mode == "single":
        if len(items) != 1:
            # jedan nenumerisan odgovor na više stavki — ne nagađamo koju
            return CheckResult(checkable=False)
        given_by_n = {items[0][0]: answers[1]}
        answered_ns = set(given_by_n)
    elif mode == "ordered":
        if len(answers) != len(items):
            return CheckResult(checkable=False)
        given_by_n = {valid[i]: answers[i + 1] for i in range(len(items))}
        answered_ns = set(given_by_n)
    else:
        return CheckResult(checkable=False)

    # Bez ijednog izračunljivog očekivanja provjera i dalje ima smisla ako
    # razlikuje pokušane od nepokušanih stavki (djelimičan odgovor na
    # višestavkovni zadatak); inače model provjerava sam.
    partial = len(items) > 1 and answered_ns and len(answered_ns) < len(items)
    if all(e is None for e in expected_by_n.values()) and not partial:
        return CheckResult(checkable=False)

    checks: list[ItemCheck] = []
    for n, text in items:
        given = given_by_n.get(n)
        if n in answered_ns:
            verdict = _judge(expected_by_n[n], given, answered=True)
        else:
            verdict = "not_attempted" if refs_mode else "missing"
        checks.append(ItemCheck(
            n=n, task=text[:200], expected=expected_by_n[n], given=given, verdict=verdict,
        ))
    return CheckResult(checkable=True, items=checks)


# --- Render bloka za prompt ---------------------------------------------------------

def _fmt_fraction(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def format_check_block(result: CheckResult) -> str:
    """Bosanski blok za user prompt; prazan string kada nema presuda."""
    if not result.checkable or not result.has_verdicts:
        return ""
    lines = [
        "PROVJERA IZ SISTEMA (izračunata u kodu — POUZDANA, ne smiješ joj protivrječiti):",
    ]
    for item in result.items:
        given = item.given.raw if item.given else None
        if item.verdict == "correct":
            lines.append(
                f"- Stavka {item.n}: TAČNO. Učenik: {given}; tačan rezultat: "
                f"{_fmt_fraction(item.expected.value)}."
            )
        elif item.verdict == "correct_value_wrong_form":
            lines.append(
                f"- Stavka {item.n}: VRIJEDNOST TAČNA ({given}), ali NIJE u traženom "
                f"obliku — objasni koji se oblik traži, bez riječi \"netačno\"."
            )
        elif item.verdict == "incorrect":
            lines.append(
                f"- Stavka {item.n}: NETAČNO. Učenik: {given}; tačan rezultat: "
                f"{_fmt_fraction(item.expected.value)}. Prikaži račun."
            )
        elif item.verdict == "missing":
            lines.append(
                f"- Stavka {item.n}: BEZ ODGOVORA — NE ocjenjuj je kao netačnu; "
                f"na kraju zamoli učenika da odgovori SAMO na ovu stavku."
            )
        elif item.verdict == "not_attempted":
            lines.append(
                f"- Stavka {item.n}: NIJE POKUŠANA — učenik je u ovoj poruci "
                f"rješavao samo stavke koje je izričito spomenuo. NE ocjenjuj je, "
                f"NE izmišljaj njegov odgovor i ne spominji je kao tačnu ni "
                f"netačnu; na kraju zatraži preostale stavke jednu po jednu "
                f"(prvo najniži broj koji nedostaje)."
            )
        else:  # unverified
            lines.append(
                f"- Stavka {item.n}: nije automatski provjerena — sam pažljivo "
                f"izračunaj rezultat PRIJE nego što presudiš."
            )
    lines.append(
        "OBAVEZNO: tvoja ocjena po stavkama mora biti IDENTIČNA ovoj provjeri. "
        "Stavku označenu kao TAČNO nikad ne proglašavaj netačnom."
    )
    return "\n".join(lines)


def summarize_result(result: CheckResult) -> dict | None:
    """Kompaktan sažetak za response JSON / testove; None kada nema provjere."""
    if not result.checkable or not result.has_verdicts:
        return None
    return {
        "items": [
            {
                "n": i.n,
                "verdict": i.verdict,
                "expected": _fmt_fraction(i.expected.value) if i.expected else None,
                "given": i.given.raw if i.given else None,
            }
            for i in result.items
        ]
    }
