"""Testovi za matbot.prompt_builder (Phase 2).

Koristi stvarni master (commitovani .xlsx) i punu temu ``skupovi_uvod``.
Tvrdi na STVARNIM vrijednostima ćelija (povučenim iz sheeta), ne na hardkodiranom
tekstu — pa ostaje ispravno i ako se sadržaj kasnije koriguje. Bez mreže/OpenAI-ja.
"""
import pytest

from matbot import content_loader as cl
from matbot import prompt_builder as pb

TOPIC = "skupovi_uvod"


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def topic_row(master):
    return master["topics_by_id"][TOPIC]


def _found(topic=TOPIC, source="selected_topic"):
    return {
        "final_topic": topic,
        "status": "found",
        "source": source,
        "message": "",
        "matches": [],
    }


def _lookup(status, topic="unknown"):
    return {
        "final_topic": topic,
        "status": status,
        "source": "fallback",
        "message": "",
        "matches": [],
    }


def _text(res):
    return res["system_prompt"] + "\n" + res["user_prompt"]


# --- 1–3: topic context se ubacuje ----------------------------------------------

def test_includes_lesson_scope_and_hint_method(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master)
    text = _text(res)
    assert res["status"] == "ready"
    assert res["topic_context_used"] is True
    assert topic_row["lesson_scope"] in text
    assert topic_row["hint_method"] in text


def test_includes_common_mistakes_and_ai_if_mistake(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC}, _found(), master)
    text = _text(res)
    assert topic_row["common_mistake_1"] in text
    assert topic_row["ai_if_mistake_1"] in text


def test_includes_solved_example(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC}, _found(), master)
    text = _text(res)
    assert topic_row["solved_example_problem"] in text
    assert topic_row["solved_example_answer"] in text


# --- 4–8: mode rules ------------------------------------------------------------

def test_explain_mode_idea_and_steps(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master)
    up = res["user_prompt"].lower()
    assert res["mode"] == "explain"
    assert "ideja" in up
    assert "2" in up and "5" in up
    assert "korak" in up


def test_practice_mode_one_task_and_wait(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "practice"}, _found(), master)
    up = res["user_prompt"].lower()
    assert res["mode"] == "practice"
    assert "jedan zadatak" in up
    assert "čekaj" in up


def test_exam_mode_known_topic_three_tasks_trick_warning(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "exam"}, _found(), master)
    text = _text(res)
    assert res["mode"] == "exam"
    for key in (
        "controlni_task_1",
        "controlni_task_2",
        "controlni_task_3",
        "controlni_trick",
        "controlni_warning",
    ):
        assert topic_row[key] in text


def test_exam_mode_unknown_topic_asks_for_area(master):
    res = pb.build_tutor_prompt({"mode": "exam"}, _lookup("unknown"), master)
    up = res["user_prompt"].lower()
    assert res["status"] == "fallback"
    assert "oblast" in up
    assert "kontrolni" in up


def test_quick_mode_short(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "quick"}, _found(), master)
    up = res["user_prompt"].lower()
    assert res["mode"] == "quick"
    assert "maksimalno" in up
    assert "rečenice" in up
    assert "rezultat" in up


# --- 9: history limit -----------------------------------------------------------

def test_trim_conversation_history_unit():
    hist = [{"role": "user", "content": f"MSG{i}"} for i in range(8)]
    assert pb.trim_conversation_history(hist) == hist[-5:]
    assert pb.trim_conversation_history(None) == []
    assert pb.trim_conversation_history("nope") == []
    assert pb.trim_conversation_history([1, 2, 3], limit=2) == [2, 3]
    assert pb.trim_conversation_history([1, 2, 3], limit=0) == []


def test_conversation_history_limited_in_prompt(master):
    hist = [{"role": "user", "content": f"MSG{i}"} for i in range(8)]
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "conversation_history": hist}, _found(), master
    )
    up = res["user_prompt"]
    assert "MSG7" in up and "MSG3" in up  # zadnjih 5 = MSG3..MSG7
    assert "MSG2" not in up and "MSG0" not in up


# --- 10: forbidden behavior -----------------------------------------------------

def test_forbidden_ai_behavior_included(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC}, _found(), master)
    assert topic_row["forbidden_ai_behavior"] in _text(res)


# --- 11–13: fallback / ne izmišljaj ---------------------------------------------

def test_unknown_topic_produces_fallback(master):
    res = pb.build_tutor_prompt({"mode": "explain"}, _lookup("unknown"), master)
    assert res["status"] == "fallback"
    assert res["topic_context_used"] is False
    assert res["video_flow_used"] is False
    assert res["final_topic"] == "unknown"


def test_ambiguous_lookup_asks_manual_selection(master):
    res = pb.build_tutor_prompt({}, _lookup("ambiguous"), master)
    up = res["user_prompt"].lower()
    assert res["status"] == "ambiguous"
    assert "više" in up
    assert "oblast" in up or "izabere" in up


def test_does_not_invent_topic_when_unknown(master):
    res = pb.build_tutor_prompt({}, _lookup("unknown"), master)
    assert res["final_topic"] == "unknown"
    assert res["effective_topic"] == "unknown"
    assert res["topic_context_used"] is False


def test_invalid_status_produces_invalid_fallback(master):
    res = pb.build_tutor_prompt({"selected_topic": "nepostoji"}, _lookup("invalid"), master)
    assert res["status"] == "invalid"
    assert res["topic_context_used"] is False
    assert "izmišljaj" in res["user_prompt"].lower()


# --- 14: VIDEO_FLOW samo za thinkific_lesson ------------------------------------

def test_video_flow_included_only_for_thinkific_lesson(master):
    vf_rows = [r for r in (master["video_flow"] or []) if r.get("topic") == TOPIC]
    assert vf_rows, "očekivani VIDEO_FLOW redovi za temu"
    vf = vf_rows[0]

    payload = {
        "entry_source": "thinkific_lesson",
        "lesson_title": vf["lesson_title"],
        "lesson_order": vf["lesson_order"],
        "course_name": "Matematika 6",
        "section_name": "Skupovi",
    }
    res = pb.build_tutor_prompt(payload, _found(source="composite"), master)
    assert res["video_flow_used"] is True
    if vf.get("sta_ucenik_upravo_naucio"):
        assert vf["sta_ucenik_upravo_naucio"] in res["user_prompt"]

    # ručni izbor teme → nema VIDEO_FLOW konteksta
    res2 = pb.build_tutor_prompt(
        {"entry_source": "manual_topic_choice", "selected_topic": TOPIC}, _found(), master
    )
    assert res2["video_flow_used"] is False


def test_video_flow_context_helper_requires_thinkific(master):
    assert pb.get_video_flow_context({"entry_source": "free_chat"}, TOPIC, master) is None
    assert pb.get_video_flow_context({"entry_source": "thinkific_lesson"}, "unknown", master) is None


# --- normalize_mode -------------------------------------------------------------

def test_normalize_mode():
    assert pb.normalize_mode("explain") == "explain"
    assert pb.normalize_mode("PRACTICE") == "practice"
    assert pb.normalize_mode("Objasni mi") == "explain"
    assert pb.normalize_mode("Sutra imam kontrolni") == "exam"
    assert pb.normalize_mode("samo rezultat") == "quick"
    assert pb.normalize_mode("") == "explain"
    assert pb.normalize_mode(None) == "explain"
    assert pb.normalize_mode("gibberish") == "explain"


# --- get_topic_context ----------------------------------------------------------

def test_get_topic_context_unknown_returns_empty(master):
    assert pb.get_topic_context("unknown", master) == {}
    assert pb.get_topic_context("nepostoji_xyz", master) == {}
    assert pb.get_topic_context("", master) == {}


def test_get_topic_context_has_all_fields(master):
    ctx = pb.get_topic_context(TOPIC, master)
    for f in pb.TOPIC_CONTEXT_FIELDS:
        assert f in ctx


# --- topic conflict (guidelines §9) ---------------------------------------------

def test_topic_conflict_uses_detected_topic(master):
    detected = "razlomci_pojam_vrste"
    payload = {
        "entry_source": "thinkific_lesson",
        "detected_topic": detected,
        "course_name": "Matematika 6",
        "section_name": "Skupovi",
        "lesson_order": 1,
        "lesson_title": "Osnove za naučiti",
    }
    res = pb.build_tutor_prompt(payload, _found(TOPIC, "composite"), master)
    assert res["topic_conflict"] is True
    assert res["opened_lesson_topic"] == TOPIC
    assert res["effective_topic"] == detected
    assert res["final_topic"] == detected
    # koristi se kontekst DETEKTOVANE teme
    assert master["topics_by_id"][detected]["lesson_scope"] in res["user_prompt"]
    assert "neslag" in res["user_prompt"].lower()


def test_no_conflict_when_detected_invalid(master):
    payload = {
        "entry_source": "thinkific_lesson",
        "detected_topic": "izmisljeno_123",
        "course_name": "Matematika 6",
        "section_name": "Skupovi",
        "lesson_order": 1,
        "lesson_title": "Osnove za naučiti",
    }
    res = pb.build_tutor_prompt(payload, _found(TOPIC, "composite"), master)
    assert res["topic_conflict"] is False
    assert res["final_topic"] == TOPIC


def test_no_conflict_when_detected_equals_lesson(master):
    payload = {"entry_source": "thinkific_lesson", "detected_topic": TOPIC}
    res = pb.build_tutor_prompt(payload, _found(TOPIC, "composite"), master)
    assert res["topic_conflict"] is False
    assert res["final_topic"] == TOPIC


# --- practice follow-up (Phase 4.3) ---------------------------------------------

def test_practice_followup_instructions_included(master):
    payload = {
        "selected_topic": TOPIC,
        "mode": "practice",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Da li je 2∈S ako je S={1,2,3}?",
        "student_message": "da",
    }
    res = pb.build_tutor_prompt(payload, _found(), master)
    up = res["user_prompt"]
    assert "PROVJERA ODGOVORA" in up
    assert "Da li je 2∈S ako je S={1,2,3}?" in up          # last_tutor_task u promptu
    assert "ODGOVOR na prethodno postavljeni zadatak" in up
    assert res["mode"] == "practice"


def test_practice_followup_does_not_restart_task(master):
    payload = {
        "selected_topic": TOPIC,
        "mode": "practice",
        "interaction_phase": "answering_practice_task",
        "student_message": "6",
    }
    res = pb.build_tutor_prompt(payload, _found(), master)
    up = res["user_prompt"]
    # follow-up NE smije instruisati novi svježi zadatak
    assert "Daj TAČNO JEDAN zadatak i onda ČEKAJ" not in up
    assert "NE počinji isti zadatak ispočetka" in up


def test_practice_followup_forces_practice_mode(master):
    # čak i bez mode polja, follow-up ide kao practice
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "interaction_phase": "answering_practice_task",
         "student_message": "6"},
        _found(), master,
    )
    assert res["mode"] == "practice"


def test_practice_followup_truncates_long_task(master):
    long_task = "x" * 2000
    txt = pb.build_practice_followup_instructions(
        {"last_tutor_task": long_task}, {}
    )
    assert "x" * 600 in txt
    assert "x" * 601 not in txt


# --- struktura rezultata / guardrails -------------------------------------------

def test_result_shape_keys(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC}, _found(), master)
    for key in (
        "system_prompt",
        "user_prompt",
        "mode",
        "final_topic",
        "opened_lesson_topic",
        "effective_topic",
        "status",
        "topic_context_used",
        "video_flow_used",
        "topic_conflict",
    ):
        assert key in res
    # globalne modularne smjernice su uvijek prisutne
    assert "MODULARNA PRAVILA" in res["system_prompt"]


def test_build_fallback_prompt_direct(master):
    res = pb.build_fallback_prompt({"mode": "quick"}, "unknown")
    assert res["status"] == "fallback"
    assert res["final_topic"] == "unknown"
    assert res["topic_context_used"] is False
