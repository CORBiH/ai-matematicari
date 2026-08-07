"""Faza 4A — porodica `fraction_arithmetic_direct` (6-04-009…012).

TESTOVI SU NAPISANI PRIJE IMPLEMENTACIJE. Pokrivaju:
  • jedan višekratni parametarski detektor (ne četiri po lekciji);
  • PASS / FAIL / UNSUPPORTED s eksplicitnom granicom dokaza;
  • razliku glavne vještine od POMOĆNOG koraka (skraćivanje, svođenje);
  • unakrsnu matricu: valjan zadatak jedne lekcije mora pasti (ili ostati
    nedokaziv) pod ugovorima ostale tri;
  • kompilaciju podataka, nepromjenjivost ugovora i porijeklo dokaza;
  • integraciju: Tutor kontekst → preflight nalaz → recenzentova ispravka →
    identična završna validacija → objava samo bez blokirajućih nalaza;
  • da nepilot lekcije i dokazano ponašanje 6-03-004 ostaju netaknuti.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from matbot.semantics import contracts as sem_contracts  # noqa: E402
from matbot.semantics import detectors as sem_detectors  # noqa: E402

PILOT = ("6-04-009", "6-04-010", "6-04-011", "6-04-012")

PASS = sem_detectors.STATUS_PASS
FAIL = sem_detectors.STATUS_FAIL
UNSUPPORTED = sem_detectors.STATUS_UNSUPPORTED


def detect(lesson_id, text):
    contract = sem_contracts.contract_for(lesson_id)
    assert contract is not None, lesson_id
    return sem_detectors.detect(contract, text)


# ---------------------------------------------------------------------------
# FIKSTURE — autorski zadaci, konzistentni s dokazima Faze 2.5/3.
# ---------------------------------------------------------------------------

VALID = {
    "6-04-009": {
        1: "Izračunaj $\\frac{2}{7} + \\frac{3}{7}$.",
        2: "Izračunaj $\\frac{7}{10} - \\frac{3}{10}$ i skrati rezultat.",
        3: "Odredi nedostajući razlomak: $\\frac{3}{8} + \\square = \\frac{7}{8}$.",
    },
    "6-04-010": {
        1: "Izračunaj $\\frac{1}{2} + \\frac{1}{4}$.",
        2: "Izračunaj $\\frac{2}{3} + \\frac{3}{5}$.",
        3: "Izračunaj $\\frac{5}{6} - \\frac{3}{4}$ i skrati rezultat.",
    },
    "6-04-011": {
        1: "Izračunaj $3 \\cdot \\frac{2}{5}$.",
        2: "Izračunaj $\\frac{3}{4} \\cdot \\frac{8}{9}$ i skrati.",
        3: "Odredi nedostajući faktor: $\\frac{2}{3} \\cdot \\square = \\frac{1}{2}$.",
    },
    "6-04-012": {
        1: "Izračunaj $\\frac{4}{5} : 2$.",
        2: "Izračunaj $\\frac{3}{8} : \\frac{9}{4}$.",
        3: "Odredi djelilac: $\\frac{5}{6} : \\square = \\frac{5}{12}$.",
    },
}

WRONG_OPERATION = {
    "6-04-009": "Izračunaj $\\frac{2}{7} \\cdot \\frac{3}{7}$.",
    "6-04-010": "Izračunaj $\\frac{1}{2} \\cdot \\frac{1}{4}$.",
    "6-04-011": "Izračunaj $\\frac{3}{4} : \\frac{8}{9}$.",
    "6-04-012": "Izračunaj $\\frac{3}{8} \\cdot \\frac{9}{4}$.",
}

# Odnos imenilaca je mjerljiv samo za sabiranje/oduzimanje.
WRONG_DENOMINATOR = {
    "6-04-009": "Izračunaj $\\frac{1}{2} + \\frac{1}{3}$.",
    "6-04-010": "Izračunaj $\\frac{2}{9} + \\frac{5}{9}$.",
}

NEIGHBOUR_SKILL = {
    "6-04-009": "Proširi razlomak $\\frac{2}{3}$ brojem 4.",
    "6-04-010": "Svedi razlomke $\\frac{1}{2}$ i $\\frac{3}{4}$ na zajednički imenilac.",
    "6-04-011": "Skrati razlomak $\\frac{6}{8}$ do nesvodivog oblika.",
    "6-04-012": "Riješi jednačinu $x \\cdot \\frac{2}{3} = \\frac{4}{9}$.",
}

# Pomoćni korak (skraćivanje/svođenje) NE smije oboriti zadatak čija je glavna
# radnja dozvoljena operacija.
SUPPORTING_SIMPLIFICATION = {
    "6-04-009": "Izračunaj $\\frac{4}{12} + \\frac{2}{12}$ pa skrati rezultat.",
    "6-04-010": "Svedi na zajednički imenilac i izračunaj $\\frac{1}{3} + \\frac{1}{6}$.",
    "6-04-011": "Skrati prije množenja pa izračunaj $\\frac{4}{9} \\cdot \\frac{3}{8}$.",
    "6-04-012": "Izračunaj $\\frac{2}{3} : \\frac{4}{9}$ i skrati rezultat.",
}

AMBIGUOUS = {
    "6-04-009": "Ana je pojela $\\frac{2}{8}$ torte. Koji dio torte je ostao?",
    "6-04-010": "Na slici je prikazan dio pravougaonika. Koji razlomak je osjenčen?",
    "6-04-011": "Od 24 učenika njih $\\frac{3}{8}$ trenira košarku. Koliko je to učenika?",
    "6-04-012": "Objasni kako se dijele razlomci.",
}


# ---------------------------------------------------------------------------
# 1) PO LEKCIJI — valjani nivoi, pogrešna operacija, pogrešan odnos imenilaca
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id", PILOT)
@pytest.mark.parametrize("level", (1, 2, 3))
def test_valid_levels_pass(lesson_id, level):
    result = detect(lesson_id, VALID[lesson_id][level])
    assert result.status == PASS, (lesson_id, level, result.code, result.evidence)


@pytest.mark.parametrize("lesson_id", PILOT)
def test_wrong_operation_fails(lesson_id):
    result = detect(lesson_id, WRONG_OPERATION[lesson_id])
    assert result.status == FAIL
    assert result.code == sem_detectors.CODE_OPERATION_MISMATCH
    assert result.evidence.get("operation")


@pytest.mark.parametrize("lesson_id", sorted(WRONG_DENOMINATOR))
def test_wrong_denominator_relation_fails(lesson_id):
    result = detect(lesson_id, WRONG_DENOMINATOR[lesson_id])
    assert result.status == FAIL
    assert result.code == sem_detectors.CODE_DENOMINATOR_MISMATCH
    assert result.evidence.get("denominators")


@pytest.mark.parametrize("lesson_id", PILOT)
def test_neighbouring_fraction_skill_fails(lesson_id):
    result = detect(lesson_id, NEIGHBOUR_SKILL[lesson_id])
    assert result.status == FAIL
    assert result.code == sem_detectors.CODE_FORBIDDEN_MAIN_SKILL


@pytest.mark.parametrize("lesson_id", PILOT)
def test_supporting_simplification_still_passes(lesson_id):
    """Pomoćni korak nije glavna vještina — zadatak ostaje valjan."""
    result = detect(lesson_id, SUPPORTING_SIMPLIFICATION[lesson_id])
    assert result.status == PASS, (lesson_id, result.code, result.evidence)


@pytest.mark.parametrize("lesson_id", PILOT)
def test_ambiguous_text_is_unsupported_not_a_false_verdict(lesson_id):
    result = detect(lesson_id, AMBIGUOUS[lesson_id])
    assert result.status == UNSUPPORTED, (lesson_id, result.status, result.evidence)
    assert result.boundary


def test_multiple_mixed_operations_are_unsupported():
    result = detect("6-04-009", "Izračunaj $\\frac{1}{2} + \\frac{1}{4} \\cdot 2$.")
    assert result.status == UNSUPPORTED


def test_symbolic_variable_is_unsupported():
    result = detect("6-04-011", "Izračunaj $\\frac{a}{b} \\cdot \\frac{c}{d}$.")
    assert result.status == UNSUPPORTED


def test_detector_never_claims_more_than_it_proves():
    """Granica dokaza mora biti eksplicitna u svakom rezultatu."""
    for lesson_id in PILOT:
        for text in (VALID[lesson_id][1], AMBIGUOUS[lesson_id]):
            assert detect(lesson_id, text).boundary.strip()


# ---------------------------------------------------------------------------
# 2) UNAKRSNA MATRICA — valjan zadatak jedne lekcije pod tuđim ugovorom
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("owner", PILOT)
@pytest.mark.parametrize("other", PILOT)
@pytest.mark.parametrize("level", (1, 2, 3))
def test_cross_lesson_matrix(owner, other, level):
    text = VALID[owner][level]
    result = detect(other, text)
    if owner == other:
        assert result.status == PASS
    else:
        assert result.status in (FAIL, UNSUPPORTED), (
            owner, other, level, result.status, result.evidence)


# ---------------------------------------------------------------------------
# 3) PODACI: kompilacija, nepromjenjivost, porijeklo
# ---------------------------------------------------------------------------

def test_the_four_pilot_lessons_keep_their_contract():
    # Kapacitetna ekspanzija je dodala nove porodice; četiri pilot lekcije
    # razlomaka i dalje nose TAČNO svoj nepromijenjen ugovor.
    all_ids = set(sem_contracts.all_contracts())
    assert set(PILOT) <= all_ids
    for lesson_id in PILOT:
        assert sem_contracts.contract_for(lesson_id).family_id == \
            "fraction_arithmetic_direct"


def test_unmapped_lessons_have_no_contract():
    # Lekcije koje NISU u pregledanoj tabeli aktivacija ostaju bez ugovora.
    for lesson_id in ("6-04-013", "6-04-014", "6-01-001", "9-05-007"):
        assert sem_contracts.contract_for(lesson_id) is None, lesson_id


def test_resolved_contract_is_immutable():
    contract = sem_contracts.contract_for("6-04-009")
    with pytest.raises(Exception):
        contract.enforcement_mode = "advisory"
    with pytest.raises(Exception):
        contract.parameters["allowed_operations"] = ("multiply",)


def test_contract_carries_provenance_and_version():
    for lesson_id in PILOT:
        contract = sem_contracts.contract_for(lesson_id)
        assert contract.family_id == "fraction_arithmetic_direct"
        assert contract.evidence_ids, lesson_id
        assert contract.contract_version
        assert contract.enforcement_mode in ("blocking", "advisory")


def test_all_four_lessons_share_one_family_and_one_detector():
    detectors = {sem_contracts.contract_for(l).detector for l in PILOT}
    families = {sem_contracts.contract_for(l).family_id for l in PILOT}
    assert len(detectors) == 1 and len(families) == 1


def test_lessons_differ_only_in_declared_parameters():
    params = {l: sem_contracts.contract_for(l).parameters for l in PILOT}
    assert params["6-04-009"]["denominator_relation"] == "equal"
    assert params["6-04-010"]["denominator_relation"] == "unlike"
    assert set(params["6-04-011"]["allowed_operations"]) == {"multiply"}
    assert set(params["6-04-012"]["allowed_operations"]) == {"divide"}


def test_compilation_is_deterministic(tmp_path):
    import build_lesson_semantics as bls

    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    bls.main(["--out", str(first)])
    bls.main(["--out", str(second)])
    assert first.read_bytes() == second.read_bytes()


def test_compiled_artifact_matches_committed_file():
    import build_lesson_semantics as bls

    assert bls.COMPILED_PATH.exists()
    committed = json.loads(bls.COMPILED_PATH.read_text(encoding="utf-8"))
    assert committed == bls.compile_all()


def test_malformed_contract_is_rejected():
    import build_lesson_semantics as bls

    with pytest.raises(bls.SemanticSchemaError):
        bls.build_assignment({"lesson_id": "6-04-009", "family_id": "nepostojeca"},
                             bls.load_families())


def test_unknown_parameter_is_rejected():
    import build_lesson_semantics as bls

    families = bls.load_families()
    row = {"lesson_id": "6-04-009", "family_id": "fraction_arithmetic_direct",
           "enforcement_mode": "blocking", "evidence_ids": ["X"],
           "parameters": {"nepoznat_parametar": 1}}
    with pytest.raises(bls.SemanticSchemaError):
        bls.build_assignment(row, families)


def test_unknown_parameter_value_is_rejected():
    import build_lesson_semantics as bls

    families = bls.load_families()
    row = {"lesson_id": "6-04-009", "family_id": "fraction_arithmetic_direct",
           "enforcement_mode": "blocking", "evidence_ids": ["X"],
           "parameters": {"denominator_relation": "nesto_drugo"}}
    with pytest.raises(bls.SemanticSchemaError):
        bls.build_assignment(row, families)


def test_no_lesson_id_literal_in_semantic_python():
    """Nijedna Python grana ne smije poznavati konkretan ID lekcije."""
    import re

    pattern = re.compile(r"\b\d-\d{2}-\d{3}\b")
    for path in sorted((ROOT / "matbot" / "semantics").glob("*.py")):
        offenders = [line for line in path.read_text(encoding="utf-8").splitlines()
                     if pattern.search(line)]
        assert not offenders, (path.name, offenders)
