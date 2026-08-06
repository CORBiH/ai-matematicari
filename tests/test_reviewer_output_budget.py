r"""Recenzent mora imati vlastiti, izmjeren budžet izlaznih tokena.

ŽIVI RELEASE GATE (commit 458d12a, scenario `harder_level2`, 4/14, 5 poziva):

    stage      : reviewer (DRUGI poziv)
    kategorija : llm_schema_parse_error
    izuzetak   : ValidationError — „Invalid JSON: EOF while parsing a string
                 at line 85 column 43“
    budžet     : max_output_tokens = 2500
    trajanje   : 25,6 s
    objavljeno : ne · stanje sačuvano · tačno dva poziva

DVA DEFEKTA, oba dokazana mjerenjem nad 347 uspješnih poziva iz živih artefakata
Faze 4E (F4E ×2 + A+B):

1. ZAJEDNIČKI BUDŽET. Tutor i Recenzent dijele `MAX_OUTPUT_TOKENS_PRACTICE`
   (2500), iako im se raspodjele bitno razlikuju:

       tutor      n=203  med=1190  p95=1671  MAX=1938   (0 % blizu granice)
       reviewer   n=144  med=1428  p95=1905  MAX=2395   (95,8 % budžeta)

   Najveći USPJEŠAN recenzentov izlaz je 2395 od 2500 — 105 tokena rezerve.
   Uzorak je k tome cenzurisan: svaki poziv koji je htio više od 2500 je
   presječen i u njemu ga nema. Tutor nikad nije prišao granici.

2. POGREŠNA KLASIFIKACIJA. `client.responses.parse` u openai 2.52.1 zove
   `parse_text` nad `output_text` BEZ obzira na `response.status`
   (openai/lib/_parsing/_responses.py). Presječen odgovor zato digne
   `pydantic.ValidationError` UNUTAR SDK poziva, prije nego što server uopšte
   vidi `status`/`incomplete_details`/`usage` — pa se iscrpljen budžet prijavi
   kao „šema ne valja“. Artefakt gate-a nema ni jedan od tih metapodataka.

Popravka NE dira Tutora, NE mijenja model, NE dodaje retry ni treći poziv.
"""
import json

import pydantic
import pytest

from matbot import config
from matbot.llm import (LLMIncompleteMaxOutputTokens, LLMInvalidOutput,
                        LLMSchemaParseError, OpenAIPracticeLLM)
from matbot.tutor.schema import ReviewerFinal, TutorDraft
from tests.conftest import make_reviewer_final, make_tutor_draft


class _FakeResponses:
    def __init__(self, result):
        self.result, self.calls, self.kwargs = result, 0, []

    def parse(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeClient:
    def __init__(self, result):
        self.responses = _FakeResponses(result)


class _FakeResponse:
    def __init__(self, parsed):
        self.status = "completed"
        self.output = []
        self.incomplete_details = None
        self.output_parsed = parsed
        self.output_text = "{}"
        self.usage = None
        self.error = None


def _llm(result):
    llm = OpenAIPracticeLLM()
    llm._client = _FakeClient(result)
    return llm


def _validation_error(payload_text):
    """Stvarna pydantic greška nad TAČNO onim tekstom koji bi model vratio."""
    try:
        ReviewerFinal.model_validate_json(payload_text)
    except pydantic.ValidationError as error:
        return error
    raise AssertionError("očekivana ValidationError nije podignuta")


def _complete_reviewer_json():
    return make_reviewer_final().model_dump_json()


# ---------------------------------------------------------------------------
# 1. ODVOJENI BUDŽETI
# ---------------------------------------------------------------------------

def test_reviewer_budget_exists_and_is_larger_than_the_tutor_budget():
    assert hasattr(config, "MAX_OUTPUT_TOKENS_REVIEWER")
    assert config.MAX_OUTPUT_TOKENS_REVIEWER > config.MAX_OUTPUT_TOKENS_PRACTICE


def test_reviewer_budget_covers_the_highest_measured_successful_output():
    """Najveći izmjeren uspješan recenzentov izlaz je 2395 tokena."""
    assert config.MAX_OUTPUT_TOKENS_REVIEWER >= 2395 * 1.25


def test_reviewer_budget_has_a_hard_finite_ceiling():
    assert config.MAX_OUTPUT_TOKENS_REVIEWER <= config.MAX_OUTPUT_TOKENS_HARD_CEILING


def test_tutor_call_still_uses_the_unchanged_practice_budget():
    llm = _llm(_FakeResponse(make_tutor_draft()))
    llm.tutor_turn("instrukcije", "ulaz")
    assert llm._client.responses.kwargs[0]["max_output_tokens"] == config.MAX_OUTPUT_TOKENS_PRACTICE


def test_reviewer_call_uses_the_reviewer_budget():
    llm = _llm(_FakeResponse(make_reviewer_final()))
    llm.reviewer_turn("instrukcije", "ulaz")
    assert llm._client.responses.kwargs[0]["max_output_tokens"] == config.MAX_OUTPUT_TOKENS_REVIEWER


def test_explain_and_quick_budgets_are_untouched():
    assert config.MAX_OUTPUT_TOKENS == 1200
    assert config.MAX_OUTPUT_TOKENS_PRACTICE == 2500


# ---------------------------------------------------------------------------
# 2. KLASIFIKACIJA NEUSPJEHA
# ---------------------------------------------------------------------------

def test_truncated_reviewer_json_is_classified_as_an_output_limit_cut():
    """Doslovan potpis pada gate-a: dokument prekinut usred stringa."""
    complete = _complete_reviewer_json()
    truncated = complete[: int(len(complete) * 0.6)]     # rez usred stringa
    llm = _llm(_validation_error(truncated))
    with pytest.raises(LLMIncompleteMaxOutputTokens) as error:
        llm.reviewer_turn("instrukcije", "ulaz")
    assert error.value.category == "llm_output_limit_truncated"


def test_malformed_but_complete_json_stays_a_separate_category():
    llm = _llm(_validation_error("{ ovo nije validan JSON }"))
    with pytest.raises(LLMSchemaParseError) as error:
        llm.reviewer_turn("instrukcije", "ulaz")
    assert error.value.category == "llm_malformed_json"


def test_valid_json_with_a_wrong_shape_is_a_schema_validation_failure():
    llm = _llm(_validation_error(json.dumps({"decision": "approve"})))
    with pytest.raises(LLMSchemaParseError) as error:
        llm.reviewer_turn("instrukcije", "ulaz")
    assert error.value.category == "llm_schema_parse_error"


def test_truncation_diagnostics_never_carry_the_raw_model_output():
    complete = _complete_reviewer_json()
    llm = _llm(_validation_error(complete[: int(len(complete) * 0.6)]))
    with pytest.raises(LLMInvalidOutput) as error:
        llm.reviewer_turn("instrukcije", "ulaz")
    blob = json.dumps(error.value.diagnostics, ensure_ascii=False, default=str)
    assert "independent_answer" not in blob
    assert "task_signature" not in blob
    # Budžet i veličina presjeka SU dozvoljena dijagnostika.
    assert error.value.diagnostics["max_output_tokens"] == config.MAX_OUTPUT_TOKENS_REVIEWER


def test_a_complete_package_at_the_old_boundary_still_parses():
    """Paket koji bi ranije bio presječen sada mora proći do validacije."""
    reviewer = make_reviewer_final()
    llm = _llm(_FakeResponse(reviewer))
    result = llm.reviewer_turn("instrukcije", "ulaz")
    assert isinstance(result.output, ReviewerFinal)
    assert result.output.final is not None


# ---------------------------------------------------------------------------
# 3. VALIDACIJA KONFIGURACIJE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["0", "-1", "-2500", "abc", "2500.5", "99999", "4001"])
def test_invalid_reviewer_budget_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("MATBOT_MAX_OUTPUT_TOKENS_REVIEWER", raw)
    with pytest.raises(config.ConfigurationError):
        config.reviewer_output_budget()


def test_empty_reviewer_budget_falls_back_to_the_validated_default(monkeypatch):
    monkeypatch.setenv("MATBOT_MAX_OUTPUT_TOKENS_REVIEWER", "")
    assert config.reviewer_output_budget() == config.MAX_OUTPUT_TOKENS_REVIEWER


def test_absent_reviewer_budget_keeps_the_default(monkeypatch):
    monkeypatch.delenv("MATBOT_MAX_OUTPUT_TOKENS_REVIEWER", raising=False)
    assert config.reviewer_output_budget() == config.MAX_OUTPUT_TOKENS_REVIEWER


@pytest.mark.parametrize("raw", ["3000", "3600", "4000"])
def test_a_reasonable_reviewer_budget_is_accepted(monkeypatch, raw):
    monkeypatch.setenv("MATBOT_MAX_OUTPUT_TOKENS_REVIEWER", raw)
    assert config.reviewer_output_budget() == int(raw)
