# -*- coding: utf-8 -*-
"""Regression: the fraction_mul quality guard was disabled by control bytes.

``matbot/task_templates.py`` (line 310) was committed as

    if re.search(r"<BS>1\\s*/\\s*1<BS>", question):

where ``<BS>`` is a literal ASCII backspace (0x08) — a heredoc had eaten the
``\\b`` word boundaries. The pattern then matched nothing, so multiplication
tasks such as "3/4 · 1/1" were never rejected even though multiplying by 1
tests nothing.

The word boundaries are load-bearing in BOTH directions: a bare "1/1" pattern
would also match inside 1/10, 11/12 and 21/15, so simply deleting them would
trade a silent miss for a silent false rejection.

Provenance only: introduced by commit 53a4dff. History is not rewritten.
"""
import io
import pathlib
import re

import pytest

from matbot.task_templates import quality_ok

SOURCE = pathlib.Path("matbot/task_templates.py")


# --------------------------------------------------------------------------- #
# The guard actually fires                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question", [
    "Izračunaj: 3/4 · 1/1.",
    "Izračunaj: 1/1 · 3/4.",
    "Izračunaj: 1 / 1 · 3/4.",      # tolerated spacing
    "Izračunaj: 2/5 · 1/1.",
])
def test_multiplication_by_one_over_one_is_rejected(question):
    assert quality_ok("fraction_mul", question, "3/4") is False


# --------------------------------------------------------------------------- #
# ...and does not fire on ordinary fractions that merely CONTAIN "1/1"         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question", [
    "Izračunaj: 1/10 · 3/4.",
    "Izračunaj: 11/12 · 3/4.",
    "Izračunaj: 21/15 · 3/4.",
    "Izračunaj: 3/4 · 1/10.",
    "Izračunaj: 11/12 · 21/15.",
    "Izračunaj: 1/12 · 11/1.",
    "Izračunaj: 31/15 · 2/3.",
])
def test_normal_fractions_are_not_falsely_rejected(question):
    assert quality_ok("fraction_mul", question, "1/2") is True


def test_word_boundaries_are_load_bearing():
    """Without \\b these very cases match — the guard must not be 'fixed' by
    dropping the boundaries."""
    naive = re.compile(r"1\s*/\s*1")
    for question in ("Izračunaj: 1/10 · 3/4.", "Izračunaj: 11/12 · 3/4.",
                     "Izračunaj: 21/15 · 3/4."):
        assert naive.search(question), question       # naive pattern misfires
        assert quality_ok("fraction_mul", question, "1/2") is True


def test_guard_applies_only_to_fraction_mul():
    """Surrounding rules are untouched: another skill still accepts 1/1."""
    assert quality_ok("fraction_add_sub", "Izračunaj: 1/1 + 3/4.", "7/4") is True


@pytest.mark.parametrize("skill_id,question,expected", [
    ("gcd", "Odredi NZD(32, 32).", False),
    ("gcd", "Odredi NZD(32, 24).", True),
    ("fraction_expand", "Proširi 2/5 na nazivnik 5.", False),
    ("fraction_expand", "Proširi 2/5 na nazivnik 15.", True),
    ("percent_of", "Koliko je 25 % od 80?", True),
])
def test_neighbouring_quality_rules_unchanged(skill_id, question, expected):
    assert quality_ok(skill_id, question, "x") is expected


# --------------------------------------------------------------------------- #
# The percent_of guard on line 317 carried the SAME defect                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question,accepted", [
    ("Koliko je 100 % od 80?", False),      # 100 % of x tests nothing
    ("Koliko je 100% od 80?", False),
    ("Koliko je 25 % od 80?", True),
    ("Koliko je 1100 % od 80?", True),      # \b keeps these out of the rule
    ("Koliko je 5100% od 80?", True),
])
def test_percent_of_hundred_guard(question, accepted):
    assert quality_ok("percent_of", question, "x") is accepted


# --------------------------------------------------------------------------- #
# The source itself must stay free of the bytes that caused this               #
# --------------------------------------------------------------------------- #
def test_source_has_no_backspace_or_form_feed():
    text = io.open(SOURCE, encoding="utf-8").read()
    assert "\x08" not in text, "ASCII backspace in task_templates.py"
    assert "\x0c" not in text, "form feed in task_templates.py"


def test_no_control_characters_anywhere_in_matbot_and_tests():
    """Guards the whole tree, so the next mangled heredoc fails fast."""
    offenders = []
    for root in ("matbot", "tests"):
        for path in pathlib.Path(root).rglob("*.py"):
            text = io.open(path, encoding="utf-8").read()
            for number, line in enumerate(text.split("\n"), 1):
                if any(ord(c) < 32 and c != "\t" for c in line):
                    offenders.append(f"{path}:{number}")
    assert not offenders, offenders
