"""Testovi orkestracije Practice turna (fake LLM, bez mreže)."""
import copy
import json

from tests.conftest import FakeLLM, make_output, make_task
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
    assert fake.call_count == 1
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."
    assert "Skrati razlomak" in r["answer"]
    assert r["session_mode"] == "practice"
    assert r["effective_topic"] == "6-01-006"


def test_one_llm_call_per_normal_turn():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Tačno!", evaluation="correct"))
    run_practice_turn(store, fake, turn_payload(msg="5/8"))
    assert fake.call_count == 2  # tačno 1 poziv po turnu, 2 turna


def test_correct_answer_keeps_task_and_increments_streak():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Tačno! $20:4=5$, $32:4=8$.", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(msg="20/32 = 5/8"))
    assert r["answer_verdict"] == "correct"
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{20}{32}$."  # nema auto novog zadatka
    assert r["next_state"]["correct_streak"] == 1
    sess = store.peek("sess-1")
    assert sess["current_task"] == "Skrati razlomak $\\frac{20}{32}$."


def test_fraction_and_sentence_answers_pass_through_verdicts():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Da, upravo tako — oba se skrate na $\\frac{5}{8}$.", evaluation="correct"))
    r1 = run_practice_turn(store, fake, turn_payload(msg="da, jer se oba skrate na isti razlomak"))
    assert r1["answer_verdict"] == "correct"
    fake.queue(make_output(reply="Blizu — brojnik je dobar, nazivnik nije.", evaluation="partially_correct"))
    r2 = run_practice_turn(store, fake, turn_payload(msg="mislim da je 5/6"))
    assert r2["answer_verdict"] == "partially_correct"


def test_partial_and_incorrect_keep_task_and_reset_streak():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Tačno!", evaluation="correct"))
    run_practice_turn(store, fake, turn_payload(msg="5/8"))
    fake.queue(make_output(reply="Nije — pogledaj nazivnik.", evaluation="incorrect"))
    r = run_practice_turn(store, fake, turn_payload(msg="mozda 6"))
    assert r["answer_verdict"] == "incorrect"
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
    assert "TRENUTNI HINT NIVO: 1" in fake.calls[2][1]


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
    fake.queue(make_output(reply="Može, evo novog.",
                           new_task=make_task(text="Skrati razlomak $\\frac{18}{24}$.", expected="3/4")))
    r = run_practice_turn(store, fake, turn_payload(msg="daj novi zadatak"))
    assert r["last_tutor_task"] == "Skrati razlomak $\\frac{18}{24}$."
    assert r["next_state"]["task"]["question"] == "Skrati razlomak $\\frac{18}{24}$."
    assert "Skrati razlomak $\\frac{18}{24}$." in r["answer"]
    sess = store.peek("sess-1")
    assert sess["expected_answer_summary"] == "3/4"
    assert sess["hint_level"] == 0
    assert sess["recent_tasks"][-1] == "Skrati razlomak $\\frac{18}{24}$."


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
    fake.queue(make_output(reply="Evo lakšeg.",
                           new_task=make_task(text="Skrati razlomak $\\frac{6}{8}$.", expected="3/4", difficulty="easy")))
    r1 = run_practice_turn(store, fake, turn_payload(msg="Daj mi lakši zadatak.", difficulty_request="easier"))
    assert "difficulty_request=easier" in fake.calls[1][1]
    assert store.peek("sess-1")["difficulty"] == "easy"
    fake.queue(make_output(reply="Evo težeg.",
                           new_task=make_task(text="Skrati $\\frac{84}{126}$ i objasni postupak.", expected="2/3", difficulty="hard")))
    r2 = run_practice_turn(store, fake, turn_payload(msg="Daj mi teži zadatak.", difficulty_request="harder"))
    assert "difficulty_request=harder" in fake.calls[2][1]
    assert store.peek("sess-1")["difficulty"] == "hard"
    assert r1["last_tutor_task"] != r2["last_tutor_task"]


def test_expected_answer_is_aid_sent_to_prompt_not_authority():
    store, fake = SessionStore(), FakeLLM()
    start_session(store, fake)
    fake.queue(make_output(reply="Zapravo si u pravu — provjerio sam ponovo.", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))
    # prompt sadrži interni očekivani odgovor kao POMOĆ...
    assert "INTERNI OČEKIVANI ODGOVOR" in fake.calls[1][1]
    assert "5/8" in fake.calls[1][1]
    # ...ali konačna presuda je modelova (server je ne mijenja)
    assert r["answer_verdict"] == "correct"


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
        new_task={"text": "Novi zadatak.", "expected_answer": "  ", "difficulty": "easy"},
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
    third_input = fake.calls[2][1]
    assert "NEDAVNI ZADACI" in third_input
    assert "\\frac{20}{32}" in third_input and "\\frac{18}{24}" in third_input


def test_server_restart_survival_uses_client_task_text_without_expected_answer():
    store, fake = SessionStore(), FakeLLM()
    # nema sesije (kao poslije restarta), ali klijent šalje last_tutor_task
    fake.queue(make_output(reply="Tačno!", evaluation="correct"))
    r = run_practice_turn(store, fake, turn_payload(
        msg="5/8", last_tutor_task="Skrati razlomak $\\frac{20}{32}$.",
        interaction_phase="answering_practice_task"))
    assert r["answer_verdict"] == "correct"
    prompt_in = fake.calls[0][1]
    assert "Skrati razlomak" in prompt_in
    # interno rješenje NE dolazi od klijenta — model računa sam
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
    calls_before = fake.call_count

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
    assert fake.call_count == calls_before + 1                # tačno JEDAN (neuspješan) poziv, bez repair-a
    raw = json.dumps(r, ensure_ascii=False)
    assert before["expected_answer_summary"] not in raw       # očekivani odgovor NIKAD ne izlazi


def test_invalid_structured_output_full_session_snapshot_unchanged():
    store, fake = SessionStore(), FakeLLM()
    before = _rich_session_snapshot(store, fake)
    before_copy = copy.deepcopy(before)
    calls_before = fake.call_count

    # strukturno validan JSON, ali sadržajno neupotrebljiv (prazan reply)
    fake.queue(make_output(reply="   "))
    r = run_practice_turn(store, fake, turn_payload(msg="5/8"))

    after = store.peek("sess-1")
    assert after == before_copy
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert "next_state" not in r
    assert r["last_tutor_task"] == before["current_task"]
    assert fake.call_count == calls_before + 1
    raw = json.dumps(r, ensure_ascii=False)
    assert before["expected_answer_summary"] not in raw


def test_unexpected_exception_full_session_snapshot_unchanged():
    store, fake = SessionStore(), FakeLLM()
    before = _rich_session_snapshot(store, fake)
    before_copy = copy.deepcopy(before)
    calls_before = fake.call_count

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
    assert fake.call_count == calls_before + 1
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
    instructions = fake.calls[-1][0]
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
    first_hint_prompt = fake.calls[1][1]
    fake.queue(make_output(reply="Hint 2.", gave_hint=True))
    run_practice_turn(store, fake, turn_payload(msg="daj mi hint", intent="hint_request"))  # calls[2], hint_level 1 -> 2
    second_hint_prompt = fake.calls[2][1]
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
    third_prompt = fake.calls[3][1]
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
    assert fake.call_count == 2
