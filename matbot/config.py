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

# --- Budžet izlaznih tokena SAMO za Practice generisanje zadatka -----------
# ZAŠTO POSTOJI ODVOJEN BUDŽET (živi nalaz, 6 poziva na lekciji „Proširivanje
# razlomaka“, 2 pala s `llm_invalid_output`):
#
# Kod reasoning modela (gpt-5-mini) `max_output_tokens` u Responses API-ju
# pokriva ZBIR reasoning tokena i vidljivog izlaza. Izmjereno na 4 USPJEŠNA
# poziva s identičnim ulazom (6774 ulaznih tokena):
#
#     output_tokens:    925   1018    616    809      (budžet je bio 1200)
#     reasoning_tokens: 640    832    448    640
#     vidljivi izlaz:   285    186    168    169
#
# Vidljivi strukturirani izlaz je stabilan (~170-285 tokena), ali reasoning
# varira gotovo 2x (448 → 832) pri POTPUNO ISTOM ulazu. Najgori uspješan poziv
# potrošio je 1018/1200 = 85% budžeta, tj. ostavio je samo ~15% rezerve za
# varijaciju koja je izmjereno veća od toga. Kad zbir pređe budžet, odgovor se
# vrati kao status="incomplete" / reason="max_output_tokens" BEZ `message`
# stavke → output_parsed is None → upravo posmatrana greška.
#
# Zato Practice (jedini mod koji generiše pitanje + 4 opcije + interne
# metapodatke) dobija veći budžet. Quick ostaje na MAX_OUTPUT_TOKENS jer vraća
# samo kratak `reply` (≤1200 znakova, MAX_QUICK_REPLY_CHARS) i nema izmjeren
# problem.
#
# GORNJA GRANICA: tvrdo ograničeno na MAX_OUTPUT_TOKENS_HARD_CEILING da
# pogrešno postavljena env varijabla ne može nekontrolisano podići trošak.
MAX_OUTPUT_TOKENS_HARD_CEILING = 4000
MAX_OUTPUT_TOKENS_PRACTICE = min(
    _int_env("MATBOT_MAX_OUTPUT_TOKENS_PRACTICE", 2500),
    MAX_OUTPUT_TOKENS_HARD_CEILING,
)

# --- Budžet izlaznih tokena SAMO za Explain ("Objasni mi") -----------------
# ŽIVI NALAZ C-9 (docs/CURRENT_STATE.md, audit 2026-08-01): Explain je RANIJE
# dijelio budžet sa Quick-om (MAX_OUTPUT_TOKENS=1200), iako Explain dozvoljava
# odgovor do MAX_EXPLAIN_REPLY_CHARS=4000 znakova (3.3x duže od Quick-ovog
# MAX_QUICK_REPLY_CHARS=1200), a njegov prompt eksplicitno poziva na "cijeli
# postupak" kad učenik to zatraži. Kod reasoning modela `max_output_tokens`
# pokriva ZBIR reasoning + vidljivog izlaza (vidi mjerenje iznad za Practice —
# reasoning sam varira 448-832 tokena pri identičnom ulazu); veći dozvoljen
# vidljivi izlaz uz ISTI budžet od 1200 ostavlja manje rezerve za tu varijaciju
# i povećava rizik od `llm_incomplete_max_output_tokens` (učenik dobije
# generičku grešku umjesto objašnjenja). Zato Explain dobija ISTI veći budžet
# kao Practice (2500) dok stvarno mjerenje na živim pozivima (planirano, još
# NIJE izvršeno u ovoj izmjeni) ne pokaže da treba drugačiju vrijednost.
#
# GORNJA GRANICA: isti MAX_OUTPUT_TOKENS_HARD_CEILING kao Practice.
MAX_OUTPUT_TOKENS_EXPLAIN = min(
    _int_env("MATBOT_MAX_OUTPUT_TOKENS_EXPLAIN", 2500),
    MAX_OUTPUT_TOKENS_HARD_CEILING,
)

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

# --- Slika zadatka u modu „Samo rezultat“ (matbot/imageinput.py) -----------
# Namjerno TVRDE konstante (bez env override-a): ovo su sigurnosne granice
# koje štite memoriju i trošak, pa pogrešno postavljena env varijabla ne smije
# moći da ih podigne. Svaka granica ima svoju ulogu:
#
#   MAX_IMAGE_BYTES        — koliko bajta upload-a uopšte smijemo pročitati
#   MAX_REQUEST_BYTES      — HTTP nivo (Flask MAX_CONTENT_LENGTH): slika +
#                            multipart metadata + JSON payload polje; 1 MiB
#                            rezerve iznad slike je više nego dovoljno za
#                            granice, headere i `payload` polje
#   MAX_IMAGE_PIXELS       — zaštita od decompression bombe PRIJE dekodiranja
#   MAX_IMAGE_DIMENSION    — gornja granica stranice nakon resizea; 2048 je
#                            izabrano jer zadržava čitljiv sitan matematički
#                            tekst (indeksi, razlomci) a ostaje daleko ispod
#                            granica modela
#   MAX_NORMALIZED_IMAGE_BYTES / MAX_IMAGE_DATA_URL_CHARS — granica NAKON
#                            našeg re-enkodiranja; bez njih bi PNG izlaz mogao
#                            biti veći od ulaza (npr. šum u fotografiji)
MAX_IMAGE_BYTES = 8 * 1024 * 1024                  # 8 MiB upload
MAX_REQUEST_BYTES = MAX_IMAGE_BYTES + 1024 * 1024  # 9 MiB cijeli HTTP body
MAX_IMAGE_PIXELS = 20_000_000                      # 20 MP dekodiranih piksela
MAX_IMAGE_DIMENSION = 2048
MAX_NORMALIZED_IMAGE_BYTES = 4 * 1024 * 1024       # 4 MiB nakon normalizacije
MAX_IMAGE_DATA_URL_CHARS = 6 * 1024 * 1024         # base64 je ~4/3 bajta
MIN_IMAGE_DIMENSION = 8                            # ispod ovoga nema zadatka
ALLOWED_IMAGE_FORMATS = ("JPEG", "PNG", "WEBP")

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
