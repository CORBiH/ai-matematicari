"""D35-5/D35-6: posebna strukturna šema za sliku, stroga kapija čitljivosti i
nezavisna provjera podržanih porodica zadataka.

Regresije su vezane za pozive 33 i 35 kampanje od 35: „$P=26\\,\\text{cm}$“ za
površinu pravougaonika 8x5, i „$x=5$“ za jednačinu čija je desna strana bila
namjerno prekrivena."""
import io
import json
import logging

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage

from matbot import imageinput
from matbot.imagecheck import claimed_result, verify_image_answer
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.quick import (
    IMAGE_MULTIPLE_TASKS_MESSAGE,
    IMAGE_NON_MATH_MESSAGE,
    IMAGE_UNREADABLE_MESSAGE,
    run_quick_turn,
)
from matbot.schema import QuickImageTurnOutput, QuickTurnOutput
from tests.conftest import FakeLLM, make_quick_image_output, make_visible_values


def jpeg_bytes(size=(60, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buffer, format="JPEG")
    return buffer.getvalue()


def valid_image():
    return imageinput.validate_image_upload(
        FileStorage(stream=io.BytesIO(jpeg_bytes()), filename="z.jpg", content_type="image/jpeg")
    )


def image_payload(msg="Daj rezultat."):
    return {
        "session_id": "img-sess", "grade": 7, "selected_topic": "",
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "",
        "last_tutor_task": "", "last_tutor_message": "", "conversation_history": [],
    }


def run_image(output, msg="Daj rezultat."):
    fake = FakeLLM()
    fake.queue(output)
    return run_quick_turn(fake, image_payload(msg), image=valid_image()), fake


RECT_AREA = dict(
    task_type="rectangle_area", requested_quantity="area", unit="cm^2",
    visible_problem_text="Pravougaonik a=8 cm, b=5 cm. Izračunaj P.",
    visible_values=make_visible_values(("a", "8", "cm"), ("b", "5", "cm")),
)


# --- 35-36: šeme su razdvojene ---------------------------------------------

def test_text_only_result_schema_is_unchanged():
    assert set(QuickTurnOutput.model_fields) == {"reply"}


def test_image_result_uses_the_dedicated_schema():
    fields = set(QuickImageTurnOutput.model_fields)
    assert fields == {
        "reply", "readability", "all_required_symbols_visible", "task_type",
        "visible_math", "visible_problem_text", "requested_quantity",
        "visible_values", "unit", "answer_confidence", "uncertainty_reason",
        "math_content_uncertain",
    }


def test_llm_selects_the_image_schema_only_when_an_image_is_present():
    from matbot.llm import OpenAIPracticeLLM

    calls = []

    class Recording(OpenAIPracticeLLM):
        def _structured_turn(self, instructions, input_text, text_format, **kw):
            calls.append(text_format)
            return None

    llm = Recording()
    llm.quick_turn("i", "t", image=None)
    llm.quick_turn("i", "t", image=valid_image())
    assert calls == [QuickTurnOutput, QuickImageTurnOutput]


# --- 37-43: kapija čitljivosti ---------------------------------------------

def test_clear_high_confidence_image_may_proceed():
    response, _fake = run_image(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", **RECT_AREA))
    assert response["answer"] == "$P=40\\,\\text{cm}^2$"


@pytest.mark.parametrize("readability", ["partially_unreadable", "unreadable"])
def test_unreadable_image_fails_closed(readability):
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", readability=readability))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_missing_required_symbol_fails_closed():
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", all_required_symbols_visible=False))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE


@pytest.mark.parametrize("confidence", ["medium", "low"])
def test_non_high_confidence_fails_closed(confidence):
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", answer_confidence=confidence))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_material_math_uncertainty_fails_closed():
    """Nečitljiv MATEMATIČKI sadržaj (math_content_uncertain=True) blokira —
    ista zaštita kao ranije, sada kroz strukturni signal."""
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", uncertainty_reason="Desna strana je prekrivena.",
        math_content_uncertain=True))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_harmless_framing_note_does_not_block_when_math_is_certain():
    """KALIBRACIJA ZA SOL (živa dijagnostika 2026-08-15): pošten opis kadra
    („izrez blizu ivice“) uz SVE čitljive simbole i math_content_uncertain=False
    NE smije sam po sebi ubiti objavu — to je ranije radilo, i temeljitiji
    model je zbog vlastite iskrenosti bio kažnjavan."""
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math="3x+5=20",
        uncertainty_reason="Izrez je blizu ivice stranice.",
        math_content_uncertain=False))
    assert response["answer"] == "$x=5$"


def test_call35_obscured_value_cannot_produce_a_public_answer():
    """Poziv 35: model prijavi da nešto nije čitljivo, ali svejedno ponudi
    broj — broj se odbacuje, ne prikazuje."""
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", readability="partially_unreadable",
        all_required_symbols_visible=False, task_type="linear_equation",
        requested_quantity="value_of_unknown",
        uncertainty_reason="Desna strana jednačine je prekrivena.",
    ))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE
    assert "5" not in response["answer"]


def test_multiple_tasks_asks_for_clarification():
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", readability="multiple_tasks"))
    assert response["answer"] == IMAGE_MULTIPLE_TASKS_MESSAGE


def test_non_math_image_gets_its_own_message():
    response, _fake = run_image(make_quick_image_output(
        reply="Nešto", readability="non_math"))
    assert response["answer"] == IMAGE_NON_MATH_MESSAGE


# --- 44-46: interna polja ne curе ------------------------------------------

def test_internal_fields_never_enter_the_browser_payload():
    response, _fake = run_image(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", **RECT_AREA))
    body = json.dumps(response, ensure_ascii=False)
    for field in ("readability", "answer_confidence", "visible_values",
                  "visible_problem_text", "uncertainty_reason",
                  "all_required_symbols_visible", "task_type"):
        assert field not in body
    assert "Pravougaonik a=8" not in body


def test_internal_fields_never_enter_history_or_state():
    response, _fake = run_image(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", **RECT_AREA))
    assert response["next_state"] == {}
    assert response["last_tutor_task"] == ""


def test_transcription_is_not_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger="matbot.quick"):
        run_image(make_quick_image_output(reply="$P=26\\,\\text{cm}$", **RECT_AREA))
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Pravougaonik a=8" not in text
    assert "base64" not in text
    assert "rectangle_area" in text  # samo ograničen kod porodice smije u log


def test_gate_log_contains_only_bounded_status_codes(caplog):
    with caplog.at_level(logging.INFO, logger="matbot.quick"):
        run_image(make_quick_image_output(
            reply="$x=5$", readability="unreadable",
            visible_problem_text="TAJNI SADRŽAJ SLIKE"))
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "TAJNI SADRŽAJ" not in text
    assert "image_gate=not_clear" in text


# --- 47-54: pravougaonik / kvadrat -----------------------------------------

def test_rectangle_area_8x5_produces_40_cm2():
    response, _fake = run_image(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", **RECT_AREA))
    assert response["answer"] == "$P=40\\,\\text{cm}^2$"


def test_call33_perimeter_like_26_cm_is_rejected_for_an_area_request():
    response, _fake = run_image(make_quick_image_output(
        reply="$P=26\\,\\text{cm}$", **RECT_AREA))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE
    assert "26" not in response["answer"]


def test_linear_unit_is_rejected_for_area():
    response, _fake = run_image(make_quick_image_output(
        reply="$P=40\\,\\text{cm}$", **RECT_AREA))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_rectangle_perimeter_8x5_produces_26_cm():
    response, _fake = run_image(make_quick_image_output(
        reply="$O=26\\,\\text{cm}$", task_type="rectangle_perimeter",
        requested_quantity="perimeter", unit="cm",
        visible_values=make_visible_values(("a", "8", "cm"), ("b", "5", "cm"))))
    assert response["answer"] == "$O=26\\,\\text{cm}$"


def test_squared_unit_is_rejected_for_perimeter():
    result = verify_image_answer(make_quick_image_output(
        reply="$O=26\\,\\text{cm}^2$", task_type="rectangle_perimeter",
        requested_quantity="perimeter", unit="cm",
        visible_values=make_visible_values(("a", "8", "cm"), ("b", "5", "cm"))))
    assert result.code == "image_unit_exponent_mismatch"
    assert not result.may_publish


def test_area_perimeter_task_type_mismatch_rejects():
    result = verify_image_answer(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", task_type="rectangle_area",
        requested_quantity="perimeter", unit="cm^2",
        visible_values=make_visible_values(("a", "8", "cm"), ("b", "5", "cm"))))
    assert result.code == "image_requested_quantity_mismatch"
    assert not result.may_publish


def test_incompatible_units_reject_safely():
    result = verify_image_answer(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", task_type="rectangle_area",
        requested_quantity="area", unit="cm^2",
        visible_values=make_visible_values(("a", "8", "cm"), ("b", "5", "m"))))
    assert result.code == "image_rectangle_values_unusable"
    assert not result.may_publish
    assert result.supported and not result.engaged


def test_missing_side_length_rejects_safely():
    result = verify_image_answer(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", task_type="rectangle_area",
        requested_quantity="area", unit="cm^2",
        visible_values=make_visible_values(("a", "8", "cm"))))
    assert result.code == "image_rectangle_wrong_side_count"
    assert not result.may_publish


def test_square_area_uses_a_single_side():
    assert verify_image_answer(make_quick_image_output(
        reply="$P=25\\,\\text{cm}^2$", task_type="square_area",
        requested_quantity="area", unit="cm^2",
        visible_values=make_visible_values(("a", "5", "cm")))).may_publish


def test_square_perimeter_is_verified():
    assert verify_image_answer(make_quick_image_output(
        reply="$O=20\\,\\text{cm}$", task_type="square_perimeter",
        requested_quantity="perimeter", unit="cm",
        visible_values=make_visible_values(("a", "5", "cm")))).may_publish
    assert not verify_image_answer(make_quick_image_output(
        reply="$O=25\\,\\text{cm}$", task_type="square_perimeter",
        requested_quantity="perimeter", unit="cm",
        visible_values=make_visible_values(("a", "5", "cm")))).may_publish


# --- 55-59: izrazi i jednačine ---------------------------------------------

def test_correct_simple_fraction_image_passes():
    assert verify_image_answer(make_quick_image_output(
        reply="Rezultat je $0,75$.", task_type="fraction_expression",
        visible_math="\\frac{3}{4}")).may_publish


def test_wrong_simple_fraction_result_rejects():
    result = verify_image_answer(make_quick_image_output(
        reply="Rezultat je $0,8$.", task_type="fraction_expression",
        visible_math="\\frac{3}{4}"))
    assert result.code == "image_expression_value_mismatch"
    assert result.engaged and not result.verified


def test_result_written_as_a_fraction_is_actually_verified():
    """Živi preflight nalaz: odgovor zapisan kao razlomak ima DVA broja, pa je
    ranije padao na „tačno jedan broj“ pravilu i cijela porodica je tiho
    preskakana — ni jedan pogrešan razlomak nije mogao biti odbijen."""
    assert claimed_result("Rezultat je $\\frac{5}{6}$.")[0] == pytest.approx(5 / 6)
    assert verify_image_answer(make_quick_image_output(
        reply="Rezultat je $\\frac{5}{6}$.", task_type="fraction_expression",
        visible_math="\\frac{2}{3}+\\frac{1}{6}")).may_publish
    assert verify_image_answer(make_quick_image_output(
        reply="Rezultat je $\\frac{3}{6}$.", task_type="fraction_expression",
        visible_math="\\frac{2}{3}+\\frac{1}{6}")).code == "image_expression_value_mismatch"


def test_plain_slash_fraction_source_is_parsed():
    assert verify_image_answer(make_quick_image_output(
        reply="$\\frac{5}{6}$", task_type="fraction_expression",
        visible_math="2/3 + 1/6")).may_publish
    assert verify_image_answer(make_quick_image_output(
        reply="$\\frac{4}{6}$", task_type="fraction_expression",
        visible_math="2/3 + 1/6")).code == "image_expression_value_mismatch"


def test_trailing_equals_in_source_is_tolerated():
    assert verify_image_answer(make_quick_image_output(
        reply="$\\frac{5}{6}$", task_type="fraction_expression",
        visible_math="2/3 + 1/6 =")).may_publish


def test_claimed_result_takes_the_right_hand_side_of_the_last_equality():
    assert claimed_result("$O=2(a+b)=26\\,\\text{cm}$")[0] == pytest.approx(26)


def test_claimed_result_is_unparsable_when_ambiguous():
    assert claimed_result("Nema brojeva ovdje.") == (None, None, None)
    assert claimed_result("Vidi $3$ i $x+1$ i $7$.")[0] == pytest.approx(7)


def test_correct_arithmetic_result_passes():
    assert verify_image_answer(make_quick_image_output(
        reply="$=17$", task_type="arithmetic", visible_math="12+5")).may_publish


def test_wrong_arithmetic_result_rejects():
    assert verify_image_answer(make_quick_image_output(
        reply="$=15$", task_type="arithmetic",
        visible_math="12+5")).code == "image_expression_value_mismatch"


def test_correct_linear_equation_result_passes_substitution():
    assert verify_image_answer(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math="2x+3=13")).may_publish


def test_wrong_linear_equation_result_rejects():
    result = verify_image_answer(make_quick_image_output(
        reply="$x=4$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math="2x+3=13"))
    assert result.code == "image_equation_substitution_failed"
    assert result.engaged and not result.verified


def test_unsupported_task_type_is_reported_as_unsupported_not_verified():
    result = verify_image_answer(make_quick_image_output(
        reply="Bilo šta", task_type="other", visible_problem_text="nešto"))
    assert not result.supported
    assert not result.engaged
    assert not result.verified
    assert not result.may_publish


# --- 60-63: invarijante ----------------------------------------------------

def test_no_second_model_call_occurs_for_a_rejected_image():
    _response, fake = run_image(make_quick_image_output(
        reply="$P=26\\,\\text{cm}$", **RECT_AREA))
    assert len(fake.quick_calls) == 1


def test_no_second_model_call_occurs_for_a_gated_image():
    _response, fake = run_image(make_quick_image_output(
        reply="$x=5$", readability="unreadable"))
    assert len(fake.quick_calls) == 1


def test_invalid_image_structure_does_not_persist_state(store):
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", readability="unreadable"))
    assert store.peek("img-sess") is None
    assert "next_state" not in response


def test_oversized_internal_field_is_rejected_as_invalid_output():
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", visible_problem_text="x" * 5000))
    assert response["answer"] == SAFE_ERROR_MESSAGE


def test_image_prompt_requires_a_visual_inventory():
    _response, fake = run_image(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", **RECT_AREA))
    instructions, _input_text = fake.quick_calls[0]
    assert "POPIS VIĐENOG" in instructions
    assert "NIKAD ne rekonstruiši skriven broj iz očekivanog rješenja" in instructions
    assert "NIKAD ne biraj vrijednost samo zato što čini jednačinu rješivom" in instructions
    assert "Nesigurnost se PRIJAVLJUJE" in instructions


# ---------------------------------------------------------------------------
# D35T-2: eksplicitna stanja provjere — „nije se izvršilo“ != „provjereno“
# ---------------------------------------------------------------------------
# Živi nalaz (kampanja od 14 poziva, pozivi 12 i 13): model je u polje s
# izrazom stavljao NASLOV zadatka („Rijesi jednacinu:“), provjera je tiho
# preskakala, a prazna lista problema je čitana kao uspjeh — pa bi i „$x=99$“
# za $3x+5=20$ bilo objavljeno.

HEADING_ONLY = "Rijesi jednacinu:"


def test_empty_result_cannot_mean_both_skipped_and_verified():
    """Ista „prazna“ situacija sada ima RAZLIČITA stanja."""
    skipped = verify_image_answer(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation", visible_math=HEADING_ONLY))
    verified = verify_image_answer(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation", visible_math="3x+5=20"))
    unsupported_family = verify_image_answer(make_quick_image_output(
        reply="$x=5$", task_type="other"))

    assert (skipped.supported, skipped.engaged, skipped.verified) == (True, False, False)
    assert (verified.supported, verified.engaged, verified.verified) == (True, True, True)
    assert (unsupported_family.supported, unsupported_family.engaged) == (False, False)
    assert not skipped.may_publish and verified.may_publish


def test_supported_engaged_and_correct_publishes():
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math="3x+5=20"))
    assert response["answer"] == "$x=5$"


def test_supported_but_not_engaged_is_a_safe_rejection():
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math=HEADING_ONLY))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_supported_with_missing_evidence_is_a_safe_rejection():
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math=""))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_supported_but_unparsable_evidence_is_not_applicable_and_continues():
    """TRI-STATE DOKTRINA (2026-08-15): dokaz koji verifikator NE UMIJE
    parsirati nije dokaz greške — NOT_APPLICABLE nastavlja kroz opšte
    validatore (verifikacija se ne tvrdi). Živa dijagnostika: sva 4 lažna
    odbijanja poslije Sol migracije bila su baš ovaj spoj not_engaged→reject."""
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math="3x + ??? = @@"))
    assert response["answer"] == "$x=5$"


def test_missing_evidence_for_supported_family_still_blocks():
    """IZUZETAK koji ostaje (tačno D35T-2 rupa): porodica izraza/jednačine
    BEZ ijednog dokaznog zapisa (prazan/naslovni visible_math) uz tvrdnju da
    je sve vidljivo — kontradikcija vlastitih tvrdnji, nikad objava."""
    response, _fake = run_image(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math=""))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE


def test_supported_with_mismatch_is_a_safe_rejection():
    response, _fake = run_image(make_quick_image_output(
        reply="$x=99$", task_type="linear_equation",
        requested_quantity="value_of_unknown", visible_math="3x+5=20"))
    assert response["answer"] == IMAGE_UNREADABLE_MESSAGE
    assert "99" not in response["answer"]


def test_call13_regression_heading_cannot_satisfy_verification():
    """Poziv 13: sa naslovom u polju, pogrešan rezultat bi ranije bio objavljen."""
    wrong = verify_image_answer(make_quick_image_output(
        reply="$x=99$", task_type="linear_equation", visible_math=HEADING_ONLY))
    assert not wrong.may_publish
    # Naslov se prepoznaje PRIJE pokušaja parsiranja, pa je kod „nema izvora“.
    assert wrong.code == "image_math_source_missing"
    assert (wrong.supported, wrong.engaged, wrong.verified) == (True, False, False)


def test_heading_only_source_rejects_for_expressions_too():
    result = verify_image_answer(make_quick_image_output(
        reply="$\\frac{5}{6}$", task_type="fraction_expression",
        visible_math="Izracunaj:"))
    assert not result.may_publish
    assert result.code == "image_math_source_missing"


def test_visible_problem_text_is_never_used_as_deterministic_evidence():
    """Čak i kad opisno polje SADRŽI ispravan izraz, ono ne smije zamijeniti
    namjensko polje — inače se vraća tiha rupa zbog koje polje i postoji."""
    result = verify_image_answer(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation",
        visible_math="", visible_problem_text="3x+5=20"))
    assert not result.may_publish
    assert result.code == "image_math_source_missing"


def test_unsupported_family_keeps_the_documented_generic_path():
    """Nepodržana porodica NE pada na kapiji provjere: prolazi dalje kroz opšte
    provjere (mathsafe/mathcheck/geometrycheck) uz istu kapiju čitljivosti."""
    response, _fake = run_image(make_quick_image_output(
        reply="Rezultat je $7$.", task_type="other"))
    assert response["answer"] == "Rezultat je $7$."


def test_missing_equality_sign_rejects():
    result = verify_image_answer(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation", visible_math="3x+5"))
    assert result.code == "image_equation_missing_equality"
    assert not result.may_publish


def test_multiple_unknowns_reject_safely():
    result = verify_image_answer(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation", visible_math="3x+2y=20"))
    assert result.code == "image_equation_unknown_ambiguous"
    assert not result.may_publish


def test_obscured_required_value_cannot_verify():
    """Poziv 14: desna strana prekrivena — polje ostaje prazno, nema objave."""
    result = verify_image_answer(make_quick_image_output(
        reply="$x=5$", task_type="linear_equation", visible_math="4x+7="))
    assert not result.may_publish


def test_substitution_uses_bounded_arithmetic_not_eval():
    import ast
    import inspect

    from matbot import imagecheck as module
    tree = ast.parse(inspect.getsource(module))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"eval", "exec", "compile", "__import__"}


def test_no_second_model_call_for_any_verification_outcome():
    for output in (
        make_quick_image_output(reply="$x=5$", task_type="linear_equation",
                                visible_math="3x+5=20"),
        make_quick_image_output(reply="$x=99$", task_type="linear_equation",
                                visible_math="3x+5=20"),
        make_quick_image_output(reply="$x=5$", task_type="linear_equation",
                                visible_math=HEADING_ONLY),
    ):
        _response, fake = run_image(output)
        assert len(fake.quick_calls) == 1


def test_visible_math_never_reaches_payload_history_or_logs(caplog):
    import logging
    with caplog.at_level(logging.DEBUG, logger="matbot.quick"):
        response, _fake = run_image(make_quick_image_output(
            reply="$x=99$", task_type="linear_equation",
            requested_quantity="value_of_unknown", visible_math="3x+5=20"))
    body = json.dumps(response, ensure_ascii=False)
    assert "visible_math" not in body
    assert "3x+5=20" not in body
    assert response.get("next_state", {}) == {} or "next_state" not in response
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "3x+5=20" not in logged
    assert "visible_math" not in logged
    assert "code=image_equation_substitution_failed" in logged   # bounded code only


def test_prompt_requires_visible_math_without_headings():
    _response, fake = run_image(make_quick_image_output(
        reply="$P=40\\,\\text{cm}^2$", **RECT_AREA))
    instructions, _input_text = fake.quick_calls[0]
    assert "'visible_math'" in instructions
    assert "NIKAD naslov" in instructions
    assert "ostavi ovo polje PRAZNO" in instructions
