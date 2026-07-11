from flask import (
    Flask, render_template, request, session, redirect, url_for,
    send_from_directory, jsonify, Response, stream_with_context,
)
from dotenv import load_dotenv
import os, base64, json, html, datetime, logging, mimetypes, threading
import socket, ipaddress, hmac, hashlib
from datetime import timedelta
from uuid import uuid4
import requests
from urllib.parse import urlparse
from openai import OpenAI
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import time



# --- Optional PIL (for image heuristics and selftest) ---
try:
    from PIL import Image, ImageStat, ImageDraw
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

import gspread
from google.oauth2.service_account import Credentials as SACreds
import google.auth

# --- Optional GCP clients ---
try:
    from google.cloud import storage as gcs_lib
except Exception:
    gcs_lib = None
try:
    from google.cloud import firestore as fs_lib
except Exception:
    fs_lib = None
try:
    from google.cloud import tasks_v2
except Exception:
    tasks_v2 = None

# ---------------- Bootstrapping ----------------
load_dotenv(override=False)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("matbot")

LOCAL_MODE = os.getenv("LOCAL_MODE", "0") == "1"
USE_FIRESTORE = os.getenv("USE_FIRESTORE", "1") == "1" and not LOCAL_MODE

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

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
UPLOAD_MAX_AGE_S = int(os.getenv("UPLOAD_MAX_AGE_S", "3600"))

def cleanup_stale_uploads(max_age_s: int | None = None):
    """Best-effort brisanje starih fajlova iz UPLOAD_DIR (na Cloud Runu /tmp živi u RAM-u)."""
    cutoff = time.time() - (max_age_s if max_age_s is not None else UPLOAD_MAX_AGE_S)
    try:
        for name in os.listdir(UPLOAD_DIR):
            path = os.path.join(UPLOAD_DIR, name)
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError as e:
                log.warning("cleanup_stale_uploads: ne mogu obrisati %s: %s", path, e)
    except OSError as e:
        log.warning("cleanup_stale_uploads: %s", e)

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

# --- Google Sheets ---
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
GSHEET_ID   = os.getenv("GSHEET_ID", "").strip()
GSHEET_NAME = os.getenv("GSHEET_NAME", "matematika-bot").strip()

sheet = None
_sheets_initialized = False
_sheets_lock = threading.Lock()
_sheets_diag = {
    "enabled": False, "mode": None, "sa_email": None,
    "spreadsheet_title": None, "spreadsheet_id": None,
    "worksheet_title": None, "error": None,
}
def _try_get_sa_email_from_creds(creds):
    email = getattr(creds, "service_account_email", None)
    if email: return email
    try:
        info = getattr(creds, "_service_account_email", None) or getattr(creds, "_subject", None)
        if info: return info
    except Exception:
        pass
    return None

def _init_sheets():
    """Lijena inicijalizacija Sheets klijenta — mrežni poziv tek pri prvoj upotrebi,
    ne pri importu (brži cold start, testovi bez mreže)."""
    global sheet, _sheets_initialized
    if _sheets_initialized:
        return sheet
    with _sheets_lock:
        if _sheets_initialized:
            return sheet
        try:
            gc = None
            b64 = os.getenv("GOOGLE_SHEETS_CREDENTIALS_B64", "").strip()
            if b64:
                info  = json.loads(base64.b64decode(b64).decode("utf-8"))
                creds = SACreds.from_service_account_info(info, scopes=SHEETS_SCOPES)
                gc = gspread.authorize(creds)
                _sheets_diag["mode"] = "b64"; _sheets_diag["sa_email"] = info.get("client_email")
            elif os.path.exists("credentials.json"):
                creds = SACreds.from_service_account_file("credentials.json", scopes=SHEETS_SCOPES)
                gc = gspread.authorize(creds)
                _sheets_diag["mode"] = "file"; _sheets_diag["sa_email"] = _try_get_sa_email_from_creds(creds)
            elif LOCAL_MODE:
                raise RuntimeError("LOCAL_MODE: Sheets isključen (nema eksplicitnih kredencijala).")
            else:
                adc_creds, _ = google.auth.default(scopes=SHEETS_SCOPES)
                gc = gspread.authorize(adc_creds)
                _sheets_diag["mode"] = "adc"; _sheets_diag["sa_email"] = _try_get_sa_email_from_creds(adc_creds)

            if not GSHEET_ID and not GSHEET_NAME:
                raise RuntimeError("GSHEET_ID ili GSHEET_NAME moraju biti postavljeni.")
            ss = gc.open_by_key(GSHEET_ID) if GSHEET_ID else gc.open(GSHEET_NAME)
            try: ws = ss.sheet1
            except Exception: ws = ss.get_worksheet(0)
            sheet = ws
            _sheets_diag.update({
                "enabled": True,
                "spreadsheet_title": getattr(ss, "title", None),
                "spreadsheet_id": getattr(ss, "id", None),
                "worksheet_title": getattr(ws, "title", None),
            })
        except Exception as e:
            _sheets_diag["error"] = str(e); sheet = None
            log.warning("Sheets inicijalizacija nije uspjela: %s", e)
        _sheets_initialized = True
    return sheet

def sheets_append_row_safe(values):
    ws = _init_sheets()
    if not ws: return False
    try:
        ws.append_row(values, value_input_option="USER_ENTERED"); return True
    except Exception as e:
        log.warning("Sheets append nije uspio: %s", e)
        return False

SHEETS_ASYNC_LOG = os.getenv("SHEETS_ASYNC_LOG", "1") == "1"

def log_to_sheet(job_id, razred, user_text, odgovor_html, source_tag, model_name):
    ts = datetime.datetime.utcnow().isoformat()
    row = [ts, razred, user_text, odgovor_html, f"{source_tag}|{model_name}", job_id]
    if SHEETS_ASYNC_LOG:
        # van hot patha — upis u Sheet zna trajati i 0.5s+
        threading.Thread(target=sheets_append_row_safe, args=(row,), daemon=True).start()
    else:
        sheets_append_row_safe(row)

# --- GCS & Firestore ---
GCS_BUCKET = (os.getenv("GCS_BUCKET") or "").strip()
GCS_SIGNED_GET = os.getenv("GCS_SIGNED_GET", "1") == "1"
storage_client = None
if not LOCAL_MODE and GCS_BUCKET and gcs_lib is not None:
    try:
        storage_client = gcs_lib.Client()
    except Exception:
        storage_client = None

fs_db = None
JOB_STORE = {}
if USE_FIRESTORE and fs_lib is not None:
    try:
        fs_db = fs_lib.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT") or None)
    except Exception as e:
        fs_db = None
        log.warning("Firestore klijent nije inicijalizovan: %s", e)

log.info("Job store: %s", "firestore" if fs_db else "in-memory dict")
if fs_db is None and not LOCAL_MODE:
    log.warning("Job store je in-memory, a LOCAL_MODE nije aktivan — kod više Cloud Run "
                "instanci polling /status može promašiti instancu i job ostaje 'pending'.")

def store_job(job_id: str, data: dict, merge: bool = True):
    if fs_db:
        fs_db.collection("jobs").document(job_id).set(data, merge=merge)
    else:
        JOB_STORE[job_id] = {**JOB_STORE.get(job_id, {}), **data}

def read_job(job_id: str) -> dict:
    if fs_db:
        doc = fs_db.collection("jobs").document(job_id).get()
        return (doc.to_dict() or {}) if doc.exists else {}
    return JOB_STORE.get(job_id, {})



# Prompt sekcije (čisti tekst, bez logike) izdvojene su u prompts.py:
from prompts import build_system_prompt, DOZVOLJENI_RAZREDI

# Parsiranje broja zadatka izdvojeno je u task_parsing.py.
from task_parsing import extract_requested_tasks, requested_clause, FOLLOWUP_TASK_RE

# Rendering izlaza modela + detekcija grafova izdvojeni su u rendering.py.
# (latexify_fractions / strip_ascii_graph_blocks koristi render_model_html; re-export radi testova.)
from rendering import (
    latexify_fractions, strip_ascii_graph_blocks, render_model_html,
    add_plot_div_once, should_plot, extract_plot_expression,
)

# Sitne čiste pomoćne funkcije izdvojene su u utils.py.
from utils import _short_name_for_display, _name_from_url, _sniff_image_mime, _bytes_to_data_url

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

# Historija razgovora (sanitizacija + gradnja poruka) izdvojena je u history.py.
from history import (
    HISTORY_MAX_TURNS, HISTORY_MAX_CHARS, HISTORY_CONTEXT_TURNS,
    strip_html_to_text, sanitize_history, _append_history_messages,
)

def answer_with_text_pipeline(pure_text: str, razred: str, history, requested, timeout_override: float | None = None, prof=None, fast: bool = False):
    system_message = {
        "role": "system",
        "content": build_system_prompt(razred) + requested_clause(requested)
    }

    messages = [system_message]
    _append_history_messages(messages, history)
    messages.append({"role":"user","content": pure_text})
    if prof: prof.mark("openai_text_start")
    response = _openai_chat(MODEL_TEXT, messages, timeout=timeout_override or OPENAI_TIMEOUT, fast=fast)
    actual_model = getattr(response, "model", MODEL_TEXT)
    if prof:
        prof.mark("openai_text_done")
        prof.set("openai_text_model", actual_model)

    raw = response.choices[0].message.content
    html_out = render_model_html(raw)
    return html_out, actual_model

def _vision_messages_base(razred: str, history):
    system_message = {"role": "system", "content": build_system_prompt(razred)}
    messages = [system_message]
    _append_history_messages(messages, history)
    return messages

def _heuristic_plain_text_image(img_bytes: bytes) -> bool:
    try:
        if len(img_bytes) > 4_000_000:
            return False
        if not HAVE_PIL:
            return True
        from io import BytesIO
        im = Image.open(BytesIO(img_bytes)).convert("RGB")
        w, h = im.size
        if w*h > 8_000_000:
            return False
        stat = ImageStat.Stat(im)
        mean = sum(stat.mean) / 3.0
        var  = sum(stat.var) / 3.0
        is_whiteish = mean > 200
        low_var = var < 1200
        return is_whiteish and low_var
    except Exception:
        return False

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

# --- SSRF zaštita za image_url ---
ALLOW_PRIVATE_IMAGE_URLS = os.getenv("ALLOW_PRIVATE_IMAGE_URLS", "0") == "1"
IMAGE_FETCH_MAX_MB = int(os.getenv("IMAGE_FETCH_MAX_MB", "20"))
_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata", "localhost"}

def is_safe_external_url(url: str) -> bool:
    """Dozvoli samo http(s) prema javnim adresama; blokiraj loopback/privatne/link-local
    i GCP metadata host. ALLOW_PRIVATE_IMAGE_URLS=1 isključuje provjeru (testovi/dev)."""
    if ALLOW_PRIVATE_IMAGE_URLS:
        return True
    try:
        p = urlparse(url or "")
        if p.scheme not in ("http", "https"):
            return False
        host = (p.hostname or "").strip().lower()
        if not host or host in _BLOCKED_HOSTNAMES:
            return False
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
        if not infos:
            return False
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except Exception:
        return False

def _fetch_image_bytes(image_url: str) -> tuple[bytes, str | None]:
    """Preuzmi sliku uz limit veličine (streaming, bez učitavanja neograničenog tijela)."""
    limit = IMAGE_FETCH_MAX_MB * 1024 * 1024
    r = requests.get(image_url, timeout=15, stream=True)
    r.raise_for_status()
    mime_hint = r.headers.get("Content-Type") or None
    cl = r.headers.get("Content-Length")
    if cl and cl.isdigit() and int(cl) > limit:
        raise ValueError(f"slika veća od {IMAGE_FETCH_MAX_MB} MB")
    chunks, total = [], 0
    for chunk in r.iter_content(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise ValueError(f"slika veća od {IMAGE_FETCH_MAX_MB} MB")
        chunks.append(chunk)
    return b"".join(chunks), mime_hint

def route_image_flow_url(image_url: str, razred: str, history, user_text=None, timeout_override: float | None = None):
    if not is_safe_external_url(image_url):
        log.warning("route_image_flow_url: blokiran nedozvoljen URL: %r", image_url)
        return ("<p><b>Link slike nije prihvaćen.</b> Pošalji sliku kao prilog (upload) "
                "ili koristi javni https link.</p>", "blocked_url", "n/a")
    try:
        t0 = _ms()
        img_bytes, mime_hint = _fetch_image_bytes(image_url)
        t1 = _ms()
        log.info("TIMING image_download_ms=%.1f", (t1 - t0))
        return route_image_flow(
            img_bytes, razred, history,
            user_text=user_text,
            timeout_override=timeout_override,
            mime_hint=mime_hint
        )
    except Exception as e:
        log.warning("route_image_flow_url: download nije uspio (%s), šaljem URL direktno Vision-u", e)
    messages = _vision_messages_base(razred, history)
    user_content = []
    if user_text:
        user_content.append({"type": "text", "text": f"Korisnički tekst: {user_text}"})
    user_content.append({"type": "text", "text": "Na slici je matematički zadatak."})
    user_content.append({"type": "image_url", "image_url": {"url": image_url}})
    messages.append({"role": "user", "content": user_content})
    resp = _openai_chat(MODEL_VISION, messages, timeout=timeout_override or OPENAI_TIMEOUT)
    actual_model = getattr(resp, "model", MODEL_VISION)
    raw = resp.choices[0].message.content
    return render_model_html(raw), "vision_url", actual_model

def route_image_flow(slika_bytes: bytes, razred: str, history, user_text=None, timeout_override: float | None = None, mime_hint: str | None = None):
    # Try Mathpix first if mode prefers/forces it, OR if heuristika prepoznaje plain-tekst
    try_mathpix = _mathpix_enabled() and (MATHPIX_MODE in ("prefer","force","on") or _heuristic_plain_text_image(slika_bytes))
    if try_mathpix:
        plain, conf = mathpix_ocr_to_text(slika_bytes)
        if plain:
            try:
                html_out, actual_model = answer_with_text_pipeline(
                    pure_text=plain if not user_text else (user_text + "\n\n" + plain),
                    razred=razred, history=history, requested=extract_requested_tasks(user_text or ""),
                    timeout_override=timeout_override or OPENAI_TIMEOUT
                )
                return html_out, "mathpix", actual_model
            except Exception as e:
                log.warning("Mathpix→tekst pipeline nije uspio, fallback na Vision: %s", e)
        # ako je mode == "force" a Mathpix nije dao tekst, ipak fallback na Vision radi robusnosti
    # fallback → Vision
    messages = _vision_messages_base(razred, history)
    data_url = _bytes_to_data_url(slika_bytes, mime_hint=mime_hint)
    user_content = []
    if user_text: user_content.append({"type": "text", "text": f"Korisnički tekst: {user_text}"})
    user_content.append({"type": "text", "text": "Na slici je matematički zadatak."})
    user_content.append({"type": "image_url", "image_url": {"url": data_url}})
    messages.append({"role": "user", "content": user_content})
    resp = _openai_chat(MODEL_VISION, messages, timeout=timeout_override or OPENAI_TIMEOUT)
    actual_model = getattr(resp, "model", MODEL_VISION)
    raw = resp.choices[0].message.content
    return render_model_html(raw), "vision_direct", actual_model


def get_history_from_request():
    """Pročitaj i sanitizuj historiju koju šalje klijent (form polje ili JSON tijelo)."""
    try:
        hx = request.form.get("history_json")
        if not hx:
            data_json = request.get_json(silent=True) or {}
            hx = data_json.get("history_json") or data_json.get("history")
        if hx:
            data = json.loads(hx) if isinstance(hx, str) else hx
            if isinstance(data, list):
                return sanitize_history(data)
    except Exception as e:
        log.warning("history_json se ne može parsirati: %s", e)
    return None

def gcs_upload_bytes(job_id: str, raw: bytes, filename_hint: str = "image.bin", content_type: str | None = None) -> str | None:
    if not (storage_client and GCS_BUCKET):
        return None
    ext = os.path.splitext(filename_hint or "")[1].lower() or ".bin"
    blob_name = f"uploads/{job_id}/{uuid4().hex}{ext}"
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    try:
        blob.upload_from_string(raw, content_type=content_type or "application/octet-stream")
        return blob_name
    except Exception as e:
        log.warning("GCS upload nije uspio (%s): %s", blob_name, e)
        return None

# ---------------- Web routes ----------------
@app.route("/", methods=["GET", "POST"])
def index():
    plot_expression_added = False
    history = get_history_from_request() or session.get("history", [])
    razred = (request.form.get("razred") or session.get("razred") or "").strip()
    if request.method == "POST":
        if razred not in DOZVOLJENI_RAZREDI:
            return render_template("index.html", history=history, razred=razred, error="Molim odaberi razred."), 400
        session["razred"] = razred
        try:
            pitanje = (request.form.get("pitanje", "") or "").strip()
            slika = request.files.get("slika")
            image_url = (request.form.get("image_url") or "").strip()
            is_ajax = request.form.get("ajax") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            if image_url:
                combined_text = pitanje
                odgovor, used_path, used_model = route_image_flow_url(image_url, razred, history, user_text=combined_text, timeout_override=HARD_TIMEOUT_S)
                session["last_image_url"] = image_url
                if (not plot_expression_added) and should_plot(combined_text):
                    expr = extract_plot_expression(combined_text, razred=razred, history=history)
                    if expr: odgovor = add_plot_div_once(odgovor, expr); plot_expression_added = True
                file_label = _name_from_url(image_url)
                display_user = (combined_text + f" [slika: {file_label}]") if combined_text else f"[slika: {file_label}]"
                history.append({"user": display_user, "bot": odgovor.strip()})
                history = history[-8:]; session["history"] = history
                sync_job_id = f"sync-{uuid4().hex[:8]}"; log_to_sheet(sync_job_id, razred, combined_text, odgovor, "vision_url", used_model)
                if is_ajax: return render_template("index.html", history=history, razred=razred)
                return redirect(url_for("index"))
            if slika and slika.filename:
                combined_text = pitanje
                body = slika.read()
                odgovor, used_path, used_model = route_image_flow(body, razred, history, user_text=combined_text, timeout_override=HARD_TIMEOUT_S, mime_hint=slika.mimetype or None)
                try:
                    cleanup_stale_uploads()
                    ext = os.path.splitext(slika.filename or "")[1].lower() or ".img"
                    fname = f"{uuid4().hex}{ext}"
                    with open(os.path.join(UPLOAD_DIR, fname), "wb") as fp: fp.write(body)
                    public_url = (request.url_root.rstrip("/") + "/uploads/" + fname)
                    session["last_image_url"] = public_url
                except Exception:
                    pass
                if (not plot_expression_added) and should_plot(combined_text):
                    expr = extract_plot_expression(combined_text, razred=razred, history=history)
                    if expr: odgovor = add_plot_div_once(odgovor, expr); plot_expression_added = True
                orig_name = _short_name_for_display(slika.filename or "upload")
                display_user = (combined_text + f" [slika: {orig_name}]") if combined_text else f"[slika: {orig_name}]"
                history.append({"user": display_user, "bot": odgovor.strip()})
                history = history[-8:]; session["history"] = history
                sync_job_id = f"sync-{uuid4().hex[:8]}"; log_to_sheet(sync_job_id, razred, combined_text, odgovor, "vision_direct", used_model)
                if is_ajax: return render_template("index.html", history=history, razred=razred)
                return redirect(url_for("index"))
            requested = extract_requested_tasks(pitanje)
            last_url = session.get("last_image_url")
            if last_url and (requested or (pitanje and FOLLOWUP_TASK_RE.match(pitanje))):
                odgovor, used_path, used_model = route_image_flow_url(last_url, razred, history, user_text=pitanje, timeout_override=HARD_TIMEOUT_S)
                if (not plot_expression_added) and should_plot(pitanje):
                    expr = extract_plot_expression(pitanje, razred=razred, history=history)
                    if expr: odgovor = add_plot_div_once(odgovor, expr); plot_expression_added = True
                file_label = _name_from_url(last_url)
                display_user = (pitanje + f" [slika: {file_label}]") if pitanje else f"[slika: {file_label}]"
                history.append({"user": display_user, "bot": odgovor.strip()})
                history = history[-8:]; session["history"] = history
                sync_job_id = f"sync-{uuid4().hex[:8]}"; log_to_sheet(sync_job_id, razred, pitanje, odgovor, "vision_url", used_model)
                if is_ajax: return render_template("index.html", history=history, razred=razred)
                return redirect(url_for("index"))
            odgovor, actual_model = answer_with_text_pipeline(pitanje, razred, history, requested, timeout_override=HARD_TIMEOUT_S)
            if (not plot_expression_added) and should_plot(pitanje):
                expr = extract_plot_expression(pitanje, razred=razred, history=history)
                if expr: odgovor = add_plot_div_once(odgovor, expr); plot_expression_added = True
            history.append({"user": pitanje, "bot": odgovor.strip()}); history = history[-8:]; session["history"] = history
            sync_job_id = f"sync-{uuid4().hex[:8]}"; log_to_sheet(sync_job_id, razred, pitanje, odgovor, "text", actual_model)
        except Exception as e:
            err_html = f"<p><b>Greška servera:</b> {html.escape(str(e))}</p>"
            history.append({"user": request.form.get('pitanje') or "[SLIKA]", "bot": err_html})
            history = history[-8:]; session["history"] = history
            if request.form.get("ajax") == "1":
                return render_template("index.html", history=history, razred=razred)
            return redirect(url_for("index"))
    return render_template("index.html", history=history, razred=razred,
                           embed_token=mint_embed_token())

@app.errorhandler(413)
def too_large(e):
    msg = (f"<p><b>Greška:</b> Fajl je prevelik (limit {MAX_MB} MB). Pokušaj ponovo ili smanji kvalitet.</p>")
    return render_template("index.html", history=[{"user":"[SLIKA]", "bot": msg}], razred=session.get("razred")), 413

@app.route("/clear", methods=["POST"])
def clear():
    if request.form.get("confirm_clear") == "1":
        session.pop("history", None)
        session.pop("razred", None)
        session.pop("last_image_url", None)
        session.pop("api_history", None)  # očisti i API historiju
    return redirect(url_for("index"))



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

@app.get("/sheets/diag")
@limiter.limit(_diag_rate_limit)
def sheets_diag():
    if not _diag_allowed():
        return jsonify({"error": "forbidden"}), 403
    _init_sheets()
    return jsonify(_sheets_diag), 200

@app.post("/sheets/selftest")
@limiter.limit(_diag_rate_limit)
def sheets_selftest():
    if not _diag_allowed():
        return jsonify({"error": "forbidden"}), 403
    if not _init_sheets():
        return jsonify({"ok": False, "error": _sheets_diag.get("error") or "Sheets not initialized"}), 500
    row = [datetime.datetime.utcnow().isoformat(), "selftest", "Hello from /sheets/selftest", "<p>OK</p>", "selftest|none", f"self-{uuid4().hex[:8]}"]
    ok = sheets_append_row_safe(row); return jsonify({"ok": ok}), (200 if ok else 500)

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

@app.get("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)

@app.post("/gcs/signed-upload")
@limiter.limit(_diag_rate_limit)
def gcs_signed_upload():
    if not _diag_allowed():
        return jsonify({"error": "forbidden"}), 403
    if not storage_client or not GCS_BUCKET or LOCAL_MODE:
        return jsonify({"ok": False, "reason": "no-gcs"}), 200
    data = request.get_json(force=True, silent=True) or {}
    content_type = (data.get("contentType") or "image/jpeg").strip()
    obj = f"uploads/{uuid4().hex}.bin"
    bucket = storage_client.bucket(GCS_BUCKET)
    blob = bucket.blob(obj)
    put_url = blob.generate_signed_url(
        version="V4",
        expiration=datetime.timedelta(minutes=15),
        method="PUT",
        content_type=content_type,
    )
    if GCS_SIGNED_GET:
        read_url = blob.generate_signed_url(
            version="V4", expiration=datetime.timedelta(minutes=45), method="GET"
        )
    else:
        try:
            blob.make_public()
            read_url = blob.public_url
        except Exception:
            read_url = blob.generate_signed_url(
                version="V4", expiration=datetime.timedelta(minutes=45), method="GET"
            )
    return jsonify({"uploadUrl": put_url, "readUrl": read_url}), 200

# --- Cloud Tasks (async) ---
PROJECT_ID        = (os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or "").strip()
REGION            = os.getenv("REGION", "europe-west1")
TASKS_QUEUE       = os.getenv("TASKS_QUEUE", "matbot-queue")
TASKS_TARGET_URL  = os.getenv("TASKS_TARGET_URL")
# BEZ nesigurnog defaulta ("super-secret" je bio pogodiv): ako nije postavljen,
# /tasks/process u produkciji odbija SVE zahtjeve (vidi tasks_process).
TASKS_SECRET      = (os.getenv("TASKS_SECRET") or "").strip()

def _create_task_cloud(payload: dict):
    if not tasks_v2:
        raise RuntimeError("google-cloud-tasks nije instaliran")
    if not TASKS_TARGET_URL:
        raise RuntimeError("TASKS_TARGET_URL je obavezan")
    if not PROJECT_ID:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT/GCP_PROJECT je obavezan")
    if not TASKS_SECRET:
        raise RuntimeError("TASKS_SECRET je obavezan za Cloud Tasks (bez defaulta)")
    tc = tasks_v2.CloudTasksClient()
    parent = tc.queue_path(PROJECT_ID, REGION, TASKS_QUEUE)
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": TASKS_TARGET_URL,
            "headers": {"Content-Type": "application/json", "X-Tasks-Secret": TASKS_SECRET},
            "body": json.dumps(payload).encode(),
        }
    }
    return tc.create_task(request={"parent": parent, "task": task})

def _enqueue(payload: dict):
    if LOCAL_MODE or (not tasks_v2) or (not TASKS_TARGET_URL) or (not PROJECT_ID):
        reason = ("LOCAL_MODE" if LOCAL_MODE else
                  "tasks_v2 nije instaliran" if not tasks_v2 else
                  "TASKS_TARGET_URL nije postavljen" if not TASKS_TARGET_URL else
                  "PROJECT_ID nije postavljen")
        if not LOCAL_MODE:
            log.warning("_enqueue: Cloud Tasks nedostupan (%s) — job %s ide u lokalni thread "
                        "(na Cloud Runu može biti CPU-throttlan).", reason, payload.get("job_id"))
        threading.Thread(target=_local_worker, daemon=True, args=(payload,)).start()
    else:
        _create_task_cloud(payload)

def _process_job_core(payload: dict) -> dict:
    job_id     = payload["job_id"]
    bucket     = payload.get("bucket")
    image_path = payload.get("image_path")
    image_url  = payload.get("image_url")
    image_inline_b64 = payload.get("image_inline_b64")
    razred     = (payload.get("razred") or "").strip()
    user_text  = (payload.get("user_text") or "").strip()
    requested  = payload.get("requested") or []
    if razred not in DOZVOLJENI_RAZREDI: razred = "5"
    history = sanitize_history(payload.get("history") or [])
    task_ai_timeout = _budgeted_timeout(default=HARD_TIMEOUT_S, margin=5.0)
    if image_path:
        if not storage_client:
            raise RuntimeError("GCS storage client not initialized (image_path zadat).")
        blob = storage_client.bucket(bucket).blob(image_path)
        img_bytes = blob.download_as_bytes()
        mime_hint = blob.content_type or mimetypes.guess_type(image_path)[0] or None
        odgovor_html, used_path, used_model = route_image_flow(img_bytes, razred, history=history, user_text=user_text, timeout_override=task_ai_timeout, mime_hint=mime_hint)
    elif image_inline_b64:
        img_bytes = base64.b64decode(image_inline_b64)
        odgovor_html, used_path, used_model = route_image_flow(img_bytes, razred, history=history, user_text=user_text, timeout_override=task_ai_timeout, mime_hint=None)
    elif image_url:
        odgovor_html, used_path, used_model = route_image_flow_url(image_url, razred, history, user_text=user_text, timeout_override=task_ai_timeout)
    else:
        odgovor_html, used_model = answer_with_text_pipeline(user_text, razred, history, requested, timeout_override=task_ai_timeout)
        used_path = "text"
    # graf radi i za async/slikovne odgovore (ranije samo u sync putanji)
    if should_plot(user_text):
        expr = extract_plot_expression(user_text, razred=razred, history=history)
        if expr:
            odgovor_html = add_plot_div_once(odgovor_html, expr)
    result = {"html": odgovor_html, "path": used_path, "model": used_model}
    return {
        "status": "done",
        "result": result,
        "finished_at": datetime.datetime.utcnow().isoformat() + "Z",
        "razred": razred,
        "user_text": user_text,
        "requested": requested,
    }

def _local_worker(payload: dict):
    job_id = payload["job_id"]
    try:
        out = _process_job_core(payload)
        store_job(job_id, out, merge=True)
        try: log_to_sheet(job_id, out.get("razred"), out.get("user_text"), out["result"]["html"], out["result"]["path"], out["result"]["model"])
        except Exception: pass
    except Exception as e:
        err_html = ("<p><b>Nije uspjela obrada.</b> Pokušaj ponovo ili pošalji jasniji unos.</p>" f"<p><code>{html.escape(str(e))}</code></p>")
        store_job(job_id, {"status": "done", "result": {"html": err_html, "path": "error", "model": "n/a"}, "finished_at": datetime.datetime.utcnow().isoformat() + "Z"}, merge=True)

def estimate_tokens(text: str) -> int:
    if not text: return 0
    return max(0, len(text) // 4)

def looks_heavy(user_text: str, has_image: bool) -> bool:
    toks = estimate_tokens(user_text or "")
    return has_image or toks > HEAVY_TOKEN_THRESHOLD


def _prepare_async_payload(job_id: str, razred: str, user_text: str, requested: list, image_url: str | None, file_bytes: bytes | None, file_name: str | None, file_mime: str | None, image_b64_str: str | None, history: list | None = None) -> dict:
    payload = {
        "job_id": job_id, "razred": razred, "user_text": user_text, "requested": requested,
        "bucket": GCS_BUCKET, "image_path": None, "image_url": image_url or None,
        "image_inline_b64": None, "history": sanitize_history(history or []),
    }
    if file_bytes:
        if not LOCAL_MODE and (storage_client and GCS_BUCKET):
            path = gcs_upload_bytes(job_id, file_bytes, filename_hint=(file_name or "image.bin"), content_type=file_mime or "application/octet-stream")
            if path: payload["image_path"] = path
        else:
            payload["image_inline_b64"] = base64.b64encode(file_bytes).decode()
        return payload
    if image_b64_str:
        b64_clean = image_b64_str.split(",", 1)[1] if "," in image_b64_str else image_b64_str
        if not LOCAL_MODE and (storage_client and GCS_BUCKET):
            try:
                raw = base64.b64decode(b64_clean)
            except Exception:
                raw = b""
            path = gcs_upload_bytes(job_id, raw, filename_hint="image.bin", content_type="application/octet-stream")
            if path: payload["image_path"] = path
        else:
            payload["image_inline_b64"] = b64_clean
        return payload
    return payload




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
        try:
            data = json.loads(request.form.get("payload") or "{}")
        except Exception:
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
    except Exception:
        log.exception("ai_tutor_chat: neuspjeh")
        return jsonify({"error": "ai_tutor_failed",
                        "detail": "Došlo je do greške na serveru. Pokušaj ponovo."}), 500
    return jsonify(result), 200


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


@app.route("/submit", methods=["POST", "OPTIONS"])
@limiter.limit(_submit_rate_limit, exempt_when=lambda: request.method == "OPTIONS")
def submit():
    debug = (request.args.get("debug") == "1") or (request.headers.get("X-Debug") == "1")
    prof = Prof() if debug else None
    if prof: prof.mark("submit_start")

    if request.method == "OPTIONS":
        return ("", 204)

    #ALWAYS define this so fallback never crashes
    sync_try = {"ok": False, "error": None}

    # --- osnovni parametri ---
    razred = (request.form.get("razred") or request.args.get("razred") or "").strip()
    user_text = (request.form.get("user_text") or request.form.get("pitanje") or "").strip()
    image_url = (request.form.get("image_url") or request.args.get("image_url") or "").strip()
    mode = (request.form.get("mode") or request.args.get("mode") or "auto").strip().lower()

    # JSON tijelo (ako postoji) ima prednost
    data = request.get_json(silent=True) or {}
    if data:
        razred    = (data.get("razred")    or razred).strip()
        user_text = (data.get("pitanje")   or data.get("user_text") or user_text).strip()
        image_url = (data.get("image_url") or image_url).strip()
        mode      = (data.get("mode")      or mode).strip().lower()

    if prof:
        prof.set("has_image", bool(image_url or request.files.get("file") or (data.get("image_b64") if data else None)))
        prof.set("mode", mode)
        prof.set("user_len", len(user_text or ""))

    if razred not in DOZVOLJENI_RAZREDI:
        razred = "5"

    requested = extract_requested_tasks(user_text)

    # fajl (slika) iz forme
    file_storage = request.files.get("file")
    file_bytes = None
    file_mime = None
    file_name = None
    if file_storage and file_storage.filename:
        file_bytes = file_storage.read()
        file_mime = file_storage.mimetype or "application/octet-stream"
        file_name = file_storage.filename

    # base64 slika iz JSON-a (ako ima)
    image_b64_str = (data.get("image_b64") if data else None)

    has_image = bool(image_url or file_bytes or image_b64_str)

    if mode not in ("auto", "sync", "async"):
        mode = "auto"

    # ---- Historija razgovora ----
    # Primarni izvor: history_json koji šalje frontend (radi i u iframe-u, bez kolačića).
    # Fallback: stara sesijska api_history (kompatibilnost sa starijim klijentima).
    api_history = get_history_from_request()
    if api_history is None:
        _sess_hist = session.get("api_history", [])
        api_history = sanitize_history(_sess_hist if isinstance(_sess_hist, list) else [])

    # --- 1) Čisti async mode ili heavy auto → odmah queue ---
    heavy = looks_heavy(user_text, has_image=has_image)
    if mode == "async" or (mode == "auto" and heavy):
        job_id = str(uuid4())
        store_job(
            job_id,
            {
                "status": "pending",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                "razred": razred,
                "user_text": user_text,
                "requested": requested,
            },
            merge=True,
        )
        payload = _prepare_async_payload(
            job_id,
            razred,
            user_text,
            requested,
            image_url or None,
            file_bytes,
            file_name,
            file_mime,
            image_b64_str,
            history=api_history,
        )

        try:
            _enqueue(payload)
            mode_tag = "async" if mode == "async" else "auto→async"
            return jsonify(
                {
                    "mode": mode_tag,
                    "job_id": job_id,
                    "status": "queued",
                    "local_mode": LOCAL_MODE,
                }
            ), 202
        except Exception as e:
            store_job(job_id, {"status": "error", "error": str(e)}, merge=True)
            return jsonify({"error": "submit_failed", "detail": str(e), "job_id": job_id}), 500

    # --- 2) Sync pokušaj (mode == "sync" ili "auto" bez heavy) ---
    if prof: prof.mark("sync_try_start")
    try:
        # za slike: preskoči sync pokušaj, odmah fallback na async
        if image_url or file_bytes or image_b64_str:
            raise TimeoutError("skip-sync-for-images")

        html_out, actual_model = answer_with_text_pipeline(
            user_text, razred, api_history, requested,
            timeout_override=SYNC_SOFT_TIMEOUT_S,
            fast=True
        )

        if should_plot(user_text):
            expr = extract_plot_expression(user_text, razred=razred, history=api_history)
            if expr:
                html_out = add_plot_div_once(html_out, expr)

        # Sesijska kopija je samo fallback za starije klijente: čisti tekst i kratko,
        # jer session kolačić ima limit ~4KB (puni HTML ga je ranije tiho prepunjavao).
        api_history.append({"user": user_text, "bot": html_out})
        _sess_fallback = [{"user": (m.get("user") or "")[:300],
                           "bot": strip_html_to_text(m.get("bot") or "")[:600]}
                          for m in api_history[-3:]]
        session["api_history"] = _sess_fallback

        try:
            log_to_sheet(f"sync-{uuid4().hex[:8]}", razred, user_text, html_out, "text", actual_model)
        except Exception:
            pass

        payload = {
            "mode": "auto(sync)" if mode == "auto" else "sync",
            "result": {"html": html_out, "path": "text", "model": actual_model}
        }
        if prof:
            prof.mark("before_return_200")
            payload["timings"] = prof.out()
        return jsonify(payload), 200

    except Exception as e:
        # IMPORTANT: store why sync failed so async response can show reason
        sync_try = {"ok": False, "error": str(e)}

    # --- 3) Sync nije uspio → fallback na async ---
    job_id = str(uuid4())
    store_job(
        job_id,
        {
            "status": "pending",
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "razred": razred,
            "user_text": user_text,
            "requested": requested,
        },
        merge=True,
    )
    payload = _prepare_async_payload(
        job_id,
        razred,
        user_text,
        requested,
        image_url or None,
        file_bytes,
        file_name,
        file_mime,
        image_b64_str,
        history=api_history,
    )
    try:
        _enqueue(payload)
        mode_tag = "auto(sync→async)" if mode == "auto" else "sync→async"
        if prof:
            prof.mark("before_return_202")
        return jsonify(
            {
                "mode": mode_tag,
                "job_id": job_id,
                "status": "queued",
                "local_mode": LOCAL_MODE,
                "reason": sync_try.get("error") or "soft-timeout-or-error",
                "timings": prof.out() if prof else None
            }
        ), 202
    except Exception as e:
        store_job(job_id, {"status": "error", "error": str(e)}, merge=True)
        return jsonify({"error": "submit_failed", "detail": str(e), "job_id": job_id}), 500



@app.get("/status/<job_id>")
def async_status(job_id):
    data = read_job(job_id)
    if not data: return jsonify({"status": "pending"}), 200
    return jsonify(data), 200

@app.get("/result/<job_id>")
def async_result(job_id):
    data = read_job(job_id)
    if not data:
        return jsonify({"status": "pending"}), 202
    if data.get("status") == "done":
        return jsonify({"job_id": job_id, "result": data.get("result")}), 200
    if data.get("status") == "error":
        return jsonify({"job_id": job_id, "status": "error", "error": data.get("error")}), 500
    return jsonify({"job_id": job_id, "status": data.get("status", "pending")}), 202

@app.post("/tasks/process")
def tasks_process():
    # Bez TASKS_SECRET-a endpoint je u produkciji ZATVOREN (deny-all) — ranije je
    # default "super-secret" tiho otvarao vrata svakome ko pogodi header.
    if not LOCAL_MODE and (not TASKS_SECRET or request.headers.get("X-Tasks-Secret") != TASKS_SECRET):
        return "Forbidden", 403
    try:
        payload = request.get_json(force=True)
        job_id = payload["job_id"]
        out = _process_job_core(payload)
        store_job(job_id, out, merge=True)
        try:
            log_to_sheet(job_id, out.get("razred"), out.get("user_text"), out["result"]["html"], out["result"]["path"], out["result"]["model"])
        except Exception:
            pass
        return "OK", 200
    except Exception as e:
        err_html = ("<p><b>Nije uspjela obrada.</b> Pokušaj ponovo ili pošalji jasniji unos.</p>" f"<p><code>{html.escape(str(e))}</code></p>")
        job_id = (request.get_json(silent=True) or {}).get("job_id", f"unknown-{uuid4().hex[:6]}")
        store_job(job_id, {"status": "done", "result": {"html": err_html, "path": "error", "model": "n/a"}, "finished_at": datetime.datetime.utcnow().isoformat() + "Z"}, merge=True)
        return "OK", 200

@app.post("/set-razred")
def set_razred():
    g = (request.form.get("razred") or "").strip()
    if g:
        session["razred"] = g
        session["history"] = []
        session["api_history"] = []      # reset i API historije
        session.pop("last_image_url", None)
    return ("", 204)



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
    if not TASKS_SECRET:
        log.warning("ENV SANITY: TASKS_SECRET nije postavljen — /tasks/process "
                    "odbija sve zahtjeve (async Cloud Tasks tok je isključen).")
    if not _cors_origins:
        log.warning("ENV SANITY: CORS_ORIGINS nije postavljen — CORS je otvoren za "
                    "SVE domene. Postavi npr. CORS_ORIGINS=https://skola.thinkific.com")
    if not AI_TUTOR_EMBED_SECRET:
        log.warning("ENV SANITY: AI_TUTOR_EMBED_SECRET nije postavljen — token "
                    "zaštita /api/ai-tutor/* je ISKLJUČENA (svi zahtjevi prolaze).")
    if not DIAG_TOKEN:
        log.info("ENV SANITY: DIAG_TOKEN nije postavljen — dijagnostički endpointi "
                 "su nedostupni (to je OK ako ih ne koristiš).")


_startup_env_sanity()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    log.info("Starting app on port %s, LOCAL_MODE=%s", port, LOCAL_MODE)
    app.run(host="0.0.0.0", port=port, debug=debug)
