"""Unit testovi za `tools/practice_eval` — SAM RUNNER, nikad kvalitet modela.

Ovi testovi su offline i koriste `FakeLLM`. To je jedina dozvoljena upotreba
lažnog modela u ovom sistemu: dokazuju da harness ispravno broji pozive, tačno
klasifikuje statuse, izoluje sesije i ne zapisuje tajne. NIJEDAN nalaz o
kvalitetu MAT-BOT-a se iz njih ne smije izvoditi — za to služi live kampanja.

Zašto baš ovo: prethodna faza je pokazala da najveća zamka nije loš model, nego
`SAFE_ERROR_MESSAGE` koji stigne sa HTTP 200 i prođe kao uspjeh. Taj slučaj
ovdje ima svoj izričit test.
"""
import io
import json
import sys

import pytest

from matbot.llm import LLMTimeout, LLMUnavailable
from tests.conftest import make_reviewer_final, make_tutor_draft, queue_two_call
from tools.practice_eval import checks as check_lib
from tools.practice_eval import report as report_lib
from tools.practice_eval import runner
from tools.practice_eval.scenario import (Scenario, ScenarioError, load_scenarios,
                                          validate_scenarios)

SCENARIO_DIR = runner.ROOT / "tools" / "practice_eval" / "scenarios"
WAVE_A = SCENARIO_DIR / "wave_a.jsonl"
WAVE_B = SCENARIO_DIR / "wave_b.jsonl"
LESSON = "6-04-001"          # Pojam razlomka — pojmovna, trajno non-contract
# Faza 4C: tri koraka koji GENERISU zadatak u A06/A07 presla su na
# semanticki dvopozivni put (jedan poziv vise po koraku), pa se najveci
# procijenjeni budzet talasa A podigao sa 100 na 103. Hint koraci ostaju
# jednopozivni.
WAVE_A_CALL_BUDGET = 103
WAVE_B_CALL_BUDGET = 150


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
    assert summary["curriculum_lessons_total"] == 536
    assert summary["unique_lessons"] == 39
    assert summary["lesson_coverage_percent"] == pytest.approx(
        100.0 * 39 / 536, abs=0.01)


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


def test_list_cli_survives_a_strict_cp1252_console(tmp_path, monkeypatch):
    row = _scenario()
    row["reason"] = "Unicode evaluator output: čćž → must not crash"
    path = tmp_path / "cp1252.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")

    raw = io.BytesIO()
    cp1252 = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    original = sys.stdout
    monkeypatch.setattr(sys, "stdout", cp1252)
    try:
        result = runner.main(["--scenarios", str(path), "--list"])
        cp1252.flush()
    finally:
        monkeypatch.setattr(sys, "stdout", original)

    output = raw.getvalue().decode("cp1252")
    assert result == 0
    assert "1 scenarios, 0 SDK calls made" in output
    assert "\\u010d" in output and "\\u2192" in output


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


# ---------------------------------------------------------------------------
# TALAS B
# ---------------------------------------------------------------------------

def test_wave_b_has_sixty_scenarios_with_the_expected_id_range():
    scenarios = load_scenarios(WAVE_B)
    assert len(scenarios) == 60
    assert [s.id for s in scenarios] == [f"B{n:02d}" for n in range(1, 61)]
    assert all(s.wave == "B" for s in scenarios)
    assert validate_scenarios(scenarios) == []


def test_wave_a_and_wave_b_never_share_an_id_or_a_session():
    every = load_scenarios(SCENARIO_DIR)
    assert len(every) == 100
    assert len({s.id for s in every}) == 100
    # Izolacija sesija mora vrijediti KROZ oba talasa, ne samo unutar jednog.
    assert len({s.session_id for s in every}) == 100


def test_every_wave_b_scenario_targets_a_wave_a_finding():
    """Scenario bez veze s dokazanim nalazom je nasumičan uzorak, ne dijagnostika."""
    for scenario in load_scenarios(WAVE_B):
        assert scenario.targets_wave_a_findings, scenario.id
        assert scenario.reason.strip(), scenario.id


def test_wave_b_stays_within_its_hundred_and_fifty_call_budget():
    minimum, maximum = runner.estimate_calls(load_scenarios(WAVE_B))
    assert maximum == WAVE_B_CALL_BUDGET
    assert minimum <= maximum


def test_every_topic_id_in_both_waves_exists_in_the_curriculum():
    from matbot.topics import lesson_info

    for scenario in load_scenarios(SCENARIO_DIR):
        assert lesson_info(scenario.grade, scenario.topic_id), \
            f"{scenario.id}: {scenario.topic_id}"
        for index, step in enumerate(scenario.steps):
            override = step.get("topic_id")
            if override:
                assert lesson_info(scenario.grade, override), f"{scenario.id} step{index}"


def test_dry_run_of_all_hundred_scenarios_reports_no_problems_and_no_calls(tmp_path):
    summary = runner.dry_run(load_scenarios(SCENARIO_DIR), tmp_path / "all")
    assert summary["problems"] == []
    assert summary["sdk_calls_made"] == 0
    assert summary["scenarios"] == 100
    assert summary["estimated_model_calls_max"] == WAVE_A_CALL_BUDGET + WAVE_B_CALL_BUDGET


def test_wave_b_gives_grade_nine_the_largest_share():
    """Talas A je u 9. razredu imao samo 30 % determinističkog prolaza."""
    from collections import Counter

    counts = Counter(s.grade for s in load_scenarios(WAVE_B))
    assert counts[9] == max(counts.values())
    assert counts[9] >= 20


def test_wave_b_scenario_without_targets_is_refused(tmp_path):
    bad = dict(_scenario("B99"), wave="B")
    bad.pop("targets_wave_a_findings", None)
    problems = validate_scenarios(_write(tmp_path, bad))
    assert any("targets_wave_a_findings" in problem for problem in problems)


def test_resume_works_for_wave_b_scenarios(tmp_path, fake_llm, monkeypatch):
    monkeypatch.setattr(runner, "_real_llm", lambda: fake_llm)
    row = dict(_scenario("B01"), wave="B", targets_wave_a_findings=["A31"])
    scenarios = _write(tmp_path, row)
    out = tmp_path / "outb"

    queue_two_call(fake_llm)
    _meta, first = runner.run_campaign(scenarios, out, 10, 1, 0, False)
    assert [record.id for record in first] == ["B01"]

    meta, second = runner.run_campaign(scenarios, out, 10, 1, 0, True)
    assert second == []
    assert meta["scenario_count_pending"] == 0


# ---------------------------------------------------------------------------
# EVALUATOR ISPRAVKE IZVEDENE IZ TALASA A
# ---------------------------------------------------------------------------

def test_follow_up_step_is_skipped_when_no_task_was_published(tmp_path, fake_llm, monkeypatch):
    """Talas A: A10/A31/A35 su nakon pada generisanja i dalje izvršavali
    follow-up korake i pravili FAIL-ove koji nisu nezavisni kvarovi."""
    fake_llm.queue(make_tutor_draft(intent="generate_task", new_task=None))   # nema zadatka
    row = _scenario(steps=[
        {"kind": "text", "message": "Daj mi zadatak.", "expect_calls": 2,
         "checks": ["published", "task_published"], "rubrics": []},
        {"kind": "choice", "select": "correct", "expect_calls": 1,
         "requires_active_task": True,
         "checks": ["verdict_correct", "calls_at_most:1"], "rubrics": []},
    ])
    meta, records, _ = _run(tmp_path, _write(tmp_path, row), fake_llm, monkeypatch)

    record = records[0]
    assert record.status == runner.STATUS_FAIL          # prvi korak i dalje pada
    assert record.preconditions_unmet == [
        {"step": 1, "reason": "no active task — step skipped, 0 SDK calls spent"}]
    # Preskočen korak ne troši poziv i ne proizvodi nijednu provjeru.
    assert meta["actual_sdk_calls"] == 1
    assert record.turns[1]["check_results"] == []
    assert not any(entry["step"] == 1 for entry in record.failed_checks)


def test_unmet_precondition_alone_prevents_a_strict_pass(tmp_path, fake_llm, monkeypatch):
    fake_llm.queue(make_tutor_draft(intent="clarification", reply="Pitaj me nešto."))
    row = _scenario(steps=[
        {"kind": "text", "message": "Zdravo.", "expect_calls": 1,
         "checks": ["published"], "rubrics": []},
        {"kind": "text", "message": "Ne znam.", "expect_calls": 1,
         "requires_active_task": True, "checks": ["published"], "rubrics": []},
    ])
    _, records, _ = _run(tmp_path, _write(tmp_path, row), fake_llm, monkeypatch)
    assert records[0].status == runner.STATUS_REVIEW
    assert records[0].preconditions_unmet


def test_task_self_contained_rejects_the_exact_wave_a_finding():
    """Regresija pinovana na A25: objavljen zadatak bez ijednog izraza."""
    published = _observation(response={
        "status": "ready", "answer": "Evo zadatka.\n\nZadatak: Izračunaj vrijednost izraza:",
        "answer_verdict": None, "last_tutor_task": "", "next_state": {"v": 1},
        "session_mode": "practice", "effective_topic": LESSON})
    result = check_lib.check_task_self_contained(published)
    assert result.outcome == check_lib.FAIL
    # Poruka imenuje ZATRAŽENI objekat, ne interpunkciju — evaluator od
    # ispravke B43 koristi uski produkcijski helper, ne pravilo o dvotački.
    assert "Izračunaj vrijednost izraza" in result.detail


def test_task_self_contained_passes_a_normal_task_without_math_delimiters():
    """Lažni pozitiv bi bio gori od promašaja: verbalni zadatak mora proći."""
    ok = _observation(response={
        "status": "ready",
        "answer": "Evo zadatka.\n\nZadatak: Koji od sljedećih brojeva je djeljiv sa 5?",
        "answer_verdict": None, "last_tutor_task": "", "next_state": {"v": 1},
        "session_mode": "practice", "effective_topic": LESSON})
    assert check_lib.check_task_self_contained(ok).outcome == check_lib.PASS

    verbal = _observation(response={
        "status": "ready", "answer": "Evo zadatka.\n\nZadatak: Šta je poluprečnik kružnice?",
        "answer_verdict": None, "last_tutor_task": "", "next_state": {"v": 1},
        "session_mode": "practice", "effective_topic": LESSON})
    assert check_lib.check_task_self_contained(verbal).outcome == check_lib.PASS


def test_a_step_may_switch_the_lesson_inside_one_session(tmp_path, fake_llm, monkeypatch):
    """Bez ovoga se ne može testirati serverska invalidacija pri promjeni teme."""
    for _ in range(2):
        queue_two_call(fake_llm)
    row = _scenario(steps=[
        {"kind": "text", "message": "Daj mi zadatak.", "expect_calls": 2,
         "checks": ["published"], "rubrics": []},
        {"kind": "text", "message": "Daj mi zadatak.", "expect_calls": 2,
         "topic_id": "6-03-005", "checks": ["published", "lesson_matches"], "rubrics": []},
    ])
    _, records, _ = _run(tmp_path, _write(tmp_path, row), fake_llm, monkeypatch)
    turns = records[0].turns
    assert turns[0]["request"]["selected_topic"] == LESSON
    assert turns[1]["request"]["selected_topic"] == "6-03-005"
    assert records[0].failed_checks == []


def test_session_summary_records_the_marked_option_for_offline_audit(tmp_path, fake_llm, monkeypatch):
    """Talas A nije mogao offline provjeriti je li označena opcija ispravna."""
    queue_two_call(fake_llm)
    _, records, _ = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)
    summary = records[0].turns[0]["session_after_summary"]
    assert summary["correct_option_id"] in ("a", "b", "c", "d")
    assert summary["marked_option_text"]
    assert summary["expected_answer"]
    assert summary["marked_option_text"] == summary["expected_answer"]


def test_reviewer_independent_evidence_errors_are_recorded_separately(tmp_path, fake_llm,
                                                                      monkeypatch):
    """Talas A: paket je izgledao čist dok je recenzentov VLASTITI dokaz padao."""
    queue_two_call(fake_llm)
    _, records, _ = _run(tmp_path, _write(tmp_path, _scenario()), fake_llm, monkeypatch)
    turn = records[0].turns[0]
    assert "reviewer_independent_evidence_errors" in turn
    assert turn["reviewer_independent_evidence_errors"] == ""      # ispravan fixture


def test_console_streams_are_switched_to_utf8():
    """Fajlovi Talasa A su bili ispravan UTF-8; konzola pod Windowsom nije."""
    from tools import run_practice_eval

    assert run_practice_eval.install_utf8_streams() is True
    assert (sys.stdout.encoding or "").lower().replace("-", "") == "utf8"


# ---------------------------------------------------------------------------
# EVALUATOR FALSE POSITIVES IZ ŽIVOG RUNA postIncompleteFix
# ---------------------------------------------------------------------------

# B43 — validan MCQ kod kojeg ČETIRI OPCIJE gramatički dovršavaju pitanje.
# Evaluator ga je oborio samo zato što tekst završava dvotačkom.
OPTION_COMPLETED_PROMPTS = (
    "U trouglu $ABC$ centar opisane kružnice je tačka koja se dobije kao:",
    "Odaberi tačnu tvrdnju:",
    "Centar opisane kružnice dobije se kao:",
    "Tačan zapis je:",
    "Koji je od ponuđenih odgovora tačan:",
)

# Dokazano nepotpuni imperativi — moraju i dalje padati.
PROVEN_INCOMPLETE_PROMPTS = (
    "Izračunaj vrijednost izraza:",
    "Riješi jednačinu:",
    "Riješi sistem linearnih jednačina:",
)


def _published(text):
    return _observation(response={
        "status": "ready", "answer": f"Evo zadatka.\n\nZadatak: {text}",
        "answer_verdict": None, "last_tutor_task": "", "next_state": {"v": 1},
        "session_mode": "practice", "effective_topic": LESSON})


@pytest.mark.parametrize("text", OPTION_COMPLETED_PROMPTS)
def test_option_completed_mcq_prompt_is_not_flagged_as_incomplete(text):
    """B43: dvotačka nije dokaz nepotpunosti kad opcije dovršavaju pitanje."""
    assert check_lib.check_task_self_contained(_published(text)).outcome != check_lib.FAIL


@pytest.mark.parametrize("text", PROVEN_INCOMPLETE_PROMPTS)
def test_proven_empty_imperative_still_fails(text):
    assert check_lib.check_task_self_contained(_published(text)).outcome == check_lib.FAIL


def test_evaluator_reuses_the_production_helper_instead_of_its_own_colon_rule():
    """Jedno pravilo za oba sloja — evaluator ne smije imati vlastito, šire."""
    from matbot.tutor.schema import incomplete_task_request

    for text in OPTION_COMPLETED_PROMPTS + PROVEN_INCOMPLETE_PROMPTS:
        production_says_incomplete = bool(incomplete_task_request(text))
        evaluator_fails = check_lib.check_task_self_contained(
            _published(text)).outcome == check_lib.FAIL
        assert production_says_incomplete == evaluator_fails, text


# B52 — dva STVARNO različita zadatka koja se razlikuju u jednom broju.
# Set-Jaccard nad riječima im je dao preklapanje 1.00 i oborio `task_differs`.
B52_FIRST = "Riješi nejednačinu: $x+\frac{1}{2}<2$. Odaberi tačan zaključak."
B52_SECOND = "Riješi nejednačinu: $x+\frac{1}{2}<1$. Odaberi tačan zaključak."


def _task_turn(text, signature, previous_texts=(), previous_signatures=()):
    return check_lib.TurnObservation(
        scenario_id="T", step_index=1, step_kind="text", topic_id=LESSON, grade=6,
        request_payload={}, http_status=200,
        response={"status": "ready", "answer": f"Evo zadatka.\n\nZadatak: {text}"},
        session_before={"current_task": previous_texts[-1] if previous_texts else ""},
        session_after={"current_task": text,
                       "current_task_signature": {"structured_signature_hash": signature}},
        sdk_calls=2, previous_task_texts=tuple(previous_texts),
        previous_task_signatures=tuple(previous_signatures))


def test_two_tasks_differing_only_in_one_number_are_not_duplicates():
    """B52: x+1/2<2 daje x<3/2, a x+1/2<1 daje x<1/2 — različiti zadaci."""
    result = check_lib.check_task_differs(
        _task_turn(B52_SECOND, "sig-b", previous_texts=(B52_FIRST,),
                   previous_signatures=("sig-a",)))
    assert result.outcome == check_lib.PASS


def test_a_repeated_structured_signature_is_still_a_duplicate():
    result = check_lib.check_task_differs(
        _task_turn(B52_SECOND, "sig-a", previous_texts=(B52_FIRST,),
                   previous_signatures=("sig-a",)))
    assert result.outcome == check_lib.FAIL


def test_a_byte_identical_republished_task_is_still_a_duplicate():
    """B51 iz Talasa B: isti tekst objavljen ponovo uz drugi potpis."""
    result = check_lib.check_task_differs(
        _task_turn(B52_FIRST, "sig-new", previous_texts=(B52_FIRST,),
                   previous_signatures=("sig-a",)))
    assert result.outcome == check_lib.FAIL


# ---------------------------------------------------------------------------
# Faza 4G (živi F4G rerun, G03): isti TEKST pitanja s NOVIM opcijama je
# legitiman nov zadatak — serverski kanonski identitet je pitanje+opcije
# (matbot/tutor/task_identity.py), a harness je poredio SAMO tekst, pa je
# ispravan turn („Koji od navedenih brojeva je djeljiv i sa 6 i sa 25?“ s
# drugim brojevima) padao kao „no new task issued“.
# ---------------------------------------------------------------------------

GENERIC = "Koji od navedenih brojeva je djeljiv i sa 6 i sa 25?"


def _identity_turn(identity_before, identity_after, signature,
                   previous_identities=(), previous_signatures=()):
    return check_lib.TurnObservation(
        scenario_id="T", step_index=1, step_kind="text", topic_id=LESSON, grade=6,
        request_payload={}, http_status=200,
        response={"status": "ready", "answer": f"Evo zadatka.\n\nZadatak: {GENERIC}"},
        session_before={"current_task": GENERIC,
                        "current_task_identity": identity_before},
        session_after={"current_task": GENERIC,
                       "current_task_identity": identity_after,
                       "current_task_signature": {"structured_signature_hash": signature}},
        sdk_calls=2, previous_task_texts=(GENERIC,),
        previous_task_identities=tuple(previous_identities),
        previous_task_signatures=tuple(previous_signatures))


def test_same_wording_with_new_options_is_a_new_task():
    obs = _identity_turn("id-a", "id-b", "sig-b",
                         previous_identities=("id-a",),
                         previous_signatures=("sig-a",))
    assert obs.issued_new_task
    assert check_lib.check_task_published(obs).outcome == check_lib.PASS
    assert check_lib.check_task_differs(obs).outcome == check_lib.PASS


def test_a_repeated_canonical_identity_is_still_a_duplicate():
    obs = _identity_turn("id-a", "id-a", "sig-b",
                         previous_identities=("id-a",),
                         previous_signatures=("sig-a",))
    assert not obs.issued_new_task
    assert check_lib.check_task_differs(obs).outcome == check_lib.FAIL
