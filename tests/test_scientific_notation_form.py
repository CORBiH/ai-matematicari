r"""Kanonski naučni zapis: dokazan čitač, ali NAMJERNO neuključen bloker.

Lekcija 8-01-017 „Naučni zapis broja“ (8. razred, model-ruta) je bila jedini
preostali UNKNOWN kandidat u klasi EXACT_PARSED_MATH: oblik $a \cdot 10^{n}$ s
$1 \le |a| < 10$ i cijelim $n$ je objektivno provjerljiv. Definicija nije
pretpostavljena — doslovno stoji u nagovještaju determinističkog generatora te
iste lekcije.

MJERENO:
    16/16 zapisnih varijanti tačno
    180 determinističkih paketa — 156 PASS, 24 UNSUPPORTED, 0 FAIL
    12 živih Luna turnova u sjenci — 2 PASS, 10 UNSUPPORTED, 0 FAIL
    315/315 mutacija uhvaćeno (100 %)

I PORED TOGA NIJE UKLJUČEN. Sjenka je pokazala objavljen i prihvaćen paket s
označenim odgovorom $2,4\cdot 10^3 = 0,24\cdot 10^4$, gdje je `0,24·10^4`
namjerno NEKANONSKI a tvrdnja tačna. Prošao je kao UNSUPPORTED samo zato što
nosi dva stepena broja 10; varijanta s jednim nekanonskim ali tačnim odgovorom
je jednako realna za ovu lekciju i bila bi lažno odbijena. Prag je nula lažnih
odbijanja, a dobitak bi bila jedna lekcija.

Ovi testovi čuvaju tu odluku i dokazuju da čitač radi ono što tvrdi.
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
SCIENTIFIC_LESSONS = [lid for lid, entry in COMPILED.items()
                      if tuple((entry.get("parameters") or {}).get("concepts") or ())
                      == ("scientific_notation",)]


# ---------------------------------------------------------------------------
# 1. ODLUKA: BLOKER NIJE UKLJUČEN
# ---------------------------------------------------------------------------

def test_the_scientific_notation_detector_is_not_wired_as_a_blocker():
    assert detectors._detect_scientific_notation not in detectors.DETECTORS.values()
    assert "power_arithmetic" not in detectors.DETECTORS


def test_the_scientific_notation_lesson_still_resolves_to_unknown():
    assert SCIENTIFIC_LESSONS, "očekivana je tačno jedna lekcija naučnog zapisa"
    for lesson_id in SCIENTIFIC_LESSONS:
        contract = semantic_contracts.contract_for(lesson_id)
        result = detectors.detect(contract, "Zapiši broj $31000$ u naučnom zapisu.",
                                  answer_text=r"$42 \cdot 10^{4}$")
        assert result.status == detectors.STATUS_UNSUPPORTED
        assert not result.blocking


def test_the_authority_artifact_still_reports_it_as_unknown():
    status = json.loads((ROOT / "data" / "semantic_authority_status.json")
                        .read_text(encoding="utf-8"))["lessons"]
    for lesson_id in SCIENTIFIC_LESSONS:
        assert status[lesson_id]["detector_status"] == "UNKNOWN"
        assert status[lesson_id]["server_can_refuse_publication"] is False


# ---------------------------------------------------------------------------
# 2. ČITAČ OBLIKA — TRI ISHODA I SVE VARIJANTE ZAPISA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    r"$4,2 \cdot 10^{5}$", r"$4.2 \cdot 10^{5}$", r"$4,2 \cdot 10^5$",
    r"$4,2 \times 10^{5}$", r"$4,2·10^{5}$", r"$7 \times 10^{-3}$",
    r"$-3,5 \cdot 10^{4}$", r"$1 \cdot 10^{0}$", r"$9,99 \cdot 10^{-7}$",
    r"$10^{6}$",
])
def test_canonical_scientific_notation_passes(text):
    assert detectors.scientific_notation_form(text)[0] == detectors.STATUS_PASS


@pytest.mark.parametrize("text", [
    r"$42 \cdot 10^{4}$", r"$0,42 \cdot 10^{6}$", r"$-42 \cdot 10^{4}$",
    r"$10 \cdot 10^{3}$", r"$0,7 \cdot 10^{-2}$",
])
def test_non_canonical_mantissa_fails(text):
    assert detectors.scientific_notation_form(text)[0] == detectors.STATUS_FAIL


@pytest.mark.parametrize("text", [
    r"$4,2 \cdot 10^{2,5}$",           # necio izložilac — ne pogađa se
    r"$420000$",                        # obrnuti smjer: običan decimalni broj
    r"$2 \cdot 10^{3} + 5$",            # zbir, ne jedan zapis
    r"$2,4\cdot 10^3 = 0,24\cdot 10^4$",  # dva stepena — živi primjer iz sjenke
    r"$3 \cdot 2^{4}$",                 # nije stepen broja 10
    "",
])
def test_unprovable_forms_stay_unknown(text):
    assert detectors.scientific_notation_form(text)[0] == detectors.STATUS_UNSUPPORTED


def test_the_reverse_direction_of_the_lesson_can_never_be_blocked():
    """„Koliko iznosi $9,9 \\cdot 10^5$?“ ima OBIČAN broj kao tačan odgovor."""
    contract = semantic_contracts.contract_for(SCIENTIFIC_LESSONS[0])
    result = detectors._detect_scientific_notation(
        contract, r"Koliko iznosi $9,9 \cdot 10^{5}$?", "$990000$")
    assert result.status == detectors.STATUS_UNSUPPORTED


def test_other_power_lessons_are_untouched():
    """Pravilo se čita iz ugovora; lekcije o kvadratu ili zakonima stepena
    prolaze netaknute."""
    others = [lid for lid, entry in COMPILED.items()
              if entry["detector"] == "power_arithmetic"
              and lid not in SCIENTIFIC_LESSONS]
    assert others
    for lesson_id in others[:6]:
        contract = semantic_contracts.contract_for(lesson_id)
        result = detectors._detect_scientific_notation(contract, "", r"$42 \cdot 10^{4}$")
        assert result.status == detectors.STATUS_UNSUPPORTED


# ---------------------------------------------------------------------------
# 3. RECEPT ZA POPRAVKU (spreman, generički, bez ID-ja lekcije)
# ---------------------------------------------------------------------------

def test_the_finding_carries_a_generic_repair_recipe():
    contract = semantic_contracts.contract_for(SCIENTIFIC_LESSONS[0])
    result = detectors._detect_scientific_notation(
        contract, "Zapiši broj u naučnom zapisu.", r"$42 \cdot 10^{4}$")
    assert result.status == detectors.STATUS_FAIL
    assert result.code == detectors.CODE_NONCANONICAL_SCIENTIFIC_NOTATION
    assert "1 \\le |a|" in result.reason and "10" in result.reason
    assert "preformuliši" in result.reason.lower()
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", result.reason)


# ---------------------------------------------------------------------------
# 4. SUSJEDNE GARANCIJE NEPROMIJENJENE
# ---------------------------------------------------------------------------

def test_the_measure_dimension_detector_is_unchanged():
    assert detectors.DETECTORS["geometry_formula_2d"] is detectors._detect_measure_dimension
    assert detectors.DETECTORS["solid_geometry_direct"] is detectors._detect_measure_dimension
    assert detectors.unit_exponents("$12$ cm²") == {2}
    assert detectors.unit_exponents("$12$ cm$^2$") == {2}


def test_no_lesson_identity_in_the_detector_module():
    source = (ROOT / "matbot" / "semantics" / "detectors.py").read_text(encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", source)
    assert not re.search(r"lesson_id\s*==", source)


def test_single_hint_diversity_and_config_are_untouched():
    from matbot import archetype_support, config, form_variants, release_config
    assert config.practice_single_hint_enabled() is True
    assert archetype_support._enabled() is True
    assert form_variants._enabled() is True
    # POVLACENJE (2026-08-14): stari motor je uklonjen, pa
    # `MATBOT_PRACTICE_PIPELINE` vise nije ni deklarisan. Cuva se
    # ono sto jos bira rutu lekcije — opseg brze rute.
    assert "MATBOT_PRACTICE_PIPELINE" not in release_config.REQUIRED_RELEASE_ENV
    assert release_config.REQUIRED_RELEASE_ENV["MATBOT_FAST_SINGLE_CALL_SCOPE"] == \
        "model_backed"


def test_zero_call_routes_stay_zero_call(monkeypatch):
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
        "session_id": "sn-det", "grade": 6, "selected_topic": "6-01-001",
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": ""})
    assert response.get("status") == "ready"
