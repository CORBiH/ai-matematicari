"""Testovi Explain moda („Objasni mi“) — FakeLLM, bez mreže.

NAPOMENA O OBIMU: fake testovi dokazuju (a) da server šalje modelu ISPRAVAN
prompt (canonical lekcija/oblast, historija, pravila) i (b) da server ispravno
obrađuje/sanitizuje odgovor i drži ugovor prema frontendu. Da li stvarni model
poštuje pravila (kraće objašnjenje, drugačiji primjer, ostanak u lekciji) —
to dokazuje samo live eval (plan u završnom izvještaju).
"""
import json

from matbot import auth, prompts
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.llm import LLMTimeout
from matbot.ratelimit import RateLimiter
from tests.conftest import FakeLLM, make_explain_output, make_output, make_task


def explain_turn_payload(msg="Objasni mi ovu temu.", **kw):
    base = {
        "session_id": "exp-sess-1",
        "grade": 6,
        "selected_topic": "6-01-006",   # Unija skupova (stvarna lekcija)
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


def http_payload(msg="Objasni mi ovu temu.", **kw):
    base = {
        "session_id": "exp-http-sess",
        "client_turn_id": "turn-1",
        "grade": 6,
        "mode": "explain",
        "entry_source": "manual_topic_choice",
        "selected_topic": "6-01-006",
        "selected_oblast": "",
        "student_message": msg,
        "conversation_history": [],
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# OSNOVNO
# ---------------------------------------------------------------------------

def test_initial_message_returns_explanation_with_full_contract():
    fake = FakeLLM()
    fake.queue(make_explain_output(
        reply="Unija skupova je skup svih elemenata koji su u A ili u B. Primjer: $A \\cup B$."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["status"] == "ready"
    assert "Unija" in r["answer"]
    assert r["session_mode"] == "explain"
    assert r["answer_verdict"] is None
    assert r["last_tutor_task"] == ""
    assert r["next_state"] == {}
    assert r["effective_topic"] == "6-01-006"


def test_prompt_uses_canonical_grade_oblast_and_lesson():
    fake = FakeLLM()
    fake.queue(make_explain_output())
    run_explain_turn(fake, explain_turn_payload())
    instructions, input_text = fake.explain_calls[0]
    assert "6. razred" in instructions
    assert "Objasni mi" in instructions
    assert "Unija skupova" in input_text                     # canonical naziv lekcije
    assert "Skupovi i skupovne operacije" in input_text      # canonical oblast iz topics.json


def test_explain_uses_exactly_one_llm_call():
    fake = FakeLLM()
    fake.queue(make_explain_output())
    run_explain_turn(fake, explain_turn_payload())
    assert fake.call_count == 1
    assert len(fake.explain_calls) == 1


def test_no_practice_state_created(flask_app, fake_llm, store):
    from tests.conftest import make_explain_output as meo
    fake_llm.queue(meo())
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    r = c.post("/api/ai-tutor/chat", json=http_payload())
    assert r.status_code == 200
    assert store.peek("exp-http-sess") is None   # NEMA Practice sesije


def test_no_practice_fields_leak_into_prompt():
    fake = FakeLLM()
    fake.queue(make_explain_output())
    run_explain_turn(fake, explain_turn_payload())
    _, input_text = fake.explain_calls[0]
    assert "INTERNI OČEKIVANI ODGOVOR" not in input_text
    assert "HINT NIVO" not in input_text
    assert "NEDAVNI ZADACI" not in input_text


def test_prompt_keeps_lesson_name_and_forbids_renaming_topic():
    instructions = prompts.build_explain_instructions(6)
    assert "NAZIV LEKCIJE" in instructions
    assert "NE preimenuj temu" in instructions
    assert "usputni primjer" in instructions


def test_prompt_requires_consecutive_step_numbering():
    instructions = prompts.build_explain_instructions(6)
    assert "UZASTOPNO" in instructions
    assert "bez ponavljanja" in instructions
    assert "preskakanja" in instructions


def test_prompt_forbids_empty_closing_phrases_and_requires_math_conclusion():
    instructions = prompts.build_explain_instructions(6)
    assert "Tu stajemo" in instructions          # navedeno kao ZABRANJENA fraza
    assert "MATEMATIČKIM zaključkom" in instructions


def test_instructions_do_not_themselves_contain_the_banned_closing_phrasing():
    """Ranije je pravilo o prvom objašnjenju doslovno sadržavalo „i tu stani“,
    što je model mogao pokupiti kao stil završetka („Tu stajemo“). Ta
    formulacija se više ne smije pojaviti kao UPUTA — jedino spominjanje smije
    biti u zabrani."""
    instructions = prompts.build_explain_instructions(6)
    assert "i tu stani" not in instructions
    assert instructions.count("Tu stajemo") == 1   # samo u zabrani


def test_prompt_limits_followup_length_but_allows_full_procedure():
    instructions = prompts.build_explain_instructions(6)
    assert "140 riječi" in instructions
    assert "osim prvog" in instructions
    assert "cijeli postupak" in instructions


def test_new_style_rules_present_for_every_grade():
    for grade in (6, 7, 8, 9):
        instructions = prompts.build_explain_instructions(grade)
        assert "NAZIV LEKCIJE" in instructions
        assert "UZASTOPNO" in instructions
        assert "MATEMATIČKIM zaključkom" in instructions
        assert "140 riječi" in instructions


def test_first_turn_prompt_says_give_initial_explanation():
    fake = FakeLLM()
    fake.queue(make_explain_output())
    run_explain_turn(fake, explain_turn_payload())
    _, input_text = fake.explain_calls[0]
    assert "početak razgovora" in input_text


# ---------------------------------------------------------------------------
# FOLLOW-UP
# ---------------------------------------------------------------------------

HISTORY = [
    {"role": "user", "content": "Objasni mi ovu temu."},
    {"role": "assistant", "content": "Unija skupova... Primjer: $A = \\{1,2\\}$, $B = \\{2,3\\}$, $A \\cup B = \\{1,2,3\\}$."},
]


def test_ne_razumijem_sends_relevant_history_to_model():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Hajde još jednostavnije: zamisli dvije kutije..."))
    run_explain_turn(fake, explain_turn_payload(msg="Ne razumijem.", conversation_history=HISTORY))
    _, input_text = fake.explain_calls[0]
    assert "KRATKA HISTORIJA" in input_text
    assert "A \\cup B" in input_text
    assert "PORUKA UČENIKA: Ne razumijem." in input_text


def test_objasni_jednostavnije_stays_in_same_lesson():
    fake = FakeLLM()
    fake.queue(make_explain_output())
    r = run_explain_turn(fake, explain_turn_payload(msg="Objasni jednostavnije.", conversation_history=HISTORY))
    _, input_text = fake.explain_calls[0]
    assert "Unija skupova" in input_text          # lekcija ostaje ista u promptu
    assert r["effective_topic"] == "6-01-006"     # topic se ne mijenja


def test_moze_primjer_returns_worked_example():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Evo primjera: $A = \\{1,5\\}$, $B = \\{5,9\\}$, pa je $A \\cup B = \\{1,5,9\\}$."))
    r = run_explain_turn(fake, explain_turn_payload(msg="Može primjer?", conversation_history=HISTORY))
    assert "\\cup" in r["answer"]
    assert r["last_tutor_task"] == ""             # primjer NIJE zadatak


def test_jos_jedan_primjer_previous_example_visible_and_rule_present():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Novi primjer: $C = \\{4,7\\}$..."))
    run_explain_turn(fake, explain_turn_payload(msg="Može još jedan primjer?", conversation_history=HISTORY))
    instructions, input_text = fake.explain_calls[0]
    assert "\\{1,2\\}" in input_text or "{1,2}" in input_text  # prethodni primjer u historiji
    assert "DRUGAČIJI" in instructions                          # pravilo: drugi primjer, druge vrijednosti


def test_objasni_drugi_korak_uses_previous_context():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Drugi korak znači da..."))
    run_explain_turn(fake, explain_turn_payload(msg="Objasni drugi korak.", conversation_history=HISTORY))
    instructions, input_text = fake.explain_calls[0]
    assert "KRATKA HISTORIJA" in input_text
    assert "objasni drugi korak" in instructions.lower()  # pravilo o koraku postoji


def test_pokazi_cijeli_postupak_does_not_create_practice_task():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Cijeli postupak: prvo..., zatim..., na kraju $A \\cup B = \\{1,2,3\\}$."))
    r = run_explain_turn(fake, explain_turn_payload(msg="Pokaži cijeli postupak.", conversation_history=HISTORY))
    assert r["last_tutor_task"] == ""
    assert r["next_state"] == {}
    assert "task" not in r["next_state"]


def test_question_with_number_is_not_graded():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Broj 12 je u nazivniku zato što..."))
    r = run_explain_turn(fake, explain_turn_payload(
        msg="Zašto je 12 u nazivniku?", conversation_history=HISTORY))
    assert r["answer_verdict"] is None


def test_off_topic_question_does_not_change_selected_topic():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="To je druga tema — kratko: ... Ako želiš, izaberi tu lekciju."))
    r = run_explain_turn(fake, explain_turn_payload(
        msg="Kako se računa površina kruga?", conversation_history=HISTORY))
    assert r["effective_topic"] == "6-01-006"   # ostaje izabrana lekcija


def test_continuing_explanation_carries_last_tutor_message():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Nastavljam..."))
    run_explain_turn(fake, explain_turn_payload(
        msg="može", interaction_phase="continuing_explanation",
        last_tutor_message="Unija skupova je... Želiš li nastavak?"))
    _, input_text = fake.explain_calls[0]
    assert "TVOJA ZADNJA PORUKA" in input_text
    assert "interaction_phase=continuing_explanation" in input_text


def test_malformed_history_items_are_skipped_safely():
    fake = FakeLLM()
    fake.queue(make_explain_output())
    bad_history = [
        "samo string", 42, None,
        {"role": "user"},                       # bez contenta
        {"role": "hacker", "content": "x"},     # nepoznata rola
        {"role": "user", "content": "validna poruka"},
    ]
    run_explain_turn(fake, explain_turn_payload(conversation_history=bad_history))
    _, input_text = fake.explain_calls[0]
    assert "validna poruka" in input_text
    assert "samo string" not in input_text
    assert "hacker" not in input_text


# ---------------------------------------------------------------------------
# API I SIGURNOST
# ---------------------------------------------------------------------------

def _authed(flask_app):
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    return c


def test_explain_works_via_chat_endpoint(flask_app, fake_llm):
    fake_llm.queue(make_explain_output(reply="Evo objašnjenja teme."))
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat", json=http_payload())
    assert r.status_code == 200
    j = r.get_json()
    assert j["session_mode"] == "explain"
    assert j["status"] == "ready"
    assert j["last_tutor_task"] == ""
    assert j["answer_verdict"] is None
    assert fake_llm.call_count == 1


def test_explain_works_via_sse_stream_endpoint(flask_app, fake_llm):
    fake_llm.queue(make_explain_output(reply="Evo objašnjenja."))
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat/stream", json=http_payload())
    assert r.status_code == 200
    assert r.content_type.startswith("text/event-stream")
    body = r.get_data(as_text=True)
    assert body.startswith("event: done\ndata: ")
    data = json.loads(body.split("data: ", 1)[1].strip())
    assert data["session_mode"] == "explain"
    assert fake_llm.call_count == 1


def test_explain_requires_token_401_no_llm(flask_app, fake_llm):
    c = flask_app.test_client()   # bez tokena
    r = c.post("/api/ai-tutor/chat", json=http_payload())
    assert r.status_code == 401
    assert fake_llm.call_count == 0


def test_explain_counts_toward_session_rate_limit(flask_app, fake_llm):
    flask_app.config["MATBOT_SESSION_LIMITER"] = RateLimiter(per_minute=1, per_hour=150)
    c = _authed(flask_app)
    fake_llm.queue(make_explain_output())
    assert c.post("/api/ai-tutor/chat", json=http_payload()).status_code == 200
    r = c.post("/api/ai-tutor/chat", json=http_payload(msg="Objasni jednostavnije."))
    assert r.status_code == 429
    assert fake_llm.call_count == 1   # drugi poziv NIJE stigao do LLM-a


def test_explain_respects_concurrency_lock_409_no_llm(flask_app, fake_llm):
    locks = flask_app.config["MATBOT_TURN_LOCKS"]
    assert locks.try_acquire("exp-http-sess") is True   # simulira turn u toku
    try:
        c = _authed(flask_app)
        r = c.post("/api/ai-tutor/chat", json=http_payload())
        assert r.status_code == 409
        assert fake_llm.call_count == 0
    finally:
        locks.release("exp-http-sess")


def test_explain_invalid_topic_for_grade_rejected(flask_app, fake_llm):
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat", json=http_payload(selected_topic="9-07-114"))  # 9. razred tema uz grade 6
    assert r.status_code == 400
    assert r.get_json()["error"] == "UNKNOWN_TOPIC"
    assert fake_llm.call_count == 0


def test_explain_client_oblast_not_trusted_canonical_used(flask_app, fake_llm):
    fake_llm.queue(make_explain_output())
    c = _authed(flask_app)
    r = c.post("/api/ai-tutor/chat", json=http_payload(selected_oblast="Izmisljena Oblast X"))
    assert r.status_code == 200
    _, input_text = fake_llm.explain_calls[0]
    assert "Izmisljena Oblast X" not in input_text
    assert "Skupovi i skupovne operacije" in input_text


def test_explain_llm_error_returns_safe_message_without_status(flask_app, fake_llm):
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
# MATHJAX
# ---------------------------------------------------------------------------

def test_valid_latex_passes_through_unchanged():
    fake = FakeLLM()
    reply = "Proširivanje: $\\frac{2}{3} = \\frac{8}{12}$. Pomnožili smo sa 4."
    fake.queue(make_explain_output(reply=reply))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["answer"] == reply


def test_control_character_frac_repaired_in_explain():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Primjer: $\x0crac{2}{3}$ se proširi."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert "$\\frac{2}{3}$" in r["answer"]
    assert not any(ord(ch) < 0x20 and ch not in ("\n", "\t") for ch in r["answer"])


def test_unbalanced_math_segment_stripped_not_broken():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Pogledaj $\\frac{2}{3 i nastavi dalje."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["answer"].count("$") % 2 == 0


def test_explain_response_has_no_control_chars_anywhere():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Red 1.\nRed 2 sa $\x0crac{1}{2}$."))
    r = run_explain_turn(fake, explain_turn_payload())
    raw = json.dumps(r, ensure_ascii=False)
    for value in json.loads(raw).values():
        if isinstance(value, str):
            assert not any(ord(ch) < 0x20 and ch not in ("\n", "\t") for ch in value)


# ---------------------------------------------------------------------------
# REGRESIJA: Practice netaknut (eksplicitni dokazi pored pune suite)
# ---------------------------------------------------------------------------

def test_practice_still_works_and_uses_one_call(flask_app, fake_llm):
    """Practice koristi STABILAN jednopozivni put (podrazumijevano nakon
    rollbacka); Explain ima svoj i ne dodiruje se — to je ovdje i poenta."""
    fake_llm.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    c = _authed(flask_app)
    payload = http_payload(mode="practice", msg="Daj mi jedan zadatak za vježbu iz ove teme.")
    r = c.post("/api/ai-tutor/chat", json=payload)
    assert r.status_code == 200
    j = r.get_json()
    assert j["session_mode"] == "practice"
    assert j["last_tutor_task"]                       # Practice I DALJE prati zadatak
    assert j["next_state"].get("task", {}).get("question")
    assert fake_llm.call_count == 1
    assert len(fake_llm.tutor_calls) == 0   # univerzalni put nije aktivan
    assert len(fake_llm.explain_calls) == 0           # practice NIJE išao kroz explain put


# ---------------------------------------------------------------------------
# matbot/rules.py integracija
# ---------------------------------------------------------------------------

def test_explain_turn_instructions_include_topic_rules_for_real_lesson():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Evo objašnjenja."))
    run_explain_turn(fake, explain_turn_payload(selected_topic="6-04-001"))  # Razlomci lekcija
    instructions, _ = fake.calls[0]
    assert "OBLAST — RAZLOMCI" in instructions
    assert "DOMEN I SIGURNOST" in instructions


def test_explain_turn_off_topic_answer_text_is_in_instructions():
    from matbot.rules import OFF_TOPIC_ANSWER

    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Ovo je van matematike."))
    run_explain_turn(fake, explain_turn_payload(msg="Ko je pobijedio prvenstvo?"))
    instructions, _ = fake.calls[0]
    assert OFF_TOPIC_ANSWER in instructions


# ---------------------------------------------------------------------------
# Konsolidacijski nalaz: Explain sada koristi ISTI centralni safety boundary
# (matbot.mathsafe.sanitize_and_validate_math_text) kao Practice — prije ove
# izmjene Explain je koristio stariji sanitize_math_text koji ne dotiče
# sadržaj bez ijednog $ (isti arhitekturni gap kao u 3 Practice live baga).
# ---------------------------------------------------------------------------

def test_explain_raw_frac_in_reply_is_repaired_not_rejected():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Razlomak je \\frac{5}{6}."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["status"] == "ready"
    assert r["answer"] == "Razlomak je $\\frac{5}{6}$."


def test_explain_literal_newline_escape_does_not_remain_visible():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Prvi dio.\\nDrugi dio nakon preloma."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["status"] == "ready"
    assert "\\n" not in r["answer"]
    assert "\n" in r["answer"]


def test_explain_valid_sqrt_survives_unchanged():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Uprošćeno: $2\\sqrt{5}$."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["status"] == "ready"
    assert r["answer"] == "Uprošćeno: $2\\sqrt{5}$."


def test_explain_ambiguous_damaged_form_never_reaches_answer():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Rezultat je 2sqrt5 otprilike."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert "status" not in r
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "sqrt" not in r["answer"]


def test_explain_unsafe_output_returns_safe_error_message_exact_contract():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Jedinica je \\text{cm}."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r == {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    assert "status" not in r
    assert "next_state" not in r


# ---------------------------------------------------------------------------
# Faza A3 (docs/CURRENT_STATE.md C-10): Explain sada poziva ISTI transport
# normalizator kao Quick (normalize_result_math_transport) PRIJE centralnog
# safety boundary-a — ranije je Explain isti oblik izlaza odbijao cio.
# ---------------------------------------------------------------------------

def test_case19_proper_inline_math_unchanged_through_transport_normalization():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Rezultat je $\\frac{3}{4}$."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["status"] == "ready"
    assert r["answer"] == "Rezultat je $\\frac{3}{4}$."


def test_case20_proper_display_math_unchanged_through_transport_normalization():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Formula: $$P=\\frac{a\\cdot h}{2}$$ je površina."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["status"] == "ready"
    assert r["answer"] == "Formula: $$P=\\frac{a\\cdot h}{2}$$ je površina."


def test_case21_clearly_mathematical_escaped_dollar_is_repaired():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Rezultat je \\$\\frac{3}{4}\\$ ukupno."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["status"] == "ready"
    assert r["answer"] == "Rezultat je $\\frac{3}{4}$ ukupno."


def test_case22_currency_is_not_converted():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Cijena je \\$5, a ostatak je 12."))
    r = run_explain_turn(fake, explain_turn_payload())
    assert r["status"] == "ready"
    assert r["answer"] == "Cijena je \\$5, a ostatak je 12."


def test_case23_explain_uses_exactly_one_call_with_transport_repair():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="Rezultat je \\$\\frac{3}{4}\\$."))
    run_explain_turn(fake, explain_turn_payload())
    assert fake.call_count == 1
    assert len(fake.explain_calls) == 1


def test_explain_unsafe_output_uses_exactly_one_llm_call_no_repair_call():
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="\\begin{cases}x=1\\end{cases}"))
    run_explain_turn(fake, explain_turn_payload())
    assert fake.call_count == 1
    assert len(fake.explain_calls) == 1


def test_explain_unsafe_output_creates_no_practice_state(flask_app, fake_llm, store):
    from tests.conftest import make_explain_output as meo
    fake_llm.queue(meo(reply="Rezultat je \\sqrt{20}."))
    c = flask_app.test_client()
    c.environ_base["HTTP_X_TUTOR_TOKEN"] = auth.issue_token()
    r = c.post("/api/ai-tutor/chat", json=http_payload())
    assert r.status_code == 200
    j = r.get_json()
    assert j == {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    assert store.peek("exp-http-sess") is None


# ---------------------------------------------------------------------------
# Faza C (docs/CURRENT_STATE.md C-2): budžet historije po poziciji stavke —
# najnoviji odgovor tutora (do 1200, čuva KRAJ), najnovija učenikova poruka
# prije trenutne (do 600, čuva POČETAK), starije stavke (do 250, nepromijenjeno).
# ---------------------------------------------------------------------------

def _long_tutor_reply(tail_marker, filler_sentences=40):
    filler = "Prvi korak objašnjava zašto tražimo zajednički nazivnik. " * filler_sentences
    return filler + tail_marker


def test_case30_latest_assistant_message_gets_larger_budget():
    long_reply = _long_tutor_reply("KONAČAN REZULTAT je $\\frac{16}{60}$.")
    history = [
        {"role": "user", "content": "Objasni mi ovu temu."},
        {"role": "assistant", "content": long_reply},
    ]
    text = prompts.build_explain_input("Lekcija", "Oblast", history, "Objasni zadnji dio.")
    tutor_line = next(l for l in text.split("\n") if l.startswith("Ti:"))
    assert len(long_reply) > 250          # sirov odgovor je duži od "starije" granice
    assert "KONAČAN REZULTAT" in tutor_line
    assert "$\\frac{16}{60}$" in tutor_line
    assert len(tutor_line) <= len("Ti: ") + prompts.HISTORY_LATEST_ASSISTANT_CHARS + 1


def test_case31_latest_prior_user_message_is_preserved():
    history = [
        {"role": "assistant", "content": "Prvo objašnjenje."},
        {"role": "user", "content": "Zašto je to tako? " * 20},
    ]
    text = prompts.build_explain_input("Lekcija", "Oblast", history, "Nastavak pitanja.")
    user_lines = [l for l in text.split("\n") if l.startswith("Učenik:")]
    assert len(user_lines) == 1
    assert "Zašto je to tako?" in user_lines[0]
    assert len(user_lines[0]) <= len("Učenik: ") + prompts.HISTORY_LATEST_USER_CHARS + 1


def test_case32_older_history_items_stay_bounded():
    history = [
        {"role": "user", "content": "Staro pitanje. " * 40},
        {"role": "assistant", "content": "Stari odgovor. " * 40},
        {"role": "user", "content": "Novije pitanje."},
        {"role": "assistant", "content": "Najnoviji odgovor."},
    ]
    text = prompts.build_explain_input("Lekcija", "Oblast", history, "Poruka.")
    lines = [l for l in text.split("\n") if l.startswith("Učenik:") or l.startswith("Ti:")]
    # prve dvije stavke (starije) ostaju na ograničenju od 250 znakova sadržaja
    assert len(lines[0]) <= len("Učenik: ") + prompts.HISTORY_OLDER_ITEM_CHARS + 1
    assert len(lines[1]) <= len("Ti: ") + prompts.HISTORY_OLDER_ITEM_CHARS + 1


def test_case33_total_history_section_stays_bounded():
    history = [
        {"role": "user", "content": "x" * 1200},
        {"role": "assistant", "content": "y" * 1200},
        {"role": "user", "content": "z" * 1200},
        {"role": "assistant", "content": "w" * 1200},
    ]
    text = prompts.build_explain_input("Lekcija", "Oblast", history, "Poruka.")
    history_start = text.index("KRATKA HISTORIJA:")
    history_end = text.index("PORUKA UČENIKA:")
    history_section = text[history_start:history_end]
    # najgori realan zbir (vidi komentar iznad HISTORY_LATEST_ASSISTANT_CHARS
    # u matbot/prompts.py): 1200 + 600 + 2*250 (uz role-prefikse) ostaje ispod
    # 3000 znakova za CIJELU sekciju historije.
    assert len(history_section) < 3000


def test_case34_mathjax_never_cut_mid_delimiter():
    long_reply = _long_tutor_reply(
        "Rezultat: $$P=\\frac{a\\cdot h}{2}$$ i konkretno $x=5$ na kraju.")
    history = [
        {"role": "user", "content": "Objasni."},
        {"role": "assistant", "content": long_reply},
    ]
    text = prompts.build_explain_input("Lekcija", "Oblast", history, "Nastavak.")
    tutor_line = next(l for l in text.split("\n") if l.startswith("Ti:"))
    assert tutor_line.count("$") % 2 == 0
    assert "$$P=\\frac{a\\cdot h}{2}$$" in tutor_line
    assert "$x=5$" in tutor_line


def test_case34b_oversized_single_mathjax_block_is_omitted_whole():
    """Ako je jedan blok veći od cijelog budžeta, nema sigurnog parcijalnog
    reza: oznaka izostavljanja je bolja od neparnog '$' u promptu."""
    huge_inline = "$" + ("1+" * 700) + "1$"
    huge_display = "$$" + ("2+" * 700) + "2$$"

    head = prompts._clip_head_preserving_math(huge_inline, 250)
    tail = prompts._clip_tail_preserving_math(huge_display, 1200)

    assert head == "…"
    assert tail == "…"
    assert head.count("$") % 2 == 0
    assert tail.count("$") % 2 == 0


def test_case35_followup_can_reference_late_step_of_prior_answer():
    long_reply = _long_tutor_reply(
        "Zadnji, TREĆI korak: dijelimo sa 3 da dobijemo konačan rezultat $x=4$.")
    history = [
        {"role": "user", "content": "Objasni mi ovu temu."},
        {"role": "assistant", "content": long_reply},
    ]
    fake = FakeLLM()
    fake.queue(make_explain_output(reply="U trećem koraku dijelimo sa 3 jer..."))
    run_explain_turn(fake, explain_turn_payload(
        msg="Zašto si u trećem koraku podijelio sa 3?", conversation_history=history))
    _, input_text = fake.explain_calls[0]
    assert "TREĆI korak" in input_text
    assert "$x=4$" in input_text


def test_case36_history_message_ordering_is_preserved():
    history = [
        {"role": "user", "content": "Prvo pitanje."},
        {"role": "assistant", "content": "Prvi odgovor."},
        {"role": "user", "content": "Drugo pitanje."},
        {"role": "assistant", "content": "Drugi odgovor."},
    ]
    text = prompts.build_explain_input("Lekcija", "Oblast", history, "Treće pitanje.")
    assert text.index("Prvo pitanje.") < text.index("Prvi odgovor.")
    assert text.index("Prvi odgovor.") < text.index("Drugo pitanje.")
    assert text.index("Drugo pitanje.") < text.index("Drugi odgovor.")
    assert text.index("Drugi odgovor.") < text.index("PORUKA UČENIKA: Treće pitanje.")


def test_case37_empty_and_malformed_history_stays_safe():
    fake = FakeLLM()
    fake.queue(make_explain_output())
    run_explain_turn(fake, explain_turn_payload(conversation_history=[]))
    _, input_text = fake.explain_calls[0]
    assert "HISTORIJA: ovo je početak razgovora" in input_text

    # build_explain_input samo direktno testira granični slučaj prazne liste
    # (malformed stavke se već filtriraju u explain._clean_history — vidi
    # test_malformed_history_items_are_skipped_safely iznad).
    text = prompts.build_explain_input("Lekcija", "Oblast", [], "Poruka.")
    assert "HISTORIJA: ovo je početak razgovora" in text
