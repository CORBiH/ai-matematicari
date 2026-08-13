"""Manual, commit-bound real-model release gate for MAT-BOT behavior.

This program is deliberately never invoked by the application or the hook.
It uses the real Practice entrypoint and the existing ``CountingLLM`` wrapper,
but only after a human starts it in a clean, committed worktree with an API key
already inherited by that shell.  It never loads ``.env`` and never prints a
secret.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "scratchpad" / "live_release_gate"
# Kapacitetna ekspanzija: lekcije oblasti djeljivosti dobijaju blocking
# semantičke ugovore u talasima (6-03-004 u prvom, 6-03-002 u Batch #2), pa
# model-scenarije kapije nosi lekcija koja je KLASIFIKATOROM ostala na
# model-putu. `_require_model_routed_plan` ispod pada GLASNO pri gradnji
# plana — prije ijednog SDK poziva — ako neka buduća aktivacija i ovu
# lekciju učini determinističkom.
#
# Batch #4: i „Tekstualni zadaci iz djeljivosti“ (6-03-010) je aktivirana
# (structured_word_problem, nula poziva), pa model-scenarije kapije od ove
# faze nosi POJMOVNA lekcija razlomaka — klasifikovana CONCEPTUAL_ONLY,
# dakle trajno na model-putu, s punim tokom (svjež/teže/lakše/hint/rješenje).
CORE_MODEL = ("6-04-001", 6)
# Lekcija koja i dalje ide DETERMINISTIČKIM K1/K3 putem (nema semantički
# ugovor). Ranije je ovdje stajala 6-04-009, ali ona od Faze 4B ide
# semantičkim dvopozivnim putem — vidi CORE_SEMANTIC.
CORE_CONTRACT = ("6-04-005", 6)
# Lekcija s AKTIVNIM semantičkim ugovorom (porodica fraction_arithmetic_direct).
CORE_SEMANTIC = ("6-04-009", 6)
REQUIRED_SCENARIO_COUNT = 14
# Faza 4H: semantic_fresh i semantic_harder (lekcija porodice
# fraction_arithmetic_direct) sada idu DETERMINISTIČKOM strategijom — nula
# SDK poziva po scenariju, i gate to IZRIČITO dokazuje (expected_calls=0,
# stroga jednakost po scenariju). Plafon je tada pao 23 → 19.
#
# ŽIVI NALAZ (zvanična kapija, scenario `first_hint`): kapija je pala s
# „expected 1 SDK call, actual 0“ — a aplikacija NIJE bila u regresiji. Model
# poziva u kapiji je bio stariji od SERVER-VLASNIČKE POMOĆI (Faza 2):
#   • `full_solution_request` je UVIJEK serverski → 0 poziva;
#   • prvi hint je serverski za klasu TVRDNJE i na vrhu ljestvice → 0 poziva,
#     a modelu ostaje samo RAČUNSKA ljestvica 1–2 → 1 poziv.
# Oba se znaju PRIJE ijednog poziva (`pipeline._help_author`), pa kapija svoje
# očekivanje IZVODI iz iste politike umjesto da ga pogađa. Zbog toga:
#   • `full_solution` očekuje TAČNO 0 (statički, dokazano prvom granom politike);
#   • `first_hint` očekuje 0 ILI 1 — ali VRIJEDNOST SE ZAMRZAVA PRIJE turna i
#     poredi se strogom jednakošću; „bilo šta od toga dvoga“ nije prihvaćeno;
#   • statički zbir je 17, pa je maksimalan dostižan plan 18, ne 19.
# Tutorska klasa = PRVI poziv turna (bilo koja ruta). Recenzentska = POPRAVAK.
_TUTOR_STAGE_NAMES = frozenset({"fast_turn", "practice_turn", "tutor_turn"})
_REVIEWER_STAGE_NAMES = frozenset({"reviewer_turn", "lesson_fidelity_turn"})
# Arhitektonska granica iz CLAUDE.md pravilo 4 — nikad se ne podiže.
_MAX_CALLS_PER_TURN = 2
# Brza ruta troši 1 poziv po scenariju izrade zadatka; recenzentski popravak je
# uslovan i dodaje najviše još jedan. Plafon zato pokriva NAJGORI dozvoljeni
# ishod (svaki modelski scenario eskalirao), a tačnost čuva ugovor po scenariju
# iznad. Vrijednost se DOKAZUJE iz plana (`max_planned_calls`), ne pogađa —
# provjera ispod pada zatvoreno ako se plan i plafon raziđu.
SDK_CALL_CEILING = 21
# Zbir svih scenarija čiji je ugovor statički (bez `first_hint`).
_STATIC_PLAN_CALLS = 10
# Sentinel: očekivanje se izvodi iz serverske politike pomoći PRIJE turna.
DERIVED_FROM_HELP_POLICY = None
# UI radnja koju kapija šalje za scenarije pomoći — ista mapa koju koristi
# produkcijski put (`pipeline._UI_ACTION_BY_INTENT`), samo za ova dva intenta.
_HELP_UI_ACTION = {"hint_request": "hint_request",
                   "solution_request": "full_solution_request"}
sys.path.insert(0, str(ROOT))

from matbot import config, release_config, feedback, mathsafe, mcq_integrity, practice  # noqa: E402
from matbot.contracts import registry as contract_registry  # noqa: E402
from matbot.semantics import contracts as semantic_contracts  # noqa: E402
from matbot.llm import OpenAIPracticeLLM  # noqa: E402
from matbot.session_store import SessionStore  # noqa: E402
from matbot.topics import lesson_info  # noqa: E402

# JEDAN izvor istine o release konfiguraciji — isti koji koristi i deploy
# provjera (matbot/release_config.py). Produkcija je jednom tiho radila bez ove
# dvije zastavice dok su gate-ovi mjerili obje uključene.
REQUIRED_PIPELINE = release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"]
REQUIRED_DIFFICULTY_LEVELS = release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_DIFFICULTY_LEVELS"]

from scratchpad.run_difficulty_canary import (  # noqa: E402
    CanaryReport, CountingLLM, SDKCallBudgetExceeded, Scenario, _LogCapture,
    _has_disallowed_control_character, _run_one_turn,
)


class GateRefusal(RuntimeError):
    """A precondition failed before any SDK delegation was possible."""


@dataclass(frozen=True)
class GateScenario:
    role: str
    scenario: Scenario
    # None = `DERIVED_FROM_HELP_POLICY`: tačna vrijednost se izvodi iz
    # serverske politike pomoći neposredno PRIJE turna (nikad poslije njega —
    # to bi bilo kružno i kapija ne bi hvatala višak/manjak poziva).
    expected_calls: int | None


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    if completed.returncode:
        raise GateRefusal("Git metadata cannot be resolved for the live release gate.")
    return completed.stdout.strip()


def _head_metadata() -> tuple[str, str]:
    sha = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    if not sha or not tree:
        raise GateRefusal("HEAD or its tree hash is unavailable.")
    return sha, tree


def _require_live_preconditions() -> tuple[str, str]:
    if _git("status", "--porcelain"):
        raise GateRefusal("Worktree is dirty; commit or stash changes before the live release gate.")
    if not bool((os.environ.get("OPENAI_API_KEY", "") or "").strip()):
        raise GateRefusal("OPENAI_API_KEY is not present in this shell environment.")
    pipeline = (os.environ.get("MATBOT_PRACTICE_PIPELINE", "") or "").strip().lower()
    if pipeline != REQUIRED_PIPELINE:
        raise GateRefusal("MATBOT_PRACTICE_PIPELINE must be exactly universal_two_call.")
    if not config.practice_difficulty_levels_enabled():
        raise GateRefusal("MATBOT_PRACTICE_DIFFICULTY_LEVELS must be exactly enabled.")
    if not isinstance(config.AI_TIMEOUT_S, (int, float)) or config.AI_TIMEOUT_S <= 0:
        raise GateRefusal("AI_TUTOR_TIMEOUT runtime configuration is invalid.")
    return _head_metadata()


def _all_lessons_for_grade(grade: int) -> list[dict]:
    data_path = ROOT / "data" / "topics.json"
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateRefusal("Canonical curriculum data is unavailable.") from exc
    rows = payload.get("grades", {}).get(str(grade), {}).get("lessons", [])
    return [row for row in rows if isinstance(row, dict) and row.get("id") and row.get("title")]


def _select_rotating_lesson(grade: int, commit_sha: str) -> tuple[str, int]:
    """Choose a reproducible eligible non-contract lesson from the tested SHA."""
    candidates = []
    for row in _all_lessons_for_grade(grade):
        lesson_id = str(row["id"])
        title = str(row["title"])
        if lesson_id in {CORE_MODEL[0], CORE_CONTRACT[0], CORE_SEMANTIC[0]}:
            continue
        if contract_registry.contract_for(lesson_id) is not None:
            continue
        # Kapacitetna ekspanzija: lekcija s blocking semantičkim ugovorom ide
        # determinističkom strategijom (0 poziva) — rotirajući scenario mora
        # ostati STVARNI model-scenario s tačno 2 poziva.
        if semantic_contracts.contract_for(lesson_id) is not None:
            continue
        if grade == 9 and not any(word in title.lower() for word in (
                "jednačin", "jednacin", "sistem", "tekstual", "algebr")):
            continue
        candidates.append(lesson_id)
    if not candidates:
        raise GateRefusal(f"No eligible canonical non-contract Grade-{grade} lesson exists.")
    candidates.sort()
    offset = int(commit_sha[:16], 16) % len(candidates)
    return candidates[offset], grade


def _routes_deterministically(lesson_id: str) -> bool:
    """Server-vlasnička činjenica: da li strukturisana izrada zadatka ove
    lekcije ide determinističkom strategijom (blocking ugovor + registrovan
    generator koji parametre POTPUNO podržava)."""
    from matbot import deterministic as deterministic_registry

    contract = semantic_contracts.contract_for(lesson_id)
    if contract is None or not contract.blocking:
        return False
    module = deterministic_registry.GENERATORS.get(contract.family_id)
    return module is not None and module.supports(dict(contract.parameters))


def _require_model_routed_plan(plan):
    """Model-scenario na determinističkoj lekciji bi IZMJERIO pogrešan broj
    poziva tek usred žive kapije. Zato se ruta svakog scenarija dokazuje PRIJE
    ijednog SDK poziva: očekivani pozivi > 0 traže model-put, a tačno 0 poziva
    traži deterministički put."""
    for item in plan:
        if item.scenario.intent in _HELP_UI_ACTION:
            # POMOĆ NIJE IZRADA ZADATKA. Njen broj poziva određuje serverska
            # politika pomoći nad OBJAVLJENIM zadatkom, ne ruta lekcije: na
            # model-lekciji `full_solution` legitimno ima 0 poziva. Ranije je
            # ovdje vrijedilo „0 poziva ⇒ deterministička lekcija“, pa bi
            # ispravan plan bio odbijen prije ijednog poziva.
            continue
        deterministic = _routes_deterministically(item.scenario.lesson_id)
        if item.expected_calls > 0 and deterministic and                 item.scenario.path != "contract":
            raise GateRefusal(
                f"Gate scenario '{item.role}' expects model calls but its "
                "lesson now routes deterministically; retarget the scenario.")
        if item.expected_calls == 0 and not deterministic:
            raise GateRefusal(
                f"Gate scenario '{item.role}' expects zero calls but its "
                "lesson no longer routes deterministically.")


def resolve_expected_calls(gate: GateScenario, session_before: dict) -> tuple[int, str]:
    """Tacan broj poziva OVOG scenarija — ZAMRZNUT PRIJE turna.

    Za scenarije pomoci pita se ISTA runtime politika koju produkcija koristi
    za rutiranje (`pipeline._help_author`), pa kapija ne drzi drugu kopiju
    politike koja bi se mogla razici s njom. Nikad se ne gleda ishod turna:
    ocekivanje izvedeno iz rezultata ne bi moglo pasti."""
    if gate.expected_calls is not None:
        return gate.expected_calls, "static_plan"
    ui_action = _HELP_UI_ACTION.get(gate.scenario.intent, "")
    if not ui_action:
        raise GateRefusal(
            f"Gate scenario '{gate.role}' has no static call contract and is "
            "not a help scenario; its expected call count cannot be derived.")
    from matbot.tutor import pipeline as tutor_pipeline
    from matbot import hint_policy

    author = tutor_pipeline._help_author(session_before or {}, ui_action)
    expected = 0 if author == hint_policy.SERVER else 1
    return expected, f"help_policy:{author}"


def max_planned_calls(plan) -> int:
    """Gornja granica plana: izvedeni scenariji pomoci trose najvise 1 poziv.

    UKLJUCUJE i uslovni recenzentski popravak: svaki scenario koji uopste zove
    model smije dobiti najvise jos jedan poziv kad preflight nadje nalaz. To je
    NAJGORI DOZVOLJENI ishod, ne ocekivanje — tacnost cuva ugovor po scenariju
    (`_scenario_errors`), koji trazi da drugi poziv bude RECENZENTSKI, nikad
    ponovljeni tutorski."""
    total = 0
    for item in plan:
        planned = 1 if item.expected_calls is None else item.expected_calls
        # Pomoć nikad ne stiže do recenzenta (nema paketa za recenziju), pa joj
        # se dodatak ne priznaje — plafon ostaje najtješnji koji arhitektura
        # dopušta.
        escalatable = planned > 0 and item.scenario.intent not in _HELP_UI_ACTION
        total += planned + (1 if escalatable else 0)
    return total


def build_release_gate_plan(commit_sha: str) -> tuple[GateScenario, ...]:
    """The exact 14-scenario plan (17 static + derived first hint), pure
    except curriculum lookup."""
    grade7 = _select_rotating_lesson(7, commit_sha)
    grade8 = _select_rotating_lesson(8, commit_sha)
    grade9 = _select_rotating_lesson(9, commit_sha)

    def scenario(name, lesson, request, session, message, calls, *, path="non_contract",
                 requires=None, interaction="task_generation", intent="", role):
        return GateScenario(
            role,
            Scenario(name, lesson[0], lesson[1], path, request, session, message,
                     requires, interaction, intent),
            calls,
        )

    plan = (
        scenario("release_gate_core_fresh_level1", CORE_MODEL, "", "release-core",
                 "Daj mi zadatak.", 1, role="fresh_level1"),
        scenario("release_gate_correct_committed_choice", CORE_MODEL, "", "release-core", "", 1,
                 requires=1, interaction="correct_choice", role="correct_choice"),
        scenario("release_gate_core_harder_level1_to_2", CORE_MODEL, "harder",
                 "release-core", "Daj mi teži zadatak.", 1, requires=1, role="harder_level2"),
        # Prvi hint: 0 kad je zadatak klase TVRDNJE (server sastavlja), 1 kad je
        # RAČUNSKI. Vrijednost izvodi `resolve_expected_calls` iz sesije prije turna.
        scenario("release_gate_first_hint", CORE_MODEL, "", "release-core", "Ne znam.",
                 DERIVED_FROM_HELP_POLICY,
                 requires=2, interaction="hint", intent="hint_request", role="first_hint"),
        # Puno rješenje je UVIJEK serverska kompozicija verifikovanog artefakta.
        scenario("release_gate_full_solution", CORE_MODEL, "", "release-core", "Uradi ga ti.", 0,
                 requires=2, interaction="full_solution", intent="solution_request", role="full_solution"),
        scenario("release_gate_core_easier_level2_to_1", CORE_MODEL, "easier",
                 "release-core", "Daj mi lakši zadatak.", 1, requires=2, role="easier_level1"),
        scenario("release_gate_core_new_same_level", CORE_MODEL, "", "release-core",
                 "Daj mi novi zadatak.", 1, requires=1, role="same_level_new"),
        scenario("release_gate_contract_fresh", CORE_CONTRACT, "", "release-contract", "Daj mi zadatak.", 1,
                 path="contract", role="contract_fresh"),
        scenario("release_gate_contract_harder", CORE_CONTRACT, "harder", "release-contract",
                 "Daj mi teži zadatak.", 1, path="contract", requires=1, role="contract_harder"),
        # Faza 4H: deterministička strategija — TAČNO nula poziva po scenariju.
        scenario("release_gate_semantic_fresh", CORE_SEMANTIC, "", "release-semantic",
                 "Daj mi zadatak.", 0, role="semantic_fresh"),
        scenario("release_gate_semantic_harder", CORE_SEMANTIC, "harder", "release-semantic",
                 "Daj mi teži zadatak.", 0, requires=1, role="semantic_harder"),
        scenario("release_gate_grade7_rotating", grade7, "", "release-grade7", "Daj mi zadatak.", 1,
                 role="grade7"),
        scenario("release_gate_grade8_rotating", grade8, "", "release-grade8", "Daj mi zadatak.", 1,
                 role="grade8"),
        scenario("release_gate_grade9_rotating", grade9, "", "release-grade9", "Daj mi zadatak.", 1,
                 role="grade9"),
    )
    _require_model_routed_plan(plan)
    return plan


def _task_output_errors(result) -> list[str]:
    errors = []
    if not result.published_task_text:
        return ["missing_published_task"]
    if result.answer_text == practice.SAFE_ERROR_MESSAGE:
        errors.append("generic_failure_response")
    if _has_disallowed_control_character(result.answer_text) or _has_disallowed_control_character(result.published_task_text):
        errors.append("control_character_in_output")
    if (result.answer_text or "").rstrip().endswith("\\"):
        errors.append("dangling_terminal_backslash")
    if mathsafe.find_unsafe_math_issues(result.published_task_text or ""):
        errors.append("malformed_task_math")
    if len(result.next_state_options) != 4:
        errors.append("missing_options")
        return errors
    options = result.next_state_options
    ids = [option.get("id") for option in options if isinstance(option, dict)]
    texts = [option.get("text", "") for option in options if isinstance(option, dict)]
    if len(ids) != 4 or len(set(ids)) != 4 or len(set(texts)) != 4:
        errors.append("ambiguous_option_structure")
        return errors
    if result.next_state_options_match_session is not True:
        errors.append("next_state_options_not_committed_options")
    if result.internal_correct_option_id_after not in ids:
        errors.append("marked_option_missing")
        return errors
    marked_index = ids.index(result.internal_correct_option_id_after)
    failure, evaluation = mcq_integrity.publication_failure(
        result.published_task_text, texts, marked_index, result.expected_answer or "",
    )
    if failure:
        errors.append(failure)
    if evaluation.applicable and result.model_marked_option_value != result.visible_correct_option_value:
        errors.append("explanation_answer_mismatch")
    return errors


def _transition_errors(role: str, result) -> list[str]:
    expected = {
        "fresh_level1": (1, 1, 1, 1),
        "harder_level2": (1, 2, 1, 2),
        "easier_level1": (2, 1, 2, 1),
        "same_level_new": (1, 1, 1, 1),
        "contract_fresh": (1, 1, 1, 1),
        "contract_harder": (1, 2, 1, 2),
        "grade7": (1, 1, 1, 1),
        "grade8": (1, 1, 1, 1),
        "grade9": (1, 1, 1, 1),
    }.get(role)
    if expected is None:
        return []
    actual = (result.previous_level, result.target_level,
              result.session_level_before, result.session_level_after)
    return [] if actual == expected else [f"incorrect_transition:{actual!r}"]


def _structured_transition_errors(gate: GateScenario, result, prior_signature=None) -> list[str]:
    """Validate the production-validated structured package, never task prose."""
    if gate.scenario.path == "contract":
        return []
    role = gate.role
    if role not in {"harder_level2", "easier_level1", "same_level_new"}:
        return []
    errors = []
    expected = 2 if role == "harder_level2" else 1 if role == "easier_level1" else result.session_level_before
    if result.previous_level != (1 if role == "harder_level2" else 2 if role == "easier_level1" else result.session_level_before):
        errors.append("unexpected_previous_committed_level")
    if result.target_level != expected:
        errors.append("wrong_server_target_level")
    if result.reviewer_final_target_level != expected:
        errors.append("wrong_reviewer_final_target_level")
    if result.final_structured_package_source not in {"reviewer_final_task", "reviewer_corrected_task"}:
        errors.append("final_package_is_not_reviewer_owned")
    if result.structured_package_validation_passed is not True:
        errors.append("structured_package_validation_failed")
    if result.structured_package_validation_errors:
        errors.append("structured_package_validation_errors_present")
    checks = result.reviewer_checks or {}
    if (checks.get("task_package_consistent") is not True
            or checks.get("difficulty_evidence_valid") is not True
            or checks.get("task_signature_consistent") is not True):
        errors.append("reviewer_structured_checks_not_all_true")
    if result.committed_task_signature_matches_final is not True:
        errors.append("committed_signature_does_not_match_final_package")
    if role == "same_level_new":
        if result.session_level_after != result.session_level_before:
            errors.append("same_level_task_changed_level")
        if not prior_signature or result.final_task_signature_canonical == prior_signature.get("structured_signature"):
            errors.append("same_level_task_reused_signature")
    return errors


def _scenario_errors(gate: GateScenario, result, prior_task: str, prior_options: Iterable[dict],
                     prior_signature=None, *, expected_calls=None) -> list[str]:
    """`expected_calls` je vrijednost ZAMRZNUTA PRIJE turna; kad nije data,
    scenario mora imati staticki ugovor."""
    if expected_calls is None:
        expected_calls = gate.expected_calls
    if expected_calls is None:
        raise GateRefusal(
            f"Gate scenario '{gate.role}' was scored without a frozen "
            "expected call count.")
    errors = []
    if not result.published:
        errors.append(result.failure_class or "turn_not_published")
        if result.session_unchanged_after_rejection is not True:
            errors.append("state_mutation_after_rejection")
        return errors
    # UGOVOR POZIVA BRZE RUTE (nije raspon). Očekuje se TAČNO `expected_calls`.
    # Drugi poziv je dozvoljen isključivo kad je to RECENZENTSKI POPRAVAK, i to
    # se dokazuje SASTAVOM poziva, ne brojem: u jednom turnu smije stajati samo
    # jedan poziv tutorske klase. Dva tutorska poziva su skriveno ponavljanje i
    # padaju i dalje — ova provjera je stroža od golog broja, jer goli broj „2“
    # ne razlikuje popravak od retry-ja.
    stages = list(getattr(result, "sdk_call_stages", ()) or ())
    tutor_stages = [name for name in stages if name in _TUTOR_STAGE_NAMES]
    reviewer_stages = [name for name in stages if name in _REVIEWER_STAGE_NAMES]
    if stages and len(tutor_stages) > 1:
        errors.append(f"repeated_tutor_stage_calls_{len(tutor_stages)}")
    if stages and len(reviewer_stages) > 1:
        errors.append(f"repeated_reviewer_stage_calls_{len(reviewer_stages)}")
    if stages and len(stages) > len(tutor_stages) + len(reviewer_stages):
        errors.append("unknown_sdk_stage_in_turn")
    escalated = bool(reviewer_stages) and expected_calls > 0
    allowed_calls = expected_calls + (1 if escalated else 0)
    if result.sdk_calls_this_turn != allowed_calls:
        errors.append(f"expected_{allowed_calls}_sdk_calls_got_{result.sdk_calls_this_turn}")
    if result.sdk_calls_this_turn > _MAX_CALLS_PER_TURN:
        errors.append(f"more_than_two_calls_in_one_turn_{result.sdk_calls_this_turn}")
    if result.lesson_id != gate.scenario.lesson_id or result.effective_topic != gate.scenario.lesson_id \
            or result.session_lesson_id_after != gate.scenario.lesson_id:
        errors.append("wrong_lesson")
    errors.extend(_transition_errors(gate.role, result))
    if (os.environ.get("MATBOT_PRACTICE_PIPELINE", "") or "").strip().lower() == REQUIRED_PIPELINE:
        errors.extend(_structured_transition_errors(gate, result, prior_signature))
    if gate.role in {"fresh_level1", "harder_level2", "easier_level1", "same_level_new",
                     "contract_fresh", "contract_harder", "grade7", "grade8", "grade9"}:
        errors.extend(_task_output_errors(result))
        if result.intro_actual != result.intro_expected:
            errors.append("untruthful_intro")
    # The candidate structured runtime supplies Reviewer-validated generic
    # difficulty evidence.  Do not reconstruct semantic difficulty from
    # Bosnian task prose in that runtime; the legacy parser remains only for
    # the rollback path's independently provable divisibility MCQs.
    use_legacy_difficulty_parser = (
        (os.environ.get("MATBOT_PRACTICE_PIPELINE", "") or "").strip().lower()
        != REQUIRED_PIPELINE
    )
    if gate.role == "harder_level2" and use_legacy_difficulty_parser:
        before = mcq_integrity.difficulty_profile(
            prior_task, [option.get("text", "") for option in prior_options]
        )
        after = mcq_integrity.difficulty_profile(
            result.published_task_text or "", [option.get("text", "") for option in result.next_state_options]
        )
        if not before.measurable or not after.measurable or after.level <= before.level:
            errors.append("difficulty_direction_not_measurable")
    if gate.role == "easier_level1" and use_legacy_difficulty_parser:
        before = mcq_integrity.difficulty_profile(
            prior_task, [option.get("text", "") for option in prior_options]
        )
        after = mcq_integrity.difficulty_profile(
            result.published_task_text or "", [option.get("text", "") for option in result.next_state_options]
        )
        if not before.measurable or not after.measurable or after.level >= before.level:
            errors.append("difficulty_direction_not_measurable")
    if gate.role == "correct_choice":
        if result.answer_verdict != "correct" or not result.task_completed_after:
            errors.append("correct_committed_answer_not_recognized")
        if result.internal_correct_option_id_before != result.internal_correct_option_id_after:
            errors.append("correct_option_changed_during_click")
    if gate.role == "first_hint":
        text = result.answer_text or ""
        if not text or text == practice.SAFE_ERROR_MESSAGE:
            errors.append("useless_hint")
        if feedback.ensure_hint_makes_progress(result.published_task_text or "", text) != text:
            errors.append("hint_does_not_make_valid_progress")
        if feedback.leaks_answer(text, result.internal_correct_option_value or "", result.expected_answer or ""):
            errors.append("first_hint_reveals_answer")
    if gate.role == "full_solution":
        text = result.answer_text or ""
        if not text or text == practice.SAFE_ERROR_MESSAGE or mathsafe.find_unsafe_math_issues(text):
            errors.append("incomplete_or_malformed_full_solution")
        if not result.task_completed_after or result.revealed_correct_option_id != result.internal_correct_option_id_after:
            errors.append("full_solution_did_not_complete_committed_task")
    return errors


def _selected_lessons(plan: Iterable[GateScenario]) -> list[dict]:
    rows = []
    for gate in plan:
        if gate.role not in {"grade7", "grade8", "grade9"}:
            continue
        lesson = lesson_info(gate.scenario.grade, gate.scenario.lesson_id) or {}
        rows.append({"role": gate.role, "grade": gate.scenario.grade,
                     "id": gate.scenario.lesson_id, "title": lesson.get("title", "")})
    return rows


def _failure_console_lines(document: dict, result_path: Path) -> list[str]:
    """Return concise, safe diagnostics for a persisted failed gate result."""
    scenarios = document.get("scenarios") or []
    failed = next((row for row in scenarios if row.get("errors")), None)
    if failed is None and scenarios:
        failed = scenarios[-1]
    failed = failed or {}
    result = failed.get("result") if isinstance(failed.get("result"), dict) else {}
    reasons = failed.get("errors") or document.get("validation_failures") or ["unknown_failure"]
    relative_path = result_path.relative_to(ROOT).as_posix()
    return [
        f"FAILED SCENARIO: {failed.get('role', 'unknown')}",
        f"COMPLETED SCENARIOS: {document.get('scenario_count', 0)}/{REQUIRED_SCENARIO_COUNT}",
        f"REASON: {reasons[0]}",
        "LEVELS: previous={previous} target={target} committed={committed}".format(
            previous=result.get("previous_level", "-"), target=result.get("target_level", "-"),
            committed=result.get("session_level_after", "-"),
        ),
        f"SDK CALLS: {document.get('actual_sdk_calls', 0)}"
        f"/{document.get('planned_sdk_calls', SDK_CALL_CEILING)}"
        f" (ceiling {document.get('sdk_call_ceiling', SDK_CALL_CEILING)})",
        "STATE PRESERVED: " + str(
            result.get("session_unchanged_after_rejection") is True
        ).lower(),
        f"RESULT: {relative_path}",
    ]


def run_live_release_gate() -> int:
    commit_sha, tree_hash = _require_live_preconditions()
    plan = build_release_gate_plan(commit_sha)
    # PLAFON je gornja granica, a PLAN je tacan ugovor: ovdje se dokazuje samo
    # da ni najskuplji moguci ishod plana ne prelazi plafon. Tacan zbir se
    # sabira dok se scenariji izvrsavaju, iz vrijednosti zamrznutih PRIJE turna.
    if len(plan) != REQUIRED_SCENARIO_COUNT:
        raise GateRefusal("The committed release-gate plan is not the required 14-scenario plan.")
    if max_planned_calls(plan) != SDK_CALL_CEILING:
        raise GateRefusal(
            f"The committed plan's maximum is {max_planned_calls(plan)} SDK calls, "
            f"but the ceiling is {SDK_CALL_CEILING}.")

    started = datetime.now(timezone.utc)
    store = SessionStore()
    llm = CountingLLM(OpenAIPracticeLLM(), SDK_CALL_CEILING)
    capture = _LogCapture()
    report = CanaryReport(campaign="release-gate", started_at=started.isoformat(),
                          model=config.OPENAI_MODEL_TEXT,
                          reasoning_effort=config.REASONING_EFFORT,
                          timeout_seconds=config.AI_TIMEOUT_S,
                          sdk_call_ceiling=SDK_CALL_CEILING)
    import logging
    matbot_logger = logging.getLogger("matbot")
    previous_level = matbot_logger.level
    matbot_logger.setLevel(logging.INFO)
    matbot_logger.addHandler(capture)
    scenario_rows = []
    planned_calls = 0
    actual_calls = 0
    failures: list[str] = []
    infrastructure_failures: list[str] = []
    try:
        for gate in plan:
            before = store.peek(gate.scenario.session_id) or {}
            # ZAMRZAVANJE PRIJE TURNA — poslije turna bi bilo kruzno.
            expected_calls, expected_basis = resolve_expected_calls(gate, before)
            planned_calls += expected_calls
            result, stop = _run_one_turn(store, llm, capture, report, gate.scenario, "release-gate")
            errors = _scenario_errors(gate, result, before.get("current_task", ""),
                                      before.get("current_options", []),
                                      before.get("current_task_signature"),
                                      expected_calls=expected_calls)
            scenario_rows.append({"role": gate.role, "expected_sdk_calls": expected_calls,
                                  "expected_call_basis": expected_basis,
                                  "errors": errors, "result": asdict(result)})
            if result.failure_is_infrastructure:
                infrastructure_failures.append(gate.role)
            if errors or stop:
                failures.extend(f"{gate.role}:{error}" for error in (errors or [result.stop_triggered or "stopped"]))
                break
        actual_calls = llm.call_count
        cap_probe_refused = False
        if not failures:
            # GRANICA SE DOKAZUJE UVIJEK, ne samo kad plan slucajno potrosi
            # cijeli budzet: brojac se dopuni do plafona (`_count` je cisti
            # brojac i ne dodiruje SDK), pa PRVI poziv IZNAD plafona mora biti
            # odbijen. `actual_calls` je snimljen prije dopune.
            while llm.call_count < SDK_CALL_CEILING:
                llm._count("release_gate_ceiling_pad")
            try:
                llm._count("release_gate_over_ceiling_probe")
            except SDKCallBudgetExceeded:
                cap_probe_refused = True
            else:  # pragma: no cover - defensive; _count must never allow this
                failures.append("sdk_call_above_ceiling_was_not_refused")
    finally:
        matbot_logger.removeHandler(capture)
        matbot_logger.setLevel(previous_level)

    finished = datetime.now(timezone.utc)
    all_required_completed = len(scenario_rows) == REQUIRED_SCENARIO_COUNT and not failures
    # TACAN UGOVOR je planirani zbir, a plafon je samo gornja granica.
    passed = bool(all_required_completed and actual_calls == planned_calls
                  and planned_calls <= SDK_CALL_CEILING
                  and cap_probe_refused and not infrastructure_failures)
    document = {
        "campaign": "release-gate",
        "verdict": "PASS" if passed else "FAIL",
        "tested_commit_sha": commit_sha,
        "tested_tree_hash": tree_hash,
        "clean_worktree": True,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "result_age_basis": "finished_at",
        "model": config.OPENAI_MODEL_TEXT,
        "reasoning_effort": config.REASONING_EFFORT,
        "timeout_seconds": config.AI_TIMEOUT_S,
        "practice_pipeline": REQUIRED_PIPELINE,
        "difficulty_levels_enabled": True,
        "selected_lessons": _selected_lessons(plan),
        "scenario_count": len(scenario_rows),
        "required_scenario_count": REQUIRED_SCENARIO_COUNT,
        "sdk_call_ceiling": SDK_CALL_CEILING,
        "planned_sdk_calls": planned_calls,
        "actual_sdk_calls": actual_calls,
        "call_above_ceiling_refused": cap_probe_refused,
        # Zatečeno ime zadržano zbog starijih čitalaca artefakta; ordinalno je
        # („dvadeseti“) i više ne opisuje granicu, pa je novo ime mjerodavno.
        "twentieth_call_refused_before_sdk": cap_probe_refused,
        "scenarios": scenario_rows,
        "validation_failures": failures,
        "infrastructure_failures": infrastructure_failures,
        "final_verdict": "LIVE RELEASE GATE PASS — PUSH ALLOWED" if passed
                         else "LIVE RELEASE GATE FAILED — PUSH BLOCKED",
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, ensure_ascii=False, indent=2)
    result_path = RESULT_DIR / f"{commit_sha}.json"
    result_path.write_text(encoded, encoding="utf-8")
    (RESULT_DIR / "latest.json").write_text(encoded, encoding="utf-8")
    if not passed:
        for line in _failure_console_lines(document, result_path):
            print(line)
    print(document["final_verdict"])
    return 0 if passed else 1


def _run_static_checks() -> None:
    """Inert plan/budget checks.  No SDK client is created or invoked."""
    plan = build_release_gate_plan("0123456789abcdef" * 4)
    assert len(plan) == REQUIRED_SCENARIO_COUNT
    assert max_planned_calls(plan) == SDK_CALL_CEILING
    assert sum(item.expected_calls or 0 for item in plan) == _STATIC_PLAN_CALLS
    assert [item.role for item in plan] == [
        "fresh_level1", "correct_choice", "harder_level2", "first_hint", "full_solution",
        "easier_level1", "same_level_new", "contract_fresh", "contract_harder",
        "semantic_fresh", "semantic_harder",
        "grade7", "grade8", "grade9",
    ]
    assert _selected_lessons(plan) == _selected_lessons(build_release_gate_plan("0123456789abcdef" * 4))
    counter = CountingLLM(object(), SDK_CALL_CEILING)
    for _ in range(SDK_CALL_CEILING):
        counter._count("static")
    try:
        counter._count("static-above-ceiling")
    except SDKCallBudgetExceeded:
        pass
    else:
        raise AssertionError(
            f"SDK call #{SDK_CALL_CEILING + 1} was not refused")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manual commit-bound MAT-BOT live release gate")
    parser.add_argument("--static-checks", action="store_true", help="run inert plan/budget checks only")
    args = parser.parse_args(argv)
    if args.static_checks:
        _run_static_checks()
        print("LIVE RELEASE GATE STATIC CHECKS PASS — ZERO SDK CALLS")
        return 0
    try:
        return run_live_release_gate()
    except GateRefusal as exc:
        print("LIVE RELEASE GATE FAILED — PUSH BLOCKED")
        print(f"REFUSING TO RUN: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
