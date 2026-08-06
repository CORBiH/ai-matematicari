"""Ciljani talas F4E mora biti strukturno ispravan PRIJE ijednog plaćenog poziva.

Talas cilja dva P0 nalaza iz produkcije: objavljen MCQ bez ijednog tačnog
odgovora i „Uradi ga ti“ koji je zamijenio aktivan zadatak. Pokreće se
eksplicitnim `--scenarios` putem (fajlovi u `scenarios/family/` namjerno nisu u
podrazumijevanom direktoriju, da se ne izvršavaju uz Talas A/B).

Ovdje se ne pravi nijedan SDK poziv — provjerava se samo da je definicija
talasa ispravna i da mjeri ono zbog čega je napisan.
"""
from pathlib import Path

from tools.practice_eval.checks import known_check_names
from tools.practice_eval.scenario import load_scenarios, validate_scenarios

FAMILY_DIR = Path(__file__).resolve().parent.parent / "tools" / "practice_eval" / "scenarios" / "family"
WAVE_F4E = FAMILY_DIR / "wave_f4e.jsonl"


def _scenarios():
    return load_scenarios(WAVE_F4E)


def test_wave_loads_and_is_internally_consistent():
    scenarios = _scenarios()
    assert len(scenarios) == 18
    assert validate_scenarios(scenarios) == []
    assert len({scenario.session_id for scenario in scenarios}) == len(scenarios)
    assert all(scenario.reason.strip() for scenario in scenarios)


def test_every_check_name_is_one_the_runner_knows():
    known = set(known_check_names())
    for scenario in _scenarios():
        for step in scenario.steps:
            for check in step["checks"]:
                normalized = check.split(":")[0] + ":N" if ":" in check else check
                assert normalized in known, f"{scenario.id}: nepoznata provjera {check!r}"


def test_call_budget_stays_inside_the_authorised_range():
    total = sum(scenario.max_model_calls for scenario in _scenarios())
    assert total == 62, total


def test_solution_steps_send_the_explicit_production_payload():
    """Srž P0-B: klik „Uradi ga ti“ se mjeri payloadom, ne tekstom dugmeta."""
    steps = [step for scenario in _scenarios() for step in scenario.steps
             if step.get("intent") == "solution_request"]
    assert len(steps) >= 6
    for step in steps:
        assert step["interaction_phase"] == "practice_help"
        assert step["send_last_task"] is True
        assert step["requires_active_task"] is True
        # Rješenje POSTOJEĆEG zadatka — nikad nov paket.
        assert "no_new_task" in step["checks"]
        assert "task_preserved" in step["checks"]
        assert "solution_complete" in step["checks"]
        assert "task_published" not in step["checks"]


def test_divisibility_scenarios_verify_the_published_package():
    """Srž P0-A: `package_clean` pokreće isti orakl koji je pao u produkciji."""
    pair_scenarios = [scenario for scenario in _scenarios()
                      if "divisibility_pair" in scenario.tags]
    assert len(pair_scenarios) == 6
    for scenario in pair_scenarios:
        for step in scenario.steps:
            assert "package_clean" in step["checks"]
            assert "options_ok" in step["checks"]


def test_the_production_sequence_is_repeated_three_times():
    replicas = [scenario for scenario in _scenarios()
                if "production_replica" in scenario.tags]
    assert len(replicas) == 3
    for scenario in replicas:
        kinds = [(step["kind"], step.get("intent", "")) for step in scenario.steps]
        assert kinds == [("text", ""), ("text", "hint_request"),
                         ("text", "solution_request")]


def test_unprovable_conditions_never_require_publication():
    """Negacija/disjunkcija: i objava i sigurno odbijanje su ispravni ishodi."""
    for scenario in _scenarios():
        if "unprovable_condition" not in scenario.tags:
            continue
        for step in scenario.steps:
            assert "published" not in step["checks"]
            assert "calls_at_most:2" in step["checks"]
