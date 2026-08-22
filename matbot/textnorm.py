# -*- coding: utf-8 -*-
"""Dva IMENOVANA ugovora normalizacije teksta — leksički i brojevno-čuvajući.

ZAŠTO POSTOJI: projekat je imao PET zasebnih implementacija normalizacije, sve
napisane s istom namjerom („mala slova, bez dijakritika i interpunkcije"), ali
mjereno u ČETIRI različita ugovora — razlikovale su se po sažimanju razmaka,
po zadržavanju „)" i po tome da li „đ" preživi. Nova, šesta kopija bila bi gora
od dijeljenja, ali ni jedno JEDNO ponašanje ne pokriva sve pozivaoce.

ZATO OVDJE NEMA „univerzalnog" normalizatora nego DVA IZRIČITA UGOVORA:

  normalize_lexical  — za prepoznavanje FRAZA. Smije uništiti interpunkciju,
                       jer njegovi potrošači porede riječi. Parametri postoje
                       da se ZATEČENA ponašanja izraze TAČNO, bajt za bajt —
                       nisu ukusi nego mjereni ugovori postojećih pozivalaca.

  normalize_numeric  — za buduće DETERMINISTIČKE parsere vrijednosti. Čuva
                       sve što nosi brojevno značenje. NIŠTA ne računa i ne
                       odlučuje ništa kurikularno.

NAJVAŽNIJE PRAVILO OVOG MODULA: `normalize_numeric` NE SMIJE koristiti NFKD.
Mjereno: NFKD tiho prepisuje „2²" u „22", „5³" u „53" i „½" u „1⁄2" — dakle
pravi DRUGI broj. Za leksički put to je bezopasno, za brojevni je pogubno.
Dijakritici se zato u brojevnom putu preslikavaju IZRIČITOM tabelom slova,
koja po konstrukciji ne može dodirnuti nijednu cifru.

ZAŠTO „đ" TRAŽI RUČNO PRESLIKAVANJE, a ostala naša slova ne (mjereno):
    č U+010D → dekompozicija '0063 030C'   (c + kvačica)
    ć U+0107 → dekompozicija '0063 0301'
    š U+0161 → dekompozicija '0073 030C'
    ž U+017E → dekompozicija '007A 030C'
    đ U+0111 → dekompozicije NEMA
NFKD rastavi prva četiri na osnovno slovo + spojni znak (koji se onda odbaci),
ali „đ" nije slovo sa znakom nego zaseban glif s crtom — pa preživi svaki
NFKD-baziran normalizator. To je jedini razlog za tabelu, ne stilski izbor.

I ZAŠTO SE „đ" NE PRESLIKAVA SVUDA: `quick_context` čita oznaku zadatka
azbukom `[a-fđčćšž]` i „pod đ)" mu je legitimna oznaka `đ`. Preslikavanje bi
mu promijenilo odgovor. Zato je `fold_dstroke` IZRIČIT izbor pozivaoca, nikad
podrazumijevan.
"""
import re
import unicodedata

# Izričita tabela naših slova — koristi je SAMO brojevni put, jer on ne smije
# ni prići NFKD-u. Velika slova su tu jer se preslikavanje radi prije lower().
_BOSNIAN_FOLD = str.maketrans({
    "č": "c", "Č": "C", "ć": "c", "Ć": "C",
    "š": "s", "Š": "S", "ž": "z", "Ž": "Z",
    "đ": "d", "Đ": "D",
})
# Leksički put treba samo „đ" — ostalo mu NFKD već odradi.
_DSTROKE_FOLD = str.maketrans({"đ": "d", "Đ": "D"})

_WHITESPACE = re.compile(r"\s+")


def _strip_marks(text):
    folded = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def normalize_lexical(text, collapse_whitespace=True, keep="", fold_dstroke=False):
    """Mala slova, bez dijakritika, interpunkcija → razmak.

    `collapse_whitespace` — sažmi nizove razmaka i obreži rubove.
    `keep`                — znakovi interpunkcije koji NISU zamijenjeni
                            razmakom (npr. „)" za oznake zadataka).
    `fold_dstroke`        — preslikaj „đ"→„d" (vidi docstring modula).

    Parametri postoje da se zatečeni ugovori izraze tačno; podrazumijevane
    vrijednosti daju najčešći od njih."""
    value = text or ""
    if fold_dstroke:
        value = value.translate(_DSTROKE_FOLD)
    stripped = _strip_marks(value).lower()
    if keep:
        pattern = re.compile(r"[^\w\s" + re.escape(keep) + r"]", re.UNICODE)
    else:
        pattern = re.compile(r"[^\w\s]", re.UNICODE)
    words_only = pattern.sub(" ", stripped)
    if collapse_whitespace:
        return " ".join(words_only.split())
    return words_only


# ---------------------------------------------------------------------------
# BROJEVNO-ČUVAJUĆI UGOVOR
# ---------------------------------------------------------------------------
# ŠTA GARANTUJE:
#   • nijedno slovo ne postaje cifra i nijedna cifra ne postaje slovo;
#   • decimalni zarez i decimalna tačka ostaju;
#   • predznak, „=", „/", „:" i „%" ostaju;
#   • eksponenti i korijen (²,³,√) ostaju NETAKNUTI — bez NFKD;
#   • granice tokena ostaju, pa se „20cm" i „20 cm" i dalje razlikuju;
#   • pokvaren token ostaje pokvaren: „2O", „2..5", „--3" se NE popravljaju.
# ŠTA NE RADI: ne računa, ne zaključuje, ne poznaje kurikulum ni sposobnosti.
def normalize_numeric(text):
    """Normalizuj za DETERMINISTIČKO parsiranje vrijednosti, bez gubitka
    brojevnog značenja. Nikad ne popravlja pokvaren zapis."""
    value = text or ""
    # NFC je kanonska KOMPOZICIJA — spaja slovo i znak u jedan kodni znak, ali
    # NIKAD ne mijenja „²" u „2". NFKD bi to uradio i time promijenio broj.
    value = unicodedata.normalize("NFC", value)
    value = value.translate(_BOSNIAN_FOLD).lower()
    return _WHITESPACE.sub(" ", value).strip()
