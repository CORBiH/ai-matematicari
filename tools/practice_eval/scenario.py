"""Učitavanje i strukturna validacija scenarija (JSONL).

JSONL je JEDINI izvor istine za scenarije. Runner nikad ne izmišlja korak koji
u fajlu ne postoji, i nikad ne mijenja očekivanje zato što je implementacija
pala.

Jedan red = jedan scenario:

    {
      "id": "A01", "wave": "A", "importance": "critical",
      "grade": 6, "oblast": "…", "topic_id": "6-03-004",
      "reason": "zašto ovaj scenario postoji",
      "tags": ["session_start"],
      "steps": [
        {"kind": "text", "message": "Daj mi zadatak.", "expect_calls": 2,
         "checks": ["published", "task_published"], "rubrics": ["clarity"]},
        {"kind": "choice", "select": "correct", "expect_calls": 1,
         "checks": ["verdict_correct"]}
      ]
    }

`kind` ∈ {text, choice, repeat_choice}. `select` ∈ {correct, wrong,
second_wrong, a|b|c|d}. `collect_help: true` znači da tekst ovog turna ulazi u
historiju hintova (za provjeru `hint_differs`).

`requires_active_task: true` je PREDUSLOV, ne očekivanje.

ZAŠTO POSTOJI (Talas A, scenariji A10/A31/A35): kad prvi korak ne uspije
objaviti zadatak, follow-up koraci (klik, hint, „uradi ga ti“, ocjena) više
nemaju šta da testiraju — a ipak su se izvršavali i proizvodili FAIL-ove koji
NISU nezavisni kvarovi, nego posljedica jednog ranijeg pada. Takav korak se
sada preskače: nula poziva, nijedna provjera se ne izvršava, a scenario ne može
završiti kao PASS jer je nešto ostalo nedokazano.

To NIJE ublažavanje očekivanja: `calls_at_most:1` i ostale provjere ostaju
identične za svaki korak koji se stvarno izvrši.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

IMPORTANCE = ("critical", "high", "medium", "exploratory")
STEP_KINDS = ("text", "choice", "repeat_choice")
SELECTS = ("correct", "wrong", "second_wrong", "a", "b", "c", "d")


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Scenario:
    id: str
    wave: str
    importance: str
    grade: int
    oblast: str
    topic_id: str
    reason: str
    steps: tuple
    tags: tuple = ()
    # Veza scenarija s konkretnim nalazom ranijeg talasa (ID scenarija ili
    # root-cause kategorija). Obavezna za Talas B: scenario koji ne cilja
    # nijedan dokazan nalaz je nasumican uzorak, ne dijagnostika.
    targets_wave_a_findings: tuple = ()

    @property
    def session_id(self):
        """Izolovan session po scenariju — nijedan scenario ne nasljeđuje tuđe stanje."""
        return f"eval-{self.wave.lower()}-{self.id.lower()}"

    @property
    def max_model_calls(self):
        return sum(step.get("expect_calls", 0) for step in self.steps)


def _require(condition, message):
    if not condition:
        raise ScenarioError(message)


def _parse_scenario(raw, source, line_number) -> Scenario:
    where = f"{source}:{line_number}"
    _require(isinstance(raw, dict), f"{where}: not a JSON object")
    for key in ("id", "wave", "importance", "grade", "oblast", "topic_id", "reason", "steps"):
        _require(key in raw, f"{where}: missing field {key!r}")
    _require(raw["wave"] in ("A", "B"), f"{where}: wave must be A or B")
    _require(raw["importance"] in IMPORTANCE, f"{where}: unknown importance {raw['importance']!r}")
    _require(isinstance(raw["grade"], int) and raw["grade"] in (6, 7, 8, 9),
             f"{where}: grade must be 6..9")
    _require(isinstance(raw["reason"], str) and raw["reason"].strip(),
             f"{where}: every scenario must state why it exists")
    _require(isinstance(raw["steps"], list) and raw["steps"], f"{where}: steps must be a non-empty list")

    steps = []
    for index, step in enumerate(raw["steps"]):
        step_where = f"{where} step{index}"
        _require(isinstance(step, dict), f"{step_where}: step must be an object")
        kind = step.get("kind")
        _require(kind in STEP_KINDS, f"{step_where}: unknown kind {kind!r}")
        _require(isinstance(step.get("checks"), list) and step["checks"],
                 f"{step_where}: every step needs at least one deterministic expectation")
        _require(isinstance(step.get("expect_calls"), int) and step["expect_calls"] >= 0,
                 f"{step_where}: expect_calls must be a non-negative integer")
        if kind in ("choice", "repeat_choice"):
            _require(step.get("select", "correct") in SELECTS,
                     f"{step_where}: unknown select {step.get('select')!r}")
        _require(isinstance(step.get("requires_active_task", False), bool),
                 f"{step_where}: requires_active_task must be a boolean")
        # Korak SMIJE promijeniti lekciju unutar iste sesije — to je jedini
        # nacin da se testira serverska invalidacija napretka pri promjeni teme
        # (`SessionStore.load` poredi curriculum_fingerprint). Postojanje te
        # lekcije provjerava `--dry-run`.
        step_topic = step.get("topic_id")
        _require(step_topic is None or (isinstance(step_topic, str) and step_topic.strip()),
                 f"{step_where}: topic_id override must be a non-empty string")
        normalized = dict(step)
        normalized.setdefault("requires_active_task", False)
        normalized.setdefault("rubrics", [])
        normalized["checks"] = list(step["checks"])
        normalized["rubrics"] = list(normalized["rubrics"])
        steps.append(normalized)

    return Scenario(
        id=str(raw["id"]), wave=raw["wave"], importance=raw["importance"],
        grade=raw["grade"], oblast=str(raw["oblast"]), topic_id=str(raw["topic_id"]),
        reason=str(raw["reason"]), steps=tuple(steps), tags=tuple(raw.get("tags", ())),
        targets_wave_a_findings=tuple(raw.get("targets_wave_a_findings", ())),
    )


def load_scenarios(path: Path) -> list:
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    if not files:
        raise ScenarioError(f"no scenario files found under {path}")
    scenarios = []
    for file_path in files:
        if not file_path.exists():
            raise ScenarioError(f"scenario file not found: {file_path}")
        for line_number, line in enumerate(
                file_path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                raw = json.loads(line)
            except ValueError as error:
                raise ScenarioError(f"{file_path.name}:{line_number}: invalid JSON — {error}")
            scenarios.append(_parse_scenario(raw, file_path.name, line_number))
    return scenarios


def validate_scenarios(scenarios) -> list:
    """Provjere koje gledaju SKUP scenarija, ne pojedinačni red."""
    problems = []
    seen = {}
    for scenario in scenarios:
        if scenario.id in seen:
            problems.append(f"duplicate scenario id {scenario.id!r}")
        seen[scenario.id] = scenario
        if not any(step.get("expect_calls", 0) for step in scenario.steps):
            problems.append(f"{scenario.id}: no step ever reaches the model")
        if scenario.wave == "B" and not scenario.targets_wave_a_findings:
            problems.append(f"{scenario.id}: wave B scenario without targets_wave_a_findings")
    sessions = [scenario.session_id for scenario in scenarios]
    if len(set(sessions)) != len(sessions):
        problems.append("two scenarios share a session id — isolation would be broken")
    return problems
