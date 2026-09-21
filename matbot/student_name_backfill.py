"""Name-only backfill za postojece Thinkific-email identitete.

Podrazumijevani poziv je iskljucivo preview. Apply zahtijeva i ``--apply`` i
otisak tacno onog plana koji je operator prethodno pregledao. Modul ne koristi
Thinkific progress importer i nema put koji kreira ucenika ili nalog.
"""
import argparse
import csv
import hashlib
import hmac
import io
import json
import logging
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

from matbot import config, reporting_db, student_identity


logger = logging.getLogger("matbot.student_name_backfill")

FIRST_NAME = "First Name"
LAST_NAME = "Last Name"
EMAIL = "Email"
REQUIRED_HEADERS = (FIRST_NAME, LAST_NAME, EMAIL)

WOULD_UPDATE = "WOULD_UPDATE"
SKIP_EXISTING_NAME = "SKIP_EXISTING_NAME"
UNMATCHED = "UNMATCHED"
ERROR = "ERROR"
DUPLICATE = "DUPLICATE"

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 10000
PLAN_VERSION = 1
PLAN_PREFIX = "name-backfill-v1-"


class BackfillInputError(RuntimeError):
    """Strukturni kod ulazne greske; nikad ne sadrzi CSV podatke ni putanju."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SourceRow:
    row_number: int
    email: str | None
    masked_email: str
    proposed_display_name: str | None
    error_code: str | None = None


@dataclass(frozen=True)
class PlanRow:
    row_number: int
    email: str | None
    masked_email: str
    student_id: int | None
    current_display_name: str | None
    proposed_display_name: str | None
    classification: str
    reason: str = ""


@dataclass(frozen=True)
class BackfillPlan:
    rows: tuple[PlanRow, ...]
    file_errors: tuple[str, ...]
    plan_id: str | None

    @property
    def totals(self):
        return {
            "total_rows": len(self.rows),
            "matched": sum(row.student_id is not None for row in self.rows),
            "would_update": sum(row.classification == WOULD_UPDATE
                                for row in self.rows),
            "already_named": sum(row.classification == SKIP_EXISTING_NAME
                                 for row in self.rows),
            "unmatched": sum(row.classification == UNMATCHED
                             for row in self.rows),
            "errors": (len(self.file_errors)
                       + sum(row.classification == ERROR for row in self.rows)),
            "duplicates": sum(row.classification == DUPLICATE
                              for row in self.rows),
        }

    @property
    def apply_allowed(self):
        totals = self.totals
        return totals["errors"] == 0 and totals["duplicates"] == 0

    def changes(self):
        return [
            {
                "student_id": row.student_id,
                "external_user_id": row.email,
                "expected_display_name": row.current_display_name,
                "display_name": row.proposed_display_name,
            }
            for row in self.rows if row.classification == WOULD_UPDATE
        ]


def _contains_control(value):
    return any(unicodedata.category(char) in ("Cc", "Cf", "Cs")
               for char in value)


def _build_display_name(first, last):
    """Ista whitespace semantika kao progress importer, ali bez trunciranja."""
    if _contains_control(first) or _contains_control(last):
        return None, "name_control_character"
    parts = []
    for value in (first, last):
        if value.strip():
            parts.append(" ".join(value.split()))
    if not parts:
        return None, "name_blank"
    name = " ".join(parts)
    if len(name) > reporting_db.MAX_DISPLAY_NAME_CHARS:
        return None, "name_too_long"
    return name, None


def mask_email(email):
    """Maskirani prikaz koji nikad ne vraca punu adresu."""
    if not isinstance(email, str) or "@" not in email:
        return "<invalid>"
    local, domain = email.rsplit("@", 1)
    visible = local[:1] if len(local) > 1 else ""
    return visible + "***@" + domain


def parse_csv(payload):
    """Vrati ``(rows, file_errors)`` bez ijednog baznog upita ili upisa."""
    if not isinstance(payload, bytes):
        raise BackfillInputError("csv_bytes_required")
    if not payload or not payload.strip():
        return (), ("csv_empty",)
    if len(payload) > MAX_CSV_BYTES:
        return (), ("csv_too_large",)
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return (), ("csv_not_utf8",)

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        return (), ("csv_empty",)
    except csv.Error:
        return (), ("csv_malformed",)

    file_errors = []
    for required in REQUIRED_HEADERS:
        count = header.count(required)
        if count == 0:
            file_errors.append("missing_required_header:" + required)
        elif count > 1:
            file_errors.append("duplicate_required_header:" + required)
    if file_errors:
        return (), tuple(file_errors)

    indexes = {name: header.index(name) for name in REQUIRED_HEADERS}
    parsed = []
    try:
        for row_number, values in enumerate(reader, start=2):
            if not values:
                continue
            if len(parsed) >= MAX_CSV_ROWS:
                file_errors.append("csv_too_many_rows")
                break
            if len(values) != len(header):
                parsed.append(SourceRow(
                    row_number, None, "<invalid>", None,
                    "row_column_count_mismatch"))
                continue

            raw_email = values[indexes[EMAIL]]
            email = student_identity.normalize_email(raw_email)
            if email is None:
                parsed.append(SourceRow(
                    row_number, None, "<invalid>", None, "email_invalid"))
                continue

            display_name, name_error = _build_display_name(
                values[indexes[FIRST_NAME]], values[indexes[LAST_NAME]])
            parsed.append(SourceRow(
                row_number, email, mask_email(email), display_name, name_error))
    except csv.Error:
        file_errors.append("csv_malformed")

    if not parsed and not file_errors:
        file_errors.append("csv_no_data_rows")
    return tuple(parsed), tuple(file_errors)


def _is_blank_display_name(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _database_scope():
    """Nepovratan marker baze; plan iz drugog okruzenja ne moze se primijeniti."""
    value = config.turso_database_url()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _plan_fingerprint(rows, file_errors):
    secret = config.SECRET_KEY
    if not secret:
        raise BackfillInputError("plan_secret_missing")
    material = {
        "version": PLAN_VERSION,
        "database_scope": _database_scope(),
        "provider": student_identity.PROVIDER_THINKIFIC_EMAIL,
        "file_errors": list(file_errors),
        "rows": [
            {
                "row_number": row.row_number,
                "email": row.email,
                "student_id": row.student_id,
                "current_display_name": row.current_display_name,
                "proposed_display_name": row.proposed_display_name,
                "classification": row.classification,
                "reason": row.reason,
            }
            for row in rows
        ],
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), encoded,
                      hashlib.sha256).hexdigest()
    return PLAN_PREFIX + digest


def build_plan(source_rows, file_errors, database):
    valid = [row for row in source_rows if row.error_code is None]
    email_counts = Counter(row.email for row in valid)
    duplicate_emails = {email for email, count in email_counts.items() if count > 1}

    emails = list(dict.fromkeys(row.email for row in valid))
    targets = database.lookup_thinkific_students(emails) if emails else {}

    by_student = defaultdict(list)
    for row in valid:
        if row.email in duplicate_emails:
            continue
        target = targets.get(row.email)
        if target is not None:
            by_student[target["student_id"]].append(row)
    conflicting_students = {student_id for student_id, rows in by_student.items()
                            if len(rows) > 1}

    planned = []
    for row in source_rows:
        target = targets.get(row.email) if row.email else None
        student_id = target["student_id"] if target else None
        current = target["display_name"] if target else None

        if row.error_code:
            classification, reason = ERROR, row.error_code
        elif row.email in duplicate_emails:
            classification, reason = DUPLICATE, "duplicate_normalized_email"
        elif student_id in conflicting_students:
            classification, reason = DUPLICATE, "conflicting_target_student"
        elif target is None:
            classification, reason = UNMATCHED, "account_not_found"
        elif _is_blank_display_name(current):
            classification, reason = WOULD_UPDATE, ""
        else:
            classification, reason = SKIP_EXISTING_NAME, "name_already_present"

        planned.append(PlanRow(
            row.row_number, row.email, row.masked_email, student_id, current,
            row.proposed_display_name, classification, reason))

    preliminary = BackfillPlan(tuple(planned), tuple(file_errors), None)
    plan_id = (_plan_fingerprint(preliminary.rows, preliminary.file_errors)
               if preliminary.apply_allowed else None)
    return BackfillPlan(preliminary.rows, preliminary.file_errors, plan_id)


def _escaped_text(value):
    safe = []
    for char in value:
        if unicodedata.category(char) in ("Cc", "Cf", "Cs"):
            safe.append("\\u%04x" % ord(char))
        else:
            safe.append(char)
    return json.dumps("".join(safe), ensure_ascii=False)


def _display_name(value):
    if value is None:
        return "<NULL>"
    if value == "":
        return "<EMPTY>"
    if isinstance(value, str) and not value.strip():
        return "<WHITESPACE>"
    return _escaped_text(str(value))


def render_plan(plan, stream=None):
    stream = stream or sys.stdout
    print("NAME-ONLY BACKFILL PREVIEW - NO DATABASE WRITES", file=stream)
    print("row | classification | student_id | email | current | proposed | reason",
          file=stream)
    for row in plan.rows:
        print("%s | %s | %s | %s | %s | %s | %s" % (
            row.row_number,
            row.classification,
            row.student_id if row.student_id is not None else "-",
            row.masked_email,
            _display_name(row.current_display_name),
            _display_name(row.proposed_display_name),
            row.reason or "-"), file=stream)
    for code in plan.file_errors:
        print("file error: %s" % code, file=stream)

    totals = plan.totals
    print("", file=stream)
    print("total rows: %s" % totals["total_rows"], file=stream)
    print("matched: %s" % totals["matched"], file=stream)
    print("would update: %s" % totals["would_update"], file=stream)
    print("already named: %s" % totals["already_named"], file=stream)
    print("unmatched: %s" % totals["unmatched"], file=stream)
    print("errors: %s" % totals["errors"], file=stream)
    print("duplicates: %s" % totals["duplicates"], file=stream)
    print("apply allowed: %s" % ("YES" if plan.apply_allowed else "NO"),
          file=stream)
    if plan.apply_allowed:
        print("plan_id: %s" % plan.plan_id, file=stream)


def _read_payload(csv_source, stdin=None):
    try:
        if csv_source == "-":
            stream = stdin
            if stream is None:
                stream = getattr(sys.stdin, "buffer", sys.stdin)
            payload = stream.read(MAX_CSV_BYTES + 1)
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
        else:
            with open(csv_source, "rb") as source:
                payload = source.read(MAX_CSV_BYTES + 1)
    except (OSError, ValueError):
        raise BackfillInputError("csv_unreadable") from None
    if len(payload) > MAX_CSV_BYTES:
        raise BackfillInputError("csv_too_large")
    return payload


def _log_plan(plan):
    totals = plan.totals
    logger.info(
        "student_name_backfill_preview total=%s matched=%s would_update=%s "
        "already_named=%s unmatched=%s errors=%s duplicates=%s allowed=%s "
        "plan_id=%s",
        totals["total_rows"], totals["matched"], totals["would_update"],
        totals["already_named"], totals["unmatched"], totals["errors"],
        totals["duplicates"], plan.apply_allowed, plan.plan_id or "none")


def run(argv=None, *, database=None, stdin=None, stdout=None):
    parser = argparse.ArgumentParser(
        prog="python -m matbot.student_name_backfill",
        description="Preview/apply popune praznih imena po Thinkific e-mailu.")
    parser.add_argument("--csv", required=True,
                        help="CSV putanja ili - za standardni ulaz")
    parser.add_argument("--apply", action="store_true",
                        help="primijeni prethodno pregledani plan")
    parser.add_argument("--confirm-plan", default="",
                        help="tacan plan_id iz previewa")
    args = parser.parse_args(argv)
    stdout = stdout or sys.stdout

    try:
        payload = _read_payload(args.csv, stdin=stdin)
        source_rows, file_errors = parse_csv(payload)
        target = database or reporting_db.get_database()
        plan = build_plan(source_rows, file_errors, target)
    except BackfillInputError as error:
        print("input error: %s" % error.code, file=stdout)
        print("apply allowed: NO", file=stdout)
        logger.info("student_name_backfill_refused code=%s", error.code)
        return 1
    except reporting_db.ReportingUnavailable as error:
        print("database error: %s" % error.code, file=stdout)
        print("apply allowed: NO", file=stdout)
        logger.info("student_name_backfill_refused code=%s", error.code)
        return 1

    render_plan(plan, stdout)
    _log_plan(plan)

    if not args.apply:
        if args.confirm_plan:
            print("apply refused: confirm_plan_without_apply", file=stdout)
            logger.info("student_name_backfill_refused code=confirm_without_apply")
            return 2
        return 0 if plan.apply_allowed else 1

    if not plan.apply_allowed:
        print("apply refused: plan_has_blocking_issues", file=stdout)
        logger.info("student_name_backfill_refused code=blocking_issues")
        return 2
    if not args.confirm_plan:
        print("apply refused: confirmation_required", file=stdout)
        logger.info("student_name_backfill_refused code=confirmation_required")
        return 2
    if not hmac.compare_digest(args.confirm_plan, plan.plan_id):
        print("apply refused: plan_mismatch", file=stdout)
        logger.info("student_name_backfill_refused code=plan_mismatch")
        return 2

    try:
        updated = target.apply_student_display_names(plan.changes())
    except reporting_db.ReportingUnavailable as error:
        print("apply failed: %s" % error.code, file=stdout)
        logger.info("student_name_backfill_apply_failed code=%s plan_id=%s",
                    error.code, plan.plan_id)
        return 1

    expected = plan.totals["would_update"]
    if updated != expected:  # data layer already rolls back; fail closed anyway.
        print("apply failed: update_count_mismatch", file=stdout)
        logger.info("student_name_backfill_apply_failed code=count_mismatch "
                    "plan_id=%s", plan.plan_id)
        return 1
    print("apply result: SUCCESS", file=stdout)
    print("updated: %s" % updated, file=stdout)
    logger.info("student_name_backfill_applied updated=%s plan_id=%s",
                updated, plan.plan_id)
    return 0


def main(argv=None):
    return run(argv)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
