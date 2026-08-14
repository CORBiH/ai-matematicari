r"""Produkcija mora raditi s ISTOM konfiguracijom kojom je gate prošao.

POTVRĐEN PRODUKCIJSKI NALAZ (prvi): na VPS-u su nedostajale

    MATBOT_PRACTICE_PIPELINE=universal_two_call
    MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled

pa je aplikacija tiho radila na legacy/podrazumijevanoj konfiguraciji, dok su
release gate-ovi mjerili obje uključene. Klik na MCQ opciju je proradio tek
nakon što su varijable dodane i kontejner ponovo kreiran.

POTVRĐEN NALAZ (drugi, ova faza): guard koji je iz toga nastao nije zvao NIKO —
ni start aplikacije, ni deploy workflow, ni pre-push, ni ijedan test. Start je
odstupanje prijavljivao kao WARNING i nastavljao, a spiskovi su se razišli:
deploy je upisivao dvije vrijednosti kojih u deklaraciji nije bilo, a kapija
izdanja je od pet deklarisanih provjeravala dvije — pa je zvanično mjerenje
prošlo s rokom od 30 s dok produkcija radi na 45 s.

Tiho odstupanje je ovdje najgori mogući ishod: sve izgleda zdravo, a mjeri se
jedan put dok se izvršava drugi. Zato konfiguracija ima JEDAN izvor istine
(`deploy/production_release.env`) koji koriste deploy skripta, guard, kapija
izdanja i offline provjera artefakta.

Ovi testovi ne diraju VPS ni produkcijski `.env` — provjeravaju deklaraciju,
guard, deploy skriptu i prosljeđivanje varijabli. Nijedan ne pravi modelski
poziv.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from matbot import release_config

ROOT = Path(__file__).resolve().parent.parent
DECLARATION = ROOT / "deploy" / "production_release.env"
APPLY_SCRIPT = ROOT / "deploy" / "apply_release_env.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-vps.yml"
_SH = shutil.which("bash") or shutil.which("sh")


def _env(**overrides):
    env = dict(release_config.REQUIRED_RELEASE_ENV)
    env.update(overrides)
    return env


def _problems(env):
    """Samo env dio — efektivni izbor modela zavisi od OVOG procesa, ne od
    proslijeđenog rječnika, pa bi ga miješanje ovdje učinilo nečitljivim."""
    return release_config.release_configuration_problems(env, include_effective=False)


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


def test_the_audited_route_flags_are_part_of_the_required_configuration():
    """Opseg brze rute i kapija raznolikosti MIJENJAJU rutu lekcija, a ranije
    ih deklaracija uopšte nije sadržavala: deploy ih je upisivao, a guard i
    kapija izdanja o njima nisu znali ništa."""
    required = release_config.REQUIRED_RELEASE_ENV
    assert required["MATBOT_FAST_SINGLE_CALL_SCOPE"] == "model_backed"
    assert required["MATBOT_DETERMINISTIC_VARIETY_GATE"] == "enabled"


def test_practice_rollback_levers_are_declared_explicitly():
    """Ugrađena vrijednost im JESTE produkcijska, pa ne mogu odlutati
    izostankom — ali mogu ostati zaboravljene poslije ručne intervencije na
    VPS-u. Deklaracija ih vraća na auditirano stanje pri svakom izdanju."""
    required = release_config.REQUIRED_RELEASE_ENV
    assert required["MATBOT_DETERMINISTIC_PRACTICE"] == "enabled"
    assert required["MATBOT_PRACTICE_SINGLE_HINT"] == "enabled"
    assert required["MATBOT_ARCHETYPE_ROTATION"] == "enabled"
    assert required["MATBOT_FORM_ROTATION"] == "enabled"


def test_the_fast_model_choice_stays_code_owned_but_is_still_verified():
    """Izbor brzog modela NAMJERNO nije env varijabla (vidi obrazloženje u
    deklaraciji), ali pogrešna vrijednost i dalje mora pasti — provjerava se
    EFEKTIVNO razriješen izbor, što hvata i env i izmjenu ugrađene vrijednosti."""
    for name in ("MATBOT_FAST_MODEL", "MATBOT_FAST_REASONING_EFFORT",
                 "MATBOT_FAST_REVIEWER_MODEL"):
        assert name not in release_config.REQUIRED_RELEASE_ENV
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["fast_model"] == "gpt-5.6-luna"
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["fast_reviewer_model"] == "gpt-5.6-luna"
    assert release_config.REQUIRED_EFFECTIVE_CONFIG["fast_reasoning_effort"] == "low"

    from matbot import config as matbot_config
    assert matbot_config.FAST_MODEL == release_config.REQUIRED_EFFECTIVE_CONFIG["fast_model"]
    assert matbot_config.FAST_REVIEWER_MODEL == \
        release_config.REQUIRED_EFFECTIVE_CONFIG["fast_reviewer_model"]
    assert matbot_config.FAST_REASONING_EFFORT == \
        release_config.REQUIRED_EFFECTIVE_CONFIG["fast_reasoning_effort"]


def test_the_declaration_is_the_only_place_the_values_are_written():
    """Nijedan potrošač ne smije ponovo ukucati vrijednost kao literal."""
    declaration = DECLARATION.read_text(encoding="utf-8")
    for name, expected in release_config.REQUIRED_RELEASE_ENV.items():
        assert f"{name}={expected}" in declaration, name
    for path in (ROOT / "matbot" / "release_config.py",
                 ROOT / "tools" / "run_live_release_gate.py",
                 ROOT / "tools" / "check_live_release_gate.py"):
        source = path.read_text(encoding="utf-8")
        assert "universal_two_call\"" not in source and "'universal_two_call'" not in source, path


def test_every_declared_value_is_safe_for_the_deploy_substitution():
    """Deploy vrijednosti ubacuje kroz `sed`; `&`, `|` i `\\` tamo nisu tekst."""
    for name, value in release_config.REQUIRED_RELEASE_ENV.items():
        assert re.fullmatch(r"[A-Za-z0-9_.:+-]+", value), name


def test_the_files_the_vps_shell_reads_have_no_carriage_returns():
    """Oba fajla čita POSIX ljuska na VPS-u. Uz CRLF bi `while IFS= read -r`
    svakoj vrijednosti dodao završni `\\r`, pa bi u `.env` završilo
    `universal_two_call\\r` — nevidljivo drukčije od očekivanog. Uređivač na
    Windowsu to lako vrati, zato trajna provjera uz `.gitattributes` pravilo."""
    for path in (DECLARATION, APPLY_SCRIPT):
        assert b"\r" not in path.read_bytes(), path
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "deploy/production_release.env text eol=lf" in attributes
    assert "*.sh text eol=lf" in attributes


def test_a_broken_declaration_fails_closed_instead_of_passing_everything():
    """Prazna deklaracija bi značila „nema šta da se provjeri“ — dakle „sve u
    redu“ za SVAKU konfiguraciju. To je tačno onaj tihi prolaz koji ovaj modul
    postoji da spriječi."""
    with pytest.raises(release_config.ReleaseConfigurationUnavailable):
        release_config._parse_required_release_env("# samo komentar\n", source="x")
    with pytest.raises(release_config.ReleaseConfigurationUnavailable):
        release_config._parse_required_release_env("BEZ_JEDNAKOSTI\n", source="x")
    with pytest.raises(release_config.ReleaseConfigurationUnavailable):
        release_config._parse_required_release_env("A=1\nA=2\n", source="x")
    with pytest.raises(release_config.ReleaseConfigurationUnavailable):
        release_config._parse_required_release_env("A=ima razmak\n", source="x")
    with pytest.raises(release_config.ReleaseConfigurationUnavailable):
        release_config._load_required_release_env(ROOT / "deploy" / "nema-me.env")


# ---------------------------------------------------------------------------
# 2. GUARD HVATA SVAKO ODSTUPANJE
# ---------------------------------------------------------------------------

def test_a_correct_configuration_passes():
    assert _problems(_env()) == []


@pytest.mark.parametrize("name", sorted(release_config.REQUIRED_RELEASE_ENV))
def test_a_missing_variable_is_reported(name):
    env = _env()
    del env[name]
    problems = _problems(env)
    assert any(name in problem for problem in problems), problems


@pytest.mark.parametrize("name", sorted(release_config.REQUIRED_RELEASE_ENV))
def test_an_empty_variable_is_reported(name):
    problems = _problems(_env(**{name: ""}))
    assert any(name in problem for problem in problems), problems


@pytest.mark.parametrize("name,wrong", [
    # Tačno primjeri iz zadatka: svaki od njih znači da produkcija izvršava
    # DRUGU arhitekturu nego onu koju je kapija izdanja izmjerila.
    ("MATBOT_PRACTICE_PIPELINE", "legacy_single_call"),
    ("MATBOT_PRACTICE_PIPELINE", "universal"),
    ("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "disabled"),
    ("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "true"),
    ("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "1"),
    ("MATBOT_FAST_SINGLE_CALL_SCOPE", "lessons"),
    ("MATBOT_FAST_SINGLE_CALL_SCOPE", "off"),
    ("MATBOT_DETERMINISTIC_VARIETY_GATE", "disabled"),
    ("MATBOT_DETERMINISTIC_PRACTICE", "disabled"),
    ("MATBOT_PRACTICE_SINGLE_HINT", "disabled"),
    ("MATBOT_ARCHETYPE_ROTATION", "disabled"),
    ("MATBOT_FORM_ROTATION", "disabled"),
    ("AI_TUTOR_TIMEOUT", "30"),
    ("AI_TUTOR_TIMEOUT", "abc"),
    ("OPENAI_MODEL_TEXT", "gpt-4o"),
    ("MATBOT_REASONING_EFFORT", "high"),
    ("MATBOT_RELEASE_ENFORCEMENT", "disabled"),
])
def test_a_wrong_value_is_reported(name, wrong):
    problems = _problems(_env(**{name: wrong}))
    assert any(name in problem for problem in problems), problems


def test_require_release_configuration_raises_on_a_wrong_value():
    """Ranije je ova funkcija postojala a nije je zvao niko; sada je zove i
    start aplikacije i deploy korak, pa njeno ponašanje mora biti dokazano."""
    with pytest.raises(RuntimeError) as excinfo:
        release_config.require_release_configuration(
            _env(MATBOT_PRACTICE_PIPELINE="legacy_single_call"))
    assert "MATBOT_PRACTICE_PIPELINE" in str(excinfo.value)


def test_release_enforcement_is_an_exact_flag():
    assert release_config.release_enforcement_enabled(
        {release_config.RELEASE_ENFORCEMENT_FLAG: "enabled"}) is True
    for value in ("", "true", "1", "Enabled", "enabled ", "disabled"):
        assert release_config.release_enforcement_enabled(
            {release_config.RELEASE_ENFORCEMENT_FLAG: value}) is False
    assert release_config.release_enforcement_enabled({}) is False


def test_the_guard_never_echoes_a_secret():
    env = _env(OPENAI_API_KEY="super-secret", FLASK_SECRET_KEY="also-secret",
               MATBOT_PRACTICE_PIPELINE="legacy_single_call")
    blob = " ".join(_problems(env))
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


def test_startup_diagnostics_prove_every_audited_choice():
    """Poslije deploya se iz jednog reda mora vidjeti KOJU arhitekturu proces
    izvršava — inače se tiho odstupanje opet vidi tek ručnim testom."""
    report = release_config.effective_configuration(_env())
    for key in ("practice_pipeline", "difficulty_levels", "fast_single_call_scope",
                "deterministic_variety_gate", "fast_model", "fast_reasoning_effort",
                "fast_reviewer_model", "single_hint", "archetype_rotation",
                "form_rotation", "timeout_seconds", "release_enforcement"):
        assert key in report, key
        assert report[key] != "(unset)", key
    assert report["timeout_seconds"] == release_config.REQUIRED_RELEASE_ENV["AI_TUTOR_TIMEOUT"]


def test_effective_configuration_survives_a_missing_variable():
    report = release_config.effective_configuration({})
    assert report["practice_pipeline"] == "(unset)"


def test_the_config_cli_never_prints_a_secret_and_fails_closed():
    """`python -m matbot.release_config --require` je deploy korak."""
    env = dict(os.environ)
    env.update(_env())
    env.update({"OPENAI_API_KEY": "super-secret", "FLASK_SECRET_KEY": "also-secret",
                "MATBOT_PRACTICE_PIPELINE": "legacy_single_call"})
    result = subprocess.run([sys.executable, "-m", "matbot.release_config", "--require"],
                            cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=180)
    assert result.returncode == 1, result.stdout
    assert "super-secret" not in result.stdout and "also-secret" not in result.stdout
    assert "super-secret" not in result.stderr and "also-secret" not in result.stderr
    assert "MATBOT_PRACTICE_PIPELINE" in result.stdout

    env["MATBOT_PRACTICE_PIPELINE"] = \
        release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"]
    accepted = subprocess.run([sys.executable, "-m", "matbot.release_config", "--require"],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=180)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


# ---------------------------------------------------------------------------
# 4. DEPLOY UPISUJE CIJELU DEKLARACIJU, IDEMPOTENTNO
# ---------------------------------------------------------------------------

def test_the_workflow_applies_the_declaration_with_the_committed_script():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "deploy/apply_release_env.sh" in workflow
    assert "deploy/production_release.env" in workflow
    # Stari oblik je ponavljao vrijednosti u samom workflowu — tri kopije jedne
    # odluke. Nijedna se ne smije vratiti.
    for value in ("MATBOT_FAST_SINGLE_CALL_SCOPE=model_backed",
                  "MATBOT_DETERMINISTIC_VARIETY_GATE=enabled"):
        assert value not in workflow, value


def test_the_workflow_verifies_the_configuration_and_fails_closed():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("python -m matbot.release_config --require") == 2, (
        "deploy mora provjeriti konfiguraciju i PRIJE zamjene usluge i nad "
        "kontejnerom koji stvarno posluzuje")
    assert "docker compose run --rm --no-deps -T matbot" in workflow
    assert "docker compose exec -T matbot" in workflow
    assert "curl -fsS --max-time 10 http://127.0.0.1:8080/healthz" in workflow


@pytest.mark.skipif(_SH is None, reason="POSIX ljuska nije dostupna")
def test_the_deploy_script_persists_every_required_value(tmp_path):
    target = tmp_path / ".env"
    target.write_text("OPENAI_API_KEY=sk-tajna\nFLASK_SECRET_KEY=tajna2\n",
                      encoding="utf-8")
    result = subprocess.run([_SH, str(APPLY_SCRIPT), str(target), str(DECLARATION)],
                            cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=180)
    assert result.returncode == 0, result.stderr
    written = target.read_text(encoding="utf-8")
    for name, expected in release_config.REQUIRED_RELEASE_ENV.items():
        assert f"{name}={expected}\n" in written, name
    # Tajne netaknute, i nikad ispisane.
    assert "OPENAI_API_KEY=sk-tajna\n" in written
    assert "FLASK_SECRET_KEY=tajna2\n" in written
    assert "sk-tajna" not in result.stdout and "tajna2" not in result.stdout


@pytest.mark.skipif(_SH is None, reason="POSIX ljuska nije dostupna")
def test_repeated_deploy_is_idempotent_and_never_duplicates_a_key(tmp_path):
    """Ponovljeni deploy je normalno stanje (svaki push na main)."""
    target = tmp_path / ".env"
    # Bez zavrsnog novog reda, i sa zatecenom POGRESNOM vrijednoscu — tacno
    # stanje VPS-a koji je nekad rucno mijenjan.
    target.write_text("OPENAI_API_KEY=sk-tajna\nMATBOT_PRACTICE_PIPELINE=legacy_single_call",
                      encoding="utf-8")

    def run():
        done = subprocess.run([_SH, str(APPLY_SCRIPT), str(target), str(DECLARATION)],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180)
        assert done.returncode == 0, done.stderr
        return target.read_text(encoding="utf-8")

    first = run()
    second = run()
    third = run()
    assert first == second == third, "ponovljeni deploy mora dati identican .env"
    assert "legacy_single_call" not in first, "pogresna zatecena vrijednost mora biti zamijenjena"
    for name in release_config.REQUIRED_RELEASE_ENV:
        occurrences = [line for line in first.splitlines() if line.startswith(f"{name}=")]
        assert len(occurrences) == 1, (name, occurrences)
    assert "OPENAI_API_KEY=sk-tajna" in first


# ---------------------------------------------------------------------------
# 5. KAPIJA IZDANJA MJERI PRODUKCIJSKU KONFIGURACIJU
# ---------------------------------------------------------------------------

def test_the_release_gate_uses_the_same_definition():
    """Gate i deploy guard NE smiju imati dvije liste istine."""
    import tools.run_live_release_gate as gate
    assert gate.REQUIRED_PIPELINE == release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"]
    assert gate.REQUIRED_DIFFICULTY_LEVELS == release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_DIFFICULTY_LEVELS"]


def test_the_release_gate_applies_the_whole_declaration():
    """ŽIVI NALAZ: kapija je mjerila rok od 30 s dok produkcija radi na 45 s,
    jer je od deklarisanih vrijednosti provjeravala samo dvije, a nijednu nije
    primjenjivala."""
    import tools.run_live_release_gate as gate
    assert gate.APPLIED_RELEASE_ENV == dict(release_config.REQUIRED_RELEASE_ENV)


def test_the_gate_applies_the_configuration_before_matbot_resolves_it():
    """Redoslijed je suština popravke: `matbot.config` rok i izbor modela čita
    PRI UVOZU, pa primjena poslije uvoza ne bi promijenila ništa."""
    source = (ROOT / "tools" / "run_live_release_gate.py").read_text(encoding="utf-8")
    apply_at = source.index('if __name__ == "__main__":\n    _apply_required_release_environment()')
    config_at = source.index("from matbot import config, release_config")
    assert apply_at < config_at, "primjena mora prethoditi uvozu matbot.config"


def test_the_gate_timeout_equals_the_production_timeout():
    """ŽIVI NALAZ: zvanična kampanja je prošla s `timeout_seconds = 30.0` dok
    produkcija radi na 45 s.

    Dokazuje se na STVARNOM skriptnom putu, u čistom okruženju bez ijedne
    produkcijske varijable, kroz `--static-checks` — inertnu granu koja ne
    troši nijedan SDK poziv."""
    import tools.run_live_release_gate as gate
    required = float(release_config.REQUIRED_RELEASE_ENV["AI_TUTOR_TIMEOUT"])
    assert gate.REQUIRED_TIMEOUT_S == required

    clean = {key: value for key, value in os.environ.items()
             if key not in release_config.REQUIRED_RELEASE_ENV}
    clean["FLASK_SECRET_KEY"] = "test-only"
    result = subprocess.run(
        [sys.executable, "tools/run_live_release_gate.py", "--static-checks"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=clean, timeout=900)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ZERO SDK CALLS" in result.stdout
    line = next(row for row in result.stdout.splitlines()
                if row.startswith("matbot_release_gate_configuration "))
    assert f"timeout_seconds={release_config.REQUIRED_RELEASE_ENV['AI_TUTOR_TIMEOUT']}" in line, line
    # Ista klasa: opseg brze rute i kapija raznolikosti MIJENJAJU rutu lekcija,
    # pa i oni moraju biti produkcijski prije nego što se plan uopšte sagradi.
    assert "fast_single_call_scope=" + \
        release_config.REQUIRED_RELEASE_ENV["MATBOT_FAST_SINGLE_CALL_SCOPE"] in line, line
    assert "deterministic_variety_gate=" + \
        release_config.REQUIRED_RELEASE_ENV["MATBOT_DETERMINISTIC_VARIETY_GATE"] in line, line
    assert "practice_pipeline=" + \
        release_config.REQUIRED_RELEASE_ENV["MATBOT_PRACTICE_PIPELINE"] in line, line


# Da uvoz kapije NE mijenja okruženje procesa dokazuje
# `test_live_release_gate.py::test_importing_the_gate_never_mutates_the_process_environment`,
# a da pozivalac bez produkcijske konfiguracije dobija ODBIJANJE (nikad tiho
# drukčije mjerenje) dokazuju `test_gate_preconditions_*` u istom fajlu.


def test_the_gate_refuses_an_environment_that_contradicts_the_declaration():
    """Kapija odsutnu vrijednost POSTAVLJA, ali svjesno drukčiju NIKAD ne
    pregazi tiho — inače bi namjeran izbor operatera nestao bez traga."""
    import tools.run_live_release_gate as gate
    hostile = dict(release_config.REQUIRED_RELEASE_ENV)
    hostile["MATBOT_PRACTICE_PIPELINE"] = "legacy_single_call"
    with pytest.raises(gate.GateRefusal) as excinfo:
        gate._apply_required_release_environment(hostile)
    assert "MATBOT_PRACTICE_PIPELINE" in str(excinfo.value)
    assert "legacy_single_call" not in str(excinfo.value)


def test_the_gate_applier_fills_in_a_missing_value(tmp_path):
    import tools.run_live_release_gate as gate
    empty = {}
    applied = gate._apply_required_release_environment(empty)
    assert applied == dict(release_config.REQUIRED_RELEASE_ENV)
    assert empty == dict(release_config.REQUIRED_RELEASE_ENV)


def test_the_offline_checker_rejects_a_result_measured_with_another_timeout():
    """Pre-push hook čita SAMO artefakt, pa parnost roka mora biti dokaziva iz
    njega. Zatečeni artefakt s 30 s ne smije više autorizovati push."""
    import tools.check_live_release_gate as checker
    required = float(release_config.REQUIRED_RELEASE_ENV["AI_TUTOR_TIMEOUT"])
    assert checker.REQUIRED_TIMEOUT_S == required
    document = {"timeout_seconds": 30.0,
                "release_configuration": dict(release_config.REQUIRED_RELEASE_ENV)}
    assert "gate_timeout_is_not_the_production_timeout" in checker.validate_result(document)
    document["timeout_seconds"] = required
    assert "gate_timeout_is_not_the_production_timeout" not in checker.validate_result(document)
    del document["timeout_seconds"]
    assert "missing_timeout_seconds" in checker.validate_result(document)


def test_the_offline_checker_rejects_a_result_measured_with_another_configuration():
    import tools.check_live_release_gate as checker
    wrong = dict(release_config.REQUIRED_RELEASE_ENV)
    wrong["MATBOT_DETERMINISTIC_VARIETY_GATE"] = "disabled"
    document = {"timeout_seconds": checker.REQUIRED_TIMEOUT_S, "release_configuration": wrong}
    assert "gate_configuration_is_not_the_production_configuration" in \
        checker.validate_result(document)
    assert "missing_release_configuration" in checker.validate_result({})


# ---------------------------------------------------------------------------
# 6. COMPOSE PROSLJEĐUJE SVAKU OBAVEZNU VARIJABLU
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
# 7. GUARD NE SMIJE ONEMOGUĆITI OBIČAN OFFLINE RAD
# ---------------------------------------------------------------------------

def _import_offline(statement):
    clean = {key: value for key, value in os.environ.items()
             if key not in release_config.REQUIRED_RELEASE_ENV}
    clean["FLASK_SECRET_KEY"] = "test-only"
    return subprocess.run([sys.executable, "-c", statement], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=clean, timeout=180)


def test_importing_the_application_offline_does_not_require_release_values():
    """Testovi i lokalni uvoz rade i bez produkcijskih zastavica."""
    result = _import_offline("import matbot.release_config, matbot.practice; print('ok')")
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_importing_the_flask_app_offline_still_works():
    """Zatvoreni pad se aktivira SAMO uz MATBOT_RELEASE_ENFORCEMENT=enabled;
    bez tog razdvajanja bi svaki lokalni `import app` padao."""
    result = _import_offline("import app; print('ok')")
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_the_flask_app_refuses_to_start_on_a_wrong_production_configuration():
    """Ranije je start SAMO logovao WARNING i nastavljao, pa je pogrešna
    konfiguracija posluživala učenike dok je `/healthz` bio zelen."""
    hostile = dict(os.environ)
    hostile.update(release_config.REQUIRED_RELEASE_ENV)
    hostile["FLASK_SECRET_KEY"] = "test-only"
    hostile["MATBOT_PRACTICE_PIPELINE"] = "legacy_single_call"
    result = subprocess.run([sys.executable, "-c", "import app"], cwd=str(ROOT),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=hostile, timeout=180)
    assert result.returncode != 0
    assert "MATBOT_PRACTICE_PIPELINE" in result.stderr
