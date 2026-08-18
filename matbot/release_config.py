"""JEDAN izvor istine o konfiguraciji s kojom release ide u produkciju.

POTVRĐEN PRODUKCIJSKI NALAZ (istorijski): na VPS-u su nedostajale dvije
zastavice arhitekture, pa je aplikacija tiho radila na podrazumijevanoj
konfiguraciji dok su release gate-ovi mjerili drugu.

Jedna od njih, `MATBOT_PRACTICE_PIPELINE`, više NE POSTOJI: motor koji je
birala je povučen (2026-08-14), pa ni zaostala vrijednost na VPS-u ne može
ništa promijeniti. `MATBOT_PRACTICE_DIFFICULTY_LEVELS` i dalje vrijedi. Klik na MCQ opciju je proradio tek
nakon što su varijable dodane i kontejner ponovo kreiran.

Tiho odstupanje je najgori mogući ishod: sve izgleda zdravo, a mjeri se jedan
put dok se izvršava drugi. Zato ovaj modul deklariše obaveznu konfiguraciju
JEDNOM, a koriste je i release gate i deploy provjera.

DRUGI NALAZ, ISTA KLASA (ova faza): guard ISPOD je postojao, ali ga nije zvao
NIKO — ni start aplikacije, ni deploy workflow, ni pre-push, ni ijedan test.
Komentar u `app.py` je tvrdio da o padu zatvoreno odlučuje „deploy provjera“,
a takva provjera nije postojala; start je odstupanje prijavljivao kao WARNING
i nastavljao. Uz to su i sami spiskovi bili razdvojeni: deploy je upisivao
opseg brze rute i kapiju raznolikosti (kojih ovdje nije bilo), a kapija
izdanja je od pet deklarisanih vrijednosti provjeravala samo dvije — pa je
zvanično mjerenje prošlo s rokom od 30 s dok produkcija radi na 45 s.

Zato od ove faze:
  • vrijednosti su PODATAK (`deploy/production_release.env`), koji čitaju i
    Python i deploy shell petlja — nema druge kopije koja može zaostati;
  • `require_release_configuration` se STVARNO zove: na startu aplikacije kad
    je `MATBOT_RELEASE_ENFORCEMENT=enabled`, i bezuslovno iz deploy koraka
    (`python -m matbot.release_config --require`);
  • kapija izdanja PRIMJENJUJE isti skup prije ijednog uvoza `matbot.config`,
    pa mjeri rok i rutu koje produkcija stvarno izvršava.

GRANICE:
  • modul NIŠTA ne postavlja i ništa ne mijenja u okruženju — samo provjerava;
  • uvoz je bezuslovan, pa lokalni rad i testovi bez produkcijskih zastavica
    i dalje rade (guard se poziva izričito, na deploy/produkcijskom putu);
  • nijedna funkcija ne vraća, ne loguje i ne ispisuje vrijednost tajne.
"""
import os
import re
from pathlib import Path

# Fajl s obaveznim vrijednostima. Isti fajl čita deploy workflow, pa nova
# vrijednost ima tačno jedno mjesto.
REQUIRED_RELEASE_ENV_PATH = (Path(__file__).resolve().parent.parent
                             / "deploy" / "production_release.env")

# Deploy vrijednosti ubacuje u `.env` kroz `sed`, gdje `&`, `|` i `\` nisu
# tekst nego zamjenska sintaksa. Skup je namjerno uzak i provjerava se pri
# učitavanju, da neispravna vrijednost padne OVDJE a ne tiho na serveru.
_SAFE_VALUE_RE = re.compile(r"\A[A-Za-z0-9_.:+-]+\Z")
_SAFE_NAME_RE = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")

# Prekidač zatvorenog pada. TAČNO poklapanje (isti obrazac kao svaka druga
# zastavica ovdje) — „true“, „1“ ili tipfeler ne uključuju provjeru.
RELEASE_ENFORCEMENT_FLAG = "MATBOT_RELEASE_ENFORCEMENT"
RELEASE_ENFORCEMENT_VALUE = "enabled"


class ReleaseConfigurationUnavailable(RuntimeError):
    """Deklaracija obavezne konfiguracije se ne može pročitati.

    PADA ODMAH, i to je namjerno strože od praznog rječnika: prazna
    deklaracija bi značila „nema šta da se provjeri“, pa bi
    `release_configuration_problems` vratio praznu listu — dakle „sve u redu“
    za SVAKU konfiguraciju. To je tačno onaj tihi prolaz zbog kojeg ovaj modul
    postoji."""


def _parse_required_release_env(text, source=""):
    """`KLJUC=vrijednost` po redu → rječnik. Komentar i prazan red se preskaču."""
    values = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ReleaseConfigurationUnavailable(
                f"{source}:{number}: red nije oblika KLJUC=vrijednost")
        name, _, value = line.partition("=")
        if not _SAFE_NAME_RE.match(name):
            raise ReleaseConfigurationUnavailable(
                f"{source}:{number}: neispravno ime varijable")
        if not _SAFE_VALUE_RE.match(value):
            # Poruka NE nosi vrijednost — fajl je ne-tajni, ali pravilo
            # „dijagnostika nikad ne ispisuje vrijednost“ ovdje nema izuzetak.
            raise ReleaseConfigurationUnavailable(
                f"{source}:{number}: vrijednost za {name} izlazi iz dozvoljenog skupa znakova")
        if name in values:
            raise ReleaseConfigurationUnavailable(
                f"{source}:{number}: {name} je deklarisan dva puta")
        values[name] = value
    if not values:
        raise ReleaseConfigurationUnavailable(
            f"{source}: deklaracija obavezne konfiguracije je prazna")
    return values


def _load_required_release_env(path=REQUIRED_RELEASE_ENV_PATH):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseConfigurationUnavailable(
            f"{path}: deklaracija obavezne konfiguracije se ne može pročitati") from exc
    return _parse_required_release_env(text, source=str(path))


# Tačne vrijednosti kojima je release gate mjeren. Poređenje je EGZAKTNO —
# „nije prazno“ ili „liči na tačno“ je upravo ono što je propustilo produkciju.
REQUIRED_RELEASE_ENV = _load_required_release_env()

# EFEKTIVNE vrijednosti koje se NE traže kao env varijable, nego se provjerava
# ono što `matbot.config` stvarno razriješi. Izbor brzog modela je namjerno
# vlasništvo KODA (vidi obrazloženje u `deploy/production_release.env`), a ova
# provjera hvata oba načina da odluta: pogrešnu env varijablu i neovlaštenu
# izmjenu ugrađene vrijednosti.
REQUIRED_EFFECTIVE_CONFIG = {
    "fast_model": "gpt-5.6-luna",
    "fast_reasoning_effort": "low",
    "fast_reviewer_model": "gpt-5.6-luna",
    # „Objasni mi“ (migracija 2026-08-15): vlastiti, kodom auditiran izbor —
    # isti mehanizam kojim je zaštićen i izbor brzog modela.
    "explain_model": "gpt-5.6-luna",
    "explain_reasoning_effort": "low",
    # „Samo rezultat“ — tekstualni Quick poziv. Migracija 2026-08-18 s Lune na
    # Sol; dokaz je upareni A/B na 150 zadataka (82,7% → 95,3%, McNemar
    # p = 0,000157), obrazloženje uz QUICK_MODEL u matbot/config.py. Slika ima
    # SVOJ izbor niže i ovom migracijom nije dirnuta.
    "quick_model": "gpt-5.6-sol",
    "quick_reasoning_effort": "low",
    # Slika u „Samo rezultat“ (migracija 2026-08-15, vision benchmark):
    # Sol / low / original — tekstualni Quick ostaje na Luni.
    "quick_image_model": "gpt-5.6-sol",
    "quick_image_reasoning_effort": "low",
    "quick_image_detail": "original",
    # „Sutra imam kontrolni“ (v1, 2026-08-15): batch generisanje testa ima
    # vlastiti, kodom auditiran identitet — isti mehanizam kao Explain/Quick.
    "kontrolni_model": "gpt-5.6-luna",
    "kontrolni_reasoning_effort": "low",
}

# Imena koja se NIKAD ne ispisuju ni u jednoj dijagnostici ovog modula.
_SECRET_NAMES = frozenset({
    "OPENAI_API_KEY", "FLASK_SECRET_KEY", "SECRET_KEY", "MATBOT_EMBED_TOKEN",
    "MATBOT_AUTH_TOKEN",
})


def release_enforcement_enabled(environ=None):
    """Da li ovaj proces mora pasti zatvoreno na pogrešnoj konfiguraciji.

    Postoji da bi razlikovao PRODUKCIJSKO IZDANJE od običnog lokalnog/testnog
    uvoza: bez ovog prekidača bi svako pokretanje van produkcije padalo, pa bi
    guard morao biti isključen — a isključen guard je upravo stanje koje je
    dovelo do tihog odstupanja."""
    env = os.environ if environ is None else environ
    return (env.get(RELEASE_ENFORCEMENT_FLAG, "") or "") == RELEASE_ENFORCEMENT_VALUE


def _effective_config_problems():
    """Odstupanja EFEKTIVNO razriješenog izbora modela od auditiranog."""
    from matbot import config as _config

    observed = {
        "fast_model": _config.FAST_MODEL,
        "fast_reasoning_effort": _config.FAST_REASONING_EFFORT,
        "fast_reviewer_model": _config.FAST_REVIEWER_MODEL,
        "explain_model": _config.EXPLAIN_MODEL,
        "explain_reasoning_effort": _config.EXPLAIN_REASONING_EFFORT,
        "quick_model": _config.QUICK_MODEL,
        "quick_reasoning_effort": _config.QUICK_REASONING_EFFORT,
        "quick_image_model": _config.QUICK_IMAGE_MODEL,
        "quick_image_reasoning_effort": _config.QUICK_IMAGE_REASONING_EFFORT,
        "quick_image_detail": _config.QUICK_IMAGE_DETAIL,
        "kontrolni_model": _config.KONTROLNI_MODEL,
        "kontrolni_reasoning_effort": _config.KONTROLNI_REASONING_EFFORT,
    }
    return [f"{key} ima pogrešnu vrijednost (očekivano: {expected})"
            for key, expected in sorted(REQUIRED_EFFECTIVE_CONFIG.items())
            if observed.get(key) != expected]


def release_configuration_problems(environ=None, include_effective=True):
    """Lista odstupanja od obavezne konfiguracije; prazna lista = sve u redu.

    Poruka nosi SAMO ime varijable, očekivanu vrijednost i stanje („nedostaje“,
    „prazno“, „pogrešna vrijednost“) — nikad zatečenu vrijednost, jer bi tako
    pogrešno imenovana tajna mogla procuriti u log.

    `include_effective=False` provjerava samo env varijable; koriste ga testovi
    i alati koji rasuđuju o proslijeđenom rječniku, a ne o ovom procesu."""
    env = os.environ if environ is None else environ
    problems = []
    for name, expected in sorted(REQUIRED_RELEASE_ENV.items()):
        if name not in env:
            problems.append(f"{name} nedostaje (očekivano: {expected})")
            continue
        value = (env.get(name) or "").strip()
        if not value:
            problems.append(f"{name} je prazno (očekivano: {expected})")
        elif value != expected:
            problems.append(f"{name} ima pogrešnu vrijednost (očekivano: {expected})")
    if include_effective:
        problems.extend(_effective_config_problems())
    return problems


def effective_configuration(environ=None):
    """Sigurni, NE-TAJNI prikaz efektivne konfiguracije za startup log.

    Svjesno ne čita nijedno ime iz `_SECRET_NAMES` i ne prosljeđuje nijednu
    vrijednost koja nije u ovoj zatvorenoj listi."""
    env = os.environ if environ is None else environ

    def read(name):
        value = (env.get(name) or "").strip()
        return value or "(unset)"

    # RUTA I MODEL SE VIDE U STARTUP LOGU. Bez toga se poslije deploya ne može
    # dokazati koju arhitekturu proces STVARNO izvršava — a upravo tiho
    # odstupanje je kvar zbog kojeg ovaj modul postoji. Vrijednosti se čitaju iz
    # `matbot.config`, dakle uključuju i ugrađenu podrazumijevanu vrijednost,
    # ne samo ono što je neko upisao u okruženje.
    from matbot import config as _config
    from matbot import deterministic_variety as _deterministic_variety

    report = {
        # `practice_pipeline` je uklonjen s povlačenjem starog motora
        # (2026-08-14): postoji samo jedan, pa nema šta da se prijavi.
        "difficulty_levels": read("MATBOT_PRACTICE_DIFFICULTY_LEVELS"),
        "fast_single_call_scope": _config.fast_single_call_scope(),
        "fast_model": _config.FAST_MODEL,
        "fast_reasoning_effort": _config.FAST_REASONING_EFFORT,
        "fast_reviewer_model": _config.FAST_REVIEWER_MODEL,
        "explain_model": _config.EXPLAIN_MODEL,
        "explain_reasoning_effort": _config.EXPLAIN_REASONING_EFFORT,
        "quick_model": _config.QUICK_MODEL,
        "quick_reasoning_effort": _config.QUICK_REASONING_EFFORT,
        "quick_image_model": _config.QUICK_IMAGE_MODEL,
        "quick_image_reasoning_effort": _config.QUICK_IMAGE_REASONING_EFFORT,
        "quick_image_detail": _config.QUICK_IMAGE_DETAIL,
        "kontrolni_model": _config.KONTROLNI_MODEL,
        "kontrolni_reasoning_effort": _config.KONTROLNI_REASONING_EFFORT,
        "deterministic_practice": ("enabled" if _config.deterministic_practice_enabled()
                                   else "disabled"),
        "deterministic_variety_gate": read("MATBOT_DETERMINISTIC_VARIETY_GATE"),
        "deterministic_families_on_model": str(_deterministic_variety.coverage()[1]),
        "deterministic_lesson_exceptions": str(_deterministic_variety.coverage()[2]),
        # JEDAN NAGOVJEŠTAJ I DVIJE OSE RAZNOLIKOSTI su produkcijsko ponašanje
        # od ovog izdanja, a sve tri su rollback ručke s ugrađenom vrijednošću.
        # Bez njih u logu se poslije deploya ne može dokazati koje je ponašanje
        # kontejner stvarno dobio.
        "single_hint": ("enabled" if _config.practice_single_hint_enabled()
                        else "disabled"),
        "archetype_rotation": read("MATBOT_ARCHETYPE_ROTATION"),
        "form_rotation": read("MATBOT_FORM_ROTATION"),
        "model": read("OPENAI_MODEL_TEXT"),
        "reasoning_effort": read("MATBOT_REASONING_EFFORT"),
        "timeout_seconds": read("AI_TUTOR_TIMEOUT"),
        "reviewer_output_tokens": read("MATBOT_MAX_OUTPUT_TOKENS_REVIEWER"),
        "release_enforcement": read(RELEASE_ENFORCEMENT_FLAG),
        "app_commit": read("MATBOT_APP_COMMIT"),
    }
    assert not (_SECRET_NAMES & set(report)), "izvještaj ne smije nositi tajnu"
    return report


def format_effective_configuration(environ=None):
    """`key=value key=value …` — jedan red za startup log, bez ijedne tajne."""
    return " ".join(f"{key}={value}"
                    for key, value in effective_configuration(environ).items())


def require_release_configuration(environ=None):
    """Padni ZATVORENO kad konfiguracija odstupa od one kojom je gate mjeren.

    Poziva se izričito: sa starta aplikacije kad je
    `MATBOT_RELEASE_ENFORCEMENT=enabled`, i bezuslovno iz deploy koraka. Nikad
    pri običnom uvozu, da lokalni rad i testovi ostanu mogući bez
    produkcijskih zastavica."""
    problems = release_configuration_problems(environ)
    if problems:
        raise RuntimeError(
            "Konfiguracija se razlikuje od one kojom je release gate prošao:\n  - "
            + "\n  - ".join(problems))


def main(argv=None):
    """`python -m matbot.release_config [--require]` — deploy provjera.

    Bez zastavice samo ispisuje efektivnu konfiguraciju (izlaz 0). Sa
    `--require` pada zatvoreno na svakom odstupanju, i to BEZUSLOVNO: deploy
    korak ne smije zavisiti od zastavice koja i sama može nedostajati."""
    import argparse
    import sys

    # DIJAGNOSTIKA NE SMIJE PASTI NA KODNOJ STRANICI KONZOLE. Poruke su na
    # bosanskom, pa `č`/`š` na konzoli s cp1252 obore `print` s
    # UnicodeEncodeError — i tada se umjesto uredne presude dobije trag steka,
    # a izlazni kod postane slučajan. Deploy korak od ovog programa traži
    # JASNU presudu, pa se izlaz izričito prebacuje na UTF-8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

    parser = argparse.ArgumentParser(
        description="MAT-BOT provjera obavezne produkcijske konfiguracije")
    parser.add_argument("--require", action="store_true",
                        help="padni zatvoreno na svakom odstupanju")
    args = parser.parse_args(argv)
    print("matbot_effective_configuration " + format_effective_configuration())
    problems = release_configuration_problems()
    for problem in problems:
        print("matbot_release_configuration_mismatch " + problem)
    if problems and args.require:
        print("MATBOT RELEASE CONFIGURATION REJECTED")
        return 1
    if args.require:
        print("MATBOT RELEASE CONFIGURATION ACCEPTED")
    return 0


if __name__ == "__main__":
    import sys as _sys

    _sys.exit(main())
