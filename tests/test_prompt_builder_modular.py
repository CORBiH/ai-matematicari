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
    assert "kompaktan" in up
    assert "rečenica" in up
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


# --- Phase 6.2: nasljeđivanje baznih matematičkih pravila iz prompts.py -----------

# markeri koji dokazuju da je pravi build_system_prompt("6") u system promptu
BASE_MARKERS = (
    "Postavi mi pitanje ili zadatak iz matematike.",   # ne-matematika odbijanje
    "VIZUELNI ZAPIS (PRAVA MATEMATIKA)",               # \frac / $$ pravila
    "\\frac",                                          # razlomci bez kose crte
    "\\cdot",                                          # množenje tačkom
    "RAZREDNA PRAVILA — 6. RAZRED",                    # razredna ograničenja
    "JEDNAČINE I NEJEDNAČINE (5–6. razred)",           # metoda nepoznatog člana
    "TERMINOLOGIJA I JEZIK",                           # uglomjer itd.
)


def _assert_base_rules(system_prompt):
    for marker in BASE_MARKERS:
        assert marker in system_prompt, f"nedostaje bazno pravilo: {marker}"


def test_base_rules_in_ready_prompt(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "grade": 6}, _found(), master)
    _assert_base_rules(res["system_prompt"])


def test_base_rules_in_quick_mode(master):
    # "Samo rezultat" NE smije oslabiti pravila zapisa
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "mode": "quick", "grade": 6}, _found(), master
    )
    _assert_base_rules(res["system_prompt"])


def test_base_rules_in_general_prompt(master):
    res = pb.build_general_tutor_prompt({"student_message": "5-1", "mode": "quick"})
    _assert_base_rules(res["system_prompt"])


def test_base_rules_in_practice_followup(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "interaction_phase": "answering_practice_task",
         "student_message": "6"},
        _found(), master,
    )
    _assert_base_rules(res["system_prompt"])


def test_base_rules_in_fallback_prompt(master):
    res = pb.build_fallback_prompt({"mode": "explain"}, "unknown")
    _assert_base_rules(res["system_prompt"])


def test_no_duplicate_base_prompt_in_builder_source():
    """Bazni prompt živi SAMO u prompts.py — builder ga uvozi, ne kopira."""
    import inspect
    import matbot.prompt_builder as builder
    src = inspect.getsource(builder)
    for sentinel in (
        "VIZUELNI ZAPIS (PRAVA MATEMATIKA)",
        "GLOBALNA PRAVILA ZAPISA",
        "RAZREDNA PRAVILA — 6. RAZRED",
        "GEOMETRIJSKI PROMPT",
    ):
        assert sentinel not in src, f"duplikat baznog prompta u prompt_builder.py: {sentinel}"


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


# --- Phase 7: exam za CIJELU OBLAST (build_exam_oblast_prompt) --------------------

@pytest.fixture(scope="module")
def oblast(master):
    return master["topics_by_id"][TOPIC]["oblast"]


def test_exam_oblast_prompt_ready(master, oblast):
    res = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": oblast,
         "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me."},
        master,
    )
    assert res is not None
    assert res["status"] == "ready"
    assert res["mode"] == "exam"
    assert res["final_topic"] == "unknown"       # pravilo 10: bez izmišljene teme
    assert res["exam_oblast"] == oblast
    up = res["user_prompt"]
    assert "OBLAST KONTROLNOG" in up
    assert "KONTROLNI IZ OBLASTI" in up
    # sve READY teme te oblasti su navedene (display_name), ništa izmišljeno
    rows = pb.get_oblast_topics(oblast, master)
    assert rows
    for row in rows:
        assert (row.get("display_name") or row["topic"]) in up
    assert "NE izmišljaj" in up


def test_exam_oblast_includes_controlni_material(master, oblast, topic_row):
    res = pb.build_exam_oblast_prompt({"mode": "exam", "selected_oblast": oblast}, master)
    up = res["user_prompt"]
    # kontrolni materijal teme iz te oblasti ulazi u prompt (stvarne ćelije sheeta)
    for field in ("controlni_task_1", "controlni_trick", "controlni_warning"):
        if topic_row.get(field):
            assert topic_row[field] in up


def test_exam_oblast_case_insensitive_and_canonical(master, oblast):
    res = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": oblast.upper()}, master
    )
    assert res is not None
    assert res["exam_oblast"] == oblast          # kanonski naziv iz mastera


def test_exam_oblast_none_when_not_applicable(master, oblast):
    # non-exam mod → None
    assert pb.build_exam_oblast_prompt(
        {"mode": "practice", "selected_oblast": oblast}, master) is None
    # selected_topic ima prednost (postojeći topic-based exam netaknut) → None
    assert pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_topic": TOPIC, "selected_oblast": oblast}, master) is None
    # nepostojeća/prazna oblast → None (pada na postojeći exam fallback)
    assert pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": "nepostojeca_oblast_xyz"}, master) is None
    assert pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": ""}, master) is None


def test_exam_oblast_uses_base_prompt_and_guidelines(master, oblast):
    res = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": oblast, "grade": 6}, master
    )
    assert "MODULARNA PRAVILA" in res["system_prompt"]


def test_get_oblast_topics_unknown_empty(master):
    assert pb.get_oblast_topics("nema_takve_oblasti", master) == []
    assert pb.get_oblast_topics("", master) == []


# --- Phase 7.1: FORMAT ODGOVORA (CHAT) — kompaktno formatiranje -------------------

def test_chat_formatting_in_all_modular_paths(master, oblast):
    """Format blok ide POSLIJE baznog prompta u SVIM modularnim putanjama."""
    results = [
        # selected_topic ready
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master),
        # thinkific_lesson
        pb.build_tutor_prompt(
            {"entry_source": "thinkific_lesson", "mode": "explain"},
            _found(source="composite"), master),
        # free_chat detected_topic
        pb.build_tutor_prompt(
            {"detected_topic": TOPIC}, _found(source="detected_topic"), master),
        # practice + practice follow-up
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "practice"}, _found(), master),
        pb.build_tutor_prompt(
            {"selected_topic": TOPIC, "interaction_phase": "answering_practice_task"},
            _found(), master),
        # exam + quick
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "exam"}, _found(), master),
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "quick"}, _found(), master),
        # free_chat unknown/general
        pb.build_general_tutor_prompt({"mode": "quick", "student_message": "5-1"}),
        # fallback
        pb.build_fallback_prompt({"mode": "exam"}, "unknown"),
        # exam-by-oblast
        pb.build_exam_oblast_prompt({"mode": "exam", "selected_oblast": oblast}, master),
    ]
    for res in results:
        sp = res["system_prompt"]
        assert "FORMAT ODGOVORA (CHAT)" in sp
        assert "KOMPAKTNO" in sp


def test_formatting_block_comes_after_base_prompt(master):
    sp = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "grade": 6}, _found(), master
    )["system_prompt"]
    # bazna matematička pravila ostaju i dolaze PRIJE chat format pravila
    assert sp.index("MODULARNA PRAVILA") < sp.index("FORMAT ODGOVORA (CHAT)")


def test_formatting_inline_vs_display_rules(master):
    sp = pb.build_tutor_prompt({"selected_topic": TOPIC}, _found(), master)["system_prompt"]
    # kratki izrazi → inline \( ... \)
    assert r"INLINE matematikom \( ... \)" in sp
    # display $$...$$ rezervisan za važan višekoračni račun
    assert "SAMO za važan višekoračni račun" in sp
    # bez sirovih markdown naslova; kratke oznake u redu
    assert "NE koristi sirove markdown naslove" in sp
    assert '"Ideja:"' in sp and '"Zaključak:"' in sp
    # numerisane liste bez ponavljanja "1."
    assert 'NE počinji svaku stavku ponovo sa "1."' in sp


def test_formatting_divisibility_rules(master):
    sp = pb.build_tutor_prompt({"selected_topic": TOPIC}, _found(), master)["system_prompt"]
    assert "izbjegavaj izolovan zapis poput 6|12" in sp
    assert "6 dijeli 12, jer je 12 : 6 = 2." in sp
    assert r"\(6 \mid 12\)" in sp
    assert "ne prekidaj rečenicu oko simbola djeljivosti" in sp


def test_quick_mode_compact_result_only():
    block = pb.build_mode_instructions("quick", "unknown", {})
    assert "KOMPAKTAN" in block
    assert "SAMO rezultat" in block
    assert "JEDNA kratka" in block


def test_explain_mode_short_unless_asked():
    block = pb.build_mode_instructions("explain", "unknown", {})
    assert "detaljno objašnjavaj SAMO ako učenik to izričito zatraži" in block


def test_exam_modes_clean_task_format(master, oblast):
    block = pb.build_mode_instructions("exam", TOPIC, pb.get_topic_context(TOPIC, master))
    assert '"Trik:"' in block and '"Upozorenje:"' in block
    res = pb.build_exam_oblast_prompt({"mode": "exam", "selected_oblast": oblast}, master)
    assert '"Trik:"' in res["user_prompt"] and '"Upozorenje:"' in res["user_prompt"]


def test_followup_compact_feedback_no_topic_restart():
    block = pb.build_practice_followup_instructions({}, {})
    assert "KRATAK i prirodan za chat" in block
    assert "NE ponavljaj cijelo objašnjenje teme" in block
