"""Optional Google Sheets transcript log for the modular tutor.

This module logs the full tutor transcript only when explicitly configured.
Without credentials it is a no-op, and every public function is defensive:
logging must never break the tutor response path.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import logging
import os
import queue
import threading
import time
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
_append_lock = threading.Lock()

_ASYNC_STOP = object()
_ASYNC_STATUS_LIMIT = 500
_async_condition = threading.Condition()
_async_queue: queue.Queue | None = None
_async_worker: threading.Thread | None = None
_async_shutdown = False
_async_pending = 0
_async_active = 0
_async_statuses: dict[str, dict[str, Any]] = {}
_async_status_order: list[str] = []
_async_stats: dict[str, int] = {
    "queued": 0,
    "delivered": 0,
    "retried": 0,
    "permanently_failed": 0,
    "dropped_on_shutdown": 0,
}


class _SheetsPermanentError(RuntimeError):
    """Permanent local/configuration failure that should not be retried."""

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
    "task_id",
    "task_status",
    "answer_type",
    "expected_answer",
    "normalized_expected",
    "student_answer",
    "normalized_student",
    "deterministic_check",
    "math_verification_used",
    "math_verification_match",
    "corrected_before_response",
    "verified_answer",
    "gpt_check_used",
    "gpt_check_confidence",
    "attempt_number",
    "total_attempt_count",
    "wrong_attempt_count",
    "hint_count",
    "parent_task_id",
    "followup_task_id",
    "task_origin",
    "completed_parent_task",
    "hint_level",
    "highest_hint_level",
    "hint_reason",
    "hint_history",
    "repeated_hint_prevented",
    "solution_revealed",
    "solved_independently",
    "solved_with_hints",
    "requires_independent_solution",
    "independent_followup_result",
    "last_hint_signature",
    "progress_signature",
    "multiple_choice_hint",
    "multiple_choice_result",
    "answer_verdict_detail",
    "sheets_event_id",
    # Phase 0 (Engine V2 shadow): one sanitized JSON telemetry field appended at
    # the END so existing columns/indices stay stable and old rows pad cleanly.
    "shadow_telemetry",
    # Phase 7 canary cohort marker ("1"/"0"). Telemetry ONLY — never affects
    # grading, state, counters or the student-visible response.
    "engine_canary",
    # Append-only. ``student_message`` is ALWAYS the raw student text; anything
    # the pipeline rewrote internally (hint prompts, adaptive instructions) is
    # recorded here instead, so the two can never be confused in analysis.
    "internal_instruction",
    # Minimal-engine routing trace: enabled / handled / decline_reason /
    # runtime_topic / canonical_topic / resolved_skill.
    "minimal_routing",
]


def _raw_student_message(payload: dict) -> str:
    """What the student ACTUALLY typed.

    The legacy pipeline overwrites ``student_message`` with internal
    instructions in ~10 places, so the raw text is captured at entry into
    ``raw_student_message`` and is authoritative here when present.
    """
    raw = payload.get("raw_student_message")
    if isinstance(raw, str) and raw.strip():
        return raw
    return payload.get("student_message") or ""


def _internal_instruction(payload: dict) -> str:
    """The rewritten instruction, when it differs from the raw message."""
    raw = payload.get("raw_student_message")
    current = payload.get("student_message")
    if isinstance(raw, str) and isinstance(current, str) and raw.strip() \
            and current.strip() and raw != current:
        return current
    return ""


def _env_flag(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def _canary_marker() -> str:
    """Sanitized canary cohort flag for every row (telemetry only)."""
    try:
        from matbot.engine_v2 import canary_marker
        return canary_marker()
    except Exception:
        return "0"


def _json_cell(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _sheet_col(n: int) -> str:
    """1-based spreadsheet column name: 1 -> A, 27 -> AA."""
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out or "A"


def _credentials_file() -> Path:
    return Path("credentials.json")


def _event_id_for_row(row: list[Any]) -> str:
    def cell(name: str) -> str:
        try:
            idx = SHEET_HEADERS.index(name)
            return str(row[idx] if idx < len(row) else "")
        except ValueError:
            return ""

    seed = "|".join([
        cell("timestamp_iso"),
        cell("event_type"),
        cell("session_id"),
        cell("message_index"),
        cell("task_id"),
    ])
    return "sheet_" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:24]


def _set_row_event_id(row: list[Any], event_id: str) -> None:
    try:
        idx = SHEET_HEADERS.index("sheets_event_id")
    except ValueError:
        return
    while len(row) < len(SHEET_HEADERS):
        row.append("")
    row[idx] = event_id


def _event_kind(row: list[Any]) -> str:
    try:
        idx = SHEET_HEADERS.index("event_type")
        return str(row[idx] if idx < len(row) else "") or "unknown"
    except ValueError:
        return "unknown"


def _make_event(row: list[Any]) -> dict[str, Any]:
    event_id = _event_id_for_row(row)
    _set_row_event_id(row, event_id)
    return {
        "event_id": event_id,
        "kind": _event_kind(row),
        "row": row,
        "enqueued_at": time.time(),
    }


def _status_snapshot(event: dict[str, Any], status: str, **extra: Any) -> dict[str, Any]:
    now = time.time()
    out = {
        "event_id": event.get("event_id"),
        "kind": event.get("kind"),
        "status": status,
        "enqueue_ts": event.get("enqueued_at"),
        "updated_ts": now,
        "retry_count": int(extra.pop("retry_count", 0) or 0),
    }
    out.update({k: v for k, v in extra.items() if v not in (None, "")})
    return out


def _record_status(event: dict[str, Any], status: str, **extra: Any) -> None:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    rec = _status_snapshot(event, status, **extra)
    with _async_condition:
        if event_id not in _async_statuses:
            _async_status_order.append(event_id)
            while len(_async_status_order) > _ASYNC_STATUS_LIMIT:
                old = _async_status_order.pop(0)
                _async_statuses.pop(old, None)
        else:
            prior = _async_statuses.get(event_id) or {}
            rec["retry_count"] = max(
                int(prior.get("retry_count", 0) or 0),
                int(rec.get("retry_count", 0) or 0),
            )
            if prior.get("enqueue_ts") is not None:
                rec["enqueue_ts"] = prior.get("enqueue_ts")
        _async_statuses[event_id] = rec
        _async_condition.notify_all()


def _bump_stat(name: str, amount: int = 1) -> None:
    with _async_condition:
        _async_stats[name] = int(_async_stats.get(name, 0) or 0) + amount
        _async_condition.notify_all()


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
            log.warning(
                "Sheets inicijalizacija nije uspjela (category=%s)",
                _sheets_error_category(exc),
            )
        finally:
            _sheets_initialized = True
    return sheet


def _ensure_width(ws: Any, needed: int) -> None:
    """Widen the sheet if it has fewer columns than the header row."""
    try:
        current = int(getattr(ws, "col_count", 0) or 0)
    except (TypeError, ValueError):
        return
    if not current or current >= needed:
        return
    resize = getattr(ws, "resize", None)
    if callable(resize):
        try:
            resize(cols=needed)
        except Exception:
            log.debug("Sheets: resize na %d kolona nije uspio", needed, exc_info=True)


def _update_range(ws: Any, range_name: str, values: list) -> None:
    """``ws.update`` across gspread 5 and 6 argument orders."""
    try:
        ws.update(values=values, range_name=range_name)
    except TypeError:
        ws.update(range_name, values)          # gspread 5.x positional order


def _sheets_safe(value: Any) -> Any:
    """Coerce one cell to something the API stores verbatim.

    With ``RAW`` every value is written exactly as given, so anything that is
    not a plain scalar must be flattened HERE rather than left for the client
    library to guess at. ``None`` becomes an empty string — never a sentinel.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _sheets_safe_row(values: list[Any]) -> list[Any]:
    """Exactly ``len(SHEET_HEADERS)`` sanitized cells — never more, never fewer."""
    row = [_sheets_safe(v) for v in list(values)[: len(SHEET_HEADERS)]]
    while len(row) < len(SHEET_HEADERS):
        row.append("")
    return row


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
        # The grid must be at least as wide as the header row. Appending columns
        # to SHEET_HEADERS without widening an existing sheet leaves data rows
        # wider than the grid, which the API can truncate or shift.
        _ensure_width(ws, len(SHEET_HEADERS))
        if existing[: len(SHEET_HEADERS)] != SHEET_HEADERS and hasattr(ws, "update"):
            end_col = _sheet_col(len(SHEET_HEADERS))
            # gspread >= 6 takes VALUES first, range second. The old order still
            # works via a deprecation shim, but relying on it is fragile.
            _update_range(ws, f"A1:{end_col}1", [SHEET_HEADERS])
        for method_name, args in (
            ("freeze", {"rows": 1}),
            ("set_basic_filter", {"name": f"A1:{_sheet_col(len(SHEET_HEADERS))}1"}),
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
                end_col = _sheet_col(len(SHEET_HEADERS))
                fmt(f"A1:{end_col}1", {"textFormat": {"bold": True}})
                fmt(f"P:{end_col}", {"wrapStrategy": "WRAP"})
            except Exception:
                pass
    except Exception:
        log.debug("Sheets layout priprema nije uspjela", exc_info=True)
    finally:
        _sheet_layout_prepared = True


def _status_code_from_exception(exc: Exception) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _sheets_error_category(exc: Exception) -> str:
    status = _status_code_from_exception(exc)
    if status == 429:
        return "rate_limited"
    if status in (500, 502, 503, 504):
        return "temporary_server_error"
    if status in (401, 403):
        return "auth_or_permission"
    if status == 404:
        return "sheet_not_found"
    if status is not None and 400 <= status < 500:
        return "invalid_request"
    if isinstance(exc, _SheetsPermanentError):
        return "not_configured"
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timeout" in text:
        return "timeout"
    if "connection" in name or "connection" in text:
        return "connection_error"
    if "rate" in text and "limit" in text:
        return "rate_limited"
    if "invalid_grant" in text or "unauthorized" in text or "forbidden" in text:
        return "auth_or_permission"
    if "not found" in text:
        return "sheet_not_found"
    return "unexpected"


def _is_transient_category(category: str) -> bool:
    return category in {
        "timeout",
        "connection_error",
        "rate_limited",
        "temporary_server_error",
    }


def _retry_count() -> int:
    try:
        return max(0, int(os.getenv("SHEETS_ASYNC_MAX_RETRIES", "2") or 2))
    except (TypeError, ValueError):
        return 2


def _retry_backoff_seconds(attempt: int) -> float:
    try:
        base = max(0.0, float(os.getenv("SHEETS_ASYNC_RETRY_BASE_S", "0.25") or 0.25))
    except (TypeError, ValueError):
        base = 0.25
    return min(5.0, base * (2 ** max(0, attempt - 1)))


#: This is a LOG, not a spreadsheet a human edits. ``USER_ENTERED`` made Sheets
#: parse every cell as if typed by a user, so a correct answer of "4/12" became
#: the date serial 46360 and "5/10" became 46300 — the graded value was right,
#: the stored value was destroyed. ``RAW`` writes each cell literally: strings
#: stay strings, numbers stay numbers, and nothing is re-interpreted.
SHEETS_VALUE_INPUT_OPTION = "RAW"


#: Temporary write diagnostic. OFF by default so normal logs are not flooded.
SHEETS_DIAGNOSTIC_ENV = "MATBOT_SHEETS_DIAGNOSTIC"


def _diagnostic_enabled() -> bool:
    return _env_flag(SHEETS_DIAGNOSTIC_ENV, "0")


def _describe_row(values: list[Any]) -> list[dict[str, Any]]:
    """Indexed header/type/repr view of a row, for the write diagnostic."""
    out: list[dict[str, Any]] = []
    for idx, header in enumerate(SHEET_HEADERS):
        value = values[idx] if idx < len(values) else "<MISSING>"
        out.append({
            "index": idx,
            "header": header,
            "type": type(value).__name__,
            "repr": repr(value)[:120],
        })
    return out


def _log_pre_write(row: list[Any]) -> None:
    log.info("SHEETS_DIAG pre_write cells=%d payload=%s", len(row),
             json.dumps(_describe_row(row), ensure_ascii=False))


def _read_back(ws: Any, updated_range: str) -> list[Any] | None:
    """Read the exact range back with UNFORMATTED values, or None."""
    spreadsheet = getattr(ws, "spreadsheet", None)
    values_get = getattr(spreadsheet, "values_get", None)
    if not callable(values_get):
        return None
    try:
        result = values_get(updated_range, params={
            "valueRenderOption": "UNFORMATTED_VALUE",
            "dateTimeRenderOption": "FORMATTED_STRING",
        })
    except Exception:
        log.exception("SHEETS_DIAG read_back failed range=%s", updated_range)
        return None
    rows = (result or {}).get("values") or []
    return list(rows[0]) if rows else []


def _log_read_back(ws: Any, response: Any, sent: list[Any]) -> None:
    """Read the written range back and report the FIRST mismatching cell."""
    updates = (response or {}).get("updates") or {}
    updated_range = updates.get("updatedRange") or ""
    log.info("SHEETS_DIAG api_response updatedRange=%s updatedColumns=%s "
             "updatedCells=%s", updated_range, updates.get("updatedColumns"),
             updates.get("updatedCells"))
    if not updated_range:
        return
    got = _read_back(ws, updated_range)
    if got is None:
        log.info("SHEETS_DIAG read_back unavailable (no values_get)")
        return
    log.info("SHEETS_DIAG read_back cells=%d payload=%s", len(got),
             json.dumps(_describe_row(got), ensure_ascii=False))

    first = None
    for idx in range(len(SHEET_HEADERS)):
        sent_v = sent[idx] if idx < len(sent) else "<MISSING>"
        got_v = got[idx] if idx < len(got) else "<MISSING>"
        # Sheets returns "" for a blank cell; treat blank/missing as equal.
        if (sent_v in ("", None) and got_v in ("", None, "<MISSING>")):
            continue
        if str(sent_v) != str(got_v):
            first = {"index": idx, "header": SHEET_HEADERS[idx],
                     "sent_type": type(sent_v).__name__, "sent": repr(sent_v)[:120],
                     "got_type": type(got_v).__name__, "got": repr(got_v)[:120]}
            break
    log.info("SHEETS_DIAG first_mismatch=%s",
             json.dumps(first, ensure_ascii=False) if first else "none")


def _log_target_row_before_append(ws: Any) -> None:
    """Does the row we are about to append into already hold data?"""
    try:
        row_count = len(ws.get_all_values()) if hasattr(ws, "get_all_values") else None
    except Exception:
        row_count = None
    next_row = (row_count + 1) if isinstance(row_count, int) else None
    log.info("SHEETS_DIAG target rows_with_data=%s next_row=%s col_count=%s",
             row_count, next_row, getattr(ws, "col_count", None))
    if next_row is None:
        return
    try:
        existing = ws.row_values(next_row) if hasattr(ws, "row_values") else None
    except Exception:
        existing = None
    log.info("SHEETS_DIAG target_row_before_append non_empty=%s sample=%s",
             bool(existing), json.dumps((existing or [])[:20], ensure_ascii=False))


def _append_row_once(values: list[Any]) -> None:
    with _append_lock:
        ws = _init_sheets()
        if not ws:
            raise _SheetsPermanentError("sheets_not_configured")
        _ensure_sheet_layout(ws)
        row = _sheets_safe_row(values)
        diagnostic = _diagnostic_enabled()
        if diagnostic:
            _log_pre_write(row)
            _log_target_row_before_append(ws)
        response = ws.append_row(row,
                                 value_input_option=SHEETS_VALUE_INPUT_OPTION)
        if diagnostic:
            try:
                _log_read_back(ws, response, row)
            except Exception:
                log.exception("SHEETS_DIAG read-back stage failed")


def _deliver_event(event: dict[str, Any]) -> bool:
    max_retries = _retry_count()
    retry_count = 0
    for attempt in range(max_retries + 1):
        try:
            _record_status(event, "delivering", retry_count=retry_count)
            _append_row_once(list(event.get("row") or []))
            _bump_stat("delivered")
            _record_status(
                event,
                "delivered",
                retry_count=retry_count,
                delivered_ts=time.time(),
            )
            return True
        except Exception as exc:
            category = _sheets_error_category(exc)
            retryable = _is_transient_category(category) and attempt < max_retries
            if retryable:
                retry_count += 1
                _bump_stat("retried")
                _record_status(
                    event,
                    "retried",
                    retry_count=retry_count,
                    error_category=category,
                )
                log.warning(
                    "Sheets async delivery retry event_id=%s category=%s retry=%s",
                    event.get("event_id"),
                    category,
                    retry_count,
                )
                time.sleep(_retry_backoff_seconds(retry_count))
                continue
            _bump_stat("permanently_failed")
            _record_status(
                event,
                "permanently_failed",
                retry_count=retry_count,
                error_category=category,
                delivered_ts=time.time(),
            )
            log.warning(
                "Sheets delivery failed event_id=%s category=%s retries=%s",
                event.get("event_id"),
                category,
                retry_count,
            )
            return False
    return False


def sheets_append_row_safe(values: list[Any]) -> bool:
    """Append one row to Sheets synchronously. Errors are sanitized and False."""
    event = _make_event(list(values))
    return _deliver_event(event)


def _first_answer_check_item(response: dict) -> dict:
    check = response.get("answer_check")
    if not isinstance(check, dict):
        return {}
    items = check.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def _build_transcript_row(payload: dict, response: dict) -> list[Any]:
    topic = response.get("final_topic") or response.get("effective_topic")
    # Kontrolni iz oblasti: final/effective ostaju "unknown" (NPP pravilo), ali je
    # razriješena tema oblast — loguj nju umjesto "unknown".
    if (not topic or str(topic).strip().lower() == "unknown") and response.get("resolved_exam_topic"):
        topic = response.get("resolved_exam_topic")
    next_state = response.get("next_state") or {}
    item = _first_answer_check_item(response)
    math_verification = response.get("math_verification") if isinstance(response.get("math_verification"), dict) else {}
    def _telemetry(key: str) -> Any:
        value = response.get(key)
        if value is None and isinstance(next_state, dict):
            value = next_state.get(key)
        return value

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
        # NOT _clean_cell: this column is the student's message verbatim, and
        # _clean_cell strips whitespace. Leading/trailing spaces are part of
        # what was actually typed.
        _raw_student_message(payload),
        _clean_cell(response.get("answer")),
        _json_cell(response.get("answer_check")),
        _json_cell(response.get("next_state")),
        _clean_cell(response.get("task_id") or next_state.get("task_id") or next_state.get("completed_task_id")),
        _clean_cell(response.get("task_status") or next_state.get("task_status")),
        _clean_cell(item.get("answer_type")),
        _clean_cell(item.get("expected_answer") or item.get("expected")),
        _clean_cell(item.get("normalized_expected")),
        # ONLY on a grading turn. The old fallback to the raw message filled
        # student_answer on every turn — a hint or a concept question is not an
        # answer, and logging one as if it were corrupts the audit.
        # An item that SETS the key (even to "") is authoritative: the minimal
        # engine leaves it empty when no final answer could be identified, and
        # falling back to the prose would record it as if it had been submitted.
        _clean_cell(
            item["student_answer"] if "student_answer" in item
            else (item.get("given")
                  or (_raw_student_message(payload) if item else ""))),
        _clean_cell(item.get("normalized_student")),
        _json_cell(item.get("deterministic_check")),
        _clean_cell(math_verification.get("math_verification_used")),
        _clean_cell(math_verification.get("math_verification_match")),
        _clean_cell(math_verification.get("corrected_before_response")),
        _clean_cell(math_verification.get("verified_answer")),
        _clean_cell(response.get("gpt_check_used")),
        _clean_cell(response.get("gpt_check_confidence")),
        _clean_cell(response.get("attempt_number") or next_state.get("attempt_count")),
        _clean_cell(response.get("total_attempt_count") or next_state.get("total_attempt_count") or next_state.get("attempt_count")),
        _clean_cell(response.get("wrong_attempt_count") or next_state.get("wrong_attempt_count")),
        _clean_cell(response.get("hint_count") or next_state.get("hint_count")),
        _clean_cell(_telemetry("parent_task_id")),
        _clean_cell(_telemetry("followup_task_id")),
        _clean_cell(_telemetry("task_origin")),
        _json_cell(_telemetry("completed_parent_task")),
        _clean_cell(_telemetry("hint_level")),
        _clean_cell(_telemetry("highest_hint_level")),
        _clean_cell(_telemetry("hint_reason")),
        _json_cell(_telemetry("hint_history")),
        _clean_cell(_telemetry("repeated_hint_prevented")),
        _clean_cell(_telemetry("solution_revealed")),
        _clean_cell(_telemetry("solved_independently")),
        _clean_cell(_telemetry("solved_with_hints")),
        _clean_cell(_telemetry("requires_independent_solution")),
        _clean_cell(_telemetry("independent_followup_result")),
        _clean_cell(_telemetry("last_hint_signature")),
        _clean_cell(_telemetry("progress_signature")),
        _json_cell(_telemetry("multiple_choice_hint")),
        _json_cell(_telemetry("multiple_choice_result")),
        _clean_cell(response.get("answer_verdict_detail") or item.get("verdict")),
        "",  # sheets_event_id (populated by _set_row_event_id)
        _json_cell(response.get("shadow_grading")),
        _canary_marker(),
        _clean_cell(_internal_instruction(payload)),
        _json_cell(response.get("minimal_routing")),
    ]


def _build_feedback_row(payload: dict) -> list[Any]:
    row = [""] * len(SHEET_HEADERS)
    values = {
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "event_type": "feedback",
        "session_id": _clean_cell(payload.get("session_id")),
        "message_index": _clean_cell(payload.get("message_index")),
        "mode": _clean_cell(payload.get("mode")),
        "topic": _clean_cell(payload.get("topic")),
        "entry_source": "feedback",
        "status": "ready",
        "feedback_verdict": _clean_cell(payload.get("verdict")),
    }
    for key, value in values.items():
        try:
            row[SHEET_HEADERS.index(key)] = value
        except ValueError:
            pass
    return row


def _async_enabled() -> bool:
    return _env_flag("SHEETS_ASYNC_LOG", "1")


def _async_worker_loop(q: queue.Queue) -> None:
    global _async_active, _async_pending
    while True:
        item = q.get()
        try:
            if item is _ASYNC_STOP:
                return
            with _async_condition:
                _async_active += 1
            _deliver_event(item)
        except Exception:
            # Defensive only: _deliver_event should sanitize/handle everything.
            log.exception("Sheets async worker unexpected failure")
        finally:
            if item is not _ASYNC_STOP:
                with _async_condition:
                    _async_pending = max(0, _async_pending - 1)
                    _async_active = max(0, _async_active - 1)
                    _async_condition.notify_all()
            try:
                q.task_done()
            except Exception:
                pass


def _ensure_async_worker() -> queue.Queue | None:
    global _async_queue, _async_worker
    with _async_condition:
        if _async_shutdown:
            return None
        if _async_queue is None:
            _async_queue = queue.Queue()
        if _async_worker is None or not _async_worker.is_alive():
            _async_worker = threading.Thread(
                target=_async_worker_loop,
                args=(_async_queue,),
                name="matbot-sheets-logger",
                daemon=True,
            )
            _async_worker.start()
        return _async_queue


def _enqueue_event(event: dict[str, Any]) -> bool:
    global _async_pending
    q = _ensure_async_worker()
    rejected = False
    with _async_condition:
        if q is None or _async_shutdown:
            _async_stats["dropped_on_shutdown"] = int(_async_stats.get("dropped_on_shutdown", 0) or 0) + 1
            rejected = True
        else:
            _async_pending += 1
            _async_stats["queued"] = int(_async_stats.get("queued", 0) or 0) + 1
    if rejected:
        _record_status(event, "dropped_on_shutdown")
        log.warning("Sheets async enqueue rejected after shutdown event_id=%s", event.get("event_id"))
        return False
    _record_status(event, "queued")
    q.put(event)
    return True


def flush(timeout: float = 10.0) -> bool:
    """Wait until accepted async rows finish delivery, bounded by timeout."""
    deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
    with _async_condition:
        while _async_pending > 0 or _async_active > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _async_condition.wait(timeout=remaining)
        return True


def shutdown(wait: bool = True, timeout: float = 10.0) -> bool:
    """Stop the async worker. A bounded flush is attempted when wait=True."""
    global _async_shutdown
    deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
    with _async_condition:
        _async_shutdown = True
    flushed = True
    if wait:
        flushed = flush(timeout=max(0.0, deadline - time.monotonic()))
    with _async_condition:
        q = _async_queue
        worker = _async_worker
        remaining_pending = _async_pending
        if remaining_pending > 0:
            _async_stats["dropped_on_shutdown"] = int(_async_stats.get("dropped_on_shutdown", 0) or 0) + remaining_pending
    if q is not None:
        try:
            q.put(_ASYNC_STOP)
        except Exception:
            pass
    if wait and worker is not None and worker.is_alive() and worker is not threading.current_thread():
        worker.join(timeout=max(0.0, deadline - time.monotonic()))
    return bool(flushed and remaining_pending == 0)


def get_delivery_stats() -> dict[str, Any]:
    with _async_condition:
        return {
            **{k: int(v or 0) for k, v in _async_stats.items()},
            "pending": int(_async_pending),
            "active": int(_async_active),
            "shutdown": bool(_async_shutdown),
            "worker_alive": bool(_async_worker and _async_worker.is_alive()),
            "recent_statuses": [dict(_async_statuses[eid]) for eid in _async_status_order if eid in _async_statuses],
        }


def _reset_async_state_for_tests() -> None:
    global _async_queue, _async_worker, _async_shutdown, _async_pending, _async_active
    shutdown(wait=True, timeout=1.0)
    with _async_condition:
        _async_queue = None
        _async_worker = None
        _async_shutdown = False
        _async_pending = 0
        _async_active = 0
        _async_statuses.clear()
        _async_status_order.clear()
        for key in list(_async_stats):
            _async_stats[key] = 0
        _async_condition.notify_all()


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
        event = _make_event(row)
        if _async_enabled():
            return _enqueue_event(event)
        return _deliver_event(event)
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
        event = _make_event(row)
        if _async_enabled():
            return _enqueue_event(event)
        return _deliver_event(event)
    except Exception:
        log.exception("Sheets feedback log nije uspio - tutor odgovor se ne prekida")
        return False


atexit.register(lambda: shutdown(wait=True, timeout=5.0))
