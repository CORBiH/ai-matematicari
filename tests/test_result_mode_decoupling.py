# -*- coding: utf-8 -*-
"""Result/Quick mod je KONTEKST-SLOBODAN: ne koristi razred/temu/lekciju.

Pokriva zahtjeve dekuplovanja:
- ignoriše opened_lesson_topic/selected_topic (tema null),
- ne preporučuje video,
- ne poziva refusal politiku za "nije za 6. razred" (rješava valjan zadatak),
- više zadataka sa slike bez broja → pita koji broj,
- "2. zadatka" → rješava samo tu stavku,
- ime fajla ne utiče na razred/temu,
- Practice/Explain modovi ostaju nepromijenjeni (tema se koristi).

Svi OpenAI pozivi su mockirani.
"""
import io
import json
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl

CHAT_URL = "/api/ai-tutor/chat"


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


def _chat(reply):
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        chat.messages = messages
        chat.calls = getattr(chat, "calls", 0) + 1
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))]
        )
    chat.calls = 0
    return chat


def _ocr(text):
    return lambda b: (text, 0.96)


# --- 2/6/7: ignoriše opened_lesson_topic, bez videa, bez refusal-a -----------------

def test_result_mode_ignores_opened_lesson_topic(master, tmap):
    chat = _chat("6 · 7 = 42.")
    out = svc.handle_chat({
        "grade": 6, "mode": "quick",
        "selected_topic": "skupovi_uvod", "entry_source": "thinkific_lesson",
        "student_message": "Koliko je 6·7?",
    }, chat, master, tmap, model="m", timeout=1)
    assert out["final_topic"] is None
    assert out["effective_topic"] is None
    assert out["opened_lesson_topic"] is None
    assert out["recommend_video"] is False
    assert out["context_policy"] == "disabled_for_result_mode"
    assert out["debug"]["ignored_opened_lesson_topic"] == "skupovi_uvod"
    assert out["debug"]["topic_source"] == "disabled"
    # system prompt: result-mod identitet, BEZ razredne didaktike/modularnih pravila
    sp = chat.messages[0]["content"]
    assert "DIDAKTIKA — 6. RAZRED" not in sp
    assert "MODULARNA PRAVILA" not in sp
    assert "Samo rezultat" in sp


def test_result_mode_does_not_refuse_other_grade_math(master, tmap):
    # OCR sa "Testovi matematika 8" + valjan zadatak → NE odbija zbog razreda
    chat = _chat("(x+3)^2 = x^2 + 6x + 9.")
    out = svc.handle_chat({
        "grade": 6, "mode": "quick", "selected_topic": "skupovi_uvod",
        "student_message": "Daj mi rezultat sa slike.",
    }, chat, master, tmap, model="m", timeout=1,
        image_bytes=b"x", ocr_image=_ocr("Testovi matematika 8\nIzračunaj (x+3)^2."))
    assert out["status"] == "ready"
    assert chat.calls == 1                      # model je pozvan (nije fallback/odbijanje)
    assert out["final_topic"] is None
    assert out["debug"]["refusal_reason"] is None
    sp = chat.messages[0]["content"]
    assert "ne odbijaj valjan matematički zadatak" in sp.lower()


# --- 4: više zadataka bez broja → pita koji ----------------------------------------

def test_result_mode_multiple_tasks_asks_which_number(master, tmap):
    chat = _chat("NE SMIJE BITI POZVAN")
    out = svc.handle_chat({
        "grade": 6, "mode": "quick",
        "student_message": "Daj mi samo rezultat zadatka sa slike.",
    }, chat, master, tmap, model="m", timeout=1,
        image_bytes=b"x", ocr_image=_ocr("1. 5 - 8\n2. -2/7 - 4/7\n3. 3/4 + 1/4"))
    assert chat.calls == 0                       # deterministički odgovor, bez modela
    assert "više zadataka" in out["answer"].lower()
    assert "broj zadatka" in out["answer"].lower()   # traži broj stavke
    assert out["debug"]["detected_task_count"] == 3
    assert out["final_topic"] is None


def test_result_mode_plural_results_solves_all_not_ask(master, tmap):
    # "rezultate" (množina) → riješi sve (poziva model), NE pita koji broj
    chat = _chat("1. rezultat 1\n2. rezultat -6/7")
    out = svc.handle_chat({
        "grade": 6, "mode": "quick",
        "student_message": "Daj mi rezultate sa slike.",
    }, chat, master, tmap, model="m", timeout=1,
        image_bytes=b"x", ocr_image=_ocr("1. 3/4 + 1/4\n2. -2/7 - 4/7"))
    assert chat.calls == 1
    assert "koji broj" not in out["answer"].lower()


# --- 5: "2. zadatka" → rješava samo stavku 2 ---------------------------------------

def test_result_mode_specific_task_number_solves_that_item(master, tmap):
    chat = _chat("Rezultat 2. zadatka je -6/7.")
    out = svc.handle_chat({
        "grade": 6, "mode": "quick",
        "student_message": "Daj mi samo rezultat 2. zadatka sa slike.",
    }, chat, master, tmap, model="m", timeout=1,
        image_bytes=b"x", ocr_image=_ocr("1. 3/4 + 1/4\n2. -2/7 - 4/7\n3. 5 - 8"))
    assert chat.calls == 1                       # rješava (ne pita)
    assert "koji broj" not in out["answer"].lower()
    assert out["answer"] == "Rezultat 2. zadatka je -6/7."
    # image_test tok rješava upravo stavku 2
    assert out["next_state"]["active_task_kind"] == "image_test"
    assert "2" in out["next_state"]["image_test"]["solved"]


# --- 9: ime fajla ne utiče na razred/temu (kroz HTTP multipart) ---------------------

def _multipart(payload, filename, content=b"fake-image-bytes"):
    return {"payload": json.dumps(payload), "image": (io.BytesIO(content), filename)}


def test_result_mode_filename_does_not_set_topic_or_grade(client, fake_openai):
    resp = client.post(
        CHAT_URL,
        data=_multipart({"mode": "quick"}, filename="8 raz.webp"),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] is None          # ime "8 raz" ne izmišlja temu ni razred
    assert body["context_policy"] == "disabled_for_result_mode"


# --- 8: Practice/Explain modovi ostaju nepromijenjeni (tema se koristi) -------------

def test_practice_mode_still_uses_topic(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "grade": 6, "mode": "practice", "selected_topic": "skupovi_uvod",
        "student_message": "Daj mi jedan zadatak za vježbu.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "skupovi_uvod"
    assert body.get("context_policy") is None    # nije result mod


def test_explain_mode_still_uses_topic(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "grade": 6, "mode": "explain", "selected_topic": "skupovi_uvod",
        "student_message": "Objasni mi ovu temu.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["final_topic"] == "skupovi_uvod"
    assert body["effective_topic"] == "skupovi_uvod"
    assert body.get("context_policy") is None
