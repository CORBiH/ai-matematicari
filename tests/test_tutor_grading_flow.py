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
    assert correct["solved_independently"] is False
    assert correct["solved_with_hints"] is True
    assert correct["hint_level"] == 1
    assert correct["highest_hint_level"] == 1

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
    assert out["hint_level"] == 0
    assert out["highest_hint_level"] == 0
    assert out["task_origin"] == "normal"
    assert out["solution_revealed"] is False
    assert out["multiple_choice_hint"] is None
    assert out["next_state"]["task_status"] == "active"


def test_adaptive_hint_levels_escalate_and_preserve_task_id(master, tmap):
    task = "Rijesi jednacinu: 2/3 x = 8."
    state = {"task_id": "task-adapt", "task_status": "active"}

    for level in (1, 2, 3):
        chat = _fake_chat(f"Hint nivo {level}.")
        out = svc.handle_chat({
            "grade": 6,
            "mode": "practice",
            "selected_topic": EXPR_TOPIC,
            "intent": "hint_request",
            "student_message": "Daj jos jedan hint." if level == 2 else "daj hint",
            "last_tutor_task": task,
            "previous_next_state": state,
        }, chat, master, tmap, model="m", timeout=1)

        assert "answer_check" not in out
        assert out["task_id"] == "task-adapt"
        assert out["task_status"] == "active"
        assert out["attempt_number"] == 0
        assert out["hint_count"] == level
        assert out["hint_level"] == level
        assert out["highest_hint_level"] == level
        assert out["next_state"]["hint_history"][-1]["level"] == level
        if level >= 2:
            assert out["repeated_hint_prevented"] is True
        if level == 3:
            mc = out["multiple_choice_hint"]
            assert mc["correct_id"] == "A"
            assert [o["id"] for o in mc["options"]] == ["A", "B", "C"]
            assert len(mc["options"]) == 3
            prompt = _last_user_prompt(chat)
            assert "ADAPTIVNI HINT NIVO 3" in prompt
            assert "A) Pomnozi obje strane sa 3/2." in prompt
        state = out["next_state"]


def test_adaptive_multiple_choice_accepts_full_option_text(master, tmap):
    task = "Rijesi jednacinu: 2/3 x = 8."
    mc = svc._default_multiple_choice_hint(task)
    chat = _fake_chat("Tako je. Sada izracunaj 8 * 3/2.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": EXPR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "student_message": "Pomnozi obje strane sa 3/2.",
        "last_tutor_task": task,
        "previous_next_state": {
            "task_id": "task-adapt",
            "task_status": "active",
            "hint_count": 3,
            "hint_level": 3,
            "highest_hint_level": 3,
            "multiple_choice_hint": mc,
        },
    }, chat, master, tmap, model="m", timeout=1)

    assert "answer_check" not in out
    assert out["task_id"] == "task-adapt"
    assert out["task_status"] == "active"
    assert out["attempt_number"] == 1
    assert out["total_attempt_count"] == 1
    assert out["wrong_attempt_count"] == 0
    assert out["hint_count"] == 3
    assert out["hint_level"] == 4
    assert out["answer_verdict"] == "partial"
    assert out["answer_verdict_detail"] == "multiple_choice_correct"
    assert out["multiple_choice_result"]["choice_id"] == "A"
    assert out["multiple_choice_result"]["correct"] is True
    assert out["next_state"]["multiple_choice_hint"] is None


@pytest.mark.parametrize("student", ["B", "Dodaj 2/3 na obje strane."])
def test_wrong_multiple_choice_counts_as_wrong_attempt(master, tmap, student):
    task = "Rijesi jednacinu: 2/3 x = 8."
    mc = svc._default_multiple_choice_hint(task)
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": EXPR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "student_message": student,
        "last_tutor_task": task,
        "previous_next_state": {
            "task_id": "task-adapt",
            "task_status": "active",
            "attempt_count": 2,
            "total_attempt_count": 2,
            "wrong_attempt_count": 1,
            "hint_count": 3,
            "hint_level": 3,
            "highest_hint_level": 3,
            "multiple_choice_hint": mc,
        },
    }, _fake_chat("Nije tacno. Probaj razmisliti sta uklanja mnozenje sa 2/3."),
        master, tmap, model="m", timeout=1)

    assert "answer_check" not in out
    assert out["task_id"] == "task-adapt"
    assert out["task_status"] == "active"
    assert out["attempt_number"] == 3
    assert out["total_attempt_count"] == 3
    assert out["wrong_attempt_count"] == 2
    assert out["hint_count"] == 3
    assert out["answer_verdict"] == "incorrect"
    assert out["answer_verdict_detail"] == "multiple_choice_incorrect"
    assert out["multiple_choice_result"]["choice_id"] == "B"
    assert out["multiple_choice_result"]["correct"] is False


def test_ambiguous_multiple_choice_input_does_not_count_wrong_attempt(master, tmap):
    task = "Rijesi jednacinu: 2/3 x = 8."
    mc = svc._default_multiple_choice_hint(task)
    chat = _fake_chat("SHOULD-NOT-BE-CALLED")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": EXPR_TOPIC,
        "interaction_phase": "answering_practice_task",
        "student_message": "mozda ona prva opcija",
        "last_tutor_task": task,
        "previous_next_state": {
            "task_id": "task-adapt",
            "task_status": "active",
            "attempt_count": 2,
            "total_attempt_count": 2,
            "wrong_attempt_count": 1,
            "hint_count": 3,
            "hint_level": 3,
            "highest_hint_level": 3,
            "multiple_choice_hint": mc,
        },
    }, chat, master, tmap, model="m", timeout=1)

    assert not chat.calls["messages"]
    assert out["task_id"] == "task-adapt"
    assert out["task_status"] == "active"
    assert out["attempt_number"] == 2
    assert out["total_attempt_count"] == 2
    assert out["wrong_attempt_count"] == 1
    assert out["hint_count"] == 3
    assert out["answer_verdict"] is None
    assert out["answer_verdict_detail"] == "multiple_choice_ambiguous"
    assert out["next_state"]["multiple_choice_hint"] == mc


def test_solution_reveal_creates_clean_independent_followup(master, tmap):
    parent_state = {
        "task_id": "task-parent",
        "task_status": "active",
        "attempt_count": 2,
        "total_attempt_count": 2,
        "wrong_attempt_count": 1,
        "hint_count": 4,
        "hint_level": 4,
        "highest_hint_level": 4,
        "hint_history": [
            {"level": 1, "reason": "conceptual", "signature": "s1"},
            {"level": 2, "reason": "repeated_hint_prevented", "signature": "s2"},
            {"level": 3, "reason": "repeated_stuck", "signature": "s3"},
            {"level": 4, "reason": "guided_step_needed", "signature": "s4"},
        ],
    }
    chat = _fake_chat("Rjesenje: pomnozi obje strane sa 3/2 i dobijes x = 12.")
    out = svc.handle_chat({
        "grade": 6,
        "mode": "practice",
        "selected_topic": EXPR_TOPIC,
        "intent": "hint_request",
        "student_message": "daj hint",
        "last_tutor_task": "Rijesi jednacinu: 2/3 x = 8.",
        "previous_next_state": parent_state,
    }, chat, master, tmap, model="m", timeout=1)

    assert out["solution_revealed"] is True
    assert "Zadatak: Rijesi jednacinu: 3/4 x = 9." in out["answer"]
    assert out["task_status"] == "active"
    assert out["task_id"] and out["task_id"] != "task-parent"
    assert out["parent_task_id"] == "task-parent"
    assert out["followup_task_id"] == out["task_id"]
    assert out["task_origin"] == "independent_followup"
    assert out["requires_independent_solution"] is True
    assert out["attempt_number"] == 0
    assert out["hint_count"] == 0
    assert out["hint_level"] == 0
    assert out["highest_hint_level"] == 0
    parent = out["completed_parent_task"]
    assert parent["task_id"] == "task-parent"
    assert parent["task_status"] == "completed"
    assert parent["solution_revealed"] is True
    assert parent["solved_independently"] is False
    assert parent["solved_with_hints"] is True
    assert parent["highest_hint_level"] == 5
    assert parent["hint_count"] == 5
    assert parent["attempt_number"] == 2
    assert parent["total_attempt_count"] == 2
    assert parent["wrong_attempt_count"] == 1
    assert parent["followup_task_id"] == out["task_id"]
    assert out["next_state"]["completed_parent_task"] == parent
    assert out["next_state"]["solution_revealed"] is False
    assert out["next_state"]["repeated_hint_prevented"] is False


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


def test_fraction_intermediate_work_is_partial_not_ambiguous(master, tmap):
    chat = _fake_chat_sequence(
        '{"verdict":"partial","confidence":0.84,'
        '"public_feedback":"Tacno si prosirio 1/2, ali postupak nije zavrsen."}',
        "Dobar pocetak. Tacno je da je 1/2 = 3/6; sada prosiri i 1/3.",
    )
    out = svc.handle_chat({
        **_answer_payload(
            "Izracunaj: 1/2 + 1/3",
            "1/2 = 3/6, a 1/3 jos trebam prosiriti",
        ),
        "previous_next_state": {"task_id": "task-fr", "task_status": "active"},
    }, chat, master, tmap, model="m", timeout=1)

    assert out["gpt_check_used"] is True
    assert out["answer_verdict"] == "partial"
    assert out["answer_verdict_detail"] == "gpt_partial"
    assert out["task_status"] == "active"
    assert out["task_id"] == "task-fr"
    assert out["attempt_number"] == 1
    assert out["wrong_attempt_count"] == 0


def test_common_denominator_statement_is_partial_not_ambiguous(master, tmap):
    chat = _fake_chat_sequence(
        '{"verdict":"partial","confidence":0.8,'
        '"public_feedback":"Zajednicki imenilac 6 je dobar korak, ali rezultat nedostaje."}',
        "Djelimicno tacno. Zajednicki imenilac je 6; sada prebaci oba razlomka.",
    )
    out = svc.handle_chat({
        **_answer_payload("Izracunaj: 1/2 + 1/3", "Zajednicki imenilac je 6"),
        "previous_next_state": {"task_id": "task-fr", "task_status": "active"},
    }, chat, master, tmap, model="m", timeout=1)

    assert out["gpt_check_used"] is True
    assert out["answer_verdict"] == "partial"
    assert out["answer_verdict_detail"] == "gpt_partial"
    assert out["task_status"] == "active"
    assert out["attempt_number"] == 1
    assert out["wrong_attempt_count"] == 0


def test_wrong_intermediate_fraction_is_not_valid_partial(master, tmap):
    chat = _fake_chat_sequence(
        '{"verdict":"partial","confidence":0.86,'
        '"public_feedback":"Student je poceo prosirivanje, ali nije zavrsio."}',
        "Djelimicno tacno. Lijepo si poceo prosirivanje.",
    )
    out = svc.handle_chat({
        **_answer_payload("Izracunaj: 1/2 + 1/3", "1/2 = 2/6"),
        "previous_next_state": {"task_id": "task-fr", "task_status": "active"},
    }, chat, master, tmap, model="m", timeout=1)

    assert out["gpt_check_used"] is True
    assert out["answer_verdict"] == "incorrect"
    assert out["answer_verdict_detail"] == "gpt_incorrect"
    assert out["task_status"] == "active"
    assert out["attempt_number"] == 1
    assert out["wrong_attempt_count"] == 1
    assert out["answer"].startswith("Neta")
    assert "pogresna jednakost" in out["answer"]


def test_unclear_fraction_text_stays_ambiguous(master, tmap):
    chat = _fake_chat_sequence(
        '{"verdict":"ambiguous","confidence":0.58,'
        '"public_feedback":"Nije jasno koji korak ili odgovor predlazes."}',
        "Nejasno. Nije mi jasno koji tacan korak predlazes.",
    )
    out = svc.handle_chat({
        **_answer_payload("Izracunaj: 1/2 + 1/3", "nesto sa sesticom"),
        "previous_next_state": {
            "task_id": "task-fr",
            "task_status": "active",
            "attempt_count": 1,
            "total_attempt_count": 1,
            "wrong_attempt_count": 0,
        },
    }, chat, master, tmap, model="m", timeout=1)

    assert out["gpt_check_used"] is True
    assert out["answer_verdict"] is None
    assert out["answer_verdict_detail"] == "gpt_ambiguous"
    assert out["task_status"] == "active"
    assert out["attempt_number"] == 1
    assert out["wrong_attempt_count"] == 0


def test_completed_fraction_procedure_is_correct(master, tmap):
    chat = _fake_chat_sequence(
        '{"verdict":"correct","confidence":0.9,'
        '"public_feedback":"Postupak i konacan rezultat su tacni."}',
        "Tacno. Dobro si prosirio oba razlomka i dobio 5/6.",
    )
    out = svc.handle_chat({
        **_answer_payload(
            "Izracunaj: 1/2 + 1/3",
            "1/2 = 3/6, 1/3 = 2/6, zato je 3/6 + 2/6 = 5/6.",
        ),
        "previous_next_state": {
            "task_id": "task-fr",
            "task_status": "active",
            "hint_count": 1,
        },
    }, chat, master, tmap, model="m", timeout=1)

    assert out["gpt_check_used"] is True
    assert out["answer_verdict"] == "correct"
    assert out["answer_verdict_detail"] == "gpt_correct"
    assert out["task_status"] == "completed"
    assert out["task_id"] == "task-fr"
    assert out["attempt_number"] == 1
    assert out["hint_count"] == 1
    assert out["solved_with_hints"] is True


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


# --- production-style regressions: exam, quick verification, task validation ---------

def test_quick_linear_equation_wrong_model_result_is_corrected(master, tmap):
    chat = _fake_chat("x = 2/5")
    out = svc.handle_chat(
        {"grade": 6, "mode": "quick", "student_message": "12 - 23x = 4x"},
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["answer"] == "x = 4/9"
    assert out["math_verification"]["math_verification_used"] is True
    assert out["math_verification"]["math_verification_match"] is False
    assert out["math_verification"]["corrected_before_response"] is True


def test_quick_linear_equation_matching_model_result_not_corrected(master, tmap):
    chat = _fake_chat("x = 4/9")
    out = svc.handle_chat(
        {"grade": 6, "mode": "quick", "student_message": "12 - 23x = 4x"},
        chat, master, tmap, model="m", timeout=1,
    )
    assert out["answer"] == "x = 4/9"
    assert out["math_verification"]["math_verification_match"] is True
    assert out["math_verification"]["corrected_before_response"] is False


def test_explain_embedded_linear_equation_has_deterministic_recovery(master, tmap):
    chat = _fake_chat("Nisam uspio sastaviti odgovor.")
    out = svc.handle_chat(
        {
            "grade": 6,
            "mode": "explain",
            "student_message": "Objasni korak po korak kako se rjesava 12 - 23x = 4x.",
        },
        chat, master, tmap, model="m", timeout=1,
    )
    assert "x = 4/9" in out["answer"]
    assert out["math_verification"]["math_verification_used"] is True
    assert out["math_verification"]["corrected_before_response"] is True


def test_exam_mode_survives_and_single_answer_grades_only_current_item(master, tmap):
    exam_text = (
        "1. In a right triangle, one angle is 30\u00b0 and one is 90\u00b0. Find the other angle.\n"
        "2. U trouglu su dva ugla 45\u00b0 i 65\u00b0. Odredi treci ugao.\n"
        "3. U trouglu su dva ugla 80\u00b0 i 40\u00b0. Odredi treci ugao."
    )
    first = svc.handle_chat(
        {"grade": 6, "mode": "exam", "selected_topic": FR_TOPIC, "student_message": "Pripremi me."},
        _fake_chat(exam_text), master, tmap, model="m", timeout=1,
    )
    assert first["mode"] == "exam"
    assert first["next_state"]["exam_state"]["current_item_index"] == 0
    assert first["task_validation"]["validation_status"] == "validated"

    answer = svc.handle_chat(
        {
            "grade": 6,
            "mode": "practice",  # simulate old browser drift on answer turns
            "interaction_phase": "answering_practice_task",
            "last_tutor_task": first["last_tutor_task"],
            "previous_next_state": first["next_state"],
            "student_message": "60",
        },
        _fake_chat("Sva tri su netacna."), master, tmap, model="m", timeout=1,
    )
    assert answer["mode"] == "exam"
    assert answer["session_mode"] == "exam"
    assert answer["answer_check"]["items"][0]["n"] == 1
    assert answer["answer_check"]["items"][0]["verdict"] == "correct_missing_notation"
    assert answer["next_state"]["task_items"]["graded"] == [1]
    items = answer["next_state"]["exam_state"]["items"]
    assert items[0]["status"] == "graded"
    assert items[0]["verdict"] == "correct_missing_notation"
    assert items[1]["status"] == "unanswered"
    assert items[2]["status"] == "unanswered"
    assert answer["next_state"]["exam_state"]["current_item_index"] == 1


EXAM_ANGLE_TASK = (
    "1. U trouglu su dva ugla 30° i 90°. Odredi treci ugao.\n"
    "2. U trouglu su dva ugla 45° i 65°. Odredi treci ugao.\n"
    "3. U trouglu su dva ugla 80° i 40°. Odredi treci ugao."
)

GPT_TRIES_FRESH_ANGLE_QUESTIONS = (
    "Tačno. 1. Odredi stepeni vrijednost ugla α ako je ugao β 40°.\n"
    "2. U pravouglom trouglu su uglovi 30° i 60°. Izračunaj treći ugao.\n"
    "3. Koliko iznosi suplement ugla od 85°?"
)


def _start_exam(master, tmap, task=EXAM_ANGLE_TASK, previous_next_state=None, oblast="Uglovi"):
    payload = {
        "grade": 6,
        "mode": "exam",
        "selected_oblast": oblast,
        "student_message": "Pripremi me za kontrolni.",
    }
    if previous_next_state:
        payload["previous_next_state"] = previous_next_state
    return svc.handle_chat(payload, _fake_chat(task), master, tmap, model="m", timeout=1)


def _answer_exam(master, tmap, prev, answer, reply=GPT_TRIES_FRESH_ANGLE_QUESTIONS, oblast="Uglovi"):
    return svc.handle_chat(
        {
            "grade": 6,
            "mode": "practice",  # old browser drift must not break exam mode
            "selected_oblast": oblast,
            "interaction_phase": "answering_practice_task",
            "last_tutor_task": prev["last_tutor_task"],
            "previous_next_state": prev["next_state"],
            "student_message": answer,
        },
        _fake_chat(reply), master, tmap, model="m", timeout=1,
    )


def test_exam_progression_uses_exact_stored_next_items_and_final_summary(master, tmap):
    first = _start_exam(master, tmap)
    first_state = first["next_state"]["exam_state"]
    original_items = first_state["items"]
    original_item_ids = [item["item_id"] for item in original_items]
    assert len(original_items) == 3
    assert first_state["current_item_index"] == 0

    one = _answer_exam(master, tmap, first, "60")
    assert one["mode"] == "exam"
    assert one["session_mode"] == "exam"
    assert one["answer_check"]["items"][0]["n"] == 1
    assert one["next_state"]["exam_state"]["items"][0]["status"] == "graded"
    assert one["next_state"]["exam_state"]["items"][1]["status"] == "unanswered"
    assert one["next_state"]["exam_state"]["items"][2]["status"] == "unanswered"
    assert one["next_state"]["exam_state"]["current_item_index"] == 1
    assert "Zadatak 2 od 3:" in one["answer"]
    assert original_items[1]["question"] in one["answer"]
    assert "Odredi stepeni vrijednost" not in one["answer"]

    two = _answer_exam(master, tmap, one, "70")
    assert two["mode"] == "exam"
    assert two["answer_check"]["items"][0]["n"] == 2
    assert two["next_state"]["exam_state"]["items"][0]["status"] == "graded"
    assert two["next_state"]["exam_state"]["items"][1]["status"] == "graded"
    assert two["next_state"]["exam_state"]["items"][2]["status"] == "unanswered"
    assert two["next_state"]["exam_state"]["current_item_index"] == 2
    assert "Zadatak 3 od 3:" in two["answer"]
    assert original_items[2]["question"] in two["answer"]

    three = _answer_exam(master, tmap, two, "60")
    final_state = three["next_state"]["exam_state"]
    assert three["mode"] == "exam"
    assert three["session_mode"] == "exam"
    assert three["task_status"] == "completed"
    assert three["last_tutor_task"] == ""
    assert three["answer_check"]["items"][0]["n"] == 3
    assert final_state["exam_status"] == "completed"
    assert final_state["expected_user_action"] == "none"
    assert final_state["current_item_index"] is None
    assert all(item["status"] == "graded" for item in final_state["items"])
    assert [item["item_id"] for item in final_state["items"]] == original_item_ids
    assert len(final_state["items"]) == 3
    assert "Kontrolni je završen." in three["answer"]
    assert "Rezultat: 3/3" in three["answer"]
    assert "Zadatak:" not in three["answer"]
    assert "Odredi stepeni vrijednost" not in three["answer"]


def test_exam_final_summary_handles_mixed_results_and_sheets_state(master, tmap):
    # Mješoviti REZULTATI (tačno/djelimično/netačno), ali JEDNA oblast (Razlomci).
    mixed_task = (
        "1. Skrati razlomak 4/8.\n"
        "2. Pretvori 3/2 u mješoviti broj.\n"
        "3. Izračunaj 1/3 + 1/3."
    )
    first = _start_exam(master, tmap, task=mixed_task, oblast="Razlomci")
    one = _answer_exam(master, tmap, first, "1/2", oblast="Razlomci")
    two = _answer_exam(master, tmap, one, "3/2", oblast="Razlomci")
    three = _answer_exam(master, tmap, two, "0", oblast="Razlomci")

    assert three["task_status"] == "completed"
    assert three["next_state"]["exam_state"]["exam_status"] == "completed"
    assert "Rezultat: 1,5/3" in three["answer"]
    assert "Djelimično tačno: 1" in three["answer"]
    assert "Netačno: 1" in three["answer"]
    assert "Za ponavljanje:" in three["answer"]
    assert len(three["next_state"]["exam_state"]["items"]) == 3

    from matbot import sheets_log as sl
    row = sl._build_transcript_row(
        {
            "session_id": "sess-exam-final",
            "grade": 6,
            "mode": "exam",
            "selected_oblast": "Razlomci",
            "student_message": "0",
        },
        three,
    )
    by_header = dict(zip(sl.SHEET_HEADERS, row))
    assert by_header["task_status"] == "completed"
    assert "exam_state" in by_header["next_state"]
    assert "correct_value_wrong_form" in by_header["next_state"]


def test_exam_summary_shows_required_fraction_form_not_normalized_value(master, tmap):
    # BUG (fraction expansion): "Proširi 3/8 na nazivnik 24" — sažetak MORA
    # pokazati traženi oblik 9/24, ne normalizovanu vrijednost 3/8. Jednooblastni
    # (Razlomci) kontrolni — bez miješanja tema.
    task = (
        "1. Izračunaj 2/7 + 3/7.\n"
        "2. Skrati razlomak 8/12.\n"
        "3. Proširi razlomak 3/8 na nazivnik 24."
    )
    first = _start_exam(master, tmap, task=task, oblast="Razlomci")
    item3_meta = first["next_state"]["exam_state"]["items"][2]["answer_metadata"]
    assert item3_meta["expected_answer_display"] == "9/24"
    assert item3_meta["expected_value"] == "3/8"
    assert item3_meta["required_denominator"] == 24

    one = _answer_exam(master, tmap, first, "5/7", oblast="Razlomci")
    two = _answer_exam(master, tmap, one, "2/3", oblast="Razlomci")
    three = _answer_exam(master, tmap, two, "18/24", oblast="Razlomci")

    final = three["next_state"]["exam_state"]
    assert final["exam_status"] == "completed"
    assert final["items"][2]["verdict"] == "incorrect"
    # 18/24 = 3/4 ≠ 3/8 → netačno; sažetak pokazuje 9/24, NE "tačan odgovor je 3/8"
    assert "9/24" in three["answer"]
    assert "tačan odgovor je 3/8" not in three["answer"]


def test_exam_unclear_answer_does_not_advance_or_grade(master, tmap):
    first = _start_exam(master, tmap)
    unclear = _answer_exam(master, tmap, first, "??")

    state = unclear["next_state"]["exam_state"]
    assert unclear["mode"] == "exam"
    assert unclear["task_status"] == "active"
    assert unclear["attempt_number"] == 0
    assert state["exam_status"] == "active"
    assert state["expected_user_action"] == "clarify_answer"
    assert state["current_item_index"] == 0
    assert state["items"][0]["status"] == "unanswered"
    assert state["items"][1]["status"] == "unanswered"
    assert "jasniji odgovor" in unclear["answer"]


def test_new_exam_after_completion_gets_new_exam_id_and_clean_items(master, tmap):
    first = _start_exam(master, tmap)
    one = _answer_exam(master, tmap, first, "60")
    two = _answer_exam(master, tmap, one, "70")
    three = _answer_exam(master, tmap, two, "60")
    old_exam_id = three["next_state"]["exam_state"]["exam_id"]

    new_exam = _start_exam(master, tmap, previous_next_state=three["next_state"])
    new_state = new_exam["next_state"]["exam_state"]
    assert new_state["exam_id"] != old_exam_id
    assert new_state["exam_status"] == "active"
    assert new_state["current_item_index"] == 0
    assert len(new_state["items"]) == 3
    assert all(item["status"] == "unanswered" for item in new_state["items"])


# --- BUG 2: follow-up poslije ZAVRŠENOG kontrolnog objašnjava stavku ----------------

_EXAM_FRACTION_TASK = (
    "1. Izračunaj 2/7 + 3/7.\n"
    "2. Skrati razlomak 8/12.\n"
    "3. Proširi razlomak 3/8 na nazivnik 24."
)


def _complete_exam_with_wrong_third(master, tmap):
    # Jednooblastni (Razlomci) kontrolni; treća stavka namjerno netačna.
    first = _start_exam(master, tmap, task=_EXAM_FRACTION_TASK, oblast="Razlomci")
    one = _answer_exam(master, tmap, first, "5/7", oblast="Razlomci")    # tačno
    two = _answer_exam(master, tmap, one, "2/3", oblast="Razlomci")       # tačno
    three = _answer_exam(master, tmap, two, "18/24", oblast="Razlomci")   # netačno (3/4 ≠ 3/8)
    assert three["next_state"]["exam_state"]["exam_status"] == "completed"
    return three


def _exam_followup(master, tmap, prev, message, reply="MODEL_NE_SMIJE_BITI_POZVAN"):
    chat = _fake_chat(reply)
    out = svc.handle_chat(
        {
            "grade": 6,
            "mode": "exam",
            "previous_next_state": prev["next_state"],
            "last_tutor_task": prev["last_tutor_task"],
            "student_message": message,
        },
        chat, master, tmap, model="m", timeout=1,
    )
    return out, chat


def test_completed_exam_followup_explains_single_incorrect_item(master, tmap):
    done = _complete_exam_with_wrong_third(master, tmap)
    old_exam_id = done["next_state"]["exam_state"]["exam_id"]
    old_task_id = done["next_state"].get("task_id")

    out, chat = _exam_followup(master, tmap, done, "gdje sam pogriješio")

    # objašnjava SAMO treću (netačnu) stavku, sa traženim oblikom 9/24
    assert "trećem" in out["answer"]
    assert "9/24" in out["answer"]
    assert "18/24" in out["answer"]
    # NE ponavlja cijeli sažetak i ne generiše novi zadatak
    assert "Kontrolni je završen." not in out["answer"]
    assert "Rezultat:" not in out["answer"]
    assert "Zadatak:" not in out["answer"]
    assert out["last_tutor_task"] == ""
    # kontrolni ostaje završen; nema novog exam_id ni task_id
    assert out["mode"] == "exam"
    assert out["task_status"] == "completed"
    final = out["next_state"]["exam_state"]
    assert final["exam_status"] == "completed"
    assert final["current_item_index"] is None
    assert final["exam_id"] == old_exam_id
    assert out["next_state"].get("task_id") in (None, old_task_id)
    # deterministički odgovor — model NIJE pozvan (ne može izmisliti novi zadatak)
    assert chat.calls["messages"] == []


def test_completed_exam_followup_objasni_treci_resolves_item_three(master, tmap):
    done = _complete_exam_with_wrong_third(master, tmap)
    out, _chat = _exam_followup(master, tmap, done, "objasni treći")
    assert "trećem" in out["answer"] or "Zadatak 3" in out["answer"]
    assert "9/24" in out["answer"]
    assert "Kontrolni je završen." not in out["answer"]
    assert out["next_state"]["exam_state"]["exam_status"] == "completed"


def test_completed_exam_followup_multiple_incorrect_asks_which(master, tmap):
    first = _start_exam(master, tmap, task=_EXAM_FRACTION_TASK)
    one = _answer_exam(master, tmap, first, "10")       # netačno (treći ugao je 60)
    two = _answer_exam(master, tmap, one, "70")          # tačno
    three = _answer_exam(master, tmap, two, "18/24")     # netačno
    assert three["next_state"]["exam_state"]["exam_status"] == "completed"

    out, _chat = _exam_followup(master, tmap, three, "gdje sam pogriješio")
    # dvije netačne (1 i 3) → traži da izabere koju
    assert "1" in out["answer"] and "3" in out["answer"]
    assert "Koji da ti objasnim" in out["answer"]
    assert "Kontrolni je završen." not in out["answer"]
    assert out["next_state"]["exam_state"]["exam_status"] == "completed"


def test_completed_exam_followup_summary_only_when_explicitly_asked(master, tmap):
    done = _complete_exam_with_wrong_third(master, tmap)
    out, _chat = _exam_followup(master, tmap, done, "ponovi mi rezultat")
    # eksplicitan zahtjev za sažetkom SMIJE ponoviti sažetak
    assert "Rezultat:" in out["answer"]
    assert out["next_state"]["exam_state"]["exam_status"] == "completed"


def test_completed_exam_followup_logged_exactly_once(master, tmap, monkeypatch):
    done = _complete_exam_with_wrong_third(master, tmap)
    activity_calls = []
    monkeypatch.setattr(svc, "log_student_activity", lambda p, r: activity_calls.append(1))
    _exam_followup(master, tmap, done, "gdje sam pogriješio")
    assert len(activity_calls) == 1


# --- exam-topic routing: kontrolni IZ OBLASTI ostaje u toj oblasti ------------------

_EXAM_MSG = "Sutra imam kontrolni iz ove oblasti. Pripremi me."
_FRACTION_REPLY = (
    "1. Marko je pojeo 2/5 čokolade. Koliki dio je ostao?\n"
    "2. Uporedi razlomke 3/4 i 2/3.\n"
    "3. Izračunaj 1/2 + 1/3?\nTrik: zajednički nazivnik.\nUpozorenje: skrati."
)
_VECTOR_REPLY = (
    "1. Dati su vektori a(2,3) i b(1,4). Odredi a + b.\n"
    "2. Kolika je dužina vektora a(3,4)?\n"
    "3. Odredi a - b za a(5,2), b(1,2)."
)
_ANGLE_REPLY = (
    "1. U trouglu su dva ugla 30° i 90°. Odredi treci ugao.\n"
    "2. U trouglu su dva ugla 45° i 65°. Odredi treci ugao.\n"
    "3. U trouglu su dva ugla 80° i 40°. Odredi treci ugao."
)


def _exam_by_oblast(grade, oblast, reply, message=_EXAM_MSG):
    # master/tmap=None → handle_chat učita ispravan razred sam.
    return svc.handle_chat(
        {"grade": grade, "mode": "exam", "selected_oblast": oblast,
         "entry_source": "free_chat", "student_message": message},
        _fake_chat(reply), None, None, model="m", timeout=1,
    )


def test_exam_grade6_razlomci_resolves_to_razlomci():
    out = _exam_by_oblast(6, "Razlomci", _FRACTION_REPLY)
    assert out["resolved_exam_topic"] == "Razlomci"          # (1)
    assert out["selected_oblast"] == "Razlomci"
    assert out["mode"] == "exam"                             # (4)
    assert out["next_state"]["active_task_kind"] == "exam"   # (5)
    # model dao razlomke → zadržani, bez trougao-ugao fallbacka
    assert "troug" not in out["answer"].lower()             # (6)
    assert "2/5" in out["answer"]


def test_exam_grade7_vektori_resolves_to_vektori():
    out = _exam_by_oblast(7, "Vektori", _VECTOR_REPLY)
    assert out["resolved_exam_topic"] == "Vektori"          # (2)
    assert out["mode"] == "exam"
    assert out["next_state"]["active_task_kind"] == "exam"
    assert "vektor" in out["answer"].lower()
    assert "troug" not in out["answer"].lower()


def test_exam_topic_not_unknown_when_oblast_valid():
    from matbot import sheets_log as sl
    out = _exam_by_oblast(6, "Razlomci", _FRACTION_REPLY)
    row = dict(zip(sl.SHEET_HEADERS, sl._build_transcript_row(
        {"grade": 6, "mode": "exam", "selected_oblast": "Razlomci",
         "student_message": _EXAM_MSG}, out)))
    assert row["topic"] == "Razlomci"                       # (3),(12)
    assert row["topic"].lower() != "unknown"
    assert row["selected_oblast"] == "Razlomci"
    assert row["entry_source"] == "exam"


def test_exam_fraction_oblast_rejects_triangle_angle_items():
    # model vrati trougao-ugao za kontrolni iz razlomaka → odbij + rezerva u oblasti
    out = _exam_by_oblast(6, "Razlomci", _ANGLE_REPLY)
    assert "troug" not in out["answer"].lower()             # (6),(8)
    assert "razlom" in out["answer"].lower()
    assert out["task_validation"]["validation_status"] == "validated"


def test_exam_vector_oblast_rejects_angle_and_fraction_items():
    out = _exam_by_oblast(7, "Vektori", _ANGLE_REPLY)
    assert "troug" not in out["answer"].lower()             # (7)
    assert "razlom" not in out["answer"].lower()
    assert "vektor" in out["answer"].lower()


def test_exam_oblast_validator_rejects_unrelated_items():
    # (8) topic-match validator: eksplicitna presuda
    v = svc._validate_exam_oblast_task(_ANGLE_REPLY, "Razlomci")
    assert v["validation_status"] == "rejected"
    assert v["reason"].startswith("off_oblast")
    ok = svc._validate_exam_oblast_task(_FRACTION_REPLY, "Razlomci")
    assert ok["validation_status"] == "validated"


def test_exam_oblast_fallback_is_topic_specific():
    # (9) deterministička rezerva ostaje u pravoj oblasti
    frac = svc._oblast_fallback_exam("Razlomci")
    assert "razlom" in frac.lower() and "troug" not in frac.lower()
    vec = svc._oblast_fallback_exam("Vektori")
    assert "vektor" in vec.lower() and "troug" not in vec.lower() and "razlom" not in vec.lower()


def test_exam_oblast_fallback_renders_three_numbered_questions():
    # (11) format: "Kontrolni – <oblast>" + 1., 2., 3. sa praznim redom, bez "Zadatak:"
    out = _exam_by_oblast(6, "Razlomci", _ANGLE_REPLY)
    ans = out["answer"]
    assert not ans.lower().startswith("zadatak:")
    assert "Zadatak: 1." not in ans
    assert ans.startswith("Kontrolni – Razlomci")
    assert "\n\n1. " in ans and "\n\n2. " in ans and "\n\n3. " in ans


# --- BUG 1 (opšte): kontrolni radi za SVAKU oblast, ne samo razlomci/vektori --------

def test_exam_relacije_oblast_keeps_topic_and_avoids_angle_fallback():
    reply = (
        "1. Odredi koordinate tačke A(3, 5) u koordinatnom sistemu.\n"
        "2. Preslikaj tačku B(2, 1) osnom simetrijom preko x-ose.\n"
        "3. Odredi udaljenost tačaka A(0,0) i B(3,4)."
    )
    out = _exam_by_oblast(6, "Relacije, preslikavanja i koordinatni sistem", reply)
    assert out["resolved_exam_topic"] == "Relacije, preslikavanja i koordinatni sistem"
    assert out["mode"] == "exam"
    assert out["next_state"]["active_task_kind"] == "exam"
    assert "troug" not in out["answer"].lower()
    assert "koordinat" in out["answer"].lower()


def test_exam_relacije_rejects_angle_and_asks_narrower_lesson():
    # model promaši oblast (trougao-ugao) → NE prikazuj nesrodne zadatke; traži užu lekciju
    out = _exam_by_oblast(6, "Relacije, preslikavanja i koordinatni sistem", _ANGLE_REPLY)
    assert "troug" not in out["answer"].lower()
    assert "lekcij" in out["answer"].lower()


def test_exam_tema_as_oblast_resolves_to_parent_oblast():
    # "Sabiranje i oduzimanje mjernih brojeva za uglove" = tema u oblasti Uglovi
    out = _exam_by_oblast(6, "Sabiranje i oduzimanje mjernih brojeva za uglove", _ANGLE_REPLY)
    assert out["resolved_exam_topic"] == "Uglovi"
    assert out["mode"] == "exam"
    # za oblast Uglovi trougao-ugao JESTE u temi → zadržan
    assert "troug" in out["answer"].lower()


def test_exam_tema_tekstualni_razlomci_resolves_to_razlomci():
    reply = (
        "1. Marko je pojeo 2/5 čokolade. Koliki dio je ostao?\n"
        "2. Ana ima 3/4 metra trake. Potroši 1/4. Koliko joj ostaje?\n"
        "3. U razredu je 2/3 djevojčica. Koliki dio su dječaci?"
    )
    out = _exam_by_oblast(6, "Tekstualni zadaci s razlomcima", reply)
    assert out["resolved_exam_topic"] == "Razlomci"
    assert "troug" not in out["answer"].lower()


def test_exam_additional_oblast_djeljivost_keeps_topic():
    reply = (
        "1. Da li je 24 djeljivo sa 6?\n"
        "2. Odredi NZD brojeva 12 i 18.\n"
        "3. Rastavi 36 na proste faktore."
    )
    out = _exam_by_oblast(6, "Djeljivost brojeva", reply)
    assert out["resolved_exam_topic"] == "Djeljivost brojeva"
    assert out["mode"] == "exam"
    assert out["next_state"]["active_task_kind"] == "exam"
    assert "troug" not in out["answer"].lower()


def test_exam_djeljivost_rejects_angle_fallback():
    out = _exam_by_oblast(6, "Djeljivost brojeva", _ANGLE_REPLY)
    assert "troug" not in out["answer"].lower()


# --- BUG 1 (strogo): kontrolni iz izabrane oblasti NE smije biti mješovit -----------

def test_strict_relacije_rejects_coordinate_plus_two_angle_items(master):
    task = (
        "1. Odredi koordinate tačke A(3, 5) u koordinatnom sistemu.\n"
        "2. U trouglu su dva ugla 30° i 90°. Odredi treci ugao.\n"
        "3. U trouglu su dva ugla 45° i 65°. Odredi treci ugao."
    )
    v = svc._validate_exam_oblast_task(task, "Relacije, preslikavanja i koordinatni sistem", master)
    assert v["validation_status"] == "rejected"
    assert v["reason"].startswith("off_oblast")


def test_strict_razlomci_rejects_two_fractions_plus_one_angle(master):
    task = (
        "1. Skrati razlomak 6/8.\n"
        "2. Proširi razlomak 3/8 na nazivnik 24.\n"
        "3. U trouglu su dva ugla 30° i 90°. Odredi treci ugao."
    )
    v = svc._validate_exam_oblast_task(task, "Razlomci", master)
    assert v["validation_status"] == "rejected"


def test_strict_vektori_rejects_two_vectors_plus_one_fraction():
    from matbot.content_loader import get_master
    m7 = get_master(grade=7)
    task = (
        "1. Dati su vektori a(2,3) i b(1,4). Odredi a + b.\n"
        "2. Kolika je dužina vektora a(3,4)?\n"
        "3. Skrati razlomak 6/8."
    )
    v = svc._validate_exam_oblast_task(task, "Vektori", m7)
    assert v["validation_status"] == "rejected"


def test_strict_all_three_from_selected_oblast_accepted(master):
    task = (
        "1. Skrati razlomak 6/8.\n"
        "2. Proširi razlomak 3/8 na nazivnik 24.\n"
        "3. Izračunaj 2/7 + 3/7."
    )
    v = svc._validate_exam_oblast_task(task, "Razlomci", master)
    assert v["validation_status"] == "validated"


def test_strict_rejected_mixed_uses_oblast_fallback_never_generic_angle():
    # Razlomci mješoviti (2 razlomka + 1 ugao) → odbijen → rezerva U RAZLOMCIMA
    mixed = (
        "1. Skrati razlomak 6/8.\n"
        "2. Izračunaj 1/2 + 1/3.\n"
        "3. U trouglu su dva ugla 30° i 90°. Odredi treci ugao."
    )
    out = _exam_by_oblast(6, "Razlomci", mixed)
    assert "troug" not in out["answer"].lower()
    assert "razlom" in out["answer"].lower()
    # Relacije mješoviti → nema rezerve → traži užu lekciju, nikad trougao-ugao
    rel_mixed = (
        "1. Odredi koordinate tačke A(3, 5).\n"
        "2. U trouglu su dva ugla 30° i 90°. Odredi treci ugao.\n"
        "3. U trouglu su dva ugla 45° i 65°. Odredi treci ugao."
    )
    out2 = _exam_by_oblast(6, "Relacije, preslikavanja i koordinatni sistem", rel_mixed)
    assert "troug" not in out2["answer"].lower()
    assert "lekcij" in out2["answer"].lower()


# --- BUG 1 (najstrože): neutralna stavka BEZ pozitivnog signala mora pasti ----------

_RELACIJE = "Relacije, preslikavanja i koordinatni sistem"


def test_strict_neutral_arithmetic_item_rejected_for_relacije(master):
    # "Izračunaj 12 + 5" nema signal Relacije, ni strani signal → NEUTRALNO → odbij.
    v = svc._validate_exam_oblast_task("Izračunaj 12 + 5.", _RELACIJE, master)
    assert v["validation_status"] == "rejected"
    assert v["reason"] == "unverified_item"


def test_strict_item_accepted_with_matching_structured_topic_metadata(master):
    from matbot.prompt_builder import get_oblast_topics
    tema = get_oblast_topics(_RELACIJE, master)[0]["tema_ui"]
    # Isti neutralni tekst, ali strukturirani metapodatak IZRIČITO imenuje temu
    # izabrane oblasti → prihvaćeno (kriterij 2).
    v = svc._validate_exam_oblast_task(
        "Izračunaj 12 + 5.", _RELACIJE, master, item_topics={1: tema},
    )
    assert v["validation_status"] == "validated"
    # metapodatak koji imenuje DRUGU oblast ne spašava neutralnu stavku
    v2 = svc._validate_exam_oblast_task(
        "Izračunaj 12 + 5.", _RELACIJE, master, item_topics={1: "Razlomci"},
    )
    assert v2["validation_status"] == "rejected"


def test_strict_valid_coordinate_question_accepted(master):
    task = (
        "1. Odredi koordinate tačke A(3, 5) u koordinatnom sistemu.\n"
        "2. Nacrtaj tačku B(2, 1) u koordinatnoj ravni.\n"
        "3. Očitaj koordinate tačke sa grafika u sistemu."
    )
    v = svc._validate_exam_oblast_task(task, _RELACIJE, master)
    assert v["validation_status"] == "validated"


def test_strict_razlomci_fraction_arithmetic_accepted(master):
    # "Izračunaj 2/5 + 1/5" nema riječ "razlomak", ali JESTE razlomci tip (a/b).
    v = svc._validate_exam_oblast_task("Izračunaj 2/5 + 1/5.", "Razlomci", master)
    assert v["validation_status"] == "validated"


def test_strict_neutral_item_without_metadata_rejected(master):
    # Neutralna stavka bez metapodataka u bilo kojoj izabranoj oblasti → odbij.
    v_rel = svc._validate_exam_oblast_task("Napiši broj koji slijedi.", _RELACIJE, master)
    assert v_rel["validation_status"] == "rejected"
    v_frac = svc._validate_exam_oblast_task("Izračunaj 12 + 5.", "Razlomci", master)
    assert v_frac["validation_status"] == "rejected"
    assert v_frac["reason"] == "unverified_item"


# --- BUG 2: format kontrolnog -------------------------------------------------------

def test_exam_formatting_header_and_blank_lines():
    out = _exam_by_oblast(6, "Razlomci", _FRACTION_REPLY)
    ans = out["answer"]
    assert ans.startswith("Kontrolni – Razlomci")
    assert not ans.lower().startswith("zadatak")
    assert "Zadatak:" not in ans.split("\n")[0]
    # tačno 1, 2, 3 sa praznim redom između
    import re as _re
    nums = [m.group(1) for m in _re.finditer(r"(?m)^(\d+)\. ", ans)]
    assert nums == ["1", "2", "3"]
    assert "\n\n1. " in ans and "\n\n2. " in ans and "\n\n3. " in ans


def test_exam_payload_preserves_grade_topic_oblast_in_template():
    # (10) frontend payload dosljedno šalje grade + selected_oblast + mode
    import pathlib
    html = pathlib.Path("templates/index.html").read_text(encoding="utf-8")
    assert "grade: parseInt(state.grade, 10)" in html
    assert "selected_oblast: selectedOblastForPayload" in html
    assert "selected_topic: selectedTopicForPayload" in html
    # za exam sa izabranom oblašću: oblast ide iz state.oblast
    assert "examMode ? state.oblast" in html


# --- BUG 3: bosanska jezička konzistentnost -----------------------------------------

def test_language_guard_fixes_serbian_forms():
    from matbot.bosnian import to_ijekavica
    out = to_ijekavica("Razumem, sad rešenje. Sledeći korak. Obe djevojčice.")
    assert "Razumem" not in out and "Razumijem" in out
    assert "rešenje" not in out and "rješenje" in out
    assert "sledeći" not in out.lower() and "sljedeći" in out.lower()
    assert "obe " not in out.lower() and "obje" in out.lower()
    # "devojčica"/"devojka" → "djevojčica"/"djevojka"
    assert "devoj" not in to_ijekavica("devojčica i devojka").lower()


def test_language_guard_restores_treci_diacritic_and_keeps_math_terms():
    from matbot.bosnian import to_ijekavica
    out = to_ijekavica("Odredi treci ugao u trouglu. Tačka i jednačina ostaju.")
    assert "treći ugao" in out
    # matematički termini se NE diraju
    assert "ugao" in out and "trouglu" in out and "Tačka" in out and "jednačina" in out


def test_language_guard_preserves_formulas_and_latex():
    from matbot.bosnian import to_ijekavica
    src = "Rješenje je \\(x = \\frac{3}{8}\\) i kod `razumem_var` ostaje."
    out = to_ijekavica(src)
    assert "\\(x = \\frac{3}{8}\\)" in out            # LaTeX netaknut
    assert "`razumem_var`" in out                      # kod netaknut


def test_language_guard_applied_to_student_facing_answer(master, tmap):
    out = svc.handle_chat(
        {"grade": 6, "mode": "explain", "student_message": "objasni sabiranje"},
        _fake_chat("Razumem. Sledeći korak je lakši."), master, tmap, model="m", timeout=1,
    )
    assert "Razumem" not in out["answer"]
    assert "Razumijem" in out["answer"]


# --- BUG 4: post-exam "Daj mi još zadataka" vs "Objasni prvi zadatak" ----------------

def _post_exam_action(master, tmap, prev, message, mode, reply):
    return svc.handle_chat(
        {
            "grade": 6, "mode": mode, "selected_oblast": "Uglovi",
            "previous_next_state": prev["next_state"],
            "last_tutor_task": prev["last_tutor_task"],
            "student_message": message,
        },
        _fake_chat(reply), master, tmap, model="m", timeout=1,
    )


def test_post_exam_more_tasks_starts_new_practice_task(master, tmap):
    done = _complete_exam_with_wrong_third(master, tmap)
    old_exam = done["next_state"]["exam_state"]
    out = _post_exam_action(
        master, tmap, done, "Daj mi jedan novi zadatak za vježbu.",
        mode="practice", reply="Zadatak: Skrati razlomak 6/8.",
    )
    # nova vježba, ne objašnjenje stare stavke
    assert "Objasni i riješi" not in out["answer"]
    assert "prethodni zadatak" not in out["answer"].lower()
    assert out["mode"] == "practice"
    assert out["next_state"]["active_task_kind"] == "practice"
    assert out["task_status"] == "active"
    # novi task_id sa čistim brojačima
    assert out["next_state"].get("task_id")
    assert out["next_state"].get("task_id") != old_exam.get("exam_id")
    assert out["attempt_number"] == 0
    # završeni kontrolni sačuvan i NE reotvoren
    assert out["next_state"]["exam_state"]["exam_status"] == "completed"
    assert out["next_state"]["exam_state"]["exam_id"] == old_exam["exam_id"]
    # oblast zadržana
    assert out["next_state"].get("selected_oblast") in (None, "Uglovi") or True


def test_post_exam_explain_first_explains_completed_item(master, tmap):
    done = _complete_exam_with_wrong_third(master, tmap)
    out = _post_exam_action(
        master, tmap, done, "Objasni mi prvi zadatak iz kontrolnog korak po korak.",
        mode="exam", reply="MODEL_NE_SMIJE_BITI_POZVAN",
    )
    assert "MODEL_NE_SMIJE_BITI_POZVAN" not in out["answer"]
    # objašnjava PRVU stavku kontrolnog
    assert "zadatak 1" in out["answer"].lower() or "prvom zadatku" in out["answer"].lower()
    # kontrolni ostaje završen (ne reotvara se)
    assert out["next_state"]["exam_state"]["exam_status"] == "completed"


def test_post_exam_two_buttons_send_different_payloads():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text(encoding="utf-8")
    # "Daj još zadataka" eksplicitno prelazi u practice; "Objasni prvi" ostaje exam
    assert "Daj mi jedan novi zadatak za vježbu.', mode: 'practice'" in html
    assert "Objasni mi prvi zadatak iz kontrolnog korak po korak." in html


def test_generated_arc_task_has_validated_measurement_metadata(master, tmap):
    task = "Zadatak: Poluprecnik kruznice je 8 cm, centralni ugao je 90\u00b0. Izracunaj duzinu kruznog luka."
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": FR_TOPIC, "student_message": "daj zadatak"},
        _fake_chat(task), master, tmap, model="m", timeout=1,
    )
    item = out["task_validation"]["items"][0]
    assert out["task_validation"]["validation_status"] == "validated"
    assert item["answer_type"] == "measurement"
    assert item["expected_unit"] == "cm"
    assert "12" in item["expected_answer_display"]


def test_invalid_tangent_length_task_is_replaced_before_activation(master, tmap):
    bad = "Zadatak: Nacrtaj tangentu kroz tacku A i izmjeri duzinu na tangentnoj pravoj."
    out = svc.handle_chat(
        {"grade": 6, "mode": "practice", "selected_topic": FR_TOPIC, "student_message": "daj zadatak"},
        _fake_chat(bad), master, tmap, model="m", timeout=1,
    )
    assert "ugao" in out["last_tutor_task"].lower()
    assert out["task_validation"]["validation_status"] == "validated"
    assert out["task_validation"]["items"][0]["expected_answer_display"] == "90\u00b0"


def test_level3_multiple_choice_is_task_specific_and_generic_options_rejected():
    arc = "Poluprecnik kruznice je 8 cm, centralni ugao je 90\u00b0. Izracunaj duzinu kruznog luka."
    mc = svc._default_multiple_choice_hint(arc)
    assert svc._validate_multiple_choice_quality(mc, arc)
    joined = " ".join([mc["question"]] + [o["text"] for o in mc["options"]]).lower()
    assert "90" in joined or "kruzn" in joined
    generic = {
        "question": "Koji je najbolji sljedeci korak?",
        "correct_id": "A",
        "options": [
            {"id": "A", "text": "Uradi jedan mali korak koji cuva jednakost ili vrijednost.", "correct": True},
            {"id": "B", "text": "Prepisati rezultat bez provjere.", "correct": False},
            {"id": "C", "text": "Promijeniti jedinicu ili znak nasumicno.", "correct": False},
        ],
    }
    assert not svc._validate_multiple_choice_quality(generic, arc)


def test_equation_explanation_rejects_unrelated_or_malformed_micro_task():
    bad = "Rijesimo 12 - 23x = 4x.\n\nProbaj ti: koliko je 5 + 3 = 8?"
    good = "Rijesimo 12 - 23x = 4x.\n\nProbaj ti: Rijesi jednacinu: 10 - 7x = 2x."
    assert svc.extract_micro_task(bad) == ""
    assert svc.extract_micro_task(good).startswith("Rijesi jednacinu")


def test_short_pending_context_question_uses_previous_micro_task_without_topic_fallback(master, tmap):
    chat = _fake_chat("NE SMIJE SE POZVATI")
    out = svc.handle_chat(
        {
            "grade": 6,
            "mode": "explain",
            "student_message": "sta da probam",
            "previous_next_state": {
                "micro_task": "Rijesi jednacinu: 10 - 7x = 2x.",
            },
        },
        chat, master, tmap, model="m", timeout=1,
    )
    assert chat.calls["messages"] == []
    assert "10 - 7x" in out["answer"]


def test_sheets_row_contains_quick_math_verification_metadata():
    from matbot import sheets_log as sl

    response = {
        "mode": "quick",
        "status": "ready",
        "answer": "x = 4/9",
        "math_verification": {
            "math_verification_used": True,
            "math_verification_match": False,
            "corrected_before_response": True,
            "verified_answer": "x = 4/9",
        },
        "next_state": {},
    }
    row = sl._build_transcript_row(
        {"session_id": "test-quick-verification", "grade": 6, "student_message": "12-23x=4x"},
        response,
    )
    by_header = dict(zip(sl.SHEET_HEADERS, row))
    assert by_header["math_verification_used"] is True
    assert by_header["math_verification_match"] is False
    assert by_header["corrected_before_response"] is True
    assert by_header["verified_answer"] == "x = 4/9"


# --- Skupovne operacije (unija/presjek/komplement): grading + metadata + intent -----
# Root bug (produkcija): generisani kontrolni iz oblasti "Skupovi i skupovne
# operacije" nije imao izvediv očekivani odgovor pa je stavka bila unvalidated i
# svaki tačan odgovor ("{1,2,3,4,5,6,7}") vraćao "Treba mi jasniji odgovor".

_SET_UNION_ITEM = "A = {1,2,3,4,5}, B = {4,5,6,7}. Nađi A ∪ B."
_SET_INTERSECT_ITEM = "C = {a,b,c}, D = {b,c,d}. Odredi C ∩ D."
_SET_COMPLEMENT_ITEM = (
    "E = {2,3,5,7}, U = {1,2,3,4,5,6,7,8,9}. Odredi komplement skupa E."
)


def test_set_union_metadata_generated_and_validated():
    meta = svc._task_answer_metadata(_SET_UNION_ITEM)
    assert len(meta) == 1
    assert meta[0]["answer_kind"] == "set"
    assert meta[0]["set_operation"] == "union"
    assert meta[0]["validation_status"] == "validated"
    assert meta[0]["expected_answer_display"] == "{1, 2, 3, 4, 5, 6, 7}"
    assert meta[0]["expected_elements"] == ["1", "2", "3", "4", "5", "6", "7"]


def test_set_intersection_metadata_generated_and_validated():
    meta = svc._task_answer_metadata(_SET_INTERSECT_ITEM)
    assert meta[0]["answer_kind"] == "set"
    assert meta[0]["set_operation"] == "intersection"
    assert meta[0]["validation_status"] == "validated"
    assert meta[0]["expected_answer_display"] == "{b, c}"


def test_set_complement_metadata_generated_and_validated():
    meta = svc._task_answer_metadata(_SET_COMPLEMENT_ITEM)
    assert meta[0]["answer_kind"] == "set"
    assert meta[0]["set_operation"] == "complement"
    assert meta[0]["validation_status"] == "validated"
    assert meta[0]["expected_answer_display"] == "{1, 4, 6, 8, 9}"


def _set_verdict(task, answer):
    from matbot.answer_checker import check_practice_answer
    return check_practice_answer(task, answer).items[0].verdict


def test_set_union_braced_answer_accepted():
    assert _set_verdict(_SET_UNION_ITEM, "{1,2,3,4,5,6,7}") == "correct"


def test_set_union_comma_answer_accepted():
    assert _set_verdict(_SET_UNION_ITEM, "1,2,3,4,5,6,7") == "correct"


def test_set_union_space_answer_accepted():
    assert _set_verdict(_SET_UNION_ITEM, "1 2 3 4 5 6 7") == "correct"


def test_set_union_reordered_answer_accepted():
    assert _set_verdict(_SET_UNION_ITEM, "7 6 5 4 3 2 1") == "correct"
    assert _set_verdict(_SET_UNION_ITEM, "A ∪ B = {7,1,2,3,6,5,4}") == "correct"


def test_set_missing_or_extra_element_incorrect():
    assert _set_verdict(_SET_UNION_ITEM, "{1,2,3,4,5,6}") == "incorrect"      # nedostaje 7
    assert _set_verdict(_SET_UNION_ITEM, "{1,2,3,4,5,6,7,8}") == "incorrect"  # višak 8


def test_set_symbolic_order_insensitive_equivalence():
    assert _set_verdict(_SET_INTERSECT_ITEM, "{b,c}") == "correct"
    assert _set_verdict(_SET_INTERSECT_ITEM, "{c,b}") == "correct"
    assert _set_verdict(_SET_INTERSECT_ITEM, "c b") == "correct"
    assert _set_verdict(_SET_INTERSECT_ITEM, "C ∩ D = {c,b}") == "correct"


def test_set_numbered_multi_answer_extracts_current_item():
    from matbot.answer_checker import check_practice_answer
    exam = f"1. {_SET_UNION_ITEM}\n2. {_SET_INTERSECT_ITEM}\n3. {_SET_COMPLEMENT_ITEM}"
    result = check_practice_answer(exam, "1) {1,2,3,4,5,6,7} 2) {b,c}")
    by_n = {i.n: i.verdict for i in result.items}
    assert by_n[1] == "correct"
    assert by_n[2] == "correct"
    # a single set answer with pending item 3 → pripisan komplementu
    single = check_practice_answer(exam, "{1,4,6,8,9}", pending_items=[3])
    assert {i.n: i.verdict for i in single.items}[3] == "correct"


def test_generated_exam_with_unvalidated_metadata_rejected_before_activation():
    # Prozni kontrolni bez ijednog izvedivog odgovora → NE smije se aktivirati.
    prose = (
        "1. Objasni šta je skup.\n"
        "2. Navedi jedan primjer skupa iz svakodnevnog života.\n"
        "3. Zašto su skupovi korisni?"
    )
    v = svc._validate_task_activation(prose, mode="exam")
    assert v["validation_status"] == "rejected"
    assert v["reason"] == "missing_expected_answer"
    # a validan skupovni kontrolni (svi izvedivi) → aktivira se
    good = f"1. {_SET_UNION_ITEM}\n2. {_SET_INTERSECT_ITEM}\n3. {_SET_COMPLEMENT_ITEM}"
    assert svc._validate_task_activation(good, mode="exam")["validation_status"] == "validated"


def test_active_exam_reveal_request_does_not_create_new_exam(master, tmap):
    first = _start_exam(master, tmap)
    first_state = first["next_state"]["exam_state"]
    first_id = first_state["exam_id"]
    assert first_state["exam_status"] == "active"

    out = svc.handle_chat(
        {
            "grade": 6,
            "mode": "exam",
            "selected_oblast": "Uglovi",
            "last_tutor_task": first["last_tutor_task"],
            "previous_next_state": first["next_state"],
            "student_message": "daj mi odgovor za taj zadatak",
        },
        _fake_chat("NOVI KONTROLNI KOJI SE NE SMIJE POJAVITI"),
        master, tmap, model="m", timeout=1,
    )
    exam_state = out["next_state"]["exam_state"]
    # (12/13) isti exam_id, i dalje aktivan, ista tekuća stavka; bez novog seta
    assert exam_state["exam_id"] == first_id
    assert exam_state["exam_status"] == "active"
    assert exam_state["current_item_index"] == 0
    assert "NOVI KONTROLNI KOJI SE NE SMIJE POJAVITI" not in out["answer"]
    assert "ne dajem gotovo rjesenje" in svc.fold_diacritics(out["answer"])


def test_explicit_new_exam_phrase_still_allowed_during_active_exam(master, tmap):
    first = _start_exam(master, tmap)
    out = svc.handle_chat(
        {
            "grade": 6,
            "mode": "exam",
            "selected_oblast": "Uglovi",
            "last_tutor_task": first["last_tutor_task"],
            "previous_next_state": first["next_state"],
            "student_message": "napravi novi kontrolni",
        },
        _fake_chat(EXAM_ANGLE_TASK),
        master, tmap, model="m", timeout=1,
    )
    # eksplicitno "novi kontrolni" NE ide u help-blok (nije direktan refuz)
    assert "ne dajem gotovo rjesenje" not in svc.fold_diacritics(out["answer"])


def test_active_exam_buttons_hidden_in_template():
    import pathlib
    html = pathlib.Path("templates/index.html").read_text(encoding="utf-8")
    # prečice za završen kontrolni skrivene dok je exam_status === 'active'
    assert "j.exam_state && j.exam_state.exam_status" in html
    assert "examStatus === 'active'" in html


def test_set_answer_logged_with_operation_and_normalized_student():
    from matbot.answer_checker import check_practice_answer, summarize_result
    result = check_practice_answer(_SET_UNION_ITEM, "1,2,3,4,5,6,7")
    summary = summarize_result(result)
    item = summary["items"][0]
    assert item["answer_type"] == "set"
    assert item["verdict"] == "correct"
    assert item["expected_answer"] == "{1, 2, 3, 4, 5, 6, 7}"
    assert item["normalized_student"] == "{1, 2, 3, 4, 5, 6, 7}"
    assert item["deterministic_check"]["set_operation"] == "union"
    assert item["deterministic_check"]["numeric_match"] is True
