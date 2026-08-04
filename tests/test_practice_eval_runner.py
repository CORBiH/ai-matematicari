"""Unit testovi za `tools/practice_eval` — SAM RUNNER, nikad kvalitet modela.

Ovi testovi su offline i koriste `FakeLLM`. To je jedina dozvoljena upotreba
lažnog modela u ovom sistemu: dokazuju da harness ispravno broji pozive, tačno
klasifikuje statuse, izoluje sesije i ne zapisuje tajne. NIJEDAN nalaz o
kvalitetu MAT-BOT-a se iz njih ne smije izvoditi — za to služi live kampanja.

Zašto baš ovo: prethodna faza je pokazala da najveća zamka nije loš model, nego
`SAFE_ERROR_MESSAGE` koji stigne sa HTTP 200 i prođe kao uspjeh. Taj slučaj
ovdje ima svoj izričit test.
"""
import json

import pytest

from matbot.llm import LLMTimeout, LLMUnavailable
from tests.conftest import make_reviewer_final, make_tutor_draft, queue_two_call
from tools.practice_eval import checks as check_lib
from tools.practice_eval import report as report_lib
from tools.practice_eval import runner
from tools.practice_eval.scenario import (Scenario, ScenarioError, load_scenarios,
                                          validate_scenarios)

WAVE_A = runner.ROOT / "tools" / "practice_eval" / "scenarios" / "wave_a.jsonl"
LESSON = "6-03-004"          # Pravila djeljivosti — postoji u data/topics.json
WAVE_A_CALL_BUDGET = 100


@pytest.fixture(autouse=True)
def _difficulty_levels_on(monkeypatch):
    """Runner se testira u TAČNO onoj konfiguraciji u kojoj se i pokreće."""
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _write(tmp_path, *scenarios):
    path = tmp_path / "scen.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in scenarios),
                    encoding="utf-8")
    return load_scenarios(path)


def _scenario(sid="T01", steps=None, importance="critical"):
    return {
        "id": sid, "wave": "A", "importance": importance, "grade": 6,
        "oblast": "Djeljivost brojeva", "topic_id": LESSON,
        "reason": "test scenario for the runner itself", "tags": ["unit"],
        "steps": steps or [{
            "kind": "text", "message": "Daj mi zadatak.", "expect_calls": 2,
            "checks": ["published", "task_published", "not_safe_error", "response_schema"],
            "rubrics": [],
        }],
    }


def _run(tmp_path, scenarios, fake_llm, monkeypatch, ceiling=10):
    monkeypatch.setattr(runner, "_real_llm", lambda: fake_llm)
    out = tmp_path / "out"
    return runner.run_campaign(scenarios, out, ceiling, 1, 0, False) + (out,)


# ---------------------------------------------------------------------------
# TALAS A — struktura i budžet
# ---------------------------------------------------------------------------

def test_wave_a_has_forty_scenarios_with_unique_ids_and_isolated_sessions():
    scenarios = load_scenarios(WAVE_A)
    assert len(scenarios) == 40
    assert len({scenario.id for scenario in scenarios}) == 40
    # Izolacija sesija je uslov, ne detalj: dva scenarija koja dijele session_id
    # naslijedila bi tuđi zadatak i napredovanje.
    assert len({scenario.session_id for scenario in scenarios}) == 40
    assert validate_scenarios(scenarios) == []


def test_wave_a_never_exceeds_the_hundred_call_budget():
    minimum, maximum = runner.estimate_calls(load_scenarios(WAVE_A))
    assert maximum == WAVE_A_CALL_BUDGET
    assert minimum <= maximum


def test_wave_a_covers_all_four_grades_and_states_a_reason_for_every_scenario():
    scenarios = load_scenarios(WAVE_A)
    assert {scenario.grade for scenario in scenarios} == {6, 7, 8, 9}
    assert all(scenario.reason.strip() for scenario in scenarios)
    assert all(step["checks"] for scenario in scenarios for step in scenario.steps)


def test_scenario_without_a_reason_is_refused(tmp_path):
    bad = _scenario()
    bad["reason"] = "   "
    with pytest.raises(ScenarioError):
        _write(tmp_path, bad)


def test_scenario_without_a_deterministic_expectation_is_refused(tmp_path):
    bad = _scenario()
    bad["steps"][0]["checks"] = []
    with pytest.raises(ScenarioError):
        _write(tmp_path, bad)


def test_unknown_step_kind_is_refused(tmp_path):
    bad = _scenario()
    bad["steps"][0]["kind"] = "telepathy"
    with pytest.raises(ScenarioError):
        _write(tmp_path, bad)


# ---------------------------------------------------------------------------
# DRY RUN — nula poziva
# ---------------------------------------------------------------------------

def test_dry_run_validates_wave_a_and_makes_zero_model_calls(tmp_path):
    summary = runner.dry_run(load_scenarios(WAVE_A), tmp_path / "dry")
    assert summary["problems"] == []
    assert summary["sdk_calls_made"] == 0
    assert summary["scenarios"] == 40
    assert summary["unique_ids_ok"] is True
    assert summary["estimated_model_calls_max"] == WAVE_A_CALL_BUDGET
    # Pokrivenost je IZRAČUNATA iz kurikuluma, ne procijenjena.
    assert summary["curriculum_lessons_total"] == 534
    assert summary["unique_lessons"] == 39
    assert summary["lesson_coverage_percent"] == pytest.approx(
        100.0 * 39 / 534, abs=0.01)


def test_dry_run_reports_an_unknown_topic_instead_of_silently_passing(tmp_path):
    bad = _scenario()
    bad["topic_id"] = "6-99-999"
    summary = runner.dry_run(_write(tmp_path, bad), tmp_path / "dry")
    assert any("does not exist" in problem for problem in summary["problems"])


def test_dry_run_rejects_an_unknown_check_name(tmp_path):
    bad = _scenario()
    bad["steps"][0]["checks"] = ["definitely_not_a_check"]
    summary = runner.dry_run(_write(tmp_path, bad), tmp_path / "dry")
    assert any("unknown check" in problem for problem in summary["problems"])


# ---------------------------------------------------------------------------
# STVARAN PUT ZAHTJEVA
# ---------------------------------------------------------------------------

def test_runner_drives_the_real_flask_route_and_counts_two_calls(tmp_path, fake_llm, monkeypatch):
    queue_two_call(fake_llm)
    meta, records, out = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)

    assert meta["actual_sdk_calls"] == 2
    assert fake_llm.tutor_calls and fake_llm.reviewer_calls      # oba stvarna stadija
    record = records[0]
    assert record.status in ("PASS", "REVIEW")
    assert record.failed_checks == []
    assert record.turns[0]["http_status"] == 200
    assert record.turns[0]["sdk_calls"] == 2
    assert (out / "results.jsonl").exists()


def test_each_scenario_runs_in_its_own_session(tmp_path, fake_llm, monkeypatch):
    for _ in range(2):
        queue_two_call(fake_llm)
    scenarios = _write(tmp_path, _scenario("T01"), _scenario("T02"))
    _, records, _ = _run(tmp_path, scenarios, fake_llm, monkeypatch)
    session_ids = {record.session_id for record in records}
    assert len(session_ids) == 2
    assert all(record.id.lower() in record.session_id for record in records)


def test_step_payload_matches_the_frontend_contract(tmp_path, fake_llm, monkeypatch):
    queue_two_call(fake_llm)
    _, records, _ = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)
    request = records[0].turns[0]["request"]
    assert request["mode"] == "practice"
    assert request["selected_topic"] == LESSON
    assert request["interaction_type"] == "student_question"
    assert request["client_turn_id"]


# ---------------------------------------------------------------------------
# STATUSI
# ---------------------------------------------------------------------------

def test_safe_error_message_with_http_200_is_a_fail(tmp_path, fake_llm, monkeypatch):
    """Najveća zamka: kanoniziran tehnički fallback stiže sa statusom 200."""
    fake_llm.queue(LLMUnavailable("boom"))
    _, records, _ = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)

    record = records[0]
    assert record.turns[0]["http_status"] == 200
    assert record.status == runner.STATUS_INFRA          # transport ima prednost
    failed = {entry["check"] for entry in record.failed_checks}
    assert "not_safe_error" in failed and "published" in failed


def test_safe_error_without_transport_failure_is_plain_fail(tmp_path, fake_llm, monkeypatch):
    """Nacrt koji prekrši pravilo polja: turn staje, model NIJE kriv za mrežu."""
    fake_llm.queue(make_tutor_draft(intent="generate_task", new_task=None))
    _, records, _ = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)

    record = records[0]
    assert record.status == runner.STATUS_FAIL
    failed = {entry["check"] for entry in record.failed_checks}
    assert "not_safe_error" in failed
    assert "technical_fallback_as_success" in record.root_causes


def test_timeout_is_reported_as_timeout_not_as_a_quality_failure(tmp_path, fake_llm, monkeypatch):
    fake_llm.queue(LLMTimeout("APITimeoutError"))
    _, records, _ = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)
    assert records[0].status == runner.STATUS_TIMEOUT


def test_scenario_with_a_rubric_can_never_be_a_strict_pass(tmp_path, fake_llm, monkeypatch):
    """Model-sudija nije odobren, pa kvalitativno ostaje NEDOKAZANO."""
    queue_two_call(fake_llm)
    row = _scenario()
    row["steps"][0]["rubrics"] = ["clarity"]
    _, records, _ = _run(tmp_path, _write(tmp_path, row), fake_llm, monkeypatch)
    assert records[0].status == runner.STATUS_REVIEW
    assert records[0].rubrics == ["clarity"]


# ---------------------------------------------------------------------------
# PLAFON POZIVA, RESUME, TAJNE
# ---------------------------------------------------------------------------

def test_call_ceiling_refuses_the_next_call_before_the_sdk(tmp_path, fake_llm, monkeypatch):
    queue_two_call(fake_llm)
    meta, _records, _out = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm,
                                monkeypatch, ceiling=1)
    assert meta["actual_sdk_calls"] == 1
    assert meta["budget_exceeded"] is True
    # Drugi (recenzentov) poziv nikad nije delegiran stvarnom adapteru.
    assert len(fake_llm.reviewer_calls) == 0


def test_resume_skips_scenarios_already_recorded(tmp_path, fake_llm, monkeypatch):
    monkeypatch.setattr(runner, "_real_llm", lambda: fake_llm)
    scenarios = _write(tmp_path, _scenario("T01"))
    out = tmp_path / "out"

    queue_two_call(fake_llm)
    runner.run_campaign(scenarios, out, 10, 1, 0, False)
    assert len(fake_llm.tutor_calls) == 1

    # Bez novih pripremljenih odgovora: da resume nije radio, FakeLLM bi pukao.
    meta, records = runner.run_campaign(scenarios, out, 10, 1, 0, True)
    assert records == []
    assert meta["scenario_count_pending"] == 0
    assert len(fake_llm.tutor_calls) == 1


def test_results_never_contain_the_auth_token_or_any_header(tmp_path, fake_llm, monkeypatch):
    from matbot import auth

    queue_two_call(fake_llm)
    _, _records, out = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)
    blob = (out / "results.jsonl").read_text(encoding="utf-8")
    blob += (out / "run_meta.json").read_text(encoding="utf-8")

    assert auth.TOKEN_HEADER not in blob
    assert "OPENAI_API_KEY" not in blob
    assert "FLASK_SECRET_KEY" not in blob
    assert "headers" not in blob
    # Zahtjev se ipak zapisuje u cijelosti — bez toga nema dijagnostike.
    assert "student_message" in blob


def test_report_is_written_with_exact_lesson_coverage(tmp_path, fake_llm, monkeypatch):
    queue_two_call(fake_llm)
    meta, _records, out = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)
    loaded = report_lib.load_records(out / "results.jsonl")
    summary = report_lib.write_reports(out, meta, loaded, 534)

    assert summary["lesson_coverage"]["curriculum_lessons_total"] == 534
    assert summary["lesson_coverage"]["unique_lessons_tested"] == 1
    assert (out / "summary.json").exists()
    markdown = (out / "report.md").read_text(encoding="utf-8")
    assert "Pokrivenost kurikuluma" in markdown
    assert "REVIEW znači" in markdown


# ---------------------------------------------------------------------------
# POJEDINAČNE PROVJERE
# ---------------------------------------------------------------------------

def _observation(**overrides):
    base = dict(scenario_id="T", step_index=0, step_kind="text", topic_id=LESSON,
                grade=6, request_payload={}, http_status=200,
                response={"status": "ready", "answer": "Evo zadatka.",
                          "answer_verdict": None, "last_tutor_task": "",
                          "next_state": {"v": 1}, "session_mode": "practice",
                          "effective_topic": LESSON},
                session_before=None, session_after=None, sdk_calls=1)
    base.update(overrides)
    return check_lib.TurnObservation(**base)


def test_response_schema_rejects_a_rejection_that_carries_next_state():
    ok = check_lib.check_response_schema(_observation())
    assert ok.outcome == check_lib.PASS

    bad = check_lib.check_response_schema(_observation(
        response={"answer": "x", "last_tutor_task": "", "next_state": {"v": 1}}))
    assert bad.outcome == check_lib.FAIL
    assert "next_state" in bad.detail


def test_response_schema_rejects_an_internal_field_in_the_payload():
    result = check_lib.check_response_schema(_observation(
        response={"status": "ready", "answer": "x", "answer_verdict": None,
                  "last_tutor_task": "", "next_state": {"v": 1},
                  "session_mode": "practice", "effective_topic": LESSON,
                  "expected_answer_summary": "$3$"}))
    assert result.outcome == check_lib.FAIL
    assert "expected_answer_summary" in result.detail


def test_not_safe_error_names_the_canned_message():
    from matbot.practice import SAFE_ERROR_MESSAGE

    result = check_lib.check_not_safe_error(_observation(
        response={"status": "ready", "answer": SAFE_ERROR_MESSAGE}))
    assert result.outcome == check_lib.FAIL


def test_hint_differs_flags_a_repeated_hint_and_passes_a_new_one():
    first = "Pogledaj imenioce prije nego što sabereš razlomke."
    repeated = check_lib.check_hint_differs(_observation(
        response={"status": "ready", "answer": first}, previous_help_texts=(first,)))
    assert repeated.outcome == check_lib.FAIL

    fresh = check_lib.check_hint_differs(_observation(
        response={"status": "ready", "answer": "Sada saberi brojnike, imenilac ostaje isti."},
        previous_help_texts=(first,)))
    assert fresh.outcome == check_lib.PASS


def test_answer_leak_is_skipped_when_the_value_is_already_in_the_task():
    """Ponavljanje broja iz samog zadatka nije curenje — i ne smije biti FAIL."""
    session = {"current_task": "Koji je veći: $\\frac{3}{5}$ ili $\\frac{2}{7}$?",
               "current_options": [{"id": "a", "text": "$\\frac{3}{5}$"}],
               "correct_option_id": "a", "expected_answer_summary": "$\\frac{3}{5}$"}
    result = check_lib.check_no_answer_leak(_observation(
        response={"status": "ready", "answer": "Rezultat je $\\frac{3}{5}$."},
        session_before=session, session_after=session))
    assert result.outcome == check_lib.SKIP

    session_hidden = dict(session, current_task="Uporedi data dva razlomka.")
    leaked = check_lib.check_no_answer_leak(_observation(
        response={"status": "ready", "answer": "Rezultat je $\\frac{3}{5}$."},
        session_before=session_hidden, session_after=session_hidden))
    assert leaked.outcome == check_lib.FAIL


def test_free_text_grading_has_no_oracle_and_says_so():
    result = check_lib.check_free_text_grading_no_oracle(_observation())
    assert result.outcome == check_lib.SKIP
    assert "no deterministic oracle" in result.detail


def test_a_skipped_check_never_counts_as_a_pass(tmp_path, fake_llm, monkeypatch):
    queue_two_call(fake_llm)
    row = _scenario()
    row["steps"][0]["checks"] = ["published", "free_text_grading_no_oracle"]
    _, records, _ = _run(tmp_path, _write(tmp_path, row), fake_llm, monkeypatch)
    assert records[0].status == runner.STATUS_REVIEW
    assert records[0].skipped_checks


def test_every_check_name_used_by_wave_a_resolves():
    for scenario in load_scenarios(WAVE_A):
        for step in scenario.steps:
            for name in step["checks"]:
                assert check_lib.resolve(name) is not None, name
            for name in step["rubrics"]:
                assert name in check_lib.RUBRICS, name


def test_root_cause_grouping_is_defined_for_every_check():
    for name in check_lib.known_check_names():
        assert check_lib.root_cause(name.replace(":N", ":1"))


# ---------------------------------------------------------------------------
# IZBOR TALASA I INTEGRITET LIVE PUTA
# ---------------------------------------------------------------------------

def _args(**overrides):
    values = {"wave": None, "scenario": [], "max_scenarios": 0}
    values.update(overrides)
    return type("Args", (), values)


def test_wave_filter_selects_only_the_named_wave(tmp_path):
    rows = [_scenario("T01"), dict(_scenario("T02"), wave="B")]
    scenarios = _write(tmp_path, *rows)
    assert [s.id for s in runner.select(scenarios, _args(wave="A"))] == ["T01"]
    assert [s.id for s in runner.select(scenarios, _args(wave="B"))] == ["T02"]


def test_wave_all_applies_no_filter(tmp_path):
    """„all“ je odsustvo filtera — mora obuhvatiti i budući talas C."""
    rows = [_scenario("T01"), dict(_scenario("T02"), wave="B")]
    scenarios = _write(tmp_path, *rows)
    assert [s.id for s in runner.select(scenarios, _args(wave="all"))] == ["T01", "T02"]
    assert runner.build_parser().parse_args(["--wave", "all"]).wave == "all"


def test_live_runner_resolves_the_real_openai_adapter_never_a_fake():
    """Live put mora završiti u pravom adapteru; FakeLLM je samo za ove testove.

    `OpenAIPracticeLLM` je lijen — konstrukcija ne traži API ključ i ne otvara
    nijednu konekciju, pa se ovo može provjeriti bez ijednog poziva."""
    from matbot.llm import OpenAIPracticeLLM

    assert isinstance(runner._real_llm(), OpenAIPracticeLLM)


def test_no_module_of_the_eval_framework_imports_a_test_double():
    """Statička provjera: nijedan fajl u tools/practice_eval ne smije uvući
    FakeLLM ni tests.conftest — inače bi live rezultat mogao biti lažan."""
    package = runner.ROOT / "tools" / "practice_eval"
    sources = list(package.glob("*.py")) + [runner.ROOT / "tools" / "run_practice_eval.py"]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "FakeLLM" not in text, path.name
        assert "tests.conftest" not in text, path.name
        assert "from tests" not in text, path.name


def test_dry_run_refuses_to_call_the_model_at_all():
    """`RefusingLLM` postoji da „dry-run“ nikad ne može tiho postati live."""
    fake = runner.RefusingLLM()
    with pytest.raises(AssertionError):
        fake.tutor_turn("instrukcije", "ulaz")
