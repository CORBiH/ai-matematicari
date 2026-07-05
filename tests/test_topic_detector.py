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


def test_heuristic_mnozenje_razlomaka_specific(master):
    for msg in (
        "nikako ne razumijem mnozenje razlomaka",
        "Kako se množe razlomci?",
        "3/4 * 2/5",
    ):
        assert (
            td.detect_topic_heuristic(msg, master)
            == "razlomci_mnozenje_razlomkom_svojstva"
        )


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


# --- Phase 1 fixes: dijakritici + redoslijed pravila (regresije iz audita) ----------

@pytest.fixture(scope="module")
def master7():
    return cl.load_master_content(grade=7)


@pytest.fixture(scope="module")
def master8():
    return cl.load_master_content(grade=8)


@pytest.fixture(scope="module")
def tmap8():
    return cl.load_thinkific_map(grade=8)


def test_fold_diacritics():
    assert td.fold_diacritics("Šta je ČETVEROUGAO? množenje đak žir ćup") == \
        "sta je cetverougao? mnozenje dak zir cup"
    assert td.fold_diacritics(None) == ""
    assert td.fold_diacritics("  ABC  ") == "abc"


def test_mnozenje_cijelih_not_sabiranje(master7):
    """Regresija: pitanje o MNOŽENJU cijelih brojeva ne smije završiti na temi
    sabiranja/oduzimanja (generičko pravilo je ranije gutalo specifično)."""
    for msg in (
        "Kako se množe cijeli brojevi?",
        "kako se mnoze cijeli brojevi",           # bez dijakritika
        "množenje cijelih brojeva",
        "kako se dijele cijeli brojevi",
        "podijeli cijele brojeve -8 i 2",
    ):
        tid = td.detect_topic_heuristic(msg, master7)
        assert tid == "cijeli_mnozenje_dijeljenje", msg
        assert tid != "cijeli_sabiranje_oduzimanje"


def test_sabiranje_cijelih_still_detected(master7):
    for msg in ("Kako se sabiraju cijeli brojevi?", "oduzimanje cijelih brojeva"):
        assert td.detect_topic_heuristic(msg, master7) == "cijeli_sabiranje_oduzimanje", msg


def test_generic_cijeli_brojevi_first_topic(master7):
    """Generičko "cijeli brojevi" (bez operacije) → prva cijeli_ tema iz sheeta."""
    tid = td.detect_topic_heuristic("Objasni mi cijele brojeve", master7)
    assert tid.startswith("cijeli_")
    assert tid in master7["topic_ids"]
    first = next(r["topic"] for r in master7["topics"] if r["topic"].startswith("cijeli_"))
    assert tid == first


def test_cetverougao_with_and_without_diacritics(master7):
    """Regresija: mojibake u obrascu ([ÄŤc]etverougl) je blokirao prepoznavanje."""
    for msg in ("Šta je četverougao?", "sta je cetverougao", "osobine četverougla"):
        tid = td.detect_topic_heuristic(msg, master7)
        assert tid.startswith("cetverougao_"), msg
        assert tid in master7["topic_ids"]


def test_cetverougao_unknown_for_grade6(master):
    """6. razred nema cetverougao_ teme → heuristika NE izmišlja temu."""
    assert td.detect_topic_heuristic("Šta je četverougao?", master) == "unknown"


def test_trougao_nominative(master7):
    """"trougao" (nominativ, bez 'l') mora biti prepoznat, ne samo "trougla"."""
    for msg in ("Šta je trougao?", "osobine trougla"):
        tid = td.detect_topic_heuristic(msg, master7)
        assert tid.startswith("trougao_"), msg


def test_diacritic_variants_match_same_topic(master):
    """Isti upit sa i bez kvačica daje ISTU temu."""
    pairs = [
        ("Kako se računa aritmetička sredina?", "kako se racuna aritmeticka sredina?"),
        ("Šta je kružnica?", "sta je kruznica?"),
        ("Objasni mi razlomke", "objasni mi razlomke"),
    ]
    for with_d, without_d in pairs:
        assert td.detect_topic_heuristic(with_d, master) == \
            td.detect_topic_heuristic(without_d, master), with_d


def test_vague_check_folds_diacritics():
    # tematska riječ bez dijakritika i dalje čini poruku konkretnom
    assert td.is_vague_message("cetverougao") is False
    assert td.is_vague_message("četverougao") is False


# --- 8. razred ---------------------------------------------------------------------

def test_grade8_heuristics_common_terms(master8):
    cases = {
        "Objasni mi stepene, eksponent i bazu": "stepeni_pravila_i_pojasnjenja_stepeni",
        "Kako se racuna korijen iz broja?": "realni_korijeni_pravila_za_racunske_operacije",
        "Sta su realni i iracionalni brojevi?": "realni_iracionalni_brojevi_i_skup_realnih_brojeva",
        "Pitagora, hipotenuza i kateta": "pitagora_pitagorina_teorema_osnovno",
        "Kako ide kvadrat binoma?": "polinomi_kvadrat_binoma",
        "Sta je polinom i slicni monomi?": "polinomi_sta_je_to_polinom_a_sta_nije",
        "Domena algebarskog razlomka: imenilac ne smije biti nula": "alg_razlomci_definiciono_podrucje_domena_i_nula_razlomljene_racionalne_funkcije",
        "Kruznica, krug, poluprecnik i precnik": "kruznica_krug_prava_i_kruznica_centralna_udaljenost_prave_sekanta_tangenta",
        "Talesova teorema i slicnost trouglova": "tales_slicnost_talesova_teorema",
        "Razmjera i proporcija": "proporcije_razmjera_omjer_mjerilo",
        "Koordinatni sistem i grafik linearne funkcije": "koordinatni_funkcija_pravougli_dekartov_koordinatni_sistem",
        "Mnogougao i dijagonale mnogougla": "mnogougao_sta_je_mnogougao_a_sta_nije",
        "Zadatak za valjak": "tijela_valjak_osnove",
    }
    for msg, expected in cases.items():
        tid = td.detect_topic_heuristic(msg, master8)
        assert tid == expected, msg
        assert tid in master8["topic_ids"]


def test_grade8_body_terms_route_to_specific_existing_topics(master8):
    cases = {
        "Izracunaj zapreminu kocke stranice 4 cm": "tijela_kocka_zadatak_1",
        "Objasni kvadar": "tijela_kvadar_zadatak_1",
        "Prizma zadatak": "tijela_prizme_osnove",
        "Piramida zadatak": "tijela_piramide_osnove",
        "Kupa zadatak": "tijela_kupa_osnove",
        "Lopta zadatak": "tijela_lopta_zadatak_1",
    }
    for msg, expected in cases.items():
        assert td.detect_topic_heuristic(msg, master8) == expected


def test_grade8_heuristics_do_not_return_grade6_or_grade7_topics(master8):
    samples = [
        "Objasni mi stepene",
        "Pitagorina teorema",
        "Domena algebarskog razlomka",
        "Grafik linearne funkcije",
        "Geometrijska tijela",
        "Ne postoji posebna tema xyz",
    ]
    for msg in samples:
        tid = td.detect_topic_heuristic(msg, master8)
        assert tid == "unknown" or tid in master8["topic_ids"]


def test_grade8_detect_topic_accepts_llm_topic_reference(master8, tmap8):
    chat = _fake_chat('{"detected_topic": "tijela_valjak_osnove"}')
    res = td.detect_topic("Neki nejasan opis zadatka 2 + 2", master8, tmap8, chat, "m")
    assert res["detected_topic"] == "tijela_valjak_osnove"
