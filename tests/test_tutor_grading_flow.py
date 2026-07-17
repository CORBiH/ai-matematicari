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
EXPR_TOPIC = "6-02-019"

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


def _fake_chat_sequence(*replies):
    calls = {"messages": []}
    remaining = list(replies)

    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        calls["messages"].append(messages)
        reply = remaining.pop(0) if remaining else replies[-1]
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


def test_wrong_form_answer_final_feedback_is_partial(master, tmap):
    chat = _fake_chat(
        "Tačno! 9/12 je ekvivalentno 18/24, ali može se još skratiti do 3/4."
    )
    out = svc.handle_chat(
        _answer_payload("Skrati razlomak 18/24.", "9/12"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "correct_value_wrong_form"
    assert out["answer"].startswith("Djelimično tačno.")
    assert "3/4" in out["answer"]
    up = _last_user_prompt(chat)
    assert "DJELIMIČNO TAČNO" in up
    assert "Djelimično tačno." in up


def test_reduced_simplify_answer_stays_fully_correct(master, tmap):
    chat = _fake_chat("Tačno. 18/24 se skraćuje na 3/4.")
    out = svc.handle_chat(
        _answer_payload("Skrati razlomak 18/24.", "3/4"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["answer_check"]["items"][0]["verdict"] == "correct"
    assert out["answer"].lower().startswith("tačno")


def test_referenced_third_task_request_is_help_not_grading(master, tmap):
    task = (
        "1. Izračunaj 1/2 + 1/3.\n"
        "2. Skrati razlomak 6/10.\n"
        "3. Izračunaj 3/4 · 2."
    )
    chat = _fake_chat(
        "Treći zadatak: 3/4 · 2 = 6/4 = 3/2. Želiš li sličan zadatak?"
    )
    out = svc.handle_chat(
        _answer_payload(task, "a treci zadatak?"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert "answer_check" not in out
    assert out["mode"] == "explain"
    assert out["practice_task_state"] == "solution_revealed"
    assert out["next_state"]["expected_user_action"] != "answer_task"
    up = _last_user_prompt(chat)
    assert "POMOĆ ZA AKTIVNI ZADATAK" in up
    assert "PROVJERA ODGOVORA" not in up
    assert "Izračunaj 3/4" in up


def test_hint_request_is_not_graded_and_does_not_reveal_solution(master, tmap):
    chat = _fake_chat(
        "Pogledaj prvo šta se dešava s brojiocem kada množiš razlomak cijelim brojem."
    )
    out = svc.handle_chat(
        _answer_payload("Izračunaj 3/4 · 2.", "daj mi hint"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert "answer_check" not in out
    assert out.get("practice_task_state") != "solution_revealed"
    assert "3/2" not in out["answer"]
    up = _last_user_prompt(chat)
    assert "traži HINT" in up
    assert "NE otkrivaj konačan rezultat" in up


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
    # BUG 2 (2026-07-10): poslije tačnog ODMAH novi zadatak, bez pitanja
    assert "ODMAH daj JEDAN novi zadatak" in up
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


def test_short_confirmation_without_pending_action_gives_new_task(master, tmap):
    """BUG 1/6 (2026-07-10): "da" u vježbi bez upamćene ponude = "daj novi
    zadatak" — nikad meta-pitanje i nikad ocjenjivanje potvrde."""
    chat = _fake_chat("Zadatak: Izračunaj 2/5 + 1/5.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "student_message": "da",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Izračunaj 1/2 + 1/3.",
    }, chat, master, tmap, model="m", timeout=1)

    assert "answer_check" not in out                       # potvrda se NE ocjenjuje
    assert "Nije tačno" not in out["answer"]
    assert "Samo mi reci šta želiš dalje" not in out["answer"]   # nema meta-pitanja
    up = _last_user_prompt(chat)
    assert "sličan novi zadatak" in up                     # rewrite → novi zadatak
    assert "Ne ocjenjuj ovu potvrdu kao odgovor." in up


def test_next_state_marks_similar_task_offer_after_grading(master, tmap):
    chat = _fake_chat("Tačno! Želiš li sličan zadatak za vježbu?")
    out = svc.handle_chat(
        _answer_payload("Ako su obojane 3/8 kruga, koji dio nije obojen?", "5/8"),
        chat, master, tmap, model="m", timeout=1,
    )

    assert out["next_state"]["expected_user_action"] == "continue_confirmation"
    assert out["next_state"]["pending_action"]["type"] == "generate_similar_task"
    assert out["next_state"]["active_task_kind"] == "practice"
    assert out["task_status"] == "completed"


def test_incorrect_answer_keeps_active_task_id_and_attempt(master, tmap):
    chat = _fake_chat("Netačno. 1/2 + 1/3 = 5/6.")
    payload = _answer_payload("Izračunaj: 1/2 + 1/3", "2/6")
    payload["previous_next_state"] = {
        "task_id": "task-old",
        "task_status": "active",
        "attempt_count": 1,
    }
    out = svc.handle_chat(payload, chat, master, tmap, model="m", timeout=1)

    assert out["answer_check"]["items"][0]["verdict"] == "incorrect"
    assert out["last_tutor_task"] == "Izračunaj: 1/2 + 1/3"
    assert out["next_state"]["task_id"] == "task-old"
    assert out["next_state"]["task_status"] == "active"
    assert out["next_state"]["attempt_count"] == 2
    assert out["next_state"]["correct_streak"] == 0


def test_wrong_required_form_keeps_active_task_even_if_model_offers_new_task(master, tmap):
    chat = _fake_chat(
        "Tačno, ali treba mješoviti oblik.\n"
        "Zadatak: Pretvori 7/3 u mješoviti broj."
    )
    task = "Pretvori 3/2 u mjesoviti broj."
    payload = _answer_payload(task, "3/2")
    payload["previous_next_state"] = {
        "task_id": "task-old",
        "task_status": "active",
        "attempt_count": 0,
    }

    out = svc.handle_chat(payload, chat, master, tmap, model="m", timeout=1)

    assert out["answer_check"]["items"][0]["verdict"] == "correct_value_wrong_form"
    assert out["answer_verdict"] == "partial"
    assert out["answer_verdict_detail"] == "correct_value_wrong_form"
    assert "Pretvori 7/3" not in out["answer"]
    assert out["last_tutor_task"] == task
    assert out["next_state"]["task_id"] == "task-old"
    assert out["next_state"]["task_status"] == "active"
    assert out["next_state"]["attempt_count"] == 1


def test_uncheckable_answer_keeps_task_without_counting_attempt(master, tmap):
    chat = _fake_chat("Možeš li napisati broj ili mjeru koju dobiješ?")
    payload = _answer_payload("Pretvori 0,15 m u cm.", "??!!")
    payload["previous_next_state"] = {
        "task_id": "task-old",
        "task_status": "active",
        "attempt_count": 2,
        "hint_count": 1,
    }

    out = svc.handle_chat(payload, chat, master, tmap, model="m", timeout=1)

    assert "answer_check" not in out
    assert out["answer_verdict"] is None
    assert out["answer_verdict_detail"] == "ambiguous"
    assert out["last_tutor_task"] == "Pretvori 0,15 m u cm."
    assert out["next_state"]["task_id"] == "task-old"
    assert out["next_state"]["task_status"] == "active"
    assert out["next_state"]["attempt_count"] == 2
    assert out["next_state"]["hint_count"] == 1


def test_marked_task_after_correct_is_not_auto_started(master, tmap):
    chat = _fake_chat("Tačno.\nZadatak: Izračunaj 1/8 + 2/8.")
    payload = _answer_payload("Izračunaj: 2/7 + 3/7", "5/7")
    payload["previous_next_state"] = {"task_id": "task-old", "task_status": "active"}

    out = svc.handle_chat(payload, chat, master, tmap, model="m", timeout=1)

    assert out["answer_verdict"] == "correct"
    assert out["next_state"]["task_status"] == "completed"
    assert out["next_state"]["completed_task_id"] == "task-old"
    assert out["last_tutor_task"] == ""
    assert "1/8 + 2/8" not in out["answer"]


def test_completed_task_rejects_late_hint(master, tmap):
    chat = _fake_chat("Tačno.")
    done = svc.handle_chat(
        _answer_payload("Izračunaj: 1/2 + 1/3", "5/6"),
        chat, master, tmap, model="m", timeout=1,
    )
    assert done["next_state"]["task_status"] == "completed"

    hint_chat = _fake_chat("SHOULD-NOT-BE-CALLED")
    hint = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "intent": "hint_request",
        "student_message": "daj hint",
        "last_tutor_task": "Izračunaj: 1/2 + 1/3",
        "previous_next_state": done["next_state"],
    }, hint_chat, master, tmap, model="m", timeout=1)

    assert not hint_chat.calls["messages"]
    assert "već završili" in hint["answer"]
    assert "answer_check" not in hint
    assert hint["next_state"]["task_status"] == "completed"
    assert hint["next_state"]["completed_task_id"] == done["next_state"]["completed_task_id"]
    assert hint["next_state"]["correct_streak"] == done["next_state"]["correct_streak"]


def test_completed_task_preserves_attempt_and_hint_history(master, tmap):
    task = "Izračunaj: 1/2 + 1/3"
    wrong_chat = _fake_chat("Netačno. 1/2 + 1/3 = 5/6.")
    wrong = svc.handle_chat({
        **_answer_payload(task, "2/6"),
        "previous_next_state": {"task_id": "task-life", "task_status": "active"},
    }, wrong_chat, master, tmap, model="m", timeout=1)

    assert wrong["next_state"]["task_id"] == "task-life"
    assert wrong["next_state"]["task_status"] == "active"
    assert wrong["attempt_number"] == 1
    assert wrong["total_attempt_count"] == 1
    assert wrong["wrong_attempt_count"] == 1
    assert wrong["hint_count"] == 0

    hint_chat = _fake_chat("Pogledaj zajednički nazivnik 6.")
    hint = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "intent": "hint_request",
        "student_message": "daj hint",
        "last_tutor_task": task,
        "previous_next_state": wrong["next_state"],
    }, hint_chat, master, tmap, model="m", timeout=1)

    assert hint["next_state"]["task_id"] == "task-life"
    assert hint["next_state"]["task_status"] == "active"
    assert hint["attempt_number"] == 1
    assert hint["total_attempt_count"] == 1
    assert hint["wrong_attempt_count"] == 1
    assert hint["hint_count"] == 1

    correct_chat = _fake_chat("Tačno.")
    correct = svc.handle_chat({
        **_answer_payload(task, "5/6"),
        "previous_next_state": hint["next_state"],
    }, correct_chat, master, tmap, model="m", timeout=1)

    assert correct["task_status"] == "completed"
    assert correct["next_state"]["completed_task_id"] == "task-life"
    assert correct["attempt_number"] == 2
    assert correct["total_attempt_count"] == 2
    assert correct["wrong_attempt_count"] == 1
    assert correct["hint_count"] == 1

    late_hint = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "intent": "hint_request",
        "student_message": "daj još hint",
        "last_tutor_task": task,
        "previous_next_state": correct["next_state"],
    }, _fake_chat("SHOULD-NOT-BE-CALLED"), master, tmap, model="m", timeout=1)

    assert late_hint["task_status"] == "completed"
    assert late_hint["attempt_number"] == 2
    assert late_hint["total_attempt_count"] == 2
    assert late_hint["wrong_attempt_count"] == 1
    assert late_hint["hint_count"] == 1


def test_new_task_after_completed_state_starts_with_clean_counters(master, tmap):
    chat = _fake_chat("Zadatak: Izracunaj 2/5 + 1/5.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "student_message": "Daj novi zadatak.",
        "previous_next_state": {
            "task_id": None,
            "task_status": "completed",
            "completed_task_id": "task-done",
            "attempt_count": 2,
            "total_attempt_count": 2,
            "wrong_attempt_count": 1,
            "hint_count": 1,
            "correct_streak": 1,
        },
    }, chat, master, tmap, model="m", timeout=1)

    assert out["task_status"] == "active"
    assert out["task_id"]
    assert out["task_id"] != "task-done"
    assert out["attempt_number"] == 0
    assert out["total_attempt_count"] == 0
    assert out["wrong_attempt_count"] == 0
    assert out["hint_count"] == 0
    assert out["next_state"]["task_status"] == "active"


def test_gpt_textual_verdict_updates_answer_verdict_and_streak(master, tmap):
    chat = _fake_chat("Tačno. Djelilac je broj kojim dijelimo bez ostatka.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Šta je djelilac?",
        "student_message": "Broj kojim dijelimo.",
        "previous_next_state": {"correct_streak": 2, "task_id": "task-text"},
    }, chat, master, tmap, model="m", timeout=1)

    assert out["answer_verdict"] == "correct"
    assert out["answer_verdict_detail"] == "gpt_correct"
    assert out["answer_check"]["gpt_check_used"] is True
    assert out["next_state"]["correct_streak"] == 3


def test_gpt_textual_partial_keeps_task_active_and_counts_attempt(master, tmap):
    chat = _fake_chat("Djelimično tačno. Dobio si dobar prvi korak, ali postupak nije završen.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Riješi jednačinu 2x + 3 = 11.",
        "student_message": "Oduzeo sam 3 i dobio 2x = 8, ali ne znam dalje.",
        "previous_next_state": {
            "task_id": "task-text",
            "task_status": "active",
            "attempt_count": 1,
            "wrong_attempt_count": 1,
            "hint_count": 1,
        },
    }, chat, master, tmap, model="m", timeout=1)

    assert out["answer_verdict"] == "partial"
    assert out["answer_verdict_detail"] == "gpt_partial"
    assert out["answer_check"]["gpt_check_used"] is True
    assert out["task_status"] == "active"
    assert out["task_id"] == "task-text"
    assert out["attempt_number"] == 2
    assert out["total_attempt_count"] == 2
    assert out["wrong_attempt_count"] == 1
    assert out["hint_count"] == 1


def test_gpt_textual_ambiguous_keeps_task_without_counting_attempt(master, tmap):
    chat = _fake_chat("Nejasno. Ne mogu pouzdano procijeniti odgovor iz ove poruke.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Objasni šta je prost broj.",
        "student_message": "onako nešto",
        "previous_next_state": {
            "task_id": "task-text",
            "task_status": "active",
            "attempt_count": 2,
            "wrong_attempt_count": 1,
            "hint_count": 1,
        },
    }, chat, master, tmap, model="m", timeout=1)

    assert out["answer_verdict"] is None
    assert out["answer_verdict_detail"] == "gpt_ambiguous"
    assert out["answer_check"]["gpt_check_used"] is True
    assert out["task_status"] == "active"
    assert out["task_id"] == "task-text"
    assert out["attempt_number"] == 2
    assert out["total_attempt_count"] == 2
    assert out["wrong_attempt_count"] == 1
    assert out["hint_count"] == 1


def test_gpt_textual_partial_variants_are_conservative(master, tmap):
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Riješi jednačinu 2x + 3 = 11.",
        "student_message": "Oduzeo sam 3 i dobio 2x = 8, ali ne znam dalje.",
        "previous_next_state": {"task_id": "task-text", "task_status": "active"},
    }, _fake_chat("Dobro si počeo — tačno je da je 2x = 8. Sada dovrši dijeljenjem sa 2."),
        master, tmap, model="m", timeout=1)

    assert out["answer_verdict"] == "partial"
    assert out["answer_verdict_detail"] == "gpt_partial"
    assert out["task_status"] == "active"

    flawed = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Riješi jednačinu 2x + 3 = 11.",
        "student_message": "Pomnožio sam obje strane sa 2 i dobio 2x = 8, zatim x = 4.",
        "previous_next_state": {"task_id": "task-text", "task_status": "active"},
    }, _fake_chat("Tačno. Konačan broj je x = 4. Međutim, prvi korak u postupku nije dobar."),
        master, tmap, model="m", timeout=1)

    assert flawed["answer_verdict"] == "partial"
    assert flawed["answer_verdict_detail"] == "gpt_partial"
    assert flawed["task_status"] == "active"


def test_structured_gpt_grade_overrides_conflicting_tutor_prose(master, tmap):
    chat = _fake_chat_sequence(
        '{"verdict":"partial","confidence":0.82,'
        '"public_feedback":"Konačan broj je dobar, ali prvi korak nije matematički ispravan."}',
        "Tačno. Konačan broj je x = 4. Međutim, prvi korak u postupku nije dobar.\n"
        "Zadatak: Riješi 3x = 12.",
    )
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": EXPR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Riješi jednačinu 2x + 3 = 11.",
        "student_message": "Pomnožio sam obje strane sa 2 i dobio 2x = 8, zatim x = 4.",
        "previous_next_state": {"task_id": "task-text", "task_status": "active"},
    }, chat, master, tmap, model="m", timeout=1)

    assert len(chat.calls["messages"]) == 2
    assert "Return JSON" in chat.calls["messages"][0][-1]["content"]
    assert "STRUKTURIRANA GPT PROVJERA" in chat.calls["messages"][1][-1]["content"]
    assert out["answer"].startswith("Djelimično tačno.")
    assert out["answer_verdict"] == "partial"
    assert out["answer_verdict_detail"] == "gpt_partial"
    assert out["gpt_check_used"] is True
    assert out["gpt_check_confidence"] == 0.82
    assert out["answer_check"]["gpt_check_used"] is True
    assert out["answer_check"]["gpt_check_confidence"] == 0.82
    assert out["answer_check"]["gpt_answer_verdict"] == "partial"
    assert out["task_status"] == "active"
    assert out["task_id"] == "task-text"
    assert out["attempt_number"] == 1
    assert out["last_tutor_task"] == "Riješi jednačinu 2x + 3 = 11."


def test_structured_gpt_ambiguous_does_not_count_attempt_or_log_reasoning(master, tmap):
    chat = _fake_chat_sequence(
        '{"verdict":"incorrect","confidence":0.61,'
        '"public_feedback":"Ne mogu pouzdano zaključiti šta je odgovor.",'
        '"reasoning":"ovo se ne smije logovati"}',
        "Tačno. Izgleda mi da je odgovor dobar.",
    )
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": FR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Objasni zašto je 1/2 veće od 1/3.",
        "student_message": "onako nekako",
        "previous_next_state": {
            "task_id": "task-text",
            "task_status": "active",
            "attempt_count": 2,
            "wrong_attempt_count": 1,
            "hint_count": 1,
        },
    }, chat, master, tmap, model="m", timeout=1)

    assert out["answer"].startswith("Nejasno.")
    assert out["answer_verdict"] is None
    assert out["answer_verdict_detail"] == "gpt_ambiguous"
    assert out["gpt_check_used"] is True
    assert out["gpt_check_confidence"] == 0.61
    assert out["task_status"] == "active"
    assert out["attempt_number"] == 2
    assert out["wrong_attempt_count"] == 1
    assert out["hint_count"] == 1
    assert "reasoning" not in str(out["answer_check"]).lower()
    assert "ne smije logovati" not in str(out["answer_check"]).lower()


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


def test_recent_tasks_in_answer_checking_prompt_for_next_task(master, tmap):
    """BUG 2 (2026-07-10): poslije tačnog odgovora tutor ODMAH daje novi zadatak
    u istoj poruci — pa i grading potez treba anti-ponavljanje spisak."""
    chat = _fake_chat()
    payload = _answer_payload("Izračunaj: 1/2 + 1/3", "5/6")
    payload["recent_tasks"] = ["Izračunaj 1/2 + 1/4."]
    svc.handle_chat(payload, chat, master, tmap, model="m", timeout=1)
    assert "NEDAVNO DATI ZADACI" in _last_user_prompt(chat)


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


def test_exam_selected_topic_ignores_stale_fraction_state(master, tmap):
    row = master["topics_by_id"][EXPR_TOPIC]
    chat = _fake_chat("Evo tri zadatka za kontrolni iz izraza s promjenljivim.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "exam",
        "selected_topic": EXPR_TOPIC,
        "selected_oblast": row["oblast"],
        "detected_topic": FR_TOPIC,
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
        "last_tutor_task": "Skrati razlomak 18/24.",
        "recent_tasks": ["Skrati razlomak 18/24."],
        "previous_next_state": {"active_task_kind": "practice"},
        "conversation_history": [
            {"role": "assistant", "content": "Evo tri zadatka za tvoj kontrolni iz razlomaka."}
        ],
    }, chat, master, tmap, model="m", timeout=1)

    up = _last_user_prompt(chat)
    all_messages = "\n".join(m["content"] for m in chat.calls["messages"][-1] if isinstance(m.get("content"), str))
    assert out["effective_topic"] == EXPR_TOPIC
    assert row["display_name"] in up
    assert "Razlomci" not in all_messages
    assert "Skrati razlomak" not in all_messages


def test_exam_selected_oblast_beats_stale_selected_topic(master, tmap):
    row = master["topics_by_id"][EXPR_TOPIC]
    chat = _fake_chat("Evo tri zadatka za kontrolni iz prirodnih brojeva i izraza.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "exam",
        "selected_topic": FR_TOPIC,
        "selected_oblast": row["oblast"],
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
        "last_tutor_task": "Skrati razlomak 18/24.",
        "recent_tasks": ["Skrati razlomak 18/24."],
        "conversation_history": [
            {"role": "assistant", "content": "Vjezbali smo razlomke."}
        ],
    }, chat, master, tmap, model="m", timeout=1)

    up = _last_user_prompt(chat)
    all_messages = "\n".join(m["content"] for m in chat.calls["messages"][-1] if isinstance(m.get("content"), str))
    assert out["effective_topic"] == "unknown"
    assert f"OBLAST KONTROLNOG: {row['oblast']}" in up
    assert row["display_name"] in up
    assert "Pojam razlomka" not in all_messages
    assert "Skrati razlomak" not in all_messages


def test_multi_answer_partial_feedback_no_global_tacno_and_fixed_numbering(master, tmap):
    task = (
        "1. Saberi razlomke 2/5 i 3/10.\n"
        "2. Uporedi razlomke 4/9 i 2/3 i odredi koji je ve\u0107i.\n"
        "3. Izra\u010dunaj rezultat izraza 3 \u00b7 4/5."
    )
    chat = _fake_chat(
        "Ta\u010dno.\n\n"
        "1. Kada sabere\u0161 2/5 i 3/10, dobije\u0161 7/10.\n"
        "1. Upore\u0111uju\u0107i razlomke 4/9 i 2/3, ve\u0107i je 2/3.\n"
        "Tre\u0107a stavka jo\u0161 \u010deka tvoj odgovor. Kako si izra\u010dunao 3 \u00b7 4/5?"
    )
    out = svc.handle_chat(
        _answer_payload(task, "1) 7/10 2) veci je 2/3"),
        chat, master, tmap, model="m", timeout=1,
    )

    verdicts = [i["verdict"] for i in out["answer_check"]["items"]]
    assert verdicts == ["correct", "correct", "missing"]
    assert out["answer"].startswith("Zadaci 1 i 2 su ta\u010dni.")
    assert not out["answer"].startswith("Ta\u010dno.")
    assert "\n2. Upore" in out["answer"]
    assert "\n1. Upore" not in out["answer"]
    assert "\n3. Tre" in out["answer"]
    up = _last_user_prompt(chat)
    assert "globalnom" in up
    assert "1., 1." in up


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
    assert body["answer_verdict"] == "correct"
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
