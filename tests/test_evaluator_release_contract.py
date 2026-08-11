"""UGOVOR ODLUKE O IZDANJU (Faza 0). ZERO poziva modela, ZERO izmjena proizvoda.

ZAŠTO POSTOJI. Živi FW-X03 (final40_c17538a) je kroz sve automatske provjere
prošao ovako:

    korak 0 (zadatak)  14 provjera → 14 PASS
    korak 1 (hint 1)   14 provjera → 14 PASS   ← objavljeno curenje tvrdnje
    korak 3 (hint 3)   15 provjera → 14 PASS, 1 SKIP  ← objavljen netačan izvod

Nula FAIL. Oba blokatora izdanja došla su iz RUČNOG čitanja sirovog odgovora.
Ako bi ijedan budući izvještaj iz „sve zeleno“ izveo spremnost za izdanje,
ponovio bi tačno tu grešku.

Ovdje se zamrzava suprotno pravilo, kao čista funkcija nad već snimljenim
zapisima (`tools/practice_eval/release_contract.py`): spremnost traži da je
svaki scenario `CLEAN`, da nijedna provjera nije ostavila
`MANUAL_SEMANTIC_REVIEW_REQUIRED` i da nema prijavljenog ručnog blokatora.

Ovaj modul NE mijenja `classify.py`, ne mijenja `report.py` i ne mijenja
nijedan postojeći izlaz kampanje.
"""
from __future__ import annotations

from tools.practice_eval import classify, release_contract


def _turn(step=0, calls=2, results=()):
    return {
        "step_index": step, "kind": "text", "sdk_calls": calls,
        "sdk_call_kinds": ["tutor_turn", "reviewer_turn"],
        "precondition_unmet": "",
        "check_results": [{"name": name, "outcome": outcome, "detail": detail}
                          for name, outcome, detail in results],
    }


def _record(scenario_id="T01", turns=(), failed=(), status="PASS"):
    return {
        "id": scenario_id, "status": status, "turns": list(turns),
        "failed_checks": [{"check": name, "step": step, "detail": detail,
                           "label": f"step{step}:{name}"}
                          for name, step, detail in failed],
        "skipped_checks": [], "rubrics": [],
    }


# Doslovan oblik FW-X03: sve zeleno na koraku nagovještaja, jedan SKIP na vrhu
# ljestvice. Nijedna provjera ne pada.
def _fw_x03_shaped_record():
    return _record(
        scenario_id="FW-X03",
        turns=[
            _turn(0, results=[("published", "pass", ""), ("options_ok", "pass", ""),
                              ("package_clean", "pass", ""), ("lesson_matches", "pass", ""),
                              ("stays_in_lesson", "pass", ""), ("math_safe", "pass", "")]),
            _turn(1, calls=1, results=[("hint_no_leak", "pass", ""),
                                       ("math_safe", "pass", ""),
                                       ("terminology_clean", "pass", ""),
                                       ("task_preserved", "pass", "")]),
            _turn(3, calls=1, results=[
                ("hint_differs", "pass", ""), ("math_safe", "pass", ""),
                ("solution_complete", "skip",
                 "the final result could not be mechanically located in the worked solution")]),
        ],
        status="REVIEW",
    )


# ---------------------------------------------------------------------------
# 1. ZELENE NISKONIVOVSKE PROVJERE NISU SPREMNOST ZA IZDANJE
# ---------------------------------------------------------------------------

def test_the_fw_x03_shaped_record_has_no_failing_check_at_all():
    """Polazna činjenica: ništa ne pada — zato je i prošlo."""
    record = _fw_x03_shaped_record()
    outcomes = {result["outcome"]
                for turn in record["turns"] for result in turn["check_results"]}
    assert "fail" not in outcomes


def test_a_single_skipped_check_blocks_release_readiness():
    verdict = release_contract.release_verdict([_fw_x03_shaped_record()])
    assert verdict.ready is False
    assert any(entry.endswith(":solution_complete") for entry in verdict.unproven)


def test_identifier_only_lesson_checks_never_carry_release_readiness():
    """`lesson_matches`/`stays_in_lesson` PASS ne dokazuje lekcijsku vjernost."""
    verdict = release_contract.release_verdict([_fw_x03_shaped_record()])
    assert "FW-X03:lesson_matches" in verdict.unproven
    assert "FW-X03:stays_in_lesson" in verdict.unproven


def test_a_manual_blocker_alone_blocks_release_even_with_everything_green():
    """Ručni nalaz (FW-X03 hint 1) mora oboriti odluku i bez ijednog FAIL-a."""
    clean = _record(turns=[_turn(0, results=[("published", "pass", ""),
                                             ("math_safe", "pass", "")])])
    verdict = release_contract.release_verdict(
        [clean], manual_blockers=("FW-X03 hint 1 — proposition-equivalent disclosure",))
    assert verdict.ready is False
    assert any(entry.startswith("manual: FW-X03 hint 1") for entry in verdict.blockers)


def test_proposition_leak_evidence_is_expressible_as_a_manual_blocker():
    """Nalaz mjerača Faze 0 ulazi u odluku kao ručni blokator dok nije uvezan."""
    from tools.practice_eval import hint_evidence, hintsemantics
    evidence = hint_evidence.FW_X03
    measurement = hintsemantics.measure(
        evidence.hint(1), evidence.marked_option_text, evidence.distractor_texts,
        evidence.task_text)
    assert measurement.disclosed is True
    verdict = release_contract.release_verdict(
        [_record(turns=[_turn(0, results=[("published", "pass", "")])])],
        manual_blockers=(f"{evidence.scenario_id} hint 1: {measurement.verdict}",))
    assert verdict.ready is False


# ---------------------------------------------------------------------------
# 2. ODLUKA JE MOGUĆA — ugovor nije „nikad spremno“
# ---------------------------------------------------------------------------

def test_a_fully_proven_clean_scenario_can_be_release_ready():
    """Bez ovoga bi ugovor bio beskoristan: potpuno dokazan scenario prolazi."""
    record = _record(status="PASS", turns=[_turn(0, results=[
        ("published", "pass", ""), ("options_ok", "pass", ""),
        ("package_clean", "pass", ""), ("math_safe", "pass", ""),
        ("numeric_consistent", "pass", ""), ("terminology_clean", "pass", "")])])
    assert classify.classify(record)["outcome_class"] == classify.CLEAN
    verdict = release_contract.release_verdict([record])
    assert verdict.ready is True
    assert verdict.blockers == () and verdict.unproven == ()


def test_a_failing_check_is_a_blocker_not_merely_unproven():
    record = _record(status="FAIL",
                     turns=[_turn(0, results=[("published", "pass", ""),
                                              ("options_ok", "fail", "option_count=0")])],
                     failed=[("options_ok", 0, "option_count=0")])
    verdict = release_contract.release_verdict([record])
    assert verdict.ready is False
    assert any("options_ok failed deterministically" in entry for entry in verdict.blockers)


def test_a_safe_fail_closed_scenario_is_still_not_release_ready():
    """Sigurno odbijanje NIJE kvar sadržaja, ali ni dokaz spremnosti."""
    record = _record(status="FAIL",
                     turns=[_turn(0, results=[("published", "fail", "safe_error")])],
                     failed=[("published", 0, "safe_error")])
    assert classify.classify(record)["outcome_class"] == classify.SAFE_FAIL_CLOSED
    assert release_contract.release_verdict([record]).ready is False


# ---------------------------------------------------------------------------
# 3. JAČINA DOKAZA SE NIKAD NE PRECJENJUJE
# ---------------------------------------------------------------------------

def test_the_weakest_strength_wins_across_repeated_checks():
    """Ista provjera PASS na jednom koraku i SKIP na drugom → nedokazano."""
    record = _record(turns=[
        _turn(0, results=[("package_clean", "pass", "")]),
        _turn(1, results=[("package_clean", "skip", "nothing to compare")]),
    ])
    strengths = release_contract.scenario_strengths(record)
    assert strengths["package_clean"] == release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED


def test_release_verdict_explains_why_unproven_is_not_verified():
    verdict = release_contract.release_verdict([_fw_x03_shaped_record()])
    assert any("never count as verified" in note for note in verdict.notes)


def test_every_declared_strength_is_a_known_vocabulary_value():
    for name in ("published", "lesson_matches", "solution_complete", "options_ok",
                 "help_curriculum_policy_consistent"):
        for outcome in ("pass", "fail", "skip", ""):
            assert release_contract.strength_for_check(name, outcome) in \
                release_contract.VERIFICATION_STRENGTHS


# ---------------------------------------------------------------------------
# 4. FAZA 3 — OGRANIČEN DOKAZ NAD SERVIRANOM POMOĆI NE NOSI SPREMNOST IZDANJA
# ---------------------------------------------------------------------------

def test_help_curriculum_policy_pass_never_carries_release_readiness():
    """PASS dokazuje ZAPIS van razreda, ne pedagošku primjerenost pomoći."""
    record = _record(status="PASS", turns=[_turn(0, results=[
        ("published", "pass", ""),
        ("help_curriculum_policy_consistent", "pass", ""),
    ])])
    verdict = release_contract.release_verdict([record])
    assert any(entry.endswith(":help_curriculum_policy_consistent")
               for entry in verdict.unproven)
    assert verdict.ready is False


def test_help_curriculum_policy_fail_is_a_release_blocker():
    record = _record(turns=[_turn(0, results=[
        ("published", "pass", ""),
        ("help_curriculum_policy_consistent", "fail", "grade_capability_mismatch"),
    ])])
    verdict = release_contract.release_verdict([record])
    assert verdict.ready is False
    assert any("help_curriculum_policy_consistent" in entry
               for entry in verdict.blockers)


def test_every_bounded_class_check_is_also_registered_as_a_real_check():
    """Ugovor ne smije imenovati mjerač koji ne postoji (tiha rupa u dokazu)."""
    from tools.practice_eval import checks as check_lib

    known = set(check_lib.known_check_names())
    for name in release_contract.BOUNDED_CLASS_CHECKS:
        assert name in known, name
        assert release_contract.strength_for_check(name, "pass") == \
            release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED
