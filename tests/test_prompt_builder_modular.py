"""Testovi za matbot.prompt_builder (NPP schema).

NPP_TOPICS nema bogate pedagoške kolone (riješeni primjeri, kontrolni zadaci,
česte greške) — sadržaj generiše model iz naziva teme (tema_ui) + npp_scope
(pravilo R5). Zato se ovdje tvrdi na: minimalnom topic bloku (naziv/oblast/scope),
NPP video preporuci (VIDEO_LINKS), mode instrukcijama i tutor system-prompt stacku.
Bez mreže/OpenAI-ja; koristi stvarne commitovane NPP fajlove.
"""
import pytest

from matbot import content_loader as cl
from matbot import prompt_builder as pb

TOPIC = "6-01-001"            # Skupovi i skupovne operacije → Pojam skupa (ima video)
TOPIC_RAZLOMCI = "6-04-031"   # Razlomci → Pojam razlomka (za konflikt teme)


@pytest.fixture(scope="module")
def master():
    return cl.load_master_content(grade=6)


@pytest.fixture(scope="module")
def master7():
    return cl.load_master_content(grade=7)


@pytest.fixture(scope="module")
def master8():
    return cl.load_master_content(grade=8)


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


# --- 1: minimalni topic kontekst (naziv/oblast/npp_scope) -----------------------

def test_includes_topic_name_oblast_scope(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master)
    text = _text(res)
    assert res["status"] == "ready"
    assert res["topic_context_used"] is True
    assert topic_row["display_name"] in text
    assert topic_row["oblast"] in text
    if topic_row.get("npp_scope"):
        assert topic_row["npp_scope"] in text


def test_topic_block_has_no_rich_legacy_fields(master):
    """NPP nema bogata polja — blok ne smije izmišljati riješene primjere/kontrolne."""
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master)
    up = res["user_prompt"]
    for label in ("Riješeni primjer", "Kontrolni zadatak", "Tipičan zadatak",
                  "Metoda hinta", "Česta greška"):
        assert label not in up, label


# --- 2: NPP video preporuka (VIDEO_LINKS) ---------------------------------------

def test_explain_offers_video_when_available(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master)
    assert res["video_recommended"] is True
    up = res["user_prompt"]
    assert "VIDEO LEKCIJA" in up
    # naziv lekcije iz VIDEO_LINKS, bez izmišljenog URL-a
    vids = master["videos_by_topic"][TOPIC]
    assert vids[0]["lesson_title"] in up
    assert "NE izmišljaj URL" in up


def test_practice_does_not_offer_video_unless_stuck(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "practice"}, _found(), master)
    assert res["video_recommended"] is False
    assert "VIDEO LEKCIJA" not in res["user_prompt"]


def test_practice_offers_video_when_stuck(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "mode": "practice", "_student_stuck": True},
        _found(), master,
    )
    assert res["video_recommended"] is True
    assert "UČENIK JE ZAPEO" in res["user_prompt"]


def test_no_video_flag_for_topic_without_video(master):
    # tema bez videa: nađi je iz mastera (data-driven)
    no_vid = next(
        t["topic"] for t in master["topics"]
        if not master["videos_by_topic"].get(t["topic"])
    )
    res = pb.build_tutor_prompt({"selected_topic": no_vid, "mode": "explain"}, _found(no_vid), master)
    assert res["video_recommended"] is False
    assert "VIDEO LEKCIJA" not in res["user_prompt"]


def test_get_video_recommendation_helper(master):
    assert pb.get_video_recommendation(TOPIC, master) == master["videos_by_topic"][TOPIC]
    assert pb.get_video_recommendation("unknown", master) == []
    assert pb.get_video_recommendation("nepostoji", master) == []


# --- 3: mode rules --------------------------------------------------------------

def test_explain_mode_idea_and_steps(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master)
    up = res["user_prompt"].lower()
    assert res["mode"] == "explain"
    assert "primjer" in up
    assert "korak" in up or "ideja" in up


def test_practice_mode_one_task_and_wait(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "practice"}, _found(), master)
    up = res["user_prompt"].lower()
    assert res["mode"] == "practice"
    assert "jedan zadatak" in up
    assert "čekaj" in up


def test_exam_mode_known_topic_three_tasks_trick_warning(master):
    """Exam generiše 3 zadatka + trik + upozorenje iz naziva teme (bez controlni_*)."""
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "exam"}, _found(), master)
    up = res["user_prompt"]
    assert res["mode"] == "exam"
    assert "3" in up
    assert '"Trik:"' in up and '"Upozorenje:"' in up


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


# --- history --------------------------------------------------------------------

def test_trim_conversation_history_unit():
    hist = [{"role": "user", "content": f"MSG{i}"} for i in range(8)]
    assert pb.trim_conversation_history(hist) == hist[-5:]
    assert pb.trim_conversation_history(None) == []
    assert pb.trim_conversation_history("nope") == []
    assert pb.trim_conversation_history([1, 2, 3], limit=2) == [2, 3]
    assert pb.trim_conversation_history([1, 2, 3], limit=0) == []


def test_conversation_history_as_role_messages(master):
    hist = [{"role": "user", "content": f"MSG{i}"} for i in range(8)]
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "conversation_history": hist}, _found(), master
    )
    hm = res["history_messages"]
    assert [m["content"] for m in hm] == ["MSG3", "MSG4", "MSG5", "MSG6", "MSG7"]
    assert all(m["role"] == "user" for m in hm)
    assert "MSG7" not in res["user_prompt"]
    assert "ZADNJE PORUKE" not in res["user_prompt"]


def test_history_messages_role_mapping():
    hm = pb.build_history_messages([
        {"role": "user", "content": "ispada iz reza (6. od kraja)"},
        {"role": "assistant", "content": "4. Hoćeš još jedan?"},
        {"role": "bot", "content": "i bot je assistant"},
        {"role": "čudno", "content": "nepoznata rola je user"},
        {"role": "user", "content": ""},
        "goli string je user poruka",
    ])
    assert [m["role"] for m in hm] == ["assistant", "assistant", "user", "user"]
    assert hm[0]["content"] == "4. Hoćeš još jedan?"
    assert hm[-1]["content"] == "goli string je user poruka"
    assert pb.build_history_messages(None) == []
    assert pb.build_history_messages("nije lista") == []


# --- fallback / ne izmišljaj ----------------------------------------------------

def test_unknown_topic_produces_fallback(master):
    res = pb.build_tutor_prompt({"mode": "explain"}, _lookup("unknown"), master)
    assert res["status"] == "fallback"
    assert res["topic_context_used"] is False
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


# --- video_flow shim (thinkific_lesson entry nije dio NPP MVP-a) -----------------

def test_video_flow_context_helper_returns_none(master):
    # NPP nema VIDEO_FLOW sheet → uvijek None (video ide kroz VIDEO_LINKS)
    assert pb.get_video_flow_context({"entry_source": "free_chat"}, TOPIC, master) is None
    assert pb.get_video_flow_context({"entry_source": "thinkific_lesson"}, TOPIC, master) is None
    assert master["video_flow"] is None


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


def test_get_topic_context_has_meta(master, topic_row):
    ctx = pb.get_topic_context(TOPIC, master)
    assert ctx["topic"] == TOPIC
    assert ctx["display_name"] == topic_row["display_name"]
    assert ctx["oblast"] == topic_row["oblast"]
    # legacy polja postoje kao ključevi (prazna) radi kompatibilnosti bloka
    for f in pb.TOPIC_CONTEXT_FIELDS:
        assert f in ctx


# --- topic conflict (guidelines §9) ---------------------------------------------

def test_topic_conflict_uses_detected_topic(master):
    detected = TOPIC_RAZLOMCI
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
    # koristi se kontekst DETEKTOVANE teme (njen naziv)
    assert master["topics_by_id"][detected]["display_name"] in res["user_prompt"]
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


# --- practice follow-up ---------------------------------------------------------

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
    assert "Da li je 2∈S ako je S={1,2,3}?" in up
    assert "The student is responding to this exact previous task:" in up
    # BUG 2 (2026-07-10): poslije tačnog odgovora slijedi novi zadatak u istoj poruci
    assert "ODMAH daj JEDAN novi zadatak" in up
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
    assert "Daj TAČNO JEDAN zadatak i onda ČEKAJ" not in up
    assert "NE počinji isti zadatak ispočetka" in up


def test_practice_followup_forces_practice_mode(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "interaction_phase": "answering_practice_task",
         "student_message": "6"},
        _found(), master,
    )
    assert res["mode"] == "practice"


def test_practice_followup_truncates_long_task(master):
    long_task = "x" * 2000
    txt = pb.build_practice_followup_instructions({"last_tutor_task": long_task}, {})
    assert "x" * 600 in txt
    assert "x" * 601 not in txt


# --- tutor system prompt stack (matbot.tutor_prompts) ---------------------------

TUTOR_MARKERS_G6 = (
    "Postavi mi pitanje ili zadatak iz matematike.",
    "MODULARNA PRAVILA",
    "JEZIK I TON (TUTOR)",
    "DIDAKTIKA — 6. RAZRED",
    "TERMINOLOGIJA I ZAPIS",
    "FORMAT ODGOVORA (CHAT)",
    "\\frac",
    "\\cdot",
)


def _assert_tutor_rules(system_prompt, grade="6"):
    for marker in TUTOR_MARKERS_G6:
        if grade != "6" and marker == "DIDAKTIKA — 6. RAZRED":
            marker = f"DIDAKTIKA — {grade}. RAZRED"
        assert marker in system_prompt, f"nedostaje pravilo novog stacka: {marker}"


def test_tutor_rules_in_ready_prompt(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "grade": 6}, _found(), master)
    _assert_tutor_rules(res["system_prompt"])


def test_tutor_rules_in_quick_mode(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "mode": "quick", "grade": 6}, _found(), master
    )
    _assert_tutor_rules(res["system_prompt"])


def test_tutor_rules_in_general_prompt(master):
    res = pb.build_general_tutor_prompt({"student_message": "5-1", "mode": "quick"})
    _assert_tutor_rules(res["system_prompt"])


def test_tutor_rules_in_practice_followup(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "interaction_phase": "answering_practice_task",
         "student_message": "6"},
        _found(), master,
    )
    _assert_tutor_rules(res["system_prompt"])


def test_tutor_rules_in_fallback_prompt(master):
    res = pb.build_fallback_prompt({"mode": "explain"}, "unknown")
    _assert_tutor_rules(res["system_prompt"])


def test_grade6_prompt_excludes_higher_grade_rules(master):
    sp = pb.build_tutor_prompt({"selected_topic": TOPIC, "grade": 6}, _found(), master)["system_prompt"]
    assert "DIDAKTIKA — 6. RAZRED" in sp
    assert "kratke rečenice" in sp
    for higher in ("DIDAKTIKA — 7. RAZRED", "DIDAKTIKA — 8. RAZRED", "DIDAKTIKA — 9. RAZRED"):
        assert higher not in sp


def test_grade7_prompt_has_grade7_rules_only(master7):
    payload = {"selected_topic": "7-01-001", "grade": 7}
    res = pb.build_tutor_prompt(payload, _found("7-01-001"), master7)
    sp = res["system_prompt"]
    assert "DIDAKTIKA — 7. RAZRED" in sp
    assert "DIDAKTIKA — 6. RAZRED" not in sp
    assert "DIDAKTIKA — 8. RAZRED" not in sp


def test_grade8_prompt_has_grade8_rules_only(master8):
    payload = {"selected_topic": "8-01-001", "grade": 8}
    res = pb.build_tutor_prompt(payload, _found("8-01-001"), master8)
    sp = res["system_prompt"]
    assert "DIDAKTIKA — 8. RAZRED" in sp
    assert "DIDAKTIKA — 6. RAZRED" not in sp
    assert "DIDAKTIKA — 7. RAZRED" not in sp


def test_constructions_only_when_topic_requires(master, master7):
    # obična tema (skupovi) → bez konstrukcijskog bloka
    sp_plain = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "grade": 6}, _found(), master
    )["system_prompt"]
    assert "KONSTRUKCIJE (ZA OVU TEMU)" not in sp_plain

    # stvarna konstrukcijska tema iz NPP 7. razreda (naziv sadrži "konstrukcij")
    row = next(
        r for r in master7["topics"]
        if "konstrukcij" in (r.get("oblast", "") + " " + r.get("display_name", "")).lower()
    )
    res = pb.build_tutor_prompt(
        {"selected_topic": row["topic"], "grade": 7}, _found(row["topic"]), master7
    )
    assert "KONSTRUKCIJE (ZA OVU TEMU)" in res["system_prompt"]


# --- struktura rezultata --------------------------------------------------------

def test_result_shape_keys(master):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC}, _found(), master)
    for key in (
        "system_prompt", "user_prompt", "mode", "final_topic", "opened_lesson_topic",
        "effective_topic", "status", "topic_context_used", "video_flow_used",
        "video_recommended", "topic_conflict",
    ):
        assert key in res
    assert "MODULARNA PRAVILA" in res["system_prompt"]


def test_build_fallback_prompt_direct(master):
    res = pb.build_fallback_prompt({"mode": "quick"}, "unknown")
    assert res["status"] == "fallback"
    assert res["final_topic"] == "unknown"
    assert res["topic_context_used"] is False


# --- exam za CIJELU OBLAST (build_exam_oblast_prompt) ----------------------------

@pytest.fixture(scope="module")
def oblast(master, topic_row):
    return topic_row["oblast"]


def test_exam_oblast_prompt_ready(master, oblast):
    res = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": oblast,
         "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me."},
        master,
    )
    assert res is not None
    assert res["status"] == "ready"
    assert res["mode"] == "exam"
    assert res["final_topic"] == "unknown"
    assert res["exam_oblast"] == oblast
    up = res["user_prompt"]
    assert "OBLAST KONTROLNOG" in up
    assert "KONTROLNI IZ OBLASTI" in up
    # sve teme te oblasti navedene po nazivu, ništa izmišljeno
    rows = pb.get_oblast_topics(oblast, master)
    assert rows
    for row in rows:
        assert (row.get("display_name") or row["topic"]) in up
    assert "NE izmišljaj" in up


def test_exam_oblast_case_insensitive_and_canonical(master, oblast):
    res = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": oblast.upper()}, master
    )
    assert res is not None
    assert res["exam_oblast"] == oblast


def test_exam_oblast_none_when_not_applicable(master, oblast):
    assert pb.build_exam_oblast_prompt(
        {"mode": "practice", "selected_oblast": oblast}, master) is None
    assert pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_topic": TOPIC, "selected_oblast": oblast}, master) is None
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


# --- FORMAT ODGOVORA (CHAT) — kompaktno formatiranje ----------------------------

def test_chat_formatting_in_all_modular_paths(master, oblast):
    results = [
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master),
        pb.build_tutor_prompt(
            {"detected_topic": TOPIC}, _found(source="detected_topic"), master),
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "practice"}, _found(), master),
        pb.build_tutor_prompt(
            {"selected_topic": TOPIC, "interaction_phase": "answering_practice_task"},
            _found(), master),
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "exam"}, _found(), master),
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "quick"}, _found(), master),
        pb.build_general_tutor_prompt({"mode": "quick", "student_message": "5-1"}),
        pb.build_fallback_prompt({"mode": "exam"}, "unknown"),
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
    assert sp.index("MODULARNA PRAVILA") < sp.index("FORMAT ODGOVORA (CHAT)")


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
    # BUG 2 (2026-07-10): tačan odgovor → odmah novi zadatak ("Zadatak: ...")
    assert "ODMAH daj JEDAN novi zadatak" in block
    assert 'Zadatak: ...' in block


# --- nastavak razgovora + robusniji practice follow-up --------------------------

def test_continuation_instructions_block():
    block = pb.build_continuation_instructions(
        {"last_tutor_message": "NZS je najmanji zajednički sadržilac. Hoćeš primjer?"}
    )
    assert "NASTAVAK RAZGOVORA" in block
    assert "NE tretiraj je kao novi zahtjev" in block
    assert "JEDAN konkretan primjer" in block
    assert "Hoćeš primjer?" in block


def test_continuation_truncates_long_message():
    block = pb.build_continuation_instructions({"last_tutor_message": "x" * 2000})
    assert "x" * 600 in block
    assert "x" * 601 not in block


def test_tutor_prompt_continuation_replaces_explain_block(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "mode": "explain",
         "interaction_phase": "continuing_explanation",
         "student_message": "može",
         "last_tutor_message": "Hoćeš da zajedno riješimo primjer?"},
        _found(), master,
    )
    up = res["user_prompt"]
    assert res["status"] == "ready"
    assert "NASTAVAK RAZGOVORA" in up
    assert "Hoćeš da zajedno riješimo primjer?" in up
    assert "MOD: OBJASNI" not in up


def test_general_prompt_continuation(master):
    res = pb.build_general_tutor_prompt(
        {"mode": "explain", "interaction_phase": "continuing_explanation",
         "student_message": "nastavi", "last_tutor_message": "Prvi korak je..."}
    )
    assert "NASTAVAK RAZGOVORA" in res["user_prompt"]
    assert "MOD: OBJASNI" not in res["user_prompt"]


def test_general_prompt_uses_last_image_context():
    res = pb.build_general_tutor_prompt(
        {
            "mode": "explain",
            "student_message": "Objasni prvi zadatak.",
            "last_image_context": "1. Izračunaj 2 + 3.\n2. Izračunaj 4 + 5.",
        }
    )
    up = res["user_prompt"]
    assert "KONTEKST ZADNJE SLIKE" in up
    assert "1. Izračunaj 2 + 3." in up
    assert "sačuvaj originalnu numeraciju" in up


def test_exam_oblast_continuation(master, oblast):
    res = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": oblast,
         "interaction_phase": "continuing_explanation",
         "student_message": "može", "last_tutor_message": "Hoćeš da počnemo s prvim?"},
        master,
    )
    up = res["user_prompt"]
    assert "NASTAVAK RAZGOVORA" in up
    assert "KONTROLNI IZ OBLASTI" not in up
    assert "OBLAST KONTROLNOG" in up


def test_general_prompt_practice_followup():
    res = pb.build_general_tutor_prompt(
        {"interaction_phase": "answering_practice_task",
         "last_tutor_task": "Koliko je 2+2?", "student_message": "4"}
    )
    assert res["mode"] == "practice"
    assert "PROVJERA ODGOVORA" in res["user_prompt"]
    assert "Koliko je 2+2?" in res["user_prompt"]


def test_practice_answer_not_treated_as_new_request(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "interaction_phase": "answering_practice_task",
         "last_tutor_task": "Da li je 2 element skupa S?", "student_message": "da"},
        _found(), master,
    )
    up = res["user_prompt"]
    assert res["mode"] == "practice"
    assert "PROVJERA ODGOVORA" in up
    assert "MOD: VJEŽBAJ (practice)" not in up


def test_explain_mode_conversational_not_repetitive(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "mode": "explain"}, _found(), master
    )
    up = res["user_prompt"]
    assert "razgovoran" in up
    assert "NE prepričavaj cijelu lekciju" in up


# --- jezik i ton u SVIM modularnim system promptovima ---------------------------

def test_language_tone_in_all_modular_paths(master, oblast):
    results = [
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master),
        pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "quick"}, _found(), master),
        pb.build_general_tutor_prompt({"mode": "quick", "student_message": "5-1"}),
        pb.build_fallback_prompt({"mode": "exam"}, "unknown"),
        pb.build_exam_oblast_prompt({"mode": "exam", "selected_oblast": oblast}, master),
    ]
    for res in results:
        sp = res["system_prompt"]
        assert "JEZIK I TON (TUTOR)" in sp
        assert "bosanskom jeziku (ijekavica)" in sp
        assert sp.index("MODULARNA PRAVILA") < sp.index("JEZIK I TON (TUTOR)")
        assert sp.index("JEZIK I TON (TUTOR)") < sp.index("FORMAT ODGOVORA (CHAT)")
