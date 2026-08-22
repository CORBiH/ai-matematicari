# -*- coding: utf-8 -*-
"""Kapija izdanja: BEZBJEDNOST je nula-tolerancija, ŽIVOST se uzorkuje.

ZAŠTO POSTOJI: dvije uzastopne kapije pale su na SIGURNO SADRŽANIM stohastičkim
padovima generisanja — Kontrolni liveness i `easier_level1` gdje je Tutor dao
neispravan MCQ, Recenzent ga pogrešno odobrio, a determinističi serverski
provjerivač ga ISPRAVNO odbio. Ništa nije objavljeno, stanje je ostalo netaknuto,
učenik je dobio sigurnu poruku — dakle bezbjednosna arhitektura je radila.

Mjereno (audit): Practice objavi 137/138 turnova (~0,72 % pad zatvoreno). Uz 12
stohastičkih scenarija i JEDAN uzorak po scenariju to je davalo ~11 % lažnih
blokada izdanja. Kontrolni, Explain i Quick su živost već razdvajali; Practice
nije.

Ovi testovi zaključavaju razdvajanje. Nijedna tvrdnja ovdje NE dira produkciju —
mijenja se samo kako kapija ocjenjuje uzorke.
"""
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_gate():
    """Učitaj alat kapije kao modul (nije paket)."""
    if "release_gate_under_test" in sys.modules:
        return sys.modules["release_gate_under_test"]
    spec = importlib.util.spec_from_file_location(
        "release_gate_under_test", ROOT / "tools" / "run_live_release_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["release_gate_under_test"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


# ---------------------------------------------------------------------------
# DVOJNIK REZULTATA TURNA — samo polja koja politika stvarno čita
# ---------------------------------------------------------------------------

@dataclass
class FakeResult:
    published: bool = True
    failure_class: str = None
    session_unchanged_after_rejection: bool = None
    sdk_calls_this_turn: int = 1
    sdk_call_stages: tuple = ("fast_turn",)
    published_task_text: str = ""
    next_state_options: list = field(default_factory=list)
    revealed_correct_option_id: str = None
    failure_is_infrastructure: bool = False
    stop_triggered: str = None


def _safe_rejection(**kw):
    """KLASA B: neispravan nacrt, ispravno odbijen, ništa nije procurilo."""
    base = dict(published=False, failure_class="reviewer_final_mcq_integrity_rejection",
                session_unchanged_after_rejection=True, sdk_calls_this_turn=2,
                sdk_call_stages=("fast_turn", "reviewer_turn"))
    base.update(kw)
    return FakeResult(**base)


# ---------------------------------------------------------------------------
# KLASIFIKACIJA ODBIJENOG TURNA
# ---------------------------------------------------------------------------

def test_contained_rejection_is_not_a_safety_failure():
    """Živi slučaj `easier_level1`: Tutor neispravan, Recenzent ga odobrio,
    serverski provjerivač odbio. Bezbjednost = PASS, uzorak živosti = FAIL."""
    assert gate._rejection_safety_errors(_safe_rejection()) == []


def test_state_mutation_after_rejection_is_a_safety_failure():
    result = _safe_rejection(session_unchanged_after_rejection=False)
    assert "state_mutation_after_rejection" in gate._rejection_safety_errors(result)


def test_call_bound_violation_on_a_rejected_turn_is_a_safety_failure():
    """Ranije se granica poziva na odbijenom turnu uopšte nije provjeravala."""
    result = _safe_rejection(sdk_calls_this_turn=3,
                             sdk_call_stages=("fast_turn", "fast_turn", "reviewer_turn"))
    errors = gate._rejection_safety_errors(result)
    assert any(e.startswith("more_than_two_calls_in_one_turn") for e in errors)
    assert any(e.startswith("repeated_tutor_stage_calls") for e in errors)


@pytest.mark.parametrize("field_name,value,code", (
    ("published_task_text", "Zadatak koji nije smio izaći",
     "rejected_turn_published_task_text"),
    ("next_state_options", [{"id": "a"}], "rejected_turn_published_options"),
    ("revealed_correct_option_id", "b", "rejected_turn_revealed_answer_key"),
))
def test_a_rejected_turn_that_leaked_anything_is_a_safety_failure(field_name, value, code):
    assert code in gate._rejection_safety_errors(_safe_rejection(**{field_name: value}))


# ---------------------------------------------------------------------------
# PODOBNOST ZA UZORKOVANJE
# ---------------------------------------------------------------------------

def test_only_model_backed_scenarios_are_resample_eligible():
    """Kriterij je ZAMRZNUTA cijena poziva, ne spisak imena scenarija."""
    assert gate._is_liveness_eligible(1) is True
    assert gate._is_liveness_eligible(2) is True
    assert gate._is_liveness_eligible(0) is False


def test_deterministic_scenarios_are_never_resampled():
    plan_roles = {"full_solution": 0, "semantic_fresh": 0, "semantic_harder": 0}
    for role, expected in plan_roles.items():
        assert gate._is_liveness_eligible(expected) is False, role


# ---------------------------------------------------------------------------
# SEKVENCIJALNA POLITIKA N=3 / K=2
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakeScenario:
    """Mora biti pravi dataclass — uzorkivač koristi `dataclasses.replace`."""
    session_id: str = "release-core"
    intent: str = ""


@dataclass
class _FakeGate:
    role: str = "easier_level1"
    scenario: _FakeScenario = field(default_factory=_FakeScenario)


class _Harness:
    """Vozi `_run_practice_scenario` nad zadanim nizom ishoda."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.turns = 0
        self.call_count = 0

    def run(self, monkeypatch, expected_calls=1, scoring=None):
        harness = self

        def fake_turn(store, llm, capture, report, scenario, campaign):
            result = harness.outcomes[harness.turns]
            harness.turns += 1
            harness.call_count += result.sdk_calls_this_turn
            llm.call_count = harness.call_count
            return result, None

        monkeypatch.setattr(gate, "_run_one_turn", fake_turn)
        # Ocjenjivac je INJEKTABILAN — inace bi ga harness prebrisao testu.
        monkeypatch.setattr(gate, "_scenario_errors", scoring or (
            lambda g, r, *a, **kw: ([] if r.published
                                    else gate._rejection_safety_errors(r))))

        class _Store:
            def peek(self, _): return None
            def save(self, _): pass

        class _LLM:
            call_count = 0

        return gate._run_practice_scenario(_Store(), _LLM(), None, None,
                                           _FakeGate(), expected_calls, {}, None)


P = FakeResult(published=True)


def test_case1_first_attempt_publishes_uses_one_attempt(monkeypatch):
    h = _Harness([P, P, P])
    attempts, safety, liveness, spent, _stop, _res = h.run(monkeypatch)
    assert h.turns == 1, "zdrav scenario ne smije platiti dodatni poziv"
    assert (safety, liveness) == ([], [])
    assert attempts[0]["classification"] == "safe_publication"


def test_case2_reject_publish_publish_passes(monkeypatch):
    h = _Harness([_safe_rejection(), P, P])
    attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch)
    assert h.turns == 3
    assert (safety, liveness) == ([], [])
    assert [a["classification"] for a in attempts] == [
        "safe_rejection", "safe_publication", "safe_publication"]


def test_case3_publish_reject_publish_passes(monkeypatch):
    h = _Harness([P, _safe_rejection(), P])
    # Prva objava odmah zaustavlja — to je i namjera (nema suvišnih poziva).
    attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch)
    assert h.turns == 1
    assert (safety, liveness) == ([], [])


def test_case4_two_rejections_fail_early_because_two_of_three_is_impossible(monkeypatch):
    h = _Harness([_safe_rejection(), _safe_rejection(), P])
    attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch)
    assert h.turns == 2, "treći uzorak je besmislen — K=2 je već nedostižno"
    assert safety == []
    assert liveness and liveness[0].startswith("practice_liveness_below_threshold")


def test_case5_unsafe_publication_on_first_sample_fails_immediately(monkeypatch):
    h = _Harness([P, P, P])
    attempts, safety, liveness, _spent, _stop, _res = h.run(
        monkeypatch, scoring=lambda g, r, *a, **kw: ["published_wrong_marked_answer"])
    assert h.turns == 1, "bezbjednosni prekršaj se NIKAD ne uzorkuje"
    assert safety == ["published_wrong_marked_answer"]
    assert liveness == []
    assert attempts[0]["classification"] == "safety_failure"


def test_case6_unsafe_publication_after_a_prior_safe_rejection_fails(monkeypatch):
    outcomes = [_safe_rejection(), FakeResult(published=True)]
    h = _Harness(outcomes)
    calls = {"n": 0}

    def scoring(g, r, *a, **kw):
        if not r.published:
            return gate._rejection_safety_errors(r)
        calls["n"] += 1
        return ["published_wrong_marked_answer"]

    _attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch, scoring=scoring)
    assert safety == ["published_wrong_marked_answer"]
    assert liveness == [], "bezbjednost se ne smije pretvoriti u glasanje"


def test_case7_state_mutation_after_rejection_fails_immediately(monkeypatch):
    h = _Harness([_safe_rejection(session_unchanged_after_rejection=False), P, P])
    _attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch)
    assert h.turns == 1
    assert "state_mutation_after_rejection" in safety
    assert liveness == []


def test_case8_call_bound_violation_fails_immediately(monkeypatch):
    h = _Harness([_safe_rejection(sdk_calls_this_turn=3,
                                  sdk_call_stages=("fast_turn", "fast_turn", "reviewer_turn")),
                  P, P])
    _attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch)
    assert h.turns == 1
    assert any(e.startswith("more_than_two_calls_in_one_turn") for e in safety)


def test_case9_reviewer_missed_it_but_server_rejected_is_liveness_not_safety(monkeypatch):
    """Tačno živi slučaj: Recenzent decision=correct, server odbio."""
    h = _Harness([_safe_rejection(), _safe_rejection(), P])
    attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch)
    assert safety == [], "sadržan pad NIJE bezbjednosni prekršaj"
    assert liveness, "ali jeste neuspjeh živosti"
    assert all(a["state_preserved"] for a in attempts if a["failed_closed"])


def test_case10_an_invalid_mcq_that_actually_published_fails_immediately(monkeypatch):
    h = _Harness([FakeResult(published=True)])
    _attempts, safety, liveness, _spent, _stop, _res = h.run(
        monkeypatch, scoring=lambda g, r, *a, **kw: ["marked_option_missing"])
    assert safety == ["marked_option_missing"]
    assert h.turns == 1


def test_case11_deterministic_scenario_is_not_resampled(monkeypatch):
    h = _Harness([_safe_rejection(sdk_calls_this_turn=0, sdk_call_stages=()), P, P])
    attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch, expected_calls=0)
    assert h.turns == 1, "server-vlasnički scenario nema šta da uzorkuje"
    assert safety == []
    assert liveness and liveness[0].startswith("practice_liveness_below_threshold")


def test_case12_healthy_run_spends_no_extra_samples(monkeypatch):
    for _ in range(5):
        h = _Harness([P, P, P])
        h.run(monkeypatch)
        assert h.turns == 1


# ---------------------------------------------------------------------------
# PLAFON I ARTEFAKT
# ---------------------------------------------------------------------------

def test_case13_ceiling_is_derived_from_the_plan_not_hardcoded():
    assert gate.PRACTICE_LIVENESS_ATTEMPTS == 3
    assert gate.PRACTICE_LIVENESS_REQUIRED == 2
    assert gate.SDK_CALL_CEILING == (gate._PRACTICE_WORST_CASE
                                     + gate.KONTROLNI_MAX_CALLS
                                     + gate.EXPLAIN_MAX_CALLS
                                     + gate.QUICK_MAX_CALLS)
    assert gate._PRACTICE_WORST_CASE == 23 * gate.PRACTICE_LIVENESS_ATTEMPTS


def test_case14_artifact_retains_every_attempt(monkeypatch):
    h = _Harness([_safe_rejection(), P, P])
    attempts, _safety, _liveness, _spent, _stop, _res = h.run(monkeypatch)
    assert len(attempts) == 3, "odbijeni uzorci se NE skrivaju kad scenario prođe"
    for record in attempts:
        for key in ("attempt", "published", "failed_closed", "failure_class",
                    "sdk_calls", "state_preserved", "classification",
                    "safety_errors", "sdk_call_stages"):
            assert key in record, key
    assert attempts[0]["failure_class"] == "reviewer_final_mcq_integrity_rejection"


# ---------------------------------------------------------------------------
# OSTALE FAZE OSTAJU NETAKNUTE
# ---------------------------------------------------------------------------

def test_other_stages_keep_their_existing_liveness_semantics():
    source = (ROOT / "tools" / "run_live_release_gate.py").read_text(encoding="utf-8")
    for marker in ("kontrolni_never_published", "explain_never_published",
                   "quick_never_published"):
        assert marker in source, marker


def test_infrastructure_failure_is_never_treated_as_a_liveness_sample(monkeypatch):
    h = _Harness([_safe_rejection(failure_is_infrastructure=True), P, P])
    attempts, safety, liveness, _spent, _stop, _res = h.run(monkeypatch)
    assert h.turns == 1
    assert liveness == ["infrastructure_failure"]
    assert attempts[0]["classification"] == "infrastructure"
