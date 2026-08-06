r"""Produkcija mora raditi s ISTOM konfiguracijom kojom je gate prošao.

POTVRĐEN PRODUKCIJSKI NALAZ: na VPS-u su nedostajale

    MATBOT_PRACTICE_PIPELINE=universal_two_call
    MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled

pa je aplikacija tiho radila na legacy/podrazumijevanoj konfiguraciji, dok su
release gate-ovi mjerili obje uključene. Klik na MCQ opciju je proradio tek
nakon što su varijable dodane i kontejner ponovo kreiran.

Tiho odstupanje je ovdje najgori mogući ishod: sve izgleda zdravo, a mjeri se
jedan put dok se izvršava drugi. Zato konfiguracija ima JEDAN izvor istine
(`matbot/release_config.py`) koji koriste i gate i deploy provjera.

Ovi testovi ne diraju VPS ni produkcijski `.env` — provjeravaju definiciju,
guard i prosljeđivanje varijabli.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from matbot import release_config

ROOT = Path(__file__).resolve().parent.parent


def _env(**overrides):
    env = dict(release_config.REQUIRED_RELEASE_ENV)
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# 1. JEDAN IZVOR ISTINE
# ---------------------------------------------------------------------------

def test_required_release_configuration_is_declared_once():
    required = release_config.REQUIRED_RELEASE_ENV
    assert required["MATBOT_PRACTICE_PIPELINE"] == "universal_two_call"
    assert required["MATBOT_PRACTICE_DIFFICULTY_LEVELS"] == "enabled"
    assert required["AI_TUTOR_TIMEOUT"] == "45"
    assert required["OPENAI_MODEL_TEXT"] == "gpt-5-mini"
    assert required["MATBOT_REASONING_EFFORT"] == "low"


def test_the_release_gate_uses_the_same_definition():
    """Gate i deploy guard NE smiju imati dvije liste istine."""
    import tools.run_live_release_gate as gate
    assert gate.REQUIRED_PIPELINE == release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"]
    assert gate.REQUIRED_DIFFICULTY_LEVELS == release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_DIFFICULTY_LEVELS"]


# ---------------------------------------------------------------------------
# 2. GUARD HVATA SVAKO ODSTUPANJE
# ---------------------------------------------------------------------------

def test_a_correct_configuration_passes():
    assert release_config.release_configuration_problems(_env()) == []


@pytest.mark.parametrize("name", sorted(release_config.REQUIRED_RELEASE_ENV))
def test_a_missing_variable_is_reported(name):
    env = _env()
    del env[name]
    problems = release_config.release_configuration_problems(env)
    assert any(name in problem for problem in problems), problems


@pytest.mark.parametrize("name", sorted(release_config.REQUIRED_RELEASE_ENV))
def test_an_empty_variable_is_reported(name):
    problems = release_config.release_configuration_problems(_env(**{name: ""}))
    assert any(name in problem for problem in problems), problems


@pytest.mark.parametrize("name,wrong", [
    ("MATBOT_PRACTICE_PIPELINE", "legacy_single_call"),
    ("MATBOT_PRACTICE_PIPELINE", "universal"),
    ("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "true"),
    ("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "1"),
    ("AI_TUTOR_TIMEOUT", "30"),
    ("AI_TUTOR_TIMEOUT", "abc"),
    ("OPENAI_MODEL_TEXT", "gpt-4o"),
    ("MATBOT_REASONING_EFFORT", "high"),
])
def test_a_wrong_value_is_reported(name, wrong):
    problems = release_config.release_configuration_problems(_env(**{name: wrong}))
    assert any(name in problem for problem in problems), problems


def test_the_guard_never_echoes_a_secret():
    env = _env(OPENAI_API_KEY="super-secret", FLASK_SECRET_KEY="also-secret",
               MATBOT_PRACTICE_PIPELINE="legacy_single_call")
    blob = " ".join(release_config.release_configuration_problems(env))
    assert "super-secret" not in blob and "also-secret" not in blob


# ---------------------------------------------------------------------------
# 3. SIGURAN IZVJEŠTAJ O EFEKTIVNOJ KONFIGURACIJI
# ---------------------------------------------------------------------------

def test_effective_configuration_reports_only_non_secret_values():
    report = release_config.effective_configuration(
        _env(OPENAI_API_KEY="super-secret", FLASK_SECRET_KEY="also-secret"))
    blob = " ".join(f"{key}={value}" for key, value in report.items())
    assert "super-secret" not in blob and "also-secret" not in blob
    assert "OPENAI_API_KEY" not in report and "FLASK_SECRET_KEY" not in report
    for key in ("practice_pipeline", "difficulty_levels", "model", "reasoning_effort",
                "timeout_seconds", "reviewer_output_tokens"):
        assert key in report, key


def test_effective_configuration_survives_a_missing_variable():
    report = release_config.effective_configuration({})
    assert report["practice_pipeline"] == "(unset)"


# ---------------------------------------------------------------------------
# 4. COMPOSE PROSLJEĐUJE SVAKU OBAVEZNU VARIJABLU
# ---------------------------------------------------------------------------

def test_compose_forwards_every_required_variable():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for name in release_config.REQUIRED_RELEASE_ENV:
        assert name in compose, f"docker-compose.yml ne prosljeđuje {name}"


def test_env_example_documents_every_required_variable():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in release_config.REQUIRED_RELEASE_ENV:
        assert name in example, f".env.example ne opisuje {name}"


# ---------------------------------------------------------------------------
# 5. GUARD NE SMIJE ONEMOGUĆITI OBIČAN OFFLINE RAD
# ---------------------------------------------------------------------------

def test_importing_the_application_offline_does_not_require_release_values():
    """Testovi i lokalni uvoz rade i bez produkcijskih zastavica."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import os;"
         "os.environ.pop('MATBOT_PRACTICE_PIPELINE', None);"
         "os.environ.pop('MATBOT_PRACTICE_DIFFICULTY_LEVELS', None);"
         "os.environ.setdefault('FLASK_SECRET_KEY', 'test-only');"
         "import matbot.release_config, matbot.practice; print('ok')"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
