"""TASK 4 — popravka EVALUATORA/HARNESSA (korijenski uzrok RC11).

Discovery-100 je potrošio 22 od 100 scenarija na ZAMRZNUTE poruke nespojive s
izabranom lekcijom, a sirovi PASS/FAIL nije razlikovao četiri suštinski
različite stvari:

  • pogrešan OBJAVLJEN sadržaj            → pravi kvar proizvoda;
  • SIGURNO odbijena objava               → ispravno ponašanje;
  • nevaljan scenario                     → kvar harnessa;
  • posljedica ranijeg sigurnog odbijanja → nije nezavisan kvar.

Tri konkretne pogrešne klasifikacije koje ovaj modul zamrzava kao regresije:

  E006  lekcija „Podudarnost trouglova - SUS“, zahtjev VAN lekcije, bot ostao
        na lekciji — evaluator je to brojao kao semantički kvar proizvoda.
  B012  scenario JESTE bio nekoherentan, ali je objavljen paket NEZAVISNO
        imao stvaran kvar (opcije `2` i `{2}` su isti odgovor). Dokaz o
        paketu se ne smije izgubiti zbog pokvarene fiksture.
  C001/C002  sankcionisana JEDNOPOZIVNA ruta nije imala isti strukturni zapis
        paketa kao univerzalna, pa je izgledala kao rupa u pokrivenosti.

Ovdje se NE mijenja nijedno produktno ponašanje — samo mjerenje.
ZERO model poziva, ZERO mreže.
"""
from __future__ import annotations

import json

import pytest

from tools.practice_eval import checks as check_lib
from tools.practice_eval import classify, coherence, report as report_lib, runner
from tools.practice_eval.scenario import Scenario, load_scenarios, validate_scenarios

FINAL_WAVE = (runner.ROOT / "tools" / "practice_eval" / "scenarios" / "family"
              / "wave_final40.jsonl")


# ---------------------------------------------------------------------------
# POMOĆNE FIKSTURE — sintetički zapisi, isti oblik koji runner stvarno piše
# ---------------------------------------------------------------------------

def _scenario(sid="T01", grade=7, topic="7-02-019", message="Daj mi zadatak.",
              alignment="must_follow", tags=(), oblast="Cijeli brojevi"):
    return Scenario(
        id=sid, wave="FFINAL40", importance="critical", grade=grade,
        oblast=oblast, topic_id=topic, reason="test", steps=(
            {"kind": "text", "message": message, "expect_calls": 2,
             "checks": ["published"], "rubrics": [], "requires_active_task": False},
        ), tags=tuple(tags), request_alignment=alignment)


def _turn(step=0, calls=2, kinds=("tutor_turn", "reviewer_turn"),
          results=(), precondition=""):
    return {
        "step_index": step, "kind": "text", "sdk_calls": calls,
        "sdk_call_kinds": list(kinds), "precondition_unmet": precondition,
        "check_results": [{"name": name, "outcome": outcome, "detail": detail}
                          for name, outcome, detail in results],
    }


def _record(turns=(), failed=(), status="FAIL"):
    return {
        "id": "T01", "status": status, "turns": list(turns),
        "failed_checks": [{"check": name, "step": step, "detail": detail,
                           "label": f"step{step}:{name}"}
                          for name, step, detail in failed],
        "skipped_checks": [], "rubrics": [],
    }


# ---------------------------------------------------------------------------
# 1 + 2) KOHERENTNOST SCENARIJA — valjan prolazi, nespojiv se prijavljuje
# ---------------------------------------------------------------------------

def test_compatible_prompt_and_lesson_is_a_valid_scenario():
    """Nejednačina na lekciji o nejednačinama — nema šta da se prijavi."""
    valid = _scenario(
        topic="7-02-019",       # Nejednačine sa sabiranjem i oduzimanjem u Z
        message="Kreiraj MCQ: riješi nejednačinu $-2<x+1<2$ u skupu cijelih brojeva.")
    assert coherence.coherence_problems(valid) == []


def test_incompatible_prompt_and_lesson_is_flagged_as_a_confound():
    """Tačna živa klasa A007/A013/B010/B011/B012: nejednačina na lekciji
    JEDNAČINA. Bot slijedi lekciju — ispravno — pa je očekivanje nevaljano."""
    confound = _scenario(
        topic="7-02-016",       # Jednačine sa sabiranjem i oduzimanjem u Z
        message="Kreiraj MCQ: riješi nejednačinu $x>-2$ u skupu cijelih brojeva.")
    problems = coherence.coherence_problems(confound)
    assert problems and coherence.RELATION_KIND_CONFLICT in problems[0]


def test_equation_request_on_an_inequality_lesson_is_also_flagged():
    """Živi A024, obrnut smjer — jednačina na lekciji o nejednačinama.
    Relacija `2(x-1)=2x+1` NIJE rješiva, pa se vrsta dokazuje iz operatora."""
    confound = _scenario(
        grade=9, oblast="Linearne jednačine i nejednačine", topic="9-04-015",
        message="Kreiraj MCQ: riješi 2(x-1)=2x+1 u skupu realnih brojeva.")
    problems = coherence.coherence_problems(confound)
    assert problems and coherence.RELATION_KIND_CONFLICT in problems[0]


def test_lesson_contract_conflict_is_flagged():
    """Živi E008/F005/F007/F008: zahtjev koji ne može zadovoljiti blokirajući
    semantički ugovor lekcije (mreža tijela) je nevaljan scenario."""
    confound = _scenario(
        grade=8, oblast="Geometrijska tijela", topic="8-05-009",   # Mreža trostrane prizme
        message="Kreiraj MCQ: riješi nejednačinu $x+1<4$ u skupu cijelih brojeva.")
    problems = coherence.coherence_problems(confound)
    assert problems and coherence.LESSON_CONTRACT_CONFLICT in problems[0]


def test_non_solve_lesson_conflict_is_flagged():
    """Živi A015: rješavanje jednačine na lekciji o SKUPOVIMA brojeva."""
    confound = _scenario(
        grade=8, oblast="Realni brojevi", topic="8-01-001",   # Skupovi N, Z, Q, I i R
        message="Kreiraj MCQ: riješi jednačinu $2x-5=0$ u skupu realnih brojeva.")
    problems = coherence.coherence_problems(confound)
    assert problems and coherence.NON_SOLVE_LESSON_CONFLICT in problems[0]


def test_unprovable_incompatibility_is_never_asserted():
    """Nedokazivo NIJE „nevaljan scenario“ — evaluator ne pogađa kurikulum."""
    unclear = _scenario(
        grade=6, oblast="Uglovi", topic="6-09-001",
        message="Daj mi zanimljiv zadatak o uglovima.")
    assert coherence.coherence_problems(unclear) == []


def test_confound_becomes_the_harness_outcome_class():
    scenario = _scenario(
        topic="7-02-016",
        message="Kreiraj MCQ: riješi nejednačinu $x>-2$ u skupu cijelih brojeva.")
    verdict = classify.classify(
        _record(turns=[_turn(results=[("published", "fail", "safe_error")])],
                failed=[("published", 0, "safe_error")]),
        scenario)
    assert verdict["outcome_class"] == classify.HARNESS_INVALID_SCENARIO
    assert verdict["coherence_problems"]


# ---------------------------------------------------------------------------
# 3) B012 — DOKAZ O PAKETU PREŽIVLJAVA NEVALJAN SCENARIO
# ---------------------------------------------------------------------------

def test_b012_package_evidence_survives_a_confounded_scenario():
    """Scenario je nekoherentan, ALI su objavljene opcije `2` i `{2}` isti
    odgovor. Taj kvar je stvaran (zatvoren u Task 1) i ne smije nestati zato
    što je drugo polje fiksture bilo pokvareno."""
    confounded = _scenario(
        grade=9, oblast="Linearne jednačine i nejednačine", topic="9-04-004",
        message="Napravi solve-linear MCQ za -4<=3x-1<8.")
    assert coherence.coherence_problems(confounded), "scenario je zaista nevaljan"

    record = _record(
        turns=[_turn(results=[
            ("published", "pass", ""),
            ("options_ok", "fail", "semantically_duplicate_options=[(0, 3)]")])],
        failed=[("options_ok", 0, "semantically_duplicate_options=[(0, 3)]")])
    verdict = classify.classify(record, confounded)

    assert verdict["outcome_class"] == classify.PRODUCT_CORRECTNESS_FAILURE
    assert verdict["package_evidence"], "dokaz o paketu je izgubljen"
    assert verdict["coherence_problems"], "nekoherentnost se i dalje prijavljuje"
    assert any("independently" in note for note in verdict["notes"])


def test_uncaptured_infrastructure_turn_is_not_package_evidence():
    """FINAL-40 FW-D01: SDK failure happened before generation, so the
    mechanical `options_ok` failure cannot describe a product package."""
    turn = _turn(calls=1, kinds=("tutor_turn",),
                 results=[("options_ok", "fail", "option_count=0")])
    turn["package_captured"] = False
    record = _record(
        status="RATE_LIMITED", turns=[turn],
        failed=[("options_ok", 0, "option_count=0")])
    # Simulate the stale persisted value written by the interrupted run; the
    # report must recompute from raw turn evidence instead of trusting it.
    record.update({
        "id": "FW-D01", "topic_id": "9-04-002", "grade": 9,
        "oblast": "Linearne jednačine i nejednačine",
        "importance": "critical", "package_evidence": record["failed_checks"],
    })

    verdict = classify.classify(record)
    assert verdict["outcome_class"] == classify.INFRA_SDK
    assert verdict["package_evidence"] == []
    assert verdict["outcome_class"] not in {
        classify.PRODUCT_CORRECTNESS_FAILURE,
        classify.SAFE_FAIL_CLOSED,
        classify.COVERAGE_GAP,
    }
    summary = report_lib.build_summary({}, [record], 534)
    assert summary["package_level_product_evidence"] == []


def test_confounded_scenario_without_package_evidence_stays_harness():
    """Kontrola uz prethodni test: bez nezavisnog dokaza o paketu nevaljan
    scenario ostaje kvar harnessa, ne proizvoda."""
    confounded = _scenario(
        topic="7-02-016",
        message="Kreiraj MCQ: riješi nejednačinu $x>-2$ u skupu cijelih brojeva.")
    verdict = classify.classify(
        _record(turns=[_turn(results=[("task_published", "fail", "no task")])],
                failed=[("task_published", 0, "no task")]),
        confounded)
    assert verdict["outcome_class"] == classify.HARNESS_INVALID_SCENARIO
    assert verdict["package_evidence"] == []


# ---------------------------------------------------------------------------
# 4 + 5) E006 vs E009 — lekcijski prioritet nije kvar, stvaran drift jeste
# ---------------------------------------------------------------------------

def test_e006_off_lesson_request_with_lesson_priority_is_not_a_failure():
    """Živi E006: zahtjev van lekcije, bot ostao na lekciji. To je ISPUNJEN
    ugovor (lekcija je vlasnik vježbe) — nikad semantički kvar proizvoda."""
    probe = _scenario(
        grade=7, oblast="Ugao i trougao", topic="7-04-013",   # Podudarnost - SUS
        message="Daj mi zadatak o kvadratnim jednačinama.",
        alignment="lesson_overrides")
    record = _record(
        status="PASS",
        turns=[_turn(results=[("stays_in_lesson", "pass", ""),
                              ("lesson_matches", "pass", ""),
                              ("published", "pass", "")])])
    verdict = classify.classify(record, probe)

    assert verdict["outcome_class"] == classify.CLEAN
    assert verdict["lesson_priority_honoured"] is True
    assert not classify.semantic_false_accept(record, probe)
    assert any("lesson priority honoured" in note for note in verdict["notes"])
    # Nespojivost je SVRHA sonde — nikad se ne prijavljuje kao kvar fiksture.
    assert coherence.coherence_problems(probe) == []


def test_e009_real_lesson_drift_is_still_detected():
    """Kontrola: kad bot STVARNO napusti lekciju, to i dalje pada. Granica se
    nije pomjerila — mjeri se `stays_in_lesson` i semantički ugovor lekcije."""
    probe = _scenario(
        grade=7, oblast="Ugao i trougao", topic="7-04-022",
        message="Napravi zadatak o podudarnosti trouglova.",
        alignment="lesson_overrides")
    drifted = _record(
        turns=[_turn(results=[
            ("package_clean", "fail",
             "semantic_fidelity_violation: angle_bisector_incenter_semantics")])],
        failed=[("package_clean", 0,
                 "semantic_fidelity_violation: angle_bisector_incenter_semantics")])
    verdict = classify.classify(drifted, probe)

    assert verdict["outcome_class"] == classify.PRODUCT_CORRECTNESS_FAILURE
    assert classify.semantic_false_accept(drifted, probe) is True


def test_leaving_the_selected_lesson_is_a_semantic_failure():
    left = _record(turns=[_turn(results=[("stays_in_lesson", "fail", "moved to 9-01-003")])],
                   failed=[("stays_in_lesson", 0, "moved to 9-01-003")])
    assert classify.semantic_false_accept(left) is True


# ---------------------------------------------------------------------------
# 6 + 7 + 8) RUTE — jednopozivna, deterministička, univerzalna
# ---------------------------------------------------------------------------


def test_deterministic_route_makes_zero_calls():
    assert classify.turn_route(
        {"sdk_calls": 0, "sdk_call_kinds": []}
    ) == classify.ROUTE_DETERMINISTIC


def test_universal_route_is_two_calls_tutor_and_reviewer():
    assert classify.turn_route(
        {"sdk_calls": 2, "sdk_call_kinds": ["tutor_turn", "reviewer_turn"]}
    ) == classify.ROUTE_UNIVERSAL_TWO_CALL


def test_skipped_precondition_is_not_mistaken_for_a_route():
    assert classify.turn_route(
        {"sdk_calls": 0, "sdk_call_kinds": [], "precondition_unmet": "no active task"}
    ) == classify.ROUTE_NO_MODEL_TURN


def test_no_package_anywhere_is_still_reported_as_missing():
    """Knjigovodstvo ostaje istinito: kad paketa nema, ne izmišlja se."""
    assert runner._final_task_package(
        {"reviewer_output": None, "tutor_output": None,
         "single_call_output": None}) is None


# ---------------------------------------------------------------------------
# 6b) JEDNOPOZIVNA RUTA KROZ STVARNI RUNNER — bez lažne rupe u pokrivenosti
# ---------------------------------------------------------------------------

@pytest.fixture
def _levels_on(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _single_call_scenario(tmp_path):
    row = {
        "id": "SC01", "wave": "A", "importance": "critical", "grade": 6,
        "oblast": "Razlomci", "topic_id": "6-04-001",
        "reason": "sanctioned single-call fraction route",
        "tags": ["unit"],
        "steps": [{"kind": "text", "message": "Daj mi zadatak.",
                   "expect_calls": 2,
                   "checks": ["published", "task_published", "package_clean",
                              "options_ok"],
                   "rubrics": []}],
    }
    path = tmp_path / "single.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    return load_scenarios(path)


# ---------------------------------------------------------------------------
# 9) TREĆI POZIV — nikad se ne skriva
# ---------------------------------------------------------------------------

def test_third_call_is_flagged_as_a_product_failure():
    record = _record(
        status="PASS",
        turns=[_turn(calls=3, kinds=("tutor_turn", "reviewer_turn", "tutor_turn"))])
    violations = classify.third_call_violations(record)
    assert violations and violations[0]["sdk_calls"] == 3
    assert classify.classify(record)["outcome_class"] == \
        classify.PRODUCT_CORRECTNESS_FAILURE


def test_third_call_is_not_hidden_behind_an_invalid_scenario():
    """Prekoračenje granice poziva je produktno pravilo (CLAUDE.md, 4) i ne
    smije se izgubiti ni kad je scenario nevaljan."""
    confounded = _scenario(
        topic="7-02-016",
        message="Kreiraj MCQ: riješi nejednačinu $x>-2$ u skupu cijelih brojeva.")
    record = _record(status="PASS", turns=[_turn(calls=3)])
    assert classify.classify(record, confounded)["outcome_class"] == \
        classify.PRODUCT_CORRECTNESS_FAILURE


def test_two_calls_are_never_flagged():
    assert classify.third_call_violations(_record(turns=[_turn(calls=2)])) == []


# ---------------------------------------------------------------------------
# 10) KORIJEN vs POSLJEDICA
# ---------------------------------------------------------------------------

def test_later_state_divergence_after_a_safe_block_is_cascade_only():
    """Kad prvi korak sigurno odbije objavu, kasnija odstupanja težine/stanja
    su POSLJEDICA — ne broje se kao nezavisni kvarovi (discovery difficulty
    grupa)."""
    record = _record(
        turns=[
            _turn(step=0, results=[("published", "fail", "safe error")]),
            _turn(step=1, results=[("level:2", "fail", "committed level=1"),
                                   ("task_differs", "fail", "no new task")]),
        ],
        failed=[("published", 0, "safe error"),
                ("level:2", 1, "committed level=1"),
                ("task_differs", 1, "no new task")])
    split = classify.cascade_split(record)
    assert split["root_step"] == 0
    assert [entry["check"] for entry in split["root"]] == ["published"]
    assert sorted(entry["check"] for entry in split["cascade"]) == \
        ["level:2", "task_differs"]


def test_cascade_only_scenario_is_classified_as_cascade():
    record = _record(
        turns=[
            _turn(step=0, results=[("not_safe_error", "fail", "canned")]),
            _turn(step=1, results=[("level:2", "fail", "committed level=1")]),
        ],
        failed=[("level:2", 1, "committed level=1")])
    # Korijenski korak nije među failed_checks (npr. druga provjera ga je
    # prijavila), pa je sve preostalo posljedica.
    verdict = classify.classify(record)
    assert verdict["outcome_class"] == classify.CASCADE_ONLY


def test_package_evidence_is_never_demoted_to_cascade():
    """Sadržaj objavljen na kasnijem koraku je mjeren na TOM koraku — nikad
    posljedica ranijeg odbijanja."""
    record = _record(
        turns=[
            _turn(step=0, results=[("published", "fail", "safe error")]),
            _turn(step=1, results=[("options_ok", "fail", "duplicate options")]),
        ],
        failed=[("published", 0, "safe error"),
                ("options_ok", 1, "duplicate options")])
    split = classify.cascade_split(record)
    assert [entry["check"] for entry in split["cascade"]] == []
    assert classify.classify(record)["outcome_class"] == \
        classify.PRODUCT_CORRECTNESS_FAILURE


def test_a_blocked_publication_alone_is_safe_not_wrong_content():
    """Sigurno odbijanje objave NIKAD ne postaje „pogrešan sadržaj“."""
    record = _record(
        turns=[_turn(results=[("published", "fail", "safe error"),
                              ("task_published", "fail", "no task")])],
        failed=[("published", 0, "safe error"), ("task_published", 0, "no task")])
    verdict = classify.classify(record)
    assert verdict["outcome_class"] == classify.SAFE_FAIL_CLOSED
    assert verdict["package_evidence"] == []


def _captured_rejected_candidate_record(*, calls=2, status="FAIL"):
    turn = _turn(calls=calls, results=[
        ("published", "fail", "safe error"),
        ("task_published", "fail", "turn not published"),
        ("package_clean", "fail", "multiple_correct_options: x=1"),
    ])
    turn.update({
        "package_captured": True,
        "response": {"answer": "safe error", "last_tutor_task": ""},
        "session_after_summary": {},
        "reviewer_decision": "fail_closed",
        "tutor_draft_issues": "multiple_correct_options: x=1",
        "reviewer_final_issues": "multiple_correct_options: x=1",
        "log_lines": [
            "tutor_rejected request_id=test stage=reviewer_final_mcq "
            "detail=multiple_correct_options: x=1"
        ],
    })
    return _record(
        status=status, turns=[turn],
        failed=[
            ("published", 0, "safe error"),
            ("task_published", 0, "turn not published"),
            ("package_clean", 0, "multiple_correct_options: x=1"),
        ])


def test_captured_bad_candidate_rejected_before_commit_is_safe_fail_closed():
    """FINAL40: nalaz ostaje vidljiv, ali odbijeni kandidat nije objavljen kvar."""
    record = _captured_rejected_candidate_record()
    verdict = classify.classify(record)

    assert verdict["outcome_class"] == classify.SAFE_FAIL_CLOSED
    assert [entry["check"] for entry in verdict["package_evidence"]] == [
        "package_clean"]
    assert classify.safely_rejected_package_steps(
        record, verdict["package_evidence"]) == {0}
    assert any("findings preserved" in note for note in verdict["notes"])


def test_captured_bad_package_that_publishes_remains_product_failure():
    turn = _turn(results=[
        ("published", "pass", ""),
        ("task_published", "pass", ""),
        ("package_clean", "fail", "multiple_correct_options: x=1"),
    ])
    turn.update({
        "package_captured": True,
        "response": {"status": "ready", "last_tutor_task": "Solve x=1"},
        "session_after_summary": {
            "current_task_chars": 9, "task_signature_hash": "published",
            "correct_option_id": "a", "expected_answer": "x=1",
        },
    })
    record = _record(
        turns=[turn],
        failed=[("package_clean", 0, "multiple_correct_options: x=1")])

    assert classify.classify(record)["outcome_class"] == \
        classify.PRODUCT_CORRECTNESS_FAILURE
    assert classify.safely_rejected_package_steps(record) == set()


def test_rejection_label_cannot_hide_committed_task_state():
    record = _captured_rejected_candidate_record()
    record["turns"][0]["session_after_summary"] = {
        "current_task_chars": 8, "task_signature_hash": "committed",
    }

    assert classify.safely_rejected_package_steps(record) == set()
    assert classify.classify(record)["outcome_class"] == \
        classify.PRODUCT_CORRECTNESS_FAILURE


def test_later_independently_bad_publication_wins_over_earlier_safe_rejection():
    record = _captured_rejected_candidate_record()
    later = _turn(step=1, results=[
        ("published", "pass", ""),
        ("package_clean", "fail", "wrong marked answer"),
    ])
    later.update({
        "package_captured": True,
        "response": {"status": "ready", "last_tutor_task": "bad task"},
        "session_after_summary": {"current_task_chars": 8,
                                  "task_signature_hash": "bad"},
    })
    record["turns"].append(later)
    record["failed_checks"].append({
        "check": "package_clean", "step": 1, "detail": "wrong marked answer",
        "label": "step1:package_clean",
    })

    verdict = classify.classify(record)
    assert verdict["outcome_class"] == classify.PRODUCT_CORRECTNESS_FAILURE
    assert {entry["step"] for entry in verdict["package_evidence"]} == {0, 1}


def test_safe_candidate_with_a_third_call_is_still_blocking():
    verdict = classify.classify(_captured_rejected_candidate_record(calls=3))
    assert verdict["third_call_violations"]
    assert verdict["outcome_class"] == classify.PRODUCT_CORRECTNESS_FAILURE


def test_sdk_infrastructure_status_wins_over_candidate_classification():
    verdict = classify.classify(
        _captured_rejected_candidate_record(status="INFRA_ERROR"))
    assert verdict["outcome_class"] == classify.INFRA_SDK


def test_safe_fail_closed_report_keeps_findings_reviewer_and_call_count():
    record = _captured_rejected_candidate_record()
    verdict = classify.classify(record)
    record.update({
        "id": "FW-SAFE", "topic_id": "7-02-019", "grade": 7,
        "oblast": "Cijeli brojevi", "importance": "critical", "sdk_calls": 2,
        "outcome_class": verdict["outcome_class"],
        "routes": verdict["routes"],
        "third_call_violations": verdict["third_call_violations"],
        "root_failures": verdict["root_failures"],
        "cascade_failures": verdict["cascade_failures"],
        "coherence_problems": [],
    })
    summary = report_lib.build_summary({}, [record], 534)
    detail = summary["safe_fail_closed_details"][0]
    assert detail["scenario"] == "FW-SAFE"
    assert detail["sdk_calls"] == 2
    assert detail["reviewer_actions"] == ["fail_closed"]
    assert "multiple_correct_options" in " ".join(detail["preflight_findings"])
    summary["examples"] = {}
    markdown = report_lib.render_markdown(summary)
    assert "SAFE_FAIL_CLOSED dijagnostika" in markdown
    assert "FW-SAFE" in markdown and "Reviewer: `fail_closed`" in markdown


def test_review_without_any_failure_is_a_coverage_gap():
    verdict = classify.classify(_record(status="REVIEW", turns=[_turn()]))
    assert verdict["outcome_class"] == classify.COVERAGE_GAP


@pytest.mark.parametrize("status,expected", [
    ("TIMEOUT", classify.TIMEOUT),
    ("INFRA_ERROR", classify.INFRA_SDK),
    ("RATE_LIMITED", classify.INFRA_SDK),
])
def test_transport_statuses_never_become_product_failures(status, expected):
    assert classify.classify(_record(status=status, turns=[_turn()]))[
        "outcome_class"] == expected


def _equivalence_observation(request, task):
    return check_lib.TurnObservation(
        scenario_id="FW-R02", step_index=0, step_kind="text",
        topic_id="7-03-019", grade=7,
        request_payload={"student_message": request}, http_status=200,
        response={"status": "ready", "answer": "Evo zadatka."},
        session_before=None, session_after={"current_task": task}, sdk_calls=2,
    )


TR_A3_REQUEST = (
    "Kreiraj samostalan MCQ sa četiri opcije za nejednačinu $x>3$. U tekstu "
    "pokušaj preoblikovanje tako što lijevoj strani dodaš 2, a desnoj strani "
    "4, pa nastavi sa dobijenom nejednačinom. Traži cijeli skup rješenja i "
    "tačno jednu matematički tačnu opciju. Ne rješavaj zadatak učeniku.")
TR_A3_BAD_TASK = (
    "Početna nejednačina je $x>3$. Dodajemo $2$ s lijeve strane i $4$ s "
    "desne strane pa dobijamo $x+2>7$. Riješi dobijenu nejednačinu $x+2>7$ "
    "i nađi cijeli skup rješenja.")


def _record_for_request_fidelity_result(result, *, published=True):
    publication = "pass" if published else "fail"
    check_results = [
        ("published", publication, "" if published else "turn not published"),
        ("task_published", publication,
         "" if published else "turn not published"),
        (result.name, result.outcome, result.detail),
    ]
    turn = _turn(results=check_results)
    turn.update({
        "package_captured": True,
        "response": ({"status": "ready", "last_tutor_task": TR_A3_BAD_TASK}
                     if published else {"last_tutor_task": ""}),
        "session_after_summary": (
            {"current_task_chars": len(TR_A3_BAD_TASK),
             "task_signature_hash": "historical-tr-a3",
             "correct_option_id": "c", "expected_answer": "(5,infinity)"}
            if published else {}),
    })
    failed = [(name, 0, detail) for name, outcome, detail in check_results
              if outcome == check_lib.FAIL]
    return _record(turns=[turn], failed=failed,
                   status="FAIL" if failed else "PASS")


def test_historical_tr_a3_is_a_published_request_fidelity_failure():
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation(TR_A3_REQUEST, TR_A3_BAD_TASK))
    assert result.outcome == check_lib.FAIL, result
    assert "transformed_relation_mismatch" in result.detail

    verdict = classify.classify(_record_for_request_fidelity_result(result))
    assert verdict["outcome_class"] == classify.PRODUCT_CORRECTNESS_FAILURE
    assert [entry["check"] for entry in verdict["package_evidence"]] == [
        "request_equivalent_reformulation"]


def test_same_tr_a3_candidate_rejected_before_commit_is_safe_fail_closed():
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation(TR_A3_REQUEST, TR_A3_BAD_TASK))
    record = _record_for_request_fidelity_result(result, published=False)
    verdict = classify.classify(record)

    assert verdict["outcome_class"] == classify.SAFE_FAIL_CLOSED
    assert verdict["package_evidence"]
    assert classify.safely_rejected_package_steps(record) == {0}


def test_positive_equivalence_check_requires_a_distinct_equal_solve_set():
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation("Riješi nejednačinu $x>3$.",
                                 "Riješi nejednačinu $x+2>5$."))
    assert result.outcome == check_lib.PASS, result

    repeated = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation("Riješi nejednačinu $x>3$.",
                                 "Riješi nejednačinu $x>3$."))
    assert repeated.outcome == check_lib.FAIL

    drifted = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation("Riješi nejednačinu $x>3$.",
                                 "Riješi nejednačinu $x<3$."))
    assert drifted.outcome == check_lib.FAIL


def test_equivalence_check_passes_for_canonical_reformulation():
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation("Riješi nejednačinu $x>3$.",
                                 "Riješi nejednačinu $x+2>5$."))
    assert result.outcome == check_lib.PASS, result


def test_equivalence_check_passes_with_original_and_resulting_inequality():
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation(
            "Riješi nejednačinu $x>3$.",
            "Početna nejednačina je $x>3$, a nakon preoblikovanja dobijamo "
            "$x+2>5$. Riješi dobijenu nejednačinu $x+2>5$."))
    assert result.outcome == check_lib.PASS, result


def test_equivalence_check_passes_with_original_and_resulting_equation():
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation(
            "Riješi jednačinu $2x=6$.",
            "Originalna jednačina je $2x=6$, a preoblikovana jednačina je "
            "$x=3$. Riješi rezultujuću jednačinu $x=3$."))
    assert result.outcome == check_lib.PASS, result


def test_equivalence_check_fails_when_expected_resulting_relation_is_missing():
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation(
            "Riješi nejednačinu $x>3$ i preoblikuj je.",
            "Riješi dobijenu nejednačinu i izaberi skup rješenja."))
    assert result.outcome == check_lib.FAIL, result
    assert "missing_requested_relation" in result.detail


def test_equivalence_check_does_not_invent_semantics_for_present_unreadable_relation():
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation(
            "Riješi nejednačinu $x>3$.",
            r"Riješi dobijenu nejednačinu $x^2>4$."))
    assert result.outcome == check_lib.SKIP, result
    assert "readable relation" in result.detail


@pytest.mark.parametrize("relation", [r"$x^2>9$", r"$|x|<3$"])
def test_equivalence_check_stays_conservative_for_nonlinear_relations(relation):
    result = check_lib.check_request_equivalent_reformulation(
        _equivalence_observation(
            "Riješi nejednačinu $x>3$.",
            f"Riješi dobijenu nejednačinu {relation}."))
    assert result.outcome == check_lib.SKIP, result
    assert "readable relation" in result.detail


# ---------------------------------------------------------------------------
# 11) POLITIKA N / N0 — prikovana uz proizvod
# ---------------------------------------------------------------------------

def test_evaluator_domain_policy_matches_the_product_exactly():
    """N = {1,2,3,...} i N0 = {0,1,2,...}. Evaluator ovu konvenciju ne smije
    tumačiti drugačije od servera (živi A020)."""
    from matbot import mcq_integrity

    assert coherence.DOMAIN_POLICY == {"N": 1, "N0": 0, "Z": None}
    assert coherence.domain_policy_matches_product()
    assert dict(mcq_integrity._SOLVE_DISCRETE_DOMAIN_MIN) == coherence.DOMAIN_POLICY


def test_ambiguous_domain_name_is_refused():
    class _Spec:
        id = "T99"
        discovery_spec = {"domain": "prirodni_sa_nulom"}

    problems = coherence.domain_policy_problems([_Spec()])
    assert problems and "prirodni_sa_nulom" in problems[0]


# ---------------------------------------------------------------------------
# ZAVRŠNI TALAS — spreman za konsolidovanu živu verifikaciju
# ---------------------------------------------------------------------------

def _final_scenarios():
    return load_scenarios(FINAL_WAVE)


def test_final_wave_is_forty_coherent_scenarios():
    scenarios = _final_scenarios()
    assert len(scenarios) == 40
    assert validate_scenarios(scenarios) == []
    assert coherence.validate_wave(scenarios) == []
    assert coherence.domain_policy_problems(scenarios) == []


def test_final_wave_covers_every_planned_category():
    """Traženi raspored §11: 12 domen · 8 zapis · 6 funkcija · 6 geometrija ·
    4 vjernost zahtjevu · 4 regresija."""
    counts = {}
    for scenario in _final_scenarios():
        for tag in scenario.tags:
            if tag.startswith("group_"):
                counts[tag] = counts.get(tag, 0) + 1
    assert counts == {
        "group_domain": 12, "group_representation": 8, "group_function": 6,
        "group_geometry": 6, "group_request_fidelity": 4, "group_regression": 4,
    }


def test_final_wave_replays_every_required_historical_identifier():
    required = {"A012", "A020", "E02", "B007", "B012", "B013", "B014", "B017",
                "C008", "D005", "A009", "A010", "A023", "F008", "E009"}
    covered = set()
    for scenario in _final_scenarios():
        covered.update(scenario.targets_wave_a_findings)
    assert required <= covered, sorted(required - covered)


def test_final_wave_contains_all_follow_up_controls():
    scenarios = _final_scenarios()
    by_tag = {tag: scenario for scenario in scenarios for tag in scenario.tags}

    e010 = by_tag["e010_mitigation"]
    assert e010.id == "FW-G04"
    assert "E010" in e010.targets_wave_a_findings
    assert "mitigation_not_full_oracle" in e010.tags

    equivalence = by_tag["request_equivalence_positive"]
    assert equivalence.id == "FW-R02"
    assert "request_equivalent_reformulation" in equivalence.steps[0]["checks"]

    zero_call = by_tag["zero_call_control"]
    assert zero_call.id == "FW-X02"
    assert all(step["expect_calls"] == 0 for step in zero_call.steps)
    assert all("zero_calls" in step["checks"] for step in zero_call.steps)

    hints = by_tag["hint_ladder"]
    hint_steps = [step for step in hints.steps
                  if step.get("intent") == "hint_request"]
    assert len(hint_steps) == 3
    assert all(step["expect_calls"] == 1 for step in hint_steps)
    assert "hint_no_leak" in hint_steps[0]["checks"]
    assert "hint_no_leak" in hint_steps[1]["checks"]
    assert "solution_complete" in hint_steps[2]["checks"]


def test_final_wave_sessions_are_isolated_and_ids_unique():
    scenarios = _final_scenarios()
    assert len({s.id for s in scenarios}) == len(scenarios)
    assert len({s.session_id for s in scenarios}) == len(scenarios)


def test_final_wave_declares_lesson_priority_probes_explicitly():
    """Sonda van lekcije mora biti IZRIČITO označena — inače bi je koherentnost
    prijavila kao nevaljan scenario (i to je upravo E006 ispravka)."""
    overrides = [s for s in _final_scenarios()
                 if s.request_alignment == "lesson_overrides"]
    assert overrides, "završni talas mora nositi bar jednu sondu lekcijskog prioriteta"
    for scenario in overrides:
        assert {"E006", "E009", "E010"} & set(scenario.targets_wave_a_findings)


def test_final_wave_dry_run_passes_with_zero_model_calls(tmp_path):
    """Cio talas prolazi offline validaciju — nijedan živi poziv nije potrošen."""
    report = runner.dry_run(_final_scenarios(), tmp_path)
    assert report["problems"] == [], report["problems"]
    assert report["scenarios"] == 40
    assert report["sdk_calls_made"] == 0


def test_dry_run_refuses_a_confounded_wave(tmp_path):
    """RC11 zaštita: nespojiv par poruke i lekcije se hvata PRIJE talasa."""
    row = {
        "id": "BAD-01", "wave": "FFINAL40", "importance": "critical", "grade": 7,
        "oblast": "Cijeli brojevi", "topic_id": "7-02-016",
        "reason": "frozen inequality prompt on an equations lesson",
        "tags": ["unit"],
        "steps": [{"kind": "text", "expect_calls": 2,
                   "message": "Kreiraj MCQ: riješi nejednačinu $x>-2$ u Z.",
                   "checks": ["published"], "rubrics": []}],
    }
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    report = runner.dry_run(load_scenarios(path), tmp_path)
    assert any(coherence.RELATION_KIND_CONFLICT in problem
               for problem in report["problems"]), report["problems"]
    assert report["sdk_calls_made"] == 0


def test_unknown_request_alignment_is_refused(tmp_path):
    row = {
        "id": "BAD-02", "wave": "A", "importance": "critical", "grade": 6,
        "oblast": "Razlomci", "topic_id": "6-04-001", "reason": "x",
        "tags": [], "request_alignment": "whatever",
        "steps": [{"kind": "text", "message": "m", "expect_calls": 1,
                   "checks": ["published"], "rubrics": []}],
    }
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(Exception) as error:
        load_scenarios(path)
    assert "request_alignment" in str(error.value)
