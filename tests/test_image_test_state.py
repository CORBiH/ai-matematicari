# -*- coding: utf-8 -*-
"""image_test tok — state-driven koračanje kroz zadatke sa uploadovane slike.

Pokriva prijavljene bugove: gubitak konteksta slike, pad u practice tokom
rada na slici, prelazni tekst kao last_tutor_task, eksplicitni stil koji
nadjačava UI mod, nastavak na konkretan zadatak. Sve mockirano — bez mreže.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.image_result_verifier import ocr_from_saved_context

OCR3 = (
    "1. Izračunaj 1/2 + 1/4.\n"
    "2. Izračunaj 3/5 - 1/5.\n"
    "3. Izračunaj 2 · 3/7."
)


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


def _chat(reply="Zadatak 1: 1/2 + 1/4 = 3/4."):
    calls = {"messages": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["messages"].append(messages)
        msg = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    chat.calls = calls
    return chat


def _prompt(chat):
    up = chat.calls["messages"][-1][-1]["content"]
    if isinstance(up, list):
        up = next(p["text"] for p in up if p.get("type") == "text")
    return up


def _upload(master, tmap, msg, mode="quick", reply="Zadatak 1: rezultat 3/4.", ocr=OCR3):
    chat = _chat(reply)
    out = svc.handle_chat(
        {"grade": 6, "mode": mode, "student_message": msg, "entry_source": "free_chat"},
        chat, master, tmap, model="m", timeout=1,
        image_bytes=b"x", image_data_url="data:image/png;base64,AAA=",
        ocr_image=lambda b: (ocr, 0.97), vision_model="v",
    )
    return out, chat


# --- BUG 3/4: kontekst slike se uvijek vraća; step intent gradi image_test ----------

def test_step_intent_starts_image_test_and_returns_context(master, tmap):
    out, chat = _upload(master, tmap, "uradi mi korak po korak")
    assert out["status"] == "ready"
    assert out["image_context"]                      # kontekst UVIJEK uz sliku
    ns = out["next_state"]
    assert ns["active_task_kind"] == "image_test"
    assert ns["pending_action"] == {
        "type": "continue_image_test", "source": "image_context", "next_item": 2,
    }
    assert ns["image_test"] == {
        "item_labels": ["1", "2", "3"], "solved": ["1"],
        "next_item": 2, "style": "step_by_step",
    }
    assert "last_tutor_task" not in out              # proza nije zadatak
    up = _prompt(chat)
    assert "MOD: ZADACI SA SLIKE (image_test)" in up
    assert "ISKLJUČIVO zadatak 1" in up


def test_plain_image_request_persists_context_without_stepping(master, tmap):
    out, _ = _upload(master, tmap, "uradi mi zadatak sa slike")
    assert out["status"] == "ready"
    assert out["image_context"]
    # bez eksplicitnog "sve"/"korak po korak" nema koračanja (postojeći tok)
    assert out["next_state"]["active_task_kind"] is None


def test_solve_all_starts_image_test(master, tmap):
    out, _ = _upload(master, tmap, "uradi mi sve zadatke")
    ns = out["next_state"]
    assert ns["active_task_kind"] == "image_test"
    assert ns["image_test"]["solved"] == ["1"]
    assert ns["pending_action"]["next_item"] == 2


# --- BUG 6: "da" nastavlja SLJEDEĆI zadatak sa slike, nikad nepovezanu vježbu -------

def test_da_continues_next_image_item_not_practice(master, tmap):
    first, _ = _upload(master, tmap, "uradi mi sve zadatke")
    chat = _chat("Zadatak 2: 3/5 - 1/5 = 2/5.")
    out = svc.handle_chat(
        {"grade": 6, "mode": "quick", "student_message": "da",
         "previous_next_state": first["next_state"],
         "last_image_context": first["image_context"]},
        chat, master, tmap, model="m", timeout=1,
    )
    up = _prompt(chat)
    assert "MOD: ZADACI SA SLIKE (image_test)" in up
    assert "ISKLJUČIVO zadatak 2" in up
    assert "Tekst zadatka 2: Izračunaj 3/5 - 1/5." in up
    assert "MOD: VJEŽBAJ (practice)" not in up        # nema nepovezane vježbe
    assert "Tipičan zadatak" not in up
    ns = out["next_state"]
    assert ns["active_task_kind"] == "image_test"
    assert ns["image_test"]["solved"] == ["1", "2"]
    assert ns["pending_action"]["next_item"] == 3


def test_nastavi_na_treci_solves_ocr_item_three(master, tmap):
    first, _ = _upload(master, tmap, "uradi mi sve zadatke")
    chat = _chat("Zadatak 3: 2 · 3/7 = 6/7.")
    out = svc.handle_chat(
        {"grade": 6, "mode": "quick", "student_message": "nastavi na treći zadatak",
         "previous_next_state": first["next_state"],
         "last_image_context": first["image_context"]},
        chat, master, tmap, model="m", timeout=1,
    )
    up = _prompt(chat)
    assert "ISKLJUČIVO zadatak 3" in up
    assert "Tekst zadatka 3: Izračunaj 2 · 3/7." in up
    # 3 preskočen redoslijed: 2 ostaje neriješen → sljedeći pending je 2
    assert out["next_state"]["pending_action"]["next_item"] == 2


def test_last_item_solved_exits_image_flow(master, tmap):
    state = {
        "expected_user_action": "continue_confirmation",
        "pending_action": {"type": "continue_image_test",
                           "source": "image_context", "next_item": 3},
        "active_task_kind": "image_test",
        "image_test": {"item_labels": ["1", "2", "3"], "solved": ["1", "2"],
                       "next_item": 3, "style": "step_by_step"},
    }
    ctx = "TEKST SA SLIKE (OCR):\n" + OCR3 + "\n\nODGOVOR TUTORA NA SLIKU:\n1. 3/4\n2. 2/5"
    chat = _chat("Zadatak 3: 6/7. Bravo, sve si riješio!")
    out = svc.handle_chat(
        {"grade": 6, "mode": "quick", "student_message": "da",
         "previous_next_state": state, "last_image_context": ctx},
        chat, master, tmap, model="m", timeout=1,
    )
    ns = out["next_state"]
    assert ns["pending_action"]["type"] != "continue_image_test"
    assert ns["active_task_kind"] != "image_test"


# --- BUG 2: prelazni tekst nikad ne postaje last_tutor_task -------------------------

@pytest.mark.parametrize("txt", [
    "Odlično, idemo na sljedeći zadatak!",
    "Super, nastavljamo",
    "Idemo dalje",
    "Želiš li da nastavimo?",
])
def test_transition_text_is_not_a_task(txt):
    assert svc._looks_like_practice_task_text(txt) is False
    assert svc.extract_practice_task(txt) == ""


@pytest.mark.parametrize("txt", [
    "Izračunaj 1/2 + 1/4.",
    "Napiši skup parnih brojeva manjih od deset.",
    # počinje prelaznom riječi, ali matematički signal (brojevi) ga čuva
    "Izračunaj sljedeći član niza 2, 4, 6.",
])
def test_real_tasks_still_recognized(txt):
    assert svc._looks_like_practice_task_text(txt) is True


def test_transition_answer_does_not_become_last_tutor_task(master, tmap):
    chat = _chat("Odlično, idemo na sljedeći zadatak!")
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "razlomci_pojam_vrste",
         "student_message": "3/4", "interaction_phase": "answering_practice_task",
         "last_tutor_task": "Izračunaj 1/2 + 1/4."},
        chat, master, tmap, model="m", timeout=1,
    )
    assert "last_tutor_task" not in out
    assert out["next_state"]["active_task_kind"] != "practice"


# --- BUG 5: eksplicitni stil nadjačava UI mod ----------------------------------------

def test_explicit_step_style_overrides_quick_mode(master, tmap):
    out, chat = _upload(master, tmap, "uradi mi korak po korak", mode="quick")
    assert out["mode"] == "explain"                  # Rezultat → objašnjenje
    up = _prompt(chat)
    assert "STIL: korak po korak" in up
    assert "SAMO REZULTAT" not in up


def test_explicit_result_only_keeps_quick(master, tmap):
    intent = svc.detect_explicit_intent("daj samo rezultat")
    assert intent == {"style": "result_only", "solve_all": False}
    assert svc.detect_explicit_intent("uradi mi sve zadatke")["solve_all"] is True
    assert svc.detect_explicit_intent("objasni mi postupak")["style"] == "step_by_step"
    assert svc.detect_explicit_intent("koliko je 2+2")["style"] is None


# --- bez konteksta slike: "nastavi" NE generiše nasumičnu vježbu --------------------

def test_nastavi_without_image_context_asks_clarification(master, tmap):
    chat = _chat("Ne smije biti pozvano sa vježbom.")
    out = svc.handle_chat(
        {"grade": 6, "mode": "quick", "student_message": "nastavi",
         "previous_next_state": {
             "expected_user_action": "continue_confirmation",
             "pending_action": {"type": None, "source": None, "next_item": None},
             "active_task_kind": None,
         }},
        chat, master, tmap, model="m", timeout=1,
    )
    assert chat.calls["messages"] == []              # deterministički odgovor, bez modela
    assert "šta želiš dalje" in out["answer"]


def test_nastavi_with_no_state_at_all_falls_back(master, tmap):
    chat = _chat("x")
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "student_message": "nastavi"},
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["status"] == "fallback"               # traži temu/zadatak, ne vježbu
    assert chat.calls["messages"] == []


# --- rubovi resolvera -----------------------------------------------------------------

def test_single_item_image_does_not_step(master, tmap):
    out, _ = _upload(
        master, tmap, "uradi mi korak po korak",
        ocr="Izračunaj 1/2 + 1/4.",
    )
    assert out["next_state"]["active_task_kind"] is None
    assert out["image_context"]


def test_practice_answer_phase_is_never_hijacked(master, tmap):
    chat = _chat("Tačno!")
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "razlomci_pojam_vrste",
         "student_message": "3/4", "interaction_phase": "answering_practice_task",
         "last_tutor_task": "Izračunaj 1/2 + 1/4.",
         "last_image_context": "TEKST SA SLIKE (OCR):\n" + OCR3},
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["next_state"]["active_task_kind"] != "image_test"
    assert out["answer_check"]["items"][0]["verdict"] == "correct"


def test_ocr_from_saved_context_roundtrip(master, tmap):
    first, _ = _upload(master, tmap, "uradi mi sve zadatke")
    assert ocr_from_saved_context(first["image_context"]).startswith("1. Izračunaj 1/2 + 1/4.")


def test_streaming_continuation_carries_image_test_state(client, fake_openai, monkeypatch):
    import json as _json
    import app as app_mod

    def fake_stream(model, messages, timeout=None, max_tokens=None):
        yield "Zadatak 2: 2/5."

    monkeypatch.setattr(app_mod, "_tutor_openai_chat_stream", fake_stream)
    state = {
        "expected_user_action": "continue_confirmation",
        "pending_action": {"type": "continue_image_test",
                           "source": "image_context", "next_item": 2},
        "active_task_kind": "image_test",
        "image_test": {"item_labels": ["1", "2", "3"], "solved": ["1"],
                       "next_item": 2, "style": "step_by_step"},
    }
    ctx = "TEKST SA SLIKE (OCR):\n" + OCR3
    resp = client.post("/api/ai-tutor/chat/stream", json={
        "grade": 6, "mode": "quick", "student_message": "da",
        "previous_next_state": state, "last_image_context": ctx,
    })
    raw = resp.get_data(as_text=True)
    done = [
        _json.loads(block.split("data:", 1)[1].strip())
        for block in raw.split("\n\n") if "event: done" in block
    ]
    assert done
    ns = done[0]["next_state"]
    assert ns["active_task_kind"] == "image_test"
    assert ns["image_test"]["solved"] == ["1", "2"]
    assert ns["pending_action"]["next_item"] == 3
