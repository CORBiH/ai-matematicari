# -*- coding: utf-8 -*-
"""Regresije za nalaze iz dječijih simulacija 2026-07-12 (N1–N6).

Sve deterministički (mock model). Pokriva: preuzimanje učenikovog zadatka (N1),
image-practice ispravke (N3), meta-identitet (N5), routing dopune (N6),
'nemoj rješenje' (N2), refusal-nije-zadatak (N4 guard).
"""
import types

import pytest

from matbot import ai_tutor_service as svc
from matbot import content_loader as cl
from matbot import prompt_builder as pb
from matbot.answer_checker import extract_task_expressions
from tests.helpers.conversation_client import ConversationClient


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


def _chat(reply="U redu."):
    calls = {"messages": []}

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["messages"].append(messages)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=reply))]
        )

    chat.calls = calls
    return chat


def _prompt(chat):
    up = chat.calls["messages"][-1][-1]["content"]
    if isinstance(up, list):
        up = next(p["text"] for p in up if p.get("type") == "text")
    return up


# ===================== N1 — učenikov vlastiti zadatak =====================

def test_extract_task_expressions_variants():
    assert extract_task_expressions("evo prvi zadatak iz knjige: 3/4 + 5/6") == ["3/4 + 5/6"]
    assert extract_task_expressions("4/9 podijeljeno sa 2/3") == ["4/9 : 2/3"]
    assert extract_task_expressions("3 puta 5 plus 2") == ["3 * 5 + 2"]
    assert len(extract_task_expressions(
        "1/2+1/4, 2/3+1/6, 3/8+1/8, 5/6-1/3, 7/10-2/5")) == 5
    # negatives — NE smiju biti prepoznati kao zadatak
    for msg in ("daj mi zadatak iz razlomaka",
                "moj drug rijesi ovakve zadatke za 10 sekundi",
                "imam ocjene 5,4,3,5,4 koji mi je prosjek",
                "ne kontam minus brojeve", "daj zadatak"):
        assert extract_task_expressions(msg) == [], msg


def test_student_task_adopted_and_graded(master, tmap):
    """N1: 'evo zadatak: 3/4 + 5/6' → TAJ zadatak aktivni; odgovor se ocjenjuje
    protiv NJEGA (ranije: bot izmišljao sličan → 'Netačno' na tačan odgovor)."""
    chat = _chat("Radimo tvoj zadatak!")
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-040",
         "student_message": "evo prvi zadatak iz knjige: 3/4 + 5/6"},
        chat, master, tmap, model="m", timeout=1)
    assert out["last_tutor_task"] == "Izračunaj: 3/4 + 5/6"
    up = _prompt(chat)
    assert "UČENIKOV VLASTITI ZADATAK" in up and "3/4 + 5/6" in up
    assert "MOD: VJEŽBAJ (practice)" not in up          # ne generiši svoj
    out2 = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-040",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": out["last_tutor_task"], "student_message": "19/12"},
        _chat("Tačno!"), master, tmap, model="m", timeout=1)
    assert [i["verdict"] for i in out2["answer_check"]["items"]] == ["correct"]


def test_student_task_list_becomes_multi_item(master, tmap):
    """N1 multi: lista od 5 izraza → numerisan zadatak + task_items; ordinalni
    odgovori se ocjenjuju po UČENIKOVIM zadacima."""
    chat = _chat("Redom!")
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-031",
         "student_message": "5 zadataka je: 1/2+1/4, 2/3+1/6, 3/8+1/8, 5/6-1/3, 7/10-2/5"},
        chat, master, tmap, model="m", timeout=1)
    assert out["next_state"]["task_items"] == {"labels": [1, 2, 3, 4, 5], "graded": []}
    out2 = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-031",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": out["last_tutor_task"],
         "previous_next_state": out["next_state"],
         "student_message": "za drugi sam dobio 5/6, treci 4/8, cetvrti 1/2, peti 3/10"},
        _chat("Bravo!"), master, tmap, model="m", timeout=1)
    by_n = {i["n"]: i["verdict"] for i in out2["answer_check"]["items"]}
    assert by_n[2] == by_n[3] == by_n[4] == by_n[5] == "correct"
    assert out2["next_state"]["task_items"]["graded"] == [2, 3, 4, 5]


def test_plain_task_request_not_adopted(master, tmap):
    """Guard: 'daj mi zadatak iz razlomaka' i dalje generiše bot-ov zadatak."""
    chat = _chat("Zadatak: Izračunaj 2/5+1/5.")
    svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": "6-04-031",
         "student_message": "daj mi zadatak iz razlomaka"},
        chat, master, tmap, model="m", timeout=1)
    assert "UČENIKOV VLASTITI" not in _prompt(chat)


def test_answer_phase_never_adopts(master, tmap):
    """Odgovor '19/12' u answer fazi se NE tumači kao novi učenikov zadatak."""
    p = {"grade": 6, "mode": "practice",
         "interaction_phase": "answering_practice_task",
         "last_tutor_task": "Izračunaj: 3/4 + 5/6",
         "student_message": "9/12 + 10/12 = 19/12"}
    svc._apply_student_task_contract(p)
    assert "_student_task" not in p


# ===================== N3 — image-practice ispravke =====================

OCR3 = ("1. Izračunaj: 2/5 + 1/10\n2. Izračunaj: 3 1/2 - 1 3/4\n"
        "3. U razredu je 28 učenika. 3/7 su dječaci. Koliko je djevojčica?")


def test_image_practice_correction_does_not_eat_next_item(master, tmap):
    """N3: ispravka poslije netačnog NE smije 'pojesti' stavku 3; persist
    last_tutor_task drži tok; eager odgovor na najavljenu stavku radi."""
    c = ConversationClient(master, tmap, mode="practice")
    c.send("evo domaca", "Zadatak 1 sa slike.", image_ocr=OCR3)
    o2 = c.send("1/2", "Tačno! Na 2?", phase="answer")
    assert "3 1/2 - 1 3/4" in o2["last_tutor_task"]      # persist: sljedeća stavka
    c.send("da", "Zadatak 2.")
    o4 = c.send("2 1/4", "Netačno, tačno je 1 3/4. Idemo na 3?", phase="answer")
    assert o4["next_state"]["image_test"]["solved"] == ["1", "2"]
    assert "28 učenika" in o4["last_tutor_task"]          # persist: stavka 3
    # ISPRAVKA — prepoznata kao stavka 2, stavka 3 OSTAJE pending
    o5 = c.send("aha da, 1 3/4", "Tako je!", phase="answer")
    assert [i["verdict"] for i in o5["answer_check"]["items"]] == ["correct"]
    assert o5["next_state"]["image_test"]["solved"] == ["1", "2"]
    assert o5["next_state"]["pending_action"]["next_item"] == 3
    # EAGER odgovor na stavku 3 bez 'da' → zatvara tok čisto
    o6 = c.send("16", "Bravo, sve si riješio!", phase="answer")
    assert o6["next_state"].get("image_test") is None      # image tok završen


# ===================== N5 — meta identitet =====================

@pytest.mark.parametrize("msg", [
    "jesi li ti pravi covjek ili robot",
    "ko te napravio",
    "jel me spijuniras? vidis li sta radim na mobitelu",
    "kako se zoveš",
])
def test_meta_identity_direct_answer(master, tmap, msg):
    chat = _chat("NE SMIJE biti pozvan.")
    out = svc.handle_chat(
        {"grade": 6, "mode": "explain", "student_message": msg},
        chat, master, tmap, model="m", timeout=1)
    assert chat.calls["messages"] == []                    # deterministički, bez modela
    assert "AI tutor za matematiku" in out["answer"]
    assert not out.get("last_tutor_task")


def test_meta_identity_skipped_mid_task():
    p = {"grade": 6, "mode": "practice",
         "interaction_phase": "answering_practice_task",
         "student_message": "jesi li ti robot"}
    svc._apply_meta_identity_contract(p)
    assert p.get("_direct_answer") is None                 # usred zadatka → model


# ===================== N6 — routing dopune =====================

def _answer_payload(msg, task="Izračunaj: 2/5 + 1/5"):
    return {"grade": 6, "mode": "practice", "selected_topic": "6-04-031",
            "interaction_phase": "answering_practice_task",
            "last_tutor_task": task, "student_message": msg}


def test_zasto_question_routes_to_help():
    p = _answer_payload("a zasto kod sabiranja minus i minus daje minus??")
    svc._apply_practice_help_contract(p)
    assert p.get("_skip_answer_check") is True
    assert p.get("interaction_phase") == "practice_help"


def test_score_question_routes_to_meta():
    p = _answer_payload("koliko bi to bilo bodova od 100, jesam prosao")
    svc._apply_practice_help_contract(p)
    assert p.get("_skip_answer_check") is True
    assert p.get("_score_question") is True
    assert "bodova" in p["student_message"] or "ocjenu" in p["student_message"]


def test_jos_jedan_isti_takav_is_new_task_request():
    assert svc.detect_new_task_request("daj jos jedan isti takav") == "same"
    assert svc.detect_new_task_request("mozes mi ponoviti isti zadatak") is None


# ===================== N2 — 'nemoj rješenje' =====================

def test_no_solution_request_flag_and_directive():
    p = _answer_payload("daj mi hint za drugi ali nemoj rjesenje",
                        task="1. Izračunaj: 1/2+1/4\n2. Izračunaj: 2/3+1/6")
    svc._apply_practice_help_contract(p)
    assert p.get("_no_solution_requested") is True
    assert p.get("_practice_help_intent") == "hint"
    block = pb.build_practice_help_instructions(p, {})
    assert block.startswith("‼️ UČENIK JE IZRIČITO TRAŽIO")


# ===================== N4 guard — refusal nije zadatak =====================

def test_refusal_redirect_line_is_not_a_task():
    assert svc._looks_like_practice_task_text(
        "Postavi mi pitanje ili zadatak iz matematike.") is False
    assert svc.extract_practice_task(
        "Postavi mi pitanje ili zadatak iz matematike.") == ""
