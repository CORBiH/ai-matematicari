"""Teže/lakše kao PODATAK, ne kao pozicija u ručno složenoj listi.

Ranije je „teži zadatak“ značilo „uzmi prvu porodicu iz liste te lekcije“ —
dakle težina je zavisila od toga šta je neko slučajno napisao prvo. Sada svaka
lekcija deklariše KOJE dimenzije smiju rasti i u kojim granicama, pa promjena
težine ne može promijeniti vještinu.

ISKRENO O OBIMU: server izvodi i STVARNO provjerava samo dimenzije koje se mogu
izmjeriti iz dokaza (`DERIVABLE`). Ostale dimenzije (npr. sličnost distraktora)
smiju stajati u ugovoru i idu u prompt kao cilj, ali se NE tvrdi da su
deterministički provjerene — „nemjerljivo“ se ne prikazuje kao „provjereno“.
"""
from dataclasses import dataclass, field

# Dimenzije koje se mogu izračunati iz strukturisanog dokaza.
DERIVABLE = ("operand_magnitude", "term_count")

# Granice veličine operanada po nivou (nivo 1 = najmanji brojevi).
_MAGNITUDE_LEVELS = ((12, 1), (50, 2))
_MAX_MAGNITUDE_LEVEL = 3


@dataclass(frozen=True)
class DifficultyResult:
    valid: bool
    code: str = "ok"
    details: dict = field(default_factory=dict)


def magnitude_level(max_abs_operand):
    for limit, level in _MAGNITUDE_LEVELS:
        if max_abs_operand <= limit:
            return level
    return _MAX_MAGNITUDE_LEVEL


def derive(facts):
    """Izmjereni nivo po dimenziji, isključivo iz dokaza."""
    return {
        "operand_magnitude": magnitude_level(facts.max_abs_operand),
        "term_count": facts.term_count,
    }


def adjustable_dimensions(contract):
    """Dimenzije koje lekcija dopušta mijenjati, determinističkim redoslijedom."""
    return tuple(name for name in DERIVABLE if name in contract.difficulty_dimensions)


def check_within_bounds(contract, facts):
    """Izmjerena težina mora biti unutar granica koje je lekcija deklarisala."""
    derived = derive(facts)
    for name, bound in contract.difficulty_dimensions.items():
        if name not in DERIVABLE:
            continue  # nemjerljivo → ne tvrdimo da je provjereno
        level = derived[name]
        if not bound.contains(level):
            return DifficultyResult(False, "difficulty_out_of_bounds", {
                "dimension": name, "level": level,
                "bounds": [bound.minimum, bound.maximum],
            })
    return DifficultyResult(True, "ok", {"derived": derived})


def target_levels(contract, difficulty_request=""):
    """Ciljni nivoi za sljedeći zadatak (ulaze u prompt).

    „harder“ podiže PRVU dimenziju koja još ima prostora, „easier“ spušta prvu
    koja ga ima — deterministički, bez slučajnosti. Kad prostora nema, cilj
    ostaje na granici: zahtjev za težim nikad ne izlazi izvan ugovora."""
    request = (difficulty_request or "").strip().lower()
    levels = {
        name: bound.default
        for name, bound in contract.difficulty_dimensions.items()
    }
    if request not in ("harder", "easier"):
        return levels
    for name in adjustable_dimensions(contract):
        bound = contract.difficulty_dimensions[name]
        if request == "harder" and levels[name] < bound.maximum:
            levels[name] = levels[name] + 1
            break
        if request == "easier" and levels[name] > bound.minimum:
            levels[name] = levels[name] - 1
            break
    return levels


def invariant_conflicts(contract):
    """Ograničenje koje je lekcija proglasila nepromjenjivim ne smije se
    pojaviti i kao dimenzija težine — inače bi „teže“ smjelo da ga pomjeri."""
    return sorted(set(contract.invariant_constraints) & set(contract.difficulty_dimensions))


# ---------------------------------------------------------------------------
# UNIVERZALNI KONTROLER NIVOA (matbot/difficulty_level.py) — adapter za
# lekcije s ugovorom. Ništa iznad ove tačke se ne mijenja: target_levels(),
# derive(), check_within_bounds(), adjustable_dimensions(), prompt_line()
# ostaju netaknuti (i dalje ih koristi zatečeni relativni "harder"/"easier"
# put kad je univerzalni kontroler isključen).
#
# ŽIVI NALAZ (audit prije uvođenja ovog adaptera): za SVIH ŠEST trenutno
# uključenih ugovora, OBJE mjerljive (DERIVABLE) dimenzije imaju
# `minimum == default` (npr. operand_magnitude: min=1, default=1, max=3).
# Naivno mapiranje Nivo1→minimum/Nivo2→default/Nivo3→maximum bi zato Nivo 1
# i Nivo 2 učinilo MATEMATIČKI IDENTIČNIM za svaku trenutno mjerljivu
# dimenziju, dok bi sesija tvrdila da se nivo povećao. `capability_for`
# otvoreno prijavljuje tu granicu umjesto da je sakrije ili izmisli nove
# brojeve u data/contract_templates.json.
# ---------------------------------------------------------------------------

CAPABILITY_THREE_LEVEL = "three_level"
CAPABILITY_TWO_LEVEL = "two_level"
CAPABILITY_ONE_LEVEL = "one_level"


def capability_for(contract):
    """three_level | two_level | one_level — SAMO nad mjerljivim (DERIVABLE)
    dimenzijama koje lekcija koristi. Čisto pitanje o već deklarisanim
    granicama (adjustable_dimensions, postojeća funkcija) — nema lesson-ID
    grananja i ne izmišlja nijedan broj."""
    has_three = False
    has_two = False
    for name in adjustable_dimensions(contract):
        bound = contract.difficulty_dimensions[name]
        if bound.minimum < bound.default < bound.maximum:
            has_three = True
        if bound.minimum < bound.maximum:
            has_two = True
    if has_three:
        return CAPABILITY_THREE_LEVEL
    if has_two:
        return CAPABILITY_TWO_LEVEL
    return CAPABILITY_ONE_LEVEL


def target_levels_for_level(contract, level):
    """Apsolutni cilj po dimenziji za UNIVERZALNI nivo (1/2/3) — generička
    projekcija na već deklarisane granice TE lekcije, prilagođena stvarnoj
    sposobnosti ugovora (capability_for). Level 2 na "three_level" ugovoru
    je identičan dosadašnjem `bound.default` (bez zahtjeva); na "two_level"
    ugovoru (trenutno SVIH šest) Nivo 1 i Nivo 2 dijele isti cilj (minimum)
    jer podaci to iskreno ne razlikuju — Nivo 3 i dalje cilja na maximum.
    Nema lesson-ID grananja: koristi SAMO contract.difficulty_dimensions,
    iste podatke koje već koristi target_levels()."""
    capability = capability_for(contract)
    levels = {}
    for name, bound in contract.difficulty_dimensions.items():
        if name not in DERIVABLE or capability == CAPABILITY_THREE_LEVEL:
            if level <= 1:
                levels[name] = bound.minimum
            elif level >= 3:
                levels[name] = bound.maximum
            else:
                levels[name] = bound.default
        elif capability == CAPABILITY_TWO_LEVEL:
            levels[name] = bound.minimum if level <= 2 else bound.maximum
        else:  # one_level — minimum == default == maximum
            levels[name] = bound.minimum
    return levels


def measurable_target_profile(contract, level):
    """Samo DERIVABLE ∩ adjustable_dimensions(contract) — isti filter kao
    verify_matches_target(). Nemjerljive dimenzije (npr.
    distractor_similarity, koja NA STVARNIM podacima danas ima tri različite
    deklarisane vrijednosti) se NIKAD ne koriste da bi se tvrdilo da se
    generisan zadatak promijenio, jer se to ne može nezavisno dokazati iz
    kostura — vidi practice.py-ovu upotrebu za `generation_changed`."""
    levels = target_levels_for_level(contract, level)
    return {name: levels[name] for name in adjustable_dimensions(contract) if name in levels}


def verify_matches_target(contract, facts, requested_levels):
    """Izvedene (SAMO DERIVABLE) vrijednosti moraju TAČNO odgovarati
    traženom cilju po dimenziji — ne samo biti unutar dozvoljenih granica
    (to i dalje isključivo provjerava check_within_bounds, nepromijenjeno).
    Nemjerljive/neprilagodljive dimenzije se preskaču (isti filter kao
    adjustable_dimensions). Poziva se SAMO kad je univerzalni kontroler
    aktivan za ovaj turn (matbot/contracts/generator.py::generate)."""
    derived = derive(facts)
    for name in adjustable_dimensions(contract):
        if name not in requested_levels:
            continue
        if derived[name] != requested_levels[name]:
            return DifficultyResult(False, "target_profile_mismatch", {
                "dimension": name, "derived": derived[name],
                "requested": requested_levels[name],
            })
    return DifficultyResult(True, "ok", {"derived": derived})


def prompt_line(contract, difficulty_request=""):
    levels = target_levels(contract, difficulty_request)
    if not levels:
        return ""
    described = ", ".join(f"{name}={level}" for name, level in sorted(levels.items()))
    line = f"CILJANA TEŽINA (dimenzije, ne vještina): {described}."
    if difficulty_request in ("harder", "easier"):
        line += (
            " Zahtjev za težim/lakšim zadatkom mijenja SAMO ove dimenzije — "
            "vještina, dozvoljene operacije i ograničenja operanada ostaju ista."
        )
    return line
