"""DVOJNI NAZIVI: „nazivnik/imenilac“ i „faktor (činilac)“ se NE sažimaju.

ŽIVI NALAZ (Edin-feedback): kanonski naslovi lekcija par pišu KOSOM CRTOM —

    „Pojam razlomka, brojnik/brojilac i nazivnik/imenilac“
    „Svođenje razlomaka na zajednički nazivnik/imenilac“

a normalizacija ih je pretvarala u „nazivnik/nazivnik“. Zaštita para je
postojala, ali je pokrivala SAMO oblik sa zagradom („nazivnik (imenilac)“),
pa je razdvojnik bio jedina razlika između ispravnog i pokvarenog izlaza.

RAZLIKA KOJU OVAJ FAJL ČUVA:
  • PRIHVATANJE ULAZA — učenik smije reći „imenilac“ ili „činilac“ i mora biti
    shvaćen; goli sinonim se i dalje normalizuje u kanonski termin;
  • KANONSKI IZLAZ — kad je dvojni naziv NAMJERNO napisan uz vlastiti kanonski
    termin, to je uvođenje pojma i ostaje netaknuto.
"""
import json
from pathlib import Path

import pytest

from matbot.terminology import contains_forbidden_term, normalize_terminology

ROOT = Path(__file__).resolve().parent.parent

SANCTIONED_PAIRS = [
    "nazivnik/imenilac",
    "nazivnik (imenilac)",
    "Nazivnik/imenilac",
    "nazivnik / imenilac",
    "faktor (činilac)",
    "faktor/činilac",
]
BARE_SYNONYMS = [
    ("imenilac razlomka je $5$", "nazivnik razlomka je $5$"),
    ("Sabiranje razlomaka jednakih imenilaca",
     "Sabiranje razlomaka jednakih nazivnika"),
    ("činilac u proizvodu", "faktor u proizvodu"),
    ("rastavi na činioce", "rastavi na faktore"),
]


# ---------------------------------------------------------------------------
# A) DVOJNI NAZIV PREŽIVLJAVA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phrase", SANCTIONED_PAIRS)
def test_sanctioned_pair_is_never_collapsed(phrase):
    assert normalize_terminology(phrase) == phrase


@pytest.mark.parametrize("phrase", SANCTIONED_PAIRS)
def test_sanctioned_pair_never_duplicates_the_canonical_term(phrase):
    result = normalize_terminology(phrase).lower()
    assert "nazivnik/nazivnik" not in result
    assert "nazivnik (nazivnik)" not in result
    assert "faktor (faktor)" not in result
    assert "faktor/faktor" not in result


def test_canonical_lesson_titles_with_the_pair_survive_normalization():
    """Naslovi koji NOSE DVOJNI NAZIV moraju proći netaknuti.

    Naslov s golim padežnim oblikom („…jednakih imenilaca“) NIJE ovdje: njega
    projekat namjerno normalizuje u vlastitoj prozi, a učenik u biraču vidi
    kanonski naslov iz `topics.json`, koji se ne normalizuje."""
    payload = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    titles = [row["title"] for grade in payload["grades"].values()
              for row in grade["lessons"]
              if "nazivnik/imenilac" in row["title"].lower()
              or "nazivnik (imenilac)" in row["title"].lower()]
    assert len(titles) >= 2, titles
    for title in titles:
        assert normalize_terminology(title) == title, title


# ---------------------------------------------------------------------------
# B) GOLI SINONIM SE I DALJE NORMALIZUJE (prihvatanje ulaza ostaje)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,expected", BARE_SYNONYMS)
def test_bare_synonym_still_becomes_the_canonical_term(source, expected):
    assert normalize_terminology(source) == expected


def test_neither_synonym_is_treated_as_a_forbidden_term():
    """Kurikularni naslovi ih legitimno nose — repo-sken ih ne smije prijaviti."""
    for text in ("imenilac", "činilac", "nazivnik/imenilac", "faktor (činilac)"):
        assert not contains_forbidden_term(text), text


# ---------------------------------------------------------------------------
# C) IDEMPOTENCIJA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", SANCTIONED_PAIRS + [s for s, _ in BARE_SYNONYMS] + [
    "Pojam razlomka, brojnik/brojilac i nazivnik/imenilac",
    "Djelilac/djelitelj i sadržilac/višekratnik prirodnog broja",
])
def test_normalization_is_idempotent(text):
    once = normalize_terminology(text)
    assert normalize_terminology(once) == once, once


# ---------------------------------------------------------------------------
# D) NASLOV DJELJIVOSTI — par je DJELILAC/DJELITELJ
# ---------------------------------------------------------------------------

def test_divisibility_title_uses_the_djelilac_djelitelj_pair():
    payload = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
    rows = [row for row in payload["grades"]["6"]["lessons"]
            if row["id"] == "6-03-001"]
    assert len(rows) == 1
    title = rows[0]["title"]
    assert title.startswith("Djelilac/djelitelj"), title
    assert "faktor" not in title.lower()
    assert normalize_terminology(title) == title
