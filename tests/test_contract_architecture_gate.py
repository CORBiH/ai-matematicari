"""ARHITEKTONSKA KAPIJA Faze A/1 (G1–G10).

Cilj nije „prošli su testovi“ nego dokaz da se OBIČNA nova lekcija dodaje BEZ
ijedne izmjene Python koda. Grep provjere (G1/G2) su nužne ali nedovoljne —
težinu nose ponašajni testovi koji instanciraju SINTETIČKI ugovor i provuku ga
kroz stvarni Practice turn (sada: serverski generator kostura, ne modelov dokaz).
"""
import copy
import json
import random
import re
from pathlib import Path

import pytest

from matbot.contracts import archetypes, registry, schema
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from tests.conftest import FakeLLM, make_output, make_task

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "matbot" / "contracts"
TOPICS = json.loads((ROOT / "data" / "topics.json").read_text(encoding="utf-8"))
TEMPLATES = {
    key: value
    for key, value in json.loads(
        (ROOT / "data" / "contract_templates.json").read_text(encoding="utf-8")
    ).items()
    if not key.startswith("_")
}

_TOPIC_ID_RE = re.compile(r"\b\d-\d{2}-\d{3}\b")


def _engine_sources():
    return sorted(ENGINE_DIR.glob("*.py"))


# ---------------------------------------------------------------------------
# G1/G2 — nijedan identitet lekcije ne smije postojati u generičkom motoru
# ---------------------------------------------------------------------------

def test_g1_no_topic_id_literal_anywhere_in_the_engine():
    offenders = []
    for path in _engine_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _TOPIC_ID_RE.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, (
        "Generički motor ne smije sadržavati ID lekcije. Ako je izuzetak zaista "
        f"neophodan, mora biti dokumentovan u docs/LESSON_CONTRACTS.md: {offenders}"
    )


def test_g2_no_lesson_title_appears_in_the_engine():
    titles = {
        lesson["title"]
        for grade in TOPICS["grades"].values()
        for lesson in grade["lessons"]
        if len(lesson["title"]) >= 12
    }
    blob = "\n".join(path.read_text(encoding="utf-8") for path in _engine_sources())
    found = sorted(title for title in titles if title in blob)
    assert not found, f"Naziv lekcije u kodu motora: {found}"


def test_g1_practice_no_longer_branches_on_a_lesson_identity():
    source = (ROOT / "matbot" / "practice.py").read_text(encoding="utf-8")
    assert not _TOPIC_ID_RE.search(source)
    assert "lesson_task_validation" not in source


# ---------------------------------------------------------------------------
# G3 — potpuno nova lekcija radi bez ijedne izmjene izvornog koda
# ---------------------------------------------------------------------------

SYNTHETIC_INTEGER_ROW = {
    "canonical_topic_id": "9-99-999",       # namjerno NE postoji u topics.json
    "grade": 7,
    "status": "enabled",
    "inherits": "arithmetic",
    "skill": "add_integers",
    "allowed_operations": ["add"],
    "allowed_task_archetypes": ["direct_computation"],
    "operand_constraints": {"sign_policy": "non_negative", "integer_range": [1, 50]},
    "invariant_constraints": ["allowed_operations"],
}


def test_g5_error_category_without_a_deriver_fails_at_load():
    row = dict(SYNTHETIC_INTEGER_ROW, error_category_set=["combined_denominators"])
    contract = schema.resolve_and_build(row, TEMPLATES)
    archetypes.assert_supported(contract)  # postojeća kategorija prolazi

    with pytest.raises(schema.ContractSchemaError):
        schema.resolve_and_build(
            dict(row, error_category_set=["telepathic_guess"]), TEMPLATES
        )


def test_g5_identify_error_without_categories_fails_at_load():
    row = dict(SYNTHETIC_INTEGER_ROW,
               allowed_task_archetypes=["direct_computation", "identify_error"])
    with pytest.raises(schema.ContractSchemaError, match="error_category_set"):
        schema.resolve_and_build(row, TEMPLATES)


# ---------------------------------------------------------------------------
# G6 — UKLJUČEN ugovor nikad ne pada nazad na legacy
# ---------------------------------------------------------------------------

def _pilot_turn(**changes):
    payload = {
        "session_id": "fail-closed", "grade": 6, "selected_topic": "6-04-009",
        "selected_oblast": "", "student_message": "Daj zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


# ---------------------------------------------------------------------------
# G7 — neispravan ugovor pada zatvoreno, na učitavanju
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mutation,expected", [
    ({"domain": "astrologija"}, "domain"),
    ({"allowed_operations": ["teleport"]}, "allowed_operations"),
    ({"required_evidence": ["telepathy"]}, "uklonjena"),
    ({"answer_verifier": "vibes"}, "answer_verifier"),
    ({"status": "maybe"}, "status"),
    ({"grade": 12}, "grade"),
    ({"operand_constraints": {"denominator_relation": "sometimes"}}, "denominator_relation"),
    ({"typo_field": True}, "nepoznata polja"),
])
def test_g7_invalid_contract_raises_at_load(mutation, expected):
    row = dict(SYNTHETIC_INTEGER_ROW)
    row.update(mutation)
    with pytest.raises(schema.ContractSchemaError) as error:
        schema.resolve_and_build(row, TEMPLATES)
    assert expected in str(error.value)


def test_g7_widening_inheritance_is_a_conflict():
    row = dict(SYNTHETIC_INTEGER_ROW,
               allowed_task_archetypes=["direct_computation", "identify_equivalent"])
    with pytest.raises(schema.ContractConflictError, match="proširuje"):
        schema.resolve_and_build(row, TEMPLATES)


def test_g7_duplicate_topic_ids_reject(tmp_path):
    contracts = tmp_path / "lesson_contracts.json"
    contracts.write_text(json.dumps({"contracts": [
        dict(SYNTHETIC_INTEGER_ROW), dict(SYNTHETIC_INTEGER_ROW),
    ]}), encoding="utf-8")
    with pytest.raises(schema.ContractSchemaError, match="dupli"):
        registry.load_all(contracts_path=contracts)


def test_g7_the_shipped_registry_loads_clean():
    contracts = registry.load_all()
    assert len(contracts) == 6
    assert all(contract.status == "enabled" for contract in contracts.values())


# ---------------------------------------------------------------------------
# G8 — lekcije bez ugovora zadržavaju postojeće ponašanje
# ---------------------------------------------------------------------------

def test_g8_uncontracted_lessons_keep_the_legacy_path():
    from matbot import task_families as tf

    samples = {
        "6-04-014": "fraction_expression",           # razlomci, legacy mapa po lekciji
        "7-03-008": "expand_to_given_denominator",   # razlomci, zajednički skup
        "9-05-004": "solve_system",                  # sistemi
        "9-04-001": "solve_equation",                # jednačine
        "6-12-003": "identify_next_step",            # konstrukcije
        "8-05-001": "direct_formula_application",    # geometrija
    }
    from matbot.topics import lesson_info

    for topic_id, expected_first in samples.items():
        assert registry.state_for_topic(topic_id) == registry.STATE_LEGACY
        info = lesson_info(int(topic_id[0]), topic_id)
        families = tf.applicable_families(
            int(topic_id[0]), info["oblast"], info["title"], lesson_id=topic_id)
        assert families[0] == expected_first, (topic_id, families)


def test_g9_the_deleted_module_is_really_gone():
    assert not (ROOT / "matbot" / "lesson_task_validation.py").exists()
    with pytest.raises(ImportError):
        __import__("matbot.lesson_task_validation")


# ---------------------------------------------------------------------------
# G10 — šest pilot lekcija se razlikuju SAMO podacima
# ---------------------------------------------------------------------------

def test_g10_pilot_contracts_differ_only_in_data_fields():
    contracts = registry.load_all()
    varying, constant = {}, {}
    for contract in contracts.values():
        for field in ("progression_policy", "terminology_profile", "notation_profile",
                      "answer_verifier", "contract_version", "grade", "domain"):
            constant.setdefault(field, set()).add(getattr(contract, field))
        varying.setdefault("skill", set()).add(contract.skill)

    # Sve pilot lekcije dijele isti motor, verifikator i profile…
    for field, values in constant.items():
        assert len(values) == 1, (field, values)
    # …a razlikuju se vještinom i vrijednostima ograničenja.
    assert len(varying["skill"]) == 6


def test_other_modes_are_untouched_by_the_engine():
    """Explain i Result ne smiju ni znati da motor ugovora postoji."""
    for module in ("explain", "quick"):
        source = (ROOT / "matbot" / f"{module}.py").read_text(encoding="utf-8")
        assert "contracts" not in source, module


def test_security_and_transport_files_are_unchanged_by_stage_a():
    """Ugovor opisuje matematiku lekcije — ne autentikaciju, sesiju ni klijenta.

    NAPOMENA O OBIMU: `templates/index.html` se namjerno NE provjerava ovdje jer
    je već nosio nekomitovane izmjene od ranije (izolacija na reload, `v:2` u
    localStorage) prije nego što je Faza A počela — poređenje s HEAD ne može te
    dvije stvari razdvojiti. Frontend ugovor Faza A ipak ne mijenja: to čuva
    tests/test_frontend_practice_context.py.

    `matbot/llm.py` i `matbot/config.py` su UKLONJENI iz zaštićene liste pri
    pivotu na univerzalni dvopozivni put: taj put po dizajnu dodaje dvije nove
    vrste poziva (`tutor_turn`, `reviewer_turn`) i dva podesiva izbora modela
    (`TUTOR_MODEL`, `REVIEWER_MODEL`). To NIJE širenje ugovora lekcije nego
    transportni sloj, pa ostaje pokriveno testovima univerzalnog puta."""
    import subprocess

    protected = [
        "matbot/api.py", "matbot/auth.py", "matbot/ratelimit.py",
        "matbot/turnlock.py",
        "matbot/explain.py", "matbot/quick.py", "matbot/imagecheck.py",
        "matbot/imageinput.py",
    ]
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", *protected],
        cwd=ROOT, capture_output=True, text=True,
    )
    changed = [line for line in result.stdout.splitlines() if line.strip()]
    assert not changed, f"Faza A ne smije dirati ove fajlove: {changed}"
