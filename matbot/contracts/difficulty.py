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
