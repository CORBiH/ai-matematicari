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
- ``step`` (tačan MEĐUKORAK: tvrdnja ekvivalentna zadatku, nedovršena — npr.
  "2x < 12" za "2x − 5 < 7") → bez ocjenskih labela; tvrdnje o grešci
  (i meke: "došlo je do male greške") se brišu, odgovor počinje potvrdom koraka.
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
    "neutralize_non_answer_grade",
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
    # "pogrešno" i ijek. "pogriješio/pogrješka", ali NE "nije pogrešno"
    r"|(?<!nije )(?<!nisu )pogr(?:e|ije|je)s\w*"
    r"|\bwrong\b|\bincorrect\b"
)

# Meke tvrdnje o grešci ("došlo je do male greške", "tu nešto ne štima") —
# nisu ocjenske labele, ali za učenika znače isto što i "Netačno". Uklanjaju se
# kad je presuda TAČNO ili TAČAN MEĐUKORAK. Namjerno usko: golo "greška"
# ("greška je dio učenja" iz empatije) se NE dira.
_SOFT_NEG_RE = re.compile(
    r"(?:doslo\s+(?:je\s+)?do|tu\s+(?:je|se)|imas|napravio\s+si|napravila\s+si|"
    r"potkrala\s+(?:ti\s+)?se|desila\s+(?:ti\s+)?se|vidim)\s+"
    r"(?:mal[aeu]|sitn[aeu]|jedn[aeu])?\s*gres[kc]\w*"
    r"|(?:tu\s+)?nesto\s+ne\s+stima|tu\s+ne\s+stima"
)
# Nakon maskiranja negativnih: samo jasne potvrde tačnosti (ne generičko "dobro").
_POS_GRADE_RE = re.compile(
    r"\btaca?n\w*|\btoca?n\w*"             # tačno/tačan/tačna, točno
    r"|\bisprav\w*"                        # ispravno/ispravan
    r"|\bbravo\b|\bodlicno\b|\bsvaka\s+cast\b|\btako\s+je\b"
    r"|\bcorrect\b"
)


# Fold koji ČUVA DUŽINU (bez strip-a) — indeksi iz foldanog teksta moraju biti
# poravnati s originalom za bezbedno brisanje spanova. ``fold_diacritics`` iz
# topic_detector radi i strip pa dužine divergiraju na tekstu s okolnim \n.
_DIACRITIC_MAP = str.maketrans({
    "č": "c", "ć": "c", "đ": "d", "š": "s", "ž": "z",
    "Č": "c", "Ć": "c", "Đ": "d", "Š": "s", "Ž": "z",
})


def _fold_keep_len(text: Any) -> str:
    return str(text or "").translate(_DIACRITIC_MAP).lower()


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

_POSITIVE_VERDICTS = (
    "correct", "correct_equivalent_form", "correct_missing_notation",
    "correct_missing_unit",
)
_PARTIAL_VERDICTS = ("correct_value_wrong_form", "partially_correct")
_INCOMPLETE_VERDICTS = ("incomplete",)
_INCORRECT_VERDICTS = ("incorrect", "wrong_unit")
# Tačan MEĐUKORAK (ekvivalentna tvrdnja, nedovršen oblik) — nije ni puno
# "Tačno" (stavka ostaje pending) ni "Netačno" (tvrdnja je istinita).
_STEP_VERDICTS = ("correct_step",)


def authoritative_verdict(result: Any) -> str:
    """``CheckResult`` → jedan sud: correct | partial | step | incomplete |
    incorrect | mixed | unknown.

    "mixed" = više ocijenjenih stavki gdje su neke tačne, a neke ne (legitimna
    po-stavkovna ocjena). "step" = tačan međukorak (bez ijedne netačne).
    "unknown" = kod nema pouzdanu presudu."""
    if result is None or not getattr(result, "checkable", False):
        return "unknown"
    graded = [
        i.verdict
        for i in getattr(result, "items", [])
        if i.verdict in _POSITIVE_VERDICTS
        or i.verdict in _PARTIAL_VERDICTS
        or i.verdict in _STEP_VERDICTS
        or i.verdict in _INCOMPLETE_VERDICTS
        or i.verdict in _INCORRECT_VERDICTS
    ]
    if not graded:
        return "unknown"
    has_incorrect = any(v in _INCORRECT_VERDICTS for v in graded)
    has_correct = any(v in _POSITIVE_VERDICTS for v in graded)
    has_partial = any(v in _PARTIAL_VERDICTS for v in graded)
    has_step = any(v in _STEP_VERDICTS for v in graded)
    has_incomplete = any(v in _INCOMPLETE_VERDICTS for v in graded)
    if (has_incorrect or has_incomplete) and (has_correct or has_partial or has_step):
        return "mixed"
    if has_incorrect:
        return "incorrect"
    if has_incomplete:
        return "incomplete"
    if has_step:
        return "step"
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


def _step_confirmed_result(result: Any) -> bool:
    """Bar jedna stavka je TAČAN MEĐUKORAK, a nijedna nije netačna/djelimična —
    svaka tvrdnja o grešci u odgovoru je tada LAŽNA i smije se ukloniti."""
    if result is None or not getattr(result, "checkable", False):
        return False
    items = getattr(result, "items", [])
    if not items or not any(i.verdict in _STEP_VERDICTS for i in items):
        return False
    return all(
        i.verdict in _STEP_VERDICTS
        or i.verdict in _POSITIVE_VERDICTS
        or i.verdict in ("missing", "not_attempted")
        for i in items
    )


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


# Formulacije "još se čeka odgovor" koje model prirodno varira — ako ih tijelo
# već sadrži za SVAKU stavku, sažetak se ne duplira (2026-07-14: live nalaz,
# guard je prepend-ao "Zadaci 2 i 3 još čekaju..." iako je model to već rekao).
_WAIT_PHRASE = (
    r"(?:cek\w*|nij?e\s+rij?esen\w*|nisu\s+rij?esen\w*"
    r"|nisi\s+(?:odgovori\w*|rij?esi\w*|pokusa\w*|posla\w*)"
    r"|jos\s+nema\w*|posalji|preosta\w*)"
)


def _body_asserts_answered_items(text: str, answered: list[int]) -> bool:
    """Početak tijela već tvrdi da su odgovorene stavke tačne ("Zadatak 1 je
    tačan, a zadaci 2 i 3 još čekaju...") — sažetak se tada ne duplira."""
    head = fold_diacritics(text)[:250]
    for n in answered:
        if not n:
            continue
        if not re.search(
            rf"\b(?:zada\w*|stavk\w*|pitanj\w*)\s+"
            rf"(?:\d{{1,2}}[.)]?\s*(?:,\s*|i\s+)?)*{n}[.)]?\b.{{0,80}}\btac",
            head,
        ):
            return False
    return True


def _body_mentions_missing_items(text: str, missing: list[int]) -> bool:
    folded = fold_diacritics(text)
    for n in missing:
        if not n:
            continue
        numbered_waits = re.search(
            rf"(?m)^\s*{n}[.)].{{0,180}}\b{_WAIT_PHRASE}", folded
        )
        # "zadatak 2 još čeka", "zadaci 2. i 3. još čekaju", "stavka 3 nije riješena"
        named_waits = re.search(
            rf"\b(?:zada\w*|stavk\w*|pitanj\w*)\s+"
            rf"(?:\d{{1,2}}[.)]?\s*(?:,\s*|i\s+)?)*{n}[.)]?\b"
            rf".{{0,180}}\b{_WAIT_PHRASE}",
            folded,
        )
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
    if not has_partial:
        # sve ODGOVORENE stavke su tačne, ostale nisu ni pokušane → svaka
        # negativna ocjena u tekstu je lažna (2026-07-14: "Netačno, ali blizu
        # si" na tačan odgovor jedine odgovorene stavke)
        cleaned, changed = _remove_negative_verdicts(body)
        if changed and cleaned.strip():
            body = cleaned
        body = _remove_soft_negatives(body)
    body = _prefix_missing_ordinal_lines(body, missing)
    body = _renumber_numbered_lines(body)
    summary_parts = []
    if not _body_asserts_answered_items(body, answered):
        summary_parts.append(_answered_subset_sentence(answered, has_partial))
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
    folded = _fold_keep_len(text)
    if len(folded) != len(text):
        return text, False
    spans = _neg_spans(folded)
    if not spans:
        return text, False
    chars = list(text)
    for s, e in sorted(spans, reverse=True):
        del chars[s:e]
    return _cleanup("".join(chars)), True


def _remove_soft_negatives(text: str) -> str:
    """Ukloni meke tvrdnje o grešci ("došlo je do male greške") — koristi se
    SAMO kad autoritativni sud kaže da greške NEMA (correct / correct_step)."""
    folded = _fold_keep_len(text)
    if len(folded) != len(text):
        return text
    spans = [m.span() for m in _SOFT_NEG_RE.finditer(folded)]
    if not spans:
        return text
    chars = list(text)
    for s, e in sorted(spans, reverse=True):
        del chars[s:e]
    return _cleanup("".join(chars))


_SENTENCE_PIECES_RE = re.compile(r"([.!?]+[\s\n]+|\n+)")


def _drop_error_claim_sentences(text: str) -> str:
    """Izbaci CIJELE rečenice koje su čista tvrdnja o grešci ("Izgleda da je tu
    došlo do male greške.") — fraza-brisanje bi ostavilo batrljak ("Izgleda da
    je tu."). Rečenica se izbacuje samo ako je kratka i ne nosi račun
    (bez cifara i LaTeX-a), da se nikad ne obriše matematički sadržaj."""
    pieces = _SENTENCE_PIECES_RE.split(text)
    out: list[str] = []
    i = 0
    while i < len(pieces):
        sentence = pieces[i]
        separator = pieces[i + 1] if i + 1 < len(pieces) else ""
        folded = fold_diacritics(sentence)
        is_claim = bool(_SOFT_NEG_RE.search(folded) or _NEG_GRADE_RE.search(folded))
        carries_math = bool(re.search(r"\d|\\\(|\\\[", sentence))
        if is_claim and not carries_math and len(sentence) <= 160:
            i += 2
            continue
        out.append(sentence)
        out.append(separator)
        i += 2
    result = "".join(out).strip()
    return result if result else text


def _has_positive_verdict(text: str) -> bool:
    folded = fold_diacritics(text)
    return bool(_POS_GRADE_RE.search(_mask(folded, _neg_spans(folded))))


def _prepend(opener: str, text: str) -> str:
    body = text.lstrip()
    if not body:
        return opener
    if fold_diacritics(body).startswith("zadatak:"):
        return f"{opener}\n{body}"
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


_PRAISE_ONLY_RE = re.compile(
    r"^(odlicno|bravo|super|sjajno|svaka\s+cast|odlican\s+posao|tako\s+je|"
    r"bas\s+lijepo|perfektno)\b[^.!?]*[.!?]*$"
)


def _drop_praise_sentences(text: str) -> str:
    """Ukloni rečenice koje su ČISTA pohvala (bez matematičkog sadržaja).

    Poslije autoritativnog "Netačno." zaostala pohvala ("Odlično si to uradio.")
    direktno protivrječi ocjeni — mora otpasti (Phase 7 nalaz)."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = [
        p for p in parts
        if not (_PRAISE_ONLY_RE.match(fold_diacritics(p).strip()) and not re.search(r"\d", p))
    ]
    return _cleanup(" ".join(kept)) if kept else ""


def _drop_dangling_fragment(text: str) -> str:
    """Poslije brisanja fraze ocjene može ostati besmislen ostatak ("Si.").

    Konzervativno: odbaci SAMO vrlo kratak ostatak bez brojeva/matematike."""
    body = text.strip()
    if not body:
        return ""
    words = re.findall(r"\w+", body)
    if len(words) <= 2 and len(body) <= 14 and not re.search(r"[\d=+\-*/<>]", body):
        return ""
    return body


def _make_positive(answer: str) -> str:
    """Autoritativno TAČNO: skini lažno negativne ocjene i garantuj potvrdan,
    prirodan uvod ("Tačno. …"), bez uvodnog "Pogledajmo zajedno …".

    BUG 5 (2026-07-10): uklanjaju se i "Djelimično tačno." labele iz tijela —
    ranije je "Djelimično tačno. …" dobio prefiks pa je ispalo
    "Tačno. Djelimično tačno."."""
    out, _changed = _remove_negative_verdicts(answer)
    out = _remove_soft_negatives(out)
    out = _remove_partial_labels(out).strip()
    out = _drop_dangling_fragment(out)          # "Tačno. Si." → "Tačno. …"
    if not out:
        return "Tačno. Tvoj odgovor je tačan."
    # skini uvodni hedge samo ako sam po sebi ne nosi potvrdu tačnosti
    hedge = _HEDGE_OPENER_RE.match(fold_diacritics(out))
    if hedge and not _starts_positive(out):
        stripped = out[hedge.end():].lstrip()
        if stripped:
            out = stripped
    out = _strip_leading_labels(out) or out
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
_PARTIAL_OPENER_RE = re.compile(r"^\s*dj?el[io]micn\w*\s+taca?n\w*\.?")
# Labela "Djelimično tačno."/"Djelomično točno." BILO GDJE u tekstu (foldano).
_PARTIAL_LABEL_ANY_RE = re.compile(r"dj?el[io]micn\w*\s+taca?n\w*[.!:]?")
# Gole ocjenjivačke labele na početku ("Tačno.", "Netačno.", "Djelimično tačno.")
_LEADING_LABEL_RE = re.compile(
    r"^\s*(?:netaca?n\w*|taca?no|toca?no|dj?el[io]micn\w*\s+taca?n\w*)\s*[.!:]\s*"
)


def _strip_leading_labels(text: str) -> str:
    """Skini SVE uzastopne ocjenjivačke labele s početka ("Tačno. Djelimično
    tačno. ..." → "..."), da pozivalac postavi tačno JEDNU autoritativnu."""
    out = text
    for _ in range(4):                     # više od 4 uzastopne labele ne postoji
        folded = fold_diacritics(out)
        m = _LEADING_LABEL_RE.match(folded)
        if not m:
            break
        out = out[m.end():].lstrip(" .!?:;-")
    return out


def _remove_partial_labels(text: str) -> str:
    """Ukloni "Djelimično tačno." labele iz TIJELA teksta (poslije autoritativne
    presude "correct" nijedna djelimična labela nije legitimna) — BUG 5."""
    folded = _fold_keep_len(text)
    if len(folded) != len(text):
        return text
    spans = [m.span() for m in _PARTIAL_LABEL_ANY_RE.finditer(folded)]
    if not spans:
        return text
    chars = list(text)
    for s, e in sorted(spans, reverse=True):
        del chars[s:e]
    return _cleanup("".join(chars))


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
    stripped = _strip_leading_labels(answer)
    if stripped == answer:
        stripped = _strip_positive_label(answer)
    if stripped != answer and stripped:
        # Phase 7: zaostala pohvala protivrječi "Netačno." — ukloni je.
        stripped = _drop_dangling_fragment(_drop_praise_sentences(stripped))
        if not stripped:
            return "Netačno. Hajde da provjerimo postupak korak po korak."
        return _prepend("Netačno.", stripped)
    folded = fold_diacritics(answer)
    spans = _neg_spans(folded)
    if spans and spans[0][0] <= 3:
        _s, e = spans[0]
        rest = answer[e:].lstrip(" .!?:;-")
        return _prepend("Netačno.", rest)
    return _prepend("Netačno.", answer.strip())


def _make_incomplete(answer: str) -> str:
    """Autoritativno NEPOTPUNO: odgovor je matematički povezan, ali nije rješenje."""
    out, _changed = _remove_negative_verdicts(answer)
    out = _strip_leading_labels(out) or out.strip()
    if not out:
        return "Nepotpuno. Dao si primjer koji odgovara uslovu, ali treba napisati cijelo rješenje."
    return _prepend("Nepotpuno.", out.strip())


def _make_partial(answer: str) -> str:
    """Autoritativno DJELIMIČNO: vrijednost je dobra, ali traženi oblik nije.

    Skida SVE uzastopne vodeće labele ("Tačno. Djelimično tačno. …") pa
    postavlja tačno jednu — bez dupliranja (BUG 5)."""
    out, _changed = _remove_negative_verdicts(answer)
    out = _strip_leading_labels(out) or out.strip()
    return _prepend("Djelimično tačno.", out.strip())


def _make_step_confirmed(answer: str) -> str:
    """Autoritativno TAČAN MEĐUKORAK: učenikova tvrdnja je istinita, samo nije
    dovršena. Svaka tvrdnja o grešci je LAŽNA — briše se (i meke: "došlo je do
    male greške"), ocjenske labele se skidaju (međukorak ne dobija ni "Tačno."
    ni "Netačno."), a odgovor mora POČETI potvrdom koraka."""
    out = _strip_leading_labels(answer) or answer.strip()
    out = _drop_error_claim_sentences(out)
    out, _changed = _remove_negative_verdicts(out)
    out = _remove_soft_negatives(out)
    out, _changed = _remove_value_negations(out)
    out = _remove_partial_labels(out).strip()
    if not out:
        return "Tako je — taj korak je tačan. Nastavi do kraja!"
    if not _starts_positive(out):
        out = _prepend("Tako je — taj korak je tačan.", out)
    return out


# --- Po-stavkovno pomirenje u višestavkovnom kontekstu (BUG 13) ---------------------
# "mixed"/multi odgovori su se ranije vraćali NETAKNUTI, pa je kontradikcija
# UNUTAR jedne stavke ("Netačno. … Tvoj odgovor je tačan!" ili "nije 12. Tačan
# rezultat je 12.") prolazila. Ovdje se svaki numerisani segment pomiruje
# posebno — legitimna miješana ocjena (1. tačno, 2. netačno) se NE dira.

# Pozitivna ocjena UPUĆENA UČENIKU (ne korekcija "tačan rezultat je X"):
_STUDENT_POS_RE = re.compile(
    r"tvoj\w*\s+(?:odgovor|rezultat|racun)\w*\s+(?:\w+\s+){0,3}?je\s+taca?n"
    r"|tvoj\w*\s+(?:odgovor|rezultat)\s+je\s+ispravan"
    r"|to\s+je\s+tacno|u\s+pravu\s+si|odgovor\s+je\s+tacan"
)
# "nije 12" — vrijednost koju negativna fraza negira.
_NEGATED_VALUE_RE = re.compile(r"ni(?:je|su)\s+(-?\d[\w/,.]*)")
_AFFIRMED_VALUE_RE = re.compile(
    r"taca?n\w*\s+(?:rezultat|odgovor)\w*\s+je\s+(-?\d[\w/,.]*)"
)
_SEGMENT_SPLIT_RE = re.compile(r"(?m)(?=^\s*\d{1,2}[.)]\s)")


def _same_value_conflict(folded: str) -> set[str]:
    """Vrijednosti koje su u ISTOM tekstu i negirane ("nije 12") i potvrđene
    ("tačan rezultat je 12") — intrinzično samo-protivrječje."""
    negated = {m.group(1).rstrip(".,;:!?") for m in _NEGATED_VALUE_RE.finditer(folded)}
    affirmed = {m.group(1).rstrip(".,;:!?") for m in _AFFIRMED_VALUE_RE.finditer(folded)}
    return negated & affirmed


def _segment_self_contradicts(segment: str) -> bool:
    """Kontradikcija UNUTAR segmenta koja je sigurno samo-protivrječje:
    (a) negativna ocjena + pozitivna ocjena upućena učeniku, ili
    (b) "nije X … tačan rezultat je X" za ISTU vrijednost X."""
    folded = _fold_keep_len(segment)
    spans = _neg_spans(folded)
    if spans and _STUDENT_POS_RE.search(_mask(folded, spans)):
        return True
    return bool(_same_value_conflict(folded))


def _remove_value_negations(text: str) -> tuple[str, bool]:
    """Ukloni "nije X" fraze za vrijednosti koje isti tekst odmah potvrđuje
    ("tačan rezultat je X") — negacija je sigurno lažna (BUG 13, S2 slučaj)."""
    folded = _fold_keep_len(text)
    if len(folded) != len(text):
        return text, False
    conflict = _same_value_conflict(folded)
    if not conflict:
        return text, False
    spans = [
        m.span()
        for m in _NEGATED_VALUE_RE.finditer(folded)
        if m.group(1).rstrip(".,;:!?") in conflict
    ]
    if not spans:
        return text, False
    chars = list(text)
    for s, e in sorted(spans, reverse=True):
        del chars[s:e]
    return _cleanup("".join(chars)), True


def _reconcile_multi_item(answer: str, check_result: Any) -> str:
    """Pomiri višestavkovni odgovor segment po segment.

    Segment = tekst od jednog numerisanog markera do sljedećeg (plus uvod prije
    prvog markera). Presuda po stavci (kad postoji) je autoritativna; bez nje
    se ispravlja samo SIGURNO samo-protivrječje unutar segmenta."""
    segments = _SEGMENT_SPLIT_RE.split(answer)
    if len(segments) <= 1:
        # nema numerisanih segmenata — tretiraj cijeli tekst kao jedan segment
        segments = [answer]

    verdict_by_n: dict[int, str] = {}
    for item in getattr(check_result, "items", []) or []:
        n = getattr(item, "n", None)
        if n:
            verdict_by_n[n] = getattr(item, "verdict", "")

    out_segments: list[str] = []
    for seg in segments:
        m = re.match(r"\s*(\d{1,2})[.)]\s", seg)
        n = int(m.group(1)) if m else None
        verdict = verdict_by_n.get(n, "")
        cleaned = None
        if verdict in _POSITIVE_VERDICTS:
            # stavka je provjereno tačna → negativne fraze u njoj su lažne
            candidate, changed = _remove_negative_verdicts(seg)
            if changed and candidate.strip():
                cleaned = candidate
        elif verdict in _INCOMPLETE_VERDICTS:
            cleaned = _make_incomplete(seg)
        elif verdict in _INCORRECT_VERDICTS and _starts_positive(seg):
            cleaned = _make_incorrect(seg)
        elif _segment_self_contradicts(seg):
            candidate, changed = _remove_negative_verdicts(seg)
            if not changed:
                # "nije 12 … tačan rezultat je 12" — negacija vrijednosti,
                # ne fraza ocjene; ukloni samo lažnu negaciju
                candidate, changed = _remove_value_negations(seg)
            if changed and candidate.strip():
                cleaned = candidate
        if cleaned is None:
            out_segments.append(seg)
        else:
            # _cleanup skida završne praznine — vrati original trailing
            # whitespace da se segmenti ne zalijepe u isti red
            trail = seg[len(seg.rstrip()):]
            out_segments.append(cleaned.rstrip() + trail)
    return "".join(out_segments)


def neutralize_non_answer_grade(answer: Any) -> str:
    """Ukloni ocjensku labelu s odgovora na PORUKU KOJA NIJE POKUŠAJ RJEŠAVANJA
    (refleksija "nisam znao da li se sabira ili oduzima", meta-komentar,
    odgovor na tutorovo pitanje "Gdje misliš da je zapelo?").

    Fix 3 (2026-07-14, screenshot 1): checker se tu suzdrži (nema odgovora za
    provjeru), a model je stohastično lijepio "Netačno." na ne-odgovor. Prompt
    to zabranjuje, ali molba nije garancija — ovo je provođenje. Skida vodeće
    labele ("Tačno."/"Netačno.") i vodeću negativnu frazu ("Netačno, ali blizu
    si"), NE dodaje novu ocjenu. Idempotentno; nikad ne baca izuzetak."""
    if not isinstance(answer, str) or not answer.strip():
        return answer
    try:
        out = (_strip_leading_labels(answer) or answer).strip()
        neg, _pos = grade_contradiction_phrases(out)
        if neg:
            # vodeća/uvodna negativna fraza ("Netačno, ali blizu si …") → makni
            # samu frazu (bez uvodnog "Hajde da provjerimo" — ovo nije osporena
            # presuda, nego poruka koja se uopšte ne ocjenjuje).
            cleaned, changed = _remove_negative_verdicts(out)
            if changed and cleaned.strip():
                out = cleaned.strip()
        # lažni "Tačno je …" uvod na ne-odgovor isto ne pripada. NE diramo opću
        # toplinu ("Odlično pitanje!", "Bravo") — to nije ocjena odgovora, a
        # ocjenske labele ("Tačno.") je već skinuo _strip_leading_labels.
        folded = fold_diacritics(out)
        m = _POSITIVE_IS_RE.match(folded)
        if m:
            out = out[m.end():].lstrip(" .!?:;-") or out
        return out or answer
    except Exception:
        return answer


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

        # Tačan MEĐUKORAK (CLASS 1): tvrdnja je istinita, samo nedovršena —
        # nijedna tvrdnja o grešci nije legitimna. Prije multi-grane, jer
        # atribuirani rezultat ima i not_attempted stavke.
        if _step_confirmed_result(check_result):
            return _make_step_confirmed(answer)

        if _correct_subset_with_missing(check_result):
            return _make_multi_missing(answer, check_result)

        # Miješano ili višestavkovni kontekst → po-stavkovna ocjena je legitimna,
        # ali kontradikcija UNUTAR JEDNE stavke nije: pomiri segment po segment.
        if verdict == "mixed" or multi:
            return _reconcile_multi_item(answer, check_result)

        if verdict == "incomplete":
            return _make_incomplete(answer)

        if verdict == "incorrect":
            return _make_incorrect(answer)

        # unknown / djelimično provjereno (jedna stavka): samo ako se odgovor
        # SAM SEBI protivrječi — makni negativno da ne bude lažno negativno.
        if has_grade_contradiction(answer):
            return _neutralize_negative(answer)
        return answer
    except Exception:
        return answer


# --------------------------------------------------------------------------- #
# Feedback must not contradict the student's own text                          #
# --------------------------------------------------------------------------- #
class _PiPresence:
    """Adapter so the guard reuses ``symbolic.mentions_pi`` verbatim."""

    def search(self, text: Any):
        from matbot import symbolic
        return symbolic.mentions_pi(text) or None


#: Notation a claim can be made about. Each entry maps a detector for "the
#: student wrote it" to the phrases that assert they did not. The presence
#: detector is the SAME one the checker uses, so the guard and the grade can
#: never disagree about whether the student wrote π.
_ABSENCE_CLAIMS: tuple[tuple[str, Any, "re.Pattern"], ...] = (
    (
        "pi",
        _PiPresence(),
        re.compile(
            r"(?:[^.!?\n]*\b(?:nedostaje|nisi\s+(?:napisao|naveo|uklju[cčć]io)|"
            r"izostavio\s+si|fali|bez)\b[^.!?\n]*(?:π|\bpi\b)[^.!?\n]*[.!?]?)"
            r"|(?:[^.!?\n]*(?:π|\bpi\b)[^.!?\n]*\b(?:nedostaje|fali|nije\s+"
            r"napisan\w*|nije\s+naveden\w*)\b[^.!?\n]*[.!?]?)",
            re.IGNORECASE),
    ),
)


def strip_false_absence_claims(answer: Any, student_text: Any) -> str:
    """Remove "you didn't write X" when the student demonstrably did.

    Production told a student π was missing from "4pi cm". A verdict guard cannot
    catch that: the sentence is not a grade, it is a false statement about the
    student's own text. Only claims proven false by that text are removed;
    everything else is left untouched.
    """
    if not isinstance(answer, str) or not answer.strip():
        return answer
    student = str(student_text or "")
    if not student.strip():
        return answer
    try:
        out = answer
        for _name, present_re, claim_re in _ABSENCE_CLAIMS:
            if not present_re.search(student):
                continue                    # the claim may well be true
            out = claim_re.sub("", out)
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
        return out or answer
    except Exception:
        return answer
