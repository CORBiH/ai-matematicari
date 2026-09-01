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

# --- „Objasni mi“ (Explain) — vlastiti izbor modela --------------------------
# Do migracije (2026-08-15) Explain je NASLJEĐIVAO model adaptera
# (OPENAI_MODEL_TEXT → gpt-5-mini u produkciji), pa bi promjena te zajedničke
# varijable tiho promijenila i Explain. Sada je izbor EKSPLICITAN i vlasništvo
# KODA — isti obrazac kao FAST_MODEL iznad: auditirana vrijednost živi ovdje,
# `.env` je smije prepisati samo svjesno (varijabla ispod), a odstupanje
# efektivne vrijednosti hvata `release_config.REQUIRED_EFFECTIVE_CONFIG`
# (fail-closed na produkcijskom startu i u kapiji izdanja).
EXPLAIN_MODEL = os.environ.get("MATBOT_EXPLAIN_MODEL", "gpt-5.6-luna")
EXPLAIN_REASONING_EFFORT = os.environ.get("MATBOT_EXPLAIN_REASONING_EFFORT", "low")

# --- „Samo rezultat“ (Quick) — vlastiti izbor modela, SAMO za tekst ---------
# Isti mehanizam vlasništva kao Explain iznad: auditirana vrijednost živi u
# KODU, `.env` je smije prepisati samo svjesno, a odstupanje efektivnog izbora
# pada zatvoreno kroz `release_config.REQUIRED_EFFECTIVE_CONFIG`.
#
# MIGRACIJA 2026-08-18: tekstualni Quick prelazi s `gpt-5.6-luna` na
# `gpt-5.6-sol`. Odluka je mjerena, ne pretpostavljena — potvrdni upareni A/B
# na 150 zadataka (300 odgovora, isti prompt, naizmjeničan redoslijed krakova,
# effort `low` u oba):
#
#   matematička tačnost   Luna 124/150 (82,7%)  →  Sol 143/150 (95,3%)
#   konceptualne greške   Luna 9                →  Sol 1
#   McNemar (22:3)        p = 0,000157
#
# Presudan je bio JEDAN mehanizam: brojnost unije računata kao |A|+|B|-1.
# Luna ga je napravila u 7 od 14 prilika u kojima je presjek stvarno imao dva
# elementa; Sol nijednom. Effort NIJE poluga koja to rješava — zaseban A/B
# (low vs medium, isti korpus) dao je p = 0,69 i pogoršao baš skupove.
#
# SLIKA I DALJE IMA VLASTITI IZBOR (QUICK_IMAGE_MODEL niže) — ova migracija ga
# ne dira, kao ni Explain, Kontrolni, Tutor i Reviewer.
QUICK_MODEL = os.environ.get("MATBOT_QUICK_MODEL", "gpt-5.6-sol")
QUICK_REASONING_EFFORT = os.environ.get("MATBOT_QUICK_REASONING_EFFORT", "low")

# --- „Samo rezultat“ — poziv SA SLIKOM (migracija 2026-08-15) ---------------
# Vision benchmark (scratchpad/vision_ab_test, 11 slika x 3 modela, 97
# ground-truth zadataka): Sol 94,3% tačnosti (100% štampano, 82,1% rukopis),
# NULA čisto računskih grešaka i najpotpunija detekcija zadataka — naspram
# Luna 79,5% (15 kritičnih OCR grešaka, uklj. > → ≥ i 53°30' → 35°30') i
# Terra 85,2% (4 računske greške na tačno pročitanom ulazu). Za mod koji
# vraća gotov rezultat sa slike, profil „nikad pogrešan na čisto pročitanom“
# je presudan — zato slika ide na Sol. (Od 2026-08-18 i TEKST ide na Sol, ali
# po vlastitom, zasebno mjerenom dokazu — vidi QUICK_MODEL iznad. Dva izbora
# ostaju odvojena podešavanja i mogu se nezavisno vratiti.)
#
# detail="original" — isto podešavanje kojim je benchmark mjeren (SDK 2.52.1
# ga podržava): model vidi originalnu fotografiju, bez downscalinga.
QUICK_IMAGE_MODEL = os.environ.get("MATBOT_QUICK_IMAGE_MODEL", "gpt-5.6-sol")
QUICK_IMAGE_REASONING_EFFORT = os.environ.get(
    "MATBOT_QUICK_IMAGE_REASONING_EFFORT", "low")
QUICK_IMAGE_DETAIL = os.environ.get("MATBOT_QUICK_IMAGE_DETAIL", "original")

# --- „Sutra imam kontrolni“ (Kontrolni) — vlastiti izbor modela -------------
# Isti mehanizam vlasništva kao Explain/Quick (2026-08-15): auditirana
# vrijednost živi u KODU, `.env` je smije prepisati samo svjesno, a odstupanje
# efektivne vrijednosti pada zatvoreno kroz
# `release_config.REQUIRED_EFFECTIVE_CONFIG`. Mod NAMJERNO ne nasljeđuje
# nijednu generičku varijablu (OPENAI_MODEL_TEXT) — batch generisanje testa je
# zaseban put i njegov model ne smije tiho odlutati s tuđom migracijom.
# --- FAZA 3C: mjesečni izvještaj za roditelja -------------------------------
# Odvojen od tutorskih modela jer je i posao odvojen: sva aritmetika je već
# gotova, pa ovaj poziv samo piše prozu. Zato NIZAK effort i skroman budžet —
# skuplje razmišljanje ovdje ne kupuje ništa, a plaća se po izvještaju.
REPORTING_MODEL = os.environ.get("MATBOT_REPORTING_MODEL", "gpt-5.6-luna")
REPORTING_REASONING_EFFORT = os.environ.get(
    "MATBOT_REPORTING_REASONING_EFFORT", "low")
MAX_OUTPUT_TOKENS_REPORTING = _int_env("MATBOT_MAX_OUTPUT_TOKENS_REPORTING", 2000)
# Rok je vlastiti i kraći od tutorskog: administrator čeka pred ekranom, a
# izvještaj koji kasni nije hitan kao odgovor djetetu usred zadatka.
REPORTING_TIMEOUT_S = _float_env("MATBOT_REPORTING_TIMEOUT", 40.0)

KONTROLNI_MODEL = os.environ.get("MATBOT_KONTROLNI_MODEL", "gpt-5.6-luna")
KONTROLNI_REASONING_EFFORT = os.environ.get(
    "MATBOT_KONTROLNI_REASONING_EFFORT", "low")
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
#   "lessons"      — samo izričito navedene (pilot; podrazumijevano u kodu)
#   "model_backed" — svaka lekcija koja ionako ide na model (PRODUKCIJA)
#   "off"          — nikad (potpuni rollback na `_two_call`)
#
# PRODUKCIJSKU VRIJEDNOST POSTAVLJA DEPLOY, NE OVAJ PODRAZUMIJEVANI IZRAZ.
# Ugrađivanje `model_backed` ovdje prevelo bi CIJELU testnu svitu na brzu rutu i
# time ugasilo dokaze univerzalnog dvopozivnog puta koji ostaje rollback. Zato
# kod čuva pilot vrijednost, a deploy upisuje `model_backed` u produkcijski
# `.env` (vidi .github/workflows/deploy-vps.yml) — isti mehanizam kojim se već
# upisuje APP_VERSION. Startup log ispisuje efektivnu vrijednost, pa tiho
# odstupanje nije moguće.
FAST_SINGLE_CALL_SCOPE = os.environ.get("MATBOT_FAST_SINGLE_CALL_SCOPE", "lessons")
# Pojedinačno isključenje unutar opsega — rollback jedne lekcije bez gašenja rute.
_FAST_EXCLUDE_RAW = os.environ.get("MATBOT_FAST_SINGLE_CALL_EXCLUDE", "")


def fast_single_call_scope() -> str:
    return (os.environ.get("MATBOT_FAST_SINGLE_CALL_SCOPE", "").strip().lower()
            or FAST_SINGLE_CALL_SCOPE)


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


# --- UKUPAN ROK GENERISANJA KONTROLNOG TESTA -------------------------------
# ŽIVI NALAZ (produkcija, 2026-08-16): jedno generisanje kontrolnog je isteklo,
# a odmah ponovljeni pokušaj je prošao normalno. Lanac rokova pokazuje ZAŠTO je
# to bilo moguće:
#
#   pregledač (fetch abort)      90 s   (templates/index.html: EXAM_ABORT_MS)
#   nginx (proxy_read_timeout)   ~60 s  PODRAZUMIJEVANO — živi na VPS-u, nije
#                                       u repou i odavde se ne može pročitati
#   gunicorn                    120 s   (Dockerfile --timeout)
#   OpenAI SDK, PO POZIVU        45 s   (AI_TUTOR_TIMEOUT, max_retries=0)
#   kontrolni UKUPNO             ---    NIJE POSTOJAO
#
# Bez ukupnog roka legitiman dvopozivni zahtjev (batch + uslovna popravka)
# smije trajati do ~90 s, što probija podrazumijevani nginx rok od 60 s — tada
# učenik dobije 504 umjesto kontrolisane poruke. Practice je isti problem
# odavno riješio (`practice_turn_deadline_s` + suženi drugi poziv); kontrolni
# to nije imao.
#
# IZBOR 50 s: mjereno p50 ≈ 9–11 s, p95 ≈ 13–15 s, najgori viđen dvopozivni
# test ≈ 21 s. Rok od 50 s daje preko dvostruke rezerve nad najgorim izmjerenim
# slučajem, a ostaje ispod podrazumijevanog proxy roka — pa se sporo
# generisanje završi NAŠOM porukom („Nismo uspjeli pripremiti test.“) i
# ponudom „Pokušaj ponovo“, nikad sirovim 504. Nema trećeg poziva ni retryja:
# kad ostatak budžeta padne ispod MIN_STAGE_BUDGET_S, popravka se preskače i
# paket pada zatvoreno.
_KONTROLNI_DEADLINE_S = _float_env("MATBOT_KONTROLNI_DEADLINE_S", 0.0)


def kontrolni_deadline_s():
    return _KONTROLNI_DEADLINE_S or 50.0
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

# --- Budžet izlaznih tokena SAMO za Kontrolni batch poziv ------------------
# ZAŠTO VLASTITI, VEĆI BUDŽET (i vlastita tvrda granica): jedan Kontrolni
# poziv generiše PET kompletnih MCQ pitanja (tekst + 4 opcije + očekivani
# odgovor + kratko rješenje po pitanju) — vidljivi izlaz je po konstrukciji
# ~5x veći od jednog Practice zadatka, a `max_output_tokens` kod reasoning
# modela pokriva i reasoning tokene (vidi mjerenje uz
# MAX_OUTPUT_TOKENS_PRACTICE). TRADE-OFF je izričit: jedan batch od ~6000
# tokena zamjenjuje pet zasebnih poziva od po ~1200+ (ukupno jeftinije i brže),
# a tvrda granica od 8000 sprječava da pogrešna env varijabla nekontrolisano
# podigne trošak/latenciju — kao i svuda, env je smije samo SPUSTITI.
MAX_OUTPUT_TOKENS_KONTROLNI_CEILING = 8000
MAX_OUTPUT_TOKENS_KONTROLNI = min(
    _int_env("MATBOT_MAX_OUTPUT_TOKENS_KONTROLNI", 6000),
    MAX_OUTPUT_TOKENS_KONTROLNI_CEILING,
)

# Ograničenja ulaza (server odbija prevelike poruke prije AI poziva)
MAX_MESSAGE_CHARS = _int_env("MATBOT_MAX_MESSAGE_CHARS", 4000)
MAX_TASK_CHARS = 600
MAX_REPLY_CHARS = 2500
MAX_EXPLAIN_REPLY_CHARS = 4000  # objašnjenje smije biti nešto duže od practice feedbacka
MAX_QUICK_REPLY_CHARS = 1200  # Quick ("Samo rezultat") je namjerno kratak i direktan
# GRANICA ZA OBJAŠNJENJE U QUICK-u (v2, 2026-08-16). „Samo rezultat" opisuje
# PODRAZUMIJEVANI oblik odgovora, a ne zabranu: kad učenik izričito traži
# postupak („objasni", „zašto", „pokaži korake"), odgovor smije biti duži — ali
# i dalje ograničen. Nije esej: 2400 znakova je otprilike jedan ekran koraka,
# dvostruko od rezultatske granice, i dalje daleko ispod Explain granice (4000).
MAX_QUICK_EXPLANATION_CHARS = 2400
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

# JEDAN NAGOVJEŠTAJ, NE LJESTVICA (zahtjev iz produkcije). Učenik traži pomoć
# jednom i dobija JEDAN koristan strateški nagovještaj; ponovni klik vraća ISTI
# tekst i NE troši novi poziv. Puno rješenje ostaje zasebna radnja („Uradi ga
# ti“). Ljestvica 1→2→3 ostaje u kodu kao rollback (`disabled`), jer je njena
# vršna kompozicija dokazana i vezana za verifikovani artefakt.
PRACTICE_SINGLE_HINT_FLAG = "MATBOT_PRACTICE_SINGLE_HINT"


def practice_single_hint_enabled():
    """True osim kad je izričito isključeno — jedan nagovještaj je zatečeno
    produkcijsko ponašanje od ovog izdanja."""
    value = (os.environ.get(PRACTICE_SINGLE_HINT_FLAG, "") or "").strip().lower()
    return value != "disabled"
MAX_SESSIONS_IN_MEMORY = 2000
MAX_RECENT_FAMILIES = 6      # historija porodica zadataka (LRU izbor + prompt)
MAX_RECENT_SIGNATURES = 8    # potpisi zadataka za otkrivanje doslovnog ponavljanja
# Koliko STRUKTURA zadatka sesija pamti za provjeru raznolikosti. Prozor je
# namjerno kratak: dovoljno da „daj novi“ ne vrati istu vježbu, a stanje sesije
# ne raste (isti princip kao svaka druga historija ovdje).
MAX_RECENT_STRUCTURES = 6

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


# --- Izvještajna baza (Turso/libSQL) — SAMO identitet učenika ---------------
# PRVI podsistem MAT-BOT-a koji uopšte piše van procesa. Tutor i dalje ne čuva
# ništa: ovdje se vodi samo mapiranje vanjskog korisnika na interni
# `students.id`, da bi kasniji mjesečni izvještaji imali stabilan identitet.
#
# NAMJERNO NE PIŠE „autentikovanog“: identitet je Thinkific e-mail iz URL-a
# lekcije, dakle PRIPISIVANJE za izvještaj, a ne dokazana autentifikacija —
# puna granica i šta se s njom NE smije raditi je u
# `matbot/student_identity.py`. Vidi i `matbot/reporting_db.py`.
#
# TAJNE SE ČITAJU SAMO PO IMENU. Kao i `SECRET_KEY` iznad, vrijednost se nikad
# ne loguje, ne vraća iz dijagnostike i ne ulazi u `deploy/production_release.env`
# (taj fajl je u repozitoriju i tajnu ne smije vidjeti). Produkcijske
# vrijednosti žive isključivo u `.env` na VPS-u, koji `docker-compose.yml`
# prosljeđuje kontejneru kroz `env_file`.
#
# Funkcije (a ne konstante učitane pri importu) namjerno: tako test i
# dijagnostika mogu promijeniti okruženje bez ponovnog učitavanja modula —
# isti obrazac kao `fast_single_call_scope()`.
def turso_database_url():
    return (os.environ.get("TURSO_DATABASE_URL", "") or "").strip()


def turso_auth_token():
    return (os.environ.get("TURSO_AUTH_TOKEN", "") or "").strip()


def reporting_db_configured():
    """Obje vrijednosti moraju postojati. Polovična konfiguracija je ISTO što i
    nikakva — nikad se ne pokušava „bez tokena, možda prođe“."""
    return bool(turso_database_url() and turso_auth_token())


# ROK JEDNE IZVJEŠTAJNE OPERACIJE. Namjerno kratak i namjerno NEZAVISAN od
# `AI_TUTOR_TIMEOUT`: izvještavanje je sporedno, pa ne smije dodati primjetno
# kašnjenje tutorskom turnu. Nema retryja — jedan pokušaj, pa odustajanje.
REPORTING_DB_TIMEOUT_S = _float_env("MATBOT_REPORTING_DB_TIMEOUT_S", 2.0)

# Koliko izvještajnih operacija smije biti u letu istovremeno. Iznad toga se
# poziv ODMAH odbacuje umjesto da se stane u red — red bi pretvorio sporu bazu
# u kašnjenje tutorskog turna, što je tačno ono što je zabranjeno.
REPORTING_DB_MAX_INFLIGHT = _int_env("MATBOT_REPORTING_DB_MAX_INFLIGHT", 2)

# Očekivana verzija izvještajne šeme (`schema_migrations`). Dijagnostika je
# poredi; aplikacija NIKAD sama ne pokreće migracije.
#
# ZAŠTO 2 (ispravka poslije živog incidenta): ova vrijednost je ostala na 1 i
# nakon što je izdanje uvelo šemu v2, pa je `python -m matbot.reporting_db --check`
# nad NEMIGRIRANOM produkcijom prijavio „schema_version: 1 (expected 1) -> OK".
# Provjera je time tvrdila da je sve u redu upravo dok je nedostajala cijela
# verzija 2. Broj mora pratiti `reporting_schema.CURRENT_SCHEMA_VERSION`; test
# to i dokazuje, da dvije vrijednosti ne mogu odlutati jedna od druge.
# Faza 3D podiže ovo na 3 (evidencija časova). Isti razlog kao gore: broj mora
# pratiti `reporting_schema.CURRENT_SCHEMA_VERSION`, inače provjera zdravlja
# opet počne tvrditi „OK" nad bazom kojoj nedostaje cijela verzija.
REPORTING_SCHEMA_VERSION = _int_env("MATBOT_REPORTING_SCHEMA_VERSION", 6)
