"""Structured diagnostics for semantically_duplicate_options rejections
(production incidents request_id=e4eb30672481 pairs=[(0,2)] and
request_id=3b9133c47a62 pairs=[(1,3)], both on 6-04-005 "Proširivanje
razlomaka"). Categories 6-16 of this pass's required tests.

The validator itself must NOT be weakened — these tests prove it still
rejects, and that the new logging is safe (no secrets, length-bounded,
never reaches the browser) and diagnostically useful (sanitized question/
options/equivalence-type)."""
import json
import pytest
import logging

from matbot import task_families as tf
from matbot.option_equivalence import classify_equivalence, find_equivalent_option_pairs
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_options, make_output, make_task


def turn_payload(msg="Daj zadatak.", **kw):
    base = {
        "session_id": "sess-dup-diag", "grade": 6, "selected_topic": "6-04-007",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "", "selected_option_id": "", "client_turn_id": "",
    }
    base.update(kw)
    return base


# --- Kategorija 6: validator ostaje nepromijenjen i aktivan ------------------

def test_case6_validator_unchanged_still_detects_equivalent_fractions():
    pairs = find_equivalent_option_pairs(["$\\frac{15}{36}$", "$\\frac{10}{36}$",
                                          "$\\frac{5}{12}$", "$\\frac{17}{36}$"])
    assert pairs == [(0, 2)]


# --- Kategorija 7-9: i dalje se odbijaju (regresija) -------------------------

def test_case7_equivalent_fraction_pair_still_rejected(caplog):
    store, fake = SessionStore(), FakeLLM()
    options = make_options("$\\frac{15}{36}$", "$\\frac{10}{36}$", "$\\frac{5}{12}$", "$\\frac{17}{36}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Proširi razlomak $\\frac{5}{12}$ tako da nazivnik bude $36$.",
                            expected="$\\frac{15}{36}$", options=options, correct_option_index=0),
    ))
    with caplog.at_level(logging.WARNING):
        r = run_practice_turn(store, fake, turn_payload())
    assert "status" not in r
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1


def test_case8_exact_vs_rounded_pair_still_rejected():
    store, fake = SessionStore(), FakeLLM()
    options = make_options("$8\\sqrt{2}\\,\\text{cm}$", "$11,3\\,\\text{cm}$", "$9\\,\\text{cm}$", "$14\\,\\text{cm}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Izračunaj dijagonalu kvadrata.", expected="$8\\sqrt{2}\\,\\text{cm}$",
                            options=options, correct_option_index=0),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert "status" not in r
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1


def test_case9_symbolically_reordered_pair_still_rejected(monkeypatch):
    monkeypatch.setattr(tf, "select_family", lambda *a, **kw: "choose_correct_formula")
    store, fake = SessionStore(), FakeLLM()
    options = make_options("$d=a\\sqrt{2}$", "$d=\\sqrt{2}a$", "$d=2a$", "$d=a^2$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Koja je formula za dijagonalu kvadrata tačna?",
                            expected="$d=a\\sqrt{2}$", options=options, correct_option_index=0,
                            task_family="choose_correct_formula"),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert "status" not in r
    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 1


# --- Kategorija 10: četiri stvarno različite opcije prolaze ------------------

def test_case10_four_genuinely_distinct_fraction_options_accepted():
    store, fake = SessionStore(), FakeLLM()
    options = make_options("$\\frac{15}{36}$", "$\\frac{10}{36}$", "$\\frac{15}{24}$", "$\\frac{17}{36}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Proširi razlomak $\\frac{5}{12}$ tako da nazivnik bude $36$.",
                            expected="$\\frac{15}{36}$", options=options, correct_option_index=0),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert r.get("status") == "ready"
    assert fake.call_count == 1


# --- Regresija: četiri STVARNO generisana skupa opcija iz živog testa -------
# (6-04-005, „Proširivanje razlomaka“ — sva četiri prihvaćena zadatka moraju
# ostati prihvaćena i nakon svih izmjena prompta/konfiguracije.)

_LIVE_ACCEPTED_OPTION_SETS = [
    ("2/5 → 20", ["$\\frac{8}{20}$", "$\\frac{2}{20}$", "$\\frac{6}{20}$", "$\\frac{8}{5}$"]),
    ("4/9 → 36", ["$\\frac{16}{36}$", "$\\frac{4}{36}$", "$\\frac{12}{36}$", "$\\frac{16}{9}$"]),
    ("7/9 → 27", ["$\\frac{21}{27}$", "$\\frac{7}{27}$", "$\\frac{14}{27}$", "$\\frac{21}{18}$"]),
    ("7/10 → 50", ["$\\frac{35}{50}$", "$\\frac{7}{50}$", "$\\frac{21}{50}$", "$\\frac{35}{10}$"]),
]


@pytest.mark.parametrize("label,options", _LIVE_ACCEPTED_OPTION_SETS,
                         ids=[s[0] for s in _LIVE_ACCEPTED_OPTION_SETS])
def test_previously_accepted_live_option_sets_remain_accepted(label, options):
    assert find_equivalent_option_pairs(options) == [], label


@pytest.mark.parametrize("label,options", _LIVE_ACCEPTED_OPTION_SETS,
                         ids=[s[0] for s in _LIVE_ACCEPTED_OPTION_SETS])
def test_previously_accepted_live_option_sets_pass_full_practice_path(label, options):
    store, fake = SessionStore(), FakeLLM()
    original, target = label.split(" → ")
    num, den = original.split("/")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(
            text=f"Proširi razlomak $\\frac{{{num}}}{{{den}}}$ tako da nazivnik bude ${target}$.",
            expected=options[0], options=make_options(*options), correct_option_index=0),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    assert r.get("status") == "ready", label
    assert fake.call_count == 1


# --- Kategorija 11-12: nema mutacije sesije, tačno jedan LLM poziv ----------

def test_case11_duplicate_rejection_does_not_mutate_session_state():
    store, fake = SessionStore(), FakeLLM()
    before = store.peek("sess-dup-diag")
    options = make_options("$\\frac{15}{36}$", "$\\frac{10}{36}$", "$\\frac{5}{12}$", "$\\frac{17}{36}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Proširi razlomak $\\frac{5}{12}$ tako da nazivnik bude $36$.",
                            expected="$\\frac{15}{36}$", options=options, correct_option_index=0),
    ))
    run_practice_turn(store, fake, turn_payload())
    after = store.peek("sess-dup-diag")
    assert after == before  # None == None, ili identično nemutirano stanje


def test_case12_exactly_one_llm_call_on_rejection():
    store, fake = SessionStore(), FakeLLM()
    options = make_options("$\\frac{15}{36}$", "$\\frac{10}{36}$", "$\\frac{5}{12}$", "$\\frac{17}{36}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Proširi razlomak $\\frac{5}{12}$ tako da nazivnik bude $36$.",
                            expected="$\\frac{15}{36}$", options=options, correct_option_index=0),
    ))
    run_practice_turn(store, fake, turn_payload())
    assert fake.call_count == 1


# --- Kategorija 13: dijagnostika se NIKAD ne šalje u browser ----------------

def test_case13_browser_payload_never_exposes_diagnostic_metadata():
    store, fake = SessionStore(), FakeLLM()
    options = make_options("$\\frac{15}{36}$", "$\\frac{10}{36}$", "$\\frac{5}{12}$", "$\\frac{17}{36}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Proširi razlomak $\\frac{5}{12}$ tako da nazivnik bude $36$.",
                            expected="$\\frac{15}{36}$", options=options, correct_option_index=0),
    ))
    r = run_practice_turn(store, fake, turn_payload())
    raw = json.dumps(r, ensure_ascii=False)
    for forbidden in ("equivalence_types", "duplicate_options_diagnostics", "pairs=",
                      "correct_option_index", "expected_answer"):
        assert forbidden not in raw


# --- Kategorija 14: strukturisani log sadrži sanitizovan question/options/tip

def test_case14_structured_log_includes_sanitized_fields_and_equivalence_type(caplog):
    store, fake = SessionStore(), FakeLLM()
    options = make_options("$\\frac{15}{36}$", "$\\frac{10}{36}$", "$\\frac{5}{12}$", "$\\frac{17}{36}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Proširi razlomak $\\frac{5}{12}$ tako da nazivnik bude $36$.",
                            expected="$\\frac{15}{36}$", options=options, correct_option_index=0),
    ))
    with caplog.at_level(logging.WARNING, logger="matbot.practice"):
        run_practice_turn(store, fake, turn_payload())
    diag_records = [rec for rec in caplog.records if rec.message.startswith("practice_duplicate_options")]
    assert len(diag_records) == 1
    msg = diag_records[0].message
    assert "request_id=" in msg
    assert "topic=6-04-007" in msg
    assert "pairs=[(0, 2)]" in msg
    assert "equivalence_types=['equivalent_fraction']" in msg
    assert "correct_option_index=0" in msg
    assert "Pro" in msg and "razlomak" in msg  # sanitizovano pitanje prisutno
    assert "\\\\frac{15}{36}" in msg or "frac{15}{36}" in msg  # sanitizovane opcije prisutne


# --- Kategorija 15: nikad se ne loguju tajne/auth vrijednosti ---------------

def test_case15_no_secrets_or_auth_tokens_in_diagnostic_log(caplog):
    store, fake = SessionStore(), FakeLLM()
    options = make_options("$\\frac{15}{36}$", "$\\frac{10}{36}$", "$\\frac{5}{12}$", "$\\frac{17}{36}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text="Proširi razlomak $\\frac{5}{12}$ tako da nazivnik bude $36$.",
                            expected="$\\frac{15}{36}$", options=options, correct_option_index=0),
    ))
    with caplog.at_level(logging.WARNING, logger="matbot.practice"):
        run_practice_turn(store, fake, turn_payload())
    full_log_text = "\n".join(rec.message for rec in caplog.records)
    for forbidden in ("api_key", "OPENAI_API_KEY", "Authorization", "Bearer ", "embed_token", "signed_token"):
        assert forbidden not in full_log_text


# --- Kategorija 16: logovane vrijednosti su dužinski ograničene -------------

def test_case16_logged_values_are_length_bounded(caplog):
    store, fake = SessionStore(), FakeLLM()
    # Padding na PITANJU i expected_answer ostaje ISPOD schema.py
    # MAX_TASK_CHARS(600)/MAX_EXPECTED_ANSWER_CHARS(400) (inače validate_output
    # odbija PRIJE nego duplicate-check uopšte pokrene) — ali IZNAD
    # _LOG_FIELD_LIMIT (200 za pitanje) da provjerimo stvarno skraćivanje.
    # Opcije ostaju NEDIRNUTE (padding bi pokvario is_fraction_option oblik i
    # odbio zadatak zbog FamilyContract prije nego stignemo do duplicate-check-a).
    long_question = "Proširi razlomak $\\frac{5}{12}$ tako da nazivnik bude $36$. " + ("x" * 300)
    options = make_options("$\\frac{15}{36}$", "$\\frac{10}{36}$", "$\\frac{5}{12}$", "$\\frac{17}{36}$")
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text=long_question, expected="$\\frac{15}{36}$ jer je " + ("z" * 300),
                            options=options, correct_option_index=0),
    ))
    with caplog.at_level(logging.WARNING, logger="matbot.practice"):
        run_practice_turn(store, fake, turn_payload())
    diag_records = [rec for rec in caplog.records if rec.message.startswith("practice_duplicate_options")]
    assert len(diag_records) == 1
    msg = diag_records[0].message
    assert "x" * 300 not in msg
    assert "z" * 300 not in msg
