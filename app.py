from flask import (
    Flask, render_template, request, jsonify, Response, stream_with_context,
)
from dotenv import load_dotenv
import os, base64, json, html, logging, time
import hmac, hashlib, fnmatch
from datetime import timedelta
import requests
from urllib.parse import urlsplit
from openai import OpenAI
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --- Optional PIL (samo za /mathpix/selftest sliku) ---
try:
    from PIL import Image, ImageDraw
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

# ---------------- Bootstrapping ----------------
load_dotenv(override=False)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("matbot")

LOCAL_MODE = os.getenv("LOCAL_MODE", "0") == "1"

SECURE_COOKIES = os.getenv("COOKIE_SECURE", "0") == "1"
app = Flask(__name__)
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SECURE_COOKIES,
    SESSION_COOKIE_NAME="matbot_session_v2",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SEND_FILE_MAX_AGE_DEFAULT=0,
)

# CORS: ako je CORS_ORIGINS postavljen (zarezom odvojene domene), ograniči;
# inače zadrži dosadašnje ponašanje (sve domene) radi kompatibilnosti.
_cors_origins = [o.strip() for o in (os.getenv("CORS_ORIGINS") or "").split(",") if o.strip()]
if _cors_origins:
    CORS(app, supports_credentials=True, origins=_cors_origins)
else:
    CORS(app, supports_credentials=True)

# Produkcija (cloudbuild) postavlja FLASK_SECRET_KEY; stariji setupi SECRET_KEY.
_secret_key = (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "").strip()
if not _secret_key:
    _secret_key = "tajna_lozinka"
    if not LOCAL_MODE:
        log.error("FLASK_SECRET_KEY/SECRET_KEY nije postavljen — koristi se NESIGURAN default! "
                  "Postavi FLASK_SECRET_KEY u okruženju.")
app.secret_key = _secret_key

MAX_MB = int(os.getenv("MAX_CONTENT_LENGTH_MB", "20"))
app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

# Najveća slika koju tutor prima kao prilog.
IMAGE_FETCH_MAX_MB = int(os.getenv("IMAGE_FETCH_MAX_MB", "20"))

HARD_TIMEOUT_S = float(os.getenv("HARD_TIMEOUT_S", "120"))
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", str(HARD_TIMEOUT_S)))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
# Modularni tutor: kraći timeout i manje retry-a — učenik ne smije čekati minutama
# (frontend prekida na ~60s; vidi AbortController u templates/index.html).
AI_TUTOR_TIMEOUT = float(os.getenv("AI_TUTOR_TIMEOUT", "45"))
AI_TUTOR_MAX_RETRIES = int(os.getenv("AI_TUTOR_MAX_RETRIES", "1"))

# --- Phase 2: potpisani kratkotrajni token za /api/ai-tutor/* ----------------------
# Server ga UGRAĐUJE u stranicu (GET /) kao <meta>; frontend ga šalje u headeru
# X-Tutor-Token. Štiti skupe tutor pozive od direktnog skriptovanja API-ja.
# Enforcement je UKLJUČEN samo kada je secret postavljen (sigurno uvođenje);
# LOCAL_MODE uvijek prolazi. Detalji: docs/deploy/embed-token.md
AI_TUTOR_EMBED_SECRET = (os.getenv("AI_TUTOR_EMBED_SECRET") or "").strip()
AI_TUTOR_TOKEN_TTL_S = int(os.getenv("AI_TUTOR_TOKEN_TTL_S", "7200"))


def mint_embed_token(expires_at: int | None = None) -> str:
    """"<exp_unix>.<hmac_sha256(secret, exp)>" ili "" kada secret nije postavljen."""
    if not AI_TUTOR_EMBED_SECRET:
        return ""
    exp = int(expires_at if expires_at is not None else time.time() + AI_TUTOR_TOKEN_TTL_S)
    sig = hmac.new(AI_TUTOR_EMBED_SECRET.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_embed_token(token: str) -> bool:
    """LOCAL_MODE → uvijek OK; bez secreta → OK (enforcement isključen);
    inače: važeći potpis + neistekao rok."""
    if LOCAL_MODE:
        return True
    if not AI_TUTOR_EMBED_SECRET:
        return True
    try:
        exp_s, sig = (token or "").split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    expected = hmac.new(AI_TUTOR_EMBED_SECRET.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _embed_token_ok() -> bool:
    token = request.headers.get("X-Tutor-Token") or request.args.get("emb") or ""
    return verify_embed_token(token)


# --- Embed gate: aplikaciji se ulazi SAMO kroz dozvoljeni embed (Thinkific) --------
# Rupa koju ovo zatvara: token se ugrađuje u GET /, pa je do sada svako ko zna URL
# mogao otvoriti stranicu, pokupiti svjež token i koristiti bota direktno.
#
# Zašto je provjera OVDJE, a ne na /api/*: tutor pozivi su SAME-ORIGIN (naša
# stranica zove naš API), pa bi traženje Thinkific origina na API-ju odbilo
# legitimne pozive. Cross-site zahtjev iz Thinkific iframe-a stiže tačno na GET /,
# i tu se odlučuje hoće li se token uopšte iskovati.
EMBED_ALLOWED_ORIGINS = [
    o.strip().rstrip("/")
    for o in (os.getenv("EMBED_ALLOWED_ORIGINS") or "").split(",")
    if o.strip()
]


def _origin_matches(origin: str, patterns: list[str]) -> bool:
    """Poređenje sa podrškom za wildcard u hostu ("https://*.thinkific.com")."""
    origin = (origin or "").strip().rstrip("/").lower()
    if not origin:
        return False
    return any(fnmatch.fnmatch(origin, p.lower()) for p in patterns)


def _embed_referrer_origin() -> str:
    """Origin one stranice koja NAS ugrađuje (ne naš vlastiti).

    Na navigaciji iframe-a browser šalje Referer stranice-domaćina. Origin header
    se na GET navigaciji uglavnom ne šalje, pa je Referer glavni signal; uz
    strict-origin-when-cross-origin politiku Thinkific pošalje bar goli origin.
    """
    origin = (request.headers.get("Origin") or "").strip()
    if origin and origin.lower() != "null":
        return origin
    referer = (request.headers.get("Referer") or "").strip()
    if not referer:
        return ""
    parts = urlsplit(referer)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def embed_entry_allowed() -> bool:
    """Smije li ovaj zahtjev otvoriti aplikaciju? Prazna lista = kapija ISKLJUČENA."""
    if LOCAL_MODE or not EMBED_ALLOWED_ORIGINS:
        return True
    return _origin_matches(_embed_referrer_origin(), EMBED_ALLOWED_ORIGINS)


_EMBED_GATE_HTML = (
    "<!doctype html><html lang=\"bs\"><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>MAT-BOT</title>"
    "<style>body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;"
    "display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;"
    "padding:24px}main{max-width:420px;text-align:center}h1{font-size:1.25rem;margin:0 0 .5rem}"
    "p{color:#94a3b8;line-height:1.5;margin:0}</style>"
    "<main><h1>MAT-BOT je dostupan kroz lekciju</h1>"
    "<p>Otvori bota iz svoje lekcije na platformi kursa. "
    "Direktan pristup ovoj adresi nije omogućen.</p></main></html>"
)


_EMBED_TOKEN_DENIED = {
    "error": "invalid_token",
    "detail": "Sesija je istekla ili pristup nije dozvoljen. Osvježi stranicu pa pokušaj ponovo.",
}

# --- Rate limiting (env-podesivo; default vrlo darežljiv da ne smeta učenicima) ---
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "1") == "1"

def _client_ip():
    # Cloud Run je iza proxyja: prva adresa u X-Forwarded-For je stvarni klijent.
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return xff or get_remote_address()

def _submit_rate_limit():
    return os.getenv("RATE_LIMIT_SUBMIT", "30 per minute")

def _diag_rate_limit():
    return os.getenv("RATE_LIMIT_DIAG", "10 per minute")

limiter = Limiter(
    key_func=_client_ip,
    app=app,
    default_limits=[],
    storage_uri=os.getenv("RATE_LIMIT_STORAGE_URI", "memory://"),
    enabled=RATE_LIMIT_ENABLED,
)

@app.errorhandler(429)
def rate_limited(e):
    return jsonify({
        "error": "rate_limited",
        "detail": "Previše zahtjeva u kratkom vremenu. Sačekaj malo pa pokušaj ponovo.",
    }), 429

SYNC_SOFT_TIMEOUT_S = float(os.getenv("SYNC_SOFT_TIMEOUT_S", "8"))
HEAVY_TOKEN_THRESHOLD = int(os.getenv("HEAVY_TOKEN_THRESHOLD", "1500"))

def _ms():
    return time.perf_counter() * 1000.0

class Prof:
    def __init__(self):
        self.t0 = _ms()
        self.steps = []
        self.meta = {}
    def mark(self, name: str):
        self.steps.append({"step": name, "ms": round(_ms() - self.t0, 1)})
    def set(self, k, v):
        self.meta[k] = v
    def out(self):
        return {"total_ms": round(_ms() - self.t0, 1), "steps": self.steps, "meta": self.meta}

def _budgeted_timeout(default: float | int = None, margin: float = 5.0) -> float:
    run_lim = float(os.getenv("RUN_TIMEOUT_SECONDS", "300") or 300)
    want = float(default if default is not None else OPENAI_TIMEOUT)
    return max(5.0, min(want, run_lim - margin))

# --- OpenAI client ---
_OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
if not _OPENAI_API_KEY:
    log.error("OPENAI_API_KEY nije postavljen u okruženju.")
client = OpenAI(api_key=_OPENAI_API_KEY, timeout=OPENAI_TIMEOUT, max_retries=OPENAI_MAX_RETRIES)
# --- Sync OpenAI client (brzi pokušaj, bez retry-a) ---
SYNC_OPENAI_TIMEOUT_S = float(os.getenv("SYNC_OPENAI_TIMEOUT_S", str(SYNC_SOFT_TIMEOUT_S)))
sync_client = OpenAI(api_key=_OPENAI_API_KEY, timeout=SYNC_OPENAI_TIMEOUT_S, max_retries=0)


DEFAULT_MODEL_VISION = "gpt-5.2"
DEFAULT_MODEL_TEXT = "gpt-5-mini"

MODEL_VISION = os.getenv("OPENAI_MODEL_VISION", DEFAULT_MODEL_VISION)
MODEL_VISION_LIGHT = os.getenv("OPENAI_MODEL_VISION_LIGHT", MODEL_VISION)
MODEL_TEXT = os.getenv("OPENAI_MODEL_TEXT", DEFAULT_MODEL_TEXT)

# gpt-5-mini je reasoning model: bez ovoga troši puni default reasoning po
# potezu (izmjereno 12–43 s/odgovor). "low" je 2–7× brže uz isti ili bolji
# kvalitet (A/B potvrđeno 2026-07-11); tačnost i onako presuđuje answer_checker.
# Prazan string (OPENAI_REASONING_EFFORT="") vraća stari default (ne šalje param).
TUTOR_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low").strip() or None


# --- Mathpix: auto-enable i default "prefer" ---
MATHPIX_APP_ID  = (os.getenv("MATHPIX_APP_ID")  or os.getenv("MATHPIX_API_ID")  or "").strip()
MATHPIX_APP_KEY = (os.getenv("MATHPIX_APP_KEY") or os.getenv("MATHPIX_API_KEY") or "").strip()

_use_flag = (os.getenv("USE_MATHPIX", "").strip())
MATHPIX_MODE = (os.getenv("MATHPIX_MODE", "prefer").strip().lower())  # default = prefer

if _use_flag == "0" or MATHPIX_MODE in ("off", "disable", "disabled"):
    USE_MATHPIX = False
else:
    USE_MATHPIX = bool(MATHPIX_APP_ID and MATHPIX_APP_KEY) or (_use_flag == "1") or (MATHPIX_MODE in ("prefer","force","on"))

def _mathpix_enabled() -> bool:
    return bool(MATHPIX_APP_ID and MATHPIX_APP_KEY) and USE_MATHPIX


# Sitne čiste pomoćne funkcije (slika → data URL) izdvojene su u utils.py.
from utils import _bytes_to_data_url

# Phase 3: modularni AI tutor endpoint (payload → lookup → prompt builder → OpenAI).
# Servis je čist i ne uvozi app; rutu ispod injektujemo postojećim _openai_chat.
from matbot import ai_tutor_service
from matbot.content_loader import ContentLoadError

def _openai_chat(model: str, messages: list, timeout: float = None, max_tokens: int | None = None, fast: bool = False, max_retries: int | None = None, reasoning_effort: str | None = None):

    def _do(params):
        base = sync_client if fast else client
        opts = {}
        if timeout is not None: opts["timeout"] = timeout
        if max_retries is not None: opts["max_retries"] = max_retries
        cli = base.with_options(**opts) if opts else base
        return cli.chat.completions.create(**params)

    params = {"model": model, "messages": messages}
    if max_tokens is not None:
        # novi SDK: max_completion_tokens; fallback na max_tokens ako zatreba
        params["max_completion_tokens"] = max_tokens
    if reasoning_effort is not None:
        params["reasoning_effort"] = reasoning_effort
    try:
        return _do(params)
    except Exception as e:
        msg = str(e)
        # model ne podržava reasoning_effort (npr. ne-reasoning model) → izbaci i pokušaj ponovo
        if "reasoning_effort" in msg and reasoning_effort is not None:
            params.pop("reasoning_effort", None)
            try:
                return _do(params)
            except Exception as e2:
                msg = str(e2)
        if "max_completion_tokens" in msg or "Unsupported parameter: 'max_completion_tokens'" in msg:
            params.pop("max_completion_tokens", None)
            if max_tokens is not None: params["max_tokens"] = max_tokens
            return _do(params)
        raise


def _tutor_openai_chat(model: str, messages: list, timeout: float = None, max_tokens: int | None = None, fast: bool = False, reasoning_effort: str | None = TUTOR_REASONING_EFFORT):
    """OpenAI poziv za modularni tutor: max_retries=AI_TUTOR_MAX_RETRIES (default 1)
    umjesto globalnog defaulta. Namjerno rezolvira ``_openai_chat`` kao modulnu
    globalu pri pozivu (testovi je monkeypatchaju).

    ``reasoning_effort`` default je TUTOR_REASONING_EFFORT ("low"); retry na prazan
    odgovor smije ga spustiti na "minimal" da oslobodi completion budžet."""
    return _openai_chat(
        model, messages, timeout=timeout, max_tokens=max_tokens, fast=fast,
        max_retries=AI_TUTOR_MAX_RETRIES, reasoning_effort=reasoning_effort,
    )


def _tutor_openai_chat_stream(model: str, messages: list, timeout: float = None, max_tokens: int | None = None):
    """Phase 2 — streaming OpenAI poziv za tutor: generator TEKST-DELTI.

    Isti max_completion_tokens/max_tokens fallback kao _openai_chat. Testovi
    monkeypatchaju cijelu funkciju (nikad stvarni API)."""
    cli = client.with_options(
        timeout=timeout if timeout is not None else AI_TUTOR_TIMEOUT,
        max_retries=AI_TUTOR_MAX_RETRIES,
    )
    params = {"model": model, "messages": messages, "stream": True}
    if max_tokens is not None:
        params["max_completion_tokens"] = max_tokens
    if TUTOR_REASONING_EFFORT is not None:
        params["reasoning_effort"] = TUTOR_REASONING_EFFORT
    try:
        stream = cli.chat.completions.create(**params)
    except Exception as e:
        msg = str(e)
        if "reasoning_effort" in msg and TUTOR_REASONING_EFFORT is not None:
            params.pop("reasoning_effort", None)
            try:
                stream = cli.chat.completions.create(**params)
            except Exception as e2:
                msg = str(e2)
                if "max_completion_tokens" in msg:
                    params.pop("max_completion_tokens", None)
                    if max_tokens is not None:
                        params["max_tokens"] = max_tokens
                    stream = cli.chat.completions.create(**params)
                else:
                    raise
        elif "max_completion_tokens" in msg:
            params.pop("max_completion_tokens", None)
            if max_tokens is not None:
                params["max_tokens"] = max_tokens
            stream = cli.chat.completions.create(**params)
        else:
            raise
    for chunk in stream:
        try:
            delta = chunk.choices[0].delta.content
        except (AttributeError, IndexError, TypeError):
            delta = None
        if delta:
            yield delta

def mathpix_ocr_to_text(img_bytes: bytes) -> tuple[str | None, float]:
    if not _mathpix_enabled():
        return (None, 0.0)
    try:
        headers = {
            "app_id": MATHPIX_APP_ID,
            "app_key": MATHPIX_APP_KEY,
            "Content-type": "application/json"
        }
        img_b64 = base64.b64encode(img_bytes).decode()
        payload = {
            "src": f"data:image/png;base64,{img_b64}",
            "formats": ["text"],
            "data_options": {"include_asciimath": False, "include_latex": False},
            "rm_spaces": True
        }
        t0 = _ms()
        r = requests.post("https://api.mathpix.com/v3/text", headers=headers, json=payload, timeout=30)
        t1 = _ms()
        log.info("TIMING mathpix_post_ms=%.1f status=%s", (t1 - t0), r.status_code)

        if r.status_code != 200:
            log.warning("Mathpix OCR vratio status %s", r.status_code)
            return (None, 0.0)
        j = r.json() or {}
        plain = (j.get("text") or "").strip()
        conf  = float(j.get("confidence") or 0.0)
        if not plain:
            return (None, 0.0)
        plain = (
            plain.replace("÷", "/")
                 .replace("×", "*")
                 .replace("–", "-")
                 .replace("—", "-")
        )
        return (plain, conf)
    except Exception as e:
        log.warning("Mathpix OCR nije uspio: %s", e)
        return (None, 0.0)

# ---------------- Web routes ----------------
@app.route("/", methods=["GET"])
def index():
    # Embed kapija: bez dozvoljenog embed origina ne serviramo ni stranicu ni token,
    # pa tutor API (koji trazi token) ostaje nedostupan direktnim posjetiocima.
    if not embed_entry_allowed():
        log.info("embed gate: odbijen ulaz (origin=%r)", _embed_referrer_origin())
        return _EMBED_GATE_HTML, 403
    return render_template("index.html", embed_token=mint_embed_token())

@app.errorhandler(413)
def too_large(e):
    # Jedini potrošač je tutor API (JSON) — legacy HTML forma više ne postoji.
    return jsonify({
        "error": "image_too_large",
        "detail": f"Slika je prevelika (limit {MAX_MB} MB). Smanji kvalitet pa pokušaj ponovo.",
    }), 413

@app.get("/healthz")
def healthz(): return {"ok": True, "local_mode": LOCAL_MODE}, 200
@app.get("/_healthz")
def _healthz(): return {"ok": True}, 200
@app.get("/_ah/health")
def ah_health(): return "OK", 200

# --- Dijagnostički endpointi: javno isključeni; pristup uz DIAG_TOKEN ili u LOCAL_MODE ---
DIAG_TOKEN = (os.getenv("DIAG_TOKEN") or "").strip()

def _diag_allowed() -> bool:
    if LOCAL_MODE:
        return True
    return bool(DIAG_TOKEN) and request.headers.get("X-Diag-Token") == DIAG_TOKEN

# --- Mathpix selftest (opcionalno) ---
@app.get("/mathpix/selftest")
@limiter.limit(_diag_rate_limit)
def mathpix_selftest():
    if not _diag_allowed():
        return jsonify({"error": "forbidden"}), 403
    if not _mathpix_enabled():
        return jsonify({"ok": False, "reason": "no-keys"}), 400
    if not HAVE_PIL:
        return jsonify({"ok": False, "reason": "no-PIL"}), 400
    try:
        import io
        img = Image.new("RGB", (320, 80), "white")
        d = ImageDraw.Draw(img)
        d.text((10, 20), "12/3 + 5", fill="black")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out, conf = mathpix_ocr_to_text(buf.getvalue())
        return jsonify({"ok": True, "text": out, "confidence": conf}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.after_request
def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0, private"
    resp.headers["Pragma"] = "no-cache"; resp.headers["Expires"] = "0"; resp.headers["Vary"] = "Cookie"
    ancestors = os.getenv("FRAME_ANCESTORS", "").strip()
    if ancestors: resp.headers["Content-Security-Policy"] = f"frame-ancestors {ancestors}"
    try: del resp.headers["X-Frame-Options"]
    except KeyError: pass
    return resp

@app.get("/api/ai-tutor/topics")
def ai_tutor_topics():
    """Phase 4: lista READY tema (grupisano po oblasti) za UI dropdown.
    Čita iz Phase 1 content_loader-a; ništa se ne hardkodira, bez tajni."""
    try:
        grade = request.args.get("grade") or 6
        return jsonify(ai_tutor_service.list_topics(grade=grade)), 200
    except ContentLoadError as exc:
        return jsonify({"error": "unsupported_grade", "detail": str(exc)}), 400
    except Exception:
        log.exception("ai_tutor_topics: neuspjeh")
        return jsonify({"error": "ai_tutor_topics_failed",
                        "detail": "Došlo je do greške na serveru. Pokušaj ponovo."}), 500


@app.route("/api/ai-tutor/chat", methods=["POST", "OPTIONS"])
@limiter.limit(_submit_rate_limit, exempt_when=lambda: request.method == "OPTIONS")
def ai_tutor_chat():
    """Phase 3: modularni AI tutor chat (6. razred MVP). Tanak wrapper oko
    matbot.ai_tutor_service.handle_chat; koristi postojeći _openai_chat."""
    if request.method == "OPTIONS":
        return ("", 204)
    if not _embed_token_ok():
        return jsonify(_EMBED_TOKEN_DENIED), 403

    # Phase 6.2: multipart = JSON payload + opciona slika zadatka (modularni tutor).
    image_bytes = None
    image_data_url = None
    if "multipart/form-data" in (request.content_type or ""):
        # PAŽNJA: pristup request.form podiže RequestEntityTooLarge kada je tijelo
        # veće od MAX_CONTENT_LENGTH. Taj izuzetak NE hvatamo (ranije ga je široki
        # `except Exception` gutao, pa je učenik dobijao zbunjujuće "invalid_json"
        # umjesto poruke da je slika prevelika) — neka ide u errorhandler(413).
        raw_payload = request.form.get("payload") or "{}"
        try:
            data = json.loads(raw_payload)
        except (ValueError, TypeError):
            data = None
        if not isinstance(data, dict):
            return jsonify({"error": "invalid_json", "detail": "Očekivan je JSON objekt u 'payload' polju."}), 400
        f = request.files.get("image")
        if f and f.filename:
            mime = (f.mimetype or "").lower()
            if not mime.startswith("image/"):
                return jsonify({"error": "invalid_image", "detail": "Fajl nije prepoznat kao slika. Pošalji JPG/PNG."}), 400
            image_bytes = f.read()
            if len(image_bytes) > IMAGE_FETCH_MAX_MB * 1024 * 1024:
                return jsonify({"error": "image_too_large", "detail": f"Slika je veća od {IMAGE_FETCH_MAX_MB} MB."}), 413
            image_data_url = _bytes_to_data_url(image_bytes, mime_hint=mime)
    else:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "invalid_json", "detail": "Očekivan je JSON objekt sa poljima zahtjeva."}), 400
    try:
        result = ai_tutor_service.handle_chat(
            data, openai_chat=_tutor_openai_chat, model=MODEL_TEXT, timeout=AI_TUTOR_TIMEOUT,
            image_bytes=image_bytes, image_data_url=image_data_url,
            ocr_image=mathpix_ocr_to_text, vision_model=MODEL_VISION,
        )
    except ContentLoadError as exc:
        return jsonify({"error": "unsupported_grade", "detail": str(exc)}), 400
    except Exception as exc:
        # Neispravna/oštećena slika: model vrati image_parse_error. To NIJE greška
        # servera — dijete treba jasnu uputu, a ne "Greška na serveru" + traceback.
        if _is_unreadable_image_error(exc):
            log.info("ai_tutor_chat: model nije mogao pročitati sliku")
            return jsonify({"error": "unreadable_image",
                            "detail": _UNREADABLE_IMAGE_MSG}), 400
        log.exception("ai_tutor_chat: neuspjeh")
        return jsonify({"error": "ai_tutor_failed",
                        "detail": "Došlo je do greške na serveru. Pokušaj ponovo."}), 500
    return jsonify(result), 200


_UNREADABLE_IMAGE_MSG = (
    "Ne mogu pročitati ovu sliku. Pošalji jasniju fotografiju (JPG ili PNG), "
    "dobro osvijetljenu i bez zamućenja."
)


def _is_unreadable_image_error(exc: Exception) -> bool:
    """Model odbio sliku (oštećen/nepodržan sadržaj) — korisnička, ne serverska greška.

    OpenAI to javlja kao 400 `image_parse_error`; hvatamo po kodu i po tekstu
    (da preživi promjenu poruke), a ne po tipu izuzetka — klijent ga zna
    zamotati."""
    code = getattr(exc, "code", "") or ""
    if code == "image_parse_error":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and (body.get("error") or {}).get("code") == "image_parse_error":
        return True
    text = str(exc).lower()
    return "image_parse_error" in text or "unsupported image" in text


def _sse_line(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.route("/api/ai-tutor/chat/stream", methods=["POST", "OPTIONS"])
@limiter.limit(_submit_rate_limit, exempt_when=lambda: request.method == "OPTIONS")
def ai_tutor_chat_stream():
    """Phase 2 — SSE streaming tutor odgovora (samo JSON/tekst; slike idu na
    non-streaming /api/ai-tutor/chat). Događaji: delta / done / error."""
    if request.method == "OPTIONS":
        return ("", 204)
    if not _embed_token_ok():
        return jsonify(_EMBED_TOKEN_DENIED), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "invalid_json",
                        "detail": "Očekivan je JSON objekt sa poljima zahtjeva."}), 400

    def _sse():
        try:
            for ev in ai_tutor_service.handle_chat_stream(
                data,
                openai_chat=_tutor_openai_chat,
                openai_chat_stream=_tutor_openai_chat_stream,
                model=MODEL_TEXT, timeout=AI_TUTOR_TIMEOUT, vision_model=MODEL_VISION,
            ):
                yield _sse_line(ev.get("event", "message"), ev.get("data", {}))
        except ContentLoadError as exc:
            yield _sse_line("error", {"error": "unsupported_grade", "detail": str(exc)})
        except Exception:
            log.exception("ai_tutor_chat_stream: neuspjeh")
            yield _sse_line("error", {
                "error": "ai_tutor_failed",
                "detail": "Došlo je do greške na serveru. Pokušaj ponovo.",
            })

    return Response(
        stream_with_context(_sse()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx: bez proxy bufferinga za SSE (vidi docs/deploy/nginx.example.conf)
            "X-Accel-Buffering": "no",
        },
    )


# ==== APP VERSION / FINGERPRINT (dodaj blizu vrha fajla, uz ostale env var) ====
APP_VERSION = os.getenv("APP_VERSION", "dev")

# ... ostatak tvog koda ...


# ==== /version endpoint (dodaj blizu ostalih @app.get ruta) ====
@app.get("/version")
def version():
    try:
        import hashlib, inspect, importlib
        mod = importlib.import_module("app")
        src = inspect.getsource(mod)
        sha = hashlib.sha256(src.encode()).hexdigest()[:12]
    except Exception:
        sha = "unknown"
    return jsonify({"version": APP_VERSION, "app_py_sha": sha}), 200


def _startup_env_sanity():
    """Phase 1 (audit): glasna provjera env varijabli pri startu u produkciji.

    Ne ruši aplikaciju (ponašanje je sigurno i bez varijabli — endpointi se
    zatvaraju), ali svaki propust mora biti VIDLJIV u logovima prve minute."""
    if LOCAL_MODE:
        return
    if not _OPENAI_API_KEY:
        log.error("ENV SANITY: OPENAI_API_KEY nije postavljen — bot ne može odgovarati.")
    if _secret_key == "tajna_lozinka":
        log.error("ENV SANITY: FLASK_SECRET_KEY/SECRET_KEY nije postavljen — "
                  "sesije koriste NESIGURAN default.")
    if not _cors_origins:
        log.warning("ENV SANITY: CORS_ORIGINS nije postavljen — CORS je otvoren za "
                    "SVE domene. Postavi npr. CORS_ORIGINS=https://skola.thinkific.com")
    if not AI_TUTOR_EMBED_SECRET:
        log.warning("ENV SANITY: AI_TUTOR_EMBED_SECRET nije postavljen — token "
                    "zaštita /api/ai-tutor/* je ISKLJUČENA (svi zahtjevi prolaze).")
    if not EMBED_ALLOWED_ORIGINS:
        log.warning("ENV SANITY: EMBED_ALLOWED_ORIGINS nije postavljen — botu se "
                    "može pristupiti DIREKTNO preko URL-a, ne samo kroz Thinkific. "
                    "Postavi npr. EMBED_ALLOWED_ORIGINS=https://*.thinkific.com")
    elif not AI_TUTOR_EMBED_SECRET:
        log.error("ENV SANITY: EMBED_ALLOWED_ORIGINS je postavljen, ali "
                  "AI_TUTOR_EMBED_SECRET NIJE — kapija tada štiti samo stranicu, a "
                  "tutor API ostaje otvoren. Postavi OBA.")
    if not DIAG_TOKEN:
        log.info("ENV SANITY: DIAG_TOKEN nije postavljen — dijagnostički endpointi "
                 "su nedostupni (to je OK ako ih ne koristiš).")


_startup_env_sanity()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    log.info("Starting app on port %s, LOCAL_MODE=%s", port, LOCAL_MODE)
    app.run(host="0.0.0.0", port=port, debug=debug)
