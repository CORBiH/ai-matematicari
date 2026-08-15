"""Tri-state doktrina slike + kalibracija nesigurnosti (2026-08-15).

Živa dijagnostika poslije Sol migracije (4 poziva, potpun trag kapija) — sva
četiri lažna odbijanja bila su ISTA klasa: `not_engaged` (verifikator ne
pokriva podoblik) tretiran identično kao `failed` (dokazana kontradikcija):

  p1 „2/3 od 27“→18      : image_math_source_unparsable  (proza „od“)
  p4 „6,56-x<3,8“→x>2,76 : image_equation_missing_equality (nejednačina u
                            polju jednačine — Literal nema tip nejednačine)
  p5 a=1,2dm, d=15cm→108 : image_rectangle_values_unusable (miješane
                            jedinice + dijagonala nije stranica)
  p3 (ranija runda)      : ista klasa, stohastička formulacija dokaza

Kapija čitljivosti NI U JEDNOM slučaju nije bila uzrok (clear/high/prazno).
"""
import pytest

from matbot import imagecheck
from matbot.quick import IMAGE_UNREADABLE_MESSAGE, run_quick_turn
from tests.conftest import FakeLLM, make_quick_image_output, make_visible_values


def run_image(output):
    fake = FakeLLM()
    fake.queue(output)

    class _Image:
        data_url = "data:image/jpeg;base64,AAAA"

        def log_metadata(self):
            return "test-image"

    return run_quick_turn(fake, {
        "grade": 7, "selected_topic": "", "selected_oblast": "",
        "student_message": "", "conversation_history": [], "interaction_phase": "",
    }, image=_Image())


# ---------------------------------------------------------------------------
# 1) VERIFIED — dokazano tačno objavljuje
# ---------------------------------------------------------------------------

def test_verified_expression_publishes():
    r = run_image(make_quick_image_output(
        reply="$18$", task_type="fraction_expression",
        visible_math="\\frac{2}{3} \\cdot 27"))
    assert r["answer"] == "$18$"


def test_od_form_is_normalized_and_verified():
    """Živi slučaj p1: školski zapis „a/b od n“ znači množenje — sada se
    verifikuje umjesto da pada kao neparsabilan."""
    v = imagecheck.verify_image_answer(make_quick_image_output(
        reply="$18$", task_type="fraction_expression",
        visible_math="\\frac{2}{3} od 27"))
    assert (v.supported, v.engaged, v.verified) == (True, True, True)


def test_od_form_with_wrong_answer_is_contradicted_and_blocks():
    """Ista normalizacija čini i POGREŠAN odgovor dokazivo pogrešnim."""
    v = imagecheck.verify_image_answer(make_quick_image_output(
        reply="$17$", task_type="fraction_expression",
        visible_math="\\frac{2}{3} od 27"))
    assert (v.engaged, v.verified) == (True, False)
    r = run_image(make_quick_image_output(
        reply="$17$", task_type="fraction_expression",
        visible_math="\\frac{2}{3} od 27"))
    assert r["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_od_normalization_is_narrow():
    """Samo puni oblik `<izraz> od <broj>`; sve drugo ostaje netaknuto."""
    assert imagecheck._normalize_od_form("2/3 od 27") == "(2/3)*(27)"
    assert imagecheck._normalize_od_form("od 27") == "od 27"
    assert imagecheck._normalize_od_form("2/3 od x") == "2/3 od x"
    assert imagecheck._normalize_od_form("2/3 od 27 od 5") == "2/3 od 27 od 5"


# ---------------------------------------------------------------------------
# 2) CONTRADICTED — dokazano pogrešno UVIJEK blokira
# ---------------------------------------------------------------------------

def test_contradicted_equation_blocks():
    r = run_image(make_quick_image_output(
        reply="$x=99$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math="3x+5=20"))
    assert r["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_wrong_unit_exponent_still_blocks():
    """Površina s linearnom jedinicom (D35-5) i dalje pada."""
    r = run_image(make_quick_image_output(
        reply="$P=40\\,\\text{cm}$", task_type="rectangle_area",
        requested_quantity="area",
        visible_values=make_visible_values(("a", "8", "cm"), ("b", "5", "cm"))))
    assert r["answer"] == IMAGE_UNREADABLE_MESSAGE


# ---------------------------------------------------------------------------
# 3) NOT_APPLICABLE — nepokriven podoblik NE blokira sam po sebi
# ---------------------------------------------------------------------------

def test_rectangle_from_side_and_diagonal_continues():
    """Živi slučaj p5: dijagonala nije stranica i jedinice su miješane —
    verifikator to ne pokriva, ali to NIJE dokaz greške."""
    out = make_quick_image_output(
        reply="$108\\,\\text{cm}^2$", task_type="rectangle_area",
        requested_quantity="area",
        visible_values=make_visible_values(("a", "1,2", "dm"), ("d", "15", "cm")))
    v = imagecheck.verify_image_answer(out)
    assert v.supported and not v.engaged          # NOT_APPLICABLE
    assert run_image(out)["answer"] == "$108\\,\\text{cm}^2$"


def test_inequality_in_equation_slot_continues():
    """Živi slučaj p4: Literal nema tip nejednačine, pa Sol bira
    linear_equation; verifikator traži '=' — nepokriveno, nastavlja se."""
    out = make_quick_image_output(
        reply="$x>2,76$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math="6,56 - x < 3,8")
    v = imagecheck.verify_image_answer(out)
    assert v.supported and not v.engaged
    assert run_image(out)["answer"] == "$x>2,76$"


def test_not_applicable_never_claims_verification():
    out = make_quick_image_output(
        reply="$108\\,\\text{cm}^2$", task_type="rectangle_area",
        requested_quantity="area",
        visible_values=make_visible_values(("a", "1,2", "dm"), ("d", "15", "cm")))
    v = imagecheck.verify_image_answer(out)
    assert v.verified is False and v.may_publish is False


def test_general_validators_still_run_after_not_applicable():
    """NOT_APPLICABLE nije povjerenje: nedosljedan lanac jednakosti u odgovoru
    i dalje pada na mathcheck-u."""
    out = make_quick_image_output(
        reply="$P=12\\cdot 9=100\\,\\text{cm}^2$", task_type="rectangle_area",
        requested_quantity="area",
        visible_values=make_visible_values(("a", "1,2", "dm"), ("d", "15", "cm")))
    r = run_image(out)
    assert "100" not in r["answer"]               # odbijeno, ne objavljeno


# ---------------------------------------------------------------------------
# 4) IZUZETAK: prazan dokaz za podržanu porodicu i dalje blokira (D35T-2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("visible_math", ["", "Riješi jednačinu:"])
def test_missing_or_heading_evidence_still_blocks(visible_math):
    r = run_image(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math=visible_math))
    assert r["answer"] == IMAGE_UNREADABLE_MESSAGE


# ---------------------------------------------------------------------------
# 5) Nesigurnost: matematički bitna blokira, kadar ne
# ---------------------------------------------------------------------------

def test_math_uncertainty_blocks_even_with_verified_evidence():
    r = run_image(make_quick_image_output(
        reply="$18$", task_type="fraction_expression",
        visible_math="\\frac{2}{3} \\cdot 27",
        uncertainty_reason="Nazivnik može biti 3 ili 8.",
        math_content_uncertain=True))
    assert r["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_framing_note_alone_does_not_block():
    r = run_image(make_quick_image_output(
        reply="$18$", task_type="fraction_expression",
        visible_math="\\frac{2}{3} \\cdot 27",
        uncertainty_reason="Izrez je vrlo blizu ivice papira.",
        math_content_uncertain=False))
    assert r["answer"] == "$18$"


def test_multiple_tasks_still_asks_the_student():
    r = run_image(make_quick_image_output(
        reply="$18$", readability="multiple_tasks"))
    assert "više zadataka" in r["answer"]
