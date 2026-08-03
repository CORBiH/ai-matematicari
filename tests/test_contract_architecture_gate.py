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

from matbot import task_family_validation
from matbot.contracts import (archetypes, evidence as ev, generator, pipeline,
                              registry, schema, verifiers)
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


def test_g3_a_synthetic_contract_needs_no_source_change():
    """Ugovor za ID lekcije koji motor nikad nije vidio — i koji ne postoji ni u
    kurikulumu — mora se učitati i GENERISATI verifikovan zadatak."""
    contract = schema.resolve_and_build(SYNTHETIC_INTEGER_ROW, TEMPLATES)
    archetypes.assert_supported(contract)
    plan = pipeline.build_plan(contract, student_message="Daj zadatak.")
    prepared = pipeline.prepare_task(contract, plan, rng=random.Random(0))
    assert prepared.ok, prepared.code
    skeleton = prepared.skeleton
    assert generator.self_verify(contract, skeleton) == (True, "ok")
    facts = ev.facts_for(skeleton.primary_nodes)
    assert facts.operations == {"add"}


def test_g3_b_new_lesson_is_enabled_end_to_end_by_data_only(monkeypatch):
    """Lekcija DRUGOG razreda i DRUGOG domena (cijeli brojevi, 7. razred) prelazi
    na motor samo dodavanjem reda ugovora — bez ijedne izmjene pod matbot/."""
    topic = "7-02-008"
    assert registry.state_for_topic(topic) == registry.STATE_LEGACY

    row = dict(SYNTHETIC_INTEGER_ROW, canonical_topic_id=topic)
    contract = schema.resolve_and_build(row, TEMPLATES)

    store, fake = SessionStore(), FakeLLM()
    with registry.override_contracts({topic: contract}):
        assert registry.state_for_topic(topic) == registry.STATE_ENGINE
        # Modelov new_task je SAMO signal — sadržaj se ignoriše.
        fake.queue(make_output(new_task=make_task()))
        response = run_practice_turn(store, fake, {
            "session_id": "synthetic-contract", "grade": 7,
            "selected_topic": topic, "selected_oblast": "",
            "student_message": "Daj zadatak.", "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": "",
        })
    assert response["status"] == "ready", response
    assert fake.call_count == 1
    session = store.peek("synthetic-contract")
    assert session["current_family"] == "direct_computation"
    # Objavljen je SERVERSKI zadatak, ne modelov default iz make_task().
    assert session["current_task"].startswith("Izračunaj:")


# ---------------------------------------------------------------------------
# G4/G5 — isti arhetip i isti deterministi kroz dva različita domena
# ---------------------------------------------------------------------------

def test_g4_one_archetype_generates_integers_and_fractions():
    integers = schema.resolve_and_build(SYNTHETIC_INTEGER_ROW, TEMPLATES)
    fractions = registry.load_all()["6-04-009"]
    assert integers.domain != fractions.domain

    for contract in (integers, fractions):
        skeleton = generator.generate(
            contract, "direct_computation", rng=random.Random(1))
        result = verifiers.verify_exact_rational(
            skeleton.truth, skeleton.option_nodes, skeleton.correct_index)
        assert result.ok, contract.canonical_topic_id
    # Ista šifra: obje putanje su prošle kroz isti registrovani arhetip.
    assert (archetypes.archetype_for("direct_computation")
            is archetypes.archetype_for("direct_computation"))


def test_g5_error_category_derivation_spans_two_domains():
    """Strukturno izvođenje kategorije radi kroz domene — deterministi koje će
    K4 generator koristiti kad identify_error dobije serverski generator."""
    contracts = registry.load_all()
    fraction_chain = tuple(ev.parse_node(step) for step in (
        {"op": "add", "args": [{"num": 7, "den": 12}, {"num": 3, "den": 12}]},
        {"num": 10, "den": 24}, {"num": 5, "den": 12},
    ))
    scaling_chain = tuple(ev.parse_node(step) for step in (
        {"num": 8, "den": 20}, {"num": 2, "den": 6},
    ))
    fraction = verifiers.verify_error_category(
        fraction_chain,
        ["combined_denominators", "wrong_numerator", "wrong_operation"],
        0, contracts["6-04-009"].error_category_set)
    scaling = verifiers.verify_error_category(
        scaling_chain,
        ["wrong_reduction", "wrong_operation", "unequal_scaling"],
        0, contracts["6-04-006"].error_category_set)
    assert fraction.ok, fraction.code
    assert scaling.ok, scaling.code


def test_g5_error_category_without_a_deriver_fails_at_load():
    row = dict(SYNTHETIC_INTEGER_ROW, error_category_set=["combined_denominators"])
    contract = schema.resolve_and_build(row, TEMPLATES)
    archetypes.assert_supported(contract)  # postojeća kategorija prolazi

    with pytest.raises(schema.ContractSchemaError):
        schema.resolve_and_build(
            dict(row, error_category_set=["telepathic_guess"]), TEMPLATES
        )


def test_g5_archetype_without_a_generator_fails_at_load():
    """Obećanje oblika koji server ne umije konstruisati je greška starta —
    tačno mehanizam kojim se K2/K4 vraćaju TEK s implementiranim generatorom."""
    row = dict(SYNTHETIC_INTEGER_ROW,
               allowed_task_archetypes=["direct_computation", "find_missing_value"])
    contract = schema.resolve_and_build(row, TEMPLATES)
    with pytest.raises(schema.ContractSchemaError, match="generator"):
        archetypes.assert_supported(contract)


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


def test_g6_self_verification_failure_never_falls_back_to_legacy(monkeypatch):
    """Prisilno oboren verifikator → generator ne može dokazati kostur → pad
    zatvoreno, BEZ legacy puta i BEZ ijednog potrošenog AI poziva (kostur se
    pravi prije poziva)."""
    store, fake = SessionStore(), FakeLLM()

    def _explode(*args, **kwargs):
        raise AssertionError("legacy porodični ugovor NE SMIJE biti pozvan")

    monkeypatch.setattr(task_family_validation, "validate_task_family", _explode)
    monkeypatch.setattr(
        verifiers, "verify_exact_rational",
        lambda *a, **kw: verifiers.VerifierResult(True, False, "forced_failure"),
    )

    before = copy.deepcopy(store.peek("fail-closed"))
    response = run_practice_turn(store, fake, _pilot_turn())

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert "next_state" not in response
    assert fake.call_count == 0, "odbijanje prije poziva ne smije potrošiti poziv"
    assert store.peek("fail-closed") == before


def test_g6_generation_failure_on_bootstrap_is_safe_and_costs_zero_calls(monkeypatch):
    store, fake = SessionStore(), FakeLLM()

    def _fail(*args, **kwargs):
        raise generator.GenerationError("forced")

    monkeypatch.setattr(generator, "generate", _fail)
    response = run_practice_turn(store, fake, _pilot_turn())
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == 0
    assert store.peek("fail-closed") is None


def test_g6_model_task_content_is_ignored_on_the_engine_path():
    """Model ne može podmetnuti svoj zadatak: objavljuje se serverski kostur."""
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(new_task=make_task(text="Izračunaj $999+999$.")))
    response = run_practice_turn(store, fake, _pilot_turn())
    assert response["status"] == "ready"
    session = store.peek("fail-closed")
    assert "999" not in session["current_task"]
    assert session["current_task"].startswith("Izračunaj:")
    # Sve opcije su serverske i tačno jedna nosi istinu (verifikovano u motoru).
    assert len(session["current_options"]) == 4


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


def test_g8_engine_kill_switch_sends_everything_to_legacy(monkeypatch):
    """R1: privremeni globalni prekidač (razvoj). Mora nestati u Fazi D."""
    monkeypatch.setenv("MATBOT_CONTRACT_ENGINE", "off")
    assert registry.state_for_topic("6-04-009") == registry.STATE_LEGACY
    monkeypatch.setenv("MATBOT_CONTRACT_ENGINE", "on")
    assert registry.state_for_topic("6-04-009") == registry.STATE_ENGINE


def test_g8_engine_is_on_by_default(monkeypatch):
    monkeypatch.delenv("MATBOT_CONTRACT_ENGINE", raising=False)
    assert registry.engine_enabled() is True


# ---------------------------------------------------------------------------
# G9 — brisanje starog validatora nije obrisalo njegovu pokrivenost
# ---------------------------------------------------------------------------

def test_g9_ported_regression_manifest_is_complete():
    from tests import test_practice_lesson_semantic_contracts as ported

    assert len(ported.PORTED_BEHAVIOURS) >= 20
    missing = [n for n in ported.PORTED_BEHAVIOURS if not hasattr(ported, f"test_{n}")]
    assert not missing, missing


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


def test_g10_pilot_contracts_use_only_implemented_capabilities():
    for contract in registry.load_all().values():
        assert set(contract.effective_archetypes) <= generator.IMPLEMENTED_ARCHETYPES
        assert contract.answer_verifier in schema.STAGE_A_VERIFIERS


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


def test_g10_declared_vocabulary_matches_the_implementation():
    """Rječnik šeme i stvarno implementirani slojevi ne smiju se razići."""
    assert schema.STAGE_A_VERIFIERS == verifiers.IMPLEMENTED
    # Generator smije pokrivati PODSKUP šemom poznatih arhetipa (K2/K4 dolaze),
    # ali nikad arhetip koji šema uopšte ne poznaje.
    assert archetypes.implemented_ids() <= schema.STAGE_A_ARCHETYPES
    assert schema.ERROR_CATEGORIES == verifiers.DERIVABLE_ERROR_CATEGORIES
