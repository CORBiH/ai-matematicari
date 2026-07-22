# -*- coding: utf-8 -*-
"""The V3 isolation boundary, enforced instead of documented.

``matbot/ai_tutor_v3`` is the greenfield AI-first backend. Its whole point is
that the frozen tutoring stack can eventually be DELETED without touching it.
That property is worthless as a comment and load-bearing as a test, so this
module checks it three ways:

  * statically — every ``import`` in every V3 source file (including imports
    nested inside functions, which a runtime check can miss);
  * dynamically — importing the package in a CLEAN interpreter must not pull a
    frozen module into ``sys.modules``;
  * upstream — ``matbot/__init__.py`` must stay import-free, because a single
    import there re-couples every submodule at once (that is exactly the state
    this stage removed).

The static checker is itself verified against synthetic violating sources, so a
bug that made it silently accept everything cannot pass unnoticed.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_V3_PKG = _REPO_ROOT / "matbot" / "ai_tutor_v3"
_V3_MODULE = "matbot.ai_tutor_v3"

#: Frozen tutoring modules. V3 may not import any of these, at any depth.
FROZEN_MODULES = (
    "matbot.ai_tutor_service",
    "matbot.answer_checker",
    "matbot.grading_guard",
    "matbot.engine_v2",
    "matbot.exam_engine",
    "matbot.solution_plan",
    "matbot.task_templates",
    "matbot.task_activation",
    "matbot.task_model",
    "matbot.turn_intent",
    "matbot.prompt_builder",
    "matbot.tutor_prompts",
    "matbot.topic_detector",
    "matbot.topic_lookup",
    "matbot.image_result_verifier",
    "matbot.minimal",
    # Frozen support modules that only exist to serve the above.
    "matbot.render",
    "matbot.eval_v2",
    "matbot.eval_scenarios",
)

#: Retained infrastructure V3 IS allowed to depend on (plus the stdlib and the
#: OpenAI SDK). Listed for the report; the test asserts on FROZEN_MODULES.
ALLOWED_MATBOT_MODULES = (
    "matbot.content_loader",
    "matbot.topic_resolver",
    "matbot.bosnian",
    "matbot.sheets_log",
    "matbot.activity_log",
)


def _is_frozen(module: str) -> bool:
    """True when ``module`` is a frozen module or lives inside one.

    Matching is on dotted-path boundaries so ``matbot.task_model`` never matches
    ``matbot.task_templates``, while ``matbot.minimal`` still catches
    ``matbot.minimal.skills``.
    """
    return any(module == f or module.startswith(f + ".") for f in FROZEN_MODULES)


def _resolve_relative(module: str | None, level: int, pkg_parts: list[str]) -> str:
    """Resolve ``from ..x import y`` to an absolute dotted path."""
    if level <= 0:
        return module or ""
    base = pkg_parts[: len(pkg_parts) - (level - 1)] if level > 1 else pkg_parts
    return ".".join([*base, module]) if module else ".".join(base)


def imported_modules(source: str, package: str = _V3_MODULE) -> set[str]:
    """Every module name a source file imports, however it spells it.

    Covers ``import a.b``, ``from a.b import c``, ``from a import b`` (where the
    imported NAME is itself a module — the form that hid the legacy coupling),
    and relative imports at any level.
    """
    found: set[str] = set()
    pkg_parts = package.split(".")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_relative(node.module, node.level, pkg_parts)
            if resolved:
                found.add(resolved)
                # ``from matbot import answer_checker`` — the module is the
                # imported NAME, not node.module.
                for alias in node.names:
                    found.add(f"{resolved}.{alias.name}")
    return found


def _v3_sources() -> list[Path]:
    return sorted(_V3_PKG.rglob("*.py"))


# --------------------------------------------------------------------------- #
# 0. The checker itself must work                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("source,expected", [
    ("import matbot.answer_checker", "matbot.answer_checker"),
    ("from matbot.grading_guard import enforce_grading_consistency", "matbot.grading_guard"),
    ("from matbot import task_templates", "matbot.task_templates"),
    ("from matbot.minimal.skills import resolve_topic", "matbot.minimal.skills"),
    ("from matbot import engine_v2, content_loader", "matbot.engine_v2"),
    ("def f():\n    from matbot import exam_engine\n", "matbot.exam_engine"),
    ("from ..ai_tutor_service import handle_chat", "matbot.ai_tutor_service"),
])
def test_checker_detects_violating_source(source, expected):
    """A guard that cannot fail is not a guard."""
    modules = imported_modules(source)
    assert expected in modules, f"checker missed {expected!r} in: {source!r}"
    assert any(_is_frozen(m) for m in modules)


@pytest.mark.parametrize("source", [
    "import json",
    "from dataclasses import dataclass",
    "from matbot.content_loader import get_master",
    "from matbot import topic_resolver",
    "from matbot.bosnian import to_ijekavica",
    "from .schemas import TutorSessionState",
    "from . import schemas",
])
def test_checker_accepts_allowed_source(source):
    """And a guard that fails on everything is equally useless."""
    assert not [m for m in imported_modules(source) if _is_frozen(m)]


def test_task_model_is_not_confused_with_task_templates():
    """Prefix matching must respect dotted boundaries."""
    assert _is_frozen("matbot.task_model")
    assert _is_frozen("matbot.task_templates")
    assert not _is_frozen("matbot.task_modeller")


# --------------------------------------------------------------------------- #
# 1. Static: no frozen import anywhere in V3                                   #
# --------------------------------------------------------------------------- #
def test_v3_package_exists():
    assert _V3_PKG.is_dir(), f"missing package: {_V3_PKG}"
    assert (_V3_PKG / "__init__.py").is_file()


def test_v3_sources_import_no_frozen_module():
    offenders: list[str] = []
    for path in _v3_sources():
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for module in sorted(imported_modules(path.read_text(encoding="utf-8"))):
            if _is_frozen(module):
                offenders.append(f"{rel} imports {module}")
    assert not offenders, "V3 must not depend on frozen tutoring code:\n" + "\n".join(offenders)


def test_v3_init_has_no_imports_at_all():
    """Even ALLOWED imports are wrong in the package __init__: they would force
    every consumer to load every submodule."""
    tree = ast.parse((_V3_PKG / "__init__.py").read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not imports, "matbot/ai_tutor_v3/__init__.py must contain no imports"


# --------------------------------------------------------------------------- #
# 2. Upstream: the parent package must stay import-free                        #
# --------------------------------------------------------------------------- #
def test_matbot_init_has_no_imports():
    """A single import here re-couples every submodule, V3 included — this is
    precisely the eager-re-export state the isolation stage removed."""
    tree = ast.parse((_REPO_ROOT / "matbot" / "__init__.py").read_text(encoding="utf-8"))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not imports, (
        "matbot/__init__.py must contain no imports; found: "
        + ", ".join(ast.dump(n)[:60] for n in imports)
    )


# --------------------------------------------------------------------------- #
# 3. Dynamic: a clean interpreter proves the real import graph                 #
# --------------------------------------------------------------------------- #
def test_importing_v3_loads_no_frozen_module():
    """Static analysis cannot see a dynamic ``importlib`` call; this can.

    Runs in a SUBPROCESS because the pytest session has already imported the
    whole legacy stack via ``tests/conftest.py``.
    """
    probe = (
        "import sys\n"
        f"import {_V3_MODULE}\n"
        f"frozen = {FROZEN_MODULES!r}\n"
        "bad = sorted(m for m in sys.modules\n"
        "             if any(m == f or m.startswith(f + '.') for f in frozen))\n"
        "print('|'.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    loaded = [m for m in result.stdout.strip().split("|") if m]
    assert not loaded, (
        "importing matbot.ai_tutor_v3 pulled in frozen modules: " + ", ".join(loaded)
    )


def test_importing_matbot_package_loads_no_tutoring_module():
    """``import matbot`` itself must stay cheap and coupling-free."""
    probe = (
        "import sys, matbot\n"
        f"frozen = {FROZEN_MODULES!r}\n"
        "bad = sorted(m for m in sys.modules\n"
        "             if any(m == f or m.startswith(f + '.') for f in frozen))\n"
        "print('|'.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    loaded = [m for m in result.stdout.strip().split("|") if m]
    assert not loaded, "import matbot pulled in frozen modules: " + ", ".join(loaded)


# --------------------------------------------------------------------------- #
# 4. Retained infrastructure must survive deletion of the frozen stack         #
# --------------------------------------------------------------------------- #
def test_topic_resolver_does_not_import_task_templates_at_module_level():
    source = (_REPO_ROOT / "matbot" / "topic_resolver.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names: set[str] = set()
    for node in top_level:
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    assert not any(_is_frozen(n) for n in names), (
        "topic_resolver must not import frozen modules at module level"
    )


def test_topic_resolver_survives_missing_task_templates(monkeypatch):
    """With ``task_templates`` genuinely gone (the state after it is deleted),
    coverage degrades to "none known" instead of raising — the honest state
    ``TopicIdentity.covered`` already models.

    Simulated by making ``find_spec`` report the module cannot be LOCATED —
    i.e. it does not exist on disk — which is the real signal
    ``_legacy_skill_provider`` acts on. This is deliberately a different
    simulation from the test below: patching ``find_spec`` can never be
    confused with "the module exists but its import raised", which is the
    exact ambiguity this fix removes.
    """
    import importlib.util

    from matbot import topic_resolver as tr

    real_find_spec = importlib.util.find_spec

    def _reports_missing(name, *args, **kwargs):
        if name == "matbot.task_templates":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _reports_missing)
    assert tr._legacy_skill_provider(6, "Razlomci", "6-04-035 Proširivanje razlomaka") == ()


def test_topic_resolver_reraises_when_task_templates_import_itself_fails(monkeypatch):
    """``task_templates`` EXISTS (``find_spec`` locates it on disk) but fails to
    import — e.g. one of ITS OWN dependencies is broken. That is a real bug
    unrelated to this module's decoupling, and must propagate, not be read as
    "no known coverage".

    ``find_spec`` is left untouched here (so it genuinely locates the real
    file), and only the subsequent ``from matbot import task_templates`` is
    made to fail — reproducing exactly the ambiguity the old bare
    ``except ImportError`` used to collapse.
    """
    import builtins

    from matbot import topic_resolver as tr

    real_import = builtins.__import__

    def _broken_internal_dependency(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "matbot" and fromlist and "task_templates" in fromlist:
            raise ModuleNotFoundError(
                "simulated: an internal dependency of task_templates is broken"
            )
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _broken_internal_dependency)
    with pytest.raises(ModuleNotFoundError):
        tr._legacy_skill_provider(6, "Razlomci", "6-04-035 Proširivanje razlomaka")


def test_legacy_skill_provider_matches_direct_task_templates_call():
    """Success path is an exact pass-through: same output, same order, as
    calling ``task_templates.select_templates`` directly. The ``find_spec``
    check must not alter behavior when the module is present and healthy."""
    from matbot import task_templates
    from matbot import topic_resolver as tr

    grade, oblast, probe = 6, "Razlomci", "6-04-035 Proširivanje razlomaka"
    direct = tuple(t.skill_id for t in task_templates.select_templates(grade, oblast, probe))
    assert direct, "fixture probe is expected to have real template coverage"
    assert tr._legacy_skill_provider(grade, oblast, probe) == direct


def test_identify_accepts_injected_skill_provider():
    """V3 can supply its own coverage source without touching task_templates."""
    from matbot import topic_resolver as tr

    calls: list[tuple] = []

    def provider(grade, oblast, probe):
        calls.append((grade, oblast, probe))
        return ("v3_skill",)

    identity = tr.identify(6, "6-04-035", skill_provider=provider)
    assert identity.skill_ids == ("v3_skill",)
    assert identity.covered is True
    assert len(calls) == 1


def test_sheets_log_does_not_import_engine_v2():
    """Checked on the AST, not on the text: the docstring explaining WHY the
    coupling was removed legitimately names ``engine_v2``."""
    source = (_REPO_ROOT / "matbot" / "sheets_log.py").read_text(encoding="utf-8")
    modules = imported_modules(source, package="matbot.sheets_log")
    offenders = sorted(m for m in modules if _is_frozen(m))
    assert not offenders, (
        "sheets_log must not import frozen modules: " + ", ".join(offenders)
    )


@pytest.mark.parametrize("value,expected", [
    ("1", "1"), ("true", "1"), ("yes", "1"), ("on", "1"),
    ("0", "0"), ("no", "0"), ("", "0"),
])
def test_sheets_log_canary_marker_matches_legacy_semantics(monkeypatch, value, expected):
    """Same truthy set and same default as the engine_v2 helper it replaced."""
    from matbot import sheets_log

    monkeypatch.setenv("ENGINE_CANARY", value)
    assert sheets_log._canary_marker() == expected


def test_sheets_log_canary_marker_defaults_off(monkeypatch):
    from matbot import sheets_log

    monkeypatch.delenv("ENGINE_CANARY", raising=False)
    assert sheets_log._canary_marker() == "0"
