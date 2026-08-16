# -*- coding: utf-8 -*-
"""Simbol koji nosi pojam više ne nestaje u normalizaciji (P1, znak `%`).

ŽIVI NALAZ: `_normalize` briše svu interpunkciju (`[^\\w\\s]`), pa „Koliko je
15% od 300 KM?“ postane „koliko je 15 od 300 km“. Nijedan pojam se ne imenuje,
guard ne može dokazati drugu temu i konzervativno vraća JAK kontekst — učenik u
lekciji „Osna simetrija“ dobije predavanje o simetriji umjesto odgovora.

Ispravka preslikava brojčani `%` na VEĆ POSTOJEĆI pojam `procenat` iz
leksikona, pa `15%` dobija tačno onu težinu koju već ima riječ „posto“.
"""
import pytest

from matbot import lesson_relevance as lr
from matbot.topics import lesson_info

SIM = lesson_info(6, "6-12-001")          # Osna simetrija u ravni
PCT = lesson_info(6, "6-06-002")          # Postotak/procenat i procenat broja
RAZLOMCI = lesson_info(6, "6-04-009")


def strong(message, lesson):
    return lr.lesson_context_is_strong(message, lesson["title"], lesson["oblast"])


# ---------------------------------------------------------------------------
# A–C: PROCENAT SE PREPOZNAJE, SIMETRIJA GA VIŠE NE PREGLASAVA
# ---------------------------------------------------------------------------

PERCENT_FORMS = [
    "Koliko je 15% od 300 KM?",                     # A
    "Objasni koliko je 15% od 300 KM.",             # B
    "Kako se računa 15% od 300 KM?",                # C
    "Zašto je 15% od 300 KM jednako 45 KM?",
    "Pojasni mi koliko je 15% od 300 KM.",
]


@pytest.mark.parametrize("message", PERCENT_FORMS)
def test_ABC_percentage_subject_is_recognised(message):
    assert "procenat" in lr.named_topics(message), message


@pytest.mark.parametrize("message", PERCENT_FORMS)
def test_ABC_symmetry_lesson_no_longer_overrides(message):
    assert strong(message, SIM) is False, message


def test_B_prefixed_matches_unprefixed():
    """Deiktički prefiks ne smije mijenjati ishod (doktrina iz f548c09)."""
    for lesson in (SIM, PCT, RAZLOMCI):
        plain = strong("Koliko je 15% od 300 KM?", lesson)
        for prefix in ("Objasni ", "Objasni mi ", "Kako se računa ", "Zašto je "):
            assert strong(prefix + "15% od 300 KM?", lesson) == plain, (prefix, lesson["id"])


# ---------------------------------------------------------------------------
# D: U LEKCIJI O PROCENTIMA ISTO PITANJE JE RELEVANTNO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", PERCENT_FORMS)
def test_D_percentage_lesson_stays_strong(message):
    assert strong(message, PCT) is True, message


def test_D_all_percentage_lessons_stay_strong():
    for grade, lesson_id in ((6, "6-06-002"), (8, "8-03-017"), (8, "8-03-018")):
        info = lesson_info(grade, lesson_id)
        assert strong("Koliko je 15% od 300 KM?", info) is True, lesson_id


# ---------------------------------------------------------------------------
# E–H: OBLICI ZAPISA PROCENTA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Koliko je 12,5% od 80?",          # E
    "Koliko je 15 % od 300?",          # F
    "Povećaj 200 za 10%.",             # G
    "Smanji 500 za 25%.",              # H
    "Koliko je 0,5% od 1000?",
    "20% od 300 je koliko?",
    "Cijena je snižena za 30%.",
])
def test_EFGH_percentage_syntax_variants(message):
    assert "procenat" in lr.named_topics(message), message
    assert strong(message, SIM) is False, message


# ---------------------------------------------------------------------------
# I: GOLI ZNAK NIJE PREDMET
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", ["%", " % ", "Šta znači %?", "100 %%"])
def test_I_bare_symbol_is_not_a_subject_by_itself(message):
    if message == "100 %%":
        pytest.skip("brojčani obrazac postoji — nije goli znak")
    assert "procenat" not in lr.symbolic_topics(message), message


def test_I_percent_requires_a_number():
    assert lr.symbolic_topics("%") == set()
    assert lr.symbolic_topics("od % nema koristi") == set()
    assert lr.symbolic_topics("15%") == {"procenat"}


# ---------------------------------------------------------------------------
# J–L: DEIKTIČKA DOKTRINA IZ f548c09 OSTAJE NETAKNUTA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Zašto?", "Kako?", "Objasni.", "Objasni jednostavnije.", "Pojasni mi.",
    "Kako si to dobio?", "Zašto ovdje dijelimo sa 3?", "Ne razumijem.",
])
def test_JK_true_followups_unchanged(message):
    assert strong(message, SIM) is True, message
    assert not lr.carries_own_subject(message), message


@pytest.mark.parametrize("message", [
    "Objasni šta je osa simetrije.",
    "Kako određujemo osu simetrije figure?",
    "Zašto pravougaonik ima dvije ose simetrije?",
])
def test_L_same_lesson_questions_stay_strong(message):
    assert strong(message, SIM) is True, message


def test_L_self_contained_distinction_unchanged():
    assert lr.carries_own_subject("Zašto x=4?")
    assert lr.carries_own_subject("Kako se računa 3/4 + 2/5?")
    assert lr.carries_own_subject("Objasni 15% od 300.")
    assert not lr.carries_own_subject("Zašto?")


# ---------------------------------------------------------------------------
# M: OSTALI MODOVI NETAKNUTI
# ---------------------------------------------------------------------------

def test_M_guard_is_called_only_by_explain():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / "matbot"
    call = re.compile(r"^[^#\n]*\blesson_relevance\.\w+\s*\(", re.MULTILINE)
    callers = sorted(
        path.name for path in root.rglob("*.py")
        if path.name != "lesson_relevance.py"
        and call.search(path.read_text(encoding="utf-8")))
    assert callers == ["explain.py"], callers


# ---------------------------------------------------------------------------
# REVIZIJA: DVOSMISLENI ZNAKOVI NAMJERNO NISU DODANI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,lesson", [
    # `=` je svuda: površina u geometriji nije jednačina
    ("Zašto je P = 24 cm²?", SIM),
    # `²` je i jedinica površine
    ("Kolika je površina od 12 cm²?", SIM),
    # `√` živi unutar tuđih formula
    ("Zašto je c = √(a²+b²)?", lesson_info(8, "8-04-003")),
])
def test_ambiguous_symbols_are_not_mapped_to_concepts(message, lesson):
    """Guard i dalje ćuti — dokaz da nije napravljen simbolički motor tema."""
    assert lr.symbolic_topics(message) == set(), message
