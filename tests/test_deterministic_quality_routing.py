"""Produkcijsko rutiranje nakon selidbe mjereno slabih determinističkih porodica.

Dokazuje CIJELU tabelu ruta odjednom, jer se greška ovdje ne vidi u jednoj
lekciji nego u tome što je neka klasa otišla na pogrešnu stranu.
"""
import json
from pathlib import Path

import pytest

from matbot import practice
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM

ROOT = Path(__file__).resolve().parents[1]
ROUTING = json.loads((ROOT / "data" / "deterministic_routing.json").read_text(encoding="utf-8"))
QUALITY = json.loads((ROOT / "data" / "deterministic_quality.json").read_text(encoding="utf-8"))
GRADE_OF = {lesson["id"]: int(grade)
            for grade, block in json.loads(
                (ROOT / "data" / "topics.json").read_text(encoding="utf-8"))["grades"].items()
            for lesson in block["lessons"]}


class StageRecorder(FakeLLM):
    """Biljezi FAZU prvog poziva i odmah prekida turn — mjeri se RUTA."""

    def __init__(self):
        super().__init__()
        self.stages = []

    def _record(self, name):
        self.stages.append(name)
        assert len(self.stages) <= 2, self.stages          # plafon iz CLAUDE.md
        raise RuntimeError("stop-after-route")

    def fast_turn(self, instructions, input_text, timeout_s=None):
        return self._record("fast_turn")

    def practice_turn(self, instructions, input_text):
        return self._record("practice_turn")

    def tutor_turn(self, instructions, input_text):
        return self._record("tutor_turn")


@pytest.fixture
def production(monkeypatch):
    """Tačna produkcijska konfiguracija poslije selidbe."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_SCOPE", "model_backed")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    from matbot import deterministic_variety
    deterministic_variety._payload.cache_clear()
    deterministic_variety._migrated_families.cache_clear()
    yield
    deterministic_variety._payload.cache_clear()
    deterministic_variety._migrated_families.cache_clear()


def _stages_for(lesson_id):
    recorder = StageRecorder()
    try:
        practice.run_practice_turn(SessionStore(), recorder, {
            "session_id": f"route-{lesson_id}", "grade": GRADE_OF[lesson_id],
            "selected_topic": lesson_id, "selected_oblast": "",
            "student_message": "Daj mi zadatak.", "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": ""})
    except RuntimeError:
        pass
    return recorder.stages


def _first_lesson(route):
    for name, row in sorted(ROUTING["families"].items()):
        if row["route"] == route:
            return QUALITY["families"][name]["lessons"][0]
    return None


def test_strong_deterministic_family_stays_zero_call(production):
    assert _stages_for(_first_lesson("KEEP_DETERMINISTIC")) == []


def test_migrated_weak_family_routes_to_fast_turn(production):
    assert _stages_for(_first_lesson("MIGRATE_TO_LUNA")) == ["fast_turn"]


def test_borderline_family_is_left_alone(production):
    assert _stages_for(_first_lesson("NEEDS_MORE_EVIDENCE")) == []


def test_general_model_backed_lesson_is_still_fast(production):
    assert _stages_for("6-04-001") == ["fast_turn"]


def test_former_contract_lesson_is_still_fast(production):
    assert _stages_for("6-04-005") == ["fast_turn"]


def test_strong_class_families_route_the_same_way_for_every_lesson(production):
    """Jaka porodica nema izuzetaka: svaka njena lekcija ostaje na nula poziva."""
    checked = 0
    for name, row in sorted(ROUTING["families"].items()):
        if row["route"] != "KEEP_DETERMINISTIC":
            continue
        for lesson_id in QUALITY["families"][name]["lessons"][:2]:
            assert _stages_for(lesson_id) == [], (name, lesson_id)
            checked += 1
    assert checked > 0


def test_migrated_family_routes_by_MEASURED_LESSON_not_by_family_alone(production):
    """ODLUKA PO PORODICI JE BILA PREGRUBA.

    Unutar slabe porodice ima pojedinacno dobrih lekcija (npr. 12 razlicitih
    recenica i uredan raspored po nivoima). One ostaju na nula poziva; samo
    mjereno slabe lekcije idu na model."""
    migrated = ROUTING["migrated_lessons"]
    exceptions = ROUTING["deterministic_lesson_exceptions"]
    assert migrated and exceptions
    for lesson_id in migrated[:4]:
        assert _stages_for(lesson_id) == ["fast_turn"], lesson_id
    for lesson_id in exceptions[:4]:
        assert _stages_for(lesson_id) == [], lesson_id


def test_no_route_ever_exceeds_two_calls(production):
    """Selidba ne dira plafon: prvi poziv je tutorski, drugi samo recenzentski."""
    for route in ("MIGRATE_TO_LUNA", "KEEP_DETERMINISTIC"):
        stages = _stages_for(_first_lesson(route))
        assert len(stages) <= 2
        assert "practice_turn" not in stages     # stari ugovorni poziv nikad


def test_rollback_restores_zero_call_for_migrated_families(monkeypatch, production):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "disabled")
    from matbot import deterministic_variety
    deterministic_variety._payload.cache_clear()
    deterministic_variety._migrated_families.cache_clear()
    assert _stages_for(_first_lesson("MIGRATE_TO_LUNA")) == []
