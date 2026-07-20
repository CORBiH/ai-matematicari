"""Mala jezička zaštita: najčešći ekavski/srpski oblici → bosanska ijekavica.

Sistemska prompt pravila i sav sistemski tekst se PIŠU u ijekavici uzvodno; ovo
je ZADNJA linija odbrane (safety net) SAMO za vrlo česte i jednoznačne oblike
koji povremeno "procure" (model ili starije stringove), sa granicama riječi da
se ništa ne prekoriguje (npr. "video" sadrži "deo" ali ga \\b štiti). Primjenjuje
se ISKLJUČIVO na sistemski generisan tekst (odgovori/hintovi/sažeci), NIKAD na
učenikov unos ni OCR — matematički blokovi (\\(...\\), $...$, kod, URL) su zaštićeni.

TERMINOLOŠKA KONVENCIJA (jedna za cijelu aplikaciju — Bosanski, ijekavica):
  * jezik: dio, rješenje, vježba, sljedeći/sljedeća, uvijek, poslije, dvije,
           razumijem, obje, djevojčica, cio/cijeli, lijepo
  * razlomak → brojnik, nazivnik (NE imenilac/imenitelj, NE brojilac/brojitelj)
  * zajednički nazivnik (NE "zajednički imenilac"); najmanji zajednički sadržalac
  * jednačina, nejednačina (NE jednadžba)
  * djeljivost, djelilac (NE djelitelj), djeljiv
  * rastavljanje na proste faktore; prost broj
  * aritmetička sredina
  * skup: unija (∪), presjek (∩), razlika
  * zbir, sabrati (NE zbroj, zbrojiti)
  * ugao (NE kut); pod pravim uglom (NE okomit)
  * decimalni zarez (NE decimalna tačka); stepen; Pitagora
  * ocjene: tačan, djelimično tačan, netačan
  * pokušaj, rješenje, objašnjenje

NAPOMENA: projekt (i ovaj filter) koristi "nazivnik" (bosanski standard); ulazni
"imenilac"/"imenitelj" se normalizuju u "nazivnik". Ovo je namjerno odstupanje od
jednog primjera u zahtjevu ("zajednički imenilac") radi konzistentnosti s
postojećim kodom i bosanskim standardom.
"""
from __future__ import annotations

import re

__all__ = ["to_ijekavica", "TERMINOLOGY"]

# Referentni rječnik odabrane terminologije (za dokumentaciju i testove).
TERMINOLOGY: dict[str, str] = {
    "denominator": "nazivnik",
    "numerator": "brojnik",
    "common_denominator": "zajednički nazivnik",
    "fraction": "razlomak",
    "equation": "jednačina",
    "inequality": "nejednačina",
    "divisibility": "djeljivost",
    "divisor": "djelilac",
    "prime_factorization": "rastavljanje na proste faktore",
    "arithmetic_mean": "aritmetička sredina",
    "set_union": "unija",
    "set_intersection": "presjek",
    "set_difference": "razlika",
    "sum": "zbir",
    "angle": "ugao",
    "correct": "tačan",
    "partially_correct": "djelimično tačan",
    "incorrect": "netačan",
    "attempt": "pokušaj",
    "solution": "rješenje",
    "explanation": "objašnjenje",
}

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
        (r"vezb(\w*)", r"vježb\1"),        # ekavski BEZ dijakritika ("vezbu") — Phase 7 nalaz
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
        (r"sledec(\w*)", r"sljedeć\1"),        # ekavski "sledeci" → "sljedeći" (uz ć)
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
        # 2026-07-19 (Phase 6): vraćanje dijakritika za VRLO česte sistemske/model
        # oblike koji "procure" bez č/ć/š/ž. Jednoznačni i ograničeni granicama
        # riječi; primjenjuju se samo na sistemski tekst (nikad na unos/OCR).
        (r"rijesi", "riješi"),
        (r"rijesiti", "riješiti"),
        (r"rjesenj(\w*)", r"rješenj\1"),
        (r"rjesava(\w*)", r"rješava\1"),
        (r"rjesi(\w*)", r"riješi\1"),
        (r"matematick(\w*)", r"matematičk\1"),
        (r"jednoznac(\w*)", r"jednoznač\1"),
        (r"vjezb(\w*)", r"vježb\1"),
        (r"posalj(\w*)", r"pošalj\1"),
        (r"pomoc(\w*)", r"pomoć\1"),
        (r"konacn(\w*)", r"konačn\1"),
        (r"gresk(\w*)", r"grešk\1"),
        (r"izracunaj(\w*)", r"izračunaj\1"),
        (r"nec(u|e|emo|ete)", r"neć\1"),
        (r"netacn(\w*)", r"netačn\1"),
        (r"netacan", "netačan"),
        (r"tacn(\w*)", r"tačn\1"),
        (r"tacan", "tačan"),
        (r"djelimicn(\w*)", r"djelimičn\1"),
        (r"sljedec(i|a|e|u|eg|em|oj|om|ih|im)", r"sljedeć\1"),
        (r"zajednick(\w*)", r"zajedničk\1"),
        (r"objasnjenj(\w*)", r"objašnjenj\1"),
        (r"rjesenje", "rješenje"),
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
