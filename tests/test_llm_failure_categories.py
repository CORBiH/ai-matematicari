"""Interne kategorije neuspjeha AI poziva (matbot/llm.py).

ŽIVI NALAZ koji je ovo iznudio: dva od šest poziva na lekciji „Proširivanje
razlomaka“ (6-04-005) pala su s JEDINSTVENOM kategorijom `llm_invalid_output`,
bez ijednog podatka o uzroku — `resp.status`, `resp.incomplete_details`,
`resp.error` i `usage` su se potpuno odbacivali.

Ovi testovi dokazuju MEHANIZAM (bez ijednog mrežnog poziva): presjecanje
budžeta izlaznih tokena u Responses API-ju NE baca `LengthFinishReasonError`
(to je koncept Chat Completions API-ja), nego vraća `status="incomplete"` /
`incomplete_details.reason="max_output_tokens"` s `output` listom koja nema
`message` stavku → `output_parsed is None`. Ranije je to bio JEDINI dohvatljiv
put do `llm_invalid_output` na ovom API-ju."""
import json
import logging

import pydantic
import pytest

from matbot import config
from matbot.llm import (
    LLMEmptyOutput, LLMError, LLMIncompleteMaxOutputTokens, LLMInvalidOutput,
    LLMRefusal, LLMSchemaParseError, LLMTimeout, LLMUnavailable,
    OpenAIPracticeLLM, failure_diagnostics_kv,
)
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.schema import PracticeTurnOutput


# --- lagani duck-typed dvojnici SDK odgovora (bez mreže) -------------------

class _Text:
    type = "output_text"

    def __init__(self, text):
        self.text = text


class _RefusalContent:
    type = "refusal"

    def __init__(self, refusal):
        self.refusal = refusal


class _Message:
    type = "message"

    def __init__(self, content):
        self.content = content


class _ReasoningItem:
    type = "reasoning"


class _Incomplete:
    def __init__(self, reason):
        self.reason = reason


class _Usage:
    def __init__(self, input_tokens, output_tokens, reasoning_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.output_tokens_details = type("D", (), {"reasoning_tokens": reasoning_tokens})()


class _FakeResponse:
    def __init__(self, status="completed", output=None, incomplete_reason=None,
                 output_parsed=None, output_text="", usage=None, error=None):
        self.status = status
        self.output = output or []
        self.incomplete_details = _Incomplete(incomplete_reason) if incomplete_reason else None
        self.output_parsed = output_parsed
        self.output_text = output_text
        self.usage = usage
        self.error = error


class _FakeResponses:
    def __init__(self, result):
        self.result = result
        self.calls = 0
        self.last_kwargs = None

    def parse(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _FakeClient:
    def __init__(self, result):
        self.responses = _FakeResponses(result)


def _llm(result):
    llm = OpenAIPracticeLLM()
    llm._client = _FakeClient(result)
    return llm


def _valid_parsed():
    return PracticeTurnOutput(reply="U redu.", evaluation=None, gave_hint=False, new_task=None)


# ---------------------------------------------------------------------------
# 1. Svaka interna kategorija neuspjeha
# ---------------------------------------------------------------------------

def test_category_incomplete_max_output_tokens():
    """(2) Potvrđena metapodacima: status=incomplete + reason=max_output_tokens.
    Ovo je TAČAN potpis dva pala živa poziva — samo reasoning stavka, bez
    `message` stavke, pa je output_parsed None."""
    resp = _FakeResponse(
        status="incomplete", incomplete_reason="max_output_tokens",
        output=[_ReasoningItem()], output_parsed=None, output_text="",
        usage=_Usage(6774, 2500, 2500),
    )
    llm = _llm(resp)
    with pytest.raises(LLMIncompleteMaxOutputTokens) as exc:
        llm.practice_turn("instrukcije", "ulaz")
    assert exc.value.category == "llm_incomplete_max_output_tokens"
    diag = exc.value.diagnostics
    assert diag["status"] == "incomplete"
    assert diag["incomplete_reason"] == "max_output_tokens"
    assert diag["usage"]["output_tokens"] == 2500
    assert diag["usage"]["reasoning_tokens"] == 2500
    assert diag["parsed_ok"] is False


def test_category_empty_output():
    """(3) Nema nikakvog tekstualnog izlaza — završeno, ali prazno."""
    resp = _FakeResponse(status="completed", output=[], output_parsed=None, output_text="")
    with pytest.raises(LLMEmptyOutput) as exc:
        _llm(resp).practice_turn("i", "u")
    assert exc.value.category == "llm_empty_output"
    assert exc.value.diagnostics["output_text_chars"] == 0


def test_category_partial_structured_output_is_schema_parse_error():
    """(4) Djelimičan strukturiran izlaz: tekst POSTOJI ali nije parsiran u šemu."""
    resp = _FakeResponse(
        status="completed", output=[_Message([_Text('{"reply": "poc')])],
        output_parsed=None, output_text='{"reply": "poc',
    )
    with pytest.raises(LLMSchemaParseError) as exc:
        _llm(resp).practice_turn("i", "u")
    assert exc.value.category == "llm_schema_parse_error"
    assert exc.value.diagnostics["output_text_chars"] > 0
    assert exc.value.diagnostics["has_message_item"] is True


def test_category_invalid_json_schema_validation_error():
    """(5) Nevalidan JSON/šema: SDK baca pydantic.ValidationError iz parse_text."""
    class _Bad(pydantic.BaseModel):
        x: int

    try:
        _Bad.model_validate_json("{not json")
    except pydantic.ValidationError as err:
        validation_error = err

    with pytest.raises(LLMSchemaParseError) as exc:
        _llm(validation_error).practice_turn("i", "u")
    assert exc.value.category == "llm_schema_parse_error"
    assert exc.value.diagnostics["exception_class"] == "ValidationError"


def test_category_refusal():
    """(6) Model je odbio — refusal sadržaj umjesto output_text."""
    resp = _FakeResponse(
        status="completed", output=[_Message([_RefusalContent("Ne mogu pomoći.")])],
        output_parsed=None, output_text="",
    )
    with pytest.raises(LLMRefusal) as exc:
        _llm(resp).practice_turn("i", "u")
    assert exc.value.category == "llm_refusal"
    assert "Ne mogu" in exc.value.diagnostics["refusal_summary"]


def test_category_refusal_via_content_filter():
    resp = _FakeResponse(status="incomplete", incomplete_reason="content_filter",
                         output=[_ReasoningItem()], output_parsed=None)
    with pytest.raises(LLMRefusal) as exc:
        _llm(resp).practice_turn("i", "u")
    assert exc.value.category == "llm_refusal"


def test_category_timeout():
    """(7) Timeout ostaje POSEBNA kategorija — nikad „nevalidna matematika“."""
    import openai

    with pytest.raises(LLMTimeout) as exc:
        _llm(openai.APITimeoutError(request=None)).practice_turn("i", "u")
    assert exc.value.category == "llm_timeout"
    assert exc.value.category != "llm_invalid_output_unknown"


def test_category_generic_sdk_error():
    """(8) Generička SDK/transport greška."""
    with pytest.raises(LLMUnavailable) as exc:
        _llm(RuntimeError("connection reset")).practice_turn("i", "u")
    assert exc.value.category == "llm_sdk_error"
    assert exc.value.diagnostics["exception_class"] == "RuntimeError"


def test_category_unknown_incomplete_reason_falls_back():
    resp = _FakeResponse(status="incomplete", incomplete_reason=None,
                         output=[_ReasoningItem()], output_parsed=None)
    with pytest.raises(LLMInvalidOutput) as exc:
        _llm(resp).practice_turn("i", "u")
    assert exc.value.category == "llm_invalid_output_unknown"


def test_previous_behavior_collapsed_all_of_these_into_one_category():
    """Regresija na PRIJAŠNJE ponašanje: sve gornje kategorije su nekad bile
    jedna te ista `llm_invalid_output`. Sada moraju biti međusobno različite."""
    categories = {
        LLMIncompleteMaxOutputTokens.category, LLMEmptyOutput.category,
        LLMSchemaParseError.category, LLMRefusal.category,
        LLMTimeout.category, LLMUnavailable.category, LLMInvalidOutput.category,
    }
    assert len(categories) == 7


# ---------------------------------------------------------------------------
# 16. Uspješno generisanje ostaje nepromijenjeno
# ---------------------------------------------------------------------------

def test_successful_call_unchanged_and_returns_usage_and_diagnostics():
    resp = _FakeResponse(status="completed", output=[_Message([_Text("{}")])],
                         output_parsed=_valid_parsed(), output_text="{}",
                         usage=_Usage(100, 200, 50))
    result = _llm(resp).practice_turn("i", "u")
    assert result.output.reply == "U redu."
    assert result.usage["output_tokens"] == 200
    assert result.diagnostics["parsed_ok"] is True


def test_practice_uses_dedicated_larger_output_budget():
    """Practice (jedini strukturirani generator) dobija svoj budžet; Explain
    i Quick ostaju na globalnom."""
    resp = _FakeResponse(status="completed", output=[_Message([_Text("{}")])],
                         output_parsed=_valid_parsed(), output_text="{}")
    llm = _llm(resp)
    llm.practice_turn("i", "u")
    assert llm._client.responses.last_kwargs["max_output_tokens"] == config.MAX_OUTPUT_TOKENS_PRACTICE
    assert config.MAX_OUTPUT_TOKENS_PRACTICE > config.MAX_OUTPUT_TOKENS
    assert config.MAX_OUTPUT_TOKENS_PRACTICE <= config.MAX_OUTPUT_TOKENS_HARD_CEILING


def test_explain_uses_dedicated_output_budget():
    """Faza D (docs/CURRENT_STATE.md C-9): Explain VIŠE ne dijeli Quick-ov
    manji globalni budžet — dozvoljava odgovor 3.3x duži od Quick-a
    (MAX_EXPLAIN_REPLY_CHARS=4000 naspram MAX_QUICK_REPLY_CHARS=1200) i mora
    imati proporcionalno veći budžet izlaznih tokena, isto obrazloženje kao
    Practice (reasoning + vidljivi izlaz dijele isti max_output_tokens kod
    reasoning modela)."""
    from matbot.schema import ExplainTurnOutput

    resp = _FakeResponse(status="completed", output=[_Message([_Text("{}")])],
                         output_parsed=ExplainTurnOutput(reply="x"), output_text="{}")
    llm = _llm(resp)
    llm.explain_turn("i", "u")
    assert llm._client.responses.last_kwargs["max_output_tokens"] == config.MAX_OUTPUT_TOKENS_EXPLAIN
    assert config.MAX_OUTPUT_TOKENS_EXPLAIN > config.MAX_OUTPUT_TOKENS
    assert config.MAX_OUTPUT_TOKENS_EXPLAIN <= config.MAX_OUTPUT_TOKENS_HARD_CEILING


def test_quick_budget_unchanged():
    """Quick vraća kratak rezultat (MAX_QUICK_REPLY_CHARS=1200) i nema
    izmjeren problem — ostaje na globalnom MAX_OUTPUT_TOKENS, netaknut Fazom D."""
    from matbot.schema import QuickTurnOutput

    resp = _FakeResponse(status="completed", output=[_Message([_Text("{}")])],
                         output_parsed=QuickTurnOutput(reply="x"), output_text="{}")
    llm = _llm(resp)
    llm.quick_turn("i", "u")
    assert llm._client.responses.last_kwargs["max_output_tokens"] == config.MAX_OUTPUT_TOKENS


def test_practice_budget_unchanged_by_explain_dedicated_budget():
    """Regresija: uvođenje MAX_OUTPUT_TOKENS_EXPLAIN ne smije dirati Practice."""
    resp = _FakeResponse(status="completed", output=[_Message([_Text("{}")])],
                         output_parsed=_valid_parsed(), output_text="{}")
    llm = _llm(resp)
    llm.practice_turn("i", "u")
    assert llm._client.responses.last_kwargs["max_output_tokens"] == config.MAX_OUTPUT_TOKENS_PRACTICE


def test_explain_budget_hard_ceiling_invariant():
    """Statička provjera istog obrasca kao Practice: bez obzira na env
    vrijednost, MAX_OUTPUT_TOKENS_EXPLAIN NIKAD ne smije biti veći od
    MAX_OUTPUT_TOKENS_HARD_CEILING — vidi matbot/config.py
    (MAX_OUTPUT_TOKENS_EXPLAIN = min(_int_env(...), MAX_OUTPUT_TOKENS_HARD_CEILING))."""
    assert config.MAX_OUTPUT_TOKENS_EXPLAIN <= config.MAX_OUTPUT_TOKENS_HARD_CEILING


def test_int_env_falls_back_safely_on_invalid_value(monkeypatch):
    """Nevaljana env vrijednost (ne-broj) NIKAD ne smije srušiti aplikaciju —
    _int_env pada nazad na dati default. Ovo je isti mehanizam koji
    MAX_OUTPUT_TOKENS_EXPLAIN (i MAX_OUTPUT_TOKENS_PRACTICE) koriste, pa ova
    provjera direktno pokriva "nevažeći override" zahtjev bez potrebe da se
    cio matbot.config modul ponovo učitava (rizično dijeljeno stanje kroz
    ostatak test sesije)."""
    monkeypatch.setenv("MATBOT_MAX_OUTPUT_TOKENS_EXPLAIN", "nije-broj")
    assert config._int_env("MATBOT_MAX_OUTPUT_TOKENS_EXPLAIN", 2500) == 2500


def test_exactly_one_sdk_call_per_turn_on_failure():
    """(14) Tačno JEDAN poziv — nikad skriveni retry, ni na neuspjeh."""
    resp = _FakeResponse(status="incomplete", incomplete_reason="max_output_tokens",
                         output=[_ReasoningItem()], output_parsed=None)
    llm = _llm(resp)
    with pytest.raises(LLMIncompleteMaxOutputTokens):
        llm.practice_turn("i", "u")
    assert llm._client.responses.calls == 1


def test_client_configured_without_sdk_retries():
    """Jedan poziv po turnu je arhitektonska garancija: max_retries=0."""
    import inspect

    src = inspect.getsource(OpenAIPracticeLLM._get_client)
    assert "max_retries=0" in src


# ---------------------------------------------------------------------------
# 11/12. Dijagnostika je ograničena i bez tajni
# ---------------------------------------------------------------------------

def test_diagnostics_never_contain_secrets():
    """(12) Čak i kad SDK ubaci ključ/token u tekst izuzetka, log ga ne smije nositi."""
    leaky = RuntimeError("auth failed for sk-proj-ABCDEF1234567890 Authorization: Bearer zzzz")
    with pytest.raises(LLMUnavailable) as exc:
        _llm(leaky).practice_turn("i", "u")
    line = failure_diagnostics_kv(exc.value)
    assert "sk-proj-ABCDEF1234567890" not in line
    assert "zzzz" not in line
    assert "[REDACTED]" in line


def test_diagnostics_values_are_length_bounded():
    """(11) Nijedna dijagnostička vrijednost nije neograničeno duga."""
    huge = RuntimeError("E" * 5000)
    with pytest.raises(LLMUnavailable) as exc:
        _llm(huge).practice_turn("i", "u")
    line = failure_diagnostics_kv(exc.value)
    assert "E" * 5000 not in line
    assert len(line) < 2000


def test_diagnostics_never_contain_full_prompt_or_output():
    resp = _FakeResponse(status="completed", output=[_Message([_Text("X" * 3000)])],
                         output_parsed=None, output_text="X" * 3000)
    with pytest.raises(LLMSchemaParseError) as exc:
        _llm(resp).practice_turn("TAJNE-INSTRUKCIJE" * 50, "TAJNI-ULAZ" * 50)
    line = failure_diagnostics_kv(exc.value)
    assert "TAJNE-INSTRUKCIJE" not in line
    assert "TAJNI-ULAZ" not in line
    assert "X" * 3000 not in line
    # ali VELIČINE jesu prisutne (to je korisna, bezbjedna dijagnostika)
    assert "instructions_chars=" in line
    assert "input_chars=" in line
    assert "output_text_chars=" in line


# ---------------------------------------------------------------------------
# 9/10/13/15. Ponašanje prema browseru i sesiji ostaje nepromijenjeno
# ---------------------------------------------------------------------------

class _FailingLLM:
    """Practice-nivo dvojnik koji baca konkretnu LLM grešku."""

    def __init__(self, error):
        self.error = error
        self.call_count = 0

    def practice_turn(self, instructions, input_text):
        self.call_count += 1
        raise self.error


def _payload(**kw):
    base = {"session_id": "sess-llm-fail", "grade": 6, "selected_topic": "6-04-007",
            "selected_oblast": "", "student_message": "Daj zadatak.", "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "", "selected_option_id": "", "client_turn_id": ""}
    base.update(kw)
    return base


@pytest.mark.parametrize("error", [
    LLMIncompleteMaxOutputTokens("t", diagnostics={"status": "incomplete",
                                                   "incomplete_reason": "max_output_tokens"}),
    LLMEmptyOutput("e", diagnostics={"output_text_chars": 0}),
    LLMSchemaParseError("s", diagnostics={"exception_class": "ValidationError"}),
    LLMRefusal("r", diagnostics={"refusal_summary": "ne mogu"}),
    LLMTimeout("t", diagnostics={"exception_class": "APITimeoutError"}),
    LLMUnavailable("u", diagnostics={"exception_class": "RuntimeError"}),
])
def test_safe_browser_response_identical_for_every_failure_category(error):
    """(9) Sigurna poruka učeniku je NEPROMIJENJENA za svaku internu kategoriju."""
    store, llm = SessionStore(), _FailingLLM(error)
    r = run_practice_turn(store, llm, _payload())
    assert r == {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    assert llm.call_count == 1  # (14) tačno jedan poziv


@pytest.mark.parametrize("error", [
    LLMIncompleteMaxOutputTokens("t", diagnostics={"status": "incomplete",
                                                   "incomplete_reason": "max_output_tokens",
                                                   "model": "gpt-5-mini"}),
    LLMTimeout("t", diagnostics={"exception_class": "APITimeoutError"}),
])
def test_no_internal_diagnostics_leak_to_browser(error):
    """(10) Nijedno dijagnostičko polje ne smije se pojaviti u odgovoru."""
    store, llm = SessionStore(), _FailingLLM(error)
    r = run_practice_turn(store, llm, _payload())
    raw = json.dumps(r, ensure_ascii=False)
    for forbidden in ("incomplete_reason", "max_output_tokens", "status",
                      "diagnostics", "exception_class", "reasoning_tokens",
                      "gpt-5-mini", "model"):
        assert forbidden not in raw


def test_failed_generation_does_not_mutate_practice_state():
    """(13) Neuspjeh NE mijenja stanje sesije (nema aktivnog zadatka)."""
    store, llm = SessionStore(), _FailingLLM(
        LLMIncompleteMaxOutputTokens("t", diagnostics={"incomplete_reason": "max_output_tokens"})
    )
    before = store.peek("sess-llm-fail")
    run_practice_turn(store, llm, _payload())
    after = store.peek("sess-llm-fail")
    assert after == before
    assert after is None or not after.get("current_task")


def test_structured_failure_log_line_is_emitted_with_category_and_topic(caplog):
    """Strukturisan, pretraživ interni log — sadrži kategoriju, temu, porodicu
    i bezbjednu dijagnostiku (mode/model/status/usage), ali nikad tajne."""
    store = SessionStore()
    llm = _FailingLLM(LLMIncompleteMaxOutputTokens("t", diagnostics={
        "model": "gpt-5-mini", "reasoning_effort": "low", "max_output_tokens": 2500,
        "status": "incomplete", "incomplete_reason": "max_output_tokens",
        "instructions_chars": 16819, "input_chars": 1911,
        "usage": {"input_tokens": 6774, "output_tokens": 2500, "reasoning_tokens": 2500},
    }))
    with caplog.at_level(logging.WARNING, logger="matbot.practice"):
        run_practice_turn(store, llm, _payload())
    line = "\n".join(rec.message for rec in caplog.records)
    assert "category=llm_incomplete_max_output_tokens" in line
    assert "topic=6-04-007" in line
    assert "mode=practice" in line
    assert "incomplete_reason=max_output_tokens" in line
    assert "max_output_tokens=2500" in line
    assert "family=" in line
    for forbidden in ("sk-", "Bearer", "api_key", "OPENAI_API_KEY"):
        assert forbidden not in line


def test_retry_remains_user_triggered_only():
    """(15) Nema skrivenog automatskog ponovnog poziva — frontend nudi dugme
    „Pokušaj ponovo“ koje šalje isti zahtjev SAMO na klik."""
    html = (__import__("pathlib").Path(__file__).resolve().parent.parent
            / "templates" / "index.html").read_text(encoding="utf-8")
    assert "Pokušaj ponovo" in html
    click_handler = html.index("function onRetryClick")
    assert html.index("retryNewTaskRequest(freshPayload)") > click_handler
