"""SLIJEPE TAČKE EVALUATORA NAD LJESTVICOM NAGOVJEŠTAJA (Faza 0).

ZERO poziva modela, ZERO mreže, ZERO izmjena proizvoda.

Ovaj fajl NE popravlja nagovještaje. On zamrzava, nad DOSLOVNIM živim
tekstom (`tools/practice_eval/hint_evidence.py`), četiri činjenice koje je do
sada znao samo ručni pregled:

  1. FW-X03 nagovještaj 1 je izrekao označenu tvrdnju, a `hint_no_leak` je
     vratio PASS — orakl curenja traži VRIJEDNOST, a odgovor je bio REČENICA.
  2. FW-X03 nagovještaj 3 je objavio NETAČAN izvod do tačnog zaključka, a
     nijedna deterministička provjera to ne može ni pokušati dokazati.
  3. TR-B1 je DVIJE kampanje ranije imao isti oblik curenja i prijavljen je
     kao čist — i, bitno, mjerač iz ove faze ga NE DOSEŽE. To je zamrznuto
     izričito da Faza 2 ne može „završiti“ popravljajući jednu rečenicu.
  4. Čisto skelovanje nad ISTIM zadatkom ne smije biti proglašeno curenjem.

Mjerač (`tools/practice_eval/hintsemantics.py`) NIJE uvezan ni u jednu
provjeru kampanje. Faza 0 mjeri; uvezivanje i produktna popravka su Faza 2.
"""
from __future__ import annotations

import pytest

from matbot import config, feedback, terminology
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.tutor import package_preflight
from tools.practice_eval import checks as check_lib
from tools.practice_eval import hint_evidence, hintsemantics, release_contract


# ---------------------------------------------------------------------------
# POMOĆNO — pravi `TurnObservation`, isti oblik koji runner stvarno gradi
# ---------------------------------------------------------------------------

def _observation(evidence, hint_level_served, hint_text=None):
    """Turn nagovještaja `hint_level_served` nad zakucanim paketom."""
    text = evidence.hint(hint_level_served) if hint_text is None else hint_text
    options = [{"id": oid, "text": otext} for oid, otext in evidence.options]
    session = {
        "lesson_id": evidence.topic_id,
        "current_task": evidence.task_text,
        "current_options": options,
        "correct_option_id": evidence.marked_option_id,
        "expected_answer_summary": evidence.marked_option_text,
        "task_completed": False,
    }
    before = dict(session, hint_level=hint_level_served - 1)
    after = dict(session, hint_level=hint_level_served)
    return check_lib.TurnObservation(
        scenario_id=evidence.scenario_id,
        step_index=hint_level_served,
        step_kind="text",
        topic_id=evidence.topic_id,
        grade=evidence.grade,
        request_payload={"student_message": "Daj mi hint.", "intent": "hint_request",
                         "interaction_phase": "practice_help"},
        http_status=200,
        response={"status": "ready", "answer": text,
                  "effective_topic": evidence.topic_id, "session_mode": "practice"},
        session_before=before,
        session_after=after,
        sdk_calls=1,
    )


def _measure(evidence, hint_level_served, hint_text=None):
    text = evidence.hint(hint_level_served) if hint_text is None else hint_text
    return hintsemantics.measure(text, evidence.marked_option_text,
                                 evidence.distractor_texts, evidence.task_text)


# ---------------------------------------------------------------------------
# 0. PROVENIJENCIJA — zakucani tekst JESTE ono što je kampanja zapisala
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("evidence", hint_evidence.ALL_EVIDENCE,
                         ids=lambda e: e.scenario_id)
def test_pinned_evidence_matches_the_local_campaign_artifact(evidence):
    """`scratchpad/` je u .gitignore — kad artefakt POSTOJI, mora se poklopiti
    bajt za bajt; kad ga nema (svjež checkout), scenario se preskače."""
    if not hint_evidence.local_artifact_available(evidence):
        pytest.skip(f"artefakt kampanje {evidence.campaign} nije lokalno prisutan")
    assert hint_evidence.local_artifact_mismatches(evidence) == []


def test_pinned_evidence_carries_no_internal_package_field():
    """CLAUDE.md pravilo 7: fikstura nosi SAMO ono što je učenik vidio."""
    visible = []
    for evidence in hint_evidence.ALL_EVIDENCE:
        visible.append(evidence.task_text)
        visible.extend(text for _option_id, text in evidence.options)
        visible.extend(text for _level, text in evidence.hints)
    blob = "\n".join(visible)
    for internal in ("difficulty_evidence", "task_signature", "correct_option_index",
                     "selected_lesson_id", "checks.", "reviewed_difficulty_evidence"):
        assert internal not in blob


# ---------------------------------------------------------------------------
# 1. FW-X03 NAGOVJEŠTAJ 1 — zatečeno ponašanje evaluatora (SLIJEPA TAČKA)
# ---------------------------------------------------------------------------

def test_fw_x03_hint1_restates_the_marked_proposition():
    """Osnovna činjenica nalaza: nagovještaj sadrži BAŠ kriterij opcije `a`."""
    hint = hint_evidence.FW_X03.hint(1)
    marked = hint_evidence.FW_X03.marked_option_text
    assert "okomita na svaku pravu iz ravni $\\alpha$ koja prolazi kroz $A$" in marked
    assert "okomita na svaku pravu iz ravni $\\alpha$ koja prolazi kroz $A$" in hint
    # Preokrenut red riječi je jedini razlog zašto podniska ne pogađa.
    assert marked not in hint


def test_current_leak_oracle_cannot_see_the_fw_x03_hint1_disclosure():
    """DOKAZ SLIJEPE TAČKE nad PRODUKCIJSKIM oraklom (`feedback.leaks_answer`).

    Ista funkcija koju koristi i `tutor/pipeline.py::_reveals_committed_answer`
    i evaluatorov `hint_no_leak`. Vrijednosno je orijentisana: tvrdnju ne vidi."""
    evidence = hint_evidence.FW_X03
    assert feedback.leaks_answer(
        evidence.hint(1), evidence.marked_option_text, evidence.marked_option_text,
        task_text=evidence.task_text) is False


def test_evaluator_hint_no_leak_passes_the_published_fw_x03_leak():
    """Zamrznuto zatečeno ponašanje EVALUATORA: PASS na objavljenom curenju."""
    result = check_lib.check_hint_no_leak(_observation(hint_evidence.FW_X03, 1))
    assert result.outcome == check_lib.PASS


# ---------------------------------------------------------------------------
# 2. FW-X03 NAGOVJEŠTAJ 1 — ŽELJENI ugovor (mjerač Faze 0)
# ---------------------------------------------------------------------------

def test_phase0_measure_proves_the_fw_x03_hint1_disclosure():
    result = _measure(hint_evidence.FW_X03, 1)
    assert result.verdict == hintsemantics.DISCLOSED
    assert result.marked_coverage == pytest.approx(1.0)
    assert result.marked_only_coverage == pytest.approx(1.0)
    assert result.distractor_only_coverage == pytest.approx(0.0)


def test_phase0_measure_does_not_fire_on_the_fw_x03_second_hint():
    """Nagovještaj 2 je legitiman međukorak — mjerač ga ne smije oboriti."""
    assert _measure(hint_evidence.FW_X03, 2).verdict == hintsemantics.NOT_PROVEN


def test_phase0_measure_never_runs_on_the_contractual_ladder_top():
    """Treći nagovještaj po ugovoru SMIJE dati rezultat — nikad se ne mjeri."""
    top = _observation(hint_evidence.FW_X03, config.MAX_HINT_LEVEL)
    assert top.serves_hint_ladder_top is True
    assert check_lib.check_hint_no_leak(top).outcome == check_lib.SKIP


# ---------------------------------------------------------------------------
# 3. POZITIVNA KONTROLA — čisto skelovanje ne smije pasti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("evidence,clean_hint", (
    (hint_evidence.FW_X03, hint_evidence.CLEAN_PROPOSITIONAL_HINT),
    (hint_evidence.TR_B1, hint_evidence.CLEAN_PROPOSITIONAL_HINT_TR_B1),
), ids=("FW-X03", "TR-B1"))
def test_clean_propositional_scaffolding_is_not_reported_as_a_disclosure(
        evidence, clean_hint):
    assert _measure(evidence, 1, clean_hint).verdict != hintsemantics.DISCLOSED


def test_clean_scaffolding_still_names_the_lesson_vocabulary():
    """Mjerač ne smije biti zabrana pominjanja pojmova lekcije."""
    clean = hint_evidence.CLEAN_PROPOSITIONAL_HINT
    assert "tvrdnje" in clean and "ako" in clean
    clean_tr = hint_evidence.CLEAN_PROPOSITIONAL_HINT_TR_B1
    assert "prave i ravni" in clean_tr


def test_clean_scaffolding_also_passes_the_existing_leak_oracle():
    """Kontrola je čista i po zatečenom pravilu — razlika je samo u FW-X03."""
    evidence = hint_evidence.FW_X03
    observation = _observation(evidence, 1, hint_evidence.CLEAN_PROPOSITIONAL_HINT)
    assert check_lib.check_hint_no_leak(observation).outcome == check_lib.PASS


# ---------------------------------------------------------------------------
# 4. FW-X03 NAGOVJEŠTAJ 3 — GRANICA DETERMINISTIČKE PROVJERE
# ---------------------------------------------------------------------------

def test_fw_x03_hint3_carries_the_false_intermediate_inference_verbatim():
    assert hint_evidence.FW_X03_FALSE_INFERENCE in hint_evidence.FW_X03.hint(3)


def test_fw_x03_hint3_reaches_the_correct_final_statement():
    """Tačan zaključak — i baš zato je izvod opasan: rezultat ne dokazuje dokaz."""
    hint = hint_evidence.FW_X03.hint(3)
    assert "Konačan rezultat:" in hint
    assert "okomita na svaku pravu iz ravni $\\alpha$ koja prolazi kroz $A$" in hint


def test_every_deterministic_gate_accepts_the_false_fw_x03_hint3():
    """Zapis, terminologija i numerička dosljednost — sve prolazi.

    Ovo je cijeli deterministički prag koji hint prelazi u produkciji
    (`tutor/pipeline.py::_run_text_turn`): mathsafe + terminologija preko
    `_safe_text`, pa `_reject_if_inconsistent`. Geometrijska notacija se ovdje
    ne poziva jer traži `LessonContext`; ona ionako sudi samo o simbolima."""
    hint = hint_evidence.FW_X03.hint(3)
    cleaned, safe = package_preflight.safe_visible_text(hint)
    assert safe is True
    assert find_numeric_inconsistencies(cleaned) == []
    assert terminology.contains_forbidden_term(cleaned) is False


def test_mathcheck_skips_the_false_inference_because_it_carries_no_closed_chain():
    """`mathcheck` po dizajnu preskače sve što nije zatvoren brojevni lanac —
    netačna implikacija je rečenica o geometrijskim objektima."""
    assert find_numeric_inconsistencies(hint_evidence.FW_X03_FALSE_INFERENCE) == []


def test_solution_completeness_check_cannot_grade_the_fw_x03_hint3_proof():
    """`solution_complete` traži REZULTAT, nikad izvod — zamrznut zatečen SKIP."""
    result = check_lib.check_solution_complete(
        _observation(hint_evidence.FW_X03, config.MAX_HINT_LEVEL))
    assert result.outcome == check_lib.SKIP
    assert "final result could not be mechanically located" in result.detail


def test_fw_x03_hint3_verification_strength_is_manual_review_required():
    """Najjača tvrdnja koju sistem o ovom nagovještaju smije izreći."""
    spot = release_contract.blind_spot("false_intermediate_reasoning")
    assert spot.strength == release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED
    assert spot.owner.startswith("nobody")


# ---------------------------------------------------------------------------
# 5. TR-B1 — ISTA SLABOST, DVIJE KAMPANJE RANIJE, I DALJE VAN DOSEGA
# ---------------------------------------------------------------------------

def test_tr_b1_hint1_directs_the_student_at_the_deciding_criterion():
    """Označena opcija paralelnost DEFINIŠE nepostojanjem zajedničke tačke, a
    nagovještaj 1 upravo to nalaže da se provjeri."""
    marked = hint_evidence.TR_B1.marked_option_text
    assert "nemaju zajedničkih tačaka" in marked
    assert "postoji li bilo koja zajednička tačka" in hint_evidence.TR_B1.hint(1)


def test_current_leak_oracle_also_misses_the_tr_b1_hint1():
    evidence = hint_evidence.TR_B1
    assert feedback.leaks_answer(
        evidence.hint(1), evidence.marked_option_text, evidence.marked_option_text,
        task_text=evidence.task_text) is False
    assert check_lib.check_hint_no_leak(_observation(evidence, 1)).outcome == check_lib.PASS


def test_phase0_measure_does_not_reach_the_tr_b1_disclosure():
    """ZAMRZNUTA MJERNA ČINJENICA — mjerač po tačnim tokenima ovo NE dosiže.

    Poslije izuzetka „token već stoji u tekstu zadatka“ označenoj opciji ostaju
    samo tri sadržajna tokena, ispod praga tvrdnje. Uz to je curenje ovdje
    PARAFRAZA u drugom padežu („zajedničkih tačaka“ → „zajednička tačka“), a ni
    Faza 0 ni Faza 2 ne uvode korjenovanje ni sinonime.

    ZAŠTO SE MJERAČ NE „POPRAVLJA“ STEMMINGOM (izmjereno u Fazi 2): skraćivanje
    tokena na zajednički korijen slije `svaku`/`svakoj` i `pravu`/`pravom`, pa
    marked-only skup FW-X03 postaje prazan — „popravka“ bi oborila JEDINI slučaj
    koji mjerač stvarno dokazuje, a TR-B1 i dalje ne bi dosegla.

    FAZA 2 ZATO OVAJ RAZRED ZATVARA KONSTRUKCIJOM, ne mjerenjem: za zadatak čiji
    je odgovor tvrdnja server sam sastavlja nagovještaje 1 i 2 iz šablona koji ne
    prepisuje nijedno slovo iz ponuđenih opcija. Dokaz je u
    `tests/test_phase2_hint_architecture.py`
    (`test_tr_b1_hint1_paraphrase_class_is_unreachable_by_construction`)."""
    result = _measure(hint_evidence.TR_B1, 1)
    assert result.verdict == hintsemantics.NOT_APPLICABLE
    assert result.proposition_tokens < hintsemantics.MIN_PROPOSITION_TOKENS


def test_the_product_measure_agrees_with_the_frozen_phase0_measure():
    """Produkcijski mjerač je VJERAN PORT, ne novi kriterij.

    Faza 2 mjeru seli u proizvod (`matbot.hint_policy`) da je i server i
    evaluator pozivaju doslovno istu. Kalibracija Faze 0 (156 živih turnova,
    1 DISCLOSED, 0 lažnih pozitiva) ostaje na snazi samo ako se dvije
    implementacije poklapaju na SVAKOM zamrznutom dokazu."""
    from matbot import hint_policy

    cases = []
    for evidence in hint_evidence.ALL_EVIDENCE:
        for level, _text in evidence.hints:
            cases.append((evidence, evidence.hint(level)))
    cases.append((hint_evidence.FW_X03, hint_evidence.CLEAN_PROPOSITIONAL_HINT))
    cases.append((hint_evidence.TR_B1, hint_evidence.CLEAN_PROPOSITIONAL_HINT_TR_B1))

    for evidence, text in cases:
        frozen = hintsemantics.measure(text, evidence.marked_option_text,
                                       evidence.distractor_texts, evidence.task_text)
        product = hint_policy.proposition_disclosure(
            text, evidence.marked_option_text, evidence.distractor_texts,
            evidence.task_text)
        assert product.verdict == frozen.verdict, (evidence.scenario_id, text[:40])
        assert product.marked_coverage == pytest.approx(frozen.marked_coverage)
        assert product.marked_only_coverage == pytest.approx(frozen.marked_only_coverage)
        assert product.proposition_tokens == frozen.proposition_tokens


def test_tr_b1_hint2_published_out_of_grade_notation_with_no_gate_to_stop_it():
    """Druga, nezavisna posljedica istog uzroka: nagovještaj ne prolazi kroz
    nijedan ugovor lekcije ni provjeru razreda, pa je lekcija 9. razreda
    osnovne škole dobila parametarski oblik prave i skalarni proizvod."""
    hint = hint_evidence.TR_B1.hint(2)
    assert "\\vec{r}=\\vec{r_0}+t\\vec{v}" in hint
    assert "\\vec{n}\\cdot" in hint
    cleaned, safe = package_preflight.safe_visible_text(hint)
    assert safe is True
    assert find_numeric_inconsistencies(cleaned) == []
    assert terminology.contains_forbidden_term(cleaned) is False


# ---------------------------------------------------------------------------
# 6. MATRICA SLIJEPIH TAČAKA (dijagnostika, tačka 11)
# ---------------------------------------------------------------------------

def test_blind_spot_matrix_covers_every_required_class():
    """Faza 2 dodaje TRI razreda; nijedan zatečeni nije uklonjen.

    `server_composed_top_hint` je nova, JAKA tvrdnja (provenijencija vrha
    ljestvice), `help_out_of_grade_technique` novi izmjereni razred (živi TR-B1
    nagovještaj 2), a `help_branch_coverage` (hardening prije živog talasa)
    zamrzava da oznaka scenarija NIKAD nije dokaz da je grana vožena.

    POPRAVKA VEZANJA ZA OPCIJU (živi H12) dodaje ČETVRTI razred:
    `solution_option_binding` je ZASEBAN od provenijencije upravo zato što je
    H12 imao urednu provenijenciju i netačno slovo opcije.

    POPRAVKA BLOKATORA FINAL40 dodaje PETI i ŠESTI razred, oba o SAMOM
    OBJAVLJENOM ZADATKU (dosad su svi razredi govorili o pomoći ili o
    rješenju): `stem_answer_disclosure` (FW-G03 — tekst zadatka sam izriče
    presudu) i `grade_capability_of_published_task` (FW-G06 — zadatak traži
    zapis koji razred nije upoznao). Oba su OGRANIČENI dokazi i zato nose
    MANUAL_SEMANTIC_REVIEW_REQUIRED."""
    assert set(release_contract.BLIND_SPOT_KEYS) == {
        "value_answer_leak",
        "proposition_answer_leak",
        "server_composed_top_hint",
        "solution_option_binding",
        "false_intermediate_reasoning",
        "final_answer_without_verified_derivation",
        "help_out_of_grade_technique",
        "help_branch_coverage",
        "lesson_semantic_alignment",
        "model_self_reported_checks",
        "stem_answer_disclosure",
        "grade_capability_of_published_task",
        # Geometrijska koherencija premise (ciljani FW-G03): prvi razred
        # koji tvrdi da objavljena KONFIGURACIJA uopšte postoji.
        "geometric_premise_coherence",
    }


def test_every_blind_spot_declares_a_known_verification_strength():
    for spot in release_contract.BLIND_SPOTS:
        assert spot.strength in release_contract.VERIFICATION_STRENGTHS
        assert spot.live_evidence.strip()
        assert spot.owner.strip()


def test_only_value_leak_binding_and_provenance_are_deterministically_verified():
    """Jake tvrdnje su NABROJANE i rastu samo uz dokaz — nikad slučajno.

    Zatečeno stanje (Faza 0): jedina deterministički dokazana klasa bilo je
    VRIJEDNOSNO curenje. Faza 2 dodaje PROVENIJENCIJU vrha ljestvice: server
    tekst sastavlja iz recenzentom odobrenog rješenja, pa se to poredi bajt za
    bajt (`checks.check_hint_top_from_verified_solution`). To NIJE tvrdnja da je
    izvod ispravan — nego da nema svježeg, neprovjerenog izvoda.

    Popravka poslije H12 dodaje TREĆU: VEZANJE ZA OPCIJU. Ona je dokaziva jer je
    skup eksplicitnih MCQ oznaka zatvoren i jer objava ne pušta artefakt koji
    ijednu od njih nosi — dakle konstrukcija, pa mjerenje.

    Sve ostalo (parafraza, ispravnost međukoraka, semantička vjernost lekciji,
    nova tehnika opisana riječima) i dalje traži ručni pregled."""
    verified = {spot.key for spot in release_contract.BLIND_SPOTS
                if spot.strength == release_contract.DETERMINISTICALLY_VERIFIED}
    assert verified == {"value_answer_leak", "server_composed_top_hint",
                        "solution_option_binding"}
    assert release_contract.blind_spot("false_intermediate_reasoning").strength == \
        release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED


def test_option_binding_is_a_separate_property_from_provenance():
    """H12 je bio PASS na provenijenciji i pokvaren na vezanju — dvije stvari.

    Ako neko ikad spoji ova dva razreda u jedan, jedan od njih prestaje da se
    mjeri. Zato su i vlasnik i živi dokaz izričito različiti."""
    provenance = release_contract.blind_spot("server_composed_top_hint")
    binding = release_contract.blind_spot("solution_option_binding")
    assert provenance.owner != binding.owner
    assert provenance.live_evidence != binding.live_evidence
    assert "H12" in binding.live_evidence


def test_value_shaped_leak_oracle_really_still_works():
    """Kontrola da gornja tvrdnja nije prazna: vrijednosno curenje se HVATA."""
    assert feedback.leaks_answer("Rješenje je $x=3$.", "$x=3$", "$x=3$",
                                 task_text="Riješi $(x+2)=5$.") is True


def test_model_self_reported_checks_are_never_evidence():
    spot = release_contract.blind_spot("model_self_reported_checks")
    assert spot.strength == release_contract.NOT_APPLICABLE


def test_identifier_only_checks_can_never_prove_lesson_semantics():
    """`lesson_matches`/`stays_in_lesson` porede ID — PASS nije semantički dokaz."""
    assert release_contract.IDENTIFIER_ONLY_CHECKS == {"lesson_matches", "stays_in_lesson"}
    for name in release_contract.IDENTIFIER_ONLY_CHECKS:
        assert release_contract.strength_for_check(name, "pass") == \
            release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED


def test_a_real_deterministic_check_pass_is_still_verified():
    assert release_contract.strength_for_check("math_safe", "pass") == \
        release_contract.DETERMINISTICALLY_VERIFIED
    assert release_contract.strength_for_check("package_clean", "skip") == \
        release_contract.MANUAL_SEMANTIC_REVIEW_REQUIRED
    assert release_contract.strength_for_check("options_ok", "fail") == \
        release_contract.DETERMINISTICALLY_FAILED
