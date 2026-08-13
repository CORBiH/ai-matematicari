"""Konfiguracija iz environment varijabli. Nikad ne loguje vrijednosti tajni."""
import os


PRACTICE_DIFFICULTY_LEVELS_FLAG = "enabled"


def exact_env_flag(name, expected):
    """Return true only when an environment value matches exactly.

    Feature switches use this instead of truthiness so a typo cannot activate a
    candidate controller in production.
    """
    return (os.environ.get(name, "") or "") == expected


def practice_difficulty_levels_enabled():
    """The sole authority for MATBOT's opt-in difficulty controller."""
    return exact_env_flag("MATBOT_PRACTICE_DIFFICULTY_LEVELS",
                          PRACTICE_DIFFICULTY_LEVELS_FLAG)


def deterministic_practice_enabled():
    """Faza 4H: deterministička strategija izvršenja unutar Practice
    orkestratora. PODRAZUMIJEVANO UKLJUČENA — `disabled` je izričit produkcijski
    rollback (i način da testovi model-strategije ispitaju model-put na lekciji
    koju inače pokriva potpun deterministički generator)."""
    value = (os.environ.get("MATBOT_DETERMINISTIC_PRACTICE", "") or "").strip().lower()
    return value != "disabled"


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


class ConfigurationError(RuntimeError):
    """Konfiguracija je prisutna ali neupotrebljiva — pada ODMAH, ne tiho.

    Namjerno drukčije od `_int_env`, koji na neispravnu vrijednost tiho vrati
    podrazumijevanu. Za budžet izlaznih tokena tiho vraćanje je opasno: pogrešno
    postavljena varijabla bi vratila presijecanje odgovora u produkciju, a to se
    vidi tek kao neuspio turn pred učenikom."""


def _validated_token_budget(name, default, minimum, ceiling):
    """Cio broj iz okruženja unutar [minimum, ceiling], ili ConfigurationError.

    Prazna/odsutna vrijednost daje `default` (unazad kompatibilno). Nula,
    negativna, decimalna, nenumerička i nerazumno velika vrijednost padaju
    odmah — poruka nosi SAMO ime varijable i granice, nikad vrijednost tajne."""
    raw = (os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigurationError(
            f"{name} mora biti cio broj između {minimum} i {ceiling}") from None
    if value < minimum or value > ceiling:
        raise ConfigurationError(
            f"{name} mora biti između {minimum} i {ceiling}")
    return value


# Model i AI parametri (interaktivni Practice put)
OPENAI_MODEL_TEXT = os.environ.get("OPENAI_MODEL_TEXT", "gpt-5-mini")
REASONING_EFFORT = os.environ.get("MATBOT_REASONING_EFFORT", "low")

# --- Univerzalni dvopozivni Practice put (Tutor + Reviewer) ----------------
# Dva ODVOJENA izbora modela, oba podrazumijevano ista kao tekstualni model.
# Postoje da bi se recenzent kasnije mogao spustiti na jeftiniji model BEZ
# ijedne izmjene Practice logike — poslovna logika ne zna koji je model u igri.
TUTOR_MODEL = os.environ.get("MATBOT_TUTOR_MODEL", OPENAI_MODEL_TEXT)
REVIEWER_MODEL = os.environ.get("MATBOT_REVIEWER_MODEL", OPENAI_MODEL_TEXT)

# --- EKSPERIMENTALNI BRZI JEDNOPOZIVNI PUT (`fast_single_call`) ------------
# ZAŠTO POSTOJI: široki živi audit je pokazao da su i kašnjenje i padovi
# vezani za RUTU, ne za razred — deterministička ruta ~0 s, ugovorna ~8 s, a
# univerzalna dvopozivna 30–41 s uz najviše odbijanja. Ovaj put pokušava da
# NORMALAN model-podržan turn spusti na JEDAN poziv, a recenzenta zadrži kao
# USLOVNU eskalaciju kad deterministički preflight nađe dokazan defekt.
#
# NIŠTA SE NE MIJENJA PODRAZUMIJEVANO: bez izričitih varijabli okruženja
# produkcija ostaje na `universal_two_call` s istim modelom. Model i ruta se
# biraju NEZAVISNO, da se A/B poređenje može voditi bez izmjene koda.
FAST_MODEL = os.environ.get("MATBOT_FAST_MODEL", "gpt-5.6-luna")
FAST_REASONING_EFFORT = os.environ.get("MATBOT_FAST_REASONING_EFFORT", "low")
# ŽIVI NALAZ (val 2, 60 turnova): brza ruta je eskalirala na recenzenta koji
# radi na SPOROM modelu, pa je 7 od 12 eskalacija umrlo na recenzentskom roku
# (padovi 37–41 s naspram 33–35 s kod uspjelih). „Brza“ ruta čiji je popravak
# spor nije brza — popravak zato ide istim brzim modelom.
FAST_REVIEWER_MODEL = os.environ.get("MATBOT_FAST_REVIEWER_MODEL", FAST_MODEL)
# Lekcije za koje je brzi put uključen — zarezom odvojena lista ID-jeva.
# Prazno (podrazumijevano) znači: nijedna lekcija, put je potpuno neaktivan.
_FAST_LESSONS_RAW = os.environ.get("MATBOT_FAST_SINGLE_CALL_LESSONS", "")


def fast_single_call_lessons() -> frozenset:
    """Skup lekcija na eksperimentalnoj brzoj ruti (podaci, ne grana po ID-ju).

    Čita se pri svakom pozivu da bi test/evaluacija mogli mijenjati opseg bez
    ponovnog učitavanja modula."""
    raw = os.environ.get("MATBOT_FAST_SINGLE_CALL_LESSONS", _FAST_LESSONS_RAW)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


# OPSEG BRZE RUTE — pravilo, ne spisak ID-jeva. Spisak od stotinu lekcija bio bi
# grananje po lekciji prerušeno u podatak; ruta se zato bira po KLASI lekcije.
# Deterministička strategija odlučuje PRIJE ove tačke, pa je sve što dovde
# stigne po definiciji modelski podržana lekcija.
#
#   "lessons"      — samo izričito navedene (zatečeno ponašanje, pilot)
#   "model_backed" — svaka lekcija koja ionako ide na model
#   "off"          — nikad (potpuni rollback na `_two_call`)
FAST_SINGLE_CALL_SCOPE = os.environ.get("MATBOT_FAST_SINGLE_CALL_SCOPE", "lessons")
# Pojedinačno isključenje unutar opsega — rollback jedne lekcije bez gašenja rute.
_FAST_EXCLUDE_RAW = os.environ.get("MATBOT_FAST_SINGLE_CALL_EXCLUDE", "")


def fast_single_call_scope() -> str:
    return os.environ.get("MATBOT_FAST_SINGLE_CALL_SCOPE",
                          FAST_SINGLE_CALL_SCOPE).strip().lower()


def fast_single_call_excluded() -> frozenset:
    raw = os.environ.get("MATBOT_FAST_SINGLE_CALL_EXCLUDE", _FAST_EXCLUDE_RAW)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def fast_single_call_enabled_for(lesson_id) -> bool:
    """Da li ova modelski podržana lekcija ide brzom rutom."""
    if not lesson_id:
        return False
    scope = fast_single_call_scope()
    if scope == "off" or lesson_id in fast_single_call_excluded():
        return False
    if scope == "model_backed":
        return True
    return lesson_id in fast_single_call_lessons()
AI_TIMEOUT_S = _float_env("AI_TUTOR_TIMEOUT", 30.0)

# Faza 4H (Workstream L): rok CIJELOG Practice turna. Podrazumijevano tačno
# 2×AI_TIMEOUT_S — dakle bajt za bajt zatečeno ponašanje — a produkcija ga
# smije spustiti da skoro istekao Tutor poziv ne započne dug recenzentski.
_PRACTICE_TURN_DEADLINE_S = _float_env("MATBOT_TURN_DEADLINE_S", 0.0)
# Ispod ovog ostatka roka drugi poziv nema smisla ni pokušavati.
MIN_STAGE_BUDGET_S = 5.0


def practice_turn_deadline_s():
    return _PRACTICE_TURN_DEADLINE_S or (2 * AI_TIMEOUT_S)
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
#
# PODIZANJE 2500 → 4000 (PP-1 LIVE-150, scenario E015): Tutorov odgovor na
# „daj laksi“ nakon koordinatno-geometrijskog zadatka nivoa 3 (egzaktan odgovor
# $Q(347/169, 157/169, 36/169)$) presječen je na 2500 tokena —
# `llm_output_limit_truncated`, ValidationError „EOF while parsing a string“,
# turn je pao zatvoreno i učenik je dobio tehnički fallback umjesto zadatka.
# Skromno povećanje latencije/troška je izričito prihvaćeno kao proizvodna
# odluka; granica ostaje TVRDA na MAX_OUTPUT_TOKENS_HARD_CEILING, pa
# podrazumijevana vrijednost sada sjedi TAČNO na njoj i env varijabla je više
# ne može podići — samo spustiti.
MAX_OUTPUT_TOKENS_HARD_CEILING = 4000
MAX_OUTPUT_TOKENS_PRACTICE = min(
    _int_env("MATBOT_MAX_OUTPUT_TOKENS_PRACTICE", 4000),
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

# --- Budžet izlaznih tokena SAMO za RECENZENTA (drugi Practice poziv) ------
# ŽIVI RELEASE GATE (commit 458d12a, scenario `harder_level2`): recenzentov
# odgovor je presječen usred stringa („EOF while parsing a string at line 85“)
# pri budžetu od 2500 tokena. Turn je pao zatvoreno — ispravno — ali je pao.
#
# MJERENJE nad 347 uspješnih poziva iz živih artefakata Faze 4E (dvije F4E
# kampanje + A+B), izlazni tokeni po pozivu:
#
#     tutor      n=203  med=1190  p95=1671  MAX=1938   → 0 % blizu granice
#     reviewer   n=144  med=1428  p95=1905  MAX=2395   → 95,8 % budžeta
#       decision=correct  n=73  med=1572  p95=2033  MAX=2183
#       decision=approve  n=69  med=1357  p95=1818  MAX=2395
#
# Tutor nikad nije prišao granici i zato ostaje NEPROMIJENJEN. Recenzent jeste:
# najveći uspješan izlaz ostavio je 105 tokena rezerve, a uzorak je cenzurisan
# (svaki poziv koji je htio više od 2500 je presječen i u uzorku ga nema).
#
# IZBOR 3200: pokriva izmjereni maksimum (2395) sa 34 % rezerve i p99 (2183) sa
# 47 %, a ostaje ispod tvrde granice od 4000. Namjerno NIJE „koliko god treba“:
# `max_output_tokens` kod reasoning modela pokriva i reasoning tokene, pa veći
# budžet znači i duži najgori slučaj. Izmjereno je 25,6 s na ~2500 tokena, a
# AI_TUTOR_TIMEOUT je 45 s po pozivu — 3200 ostaje unutar tog okvira, dok bi
# 4000 prišlo timeoutu i samo premjestilo kvar iz presjecanja u istek vremena.
MAX_OUTPUT_TOKENS_REVIEWER_MIN = 1500
MAX_OUTPUT_TOKENS_REVIEWER_DEFAULT = 3200


def reviewer_output_budget():
    """Validiran budžet recenzenta — jedini izvor istine za drugi poziv."""
    return _validated_token_budget(
        "MATBOT_MAX_OUTPUT_TOKENS_REVIEWER",
        MAX_OUTPUT_TOKENS_REVIEWER_DEFAULT,
        MAX_OUTPUT_TOKENS_REVIEWER_MIN,
        MAX_OUTPUT_TOKENS_HARD_CEILING,
    )


MAX_OUTPUT_TOKENS_REVIEWER = reviewer_output_budget()

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
