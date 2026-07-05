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
    assert "primjer" in up
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


def test_conversation_history_as_role_messages(master):
    """Phase 2: historija NE ide u user_prompt tekst — vraća se kao role poruke
    (zadnjih 5), koje servis šalje kao prave chat poruke."""
    hist = [{"role": "user", "content": f"MSG{i}"} for i in range(8)]
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "conversation_history": hist}, _found(), master
    )
    hm = res["history_messages"]
    assert [m["content"] for m in hm] == ["MSG3", "MSG4", "MSG5", "MSG6", "MSG7"]
    assert all(m["role"] == "user" for m in hm)
    # user_prompt više NE sadrži historiju (nema duplog konteksta)
    assert "MSG7" not in res["user_prompt"]
    assert "ZADNJE PORUKE" not in res["user_prompt"]


def test_history_messages_role_mapping():
    # 6 stavki → uzima se zadnjih 5; prazna se preskače; role se normalizuju
    hm = pb.build_history_messages([
        {"role": "user", "content": "ispada iz reza (6. od kraja)"},
        {"role": "assistant", "content": "4. Hoćeš još jedan?"},
        {"role": "bot", "content": "i bot je assistant"},
        {"role": "čudno", "content": "nepoznata rola je user"},
        {"role": "user", "content": ""},              # prazno se preskače
        "goli string je user poruka",
    ])
    assert [m["role"] for m in hm] == ["assistant", "assistant", "user", "user"]
    assert hm[0]["content"] == "4. Hoćeš još jedan?"
    assert hm[-1]["content"] == "goli string je user poruka"
    assert pb.build_history_messages(None) == []
    assert pb.build_history_messages("nije lista") == []


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
    assert "The student is responding to this exact previous task:" in up
    assert "Do not introduce a new task unless the student asks for one." in up
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


def test_practice_followup_topic_block_excludes_typical_tasks(master, topic_row):
    payload = {
        "selected_topic": TOPIC,
        "mode": "practice",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Da li je 2∈S ako je S={1,2,3}?",
        "student_message": "ne znam",
    }
    res = pb.build_tutor_prompt(payload, _found(), master)
    up = res["user_prompt"]
    assert "PODACI O TEMI" in up
    assert topic_row["hint_method"] in up
    assert topic_row["typical_task_1"] not in up
    assert "Tipičan zadatak" not in up
    assert "MOD: VJEŽBAJ (practice)" not in up


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


# --- Phase 2 (audit): NOVI tutor prompt stack (matbot.tutor_prompts) ---------------

# markeri koji dokazuju da je novi stack u system promptu (za 6. razred)
TUTOR_MARKERS_G6 = (
    "Postavi mi pitanje ili zadatak iz matematike.",   # ne-matematika odbijanje
    "MODULARNA PRAVILA",                               # biblioteka tema
    "JEZIK I TON (TUTOR)",                             # bosanski + topao ton
    "DIDAKTIKA — 6. RAZRED",                           # razred-uslovna pravila
    "TERMINOLOGIJA I ZAPIS",                           # uglomjer, zarez, ·, : ...
    "FORMAT ODGOVORA (CHAT)",                          # jedina format sekcija
    "\\frac",                                          # razlomačka crta
    "\\cdot",                                          # množenje tačkom
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
    # "Samo rezultat" NE smije oslabiti pravila zapisa
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


def test_no_legacy_base_prompt_in_tutor_stack(master):
    """Legacy sekcije iz prompts.py NE ulaze u tutor system prompt (Phase 2):
    bez 8/9. gradiva, bez konstrukcija po defaultu, bez kontradikcije."""
    import inspect
    import matbot.prompt_builder as builder
    import matbot.tutor_prompts as tp
    for src in (inspect.getsource(builder), inspect.getsource(tp)):
        for sentinel in (
            "VIZUELNI ZAPIS (PRAVA MATEMATIKA)",
            "GLOBALNA PRAVILA ZAPISA",
            "RAZREDNA PRAVILA — 6. RAZRED",
            "GEOMETRIJSKI PROMPT",
        ):
            assert sentinel not in src, f"legacy sekcija u tutor stacku: {sentinel}"
    sp = pb.build_tutor_prompt({"selected_topic": TOPIC, "grade": 6}, _found(), master)["system_prompt"]
    for legacy in (
        "VIZUELNI ZAPIS (PRAVA MATEMATIKA)",
        "LINEARNA FUNKCIJA",
        "SISTEMI LINEARNIH JEDNAČINA",
        "KOORDINATNA GEOMETRIJA",
        "UNIVERZALNI GEOMETRIJSKI PROMPT",
    ):
        assert legacy not in sp, f"legacy sekcija u system promptu: {legacy}"


def test_no_contradictory_formatting_rules(master):
    """Phase 2: kontradikcija je UKLONJENA — nema pravila 'sve u $$' niti
    'svaki korak u novi red', a format sekcija postoji tačno JEDNOM."""
    sp = pb.build_tutor_prompt({"selected_topic": TOPIC, "grade": 6}, _found(), master)["system_prompt"]
    assert "OBAVEZNO piši unutar dvostrukih znakova dolara" not in sp
    assert "Svaki logički korak ide u NOVI RED" not in sp
    assert "PREDNOST nad ranijim pravilima" not in sp      # precedence hack više ne treba
    assert sp.count("FORMAT ODGOVORA (CHAT)") == 1
    # inline pravilo i display-samo-za-važno su tu (jedini izvor formata)
    assert r"INLINE matematikom \( ... \)" in sp
    assert "SAMO za važan višekoračni račun" in sp


def test_grade6_prompt_excludes_higher_grade_rules(master):
    sp = pb.build_tutor_prompt({"selected_topic": TOPIC, "grade": 6}, _found(), master)["system_prompt"]
    assert "DIDAKTIKA — 6. RAZRED" in sp
    assert "metodom nepoznatog člana" in sp
    for higher in ("LINEARNA FUNKCIJA", "Pitagorin", "prebacivanjem: nepoznate na lijevu",
                   "DIDAKTIKA — 7. RAZRED", "sistemi jednačina", "koordinatn"):
        assert higher.lower() not in sp.lower(), higher


def test_grade7_prompt_has_grade7_rules_only(master7):
    payload = {"selected_topic": "cijeli_sabiranje_oduzimanje", "grade": 7}
    res = pb.build_tutor_prompt(payload, _found("cijeli_sabiranje_oduzimanje"), master7)
    sp = res["system_prompt"]
    assert "DIDAKTIKA — 7. RAZRED" in sp
    assert "MIJENJA PREDZNAK" in sp                        # prebacivanje (7. razred)
    assert "DIDAKTIKA — 6. RAZRED" not in sp
    assert "NEPOZNATI UMANJENIK" not in sp                 # veze operacija su 6. razred
    assert "bez linearne funkcije" in sp                   # više gradivo eksplicitno zabranjeno


def test_grade8_prompt_has_grade8_rules_only(master8):
    topic = "alg_razlomci_definiciono_podrucje_domena_i_nula_razlomljene_racionalne_funkcije"
    payload = {"selected_topic": topic, "grade": 8}
    res = pb.build_tutor_prompt(payload, _found(topic), master8)
    sp = res["system_prompt"]
    assert "DIDAKTIKA — 8. RAZRED" in sp
    for expected in (
        "Stepeni: pravila objašnjavaj korak po korak",
        "Korijeni i realni brojevi",
        "prepoznaj hipotenuzu",
        "pazi na predznake, slične članove",
        "imenilac ne smije biti nula",
        "Koordinatni sistem i linearne funkcije",
        "Geometrijska tijela",
        "Sličnost trouglova i Talesova teorema",
    ):
        assert expected in sp
    assert "DIDAKTIKA — 6. RAZRED" not in sp
    assert "DIDAKTIKA — 7. RAZRED" not in sp
    assert "NEPOZNATI UMANJENIK" not in sp
    assert "prebacivanjem: nepoznate na lijevu" not in sp
    assert "MIJENJA PREDZNAK" not in sp
    assert "bez linearne funkcije" not in sp


def test_constructions_only_when_topic_requires(master, master7):
    """Konstrukcijski blok NE ide u običnu temu; ide u konstrukcijsku temu/oblast."""
    sp_plain = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "grade": 6}, _found(), master
    )["system_prompt"]
    assert "KONSTRUKCIJE (ZA OVU TEMU)" not in sp_plain

    # stvarna konstrukcijska tema iz mastera 7. razreda (data-driven, ne izmišljena)
    row = next(
        r for r in master7["topics"]
        if "konstrukcij" in (r.get("oblast", "") + r.get("topic", "")).lower()
    )
    res = pb.build_tutor_prompt(
        {"selected_topic": row["topic"], "grade": 7}, _found(row["topic"]), master7
    )
    assert "KONSTRUKCIJE (ZA OVU TEMU)" in res["system_prompt"]
    assert "ANALIZA" in res["system_prompt"]

    # i exam za KONSTRUKCIJSKU OBLAST dobija blok (oblast čiji naziv to traži)
    constr_oblast = next(
        r["oblast"] for r in master7["topics"]
        if "konstrukcij" in r.get("oblast", "").lower()
    )
    res_ob = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": constr_oblast, "grade": 7}, master7
    )
    assert res_ob is not None
    assert "KONSTRUKCIJE (ZA OVU TEMU)" in res_ob["system_prompt"]

    # exam za NE-konstrukcijsku oblast NE dobija blok (npr. Cijeli brojevi)
    res_plain = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": "Cijeli brojevi", "grade": 7}, master7
    )
    assert "KONSTRUKCIJE (ZA OVU TEMU)" not in res_plain["system_prompt"]


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


# --- Phase 7.2: nastavak razgovora + robusniji practice follow-up -----------------

def test_continuation_instructions_block():
    block = pb.build_continuation_instructions(
        {"last_tutor_message": "NZS je najmanji zajednički sadržilac. Hoćeš primjer?"}
    )
    assert "NASTAVAK RAZGOVORA" in block
    assert "NE tretiraj je kao novi zahtjev" in block
    assert "NE ponavljaj" in block
    assert "JEDAN konkretan primjer" in block
    assert "vođeni primjer" in block
    assert "Hoćeš primjer?" in block               # zadnja poruka ulazi u blok
    assert 'naslov teme ("Tema:")' in block


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
    assert "MOD: OBJASNI" not in up                # ne ponavlja objašnjenje ispočetka


def test_general_prompt_continuation(master):
    res = pb.build_general_tutor_prompt(
        {"mode": "explain", "interaction_phase": "continuing_explanation",
         "student_message": "nastavi", "last_tutor_message": "Prvi korak je..."}
    )
    assert "NASTAVAK RAZGOVORA" in res["user_prompt"]
    assert "MOD: OBJASNI" not in res["user_prompt"]


def test_exam_oblast_continuation(master, oblast):
    res = pb.build_exam_oblast_prompt(
        {"mode": "exam", "selected_oblast": oblast,
         "interaction_phase": "continuing_explanation",
         "student_message": "može", "last_tutor_message": "Hoćeš da počnemo s prvim?"},
        master,
    )
    up = res["user_prompt"]
    assert "NASTAVAK RAZGOVORA" in up
    assert "KONTROLNI IZ OBLASTI" not in up        # ne ispisuje ponovo 3 zadatka
    assert "OBLAST KONTROLNOG" in up               # materijal oblasti ostaje kontekst


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
    assert "MOD: VJEŽBAJ (practice)" not in up     # nije novi svježi zadatak


def test_followup_hint_for_ne_znam_and_confirmations():
    block = pb.build_practice_followup_instructions({}, {})
    assert '"ne znam"' in block                    # hint umjesto novog zadatka
    assert "NE novi zadatak" in block
    assert "NE ponavljaj isti zadatak osim ako je odgovor nejasan." in block
    assert '"hajde"' in block                      # potvrda → sljedeći korak


def test_explain_mode_conversational_not_repetitive(master):
    res = pb.build_tutor_prompt(
        {"selected_topic": TOPIC, "mode": "explain"}, _found(), master
    )
    up = res["user_prompt"]
    assert "razgovoran" in up
    assert "NE prepričavaj cijelu lekciju" in up
    assert "VEĆ sadrži objašnjenje ove teme" in up
    assert '"Hoćeš primjer?"' in up


# --- Phase 1 (audit): jezik i ton tutora u SVIM modularnim system promptovima ------

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
        assert "Pohvali trud" in sp
        assert 'Obraćaj se učeniku sa "ti"' in sp
        # ton dolazi prije chat formata, poslije modularnih pravila
        assert sp.index("MODULARNA PRAVILA") < sp.index("JEZIK I TON (TUTOR)")
        assert sp.index("JEZIK I TON (TUTOR)") < sp.index("FORMAT ODGOVORA (CHAT)")


def test_chat_format_is_single_source(master):
    """Phase 2: format pravila postoje na JEDNOM mjestu — nema više precedence
    hacka jer nema legacy bloka koji bi se pregazio."""
    sp = pb.build_tutor_prompt({"selected_topic": TOPIC}, _found(), master)["system_prompt"]
    assert "PREDNOST nad ranijim pravilima" not in sp
    assert sp.count("FORMAT ODGOVORA (CHAT)") == 1
    assert "KONAČAN REZULTAT istakni podebljano" in sp


# --- Phase 1 (audit): topic blok filtriran po modu ----------------------------------

def test_topic_block_explain_excludes_controlni(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "explain"}, _found(), master)
    up = res["user_prompt"]
    # explain zadržava scope/greške/hint/riješeni primjer...
    assert topic_row["lesson_scope"] in up
    assert topic_row["common_mistake_1"] in up
    assert topic_row["solved_example_problem"] in up
    # ...ali NE šalje kontrolne zadatke ni tipične zadatke za vježbu
    assert topic_row["controlni_task_1"] not in up
    assert "Kontrolni zadatak 1" not in up
    assert "Tipičan zadatak 1" not in up


def test_topic_block_practice_has_typical_no_controlni(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "practice"}, _found(), master)
    up = res["user_prompt"]
    assert topic_row["typical_task_1"] in up
    assert topic_row["common_mistake_1"] in up
    assert topic_row["hint_method"] in up
    assert "Kontrolni zadatak 1" not in up


def test_topic_block_exam_has_controlni_no_solved_example(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "exam"}, _found(), master)
    up = res["user_prompt"]
    assert topic_row["controlni_task_1"] in up
    assert topic_row["controlni_trick"] in up
    # riješeni primjer i tipični zadaci ne idu u exam kontekst
    assert "Riješeni primjer — zadatak" not in up
    assert "Tipičan zadatak 1" not in up


def test_topic_block_quick_minimal(master, topic_row):
    res = pb.build_tutor_prompt({"selected_topic": TOPIC, "mode": "quick"}, _found(), master)
    up = res["user_prompt"]
    assert topic_row["lesson_scope"] in up               # meta ostaje
    for label in ("Kontrolni zadatak", "Tipičan zadatak", "Riješeni primjer",
                  "Česta greška", "Metoda hinta"):
        assert label not in up, label


def test_topic_block_unknown_mode_keeps_all_fields(master, topic_row):
    """Backward-compat: bez poznatog moda blok šalje sva polja (staro ponašanje)."""
    ctx = pb.get_topic_context(TOPIC, master)
    block = pb._build_topic_block(ctx, mode=None)
    assert topic_row["controlni_task_1"] in block
    assert topic_row["typical_task_1"] in block
    assert topic_row["solved_example_problem"] in block
