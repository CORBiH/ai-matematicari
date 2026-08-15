"""Testovi Quick moda („Samo rezultat“) — FakeLLM, bez mreže.

NAPOMENA O OBIMU: fake testovi dokazuju (a) da server šalje modelu ISPRAVAN
prompt (kratak kontekst, historija, pravila) i (b) da server ispravno
obrađuje/sanitizuje odgovor i drži ugovor prema frontendu. Da li stvarni model
poštuje pravila (kratak odgovor, ne izmišlja podatke, traži pojašnjenje) —
to dokazuje samo live eval (plan u završnom izvještaju).
"""
import json

from matbot import auth, prompts
from matbot.llm import LLMTimeout
from matbot.quick import run_quick_turn
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.ratelimit import RateLimiter
from tests.conftest import queue_two_call, FakeLLM, make_output, make_quick_output, make_task


def quick_turn_payload(msg="Koliko je 3/4 + 2/5?", **kw):
    base = {
        "session_id": "quick-sess-1",
        "grade": 6,
        "selected_topic": "",
        "selected_oblast": "",
        "student_message": msg,
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
        "last_tutor_message": "",
        "conversation_history": [],
    }
    base.update(kw)
    return base


def http_payload(msg="Koliko je 3/4 + 2/5?", **kw):
    base = {
        "session_id": "quick-http-sess",
        "client_turn_id": "turn-1",
        "grade": 6,
        "mode": "quick",
        "entry_source": "free_chat",
        "selected_topic": "",
        "selected_oblast": "",
        "student_message": msg,
        "conversation_history": [],
    }
    base.update(kw)
    return base


def _authed(flask_app):
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    return c


# ---------------------------------------------------------------------------
# OSNOVNO
# ---------------------------------------------------------------------------

def test_quick_returns_full_contract_and_one_llm_call():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Rezultat je $\\frac{23}{20}=1\\frac{3}{20}$."))
    r = run_quick_turn(fake, quick_turn_payload())
    assert r["status"] == "ready"
    assert "\\frac{23}{20}" in r["answer"]
    assert r["session_mode"] == "quick"
    assert r["answer_verdict"] is None
    assert r["last_tutor_task"] == ""
    assert r["next_state"] == {}
    assert fake.call_count == 1
    assert len(fake.quick_calls) == 1


def test_quick_works_via_chat_endpoint(flask_app, fake_llm):
    fake_llm.queue(make_quick_output(reply="Rezultat je $x=5$."))
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat", json=http_payload(msg="Riješi 3x + 7 = 22."))
    assert r.status_code == 200
    j = r.get_json()
    assert j["session_mode"] == "quick"
    assert j["status"] == "ready"
    assert j["last_tutor_task"] == ""
    assert j["answer_verdict"] is None
    assert j["next_state"] == {}
    assert fake_llm.call_count == 1


def test_quick_works_via_sse_stream_endpoint(flask_app, fake_llm):
    fake_llm.queue(make_quick_output(reply="Rezultat je $x=5$."))
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat/stream", json=http_payload(msg="Riješi 3x + 7 = 22."))
    assert r.status_code == 200
    assert r.content_type.startswith("text/event-stream")
    body = r.get_data(as_text=True)
    assert body.startswith("event: done\ndata: ")
    data = json.loads(body.split("data: ", 1)[1].strip())
    assert data["session_mode"] == "quick"
    assert fake_llm.call_count == 1


def test_quick_does_not_create_practice_session(flask_app, fake_llm, store):
    fake_llm.queue(make_quick_output())
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat", json=http_payload())
    assert r.status_code == 200
    assert store.peek("quick-http-sess") is None   # NEMA Practice sesije


def test_quick_response_has_no_practice_or_choice_fields():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Rezultat je $x=5$."))
    r = run_quick_turn(fake, quick_turn_payload())
    assert "options" not in r
    assert "expected_answer" not in r
    assert "correct_option_id" not in r
    assert "wrong_option_ids" not in r
    assert "task" not in r["next_state"]
    assert "hint_level" not in r["next_state"]
    assert "correct_streak" not in r["next_state"]


# ---------------------------------------------------------------------------
# PROMPT
# ---------------------------------------------------------------------------

def test_prompt_gets_grade_and_current_message():
    fake = FakeLLM()
    fake.queue(make_quick_output())
    run_quick_turn(fake, quick_turn_payload(msg="Skrati 12x/18."))
    instructions, input_text = fake.quick_calls[0]
    assert "6. razred" in instructions
    assert "Samo rezultat" in instructions
    assert "PORUKA UČENIKA: Skrati 12x/18." in input_text


def test_prompt_uses_canonical_lesson_context_when_selected():
    fake = FakeLLM()
    fake.queue(make_quick_output())
    run_quick_turn(fake, quick_turn_payload(selected_topic="6-01-006"))
    _, input_text = fake.quick_calls[0]
    assert "Unija skupova" in input_text   # canonical naziv lekcije iz topics.json


def test_prompt_sends_at_most_three_previous_exchanges():
    fake = FakeLLM()
    fake.queue(make_quick_output())
    history = []
    for i in range(6):
        history.append({"role": "user", "content": f"Pitanje {i}"})
        history.append({"role": "assistant", "content": f"Odgovor {i}"})
    run_quick_turn(fake, quick_turn_payload(conversation_history=history))
    _, input_text = fake.quick_calls[0]
    # samo zadnje 3 razmjene (6 poruka) smiju biti prisutne
    assert "Pitanje 0" not in input_text
    assert "Pitanje 3" in input_text
    assert "Pitanje 5" in input_text


def test_prompt_never_leaks_practice_state():
    fake = FakeLLM()
    fake.queue(make_quick_output())
    run_quick_turn(fake, quick_turn_payload())
    instructions, input_text = fake.quick_calls[0]
    for leaked in ("INTERNI OČEKIVANI ODGOVOR", "HINT NIVO", "NEDAVNI ZADACI",
                   "correct_option_id", "expected_answer", "session_id",
                   "client_turn_id"):
        assert leaked not in instructions
        assert leaked not in input_text


# ---------------------------------------------------------------------------
# PONAŠANJE (posebni slučajevi iz spec-a)
# ---------------------------------------------------------------------------

def test_fraction_addition_returns_concise_result():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$\\frac{5}{6}$"))
    r = run_quick_turn(fake, quick_turn_payload(msg="Koliko je 2/3 + 1/6?"))
    assert r["answer"] == "$\\frac{5}{6}$"


def test_linear_equation_returns_concise_result():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x=5$"))
    r = run_quick_turn(fake, quick_turn_payload(msg="Riješi 4x - 7 = 13."))
    assert r["answer"] == "$x=5$"


def test_quadratic_equation_returns_both_roots():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x_1=2,\\ x_2=3$"))
    r = run_quick_turn(fake, quick_turn_payload(msg="Riješi x² - 5x + 6 = 0."))
    assert "x_1=2" in r["answer"] and "x_2=3" in r["answer"]


def test_no_real_solution_case():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Nema rješenja u skupu realnih brojeva."))
    r = run_quick_turn(fake, quick_turn_payload(msg="Riješi x² + 1 = 0 u skupu realnih brojeva."))
    assert "Nema rješenja" in r["answer"]


def test_geometry_keeps_unit():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Površina je $P=40\\ \\text{cm}^2$."))
    r = run_quick_turn(fake, quick_turn_payload(msg="Izračunaj površinu pravougaonika stranica 5 cm i 8 cm."))
    assert "cm" in r["answer"]


def test_insufficient_data_asks_instead_of_guessing():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Nedostaju potrebni podaci, na primjer osnovica i odgovarajuća visina."))
    r = run_quick_turn(fake, quick_turn_payload(msg="Izračunaj površinu trougla."))
    assert "Nedostaju" in r["answer"]
    assert fake.call_count == 1   # bez dodatnog AI poziva za pojašnjenje


def test_ambiguous_message_asks_for_full_expression():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Na koji izraz misliš? Pošalji cijeli zadatak."))
    r = run_quick_turn(fake, quick_turn_payload(msg="Koliko je ovo?"))
    assert "cijeli zadatak" in r["answer"]
    assert fake.call_count == 1


def test_shows_procedure_uses_history_when_asked_how():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$3x+7=22$\n$3x=15$\n$x=5$"))
    history = [
        {"role": "user", "content": "Riješi 3x + 7 = 22."},
        {"role": "assistant", "content": "Rezultat je $x=5$."},
    ]
    r = run_quick_turn(fake, quick_turn_payload(msg="Kako si to dobio?", conversation_history=history))
    _, input_text = fake.quick_calls[0]
    assert "KRATKA HISTORIJA" in input_text
    assert "3x+7=22" in r["answer"] or "x=5" in r["answer"]
    assert fake.call_count == 1


def test_off_topic_question_stays_brief():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="MAT-BOT je namijenjen matematici — pošalji matematičko pitanje ili zadatak."))
    r = run_quick_turn(fake, quick_turn_payload(msg="Ko je pobijedio Ligu prvaka?"))
    assert "matematici" in r["answer"]
    assert len(r["answer"]) < 200


def test_quick_never_creates_new_task_or_grades():
    instructions = prompts.build_quick_instructions(6)
    assert "NIKAD sam od sebe ne generišeš novi zadatak" in instructions
    assert "ne ocjenjuješ učenika" in instructions


# ---------------------------------------------------------------------------
# MATHJAX
# ---------------------------------------------------------------------------

def test_valid_latex_passes_through_unchanged():
    fake = FakeLLM()
    reply = "Rezultat je $\\frac{23}{20}=1\\frac{3}{20}$."
    fake.queue(make_quick_output(reply=reply))
    r = run_quick_turn(fake, quick_turn_payload())
    assert r["answer"] == reply


def test_control_character_frac_repaired_in_quick():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Rezultat: $\x0crac{2}{3}$."))
    r = run_quick_turn(fake, quick_turn_payload())
    assert "$\\frac{2}{3}$" in r["answer"]
    assert not any(ord(ch) < 0x20 and ch not in ("\n", "\t") for ch in r["answer"])


def test_unbalanced_math_segment_stripped_not_broken():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Pogledaj $\\frac{2}{3 i nastavi dalje."))
    r = run_quick_turn(fake, quick_turn_payload())
    assert r["answer"].count("$") % 2 == 0


def test_quick_response_has_no_control_chars_anywhere():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Red 1.\nRed 2 sa $\x0crac{1}{2}$."))
    r = run_quick_turn(fake, quick_turn_payload())
    raw = json.dumps(r, ensure_ascii=False)
    for value in json.loads(raw).values():
        if isinstance(value, str):
            assert not any(ord(ch) < 0x20 and ch not in ("\n", "\t") for ch in value)


# ---------------------------------------------------------------------------
# SIGURNOST
# ---------------------------------------------------------------------------

def test_quick_requires_token_401_no_llm(flask_app, fake_llm):
    c = flask_app.test_client()   # bez tokena
    r = c.post("/api/ai-tutor/chat", json=http_payload())
    assert r.status_code == 401
    assert fake_llm.call_count == 0


def test_quick_counts_toward_session_rate_limit(flask_app, fake_llm):
    flask_app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=1, per_hour=150)
    c = _authed(flask_app)
    fake_llm.queue(make_quick_output())
    assert c.post("/api/ai-tutor/chat", json=http_payload()).status_code == 200
    r = c.post("/api/ai-tutor/chat", json=http_payload(msg="Skrati 12x/18."))
    assert r.status_code == 429
    assert fake_llm.call_count == 1   # drugi poziv NIJE stigao do LLM-a


def test_quick_respects_ip_rate_limit(flask_app, fake_llm):
    flask_app.config["MATBOT_IP_LIMITER"] = RateLimiter(per_minute=1, per_hour=150)
    c = _authed(flask_app)
    fake_llm.queue(make_quick_output())
    assert c.post("/api/ai-tutor/chat", json=http_payload()).status_code == 200
    r = c.post("/api/ai-tutor/chat", json=http_payload(msg="Skrati 12x/18."))
    assert r.status_code == 429
    assert fake_llm.call_count == 1


def test_quick_respects_concurrency_lock_409_no_llm(flask_app, fake_llm):
    locks = flask_app.config["MATBOT_TURN_LOCKS"]
    assert locks.try_acquire("quick-http-sess") is True   # simulira turn u toku
    try:
        c = _authed(flask_app)
        r = c.post("/api/ai-tutor/chat", json=http_payload())
        assert r.status_code == 409
        assert fake_llm.call_count == 0
    finally:
        locks.release("quick-http-sess")


def test_quick_oversized_message_rejected_before_llm(flask_app, fake_llm):
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat", json=http_payload(msg="x" * 5000))
    assert r.status_code == 200
    assert "preduga" in r.get_json()["answer"]
    assert fake_llm.call_count == 0


def test_quick_llm_error_returns_safe_message_without_status(flask_app, fake_llm):
    fake_llm.queue(LLMTimeout("t"))
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat", json=http_payload())
    assert r.status_code == 200
    j = r.get_json()
    assert j["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in j
    assert "next_state" not in j
    assert fake_llm.call_count == 1   # jedan (neuspješan) poziv, bez repair-a


# ---------------------------------------------------------------------------
# REGRESIJA: Practice i Explain netaknuti (eksplicitni dokazi pored pune suite)
# ---------------------------------------------------------------------------

def test_practice_still_works_and_is_not_routed_through_quick(flask_app, fake_llm):
    """Practice ide JEDINIM motorom; za ovu lekciju to je deterministicka
    strategija (nula poziva). Quick ima svoj put i ne dodiruje se."""
    queue_two_call(fake_llm)
    c = _authed(flask_app)
    payload = http_payload(
        mode="practice", msg="Daj mi jedan zadatak za vježbu iz ove teme.",
        selected_topic="6-04-007",
    )
    r = c.post("/api/ai-tutor/chat", json=payload)
    assert r.status_code == 200
    j = r.get_json()
    assert j["session_mode"] == "practice"
    assert j["last_tutor_task"]                       # Practice I DALJE prati zadatak
    assert j["next_state"].get("task", {}).get("question")
    # 6-04-007 ima potpun serverski generator -> deterministicka ruta,
    # nula modelskih poziva. Quick put se i dalje ne dodiruje.
    assert fake_llm.call_count == 0
    assert len(fake_llm.quick_calls) == 0
    assert len(fake_llm.quick_calls) == 0              # practice NIJE išao kroz quick put


def test_explain_still_works_and_is_not_routed_through_quick(flask_app, fake_llm):
    from tests.conftest import make_explain_output
    fake_llm.queue(make_explain_output(reply="Evo objašnjenja teme."))
    c = _authed(flask_app)
    payload = http_payload(mode="explain", msg="Objasni mi ovu temu.")
    r = c.post("/api/ai-tutor/chat", json=payload)
    assert r.status_code == 200
    j = r.get_json()
    assert j["session_mode"] == "explain"
    assert fake_llm.call_count == 1
    assert len(fake_llm.quick_calls) == 0


# ---------------------------------------------------------------------------
# matbot/rules.py integracija
# ---------------------------------------------------------------------------

def test_quick_turn_instructions_include_topic_rules_for_real_lesson():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$x=5$."))
    run_quick_turn(fake, quick_turn_payload(selected_topic="6-04-001"))  # Razlomci lekcija
    instructions, _ = fake.calls[0]
    assert "OBLAST — RAZLOMCI" in instructions
    assert "DOMEN I SIGURNOST" in instructions


def test_quick_turn_off_topic_answer_text_is_in_instructions():
    from matbot.rules import OFF_TOPIC_ANSWER

    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Ovo je van matematike."))
    run_quick_turn(fake, quick_turn_payload(msg="Ko je pobijedio prvenstvo?"))
    instructions, _ = fake.calls[0]
    assert OFF_TOPIC_ANSWER in instructions


# ---------------------------------------------------------------------------
# Konsolidacijski nalaz: Quick sada koristi ISTI centralni safety boundary
# (matbot.mathsafe.sanitize_and_validate_math_text) kao Practice/Explain.
# ---------------------------------------------------------------------------

def test_quick_raw_frac_reply_is_repaired_safely():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="\\frac{5}{6}"))
    r = run_quick_turn(fake, quick_turn_payload())
    assert r["status"] == "ready"
    assert r["answer"] == "$\\frac{5}{6}$"


def test_quick_valid_sqrt_and_units_survive_unchanged():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$54\\sqrt{3}\\,\\text{cm}^3$"))
    r = run_quick_turn(fake, quick_turn_payload())
    assert r["status"] == "ready"
    assert r["answer"] == "$54\\sqrt{3}\\,\\text{cm}^3$"


def test_quick_literal_newline_escape_not_visible():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Rezultat: $5$.\\nNapomena: zaokruženo."))
    r = run_quick_turn(fake, quick_turn_payload())
    assert r["status"] == "ready"
    assert "\\n" not in r["answer"]
    assert "\n" in r["answer"]


def test_quick_ambiguous_damaged_form_is_rejected():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="54sqrt3,textcm3"))
    r = run_quick_turn(fake, quick_turn_payload())
    assert "status" not in r
    assert r["answer"] == SAFE_ERROR_MESSAGE


def test_quick_unsafe_output_returns_safe_error_message_exact_contract():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="Vrijednost je \\sqrt{9}."))
    r = run_quick_turn(fake, quick_turn_payload())
    assert r == {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    assert "next_state" not in r


def test_quick_unsafe_output_uses_exactly_one_llm_call():
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="\\begin{cases}x=1\\end{cases}"))
    run_quick_turn(fake, quick_turn_payload())
    assert fake.call_count == 1
    assert len(fake.quick_calls) == 1


def test_quick_unsafe_output_leaks_no_practice_state(flask_app, fake_llm, store):
    from tests.conftest import make_quick_output as mqo
    fake_llm.queue(mqo(reply="Jedinica je \\text{cm}."))
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    r = c.post("/api/ai-tutor/chat", json=http_payload())
    assert r.status_code == 200
    j = r.get_json()
    assert j == {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    assert store.peek("quick-http-sess") is None
