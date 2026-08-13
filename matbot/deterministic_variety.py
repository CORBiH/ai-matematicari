"""Mjerena raznolikost determinističkih porodica — čitalac artefakta.

ZAŠTO POSTOJI (živi nalaz, statička revizija 352 determinističke lekcije):
nulti poziv i nula sekundi imaju veliku vrijednost, ali ne po svaku cijenu.
49 lekcija mjereno je kao slabe: na tri nivoa težine daju istu rečenicu s
drugim brojevima (npr. dvije arhetipske rečenice na 18 uzoraka). Za učenika
koji tri puta traži teže to nije ljestvica nego ponavljanje.

Odluka je PODATAK, ne grananje po lekciji: mjerenje se kompajlira u
`data/deterministic_variety.json` (scripts/build_deterministic_variety.py), a
birač strategije samo pita „je li ova porodica mjereno slaba?“. Dodavanje ili
popravak generatora mijenja mjerenje, ne Python.

Inertan po dizajnu: bez artefakta ponašanje je bajt-identično zatečenom.
"""
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_ARTIFACT = Path(__file__).resolve().parent.parent / "data" / "deterministic_variety.json"


def _enabled() -> bool:
    """Prekidač rute — PODRAZUMIJEVANO ISKLJUČEN, i to namjerno.

    Uključivanje oduzima determinističku rutu 49 lekcijama, a time i garanciju
    „nula poziva, nula sekundi, matematika dokazana serverom“ koju te porodice
    danas daju. To je zamjena jedne stvarne vrijednosti za drugu i mora biti
    svjesna odluka s vlastitim mjerenjem, ne nusproizvod ove revizije. Mjerenje
    i mehanizam postoje; odluka ostaje na čovjeku."""
    return os.environ.get("MATBOT_DETERMINISTIC_VARIETY_GATE", "disabled") == "enabled"


@lru_cache(maxsize=1)
def _payload() -> dict:
    try:
        return json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Nema mjerenja → nema odluke. Odsustvo podatka nikad ne mijenja rutu.
        return {}


@lru_cache(maxsize=1)
def _weak() -> frozenset:
    return frozenset(_payload().get("weak_variety_lessons") or ())


def is_weak(lesson_id) -> bool:
    """True samo kad je lekcija MJERENA i mjerenje ju je proglasilo slabom."""
    if not lesson_id or not _enabled():
        return False
    return lesson_id in _weak()


def measurement(lesson_id) -> dict:
    return dict((_payload().get("measurements") or {}).get(lesson_id) or {})


def coverage():
    payload = _payload()
    return len(payload.get("measurements") or {}), len(_weak())
