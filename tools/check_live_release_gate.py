"""Offline verifier for a commit-bound live release-gate result.

This checker imports no LLM adapter and cannot make an SDK call.  It is used
by both humans and the pre-push hook to reject stale, incomplete, or
wrong-commit results.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from matbot import release_config  # noqa: E402

REQUIRED_SCENARIOS = 15
# Faza 4H: semantic_fresh/semantic_harder idu deterministički (0 poziva) —
# plafon i tačan broj poziva prolazne kampanje pali su 23 → 19.
#
# SERVER-VLASNIČKA POMOĆ (živi nalaz na scenariju `first_hint`): puno rješenje
# je uvijek serversko (0 poziva), a prvi hint je serverski za klasu tvrdnje,
# pa TAČAN broj poziva prolazne kampanje više NIJE jedna konstanta — statički
# dio plana je 17, a `first_hint` mu dodaje 0 ili 1. Zato se ovdje NE
# duplira nijedan zbir: tačan ugovor se čita IZ ARTEFAKTA
# (`planned_sdk_calls`), a ovdje ostaje samo plafon kao gornja granica.
# Plafon MORA pratiti runner (`tools/run_live_release_gate.py`): brza ruta trosi
# 1 poziv po scenariju, a uslovni recenzentski popravak najvise jos jedan.
PRACTICE_CALL_CEILING = 23
# „SUTRA IMAM KONTROLNI“ (v1): kapija dodatno dokazuje novi mod — dva stvarna
# testa, svaki najvise 2 poziva (batch + uslovna popravka, bez treceg). Zbir
# se cita iz artefakta (`kontrolni_sdk_calls`), plafon je zbir oba dijela.
KONTROLNI_REQUIRED_TESTS = 2
KONTROLNI_MAX_CALLS = 2 * KONTROLNI_REQUIRED_TESTS
# PROSIRENJE POKRIVENOSTI: „Objasni mi" i „Samo rezultat" (tekst + slika) su do
# sada bili potpuno nemjereni, pa je izmjena u njima mogla proci kapiju bez
# ijednog zivog dokaza (zabiljezeno kao procesni P1). Oba moda troše TACNO
# jedan poziv po turnu, pa im je zbir fiksan i provjerava se strogom
# jednakoscu — artefakt bez tih polja NE PROLAZI, jer je mjerio arhitekturu
# bez tih modova.
EXPLAIN_REQUIRED_TURNS = 4
EXPLAIN_MAX_CALLS = EXPLAIN_REQUIRED_TURNS
QUICK_REQUIRED_TURNS = 4
QUICK_MAX_CALLS = QUICK_REQUIRED_TURNS
REQUIRED_CALL_CEILING = (PRACTICE_CALL_CEILING + KONTROLNI_MAX_CALLS
                         + EXPLAIN_MAX_CALLS + QUICK_MAX_CALLS)
REQUIRED_COVERED_MODES = ["practice", "kontrolni", "explain", "quick_text",
                          "quick_image"]
MAX_AGE = timedelta(hours=24)
# NIJEDAN LITERAL SE NE PONAVLJA. Ruta i rok su ranije stajali ovdje kao
# vlastita kopija; kopija je upravo ono što je pustilo kampanju s rokom od
# 30 s dok produkcija radi na 45 s. Izvor je isti fajl koji čita i deploy.
REQUIRED_TIMEOUT_S = float(release_config.REQUIRED_RELEASE_ENV["AI_TUTOR_TIMEOUT"])


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    if completed.returncode:
        raise ValueError("Git metadata is unavailable.")
    return completed.stdout.strip()


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def validate_result(document: dict, *, expected_commit: str | None = None,
                    expected_tree: str | None = None, now: datetime | None = None) -> list[str]:
    """Return every fail-closed reason; performs no network or SDK work."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["result_not_an_object"]
    if document.get("campaign") != "release-gate":
        errors.append("wrong_campaign")
    if document.get("verdict") != "PASS":
        errors.append("verdict_is_not_pass")
    if expected_commit and document.get("tested_commit_sha") != expected_commit:
        errors.append("commit_sha_mismatch")
    if expected_tree and document.get("tested_tree_hash") != expected_tree:
        errors.append("tree_hash_mismatch")
    if document.get("clean_worktree") is not True:
        errors.append("clean_worktree_not_confirmed")
    if document.get("difficulty_levels_enabled") is not True:
        errors.append("difficulty_levels_not_enabled")
    # ROK KOJIM JE MJERENO MORA BITI PRODUKCIJSKI. Zatečeni (stariji) artefakt
    # nema ovo polje ili nosi ugrađenih 30 s — i ne smije autorizovati push.
    measured_timeout = document.get("timeout_seconds")
    if not isinstance(measured_timeout, (int, float)) or isinstance(measured_timeout, bool):
        errors.append("missing_timeout_seconds")
    elif float(measured_timeout) != REQUIRED_TIMEOUT_S:
        errors.append("gate_timeout_is_not_the_production_timeout")
    # Cijela primijenjena konfiguracija, ne samo ruta: kapija koja je mjerila
    # drugi opseg brze rute ili drugu kapiju raznolikosti mjerila je drugu
    # arhitekturu nego što produkcija izvršava.
    applied = document.get("release_configuration")
    if not isinstance(applied, dict):
        errors.append("missing_release_configuration")
    elif applied != dict(release_config.REQUIRED_RELEASE_ENV):
        errors.append("gate_configuration_is_not_the_production_configuration")
    if document.get("scenario_count") != REQUIRED_SCENARIOS:
        errors.append("wrong_scenario_count")
    if document.get("required_scenario_count") != REQUIRED_SCENARIOS:
        errors.append("missing_required_scenario_count")
    planned = document.get("planned_sdk_calls")
    actual = document.get("actual_sdk_calls")
    # Kontrolni stage je USLOVAN po pozivima (1 ili 2 po testu) — čita se iz
    # artefakta, uz vlastitu tvrdu granicu. Zatečeni (stariji) artefakt bez
    # ovih polja NE prolazi: mjerio je arhitekturu bez kontrolni moda.
    kontrolni_calls = document.get("kontrolni_sdk_calls")
    if (not isinstance(kontrolni_calls, int) or isinstance(kontrolni_calls, bool)
            or not KONTROLNI_REQUIRED_TESTS <= kontrolni_calls <= KONTROLNI_MAX_CALLS):
        errors.append("invalid_kontrolni_sdk_calls")
        kontrolni_calls = None
    if document.get("kontrolni_max_calls") != KONTROLNI_MAX_CALLS:
        errors.append("wrong_kontrolni_max_calls")
    if document.get("kontrolni_required_tests") != KONTROLNI_REQUIRED_TESTS:
        errors.append("missing_kontrolni_required_tests")
    kontrolni_tests = document.get("kontrolni_tests")
    if (not isinstance(kontrolni_tests, list)
            or len(kontrolni_tests) != KONTROLNI_REQUIRED_TESTS):
        errors.append("required_kontrolni_tests_missing")
    else:
        published_rows = 0
        for row in kontrolni_tests:
            if (not isinstance(row, dict) or row.get("errors")
                    or not isinstance(row.get("sdk_calls"), int)
                    or not 1 <= row["sdk_calls"] <= 2):
                errors.append("kontrolni_test_not_clean")
                break
            # PAD ZATVORENO NIJE KVAR BEZBJEDNOSTI (ispravan ishod kad paket ne
            # zadovolji validatore), ali bar jedan test mora biti STVARNO
            # objavljen — inače bi kapija prošla i za mod koji ništa ne objavi.
            if row.get("status") == "ready":
                published_rows += 1
            elif row.get("status") != "failed":
                errors.append("kontrolni_test_not_clean")
                break
        else:
            if published_rows == 0:
                errors.append("kontrolni_never_published")

    # --- PROSIRENA POKRIVENOST: Explain i Quick -----------------------------
    if document.get("covered_modes") != REQUIRED_COVERED_MODES:
        errors.append("missing_mode_coverage")
    explain_calls = document.get("explain_sdk_calls")
    if explain_calls != EXPLAIN_MAX_CALLS:
        errors.append("invalid_explain_sdk_calls")
        explain_calls = None
    if document.get("explain_required_turns") != EXPLAIN_REQUIRED_TURNS:
        errors.append("missing_explain_required_turns")
    quick_calls = document.get("quick_sdk_calls")
    if quick_calls != QUICK_MAX_CALLS:
        errors.append("invalid_quick_sdk_calls")
        quick_calls = None
    if document.get("quick_required_turns") != QUICK_REQUIRED_TURNS:
        errors.append("missing_quick_required_turns")
    for field, required, label in (("explain_turns", EXPLAIN_REQUIRED_TURNS, "explain"),
                                   ("quick_turns", QUICK_REQUIRED_TURNS, "quick")):
        rows = document.get(field)
        if not isinstance(rows, list) or len(rows) != required:
            errors.append(f"required_{label}_turns_missing")
            continue
        for row in rows:
            if (not isinstance(row, dict) or row.get("errors")
                    or row.get("sdk_calls") != 1):
                errors.append(f"{label}_turn_not_clean")
                break

    if not isinstance(planned, int) or isinstance(planned, bool) or planned <= 0:
        # Zatečeni (stari) artefakt nema ovo polje — i ne smije proći.
        errors.append("missing_planned_sdk_calls")
    else:
        if planned > PRACTICE_CALL_CEILING:
            errors.append("planned_sdk_calls_above_ceiling")
        # Uslovni recenzentski popravak dodaje pozive koji se ne mogu zamrznuti
        # prije turna. Ne prastaju se paušalno: runner ih broji SAMO kad je
        # dodatni poziv recenzentska faza scenarija koji je inace prosao.
        escalated = document.get("escalated_sdk_calls")
        if escalated is None:
            errors.append("missing_escalated_sdk_calls")
        elif not isinstance(escalated, int) or escalated < 0:
            errors.append("invalid_escalated_sdk_calls")
        elif (kontrolni_calls is not None and explain_calls is not None
              and quick_calls is not None
              and actual != planned + escalated + kontrolni_calls
              + explain_calls + quick_calls):
            errors.append("wrong_sdk_call_count")
        elif planned + escalated > PRACTICE_CALL_CEILING:
            errors.append("planned_sdk_calls_above_ceiling")
    if document.get("sdk_call_ceiling") != REQUIRED_CALL_CEILING:
        errors.append("wrong_sdk_call_ceiling")
    if document.get("call_above_ceiling_refused") is not True:
        errors.append("call_above_ceiling_not_refused")
    if document.get("validation_failures"):
        errors.append("hidden_validation_failures")
    if document.get("infrastructure_failures"):
        errors.append("infrastructure_failure")
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != REQUIRED_SCENARIOS:
        errors.append("required_scenarios_missing")
    else:
        required_roles = {
            "fresh_level1", "correct_choice", "harder_level2", "first_hint", "full_solution",
            "easier_level1", "same_level_new", "contract_fresh", "contract_harder",
            "semantic_fresh", "semantic_harder", "migrated_deterministic",
            "grade7", "grade8", "grade9",
        }
        actual_roles = {row.get("role") for row in scenarios if isinstance(row, dict)}
        if actual_roles != required_roles:
            errors.append("required_scenario_roles_missing")
        for row in scenarios:
            result = row.get("result") if isinstance(row, dict) else None
            if not isinstance(row, dict) or row.get("errors") or not isinstance(result, dict):
                errors.append("scenario_failed_or_skipped")
                break
            if result.get("attempted") is not True or result.get("published") is not True:
                errors.append("scenario_failed_or_skipped")
                break
            if result.get("failure_is_infrastructure") is True:
                errors.append("scenario_infrastructure_failure")
                break
    finished = _parse_time(document.get("finished_at"))
    reference = now or datetime.now(timezone.utc)
    if finished is None:
        errors.append("missing_finished_at")
    elif reference - finished > MAX_AGE or finished > reference + timedelta(minutes=5):
        errors.append("result_expired_or_invalid_time")
    return errors


def _load(path: Path) -> dict:
    # Nedostajuća i pokvarena datoteka su se ranije javljale ISTOM porukom, pa je
    # blokiran push izgledao kao oštećen artefakt umjesto kao artefakt kojeg
    # nema. Razlikovanje je čisto dijagnostičko — nijedna provjera se ne mijenja.
    if not path.is_file():
        raise ValueError(f"Release-gate result {path} does not exist.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Release-gate result cannot be read as UTF-8 JSON.") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline MAT-BOT live release-gate verifier")
    parser.add_argument("result", type=Path, help="release-gate JSON result")
    parser.add_argument("--expected-commit", help="pushed local commit SHA; defaults to current HEAD")
    parser.add_argument("--expected-tree", help="expected Git tree hash; defaults to current HEAD tree")
    args = parser.parse_args(argv)
    try:
        document = _load(args.result)
        commit = args.expected_commit or _git("rev-parse", "HEAD")
        tree = args.expected_tree or _git("rev-parse", "HEAD^{tree}")
        errors = validate_result(document, expected_commit=commit, expected_tree=tree)
    except ValueError as exc:
        print("LIVE RELEASE GATE FAILED — PUSH BLOCKED")
        print(f"Offline result check failed: {exc}")
        return 1
    if errors:
        print("LIVE RELEASE GATE FAILED — PUSH BLOCKED")
        print("Offline result check failures: " + ", ".join(sorted(set(errors))))
        return 1
    print("LIVE RELEASE GATE PASS — PUSH ALLOWED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
