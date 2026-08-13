"""Mjerena raznolikost determinističkih porodica — kapija i njen rollback.

Statička revizija 352 determinističke lekcije pokazala je da 49 njih na tri
nivoa težine daje istu rečenicu s drugim brojevima. Mjerenje je kompajlirano u
podatak; kapija je NAMJERNO isključena, jer bi uključivanje oduzelo tim
lekcijama garanciju nula poziva.
"""
import json
from pathlib import Path

import pytest

from matbot import deterministic_variety
from matbot.tutor import lesson_context, pipeline as tutor_pipeline

ARTIFACT = Path(__file__).resolve().parents[1] / "data" / "deterministic_variety.json"


@pytest.fixture(autouse=True)
def _clear_caches():
    deterministic_variety._payload.cache_clear()
    deterministic_variety._weak.cache_clear()
    yield
    deterministic_variety._payload.cache_clear()
    deterministic_variety._weak.cache_clear()


def _weak_lesson():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))["weak_variety_lessons"][0]


def test_gate_is_off_by_default_so_behaviour_is_unchanged(monkeypatch):
    monkeypatch.delenv("MATBOT_DETERMINISTIC_VARIETY_GATE", raising=False)
    assert not deterministic_variety.is_weak(_weak_lesson())


def test_measured_weak_lesson_leaves_the_deterministic_route_when_enabled(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    lesson_id = _weak_lesson()
    assert deterministic_variety.is_weak(lesson_id)
    grade = int(lesson_id.split("-")[0])
    context = lesson_context.build(grade, lesson_id)
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    assert tutor_pipeline._deterministic_generator_for(context) is None


def test_strong_lesson_keeps_its_zero_call_generator(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    weak = set(payload["weak_variety_lessons"])
    strong = next(k for k, v in payload["measurements"].items()
                  if k not in weak and v["variety_ratio"] >= 0.9)
    assert not deterministic_variety.is_weak(strong)


def test_missing_artifact_never_changes_a_route(monkeypatch):
    """Odsustvo mjerenja nije dokaz slabosti."""
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    monkeypatch.setattr(deterministic_variety, "_ARTIFACT", Path("nema.json"))
    deterministic_variety._payload.cache_clear()
    deterministic_variety._weak.cache_clear()
    assert not deterministic_variety.is_weak(_weak_lesson())


def test_measurement_covers_every_deterministic_lesson_it_judges():
    payload = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert set(payload["weak_variety_lessons"]) <= set(payload["measurements"])
    assert len(payload["measurements"]) >= 300
