"""Sigurno usklađivanje tekućeg registra sa četiri Thinkific izvoza.

Ovaj modul NE uvozi napredak. On od istih, već validiranih ``Student Progress``
fajlova pravi pregled promjena registra, potpisuje tačno pregledano stanje i
predaje izabrane promjene jedinoj transakciji u ``reporting_db``.

E-mail je jedini identitet. Ime služi za prikaz i smije se osvježiti, ali se po
njemu nikad ne spajaju dva učenika. Broj u imenu je samo prijedlog za ljudsku
potvrdu razreda; Thinkific kurs i dalje nije autoritet nad tekućim razredom.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import hmac
import json

from matbot import config, reporting_db, student_grades, student_identity


PLAN_VERSION = "thinkific-roster-v1"
REQUIRED_COURSES = ("grade_6", "grade_7", "grade_8", "grade_9")

NAME = "name"
ADD = "add"
ARCHIVE = "archive"
REACTIVATE = "reactivate"
GRADE = "grade"

_INACTIVE = frozenset(reporting_db.ReportingDatabase._INACTIVE_STATUSES)


class RosterPlanError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _secret():
    value = config.SECRET_KEY
    if not value:
        raise RosterPlanError("plan_secret_missing")
    return value.encode("utf-8")


def _mask_email(email):
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "<neispravna adresa>"
    return (local[:1] if local else "") + "***@" + domain


def _name_key(value):
    return " ".join(str(value or "").split()).casefold()


def _status(value):
    return str(value or "active").strip().lower() or "active"


def _grade_label(values):
    grades = tuple(sorted(int(value) for value in (values or ())))
    if not grades:
        return "Nepotvrđen"
    return " + ".join("%d. razred" % value for value in grades)


def _subject_key(kind, subject):
    material = (PLAN_VERSION + "\x00" + kind + "\x00" + str(subject)).encode(
        "utf-8")
    digest = hmac.new(_secret(), material, hashlib.sha256).hexdigest()[:24]
    return "%s:%s" % (kind, digest)


@dataclass(frozen=True)
class RosterAction:
    key: str
    kind: str
    student_id: int | None
    email: str | None
    label: str
    before: str
    after: str
    default_selected: bool
    expected_name: str | None = None
    proposed_name: str | None = None
    expected_status: str | None = None
    current_grades: tuple[int, ...] = ()
    proposed_grades: tuple[int, ...] = ()
    warning: str = ""

    def public(self):
        return {
            "key": self.key,
            "kind": self.kind,
            "student_id": self.student_id,
            "label": self.label,
            "email": _mask_email(self.email) if self.email else "",
            "before": self.before,
            "after": self.after,
            "default_selected": self.default_selected,
            "requires_explicit_confirmation": self.kind in (
                ARCHIVE, REACTIVATE, GRADE),
            "warning": self.warning,
        }

    def fingerprint(self):
        return {
            "key": self.key,
            "kind": self.kind,
            "student_id": self.student_id,
            "email": self.email,
            "label": self.label,
            "before": self.before,
            "after": self.after,
            "default_selected": self.default_selected,
            "expected_name": self.expected_name,
            "proposed_name": self.proposed_name,
            "expected_status": self.expected_status,
            "current_grades": list(self.current_grades),
            "proposed_grades": list(self.proposed_grades),
            "warning": self.warning,
        }


@dataclass(frozen=True)
class RosterPlan:
    report_month: str
    actions: tuple[RosterAction, ...]
    unchanged: int
    blockers: tuple[str, ...]
    notices: tuple[str, ...]
    source_hashes: tuple[tuple[str, str], ...]
    plan_id: str | None

    @property
    def apply_allowed(self):
        return not self.blockers and bool(self.plan_id)

    @property
    def summary(self):
        counts = Counter(action.kind for action in self.actions)
        return {
            "unchanged": self.unchanged,
            "name_changes": counts[NAME],
            "grade_suggestions": counts[GRADE],
            "new_students": counts[ADD],
            "missing_students": counts[ARCHIVE],
            "reactivations": counts[REACTIVATE],
            "possible_duplicates": sum(
                1 for action in self.actions if action.warning),
            "blockers": len(self.blockers),
        }

    def public(self):
        return {
            "report_month": self.report_month,
            "plan_id": self.plan_id,
            "apply_allowed": self.apply_allowed,
            "summary": self.summary,
            "actions": [action.public() for action in self.actions],
            "blockers": list(self.blockers),
            "notices": list(self.notices),
        }


def _plan_id(report_month, actions, unchanged, blockers, notices,
             source_hashes):
    if blockers:
        return None
    scope = hashlib.sha256(
        config.turso_database_url().encode("utf-8")).hexdigest()
    material = {
        "version": PLAN_VERSION,
        "database_scope": scope,
        "report_month": report_month,
        "actions": [action.fingerprint() for action in actions],
        "unchanged": unchanged,
        "blockers": list(blockers),
        "notices": list(notices),
        "source_hashes": list(source_hashes),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return PLAN_VERSION + "-" + hmac.new(
        _secret(), encoded, hashlib.sha256).hexdigest()


def _union_rows(parsed_files):
    """Vrati kanonski red po e-mailu i blokere za neodređen izvor."""
    by_email = defaultdict(list)
    blockers = []
    for course_key in REQUIRED_COURSES:
        parsed = parsed_files[course_key]
        seen_in_file = set()
        for row in parsed.rows:
            email = student_identity.normalize_email(row.email)
            if email is None:
                # Parser odbija praznu adresu, a ova provjera zatvara ostatak.
                blockers.append("Jedan red nema upotrebljivu e-mail adresu.")
                continue
            if email in seen_in_file:
                blockers.append(
                    "%s sadrži ponovljenu e-mail adresu %s."
                    % (parsed.course_name, _mask_email(email)))
            seen_in_file.add(email)
            by_email[email].append((course_key, row.display_name))

    roster = {}
    for email, entries in by_email.items():
        names = {name for _course, name in entries if name}
        if len(names) > 1:
            blockers.append(
                "Za %s četiri izvoza daju različita imena."
                % _mask_email(email))
        roster[email] = sorted(names)[-1] if names else None
    return roster, blockers


def build_plan(parsed_files, database):
    """Četiri validirana izvoza + trenutno stanje baze → potpisan pregled."""
    if set(parsed_files) != set(REQUIRED_COURSES):
        raise RosterPlanError("all_four_courses_required")
    months = {parsed.report_month for parsed in parsed_files.values()}
    if len(months) != 1:
        raise RosterPlanError("report_month_mismatch")
    report_month = months.pop()
    source_hashes = tuple(
        (key, parsed_files[key].source_sha256) for key in REQUIRED_COURSES)
    roster, source_blockers = _union_rows(parsed_files)
    state = database.fetch_roster_state()

    students = {row["student_id"]: row for row in state["students"]}
    email_to_ids = defaultdict(set)
    blockers = list(source_blockers)
    notices = []
    for row in state["accounts"]:
        normalized = student_identity.normalize_email(row["external_user_id"])
        if normalized is None:
            notices.append(
                "Povezani Thinkific nalog učenika #%d nema kanonsku adresu."
                % row["student_id"])
            continue
        email_to_ids[normalized].add(row["student_id"])
    for email, ids in email_to_ids.items():
        if len(ids) > 1:
            blockers.append(
                "E-mail %s je povezan sa više MAT-BOT učenika."
                % _mask_email(email))

    name_to_ids = defaultdict(set)
    for student in students.values():
        key = _name_key(student["display_name"])
        if key:
            name_to_ids[key].add(student["student_id"])
    for key, ids in name_to_ids.items():
        if len(ids) > 1:
            label = next(students[student_id]["display_name"]
                         for student_id in sorted(ids))
            notices.append(
                "Mogući duplikat imena %s na MAT-BOT zapisima %s; "
                "nije izvršeno automatsko spajanje."
                % (label, ", ".join("#%d" % student_id
                                    for student_id in sorted(ids))))

    actions = []
    present_ids = set()
    unchanged = 0
    for email in sorted(roster):
        proposed_name = roster[email]
        matched = sorted(email_to_ids.get(email, ()))
        if len(matched) > 1:
            continue
        if not matched:
            warning = ""
            same_name = name_to_ids.get(_name_key(proposed_name), set())
            if same_name:
                warning = (
                    "Isto ime već postoji na drugom MAT-BOT zapisu. "
                    "Neće biti automatski spojeno.")
            label = proposed_name or "Novi učenik"
            actions.append(RosterAction(
                key=_subject_key(ADD, email), kind=ADD, student_id=None,
                email=email, label=label, before="Nije u MAT-BOT-u",
                after="Dodaj učenika", default_selected=True,
                proposed_name=proposed_name, warning=warning))
            # Novi učenik smije dobiti razred samo kao zasebnu, neoznačenu
            # potvrdu osoblja. Dodavanje samo po sebi ostavlja razred prazan.
            suggested = student_grades.name_grade_hint(proposed_name)
            if suggested is not None:
                actions.append(RosterAction(
                    key=_subject_key(GRADE, "new:" + email), kind=GRADE,
                    student_id=None, email=email, label=label,
                    before="Nepotvrđen", after="%d. razred" % suggested,
                    default_selected=False, proposed_name=proposed_name,
                    current_grades=(), proposed_grades=(suggested,)))
            continue

        student_id = matched[0]
        present_ids.add(student_id)
        student = students.get(student_id)
        if student is None:
            blockers.append(
                "Thinkific nalog pokazuje na nepostojećeg MAT-BOT učenika.")
            continue
        label = proposed_name or student["display_name"] or (
            "Učenik #%d" % student_id)
        changed = False
        existing_name = student["display_name"]
        if proposed_name and proposed_name != existing_name:
            actions.append(RosterAction(
                key=_subject_key(NAME, student_id), kind=NAME,
                student_id=student_id, email=email, label=label,
                before=existing_name or "Bez imena", after=proposed_name,
                default_selected=True, expected_name=existing_name,
                proposed_name=proposed_name))
            changed = True

        status = _status(student["status"])
        if status in _INACTIVE:
            actions.append(RosterAction(
                key=_subject_key(REACTIVATE, student_id), kind=REACTIVATE,
                student_id=student_id, email=email, label=label,
                before=status, after="active", default_selected=False,
                expected_status=status))
            changed = True

        suggested = student_grades.name_grade_hint(
            proposed_name or existing_name)
        current = tuple(student.get("grades") or ())
        if suggested is not None and current != (suggested,):
            actions.append(RosterAction(
                key=_subject_key(GRADE, student_id), kind=GRADE,
                student_id=student_id, email=email, label=label,
                before=_grade_label(current),
                after="%d. razred" % suggested,
                default_selected=False, current_grades=current,
                proposed_grades=(suggested,)))
            changed = True
        if not changed:
            unchanged += 1

    for student_id, student in sorted(students.items()):
        if student_id in present_ids:
            continue
        if student.get("account_type") != reporting_db.ACCOUNT_TYPE_STUDENT:
            continue
        status = _status(student["status"])
        if status in _INACTIVE:
            continue
        label = student["display_name"] or "Učenik #%d" % student_id
        actions.append(RosterAction(
            key=_subject_key(ARCHIVE, student_id), kind=ARCHIVE,
            student_id=student_id, email=None, label=label,
            before=status, after="archived", default_selected=False,
            expected_name=student["display_name"], expected_status=status,
            current_grades=tuple(student.get("grades") or ())))

    order = {NAME: 0, ADD: 1, GRADE: 2, REACTIVATE: 3, ARCHIVE: 4}
    actions.sort(key=lambda item: (order[item.kind], item.label.casefold(),
                                   item.student_id or 0, item.key))
    blockers = tuple(dict.fromkeys(blockers))
    notices = tuple(dict.fromkeys(notices))
    actions = tuple(actions)
    plan_id = _plan_id(report_month, actions, unchanged, blockers, notices,
                       source_hashes)
    return RosterPlan(report_month, actions, unchanged, blockers, notices,
                      source_hashes, plan_id)


def selected_actions(plan, keys):
    """Klijentski izbor pretvori u pregledane akcije; nepoznato pada zatvoreno."""
    requested = list(dict.fromkeys(str(key) for key in (keys or ())))
    available = {action.key: action for action in plan.actions}
    if any(key not in available for key in requested):
        raise RosterPlanError("unknown_action")
    chosen = [available[key] for key in requested]
    selected_adds = {action.email for action in chosen if action.kind == ADD}
    if any(action.kind == GRADE and action.student_id is None
           and action.email not in selected_adds for action in chosen):
        raise RosterPlanError("new_grade_without_add")
    return chosen
