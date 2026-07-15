"""Optional Google Sheets transcript log for the modular tutor.

This module logs the full tutor transcript only when explicitly configured.
Without credentials it is a no-op, and every public function is defensive:
logging must never break the tutor response path.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # Optional dependency; requirements installs it for runtime, tests may mock it.
    import gspread  # type: ignore
except Exception:  # pragma: no cover - exercised only when dependency is absent
    gspread = None  # type: ignore

try:  # Optional dependency; kept lazy-safe for import-time behavior.
    from google.oauth2.service_account import Credentials as SACreds  # type: ignore
except Exception:  # pragma: no cover - exercised only when dependency is absent
    SACreds = None  # type: ignore

log = logging.getLogger("matbot.sheets")

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

sheet = None
_sheets_initialized = False
_sheet_layout_prepared = False
_sheets_lock = threading.Lock()

SHEET_HEADERS = [
    "timestamp_iso",
    "event_type",
    "session_id",
    "message_index",
    "grade",
    "mode",
    "topic",
    "entry_source",
    "status",
    "answer_verdict",
    "feedback_verdict",
    "recommend_video",
    "correct_streak",
    "topic_conflict",
    "selected_oblast",
    "last_tutor_task",
    "student_message",
    "answer",
    "answer_check",
    "next_state",
]


def _env_flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def _json_cell(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _credentials_file() -> Path:
    return Path("credentials.json")


def _has_explicit_credentials() -> bool:
    if (os.getenv("GOOGLE_SHEETS_CREDENTIALS_B64") or "").strip():
        return True
    return _credentials_file().exists()


def _try_get_sa_email_from_creds(creds: Any) -> str | None:
    email = getattr(creds, "service_account_email", None)
    if email:
        return str(email)
    try:
        info = getattr(creds, "_service_account_email", None) or getattr(creds, "_subject", None)
        if info:
            return str(info)
    except Exception:
        pass
    return None


def _init_sheets():
    """Lazy, thread-safe initialization of the first worksheet."""
    global sheet, _sheets_initialized
    if _sheets_initialized:
        return sheet
    with _sheets_lock:
        if _sheets_initialized:
            return sheet
        try:
            if gspread is None or SACreds is None:
                raise RuntimeError("gspread/google-auth nisu dostupni")

            b64 = (os.getenv("GOOGLE_SHEETS_CREDENTIALS_B64") or "").strip()
            creds = None
            if b64:
                info = json.loads(base64.b64decode(b64).decode("utf-8"))
                creds = SACreds.from_service_account_info(info, scopes=SHEETS_SCOPES)
            elif _credentials_file().exists():
                creds = SACreds.from_service_account_file(
                    str(_credentials_file()), scopes=SHEETS_SCOPES
                )
            elif _env_flag("LOCAL_MODE"):
                return None
            else:
                return None

            gc = gspread.authorize(creds)
            gsheet_id = (os.getenv("GSHEET_ID") or "").strip()
            gsheet_name = (os.getenv("GSHEET_NAME") or "matematika-bot").strip()
            ss = gc.open_by_key(gsheet_id) if gsheet_id else gc.open(gsheet_name)
            try:
                sheet = ss.sheet1
            except Exception:
                sheet = ss.get_worksheet(0)
            log.info(
                "Sheets transcript log enabled (sa=%s, worksheet=%s)",
                _try_get_sa_email_from_creds(creds),
                getattr(sheet, "title", None),
            )
        except Exception as exc:
            sheet = None
            log.warning("Sheets inicijalizacija nije uspjela: %s", exc)
        finally:
            _sheets_initialized = True
    return sheet


def _ensure_sheet_layout(ws: Any) -> None:
    """Best-effort header/format setup. Never raises."""
    global _sheet_layout_prepared
    if _sheet_layout_prepared or not ws:
        return
    try:
        existing = []
        if hasattr(ws, "row_values"):
            try:
                existing = list(ws.row_values(1) or [])
            except Exception:
                existing = []
        if existing[: len(SHEET_HEADERS)] != SHEET_HEADERS and hasattr(ws, "update"):
            end_col = chr(ord("A") + len(SHEET_HEADERS) - 1)
            ws.update(f"A1:{end_col}1", [SHEET_HEADERS])
        for method_name, args in (
            ("freeze", {"rows": 1}),
            ("set_basic_filter", {"name": f"A1:{chr(ord('A') + len(SHEET_HEADERS) - 1)}1"}),
        ):
            method = getattr(ws, method_name, None)
            if callable(method):
                try:
                    if method_name == "set_basic_filter":
                        method(args["name"])
                    else:
                        method(**args)
                except Exception:
                    pass
        fmt = getattr(ws, "format", None)
        if callable(fmt):
            try:
                fmt("A1:T1", {"textFormat": {"bold": True}})
                fmt("P:T", {"wrapStrategy": "WRAP"})
            except Exception:
                pass
    except Exception:
        log.debug("Sheets layout priprema nije uspjela", exc_info=True)
    finally:
        _sheet_layout_prepared = True


def sheets_append_row_safe(values: list[Any]) -> bool:
    """Append one row to Sheets. Errors are logged and converted to False."""
    try:
        ws = _init_sheets()
        if not ws:
            return False
        _ensure_sheet_layout(ws)
        ws.append_row(values, value_input_option="USER_ENTERED")
        return True
    except Exception as exc:
        log.warning("Sheets append nije uspio: %s", exc)
        return False


def _build_transcript_row(payload: dict, response: dict) -> list[Any]:
    topic = response.get("final_topic") or response.get("effective_topic")
    next_state = response.get("next_state") or {}
    return [
        datetime.now(timezone.utc).isoformat(),
        "chat",
        _clean_cell(payload.get("session_id")),
        _clean_cell(payload.get("message_index")),
        _clean_cell(payload.get("grade")),
        _clean_cell(response.get("mode")),
        _clean_cell(topic),
        _clean_cell(response.get("entry_source_used")),
        _clean_cell(response.get("status")),
        _clean_cell(response.get("answer_verdict")),
        "",
        _clean_cell(response.get("recommend_video")),
        _clean_cell(next_state.get("correct_streak") if isinstance(next_state, dict) else ""),
        _clean_cell(response.get("topic_conflict")),
        _clean_cell(payload.get("selected_oblast")),
        _clean_cell(response.get("last_tutor_task")),
        _clean_cell(payload.get("student_message")),
        _clean_cell(response.get("answer")),
        _json_cell(response.get("answer_check")),
        _json_cell(response.get("next_state")),
    ]


def _build_feedback_row(payload: dict) -> list[Any]:
    return [
        datetime.now(timezone.utc).isoformat(),
        "feedback",
        _clean_cell(payload.get("session_id")),
        _clean_cell(payload.get("message_index")),
        "",
        _clean_cell(payload.get("mode")),
        _clean_cell(payload.get("topic")),
        "feedback",
        "ready",
        "",
        _clean_cell(payload.get("verdict")),
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]


def _async_enabled() -> bool:
    return _env_flag("SHEETS_ASYNC_LOG", "1")


def log_transcript_to_sheet(payload: dict, response: dict) -> bool:
    """Log a full tutor transcript row to Google Sheets.

    Returns True when the row was written synchronously or queued for async write.
    Returns False for disabled/non-ready/failing paths. Never raises.
    """
    try:
        payload = payload or {}
        response = response or {}
        if response.get("status") != "ready":
            return False
        if not _has_explicit_credentials():
            return False
        if gspread is None or SACreds is None:
            log.warning("Sheets transcript log iskljucen: gspread/google-auth nisu dostupni")
            return False

        row = _build_transcript_row(payload, response)
        if _async_enabled():
            threading.Thread(target=sheets_append_row_safe, args=(row,), daemon=True).start()
            return True
        return sheets_append_row_safe(row)
    except Exception:
        log.exception("Sheets transcript log nije uspio - tutor odgovor se ne prekida")
        return False


def log_feedback_to_sheet(payload: dict) -> bool:
    """Log thumbs-up/down metadata to Google Sheets.

    Row shape extends transcript rows with: event_type, message_index,
    feedback_verdict. It never stores message text.
    """
    try:
        payload = payload or {}
        if not _has_explicit_credentials():
            return False
        if gspread is None or SACreds is None:
            log.warning("Sheets feedback log iskljucen: gspread/google-auth nisu dostupni")
            return False
        verdict = _clean_cell(payload.get("verdict"))
        if verdict not in ("up", "down"):
            return False
        row = _build_feedback_row(payload)
        if _async_enabled():
            threading.Thread(target=sheets_append_row_safe, args=(row,), daemon=True).start()
            return True
        return sheets_append_row_safe(row)
    except Exception:
        log.exception("Sheets feedback log nije uspio - tutor odgovor se ne prekida")
        return False
