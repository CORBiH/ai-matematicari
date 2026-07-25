# -*- coding: utf-8 -*-
"""V3's ONE student-facing math rendering boundary — used only by ``adapter``.

The frontend (``templates/index.html``) runs MathJax 3 configured with
``inlineMath: [["$","$"], ["\\\\(","\\\\)"]]``; display math keeps the MathJax
defaults ``\\[...\\]`` / ``$$...$$``. This module targets those SAME delimiters
— it is not a second rendering system, and it reuses the same contract
``matbot/minimal/mathfmt.py`` already relies on for the legacy/Minimal engine.

The V3 Math Notation Policy instructs the model to write LaTeX directly in its
answer text (``\\frac{1}{2}``, ``\\sqrt{2}``, …). The live bug this module
fixes: the model sometimes emits that LaTeX WITHOUT the delimiters MathJax's
inline scanner looks for, so the browser prints the raw command literally.
``normalize_math_for_display`` repairs that deterministically — it never makes
a model call.

Two hard rules, mirroring ``mathfmt.py``:

* Text already inside a supported delimiter (``\\( … \\)``, ``\\[ … \\]``,
  ``$ … $``, ``$$ … $$``) is passed through completely untouched — never
  re-scanned, never double-wrapped.
* Only text containing an actual LaTeX marker (a ``\\command``, or a bare
  ``^{`` / ``_{`` script group) is touched at all. Ordinary Bosnian prose,
  decimal commas, and the ``·`` multiplication sign the model already emits
  are never treated as mathematics.
"""
from __future__ import annotations

import re
from typing import Any

INLINE_OPEN, INLINE_CLOSE = r"\(", r"\)"

#: Text already inside one of the frontend's four supported math delimiters —
#: matched non-greedily so adjacent formulas are matched as separate spans,
#: and passed through completely untouched (this is what makes the function
#: idempotent and non-double-wrapping).
_PROTECTED_RE = re.compile(
    r"\\\(.*?\\\)|\\\[.*?\\\]|\$\$.*?\$\$|\$[^$\n]*?\$", re.DOTALL)

#: LaTeX control words the V3 Math Notation Policy actually asks the model to
#: use. Deliberately a closed, generic (not lesson-specific) set — every
#: mathematical area (fractions, roots, powers, equations, sets, geometry)
#: expresses itself through this same small vocabulary of commands.
_LATEX_COMMANDS = (
    r"frac|sqrt|cdot|times|div|pm|mp|leq|geq|neq|approx|equiv|infty|"
    r"sum|prod|int|left|right|begin|end|text|cdots|ldots|dots|"
    r"overline|underline|hat|vec|alpha|beta|gamma|delta|pi|theta|"
    r"circ|angle|triangle|perp|parallel|in|subset|cup|cap|emptyset"
)

#: A maximal run of touching (no whitespace gap) "math-shaped" characters: a
#: LaTeX command with its brace/bracket groups, a bare brace group (a
#: superscript/subscript body), digits, or a bare operator. Concatenated with
#: NO separator between alternatives, so a run naturally stops at whitespace
#: or at an ordinary word — that boundary is what keeps prose untouched.
_RUN_RE = re.compile(
    r"(?:"
    r"\\(?:" + _LATEX_COMMANDS + r")(?:\s*\{[^{}]*\}|\s*\[[^\[\]]*\])*"
    r"|\{[^{}]*\}"
    r"|[0-9]+"
    r"|[+\-=^_]"
    r")+"
)


def _needs_wrapping(run: str) -> bool:
    return "\\" in run or "^{" in run or "_{" in run


def _wrap_unprotected(segment: str) -> str:
    if not segment:
        return segment
    if "\\" not in segment and "^{" not in segment and "_{" not in segment:
        return segment
    out: list[str] = []
    pos = 0
    for m in _RUN_RE.finditer(segment):
        run = m.group(0)
        if not _needs_wrapping(run):
            continue
        out.append(segment[pos:m.start()])
        out.append(f"{INLINE_OPEN} {run} {INLINE_CLOSE}")
        pos = m.end()
    out.append(segment[pos:])
    return "".join(out)


def normalize_math_for_display(text: Any) -> str:
    """Wrap bare, undelimited LaTeX in ``text`` so MathJax actually renders it.

    Deterministic and idempotent: running this twice on its own output is a
    no-op, since the first pass's ``\\( … \\)`` spans are recognised as
    already-protected on the second pass. Safe to call on any V3
    student-facing string — a plain Bosnian sentence with no LaTeX marker in
    it is returned completely unchanged (same object identity is not
    guaranteed, but the text is byte-identical).
    """
    raw = str(text or "")
    if not raw or "\\" not in raw and "^{" not in raw and "_{" not in raw:
        return raw

    out: list[str] = []
    pos = 0
    for m in _PROTECTED_RE.finditer(raw):
        out.append(_wrap_unprotected(raw[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_wrap_unprotected(raw[pos:]))
    return "".join(out)
