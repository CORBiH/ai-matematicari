# -*- coding: utf-8 -*-
"""V3's student-facing math rendering boundary.

Live bug: the model writes LaTeX directly in its answer text (per the Math
Notation Policy) but sometimes without the delimiters MathJax's inline scanner
(``inlineMath: [["$","$"], ["\\(","\\)"]]``, see templates/index.html) looks
for, so the browser prints e.g. ``\\frac{3}{4}`` literally instead of a
rendered fraction. ``normalize_math_for_display`` repairs that deterministically,
at the adapter boundary, with no model call.
"""
from __future__ import annotations

from matbot.ai_tutor_v3.rendering import normalize_math_for_display


def test_bare_frac_gets_wrapped_in_supported_delimiters():
    out = normalize_math_for_display(r"Proširi razlomak \frac{3}{4} brojem 2.")
    assert r"\( \frac{3}{4} \)" in out


def test_bare_sqrt_gets_wrapped():
    out = normalize_math_for_display(r"Izračunaj \sqrt{16}.")
    assert r"\( \sqrt{16} \)" in out


def test_already_delimited_inline_math_is_not_touched():
    text = r"Proširi razlomak \( \frac{3}{4} \) brojem 2."
    assert normalize_math_for_display(text) == text


def test_already_delimited_math_is_not_double_wrapped():
    out = normalize_math_for_display(r"\( \frac{1}{2} \)")
    assert out.count(r"\(") == 1
    assert out.count(r"\)") == 1


def test_display_math_block_is_preserved_untouched():
    text = r"\[ \frac{1}{2} + \frac{1}{3} = \frac{5}{6} \]"
    assert normalize_math_for_display(text) == text


def test_dollar_delimited_inline_math_is_preserved():
    text = r"Rezultat je $ \frac{1}{2} $ tačno."
    assert normalize_math_for_display(text) == text


def test_ordinary_bosnian_text_is_untouched():
    text = "Tačno! Odličan posao, nastavi ovako."
    assert normalize_math_for_display(text) == text


def test_decimal_comma_is_preserved():
    text = "Rezultat je 3,5 kilograma."
    assert normalize_math_for_display(text) == text


def test_multiplication_dot_is_preserved_as_is():
    text = "60 = 2 · 2 · 3 · 5"
    assert normalize_math_for_display(text) == text


def test_rendering_is_idempotent():
    text = r"Proširi \frac{3}{4} pa izračunaj \sqrt{9}."
    once = normalize_math_for_display(text)
    twice = normalize_math_for_display(once)
    assert once == twice


def test_multiple_separate_formulas_each_get_wrapped_once():
    text = r"\frac{1}{2} i \frac{1}{3} nisu isti razlomci."
    out = normalize_math_for_display(text)
    assert out.count(r"\(") == 2
    assert out.count(r"\)") == 2


def test_empty_and_none_text_are_handled_safely():
    assert normalize_math_for_display("") == ""
    assert normalize_math_for_display(None) == ""


def test_multi_command_expression_wrapped_as_one_span():
    out = normalize_math_for_display(r"Izračunaj: 2\cdot3\cdot5 = 30.")
    # one contiguous run (no whitespace break) wraps as a single formula
    assert r"\( 2\cdot3\cdot5=30 \)" in out or r"\( 2\cdot3\cdot5 \)" in out


def test_preserves_backslash_for_json_and_sse_serialization():
    """The renderer operates on the Python string BEFORE json.dumps/SSE
    encoding — it must not do anything that would corrupt that later escaping
    (e.g. emit a lone unescaped control character)."""
    import json
    out = normalize_math_for_display(r"\frac{1}{2}")
    encoded = json.dumps({"answer": out}, ensure_ascii=False)
    decoded = json.loads(encoded)
    assert decoded["answer"] == out
    assert r"\frac{1}{2}" in out
