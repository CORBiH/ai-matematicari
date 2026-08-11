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

from matbot.hint_policy import COMPUTATIONAL as COMPUTATIONAL_CLASS
from matbot.hint_policy import PROPOSITIONAL as PROPOSITIONAL_CLASS
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

# OGRANIČEN (token-nivo) DOKAZ — arhitektonska Faza 2. PASS ovih provjera znači
# „struktura otkrivanja se nije mogla dokazati po TAČNIM tokenima“, a ne
# „nagovještaj semantički ne otkriva odgovor“. Živi TR-B1 dokazuje razliku:
# parafraza u drugom padežu prolazi mjerač. Klasu parafraze zatvara KONSTRUKCIJA
# u proizvodu (`matbot/hint_policy.py`: server sam sastavlja nagovještaje 1 i 2
# kad je odgovor tvrdnja), pa ovaj PASS nikad ne smije nositi spremnost izdanja.
BOUNDED_TOKEN_CHECKS = frozenset({
    "hint_proposition_no_leak",
})

# OGRANIČEN (klasni) DOKAZ — FINAL40 blokatori. Oba mjerača zovu produkcijsku
# funkciju, ali svaka od njih pokriva samo svoju DOKAZIVU KLASU:
#   • `stem_answer_disclosure_safe` sudi samo MCQ IZBORA ENTITETA i uski
#     point→ray most za imenovani ugao; zadatak s rečeničnim opcijama nikad ne
#     može biti oboren, pa PASS ne znači da tekst ne otkriva odgovor;
#   • `curriculum_task_form_consistent` dokazuje samo da nije upotrijebljen
#     ZAPIS van razreda (korijen u 6. razredu, trigonometrija/logaritmi);
#     pedagoški oblik zadatka time nije izmjeren.
# Zato njihov PASS nikad ne nosi spremnost izdanja — ručne rubrike ostaju.
BOUNDED_CLASS_CHECKS = frozenset({
    "stem_answer_disclosure_safe",
    "curriculum_task_form_consistent",
    # Kurikularna politika nad SERVIRANOM pomoći (Faza 3, G-1): dokazuje se
    # isto što i za paket — ZAPIS van razreda i imenovana metodska proza —
    # nikad da je nagovještaj pedagoški primjeren lekciji. Zato je i njegov
    # PASS ograničen dokaz, tačno kao kod mjerača paketa iznad.
    "help_curriculum_policy_consistent",
    # Geometrijska koherencija: dokazuje se TAČNO jedna klasa
    # protivrječnosti (poklopljeni zraci uz nenulti ugao). PASS nikad ne
    # znači da je geometrija zadatka ispravna — samo da ta klasa nije
    # dokazana.
    "geometry_relation_consistent",
})

# Provjere čiji je SKIP po dizajnu čest i znači „ne mogu ništa dokazati“.
# Nabrojane su da izvještaj ne bi smio prikazati njihov izostanak kao uspjeh.
KNOWN_SKIPPING_CHECKS = frozenset({
    "numeric_consistent", "geometry_ok", "task_self_contained",
    "solution_complete", "hint_no_leak", "no_answer_leak", "package_clean",
    "free_text_grading_no_oracle",
    "hint_proposition_no_leak", "hint_top_from_verified_solution",
    "help_has_task_scaffold", "help_notation_in_scope",
    "help_curriculum_policy_consistent",
    "stem_answer_disclosure_safe", "curriculum_task_form_consistent",
    "geometry_relation_consistent",
})


def strength_for_check(name: str, outcome: str) -> str:
    """Jačina dokaza jedne provjere — nikad jača nego što provjera stvarno jest."""
    if outcome == "fail":
        return DETERMINISTICALLY_FAILED
    if outcome == "skip":
        return MANUAL_SEMANTIC_REVIEW_REQUIRED
    if outcome == "pass":
        # PASS provjere koja poredi samo identifikator NIJE semantički dokaz.
        if (name in IDENTIFIER_ONLY_CHECKS or name in BOUNDED_TOKEN_CHECKS
                or name in BOUNDED_CLASS_CHECKS):
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
        owner=("matbot.hint_policy — construction for propositional tasks (server "
               "composes hints 1/2); checks.check_hint_proposition_no_leak measures "
               "the exact-token subset"),
        # Konstrukcija zatvara klasu za propozicione zadatke, ali mjerač i dalje ne
        # dosiže parafrazu, a računski zadaci ostaju modelovi — najjača ISTINITA
        # tvrdnja o proizvoljnom nagovještaju ostaje ručni pregled.
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence="FW-X03 hint 1 (hint_no_leak PASS) and TR-B1 hint 1 (audit: 0 leaks)",
    ),
    BlindSpot(
        key="server_composed_top_hint",
        question="Is the ladder top / full solution a verified artifact, not fresh prose?",
        owner=("matbot.hint_policy.compose_top_hint via matbot.tutor.pipeline; "
               "checks.check_hint_top_from_verified_solution re-derives and compares it"),
        strength=DETERMINISTICALLY_VERIFIED,
        live_evidence="FW-X03 hint 3 was fresh model prose with a false intermediate step",
    ),
    BlindSpot(
        key="solution_option_binding",
        question="Does the student-visible text point at the CURRENT correct option?",
        owner=("matbot.mcq_integrity.option_label_normalization at publication "
               "(matbot.tutor.pipeline._bind_artifact_to_published_options) and "
               "option_binding_failure at help time; "
               "checks.check_solution_option_binding_consistent measures it"),
        # Konstrukcija je deterministička i potpuna za EKSPLICITNE MCQ oznake:
        # objava ne pušta artefakt koji imenuje slovo opcije, pa kontradikcija
        # ne može ni nastati. Mjerač poredi isti zatvoreni skup oznaka.
        strength=DETERMINISTICALLY_VERIFIED,
        live_evidence=("H12 (phase2_hint_live_510b1be): verified artifact said "
                       "„opcija a“ while the server committed option c"),
    ),
    BlindSpot(
        key="false_intermediate_reasoning",
        question="Is every inference inside a hint or solution mathematically sound?",
        owner=("nobody for model-authored hint levels 1-2 — mathcheck skips any "
               "expression carrying a variable; the ladder top no longer contains "
               "model-authored reasoning at all"),
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence="FW-X03 hint 3 published a false in-plane perpendicularity implication",
    ),
    BlindSpot(
        key="final_answer_without_verified_derivation",
        question="Does a correct final result prove the published derivation?",
        owner=("checks.check_solution_complete locates the result, never the proof; "
               "provenance is now proven separately by "
               "checks.check_hint_top_from_verified_solution"),
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence="FW-X03 step 3 recorded solution_complete=SKIP while the proof was false",
    ),
    BlindSpot(
        key="help_out_of_grade_technique",
        question="Does a hint introduce machinery absent from the approved task?",
        owner=("two independent owners, both production functions the evaluator "
               "calls directly. (1) matbot.hint_policy.out_of_scope_notation_codes "
               "via matbot.tutor.pipeline; checks.check_help_notation_in_scope. It "
               "proves only that certain advanced NOTATION was introduced that the "
               "approved task does not already use (proportionality to the TASK); it "
               "proves nothing about semantic grade appropriateness. (2) Phase 3: "
               "matbot.practice_policy.text_policy_failures now also runs on "
               "MODEL-authored help in matbot.tutor.pipeline._finalize_help_answer, "
               "which replaces the offending text with the server scaffold instead "
               "of failing the turn; checks.check_help_curriculum_policy_consistent "
               "resolves the SAME policy from the same server-owned lesson context "
               "(capability of the GRADE). Together they close out-of-grade "
               "NOTATION and named method prose on every help surface. For "
               "propositional help the class is eliminated by construction "
               "(server-composed hints 1-2, verified-artifact hint 3). Neither owner "
               "closes the semantic class: whether a MODEL-authored computational "
               "hint's reasoning actually suits the lesson stays a manual "
               "live-review duty"),
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence=("TR-B1 hint 2 served a parametric line and a dot product in "
                       "grade 9; FW-G06 proved the same class for the package, and "
                       "until Phase 3 the identical grade-6 radical was forbidden by "
                       "the help prompt while no server gate measured the hint"),
    ),
    BlindSpot(
        key="help_branch_coverage",
        question="Did the campaign actually exercise BOTH help ladders?",
        owner=("checks.task_class:<class> records the server-selected class AFTER "
               "publication; release_contract.hint_branch_coverage counts it. A "
               "scenario tag is never evidence — the model owns the generated "
               "answer shape, so a miss is a coverage gap, never a defect"),
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence=("wave F6H labels lessons recognition/computational, but the "
                       "class is only knowable from the published options"),
    ),
    BlindSpot(
        key="stem_answer_disclosure",
        question="Does the TASK TEXT itself state which option is correct?",
        owner=("matbot.stem_disclosure.stem_answer_disclosure at draft preflight, "
               "at the reviewer-final invariant and at publication "
               "(matbot.tutor.pipeline._validate_task_server_side); "
               "checks.check_stem_answer_disclosure_safe calls the SAME function. "
               "An ADDITIONAL, non-deterministic layer sits above it: the "
               "Reviewer must report `stem_requires_student_reasoning`, which "
               "matbot.tutor.reviewer_authority lists as MODEL_ONLY_BLOCKING, so "
               "a false value forbids both `approve` and `correct`. That is a "
               "model judgement, never server proof: it exists precisely because "
               "the PARAPHRASED class is not deterministically provable, and it "
               "never replaces the bounded detectors"),
        # Konstrukcija ne postoji — model piše tekst — pa je ovo DETEKCIJA, i
        # to samo u klasi izbora entiteta plus uskom point→ray mostu za
        # eksplicitno imenovani ugao. Zadatak čije su opcije rečenice („Koja
        # tvrdnja je tačna…“) ostaje potpuno nepokriven.
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence=("FW-G03 (final40 2fe5636): the stem said ray BD lies "
                       "between BA and BC, then asked which ray lies between "
                       "BA and BC; no_leak PASS, reviewer decision=correct. "
                       "Recurred PARAPHRASED on 7c13eb9 (M-FW-G03): 'zraka BD "
                       "prolazi izmedju BA i BC' answering 'koji krak dijeli "
                       "ugao' — every bounded detector correctly returned "
                       "NOT_PROVEN, which is why the class moved to the "
                       "Reviewer instead of growing another regex"),
    ),
    BlindSpot(
        key="grade_capability_of_published_task",
        question="Does the published task need machinery this GRADE has not met?",
        owner=("matbot.practice_policy — resolved per (grade, lesson) and enforced "
               "on every visible surface by text_policy_failures at draft "
               "preflight and at publication; "
               "checks.check_curriculum_task_form_consistent resolves the SAME "
               "policy from the same server-owned lesson context"),
        # Dokazuje se ZAPIS (korijen prije 8. razreda, trigonometrija/logaritmi),
        # nikad pedagoški oblik zadatka. „Zadatak je pojmovno prekomplikovan za
        # lekciju“ ostaje ručna presuda.
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence=("FW-G06 (final40 c04d2a1, c17538a and 2fe5636 — the same "
                       "defect three campaigns running): grade-6 lesson "
                       "„Simetrala ugla i konstrukcija“ published an "
                       "equilateral-triangle inradius task marked $\\sqrt{3}$"),
    ),
    BlindSpot(
        key="geometric_premise_coherence",
        question="Does the published task assert a configuration that cannot exist?",
        owner=("matbot.geometrycheck.geometry_relation_contradictions at draft "
               "preflight and at publication via find_geometry_issues "
               "(matbot.tutor.pipeline._reject_if_geometry_invalid); "
               "checks.check_geometry_relation_consistent calls the SAME function"),
        # Egzaktna Euklidska činjenica, ali samo za JEDNU klasu premise;
        # „je li zrak stvarno unutar ugla" i svaka druga konfiguracija ostaju
        # potpuno nedokazane bez slike.
        strength=MANUAL_SEMANTIC_REVIEW_REQUIRED,
        live_evidence=("FW-G03 (targeted b9151fc): the stem put rays BD/BE/BF/BG "
                       "ON arm BA and then gave them nonzero angles to BA; "
                       "reviewer decision=approve, geometry_ok=SKIP"),
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


# ---------------------------------------------------------------------------
# POKRIVENOST GRANA POMOĆI (hardening prije živog talasa, Problem C)
# ---------------------------------------------------------------------------
# Oznaka scenarija („recognition“ / „computational“) NIJE dokaz da je grana
# vožena: oblik opcija bira model pri generisanju, pa se klasa zna TEK poslije
# objave. Ova funkcija čita SNIMLJENE rezultate provjere `task_class:<klasa>` i
# broji koliko je grana STVARNO izvršeno. Ne poziva model i ne mijenja nijedan
# postojeći izlaz kampanje.
#
# Scenario se broji SAMO kad je cijela ljestvica poslužena: klasa se poklopila i
# vrh ljestvice je dokazano serverska kompozicija provjerenog artefakta.
REQUIRED_PROPOSITIONAL_LADDERS = 3
REQUIRED_COMPUTATIONAL_LADDERS = 3
REQUIRED_SYMBOLIC_PROPOSITIONAL = 1


@dataclass(frozen=True)
class BranchCoverage:
    propositional: tuple = ()
    computational: tuple = ()
    symbolic_propositional: tuple = ()
    gaps: tuple = ()
    complete: bool = False
    notes: tuple = ()


def _passed_checks(record) -> set:
    names = set()
    for turn in (record or {}).get("turns") or ():
        for result in turn.get("check_results") or ():
            if (result.get("outcome") or "") == "pass":
                names.add(result.get("name") or "")
    return names


def _skipped_task_class_labels(record) -> list:
    labels = []
    for turn in (record or {}).get("turns") or ():
        for result in turn.get("check_results") or ():
            name = result.get("name") or ""
            if name.startswith("task_class:") and (result.get("outcome") or "") == "skip":
                labels.append(name)
    return labels


def hint_branch_coverage(records) -> BranchCoverage:
    """Koje su grane ljestvice pomoći ŽIVO izvršene, po snimljenim zapisima."""
    propositional, computational, symbolic, gaps, notes = [], [], [], [], []
    for record in records or ():
        scenario_id = (record or {}).get("id") or "?"
        passed = _passed_checks(record)
        served_top = "hint_top_from_verified_solution" in passed
        if f"task_class:{PROPOSITIONAL_CLASS}" in passed and served_top:
            propositional.append(scenario_id)
            if "symbolic_marked_answer" in passed:
                symbolic.append(scenario_id)
        elif f"task_class:{COMPUTATIONAL_CLASS}" in passed and served_top:
            computational.append(scenario_id)
        else:
            for label in dict.fromkeys(_skipped_task_class_labels(record)):
                gaps.append(f"{scenario_id}:{label}")
            if not _skipped_task_class_labels(record) and (
                    f"task_class:{PROPOSITIONAL_CLASS}" in passed
                    or f"task_class:{COMPUTATIONAL_CLASS}" in passed):
                gaps.append(f"{scenario_id}: class matched but the ladder top was "
                            "not proven server-composed")

    complete = (len(propositional) >= REQUIRED_PROPOSITIONAL_LADDERS
                and len(computational) >= REQUIRED_COMPUTATIONAL_LADDERS
                and len(symbolic) >= REQUIRED_SYMBOLIC_PROPOSITIONAL)
    if not complete:
        notes.append(
            "help branch coverage incomplete: Phase 2 needs at least "
            f"{REQUIRED_PROPOSITIONAL_LADDERS} propositional and "
            f"{REQUIRED_COMPUTATIONAL_LADDERS} computational full ladders plus "
            f"{REQUIRED_SYMBOLIC_PROPOSITIONAL} short-symbolic propositional task; "
            "a scenario tag is never evidence")
    return BranchCoverage(
        propositional=tuple(dict.fromkeys(propositional)),
        computational=tuple(dict.fromkeys(computational)),
        symbolic_propositional=tuple(dict.fromkeys(symbolic)),
        gaps=tuple(dict.fromkeys(gaps)),
        complete=complete,
        notes=tuple(notes),
    )


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
