# -*- coding: utf-8 -*-
"""Concept 5: **ResponseRenderer** — words for an already-frozen decision.

The renderer receives a decided state and returns Bosnian text for a child in
grades 6–9. It is structurally unable to change anything: it takes read-only
objects, returns a string, and never writes state.

OpenAI is optional here and strictly subordinate. It may rephrase a sentence the
engine already wrote. It is given the verdict as a fact, is forbidden the
expected answer when the task is unsolved, and its output is validated before
use — if it drifts, the deterministic text is kept. Nothing is ever parsed back
out of it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from matbot.bosnian import to_ijekavica
from matbot.minimal.grading import GradingResult
from matbot.minimal.intent import fold as _fold
from matbot.minimal.state import ActiveTask, SessionState

MAX_PHRASED_CHARS = 400


def _pick(pool: Sequence[str], seed: Any, salt: str = "") -> str:
    """Deterministic variety: stable for a turn, different across turns."""
    if not pool:
        return ""
    key = f"{salt}|{seed}".encode("utf-8", "replace")
    return pool[int(hashlib.sha256(key).hexdigest(), 16) % len(pool)]


_CORRECT = ("Tačno.", "Tako je.", "Tačno, dobro si to uradio.")
_PARTIAL = ("Dio je dobar, ali nije sve.", "Na dobrom si putu — još nije potpuno.",
            "Blizu si, nedostaje još jedan korak.")
_INCORRECT = ("Nije još tačno.", "Ovaj odgovor nije tačan.", "Nije tačno.")
_UNVERIFIED = ("Nisam siguran da sam dobro razumio tvoj odgovor.",)
_NEXT_INVITE = ("Želiš li još jedan zadatak?", "Idemo na sljedeći?",
                "Hoćeš još jedan zadatak?")

#: First-line, non-revealing help per skill. Never contains the answer.
_HINTS: dict[str, tuple[str, ...]] = {
    "fraction_expand": (
        "Proširivanje znači da brojnik i nazivnik množiš ISTIM brojem.",
        "Pogledaj koliko puta je novi nazivnik veći od starog — tim brojem množiš i brojnik.",
    ),
    "fraction_add_unlike": (
        "Kad su nazivnici različiti, prvo im nađi zajednički nazivnik.",
        "Svaki razlomak proširi do zajedničkog nazivnika, pa saberi samo brojnike.",
    ),
    "linear_equation": (
        "Prvo prebaci slobodan broj na drugu stranu, pa tek onda dijeli.",
        "Šta moraš oduzeti s obje strane da x ostane sam?",
    ),
    "divisibility": (
        "Broj je djeljiv sa 6 ako je djeljiv i sa 2 i sa 3.",
        "Za 2 gledaš posljednju cifru, a za 3 zbir cifara.",
    ),
    "prime_factorization": (
        "Kreni od najmanjeg prostog broja: probaj redom 2, 3, 5, 7.",
        "Dijeli dok možeš sa 2, pa pređi na sljedeći prosti broj.",
    ),
}


@dataclass(frozen=True)
class RenderContext:
    """Everything the renderer may know — all of it already decided."""
    state: SessionState
    intent: str
    grading: GradingResult | None = None
    task: ActiveTask | None = None
    hint_level: int = 0
    #: True only when the task is finished and the answer may be shown.
    may_reveal: bool = False
    unsupported_topic: str = ""

    @property
    def seed(self) -> str:
        return f"{self.state.session_id}|{self.state.turn_index}"


# --------------------------------------------------------------------------- #
# Deterministic text                                                           #
# --------------------------------------------------------------------------- #
def present_task(ctx: RenderContext) -> str:
    task = ctx.task
    if task is None:
        return ""
    opener = _pick(("Evo zadatka:", "Idemo na zadatak:", "Riješi ovaj zadatak:"),
                   ctx.seed, "present")
    return f"{opener}\n\n{task.question}"


def feedback(ctx: RenderContext) -> str:
    """Verdict, then what to do next. Never the answer unless solved."""
    g = ctx.grading
    if g is None:
        return ""
    if g.verdict == "correct":
        head = _pick(_CORRECT, ctx.seed, "correct")
        return f"{head} {_pick(_NEXT_INVITE, ctx.seed, 'invite')}"
    if g.verdict == "partial":
        head = _pick(_PARTIAL, ctx.seed, "partial")
    elif g.verdict == "unverified":
        head = _pick(_UNVERIFIED, ctx.seed, "unverified")
    else:
        head = _pick(_INCORRECT, ctx.seed, "incorrect")

    task = ctx.task
    if task is not None and not ctx.may_reveal:
        nudge = _hint_text(task.skill_id, task.hints_given)
        return f"{head} {nudge}"
    if ctx.may_reveal and task is not None and task.expected_display:
        return f"{head} Tačan odgovor je {task.expected_display}."
    return head


def _hint_text(skill_id: str, level: int) -> str:
    pool = _HINTS.get(skill_id) or ()
    if not pool:
        return "Pogledaj ponovo šta je dato, a šta se traži."
    return pool[min(max(level, 0), len(pool) - 1)]


def help_reply(ctx: RenderContext) -> str:
    """Progressive support that never reveals and never drops the task."""
    task = ctx.task
    if task is None:
        return ("Trenutno nemamo aktivan zadatak. Reci „daj mi zadatak” pa "
                "krećemo.")
    opener = _pick(("Nema problema.", "U redu, idemo polako.", "Hajde zajedno."),
                   ctx.seed, "help")
    hint = _hint_text(task.skill_id, task.hints_given)
    return f"{opener} {hint}\n\nZadatak je i dalje:\n{task.question}"


def unsupported_topic(ctx: RenderContext) -> str:
    """Honest refusal. Never a task from a different topic."""
    name = ctx.unsupported_topic or "ova tema"
    return (f"Za temu „{name}” još nemam zadatke koje mogu pouzdano provjeriti, "
            "pa ti ne bih dao zadatak iz druge teme. Izaberi neku od tema koje "
            "za sada podržavam.")


def other_turn(ctx: RenderContext) -> str:
    task = ctx.task
    if task is not None:
        return ("Nisam siguran da je to odgovor na zadatak. Napiši svoj odgovor, "
                "ili reci „pomozi” ako ti treba pomoć.\n\nZadatak je:\n"
                f"{task.question}")
    return ("Reci „daj mi zadatak” pa ću ti dati zadatak iz izabrane teme.")


# --------------------------------------------------------------------------- #
# Optional OpenAI phrasing — subordinate, validated, never authoritative       #
# --------------------------------------------------------------------------- #
#: Verdict words the model may not introduce. Written diacritic-free and always
#: matched against FOLDED text — "Netačno" must not slip past a pattern that
#: only spells "netacno".
_BANNED_IN_PHRASING = re.compile(
    r"\b(tacn[oa]|netacn\w*|pogresn\w*|bravo|odlicno|super)\b")

_PHRASING_SYSTEM = (
    "Ti si tutor matematike za dijete (11–14 godina) u Bosni i Hercegovini.\n"
    "Dobićeš GOTOVU poruku. Tvoj JEDINI zadatak je da je preformulišeš da zvuči "
    "toplije i prirodnije.\n"
    "STROGA PRAVILA:\n"
    "- NE mijenjaj značenje niti ocjenu tačnosti.\n"
    "- NE dodaj rješenje, rezultat ni novi zadatak.\n"
    "- NE postavljaj novo pitanje iz matematike.\n"
    "- Piši ijekavicom, kratko (najviše 2 rečenice).\n"
    "- Vrati SAMO preformulisanu poruku, bez objašnjenja."
)


def phrase_with_model(text: str, *, openai_chat: Callable | None, model: str,
                      timeout: float | None, allow_verdict_words: bool) -> str:
    """Let the model rephrase ``text``. Returns ``text`` unchanged on any doubt.

    The decision is already frozen; this only changes wording. Every failure
    mode — exception, empty reply, drift, added verdict, added math — falls back
    to the deterministic text.
    """
    if openai_chat is None or not text.strip():
        return text
    try:
        response = openai_chat(
            model,
            [{"role": "system", "content": _PHRASING_SYSTEM},
             {"role": "user", "content": text}],
            timeout=timeout, max_tokens=200,
        )
        candidate = (response.choices[0].message.content or "").strip()
    except Exception:
        return text
    if not candidate or len(candidate) > MAX_PHRASED_CHARS:
        return text
    # The model must not introduce a verdict where the engine did not state one,
    # and must not invent numbers (a smuggled answer or a new task).
    if not allow_verdict_words and _BANNED_IN_PHRASING.search(_fold(candidate)):
        return text
    if set(re.findall(r"\d+", candidate)) - set(re.findall(r"\d+", text)):
        return text
    return to_ijekavica(candidate)


def render(ctx: RenderContext, *, openai_chat: Callable | None = None,
           model: str = "", timeout: float | None = None) -> str:
    """Produce the student-facing message for this turn."""
    from matbot.minimal.intent import TurnIntent

    if ctx.unsupported_topic:
        return to_ijekavica(unsupported_topic(ctx))
    if ctx.intent == TurnIntent.NEW_TASK.value and ctx.task is not None \
            and ctx.grading is None:
        return to_ijekavica(present_task(ctx))
    if ctx.intent == TurnIntent.HELP.value:
        return to_ijekavica(help_reply(ctx))
    if ctx.grading is not None:
        text = feedback(ctx)
        # Only the short feedback line is ever handed to the model, and only
        # when the task is finished — while a task is open the deterministic
        # wording carries the hint, which must stay exact.
        if ctx.grading.verdict == "correct":
            text = phrase_with_model(text, openai_chat=openai_chat, model=model,
                                     timeout=timeout, allow_verdict_words=True)
        return to_ijekavica(text)
    return to_ijekavica(other_turn(ctx))
