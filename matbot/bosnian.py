"""Mala jezička zaštita: najčešći ekavski/srpski oblici → bosanska ijekavica.

Sistemska prompt pravila već traže ijekavicu, ali model povremeno "procuri"
("deo", "rešenje", "vežba") ili starije termine za razlomke. Ovo je zadnja
linija odbrane SAMO za vrlo česte i jednoznačne oblike — namjerno kratka lista
sa granicama riječi, da se ništa ne prekoriguje (npr. "video" sadrži "deo" ali
ga \\b štiti).
"""
from __future__ import annotations

import re

__all__ = ["to_ijekavica"]

# (ekavski obrazac, ijekavska zamjena) — poredak nebitan, obrasci disjunktni.
# Samo oblici koji su NEDVOSMISLENO ekavski; dvosmislene riječi se ne diraju.
_REPLACEMENTS: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(rf"\b{pat}\b"), repl)
    for pat, repl in (
        (r"deo", "dio"),
        (r"dela", "dijela"),
        (r"delu", "dijelu"),
        (r"delovi", "dijelovi"),
        (r"delova", "dijelova"),
        (r"delovima", "dijelovima"),
        (r"rešenj(\w*)", r"rješenj\1"),
        (r"rešiti", "riješiti"),
        (r"reši", "riješi"),
        (r"vežb(\w*)", r"vježb\1"),
        (r"deljenj(\w*)", r"dijeljenj\1"),
        (r"deljiv(\w*)", r"djeljiv\1"),
        (r"deliti", "dijeliti"),
        (r"podeli", "podijeli"),
        (r"podeliti", "podijeliti"),
        (r"celi", "cijeli"),
        (r"cela", "cijela"),
        (r"celo", "cijelo"),
        (r"celu", "cijelu"),
        (r"celih", "cijelih"),
        (r"ceo", "cio"),
        (r"celin(\w*)", r"cjelin\1"),
        (r"primer(\w*)", r"primjer\1"),
        (r"sledeć(\w*)", r"sljedeć\1"),
        (r"sledec(\w*)", r"sljedec\1"),
        # 2026-07-18 (jezička konzistentnost): česti srpski/ekavski oblici prijavljeni
        # sa produkcije. "razume-" → "razumije-"; "obe" → "obje"; "devoj-" → "djevoj-".
        (r"razumem", "razumijem"),
        (r"razumemo", "razumijemo"),
        (r"razumeš", "razumiješ"),
        (r"razumeju", "razumiju"),
        (r"razume", "razumije"),
        (r"obe", "obje"),
        (r"devoj(\w*)", r"djevoj\1"),
        # Diacritic-stripped redni broj "treci ugao" → "treći ugao" (matematički
        # termin "ugao" ostaje netaknut; samo redni broj dobija ć).
        (r"trec(eg|em|oj|om|ih|im|i|a|e|u)", r"treć\1"),
        (r"uvek", "uvijek"),
        (r"posle", "poslije"),
        (r"ovde", "ovdje"),
        (r"gde", "gdje"),
        (r"dve", "dvije"),
        (r"lepo", "lijepo"),
        (r"uspeh", "uspjeh"),
        (r"vrednost(\w*)", r"vrijednost\1"),
        (r"promenljiv(\w*)", r"promjenljiv\1"),
        (r"prover(a|e|i|u|om|avaj\w*|imo|iti|it)", r"provjer\1"),  # provera→provjera (ne dira 'provjer')
        # 2026-07-10: oblici uhvaćeni na Farisovim testovima
        (r"umesto", "umjesto"),
        (r"poslednj(\w*)", r"posljednj\1"),
        (r"netočn(\w*)", r"netačn\1"),
        (r"točn(\w*)", r"tačn\1"),
        (r"primjerice", "na primjer"),
        (r"brojiteljem", "brojnikom"),
        (r"imeniteljem", "nazivnikom"),
        (r"brojitelj(\w*)", r"brojnik\1"),
        (r"imenitelj(\w*)", r"nazivnik\1"),
        (r"brojilac", "brojnik"),
        (r"brojioca", "brojnika"),
        (r"brojiocu", "brojniku"),
        (r"brojiocem", "brojnikom"),
        (r"brojioci", "brojnici"),
        (r"brojilaca", "brojnika"),
        (r"brojiocima", "brojnicima"),
        (r"imenilac", "nazivnik"),
        (r"imenioca", "nazivnika"),
        (r"imeniocu", "nazivniku"),
        (r"imeniocem", "nazivnikom"),
        (r"imenioci", "nazivnici"),
        (r"imenilaca", "nazivnika"),
        (r"imeniocima", "nazivnicima"),
        (r"prvih\s+dvoje\s+odgovora", "prva dva odgovora"),
        (r"prvih\s+dvoje\s+zadataka", "prva dva zadatka"),
        (r"prvih\s+dvoje", "prva dva"),
        (r"probaj\s+ponovo", "Želiš li sličan zadatak za vježbu?"),
        # 2026-07-11 (KORAK 3): rod se ne slaže — "pitanje" je srednji rod.
        # Cilja SAMO ispred "pitanje", pa "dobar zadatak" ostaje netaknut.
        (r"dobar\s+(?:je\s+)?pitanje", "dobro pitanje"),
        # 2026-07-13 (AUD-09/C2): hrvatski/stariji termini iz audita.
        (r"zbroj", "zbir"),
        (r"zbroja", "zbira"),
        (r"zbroju", "zbiru"),
        (r"zbrojem", "zbirom"),
        (r"zbrojiti", "sabrati"),
        (r"zbroji", "saberi"),
        # "okomit" SAMO u frazama gdje zamjena čuva gramatiku ("okomito na" /
        # "okomit(a/e/i) na"); goli pridjev se ne dira (pokvario bi rečenicu).
        (r"okomit(?:o|a|e|i|u)?\s+na", "pod pravim uglom na"),
        (r"okomic(a|e|i|u|om)", r"normal\1"),
        (r"decimaln(a|e|u|oj)\s+ta[čc]k(a|e|u|om|i)", r"decimalni zarez"),
        (r"pithagor(\w*)", r"pitagor\1"),
        (r"kutov(\w*)", r"uglov\1"),
        (r"\bkut\b", "ugao"),
        (r"\bkuta\b", "ugla"),
        (r"\bkutu\b", "uglu"),
        (r"\bkutom\b", "uglom"),
    )
)

_PROTECTED_RE = re.compile(
    r"```[\s\S]*?```"
    r"|`[^`\n]*`"
    r"|https?://[^\s<>)]+"
    r"|www\.[^\s<>)]+"
    r"|\\\([\s\S]*?\\\)"
    r"|\\\[[\s\S]*?\\\]"
    r"|\$\$[\s\S]*?\$\$"
    r"|\$[^$\n]*\$"
)


def _preserve_case(source: str, replacement: str) -> str:
    """Zadrži veliko početno slovo ("Deo" → "Dio")."""
    if source and source[0].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _apply_replacements(text: str) -> str:
    out = text
    for pattern, repl in _REPLACEMENTS:
        # case-insensitive prolaz sa čuvanjem velikog početnog slova
        ci = re.compile(pattern.pattern, re.IGNORECASE)

        def _sub(m: re.Match, _repl=repl) -> str:
            return _preserve_case(m.group(0), m.expand(_repl))

        out = ci.sub(_sub, out)
    out = re.sub(r"(Želiš li sličan zadatak za vježbu\?)[.!?]+", r"\1", out)
    # Razmak između iznosa i valute: "236,50KM" → "236,50 KM" (decimalni zarez
    # se NE dira). Samo prilijepljen slučaj; već razmaknuto ostaje isto.
    out = re.sub(r"(?<=\d)(?=KM\b)", " ", out)
    return out


def to_ijekavica(text: str) -> str:
    """Zamijeni česte oblike, ali ne diraj URL-ove, kod i matematičke blokove."""
    if not text:
        return text
    out: list[str] = []
    pos = 0
    for m in _PROTECTED_RE.finditer(text):
        out.append(_apply_replacements(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_apply_replacements(text[pos:]))
    return "".join(out)
