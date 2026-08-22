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

# KORJENOVANJE TRAŽI ISTU VRSTU SEMANTIČKOG DOKAZA KAO I PITAGORA.
# Živi defekt nađen na izdanjnoj kapiji: bez ovoga je gola aritmetička
# podudarnost obarala sasvim običnu građu 6. razreda — „Marko ima 1,5 KM, a
# Ivana 2 KM" ($1{,}5^2=2{,}25$, a $2$ nije potpun kvadrat) čitalo se kao
# objavljena aproksimacija $\sqrt{2}$. Mjereno: 8/8 svakodnevnih tekstualnih
# zadataka lažno blokirano.
#
# Operacija koja se OVDJE dokazuje je „izvadi korijen POVRŠINE i dobij DUŽINU".
# Zato dokaz traži OBA konteksta, ne samo brojeve:
#   • kvadratni/površinski kontekst — odakle se korijenuje;
#   • imenovana dužinska veličina — šta se dobija.
# Bez oba, veza je slučajna i vraća se prazno (fail-open).
_SQUARE_CONTEXT = re.compile(r"\bpovrsin\w*|\bkvadrat\w*|\bcm2\b|\bm2\b|\^2|"
                             r"\bsam\s+sa\s+sobom\b|\bna\s+kvadrat\b")
_LENGTH_TARGET = re.compile(r"\bstranic\w*|\bstran[ae]\b|\bduzin\w*|\bivic\w*")

# Gornja granica pretrage — odgovor učeniku je kratak, a ovo drži trošak
# kvadratne pretlage zanemarivim.
_MAX_NUMBERS = 40

PYTHAGOREAN_RESULT_CODE = "published_pythagorean_result"
ROOT_APPROXIMATION_CODE = "published_root_approximation"


def _measured(text):
    """[(vrijednost, broj decimala)] — decimale su dio DOKAZA, ne kozmetika."""
    out = []
    for token in _NUMBER.findall(textnorm.normalize_numeric(text))[:_MAX_NUMBERS]:
        plain = token.replace(",", ".")
        try:
            value = Fraction(plain)
        except (ValueError, ZeroDivisionError):
            continue
        if 0 < value < 100000:
            decimals = len(plain.split(".")[1]) if "." in plain else 0
            out.append((value, decimals))
    return out


def _values(text):
    return [value for value, _ in _measured(text)]


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

    Dokaz traži TRI stvari zajedno: kvadratni/površinski kontekst, imenovanu
    dužinsku veličinu, i cio broj P koji NIJE potpun kvadrat uz necjelobrojnu
    vrijednost v takvu da je v² blizu P. Potpun kvadrat se namjerno preskače —
    tamo do rezultata vodi i dozvoljeni put množenja.

    Sama aritmetička podudarnost NIJE dokaz: bez konteksta bi obična cijena i
    obična količina u istom zadatku slučajno zadovoljile relaciju."""
    normalized = textnorm.normalize_numeric(text or "")
    if not (_SQUARE_CONTEXT.search(normalized) and _LENGTH_TARGET.search(normalized)):
        return None
    measured = _measured(text)
    for area, area_decimals in measured:
        if area_decimals or area < 2:
            continue
        whole = int(area)
        if isqrt(whole) ** 2 == whole:
            continue                      # potpun kvadrat → dozvoljen put
        for value, decimals in measured:
            if decimals == 0:
                continue                  # aproksimacija je necjelobrojna
            # EGZAKTAN TEST ZAOKRUŽIVANJA umjesto proizvoljnog prozora: `value`
            # je aproksimacija korijena tačno kad `area` leži u intervalu koji
            # se na TOM broju decimala zaokružuje na `value`. Bez floata.
            #
            # Živi propust koji je ovo iznudio (izdanjska kampanja, 6. razred):
            # „stranica je približno 4,2 cm" za P=18. Raniji fiksni prozor od
            # 0,35 to je promašio jer je |4,2²−18| = 0,36 — a greška raste s
            # √P, pa nijedan APSOLUTAN prag ne može biti tačan za sve P.
            half = Fraction(1, 2 * 10 ** decimals)
            if (value - half) ** 2 <= area <= (value + half) ** 2:
                return (area, value)
    return None


# Koja operacija stoji iza kojeg dokaza. Imena su iz JEDNOG rječnika
# sposobnosti (`capability_requests.KNOWN_OPERATIONS`), pa se podaci i kod ne
# mogu razići.
_EVIDENCE = (
    (PYTHAGOREAN_RESULT_CODE, "pythagoras_operation", find_pythagorean_result),
    (ROOT_APPROXIMATION_CODE, "radical_operation", find_root_approximation),
)


def _already_given(request, produced):
    """Je li tražena vrijednost VEĆ STAJALA u učenikovom pitanju.

    ŽIVI DEFEKT NAĐEN NA IZDANJNOJ KAPIJI: „Koliki je obim trougla sa
    stranicama 3, 4 i 5 cm?" je legitiman zadatak 7. razreda, ali ako odgovor
    usput kaže „najduža stranica je 5 cm", brojevi čine 3²+4²=5² i kapija bi
    ga oborila. Tu ništa nije IZVEDENO — petica je DATA u pitanju.

    Razlika između „izvedeno" i „dato" je server-vlasnička činjenica: pitanje
    je učenikov tekst. Kad je vrijednost već u pitanju, dokaza o izvođenju
    nema i vraća se fail-open."""
    if not request or produced is None:
        return False
    return produced in set(_values(request))


def executed_operation_failures(policy, answer, request=""):
    """Kodovi za operacije koje je odgovor DOKAZANO izveo, a razred ih nema.

    `request` je učenikova poruka; služi SAMO da se odbaci lažni dokaz kad je
    vrijednost bila data, nikad da nešto dodatno zabrani.

    Prazna torka znači „nije dokazano" — nikad „nema prekršaja s pouzdanjem"."""
    if policy is None or not answer:
        return ()
    failures = []
    for code, operation, detector in _EVIDENCE:
        if capability_requests.operation_allowed(operation, policy):
            continue
        evidence = detector(answer)
        if not evidence:
            continue
        if _already_given(request, evidence[-1]):
            continue                      # vrijednost je DATA, nije izvedena
        failures.append(code)
    return tuple(failures)
