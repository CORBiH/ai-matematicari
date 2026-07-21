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
from matbot.minimal import concept_facts, solution_facts
from matbot.minimal.grading import GradingResult
from matbot.minimal.intent import fold as _fold
from matbot.minimal.state import ActiveTask, SessionState

MAX_PHRASED_CHARS = 400

#: Skills whose concept questions are about fraction expansion.
_FRACTION_SKILLS = frozenset({"fraction_expand", "fraction_add_unlike"})


# --------------------------------------------------------------------------- #
# Language policy — ONE place, applied to every rendered string                 #
# --------------------------------------------------------------------------- #
#: Serbian Cyrillic → Bosnian Latin. Digraphs first so "њ" → "nj", not "n"+"j".
_CYRILLIC_MAP = {
    "Љ": "Lj", "Њ": "Nj", "Џ": "Dž", "љ": "lj", "њ": "nj", "џ": "dž",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Ђ": "Đ", "Е": "E",
    "Ж": "Ž", "З": "Z", "И": "I", "Ј": "J", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "Ћ": "Ć",
    "У": "U", "Ф": "F", "Х": "H", "Ц": "C", "Ч": "Č", "Ш": "Š",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "đ", "е": "e",
    "ж": "ž", "з": "z", "и": "i", "ј": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "ћ": "ć",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "č", "ш": "š",
}
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")

#: Gender-marked wording. Bosnian past participles carry gender, so any of these
#: means the text is guessing who the student is. Slash forms are equally bad —
#: they are still gender-marked, just twice.
_GENDERED_RE = re.compile(
    r"\b\w+(?:io|ao|la)\s*/\s*\w+(?:la|ila|ao|io)\b"          # riješio/riješila
    r"|\b(?:rije[sš]i|uradi|napravi|postavi|poku[sš]a|potrudi|"
    r"nau[cč]i|zapo[cč]e|zavr[sš]i|po[cč]e|misli|shvati|"
    # added 2026-07-21: these slipped past the first version of this list
    r"(?:po)?mno[zž]i|dob[i]|vid[i]|rad[i]|ra[cč]una|gleda|prova)(?:o|la)\b"
    # želio/željela and volio/voljela do not share a single stem
    r"|\b[zž]eli?o\b|\b[zž]eljela\b|\bvolio\b|\bvoljela\b"
    r"|\b(?:bio|bila|siguran|sigurna|spreman|spremna)\b",
    re.IGNORECASE)

#: Openers that read as sycophantic or odd after a routine correct answer.
_BAD_OPENER_RE = re.compile(r"^\s*(naravno|svakako|apsolutno)\b[,!.]?", re.IGNORECASE)

#: Praise. Rejected in model wording ALWAYS — a routine correct answer does not
#: deserve "Odlično!", and ``allow_verdict_words`` must not smuggle it through.
_PRAISE_RE = re.compile(r"\b(bravo|odlicno|super|savrseno|fantasticno|genijalno)\b")

#: Claims that the next task is under way. Feedback never creates a task, so a
#: phrase like "Idemo na sljedeći!" is simply false — production emitted exactly
#: that while the active task became null.
_IMPLIES_NEXT_TASK_RE = re.compile(
    r"\bidemo\s+(na\s+)?(sljedec\w*|dalje)\b|\bevo\s+(novog|sljedeceg)\b"
    r"|\bslijedi\s+(novi|sljedeci)\b|\bnastavljamo\s+sa\b|\bprelazimo\s+na\b")

#: Math must survive language normalization untouched.
_MATH_SPAN_RE = re.compile(r"\d+\s*/\s*\d+|[a-zA-Z]\s*=\s*[^\s,.;]+|\d+")


def to_latin(text: str) -> str:
    """Transliterate any Cyrillic to Bosnian Latin. Latin text is unchanged."""
    if not _CYRILLIC_RE.search(text or ""):
        return text
    return "".join(_CYRILLIC_MAP.get(ch, ch) for ch in text)


def has_cyrillic(text: Any) -> bool:
    return bool(_CYRILLIC_RE.search(str(text or "")))


def is_gendered(text: Any) -> bool:
    return bool(_GENDERED_RE.search(str(text or "")))


def enforce_language(text: str) -> str:
    """The single language gate every rendered string passes through.

    Transliterates Cyrillic and strips a sycophantic opener. It does NOT try to
    de-gender text — that is unreliable — so callers that accept model wording
    must reject gendered candidates outright (see ``phrase_with_model``).
    Mathematical spans are never altered: transliteration touches only Cyrillic
    letters, and the opener strip is anchored to the start of the string.
    """
    out = to_latin(str(text or ""))
    out = _BAD_OPENER_RE.sub("", out).lstrip()
    return out


def _pick(pool: Sequence[str], seed: Any, salt: str = "") -> str:
    """Deterministic variety: stable for a turn, different across turns."""
    if not pool:
        return ""
    key = f"{salt}|{seed}".encode("utf-8", "replace")
    return pool[int(hashlib.sha256(key).hexdigest(), 16) % len(pool)]


#: LANGUAGE POLICY (see ``enforce_language``): Bosnian Latin script only, and
#: never a gendered form. Bosnian marks gender on past participles ("uradio" /
#: "uradila"), so every phrase here is written to avoid them entirely — no
#: slash forms, no guessing. "Postupak je dobar." replaces "Dobro si postavio".
_CORRECT = ("Tačno.", "Tako je.", "Tačno, postupak je dobar.")
_PARTIAL = ("Dio je dobar, ali nije sve.", "Na dobrom si putu — još nije potpuno.",
            "Blizu si, nedostaje još jedan korak.")
_INCORRECT = ("Nije još tačno.", "Ovaj odgovor nije tačan.", "Nije tačno.")
#: "Nisam siguran" is masculine even though the TUTOR is speaking — the policy
#: is no gender marking anywhere, so this is phrased impersonally.
_UNVERIFIED = ("Ovo ne prepoznajem kao odgovor na zadatak.",)
#: A correct answer COMPLETES the task; it does not start the next one. So the
#: invite must be a question, never "Idemo na sljedeći!" — production said that
#: while active_task became null and no new task was sent.
_NEXT_INVITE = ("Želiš li novi zadatak?", "Želiš li još jedan zadatak?",
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
    #: The student's EXACT raw question, for CONCEPT_QUESTION turns.
    concept_question: str = ""
    #: The denominator a fraction_expand task requires, for precise feedback.
    target_denominator: int | None = None
    #: True when the student asked for harder/easier on a skill that has no
    #: objective bands. We say so rather than implying the task changed.
    difficulty_unsupported: bool = False

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
    if ctx.difficulty_unsupported:
        # Honest: this IS a new task, but not a harder or easier one. Claiming
        # otherwise is what made "Daj mi lakši zadatak" return a harder task.
        opener = ("Za ovu temu još ne mogu birati težinu, pa evo novog zadatka "
                  "iste težine:")
    return f"{opener}\n\n{task.question}"


def feedback(ctx: RenderContext) -> str:
    """Verdict, then what to do next. Never the answer unless solved."""
    g = ctx.grading
    if g is None:
        return ""
    if g.verdict == "correct":
        head = _pick(_CORRECT, ctx.seed, "correct")
        return f"{head} {_pick(_NEXT_INVITE, ctx.seed, 'invite')}"
    if g.detail == "ambiguous_final_answer":
        return ("U poruci vidim više brojeva. Koji je tvoj konačan odgovor?")
    if g.detail == "incorrect_target_denominator":
        # Name the requirement the answer missed, not a generic "not yet".
        needed = ctx.target_denominator
        target = f" nazivnik {needed}" if needed else " zadani nazivnik"
        return (f"Vrijednost razlomka je ista, ali zadatak traži{target}. "
                "Pomnoži brojnik i nazivnik istim brojem.")
    if g.verdict == "partial":
        head = _pick(_PARTIAL, ctx.seed, "partial")
    elif g.verdict == "unverified":
        head = _pick(_UNVERIFIED, ctx.seed, "unverified")
    else:
        head = _pick(_INCORRECT, ctx.seed, "incorrect")

    task = ctx.task
    if task is not None and not ctx.may_reveal:
        nudge = _hint_text(task.skill_id, task.hints_given, task.question)
        return f"{head} {nudge}"
    if ctx.may_reveal and task is not None and task.expected_display:
        return f"{head} Tačan odgovor je {task.expected_display}."
    return head


def _hint_text(skill_id: str, level: int, question: str = "") -> str:
    """One rung of the hint ladder.

    For skills with a deterministic ladder the rung is COMPUTED from the
    task's own numbers. The static pool is only a fallback: clamping to its
    last entry is what produced five identical hints in production.
    """
    if skill_id == "fraction_add_unlike" and question:
        facts = solution_facts.resolve_add_facts(question)
        if facts is not None:
            return solution_facts.add_hint(facts, level)
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
    hint = _hint_text(task.skill_id, task.hints_given, task.question)
    return f"{opener} {hint}\n\nZadatak je i dalje:\n{task.question}"


_CONCEPT_SYSTEM = (
    "Ti si tutor matematike za učenika 6. razreda u Bosni i Hercegovini.\n"
    "Odgovori KRATKO i jasno na pitanje učenika o gradivu.\n"
    "PRAVILA:\n"
    "- Piši bosanskom latinicom, ijekavicom.\n"
    "- Najviše 3–4 kratke rečenice.\n"
    "- Daj jedan konkretan primjer s brojevima.\n"
    "- NE postavljaj novi zadatak i NE traži od učenika da nešto riješi.\n"
    "- NE koristi rodno označene oblike (npr. „riješio”, „željela”).\n"
    "- Ne hvali pretjerano."
)

#: Said when a concept question arrives and no model is available.
_CONCEPT_FALLBACK = (
    "Dobro pitanje. Za sada ti na to ne mogu odgovoriti detaljno, ali mogu ti "
    "dati zadatak iz ove teme ili objasniti korak po korak kroz primjer."
)

#: Used when the model produced arithmetic for a question whose numbers could
#: NOT be verified. States the rule without computing anything.
_CONCEPT_NO_NUMBERS = (
    "Ne mogu pouzdano pročitati brojeve iz tvog pitanja, pa neću računati "
    "napamet. Pravilo je ovo: proširivanje znači da brojnik i nazivnik množiš "
    "istim cijelim brojem, pa se vrijednost razlomka ne mijenja. Napiši mi "
    "razlomak i nazivnik koji te zanimaju, pa ćemo to zajedno provjeriti."
)

#: Arithmetic in free-form model text: a fraction, an equation, or a product.
_CALCULATION_RE = re.compile(r"\d+\s*/\s*\d+|=\s*\d|\d\s*[·*x×]\s*\d")


def concept_answer(ctx: RenderContext, *, openai_chat: Callable | None,
                   model: str, timeout: float | None) -> str:
    """Answer a question ABOUT the maths. Creates nothing, grades nothing.

    The model is given the canonical topic, the resolved skill, the student's
    exact raw question and (when present) the active task — and its reply is put
    through the same language policy as every other string. On any failure the
    deterministic fallback is used.
    """
    question = (ctx.concept_question or "").strip()
    if not question:
        return _CONCEPT_FALLBACK

    # VERIFIED ARITHMETIC FIRST. When the question is a concrete
    # fraction-expansion case, the numbers come from the deterministic resolver
    # and the model may only rephrase them — production invented
    # "2 · (24/13) = 48/24" when it was free to calculate.
    # The expansion rule underlies BOTH fraction skills, so a concept
    # question about it is answered from verified facts either way.
    if (ctx.state.topic.skill_id or "") in _FRACTION_SKILLS:
        facts = concept_facts.resolve_expand_question(question)
        if facts is not None:
            text = concept_facts.explain(facts)
            # ``phrase_with_model`` rejects any candidate introducing a number
            # that is not already in the text, so a fabricated result cannot
            # survive the rephrase.
            return phrase_with_model(
                text, openai_chat=openai_chat, model=model, timeout=timeout,
                allow_verdict_words=False, require_same_numbers=True)

    if openai_chat is None:
        return _CONCEPT_FALLBACK
    topic = ctx.state.topic
    context = [
        f"Tema: {topic.title or topic.npp_id or 'nepoznata'}",
        f"Vještina: {topic.skill_id or 'nepoznata'}",
    ]
    if ctx.task is not None:
        # Present so the answer can stay relevant — the ANSWER is never included.
        context.append(f"Trenutni zadatak učenika: {ctx.task.question}")
    context.append(f"Pitanje učenika: {question}")
    try:
        response = openai_chat(
            model,
            [{"role": "system", "content": _CONCEPT_SYSTEM},
             {"role": "user", "content": "\n".join(context)}],
            timeout=timeout, max_tokens=320,
        )
        text = (response.choices[0].message.content or "").strip()
    except Exception:
        return _CONCEPT_FALLBACK
    if not text:
        return _CONCEPT_FALLBACK
    # Same policy as everything else: no Cyrillic, no gendered wording.
    text = to_latin(text)
    if is_gendered(text):
        return _CONCEPT_FALLBACK
    # No verified facts were available for this question, so the model must not
    # CALCULATE. Any arithmetic in a free-form answer is unverifiable and is
    # rejected outright — that is how "2 · (24/13) = 48/24" reached a student.
    if _CALCULATION_RE.search(text):
        return _CONCEPT_NO_NUMBERS
    return text[:900]


def clarification(ctx: RenderContext) -> str:
    """An unrecognised message NEVER creates a task; it asks what was meant."""
    if ctx.task is not None:
        # "Nisam siguran" is gender-marked; the policy forbids that even when the
        # TUTOR is the subject, so the clarification is phrased impersonally.
        return ("Nije mi jasno šta želiš. Možeš odgovoriti na zadatak, tražiti "
                "pomoć ili novi zadatak.\n\nZadatak je:\n" + ctx.task.question)
    return "Nije mi jasno šta želiš. Da li želiš novi zadatak ili objašnjenje?"


def solution_reply(ctx: RenderContext) -> str:
    """The full worked solution, on explicit request only.

    Deterministic end to end: the model never sees this, so it cannot
    fabricate a step. Ends by inviting an INDEPENDENT next task, because
    this one no longer measures the student.
    """
    task = ctx.task
    if task is None:
        return ("Trenutno nemamo aktivan zadatak. Reci „daj mi zadatak” pa "
                "krećemo.")
    facts = solution_facts.resolve_add_facts(task.question) \
        if task.skill_id == "fraction_add_unlike" else None
    if facts is None:
        if not task.expected_display:
            return "Za ovaj zadatak ti ne mogu pokazati postupak korak po korak."
        return ("Evo rješenja:\n\n" + f"{task.question}\n= "
                f"{task.expected_display}\n\n"
                "Pokušaj sada sam jedan sličan zadatak — reci „daj mi zadatak”.")
    return ("Evo cijelog postupka:\n\n" + solution_facts.add_solution(facts)
            + "\n\nSada probaj ti jedan sličan zadatak — reci "
            "„daj mi zadatak”.")


def unsupported_topic(ctx: RenderContext) -> str:
    """Honest refusal. Never a task from a different topic."""
    name = ctx.unsupported_topic or "ova tema"
    return (f"Za temu „{name}” još nemam zadatke koje mogu pouzdano provjeriti, "
            "pa ti ne bih dao zadatak iz druge teme. Izaberi neku od tema koje "
            "za sada podržavam.")


def other_turn(ctx: RenderContext) -> str:
    task = ctx.task
    if task is not None:
        return ("Ovo ne prepoznajem kao odgovor na zadatak. Napiši svoj odgovor, "
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
                      timeout: float | None, allow_verdict_words: bool,
                      require_same_numbers: bool = False) -> str:
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
    folded_candidate = _fold(candidate)
    if not allow_verdict_words and _BANNED_IN_PHRASING.search(folded_candidate):
        return text
    # These two hold even when verdict words are allowed: praise is never
    # warranted for a routine answer, and feedback never starts a task.
    if _PRAISE_RE.search(folded_candidate):
        return text
    if _IMPLIES_NEXT_TASK_RE.search(folded_candidate):
        return text
    # Language policy is enforced by REJECTION here, not by rewriting: a model
    # reply that guesses the student's gender ("riješio", "potrudila") or slips
    # into Cyrillic is discarded in favour of the deterministic text.
    if is_gendered(candidate) or has_cyrillic(candidate):
        return text
    if _BAD_OPENER_RE.match(candidate):
        return text
    original_numbers = set(re.findall(r"\d+", text))
    candidate_numbers = set(re.findall(r"\d+", candidate))
    if candidate_numbers - original_numbers:
        return text                     # invented a number
    if require_same_numbers and original_numbers - candidate_numbers:
        # Dropped a VERIFIED fact. For a conceptual explanation the numbers are
        # the answer, so a rephrase that loses them is worse than no rephrase.
        return text
    return to_ijekavica(candidate)


def _finish(text: str) -> str:
    """ijekavica + the language gate. The LAST thing every string passes."""
    return enforce_language(to_ijekavica(text))


def render(ctx: RenderContext, *, openai_chat: Callable | None = None,
           model: str = "", timeout: float | None = None) -> str:
    """Produce the student-facing message for this turn."""
    from matbot.minimal.intent import NEW_TASK_INTENTS, TurnIntent
    _NEW_TASK_INTENT_VALUES = {i.value for i in NEW_TASK_INTENTS}

    if ctx.unsupported_topic:
        return _finish(unsupported_topic(ctx))
    if ctx.intent in _NEW_TASK_INTENT_VALUES and ctx.task is not None \
            and ctx.grading is None:
        return _finish(present_task(ctx))
    if ctx.intent == TurnIntent.HELP.value:
        return _finish(help_reply(ctx))
    if ctx.intent == TurnIntent.SOLUTION_REQUEST.value:
        return _finish(solution_reply(ctx))
    if ctx.intent == TurnIntent.CONCEPT_QUESTION.value:
        text = concept_answer(ctx, openai_chat=openai_chat, model=model,
                              timeout=timeout)
        if ctx.task is not None:
            # Brief reminder of the open task — never its answer.
            text = f"{text}\n\nZadatak je i dalje:\n{ctx.task.question}"
        return _finish(text)
    if ctx.intent == "declined":
        return _finish("U redu. Kad poželiš, reci „daj mi zadatak”.")
    if ctx.intent == TurnIntent.OTHER.value:
        return _finish(clarification(ctx))
    if ctx.grading is not None:
        text = feedback(ctx)
        # Only the short feedback line is ever handed to the model, and only
        # when the task is finished — while a task is open the deterministic
        # wording carries the hint, which must stay exact.
        if ctx.grading.verdict == "correct":
            text = phrase_with_model(text, openai_chat=openai_chat, model=model,
                                     timeout=timeout, allow_verdict_words=True)
        return _finish(text)
    return _finish(other_turn(ctx))
