# -*- coding: utf-8 -*-
"""POSTOJI LI TROUGAO KOJI ZADATAK OPISUJE — dokaz NEMOGUĆNOSTI, ne rješavanje.

ŽIVI NALAZ (post-deploy proba izdanja `3128968`, kontrolni, 7. razred, oblast
„Ugao i trougao“, pitanje q5) — objavljeno je:

    „U trouglu $ABC$ dati su uglovi $\\alpha=50^\\circ$ i $\\beta=60^\\circ$,
     stranice $a=6\\,\\text{cm}$ i $b=7\\,\\text{cm}$. …“

Taj trougao NE POSTOJI. Sinusna teorema traži $a/\\sin\\alpha = b/\\sin\\beta$, a

    $6/\\sin 50^\\circ = 7{,}832$        $7/\\sin 60^\\circ = 8{,}083$

razlikuju se 3,2 %. Zadatak je dakle tražio obim objekta koji ne može postojati.

ZAŠTO GA NIJEDAN POSTOJEĆI SLOJ NIJE OBORIO (izmjereno, sloj po sloj):
`mathsafe` vidi ispravan MathJax; `mathcheck` u zadatku nema nijedan lanac
jednakosti pa nema šta provjeriti, a modelovo rješenje ($6+7+8=21$) je
aritmetički TAČNO; `geometrycheck` sudi NOTACIJU (R/r/d/P/O), ne postojanje
objekta; orakli vrijednosti (djeljivost, direktan račun, poređenje, sistem,
piramida, projekcija) nisu primjenjivi; `exactly_one` ne važi jer su opcije
vrijednosti; `stem_disclosure` ne nalazi odgovor u tekstu. Svi su ćutali
ISPRAVNO — nijedan od njih ne postavlja pitanje „da li opisani objekat uopšte
postoji“. Ovaj modul postavlja samo to pitanje.

GRANICE (namjerno uske — ovo NIJE geometrijski solver):
  • sudi se ISKLJUČIVO iz ZADATKU VIDLJIVIH podataka; označena opcija,
    `expected_answer` i modelovo rješenje NISU dokaz — njih objava upravo
    provjerava;
  • korespondencija ugao↔stranica se NE POGAĐA: koristi se kanonska konvencija
    koju projekat već ima (`kontrolni._GREEK_SIDE`: α↔a, β↔b, γ↔c), proširena
    na drugi trougao (δ↔d, ε↔e, φ↔f). Svaki drugi zapis znači ĆUTANJE;
  • indeksirani ugao (`\\alpha_1`) NIJE isti ugao, pa se ne hvata;
  • zadatak s VIŠE trouglova se dijeli po spomenu trougla i podaci se NIKAD ne
    miješaju između njih;
  • ne rješava se zadatak i ne izvodi se nijedna tražena veličina.

TRIGONOMETRIJA JE OVDJE INTERNA. Učenik 6–9. razreda je ne vidi: `sin` se
koristi samo da server sebi odgovori „mogu li ovi vidljivi podaci opisati jedan
trougao“. Ništa iz ovog modula ne ide u zadatak, hint, rješenje ni objašnjenje.
"""
import math
import re

from matbot.mathcheck import safe_numeric_value
from matbot.mathsegments import TEXT, tokenize_math

# Jedan kod za cijelu porodicu — bez množenja kodova (log-only, kao i svi
# ostali; učenik vidi samo kontrolisanu poruku).
INCONSISTENT_CODE = "inconsistent_triangle_givens"

# Kanonska korespondencija ugao → NASPRAMNA stranica. Uglovi se pamte POD
# SLOVOM SVOJE NASPRAMNE STRANICE, pa je „par“ prosto isto slovo u oba rječnika.
_ANGLE_SIDE = {
    "alpha": "a", "beta": "b", "gamma": "c",
    "delta": "d", "varepsilon": "e", "epsilon": "e",
    "varphi": "f", "phi": "f",
}
# Trojke stranica jednog trougla. Podaci se sude SAMO unutar jedne trojke.
_SIDE_TRIPLES = (("a", "b", "c"), ("d", "e", "f"))

# „trougao“ NEMA korijen „trougl“ (živi nalaz već zabilježen uz
# `kontrolni._angle_side_correspondence_failure`) — zato oba oblika.
_TRIANGLE_ANCHOR_RE = re.compile(r"troug(?:ao|l)", re.IGNORECASE)

# ĆUTANJE kad simboli ne znače ono što ovaj modul pretpostavlja:
#   • približne/zaokružene vrijednosti nisu egzaktni školski podaci;
#   • vanjski/spoljašnji ugao nije unutrašnji ugao trougla.
_ABSTAIN_RE = re.compile(
    r"pribli[žz]n|zaokru[žz]|okruglo|≈|\\approx|vanjsk|spolja[šs]nj|spoljn",
    re.IGNORECASE)

_ANGLE_NAMES = "|".join(sorted(_ANGLE_SIDE, key=len, reverse=True))
_ANGLE_RE = re.compile(
    r"\\(" + _ANGLE_NAMES + r")(?!_)\s*=\s*"
    r"(\d+(?:[.,]\d+)?)\s*(?:\^\s*\\circ|°)")

# Stranica: `a=6`, `a=6\,\text{cm}`, `b=5\sqrt{3}`. Negativni pogled unazad
# sprječava da se uhvati POSLJEDNJE SLOVO komande — `\alpha=50` završava na
# „a“, `\beta=60` i `\gamma=70` takođe — što bi ugao lažno pročitalo kao stranicu.
# Zarez razdvaja dvije dodjele (`a=6, b=7`), ali NE prekida vrijednost kad je
# dio LaTeX razmaka (`6\,\text{cm}`) ili decimalnog zapisa (`2,5`) — oba su
# svakodnevna u ovom projektu, i oba bi inače dala pogrešno pročitan podatak.
_SIDE_ASSIGN_RE = re.compile(
    r"(?<![A-Za-z\\])([a-f])\s*=\s*"
    r"((?:[^,;=]|(?<=\\),|(?<=\d),(?=\d))+)")

# Mjerna jedinica na kraju vrijednosti; skida se PRIJE egzaktnog računanja.
_UNIT_SUFFIX_RE = re.compile(
    r"(?:\\[,;:!]|\s|~)*"
    r"(?:\\(?:text|mathrm)\s*\{\s*([A-Za-z]+)\s*\}|([A-Za-z]+))\s*$")

_UNITS = frozenset(("mm", "cm", "dm", "m", "km"))

# SAMO računska greška, nikad „dovoljno blizu geometrija“. Egzaktno saglasni
# školski podaci ($\alpha=30^\circ$, $a=5$, $\beta=60^\circ$, $b=5\sqrt3$) se u
# pokretnom zarezu poklapaju do ~1e-16; živi nalaz promašuje za 3,2 %.
_TOLERANCE = 1e-9


class _Unreadable(Exception):
    """Vrijednost se ne može pročitati EGZAKTNO — cijeli isječak se preskače.

    Postoji da se `b=5\\sqrt{3}` nikad ne pročita kao `b=5`: pogrešno pročitan
    podatak bi oborio SAVRŠENO ISPRAVAN trougao (30°/60° s $a=5$, $b=5\\sqrt3$
    je tačno saglasan). Radije bez presude nego pogrešna presuda."""


def _number(raw):
    return float(str(raw).replace(",", "."))


def _length(raw):
    """(dužina, jedinica) iz desne strane dodjele, ili `_Unreadable`."""
    body = (raw or "").strip()
    unit = ""
    match = _UNIT_SUFFIX_RE.search(body)
    if match:
        candidate = (match.group(1) or match.group(2) or "").lower()
        if candidate in _UNITS:
            unit = candidate
            body = body[:match.start()].strip()
    status, value = safe_numeric_value(body)
    if status != "value" or value is None:
        raise _Unreadable(body[:40])
    return value, unit


def _scopes(text):
    """Matematički sadržaji grupisani PO TROUGLU.

    Jedan spomen trougla = cijeli zadatak opisuje taj trougao (podaci smiju
    stajati i prije spomena). Dva ili više spomena = svaki dobija svoju grupu,
    od svog spomena do sljedećeg; podaci dvaju trouglova se NIKAD ne miješaju,
    jer bi poređenje preko granice bilo IZMIŠLJENA korespondencija."""
    tokens = list(tokenize_math(text or ""))
    anchors = sum(len(_TRIANGLE_ANCHOR_RE.findall(content))
                  for kind, content in tokens if kind == TEXT)
    if not anchors:
        return []
    groups = [[]]
    for kind, content in tokens:
        if kind == TEXT:
            if anchors > 1:
                for _ in _TRIANGLE_ANCHOR_RE.findall(content):
                    groups.append([])
        else:
            groups[-1].append(content)
    return groups


def _parse(contents):
    """(uglovi_po_slovu_naspramne_stranice, stranice_po_slovu)."""
    angles = {}
    sides = {}
    for content in contents:
        for name, value in _ANGLE_RE.findall(content):
            angles.setdefault(_ANGLE_SIDE[name], _number(value))
        for letter, raw in _SIDE_ASSIGN_RE.findall(content):
            if letter in sides:
                continue
            sides[letter] = _length(raw)
    return angles, sides


def _units_comparable(entries):
    """Poređenje ima smisla samo kad su dužine u ISTOJ mjeri."""
    return len({unit for _value, unit in entries if unit}) <= 1


def _triple_failure(angles, sides):
    """Kod odbijanja za JEDAN trougao, ili prazan string."""
    for value in angles.values():
        if value <= 0 or value >= 180:
            return INCONSISTENT_CODE
    if len(angles) == 3:
        if abs(sum(angles.values()) - 180.0) > _TOLERANCE:
            return INCONSISTENT_CODE
    elif len(angles) == 2 and sum(angles.values()) >= 180.0 - _TOLERANCE:
        # Dva unutrašnja ugla već popunjavaju (ili prebacuju) puni zbir — treći
        # bi morao biti ≤ 0. Nemoguće i bez trećeg podatka.
        return INCONSISTENT_CODE

    if len(sides) == 3:
        entries = list(sides.values())
        if any(value <= 0 for value, _unit in entries):
            return INCONSISTENT_CODE
        if _units_comparable(entries):
            lengths = sorted(value for value, _unit in entries)
            if lengths[0] + lengths[1] <= lengths[2] + _TOLERANCE * lengths[2]:
                return INCONSISTENT_CODE

    # SINUSNA TEOREMA nad DOKAZANIM parovima (ugao i NJEGOVA naspramna
    # stranica, oboje izričito vidljivi). Jedan par ne dokazuje ništa.
    pairs = [letter for letter in sides if letter in angles]
    if len(pairs) >= 2:
        entries = [sides[letter] for letter in pairs]
        if _units_comparable(entries):
            ratios = []
            for letter in pairs:
                length, _unit = sides[letter]
                sine = math.sin(math.radians(angles[letter]))
                if length <= 0 or sine <= 0:
                    return INCONSISTENT_CODE
                ratios.append(length / sine)
            low, high = min(ratios), max(ratios)
            if high - low > _TOLERANCE * high:
                return INCONSISTENT_CODE
    return ""


def publication_failure(text):
    """Kod odbijanja kad su VIDLJIVI podaci trougla međusobno nemogući, inače ''.

    Ćuti na svemu što ne umije dokazati — nepotpuni podaci, nepoznat zapis,
    nedokaziva korespondencija. Ćutanje znači „bez presude“, nikad „ispravno“."""
    body = text or ""
    if not _TRIANGLE_ANCHOR_RE.search(body) or _ABSTAIN_RE.search(body):
        return ""
    for contents in _scopes(body):
        try:
            angles, sides = _parse(contents)
        except _Unreadable:
            continue          # radije bez presude nego pogrešna presuda
        if not angles and not sides:
            continue
        for triple in _SIDE_TRIPLES:
            failure = _triple_failure(
                {k: v for k, v in angles.items() if k in triple},
                {k: v for k, v in sides.items() if k in triple})
            if failure:
                return failure
    return ""
