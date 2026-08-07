"""Potpunost determinističkog POSTUPKA (rješenja) — regresija talasa F5F.

ŽIVI NALAZ (talas F5F, scenariji F02/F03, lekcije 8-08-011 i 8-04-004):
postupak za površinu kruga glasio je „Primijenimo formulu: $P = \\pi r^2$.
Računamo: $P = 16\\pi = 16\\pi$ cm².“ — lanac se već završavao konačnom
vrijednošću pa je sastavljač paketa NA NJEGA ponovo dodao prikaz odgovora
(degenerisana jednakost), a korak uvrštavanja uopšte nije bio u rješenju,
pa je cijeli „potpuni postupak“ imao 70–79 znakova i pao na živoj provjeri
`solution_complete` (< 80 znakova). Ovdje se za SVAKU geometrijsku vrstu
dokazuje: postupak sadrži formulu i računski lanac bez degenerisanih
jednakosti i dovoljno je razrađen da prođe isti živi prag.
"""
import random
import re

import pytest

from matbot.deterministic import core, geometry

_SEGMENT = re.compile(r"\$([^$]+)\$")

# Isti prag kao živa provjera `solution_complete` u tools/practice_eval/checks.py.
MIN_SOLUTION_LENGTH = 80


def _degenerate_equalities(text):
    """Vrati parove susjednih ČLANOVA lanca jednakosti koji su identični."""
    offenders = []
    for segment in _SEGMENT.findall(text):
        parts = [part.strip() for part in segment.split("=")]
        for left, right in zip(parts, parts[1:]):
            if left and left == right:
                offenders.append(f"{left} = {right}")
    return offenders


@pytest.mark.parametrize("level", [1, 2, 3])
@pytest.mark.parametrize("kind", sorted(geometry._KINDS))
def test_geometry_solutions_are_complete_and_non_degenerate(kind, level):
    produced = 0
    for seed in range(12):
        rng = random.Random(seed)
        try:
            package = geometry.generate_package(
                "lekcija", "Naslov lekcije", {"kinds": [kind]}, level, rng)
        except core.DeterministicGenerationError:
            continue
        produced += 1
        solution = package.solution
        assert not _degenerate_equalities(solution), (kind, level, solution)
        assert len(solution.strip()) >= MIN_SOLUTION_LENGTH, \
            (kind, level, len(solution.strip()), solution)
    assert produced, (kind, level, "nijedan paket nije nastao")
