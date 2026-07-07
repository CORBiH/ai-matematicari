"""Testovi za POST /api/ai-tutor/chat (Phase 3).

OpenAI se mockira postojećim ``fake_openai`` fixtureom (monkeypatch na
``app._openai_chat``). Testovi za ne-ready statuse NAMJERNO ne koriste
``fake_openai`` — ako bi endpoint pogrešno pozvao OpenAI, ``_isolate`` fixture bi
podigao AssertionError i test bi pao. Dakle: nikad stvaran API/mrežni poziv.
"""
import pytest

from matbot import content_loader as cl

CHAT_URL = "/api/ai-tutor/chat"


@pytest.fixture(autouse=True)
def _tmp_activity_db(monkeypatch, tmp_path):
    """Phase 5: svaki test u ovom modulu loguje u svoj tmp SQLite (ne u repo storage/)."""
    monkeypatch.setenv("MATBOT_DB_PATH", str(tmp_path / "activity.sqlite3"))
    yield tmp_path / "activity.sqlite3"


@pytest.fixture(scope="module")
def tmap():
    return cl.load_thinkific_map()


@pytest.fixture(scope="module")
def tmap8():
    return cl.load_thinkific_map(grade=8)


@pytest.fixture(scope="module")
def composite_payload(tmap):
    """Stvaran kompozitni payload iz MAP-a (robusno na izmjene sadržaja)."""
    row = next(
        l for l in tmap["lessons"]
        if l["topic"] and l["course_name"] and l["section_name"]
        and l["lesson_order"] and l["lesson_title"]
    )
    return {
        "entry_source": "thinkific_lesson",
        "course_name": row["course_name"],
        "section_name": row["section_name"],
        "lesson_order": row["lesson_order"],
        "lesson_title": row["lesson_title"],
        "mode": "explain",
    }, row["topic"]


@pytest.fixture(scope="module")
def composite_payload8(tmap8):
    row = next(
        l for l in tmap8["lessons"]
        if l["topic"] and l["course_name"] and l["section_name"]
        and l["lesson_order"] and l["lesson_title"]
    )
    return {
        "grade": 8,
        "entry_source": "thinkific_lesson",
        "course_name": row["course_name"],
        "section_name": row["section_name"],
        "lesson_order": row["lesson_order"],
        "lesson_title": row["lesson_title"],
        "mode": "explain",
    }, row["topic"]


@pytest.fixture(scope="module")
def ambiguous_title(tmap):
    by_title: dict[str, set] = {}
    for l in tmap["lessons"]:
        by_title.setdefault(l["lesson_title"], set()).add(l["topic"])
    titles = [t for t, topics in by_title.items() if len(topics) > 1]
    assert titles, "očekivan bar jedan dvosmislen naslov u MAP-u"
    return titles[0]


# --- 1: thinkific_lesson composite → 200 + final_topic --------------------------

def test_composite_thinkific_returns_final_topic(client, fake_openai, composite_payload):
    payload, expected_topic = composite_payload
    resp = client.post(CHAT_URL, json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == expected_topic
    assert body["answer"] == fake_openai.state["reply"]
    assert body["entry_source_used"] == "thinkific_lesson"


# --- 2: selected_topic → 200 + final_topic --------------------------------------

def test_selected_topic_returns_final_topic(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod", "mode": "explain"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "skupovi_uvod"
    assert body["answer"] == fake_openai.state["reply"]
    # recommended_mode: explain → practice
    assert body["recommended_mode"] == "practice"
    # skupovi_uvod ima when_to_recommend_video → recommend_video True
    assert body["recommend_video"] is True


# --- 3: unknown → fallback, bez pada, bez OpenAI --------------------------------

def test_unknown_topic_returns_fallback(client):
    resp = client.post(CHAT_URL, json={"student_message": "trebam pomoć"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert body["final_topic"] == "unknown"
    assert body["answer"]  # neprazan bosanski fallback
    assert "oblast" in body["answer"].lower()


# --- 4: ambiguous → status ambiguous + traži izbor ------------------------------

def test_ambiguous_lesson_title(client, ambiguous_title):
    resp = client.post(CHAT_URL, json={"lesson_title": ambiguous_title})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ambiguous"
    assert body["final_topic"] == "unknown"
    low = body["answer"].lower()
    assert "izaberi" in low or "oblast" in low


# --- 5: invalid detected_topic → invalid, bez izmišljanja teme ------------------

def test_invalid_detected_topic(client):
    resp = client.post(CHAT_URL, json={"detected_topic": "izmisljeno_xyz"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "invalid"
    assert body["final_topic"] == "unknown"


# --- 6: nevalidan mode → default explain ----------------------------------------

def test_invalid_mode_defaults_to_explain(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod", "mode": "blabla"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["mode"] == "explain"


# --- 7: conversation_history stiže do prompt buildera (i trimuje se na 5) --------

def test_conversation_history_passed_as_role_messages(client, fake_openai):
    """Phase 2: historija ide kao PRAVE role poruke između system i user poruke,
    NE kao tekst unutar user prompta."""
    history = [{"role": "user", "content": f"MSG{i}"} for i in range(8)]
    resp = client.post(
        CHAT_URL,
        json={"selected_topic": "skupovi_uvod", "conversation_history": history},
    )
    assert resp.status_code == 200
    sent = fake_openai.calls.messages[-1]
    # struktura: [system, MSG3..MSG7, finalna user poruka]
    assert sent[0]["role"] == "system"
    middle = sent[1:-1]
    assert [m["content"] for m in middle] == ["MSG3", "MSG4", "MSG5", "MSG6", "MSG7"]
    assert all(m["role"] == "user" for m in middle)
    # finalna user poruka NE sadrži historiju (topic kontekst da, historiju ne)
    final_user = sent[-1]["content"]
    assert "MSG7" not in final_user and "MSG3" not in final_user
    assert "PODACI O TEMI" in final_user                 # topic kontekst ostaje


def test_history_roles_preserved_in_messages(client, fake_openai):
    history = [
        {"role": "user", "content": "Objasni mi razlomke"},
        {"role": "assistant", "content": "Razlomak je dio cjeline. Hoćeš primjer?"},
    ]
    client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod",
                                "conversation_history": history})
    sent = fake_openai.calls.messages[-1]
    assert sent[1] == {"role": "user", "content": "Objasni mi razlomke"}
    assert sent[2]["role"] == "assistant"
    assert "Hoćeš primjer?" in sent[2]["content"]


# --- 8: student_id nije obavezan ------------------------------------------------

def test_student_id_not_required(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


# --- 9: forgiving parsing; 400 samo kada je tijelo zaista neispravno ------------

def test_non_json_body_returns_400(client):
    resp = client.post(CHAT_URL, data="ovo nije json", content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_json"


def test_json_array_returns_400(client):
    resp = client.post(CHAT_URL, json=[1, 2, 3])
    assert resp.status_code == 400


def test_empty_object_is_forgiving_fallback(client):
    resp = client.post(CHAT_URL, json={})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "fallback"


# --- OPTIONS preflight ----------------------------------------------------------

def test_options_preflight(client):
    resp = client.open(CHAT_URL, method="OPTIONS")
    assert resp.status_code == 204


# --- Phase 6: free_chat detekcija teme + pametniji fallbackovi --------------------

@pytest.fixture(scope="module")
def master():
    return cl.load_master_content()


@pytest.fixture(scope="module")
def master7():
    return cl.load_master_content(grade=7)


@pytest.fixture(scope="module")
def master8():
    return cl.load_master_content(grade=8)


def test_free_chat_aritmeticka_sredina_ready(client, fake_openai):
    """Tema NIJE izabrana — heuristika prepoznaje aritmetičku sredinu → ready."""
    resp = client.post(CHAT_URL, json={
        "entry_source": "free_chat",
        "student_message": "Kako se računa aritmetička sredina brojeva 4, 6 i 8?",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "aritmeticka_sredina"
    assert body["answer"] == fake_openai.state["reply"]
    # heuristika je pogodila → samo JEDAN OpenAI poziv (odgovor, bez klasifikatora)
    assert len(fake_openai.calls.messages) == 1


def test_grade_7_selected_topic_returns_ready_with_grade_7_context(client, fake_openai, master7):
    topic = "cijeli_sabiranje_oduzimanje"
    resp = client.post(CHAT_URL, json={
        "grade": 7,
        "selected_topic": topic,
        "mode": "explain",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == topic
    assert topic in master7["topic_ids"]
    system_prompt = fake_openai.calls.messages[-1][0]["content"]
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "7. RAZRED" in system_prompt
    assert "MODULARNA PRAVILA (6. RAZRED" not in system_prompt
    assert "Sabiranje i oduzimanje cijelih brojeva" in user_prompt


def test_grade_7_free_chat_cijeli_brojevi_ready_not_grade_6(client, fake_openai, master, master7):
    resp = client.post(CHAT_URL, json={
        "grade": "7",
        "entry_source": "free_chat",
        "student_message": "Kako se sabiraju cijeli brojevi?",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "cijeli_sabiranje_oduzimanje"
    assert body["final_topic"] in master7["topic_ids"]
    assert body["final_topic"] not in master["topic_ids"]
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "Cijeli brojevi" in user_prompt
    assert "Skupovi" not in user_prompt


def test_grade_7_exam_oblast_ready(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "grade": 7,
        "mode": "exam",
        "selected_oblast": "Cijeli brojevi",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "exam"
    assert body["final_topic"] == "unknown"
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "OBLAST KONTROLNOG: Cijeli brojevi" in user_prompt


def test_grade_7_quick_mode_ready(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "grade": 7,
        "mode": "quick",
        "student_message": "-5 + 8",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "quick"
    assert body["answer"] == fake_openai.state["reply"]


def test_grade_8_selected_topic_returns_ready_with_grade_8_context(client, fake_openai, master8):
    topic = "alg_razlomci_definiciono_podrucje_domena_i_nula_razlomljene_racionalne_funkcije"
    resp = client.post(CHAT_URL, json={
        "grade": 8,
        "selected_topic": topic,
        "mode": "explain",
        "student_message": "Objasni mi domenu.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == topic
    assert topic in master8["topic_ids"]
    system_prompt = fake_openai.calls.messages[-1][0]["content"]
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "8. RAZRED" in system_prompt
    assert "nazivnik ne smije biti nula" in system_prompt
    assert "Definiciono područje" in user_prompt


def test_grade_8_free_chat_pitagora_ready(client, fake_openai, master8):
    resp = client.post(CHAT_URL, json={
        "grade": 8,
        "entry_source": "free_chat",
        "student_message": "Kako se koristi Pitagorina teorema za hipotenuzu i katete?",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "pitagora_pitagorina_teorema_osnovno"
    assert body["final_topic"] in master8["topic_ids"]
    assert len(fake_openai.calls.messages) == 1


def test_grade_8_exam_oblast_ready(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "grade": 8,
        "mode": "exam",
        "selected_oblast": "Pitagorina teorema",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "exam"
    assert body["final_topic"] == "unknown"
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "OBLAST KONTROLNOG: Pitagorina teorema" in user_prompt
    assert "KONTROLNI IZ OBLASTI" in user_prompt


def test_grade_8_quick_mode_ready(client, fake_openai):
    # Result/Quick mod je kontekst-slobodan: selected_topic se IGNORIŠE, tema null.
    resp = client.post(CHAT_URL, json={
        "grade": 8,
        "mode": "quick",
        "selected_topic": "polinomi_kvadrat_binoma",
        "student_message": "Izračunaj (x+3)^2",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "quick"
    assert body["final_topic"] is None
    assert body["effective_topic"] is None
    assert body["recommend_video"] is False
    assert body["context_policy"] == "disabled_for_result_mode"
    assert body["debug"]["ignored_opened_lesson_topic"] == "polinomi_kvadrat_binoma"


def test_grade_8_composite_thinkific_returns_final_topic(client, fake_openai, composite_payload8):
    payload, expected_topic = composite_payload8
    resp = client.post(CHAT_URL, json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == expected_topic
    assert body["entry_source_used"] == "thinkific_lesson"


def test_grade_8_practice_followup_keeps_exact_task(client, fake_openai, master8):
    topic = "alg_razlomci_definiciono_podrucje_domena_i_nula_razlomljene_racionalne_funkcije"
    visible_task = "Odredi uslov za razlomak 1/(x-3)."
    resp = client.post(CHAT_URL, json={
        "grade": 8,
        "mode": "practice",
        "selected_topic": topic,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": visible_task,
        "student_message": "ne znam",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "practice"
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert f"The student is responding to this exact previous task: {visible_task}" in user_prompt
    assert "Do not introduce a new task unless the student asks for one." in user_prompt
    assert "Tipičan zadatak" not in user_prompt
    row = master8["topics_by_id"][topic]
    for key in ("typical_task_1", "typical_task_2", "typical_task_3"):
        if row.get(key) and row[key] not in visible_task:
            assert row[key] not in user_prompt


def test_free_chat_fractions_no_topic_required(client, fake_openai, master):
    resp = client.post(CHAT_URL, json={"student_message": "Izračunaj 1/2 + 1/3"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"].startswith("razlomci_")
    assert body["final_topic"] in master["topic_ids"]


def test_fraction_multiplication_task_then_short_answer_keeps_topic(client, fake_openai):
    topic = "razlomci_mnozenje_razlomkom_svojstva"
    first_msg = (
        "nikako ne razumijem mnozenje razlomaka "
        "daj mi neki zadatak i objasni kako ga radis"
    )
    first = client.post(CHAT_URL, json={
        "entry_source": "free_chat",
        "mode": "quick",
        "student_message": first_msg,
    })
    assert first.status_code == 200
    first_body = first.get_json()
    assert first_body["status"] == "ready"
    # Result/Quick mod: prvi (quick) potez je kontekst-slobodan → tema null.
    assert first_body["final_topic"] is None
    assert first_body["mode"] == "quick"

    second = client.post(CHAT_URL, json={
        "entry_source": "free_chat",
        "mode": "quick",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "3/4 * 2/5",
        "student_message": "5/18",
        "conversation_history": [
            {"role": "user", "content": first_msg},
            {"role": "assistant", "content": "Zadatak: 3/4 * 2/5"},
        ],
    })
    assert second.status_code == 200
    second_body = second.get_json()
    assert second_body["status"] == "ready"
    assert second_body["mode"] == "practice"
    assert second_body["final_topic"] == topic
    assert len(fake_openai.calls.messages) == 2
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "PROVJERA ODGOVORA" in user_prompt
    assert "ZADNJI ZADATAK" in user_prompt
    assert "3/4 * 2/5" in user_prompt
    assert "Do not introduce a new task unless the student asks for one." in user_prompt
    assert "Množenje razlomka razlomkom" in user_prompt


def test_nonstreaming_practice_response_includes_last_tutor_task(client, fake_openai):
    task = "Uporedi brojeve: 7 205 i 7 250. Koji je veći broj? Koristi znakove <, > ili =."
    fake_openai.state["reply"] = task
    resp = client.post(CHAT_URL, json={
        "mode": "practice",
        "selected_topic": "n_n0_uporedjivanje_poluprava_prethodnik_sljedbenik",
        "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["last_tutor_task"] == task


def test_ne_znam_after_comparison_task_prompt_keeps_exact_task(client, fake_openai, master):
    topic = "n_n0_uporedjivanje_poluprava_prethodnik_sljedbenik"
    visible_task = (
        "Uporedi brojeve: 7 205 i 7 250. Koji je veći broj? "
        "Koristi znakove <, > ili =."
    )
    resp = client.post(CHAT_URL, json={
        "mode": "practice",
        "selected_topic": topic,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": visible_task,
        "student_message": "ne znam",
        "conversation_history": [
            {"role": "assistant", "content": visible_task},
        ],
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "practice"
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert f"The student is responding to this exact previous task: {visible_task}" in user_prompt
    assert "Do not introduce a new task unless the student asks for one." in user_prompt
    assert visible_task in user_prompt
    assert "Tipičan zadatak" not in user_prompt
    row = master["topics_by_id"][topic]
    for key in ("typical_task_1", "typical_task_2", "typical_task_3"):
        if row.get(key) and row[key] not in visible_task:
            assert row[key] not in user_prompt


def test_answering_practice_task_other_topic_is_not_hardcoded(client, fake_openai, master7):
    topic = "cijeli_sabiranje_oduzimanje"
    visible_task = "Izračunaj: -7 + 12."
    resp = client.post(CHAT_URL, json={
        "grade": 7,
        "mode": "practice",
        "selected_topic": topic,
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": visible_task,
        "student_message": "-5",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "practice"
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert f"The student is responding to this exact previous task: {visible_task}" in user_prompt
    row = master7["topics_by_id"][topic]
    for key in ("typical_task_1", "typical_task_2", "typical_task_3"):
        if row.get(key):
            assert row[key] not in user_prompt


def test_new_task_request_still_uses_fresh_practice_prompt(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "mode": "practice",
        "selected_topic": "n_n0_uporedjivanje_poluprava_prethodnik_sljedbenik",
        "student_message": "Daj mi novi zadatak.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    user_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "MOD: VJEŽBAJ (practice)" in user_prompt
    assert "PROVJERA ODGOVORA" not in user_prompt


def test_free_chat_classifier_valid_topic_accepted(client, fake_openai):
    """Heuristika ne pogađa; LLM klasifikator (mock) vraća validnu temu."""
    fake_openai.state["reply"] = '{"detected_topic": "n_n0_mnozenje"}'
    resp = client.post(CHAT_URL, json={"student_message": "Izračunaj 25 · 37"})
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "n_n0_mnozenje"
    # klasifikator + odgovor = 2 poziva
    assert len(fake_openai.calls.messages) == 2


def test_free_chat_classifier_garbage_general_answer(client, fake_openai):
    """Klasifikator vrati smeće → unknown → opšti odgovor BEZ izmišljene teme."""
    # default fake reply "Test odgovor: x = 3" nije JSON → klasifikator = unknown
    resp = client.post(CHAT_URL, json={"student_message": "Izračunaj 25 · 37 - 4"})
    body = resp.get_json()
    assert body["status"] == "ready"                 # ipak odgovaramo (opšti prompt)
    assert body["final_topic"] == "unknown"          # tema NIJE izmišljena
    assert len(fake_openai.calls.messages) == 2      # klasifikator + odgovor
    # opšti prompt ne smije imati topic kontekst
    answer_prompt = fake_openai.calls.messages[-1][-1]["content"]
    assert "PODACI O TEMI" not in answer_prompt


def test_classifier_not_called_when_topic_selected(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "selected_topic": "skupovi_uvod",
        "student_message": "Izračunaj 25 · 37",     # i konkretna poruka
    })
    assert resp.get_json()["final_topic"] == "skupovi_uvod"
    assert len(fake_openai.calls.messages) == 1      # samo odgovor, bez detekcije


def test_vague_free_chat_still_fallback(client):
    # bez fake_openai — dokaz da se OpenAI NE zove za vague poruke
    resp = client.post(CHAT_URL, json={"student_message": "Kako ovo"})
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert body["final_topic"] == "unknown"


def test_exam_no_topic_asks_oblast(client, master):
    resp = client.post(CHAT_URL, json={"mode": "exam", "student_message": "Sutra imam kontrolni"})
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert "Iz koje oblasti je kontrolni?" in body["answer"]
    # lista oblasti dolazi iz mastera (data-driven, ne hardkodirano)
    some_oblast = master["topics"][0]["oblast"]
    assert some_oblast in body["answer"]


def test_message_cap_4000(client, fake_openai):
    long_msg = "Izračunaj " + "X" * 8000
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod",
                                       "student_message": long_msg})
    assert resp.status_code == 200
    sent = fake_openai.calls.messages[-1][-1]["content"]
    assert "X" * 3000 in sent                        # poruka je stigla…
    assert "X" * 4001 not in sent                    # …ali skraćena na max 4000


def test_history_caps(client, fake_openai):
    history = [{"role": "user", "content": f"H{i}" + "y" * 3000} for i in range(7)]
    resp = client.post(CHAT_URL, json={
        "selected_topic": "skupovi_uvod",
        "conversation_history": history,
    })
    assert resp.status_code == 200
    # Phase 2: historija su role poruke [system, H2..H6, user]
    sent = fake_openai.calls.messages[-1]
    hist_contents = [m["content"] for m in sent[1:-1]]
    assert any(c.startswith("H6") for c in hist_contents)
    assert any(c.startswith("H2") for c in hist_contents)   # zadnjih 5 (H2..H6)
    assert not any(c.startswith("H1") or c.startswith("H0") for c in hist_contents)
    # po stavci max 1500 ukupno ("H6" + 1498 y-ona)
    for c in hist_contents:
        assert len(c) <= 1500
    assert "y" * 1400 in hist_contents[-1]


def test_500_does_not_leak_exception(client, monkeypatch):
    import app as app_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("SUPER TAJNA INTERNA GREŠKA 42")

    monkeypatch.setattr(app_mod.ai_tutor_service, "handle_chat", _boom)
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 500
    text = resp.get_data(as_text=True)
    assert "SUPER TAJNA" not in text                 # bez curenja internih detalja
    assert resp.get_json()["error"] == "ai_tutor_failed"


# --- Phase 6.2: bazna pravila + "5-1" + slika u modularnom tutoru -----------------

def test_quick_simple_expression_no_topic(client, fake_openai):
    """'5-1' u quick modu bez teme: NE fallback — poziva se OpenAI sa result-mod
    (kontekst-slobodnim) pravilima: bez razredne didaktike, uz terminologiju/zapis."""
    resp = client.post(CHAT_URL, json={"mode": "quick", "student_message": "5-1"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["answer"] == fake_openai.state["reply"]
    assert body["final_topic"] is None
    # Result mod system prompt: identitet "Samo rezultat", BEZ razredne didaktike
    system_sent = fake_openai.calls.messages[-1][0]["content"]
    assert "Samo rezultat" in system_sent
    assert "ne odbijaj valjan matematički zadatak" in system_sent.lower()
    assert "DIDAKTIKA — 6. RAZRED" not in system_sent
    assert "MODULARNA PRAVILA" not in system_sent
    assert "TERMINOLOGIJA I ZAPIS" in system_sent


import io as _io
import json as _json


def _multipart(payload: dict, filename="zadatak.png", content=b"fake-image-bytes"):
    return {
        "payload": _json.dumps(payload),
        "image": (_io.BytesIO(content), filename),
    }


def test_multipart_image_vision_path(client, fake_openai):
    """Slika + quick: Vision multimodalna poruka; tema se IGNORIŠE (result mod)."""
    resp = client.post(
        CHAT_URL,
        data=_multipart({"selected_topic": "skupovi_uvod", "mode": "quick"}),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] is None          # result mod: bez teme
    # zadnja poruka je multimodalna: tekst + data: URL slike
    content = fake_openai.calls.messages[-1][-1]["content"]
    assert isinstance(content, list)
    assert any(
        p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:")
        for p in content
    )
    # result-mod pravila u system promptu (terminologija/zapis ostaje)
    assert "TERMINOLOGIJA I ZAPIS" in fake_openai.calls.messages[-1][0]["content"]


def test_multipart_image_no_text_no_topic(client, fake_openai):
    """Slika bez teksta i bez teme → opšti Vision odgovor, tema se NE izmišlja."""
    resp = client.post(
        CHAT_URL,
        data=_multipart({"mode": "quick"}),
        content_type="multipart/form-data",
    )
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] is None          # result mod: tema null, ne "unknown"
    content = fake_openai.calls.messages[-1][-1]["content"]
    assert isinstance(content, list)


def test_multipart_rejects_non_image(client):
    resp = client.post(
        CHAT_URL,
        data=_multipart({"mode": "quick"}, filename="zadatak.txt"),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_image"


def test_multipart_bad_payload_json(client):
    resp = client.post(
        CHAT_URL,
        data={"payload": "ovo nije json"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_json"


def test_multipart_without_image_still_works(client, fake_openai):
    resp = client.post(
        CHAT_URL,
        data={"payload": _json.dumps({"selected_topic": "skupovi_uvod"})},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"


# --- Phase 5: activity logging ---------------------------------------------------

def test_chat_logs_ready_response(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post(CHAT_URL, json={
        "selected_topic": "skupovi_uvod",
        "entry_source": "manual_topic_choice",
        "session_id": "sess-log-1",
        "student_message": "OVO JE TAJNA PORUKA 12345",
    })
    assert resp.status_code == 200
    rows = al.get_recent_activity(session_id="sess-log-1", path=_tmp_activity_db)
    assert len(rows) == 1
    r = rows[0]
    assert r["event_type"] == "topic_selected"
    assert r["final_topic"] == "skupovi_uvod"
    assert r["status"] == "ready"
    assert r["session_id"] == "sess-log-1"
    assert r["student_id"] is None                       # student_id nije obavezan
    # u DB NEMA pune poruke niti AI odgovora
    raw = _tmp_activity_db.read_bytes()
    assert b"OVO JE TAJNA PORUKA 12345" not in raw
    assert fake_openai.state["reply"].encode("utf-8") not in raw


def test_chat_logs_grade(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post(CHAT_URL, json={
        "grade": 7,
        "selected_topic": "cijeli_sabiranje_oduzimanje",
        "entry_source": "manual_topic_choice",
        "session_id": "sess-log-grade-7",
    })
    assert resp.status_code == 200
    rows = al.get_recent_activity(session_id="sess-log-grade-7", path=_tmp_activity_db)
    assert len(rows) == 1
    assert rows[0]["grade"] == 7


def test_chat_logs_grade_8(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post(CHAT_URL, json={
        "grade": 8,
        "selected_topic": "stepeni_pravila_i_pojasnjenja_stepeni",
        "entry_source": "manual_topic_choice",
        "session_id": "sess-log-grade-8",
    })
    assert resp.status_code == 200
    rows = al.get_recent_activity(session_id="sess-log-grade-8", path=_tmp_activity_db)
    assert len(rows) == 1
    assert rows[0]["grade"] == 8


def test_chat_logs_fallback_response(client, _tmp_activity_db):
    from matbot import activity_log as al
    resp = client.post(CHAT_URL, json={
        "session_id": "sess-log-2",
        "student_message": "nepoznato pitanje",
    })
    assert resp.status_code == 200
    rows = al.get_recent_activity(session_id="sess-log-2", path=_tmp_activity_db)
    assert len(rows) == 1
    assert rows[0]["status"] == "fallback"
    assert rows[0]["event_type"] == "ai_message"


def test_chat_logs_practice_answer_event(client, fake_openai, _tmp_activity_db):
    from matbot import activity_log as al
    client.post(CHAT_URL, json={
        "selected_topic": "skupovi_uvod",
        "session_id": "sess-log-3",
        "interaction_phase": "answering_practice_task",
        "last_tutor_task": "Da li je 2∈S?",
        "student_message": "da",
    })
    rows = al.get_recent_activity(session_id="sess-log-3", path=_tmp_activity_db)
    assert rows and rows[0]["event_type"] == "practice_answer"
    assert rows[0]["mode"] == "practice"


def test_chat_ok_when_logging_fails(client, fake_openai, monkeypatch):
    import matbot.ai_tutor_service as svc

    def _boom(*args, **kwargs):
        raise RuntimeError("baza nedostupna")

    monkeypatch.setattr(svc, "log_student_activity", _boom)
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ready"          # odgovor preživi pad logovanja


# --- response shape -------------------------------------------------------------

def test_response_has_all_fields(client, fake_openai):
    resp = client.post(CHAT_URL, json={"selected_topic": "skupovi_uvod", "mode": "quick"})
    body = resp.get_json()
    for key in (
        "answer",
        "final_topic",
        "opened_lesson_topic",
        "effective_topic",
        "entry_source_used",
        "topic_conflict",
        "recommended_mode",
        "recommend_video",
        "parent_report_signal",
        "status",
        "mode",
    ):
        assert key in body
    # quick → recommended_mode explain
    assert body["recommended_mode"] == "explain"


# --- Phase 7: exam za CIJELU OBLAST (selected_oblast bez selected_topic) ----------

@pytest.fixture(scope="module")
def oblast_name(master):
    return master["topics_by_id"]["skupovi_uvod"]["oblast"]


def test_exam_by_oblast_returns_ready(client, fake_openai, oblast_name):
    resp = client.post(CHAT_URL, json={
        "mode": "exam",
        "selected_oblast": oblast_name,
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["mode"] == "exam"
    assert body["final_topic"] == "unknown"          # tema se ne izmišlja
    assert body["answer"] == fake_openai.state["reply"]
    # tačno JEDAN OpenAI poziv (bez klasifikatora teme za auto-poruku)
    assert len(fake_openai.calls.messages) == 1
    user_prompt = fake_openai.calls.messages[0][-1]["content"]
    assert "OBLAST KONTROLNOG" in user_prompt
    assert "KONTROLNI IZ OBLASTI" in user_prompt


def test_exam_by_unknown_oblast_falls_back_without_openai(client):
    # nepostojeća oblast → deterministički exam fallback; bez fake_openai
    # fixture-a bi _isolate digao AssertionError da je OpenAI pozvan
    resp = client.post(CHAT_URL, json={
        "mode": "exam",
        "selected_oblast": "nepostojeca_oblast_xyz",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "fallback"
    assert body["final_topic"] == "unknown"
    assert "Iz koje oblasti je kontrolni?" in body["answer"]


def test_exam_with_topic_still_topic_based(client, fake_openai, oblast_name):
    # postojeći topic-based exam tok netaknut i kada frontend pošalje i oblast
    resp = client.post(CHAT_URL, json={
        "mode": "exam",
        "selected_topic": "skupovi_uvod",
        "selected_oblast": oblast_name,
    })
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "skupovi_uvod"


def test_exam_by_oblast_logged(client, fake_openai, oblast_name, _tmp_activity_db):
    from matbot import activity_log as al
    client.post(CHAT_URL, json={
        "mode": "exam",
        "selected_oblast": oblast_name,
        "session_id": "sess-log-oblast",
        "student_message": "Sutra imam kontrolni iz ove oblasti. Pripremi me.",
    })
    rows = al.get_recent_activity(session_id="sess-log-oblast", path=_tmp_activity_db)
    assert rows and rows[0]["event_type"] == "exam_mode_used"
    assert rows[0]["status"] == "ready"


# --- Phase 7.2: nastavak razgovora (continuing_explanation) ------------------------

def test_continuation_vague_message_not_fallback(client, fake_openai):
    """'može' bez teme, ali sa continuing_explanation → ready nastavak (opći
    prompt), NE deterministički fallback i NE LLM klasifikator teme."""
    resp = client.post(CHAT_URL, json={
        "mode": "explain",
        "student_message": "može",
        "interaction_phase": "continuing_explanation",
        "last_tutor_message": "NZS je najmanji zajednički sadržilac. Hoćeš primjer?",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "unknown"
    assert body["answer"] == fake_openai.state["reply"]
    assert len(fake_openai.calls.messages) == 1          # samo odgovor, bez detekcije
    user_prompt = fake_openai.calls.messages[0][-1]["content"]
    assert "NASTAVAK RAZGOVORA" in user_prompt
    assert "Hoćeš primjer?" in user_prompt


def test_continuation_with_topic_uses_continuation_block(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "selected_topic": "skupovi_uvod",
        "mode": "explain",
        "student_message": "nastavi",
        "interaction_phase": "continuing_explanation",
        "last_tutor_message": "Hoćeš da zajedno riješimo primjer?",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ready"
    assert body["final_topic"] == "skupovi_uvod"
    up = fake_openai.calls.messages[-1][-1]["content"]
    assert "NASTAVAK RAZGOVORA" in up
    assert "MOD: OBJASNI" not in up                      # ne ponavlja objašnjenje


def test_continuation_without_message_still_fallback(client):
    """Prazna poruka sa continuation fazom → i dalje deterministički fallback
    (bez OpenAI poziva — _isolate bi digao AssertionError)."""
    resp = client.post(CHAT_URL, json={
        "interaction_phase": "continuing_explanation",
        "last_tutor_message": "Hoćeš primjer?",
    })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "fallback"


def test_last_tutor_message_capped(client, fake_openai):
    resp = client.post(CHAT_URL, json={
        "selected_topic": "skupovi_uvod",
        "student_message": "može",
        "interaction_phase": "continuing_explanation",
        "last_tutor_message": "Y" * 5000,
    })
    assert resp.status_code == 200
    sent = fake_openai.calls.messages[-1][-1]["content"]
    assert "Y" * 600 in sent                             # poruka je stigla…
    assert "Y" * 1001 not in sent                        # …ali skraćena (cap 1000)
