r"""Jedinica autorizacije pre-push hooka je JEDAN testirani tree.

ŽIVI SIMPTOM: `git push origin main` je nad 28 nepushanih commitova prvo ispisao
`LIVE RELEASE GATE PASS — PUSH ALLOWED` za HEAD, a onda nastavio tražiti zaseban
artefakt za svaki raniji commit i blokirao push.

Uzrok je bio model autorizacije: hook je petljao kroz SVAKI pushani commit i za
svaki tražio vlastiti release-gate artefakt. Prolazan gate nad najnovijim
testiranim stanjem već sadrži sve svoje pretke, pa je ispravna jedinica jedan
relevantan tree — najnoviji behavior-affecting commit (ili njegov kasniji
potomak u istom rangeu, koji je onda nužno exempt).

Testovi voze STVARNI `.githooks/pre-push` i STVARNI
`tools/check_live_release_gate.py` u privremenom git repou. Nijedna stroga
provjera artefakta se ne stubuje: scenario count, SDK count, freshness, verdict,
tree hash i skriveni validation failures ostaju kakvi jesu.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".githooks" / "pre-push"
CHECKER = ROOT / "tools" / "check_live_release_gate.py"

REQUIRED_ROLES = (
    "fresh_level1", "correct_choice", "harder_level2", "first_hint", "full_solution",
    "easier_level1", "same_level_new", "contract_fresh", "contract_harder",
    "semantic_fresh", "semantic_harder", "migrated_deterministic",
    "grade7", "grade8", "grade9",
)


def _run(args, cwd, **kwargs):
    return subprocess.run(args, cwd=str(cwd), text=True, encoding="utf-8",
                          errors="replace", capture_output=True, check=False, **kwargs)


class _Completed:
    def __init__(self, completed):
        self.returncode = completed.returncode
        self.stdout = completed.stdout.decode("utf-8", "replace")
        self.stderr = completed.stderr.decode("utf-8", "replace")


def _run_hook(hook_path, cwd, stdin_text):
    r"""Git hooku stdin stiže s doslovnim LF-om.

    Tekstualni mod na Windowsu bi `\n` pretvorio u `\r\n`, SHA bi dobio zalutali
    `\r` i `git rev-list` bi pukao — što je artefakt harnessa, ne ponašanje
    hooka. Zato se piše u bajtovima.
    """
    return _Completed(subprocess.run(
        ["sh", str(hook_path), "origin", "git@example.invalid:repo.git"],
        cwd=str(cwd), input=stdin_text.encode("utf-8"),
        capture_output=True, check=False))


def passing_document(commit_sha, tree_hash):
    """Artefakt koji zadovoljava SVE postojeće provjere verifiera."""
    return {
        "campaign": "release-gate",
        "verdict": "PASS",
        "tested_commit_sha": commit_sha,
        "tested_tree_hash": tree_hash,
        "clean_worktree": True,
        "practice_pipeline": "universal_two_call",
        "difficulty_levels_enabled": True,
        "scenario_count": 15,
        "required_scenario_count": 15,
        # SERVER-VLASNIČKA POMOĆ: tačan ugovor je PLANIRANI zbir iz same
        # kampanje (statički + izvedeni prvi hint); plafon je samo gornja granica.
        "planned_sdk_calls": 17,
        "escalated_sdk_calls": 0,
        "actual_sdk_calls": 17,
        "sdk_call_ceiling": 23,
        "call_above_ceiling_refused": True,
        "twentieth_call_refused_before_sdk": True,
        "validation_failures": [],
        "infrastructure_failures": [],
        "scenarios": [
            {"role": role, "errors": [],
             "result": {"attempted": True, "published": True,
                        "failure_is_infrastructure": False}}
            for role in REQUIRED_ROLES
        ],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


class Repo:
    """Privremeni git repo s pravim hookom i pravim verifierom."""

    def __init__(self, path):
        self.path = path
        self.shas = {}

    def git(self, *args):
        completed = _run(["git", *args], self.path)
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    def commit(self, name, relative_path, message=None):
        target = self.path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{name}\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("-c", "user.name=T", "-c", "user.email=t@example.invalid",
                 "commit", "-q", "-m", message or name)
        self.shas[name] = self.git("rev-parse", "HEAD")
        return self.shas[name]

    def write_artifact(self, name, document=None, raw=None):
        sha = self.shas[name]
        target = self.path / "scratchpad" / "live_release_gate" / f"{sha}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            target.write_text(raw, encoding="utf-8")
            return target
        tree = self.git("rev-parse", f"{sha}^{{tree}}")
        target.write_text(json.dumps(document or passing_document(sha, tree)),
                          encoding="utf-8")
        return target

    def push(self, remote_name, local_name=None):
        """Pokreni hook doslovno onako kako ga git zove, bez ijedne mreže."""
        local = self.shas[local_name] if local_name else self.git("rev-parse", "HEAD")
        remote = self.shas[remote_name]
        stdin = f"refs/heads/main {local} refs/heads/main {remote}\n"
        return _run_hook(self.path / ".githooks" / "pre-push", self.path, stdin)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _run(["git", "init", "-q", "-b", "main"], path)
    # Isto kao u pravom repou — inače bi `git add -A` uvukao artefakt u sljedeći
    # commit i taj commit bi postao behavior-affecting.
    (path / ".gitignore").write_text(
        ".venv/\nscratchpad/live_release_gate/\n", encoding="utf-8", newline="\n")
    (path / ".githooks").mkdir()
    (path / ".githooks" / "pre-push").write_text(
        HOOK.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    (path / "tools").mkdir()
    (path / "tools" / "check_live_release_gate.py").write_text(
        CHECKER.read_text(encoding="utf-8"), encoding="utf-8")
    # Hook bira interpreter sam; dajemo mu isti kojim testovi rade.
    venv_bin = path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    shim = venv_bin / "python"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n',
                    encoding="utf-8", newline="\n")
    os.chmod(shim, 0o755)

    fixture = Repo(path)
    fixture.commit("base", "docs/START.md")
    return fixture


def behavior(repo, name):
    return repo.commit(name, f"matbot/{name}.py")


def docs(repo, name):
    return repo.commit(name, f"docs/{name}.md")


def allowed(result):
    return result.returncode == 0 and "PUSH ALLOWED" in result.stdout


# ---------------------------------------------------------------------------
# 1. VELIK RANGE, JEDAN ARTEFAKT ZA NAJNOVIJI BEHAVIOR COMMIT
# ---------------------------------------------------------------------------

def test_fifteen_behavior_commits_need_only_the_newest_artifact(repo):
    for index in range(15):
        behavior(repo, f"b{index:02d}")
    repo.write_artifact("b14")

    result = repo.push("base")
    assert allowed(result), result.stdout + result.stderr
    # Verifier je pozvan tačno jednom — jedan PASS red, nijedan FAILED red.
    assert result.stdout.count("PUSH ALLOWED") == 1
    assert "PUSH BLOCKED" not in result.stdout


def test_older_commits_are_never_consulted(repo):
    """Petlja se mora zaustaviti: stariji artefakti se ni ne otvaraju."""
    for index in range(5):
        behavior(repo, f"b{index:02d}")
    repo.write_artifact("b04")
    # Namjerno pokvareni artefakti za pretke — hook ih ne smije ni dotaći.
    for index in range(4):
        repo.write_artifact(f"b{index:02d}", raw="{ not json")

    result = repo.push("base")
    assert allowed(result), result.stdout + result.stderr
    for index in range(4):
        assert repo.shas[f"b{index:02d}"] not in result.stdout


# ---------------------------------------------------------------------------
# 2–5. BLOKIRANJA KOJA MORAJU OSTATI
# ---------------------------------------------------------------------------

def test_a_pass_for_an_older_ancestor_does_not_authorize_a_newer_behavior_commit(repo):
    behavior(repo, "old")
    repo.write_artifact("old")
    behavior(repo, "new")

    result = repo.push("base")
    assert not allowed(result)
    assert repo.shas["new"] in result.stdout


def test_a_missing_artifact_for_the_newest_behavior_commit_blocks(repo):
    behavior(repo, "b0")
    result = repo.push("base")
    assert not allowed(result)
    assert result.returncode == 1


def test_a_failing_artifact_blocks(repo):
    sha = behavior(repo, "b0")
    tree = repo.git("rev-parse", f"{sha}^{{tree}}")
    document = passing_document(sha, tree)
    document["verdict"] = "FAIL"
    repo.write_artifact("b0", document=document)

    result = repo.push("base")
    assert not allowed(result)
    assert "verdict_is_not_pass" in result.stdout


def test_a_malformed_artifact_blocks(repo):
    behavior(repo, "b0")
    repo.write_artifact("b0", raw="{ this is not json")

    result = repo.push("base")
    assert not allowed(result)
    assert "cannot be read as UTF-8 JSON" in result.stdout


@pytest.mark.parametrize("field,value", [
    ("scenario_count", 3),
    ("actual_sdk_calls", 5),
    ("planned_sdk_calls", 0),
    ("planned_sdk_calls", 19),
    ("sdk_call_ceiling", 12),
    ("required_scenario_count", 3),
    ("call_above_ceiling_refused", False),
    ("clean_worktree", False),
    ("practice_pipeline", "legacy_single_call"),
    ("difficulty_levels_enabled", False),
    ("validation_failures", ["harder_level2:something"]),
    ("infrastructure_failures", ["llm_timeout"]),
    ("finished_at", "2020-01-01T00:00:00+00:00"),
])
def test_every_existing_strict_check_still_blocks(repo, field, value):
    """Popravka mijenja SAMO jedinicu autorizacije — nijedan prag."""
    sha = behavior(repo, "b0")
    document = passing_document(sha, repo.git("rev-parse", f"{sha}^{{tree}}"))
    document[field] = value
    repo.write_artifact("b0", document=document)
    assert not allowed(repo.push("base"))


def test_a_tree_hash_mismatch_still_blocks(repo):
    sha = behavior(repo, "b0")
    document = passing_document(sha, "0" * 40)
    repo.write_artifact("b0", document=document)
    result = repo.push("base")
    assert not allowed(result)
    assert "tree_hash_mismatch" in result.stdout


# ---------------------------------------------------------------------------
# 6–8. EXEMPTION POLITIKA OSTAJE KAKVA JESTE
# ---------------------------------------------------------------------------

def test_a_documentation_only_range_is_allowed_without_any_artifact(repo):
    docs(repo, "d0")
    docs(repo, "d1")
    result = repo.push("base")
    assert result.returncode == 0
    assert "PUSH BLOCKED" not in result.stdout


def test_a_behavior_commit_followed_by_docs_is_authorized_by_the_behavior_artifact(repo):
    behavior(repo, "b0")
    repo.write_artifact("b0")
    docs(repo, "d0")

    result = repo.push("base")
    assert allowed(result), result.stdout + result.stderr


def test_an_artifact_for_the_later_docs_commit_also_authorizes(repo):
    """Testirani tree smije biti i kasniji potomak, ako je exempt."""
    behavior(repo, "b0")
    docs(repo, "d0")
    repo.write_artifact("d0")

    assert allowed(repo.push("base"))


def test_a_new_behavior_commit_after_a_passing_artifact_blocks(repo):
    behavior(repo, "b0")
    repo.write_artifact("b0")
    behavior(repo, "b1")

    result = repo.push("base")
    assert not allowed(result)
    assert repo.shas["b1"] in result.stdout


def test_a_test_only_commit_is_still_behavior_affecting(repo):
    """`tests/*` ostaje u gate opsegu — exemption se ne proširuje."""
    repo.commit("t0", "tests/test_x.py")
    assert not allowed(repo.push("base"))


# ---------------------------------------------------------------------------
# 9. NOVA GRANA: PRVI PUSH BEZ REMOTE STANJA
# ---------------------------------------------------------------------------

def test_a_brand_new_branch_still_requires_the_newest_artifact(repo):
    behavior(repo, "b0")
    behavior(repo, "b1")
    repo.write_artifact("b1")

    local = repo.shas["b1"]
    stdin = f"refs/heads/main {local} refs/heads/main {'0' * 40}\n"
    result = _run_hook(repo.path / ".githooks" / "pre-push", repo.path, stdin)
    assert allowed(result), result.stdout + result.stderr


# ---------------------------------------------------------------------------
# 10. STVARNI REPO: JEDAN ZAHTIJEVANI COMMIT, BEZ PRETRAGE PREDAKA
# ---------------------------------------------------------------------------

def _real_outgoing():
    completed = _run(["git", "rev-list", "origin/main..HEAD"], ROOT)
    if completed.returncode:
        pytest.skip("origin/main nije dostupan u ovom checkoutu")
    return completed.stdout.split()


def test_the_real_outgoing_range_resolves_to_a_single_required_commit():
    """Regresija: raniji hook je tražio artefakt za svaki od ovih commitova."""
    outgoing = _real_outgoing()
    if len(outgoing) < 2:
        pytest.skip("nema dovoljno nepushanih commitova")

    stdin = "refs/heads/main %s refs/heads/main %s\n" % (
        outgoing[0], _run(["git", "rev-parse", "origin/main"], ROOT).stdout.strip())
    result = _run_hook(HOOK, ROOT, stdin)

    # Hook smije imenovati NAJVIŠE JEDAN commit ispod HEAD-a — najnoviji
    # behavior-affecting commit kao jedinicu autorizacije — nikad cijeli rep
    # predaka (regresija: raniji hook je nabrajao svaki od 28 commitova).
    # Faza 4G: raniji oblik `mentioned == []` je važio samo dok je sam HEAD
    # behavior-affecting; docs/tests commit na vrhu legitimno pomjera
    # zahtijevani commit jedan-dva mjesta niže, i hook ga tada smije imenovati.
    mentioned = [sha for sha in outgoing[1:] if sha in result.stdout]
    assert len(mentioned) <= 1, mentioned
    assert result.stdout.count("PUSH ALLOWED") <= 1
