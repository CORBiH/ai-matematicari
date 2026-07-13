# -*- coding: utf-8 -*-
"""Višepotezni service-level regresije (AUD-01, AUD-02) sa browser state-carry.

Deterministički — model je uvijek mockiran (bez mreže/API-ja). Pokriva bugove
koje jednopotezni testovi ne mogu uhvatiti: image_test "practice" tok i
ordinalno imenovani višestruki odgovori kroz nekoliko poteza.
"""
import pytest

from matbot import content_loader as cl
from tests.helpers.conversation_client import ConversationClient

OCR3 = (
    "1. Izračunaj: 3/10 + 4/10\n"
    "2. Izračunaj: 7/12 - 5/12\n"
    "3. Marko je prešao 15 km za 3 sata. Kolikom brzinom se kretao?"
)
MULTI3 = "1. Izračunaj: 2/9 + 4/9\n2. Izračunaj: 5/8 - 1/8\n3. Izračunaj: 1/2 + 1/3"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content(grade=6)


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map(grade=6)


# ===================== AUD-01 — fresh multi-image u Vježbi =====================

def test_practice_fresh_multi_image_uses_image_tasks(master, tmap):
    """Fresh slika sa ≥2 zadatka u practice → image_test aktivan, zadatak 1 sa
    slike, prompt uzemljen na OCR, BEZ izmišljenog zadatka."""
    c = ConversationClient(master, tmap, mode="practice")
    out = c.send("", "Zadatak 1: Izračunaj 3/10 + 4/10. Pošalji odgovor.",
                 image_ocr=OCR3)
    ns = out["next_state"]
    assert ns["active_task_kind"] == "image_test"
    assert ns["image_test"]["style"] == "practice"
    assert ns["image_test"]["current"] == "1"
    assert ns["image_test"]["item_labels"] == ["1", "2", "3"]
    assert "3/10 + 4/10" in out["last_tutor_task"]              # OCR, ne izmišljotina
    prompt = out["_prompt"]
    assert "MOD: ZADACI SA SLIKE" in prompt
    assert "3/10 + 4/10" in prompt
    assert "NE rješavaj ga ti" in prompt                        # predstavi, ne riješi


def test_practice_fresh_multi_image_does_not_generate_replacement_task(master, tmap):
    """Prompt NE smije nositi practice-generator koji izmišlja nepovezani zadatak."""
    c = ConversationClient(master, tmap, mode="practice")
    out = c.send("", "Zadatak 1 sa slike.", image_ocr=OCR3)
    prompt = out["_prompt"]
    assert "MOD: VJEŽBAJ" not in prompt and "MOD: VJEŽBA — PROVJERA" not in prompt
    # aktivni zadatak je doslovno OCR stavka 1
    assert out["last_tutor_task"].strip().startswith("Izračunaj: 3/10 + 4/10")


def test_practice_fresh_multi_image_answer_checked_against_ocr_task(master, tmap):
    """Odgovor 7/10 se provjerava protiv 3/10+4/10 (OCR stavka), ne izmišljenog."""
    c = ConversationClient(master, tmap, mode="practice")
    c.send("", "Zadatak 1: 3/10 + 4/10. Pošalji odgovor.", image_ocr=OCR3)
    out = c.send("7/10", "Tačno! Hoćeš sljedeći zadatak sa slike?", phase="answer")
    verdicts = [i["verdict"] for i in (out.get("answer_check") or {}).get("items", [])]
    assert verdicts == ["correct"]
    ns = out["next_state"]
    assert ns["active_task_kind"] == "image_test"              # tok se ne gubi
    assert ns["image_test"]["solved"] == ["1"]
    assert ns["pending_action"]["type"] == "continue_image_test"
    assert ns["pending_action"]["next_item"] == 2
    # followup prompt nudi SLJEDEĆU stavku sa slike, ne izmišlja
    assert "ZADACI SA SLIKE" in out["_prompt"]
    assert "sljedeći zadatak 2" in out["_prompt"]


def test_practice_multi_image_continue_uses_next_ocr_task(master, tmap):
    """'da' poslije stavke 1 → prelazak na stavku 2 SA SLIKE (7/12 - 5/12)."""
    c = ConversationClient(master, tmap, mode="practice")
    c.send("", "Zadatak 1: 3/10 + 4/10.", image_ocr=OCR3)
    c.send("7/10", "Tačno! Nastavljamo?", phase="answer")
    out = c.send("da", "Zadatak 2: Izračunaj 7/12 - 5/12. Pošalji odgovor.")
    ns = out["next_state"]
    assert ns["active_task_kind"] == "image_test"
    assert ns["image_test"]["current"] == "2"
    assert ns["image_test"]["solved"] == ["1"]
    assert "7/12 - 5/12" in out["last_tutor_task"]
    assert "7/12 - 5/12" in out["_prompt"]


def test_practice_multi_image_second_answer_checked_and_flow_completes(master, tmap):
    """Puni tok: stavka1 tačno → stavka2 tačno → stavka3."""
    c = ConversationClient(master, tmap, mode="practice")
    c.send("", "Zadatak 1.", image_ocr=OCR3)
    c.send("7/10", "Tačno!", phase="answer")
    c.send("da", "Zadatak 2: 7/12 - 5/12.")
    out = c.send("1/6", "Tačno! Idemo na treći?", phase="answer")
    verdicts = [i["verdict"] for i in (out.get("answer_check") or {}).get("items", [])]
    assert verdicts == ["correct"]                              # 2/12 = 1/6
    ns = out["next_state"]
    assert ns["image_test"]["solved"] == ["1", "2"]
    assert ns["pending_action"]["next_item"] == 3


# ---- regresije: NE dirati postojeće image tokove ----

def test_quick_multi_image_does_not_enter_practice_image_test(master, tmap):
    """Result/Quick mod NE ulazi u image_test 'practice' — zadržava svoj tok.
    AUD-07 (B3, 2026-07-13): prazna/generička poruka + svježa multi-slika sad
    deterministički PITA koji broj (bez poziva modela), umjesto da model
    povremeno riješi sve."""
    c = ConversationClient(master, tmap, mode="quick")
    out = c.send("", "NE SMIJE BITI POZVAN", image_ocr=OCR3, expect_model=False)
    ns = out["next_state"]
    assert (ns.get("image_test") or {}).get("style") != "practice"
    assert "broj zadatka" in out["answer"].lower()      # deterministički ask


def test_single_task_image_practice_does_not_step(master, tmap):
    """Jedan zadatak sa slike (practice) → NE ulazi u image_test koračanje."""
    c = ConversationClient(master, tmap, mode="practice")
    out = c.send("", "Izračunaj 1/2 + 1/4.", image_ocr="Izračunaj: 1/2 + 1/4")
    assert out["next_state"].get("active_task_kind") != "image_test"


# ===================== AUD-02 — ordinalni višestruki odgovori =====================

def test_ordinal_multi_updates_task_items(master, tmap):
    """'prvi je 6/9, drugi 4/8, treci ne znam' → stavke 1,2 ocijenjene, 3 pending."""
    c = ConversationClient(master, tmap, mode="practice", topic="6-04-031")
    out = c.send("prvi je 6/9, drugi 4/8, treci ne znam", "1. Tačno 2. Tačno 3. čeka",
                 phase="answer", seed_task=MULTI3,
                 extra={"previous_next_state": {"task_items": {"labels": [1, 2, 3], "graded": []}}})
    check = {i["n"]: i["verdict"] for i in (out.get("answer_check") or {}).get("items", [])}
    assert check.get(1) == "correct"                            # 6/9 = 2/3
    assert check.get(2) == "correct"                            # 4/8 = 1/2
    assert check.get(3) in ("missing", "not_attempted")         # 'ne znam' → nepokušano
    assert out["next_state"]["task_items"] == {"labels": [1, 2, 3], "graded": [1, 2]}


def test_ordinal_followup_preserves_original_task_context(master, tmap):
    """Kasniji 'treci je 5/6' se veže za ORIGINALNU stavku 3 (ne traži se ponovo)."""
    c = ConversationClient(master, tmap, mode="practice", topic="6-04-031")
    c.send("prvi je 6/9, drugi 4/8, treci ne znam", "1,2 tačno; 3 čeka",
           phase="answer", seed_task=MULTI3,
           extra={"previous_next_state": {"task_items": {"labels": [1, 2, 3], "graded": []}}})
    out = c.send("treci je 5/6", "3. Tačno!", phase="answer")
    check = {i["n"]: i["verdict"] for i in (out.get("answer_check") or {}).get("items", [])}
    assert check.get(3) == "correct"                            # 1/2 + 1/3 = 5/6
    assert out["next_state"]["task_items"]["graded"] == [1, 2, 3]
