"""ALGEBARSKI OBLIK je dio gradiva, ne ukras.

RUCNI NALAZ (6-07-003, „Nejednacine s razlomcima oblika x +- a < b / > b i
a +- x < b / > b“): NPP lekcije izricito nabraja OBA porodicna oblika, a ucenik
je 20 od 20 puta dobijao samo `x`-prvi. Mjereno nad stvarnim serverskim putem,
bez ijednog modelskog poziva — lekcija ide determinstickom rutom.

`a - x` nije kozmeticka varijanta: nepoznata je tamo UMANJILAC, pa izolacija
OBRCE smjer nejednakosti. To je upravo ono sto lekcija uci.
"""
import json
import random
import re
from fractions import Fraction
from pathlib import Path

import pytest

from matbot import form_variants as fv
from matbot import mcq_integrity
from matbot import solution_consistency
from matbot.deterministic import equations
from matbot.practice_policy import resolve as resolve_policy

ROOT = Path(__file__).resolve().parents[1]
LESSON = "6-07-003"
PARAMS = {"number_domain": "rational_nonneg",
          "shapes": ["solve_inequality_additive"]}

SUPPORT = json.loads(
    (ROOT / "data" / "task_form_variants.json").read_text(encoding="utf-8"))
OBJECTIVES = json.loads(
    (ROOT / "data" / "lesson_objectives.compiled.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _clear():
    fv._payload.cache_clear()
    yield
    fv._payload.cache_clear()


# ---------------------------------------------------------------------------
# 1) GRADIVO STVARNO TRAZI OBA OBLIKA
# ---------------------------------------------------------------------------

def test_curriculum_evidence_declares_both_families():
    rows = OBJECTIVES.get("lessons") or OBJECTIVES
    evidence = " ".join(str(part) for part
                        in (rows[LESSON].get("supporting_concepts") or ()))
    assert "x ± a" in evidence and "a ± x" in evidence
    assert rows[LESSON]["objective_source"] == "npp_exact_mapping"
    assert set(SUPPORT["lessons"][LESSON]["supported"]) == set(fv.ALL_VARIANTS)


# ---------------------------------------------------------------------------
# 2-6) KLASIFIKATOR OBLIKA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (r"Riješi nejednačinu: $x + \frac{2}{3} < \frac{4}{3}$", fv.X_PLUS_A),
    (r"Riješi nejednačinu: $x - \frac{1}{2} > \frac{1}{4}$", fv.X_MINUS_A),
    (r"Riješi nejednačinu: $\frac{1}{2} + x < 1$", fv.A_PLUS_X),
    (r"Riješi nejednačinu: $\frac{3}{4} - x < \frac{1}{2}$", fv.A_MINUS_X),
])
def test_each_algebraic_form_is_recognised(text, expected):
    assert fv.classify(text) == expected


@pytest.mark.parametrize("swapped,expected", [
    # Zamjena STRANA cijele nejednacine NIJE drugi oblik — nepoznata je i dalje
    # prvi operand svoje strane.
    (r"Riješi nejednačinu: $\frac{4}{3} > x - \frac{2}{3}$", fv.X_MINUS_A),
    (r"Riješi nejednačinu: $1 < x + \frac{1}{2}$", fv.X_PLUS_A),
])
def test_swapping_sides_never_fakes_a_second_operand_form(swapped, expected):
    assert fv.classify(swapped) == expected
    assert fv.classify(swapped) not in (fv.A_PLUS_X, fv.A_MINUS_X)


def test_unprovable_text_claims_nothing():
    assert fv.classify("") == ""
    assert fv.classify("Neka recenica bez relacije.") == ""
    assert fv.classify("$x + 1 < x + 2$") == ""       # nepoznata na obje strane


# ---------------------------------------------------------------------------
# 7) SERVERSKA ROTACIJA OBLIKA
# ---------------------------------------------------------------------------

def test_rotation_uses_every_declared_form_before_repeating():
    seen, recent = [], []
    for _ in range(len(fv.ALL_VARIANTS)):
        chosen = fv.preferred_variant(LESSON, recent)
        seen.append(chosen)
        recent.append(chosen)
    assert sorted(seen) == sorted(fv.ALL_VARIANTS)


def test_rotation_recycles_the_least_recently_used_form():
    """Niz `A B C D A` ne smije opet vratiti `A` — taj je upravo vidjen."""
    recent = [fv.X_PLUS_A, fv.X_MINUS_A, fv.A_PLUS_X, fv.A_MINUS_X, fv.X_PLUS_A]
    assert fv.preferred_variant(LESSON, recent) == fv.X_MINUS_A


def test_lesson_without_declared_forms_gets_no_rotation():
    assert fv.supported("6-01-012") == ()
    assert fv.preferred_variant("6-01-012", []) == ""


def test_form_rotation_has_a_rollback(monkeypatch):
    monkeypatch.setenv("MATBOT_FORM_ROTATION", "disabled")
    assert fv.preferred_variant(LESSON, []) == ""


def test_history_window_is_bounded_by_configuration():
    from matbot import config
    assert config.MAX_RECENT_STRUCTURES >= 4      # dovoljno za sva cetiri oblika
    window = [fv.X_PLUS_A] * 200
    assert fv.preferred_variant(LESSON, window) in fv.ALL_VARIANTS


# ---------------------------------------------------------------------------
# 8-10) MATEMATIKA `a - x` — TACNA RACIONALNA ARITMETIKA
# ---------------------------------------------------------------------------

_FRAC_RE = re.compile(r"\\frac\{(-?\d+)\}\{(\d+)\}")


def _number(token):
    token = token.strip().strip("$").strip()
    match = _FRAC_RE.fullmatch(token)
    if match:
        return Fraction(int(match.group(1)), int(match.group(2)))
    return Fraction(int(token.replace("{", "").replace("}", "")))


def _solve(inequality):
    """Nezavisno rjesenje objavljene nejednacine — ne vjeruje generatoru."""
    body = inequality.replace("$", "").strip()
    relation = re.search(r"[<>]", body)
    symbol = body[relation.start()]
    left, right = body[:relation.start()].strip(), body[relation.end():].strip()
    rhs = _number(right)
    flipped = {"<": ">", ">": "<"}[symbol]
    if left.startswith("x"):
        rest = left[1:].strip()
        value = _number(rest[1:])
        return symbol, (rhs - value if rest[0] == "+" else rhs + value)
    split = re.search(r"[+\-](?=\s*x\b)", left)
    value = _number(left[:split.start()])
    if left[split.start()] == "+":
        return symbol, rhs - value
    return flipped, value - rhs               # a - x: smjer se OBRCE


def _package(form, seed, level=1):
    return equations.generate_package(
        LESSON, "Nejednačine", PARAMS, level, rng=random.Random(seed),
        policy=resolve_policy(6), preferred_form=form)


def _marked(package):
    text = package.display_answer.replace("$", "").strip()
    relation = re.search(r"[<>]", text)
    return text[relation.start()], _number(text[relation.end():])


@pytest.mark.parametrize("form", fv.ALL_VARIANTS)
def test_generator_produces_the_requested_form(form):
    produced = {fv.classify(_package(form, seed).question) for seed in range(6)}
    assert produced == {form}


def test_a_minus_x_reverses_the_inequality_direction():
    """`a - x < b` daje `x > ...`, a `a - x > b` daje `x < ...`."""
    seen = set()
    for seed in range(40):
        package = _package(fv.A_MINUS_X, seed)
        inequality = package.question.split(":", 1)[1].strip()
        written = re.search(r"[<>]", inequality).group()
        marked_symbol, _ = _marked(package)
        assert marked_symbol != written, inequality
        seen.add(written)
    assert seen == {"<", ">"}                 # oba smjera stvarno nastaju


@pytest.mark.parametrize("form", fv.ALL_VARIANTS)
def test_marked_answer_matches_independently_solved_answer(form):
    for seed in range(30):
        package = _package(form, seed, level=1 + seed % 3)
        inequality = package.question.split(":", 1)[1].strip()
        assert _solve(inequality) == _marked(package), inequality


@pytest.mark.parametrize("form", fv.ALL_VARIANTS)
def test_exactly_one_option_is_mathematically_correct(form):
    for seed in range(30):
        package = _package(form, seed, level=1 + seed % 3)
        truth = _solve(package.question.split(":", 1)[1].strip())
        correct = 0
        for option in package.option_texts:
            body = option.replace("$", "").strip()
            relation = re.search(r"[<>]", body)
            if (body[relation.start()], _number(body[relation.end():])) == truth:
                correct += 1
        assert correct == 1, package.option_texts


def test_forgetting_to_reverse_is_offered_as_a_wrong_option():
    """Najvazniji distraktor za `a - x`: ucenik koji NIJE obrnuo smjer."""
    package = _package(fv.A_MINUS_X, 1)
    inequality = package.question.split(":", 1)[1].strip()
    written = re.search(r"[<>]", inequality).group()
    _, bound = _marked(package)
    unreversed = [option for option in package.option_texts
                  if re.search(r"[<>]", option).group() == written
                  and _number(option.replace("$", "").split(written)[1]) == bound]
    assert len(unreversed) == 1


# ---------------------------------------------------------------------------
# 11-12) POSTOJECE ZASTITE OSTAJU ZIVE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("form", fv.ALL_VARIANTS)
def test_server_oracle_independently_confirms_the_marked_option(form):
    """`mcq_integrity` SAM rijesi objavljenu nejednacinu i presudi oznaku.

    Za `a - x` to znaci da orakl mora sam obrnuti smjer — da ne obrne, oznacena
    opcija bi mu izgledala netacno."""
    for seed in range(20):
        package = _package(form, seed, level=1 + seed % 3)
        verdict = mcq_integrity.evaluate_linear_solve_mcq(
            package.question, package.option_texts)
        assert verdict.applicable and verdict.valid, package.question
        assert verdict.correct_indices == (package.correct_index,), package.question


def test_a_wrong_marked_option_is_caught_by_the_server_oracle():
    """Kontrola same zastite: pomjerena oznaka MORA prestati da se poklapa."""
    package = _package(fv.A_MINUS_X, 1)
    verdict = mcq_integrity.evaluate_linear_solve_mcq(
        package.question, package.option_texts)
    wrong_index = (package.correct_index + 1) % len(package.option_texts)
    assert verdict.correct_indices != (wrong_index,)


@pytest.mark.parametrize("form", fv.ALL_VARIANTS)
def test_solution_text_never_contradicts_the_marked_answer(form):
    for seed in range(20):
        package = _package(form, seed)
        diverged, _ = solution_consistency.divergence(
            package.display_answer, package.solution)
        assert not diverged, package.solution


# ---------------------------------------------------------------------------
# 13-14) ARHITEKTURA
# ---------------------------------------------------------------------------

def test_no_lesson_id_patch_in_the_engine():
    for module in ("form_variants.py", "deterministic/equations.py"):
        source = (ROOT / "matbot" / module).read_text(encoding="utf-8")
        assert not re.search(r"\b\d-\d\d-\d\d\d\b", source), module
    script = (ROOT / "scripts" / "build_form_variant_support.py").read_text(
        encoding="utf-8")
    assert not re.search(r"\b\d-\d\d-\d\d\d\b", script)


def test_declared_forms_come_from_evidence_not_a_handwritten_list():
    for lesson_id, row in SUPPORT["lessons"].items():
        assert row["source"] == "npp_declared_forms", lesson_id
        assert len(row["supported"]) >= 2
