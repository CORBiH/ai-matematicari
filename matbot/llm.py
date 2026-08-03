"""Jedino mjesto koje poziva OpenAI. Jedan poziv po turnu, bez retryja.

Ne loguje: puni prompt, API ključ, skriveni reasoning. Loguje samo kategoriju
greške i OGRANIČENU dijagnostiku (vidi LLMError.diagnostics); latenciju i usage
vraća pozivaocu.

DIJAGNOSTIKA NEUSPJEHA (živi nalaz: dva od šest poziva na lekciji „Proširivanje
razlomaka“ pala su s jedinstvenom kategorijom `llm_invalid_output`, bez ijednog
podatka o tome ZAŠTO):

Ranije je SVAKI problem s izlazom padao u istu kategoriju, a `resp.status`,
`resp.incomplete_details`, `resp.error` i `usage` su se POTPUNO odbacivali —
uzrok je po dizajnu bio nesaznatljiv. Uz to, `openai.LengthFinishReasonError`
je koncept Chat Completions API-ja: `client.responses.parse()` ga NIKAD ne
baca (vidi openai/lib/_parsing/_responses.py:parse_response — samo sastavlja
ParsedResponse, bez provjere finish reasona), pa je taj except blok bio MRTAV
kod na ovom putu. Presjecanje budžeta izlaznih tokena se umjesto toga
manifestuje kao `status="incomplete"` +
`incomplete_details.reason="max_output_tokens"`, gdje `output` sadrži samo
reasoning stavku bez `message` stavke → `output_parsed is None`.

Zato se sada, PRIJE generičkog `output_parsed is None`, eksplicitno razlikuju:
  • llm_incomplete_max_output_tokens — potrošen budžet izlaznih tokena
  • llm_refusal                      — model odbio ili content filter
  • llm_empty_output                 — nema nikakvog tekstualnog izlaza
  • llm_schema_parse_error           — tekst postoji ali ne odgovara šemi
  • llm_timeout / llm_sdk_error      — mreža/transport, NE matematika
  • llm_invalid_output_unknown       — ništa od gore navedenog (ostaje rijetko)
"""
import re
import time
from dataclasses import dataclass, field

from matbot import config
from matbot.schema import (
    ExplainTurnOutput,
    PracticeTurnOutput,
    QuickImageTurnOutput,
    QuickTurnOutput,
)
from matbot.tutor.schema import ReviewerFinal, TutorDraft

# Maksimalna dužina bilo koje pojedinačne dijagnostičke vrijednosti u logu.
_DIAG_FIELD_LIMIT = 200

# Obrasci koji NIKAD ne smiju završiti u logu, čak ni kad ih SDK ubaci u tekst
# izuzetka (npr. ako neko u budućnosti proslijedi ključ u poruci greške).
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    # Namjerno se briše OSTATAK REDA nakon ključne riječi: vrijednost tajne može
    # biti razdvojena razmakom („Authorization: Bearer <token>“), pa je jedino
    # sigurno odbaciti sve do kraja reda. Pretjerano brisanje u dijagnostici je
    # prihvatljivo — procurjela tajna nije.
    re.compile(
        r"(?i)\b(?:bearer|authorization|api[_-]?key|apikey|access[_-]?token|"
        r"embed[_-]?token|session[_-]?token|token|secret|password)\b.*"
    ),
)


def _scrub(text):
    """Ukloni sve što liči na tajnu i skrati na sigurnu dužinu."""
    out = "" if text is None else str(text)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    if len(out) > _DIAG_FIELD_LIMIT:
        out = out[:_DIAG_FIELD_LIMIT] + "…"
    return out


_DIAG_LOG_ORDER = (
    "model", "reasoning_effort", "max_output_tokens", "status", "incomplete_reason",
    "response_error_code", "exception_class", "exception_summary", "refusal_summary",
    "instructions_chars", "input_chars", "output_text_chars", "has_message_item",
    # Slika: SAMO ograničeni metapodaci (format/dimenzije/veličina nakon
    # normalizacije). Nikad data URL, base64 ni bajtovi sadržaja.
    "image_format", "image_width", "image_height", "image_normalized_bytes",
    "parsed_ok", "usage", "latency_ms",
)


def failure_diagnostics_kv(err):
    """Vrati 'k=v k=v' string iz err.diagnostics za STRUKTURISAN interni log.

    Svaka vrijednost je scrub-ovana i dužinski ograničena. Sadrži isključivo
    bezbjedna polja (model/effort/budžet/status/usage/veličine) — nikad API
    ključ, auth ili embed token, puni prompt ni puni izlaz modela. NIKAD se
    ne šalje u browser."""
    diagnostics = getattr(err, "diagnostics", None) or {}
    parts = []
    for key in _DIAG_LOG_ORDER:
        if key in diagnostics and diagnostics[key] is not None:
            parts.append(f"{key}={_scrub(diagnostics[key])}")
    for key in sorted(set(diagnostics) - set(_DIAG_LOG_ORDER)):
        if diagnostics[key] is not None:
            parts.append(f"{key}={_scrub(diagnostics[key])}")
    return " ".join(parts)


class LLMError(Exception):
    """Bazna greška AI poziva; category ide u tehnički log.

    `diagnostics` je dict SIGURNIH, dužinski ograničenih vrijednosti (model,
    reasoning effort, budžet tokena, status odgovora, razlog nepotpunosti,
    usage, veličine ulaza/izlaza). NIKAD ne sadrži API ključ, auth/embed token,
    puni prompt ni puni izlaz modela — i NIKAD se ne šalje u browser."""

    category = "llm_error"

    def __init__(self, message="", diagnostics=None):
        super().__init__(message)
        self.diagnostics = dict(diagnostics or {})


class LLMTimeout(LLMError):
    category = "llm_timeout"


class LLMUnavailable(LLMError):
    """Mreža/transport/SDK greška — NIJE problem s matematikom ni sa šemom."""

    category = "llm_sdk_error"


class LLMInvalidOutput(LLMError):
    """Poziv je uspio, ali upotrebljiv strukturiran izlaz nije dobijen.
    Podklase niže nose TAČAN razlog; ova bazna ostaje za nepoznate slučajeve."""

    category = "llm_invalid_output_unknown"


class LLMIncompleteMaxOutputTokens(LLMInvalidOutput):
    category = "llm_incomplete_max_output_tokens"


class LLMEmptyOutput(LLMInvalidOutput):
    category = "llm_empty_output"


class LLMSchemaParseError(LLMInvalidOutput):
    category = "llm_schema_parse_error"


class LLMRefusal(LLMInvalidOutput):
    category = "llm_refusal"


@dataclass
class LLMResult:
    output: PracticeTurnOutput
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)


def _usage_dict(resp):
    usage = {}
    u = getattr(resp, "usage", None)
    if u is None:
        return usage
    usage["input_tokens"] = getattr(u, "input_tokens", None)
    usage["output_tokens"] = getattr(u, "output_tokens", None)
    details = getattr(u, "output_tokens_details", None)
    if details is not None:
        usage["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)
    return usage


def _first_refusal(resp):
    """Vrati tekst odbijanja ako ga model vrati kao 'refusal' sadržaj."""
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return getattr(content, "refusal", "") or "(bez teksta)"
    return None


def _has_message_item(resp):
    return any(getattr(i, "type", None) == "message" for i in (getattr(resp, "output", None) or []))


def _output_text(resp):
    try:
        return getattr(resp, "output_text", "") or ""
    except Exception:
        return ""


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
        # Practice je JEDINI mod koji generiše strukturiran zadatak (pitanje +
        # 4 opcije + interni metapodaci) i zato ima VEĆI budžet izlaznih tokena
        # od Quick — vidi config.MAX_OUTPUT_TOKENS_PRACTICE.
        return self._structured_turn(
            instructions, input_text, PracticeTurnOutput,
            max_output_tokens=config.MAX_OUTPUT_TOKENS_PRACTICE,
        )

    def tutor_turn(self, instructions: str, input_text: str) -> LLMResult:
        """PRVI od dva poziva univerzalnog Practice puta (nacrt).

        Model, reasoning effort, `store=False` i `max_retries=0` su identični
        ostalim putevima — razlikuje se samo strukturna šema."""
        return self._structured_turn(
            instructions, input_text, TutorDraft,
            max_output_tokens=config.MAX_OUTPUT_TOKENS_PRACTICE,
            model=config.TUTOR_MODEL,
        )

    def reviewer_turn(self, instructions: str, input_text: str) -> LLMResult:
        """DRUGI (i posljednji) poziv: nezavisna provjera + konačan payload.

        `config.REVIEWER_MODEL` je zaseban podesiv izbor da bi se recenzent
        kasnije mogao spustiti na jeftiniji model BEZ ijedne izmjene Practice
        logike. Podrazumijevano je isti model kao Tutor."""
        return self._structured_turn(
            instructions, input_text, ReviewerFinal,
            max_output_tokens=config.MAX_OUTPUT_TOKENS_PRACTICE,
            model=config.REVIEWER_MODEL,
        )

    def explain_turn(self, instructions: str, input_text: str) -> LLMResult:
        # Explain dozvoljava odgovor do 3.3x duži od Quick-a (vidi
        # config.MAX_OUTPUT_TOKENS_EXPLAIN, živi nalaz C-9) — zato NE dijeli
        # Quick-ov manji budžet, nego ima svoj, isti kao Practice.
        return self._structured_turn(
            instructions, input_text, ExplainTurnOutput,
            max_output_tokens=config.MAX_OUTPUT_TOKENS_EXPLAIN,
        )

    def quick_turn(self, instructions: str, input_text: str, image=None) -> LLMResult:
        """`image`: matbot.imageinput.ValidatedImage ili None.

        Slika je podržana SAMO na ovom (Quick/Rezultat) putu i mijenja oblik
        `input` polja i STRUKTURNU ŠEMU — model, reasoning effort, budžet
        tokena, `store=False` i `max_retries=0` ostaju identični tekstualnom
        pozivu, i dalje kao TAČNO JEDAN poziv modela.

        Šema je QuickImageTurnOutput (matbot/schema.py): živi nalaz D35-5/D35-6
        pokazao je da je gola `{reply}` šema činila sliku neprovjerljivom, pa
        model uz odgovor mora prijaviti i šta je stvarno vidio. Tekstualni
        poziv ostaje na QuickTurnOutput, bajt za bajt kao ranije."""
        text_format = QuickImageTurnOutput if image is not None else QuickTurnOutput
        return self._structured_turn(instructions, input_text, text_format, image=image)

    def _build_input(self, input_text, image):
        """Tekst → string (nepromijenjen put). Tekst + slika → jedna user
        poruka sa tačno jednom `input_text` i tačno jednom `input_image`
        stavkom (Responses API format, openai SDK 2.41.1).

        `detail="high"` je izabran namjerno: sitan matematički tekst (indeksi,
        eksponenti, razlomačke crte) se na `low` detalju gubi."""
        if image is None:
            return input_text
        return [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": input_text},
                {"type": "input_image", "image_url": image.data_url, "detail": "high"},
            ],
        }]

    def _structured_turn(self, instructions: str, input_text: str, text_format,
                          max_output_tokens=None, image=None, model=None) -> LLMResult:
        import openai
        import pydantic

        budget = max_output_tokens or self.max_output_tokens
        # `model` je per-poziv izbor (Tutor vs Reviewer). Kad izostane, važi
        # model adaptera — svi zatečeni putevi se time ne mijenjaju.
        active_model = model or self.model
        client = self._get_client()
        diag = {
            "model": active_model,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": budget,
            "instructions_chars": len(instructions or ""),
            "input_chars": len(input_text or ""),
            "parsed_ok": False,
        }
        if image is not None:
            # SAMO ograničeni metapodaci. Nikad data URL, base64, dužina data
            # URL-a ni ijedan bajt sadržaja — ni ovdje ni u logu grešaka.
            diag.update({
                "image_format": image.image_format,
                "image_width": image.width,
                "image_height": image.height,
                "image_normalized_bytes": image.normalized_bytes,
            })
        t0 = time.monotonic()
        try:
            resp = client.responses.parse(
                model=active_model,
                instructions=instructions,
                input=self._build_input(input_text, image),
                text_format=text_format,
                reasoning={"effort": self.reasoning_effort},
                max_output_tokens=budget,
                # Svaki turn šalje kompletan prompt iznova (instructions+input);
                # ne koristimo previous_response_id ni background mode, pa nema
                # razloga da OpenAI čuva ovaj odgovor na svojoj strani.
                store=False,
            )
        except openai.APITimeoutError as e:
            diag["latency_ms"] = int((time.monotonic() - t0) * 1000)
            diag["exception_class"] = type(e).__name__
            raise LLMTimeout(type(e).__name__, diagnostics=diag) from e
        except pydantic.ValidationError as e:
            # Tekst je stigao, ali ne odgovara strict šemi (parse_text →
            # model_validate_json baca ValidationError UNUTAR SDK poziva).
            diag["latency_ms"] = int((time.monotonic() - t0) * 1000)
            diag["exception_class"] = type(e).__name__
            diag["exception_summary"] = _scrub(e)
            diag["error_count"] = len(e.errors()) if hasattr(e, "errors") else None
            raise LLMSchemaParseError(type(e).__name__, diagnostics=diag) from e
        except openai.LengthFinishReasonError as e:
            # NAPOMENA: Responses API ovo NIKAD ne baca (vidi docstring modula).
            # Blok ostaje samo radi potpunosti ako SDK to jednom promijeni.
            diag["latency_ms"] = int((time.monotonic() - t0) * 1000)
            diag["exception_class"] = type(e).__name__
            raise LLMIncompleteMaxOutputTokens(type(e).__name__, diagnostics=diag) from e
        except Exception as e:
            diag["latency_ms"] = int((time.monotonic() - t0) * 1000)
            diag["exception_class"] = type(e).__name__
            diag["exception_summary"] = _scrub(e)
            raise LLMUnavailable(type(e).__name__, diagnostics=diag) from e

        latency_ms = int((time.monotonic() - t0) * 1000)
        usage = _usage_dict(resp)
        incomplete = getattr(resp, "incomplete_details", None)
        incomplete_reason = getattr(incomplete, "reason", None) if incomplete is not None else None
        resp_error = getattr(resp, "error", None)
        output_text = _output_text(resp)
        diag.update({
            "latency_ms": latency_ms,
            "status": getattr(resp, "status", None),
            "incomplete_reason": incomplete_reason,
            "response_error_code": getattr(resp_error, "code", None) if resp_error is not None else None,
            "output_text_chars": len(output_text),
            "has_message_item": _has_message_item(resp),
            "usage": usage,
        })

        refusal = _first_refusal(resp)
        if refusal is not None:
            diag["refusal_summary"] = _scrub(refusal)
            raise LLMRefusal("model refused", diagnostics=diag)

        if getattr(resp, "status", None) == "incomplete":
            if incomplete_reason == "max_output_tokens":
                raise LLMIncompleteMaxOutputTokens(
                    "incomplete: max_output_tokens", diagnostics=diag
                )
            if incomplete_reason == "content_filter":
                raise LLMRefusal("incomplete: content_filter", diagnostics=diag)
            raise LLMInvalidOutput(
                f"incomplete: {incomplete_reason or 'unknown'}", diagnostics=diag
            )

        if resp.output_parsed is None:
            if not output_text.strip():
                raise LLMEmptyOutput("no output text", diagnostics=diag)
            raise LLMSchemaParseError("output text not parsed to schema", diagnostics=diag)

        diag["parsed_ok"] = True
        return LLMResult(output=resp.output_parsed, latency_ms=latency_ms,
                         usage=usage, diagnostics=diag)
