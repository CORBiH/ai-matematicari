"""Deterministic sanity checks for image-derived result lists.

The tutor may ask the model for results from an uploaded image. This module
checks any OCR task text we can parse, corrects deterministic mismatches before
display, and records enough context so later explanations do not silently
contradict the earlier result list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from matbot.answer_checker import Expected, derive_expected, parse_number_token, split_numbered_items
from matbot.content_loader import normalize_value


@dataclass
class _TaskItem:
    label: str
    number: int | None
    task: str


@dataclass
class _AnswerItem:
    label: str
    number: int | None
    text: str
    line_index: int | None


_NUM_MARKER_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?(\d{1,2})[.)]\s*(?:([a-zA-Zčć])\)\s*)?(.+?)\s*$"
)
_LETTER_MARKER_RE = re.compile(r"^\s*(?:[-*•]\s*)?([a-zA-Zčć])\)\s*(.+?)\s*$")
_TIME_RE = re.compile(r"\b(-?\d+(?:[,.]\d+)?)\s*(h|sat(?:i|a|om)?|min(?:uta|ute|ut)?)\b")
_DIST_RE = re.compile(r"\b(-?\d+(?:[,.]\d+)?)\s*(km|kilomet(?:ar|ra|ara)?|m|met(?:ar|ra|ara)?)\b")
_SPEED_RE = re.compile(
    r"\b(-?\d+(?:[,.]\d+)?)\s*(?:km\s*/\s*h|km\s*/\s*sat|kmh|kilomet(?:ara|ra)?\s+na\s+sat)\b"
)


def _num(raw: str) -> Fraction:
    return Fraction(raw.replace(",", "."))


def _fmt_fraction(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _fmt_expected(expected: Expected) -> str:
    base = _fmt_fraction(expected.value)
    return f"{base} {expected.unit}".strip() if expected.unit else base


def _display_label(label: str) -> str:
    if "." in label:
        n, sub = label.split(".", 1)
        return f"{n}. {sub})"
    if label.isdigit():
        return f"{label}."
    return f"{label})"


def _normalize_label(num: str | None, sub: str | None, fallback: int) -> tuple[str, int | None]:
    if num:
        n = int(num)
        s = (sub or "").lower()
        return (f"{n}.{s}" if s else str(n), n)
    if sub:
        return sub.lower(), None
    return str(fallback), fallback


def _append_continuation(items: list[_TaskItem], line: str) -> None:
    if items:
        items[-1].task = (items[-1].task + " " + line.strip()).strip()


def extract_image_tasks(ocr_text: str) -> list[dict[str, Any]]:
    """Public-ish helper for tests/debugging: OCR text -> task item dicts."""
    return [item.__dict__ for item in _extract_task_items(ocr_text)]


def _extract_task_items(ocr_text: str) -> list[_TaskItem]:
    text = normalize_value(ocr_text).replace("\r\n", "\n").replace("\r", "\n")
    numbered = split_numbered_items(text)
    if numbered:
        return [_TaskItem(label=str(n), number=n, task=t.strip()) for n, t in numbered]

    items: list[_TaskItem] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _NUM_MARKER_RE.match(line)
        if m:
            label, number = _normalize_label(m.group(1), m.group(2), len(items) + 1)
            items.append(_TaskItem(label=label, number=number, task=m.group(3).strip()))
            continue
        m = _LETTER_MARKER_RE.match(line)
        if m:
            label, number = _normalize_label(None, m.group(1), len(items) + 1)
            items.append(_TaskItem(label=label, number=number, task=m.group(2).strip()))
            continue
        if items:
            _append_continuation(items, line)

    if not items and derive_expected(text):
        items.append(_TaskItem(label="1", number=1, task=text))
    return items


def _extract_answer_items(answer: str) -> list[_AnswerItem]:
    text = normalize_value(answer).replace("\r\n", "\n").replace("\r", "\n")
    items: list[_AnswerItem] = []
    for idx, raw_line in enumerate(text.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        m = _NUM_MARKER_RE.match(line)
        if m:
            label, number = _normalize_label(m.group(1), m.group(2), len(items) + 1)
            items.append(_AnswerItem(label=label, number=number, text=m.group(3).strip(), line_index=idx))
            continue
        m = _LETTER_MARKER_RE.match(line)
        if m:
            label, number = _normalize_label(None, m.group(1), len(items) + 1)
            items.append(_AnswerItem(label=label, number=number, text=m.group(2).strip(), line_index=idx))
            continue
        if len(text.splitlines()) == 1:
            items.append(_AnswerItem(label="1", number=1, text=line, line_index=idx))
    return items


def _find_answer(task: _TaskItem, idx: int, answers: list[_AnswerItem]) -> _AnswerItem | None:
    by_label = {a.label: a for a in answers}
    if task.label in by_label:
        return by_label[task.label]
    if task.number is not None:
        for a in answers:
            if a.number == task.number:
                return a
    return answers[idx] if idx < len(answers) else None


def _time_to_minutes(value: Fraction, unit: str) -> Fraction:
    return value * 60 if unit.startswith("sat") or unit == "h" else value


def _value_for_expected(answer_text: str, expected: Expected) -> tuple[Fraction | None, str | None]:
    if expected.unit in ("sata", "minuta"):
        for m in _TIME_RE.finditer(answer_text):
            minutes = _time_to_minutes(_num(m.group(1)), m.group(2))
            value = minutes / 60 if expected.unit == "sata" else minutes
            return value, m.group(0)
    if expected.unit == "km":
        for m in _DIST_RE.finditer(answer_text):
            value = _num(m.group(1)) / 1000 if m.group(2).startswith("m") and m.group(2) != "km" else _num(m.group(1))
            return value, m.group(0)
    if expected.unit == "km/h":
        for m in _SPEED_RE.finditer(answer_text):
            return _num(m.group(1)), m.group(0)
    token = parse_number_token(answer_text)
    if token is not None:
        return token.value, token.raw
    return None, None


def _verification_entry(
    task: _TaskItem,
    answer_item: _AnswerItem | None,
    expected: Expected | None,
) -> dict[str, Any]:
    base = {
        "label": task.label,
        "display_label": _display_label(task.label),
        "task": task.task[:500],
        "status": "unverified",
        "expected": _fmt_expected(expected) if expected else None,
        "given": None,
        "basis": expected.basis if expected else "",
        "line_index": answer_item.line_index if answer_item else None,
    }
    if expected is None:
        return base
    if answer_item is None:
        base["status"] = "missing_answer"
        return base
    value, raw = _value_for_expected(answer_item.text, expected)
    base["given"] = raw
    if value is None:
        base["status"] = "unverified_answer"
    elif value == expected.value:
        base["status"] = "verified"
    else:
        base["status"] = "corrected"
    return base


def _verify(ocr_text: str, answer: str) -> dict[str, Any] | None:
    tasks = _extract_task_items(ocr_text)
    if not tasks:
        return None
    answers = _extract_answer_items(answer)
    entries = []
    for idx, task in enumerate(tasks):
        expected = derive_expected(task.task)
        entries.append(_verification_entry(task, _find_answer(task, idx, answers), expected))
    if not entries:
        return None
    return {"items": entries}


def _apply_corrections(answer: str, verification: dict[str, Any]) -> str:
    corrected = [i for i in verification.get("items", []) if i.get("status") == "corrected"]
    if not corrected:
        return answer
    lines = answer.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    appended: list[str] = []
    for item in corrected:
        line = f"{item['display_label']} {_fmt_expected_text(item)}"
        idx = item.get("line_index")
        if isinstance(idx, int) and 0 <= idx < len(lines):
            lines[idx] = line
        else:
            appended.append(line)
    out = "\n".join(lines).strip()
    if appended:
        out = (out + "\n\nProvjereni rezultati:\n" + "\n".join(appended)).strip()
    return out


def _fmt_expected_text(item: dict[str, Any]) -> str:
    return normalize_value(item.get("expected"))


def verify_image_result_answer(ocr_text: str, answer: str) -> tuple[str, dict[str, Any] | None]:
    """Return possibly corrected answer plus per-item verification metadata."""
    ocr = normalize_value(ocr_text)
    if not ocr or not normalize_value(answer):
        return answer, None
    verification = _verify(ocr, answer)
    if not verification:
        return answer, None
    return _apply_corrections(answer, verification), verification


def format_image_verification_for_context(verification: dict[str, Any] | None) -> str:
    if not verification:
        return ""
    lines = ["PROVJERA REZULTATA SLIKE:"]
    for item in verification.get("items", []):
        status = item.get("status")
        if status == "verified":
            lines.append(
                f"- {item['display_label']} VERIFIKOVANO: {item['expected']}. "
                f"Osnova: {item.get('basis') or 'deterministički račun'}."
            )
        elif status == "corrected":
            lines.append(
                f"- {item['display_label']} ISPRAVLJENO: ranije/model je dao {item.get('given') or 'nejasan odgovor'}, "
                f"tačno je {item['expected']}. Osnova: {item.get('basis') or 'deterministički račun'}."
            )
        elif status in ("missing_answer", "unverified_answer"):
            lines.append(
                f"- {item['display_label']} NEPOTVRĐENO: zadatak je parsiran, ali odgovor nije pouzdano pročitan. "
                "Ne izmišljaj rezultat."
            )
        else:
            lines.append(
                f"- {item['display_label']} NEPOTVRĐENO: nema sigurnog determinističkog računa. "
                "Ako slika nije jasna, traži pojašnjenje."
            )
    return "\n".join(lines)


def _extract_context_section(context: str, heading: str) -> str:
    pattern = re.compile(rf"{heading}:\s*\n(?P<body>.*?)(?=\n\n[A-ZČĆŠŽĐA-Z ()]+:|\Z)", re.DOTALL)
    m = pattern.search(context)
    return normalize_value(m.group("body")) if m else ""


def augment_saved_image_context(context: str) -> str:
    """Append a deterministic re-check block to old/new saved image context."""
    ctx = normalize_value(context)
    if not ctx or "PROVJERA SAČUVANOG KONTEKSTA" in ctx:
        return ctx
    ocr = (
        _extract_context_section(ctx, r"TEKST SA SLIKE \(OCR\)")
        or _extract_context_section(ctx, r"TEKST ZADATKA SA SLIKE \(OCR\)")
    )
    answer = _extract_context_section(ctx, r"ODGOVOR TUTORA NA SLIKU")
    if not ocr or not answer:
        return ctx
    _corrected, verification = verify_image_result_answer(ocr, answer)
    if not verification:
        return ctx
    lines = ["PROVJERA SAČUVANOG KONTEKSTA:"]
    for item in verification.get("items", []):
        if item.get("status") == "corrected":
            lines.append(
                f"- {item['display_label']} RANIJI ODGOVOR JE POGREŠAN: napisano je "
                f"{item.get('given') or 'nejasno'}, tačno je {item['expected']}. "
                f"U follow-upu počni jasnom ispravkom: \"Ranije sam pogrešno napisao "
                f"{item.get('given') or 'taj rezultat'}. Tačno je {item['expected']}, jer "
                f"{item.get('basis') or 'račun daje taj rezultat'}.\""
            )
        elif item.get("status") == "verified":
            lines.append(
                f"- {item['display_label']} raniji odgovor je potvrđen: {item['expected']}. "
                f"Osnova: {item.get('basis') or 'deterministički račun'}."
            )
    return ctx + "\n\n" + "\n".join(lines)


def correction_preface_from_context(context: str) -> str:
    """Student-facing correction sentence from an augmented saved context."""
    ctx = normalize_value(context)
    if "RANIJI ODGOVOR JE POGREŠAN" not in ctx:
        return ""
    m = re.search(
        r"RANIJI ODGOVOR JE POGREŠAN:\s+napisano je (?P<given>.*?),\s+"
        r"tačno je (?P<expected>.*?)\.\s+.*?jer (?P<basis>.*?)\.\"",
        ctx,
        re.DOTALL,
    )
    if not m:
        return ""
    given = normalize_value(m.group("given"))
    expected = normalize_value(m.group("expected"))
    basis = normalize_value(m.group("basis"))
    if not given or not expected:
        return ""
    return f"Ranije sam pogrešno napisao {given}. Tačno je {expected}, jer {basis}."
