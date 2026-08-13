"""Koje arhetipe lekcija podržava i KOJI server traži sljedeći.

ZAŠTO SERVER BIRA, A NE MODEL (mjereno nad 921 objavljenim zadatkom):
raspodjela arhetipa bila je 68 % `DIRECT_COMPUTE`, a `ERROR_ANALYSIS`,
`FIND_MISSING_VALUE` i `COMPARE_RESULTS` ispod 1 % svaki. Uputa „budi
drugačiji“ očito nije dovoljna — model se vraća na najčešći oblik. Zato server
IMENUJE sljedeći oblik, a model ga ispunjava sadržajem.

Rotacija je LRU: prvo oblici koje sesija još nije koristila, pa najstariji.
Preferencija NIJE dozvola da se izađe iz lekcije — semantičke kapije ostaju
iznad nje, a `NARROW_SCOPE` lekcija se ne tjera u izmišljanje.

Inertan bez artefakta: bez `data/task_archetype_support.json` ponašanje je
bajt-identično zatečenom.
"""
import json
import os
from functools import lru_cache
from pathlib import Path

from matbot import task_archetypes

_ARTIFACT = Path(__file__).resolve().parent.parent / "data" / "task_archetype_support.json"


def _enabled() -> bool:
    """Rotacija arhetipa. `disabled` vraća ponašanje bez preferencije."""
    return os.environ.get("MATBOT_ARCHETYPE_ROTATION", "enabled") != "disabled"


@lru_cache(maxsize=1)
def _payload() -> dict:
    try:
        return json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def supported(lesson_id) -> tuple:
    """Arhetipi koje lekcija legitimno podržava; prazno = nema mjerila."""
    row = (_payload().get("lessons") or {}).get(lesson_id) or {}
    return tuple(row.get("supported") or ())


def is_narrow(lesson_id) -> bool:
    """True kad lekcija ima premalo legitimnih oblika da bi se tražila rotacija."""
    row = (_payload().get("lessons") or {}).get(lesson_id) or {}
    return bool(row.get("narrow_scope"))


def preferred_archetype(lesson_id, recent_archetypes=()) -> str:
    """Oblik koji server traži za SLJEDEĆI zadatak, ili '' kad ne traži ništa.

    Prvo neiskorišten podržan oblik; kad su svi iskorišteni, reciklira se
    NAJDAVNIJE korišten (LRU). Uska lekcija ne dobija preferenciju — tamo je
    vjernost lekciji važnija od raznolikosti."""
    if not _enabled():
        return ""
    options = supported(lesson_id)
    if len(options) < 2 or is_narrow(lesson_id):
        return ""
    recent = [a for a in (recent_archetypes or ()) if a]
    unused = [a for a in options if a not in recent]
    if unused:
        # Stabilan poredak (ne slučajan): ista sesija daje isti raspored, a
        # razlika među sesijama dolazi od stvarne historije, ne od kocke.
        return unused[0]
    for archetype in recent:            # `recent` je od najstarijeg ka najnovijem
        if archetype in options:
            return archetype
    return options[0]


def coverage():
    lessons = _payload().get("lessons") or {}
    narrow = sum(1 for row in lessons.values() if row.get("narrow_scope"))
    return len(lessons), narrow


def classify(task_text, option_texts=()):
    """Prolaz do determinističkog klasifikatora — jedan ulaz za cijeli projekat."""
    return task_archetypes.classify(task_text, option_texts)
