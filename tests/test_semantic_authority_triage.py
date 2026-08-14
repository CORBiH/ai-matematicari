r"""Trijaža UNKNOWN semantičkih pravila: šta je ODLUČENO i zašto tako ostaje.

Ova faza NIJE dodala nijedan novi blokirajući detektor, i to je nalaz, ne
propust. Najjači kandidat — generički primitiv KLASE ODGOVORA (prepoznavanje
naspram izračunatog rezultata), izveden istom metodom kojom je izveden prihvaćen
primitiv dimenzije — mjeren je i odbijen:

    determinstički korpus : 21.120 paketa, 0 lažnih blokada
    ŽIVI korpus (model)   :  3.287 paketa, 48 lažnih blokada (1,46 %)

Sve 48 su ručno pregledane i sve su lažne. Prag za novi bloker je NULA.

POUKA: izvođenje pravila iz determinističkog generatora radi za MJERNU JEDINICU
(dimenzija tražene veličine je fizičko svojstvo pitanja), ali ne i za KLASU
ODGOVORA (oblik odgovora bira autor, a model legitimno bira drukčije nego
generator). Determinstički korpus zato nije valjan zamjenik za model-autorski
kad se dokazuje svojstvo koje autor bira.

Testovi ispod čuvaju tu odluku i štite već dokazan detektor dimenzije.
"""
import json
import re
from pathlib import Path

import pytest

from matbot.semantics import contracts as semantic_contracts
from matbot.semantics import detectors

ROOT = Path(__file__).resolve().parent.parent
COMPILED = json.loads((ROOT / "data" / "lesson_semantics.compiled.json")
                      .read_text(encoding="utf-8"))["lessons"]
AUTHORITY = json.loads((ROOT / "data" / "semantic_authority_status.json")
                       .read_text(encoding="utf-8"))
ANSWER_CLASSES = json.loads((ROOT / "data" / "semantic_answer_classes.json")
                            .read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. ODBIJEN PRIMITIV NE SMIJE BITI ZAKAČEN
# ---------------------------------------------------------------------------

def test_the_answer_class_primitive_is_not_wired_as_a_blocker():
    """Mjereno 1,46 % lažnih blokada na živom korpusu — prag je nula."""
    assert detectors._detect_answer_class not in detectors.DETECTORS.values()
    for handler in detectors.DETECTORS.values():
        assert handler is not detectors._detect_answer_class


def test_a_family_without_its_own_detector_still_returns_unknown():
    """Rezervni put NE smije voditi na odbijeni primitiv."""
    unimplemented = [lid for lid, entry in COMPILED.items()
                     if entry["detector"] not in detectors.DETECTORS]
    assert unimplemented
    for lesson_id in unimplemented[:25]:
        contract = semantic_contracts.contract_for(lesson_id)
        result = detectors.detect(contract, "bilo koji zadatak",
                                  answer_text="$5$ cm")
        assert result.status == detectors.STATUS_UNSUPPORTED
        assert "nije implementiran" in result.reason
        assert not result.blocking


def test_the_rejected_primitive_still_behaves_correctly_as_a_function():
    """Kod ostaje kao dokaz mjerenja; njegova tri ishoda moraju biti ispravna
    da bi se provjera mogla jeftino ponoviti kad se sakupi živi korpus."""
    prose_token = next(k for k, v in ANSWER_CLASSES["class_by_token"].items()
                       if v == "prose")
    value_token = next(k for k, v in ANSWER_CLASSES["class_by_token"].items()
                       if v == "value")

    def contract_for(token):
        return type("C", (), {"parameters": {"kinds": (token,)},
                              "detector": "x", "family_id": "x"})()

    # PASS
    assert detectors._detect_answer_class(
        contract_for(value_token), "", "$12$").status == detectors.STATUS_PASS
    # FAIL
    assert detectors._detect_answer_class(
        contract_for(value_token), "",
        "oštar ugao").status == detectors.STATUS_FAIL
    # UNKNOWN — nema odgovora
    assert detectors._detect_answer_class(
        contract_for(value_token), "", "").status == detectors.STATUS_UNSUPPORTED
    # UNKNOWN — nedokazan pojam
    assert detectors._detect_answer_class(
        contract_for("nepostojeci_pojam"), "",
        "$12$").status == detectors.STATUS_UNSUPPORTED
    # UNKNOWN — lekcija traži i jedno i drugo
    both = type("C", (), {"parameters": {"kinds": (value_token, prose_token)},
                          "detector": "x", "family_id": "x"})()
    assert detectors._detect_answer_class(
        both, "", "$12$").status == detectors.STATUS_UNSUPPORTED


def test_the_derived_answer_class_map_rejected_the_ambiguous_tokens():
    """Pojam bez jednoglasne klase se NE upisuje — to je dio metode."""
    assert ANSWER_CLASSES["rejected_tokens"], "očekivani su odbačeni pojmovi"
    for token in ANSWER_CLASSES["rejected_tokens"]:
        assert token not in ANSWER_CLASSES["class_by_token"]


# ---------------------------------------------------------------------------
# 2. DOKAZAN DETEKTOR DIMENZIJE OSTAJE NETAKNUT (regresija)
# ---------------------------------------------------------------------------

def test_the_measure_dimension_detector_is_unchanged():
    assert detectors.DETECTORS["geometry_formula_2d"] is detectors._detect_measure_dimension
    assert detectors.DETECTORS["solid_geometry_direct"] is detectors._detect_measure_dimension
    # Skup detektora koji dokaz čitaju iz OZNAČENOG odgovora smije rasti kad se
    # doda nov takav detektor; ove dvije porodice moraju ostati u njemu.
    assert {"geometry_formula_2d", "solid_geometry_direct"} <= \
        detectors._ANSWER_EVIDENCE_DETECTORS


def test_the_measure_dimension_detector_still_proves_its_three_outcomes():
    dimensions = json.loads((ROOT / "data" / "semantic_measure_dimensions.json")
                            .read_text(encoding="utf-8"))
    by_kind = dimensions["dimension_by_kind"]
    unitless = set(dimensions["unitless_kinds"])
    area_lesson = None
    for lesson_id, entry in COMPILED.items():
        if entry["detector"] not in ("geometry_formula_2d", "solid_geometry_direct"):
            continue
        contract = semantic_contracts.contract_for(lesson_id)
        kinds = tuple(contract.parameters.get("kinds") or ())
        if kinds and not any(k in unitless for k in kinds) \
                and {by_kind.get(k) for k in kinds} == {2}:
            area_lesson = contract
            break
    assert area_lesson is not None
    assert detectors.detect(area_lesson, "", answer_text="$24$ cm²").status \
        == detectors.STATUS_PASS
    assert detectors.detect(area_lesson, "", answer_text="$24$ cm").status \
        == detectors.STATUS_FAIL
    assert detectors.detect(area_lesson, "", answer_text="$24$").status \
        == detectors.STATUS_UNSUPPORTED


def test_the_unit_reader_is_unchanged():
    assert detectors.unit_exponents("$12$ cm") == {1}
    assert detectors.unit_exponents("$12$ cm²") == {2}
    assert detectors.unit_exponents("$12$ cm$^2$") == {2}
    assert detectors.unit_exponents("$12$ cm³") == {3}
    assert detectors.unit_exponents("$2^3 = 8$") == set()


# ---------------------------------------------------------------------------
# 3. ARTEFAKT STVARNOG AUTORITETA NE SMIJE OBMANUTI
# ---------------------------------------------------------------------------

def test_every_contract_is_labelled_blocking_but_authority_is_reported_separately():
    """Podaci ugovora traže blokadu za SVE; artefakt kaže šta server MOŽE."""
    assert all(entry["enforcement_mode"] == "blocking" for entry in COMPILED.values())
    assert set(AUTHORITY["lessons"]) == set(COMPILED)
    for lesson_id, row in AUTHORITY["lessons"].items():
        assert row["requested_enforcement"] == "blocking"
        assert row["detector_status"] in ("IMPLEMENTED", "REDUNDANT", "UNKNOWN")
        assert row["server_can_refuse_publication"] == (
            row["detector_status"] == "IMPLEMENTED")


def test_the_authority_artifact_matches_the_live_registry():
    """Artefakt se ne smije razići s kodom — inače opet obmanjuje."""
    for lesson_id, row in AUTHORITY["lessons"].items():
        implemented = COMPILED[lesson_id]["detector"] in detectors.DETECTORS
        if row["detector_status"] == "IMPLEMENTED":
            assert implemented, lesson_id
            assert row["production_route"] == "model", lesson_id
        if row["detector_status"] == "UNKNOWN":
            assert not implemented, lesson_id


def test_unknown_lessons_are_never_counted_as_protection():
    unknown = [l for l, r in AUTHORITY["lessons"].items()
               if r["detector_status"] == "UNKNOWN"]
    assert unknown
    for lesson_id in unknown:
        assert AUTHORITY["lessons"][lesson_id]["server_can_refuse_publication"] is False


# ---------------------------------------------------------------------------
# 4. NEMA GRANANJA PO LEKCIJI; MAPE SU PODACI
# ---------------------------------------------------------------------------

def test_no_lesson_identity_in_the_detector_module():
    source = (ROOT / "matbot" / "semantics" / "detectors.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", source)
    assert not re.search(r"lesson_id\s*==", source)


def test_the_maps_are_data_files_not_python_literals():
    for name in ("semantic_measure_dimensions.json", "semantic_answer_classes.json",
                 "semantic_authority_status.json"):
        assert (ROOT / "data" / name).is_file(), name


# ---------------------------------------------------------------------------
# 5. SUSJEDNE GARANCIJE NEPROMIJENJENE
# ---------------------------------------------------------------------------

def test_zero_call_routes_stay_zero_call(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_VARIETY_GATE", "enabled")
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore

    class NoModel:
        def __getattr__(self, name):
            def explode(*args, **kwargs):
                raise AssertionError(f"model pozvan na 0-pozivnoj ruti: {name}")
            return explode

    response = run_practice_turn(SessionStore(), NoModel(), {
        "session_id": "triage-det", "grade": 6, "selected_topic": "6-01-001",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": ""})
    assert response.get("status") == "ready"


def test_single_hint_diversity_and_config_are_untouched():
    from matbot import archetype_support, config, form_variants, release_config
    assert config.practice_single_hint_enabled() is True
    assert archetype_support._enabled() is True
    assert form_variants._enabled() is True
    assert release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"] == \
        "universal_two_call"


def test_the_detector_never_calls_a_model():
    contract = semantic_contracts.contract_for(next(iter(COMPILED)))
    for answer in ("$24$ cm²", "oštar ugao", ""):
        result = detectors.detect(contract, "zadatak", answer_text=answer)
        assert result.status in (detectors.STATUS_PASS, detectors.STATUS_FAIL,
                                 detectors.STATUS_UNSUPPORTED)
