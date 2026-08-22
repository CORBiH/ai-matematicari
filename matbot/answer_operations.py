# -*- coding: utf-8 -*-
"""Koju je OPERACIJU objavljeni odgovor stvarno IZVEO — dokazano računom.

ZAŠTO POSTOJI (mjerena kampanja od 20 poziva, 5 propusta): postojeće kapije
gledaju ZAHTJEV (prije modela) i NOTACIJU (poslije modela). Između njih ostaje
rupa: model odbije METODU, a onda svejedno objavi njen REZULTAT — bez ijednog
zabranjenog simbola, pa ga provjera notacije po konstrukciji ne vidi.

Živi primjeri koji su prošli sve postojeće kapije:
    6. razred: „hipotenuza je 5 cm. Postupak … uči se kasnije."
    7. razred: „Kasnije se dobija da je udaljenost 5 cm."
    6. razred: „stranica je približno 4,5 cm. Tačnija procjena je oko 4,47 cm."
    7. razred: na pitanje „Šta je hipotenuza?" — „ako su katete 3 i 4,
               hipotenuza c = 5 cm" (pojmovno pitanje koje NIJEDNA ulazna
               kapija ne smije blokirati)

Posljednji slučaj je i dokaz da se ovo NE MOŽE riješiti na ulazu: zahtjev je
čisto pojmovan i mora proći. Odluka zato mora gledati OBJAVLJENI TEKST.

AUTORITET — NIVO „SERVER SAM PROVJERI":
    • ništa se ne čita iz modelove tvrdnje o tome šta je uradio;
    • ništa se ne zaključuje iz ključnih riječi same po sebi;
    • server EGZAKTNO provjeri da brojevi u odgovoru čine baš onu vezu koju
      zabranjena operacija proizvodi (a²+b²=c², odnosno v²≈P za nepotpun
      kvadrat) — tek to je dokaz da je operacija izvedena;
    • dozvolu i dalje daje ISKLJUČIVO `practice_policy` preko
      `capability_requests.operation_allowed`.

FAIL-OPEN: kad se veza ne može dokazati, vraća se prazno i objava teče dalje.
Nedokazano nikad ne znači zabranjeno.

ŠTO OVAJ MODUL NE RADI: ne čita skriveno rezonovanje modela (nema ga u šemi —
`ExplainTurnOutput` ima jedno jedino polje `reply`), ne popravlja tekst, ne
poznaje lekcije ni razrede i ne donosi kurikularnu odluku.
"""
import re
from fractions import Fraction
from math import isqrt

from matbot import capability_requests, textnorm

# Brojevi se čitaju iz BROJEVNO-ČUVAJUĆEG zapisa: leksička normalizacija bi
# „4,47" pretvorila u „4 47" i time uništila upravo dokaz koji tražimo.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# Riječ koja imenuje veličinu koju Pitagorina teorema daje. Sama po sebi NIJE
# dokaz — dokaz je egzaktna veza brojeva; ovo samo sprječava da se slučajna
# trojka u nevezanom tekstu čita kao hipotenuza.
_PYTHAGOREAN_TARGET = re.compile(
    r"hipotenuz\w*|najduz\w*\s+stranic\w*|udaljenost\w*|dijagonal\w*|rastojanj\w*")

# Gornja granica pretrage — odgovor učeniku je kratak, a ovo drži trošak
# kvadratne pretlage zanemarivim.
_MAX_NUMBERS = 40
_APPROX_TOLERANCE = Fraction(35, 100)

PYTHAGOREAN_RESULT_CODE = "published_pythagorean_result"
ROOT_APPROXIMATION_CODE = "published_root_approximation"


def _values(text):
    out = []
    for token in _NUMBER.findall(textnorm.normalize_numeric(text))[:_MAX_NUMBERS]:
        try:
            value = Fraction(token.replace(",", "."))
        except (ValueError, ZeroDivisionError):
            continue
        if 0 < value < 100000:
            out.append(value)
    return out


def find_pythagorean_result(text):
    """(a, b, c) ako odgovor TVRDI stranicu koju daje Pitagorina teorema.

    Dokaz je egzaktan: a² + b² = c² nad brojevima koji svi stoje u tekstu, uz
    riječ koja imenuje traženu veličinu. Bez trojke nema tvrdnje — spominjanje
    katete i hipotenuze bez rezultata ovdje NIŠTA ne pokreće."""
    normalized = textnorm.normalize_numeric(text or "")
    if not _PYTHAGOREAN_TARGET.search(normalized):
        return None
    values = _values(text)
    present = set(values)
    for index, a in enumerate(values):
        for b in values[index + 1:]:
            if a == b:
                continue
            square = a * a + b * b
            if square.denominator != 1:
                continue
            root = isqrt(int(square))
            if root * root != int(square):
                continue
            candidate = Fraction(root)
            if candidate in present and candidate != a and candidate != b:
                return (min(a, b), max(a, b), candidate)
    return None


def find_root_approximation(text):
    """(P, v) ako odgovor TVRDI približnu vrijednost korijena.

    Dokaz: u tekstu stoji cio broj P koji NIJE potpun kvadrat, i necjelobrojna
    vrijednost v takva da je v² blizu P. Potpun kvadrat se namjerno preskače —
    tamo do rezultata vodi i dozvoljeni put množenja."""
    values = _values(text)
    for area in values:
        if area.denominator != 1 or area < 2:
            continue
        whole = int(area)
        if isqrt(whole) ** 2 == whole:
            continue                      # potpun kvadrat → dozvoljen put
        for value in values:
            if value.denominator == 1:
                continue                  # aproksimacija je necjelobrojna
            difference = value * value - area
            if -_APPROX_TOLERANCE <= difference <= _APPROX_TOLERANCE:
                return (area, value)
    return None


# Koja operacija stoji iza kojeg dokaza. Imena su iz JEDNOG rječnika
# sposobnosti (`capability_requests.KNOWN_OPERATIONS`), pa se podaci i kod ne
# mogu razići.
_EVIDENCE = (
    (PYTHAGOREAN_RESULT_CODE, "pythagoras_operation", find_pythagorean_result),
    (ROOT_APPROXIMATION_CODE, "radical_operation", find_root_approximation),
)


def executed_operation_failures(policy, answer):
    """Kodovi za operacije koje je odgovor DOKAZANO izveo, a razred ih nema.

    Prazna torka znači „nije dokazano" — nikad „nema prekršaja s pouzdanjem"."""
    if policy is None or not answer:
        return ()
    failures = []
    for code, operation, detector in _EVIDENCE:
        if capability_requests.operation_allowed(operation, policy):
            continue
        if detector(answer):
            failures.append(code)
    return tuple(failures)
