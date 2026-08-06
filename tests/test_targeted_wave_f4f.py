"""Kombinovani talas F4F mora biti ispravan PRIJE ijednog plaćenog poziva.

Talas pokriva pet popravki Faze 4F zajedno: budžet recenzenta, kanonsku
jedinstvenost novog zadatka, UI stanje vezano za identitet, opstanak zadatka
kroz sigurnu grešku i paritet release konfiguracije.

Nula SDK poziva — provjerava se samo definicija.
"""
from pathlib import Path

from tools.practice_eval.checks import known_check_names
from tools.practice_eval.scenario import load_scenarios, validate_scenarios

WAVE = (Path(__file__).resolve().parent.parent / "tools" / "practice_eval"
        / "scenarios" / "family" / "wave_f4f.jsonl")


def _scenarios():
    return load_scenarios(WAVE)


def test_wave_loads_and_is_internally_consistent():
    scenarios = _scenarios()
    assert len(scenarios) == 26
    assert validate_scenarios(scenarios) == []
    assert len({s.session_id for s in scenarios}) == len(scenarios)
    assert all(s.reason.strip() for s in scenarios)


def test_every_check_name_is_known_to_the_runner():
    known = set(known_check_names())
    for scenario in _scenarios():
        for step in scenario.steps:
            for check in step["checks"]:
                normalized = check.split(":")[0] + ":N" if ":" in check else check
                assert normalized in known, f"{scenario.id}: {check!r}"


def test_the_call_budget_stays_within_the_authorised_ceiling():
    total = sum(s.max_model_calls for s in _scenarios())
    assert total == 100 and total <= 120, total


def test_every_explicit_new_task_step_requires_a_different_task():
    """Srž nalaza B: „novi/lakši/teži zadatak“ mora dati drugačiji zadatak."""
    checked = 0
    for scenario in _scenarios():
        for index, step in enumerate(scenario.steps):
            message = (step.get("message") or "").lower()
            if step["kind"] != "text" or "zadatak" not in message:
                continue
            if not any(word in message for word in ("novi", "još jedan", "lakši", "teži")):
                continue
            if index == 0:
                # Prvi korak nema aktivan zadatak od kojeg bi se razlikovao —
                # `task_differs` tu nema šta da poredi.
                continue
            assert "task_differs" in step["checks"], f"{scenario.id}: {message!r}"
            checked += 1
    assert checked >= 10, checked


def test_help_steps_never_expect_a_new_task():
    for scenario in _scenarios():
        for step in scenario.steps:
            if step.get("intent") in ("hint_request", "solution_request"):
                assert "no_new_task" in step["checks"]
                assert "task_preserved" in step["checks"]
                assert "task_published" not in step["checks"]


def test_the_wave_spans_more_than_one_lesson_and_oblast():
    scenarios = _scenarios()
    assert len({s.topic_id for s in scenarios}) >= 4
    assert len({s.oblast for s in scenarios}) >= 3
    assert len({s.grade for s in scenarios}) >= 2


def test_unprovable_conditions_never_require_publication():
    for scenario in _scenarios():
        if "unprovable_condition" not in scenario.tags:
            continue
        for step in scenario.steps:
            assert "published" not in step["checks"]
            assert "calls_at_most:2" in step["checks"]
