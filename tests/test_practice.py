"""Testovi orkestracije Practice turna (fake LLM, bez mreže)."""
import copy
import json

from tests.conftest import FakeLLM, make_options, make_output, make_task
from matbot import prompts
from matbot.llm import LLMTimeout, LLMUnavailable
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.schema import PracticeTurnOutput
from matbot.session_store import SessionStore


def turn_payload(msg="Daj mi jedan zadatak za vježbu iz ove teme.", **kw):
    base = {
        "session_id": "sess-1",
        "grade": 6,
        "selected_topic": "6-01-006",   # Unija skupova (stvarna lekcija iz topics.json)
        "selected_oblast": "",
        "student_message": msg,
        "intent": "",
        "difficulty_request": "",
        "interaction_phase": "",
        "last_tutor_task": "",
    }
    base.update(kw)
    return base


def start_session(store, fake, task_text="Skrati razlomak $\\frac{20}{32}$.", expected="5/8"):
    """Prvi turn: model vraća početni zadatak."""
    fake.queue(make_output(reply="Evo zadatka za tebe.",
                           new_task=make_task(text=task_text, expected=expected)))
    return run_practice_turn(store, fake, turn_payload())


def test_session_start_generates_one_task():
    store, fake = SessionStore(), FakeLLM()
    r = start_session(store, fake)
    assert r["status"] == "ready"
    assert fake.practice_call_count == 1
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."
    assert "Skrati razlomak" in r["answer"]
    assert r["session_mode"] == "practice"
    assert r["effective_topic"] == "6-01-006"


def test_one_llm_call_per_normal_turn():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Tačno!", evaluation="correct"))
    run_practice_turn(store, fake, turn_payload(msg="5/8"))
    assert fake.practice_call_count == 2  # tačno 1 poziv po turnu, 2 turna


def test_text_answer_is_never_graded_even_when_model_says_correct():
    """Novi ugovor: tekst NIJE pokušaj odgovora, čak i kad model (pogrešno)
    postavi evaluation='correct' — grading ide isključivo kroz choice_answer
    (vidi test_choice_answer.py). Model 'evaluation' se ignoriše."""
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Tačno! $20:4=5$, $32:4=8$.", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(msg="20/32 = 5/8"))
    assert r["answer_verdict"] is None
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."  # nema auto novog zadatka
    assert r["next_state"]["correct_streak"] == 0
    sess = store.peek("sess-1")
    assert sess["current_task"] == "Skrati razlomak $\\frac{20}{32}$."


def test_fraction_and_sentence_text_messages_are_never_graded():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Da, upravo tako — oba se skrate na $\\frac{5}{8}$.", evaluation="correct"))
    r1 = run_practice_turn(store, fake, turn_payload(msg="da, jer se oba skrate na isti razlomak"))
    assert r1["answer_verdict"] is None
    fake.queue(make_output(reply="Blizu — brojnik je dobar, nazivnik nije.", evaluation="partially_correct"))
    r2 = run_practice_turn(store, fake, turn_payload(msg="mislim da je 5/6"))
    assert r2["answer_verdict"] is None


def test_incorrect_text_message_does_not_reset_streak():
    """correct_streak se sada mijenja ISKLJUČIVO kroz choice_answer — tekstualna
    poruka (čak i ona koja liči na netačan pokušaj) je ne dira."""
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Tačno!", evaluation="correct"))
    run_practice_turn(store, fake, turn_payload(msg="5/8"))
    fake.queue(make_output(reply="Nije — pogledaj nazivnik.", evaluation="incorrect"))
    r = run_practice_turn(store, fake, turn_payload(msg="mozda 6"))
    assert r["answer_verdict"] is None
    assert r["next_state"]["correct_streak"] == 0
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."


def test_ne_znam_raises_hint_level_keeps_task():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Pogledaj koji broj dijeli i 20 i 32.", gave_hint=True))
    r = run_practice_turn(store, fake, turn_payload(msg="ne znam"))
    assert r["answer_verdict"] is None       # "ne znam" NIJE netačan odgovor
    assert r["next_state"]["hint_level"] == 1
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."


def test_second_hint_is_progressive_and_prompt_carries_level():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Hint 1.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="ne znam"))
    fake.queue(make_output(reply="Hint 2: podijeli sa 4.", gave_hint=True))
    r = run_practice_turn(store, fake, turn_payload(msg="daj mi hint", intent="hint_request"))
    assert r["next_state"]["hint_level"] == 2
    # prompt za drugi hint nosi trenutni nivo 1 → model zna da bude konkretniji
    assert "TRENUTNI HINT NIVO: 1" in fake.practice_calls[2][1]


def test_question_about_task_keeps_task_no_evaluation():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Vrijednost se ne mijenja jer množimo i brojnik i nazivnik istim brojem."))
    r = run_practice_turn(store, fake, turn_payload(msg="zašto se vrijednost ne mijenja?"))
    assert r["answer_verdict"] is None
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."
    assert store.peek("sess-1")["hint_level"] == 0


def test_number_in_message_that_is_not_an_answer():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Imaš još vremena — hoćeš da krenemo od brojnika?"))
    r = run_practice_turn(store, fake, turn_payload(msg="imam kontrolni za 5 dana"))
    assert r["answer_verdict"] is None
    assert r["next_state"]["correct_streak"] == 0
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."


def test_new_task_request_replaces_task_and_matches_last_tutor_task():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    # Drugi zadatak mora imati DRUGAČIJI pedagoški oblik od prvog — server
    # dodjeljuje drugu porodicu, pa „isto pitanje s drugim brojevima“ pada na
    # pedagogical_shape zaštitu (vidi task_families.is_duplicate_shape).
    fake.queue(make_output(reply="Može, evo novog.",
                           new_task=make_task(text="Dopuni: $7 + \\square = 15$.", expected="8",
                                              options=make_options("8", "7", "9", "22"))))
    r = run_practice_turn(store, fake, turn_payload(msg="daj novi zadatak"))
    assert r["last_tutor_task"] == "Dopuni: $7 + \\square = 15$."
    assert r["next_state"]["task"]["question"] == "Dopuni: $7 + \\square = 15$."
    assert "Dopuni: $7 + \\square = 15$." in r["answer"]
    sess = store.peek("sess-1")
    assert sess["expected_answer_summary"] == "8"
    assert sess["hint_level"] == 0
    assert sess["recent_tasks"][-1] == "Dopuni: $7 + \\square = 15$."


def test_task_hidden_only_in_reply_does_not_change_active_task():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    # model "prokrijumčari" drugi zadatak u reply, ali new_task = null
    fake.queue(make_output(reply="Evo ti drugi: izračunaj $7 \\cdot 8$."))
    r = run_practice_turn(store, fake, turn_payload(msg="ok"))
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."  # NE mijenja se
    assert store.peek("sess-1")["current_task"] == "Skrati razlomak $\\frac{20}{32}$."


def test_easier_and_harder_requests_flow_to_prompt():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    # Svaki naredni zadatak dolazi iz DRUGE porodice, pa mora imati i drugačiji
    # pedagoški oblik (inače ga hvata is_duplicate_shape).
    fake.queue(make_output(reply="Evo lakšeg.",
                           new_task=make_task(text="Izračunaj $4+5$.", expected="9",
                                              options=make_options("9", "4", "5", "10"),
                                              difficulty="easy")))
    r1 = run_practice_turn(store, fake, turn_payload(msg="Daj mi lakši zadatak.", difficulty_request="easier"))
    assert "difficulty_request=easier" in fake.practice_calls[1][1]
    assert store.peek("sess-1")["difficulty"] == "easy"
    fake.queue(make_output(reply="Evo težeg.",
                           new_task=make_task(
                               text="Izračunaj $18\\cdot7-9$.", expected="117",
                               options=make_options("117", "126", "108", "135"),
                               difficulty="hard")))
    r2 = run_practice_turn(store, fake, turn_payload(msg="Daj mi teži zadatak.", difficulty_request="harder"))
    assert "difficulty_request=harder" in fake.practice_calls[2][1]
    assert store.peek("sess-1")["difficulty"] == "hard"
    assert r1["last_tutor_task"] != r2["last_tutor_task"]


def test_expected_answer_is_aid_sent_to_prompt_but_text_never_graded():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Zapravo si u pravu — provjerio sam ponovo.", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))
    # prompt sadrži interni očekivani odgovor kao POMOĆ...
    assert "INTERNI OČEKIVANI ODGOVOR" in fake.practice_calls[1][1]
    assert "5/8" in fake.practice_calls[1][1]
    # ...ali tekstualna poruka se NIKAD ne ocjenjuje (grading ide kroz choice_answer)
    assert r["answer_verdict"] is None


def test_timeout_does_not_change_state():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    before = store.peek("sess-1")
    fake.queue(LLMTimeout("timeout"))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r and "next_state" not in r
    assert store.peek("sess-1") == before


def test_llm_unavailable_does_not_change_state():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    before = store.peek("sess-1")
    fake.queue(LLMUnavailable("boom"))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("sess-1") == before


def test_invalid_output_empty_reply_does_not_change_state():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    before = store.peek("sess-1")
    fake.queue(make_output(reply="   "))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("sess-1") == before


def test_invalid_output_task_without_expected_answer_rejected():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    before = store.peek("sess-1")
    bad = PracticeTurnOutput(
        reply="Evo novog.", evaluation=None, gave_hint=False,
        new_task={
            "text": "Novi zadatak.", "expected_answer": "  ", "difficulty": "easy",
            "options": [{"text": "1"}, {"text": "2"}, {"text": "3"}, {"text": "4"}],
            "correct_option_index": 0,
        },
    )
    fake.queue(bad)
    r = run_practice_turn(store, fake, turn_payload(msg="daj novi"))
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("sess-1") == before


def test_recent_tasks_flow_into_prompt_for_anti_repeat():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Evo novog.",
                           new_task=make_task(text="Skrati $\\frac{18}{24}$.", expected="3/4")))
    run_practice_turn(store, fake, turn_payload(msg="daj novi zadatak"))
    fake.queue(make_output(reply="Evo još jednog.",
                           new_task=make_task(text="Skrati $\\frac{45}{60}$.", expected="3/4")))
    run_practice_turn(store, fake, turn_payload(msg="daj novi zadatak"))
    third_input = fake.practice_calls[2][1]
    assert "NEDAVNI ZADACI" in third_input
    assert "\\frac{20}{32}" in third_input and "\\frac{18}{24}" in third_input


def test_server_restart_does_not_trust_client_task_text_without_server_state():
    store, fake = SessionStore(), FakeLLM()
    # nema sesije (kao poslije restarta), ali klijent šalje last_tutor_task
    fake.queue(make_output(reply="Tačno!", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(
        msg="5/8", last_tutor_task="Skrati razlomak $\\frac{20}{32}$.",
        interaction_phase="answering_practice_task"))
    assert r["answer_verdict"] is None  # tekst se nikad ne ocjenjuje
    prompt_in = fake.practice_calls[0][1]
    assert "Skrati razlomak" not in prompt_in
    assert "AKTIVNI ZADATAK: još ne postoji" in prompt_in
    assert r["last_tutor_task"] == ""
    # Interno rješenje i aktivni zadatak ne dolaze od klijenta.
    assert "INTERNI OČEKIVANI ODGOVOR" not in prompt_in


# ---------------------------------------------------------------------------
# Hardening: duboki snapshot cijele sesije prije/poslije greške (ne samo
# "store.save nije pozvan") + dokaz da last_tutor_task na grešci vraća STVARNI
# aktivni zadatak (ne prazan string), da nema drugog LLM poziva i da očekivani
# odgovor nikad ne izlazi iz servera.
# ---------------------------------------------------------------------------

def _rich_session_snapshot(store, fake):
    """Sesija sa netrivijalnim stanjem prije greške: aktivan zadatak, jedan
    prošli hint (hint_level=1), jedan streak korak i historija — tako
    snapshot poređenje stvarno nešto dokazuje (ne poredi samo prazne dict-ove)."""
    start_session(store, fake)  # current_task, recent_tasks=[task], hint_level=0
    fake.queue(make_output(reply="Pogledaj koji broj dijeli i 20 i 32.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="ne znam"))  # hint_level -> 1
    return store.peek("sess-1")


def test_llm_timeout_full_session_snapshot_unchanged():
    store, fake = SessionStore(), FakeLLM()
    before = _rich_session_snapshot(store, fake)
    before_copy = copy.deepcopy(before)
    calls_before = fake.practice_call_count

    fake.queue(LLMTimeout("timeout"))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))

    after = store.peek("sess-1")
    assert after == before_copy, "cijela sesija (current_task, expected_answer_summary, hint_level, correct_streak, recent_tasks, recent_turns) mora ostati bit-za-bit identična"
    assert after["current_task"] == before["current_task"]
    assert after["expected_answer_summary"] == before["expected_answer_summary"]
    assert after["hint_level"] == before["hint_level"]
    assert after["correct_streak"] == before["correct_streak"]
    assert after["recent_tasks"] == before["recent_tasks"]
    assert after["recent_turns"] == before["recent_turns"]

    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert "next_state" not in r
    assert r["last_tutor_task"] == before["current_task"]     # ISTINIT zadatak, ne ""
    assert fake.practice_call_count == calls_before + 1                # tačno JEDAN (neuspješan) poziv, bez repair-a
    raw = json.dumps(r, ensure_ascii=False)
    assert before["expected_answer_summary"] not in raw       # očekivani odgovor NIKAD ne izlazi


def test_invalid_structured_output_full_session_snapshot_unchanged():
    store, fake = SessionStore(), FakeLLM()
    before = _rich_session_snapshot(store, fake)
    before_copy = copy.deepcopy(before)
    calls_before = fake.practice_call_count

    # strukturno validan JSON, ali sadržajno neupotrebljiv (prazan reply)
    fake.queue(make_output(reply="   "))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))

    after = store.peek("sess-1")
    assert after == before_copy
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert "next_state" not in r
    assert r["last_tutor_task"] == before["current_task"]
    assert fake.practice_call_count == calls_before + 1
    raw = json.dumps(r, ensure_ascii=False)
    assert before["expected_answer_summary"] not in raw


def test_unexpected_exception_full_session_snapshot_unchanged():
    store, fake = SessionStore(), FakeLLM()
    before = _rich_session_snapshot(store, fake)
    before_copy = copy.deepcopy(before)
    calls_before = fake.practice_call_count

    class BoomInternalDetail(Exception):
        pass

    fake.queue(BoomInternalDetail("stack trace sa internim detaljima koje učenik ne smije vidjeti"))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))

    after = store.peek("sess-1")
    assert after == before_copy
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert "next_state" not in r
    assert r["last_tutor_task"] == before["current_task"]
    assert fake.practice_call_count == calls_before + 1
    raw = json.dumps(r, ensure_ascii=False)
    assert "BoomInternalDetail" not in raw
    assert "stack trace" not in raw
    assert before["expected_answer_summary"] not in raw


def test_mutable_session_reference_cannot_bypass_no_state_change_rule():
    """Dokazuje copy-on-write: čak i ako pozivalac (hipotetski) drži referencu
    na dict koji je load() vratio i mutira ga PRIJE greške, store i dalje ostaje
    netaknut jer store.load() nikad ne vraća objekat koji store interno drži."""
    store = SessionStore()
    session = store.load(session_id="sess-cow", grade=6, lesson_id="6-01-006",
                          lesson_title="Unija skupova", oblast="Skupovi", mode="practice")
    session["current_task"] = "Zadatak A"
    session["expected_answer_summary"] = "tajna"
    session["recent_tasks"].append("Zadatak A")
    store.save(session)

    committed_before = store.peek("sess-cow")

    # Pozivalac dobije NOVU kopiju i pokuša je mutirati BEZ pozivanja save()
    leaked_ref = store.load(session_id="sess-cow", grade=6, lesson_id="6-01-006",
                             lesson_title="Unija skupova", oblast="Skupovi", mode="practice")
    leaked_ref["current_task"] = "MUTIRANO IZVAN SAVE()"
    leaked_ref["recent_tasks"].append("MUTIRANO")
    leaked_ref["expected_answer_summary"] = "MUTIRANO"

    # store MORA ostati netaknut sve dok se eksplicitno ne pozove save()
    assert store.peek("sess-cow") == committed_before
    assert store.peek("sess-cow")["current_task"] == "Zadatak A"
    assert store.peek("sess-cow")["recent_tasks"] == ["Zadatak A"]


# ---------------------------------------------------------------------------
# Pedagoški hardening: eksplicitni zahtjev za rješenjem, progresivni hintovi,
# uklanjanje automatskog "Želiš novi zadatak?", MathJax sanitizacija.
#
# NAPOMENA O OBIMU: FakeLLM je deterministički — ovi testovi dokazuju da (a)
# PROMPT koji server šalje modelu sadrži nova pravila/primjere, i (b) da server
# ISPRAVNO OBRAĐUJE odgovor modela KAD model zaista postupi po pravilu (pravi
# zadatak ostaje aktivan, new_task=null se poštuje, hint_level se ažurira,
# MathJax sanitizacija radi). Da li će stvarni model iz ~5 primjera generalizovati
# na sve slične formulacije i stvarno prestati završavati sa "Želiš novi
# zadatak?" — to dokazuje SAMO live eval, ne fake test.
# ---------------------------------------------------------------------------

TASK_4_15 = "Proširi razlomak $\\frac{4}{15}$ tako da nazivnik bude 60."


def test_uradi_ga_ti_gives_full_procedure_and_result_keeps_task():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")
    fake.queue(make_output(
        reply="Računamo $60 : 15 = 4$. Zato i brojnik množimo sa 4: $4 \\cdot 4 = 16$. Prošireni razlomak je $\\frac{16}{60}$.",
        evaluation=None, gave_hint=True, new_task=None,
    ))
    r = run_practice_turn(store, fake, turn_payload(msg="uradi ga ti"))
    assert r["answer_verdict"] is None                 # nije bio pokušaj odgovora
    assert r["last_tutor_task"] == TASK_4_15            # zadatak OSTAJE isti
    assert "16" in r["answer"] and "60" in r["answer"]  # konačan rezultat prisutan
    assert "= 4" in r["answer"] or ": 15" in r["answer"] or "60 : 15" in r["answer"]  # postupak prisutan
    # prompt eksplicitno instruira model za ovu i slične formulacije
    instructions = fake.practice_calls[-1][0]
    for phrase in ("uradi ga ti", "pokaži rješenje", "riješi ga ti"):
        assert phrase in instructions


def test_pokazi_mi_rjesenje_does_not_return_just_another_hint():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")
    fake.queue(make_output(reply="Prvo pronađi broj kojim treba pomnožiti 15 da dobiješ 60.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="ne znam"))   # hint 1 (hint_level -> 1)
    fake.queue(make_output(
        reply="Računamo $60 : 15 = 4$, pa je $4 \\cdot 4 = 16$. Prošireni razlomak je $\\frac{16}{60}$.",
        gave_hint=True, new_task=None,
    ))
    r = run_practice_turn(store, fake, turn_payload(msg="pokaži mi rješenje"))
    assert "16" in r["answer"]              # sadrži konačan rezultat, nije samo usmjeravajući hint
    assert r["last_tutor_task"] == TASK_4_15


def test_second_hint_prompt_guidance_more_concrete_than_first():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")   # calls[0]
    fake.queue(make_output(reply="Hint 1.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="ne znam"))       # calls[1], hint_level 0 -> 1
    first_hint_prompt = fake.practice_calls[1][1]
    fake.queue(make_output(reply="Hint 2.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="daj mi hint", intent="hint_request"))  # calls[2], hint_level 1 -> 2
    second_hint_prompt = fake.practice_calls[2][1]
    assert first_hint_prompt != second_hint_prompt
    assert "HINT NIVO 1" in first_hint_prompt
    assert "bez računa" in first_hint_prompt or "BEZ računa" in first_hint_prompt
    assert "HINT NIVO 2" in second_hint_prompt
    assert "koji tačno račun" in second_hint_prompt


def test_hint_level_3_prompt_allows_full_solution():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")   # calls[0]
    fake.queue(make_output(reply="Hint 1.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="ne znam"))          # calls[1], hint_level 0 -> 1
    fake.queue(make_output(reply="Hint 2.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="daj mi hint"))      # calls[2], hint_level 1 -> 2
    fake.queue(make_output(
        reply="Računamo $60 : 15 = 4$, pa je $4 \\cdot 4 = 16$. Prošireni razlomak je $\\frac{16}{60}$.",
        gave_hint=True, new_task=None,
    ))
    r = run_practice_turn(store, fake, turn_payload(msg="daj mi hint"))  # calls[3], hint_level 2 -> 3 (cap)
    third_prompt = fake.practice_calls[3][1]
    assert "HINT NIVO 3" in third_prompt
    assert "cijeli postupak" in third_prompt.lower() or "konačan rezultat" in third_prompt
    assert "16" in r["answer"]
    assert r["next_state"]["hint_level"] == 3


def test_explicit_solution_request_does_not_generate_new_task():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")
    fake.queue(make_output(
        reply="Računamo $60 : 15 = 4$, pa je $4 \\cdot 4 = 16$. Prošireni razlomak je $\\frac{16}{60}$.",
        evaluation=None, gave_hint=True, new_task=None,
    ))
    r = run_practice_turn(store, fake, turn_payload(msg="uradi cijeli zadatak"))
    assert "task" in r["next_state"]
    assert r["next_state"]["task"]["question"] == TASK_4_15


def test_active_task_unchanged_after_full_solution_reveal():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")
    fake.queue(make_output(
        reply="Rješenje: $\\frac{16}{60}$.", evaluation=None, gave_hint=True, new_task=None,
    ))
    run_practice_turn(store, fake, turn_payload(msg="reci mi odgovor"))
    assert store.peek("sess-1")["current_task"] == TASK_4_15


def test_prompt_forbids_automatic_new_task_question():
    instructions = prompts.build_instructions(6)
    assert "Želiš novi zadatak" in instructions
    assert "dugme za novi zadatak" in instructions
    # passthrough: server ne dodaje niti uklanja tekst iz replyja — provjera
    # ožičenja, ne dokaz da će model zaista prestati (to dokazuje live eval)
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Tačno! $20:4=5$, $32:4=8$.", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))
    assert "Želiš novi zadatak" not in r["answer"]


def test_unbalanced_dollar_from_model_does_not_reach_frontend_broken():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")
    fake.queue(make_output(reply="Rezultat je $16/60 skoro tačno.", evaluation="incorrect"))
    r = run_practice_turn(store, fake, turn_payload(msg="17/60"))
    assert r["answer"].count("$") % 2 == 0
    assert "16/60" in r["answer"]


def test_unbalanced_braces_from_model_do_not_reach_frontend_as_mathjax_error():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Izračunaj $\\frac{16}{60$ (greška u zagradi).", expected="16/60"),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert r["answer"].count("$") % 2 == 0
    assert r["last_tutor_task"].count("$") % 2 == 0
    assert "$\\frac{16}{60$" not in r["answer"]
    assert "$\\frac{16}{60$" not in r["last_tutor_task"]


def test_one_llm_call_per_turn_still_holds_after_hardening():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")
    fake.queue(make_output(reply="Puno rješenje: $\\frac{16}{60}$.", gave_hint=True, new_task=None))
    run_practice_turn(store, fake, turn_payload(msg="uradi ga ti"))
    assert fake.practice_call_count == 2


# ---------------------------------------------------------------------------
# JSON dvostruko-escape bug (form feed umjesto \frac, itd.) — dokaz da je ista
# očišćena vrijednost jedini izvor istine na svim mjestima gdje se tekst
# zadatka pojavljuje, i da klijentski last_tutor_task fallback prolazi kroz
# istu sanitizaciju.
# ---------------------------------------------------------------------------

def _has_control_chars(s):
    return any(ord(ch) < 0x20 and ch not in ("\n", "\t") for ch in s)


def test_new_task_with_control_char_bug_is_identical_everywhere():
    store, fake = SessionStore(), FakeLLM()
    broken_task = "Izračunaj $\x0crac{3}{5} : 2$."
    expected_clean = "Izračunaj $\\frac{3}{5} : 2$."
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(text=broken_task, expected="6/5")))
    r = run_practice_turn(store, fake, turn_payload())

    assert expected_clean in r["answer"]
    assert r["last_tutor_task"] == expected_clean
    assert r["next_state"]["task"]["question"] == expected_clean
    assert store.peek("sess-1")["current_task"] == expected_clean

    # niko od njih ne smije sadržavati sirov kontrolni znak
    for value in (r["answer"], r["last_tutor_task"], r["next_state"]["task"]["question"],
                  store.peek("sess-1")["current_task"]):
        assert not _has_control_chars(value)


def test_reply_control_char_bug_sanitized_in_answer():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake, task_text=TASK_4_15, expected="16/60")
    fake.queue(make_output(
        reply="Računamo $60\x08egin{}: 15$.",  # namjerno slomljen primjer sa backspace znakom
        evaluation=None, gave_hint=True, new_task=None,
    ))
    r = run_practice_turn(store, fake, turn_payload(msg="uradi ga ti"))
    assert not _has_control_chars(r["answer"])


def test_client_last_tutor_task_is_ignored_even_when_it_contains_broken_math():
    """Browserov tekst bez serverskog identiteta zadatka nije obnovljivo stanje."""
    store, fake = SessionStore(), FakeLLM()
    broken_client_task = "Prošireni razlomak: $16\x0crac{60}: 2$."  # simulira stari bug u klijentskom stanju
    fake.queue(make_output(reply="Tačno!", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(
        msg="16/60", last_tutor_task=broken_client_task,
        interaction_phase="answering_practice_task",
    ))
    assert not _has_control_chars(r["last_tutor_task"])
    prompt_sent = fake.practice_calls[0][1]
    assert not _has_control_chars(prompt_sent)
    assert broken_client_task not in prompt_sent
    assert r["last_tutor_task"] == ""


def test_http_response_never_contains_raw_control_chars():
    """Puna JSON serijalizacija odgovora ne smije sadržavati ASCII kontrolne
    znakove U+0000–U+001F, osim normalnih \\n i \\t koji mogu legalno postojati
    u običnom (ne-matematičkom) tekstu odgovora."""
    store, fake = SessionStore(), FakeLLM()
    broken_task = "Izračunaj $\x0crac{3}{5} : 2$."
    fake.queue(make_output(reply="Evo zadatka.\nDrugi red teksta.",
                           new_task=make_task(text=broken_task, expected="6/5")))
    r = run_practice_turn(store, fake, turn_payload())
    raw = json.dumps(r, ensure_ascii=False)
    # json.dumps escapeuje \n i \t kao \\n / \\t u samom JSON tekstu, pa
    # provjeravamo dekodiranu (parsed) formu svake string vrijednosti
    def walk(node):
        if isinstance(node, str):
            assert not _has_control_chars(node), repr(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(json.loads(raw))


def test_full_practice_response_has_exactly_one_backslash_everywhere():
    """Regresija za konkretan live bug: rekonstrukcija kontrolnog znaka je
    (po hipotezi) mogla proizvesti DVA backslasha umjesto jednog, zbog čega bi
    MathJax prikazao "frac34cdot8" umjesto formule. Provjerava SVA četiri
    mjesta gdje se tekst zadatka pojavljuje: answer, last_tutor_task,
    next_state.task.question i server session state (recent_tasks)."""
    store, fake = SessionStore(), FakeLLM()
    broken_task = "Izračunaj $\x0crac{3}{4}$ i pomnoži sa 8."
    expected_clean_fragment = "$\\frac{3}{4}$"   # JEDAN backslash u Python stringu

    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(text=broken_task, expected="6")))
    r = run_practice_turn(store, fake, turn_payload())

    answer = r["answer"]
    last_tutor_task = r["last_tutor_task"]
    next_state_question = r["next_state"]["task"]["question"]
    session_recent_tasks = store.peek("sess-1")["recent_tasks"]

    for label, value in (
        ("answer", answer),
        ("last_tutor_task", last_tutor_task),
        ("next_state.task.question", next_state_question),
    ):
        assert expected_clean_fragment in value, f"{label}: {value!r}"
        assert _backslash_run_length(value, "frac") == 1, f"{label}: {value!r}"
        wire = json.dumps(value)
        assert r"\\frac" in wire, f"{label} wire: {wire!r}"
        assert r"\\\\frac" not in wire, f"{label} wire (DUPLIRAN backslash — BUG): {wire!r}"

    assert any(expected_clean_fragment in t for t in session_recent_tasks)
    for t in session_recent_tasks:
        if "frac" in t:
            assert _backslash_run_length(t, "frac") == 1, repr(t)


def _backslash_run_length(s, marker):
    idx = s.index(marker)
    count = 0
    while idx - count - 1 >= 0 and s[idx - count - 1] == "\\":
        count += 1
    return count


# ---------------------------------------------------------------------------
# matbot/rules.py integracija: run_practice_turn stvarno prosljeđuje canonical
# lesson_title/oblast do instrukcija (ne samo direktan poziv prompts.build_instructions).
# ---------------------------------------------------------------------------

def test_practice_turn_instructions_include_topic_rules_for_real_lesson():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    run_practice_turn(store, fake, turn_payload(selected_topic="6-04-001"))  # Razlomci lekcija
    instructions, _ = fake.practice_calls[0]
    assert "OBLAST — RAZLOMCI" in instructions
    assert "DOMEN I SIGURNOST" in instructions


def test_practice_turn_off_topic_answer_text_is_in_instructions():
    from matbot.rules import OFF_TOPIC_ANSWER

    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Ovo je van matematike."))
    run_practice_turn(store, fake, turn_payload(msg="Ko je pobijedio prvenstvo?"))
    instructions, _ = fake.practice_calls[0]
    assert OFF_TOPIC_ANSWER in instructions


# ---------------------------------------------------------------------------
# Live produkcijski nalaz (3 stvarna formatting bagova u Practice MC izlazu):
# full-path regresija kroz run_practice_turn — schema → sanitize/validate →
# session store → browser-safe next_state.
# ---------------------------------------------------------------------------

def test_failure1_full_path_raw_frac_feedback_is_repaired_not_rejected():
    """Choice-answer feedback sa sirovim \\frac (bez $) mora stići do browsera
    VEĆ ispravljeno ($...$), ne odbijeno — narrow-wrap je bezbjedan repair."""
    from tests.conftest import make_options as _mk_opts

    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Koliko je $\\frac{3}{24}$ skraćeno?", expected="1/8",
                            options=_mk_opts("1/8", "3/16", "1/4", "3/8"), correct_option_index=0),
    ))
    r0 = run_practice_turn(store, fake, turn_payload())
    correct_id = r0["next_state"]["task"]["options"][0]["id"]  # placeholder, prava provjera niže

    # pronađi stvarni correct_option_id iz store-a (server-truth, ne iz responsa)
    sess = store.peek("sess-1")
    real_correct_id = sess["correct_option_id"]

    fake.queue(make_output(reply="Izabrao si \\frac{3}{24}. Tačno!", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(
        interaction_type="choice_answer", selected_option_id=real_correct_id, client_turn_id="t1",
    ))
    assert r.get("status") == "ready"
    assert r["answer"] == "Izabrao si $\\frac{3}{24}$. Tačno!"
    assert "\\frac{3}{24}" not in r["answer"].replace("$\\frac{3}{24}$", "")  # nema goli \frac izvan $
    assert fake.practice_call_count == 2  # tačno 1 poziv po turnu, 2 turna — bez drugog/repair poziva


def test_failure2_full_path_option_ordered_pair_and_newline_repaired():
    """Nov zadatak sa doslovnim '\\n' na početku pitanja i neomotanim uređenim
    parom u opciji mora biti PRIHVAĆEN uz reparaciju, ne odbijen."""
    options = make_options("(0,\\frac{8}{3})", "(0,2)", "(1,3)", "(2,5)")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(
        reply="Evo sistema.",
        new_task=make_task(
            text="\\nKoji je tačan uređeni par $(x,y)$?",
            expected="(0,8/3)", options=options, correct_option_index=0,
        ),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert r["status"] == "ready"
    question = r["next_state"]["task"]["question"]
    assert "\\n" not in question
    assert question.startswith("\n")
    assert "$(x,y)$" in question

    opts = r["next_state"]["task"]["options"]
    assert len(opts) == 4
    texts = [o["text"] for o in opts]
    assert "$(0,\\frac{8}{3})$" in texts
    assert not any("\\frac" in t and not t.startswith("$") for t in texts)  # nema sirov \frac izvan $

    # correct-option identitet ostaje ispravan NAKON reparacije: server-truth
    # correct_option_id mora pokazivati baš na repariranu $(0,\frac{8}{3})$ opciju
    sess = store.peek("sess-1")
    correct_text = next(o["text"] for o in sess["current_options"] if o["id"] == sess["correct_option_id"])
    assert correct_text == "$(0,\\frac{8}{3})$"

    # browser-safe: expected_answer/correct_option_id se NIKAD ne šalju
    assert "correct_option_id" not in r
    assert "expected_answer" not in r
    assert "expected_answer_summary" not in json.dumps(r)
    assert fake.practice_call_count == 1  # jedan poziv, bez drugog/repair poziva


def test_failure3_full_path_damaged_sqrt_units_option_is_repaired_when_unambiguous():
    """Oštećena opcija s NEDVOSMISLENIM radikandom/jedinicom (Defekt 1, živi
    nalaz) se popravlja deterministički — cio zadatak se više ne odbacuje
    kad je popravka jednoznačna, bez drugog/repair AI poziva."""
    options = make_options("54sqrt3,textcm^3", "180sqrt3,textcm^3", "90sqrt3,textcm^3", "30sqrt3,textcm^3")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Izračunaj zapreminu.", expected="$54\\sqrt{3}\\,\\text{cm}^3$",
                            options=options, correct_option_index=0),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert r.get("status") == "ready"
    assert fake.practice_call_count == 1
    sess = store.peek("sess-1")
    texts = [o["text"] for o in sess["current_options"]]
    assert "$54\\sqrt{3},\\text{cm}^3$" in texts
    import re as _re
    assert not any(_re.search(r"(?<!\\)sqrt|(?<!\\)text", t) for t in texts)


def test_ambiguous_damaged_sqrt_option_still_rejected_safely():
    """Kontrolni slučaj: radikand koji NIJE brojčani token/({}/()) ostaje
    nejednoznačan — i dalje se odbija cio zadatak, bez drugog poziva."""
    options = make_options("$sqrtx$", "$sqrty$", "$sqrtz$", "$sqrtw$")
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Izračunaj.", expected="$sqrtx$",
                            options=options, correct_option_index=0),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert "status" not in r
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.practice_call_count == 1

    sess = store.peek("sess-1")
    assert sess is None or not sess.get("current_task")


def test_raw_sqrt_in_feedback_without_dollar_rejected_safely_no_second_call():
    """\\sqrt (za razliku od \\frac) nema uzak siguran repair u prozi — mora
    biti odbijen bez pokušaja da server sam izmisli $...$ omot."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task()))
    run_practice_turn(store, fake, turn_payload())

    sess = store.peek("sess-1")
    correct_id = sess["correct_option_id"]

    fake.queue(make_output(reply="Rezultat je \\sqrt{20}.", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(
        interaction_type="choice_answer", selected_option_id=correct_id, client_turn_id="t2",
    ))
    assert "status" not in r
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.practice_call_count == 2  # 2 turna, i dalje tačno 1 poziv PO turnu (bez drugog)


def test_explain_and_quick_also_use_central_safety_boundary():
    """Konsolidacijska izmjena: sva tri moda dijele ISTU centralnu funkciju
    (matbot.mathsafe.sanitize_and_validate_math_text) — vidi
    tests/test_mathsafe.py i tests/test_explain.py/test_quick.py za pune
    provjere ponašanja po modu."""
    import inspect

    from matbot import explain, practice, quick

    assert "sanitize_and_validate_math_text" in inspect.getsource(explain)
    assert "sanitize_and_validate_math_text" in inspect.getsource(quick)
    assert "sanitize_and_validate_math_text" in inspect.getsource(practice)
