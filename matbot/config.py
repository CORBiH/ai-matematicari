"""Konfiguracija iz environment varijabli. Nikad ne loguje vrijednosti tajni."""
import os


def _float_env(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


# Model i AI parametri (interaktivni Practice put)
OPENAI_MODEL_TEXT = os.environ.get("OPENAI_MODEL_TEXT", "gpt-5-mini")
REASONING_EFFORT = os.environ.get("MATBOT_REASONING_EFFORT", "low")
AI_TIMEOUT_S = _float_env("AI_TUTOR_TIMEOUT", 30.0)
MAX_OUTPUT_TOKENS = _int_env("MATBOT_MAX_OUTPUT_TOKENS", 1200)

# Ograničenja ulaza (server odbija prevelike poruke prije AI poziva)
MAX_MESSAGE_CHARS = _int_env("MATBOT_MAX_MESSAGE_CHARS", 4000)
MAX_TASK_CHARS = 600
MAX_REPLY_CHARS = 2500
MAX_EXPLAIN_REPLY_CHARS = 4000  # objašnjenje smije biti nešto duže od practice feedbacka
MAX_QUICK_REPLY_CHARS = 1200  # Quick ("Samo rezultat") je namjerno kratak i direktan
MAX_EXPECTED_ANSWER_CHARS = 400
MAX_OPTION_TEXT_CHARS = 200
MAX_HISTORY_ITEMS = _int_env("MATBOT_MAX_HISTORY_ITEMS", 6)
MAX_HISTORY_CHARS_PER_ITEM = _int_env("MATBOT_MAX_HISTORY_CHARS_PER_ITEM", 3000)

# Session store
MAX_RECENT_TASKS = 3
MAX_RECENT_TURNS = 3
MAX_HINT_LEVEL = 3
MAX_SESSIONS_IN_MEMORY = 2000
MAX_RECENT_FAMILIES = 6      # historija porodica zadataka (LRU izbor + prompt)
MAX_RECENT_SIGNATURES = 8    # potpisi zadataka za otkrivanje doslovnog ponavljanja

# Gornja granica za OBIČAN feedback na PRVI pogrešan klik ("Netačno." + hint).
# Nije tvrdo sječenje: shape_first_wrong_feedback skraćuje SAMO na sigurnoj
# granici rečenice s uravnoteženim $...$ — validan MathJax se nikad ne lomi.
MAX_FIRST_WRONG_FEEDBACK_CHARS = 320

# --- Security hardening (Faza: token + rate limit + concurrency lock) ------
# FLASK_SECRET_KEY je primarni naziv (novi security kod). SECRET_KEY je
# kompatibilni alias — produkcijski VPS ga već ima postavljenog iz ranije faze.
# Ako postoje OBA, FLASK_SECRET_KEY ima prednost. OBAVEZAN u produkciji —
# app.py odbija start ako nijedan nije postavljen (vidi require_secret_key niže).
# Testovi eksplicitno postavljaju jasno označen nesiguran test secret
# (tests/conftest.py) — jedino mjesto gdje je to dozvoljeno.
def _resolve_secret_key():
    return os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY") or ""


SECRET_KEY = _resolve_secret_key()


def require_secret_key(secret):
    """Baca RuntimeError ako je secret prazan. Nikad ne uključuje vrijednost
    secreta (ni tuđu ni ničiju) u poruku greške."""
    if not secret:
        raise RuntimeError(
            "FLASK_SECRET_KEY (ili kompatibilni alias SECRET_KEY) nije postavljen. "
            "Postavi jedan od njih u .env prije pokretanja (npr. `openssl rand -hex 32`) "
            "— bez njega se embed_token ne može bezbjedno potpisati, pa aplikacija "
            "namjerno ne starta."
        )
    return secret

# Kratkotrajni potpisani frontend token (anonimna zaštita, NE Thinkific identitet).
TOKEN_TTL_SECONDS = _int_env("MATBOT_TOKEN_TTL_SECONDS", 7200)

# Rate limiting — dva nivoa, oba podesiva. Brojači se gube na restart (OK za
# ovu fazu). Vidi matbot/ratelimit.py.
SESSION_LIMIT_PER_MINUTE = _int_env("MATBOT_SESSION_LIMIT_PER_MINUTE", 15)
SESSION_LIMIT_PER_HOUR = _int_env("MATBOT_SESSION_LIMIT_PER_HOUR", 150)
IP_LIMIT_PER_MINUTE = _int_env("MATBOT_IP_LIMIT_PER_MINUTE", 120)
IP_LIMIT_PER_HOUR = _int_env("MATBOT_IP_LIMIT_PER_HOUR", 1000)
