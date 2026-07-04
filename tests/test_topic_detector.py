"""Testovi za matbot.topic_detector (Phase 6).

Heuristike + LLM klasifikator (mockiran callable — nikad stvarni API).
"""
import types

import pytest

from matbot import content_loader as cl
from matbot import topic_detector as td


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _fake_chat(reply):
    """Mali mock openai_chat: broji pozive i vraća zadani tekst."""
    calls = {"n": 0, "messages": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False):
        calls["n"] += 1
        calls["messages"].append(messages)
        msg = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    chat.calls = calls
    return chat


# --- is_vague_message -------------------------------------------------------------

def test_vague_messages():
    for msg in ("Ne razumijem", "Pomozi", "Kako ovo", "", None, "ok"):
        assert td.is_vague_message(msg) is True, msg


def test_concrete_messages():
    for msg in (
        "Kako se računa aritmetička sredina brojeva 4, 6 i 8?",
        "Izračunaj 1/2 + 1/3",
        "Šta su djelitelji broja 18?",
        "Objasni mi decimalne brojeve",
        "Koliko je pet plus tri",
        "5-1",          # Phase 6.2: kratki izrazi sa signalom su KONKRETNI
        "2+2",
    ):
        assert td.is_vague_message(msg) is False, msg


# --- heuristike --------------------------------------------------------------------

def test_heuristic_aritmeticka_sredina(master):
    tid = td.detect_topic_heuristic("Kako se računa aritmetička sredina brojeva 4, 6 i 8?", master)
    assert tid == "aritmeticka_sredina"
    tid2 = td.detect_topic_heuristic("kako izračunati prosjek ocjena", master)
    assert tid2 == "aritmeticka_sredina"


def test_heuristic_fractions(master):
    tid = td.detect_topic_heuristic("Izračunaj 1/2 + 1/3", master)
    assert tid.startswith("razlomci_")
    assert tid in master["topic_ids"]


def test_heuristic_decimalni(master):
    tid = td.detect_topic_heuristic("Objasni mi decimalne brojeve", master)
    assert tid.startswith("decimalni_")
    assert tid in master["topic_ids"]


def test_heuristic_djeljivost(master):
    tid = td.detect_topic_heuristic("Šta su djelitelji broja 18?", master)
    assert tid.startswith("djeljivost_")
    assert tid in master["topic_ids"]
    assert td.detect_topic_heuristic("izračunaj NZD za 12 i 18", master) == "djeljivost_NZD"


def test_heuristic_skupovi_geometrija(master):
    assert td.detect_topic_heuristic("šta je unija skupova", master) == "skupovi_operacije"
    assert td.detect_topic_heuristic("komplement skupa A", master) == "skupovi_komplement"
    t_geo = td.detect_topic_heuristic("šta je kružnica", master)
    assert t_geo in master["topic_ids"]
    t_ugao = td.detect_topic_heuristic("kako se mjeri ugao uglomjerom", master)
    assert t_ugao in master["topic_ids"]


def test_heuristic_unknown_for_plain_arithmetic(master):
    assert td.detect_topic_heuristic("Izračunaj 25 · 37 - 4", master) == "unknown"


def test_heuristic_never_invents(master):
    # sve što heuristika vrati mora biti u masteru ili unknown
    samples = ["razlomci", "decimalni", "skupovi", "djeljivost", "ugao", "kružnica", "xyz"]
    for s in samples:
        tid = td.detect_topic_heuristic(s, master)
        assert tid == "unknown" or tid in master["topic_ids"]


# --- LLM klasifikator ---------------------------------------------------------------

def test_llm_valid_topic_accepted(master, tmap):
    chat = _fake_chat('{"detected_topic": "skupovi_uvod"}')
    assert td.detect_topic_llm("neka poruka", master, tmap, chat, "m") == "skupovi_uvod"
    assert chat.calls["n"] == 1


def test_llm_invented_topic_coerced_to_unknown(master, tmap):
    chat = _fake_chat('{"detected_topic": "izmisljena_tema_9000"}')
    assert td.detect_topic_llm("poruka", master, tmap, chat, "m") == "unknown"


def test_llm_garbage_output_is_unknown(master, tmap):
    for garbage in ("nije json", "", '{"nesto": "drugo"}', "{slomljen json"):
        chat = _fake_chat(garbage)
        assert td.detect_topic_llm("poruka", master, tmap, chat, "m") == "unknown"


def test_llm_json_embedded_in_prose(master, tmap):
    chat = _fake_chat('Evo odgovora: {"detected_topic": "djeljivost_uvod"} eto.')
    assert td.detect_topic_llm("poruka", master, tmap, chat, "m") == "djeljivost_uvod"


def test_llm_exception_is_unknown(master, tmap):
    def boom(*a, **k):
        raise RuntimeError("api pao")
    assert td.detect_topic_llm("poruka", master, tmap, boom, "m") == "unknown"


# --- detect_topic orkestracija -------------------------------------------------------

def test_detect_heuristic_wins_no_llm_call(master, tmap):
    chat = _fake_chat('{"detected_topic": "skupovi_uvod"}')
    res = td.detect_topic("Izračunaj 1/2 + 1/3", master, tmap, openai_chat=chat, model="m")
    assert res["method"] == "heuristic"
    assert chat.calls["n"] == 0                      # LLM se NE zove kad heuristika pogodi


def test_detect_llm_fallback(master, tmap):
    chat = _fake_chat('{"detected_topic": "n_n0_mnozenje"}')
    res = td.detect_topic("Izračunaj 25 · 37", master, tmap, openai_chat=chat, model="m")
    assert res == {"detected_topic": "n_n0_mnozenje", "method": "llm"}


def test_detect_vague_skips_llm(master, tmap):
    chat = _fake_chat('{"detected_topic": "skupovi_uvod"}')
    res = td.detect_topic("Pomozi", master, tmap, openai_chat=chat, model="m")
    assert res["detected_topic"] == "unknown"
    assert chat.calls["n"] == 0


def test_detect_without_openai_chat(master, tmap):
    res = td.detect_topic("Izračunaj 25 · 37", master, tmap, openai_chat=None)
    assert res == {"detected_topic": "unknown", "method": "none"}
