# -*- coding: utf-8 -*-
"""Dva imenovana ugovora normalizacije — leksički i brojevno-čuvajući.

ZAŠTO POSTOJI: projekat je imao PET implementacija normalizacije u ČETIRI
mjerena ugovora. Ovi testovi zaključavaju oba ugovora i, važnije, dokazuju da
migracija na zajednički modul NIJE promijenila nijedno zatečeno ponašanje.

Brojevni ugovor NE donosi nijednu kurikularnu odluku i ne računa ništa — on
samo čuva ono što bi budući deterministički parser morao vidjeti.
"""
import unicodedata

import pytest

from matbot import (capability_requests, lesson_relevance, quick,
                    quick_context, textnorm)
from matbot.semantics import request_shapes

lex = textnorm.normalize_lexical
num = textnorm.normalize_numeric


# ---------------------------------------------------------------------------
# LEKSIČKI UGOVOR
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", (
    ("ŠTA JE HIPOTENUZA?", "sta je hipotenuza"),
    ("Kolika  je   hipotenuza?", "kolika je hipotenuza"),
    ("  rubni   razmaci  ", "rubni razmaci"),
    ("čćšž", "ccsz"),
    ("ČĆŠŽ", "ccsz"),
))
def test_lexical_lowercases_folds_and_collapses(raw, expected):
    assert lex(raw) == expected


def test_lexical_keeps_dstroke_unless_asked():
    """Podrazumijevano „đ" PREŽIVI — potrošači koji ga koriste kao oznaku."""
    assert lex("Nađi") == "nađi"
    assert lex("Nađi", fold_dstroke=True) == "nadi"


def test_lexical_can_preserve_selected_punctuation():
    assert ")" not in lex("pod b)")
    assert lex("pod b)", keep=")") == "pod b)"


def test_lexical_can_skip_whitespace_collapsing():
    assert lex("a    b", collapse_whitespace=False) == "a    b"
    assert lex("a    b") == "a b"


def test_lexical_destroys_value_bearing_punctuation_by_design():
    """Ovo NIJE greška leksičkog ugovora nego njegova svrha — zato brojevni
    put postoji zasebno."""
    assert lex("2,25") == "2 25"
    assert lex("P = 36") == "p 36"


# ---------------------------------------------------------------------------
# BROJEVNO-ČUVAJUĆI UGOVOR
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", (
    "2,25", "0,5", "1,75", "2.25", "P = 36", "x = -3", "1/2", "3:4",
    "50%", "6 × 8", "6 x 8",
))
def test_numeric_preserves_value_bearing_syntax(raw):
    out = num(raw)
    for ch in raw:
        if ch in ",.=-/:%×":
            assert ch in out, (raw, ch, out)


def test_numeric_never_rewrites_superscripts():
    """NFKD bi „2²" pretvorio u „22" — dakle DRUGI broj. Brojevni put ne smije."""
    assert unicodedata.normalize("NFKD", "2²") == "22"      # dokaz opasnosti
    assert num("2²") == "2²"
    assert num("5³") == "5³"
    assert num("√20") == "√20"


def test_numeric_folds_our_letters_without_touching_digits():
    assert num("POVRŠINA") == "povrsina"
    assert num("Nađi") == "nadi"
    assert num("čćšžđ") == "ccszd"
    assert num("2,25 cm²") == "2,25 cm²"


def test_numeric_collapses_whitespace_but_keeps_token_boundaries():
    assert num("20   cm") == "20 cm"
    assert num("20cm") == "20cm"
    assert num("20 cm") != num("20cm")


# ---------------------------------------------------------------------------
# SIGURNOST POKVARENIH TOKENA — nikad tiho popravljanje
# ---------------------------------------------------------------------------

MALFORMED = ("2O", "O2", "2l", "l2", "1O,5", "2..5", "2,,5", "--3", "3-", ",5.")


@pytest.mark.parametrize("raw", MALFORMED)
def test_numeric_leaves_malformed_tokens_malformed(raw):
    out = num(raw)
    assert out == raw.lower(), (raw, out)


def test_numeric_never_turns_letters_into_digits():
    """Bez fuzzy popravke: O ne postaje 0, l ne postaje 1."""
    assert num("2O") == "2o" != "20"
    assert num("2l") == "2l" != "21"
    assert num("1O,5") == "1o,5"


def test_malformed_stays_distinguishable_from_valid():
    """Ključno za budući parser: mora moći ODBITI, a ne pogoditi."""
    assert num("20") != num("2O")
    assert num("2,25") != num("225")
    assert num("-3") != num("3")
    assert num("20cm") != num("20 cm")


def test_lexical_would_have_destroyed_those_distinctions():
    """Zašto brojevni ugovor uopšte postoji — leksički ih spaja ili kvari."""
    assert lex("2,25") == lex("2 25")          # decimalni zarez izgubljen
    assert lex("--3") == lex("3")              # pokvaren predznak -> validan broj
    assert lex("2²") == "22"                   # NFKD napravio drugi broj


# ---------------------------------------------------------------------------
# MIGRACIJA — nijedan pozivalac nije promijenio ponašanje
# ---------------------------------------------------------------------------

SAMPLES = (
    "", "   ", "Nađi hipotenuzu za katete 8 i 15.", "Nadi hipotenuzu",
    "čćšžĐŽ", "pod b)", "pod đ)", "zadatak 3)", "Treći.", "2,25", "P = 36",
    "2²", "√20", "6 × 8", "2O", "20cm", "20 cm", "--3",
    "Kolika  je   hipotenuza?", "ŠTA JE HIPOTENUZA?",
    "Pravougaonik je 6 cm x 8 cm. Kolika mu je dijagonala?",
    "Površina je 20 kvadratnih centimetara. Kolika je dijagonala?",
)

MIGRATED = (
    (capability_requests._normalize,
     lambda t: lex(t, collapse_whitespace=False)),
    (lesson_relevance._normalize, lambda t: lex(t)),
    (quick._normalized_conversation_phrase, lambda t: lex(t)),
    (quick_context._fold, lambda t: lex(t, keep=")")),
    (request_shapes._normalize,
     lambda t: lex(t, collapse_whitespace=False, fold_dstroke=True)),
)


@pytest.mark.parametrize("sample", SAMPLES)
def test_every_migrated_caller_matches_its_declared_profile(sample):
    for caller, profile in MIGRATED:
        assert caller(sample) == profile(sample), (caller, sample)


def test_quick_context_task_label_still_reads_dstroke():
    """Regresijska brana: `đ` je u azbuci oznaka, pa se NE smije preslikati."""
    assert quick_context.requested_task_label("pod đ)") == "đ"
    assert quick_context.requested_task_label("pod b)") == "b"


def test_request_shapes_still_folds_dstroke():
    assert request_shapes._normalize("Nađi") == "nadi"


def test_textnorm_carries_no_curriculum_logic():
    source = open(textnorm.__file__, encoding="utf-8").read()
    for banned in ("grade", "razred", "policy", "capability", "sqrt_operation",
                   "pythagoras", "lesson_id", "Fraction", "isqrt"):
        assert banned not in source, banned
