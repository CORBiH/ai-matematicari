"""Deterministički serverski generatori Practice paketa (Faza 4H+).

Jedan generator po SEMANTIČKOJ PORODICI, nikad po lekciji: razlike među
lekcijama nose isključivo parametri kompajliranog ugovora porodice
(data/lesson_semantic_assignments.json). Ovo NIJE drugi vrhovni pipeline —
generatori se koriste ISKLJUČIVO iz postojećeg Practice orkestratora
(matbot/tutor/pipeline.py), koji za njihov izlaz pokreće iste serverske
validatore i istu objavu kao za model-pakete.

REGISTAR: modul kapaciteta služi jednu ili više porodica (`FAMILY_ID` ili
`FAMILY_IDS`) i mora izložiti `supports(parameters)` i
`generate_package(lesson_id, lesson_title, parameters, level, rng=None)`.
Lekcija je pokrivena SAMO kad njen blocking ugovor pripada registrovanoj
porodici čije parametre modul u potpunosti podržava — sve ostalo ostaje na
model-putu bez ijedne izmjene ponašanja.
"""
from matbot.deterministic import (anglework, arithmetic, conversions,
                                  equations, fractions, functions, geometry,
                                  numbertheory, ordering, polynomials, powers,
                                  quantities, ratio, systems, units)
from matbot.deterministic.core import DeterministicGenerationError  # noqa: F401


def _family_ids(module):
    declared = getattr(module, "FAMILY_IDS", None)
    return tuple(declared) if declared else (module.FAMILY_ID,)


_MODULES = (anglework, arithmetic, conversions, equations, fractions,
            functions, geometry, numbertheory, ordering, polynomials,
            powers, quantities, ratio, systems, units)

GENERATORS = {family_id: module
              for module in _MODULES
              for family_id in _family_ids(module)}
