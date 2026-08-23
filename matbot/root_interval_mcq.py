# -*- coding: utf-8 -*-
"""Uski deterministički orakl: IZMEĐU KOJA DVA UZASTOPNA CIJELA BROJA JE KORIJEN.

ZAŠTO POSTOJI (živi nalaz, verifikacija Kontrolnog poslije izdanja, 8. razred,
oblast „Realni brojevi", lekcija o procjeni korijena):

    „Između koja dva uzastopna prirodna broja se nalazi $\\sqrt{70}$?"
    Ponuđeno: „Između $8$ i $9$" / „Između $6$ i $7$" / „Između $7$ i $8$" /
    „Između $9$ i $10$".  Označeno: „Između $7$ i $8$".

Tačno je $8$ i $9$ (jer je $8^2=64<70<81=9^2$) — i ta opcija je BILA ponuđena.
Najgore: RJEŠENJE u istom paketu izvodi tačan odgovor („…broj $70$ se nalazi
između $8^2$ i $9^2$. Zato se $\\sqrt{70}$ nalazi između $8$ i $9$."), pa je
paket sam sebi protivrječio i svejedno objavljen. Posljedica je pogrešan ključ
odgovora, dakle pogrešna ocjena učenika.

ZAŠTO GA POSTOJEĆI VALIDATORI NISU MOGLI UHVATITI:
  • `expected_answer` je bio JEDNAK označenoj opciji — oba polja su bila
    pogrešna zajedno, pa provjera samodosljednosti modela nema šta prijaviti;
  • `mathcheck` ne nalazi grešku: u tekstu, opciji i rješenju je svaki
    pojedinačni račun aritmetički tačan;
  • `_solution_contradicts_marked_value` traži da označena opcija ima TAČNO
    JEDNU dokazivu vrijednost; „Između $7$ i $8$" ih ima dvije, pa orakl ćuti;
  • `mcq_integrity`, `exactly_one` i geometrijski orakli nisu primjenjivi.

Greška je dakle u KLJUČU, a ne u računu — isti oblik kao raniji živi nalazi
(zamjena uloga veličina), pa se rješava na isti način: server sam izračuna
tačan odgovor iz TEKSTA ZADATKA i tek onda ocijeni ponuđene opcije.

MATEMATIKA (egzaktna, cjelobrojna — bez `float`):

    k = isqrt(N)
    k^2 < N < (k+1)^2   ->   k < sqrt(N) < k+1

Za POTPUN KVADRAT ($N=k^2$) orakl NAMJERNO ĆUTI. „Između koja dva uzastopna
broja" za $\\sqrt{64}=8$ nema dogovoreno značenje (broj je sam cijeli, nije
strogo između ničega), a u izmjerenom korpusu generator takav radikand u ovoj
porodici nikad nije emitovao. Semantika se NE izmišlja: paket dalje sude
postojeći validatori, a ovaj orakl ne tvrdi ništa — isti princip kao
`mathcheck` („preskočeno nije dokaz ispravnosti").

PREPOZNAVANJE PORODICE je strukturno, nikad po lekciji ili razredu: tekst mora
sadržati oznaku uzastopnosti („uzastopn…"), prijedlog „između" i TAČNO JEDAN
`\\sqrt{N}` s prirodnim $N$.

GRAMATIKA OPCIJA je zatvorena i izvedena iz STVARNOG korpusa (5 generisanih
zadataka, 4 oblika):

    „između 6 i 7"          — gola proza
    „Između $8$ i $9$"      — proza s brojevima u matematici
    „$49$ i $50$"           — samo par brojeva
    „$7<\\sqrt{50}<8$"       — lanac nejednakosti

Opcija koja se ne uklapa ni u jedan oblik se NE pogađa. Ako se ne može
pročitati OZNAČENA opcija, orakl ćuti; ako se ona pročita a ne poklapa se s
dokazanim intervalom, paket pada — to je dokaz, ne nagađanje.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from math import isqrt
from typing import Iterable, Optional, Tuple

# Porodica: „uzastopn…" + „između" + jedan korijen prirodnog broja.
_CONSECUTIVE_RE = re.compile(r"uzastopn", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"izme(?:\u0111|dj|d)u", re.IGNORECASE)
_SQRT_RE = re.compile(r"\\sqrt\s*\{\s*(\d+)\s*\}")

# Lanac „a < \sqrt{N} < b" — jedini oblik u kojem opcija sama nosi korijen.
_CHAIN_RE = re.compile(
    r"(\d+)\s*<\s*\\sqrt\s*\{\s*(\d+)\s*\}\s*<\s*(\d+)")
# Par „a i b" (sa ili bez „između", sa ili bez `$…$`). Veznik je obavezan —
# bez njega dva broja u opciji ne tvrde interval.
_PAIR_RE = re.compile(
    r"^\s*(?:izme(?:\u0111|dj|d)u\s+)?\$?\s*(\d+)\s*\$?\s+i\s+\$?\s*(\d+)\s*\$?\s*[.]?\s*$",
    re.IGNORECASE)


@dataclass(frozen=True)
class RootIntervalMCQResult:
    applicable: bool
    valid: bool
    reason_code: str = ""
    radicand: Optional[int] = None
    expected: Optional[Tuple[int, int]] = None
    option_intervals: tuple = ()
    correct_indices: tuple = ()


def _radicand(question: str) -> Optional[int]:
    """Prirodan broj pod korijenom, ili None kad oblik nije jednoznačan."""
    text = question or ""
    if not _CONSECUTIVE_RE.search(text) or not _BETWEEN_RE.search(text):
        return None
    found = _SQRT_RE.findall(text)
    if len(found) != 1:
        return None                      # nula ili više korijena: ne pogađa se
    value = int(found[0])
    return value if value > 0 else None


def _option_interval(option: str) -> Optional[Tuple[int, int]]:
    """Interval koji opcija TVRDI, ili None kad se ne može pročitati."""
    raw = (option or "").strip()
    chain = _CHAIN_RE.search(raw)
    if chain:
        low, _under, high = (int(chain.group(1)), int(chain.group(2)),
                             int(chain.group(3)))
        return (low, high)
    if "\\sqrt" in raw:
        return None                      # korijen izvan poznatog lanca: ne pogađa se
    bare = raw.replace("$", " ")
    bare = re.sub(r"\s+", " ", bare).strip()
    pair = _PAIR_RE.match(bare)
    if pair:
        return (int(pair.group(1)), int(pair.group(2)))
    return None


def evaluate_root_interval_mcq(question: str,
                               option_texts: Iterable[str]) -> RootIntervalMCQResult:
    """Egzaktna procjena porodice; `applicable=False` znači „ne tiče me se"."""
    options = list(option_texts or ())
    radicand = _radicand(question)
    if radicand is None or len(options) < 2:
        return RootIntervalMCQResult(False, False)

    root = isqrt(radicand)
    if root * root == radicand:
        # Potpun kvadrat: značenje pitanja nije dogovoreno — ćuti (vidi docstring).
        return RootIntervalMCQResult(False, False, radicand=radicand)

    expected = (root, root + 1)
    intervals = tuple(_option_interval(text) for text in options)
    matches = tuple(i for i, iv in enumerate(intervals) if iv == expected)

    # Jedinstvenost se traži SAMO kad su sve opcije čitljive — inače se ne zna
    # šta nečitljiva opcija tvrdi, pa se o njoj ništa ne tvrdi ni ovdje.
    if all(iv is not None for iv in intervals):
        if not matches:
            return RootIntervalMCQResult(
                True, False, "correct_interval_absent", radicand, expected,
                intervals, matches)
        if len(matches) > 1:
            return RootIntervalMCQResult(
                True, False, "correct_interval_duplicated", radicand, expected,
                intervals, matches)

    return RootIntervalMCQResult(True, True, "", radicand, expected,
                                 intervals, matches)


def publication_failure(question: str, option_texts: Iterable[str],
                        marked_index: int) -> str:
    """Kod odbijanja objave, ili "" kad orakl ne prigovara / ne primjenjuje se."""
    result = evaluate_root_interval_mcq(question, option_texts)
    if not result.applicable:
        return ""
    if not result.valid:
        return result.reason_code
    options = list(option_texts or ())
    if not 0 <= marked_index < len(options):
        return ""
    marked = result.option_intervals[marked_index]
    if marked is None:
        return ""                        # označena opcija nečitljiva: ne pogađa se
    if marked != result.expected:
        return "marked_interval_mismatch"
    return ""
