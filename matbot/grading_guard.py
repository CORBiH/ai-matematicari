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
- ``partial`` (tačna vrijednost, pogrešan/ nedovršen oblik) → odgovor mora
  početi sa "Djelimično tačno.", ne sa punim "Tačno.".
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

_POSITIVE_VERDICTS = ("correct",)
_PARTIAL_VERDICTS = ("correct_value_wrong_form",)


def authoritative_verdict(result: Any) -> str:
    """``CheckResult`` → jedan sud: correct | partial | incorrect | mixed | unknown.

    "mixed" = više ocijenjenih stavki gdje su neke tačne, a neke ne (legitimna
    po-stavkovna ocjena). "unknown" = kod nema pouzdanu presudu."""
    if result is None or not getattr(result, "checkable", False):
        return "unknown"
    graded = [
        i.verdict
        for i in getattr(result, "items", [])
        if i.verdict in _POSITIVE_VERDICTS
        or i.verdict in _PARTIAL_VERDICTS
        or i.verdict == "incorrect"
    ]
    if not graded:
        return "unknown"
    has_incorrect = any(v == "incorrect" for v in graded)
    has_correct = any(v in _POSITIVE_VERDICTS for v in graded)
    has_partial = any(v in _PARTIAL_VERDICTS for v in graded)
    if has_incorrect and (has_correct or has_partial):
        return "mixed"
    if has_incorrect:
        return "incorrect"
    if has_partial:
        return "partial"
    return "correct"


def _all_items_correct(result: Any) -> bool:
    """Je li CIJELI provjereni obim tačan (nijedna stavka nije netačna, bez
    odgovora, nepokušana ni neprovjerena)? Samo tada je globalno uklanjanje
    negativne ocjene sigurno — inače negativna fraza može legitimno pripadati
    stavci koju kod nije mogao provjeriti."""
    if result is None or not getattr(result, "checkable", False):
        return False
    items = getattr(result, "items", [])
    return bool(items) and all(i.verdict in _POSITIVE_VERDICTS for i in items)


def _all_items_partial_or_correct(result: Any) -> bool:
    if result is None or not getattr(result, "checkable", False):
        return False
    items = getattr(result, "items", [])
    return bool(items) and all(
        i.verdict in _POSITIVE_VERDICTS or i.verdict in _PARTIAL_VERDICTS
        for i in items
    ) and any(i.verdict in _PARTIAL_VERDICTS for i in items)


def _correct_subset_with_missing(result: Any) -> tuple[list[int], list[int], bool] | None:
    """Answered subset is correct/partial, but at least one numbered item is missing."""
    if result is None or not getattr(result, "checkable", False):
        return None
    items = list(getattr(result, "items", []) or [])
    if len(items) <= 1:
        return None
    answered: list[int] = []
    missing: list[int] = []
    has_partial = False
    for item in items:
        verdict = getattr(item, "verdict", "")
        if verdict in _POSITIVE_VERDICTS:
            answered.append(getattr(item, "n", 0))
        elif verdict in _PARTIAL_VERDICTS:
            answered.append(getattr(item, "n", 0))
            has_partial = True
        elif verdict in ("missing", "not_attempted"):
            missing.append(getattr(item, "n", 0))
        else:
            return None
    if not answered or not missing:
        return None
    return answered, missing, has_partial


def _join_numbers(nums: list[int]) -> str:
    clean = [str(n) for n in nums if n]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    return ", ".join(clean[:-1]) + " i " + clean[-1]


def _missing_sentence(nums: list[int]) -> str:
    joined = _join_numbers(nums)
    if not joined:
        return ""
    if len([n for n in nums if n]) == 1:
        return f"Zadatak {joined} još čeka tvoj odgovor."
    return f"Zadaci {joined} još čekaju tvoj odgovor."


def _answered_subset_sentence(nums: list[int], has_partial: bool) -> str:
    joined = _join_numbers(nums)
    if not joined:
        return ""
    if len([n for n in nums if n]) == 1:
        return (
            f"Zadatak {joined} je djelimično tačan."
            if has_partial
            else f"Zadatak {joined} je tačan."
        )
    return (
        f"Zadaci {joined} su tačni ili djelimično tačni."
        if has_partial
        else f"Zadaci {joined} su tačni."
    )


_ORDINAL_LINE_PREFIXES = {
    1: r"prv\w*",
    2: r"drug\w*",
    3: r"tre[cć]\w*",
    4: r"(?:cetvrt|četvrt)\w*",
    5: r"pet\w*",
    6: r"(?:sest|šest)\w*",
    7: r"sedm\w*",
    8: r"osm\w*",
    9: r"devet\w*",
}


def _prefix_missing_ordinal_lines(text: str, missing: list[int]) -> str:
    out = text
    for n in missing:
        if not n or re.search(rf"(?m)^\s*{n}[.)]\s+", out):
            continue
        ordinal = _ORDINAL_LINE_PREFIXES.get(n)
        if not ordinal:
            continue
        pattern = re.compile(
            rf"(?im)^(\s*)({ordinal}\s+(?:stavk\w*|zadat\w*|pitanj\w*)\b)"
        )
        out = pattern.sub(rf"\g<1>{n}. \g<2>", out, count=1)
    return out


def _renumber_numbered_lines(text: str) -> str:
    counter = 0

    def repl(match: re.Match) -> str:
        nonlocal counter
        counter += 1
        return f"{match.group(1)}{counter}. "

    return re.sub(r"(?m)^(\s*)\d{1,2}[.)]\s+", repl, text)


def _body_mentions_missing_items(text: str, missing: list[int]) -> bool:
    folded = fold_diacritics(text)
    for n in missing:
        if not n:
            continue
        numbered_waits = re.search(rf"(?m)^\s*{n}[.)].{{0,180}}\bcek", folded)
        named_waits = re.search(rf"\bzadatak\s+{n}\b.{{0,180}}\bcek", folded)
        if not (numbered_waits or named_waits):
            return False
    return True


def _make_multi_missing(answer: str, result: Any) -> str:
    subset = _correct_subset_with_missing(result)
    if not subset:
        return answer
    answered, missing, has_partial = subset
    body = answer.strip()
    stripped = _strip_positive_label(body)
    if stripped != body:
        body = stripped
    body = _prefix_missing_ordinal_lines(body, missing)
    body = _renumber_numbered_lines(body)
    summary_parts = [_answered_subset_sentence(answered, has_partial)]
    if not _body_mentions_missing_items(body, missing):
        summary_parts.append(_missing_sentence(missing))
    summary = " ".join(p for p in summary_parts if p)
    if summary and not fold_diacritics(body).startswith(fold_diacritics(summary)):
        return _prepend(summary, body)
    return body


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


def _first_sentence(text: str) -> str:
    m = re.match(r"[^.!?\n]*[.!?\n]?", text.strip())
    return m.group(0) if m else text[:80]


def _starts_positive(text: str) -> bool:
    """Prva rečenica sadrži potvrdu tačnosti (odgovor POČINJE pozitivno)."""
    head = fold_diacritics(_first_sentence(text))
    return bool(_POS_GRADE_RE.search(_mask(head, _neg_spans(head))))


# Uvod tipa "Pogledajmo/Provjerimo/Hajde da riješimo zajedno …" — prikladan dok
# provjeravamo, ali NE za odgovor koji je već potvrđeno tačan (BUG: stil).
# Skida SAMO uvodnu frazu (glagol + par pratećih riječi + interpunkcija), NIKAD
# do kraja rečenice — da ne pojede provjeru računa koja slijedi u istoj rečenici.
_HEDGE_OPENER_RE = re.compile(
    r"^\s*(?:e\s+)?(?:pa\s+)?(?:hajde\s+da\s+|hajdemo\s+da\s+|idemo\s+(?:da\s+)?|da\s+)?"
    r"(?:pogledaj\w*|provjeri\w*|provjerimo|vidimo|rijesi\w*|rijesimo|"
    r"izracunaj\w*|racunajmo|krenimo|krecemo)"
    r"(?:\s+(?:ovaj|ovu|ovo|taj|to|ga|je|zajedno|zadatak\w*|primjer\w*|rezultat\w*))*"
    r"\s*[:,.\-—]*\s*"
)


def _make_positive(answer: str) -> str:
    """Autoritativno TAČNO: skini lažno negativne ocjene i garantuj potvrdan,
    prirodan uvod ("Tačno. …"), bez uvodnog "Pogledajmo zajedno …"."""
    out, _changed = _remove_negative_verdicts(answer)
    out = out.strip()
    if not out:
        return "Tačno. Tvoj odgovor je tačan."
    # skini uvodni hedge samo ako sam po sebi ne nosi potvrdu tačnosti
    hedge = _HEDGE_OPENER_RE.match(fold_diacritics(out))
    if hedge and not _starts_positive(out):
        stripped = out[hedge.end():].lstrip()
        if stripped:
            out = stripped
    stripped = _strip_positive_label(out)
    if stripped != out:
        return _prepend("Tačno.", stripped)
    return _prepend("Tačno.", out)


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
_POSITIVE_LABEL_OPENER_RE = re.compile(
    r"^\s*(tacno|tocno|bravo|odlicno|super|svaka\s+cast|tako\s+je)\b[^.!?\n]*[.!?]?"
)
_POSITIVE_IS_RE = re.compile(r"^\s*(?:tacno|tocno)\s+je\s+")
_PARTIAL_OPENER_RE = re.compile(r"^\s*djelimicn\w*\s+taca?n\w*\.?")


def _strip_positive_label(text: str) -> str:
    folded = fold_diacritics(text)
    m = _POSITIVE_IS_RE.match(folded)
    if m:
        return text[m.end():].lstrip(" .!?:;-")
    m = _POSITIVE_LABEL_OPENER_RE.match(folded)
    if m:
        return text[m.end():].lstrip(" .!?:;-")
    return text


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


def _make_incorrect(answer: str) -> str:
    """Autoritativno NETAČNO: stabilna prva labela bez pozitivnog uvoda."""
    folded = fold_diacritics(answer)
    stripped = _strip_positive_label(answer)
    if stripped != answer:
        answer = stripped
        return _prepend("Netačno.", answer)
    spans = _neg_spans(folded)
    if spans and spans[0][0] <= 3:
        _s, e = spans[0]
        rest = answer[e:].lstrip(" .!?:;-")
        return _prepend("Netačno.", rest)
    return _prepend("Netačno.", answer.strip())


def _make_partial(answer: str) -> str:
    """Autoritativno DJELIMIČNO: vrijednost je dobra, ali traženi oblik nije."""
    out, _changed = _remove_negative_verdicts(answer)
    folded = fold_diacritics(out)
    partial = _PARTIAL_OPENER_RE.match(folded)
    if partial:
        rest = out[partial.end():].lstrip(" .!?:;-")
        return _prepend("Djelimično tačno.", rest)
    out = _strip_positive_label(out)
    return _prepend("Djelimično tačno.", out.strip())


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

        # Vrijednost je ekvivalentna, ali traženi oblik nije zadovoljen (npr.
        # skraćivanje nije do kraja ili proširivanje nije na traženi nazivnik).
        # To nije puno "Tačno", nego stabilna djelimična ocjena.
        if _all_items_partial_or_correct(check_result):
            return _make_partial(answer)

        if _correct_subset_with_missing(check_result):
            return _make_multi_missing(answer, check_result)

        # Miješano (neke tačne, neke ne) ili višestavkovni kontekst → po-stavkovna
        # ocjena je legitimna, tekst se ne dira.
        if verdict == "mixed" or multi:
            return answer

        if verdict == "incorrect":
            return _make_incorrect(answer)

        # unknown / djelimično provjereno (jedna stavka): samo ako se odgovor
        # SAM SEBI protivrječi — makni negativno da ne bude lažno negativno.
        if has_grade_contradiction(answer):
            return _neutralize_negative(answer)
        return answer
    except Exception:
        return answer
