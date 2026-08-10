"""UGOVOR ODLUKE O IZDANJU — koliko je nešto STVARNO dokazano (Faza 0).

ZAŠTO POSTOJI. Kampanjski izvještaj zna razlikovati PASS/FAIL/REVIEW i klasu
ishoda (`classify.py`), ali nigdje ne stoji zapisano koliko JAKA je tvrdnja iza
zelene provjere. Živi ishod FW-X03: četrnaest provjera PASS, jedna SKIP, nula
FAIL — a objavljeni nagovještaj 1 je otkrio odgovor i nagovještaj 3 je objavio
netačan izvod. Oba nalaza su došla iz ručnog čitanja.

Dva strukturna razloga, oba zamrznuta ovdje kao podaci:

  1. Provjera koja poredi SAMO IDENTIFIKATOR (`lesson_matches`,
     `stays_in_lesson` porede `topic_id` i `session_mode`) ne dokazuje
     semantičko svojstvo po kojem je nazvana. Njen PASS nikad ne smije značiti
     „zadatak ispituje baš ovu lekciju“.
  2. `checks.*` recenzenta su TVRDNJE MODELA (vidi
     `matbot/tutor/reviewer_authority.py`) i nikad nisu dokaz.

Ovaj modul NE mijenja nijedan postojeći izvještaj ni klasifikaciju: on je čista
funkcija nad već snimljenim zapisima, uvezena samo iz testova ove faze.
Postoji da buduća automatska odluka o izdanju NE MOŽE tiho predstaviti
nedokazano kao dokazano.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tools.practice_eval import classify

# --- jačina dokaza ---------------------------------------------------------
DETERMINISTICALLY_VERIFIED = "DETERMINISTICALLY_VERIFIED"
DETERMINISTICALLY_FAILED = "DETERMINISTICALLY_FAILED"
MANUAL_SEMANTIC_REVIEW_REQUIRED = "MANUAL_SEMANTIC_REVIEW_REQUIRED"
NOT_APPLICABLE = "NOT_APPLICABLE"

VERIFICATION_STRENGTHS = (
    DETERMINISTICALLY_VERIFIED,
    DETERMINISTICALLY_FAILED,
    MANUAL_SEMANTIC_REVIEW_REQUIRED,
    NOT_APPLICABLE,
)

# Provjere koje porede ISKLJUČIVO identifikator/plumbing. Njihov PASS dokazuje
# da se stanje nije preselilo na drugu lekciju — nikad da je sadržaj u lekciji.
IDENTIFIER_ONLY_CHECKS = frozenset({
    "lesson_matches",
    "stays_in_lesson",
})

# Provjere čiji je SKIP po dizajnu čest i znači „ne mogu ništa dokazati“.
# Nabrojane su da izvještaj ne bi smio prikazati njihov izostanak kao uspjeh.
KNOWN_SKIPPING_CHECKS = frozenset({
    "numeric_consistent", "geometry_ok", "task_self_contained",
    "solution_complete", "hint_no_leak", "no_answer_leak", "package_clean",
    "free_text_grading_no_oracle",
})


def strength_for_check(name: str, outcome: str) -> str:
    """Jačina dokaza jedne provjere — nikad jača nego što provjera stvarno jest."""
    if outcome == "fail":
        return DETERMINISTICALLY_FAILED
    if outcome == "skip":
        return MANUAL_SEMANTIC_REVIEW_REQUIRED
    if outcome == "pass":
        # PASS provjere koja poredi samo identifikator NIJE semantički dokaz.
        if name in IDENTIFIER_ONLY_CHECKS:
            return MANUAL_SEMANTIC_REVIEW_REQUIRED
        return DETERMINISTICALLY_VERIFIED
    return NOT_APPLICABLE


# ---------------------------------------------------------------------------
# MATRICA SLIJEPIH TAČAKA (arhitektonska dijagnostika, tačka 11)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BlindSpot:
    """Jedno svojstvo ispravnosti i najjača tvrdnja koju evaluator o njemu ima."""

    key: str
    question: str
    owner: str                  # ko to danas stvarno mjeri (ili „nobody“)
    strength: str               # najjača moguća tvrdnja DANAS
    live_evidence: str          # živi zapis koji to dokazuje


BLIND_SPOTS = (
    BlindSpot(
        key="value_answer_leak",
        question="Does the reply state the committed answer as a value?",
        owner="feedback.leaks_answer via checks.check_hint_no_leak",
        strength=DETERMINISTICALLY_VERIFIED,
        live_evidence="B53 (postFinalFixes): committed $x=3$ restated in prose was caught",
    ),
    BlindSpot(
        key="proposition_answer_leak",
        question="Does the reply restate the marked PROPOSITION for hint 1/2?",
        owner="nobody in production; tools.practice_eval.hintsemantics measures a subset",
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence="FW-X03 hint 1 (hint_no_leak PASS) and TR-B1 hint 1 (audit: 0 leaks)",
    ),
    BlindSpot(
        key="false_intermediate_reasoning",
        question="Is every inference inside a hint or solution mathematically sound?",
        owner="nobody — mathcheck skips any expression carrying a variable",
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence="FW-X03 hint 3 published a false in-plane perpendicularity implication",
    ),
    BlindSpot(
        key="final_answer_without_verified_derivation",
        question="Does a correct final result prove the published derivation?",
        owner="checks.check_solution_complete — locates the result, never the proof",
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence="FW-X03 step 3 recorded solution_complete=SKIP while the proof was false",
    ),
    BlindSpot(
        key="lesson_semantic_alignment",
        question="Does the published task exercise the SELECTED lesson's own skill?",
        owner="checks.check_lesson_matches / check_stays_in_lesson compare IDs only",
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence="38/40 FINAL40 scenarios carry the never-auto-graded lesson_alignment rubric",
    ),
    BlindSpot(
        key="model_self_reported_checks",
        question="Do the Reviewer's own checks.* prove anything about the package?",
        owner="matbot.tutor.reviewer_authority — self-report is explicitly never proof",
        strength=NOT_APPLICABLE,
        live_evidence="FW-D04: decision=correct while inside_lesson=false killed the turn",
    ),
)

BLIND_SPOT_KEYS = tuple(spot.key for spot in BLIND_SPOTS)


def blind_spot(key: str) -> BlindSpot:
    return next(spot for spot in BLIND_SPOTS if spot.key == key)


# ---------------------------------------------------------------------------
# ODLUKA O IZDANJU
# ---------------------------------------------------------------------------

# Klase ishoda koje same po sebi NE mogu nositi spremnost za izdanje.
_NON_RELEASE_OUTCOMES = frozenset({
    classify.PRODUCT_CORRECTNESS_FAILURE,
    classify.COVERAGE_GAP,
    classify.SAFE_FAIL_CLOSED,
    classify.HARNESS_INVALID_SCENARIO,
    classify.CASCADE_ONLY,
    classify.EVALUATOR_MISCLASSIFICATION,
    classify.INFRA_SDK,
    classify.TIMEOUT,
})


@dataclass(frozen=True)
class ReleaseVerdict:
    ready: bool
    blockers: tuple = ()
    unproven: tuple = ()
    notes: tuple = field(default_factory=tuple)


def scenario_strengths(record) -> dict:
    """Jačina dokaza po provjeri za jedan snimljen scenario."""
    strengths = {}
    for turn in (record or {}).get("turns") or ():
        for result in turn.get("check_results") or ():
            name = result.get("name") or ""
            current = strength_for_check(name, result.get("outcome") or "")
            previous = strengths.get(name)
            # Najslabija tvrdnja pobjeđuje: jedan SKIP poništava raniji PASS.
            order = {DETERMINISTICALLY_FAILED: 0, MANUAL_SEMANTIC_REVIEW_REQUIRED: 1,
                     NOT_APPLICABLE: 2, DETERMINISTICALLY_VERIFIED: 3}
            if previous is None or order[current] < order[previous]:
                strengths[name] = current
    return strengths


def release_verdict(records, scenarios=None, manual_blockers=()) -> ReleaseVerdict:
    """Je li kampanja spremna za izdanje?

    Spremna je SAMO kad je svaki scenario `CLEAN`, nijedna provjera nije
    ostavila `MANUAL_SEMANTIC_REVIEW_REQUIRED`, i nijedan ručni blokator nije
    prijavljen. Zelene niskonivovske provjere same po sebi nikad nisu dovoljne
    — to je upravo ono što je FW-X03 propustio."""
    scenarios = scenarios or {}
    blockers, unproven, notes = [], [], []

    for record in records or ():
        scenario_id = (record or {}).get("id") or "?"
        classification = classify.classify(record, scenarios.get(scenario_id))
        outcome = classification["outcome_class"]
        if outcome in _NON_RELEASE_OUTCOMES:
            blockers.append(f"{scenario_id}: {outcome}")
        for name, strength in sorted(scenario_strengths(record).items()):
            if strength == MANUAL_SEMANTIC_REVIEW_REQUIRED:
                unproven.append(f"{scenario_id}:{name}")
            elif strength == DETERMINISTICALLY_FAILED:
                blockers.append(f"{scenario_id}: {name} failed deterministically")

    for entry in manual_blockers or ():
        blockers.append(f"manual: {entry}")

    if unproven:
        notes.append("unproven checks never count as verified — the campaign "
                     "cannot be declared release ready on green low-level checks alone")
    return ReleaseVerdict(
        ready=not blockers and not unproven,
        blockers=tuple(dict.fromkeys(blockers)),
        unproven=tuple(dict.fromkeys(unproven)),
        notes=tuple(notes),
    )
