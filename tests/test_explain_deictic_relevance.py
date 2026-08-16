# -*- coding: utf-8 -*-
"""Deiktički prefiks više ne gasi provjeru relevantnosti lekcije (P1).

ŽIVI NALAZ: `_DEICTIC_PHRASES` sadrži i gole riječi „objasni“, „kako“,
„zasto“, „pojasni“, a `_is_deictic` hvata svaku poruku koja tako POČINJE, pa je
jedna uvodna riječ gasila cijelu provjeru. Mjereno nad svih 536 lekcija:

    „Pitagorina teorema u pravouglom trouglu.“   slab kontekst u 487 lekcija
    „Objasni: <ista rečenica>“                   slab kontekst u   0 lekcija

Ista matematika, suprotan zaključak. Ispravka NIJE brisanje tih riječi — one
postoje zbog stvarnih nastavaka („Kako?“, „Zašto?“) koji bez lekcije nemaju
značenje. Razlika je NOSI LI PORUKA VLASTITI PREDMET.
"""
import pytest

from matbot import lesson_relevance as lr

SIMETRIJA = ("Osna simetrija u ravni", "Izometrijske transformacije i konstrukcije")
RAZLOMCI = ("Sabiranje i oduzimanje razlomaka jednakih imenilaca", "Razlomci")


def strong(message, lesson=SIMETRIJA):
    return lr.lesson_context_is_strong(message, lesson[0], lesson[1])


# ---------------------------------------------------------------------------
# A–B: SAMOSTALNO PITANJE O IZABRANOJ LEKCIJI — i dalje relevantno
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Objasni šta je osa simetrije.",
    "Kako određujemo osu simetrije figure?",
    "Zašto pravougaonik ima dvije ose simetrije?",
    "Objasni mi osnu simetriju.",
])
def test_AB_self_contained_same_lesson_stays_relevant(message):
    assert strong(message) is True


def test_AB_same_lesson_questions_are_recognised_as_self_contained():
    """Nisu prazan deiktički nastavak — imaju svoj predmet, samo se poklapa."""
    assert lr.carries_own_subject("Objasni šta je osa simetrije.")
    assert lr.carries_own_subject("Kako određujemo osu simetrije figure?")


# ---------------------------------------------------------------------------
# C–E, J: SAMOSTALNO PITANJE IZVAN LEKCIJE — prefiks ne daje zaobilaznicu
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Objasni koliko je 15% od 300 KM.",
    "Kako se računa 3/4 + 2/5?",
    "Zašto je x=4 u 2x+5=13?",
    "Pojasni Pitagorinu teoremu.",
    "Objasni mi šta je presjek skupova.",
    "A sad objasni 20% od 500.",
])
def test_CDEJ_prefixed_self_contained_gets_no_deictic_bypass(message):
    """Poruka NIJE prazan nastavak — mora se suditi po sadržaju."""
    assert lr.carries_own_subject(message), message


@pytest.mark.parametrize("message,lesson", [
    ("Pojasni Pitagorinu teoremu.", SIMETRIJA),
    ("Objasni mi šta je presjek skupova.", SIMETRIJA),
    ("Kako se računa površina kruga poluprečnika 4 cm?", SIMETRIJA),
    ("Objasni mi osnu simetriju.", RAZLOMCI),
])
def test_CDEJ_off_lesson_named_subject_is_weak_context(message, lesson):
    assert lr.lesson_context_is_strong(message, lesson[0], lesson[1]) is False


# ---------------------------------------------------------------------------
# F–H, L–M: STVARNI NASTAVCI — ponašanje nepromijenjeno
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Kako?", "Zašto?", "Objasni.", "Objasni mi.", "Pojasni.", "Pojasni mi.",
    "Objasni jednostavnije.", "Objasni mi jednostavnije.", "Objasni drugačije.",
    "Kako si to dobio?", "Zašto ovdje dijelimo sa 3?", "Ne razumijem.",
    "Daj mi još jedan primjer.", "Nastavi.", "Dalje.", "Ponovi još jednom.",
    "Objasni mi ovo.", "Šta to znači?",
])
def test_FGHLM_true_followups_keep_previous_context(message):
    assert strong(message) is True
    assert not lr.carries_own_subject(message), message


# ---------------------------------------------------------------------------
# K: KRATKO ALI SAMOSTALNO
# ---------------------------------------------------------------------------

def test_K_short_deictic_with_formula_is_not_an_empty_followup():
    assert lr.carries_own_subject("Zašto x=4?")
    assert lr.carries_own_subject("Kako 2+2=4?")
    # a bez formule ostaje nastavak
    assert not lr.carries_own_subject("Zašto?")


# ---------------------------------------------------------------------------
# SISTEMSKI DOKAZ: PREFIKS NE MIJENJA ISHOD
# ---------------------------------------------------------------------------

BASES = [
    "Pitagorina teorema u pravouglom trouglu.",
    "Sabiranje razlomaka 3/4 + 2/5.",
    "Presjek skupova A i B.",
    "Površina kruga poluprečnika 4 cm.",
    "Rješenje jednačine 2x+5=13.",
    "Koliko je 15% od 300 KM?",
]
PREFIXES = ["Objasni: ", "Objasni mi: ", "Kako: ", "Zašto: ", "Pojasni mi: "]


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_prefix_never_changes_the_verdict(base, prefix):
    for lesson in (SIMETRIJA, RAZLOMCI):
        plain = lr.lesson_context_is_strong(base, lesson[0], lesson[1])
        prefixed = lr.lesson_context_is_strong(prefix + base, lesson[0], lesson[1])
        assert plain == prefixed, (base, prefix, lesson[0])


# ---------------------------------------------------------------------------
# N: OSTALI MODOVI NETAKNUTI
# ---------------------------------------------------------------------------

def test_N_relevance_guard_is_called_only_by_explain():
    """Quick/Practice/Kontrolni ne ZOVU ovaj modul — dokaz na nivou izvora.

    Traži se stvarni poziv, ne pomen u komentaru: `matbot/prompts.py` uredno
    citira modul u dva komentara, ali ga nikad ne izvršava."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / "matbot"
    call = re.compile(r"^[^#\n]*\blesson_relevance\.\w+\s*\(", re.MULTILINE)
    callers = sorted(
        path.name for path in root.rglob("*.py")
        if path.name != "lesson_relevance.py"
        and call.search(path.read_text(encoding="utf-8")))
    assert callers == ["explain.py"], callers


def test_no_lesson_selected_is_always_strong():
    assert lr.lesson_context_is_strong("Objasni Pitagorinu teoremu.", "", "") is True
