"""Univerzalni troslojni kontroler pedagoške težine za Practice — jedan
server-owned nivo (1/2/3) dijeljen kroz SVIH 534 lekcija.

ZAŠTO POSTOJI (živi nalaz): generisan zadatak je mogao pogoditi tačnu
izabranu lekciju, a ipak početi na neprimjereno naprednom nivou — npr. svježa
lekcija „Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25“ (razred 6) je
otvarala sesiju ostatkom dijeljenja 375:25 ili traženjem nedostajuće cifre za
djeljivost sa 9, umjesto uvodnog pitanja tipa „da li je 24 djeljivo sa 3?“.
Porodica (matbot/task_families.py) kaže ŠTA se vježba; ovaj modul dodaje
ORTOGONALNU osu — KOLIKO je zadatak zahtjevan — bez ijedne izmjene rutiranja
porodica.

GRANICE (namjerno uske):
  • server je JEDINI vlasnik nivoa i svih prelaza (nikad model);
  • tri nivoa: 1 (uvodni), 2 (standardna primjena), 3 (napredna primjena);
  • osam dijeljenih dimenzija (ispod) — JEDNA fiksna tabela koju dijele i
    Tutor i Recenzent, da nikad ne vide dvije različito formulisane verzije
    „istog“ pravila;
  • ovaj modul NIKAD ne zna nijedan ID lekcije niti porodicu — čisto
    deterministička aritmetika prelaza + fiksan tekst.
"""
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# TAČNO deterministično mapiranje nivoa → postojeća oznaka (NewTask.difficulty
# / TaskSkeleton.difficulty_label dijele isti tročlani rječnik). Ovo je JEDINO
# mjesto gdje se mapiranje definiše — server ga koristi da PREPIŠE modelovu
# deklarisanu oznaku, nikad obrnuto (vidi matbot/practice.py).
# ---------------------------------------------------------------------------
LEVEL_TO_LABEL = {1: "easy", 2: "standard", 3: "hard"}

MIN_LEVEL = 1
MAX_LEVEL = 3


@dataclass(frozen=True)
class DifficultyTransition:
    """Rezultat JEDNOG determinističkog prelaza nivoa. `boundary_reason` je
    popunjen TAČNO kad je zahtjev za promjenom smjera stigao na već krajnjoj
    tački (nivo se NIJE pomjerio) — razlikuje se od običnog "same" zahtjeva
    (kad korisnik uopšte nije tražio promjenu smjera)."""

    previous_level: int
    target_level: int
    request: str                          # "same" | "harder" | "easier"
    level_changed: bool
    boundary_reason: Optional[str] = None  # "at_minimum" | "at_maximum" | None


def transition(previous_level, difficulty_request=""):
    """Jedini izvor prelaza nivoa — koristi ga i put bez ugovora (Tutor +
    Recenzent) i put sa ugovorom (šest determinističkih lekcija), IDENTIČNO.

    `previous_level` MORA već biti u [1,3] (server ga tako i čuva u sesiji —
    vidi _fresh_session/session.get("difficulty_level", 1)); ovdje se ipak
    stišće u granice odbrambeno, da nijedan pozivalac ne može pokvariti
    invarijantu lošim ulazom."""
    previous = min(max(int(previous_level), MIN_LEVEL), MAX_LEVEL)
    request = (difficulty_request or "").strip().lower()
    label = "harder" if request == "harder" else "easier" if request == "easier" else "same"

    if label == "harder":
        target = min(previous + 1, MAX_LEVEL)
    elif label == "easier":
        target = max(previous - 1, MIN_LEVEL)
    else:
        target = previous

    changed = target != previous
    boundary = None
    if not changed and label == "harder":
        boundary = "at_maximum"
    elif not changed and label == "easier":
        boundary = "at_minimum"

    return DifficultyTransition(previous, target, label, changed, boundary)


def at_generatable_boundary(source):
    """Isti prelaz, ali zaustavljen na nivou koji lekcija UMIJE proizvesti.

    Ljestvica ima tri nivoa, a pojedina lekcija ne mora umjeti sva tri: profil
    višeg nivoa može tražiti kombinaciju dimenzija koju njen arhetip ne može
    generisati. Tada je taj niži nivo NJEN stvarni vrh, pa se prelaz opisuje
    tačno kao i prava granica ljestvice — nivo se ne mijenja i `boundary_reason`
    imenuje kraj. Time uvod ostaje pošten („već najviši nivo“) umjesto da server
    tvrdi promjenu koje nema, ili da turn padne na generičku grešku.

    Vraća NOV objekat; ulazni prelaz se ne mijenja."""
    boundary = "at_maximum" if source.request == "harder" else (
        "at_minimum" if source.request == "easier" else None)
    return DifficultyTransition(
        previous_level=source.previous_level,
        target_level=source.previous_level,
        request=source.request,
        level_changed=False,
        boundary_reason=boundary,
    )


# ---------------------------------------------------------------------------
# OSAM DIJELJENIH DIMENZIJA — jedna fiksna tabela, dijele je Tutor i
# Recenzent (matbot/prompts.py, matbot/lesson_fidelity.py). Namjerno OPŠTE
# (nijedna lekcija, nijedna oblast se ne pominje) — veći brojevi sami po sebi
# NE znače teže; to je izričito ono što ovaj rubrik izbjegava.
# ---------------------------------------------------------------------------
_DIMENSIONS = (
    "broj koraka rezonovanja",
    "brojčana/simbolička složenost",
    "složenost zapisa/reprezentacije",
    "nivo apstrakcije",
    "direktno naspram obrnutog rezonovanja",
    "broj kombinovanih uslova",
    "količina oslonca (scaffolding)",
    "bliskost distraktora tačnom odgovoru",
)

_LEVEL_TITLES = {1: "uvodni", 2: "standardna primjena", 3: "napredna primjena"}

_LEVEL_ANCHORS = {
    1: (
        "jedna direktna primjena: da/ne, prepoznavanje, klasifikacija, izbor, račun ili zamjena vrijednosti; bez povezanih međukoraka",
        "mali, jednostavni brojevi/simboli — direktno prepoznatljivi",
        "najjednostavniji uobičajeni zapis te vještine, bez ugniježdenih izraza",
        "konkretan, opipljiv primjer — bez uopštavanja",
        "direktno rezonovanje (primijeni pravilo naprijed, ne unazad)",
        "tačno jedan uslov/podatak koji se provjerava odjednom",
        "pitanje samo imenuje traženu radnju, bez skrivenih koraka",
        "distraktori su OČIGLEDNO različiti od tačnog odgovora",
    ),
    2: (
        "dva kratka, povezana koraka",
        "umjereni brojevi/simboli, uobičajeni za razred i lekciju",
        "standardan zapis te vještine, po potrebi jedan ugniježđen izraz",
        "kombinacija konkretnog primjera i opšteg pravila",
        "pretežno direktno, uz po jedan korak provjere",
        "dva povezana uslova/podatka ili dvije povezane ideje; samo kombinovanje nije napredni nivo",
        "pitanje ne razlaže korake — učenik sam bira put",
        "distraktori su vjerodostojni, ali i dalje razlučivi pažljivim računom",
    ),
    3: (
        "više povezanih koraka ILI jedan korak koji zahtijeva planiranje unaprijed",
        "veći ili složeniji brojevi/simbolički izrazi, i dalje unutar okvira lekcije",
        "zapis sa više ugniježđenih dijelova, ali i dalje standardna notacija lekcije",
        "prenošenje pravila na manje uobičajen, ali i dalje pripadajući primjer",
        "obrnuto rezonovanje gdje je prirodno za tu lekciju (npr. iz rezultata nazad na uslov)",
        "tri ili više kombinovanih uslova/podataka",
        "minimalan oslonac — učenik sam strukturira cio postupak",
        "distraktori su blizu tačnog odgovora — česte, uvjerljive greške",
    ),
}


def prompt_block(level):
    """Kratak, FIKSAN tekst za dati nivo — ISTI izvor koriste i Tutor i
    Recenzent (matbot/prompts.py::build_input, matbot/lesson_fidelity.py::
    build_input), da nikad ne dobiju dvije nezavisno formulisane verzije
    „istog“ pravila. Nikad ne pominje lekciju, oblast ni porodicu — te
    ostaju u ODVOJENOM, već postojećem bloku (PORODICA ZADATKA)."""
    level = min(max(int(level), MIN_LEVEL), MAX_LEVEL)
    title = _LEVEL_TITLES[level]
    lines = [f"CILJANI NIVO TEŽINE: {level} ({title})"]
    for dimension, anchor in zip(_DIMENSIONS, _LEVEL_ANCHORS[level]):
        lines.append(f"- {dimension}: {anchor}")
    lines.append(
        "OVAJ ZADATAK MORA OSTATI TAČNO ISTA VRSTA/FORMA LEKCIJE NA SVAKOM "
        "NIVOU — dimenzije iznad se smiju pomjeriti, vještina/porodica/oblik "
        "zadatka se NIKAD ne mijenja."
    )
    return "\n".join(lines)
