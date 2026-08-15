"""B. Audit porodica zadataka nad SVIH 534 stvarnih lekcija iz data/topics.json.

Cilj: dokazati da svaka lekcija ima bar 2 (po mogućnosti 3+) genuinski
različite primjenjive porodice, da nijedna porodica u katalogu nije
nedostupna, i da rotacija ("Novi zadatak" prije odgovora, više zaredom) nikad
lažno označava porodicu kao savladanu niti se zaglavljuje na istoj.
"""
import json
from collections import Counter
from pathlib import Path

from matbot import task_families as tf

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "topics.json"


def _all_lessons():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    for grade, grade_data in data["grades"].items():
        for lesson in grade_data["lessons"]:
            yield int(grade), lesson["id"], lesson["oblast"], lesson["title"]


# ---------------------------------------------------------------------------
# Statistike nad cijelim kurikulumom
# ---------------------------------------------------------------------------

def test_every_lesson_has_at_least_two_applicable_families():
    under_two = [
        (g, topic_id, o, t, tf.applicable_families(g, o, t, lesson_id=topic_id))
        for g, topic_id, o, t in _all_lessons()
        if len(tf.applicable_families(g, o, t, lesson_id=topic_id)) < 2
    ]
    assert not under_two, f"Lekcije s manje od 2 porodice: {under_two[:10]}"


def test_family_count_statistics_over_full_curriculum():
    counts = [
        len(tf.applicable_families(g, o, t, lesson_id=topic_id))
        for g, topic_id, o, t in _all_lessons()
    ]
    minimum, maximum = min(counts), max(counts)
    average = sum(counts) / len(counts)
    distribution = Counter(counts)

    # Zaključana očekivanja nad TRENUTNIM katalogom (vidi izvještaj) — svaka
    # promjena kataloga koja ovo pokvari mora biti svjesna odluka, ne slučajna
    # regresija skupa primjenjivih porodica.
    assert minimum >= 3
    assert maximum <= 8
    assert 6.0 <= average <= 7.5
    assert all(count >= 2 for count in distribution)


def test_no_lesson_has_exactly_two_families_given_current_catalog():
    """Trenutni katalog (fallback grupe od 4-8 članova) nikad ne producira
    tačno 2 primjenjive porodice — ako se ovo promijeni, provjeri da razlog
    nije slučajno sužavanje neke fallback grupe."""
    exactly_two = [
        (g, topic_id, o, t) for g, topic_id, o, t in _all_lessons()
        if len(tf.applicable_families(g, o, t, lesson_id=topic_id)) == 2
    ]
    assert exactly_two == []


def test_no_family_in_the_catalog_is_unreachable():
    used = set()
    for g, topic_id, o, t in _all_lessons():
        used.update(tf.applicable_families(g, o, t, lesson_id=topic_id))
    catalog = set(tf.FAMILY_DESCRIPTIONS.keys())
    unreachable = catalog - used
    assert not unreachable, f"Nedostupne porodice: {unreachable}"


def test_every_lesson_family_set_has_no_duplicate_entries():
    for g, topic_id, o, t in _all_lessons():
        families = tf.applicable_families(g, o, t, lesson_id=topic_id)
        assert len(families) == len(set(families)), (g, topic_id, o, t)


# ---------------------------------------------------------------------------
# 1-2. "Novi zadatak" traženo prije odgovora, i više puta zaredom
# ---------------------------------------------------------------------------
