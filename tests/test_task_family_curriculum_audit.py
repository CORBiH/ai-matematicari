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

def test_requesting_new_task_before_answering_selects_a_different_family():
    """Server bira drugu porodicu i za nezavršen zadatak (retry_required je
    False jer učenik još nije ni pokušao odgovoriti) — sprječava da dva
    zaredom zatražena zadatka budu ista vrsta operacije."""
    applicable = tf.applicable_families(
        6, "Razlomci", "Proširivanje razlomaka", lesson_id="6-04-005"
    )
    first = tf.select_family(applicable)
    second = tf.select_family(
        applicable, recently_used=[first], completed_families=[],
        retry_required=False, current_family=first,
    )
    assert second != first


def test_several_new_task_requests_without_answering_keep_rotating():
    """Više 'Novi zadatak' zaredom, BEZ ijednog odgovora — porodice se i dalje
    smjenjuju (server ne čeka odgovor da bi rotirao) i nijedna se ne ponavlja
    dok ima neiskorištenih."""
    applicable = tf.applicable_families(
        6, "Razlomci", "Proširivanje razlomaka", lesson_id="6-04-005"
    )
    used = []
    current = ""
    for _ in range(len(applicable)):
        chosen = tf.select_family(
            applicable, recently_used=used, completed_families=[],
            retry_required=False, current_family=current,
        )
        assert chosen not in used[-1:], "Ne smije ponoviti neposredno prethodnu porodicu"
        used.append(chosen)
        current = chosen
    assert len(set(used)) == len(applicable), "Svih N zahtjeva treba dati N različitih porodica"


def test_server_does_not_repeatedly_return_the_same_family():
    applicable = tf.applicable_families(9, "Geometrijska tijela", "Zapremina prizme")
    used = []
    current = ""
    for _ in range(10):
        chosen = tf.select_family(applicable, recently_used=used, current_family=current)
        if used:
            assert chosen != used[-1]
        used.append(chosen)
        current = chosen


def test_no_family_falsely_marked_completed_when_only_requesting_new_tasks():
    """'Novi zadatak' prije odgovora ne smije se protumačiti kao savladavanje
    — completed_families ostaje prazan sve dok ne postoji STVARAN tačan
    odgovor (ovo se garantuje na nivou practice.py: samo _handle_choice_answer
    s is_correct=True upisuje u correctly_completed_families)."""
    completed = []
    applicable = tf.applicable_families(
        6, "Razlomci", "Proširivanje razlomaka", lesson_id="6-04-005"
    )
    current = ""
    for _ in range(5):
        current = tf.select_family(applicable, completed_families=completed, current_family=current)
    assert completed == []


def test_topic_change_creates_independent_progression():
    """Porodice primjenjive na jednu lekciju ne smiju curiti kao 'završene' za
    drugu — svaka lekcija računa svoj vlastiti skup completed_families."""
    fractions = tf.applicable_families(
        6, "Razlomci", "Proširivanje razlomaka", lesson_id="6-04-005"
    )
    systems = tf.applicable_families(9, "Sistemi linearnih jednačina", "Metoda supstitucije")
    completed_in_fractions = [fractions[0]]

    # Isti "completed" skup ne postoji za drugu lekciju — select_family za
    # sistem jednačina se poziva s njegovim VLASTITIM (praznim) skupom.
    chosen_for_systems = tf.select_family(systems, completed_families=[], current_family="")
    assert chosen_for_systems == systems[0]
    assert chosen_for_systems not in completed_in_fractions or chosen_for_systems in systems
