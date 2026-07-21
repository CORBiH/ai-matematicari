# -*- coding: utf-8 -*-
"""The ONE place student-visible mathematics is turned into LaTeX.

The frontend already runs MathJax 3 (``templates/index.html``) configured with
``inlineMath: [["$","$"], ["\\\\(","\\\\)"]]``; display math keeps the MathJax
defaults ``\\[...\\]`` and ``$$...$$``. This module reuses those delimiters — it
does not introduce a second rendering system.

Two hard rules:

* It formats only text the ENGINE produced (task questions, hints, worked
  solutions, feedback). Raw student messages, expected answers, normalized
  values, ``deterministic_check`` and every Sheets audit field stay plain text.
* It converts recognised mathematical TOKENS, never arbitrary prose. A date, a
  URL or a stray slash cannot become a fraction (see ``_is_math_slash``).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

INLINE_OPEN, INLINE_CLOSE = r"\(", r"\)"
BLOCK_OPEN, BLOCK_CLOSE = r"\[", r"\]"

#: "1 2/15" — a mixed number must be matched before a bare fraction.
_MIXED_RE = re.compile(r"(?<![\d/])(\d+)\s+(\d+)\s*/\s*(\d+)(?![\d/])")
#: "5/6" standing alone: not part of a/b/c, a date, or a version string.
_FRACTION_RE = re.compile(r"(?<![\d/\w])(\d+)\s*/\s*(\d+)(?![\d/])")
#: Superscript digits the templates may emit (2² → 2^{2}).
_SUPERSCRIPTS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                 "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9"}
_SUPERSCRIPT_RE = re.compile("([⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
#: Multiplication signs used by the templates.
_TIMES_RE = re.compile(r"\s*[×·⋅]\s*")
#: Already-formatted math — never double-escape it.
_HAS_LATEX_RE = re.compile(r"\\frac|\\cdot|\\\(|\\\[|\\begin")


def _is_math_slash(text: str, start: int, end: int, numerator: str,
                   denominator: str) -> bool:
    """False for a URL, a date or a version — never turn those into fractions."""
    before = text[max(0, start - 10):start].lower()
    if "http" in before or "www." in before:
        return False
    # A leading zero means a date or a time ("12/05", "01/02"), not a fraction.
    if len(numerator) > 1 and numerator.startswith("0"):
        return False
    if len(denominator) > 1 and denominator.startswith("0"):
        return False
    if int(denominator or 0) == 0:
        return False
    return True


def to_latex(expr: Any) -> str:
    """A mathematical EXPRESSION as LaTeX body (no delimiters).

    ``5/6 + 4/9`` → ``\\frac{5}{6}+\\frac{4}{9}``;
    ``1 2/15`` → ``1\\frac{2}{15}``; ``2x + 3 = 11`` → ``2x+3=11``;
    ``60 = 2 × 2 × 3 × 5`` → ``60=2\\cdot2\\cdot3\\cdot5``; ``2²`` → ``2^{2}``.
    """
    text = str(expr or "").strip()
    if not text:
        return ""
    if _HAS_LATEX_RE.search(text):
        return text                       # already formatted; leave it alone
    text = _MIXED_RE.sub(lambda m: f"{m.group(1)}\\frac{{{m.group(2)}}}{{{m.group(3)}}}",
                         text)
    text = _FRACTION_RE.sub(lambda m: f"\\frac{{{m.group(1)}}}{{{m.group(2)}}}", text)
    text = _SUPERSCRIPT_RE.sub(
        lambda m: "^{" + "".join(_SUPERSCRIPTS.get(c, c) for c in m.group(1)) + "}",
        text)
    # "\cdot2" is valid LaTeX (a control word ends at a non-letter) and matches
    # the compact form the spec asks for.
    text = _TIMES_RE.sub(r"\\cdot", text)
    # Tighten spacing around operators; LaTeX adds its own.
    text = re.sub(r"\s*([+\-=])\s*", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def inline(expr: Any) -> str:
    """``\\( … \\)`` — for a short expression inside a sentence."""
    body = to_latex(expr)
    return f"{INLINE_OPEN} {body} {INLINE_CLOSE}" if body else ""


def block(steps: Iterable[Any]) -> str:
    """``\\[\\begin{aligned} … \\end{aligned}\\]`` on ONE physical line.

    One line matters: the frontend renderer joins lines with ``<br>``, and a
    ``<br>`` inside display math would break MathJax's scan.
    """
    rows = [to_latex(s) for s in steps if str(s or "").strip()]
    if not rows:
        return ""
    if len(rows) == 1:
        return f"{BLOCK_OPEN} {rows[0]} {BLOCK_CLOSE}"
    # Standard aligned shape — "head &= r1 \\ &= r2 \\ &= r3" — so every step
    # lines up on the equals sign.
    head = rows[0]
    rest = [r.lstrip("=").strip() for r in rows[1:]]
    body = f"{head} &= " + r" \\ &= ".join(rest)
    return (f"{BLOCK_OPEN}\\begin{{aligned}} {body} "
            f"\\end{{aligned}}{BLOCK_CLOSE}")


def format_math_tokens(text: Any) -> str:
    """Wrap standalone fraction / mixed-number tokens of ENGINE text in inline math.

    Used for sentences that mix prose and mathematics ("Proširi 1/4 na nazivnik
    20."). Only recognised numeric tokens are touched, so ordinary prose — and
    anything already containing LaTeX — passes through unchanged.
    """
    raw = str(text or "")
    if not raw or _HAS_LATEX_RE.search(raw):
        return raw

    def _mixed(match: re.Match) -> str:
        return (f"{INLINE_OPEN} {match.group(1)}"
                f"\\frac{{{match.group(2)}}}{{{match.group(3)}}} {INLINE_CLOSE}")

    def _fraction(match: re.Match) -> str:
        if not _is_math_slash(raw, match.start(), match.end(),
                              match.group(1), match.group(2)):
            return match.group(0)
        return (f"{INLINE_OPEN} \\frac{{{match.group(1)}}}"
                f"{{{match.group(2)}}} {INLINE_CLOSE}")

    out = _MIXED_RE.sub(_mixed, raw)
    return _FRACTION_RE.sub(_fraction, out)


#: "Izračunaj: <expr>." / "Riješi jednačinu: <expr>." — the part after the colon
#: is a pure expression, so it is formatted as one inline block.
_LABELLED_RE = re.compile(r"^(.*?:\s*)(.+?)(\s*\.?)$", re.DOTALL)
_EXPRESSION_LABELS = ("izracunaj", "rijesi jednacinu", "izracunajte")


def format_question(question: Any) -> str:
    """A task question, rendered for the student.

    A labelled expression ("Izračunaj: 5/6 + 4/9.") becomes one inline formula;
    everything else has its numeric tokens wrapped individually so the prose
    around them is untouched.
    """
    text = str(question or "").strip()
    if not text or _HAS_LATEX_RE.search(text):
        return text
    match = _LABELLED_RE.match(text)
    if match:
        import unicodedata
        label = match.group(1)
        folded = "".join(
            c for c in unicodedata.normalize("NFKD", label.lower())
            if not unicodedata.combining(c)).strip(" :")
        if folded in _EXPRESSION_LABELS:
            return f"{label}{inline(match.group(2))}{match.group(3)}"
    return format_math_tokens(text)
