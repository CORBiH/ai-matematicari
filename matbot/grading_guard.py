# -*- coding: utf-8 -*-
"""Jedan autoritativni sud o tačnosti → konzistentan odgovor (bez kontradikcije).

Motiv (audit): ocjena je nastajala na više NEZAVISNIH mjesta — brza heuristika,
LLM koji piše prozu, deterministički ``answer_checker`` (presuda u promptu) i
provjera slike. Model je i pored obavezujuće presude u promptu znao napisati
kontradiktoran odgovor ("Nije tačno … tačan je 5/8") ili tačan odgovor proglasiti
netačnim. Prompt pravila su *molba*; ovaj modul je *provođenje*.

Princip: PRIJE nego što se odgovor pošalje, generisani tekst se pomiruje sa
JEDNIM autoritativnim sudom (``answer_checker.CheckResult``):

- ``correct``  → iz odgovora se uklanjaju negativne ocjene (bile su lažno
  negativne); ako ne ostane nijedna potvrda, dodaje se kratka pozitivna.
- ``incorrect`` → ako odgovor lažno POČINJE potvrdom ("Tačno!"), uvod se
  neutrališe; sama korekcija ("tačno je 5/8") ostaje.
- ``mixed`` (više stavki: neke tačne, neke ne) → po-stavkovna ocjena je
  legitimna, tekst se ne dira.
- ``unknown`` → ako se odgovor SAM SEBI protivrječi (i pozitivno i negativno),
  negativni sud se uklanja da se izbjegne LAŽNO NEGATIVNO; kad ništa ne ostane,
  koristi se neutralno "Hajde da provjerimo zajedno." (nikad izmišljeno "netačno").

Modul je ČIST (bez mreže/IO/importa app-a) i NIKAD ne baca izuzetak prema
pozivaocu (``enforce_grading_consistency`` hvata sve).
"""
from __future__ import annotations

import re
from typing import Any

from matbot.topic_detector import fold_diacritics

__all__ = [
    "authoritative_verdict",
    "enforce_grading_consistency",
    "grade_contradiction_phrases",
    "has_grade_contradiction",
]

# --- Fraze ocjene (poklapaju se na FOLDANOM tekstu: dijakritici → ASCII, lower) -----
# Negativne se traže PRVE i "maskiraju", da "tačno" unutar "nije tačno" ne bi
# pogrešno prošlo kao pozitivna ocjena.
# Napomena: obrasci se poklapaju na FOLDANOM tekstu (ASCII, lower), zato "č"→"c".
# "taca?n" hvata i "tacno/tacna" (srednji/ženski rod) i "tacan" (muški rod).
_NEG_GRADE_RE = re.compile(
    r"ni(?:je|su)\s+taca?n\w*"             # nije/nisu tačno, tačan, tačni
    r"|ni(?:je|su)\s+toca?n\w*"            # ijek./hrv. "nije točno"
    r"|netaca?n\w*|netoca?n\w*"            # netačno/netačan/netočno
    r"|ni(?:je|su)\s+isprav\w*|neisprav\w*"
    r"|ni(?:je|su)\s+dobr\w*"
    r"|(?<!nije )(?<!nisu )pogres\w*"      # "pogrešno", ali NE "nije pogrešno"
    r"|\bwrong\b|\bincorrect\b"
)
# Nakon maskiranja negativnih: samo jasne potvrde tačnosti (ne generičko "dobro").
_POS_GRADE_RE = re.compile(
    r"\btaca?n\w*|\btoca?n\w*"             # tačno/tačan/tačna, točno
    r"|\bisprav\w*"                        # ispravno/ispravan
    r"|\bbravo\b|\bodlicno\b|\bsvaka\s+cast\b|\btako\s+je\b"
    r"|\bcorrect\b"
)


def _neg_spans(folded: str) -> list[tuple[int, int]]:
    return [m.span() for m in _NEG_GRADE_RE.finditer(folded)]


def _mask(folded: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return folded
    chars = list(folded)
    for s, e in spans:
        for i in range(s, e):
            chars[i] = " "
    return "".join(chars)


def grade_contradiction_phrases(text: Any) -> tuple[bool, bool]:
    """(ima_negativnu_ocjenu, ima_pozitivnu_ocjenu) u proizvoljnom tekstu."""
    folded = fold_diacritics(text)
    if not folded:
        return False, False
    spans = _neg_spans(folded)
    has_pos = bool(_POS_GRADE_RE.search(_mask(folded, spans)))
    return bool(spans), has_pos


def has_grade_contradiction(text: Any) -> bool:
    """True kada isti tekst sadrži i pozitivnu i negativnu ocjenu tačnosti.

    Namjerno "tup" detektor (ne razlikuje po-stavkovni kontekst) — koristi se
    kao POSLJEDNJA provjera nad JEDNOSTRUKIM sudom (odgovor na jedan zadatak);
    višestavkovne odgovore pomiruje ``enforce_grading_consistency`` preko
    autoritativne presude, ne ovaj detektor."""
    neg, pos = grade_contradiction_phrases(text)
    return neg and pos


# --- Autoritativni sud iz determinističke provjere ---------------------------------

_POSITIVE_VERDICTS = ("correct", "correct_value_wrong_form")


def authoritative_verdict(result: Any) -> str:
    """``CheckResult`` → jedan sud: correct | incorrect | mixed | unknown.

    "mixed" = više ocijenjenih stavki gdje su neke tačne, a neke ne (legitimna
    po-stavkovna ocjena). "unknown" = kod nema pouzdanu presudu."""
    if result is None or not getattr(result, "checkable", False):
        return "unknown"
    graded = [
        i.verdict
        for i in getattr(result, "items", [])
        if i.verdict in _POSITIVE_VERDICTS or i.verdict == "incorrect"
    ]
    if not graded:
        return "unknown"
    has_incorrect = any(v == "incorrect" for v in graded)
    has_correct = any(v in _POSITIVE_VERDICTS for v in graded)
    if has_incorrect and has_correct:
        return "mixed"
    return "incorrect" if has_incorrect else "correct"


def _all_items_correct(result: Any) -> bool:
    """Je li CIJELI provjereni obim tačan (nijedna stavka nije netačna, bez
    odgovora, nepokušana ni neprovjerena)? Samo tada je globalno uklanjanje
    negativne ocjene sigurno — inače negativna fraza može legitimno pripadati
    stavci koju kod nije mogao provjeriti."""
    if result is None or not getattr(result, "checkable", False):
        return False
    items = getattr(result, "items", [])
    return bool(items) and all(i.verdict in _POSITIVE_VERDICTS for i in items)


_MULTI_ITEM_TEXT_RE = re.compile(
    r"\bstavk\w*|\bprv\w+\b.{0,60}\bdrug\w+\b|\bdrug\w+\b.{0,60}\btrec\w+\b"
)
_NUMBERED_LINE_RE = re.compile(r"(?:^|\n)\s*\d{1,2}[.)]\s")


def _multi_item_context(result: Any, answer: str) -> bool:
    """Odgovor pokriva VIŠE stavki (pa je po-stavkovna ocjena legitimna).

    Kada je tako, tup detektor kontradikcije se NE primjenjuje — miješana
    ocjena ("1. tačno, 2. netačno") nije samo-protivrječje."""
    items = getattr(result, "items", None) if result is not None else None
    if items and len(items) > 1:
        return True
    folded = fold_diacritics(answer)
    if _MULTI_ITEM_TEXT_RE.search(folded):
        return True
    return len(_NUMBERED_LINE_RE.findall(answer)) >= 2


# --- Uklanjanje/čišćenje negativnih fraza iz teksta --------------------------------

def _cleanup(text: str) -> str:
    """Popravi interpunkciju/razmake nastale nakon brisanja fraze ocjene."""
    out = text
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+([,.;:!?])", r"\1", out)          # razmak pred interpunkciju
    out = re.sub(r"([,;:])\s*([.!?])", r"\2", out)          # ", ." → "."
    out = re.sub(r"([.!?])\s*[,;:]+", r"\1", out)           # ". ," → "."
    out = re.sub(r"([.!?])[ \t]*\1+", r"\1", out)           # ".." → "."
    # rečenica koja počinje zaostalom interpunkcijom/veznikom-zarezom
    out = re.sub(r"(^|[.!?]\n?[ \t]*)[,;:][ \t]*", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    # velika početna slova rečenica (početak teksta i poslije .!?)
    def _cap(m: re.Match) -> str:
        return m.group(1) + m.group(2).upper()

    out = re.sub(r"(^|[.!?]\s+|\n[ \t]*)([a-zčćđšž])", _cap, out)
    # prazni redovi / uvodna interpunkcija na početku
    out = re.sub(r"^[\s,.;:!?]+", "", out)
    return out.strip()


def _remove_negative_verdicts(text: str) -> tuple[str, bool]:
    """Ukloni sve negativne fraze ocjene; vrati (novi_tekst, da_li_je_mijenjano).

    Indeksi iz foldanog teksta poravnati su s originalom (folding je 1:1 po
    znaku za naš domen). Ako se dužine ne poklope, radije NE mijenjamo tekst."""
    folded = fold_diacritics(text)
    if len(folded) != len(text):
        return text, False
    spans = _neg_spans(folded)
    if not spans:
        return text, False
    chars = list(text)
    for s, e in sorted(spans, reverse=True):
        del chars[s:e]
    return _cleanup("".join(chars)), True


def _has_positive_verdict(text: str) -> bool:
    folded = fold_diacritics(text)
    return bool(_POS_GRADE_RE.search(_mask(folded, _neg_spans(folded))))


def _prepend(opener: str, text: str) -> str:
    body = text.lstrip()
    if not body:
        return opener
    return f"{opener} {body}"


def _make_positive(answer: str) -> str:
    """Autoritativno TAČNO: skini lažno negativne ocjene, osiguraj potvrdu."""
    out, changed = _remove_negative_verdicts(answer)
    if not changed:
        return answer
    if not out.strip():
        return "Tačno! Tvoj odgovor je ispravan."
    if not _has_positive_verdict(out):
        out = _prepend("Tačno!", out)
    return out


def _neutralize_negative(answer: str) -> str:
    """Nesiguran sud + samo-kontradikcija: makni negativno, izbjegni lažno NE."""
    out, changed = _remove_negative_verdicts(answer)
    if not changed:
        return answer
    if not out.strip():
        return "Hajde da provjerimo zajedno."
    if not _has_positive_verdict(out):
        out = _prepend("Hajde da provjerimo zajedno —", out)
    return out


_POSITIVE_OPENER_RE = re.compile(
    r"^\s*(taca?n\w*|toca?n\w*|bravo|odlicno|super|svaka\s+cast|tako\s+je)\b[^.!?\n]*[.!?]?"
)


def _fix_false_positive_opener(answer: str) -> str:
    """Autoritativno NETAČNO, a odgovor POČINJE potvrdom → neutrališi uvod.

    Sama korekcija ("tačno je 5/8") se NE dira; mijenja se samo lažni uvodni
    sud da odgovor ne bi krenuo pogrešnim signalom."""
    folded = fold_diacritics(answer)
    m = _POSITIVE_OPENER_RE.match(folded)
    if not m:
        return answer
    rest = answer[m.end():].lstrip()
    return _prepend("Skoro — hajde da provjerimo zajedno.", rest) if rest else \
        "Skoro — hajde da provjerimo zajedno."


def enforce_grading_consistency(answer: Any, check_result: Any = None) -> str:
    """Pomiri generisani odgovor sa JEDNIM autoritativnim sudom o tačnosti.

    Zadnja linija odbrane u ``_finalize_response`` — svaki odgovor prolazi kroz
    ovo prije slanja. Nikad ne baca izuzetak: na grešci vraća original."""
    if not isinstance(answer, str) or not answer.strip():
        return answer
    try:
        verdict = authoritative_verdict(check_result)
        multi = _multi_item_context(check_result, answer)

        # Cijeli provjereni obim je tačan → nijedna negativna ocjena nije
        # legitimna; sigurno ih uklanjamo (radi i za jednu i za više stavki).
        if _all_items_correct(check_result):
            return _make_positive(answer)

        # Miješano (neke tačne, neke ne) ili višestavkovni kontekst → po-stavkovna
        # ocjena je legitimna, tekst se ne dira.
        if verdict == "mixed" or multi:
            return answer

        if verdict == "incorrect":
            # jedna stavka, netačna, a odgovor lažno POČINJE potvrdom
            if _POSITIVE_OPENER_RE.match(fold_diacritics(answer)):
                return _fix_false_positive_opener(answer)
            return answer

        # unknown / djelimično provjereno (jedna stavka): samo ako se odgovor
        # SAM SEBI protivrječi — makni negativno da ne bude lažno negativno.
        if has_grade_contradiction(answer):
            return _neutralize_negative(answer)
        return answer
    except Exception:
        return answer
