"""Testovi za matbot.topic_detector (NPP detekcija teme).

Heuristika je DATA-DRIVEN (iz naziva tema mastera) i KONZERVATIVNA (radije unknown
nego pogrešna tema). LLM klasifikator je mockiran callable — nikad stvarni API.
Tvrdnje se izvode iz samog mastera (npp_topic_id-evi), uz par stabilnih sidara
(NZD/NZS u 6. razredu).
"""
import types

import pytest

from matbot import content_loader as cl
from matbot import topic_detector as td

ALL_GRADES = (6, 7, 8, 9)


@pytest.fixture(scope="module")
def masters():
    return {g: cl.load_master_content(grade=g) for g in ALL_GRADES}


@pytest.fixture(scope="module")
def master(masters):
    return masters[6]


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map(grade=6)


def _fake_chat(reply):
    calls = {"n": 0, "messages": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False):
        calls["n"] += 1
        calls["messages"].append(messages)
        msg = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    chat.calls = calls
    return chat


# --- fold_diacritics ------------------------------------------------------------

def test_fold_diacritics():
    assert td.fold_diacritics("Šta je ČETVEROUGAO? množenje đak žir ćup") == \
        "sta je cetverougao? mnozenje dak zir cup"
    assert td.fold_diacritics(None) == ""
    assert td.fold_diacritics("  ABC  ") == "abc"


# --- is_vague_message -----------------------------------------------------------

def test_vague_messages_without_master():
    for msg in ("Ne razumijem", "Pomozi", "", None, "ok"):
        assert td.is_vague_message(msg) is True, msg


def test_math_signal_is_concrete():
    for msg in ("Izračunaj 1/2 + 1/3", "5-1", "2+2", "riješi zadatak"):
        assert td.is_vague_message(msg) is False, msg


def test_topic_phrase_is_concrete_with_master(master):
    for msg in ("daj mi zadatke sa razlomcima", "objasni mi skupove",
                "ne razumijem NZS"):
        assert td.is_vague_message(msg, master) is False, msg


def test_vague_stays_vague_with_master(master):
    for msg in ("Pomozi", "ne razumijem", "molim te"):
        assert td.is_vague_message(msg, master) is True, msg


def test_vague_check_folds_diacritics(master):
    # ista riječ sa i bez kvačica jednako čini poruku konkretnom (razlomci)
    assert td.is_vague_message("razlomci", master) is False
    assert td.is_vague_message("razlomci", master) == td.is_vague_message("razlomci", master)


# --- heuristika: konzervativna, nikad ne izmišlja -------------------------------

@pytest.mark.parametrize("grade", ALL_GRADES)
def test_heuristic_never_invents(masters, grade):
    m = masters[grade]
    samples = ["razlomci", "decimalni", "skupovi", "ugao", "kružnica",
               "stepeni", "polinom", "xyz", "blabla nepostojece", ""]
    for s in samples:
        tid = td.detect_topic_heuristic(s, m)
        assert tid == "unknown" or tid in m["topic_ids"], (grade, s)


def test_heuristic_nzd_nzs_anchors(master):
    """Stabilna sidra: skraćenice NZD/NZS (velika slova bez samoglasnika)."""
    nzd = td.detect_topic_heuristic("izračunaj NZD za 12 i 18", master)
    nzs = td.detect_topic_heuristic("ne razumijem NZS", master)
    assert nzd in master["topic_ids"] and "nzd" in master["topics_by_id"][nzd]["display_name"].lower()
    assert nzs in master["topic_ids"] and "nzs" in master["topics_by_id"][nzs]["display_name"].lower()
    assert nzd != nzs


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_heuristic_distinctive_term_maps_to_its_topic(masters, grade):
    """Distinktivna riječ iz naziva teme vraća baš tu temu (data-driven)."""
    m = masters[grade]
    index = td._build_index(m)
    # uzmi nekoliko distinktivnih riječi i provjeri da vode na svoju temu
    checked = 0
    for word, tid in index["topic_words"].items():
        if len(word) < 6:            # duže riječi su pouzdanije za test
            continue
        assert td.detect_topic_heuristic(word, m) == tid
        checked += 1
        if checked >= 5:
            break
    assert checked > 0


def test_heuristic_ambiguous_returns_unknown(master):
    # čist aritmetički izraz bez tematske riječi → unknown (pušta se LLM-u)
    assert td.detect_topic_heuristic("Izračunaj 25 · 37 - 4", master) == "unknown"


# --- LLM klasifikator -----------------------------------------------------------

def test_llm_valid_topic_accepted(master, tmap):
    valid = master["topics"][0]["topic"]      # npr. 6-01-001
    chat = _fake_chat(f'{{"detected_topic": "{valid}"}}')
    assert td.detect_topic_llm("neka poruka", master, tmap, chat, "m") == valid
    assert chat.calls["n"] == 1


def test_llm_invented_topic_coerced_to_unknown(master, tmap):
    chat = _fake_chat('{"detected_topic": "izmisljena_tema_9000"}')
    assert td.detect_topic_llm("poruka", master, tmap, chat, "m") == "unknown"


def test_llm_garbage_output_is_unknown(master, tmap):
    for garbage in ("nije json", "", '{"nesto": "drugo"}', "{slomljen json"):
        chat = _fake_chat(garbage)
        assert td.detect_topic_llm("poruka", master, tmap, chat, "m") == "unknown"


def test_llm_json_embedded_in_prose(master, tmap):
    valid = master["topics"][0]["topic"]
    chat = _fake_chat(f'Evo odgovora: {{"detected_topic": "{valid}"}} eto.')
    assert td.detect_topic_llm("poruka", master, tmap, chat, "m") == valid


def test_llm_exception_is_unknown(master, tmap):
    def boom(*a, **k):
        raise RuntimeError("api pao")
    assert td.detect_topic_llm("poruka", master, tmap, boom, "m") == "unknown"


def test_llm_prompt_lists_npp_ids(master, tmap):
    valid = master["topics"][0]["topic"]
    chat = _fake_chat(f'{{"detected_topic": "{valid}"}}')
    td.detect_topic_llm("poruka", master, tmap, chat, "m")
    user_msg = chat.calls["messages"][0][1]["content"]
    assert valid in user_msg                  # lista sadrži npp_topic_id-eve


# --- detect_topic orkestracija --------------------------------------------------

def test_detect_heuristic_wins_no_llm_call(master, tmap):
    chat = _fake_chat('{"detected_topic": "6-01-001"}')
    res = td.detect_topic("ne razumijem NZS", master, tmap, openai_chat=chat, model="m")
    assert res["method"] == "heuristic"
    assert res["detected_topic"] in master["topic_ids"]
    assert chat.calls["n"] == 0               # LLM se NE zove kad heuristika pogodi


def test_detect_llm_fallback(master, tmap):
    valid = master["topics"][0]["topic"]
    chat = _fake_chat(f'{{"detected_topic": "{valid}"}}')
    # čist aritmetički izraz: heuristika unknown, ali nije vague (math signal) → LLM
    res = td.detect_topic("Izračunaj 25 · 37", master, tmap, openai_chat=chat, model="m")
    assert res == {"detected_topic": valid, "method": "llm"}
    assert chat.calls["n"] == 1


def test_detect_vague_skips_llm(master, tmap):
    chat = _fake_chat('{"detected_topic": "6-01-001"}')
    res = td.detect_topic("Pomozi", master, tmap, openai_chat=chat, model="m")
    assert res["detected_topic"] == "unknown"
    assert chat.calls["n"] == 0


def test_detect_without_openai_chat(master, tmap):
    res = td.detect_topic("Izračunaj 25 · 37", master, tmap, openai_chat=None)
    assert res == {"detected_topic": "unknown", "method": "none"}


@pytest.mark.parametrize("grade", ALL_GRADES)
def test_detect_topic_result_always_valid(masters, grade):
    m = masters[grade]
    tmap = cl.load_thinkific_map(grade=grade)
    for msg in ("razlomci", "pomozi", "Izračunaj 2+2", "nepostojece xyz"):
        res = td.detect_topic(msg, m, tmap, openai_chat=None)
        assert res["detected_topic"] == "unknown" or res["detected_topic"] in m["topic_ids"]
