# -*- coding: utf-8 -*-
"""Audit — integracija determinističkog ocjenjivanja, anti-ponavljanja i
jezičke zaštite u tutor tok (handle_chat + prompt builder).

Svi OpenAI pozivi su mockirani (fake_openai / lokalni fake) — nikad stvarni API.
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot.bosnian import to_ijekavica
from matbot.tutor_prompts import build_tutor_system_prompt

CHAT_URL = "/api/ai-tutor/chat"
FR_TOPIC = "6-04-031"

TASK_D = (
    "1. Ako je obojeno 5/12 pizze, koji dio nije obojen?\n"
    "2. Dječak je potrošio 3/10 novca. Koji dio novca mu je ostao?\n"
    "3. Pretvori 2 1/4 u nepravi razlomak."
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


def _fake_chat(reply="U redu."):
    calls = {"messages": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["messages"].append(messages)
        msg = types.SimpleNamespace(content=reply)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    chat.calls = calls
    return chat


def _answer_payload(task, student, topic=FR_TOPIC):
    return {
        "grade": 6,
        "mode": "practice",
        "selected_topic": topic,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": task,
        "student_message": student,
    }


def _last_user_prompt(chat):
    return chat.calls["messages"][-1][-1]["content"]


# --- deterministička presuda ulazi u prompt i response ------------------------------

def test_correct_answer_gets_binding_verdict_in_prompt(master, tmap):
    chat = _fake_chat()
    out = svc.handle_chat(
        _answer_payload("Ako su obojane 3/8 kruga, koji dio nije obojen?", "5/8"),
        chat, master, tmap, model="m", timeout=1,
    )
    up = _last_user_prompt(chat)
    assert "PROVJERA IZ SISTEMA" in up
    assert "Stavka 1: TAČNO" in up
    assert out["answer_check"]["items"][0]["verdict"] == "correct"


def test_wrong_answer_verdict_includes_correct_result(master, tmap):
    chat = _fake_chat()
    out = svc.handle_chat(
        _answer_payload("Ako su obojane 3/8 kruga, koji dio nije obojen?", "3/8"),
        chat, master, tmap, model="m", timeout=1,
    )
    up = _last_user_prompt(chat)
    assert "Stavka 1: NETAČNO" in up
    assert "5/8" in up                          # tačan rezultat je u promptu
    assert out["answer_check"]["items"][0]["verdict"] == "incorrect"


def test_multi_item_missing_answer_requested_not_failed(master, tmap):
    chat = _fake_chat()
    out = svc.handle_chat(
        _answer_payload(TASK_D, "2) 7/10 3) 9/4"), chat, master, tmap,
        model="m", timeout=1,
    )
    up = _last_user_prompt(chat)
    assert "Stavka 1: BEZ ODGOVORA" in up
    assert "NE ocjenjuj je kao netačnu" in up
    verdicts = [i["verdict"] for i in out["answer_check"]["items"]]
    assert verdicts == ["missing", "correct", "correct"]


def test_partial_referenced_third_task_does_not_grade_first_two(master, tmap):
    task = (
        "1. Šta znači djeljivost?\n"
        "2. Navedi pravilo djeljivosti sa 2.\n"
        "3. Zašto se broj ne može dijeliti nulom?"
    )
    chat = _fake_chat()
    out = svc.handle_chat(
        _answer_payload(
            task,
            "Odgovor na treće pitanje je da se broj ne može dijeliti sa nulom.",
        ),
        chat, master, tmap, model="m", timeout=1,
    )
    up = _last_user_prompt(chat)
    verdicts = [i["verdict"] for i in out["answer_check"]["items"]]
    assert verdicts == ["not_attempted", "not_attempted", "unverified"]
    assert "Stavka 1: NIJE POKUŠANA" in up
    assert "Stavka 2: NIJE POKUŠANA" in up
    assert "NE izmišljaj njegov odgovor" in up
    assert "prvo najniži broj koji nedostaje" in up


def test_unverifiable_answer_has_no_verdict_but_keeps_rules(master, tmap):
    chat = _fake_chat()
    out = svc.handle_chat(
        _answer_payload("Objasni zašto je 1/2 veće od 1/3.", "zato što je pola veće"),
        chat, master, tmap, model="m", timeout=1,
    )
    up = _last_user_prompt(chat)
    assert "PROVJERA IZ SISTEMA" not in up      # kod nema presudu → ne izmišlja je
    assert "NIKAD ne piši" in up                # ali pravilo provjere ostaje
    assert "answer_check" not in out


def test_followup_prompt_contains_grading_safety_rules(master, tmap):
    chat = _fake_chat()
    svc.handle_chat(
        _answer_payload("Izračunaj: 1/2 + 1/3", "5/6"), chat, master, tmap,
        model="m", timeout=1,
    )
    up = _last_user_prompt(chat)
    assert "PRVA REČENICA" in up                # konačan sud bez kontradikcije
    assert "PRIHVATI EKVIVALENTNE OBLIKE" in up
    assert "Želiš li sličan zadatak za vježbu?" in up
    assert "ponudi JEDAN sličan novi" not in up


# --- explicit next_state / confirmation contract ------------------------------------

def test_next_state_marks_image_continue_confirmation(master, tmap):
    chat = _fake_chat("Hoćeš da nastavimo sa zadatkom 12?")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "explain",
        "selected_topic": FR_TOPIC,
        "student_message": "Objasni rezultate sa slike.",
        "last_image_context": "Zadatak 11 je već objašnjen. Zadatak 12 čeka.",
    }, chat, master, tmap, model="m", timeout=1)

    assert out["next_state"]["expected_user_action"] == "continue_confirmation"
    assert out["next_state"]["pending_action"] == {
        "type": "continue_image_test",
        "source": "image_context",
        "next_item": 12,
    }
    assert out["next_state"]["active_task_kind"] == "image_test"


def test_confirmation_da_continues_image_item_without_grading(master, tmap):
    chat = _fake_chat("Zadatak 12: rezultat je 4.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "student_message": "da",
        "interaction_phase": "confirmation",
        "intent": "continue_confirmation",
        "pending_action": {
            "type": "continue_image_test",
            "source": "image_context",
            "next_item": 12,
        },
        "last_tutor_task": "Zadatak 11: izračunaj 2 + 2.",
        "last_image_context": "Zadatak 11 je već objašnjen. Zadatak 12: 2 + 2.",
    }, chat, master, tmap, model="m", timeout=1)

    up = _last_user_prompt(chat)
    assert "PROVJERA IZ SISTEMA" not in up
    assert "answer_check" not in out
    assert "Nije tačno" not in out["answer"]
    assert "zadatkom 12" in svc.fold_diacritics(up)


def test_confirmation_da_generates_similar_task_without_grading(master, tmap):
    chat = _fake_chat("Novi zadatak: Izračunaj 2/3 + 1/6.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "student_message": "da",
        "interaction_phase": "confirmation",
        "intent": "continue_confirmation",
        "pending_action": {
            "type": "generate_similar_task",
            "source": "practice",
            "next_item": None,
        },
        "last_tutor_task": "Izračunaj 1/2 + 1/3.",
        "recent_tasks": ["Izračunaj 1/2 + 1/3."],
    }, chat, master, tmap, model="m", timeout=1)

    up = _last_user_prompt(chat)
    assert "PROVJERA IZ SISTEMA" not in up
    assert "answer_check" not in out
    assert "slican novi zadatak" in svc.fold_diacritics(up)
    assert "NEDAVNO DATI ZADACI" in up


def test_confirmation_moze_explains_task_without_grading(master, tmap):
    chat = _fake_chat("Objašnjenje: prvo nađemo zajednički nazivnik.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "student_message": "može",
        "interaction_phase": "confirmation",
        "intent": "continue_confirmation",
        "pending_action": {
            "type": "explain_task",
            "source": "current_task",
            "next_item": None,
        },
        "last_tutor_task": "Izračunaj 1/2 + 1/3.",
    }, chat, master, tmap, model="m", timeout=1)

    up = _last_user_prompt(chat)
    assert "PROVJERA IZ SISTEMA" not in up
    assert "answer_check" not in out
    assert "objasni prethodni zadatak" in svc.fold_diacritics(up)


def test_short_confirmation_without_pending_action_is_not_graded(master, tmap):
    chat = _fake_chat("Ovo ne smije biti pozvano.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "student_message": "da",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Izračunaj 1/2 + 1/3.",
    }, chat, master, tmap, model="m", timeout=1)

    assert chat.calls["messages"] == []
    assert "answer_check" not in out
    assert "Nije tačno" not in out["answer"]
    assert out["next_state"]["expected_user_action"] == "none"


def test_next_state_marks_similar_task_offer_after_grading(master, tmap):
    chat = _fake_chat("Tačno! Želiš li sličan zadatak za vježbu?")
    out = svc.handle_chat(
        _answer_payload("Ako su obojane 3/8 kruga, koji dio nije obojen?", "5/8"),
        chat, master, tmap, model="m", timeout=1,
    )

    assert out["next_state"]["expected_user_action"] == "continue_confirmation"
    assert out["next_state"]["pending_action"]["type"] == "generate_similar_task"
    assert out["next_state"]["active_task_kind"] == "practice"


# --- anti-ponavljanje zadataka -------------------------------------------------------

def test_recent_tasks_enter_practice_prompt(master, tmap):
    chat = _fake_chat()
    svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "student_message": "Daj mi novi zadatak.",
        "recent_tasks": ["Izračunaj 1/2 + 1/4.", "Koji dio kruga nije obojen ako je obojano 3/8?"],
    }, chat, master, tmap, model="m", timeout=1)
    up = _last_user_prompt(chat)
    assert "NEDAVNO DATI ZADACI" in up
    assert "Izračunaj 1/2 + 1/4." in up
    assert "drugi brojevi" in up


def test_recent_tasks_not_in_answer_checking_prompt(master, tmap):
    chat = _fake_chat()
    payload = _answer_payload("Izračunaj: 1/2 + 1/3", "5/6")
    payload["recent_tasks"] = ["Izračunaj 1/2 + 1/4."]
    svc.handle_chat(payload, chat, master, tmap, model="m", timeout=1)
    assert "NEDAVNO DATI ZADACI" not in _last_user_prompt(chat)


def test_recent_tasks_sanitized(master, tmap):
    chat = _fake_chat()
    svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "student_message": "Daj mi novi zadatak.",
        "recent_tasks": [f"Zadatak {i} " + "x" * 500 for i in range(20)] + [123, None],
    }, chat, master, tmap, model="m", timeout=1)
    up = _last_user_prompt(chat)
    assert "Zadatak 15" not in up               # zadržava se samo rep liste
    assert "Zadatak 19" in up
    assert "x" * 301 not in up                  # skraćeno na 300 znakova


# --- exam: svi numerisani zadaci ostaju u last_tutor_task ---------------------------

def test_exam_multi_task_extraction_keeps_all_items(master, tmap):
    reply = (
        "Evo zadataka za pripremu:\n"
        "1. Ako je obojeno 5/12 pizze, koji dio nije obojen?\n"
        "2. Dječak je potrošio 3/10 novca. Koji dio novca mu je ostao?\n"
        "3. Pretvori 2 1/4 u nepravi razlomak.\n"
        "Trik: pazi na zajednički imenilac.\n"
        "Upozorenje: ne zaboravi skratiti rezultat."
    )
    chat = _fake_chat(reply)
    out = svc.handle_chat({
        "grade": 6, "mode": "exam", "selected_oblast": "Razlomci",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    }, chat, master, tmap, model="m", timeout=1)
    task = out.get("last_tutor_task", "")
    assert task.startswith("1. ")
    assert "2. Dječak je potrošio 3/10" in task
    assert "3. Pretvori 2 1/4" in task
    assert "Trik:" not in task


def test_practice_single_task_extraction_unchanged(master, tmap):
    reply = "Evo zadatka: Izračunaj 1/2 + 1/4. Koji je rezultat?"
    chat = _fake_chat(reply)
    out = svc.handle_chat({
        "grade": 6, "mode": "practice", "selected_topic": FR_TOPIC,
        "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
    }, chat, master, tmap, model="m", timeout=1)
    assert "Izračunaj 1/2 + 1/4" in out.get("last_tutor_task", "")


# --- bosanska ijekavica --------------------------------------------------------------

def test_ijekavica_postprocessing_on_answer(master, tmap):
    chat = _fake_chat(
        "Tačno! Obojeni deo je 3/8, a rešenje je 5/8. "
        "Brojilac je 5, imenilac je 8. Prvih dvoje odgovora su dobra. "
        "Probaj ponovo."
    )
    out = svc.handle_chat(
        _answer_payload("Ako su obojane 3/8 kruga, koji dio nije obojen?", "5/8"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert "deo" not in out["answer"].split()
    assert "dio" in out["answer"]
    assert "rješenje" in out["answer"]
    assert "vježb" in out["answer"]
    assert "brojilac" not in out["answer"].lower()
    assert "imenilac" not in out["answer"].lower()
    assert "brojnik" in out["answer"].lower()
    assert "nazivnik" in out["answer"].lower()
    assert "prvih dvoje" not in out["answer"].lower()
    assert "prva dva odgovora" in out["answer"].lower()
    assert "Probaj ponovo" not in out["answer"]
    assert "Želiš li sličan zadatak za vježbu?" in out["answer"]


@pytest.mark.parametrize("src,expected", [
    ("koji deo nije obojen", "koji dio nije obojen"),
    ("Deo kruga", "Dio kruga"),
    ("rešenje je 5/8", "rješenje je 5/8"),
    ("Rešenja zadataka", "Rješenja zadataka"),
    ("vežbaj svaki dan", "vježbaj svaki dan"),
    ("celi broj", "cijeli broj"),
    ("sledeći korak", "sljedeći korak"),
    ("primer iz knjige", "primjer iz knjige"),
    ("brojilac je iznad crte", "brojnik je iznad crte"),
    ("imenilac ne smije biti nula", "nazivnik ne smije biti nula"),
    ("prvih dvoje zadataka", "prva dva zadatka"),
    ("Probaj ponovo.", "Želiš li sličan zadatak za vježbu?"),
])
def test_to_ijekavica_replacements(src, expected):
    assert to_ijekavica(src) == expected


@pytest.mark.parametrize("untouched", [
    "pogledaj video lekciju",            # "video" sadrži "deo" ali NIJE riječ deo
    "dio kruga je obojen",               # već ispravno
    "\\(\\frac{3}{5}\\) ostaje isto",    # matematički zapis netaknut
    "`imenilac` u kodu ostaje isto",
    "imenilac_var = 0",
    "https://example.com/imenilac ostaje URL",
    "modeli i dijelovi",
])
def test_to_ijekavica_does_not_overcorrect(untouched):
    assert to_ijekavica(untouched) == untouched


def test_system_prompt_has_accuracy_and_ijekavica_rules():
    sp = build_tutor_system_prompt(6)
    assert "TAČNOST PRI PROVJERI ODGOVORA" in sp
    assert "NIKAD 'deo'" in sp
    assert "brojnik i nazivnik" in sp
    assert "NIKAD 'brojilac'" in sp
    assert "NIKAD \"prvih dvoje\"" in sp
    assert "kratke rečenice" in sp
    assert "3/5 = 6/10" in sp
    assert "2 1/4 = 9/4" in sp


# --- kroz HTTP rutu (isti fake_openai kao ostali endpoint testovi) ------------------

def test_route_correct_answer_check_in_response(client, fake_openai):
    resp = client.post(CHAT_URL, json=_answer_payload(
        "Ako je pojedeno 7/12 pizze, koji dio je ostao?", "5/12"
    ))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["answer_check"]["items"][0]["verdict"] == "correct"
    up = fake_openai.calls.messages[-1][-1]["content"]
    assert "PROVJERA IZ SISTEMA" in up
    assert "Stavka 1: TAČNO" in up


def test_route_streaming_done_includes_answer_check(client, fake_openai, monkeypatch):
    import app as app_mod
    import json as _json

    def fake_stream(model, messages, timeout=None, max_tokens=None):
        yield "Tačno! "
        yield "Bravo."

    monkeypatch.setattr(app_mod, "_tutor_openai_chat_stream", fake_stream)
    resp = client.post("/api/ai-tutor/chat/stream", json=_answer_payload(
        "Ako je pojedeno 7/12 pizze, koji dio je ostao?", "5/12"
    ))
    assert resp.status_code == 200
    raw = resp.get_data(as_text=True)
    done = [
        _json.loads(block.split("data:", 1)[1].strip())
        for block in raw.split("\n\n") if "event: done" in block
    ]
    assert done and done[0]["answer_check"]["items"][0]["verdict"] == "correct"
