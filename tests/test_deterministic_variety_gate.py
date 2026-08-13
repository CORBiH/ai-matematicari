"""Kvalitet determinističkih PORODICA → izbor rute (kapija i njen rollback).

Mjerenje nad 352 determinističke lekcije pokazalo je da u 21 porodici učenik na
„daj novi“ i na sva tri nivoa dobija ISTU rečenicu s drugim brojevima. Te
porodice idu modelskoj ruti; jake ostaju na nula poziva.

Odluka je po PORODICI (generatoru) i dolazi iz mjerenja — nikad po ID-ju
lekcije. Ovi testovi čuvaju upravo to.
"""
import json
from pathlib import Path

import pytest

from matbot import deterministic_variety
from matbot.tutor import lesson_context, pipeline as tutor_pipeline

ROOT = Path(__file__).resolve().parents[1]
ROUTING = json.loads((ROOT / "data" / "deterministic_routing.json").read_text(encoding="utf-8"))
QUALITY = json.loads((ROOT / "data" / "deterministic_quality.json").read_text(encoding="utf-8"))
GRADE_OF = {lesson["id"]: int(grade)
            for grade, block in json.loads(
                (ROOT / "data" / "topics.json").read_text(encoding="utf-8"))["grades"].items()
            for lesson in block["lessons"]}


@pytest.fixture(autouse=True)
def _clear_caches():
    deterministic_variety._payload.cache_clear()
    deterministic_variety._migrated_families.cache_clear()
    yield
    deterministic_variety._payload.cache_clear()
    deterministic_variety._migrated_families.cache_clear()


def _sample(route):
    for name, row in sorted(ROUTING["families"].items()):
        if row["route"] == route:
            return name, QUALITY["families"][name]["lessons"][0]
    return None, None


def _routes_deterministically(lesson_id):
    context = lesson_context.build(GRADE_OF[lesson_id], lesson_id)
    return tutor_pipeline._deterministic_generator_for(context) is not None


@pytest.fixture
def release_env(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


# ---------------------------------------------------------------------------
# 1) ROLLBACK: bez zastavice ponašanje je bajt-identično determinističkom
# ---------------------------------------------------------------------------

def test_gate_is_off_by_default_so_behaviour_is_unchanged(monkeypatch, release_env):
    monkeypatch.delenv("MATBOT_DETERMINISTIC_VARIETY_GATE", raising=False)
    family, lesson_id = _sample("MIGRATE_TO_LUNA")
    assert not deterministic_variety.family_routes_to_model(family)
    assert _routes_deterministically(lesson_id)


def test_disabled_is_a_full_rollback(monkeypatch, release_env):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "disabled")
    for family in ROUTING["migrate_to_luna_families"]:
        assert not deterministic_variety.family_routes_to_model(family)


# ---------------------------------------------------------------------------
# 2) UKLJUČENO: mjereno slaba porodica ide modelskoj ruti, jaka ostaje 0-call
# ---------------------------------------------------------------------------

def test_migrated_family_leaves_the_deterministic_route(monkeypatch, release_env):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    family, lesson_id = _sample("MIGRATE_TO_LUNA")
    assert deterministic_variety.family_routes_to_model(family)
    assert not _routes_deterministically(lesson_id)


def test_strong_family_keeps_its_zero_call_generator(monkeypatch, release_env):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    family, lesson_id = _sample("KEEP_DETERMINISTIC")
    assert not deterministic_variety.family_routes_to_model(family)
    assert _routes_deterministically(lesson_id)


@pytest.mark.parametrize("route", ["KEEP_DETERMINISTIC",
                                   "KEEP_DETERMINISTIC_FOR_REPRESENTATION",
                                   "NEEDS_MORE_EVIDENCE"])
def test_only_the_migrate_class_ever_leaves_the_generator(route, monkeypatch, release_env):
    """Nijedna druga klasa se ne seli — ni granična, ni ona zbog prikaza."""
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    for name, row in ROUTING["families"].items():
        if row["route"] == route:
            assert not deterministic_variety.family_routes_to_model(name), name


# ---------------------------------------------------------------------------
# 3) ODLUKA JE PODATAK O PORODICI, NE SPISAK LEKCIJA
# ---------------------------------------------------------------------------

def test_decision_is_family_driven_not_a_lesson_list():
    """Artefakt smije nositi lekcije kao DOKAZ, ali ruta se bira po porodici."""
    assert ROUTING["migrate_to_luna_families"]
    assert all(isinstance(name, str) and "-" not in name
               for name in ROUTING["migrate_to_luna_families"])
    for name, row in ROUTING["families"].items():
        assert row["route"] in {"KEEP_DETERMINISTIC", "MIGRATE_TO_LUNA",
                                "KEEP_DETERMINISTIC_FOR_REPRESENTATION",
                                "NEEDS_MORE_EVIDENCE"}
        assert row["reason"]


def test_no_lesson_id_patch_table_exists_in_the_engine():
    """Nijedan ID lekcije ne smije ući u kod koji bira rutu."""
    import re

    source = (ROOT / "matbot" / "deterministic_variety.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", source)
    selector = (ROOT / "matbot" / "tutor" / "pipeline.py").read_text(encoding="utf-8")
    window = selector[selector.index("def _deterministic_generator_for"):][:1600]
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", window)


def test_missing_artifact_never_changes_a_route(monkeypatch, release_env):
    """Odsustvo mjerenja nije dokaz slabosti."""
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    monkeypatch.setattr(deterministic_variety, "_ARTIFACT", Path("nema.json"))
    deterministic_variety._payload.cache_clear()
    deterministic_variety._migrated_families.cache_clear()
    family, _ = _sample("MIGRATE_TO_LUNA")
    assert not deterministic_variety.family_routes_to_model(family)


def test_every_migrated_family_was_measured_weak():
    """Nijedna porodica se ne seli bez mjerenja koje to opravdava."""
    for name in ROUTING["migrate_to_luna_families"]:
        assert QUALITY["families"][name]["weak"], name
        assert ROUTING["families"][name]["median_distinct_templates"] <= 3, name


def test_measurement_covers_every_family_it_judges():
    assert set(ROUTING["families"]) == set(QUALITY["families"])
    assert QUALITY["deterministic_lessons_measured"] >= 300


# ---------------------------------------------------------------------------
# 4) PRECIZNOST PO LEKCIJI — odluka po porodici je bila pregruba
# ---------------------------------------------------------------------------

def test_individually_strong_lesson_stays_deterministic_inside_a_weak_family(
        monkeypatch, release_env):
    """Nalaz: lekcija s 12 razlicitih recenica otisla je na model samo zato sto
    joj je PORODICA mjerena kao slaba."""
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    exceptions = ROUTING["deterministic_lesson_exceptions"]
    assert exceptions, "ocekivana bar jedna pojedinacno dobra lekcija"
    for lesson_id in exceptions[:5]:
        family = QUALITY["lessons"][lesson_id]["family"]
        assert family in ROUTING["migrate_to_luna_families"], lesson_id
        assert not deterministic_variety.family_routes_to_model(family, lesson_id)
        assert _routes_deterministically(lesson_id), lesson_id


def test_individually_weak_sibling_still_routes_to_the_model(monkeypatch, release_env):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    for lesson_id in ROUTING["migrated_lessons"][:5]:
        family = QUALITY["lessons"][lesson_id]["family"]
        assert deterministic_variety.family_routes_to_model(family, lesson_id)
        assert not _routes_deterministically(lesson_id), lesson_id


def test_every_exception_is_measured_strong_not_handwritten():
    """Izuzeci moraju biti IZVEDENI iz mjerenja, nikad rucno pisani."""
    for lesson_id in ROUTING["deterministic_lesson_exceptions"]:
        row = QUALITY["lessons"][lesson_id]
        assert row["weak"] is False, lesson_id
        assert row["reasons"] == [], lesson_id
        assert row["distinct_templates"] >= 3, lesson_id
    overlap = set(ROUTING["deterministic_lesson_exceptions"]) & set(ROUTING["migrated_lessons"])
    assert not overlap
