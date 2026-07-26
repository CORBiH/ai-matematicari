"""Jedino mjesto koje poziva OpenAI. Jedan poziv po turnu, bez retryja.

Ne loguje: puni prompt, API ključ, skriveni reasoning. Loguje samo kategoriju
greške; latenciju i usage vraća pozivaocu.
"""
import time
from dataclasses import dataclass, field

from matbot import config
from matbot.schema import PracticeTurnOutput


class LLMError(Exception):
    """Bazna greška AI poziva; category ide u tehnički log."""
    category = "llm_error"


class LLMTimeout(LLMError):
    category = "llm_timeout"


class LLMInvalidOutput(LLMError):
    category = "llm_invalid_output"


class LLMUnavailable(LLMError):
    category = "llm_unavailable"


@dataclass
class LLMResult:
    output: PracticeTurnOutput
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)


class OpenAIPracticeLLM:
    """Adapter nad OpenAI Responses API + strict structured output (Pydantic).

    Sintaksa potvrđena prema instaliranom SDK-u (openai 2.41.1):
    client.responses.parse(model=..., instructions=..., input=...,
                           text_format=PracticeTurnOutput,
                           reasoning={"effort": ...}, max_output_tokens=...).
    """

    def __init__(self, model=None, timeout_s=None, reasoning_effort=None, max_output_tokens=None):
        self.model = model or config.OPENAI_MODEL_TEXT
        self.timeout_s = timeout_s or config.AI_TIMEOUT_S
        self.reasoning_effort = reasoning_effort or config.REASONING_EFFORT
        self.max_output_tokens = max_output_tokens or config.MAX_OUTPUT_TOKENS
        self._client = None  # lazy: ne traži API ključ dok se stvarno ne pozove

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            # max_retries=0: interaktivni put NIKAD ne pravi drugi (skriveni) poziv
            self._client = OpenAI(max_retries=0, timeout=self.timeout_s)
        return self._client

    def practice_turn(self, instructions: str, input_text: str) -> LLMResult:
        import openai

        client = self._get_client()
        t0 = time.monotonic()
        try:
            resp = client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=input_text,
                text_format=PracticeTurnOutput,
                reasoning={"effort": self.reasoning_effort},
                max_output_tokens=self.max_output_tokens,
                # Svaki turn šalje kompletan prompt iznova (instructions+input);
                # ne koristimo previous_response_id ni background mode, pa nema
                # razloga da OpenAI čuva ovaj odgovor na svojoj strani.
                store=False,
            )
        except openai.APITimeoutError as e:
            raise LLMTimeout(str(type(e).__name__)) from e
        except openai.LengthFinishReasonError as e:
            raise LLMInvalidOutput(str(type(e).__name__)) from e
        except Exception as e:
            raise LLMUnavailable(str(type(e).__name__)) from e

        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.output_parsed is None:
            raise LLMInvalidOutput("output_parsed is None")

        usage = {}
        if getattr(resp, "usage", None) is not None:
            u = resp.usage
            usage = {
                "input_tokens": getattr(u, "input_tokens", None),
                "output_tokens": getattr(u, "output_tokens", None),
            }
            details = getattr(u, "output_tokens_details", None)
            if details is not None:
                usage["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)

        return LLMResult(output=resp.output_parsed, latency_ms=latency_ms, usage=usage)
