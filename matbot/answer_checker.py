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
    "parse_set_answer",
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
    t = _normalize_vulgar_fractions(t)
    # Bosanski lokal: decimalni separator je zarez. Tačka IZMEĐU dvije cifre je
    # skoro sigurno decimalna ("8.45" → "8,45"); tako "8.45" ne biva pogrešno
    # pročitano kao stavka "8." s odgovorom "45". Tačka koja nije između cifara
    # (kraj rečenice, "8.45.") ostaje netaknuta. Hiljadni separatori se u ovom
    # gradivu ne pišu tačkom, pa nema dvosmislenosti koju bismo pokvarili.
    t = _DOT_DECIMAL_RE.sub(",", t)
    # A3 (AUD-05): zagrade oko JEDNOG broja su samo zapis ("(-3) + 5" → "-3 + 5")
    # — skidanjem ih izraz postaje parsabilan za _EXPR_PREFIX_RE/_eval_expr.
    t = _PAREN_NUMBER_RE.sub(r"\1", t)
    return t


# Tačka između dvije cifre = decimalni separator (konzervativno, samo taj slučaj).
_DOT_DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")

# Zagrada oko jednog broja/razlomka: "(-3)", "( 2/5 )", "(1,5)" → goli broj.
_PAREN_NUMBER_RE = re.compile(
    r"\(\s*(-?\d+(?:[,.]\d+)?(?:\s*/\s*\d+)?(?:\s+\d+\s*/\s*\d+)?)\s*\)"
)
_VULGAR_FRACTIONS = {
    "\u00bd": "1/2",
    "\u00bc": "1/4",
    "\u00be": "3/4",
}


def _normalize_vulgar_fractions(text: str) -> str:
    out = text
    for glyph, frac in _VULGAR_FRACTIONS.items():
        out = re.sub(rf"(?<=\d)\s*{re.escape(glyph)}", f" {frac}", out)
        out = out.replace(glyph, f" {frac} ")
    return out


# --- Parsiranje jednog broja (razlomak / mješoviti / cijeli / decimalni) ---------

@dataclass
class NumberToken:
    value: Fraction
    form: str          # "fraction" | "mixed" | "integer" | "decimal"
    raw: str = ""
    unit: str | None = None
    unit_raw: str = ""
    notation: str | None = None
    unrecognized_unit: str = ""
    # Skupovi: kada je ``form == "set"`` odgovor je SKUP kanonskih elemenata
    # (``value`` tada nosi broj elemenata radi kompatibilnosti, ``raw`` prikaz).
    elements: "frozenset[str] | None" = None

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
_ANSWER_SUFFIX_RE = re.compile(
    r"\s*(%|\u00b0|[A-Za-zČĆĐŠŽčćđšž]+(?:\s*/\s*[A-Za-zČĆĐŠŽčćđšž]+)?)"
)
_DEGREE_WORD_RE = re.compile(r"^(?:stepen\w*|stupanj\w*|stupnje\w*)$")


def _with_suffix(norm: str, end: int, tok: NumberToken) -> tuple[int, NumberToken]:
    """Attach a tightly following answer suffix (unit, %, degree) to a token.

    Unknown alphabetic suffixes are preserved as evidence but not folded into
    ``raw``; otherwise prose like "5 plus 3" could become a fake clean answer.
    """
    m = _ANSWER_SUFFIX_RE.match(norm, end)
    if not m:
        return end, tok
    suffix = m.group(1).strip()
    folded = _fold(suffix).replace(" ", "")
    raw = (tok.raw + norm[end:m.end()]).strip()
    if suffix == "%":
        return m.end(), NumberToken(
            value=tok.value / 100,
            form="percentage",
            raw=raw,
            unit="%",
            unit_raw=suffix,
            notation="percent",
        )
    if suffix == "\u00b0" or _DEGREE_WORD_RE.match(folded):
        return m.end(), NumberToken(
            value=tok.value,
            form=tok.form,
            raw=raw,
            unit="\u00b0",
            unit_raw=suffix,
            notation="degree",
        )
    unit = _unit_key(folded)
    if unit:
        return m.end(), NumberToken(
            value=tok.value,
            form=tok.form,
            raw=raw,
            unit=unit,
            unit_raw=suffix,
        )
    if len(folded) <= 12:
        return end, NumberToken(
            value=tok.value,
            form=tok.form,
            raw=tok.raw,
            unrecognized_unit=suffix,
        )
    return end, tok


def parse_number_token(text: str) -> NumberToken | None:
    """Parsiraj TAČNO JEDAN broj iz kratkog teksta; None ako nije jednoznačno."""
    tokens = _scan_number_tokens(_normalize_math_text(text))
    return tokens[0] if len(tokens) == 1 else None


def _scan_number_tokens(norm: str) -> list[NumberToken]:
    """Svi brojevni tokeni u tekstu, s lijeva na desno, bez preklapanja."""
    found: list[tuple[int, int, NumberToken]] = []

    def _add(m: re.Match, tok: NumberToken):
        span_end, tok = _with_suffix(norm, m.end(), tok)
        for s, e, _t in found:
            if m.start() < e and span_end > s:
                return
        found.append((m.start(), span_end, tok))

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
    kind: str                     # "complement" | "to_improper" | "to_mixed" | "arithmetic" | "simplify" | "rate_*" | "ratio"
    required_form: str | None = None   # "fraction" (nepravi) | "mixed" | None
    unit: str | None = None
    basis: str = ""
    answer_type: str = "number"
    expected_display: str = ""
    unit_policy: str = "not_applicable"
    equivalent_forms_allowed: bool = True
    form_affects_score: bool = False
    tolerance: Fraction | None = None
    target_denominator: int | None = None
    # "high" → smije se presuditi i TAČNO i NETAČNO; "positive_only" → samo
    # potvrda tačnog (kontekst bi mogao mijenjati jedinicu odgovora, pa se
    # različit odgovor NE proglašava netačnim nego unverified).
    confidence: str = "high"
    # CLASS 1 (2026-07-14): vrijednosti tačnih MEĐUKORAKA računa — prefiks
    # rezultati izraza istog prioriteta ("5/12 + 7/12 - 3/12" → {1}). Odgovor
    # jednak međukoraku je correct_step, nikad "netačno".
    step_values: tuple = ()
    # Skupovne operacije (2026-07-19): kanonski elementi očekivanog SKUPA i
    # imenovana operacija ("union" | "intersection" | "complement" | "elements").
    expected_elements: tuple = ()
    set_operation: str = ""


_COMPLEMENT_SIGNAL_RE = re.compile(
    r"\b(nije|nisu|ne\s+bude)\b|\bosta(?:je|lo|la|o|ne)\b|\bpreosta\w*"
)
_QUESTION_SIGNAL_RE = re.compile(r"\b(koji|koja|koliki|kolika|koliko)\b")
_DIO_RE = re.compile(r"\b(dio|dijel\w*|deo|dela)\b")
_TO_IMPROPER_RE = re.compile(r"neprav\w*\s+razlom\w*")
_TO_MIXED_RE = re.compile(r"m[ij]e[sš]?ovit\w*\s+broj\w*")
_CONVERT_RE = re.compile(r"\bpretvori|\bzapisi|\bnapisi|\bpredstavi")
_EXPAND_RE = re.compile(r"\bprosir\w*")
_EXPAND_TARGET_DEN_RE = re.compile(
    r"\b(?:na|do)\s+(?:desn\w+\s+)?nazivnik\w*\s+(\d+)\b"
    r"|\bnazivnik\w*\s+(\d+)\b"
)
_SIMPLIFY_RE = re.compile(r"\bskrati")
# 2026-07-11 (#2): imperativi "pomnozi/saberi/oduzmi/podijeli" uz eksplicitan
# izraz iza njih ("Pomnoži: 7/3 · 2") ranije nisu hvatani pa deterministički
# rezultat nije izveden i model je znao halucinirati (7/3 umjesto 14/3).
_CALC_LEAD_RE = re.compile(
    r"\b(izracunaj|izracunajte|koliko\s+je|odredi\s+vrijednost|"
    r"pomnozi|izmnozi|saberi|zberi|oduzmi|podijeli|podeli)\b"
)
_ADD_WORD_RE = re.compile(r"\b(saberi|zbroji|zbir|suma)\b")
_SUB_WORD_RE = re.compile(r"\b(oduzmi|razlika)\b")
_COMPARE_WORD_RE = re.compile(r"\b(uporedi|usporedi|poredi|uporedjuj)\b")
_GREATER_WORD_RE = re.compile(r"\b(vec\w*|najvec\w*)\b")
_SMALLER_WORD_RE = re.compile(r"\b(manj\w*|najmanj\w*)\b")


_NUM_UNIT = r"(-?\d+(?:[,.]\d+)?)"
_DIST_RE = re.compile(rf"\b{_NUM_UNIT}\s*(km|kilomet(?:ar|ra|ara|araima|rima)?|m|met(?:ar|ra|ara|rima)?)\b")
_TIME_RE = re.compile(rf"\b{_NUM_UNIT}\s*(h|sat(?:i|a|om)?|min(?:uta|ute|ut)?)\b")
_SPEED_RE = re.compile(
    rf"\b{_NUM_UNIT}\s*(?:km\s*/\s*h|km\s*/\s*sat|kmh|kilomet(?:ara|ra)?\s+na\s+sat)\b"
)
_TIME_ASK_RE = re.compile(r"\b(koliko\s+(?:mu\s+|joj\s+)?(?:treba|vremena|sat\w*|minut\w*)|za\s+koliko\s+vremena)\b")
_DIST_ASK_RE = re.compile(r"\b(koliki\s+(?:put|put\s+predje|put\s+prede)|kolika\s+(?:udaljenost|duzina)|koliko\s+(?:km|kilomet\w*|metara))\b")
_SPEED_ASK_RE = re.compile(r"\b(kolika\s+brzin\w*|odredi\s+brzin\w*|izracunaj\s+brzin\w*)\b")
_RATIO_ASK_RE = re.compile(r"\b(omjer|odnos|prema)\b")


def _num_to_fraction(raw: str) -> Fraction:
    return Fraction(raw.replace(",", "."))


def _time_to_minutes(value: Fraction, unit: str) -> Fraction:
    return value * 60 if unit.startswith("sat") or unit == "h" else value


def _minutes_to_unit(minutes: Fraction, unit: str) -> Fraction:
    return minutes / 60 if unit == "sata" else minutes


def _time_unit_from_question(folded: str, fallback: str = "sata") -> str:
    if re.search(r"\bminut|min\b", folded):
        return "minuta"
    if re.search(r"\bsat|sati|sata|h\b", folded):
        return "sata"
    return fallback


def _distance_to_km(value: Fraction, unit: str) -> Fraction:
    return value / 1000 if unit.startswith("m") and unit != "km" else value


def _fmt_fraction(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _fmt_expected(expected: Expected) -> str:
    if expected.expected_display:
        return expected.expected_display
    if expected.kind == "inequality" and expected.required_form:
        return f"x {expected.required_form} {_fmt_fraction(expected.value)}"
    if expected.kind == "yes_no":
        # value: 1 = "da", 0 = "ne"; basis nosi kratko obrazloženje
        word = "da" if expected.value else "ne"
        return f"{word} ({expected.basis})" if expected.basis else word
    if expected.kind == "choice":
        # unit nosi izabranu opciju ("ugao D"), value njenu vrijednost
        label = expected.unit or ""
        return f"{label} ({_fmt_fraction(expected.value)})".strip()
    if expected.kind == "expand" and expected.target_denominator:
        numerator = expected.value * expected.target_denominator
        if numerator.denominator == 1:
            return f"{numerator.numerator}/{expected.target_denominator}"
    if expected.answer_type == "percentage" or expected.unit == "%":
        return f"{_fmt_fraction(expected.value * 100)}%"
    if expected.answer_type == "angle" or expected.unit == "\u00b0":
        return f"{_fmt_fraction(expected.value)}\u00b0"
    base = _fmt_fraction(expected.value)
    return f"{base} {expected.unit}".strip() if expected.unit else base


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
    return Expected(
        value=Fraction(1) - tok.value,
        kind="complement",
        answer_type="rational",
        confidence=confidence,
    )


def _try_conversion(folded: str, tokens: list[NumberToken]) -> Expected | None:
    if not _CONVERT_RE.search(folded):
        return None
    # #2 (2026-07-11): "Pomnoži ... i napiši kao mješoviti broj: 7/3 · 2" NIJE
    # čista konverzija — ima pravu operaciju. Ne uzimaj samo prvi razlomak;
    # prepusti aritmetici (inače bi vratili 7/3 umjesto 14/3).
    if _EXPR_PREFIX_RE.search(folded):
        return None
    if _TO_IMPROPER_RE.search(folded):
        mixed = [t for t in tokens if t.form == "mixed"]
        if len(mixed) == 1:
            return Expected(
                value=mixed[0].value,
                kind="to_improper",
                required_form="fraction",
                answer_type="rational",
                form_affects_score=True,
            )
    if _TO_MIXED_RE.search(folded):
        improper = [t for t in tokens if t.form == "fraction" and abs(t.value) > 1]
        if len(improper) == 1:
            return Expected(
                value=improper[0].value,
                kind="to_mixed",
                required_form="mixed",
                answer_type="mixed_number",
                form_affects_score=True,
            )
    return None


def _try_simplify(folded: str, tokens: list[NumberToken]) -> Expected | None:
    if not _SIMPLIFY_RE.search(folded):
        return None
    fracs = [t for t in tokens if t.form == "fraction"]
    if len(fracs) != 1:
        return None
    return Expected(
        value=fracs[0].value,
        kind="simplify",
        required_form="fraction",
        answer_type="rational",
        form_affects_score=True,
    )


def _try_expand(folded: str, tokens: list[NumberToken]) -> Expected | None:
    if not _EXPAND_RE.search(folded):
        return None
    fracs = [t for t in tokens if t.form == "fraction"]
    if len(fracs) != 1:
        return None
    target = None
    for m in _EXPAND_TARGET_DEN_RE.finditer(folded):
        target = int(next(g for g in m.groups() if g))
        break
    if not target or target <= 0:
        return None
    # Proširivanje = množenje brojnika I nazivnika istim CIJELIM brojem, pa traženi
    # nazivnik mora biti djeljiv originalnim (npr. 3/8 → 24 ✓, 3/8 → 20 ✗). Ako
    # nije, zadatak nema rješenje u traženom obliku → vrati None (validacija ga
    # odbija/regeneriše umjesto da čuvamo pogrešan expected).
    _num, written_den = _fraction_parts(fracs[0].raw)
    if written_den <= 0 or target % written_den != 0:
        return None
    return Expected(
        value=fracs[0].value,
        kind="expand",
        required_form="fraction",
        answer_type="rational",
        form_affects_score=True,
        target_denominator=target,
    )


def _try_worded_fraction_operation(folded: str, tokens: list[NumberToken]) -> Expected | None:
    if len(tokens) != 2:
        return None
    if _ADD_WORD_RE.search(folded):
        return Expected(
            value=tokens[0].value + tokens[1].value,
            kind="arithmetic",
            required_form="fraction",
            answer_type="rational",
        )
    if _SUB_WORD_RE.search(folded):
        return Expected(
            value=tokens[0].value - tokens[1].value,
            kind="arithmetic",
            required_form="fraction",
            answer_type="rational",
        )
    return None


def _try_fraction_comparison(folded: str, tokens: list[NumberToken]) -> Expected | None:
    fracs = [t for t in tokens if t.form == "fraction"]
    if len(fracs) != 2 or not _COMPARE_WORD_RE.search(folded):
        return None
    wants_greater = bool(_GREATER_WORD_RE.search(folded))
    wants_smaller = bool(_SMALLER_WORD_RE.search(folded))
    if wants_greater == wants_smaller:
        return None
    chosen = max(fracs, key=lambda t: t.value) if wants_greater else min(fracs, key=lambda t: t.value)
    return Expected(
        value=chosen.value,
        kind="comparison",
        required_form="fraction",
        answer_type="rational",
    )


# --- A3 (AUD-05/11, 2026-07-13): procenti, stepeni, pretvaranje jedinica -------------

_PERCENT_OF_RE = re.compile(
    r"(\d+(?:[,.]\d+)?)\s*%\s*(?:od|broja)\s*(\d+(?:[,.]\d+)?)"
)
_PERCENT_REVERSE_RE = re.compile(
    r"(\d+(?:[,.]\d+)?)\s*%\s*(?:nekog\s+|nepoznatog\s+)?broja\s+(?:je|iznosi)\s*(\d+(?:[,.]\d+)?)"
)


def _try_percent_of(folded: str, tokens: list[NumberToken]) -> Expected | None:
    """"Koliko je 20% od 50?" → 10; "15% broja je 30, koji je broj?" → 200."""
    m = _PERCENT_REVERSE_RE.search(folded)
    if m:
        pct = _num_to_fraction(m.group(1))
        value = _num_to_fraction(m.group(2))
        if pct > 0:
            return Expected(value=value * 100 / pct, kind="arithmetic")
    m = _PERCENT_OF_RE.search(folded)
    if m:
        pct = _num_to_fraction(m.group(1))
        base = _num_to_fraction(m.group(2))
        return Expected(value=base * pct / 100, kind="arithmetic")
    return None


_PERCENT_FORM_TASK_RE = re.compile(
    r"\b(?:kao|u)\s+(?:procen\w*|postot\w*|%)\b|\bprocen\w*\s+zapis\w*"
)
_DECIMAL_FORM_TASK_RE = re.compile(r"\b(?:kao|u)\s+decimal\w*\b|decimaln\w*\s+zapis\w*")
_FRACTION_FORM_TASK_RE = re.compile(r"\b(?:kao|u)\s+razlom\w*\b|razloma\w*\s+zapis\w*")


def _try_percent_fraction_conversion(folded: str, tokens: list[NumberToken]) -> Expected | None:
    """Simple percent/fraction/decimal representation tasks.

    The stored value is the underlying ratio, so 50%, 0.5 and 1/2 can compare
    exactly; required-form wording is handled separately by ``_judge``.
    """
    has_form_signal = bool(
        _PERCENT_FORM_TASK_RE.search(folded)
        or _DECIMAL_FORM_TASK_RE.search(folded)
        or _FRACTION_FORM_TASK_RE.search(folded)
    )
    has_percent_value = any(t.form == "percentage" for t in tokens)
    if not has_form_signal and not has_percent_value:
        return None
    if len(tokens) != 1:
        return None
    tok = tokens[0]
    if tok.form not in ("fraction", "decimal", "integer", "percentage"):
        return None
    required = None
    answer_type = "number"
    unit = None
    if _PERCENT_FORM_TASK_RE.search(folded):
        required = "percentage"
        answer_type = "percentage"
        unit = "%"
    elif _DECIMAL_FORM_TASK_RE.search(folded):
        required = "decimal"
        answer_type = "decimal"
    elif _FRACTION_FORM_TASK_RE.search(folded):
        required = "fraction"
        answer_type = "rational"
    else:
        answer_type = "percentage" if tok.form == "percentage" else "number"
        unit = "%" if tok.form == "percentage" else None
    return Expected(
        value=tok.value,
        kind="percentage" if answer_type == "percentage" else "conversion",
        required_form=required,
        unit=unit,
        answer_type=answer_type,
        unit_policy="not_applicable",
        form_affects_score=bool(required),
    )


def _try_angle_arithmetic(folded: str, norm_text: str) -> Expected | None:
    """Angle arithmetic where the degree mark is notation, not a new unit scale."""
    if "\u00b0" not in norm_text and not re.search(r"\bug(ao|la|lu|lovi|love|lova)\b|\bstepen", folded):
        return None
    stripped = re.sub(r"\s*(?:\u00b0|stepen\w*)", "", norm_text, flags=re.IGNORECASE)
    expected = _try_arithmetic(_fold(stripped), stripped)
    if expected is None:
        return None
    expected.kind = "angle"
    expected.unit = "\u00b0"
    expected.answer_type = "angle"
    expected.unit_policy = "optional_if_clear"
    return expected


_TRIANGLE_WORD_RE = re.compile(r"\b(trougl\w*|trokut\w*|triangle\w*)\b")
_MISSING_TRIANGLE_ANGLE_RE = re.compile(
    r"\b(?:trec\w*|preostal\w*|drug\w*|other|missing|unknown|find|nepoznat\w*|koliki|kolika|odredi|nadj?i|izracunaj)\b"
)
_DEGREE_VALUE_RE = re.compile(r"(-?\d+(?:[,.]\d+)?)\s*(?:\u00b0|stepen\w*)", re.IGNORECASE)


def _try_triangle_missing_angle(folded: str, norm_text: str) -> Expected | None:
    """Missing angle in a triangle: two known angles imply the third.

    Conservative by design: requires triangle wording, a prompt for the missing
    angle, and at least two explicit degree values (or one plus right-triangle
    wording).
    """
    if not _TRIANGLE_WORD_RE.search(folded):
        return None
    if not _MISSING_TRIANGLE_ANGLE_RE.search(folded):
        return None
    values: list[Fraction] = []
    for m in _DEGREE_VALUE_RE.finditer(norm_text):
        try:
            values.append(Fraction(m.group(1).replace(",", ".")))
        except (ValueError, ZeroDivisionError):
            return None
    if len(values) == 1 and re.search(r"\bpravougl\w*|pravokut\w*|right\s+triangle|prav\w*\s+ug\w*\b", folded):
        values.append(Fraction(90))
    if len(values) < 2:
        return None
    known = values[:2]
    missing = Fraction(180) - sum(known)
    if missing <= 0 or missing >= 180:
        return None
    return Expected(
        value=missing,
        kind="angle",
        unit="\u00b0",
        answer_type="angle",
        unit_policy="optional_if_clear",
    )


_ARC_TOPIC_RE = re.compile(r"\b(kruzn\w*|kruznic\w*|krug\w*)\b.*\b(luk\w*|luka)\b|\b(luk\w*|luka)\b.*\b(kruzn\w*|kruznic\w*|krug\w*)\b")
_ARC_UNIT_WORD = r"(kilomet\w*|centimet\w*|milimet\w*|met(?:ar|ra|ara|rima)?|km|dm|cm|mm|m)\b"
_RADIUS_RE = re.compile(
    rf"(?:r\s*=\s*|poluprecnik\w*(?:\s+kruznic\w*)?\s*(?:je|iznosi|=)?\s*|"
    rf"polumjer\w*(?:\s+kruznic\w*)?\s*(?:je|iznosi|=)?\s*)"
    rf"(-?\d+(?:[,.]\d+)?)\s*{_ARC_UNIT_WORD}",
    re.IGNORECASE,
)
_CENTRAL_ANGLE_RE = re.compile(
    r"(?:centraln\w+\s+)?ug(?:ao|la|lu)?\w*\s*(?:je|iznosi|=)?\s*"
    r"(-?\d+(?:[,.]\d+)?)\s*(?:\u00b0|stepen\w*)",
    re.IGNORECASE,
)


def _try_arc_length(folded: str, norm_text: str) -> Expected | None:
    """Arc length from radius and central angle.

    Expected stores a decimal approximation because the checker core is rational;
    ``expected_display`` keeps the exact pi form visible for logging/UI.
    """
    if not _ARC_TOPIC_RE.search(folded):
        return None
    if not re.search(r"\b(duzin\w*|duljin\w*|izracunaj|odredi|kolik\w*)\b", folded):
        return None
    radius_m = _RADIUS_RE.search(norm_text)
    angle_m = _CENTRAL_ANGLE_RE.search(norm_text) or _DEGREE_VALUE_RE.search(norm_text)
    if not (radius_m and angle_m):
        return None
    try:
        radius = Fraction(radius_m.group(1).replace(",", "."))
        angle = Fraction(angle_m.group(1).replace(",", "."))
    except (ValueError, ZeroDivisionError):
        return None
    if radius <= 0 or angle <= 0 or angle > 360:
        return None
    unit = _unit_key(radius_m.group(2) or "")
    if unit not in {"mm", "cm", "dm", "m", "km"}:
        return None
    pi_coeff = Fraction(2) * radius * angle / Fraction(360)
    approx = Fraction(round(float(pi_coeff) * 3.141592653589793 * 100), 100)
    if pi_coeff.denominator == 1:
        exact = f"{pi_coeff.numerator}\u03c0"
    else:
        exact = f"{pi_coeff.numerator}\u03c0/{pi_coeff.denominator}"
    return Expected(
        value=approx,
        kind="arc_length",
        unit=unit,
        answer_type="measurement",
        expected_display=f"{exact} {unit} \u2248 {_fmt_fraction(approx)} {unit}",
        unit_policy="required",
        tolerance=Fraction(2, 100),
        confidence="high",
    )


def _try_tangent_radius_angle(folded: str, norm_text: str) -> Expected | None:
    """Radius and tangent at the point of tangency are perpendicular."""
    if "tangent" not in folded:
        return None
    if not re.search(r"\bradijus|poluprecnik|polumjer\b", folded):
        return None
    if not re.search(r"\bugao|ugl\w*|grade|zaklap\w*|koliki|odredi|izracunaj", folded):
        return None
    return Expected(
        value=Fraction(90),
        kind="angle",
        unit="\u00b0",
        answer_type="angle",
        unit_policy="optional_if_clear",
    )


_POWER_RE = re.compile(r"(-?\d+(?:[,.]\d+)?|-?\d+\s*/\s*\d+)\s*(?:\^|\*\*)\s*(\d)")
_POWER_WORD_RE = re.compile(
    r"(?:kvadrat\s+broja|kvadriraj)\s+(-?\d+(?:[,.]\d+)?)"
    r"|(-?\d+(?:[,.]\d+)?)\s+na\s+(kvadrat|kub|drugu|trecu)"
)


def _try_power(folded: str, tokens: list[NumberToken]) -> Expected | None:
    """"3^2", "5 na kvadrat", "kvadrat broja 4" → mali stepeni (eksponent ≤ 6).

    Konzervativno: stepen mora biti JEDINI račun u stavci ("2^3 + 1" se ne
    presuđuje — kombinovani izrazi bi dali pogrešan expected)."""
    m = _POWER_RE.search(folded)
    if m:
        exp = int(m.group(2))
        if exp > 6:
            return None
        # kombinovani izraz? van stepena ne smije ostati ni jedna cifra
        rest = folded[:m.start()] + folded[m.end():]
        if re.search(r"\d", rest):
            return None
        toks = _scan_number_tokens(m.group(1))
        if len(toks) == 1:
            return Expected(value=toks[0].value ** exp, kind="arithmetic")
        return None
    m = _POWER_WORD_RE.search(folded)
    if m:
        if m.group(1) is not None:
            return Expected(value=_num_to_fraction(m.group(1)) ** 2, kind="arithmetic")
        base = _num_to_fraction(m.group(2))
        exp = 3 if m.group(3) in ("kub", "trecu") else 2
        return Expected(value=base ** exp, kind="arithmetic")
    return None


# Faktor prema OSNOVNOJ jedinici grupe (dužina→mm, masa→g, vrijeme→min).
_UNIT_FACTORS = {
    "km": ("len", Fraction(1_000_000)), "kilometar": ("len", Fraction(1_000_000)),
    "m": ("len", Fraction(1000)), "metar": ("len", Fraction(1000)),
    "dm": ("len", Fraction(100)),
    "cm": ("len", Fraction(10)), "centimetar": ("len", Fraction(10)),
    "mm": ("len", Fraction(1)), "milimetar": ("len", Fraction(1)),
    "t": ("mass", Fraction(1_000_000)), "tona": ("mass", Fraction(1_000_000)),
    "kg": ("mass", Fraction(1000)), "kilogram": ("mass", Fraction(1000)),
    "dag": ("mass", Fraction(10)), "dekagram": ("mass", Fraction(10)),
    "g": ("mass", Fraction(1)), "gram": ("mass", Fraction(1)),
    "h": ("time", Fraction(60)), "sat": ("time", Fraction(60)),
    "min": ("time", Fraction(1)), "minut": ("time", Fraction(1)), "minuta": ("time", Fraction(1)),
}
# Duži oblici PRIJE kraćih (inače "m" pojede početak od "min"/"mm") + \b iza.
_UNIT_WORD = (
    r"(kilomet\w*|centimet\w*|milimet\w*|met(?:ar|ra|ara|rima)?|"
    r"kilogram\w*|dekagram\w*|gram\w*|ton\w*|minut\w*|sat\w*|"
    r"km|dm|cm|mm|kg|dag|min|m|t|g|h)\b"
)
_UNIT_CONVERT_RE = re.compile(
    rf"(?:pretvori|izrazi|koliko\s+(?:je|ima|iznosi))\b.{{0,30}}?"
    rf"(-?\d+(?:[,.]\d+)?)\s*{_UNIT_WORD}\s+(?:u|ima\s+u)\s+{_UNIT_WORD}"
)


def _unit_key(raw: str) -> str | None:
    raw = raw.lower()
    if raw in _UNIT_FACTORS:
        return raw
    for stem, key in (
        ("kilomet", "km"), ("centimet", "cm"), ("milimet", "mm"), ("met", "m"),
        ("kilogram", "kg"), ("dekagram", "dag"), ("gram", "g"), ("ton", "t"),
        ("sat", "h"), ("minut", "min"),
    ):
        if raw.startswith(stem):
            return key
    return None


def _unit_factor(unit: str | None) -> tuple[str, Fraction] | None:
    key = _unit_key(unit or "")
    if not key:
        return None
    return _UNIT_FACTORS.get(key)


def _judge_measurement(expected: Expected, given: NumberToken) -> str | None:
    if expected.answer_type != "measurement" or not expected.unit:
        return None
    exp = _unit_factor(expected.unit)
    if exp is None:
        return None
    exp_group, exp_factor = exp
    exp_base = expected.value * exp_factor
    if given.unrecognized_unit:
        return "wrong_unit" if given.value == expected.value else "incorrect"
    if given.unit:
        stu = _unit_factor(given.unit)
        if stu is None:
            return "wrong_unit" if given.value == expected.value else "incorrect"
        stu_group, stu_factor = stu
        if stu_group != exp_group:
            return "wrong_unit"
        if given.value * stu_factor == exp_base:
            exp_key = _unit_key(expected.unit)
            stu_key = _unit_key(given.unit)
            return "correct" if stu_key == exp_key and given.value == expected.value else "correct_equivalent_form"
        return "wrong_unit" if given.value == expected.value else "incorrect"
    if _values_match(expected, given):
        return "correct_missing_unit"
    return "incorrect"


def _judge_angle(expected: Expected, given: NumberToken) -> str | None:
    if expected.answer_type != "angle":
        return None
    if not _values_match(expected, given):
        return "incorrect"
    if given.unrecognized_unit:
        return "wrong_unit"
    if given.notation == "degree" or given.unit == "\u00b0":
        return "correct"
    if given.unit:
        return "wrong_unit"
    return "correct_missing_notation"


def _judge_percentage(expected: Expected, given: NumberToken) -> str | None:
    if expected.answer_type != "percentage":
        return None
    if not _values_match(expected, given):
        return "incorrect"
    if expected.required_form == "percentage" and given.form != "percentage":
        return "correct_value_wrong_form"
    return "correct" if given.form == "percentage" else "correct_equivalent_form"


def _compact_math_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").replace(",", "."))


def _equivalent_form_verdict(expected: Expected, given: NumberToken) -> str | None:
    if not expected.equivalent_forms_allowed or expected.answer_type != "rational":
        return None
    if given.form not in ("fraction", "mixed", "decimal", "integer", "percentage"):
        return None
    expected_raw = _compact_math_text(_fmt_expected(expected))
    given_raw = _compact_math_text(given.raw)
    if expected_raw and given_raw and expected_raw != given_raw:
        return "correct_equivalent_form"
    return None


def _try_unit_conversion(folded: str, tokens: list[NumberToken]) -> Expected | None:
    """"Pretvori 3 m u cm" → 300 cm; dužina/masa/vrijeme unutar iste grupe."""
    m = _UNIT_CONVERT_RE.search(folded)
    if not m:
        return None
    src = _unit_key(m.group(2))
    dst = _unit_key(m.group(3))
    if not src or not dst or src == dst:
        return None
    src_group, src_f = _UNIT_FACTORS[src]
    dst_group, dst_f = _UNIT_FACTORS[dst]
    if src_group != dst_group:
        return None
    value = _num_to_fraction(m.group(1)) * src_f / dst_f
    return Expected(
        value=value,
        kind="measurement",
        unit=dst,
        answer_type="measurement",
        unit_policy="optional_if_clear",
    )


_NZD_RE = re.compile(r"najvec\w*\s+zajednick\w*\s+dj?el\w*|\bnzd\b")
_NZS_RE = re.compile(r"najmanj\w*\s+zajednick\w*\s+sadrz\w*|\bnzs\b")


def _try_gcd_lcm(folded: str, tokens: list[NumberToken]) -> Expected | None:
    """"Koji je NZD brojeva 24 i 36?" → 12; NZS analogno (math.gcd/lcm).

    Konzervativno: tačno dva cijela pozitivna broja u stavci."""
    wants_gcd = bool(_NZD_RE.search(folded))
    wants_lcm = bool(_NZS_RE.search(folded))
    if wants_gcd == wants_lcm:          # ni jedno ni oboje → ne diramo
        return None
    ints = [t for t in tokens if t.form == "integer" and t.value > 0]
    if len(ints) != 2:
        return None
    from math import gcd
    a, b = int(ints[0].value), int(ints[1].value)
    if wants_gcd:
        return Expected(value=Fraction(gcd(a, b)), kind="arithmetic")
    return Expected(value=Fraction(a * b // gcd(a, b)), kind="arithmetic")


def _try_rate_or_ratio(folded: str, norm_text: str) -> Expected | None:
    distances = [
        (m.start(), _distance_to_km(_num_to_fraction(m.group(1)), m.group(2)))
        for m in _DIST_RE.finditer(norm_text)
    ]
    times = [
        (m.start(), _time_to_minutes(_num_to_fraction(m.group(1)), m.group(2)))
        for m in _TIME_RE.finditer(norm_text)
    ]
    speeds = [
        (m.start(), _num_to_fraction(m.group(1)))
        for m in _SPEED_RE.finditer(norm_text)
    ]

    # "Odredi omjer 40 minuta prema 2 sata" -> 40/120 = 1/3.
    if _RATIO_ASK_RE.search(folded) and len(times) >= 2:
        first = times[0][1]
        second = times[1][1]
        if second:
            value = first / second
            return Expected(
                value=value,
                kind="ratio",
                required_form="fraction",
                answer_type="rational",
                form_affects_score=True,
                basis=f"{_fmt_fraction(first)} minuta : {_fmt_fraction(second)} minuta = {_fmt_fraction(value)}",
            )

    if _TIME_ASK_RE.search(folded):
        unit = _time_unit_from_question(folded)
        # "65 km za 1 sat, koliko sati za 260 km" -> 260 * 60 / 65 = 240 min = 4 sata.
        if len(distances) >= 2 and times:
            base_km = distances[0][1]
            target_km = distances[-1][1]
            base_minutes = times[0][1]
            if base_km and base_minutes:
                minutes = target_km * base_minutes / base_km
                value = _minutes_to_unit(minutes, unit)
                basis = (
                    f"{_fmt_fraction(target_km)} : "
                    f"{_fmt_fraction(base_km / (base_minutes / 60))} = "
                    f"{_fmt_fraction(value)} {unit}"
                    if base_minutes
                    else ""
                )
                return Expected(value=value, kind="rate_time", unit=unit, basis=basis)
        # "260 km brzinom 65 km/h, koliko vremena" -> 260 / 65 = 4 sata.
        if distances and speeds:
            target_km = distances[-1][1]
            speed = speeds[0][1]
            if speed:
                hours = target_km / speed
                value = _minutes_to_unit(hours * 60, unit)
                return Expected(
                    value=value,
                    kind="rate_time",
                    unit=unit,
                    basis=f"{_fmt_fraction(target_km)} : {_fmt_fraction(speed)} = {_fmt_fraction(value)} {unit}",
                )

    if _DIST_ASK_RE.search(folded) and speeds and times:
        speed = speeds[0][1]
        hours = times[0][1] / 60
        value = speed * hours
        return Expected(
            value=value,
            kind="rate_distance",
            unit="km",
            basis=f"{_fmt_fraction(speed)} · {_fmt_fraction(hours)} = {_fmt_fraction(value)} km",
        )

    if _SPEED_ASK_RE.search(folded) and distances and times:
        km = distances[-1][1]
        hours = times[0][1] / 60
        if hours:
            value = km / hours
            return Expected(
                value=value,
                kind="rate_speed",
                unit="km/h",
                basis=f"{_fmt_fraction(km)} : {_fmt_fraction(hours)} = {_fmt_fraction(value)} km/h",
            )

    return None


_EXPR_PREFIX_RE = re.compile(
    r"[\s:=]*((?:-?\d+(?:\s+\d+\s*/\s*\d+|\s*/\s*\d+|,\d+)?\s*[+\-*:]\s*)+"
    r"-?\d+(?:\s+\d+\s*/\s*\d+|\s*/\s*\d+|,\d+)?)"
)
_TASK_LEAD_STRIP_RE = re.compile(r"^\s*(zadatak(\s+za\s+vjezbu)?|primjer)\s*[:.\-]?\s*", re.IGNORECASE)

# N1 (2026-07-12): dječiji riječi-operatori → simboli (samo za detekciju izraza
# u poruci; konzervativno — samo jednoznačne fraze).
_WORD_OPS = (
    (re.compile(r"\bpodijeljeno\s+sa?\b|\bkroz\b"), ":"),
    (re.compile(r"\bputa\b|\bpomnozeno\s+sa?\b"), "*"),
    (re.compile(r"\bplus\b"), "+"),
    (re.compile(r"\bminus\b(?=\s*\d)"), "-"),   # samo ispred broja ("minus 5", ne "minus brojevi")
)


def extract_task_expressions(text: str, limit: int = 5) -> list[str]:
    """Nađi konkretne računske izraze u učenikovoj poruci ("evo zadatak: 3/4 + 5/6",
    "1/2+1/4, 2/3+1/6"). Vraća normalizovane izraze (·→*, riječi-operatori→simboli)
    ili []. Koristi se da bot PREUZME učenikov zadatak umjesto da izmišlja svoj."""
    norm = _normalize_math_text(text or "")
    folded = _fold(norm)
    for pat, sym in _WORD_OPS:
        folded = pat.sub(f" {sym} ", folded)
    out: list[str] = []
    for m in _EXPR_PREFIX_RE.finditer(folded):
        expr = re.sub(r"\s+", " ", m.group(1).strip())
        if expr and expr not in out:
            out.append(expr)
        if len(out) >= limit:
            break
    return out


def _try_arithmetic(folded: str, norm_text: str) -> Expected | None:
    """"Izračunaj: 1/2 + 1/3" → 5/6; isto i kada je zadatak SAMO izraz
    ("3/4 · 2/5"). Bez zagrada; množenje i dijeljenje prije sabiranja i
    oduzimanja (školski prioritet)."""
    m = _CALC_LEAD_RE.search(folded)
    if m:
        tail = norm_text[m.end():]
        expr_m = _EXPR_PREFIX_RE.match(tail)
        if not expr_m:
            expr_m = _EXPR_PREFIX_RE.search(tail)
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
    answer_type = "decimal" if re.search(r"\d+,\d+", expr_m.group(1)) else "rational"
    return Expected(
        value=value,
        kind="arithmetic",
        answer_type=answer_type,
        step_values=_expr_step_values(expr_m.group(1), value),
    )


_NUMBER_CHUNK_RE = re.compile(
    r"\s*(-?\d+\s+\d+\s*/\s*\d+|-?\d+\s*/\s*\d+|-?\d+,\d+|-?\d+)"
)
_OP_CHUNK_RE = re.compile(r"\s*([+\-*:])")


def _parse_expr_items(expr: str) -> list | None:
    """Izraz → naizmjenična lista [vrijednost, op, vrijednost, ...]; None ako
    nije čist niz broj-operator-broj."""
    pos = 0
    items: list = []
    expect_number = True
    m = None
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
    return items


def _expr_step_values(expr: str, final: Fraction) -> tuple:
    """Vrijednosti tačnih MEĐUKORAKA računa: kumulativni prefiksi izraza,
    s lijeva na desno ("5/12 + 7/12 - 3/12" → (1,)).

    Samo kad su SVI operatori istog prioriteta (svi aditivni ili svi
    multiplikativni) — kod miješanog prioriteta prefiks slijeva NIJE validan
    međukorak. Konačan rezultat se isključuje (to je pun odgovor)."""
    items = _parse_expr_items(expr)
    if not items or len(items) < 5:          # bar dva operatora → postoji međukorak
        return ()
    ops = {items[i] for i in range(1, len(items), 2)}
    if not (ops <= {"+", "-"} or ops <= {"*", ":"}):
        return ()
    steps: list[Fraction] = []
    acc = items[0]
    try:
        for i in range(1, len(items) - 2, 2):
            op, val = items[i], items[i + 1]
            if op == "+":
                acc = acc + val
            elif op == "-":
                acc = acc - val
            elif op == "*":
                acc = acc * val
            else:
                acc = acc / val
            steps.append(acc)
    except ZeroDivisionError:
        return ()
    return tuple(s for s in steps if s != final)


def _eval_expr(expr: str) -> Fraction | None:
    """Evaluiraj niz broj-operator-broj... sa prioritetom (*, :) pa (+, -)."""
    items = _parse_expr_items(expr)
    if items is None:
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


# --- Proste linearne jednačine s jednom nepoznatom (x) -----------------------------
# Opšta klasa (ne primjeri): svaka jednačina koja stane u ovu gramatiku rješava se
# egzaktno preko Fraction, bez obzira na temu/razred/formulaciju. Ako parsiranje
# nije sigurno → None (nikad izmišljena presuda).

_EQ_LABEL_RE = re.compile(
    r"^\s*(?:"
    # riješi [ne]jednačinu [s razlomkom/razlomcima]
    r"rij?e[sš]?i(?:\s+(?:jedna[cč]inu|nejedna[cč]inu)(?:\s+sa?\s+razlom\w*)?)?"
    r"|odredi\s+x|izra[cč]unaj\s+x|nadj?i\s+x"
    r"|nadj?i\s+nepoznat\w*\s+broj"
    r"|kolik[oa]\s+je\s+x|kolika\s+je\s+vrijednost\s+x"
    r")\s*[:.\-]?\s*",
    re.IGNORECASE,
)
# Poslije skidanja labela: čista jednačina smije sadržati samo matematičke znakove
# i nepoznatu x (bilo koje drugo slovo/riječ → nije čista jednačina → ne diramo).
_CLEAN_EQ_RE = re.compile(r"^[0-9x/*():+\-,\s=]+$")
_NUM_FACTOR_RE = re.compile(r"^-?\d+(?:,\d+)?$")


def _paren_strip_numbers(expr: str) -> str:
    """"(2/3)" → "2/3" (samo zagrada oko čistog broja/razlomka)."""
    prev = None
    while prev != expr:
        prev = expr
        expr = re.sub(r"\((-?\d+(?:,\d+)?(?:/-?\d+(?:,\d+)?)?)\)", r"\1", expr)
    return expr


def _mixed_to_improper(expr: str) -> str:
    """"4 1/4" → "17/4" prije parsiranja strane jednačine/nejednačine.

    ``_insert_implicit_mult`` briše razmake, pa bi mješoviti broj bez ove
    konverzije postao "41/4" — pogrešna vrijednost koja tačan odgovor
    proglasi netačnim."""
    def repl(m: re.Match) -> str:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if den == 0:
            return m.group(0)
        sign = -1 if whole < 0 else 1
        value = Fraction(whole) + sign * Fraction(num, den)
        return f"{value.numerator}/{value.denominator}"

    return _MIXED_RE.sub(repl, expr)


def _insert_implicit_mult(expr: str) -> str:
    """"2/3 x"→"2/3*x", "(2/3)x"→"...*x", "3x"→"3*x", ")(" → ")*("."""
    expr = expr.replace(" ", "")
    expr = re.sub(r"(?<=[0-9)])(?=[x(])", "*", expr)
    expr = re.sub(r"(?<=x)(?=[0-9(])", "*", expr)
    expr = re.sub(r"(?<=\))(?=\()", "*", expr)
    return expr


def _num_factor(tok: str) -> Fraction | None:
    if not _NUM_FACTOR_RE.match(tok):
        return None
    return Fraction(tok.replace(",", "."))


def _eval_linear_term(body: str) -> tuple[Fraction, int] | None:
    """Jedan proizvod faktora → (koeficijent, stepen_x); None ako nepodržano."""
    if not body:
        return None
    parts = re.split(r"([*/])", body)
    factors = parts[0::2]
    ops = parts[1::2]
    coeff = Fraction(1)
    xdeg = 0
    for i, factor in enumerate(factors):
        op = "*" if i == 0 else ops[i - 1]
        if factor == "x":
            if op == "/":
                return None                       # x u nazivniku — nepodržano
            xdeg += 1
            continue
        val = _num_factor(factor)
        if val is None:
            return None
        if op == "*":
            coeff *= val
        elif val == 0:
            return None                           # dijeljenje nulom
        else:
            coeff /= val
    if xdeg > 1:
        return None                               # nelinearno (x·x)
    return coeff, xdeg


def _parse_linear_side(side: str) -> tuple[Fraction, Fraction] | None:
    """Strana jednačine → (a, b) za a·x + b; None ako nije podržano."""
    side = _mixed_to_improper(side)
    side = _insert_implicit_mult(_paren_strip_numbers(side.replace(":", "/")))
    if not side or "(" in side or ")" in side:
        return None
    if side[0] not in "+-":
        side = "+" + side
    a = Fraction(0)
    b = Fraction(0)
    matched_end = 0
    for m in re.finditer(r"[+-][^+-]+", side):
        if m.start() != matched_end:
            return None
        matched_end = m.end()
        sign = -1 if m.group(0)[0] == "-" else 1
        term = _eval_linear_term(m.group(0)[1:])
        if term is None:
            return None
        coeff, xdeg = term
        if xdeg == 1:
            a += sign * coeff
        else:
            b += sign * coeff
    if matched_end != len(side):
        return None
    return a, b


def _try_linear_equation(folded: str, norm_text: str) -> Expected | None:
    """"Riješi: (2/3)x = 8/9" → x = 4/3. Rješava a₁x+b₁ = a₂x+b₂ egzaktno."""
    m = _EQ_LABEL_RE.match(folded)
    expr = norm_text[m.end():] if m else norm_text
    expr = expr.strip().rstrip(".!?").strip()
    if expr.count("=") != 1 or "x" not in expr:
        return None
    if not _CLEAN_EQ_RE.match(expr):
        return None
    lhs, rhs = expr.split("=")
    left = _parse_linear_side(lhs)
    right = _parse_linear_side(rhs)
    if left is None or right is None:
        return None
    a = left[0] - right[0]           # koeficijent uz x
    b = right[1] - left[1]           # slobodni članovi na desnu stranu
    if a == 0:
        return None                  # nema jedinstvenog rješenja
    return Expected(
        value=b / a,
        kind="equation",
        answer_type="equation_solution",
        confidence="high",
    )


# --- Proste linearne nejednačine s jednom nepoznatom (x) ---------------------------
# Rješenje se svodi na kanonski oblik "x OP granica" (OP ∈ {<, <=, >, >=}) i
# poredi i po ZNAKU i po GRANICI. Znak se OKREĆE pri dijeljenju negativnim
# koeficijentom uz x. Ekvivalentni oblici ("x < 8" == "8 > x") daju isti kanon.
# Namjerno ODVOJENO od jednačina: ovdje presuda nosi operator, a ne samo broj,
# pa NE ide kroz numerički _judge (koji operator ne poznaje).

_INEQ_OP_RE = re.compile(r"<=|>=|<|>")
_CLEAN_INEQ_SIDE_RE = re.compile(r"^[0-9x/*():+\-,\s]+$")
_FLIP_OP = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}

# Konačan oblik rješenja: nepoznata SAMA na jednoj strani, čist broj (cijeli,
# decimalni, razlomak ili mješoviti) na drugoj. "2x < 12" NIJE konačan oblik.
_BARE_NUMBER_RE = re.compile(
    r"^\s*-?\d+(?:,\d+)?(?:\s*/\s*\d+)?(?:\s+\d+\s*/\s*\d+)?\s*$"
)


def _is_final_form(lhs: str, rhs: str) -> bool:
    """Da li je "lhs OP rhs" konačan oblik ("x = 17/4", "x < 6", "6 > x")?"""
    left, right = lhs.strip(), rhs.strip()
    if left == "x" and "x" not in right and _BARE_NUMBER_RE.match(right):
        return True
    if right == "x" and "x" not in left and _BARE_NUMBER_RE.match(left):
        return True
    return False


def _normalize_ineq_ops(text: str) -> str:
    """Ujednači zapis znakova nejednakosti (unicode/LaTeX/„=<") na <,<=,>,>=."""
    return (
        (text or "")
        .replace("≤", "<=").replace("≥", ">=")
        .replace("\\leq", "<=").replace("\\geq", ">=")
        .replace("\\le", "<=").replace("\\ge", ">=")
        .replace("\\lt", "<").replace("\\gt", ">")
        .replace("=<", "<=").replace("=>", ">=")
    )


def _solve_linear_inequality(math_text: str) -> tuple[str, Fraction, bool] | None:
    """Linearna nejednačina po x → (kanonski_op, granica, konačan_oblik);
    None ako nepodržano.

    ``math_text`` je već prošao ``_normalize_math_text`` (frac, decimalni zarez).
    """
    expr = _normalize_ineq_ops(math_text).strip().rstrip(".!?").strip()
    ops = _INEQ_OP_RE.findall(expr)
    if len(ops) != 1 or "x" not in expr:
        return None                       # bez znaka, jednakost, ili složeno
    op = ops[0]
    m = _INEQ_OP_RE.search(expr)
    lhs, rhs = expr[:m.start()], expr[m.end():]
    if not _CLEAN_INEQ_SIDE_RE.match(lhs) or not _CLEAN_INEQ_SIDE_RE.match(rhs):
        return None
    left = _parse_linear_side(lhs)
    right = _parse_linear_side(rhs)
    if left is None or right is None:
        return None
    a = left[0] - right[0]                 # koeficijent uz x
    b = left[1] - right[1]                 # slobodni član (na lijevoj strani)
    if a == 0:
        return None                        # nema x → nije nejednačina po x
    # a·x + b OP 0  →  x (OP ili OP-flip pri a<0) (-b/a)
    canon = op if a > 0 else _FLIP_OP[op]
    return canon, -b / a, _is_final_form(lhs, rhs)


def _check_single_inequality(task_text: str, student_text: str) -> "CheckResult | None":
    """Puna presuda za jednu podržanu nejednačinu; None → nije nejednačina ili
    učenikov odgovor nije parsiran kao nejednačina (ostavi opštem toku).

    CLASS 1 (2026-07-14): ekvivalentna tvrdnja koja NIJE konačan oblik
    ("2x < 12" za zadatak "2x - 5 < 7") je TAČAN MEĐUKORAK (``correct_step``),
    nikad "netačno" — učenik je na dobrom putu, samo nije dovršio."""
    norm_task = _normalize_math_text(task_text)
    m = _EQ_LABEL_RE.match(_fold(norm_task))
    task_expr = norm_task[m.end():] if m else norm_task
    expected = _solve_linear_inequality(task_expr)
    if expected is None:
        return None
    student = _solve_linear_inequality(_normalize_math_text(student_text))
    exp_op, exp_val, _task_final = expected
    expected_obj = Expected(
        value=exp_val,
        kind="inequality",
        required_form=exp_op,
        answer_type="inequality_solution",
        confidence="high",
    )
    if student is None:
        number = parse_number_token(student_text)
        if number is None:
            return None                    # odgovor nije jasna nejednačina po x
        satisfies = {
            "<": number.value < exp_val,
            "<=": number.value <= exp_val,
            ">": number.value > exp_val,
            ">=": number.value >= exp_val,
        }[exp_op]
        verdict = "incomplete" if satisfies else "incorrect"
        return CheckResult(checkable=True, items=[
            ItemCheck(n=1, task=task_text.strip()[:200], expected=expected_obj,
                      given=number, verdict=verdict),
        ])
    stu_op, stu_val, stu_final = student
    raw_student = re.sub(r"\s+", " ", _student_statement_raw(student_text))[:80]
    given = NumberToken(
        value=stu_val, form="inequality",
        raw=raw_student or f"x {stu_op} {_fmt_fraction(stu_val)}",
    )
    if stu_op == exp_op and stu_val == exp_val:
        verdict = "correct" if stu_final else "correct_step"
    else:
        verdict = "incorrect"
    return CheckResult(checkable=True, items=[
        ItemCheck(n=1, task=task_text.strip()[:200], expected=expected_obj,
                  given=given, verdict=verdict),
    ])


def _student_statement_raw(text: str) -> str:
    """Kratak, čitljiv zapis učenikove tvrdnje za prompt ("2x<12", "x = 17/4")."""
    return _normalize_ineq_ops(_normalize_math_text(text or "")).strip().rstrip(".!?")


def _solve_student_equation(student_text: str) -> tuple[Fraction, bool] | None:
    """Učenikova tvrdnja-jednačina po x → (rješenje, konačan_oblik); None ako
    poruka nije čista jednačina (tada odlučuje opšti tok)."""
    norm = _normalize_math_text(student_text)
    m = _EQ_LABEL_RE.match(_fold(norm))
    expr = norm[m.end():] if m else norm
    expr = expr.strip().rstrip(".!?").strip()
    if expr.count("=") != 1 or "x" not in expr:
        return None
    if not _CLEAN_EQ_RE.match(expr):
        return None
    lhs, rhs = expr.split("=")
    left = _parse_linear_side(lhs)
    right = _parse_linear_side(rhs)
    if left is None or right is None:
        return None
    a = left[0] - right[0]
    b = right[1] - left[1]
    if a == 0:
        return None                        # "3 = 3" i sl. — nema x rješenja
    return b / a, _is_final_form(lhs, rhs)


def _check_single_equation(task_text: str, student_text: str) -> "CheckResult | None":
    """Puna presuda kada je zadatak linearna jednačina po x, a učenikov odgovor
    je TVRDNJA-JEDNAČINA ("x = 4 1/4", "x = 5 - 3/4", "2x = 12").

    Učenikova jednačina se RIJEŠI pa uporedi sa rješenjem zadatka:
    - isto rješenje + konačan oblik ("x = broj")  → correct
    - isto rješenje, nedovršen oblik ("2x = 12")  → correct_step (tačan međukorak)
    - različito rješenje                          → incorrect (tvrdnja je
      matematički nespojiva sa zadatkom — transformacija je pogrešna)."""
    norm_task = _normalize_math_text(task_text)
    expected = _try_linear_equation(_fold(norm_task), norm_task)
    if expected is None:
        return None
    student = _solve_student_equation(student_text)
    if student is None:
        return None
    stu_val, stu_final = student
    raw_student = re.sub(r"\s+", " ", _student_statement_raw(student_text))[:80]
    given = NumberToken(value=stu_val, form="equation", raw=raw_student)
    if stu_val == expected.value:
        verdict = "correct" if stu_final else "correct_step"
    else:
        verdict = "incorrect"
    return CheckResult(checkable=True, items=[
        ItemCheck(n=1, task=task_text.strip()[:200], expected=expected,
                  given=given, verdict=verdict),
    ])


# --- Skupovi i skupovne operacije (unija, presjek, komplement, elementi) ------------
# Opšta klasa: definisani skupovi "A = {..}" + operacija → deterministički rezultat
# kao SKUP. Poređenje je PRAVA jednakost skupova (redoslijed i duplikati nebitni,
# vitičaste zagrade opcione). Konzervativno: bez definisanih skupova ili bez
# prepoznate operacije → None (ne izmišlja se presuda). Radi na SIROVOM tekstu (ne
# kroz _normalize_math_text) da se zarezi-separatori ne pomiješaju s decimalnim.
_SET_DEF_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s*=\s*\{([^{}]*)\}")
_UNIVERSE_RE = re.compile(
    r"\b(?:univerz\w*|univers\w*|osnovni\s+skup|univerzaln\w*\s+skup|universe)\b"
    r"\s*(?:je|=|:)?\s*\{([^{}]*)\}",
    re.IGNORECASE,
)
_SET_UNION_RE = re.compile(r"∪|\\cup|\bunij\w*|\bunion\b")
_SET_INTERSECT_RE = re.compile(r"∩|\\cap|\bpres[ijy]?ek\w*|\bintersection\b")
_SET_COMPLEMENT_RE = re.compile(r"\bkomplement\w*|\bcomplement\b|\\complement|\bnisu\s+u\b")
_SET_ELEMENTS_RE = re.compile(
    r"\belement\w*\s+skup\w*|\bnabroj\s+element\w*|\bkoji\s+su\s+element\w*"
)
_EMPTY_SET_RE = re.compile(
    r"^\s*(?:\{\s*\}|∅|\\emptyset|\\varnothing|prazan\s+skup|nema\s+element\w*)\s*$",
    re.IGNORECASE,
)


def _canon_set_element(raw: str) -> str | None:
    """Jedan element skupa → kanonski oblik; None ako nije valjan element.
    Cijeli/decimalni broj → numerički kanon; jedno slovo → malo slovo. Višeznakovne
    riječi se ODBIJAJU (spriječi da proza "unija"/"skup" prođe kao element)."""
    tok = _fold(raw.strip()).strip(".")
    if not tok:
        return None
    if re.fullmatch(r"-?\d+", tok):
        return str(int(tok))
    if re.fullmatch(r"-?\d+[.,]\d+", tok):
        return tok.replace(",", ".")
    if re.fullmatch(r"[a-z]", tok):
        return tok
    return None


def _parse_set_body(body: str) -> "frozenset[str] | None":
    """Sadržaj između zagrada ("1, 2, 3" ili "c, b") → skup kanonskih elemenata."""
    body = (body or "").strip()
    if body == "":
        return frozenset()
    out: list[str] = []
    for part in re.split(r"[,;]|\s+", body):
        if not part:
            continue
        canon = _canon_set_element(part)
        if canon is None:
            return None
        out.append(canon)
    return frozenset(out)


def _is_numeric_element(element: str) -> bool:
    try:
        Fraction(element)
        return True
    except (ValueError, ZeroDivisionError):
        return False


def _format_set(elements) -> str:
    numeric = sorted((e for e in elements if _is_numeric_element(e)), key=Fraction)
    symbolic = sorted(e for e in elements if not _is_numeric_element(e))
    return "{" + ", ".join([*numeric, *symbolic]) + "}"


def parse_set_answer(text: str) -> "frozenset[str] | None":
    """Učenikov odgovor-skup → skup kanonskih elemenata; None ako nije skup.

    Prihvata (redoslijed/duplikati/zagrade nebitni): ``{1,2,3}``, ``1,2,3``,
    ``1 2 3``, ``A ∪ B = {1,2,3}``, ``C ∩ D = {c,b}``, prazan skup
    (``{}``, ``∅``, "prazan skup"). Bez zagrada prolazi SAMO čista lista
    elemenata — rečenica sa riječima se odbija (višeznakovne riječi nisu element)."""
    if not text:
        return None
    raw = text.strip()
    if _EMPTY_SET_RE.match(_fold(raw)):
        return frozenset()
    if "=" in raw:                       # "LHS = RHS" → rezultat je desna strana
        raw = raw.rsplit("=", 1)[1].strip()
    brace = re.search(r"\{([^{}]*)\}", raw)
    body = brace.group(1) if brace else raw
    return _parse_set_body(body)


def _try_set_operation(folded: str, raw_text: str) -> Expected | None:
    named: dict[str, "frozenset[str]"] = {}
    for m in _SET_DEF_RE.finditer(raw_text):
        body = _parse_set_body(m.group(2))
        if body is not None:
            named[m.group(1)] = body
    universe = None
    um = _UNIVERSE_RE.search(raw_text)
    if um:
        universe = _parse_set_body(um.group(1))
    if universe is None and "U" in named:
        universe = named.pop("U")        # imenovani univerzalni skup "U = {..}"
    if not named:
        return None
    if _SET_UNION_RE.search(folded):
        op = "union"
    elif _SET_INTERSECT_RE.search(folded):
        op = "intersection"
    elif _SET_COMPLEMENT_RE.search(folded):
        op = "complement"
    elif _SET_ELEMENTS_RE.search(folded):
        op = "elements"
    else:
        return None
    if op == "union":
        if len(named) < 2:
            return None
        result = frozenset().union(*named.values())
    elif op == "intersection":
        if len(named) < 2:
            return None
        sets = list(named.values())
        result = sets[0]
        for s in sets[1:]:
            result = result & s
    elif op == "complement":
        if universe is None or len(named) < 1:
            return None
        target = next(iter(named.values()))
        result = frozenset(universe - target)
    else:                                # elements
        result = next(iter(named.values()))
    return Expected(
        value=Fraction(len(result)),
        kind="set",
        answer_type="set",
        expected_elements=tuple(sorted(result)),
        expected_display=_format_set(result),
        set_operation=op,
        confidence="high",
    )


def derive_expected(item_text: str) -> Expected | None:
    """Pokušaj deterministički izračunati očekivani rezultat stavke; None = ne zna."""
    # Skupovne operacije PRVE: rade na sirovom tekstu (zarez = separator, ne
    # decimalni) pa moraju prije bilo koje brojevne normalizacije.
    set_op = _try_set_operation(_fold(item_text or ""), item_text or "")
    if set_op is not None:
        return set_op
    norm = _normalize_math_text(item_text or "")
    folded = _fold(norm)
    tokens = _scan_number_tokens(norm)
    for solver in (
        _try_conversion,
        _try_percent_fraction_conversion,
        _try_expand,
        _try_simplify,
        _try_complement,
        _try_worded_fraction_operation,
        _try_fraction_comparison,
        _try_gcd_lcm,
        # A3 (2026-07-13): procenti, stepeni, jedinice
        _try_percent_of,
        _try_power,
        _try_unit_conversion,
    ):
        result = solver(folded, tokens)
        if result is not None:
            return result
    angle = _try_angle_arithmetic(folded, norm)
    if angle is not None:
        return angle
    triangle_angle = _try_triangle_missing_angle(folded, norm)
    if triangle_angle is not None:
        return triangle_angle
    arc = _try_arc_length(folded, norm)
    if arc is not None:
        return arc
    tangent = _try_tangent_radius_angle(folded, norm)
    if tangent is not None:
        return tangent
    # A3 guard: stepen koji solver NIJE riješio (kombinovani izraz "2^3 + 1")
    # ne smije pasti u _try_arithmetic — on ne zna "^" pa bi parsirao samo
    # ostatak ("3 + 1") i dao POGREŠAN expected. Bolje "ne znam" nego krivo.
    if re.search(r"\^|\*\*|\bna\s+(?:kvadrat|kub|drugu|trecu)\b", folded):
        return None
    equation = _try_linear_equation(folded, norm)
    if equation is not None:
        return equation
    rate = _try_rate_or_ratio(folded, norm)
    if rate is not None:
        return rate
    return _try_arithmetic(folded, norm)


# --- Parsiranje učenikovog odgovora ------------------------------------------------

# "1) 3/5 2) 1/4" — marker MORA imati interpunkciju iza broja, da se "2 1/4"
# (mješoviti broj) ne protumači kao "stavka 2, odgovor 1/4".
_ANSWER_MARKER_RE = re.compile(r"(?:^|[\s,;])(\d{1,2})\s*[).:]\s*")

# AUD-02 (2026-07-11): ordinalno imenovani odgovori — "prvi je 6/9, drugi 4/8,
# treci ne znam". Ranije: parse_student_answers → "none" (izgubljeni odgovori).
_ORDINAL_STEM_NUM = {
    "prv": 1, "drug": 2, "trec": 3, "cetvrt": 4, "pet": 5,
    "sest": 6, "sedm": 7, "osm": 8,
}
# Marker: ordinalni korijen + rod-nastavak + opcioni veznik "je"/"="/":". Radi
# na foldovanom tekstu (č/ć/š/ž/đ → ascii); _fold je 1:1 pa su indeksi poravnati
# s originalom.
_ORDINAL_MARKER_RE = re.compile(
    r"(?:^|[\s,;.])(prv|drug|trec|cetvrt|pet|sest|sedm|osm)"
    r"(?:i|a|o|e|og|om|u|oj)?\b\s*(?:je\b|=|:)?\s*"
)
# Poslije ordinala ovo NIJE odgovor nego referenca/objašnjenje ("prvi KORAK",
# "drugi NAČIN", "treći ZADATAK mi nije jasan", "prvi primjer") → ne parsiraj kao
# predani odgovor.
_ORDINAL_GUARD_RE = re.compile(
    r"^(?:korak|nacin|primjer|zadat\w*|zadac\w*|pitanj\w*|stavk\w*|razlom\w*|"
    r"broj(?:nik\w*|itelj\w*)?\b|nazivnik\w*|clan\w*|sabir\w*|put\b|dio\b|nije\b|"
    r"treba\w*|metod\w*|razred\w*|primjer\w*|je\s+lak|je\s+tez|mi\b|se\b|je\b)"
)


def _ordinal_answer_marks(folded: str) -> list[tuple[int, int, int]]:
    """Vrati (start, end_of_marker, item_number) za svaki ordinal koji NIJE
    referenca/objašnjenje (guard). Radi na foldovanom tekstu."""
    out: list[tuple[int, int, int]] = []
    for m in _ORDINAL_MARKER_RE.finditer(folded):
        n = _ORDINAL_STEM_NUM.get(m.group(1))
        if not n:
            continue
        seg = folded[m.end():]
        if _NONANSWER_SEG_RE.match(seg):
            out.append((m.start(1), m.end(), n))     # "treci ne znam" → nepokušano
            continue
        if _ORDINAL_GUARD_RE.match(seg):
            continue                                  # referenca/objašnjenje, ne odgovor
        out.append((m.start(1), m.end(), n))
    return out


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

    # AUD-02: ordinalno imenovani odgovori ("prvi je 6/9, drugi 4/8, treci ne
    # znam"). Traži ≥2 markera (jedan ordinal je dvosmislen → prepušta se
    # detect_referenced_items/single putu) i bar jedan stvaran broj.
    folded = _fold(norm)
    omarks = _ordinal_answer_marks(folded)
    if len(omarks) >= 2:
        ordinal: dict[int, NumberToken | None] = {}
        for i, (_s, e, n) in enumerate(omarks):
            end = omarks[i + 1][0] if i + 1 < len(omarks) else len(norm)
            seg = norm[e:end]
            if _NONANSWER_SEG_RE.match(_fold(seg)):
                ordinal[n] = None                     # nepokušano ("ne znam")
            else:
                toks = _scan_number_tokens(seg)
                ordinal[n] = toks[0] if len(toks) == 1 else None
        if any(t is not None for t in ordinal.values()):
            return "numbered", ordinal

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


# Segment stavke koji je EKSPLICITAN NE-odgovor ("3) ne znam") — učenik nije
# pokušao tu stavku, ne smije se računati kao odgovorena/ocijenjena (#5).
_NONANSWER_SEG_RE = re.compile(
    r"^\s*(?:ne\s*znam\w*|nemam\s+pojma|ne\s+znam\s+kako|preskac\w*|preskoci\w*|"
    r"nista|ne\s+umijem|ne\s+umem|\?+|-{1,3}|prazno|bez\s+odgovora|pas)\s*$"
)


def _numbered_nonanswer_items(student_text: str) -> set[int]:
    """Brojevi stavki čiji je segment eksplicitan ne-odgovor ("3) ne znam" ili
    ordinalno "treci ne znam")."""
    norm = _fold(_normalize_math_text(student_text or ""))
    out: set[int] = set()
    marks = [(m.start(1), m.end(), int(m.group(1)))
             for m in _ANSWER_MARKER_RE.finditer(norm)]
    for i, (_s, e, n) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(norm)
        if _NONANSWER_SEG_RE.match(norm[e:end]):
            out.add(n)
    # AUD-02: ordinalni ne-odgovori ("treci ne znam")
    omarks = _ordinal_answer_marks(norm)
    for i, (_s, e, n) in enumerate(omarks):
        end = omarks[i + 1][0] if i + 1 < len(omarks) else len(norm)
        if _NONANSWER_SEG_RE.match(norm[e:end]):
            out.add(n)
    return out


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
    # AUD-02: direktna referenca s odgovorom ("treci je 5/6", "drugi = 4/8",
    # "cetvrti 7", "prvi je x=4"). Guard u _ordinal_answer_marks isključuje
    # "prvi korak"/"drugi zadatak" (to hvataju gornja pravila kao referencu).
    for _s, _e, n in _ordinal_answer_marks(folded):
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
    # "correct" | "correct_value_wrong_form" | "correct_step" | "incorrect"
    # | "missing" | "not_attempted" | "unverified"
    # correct_step = tvrdnja ekvivalentna zadatku, ali NIJE konačan oblik
    #   ("2x < 12" za "2x - 5 < 7") — tačan međukorak, NIKAD "netačno";
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
                "correct", "correct_equivalent_form", "correct_missing_notation",
                "correct_missing_unit", "correct_value_wrong_form", "correct_step",
                "partially_correct", "incomplete", "wrong_unit", "incorrect",
                "ambiguous", "needs_review", "missing", "not_attempted",
            )
            for i in self.items
        )


def _values_match(expected: Expected, given: NumberToken) -> bool:
    """Egzaktna jednakost; uz malu toleranciju SAMO kad učenik da decimalnu
    aproksimaciju razlomka (npr. 1,333333 za 4/3). Zahtijeva ≥3 decimale da se
    kratke vrijednosti (1,3) i dalje traže egzaktno."""
    if given.value == expected.value:
        return True
    if expected.tolerance is not None and abs(given.value - expected.value) <= expected.tolerance:
        return True
    if given.form == "decimal" and expected.value.denominator != 1:
        frac_digits = len(given.raw.split(",")[-1]) if "," in given.raw else 0
        if frac_digits >= 3:
            ev, gv = float(expected.value), float(given.value)
            return abs(gv - ev) <= 5e-4 * max(1.0, abs(ev))
    return False


def _judge(expected: Expected | None, given: NumberToken | None, answered: bool) -> str:
    if given is None:
        # answered=True znači: odgovor postoji, ali ga nismo znali pročitati
        return "unverified" if answered else "missing"
    if expected is None:
        return "unverified"
    specialized = (
        _judge_measurement(expected, given)
        or _judge_angle(expected, given)
        or _judge_percentage(expected, given)
    )
    if specialized is not None:
        return specialized
    if not _values_match(expected, given):
        # CLASS 1: vrijednost tačnog MEĐUKORAKA ("12/12" za 5/12+7/12-3/12,
        # poslije hinta "prvo saberi") — tačan korak, ne "netačno".
        if any(given.value == s for s in expected.step_values):
            return "correct_step"
        # positive_only: različita vrijednost može biti druga jedinica/oblik —
        # ne smijemo tvrditi "netačno", model provjerava sam
        return "incorrect" if expected.confidence == "high" else "unverified"
    if expected.required_form == "fraction" and given.form != "fraction":
        return "correct_value_wrong_form"
    if expected.required_form == "mixed" and given.form not in ("mixed", "integer"):
        return "correct_value_wrong_form"
    if expected.required_form == "percentage" and given.form != "percentage":
        return "correct_value_wrong_form"
    if expected.required_form == "decimal" and given.form != "decimal":
        return "correct_value_wrong_form"
    if expected.kind == "simplify" and not given.is_reduced_fraction:
        return "correct_value_wrong_form"
    if expected.kind == "expand":
        _num, den = _fraction_parts(given.raw)
        if expected.target_denominator and den != expected.target_denominator:
            return "correct_value_wrong_form"
    equiv = _equivalent_form_verdict(expected, given)
    if equiv:
        return equiv
    return "correct"


def check_practice_answer(
    task_text: str,
    student_text: str,
    pending_items: list[int] | None = None,
) -> CheckResult:
    """Uporedi učenikov odgovor sa zadatkom, po stavkama. Nikad ne baca izuzetak.

    ``pending_items``: brojevi stavki višestavkovnog zadatka koje JOŠ NISU
    ocijenjene (iz ``task_items`` stanja). Koristi se za atribuciju jednog
    nenumerisanog odgovora pravoj stavci; None = sve stavke dolaze u obzir."""
    try:
        return _check(task_text or "", student_text or "", pending_items)
    except Exception:
        return CheckResult(checkable=False)


# --- Izbor između imenovanih opcija ("Koji je ugao veći, C ili D?") -----------------
# BUG 7 (2026-07-10): odgovor-slovo ("d") nije numerički pa je provjera vraćala
# checkable=False, a model je sam ocijenio i pogriješio. Ovdje se poređenje sa
# imenovanim vrijednostima presuđuje deterministički.

_NAMED_VALUE_RE = re.compile(
    r"(?:m\s*[∠<]\s*|ug(?:ao|la|lu|lom)\s+|razlomak\s+)?"
    r"\b([a-z])\b\s*(?:=|iznosi|je)\s*(-?\d+(?:,\d+)?)"
)
_CHOICE_QUESTION_RE = re.compile(
    r"\bkoji\w*\b.{0,60}\b(vec\w*|manj\w*|najvec\w*|najmanj\w*)\b"
)
_CHOICE_LETTER_ANSWER_RE = re.compile(
    r"^\s*(?:ug(?:ao|la|lu)\s+)?([a-z])\s*(?:je)?(?:\s+vec\w*|\s+manj\w*)?\s*[.!?]*\s*$"
)


def _check_choice_comparison(task_text: str, student_text: str) -> CheckResult | None:
    """Presudi izbor imenovane opcije kad zadatak daje vrijednosti (npr. uglovi)."""
    if split_numbered_items(task_text):
        return None                       # višestavkovni zadatak — opšti tok
    norm_task = _normalize_math_text(task_text)
    folded_task = _fold(norm_task)
    q = _CHOICE_QUESTION_RE.search(folded_task)
    if not q:
        return None
    wants_greater = q.group(1).startswith(("vec", "najvec"))
    named: dict[str, Fraction] = {}
    for m in _NAMED_VALUE_RE.finditer(folded_task):
        letter, raw = m.group(1), m.group(2)
        try:
            named[letter] = Fraction(raw.replace(",", "."))
        except (ValueError, ZeroDivisionError):
            return None
    if len(named) != 2:
        return None
    (l1, v1), (l2, v2) = sorted(named.items())
    if v1 == v2:
        return None                       # jednake vrijednosti — nema izbora
    if wants_greater:
        exp_letter, exp_val = (l1, v1) if v1 > v2 else (l2, v2)
    else:
        exp_letter, exp_val = (l1, v1) if v1 < v2 else (l2, v2)

    ans = _CHOICE_LETTER_ANSWER_RE.match(_fold(student_text or "").strip())
    if not ans:
        return None                       # odgovor nije čisto slovo — opšti tok
    given_letter = ans.group(1)
    if given_letter not in named:
        return None
    expected = Expected(
        value=exp_val, kind="choice", unit=f"ugao {exp_letter.upper()}",
    )
    given = NumberToken(
        value=named[given_letter], form="choice", raw=given_letter.upper(),
    )
    verdict = "correct" if given_letter == exp_letter else "incorrect"
    return CheckResult(checkable=True, items=[
        ItemCheck(n=1, task=task_text.strip()[:200], expected=expected,
                  given=given, verdict=verdict),
    ])


# --- Da/ne zadaci djeljivosti ("Da li je 48 djeljiv sa 6?") -------------------------
# BUG 6/7 (2026-07-10): "da"/"ne" na yes-no zadatak je ODGOVOR i presudiv je u
# kodu (N % k); ranije je model sam ocjenjivao i znao pogriješiti.

_DIVISIBILITY_ASK_RE = re.compile(
    r"(?:da\s+li\s+|provjeri\s+(?:da\s+li\s+)?)?je\s*(?:li)?\s+(?:broj\s+)?(\d+)\s+dj?eljiv\w*\s+sa?\s+(\d+)"
)
_YES_ANSWER_RE = re.compile(r"^\s*(da|jeste|jest|djeljiv\s+je)\b[\s.!?]*$")
_NO_ANSWER_RE = re.compile(r"^\s*(ne|nije)\b[\s.!?]*$")


def _check_yes_no_divisibility(task_text: str, student_text: str) -> CheckResult | None:
    if split_numbered_items(task_text):
        return None
    folded_task = _fold(_normalize_math_text(task_text))
    m = _DIVISIBILITY_ASK_RE.search(folded_task)
    if not m:
        return None
    n, k = int(m.group(1)), int(m.group(2))
    if k == 0:
        return None
    ans = _fold(student_text or "").strip()
    if _YES_ANSWER_RE.match(ans):
        said_yes = True
    elif _NO_ANSWER_RE.match(ans):
        said_yes = False
    else:
        return None                       # nije čist da/ne — opšti tok
    truth = (n % k == 0)
    q, r = divmod(n, k)
    basis = f"{n} : {k} = {q}" if truth else f"{n} : {k} = {q}, ostatak {r}"
    expected = Expected(value=Fraction(1 if truth else 0), kind="yes_no", basis=basis)
    given = NumberToken(
        value=Fraction(1 if said_yes else 0), form="yes_no",
        raw="da" if said_yes else "ne",
    )
    verdict = "correct" if said_yes == truth else "incorrect"
    return CheckResult(checkable=True, items=[
        ItemCheck(n=1, task=task_text.strip()[:200], expected=expected,
                  given=given, verdict=verdict),
    ])


# Presude kojima se odgovor smije PRIPISATI stavci: tačan, tačna vrijednost u
# pogrešnom obliku ili tačan međukorak. "incorrect" se pripisuje SAMO kada je
# preostala tačno jedna stavka (inače ne znamo koju je stavku pokušao).
_ATTRIBUTABLE_VERDICTS = (
    "correct", "correct_equivalent_form", "correct_missing_notation",
    "correct_missing_unit", "correct_value_wrong_form", "correct_step",
)


def _attribute_unnumbered_answer(
    items: list[tuple[int, str]],
    student_text: str,
    pending_items: list[int] | None,
) -> CheckResult | None:
    """JEDAN nenumerisan odgovor na višestavkovni zadatak → pripiši ga stavci.

    Svaka pending (još neocijenjena) stavka se provjeri kao samostalan zadatak.
    Pripisujemo SAMO kad je nedvosmisleno:
    - odgovor je tačan/tačan-međukorak za TAČNO JEDNU pending stavku, ili
    - preostala je TAČNO JEDNA pending stavka (tada vrijedi i "netačno").
    Sve ostalo → None (konzervativno; model ocjenjuje sam kao do sada).
    Već ocijenjene stavke se NE vraćaju u rezultat (guard ih ne smije
    proglašavati "bez odgovora")."""
    valid = [n for n, _t in items]
    if pending_items is None:
        pending = list(valid)
    else:
        pending = [n for n in pending_items if n in valid] or list(valid)
    candidates = [(n, text) for n, text in items if n in pending]
    if not candidates:
        return None
    minis: dict[int, CheckResult] = {
        n: _check(text, student_text) for n, text in candidates
    }

    def _sole_verdict(result: CheckResult) -> str:
        if result.checkable and len(result.items) == 1:
            return result.items[0].verdict
        return ""

    positive = [n for n in minis if _sole_verdict(minis[n]) in _ATTRIBUTABLE_VERDICTS]
    chosen: int | None = None
    if len(positive) == 1:
        chosen = positive[0]
    elif not positive and len(candidates) == 1 and \
            _sole_verdict(minis[candidates[0][0]]) == "incorrect":
        chosen = candidates[0][0]
    if chosen is None:
        return None

    chosen_item = minis[chosen].items[0]
    checks: list[ItemCheck] = []
    for n, text in items:
        if n == chosen:
            checks.append(ItemCheck(
                n=n, task=text[:200], expected=chosen_item.expected,
                given=chosen_item.given, verdict=chosen_item.verdict,
            ))
        elif n in pending:
            checks.append(ItemCheck(
                n=n, task=text[:200], expected=None, given=None,
                verdict="not_attempted",
            ))
    return CheckResult(checkable=True, items=checks)


def _extract_numbered_set_answers(
    student_text: str, valid: list[int]
) -> dict[int, "frozenset[str]"]:
    """"1) {1,2,3} 2) {b,c}" → {1: {..}, 2: {..}} (samo postojeći brojevi stavki)."""
    norm = student_text or ""
    marks = [
        (m.start(1), m.end(), int(m.group(1)))
        for m in _ANSWER_MARKER_RE.finditer(norm)
    ]
    if not marks:
        return {}
    out: dict[int, "frozenset[str]"] = {}
    for i, (_s, e, n) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(norm)
        parsed = parse_set_answer(norm[e:end])
        if parsed is not None and n in valid:
            out[n] = parsed
    return out


def _check_set_task(
    task_text: str, student_text: str, pending_items: list[int] | None = None
) -> CheckResult | None:
    """Puna presuda kada je zadatak skupovna operacija (unija/presjek/komplement).

    Presuda je PRAVA jednakost skupova. Podržava numerisane odgovore
    ("1) {..} 2) {..}") i jedan skup pripisan tekućoj stavci. None → nije skupovni
    zadatak ili odgovor nije skup (prepusti opštem toku)."""
    stripped = (task_text or "").strip()
    if not stripped:
        return None
    items = split_numbered_items(task_text) or [(1, stripped)]
    expected_by_n = {n: _try_set_operation(_fold(text), text) for n, text in items}
    if not any(e is not None for e in expected_by_n.values()):
        return None
    valid = [n for n, _t in items]
    numbered = _extract_numbered_set_answers(student_text, valid)
    given_by_n: dict[int, "frozenset[str]"] = {}
    answered: set[int] = set()
    if numbered:
        given_by_n = dict(numbered)
        answered = set(numbered)
    else:
        single = parse_set_answer(student_text)
        if single is None:
            return None
        pending = [n for n in (pending_items or valid) if n in valid] or valid
        pend_set = [n for n in pending if expected_by_n.get(n) is not None]
        matches = [
            n for n in pend_set
            if frozenset(expected_by_n[n].expected_elements) == single
        ]
        if len(matches) == 1:
            target = matches[0]
        elif len(pend_set) == 1:
            target = pend_set[0]
        elif len([n for n, e in expected_by_n.items() if e is not None]) == 1:
            target = next(n for n, e in expected_by_n.items() if e is not None)
        else:
            return None
        given_by_n = {target: single}
        answered = {target}
    checks: list[ItemCheck] = []
    for n, text in items:
        expected = expected_by_n[n]
        if n in answered:
            if expected is None:
                checks.append(ItemCheck(n=n, task=text[:200], expected=None,
                                        given=None, verdict="unverified"))
                continue
            given_set = given_by_n[n]
            verdict = (
                "correct"
                if given_set == frozenset(expected.expected_elements)
                else "incorrect"
            )
            given = NumberToken(
                value=Fraction(len(given_set)), form="set",
                raw=_format_set(given_set), elements=given_set,
            )
            checks.append(ItemCheck(n=n, task=text[:200], expected=expected,
                                    given=given, verdict=verdict))
        else:
            checks.append(ItemCheck(
                n=n, task=text[:200], expected=expected, given=None,
                verdict="not_attempted" if len(items) > 1 else "missing",
            ))
    return CheckResult(checkable=True, items=checks)


def _check(
    task_text: str,
    student_text: str,
    pending_items: list[int] | None = None,
) -> CheckResult:
    # Skupovne operacije prve — presuda je jednakost skupova, ne brojevni token.
    set_result = _check_set_task(task_text, student_text, pending_items)
    if set_result is not None:
        return set_result
    # Nejednačine se presuđuju posebno (presuda nosi operator, ne samo broj).
    inequality = _check_single_inequality(task_text, student_text)
    if inequality is not None:
        return inequality
    # Tvrdnja-jednačina ("x = 5 - 3/4", "2x = 12") na zadatak-jednačinu.
    equation = _check_single_equation(task_text, student_text)
    if equation is not None:
        return equation
    # Izbor imenovane opcije i da/ne djeljivost — cijeli zadatak, prije stavki.
    choice = _check_choice_comparison(task_text, student_text)
    if choice is not None:
        return choice
    yes_no = _check_yes_no_divisibility(task_text, student_text)
    if yes_no is not None:
        return yes_no
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
    # Atribucija (2026-07-14): JEDAN nenumerisan odgovor ("x=4 1/4", "2x<12",
    # "da") na višestavkovni zadatak — provjeri ga protiv svake pending stavke
    # kao samostalnog zadatka i pripiši kad je nedvosmisleno. Ranije se odmah
    # odustajalo (checkable=False) pa je model ocjenjivao sam i znao tačan
    # odgovor proglasiti netačnim.
    if len(items) > 1 and not refs and mode in ("single", "none"):
        attributed = _attribute_unnumbered_answer(items, student_text, pending_items)
        if attributed is not None:
            return attributed
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
        # #5: "3) ne znam" NIJE pokušaj — stavka ostaje nepokušana (pending)
        nonanswer = _numbered_nonanswer_items(student_text)
        given_by_n = {n: t for n, t in answers.items() if n in valid and t is not None}
        answered_ns = {n for n in answers if n in valid and n not in nonanswer}
        if not answered_ns:
            return CheckResult(checkable=False)
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
                f"{_fmt_expected(item.expected)}."
            )
        elif item.verdict == "correct_equivalent_form":
            lines.append(
                f"- Stavka {item.n}: TAČNO, EKVIVALENTAN OBLIK. Učenik: {given}; "
                f"očekivani zapis: {_fmt_expected(item.expected)}. Prihvati kao "
                "tačno i kratko spomeni da je oblik ekvivalentan."
            )
        elif item.verdict == "correct_missing_notation":
            lines.append(
                f"- Stavka {item.n}: TAČNO, NEDOSTAJE OZNAKA. Učenik: {given}; "
                f"tačan zapis: {_fmt_expected(item.expected)}. Počni kao tačno i "
                "kratko podsjeti učenika na oznaku."
            )
        elif item.verdict == "correct_missing_unit":
            lines.append(
                f"- Stavka {item.n}: TAČNO, NEDOSTAJE MJERNA JEDINICA. Učenik: {given}; "
                f"tačan zapis: {_fmt_expected(item.expected)}. Počni kao tačno i "
                "kratko podsjeti učenika na jedinicu."
            )
        elif item.verdict == "correct_value_wrong_form":
            expected = _fmt_expected(item.expected) if item.expected else "traženi oblik"
            detail = f" Očekivani oblik: {expected}."
            if item.expected and item.expected.kind == "simplify":
                detail = f" Može se još skratiti do {expected}."
            elif item.expected and item.expected.kind == "expand" and item.expected.target_denominator:
                detail = (
                    f" Traženi nazivnik je {item.expected.target_denominator}; "
                    f"očekivani oblik je {expected}."
                )
            lines.append(
                f"- Stavka {item.n}: DJELIMIČNO TAČNO. Vrijednost je ekvivalentna "
                f"({given}), ali NIJE u traženom obliku.{detail} Počni odgovor "
                f"tačno sa \"Djelimično tačno.\" i nemoj koristiti labelu \"Tačno.\"."
            )
        elif item.verdict == "correct_step":
            expected = _fmt_expected(item.expected) if item.expected else ""
            final_note = (
                f" Konačan oblik (NE otkrivaj ga učeniku): {expected}."
                if expected else ""
            )
            lines.append(
                f"- Stavka {item.n}: TAČAN MEĐUKORAK. Učenikova tvrdnja "
                f"({given}) je TAČNA i ekvivalentna zadatku, ali nije dovršena "
                f"do konačnog oblika.{final_note} NIKAD ne reci da je "
                f"pogriješio niti da je došlo do greške."
            )
        elif item.verdict == "incorrect":
            lines.append(
                f"- Stavka {item.n}: NETAČNO. Učenik: {given}; tačan rezultat: "
                f"{_fmt_expected(item.expected)}. Ne prikazuj kompletno rjesenje "
                "na ovom obicnom pogresnom pokusaju; daj kratak feedback i pozovi "
                "ucenika da zatrazi hint ako zeli sljedeci korak."
            )
        elif item.verdict == "wrong_unit":
            lines.append(
                f"- Stavka {item.n}: POGREŠNA ILI NEPREPOZNATA JEDINICA. "
                f"Učenik: {given}; očekivano: {_fmt_expected(item.expected)}. "
                "Ne prihvataj kao potpuno tačno; objasni razliku u jedinici ili "
                "pitaj je li jedinica tipfeler."
            )
        elif item.verdict == "incomplete":
            lines.append(
                f"- Stavka {item.n}: NEPOTPUN ODGOVOR. Učenik je dao primjer "
                f"({given}), ali zadatak traži cijelo rješenje: "
                f"{_fmt_expected(item.expected)}. Ne označavaj kao tačno."
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
    verdicts = [i.verdict for i in result.items]
    accepted_verdicts = (
        "correct", "correct_equivalent_form", "correct_missing_notation",
        "correct_missing_unit",
    )
    all_correct = bool(verdicts) and all(v in accepted_verdicts for v in verdicts)
    step_confirmed = any(v == "correct_step" for v in verdicts) and all(
        v in accepted_verdicts + ("correct_step", "missing", "not_attempted")
        for v in verdicts
    )
    all_partial_or_correct = bool(verdicts) and all(
        v in accepted_verdicts + ("correct_value_wrong_form",) for v in verdicts
    ) and any(v == "correct_value_wrong_form" for v in verdicts)
    missing_ns = [
        i.n for i in result.items if i.verdict in ("missing", "not_attempted")
    ]
    answered_ok_ns = [
        i.n
        for i in result.items
        if i.verdict in accepted_verdicts + ("correct_value_wrong_form",)
    ]
    answered_subset_ok = bool(missing_ns) and bool(answered_ok_ns) and all(
        i.verdict in accepted_verdicts + ("correct_value_wrong_form", "missing", "not_attempted")
        for i in result.items
    )
    any_incorrect = any(v in ("incorrect", "wrong_unit", "incomplete") for v in verdicts)
    if step_confirmed:
        lines.append(
            "STIL (TAČAN MEĐUKORAK): NE koristi ocjenske labele (\"Tačno.\", "
            "\"Netačno.\", \"Djelimično tačno.\") i NIKAD ne reci da je učenik "
            "pogriješio, da je došlo do greške ili da nešto ne štima — korak JE "
            "tačan. Potvrdi ga toplo i prirodno (\"Tako je!\", \"Bravo, to je "
            "tačno.\"), pa JEDNIM kratkim pitanjem traži da dovrši do konačnog "
            "oblika. NE otkrivaj konačan rezultat i NE daji novi zadatak."
        )
    elif all_correct:
        lines.append(
            "STIL (TAČAN ODGOVOR): počni tačno sa \"Tačno.\", "
            "pa SAMO kratka provjera računa (1–2 rečenice). NE piši puni postupak "
            "korak-po-korak osim ako je učenik izričito tražio objašnjenje "
            "(\"objasni\", \"kako\", \"korak po korak\"). "
            "Ne počinji sa \"Pogledajmo zajedno\" ni sličnim uvodom. "
            "Ako uputa moda kaže da poslije tačnog odgovora slijedi novi zadatak, "
            "dodaj ga ODMAH u istoj poruci (red \"Zadatak: ...\")."
        )
    elif all_partial_or_correct:
        lines.append(
            "STIL (DJELIMIČNO TAČAN ODGOVOR): počni tačno sa "
            "\"Djelimično tačno.\". Zatim kratko reci da je vrijednost "
            "ekvivalentna, ali oblik nije dovršen/tražen, i napiši očekivani "
            "oblik. NE koristi labelu \"Tačno.\" i NE govori \"Netačno.\". "
            "Ako uputa moda kaže da poslije tačnog odgovora slijedi novi zadatak, "
            "dodaj ga ODMAH u istoj poruci (red \"Zadatak: ...\")."
        )
    elif answered_subset_ok:
        answered = ", ".join(str(n) for n in answered_ok_ns)
        missing = ", ".join(str(n) for n in missing_ns)
        lines.append(
            "STIL (DIO VIŠESTAVKOVNOG ODGOVORA JE TAČAN): NE počinji globalnom "
            "labelom \"Tačno.\" jer nisu odgovorene sve stavke. Počni jasnom "
            f"rečenicom da su zadaci {answered} tačni, a da zadaci {missing} "
            "još čekaju odgovor. Objašnjenja numeriši prema originalnim brojevima "
            "zadataka (1., 2., 3.); nikad ne piši 1., 1. za dvije različite stavke."
        )
    elif any_incorrect:
        lines.append(
            "STIL (NETAČAN ODGOVOR): blago reci da nije tačno. Ne otkrivaj "
            "kompletno rješenje ni konačni rezultat prije adaptivnog hinta nivoa 5; "
            "daj samo kratku povratnu informaciju ili jedan mali sljedeći korak."
        )
    return "\n".join(lines)


def summarize_result(result: CheckResult) -> dict | None:
    """Kompaktan sažetak za response JSON / testove; None kada nema provjere."""
    if not result.checkable or not result.has_verdicts:
        return None
    def _norm_student(tok: NumberToken | None) -> str | None:
        if tok is None:
            return None
        if tok.form == "set":
            return tok.raw or _format_set(tok.elements or frozenset())
        base = _fmt_fraction(tok.value)
        if tok.form == "percentage":
            return f"{_fmt_fraction(tok.value * 100)}%"
        if tok.unit:
            return f"{base} {tok.unit}".strip()
        if tok.unrecognized_unit:
            return f"{base} {tok.unrecognized_unit}".strip()
        return base

    def _deterministic_details(item: ItemCheck) -> dict:
        expected = item.expected
        given = item.given
        if expected is not None and expected.answer_type == "set":
            return {
                "parsed": given is not None,
                "numeric_match": bool(
                    given is not None
                    and given.elements == frozenset(expected.expected_elements)
                ),
                "unit_match": False,
                "unit_missing": False,
                "unit_unrecognized": False,
                "required_form_match": True,
                "set_operation": expected.set_operation or None,
                "expected_elements": list(expected.expected_elements),
                "student_elements": sorted(given.elements) if given and given.elements is not None else None,
            }
        numeric_match = bool(
            expected is not None and given is not None and _values_match(expected, given)
        )
        unit_match = bool(
            expected is not None
            and given is not None
            and expected.unit
            and given.unit
            and _unit_key(expected.unit) == _unit_key(given.unit)
        )
        return {
            "parsed": given is not None,
            "numeric_match": numeric_match,
            "unit_match": unit_match,
            "unit_missing": bool(expected and expected.unit and given and not given.unit and not given.unrecognized_unit),
            "unit_unrecognized": bool(given and given.unrecognized_unit),
            "required_form_match": bool(
                expected is None
                or not expected.required_form
                or (given is not None and given.form == expected.required_form)
                or (expected.required_form == "mixed" and given is not None and given.form == "integer")
            ),
        }

    return {
        "gpt_check_used": False,
        "gpt_check_confidence": None,
        "items": [
            {
                "n": i.n,
                "verdict": i.verdict,
                "expected": _fmt_expected(i.expected) if i.expected else None,
                "expected_answer": _fmt_expected(i.expected) if i.expected else None,
                "normalized_expected": (
                    _fmt_expected(i.expected)
                    if i.expected and i.expected.answer_type == "set"
                    else _fmt_fraction(i.expected.value) if i.expected else None
                ),
                "answer_type": i.expected.answer_type if i.expected else None,
                "expected_unit": i.expected.unit if i.expected else None,
                "unit_policy": i.expected.unit_policy if i.expected else None,
                "required_form": i.expected.required_form if i.expected else None,
                "equivalent_forms_allowed": i.expected.equivalent_forms_allowed if i.expected else None,
                "unit": i.expected.unit if i.expected else None,
                "given": i.given.raw if i.given else None,
                "student_answer": i.given.raw if i.given else None,
                "normalized_student": _norm_student(i.given),
                "student_unit": i.given.unit if i.given else None,
                "unrecognized_unit": i.given.unrecognized_unit if i.given else None,
                "deterministic_check": _deterministic_details(i),
            }
            for i in result.items
        ]
    }
