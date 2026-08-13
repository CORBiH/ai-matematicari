"""PRIMARNA VJEŠTINA LEKCIJE — kompajlirani kurikularni ishodi po lekciji.

ZAŠTO POSTOJI (živi nalaz, semantički zanos susjedne vještine): model je za
lekciju „Skup racionalnih brojeva Q“ pisao zadatke čija je stvarna vještina
sabiranje razlomaka. Mjerilo je do tada bilo samo „sesija je ostala na lekciji“,
a to NIJE isto što i „zadatak ispituje ono što lekcija predaje“.

Uzrok nije bio nedostatak podataka nego INSTALACIJA: mjereno nad kurikulumom,
152 od 184 model-podržanih lekcija dobijalo je u promptu SAMO naslov, dok je
kanonsko mapiranje NPP ishoda na lekcije postojalo i stajalo neiskorišteno.

Ovaj modul je čitalac generisanog artefakta (scripts/build_lesson_objectives.py):
  primary_skills        — ishodi TE lekcije; zadatak mora ciljati jedan od njih
  supporting_concepts   — smiju se pojaviti kao alat, NIKAD kao cilj
  neighbour_exclusions  — ishodi SUSJEDNIH lekcija; nikad cilj ovog zadatka

Odsustvo zapisa NIJE greška: lekcija bez mapirane evidencije ponaša se tačno
kao ranije (naslov + oblast + razred). Podatak se nikad ne izmišlja.
"""
import json
import threading
from pathlib import Path
from types import MappingProxyType

_PATH = Path(__file__).resolve().parent.parent / "data" / "lesson_objectives.compiled.json"
_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is None:
            try:
                payload = json.loads(_PATH.read_text(encoding="utf-8"))
                lessons = payload.get("lessons", {}) or {}
            except (OSError, ValueError):
                # Nedostupan artefakt NIKAD ne ruši Practice — samo znači da
                # lekcije nemaju dodatni semantički signal, kao i ranije.
                lessons = {}
            _cache = MappingProxyType(lessons)
    return _cache


def objectives_for(lesson_id):
    """Zapis lekcije ili prazan dict. Nikad ne baca."""
    return _load().get(str(lesson_id or ""), {})


def primary_skills(lesson_id):
    return tuple(objectives_for(lesson_id).get("primary_skills", ()) or ())


def supporting_concepts(lesson_id):
    return tuple(objectives_for(lesson_id).get("supporting_concepts", ()) or ())


def neighbour_exclusions(lesson_id):
    return tuple(objectives_for(lesson_id).get("neighbour_exclusions", ()) or ())


def coverage():
    """(lekcija s ishodima, lekcija s primarnom vještinom) — za testove/audit."""
    lessons = _load()
    with_primary = sum(1 for row in lessons.values() if row.get("primary_skills"))
    return len(lessons), with_primary
