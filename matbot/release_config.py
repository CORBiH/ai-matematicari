"""JEDAN izvor istine o konfiguraciji s kojom release ide u produkciju.

POTVRĐEN PRODUKCIJSKI NALAZ: na VPS-u su nedostajale

    MATBOT_PRACTICE_PIPELINE=universal_two_call
    MATBOT_PRACTICE_DIFFICULTY_LEVELS=enabled

pa je aplikacija tiho radila na legacy/podrazumijevanoj konfiguraciji, dok su
release gate-ovi mjerili obje uključene. Klik na MCQ opciju je proradio tek
nakon što su varijable dodane i kontejner ponovo kreiran.

Tiho odstupanje je najgori mogući ishod: sve izgleda zdravo, a mjeri se jedan
put dok se izvršava drugi. Zato ovaj modul deklariše obaveznu konfiguraciju
JEDNOM, a koriste je i release gate i deploy provjera.

GRANICE:
  • modul NIŠTA ne postavlja i ništa ne mijenja u okruženju — samo provjerava;
  • uvoz je bezuslovan, pa lokalni rad i testovi bez produkcijskih zastavica
    i dalje rade (guard se poziva izričito, na deploy putu);
  • nijedna funkcija ne vraća, ne loguje i ne ispisuje vrijednost tajne.
"""
import os

# Tačne vrijednosti kojima je release gate mjeren. Poređenje je EGZAKTNO —
# „nije prazno“ ili „liči na tačno“ je upravo ono što je propustilo produkciju.
REQUIRED_RELEASE_ENV = {
    "MATBOT_PRACTICE_PIPELINE": "universal_two_call",
    "MATBOT_PRACTICE_DIFFICULTY_LEVELS": "enabled",
    "AI_TUTOR_TIMEOUT": "45",
    "OPENAI_MODEL_TEXT": "gpt-5-mini",
    "MATBOT_REASONING_EFFORT": "low",
}

# Imena koja se NIKAD ne ispisuju ni u jednoj dijagnostici ovog modula.
_SECRET_NAMES = frozenset({
    "OPENAI_API_KEY", "FLASK_SECRET_KEY", "SECRET_KEY", "MATBOT_EMBED_TOKEN",
    "MATBOT_AUTH_TOKEN",
})


def release_configuration_problems(environ=None):
    """Lista odstupanja od obavezne konfiguracije; prazna lista = sve u redu.

    Poruka nosi SAMO ime varijable, očekivanu vrijednost i stanje („nedostaje“,
    „prazno“, „pogrešna vrijednost“) — nikad zatečenu vrijednost, jer bi tako
    pogrešno imenovana tajna mogla procuriti u log."""
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
    return problems


def effective_configuration(environ=None):
    """Sigurni, NE-TAJNI prikaz efektivne konfiguracije za startup log.

    Svjesno ne čita nijedno ime iz `_SECRET_NAMES` i ne prosljeđuje nijednu
    vrijednost koja nije u ovoj zatvorenoj listi."""
    env = os.environ if environ is None else environ

    def read(name):
        value = (env.get(name) or "").strip()
        return value or "(unset)"

    report = {
        "practice_pipeline": read("MATBOT_PRACTICE_PIPELINE"),
        "difficulty_levels": read("MATBOT_PRACTICE_DIFFICULTY_LEVELS"),
        "model": read("OPENAI_MODEL_TEXT"),
        "reasoning_effort": read("MATBOT_REASONING_EFFORT"),
        "timeout_seconds": read("AI_TUTOR_TIMEOUT"),
        "reviewer_output_tokens": read("MATBOT_MAX_OUTPUT_TOKENS_REVIEWER"),
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

    Poziva se izričito na deploy/preflight putu — nikad pri običnom uvozu, da
    lokalni rad i testovi ostanu mogući bez produkcijskih zastavica."""
    problems = release_configuration_problems(environ)
    if problems:
        raise RuntimeError(
            "Konfiguracija se razlikuje od one kojom je release gate prošao:\n  - "
            + "\n  - ".join(problems))
