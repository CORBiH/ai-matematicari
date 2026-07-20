"""Engine V2 — Phase 4: Stable Exam Engine (a self-contained state machine).

A deterministic ``kontrolni`` (exam) that is entirely owned in state — the model
is NOT called, so GPT can never invent items, drift the mode, or reopen a
completed exam. Items are code-generated from the deterministically-checkable
skills and pre-validated (each has a computable expected answer), so no
unvalidated / ungradeable / off-topic item can ever become active. Grading is
deterministic (``answer_checker``), and each student answer is attributed to
exactly ONE item (the current one) — no answer bleed.

State machine:  (no exam) --start--> active --grade all items--> completed(terminal)
  * active   : present items; one answer per turn -> current item; help = hint,
               no reveal, no advance, no new exam.
  * completed: terminal. Explaining an item never reopens it; only an explicit
               "novi kontrolni" starts a fresh exam.

Kept SEPARATE from the Practice Step Engine: this module owns whole exam turns via
a top-level short-circuit, so the two engines never run in the same turn.

Flag ``MATBOT_ENGINE_V2_EXAM`` = ``off`` (default) | ``on``. Off → legacy exam
path (rollback), unchanged.
"""
from __future__ import annotations

import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any

from matbot.answer_checker import check_practice_answer, derive_expected, _fmt_expected
from matbot.grading_guard import authoritative_verdict
from matbot import render, task_templates, turn_intent

ENGINE = "v2"
DEFAULT_ITEM_COUNT = 3

_POSITIVE = {
    "correct", "correct_equivalent_form", "correct_missing_notation",
    "correct_missing_unit",
}


def exam_mode() -> str:
    """``off`` (default) | ``on`` | ``drain``.

    * ``on``    — V2 owns new AND existing V2 exams.
    * ``drain`` — V2 finishes exams it already started, but starts NO new ones
                  (new exam requests fall through to the legacy path). Used to
                  roll back safely without stranding a student mid-exam.
    * ``off``   — legacy exam path; any stale V2 exam state is stripped before
                  the legacy normalizer can see it (never corrupted/reopened).
    """
    raw = (os.getenv("MATBOT_ENGINE_V2_EXAM") or "off").strip().lower()
    return raw if raw in ("on", "drain") else "off"


def exam_enabled() -> bool:
    """True when the V2 engine may handle *some* exam turn (on or drain)."""
    return exam_mode() in ("on", "drain")


def accepts_new_exams() -> bool:
    return exam_mode() == "on"


def _fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


# Help / skip / submit / answer-signal detection now lives in ``turn_intent`` —
# the exam used to keep private copies of all four, which is exactly how it came
# to disagree with the practice flow about the same sentence.
_NEW_EXAM_RE = re.compile(r"\bnov\w*\s+(kontroln\w*|test\w*|ispit\w*)\b|\bjo[sš]\s+jedan\s+kontroln\w*")
_EXPLAIN_RE = re.compile(
    r"\b(objasn\w*|obrazlo\w*|za[sš]to|gdje\s+sam\s+pogrije[sš]|gdje\s+je\s+gre[sš]|poka[zž]i\s+rje[sš])\w*"
)
_ORDINALS = {
    "prvi": 1, "prva": 1, "prvo": 1, "1": 1, "1.": 1,
    "drugi": 2, "druga": 2, "drugo": 2, "2": 2, "2.": 2,
    "treci": 3, "treca": 3, "trece": 3, "3": 3, "3.": 3,
    "cetvrti": 4, "4": 4, "4.": 4, "peti": 5, "5": 5, "5.": 5,
}


# --------------------------------------------------------------------------- #
# Deterministic, pre-validated item pool (grade 6 supported skills)            #
# --------------------------------------------------------------------------- #
_ITEM_POOL = [
    "Izračunaj: 3/4 + 1/4.",
    "Riješi jednačinu: 2x + 3 = 11.",
    "Koliko je 20% od 50?",
    "Odredi NZD(12, 18).",
    "Izračunaj: 2/3 + 1/6.",
    "Rastavi 18 na proste faktore.",
    "Pretvori 3 m u cm.",
    "Izračunaj: 5/6 - 1/3.",
    "Riješi jednačinu: 3x - 5 = 7.",
    "Koliko je 25% od 80?",
]


def _expected_display(question: str) -> str:
    exp = derive_expected(question)
    if exp is None:
        return ""
    disp = str(getattr(exp, "expected_display", "") or "")
    return disp or _fmt_expected(exp)


def _validated_pool() -> list[str]:
    """Only items with a computable expected answer are eligible (pre-validation)."""
    return [q for q in _ITEM_POOL if derive_expected(q) is not None]


def _select_items(seed: str, count: int) -> list[str]:
    """Deterministic, stable selection from the validated pool for a session."""
    pool = _validated_pool()
    if not pool:
        return []
    n = max(1, min(count, len(pool)))
    h = abs(hash(("matbot-exam", seed))) % len(pool)
    return [pool[(h + i) % len(pool)] for i in range(n)]


# --------------------------------------------------------------------------- #
# Data model                                                                   #
# --------------------------------------------------------------------------- #
@dataclass
class ExamItem:
    item_id: str
    question: str
    expected_display: str = ""
    status: str = "unanswered"          # unanswered | graded | skipped
    student_answer: str | None = None   # ONLY a real answer, never a help request
    verdict: str | None = None          # correct | incorrect | unverified | skipped
    correct: bool | None = None
    help_count: int = 0                 # progressive, non-revealing support

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id, "question": self.question,
            "expected_display": self.expected_display, "status": self.status,
            "student_answer": self.student_answer, "verdict": self.verdict,
            "correct": self.correct, "help_count": self.help_count,
        }


@dataclass
class ExamState:
    exam_id: str
    exam_status: str                    # active | completed
    current_index: int | None
    items: list[ExamItem] = field(default_factory=list)
    oblast: str = ""
    tema: str = ""
    topic_covered: bool = True          # False = generic fallback for a requested topic
    seed: str = ""                      # stable wording seed (never a secret)
    grade: Any = 6

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def score(self) -> int:
        return sum(1 for it in self.items if it.correct)

    def to_dict(self) -> dict:
        return {
            "engine": ENGINE,
            "exam_id": self.exam_id,
            "mode": "exam",
            "exam_status": self.exam_status,
            "current_item_index": self.current_index,
            "expected_user_action": "none" if self.exam_status == "completed" else "answer_task",
            "oblast": self.oblast,
            "tema": self.tema,
            "topic_covered": self.topic_covered,
            "seed": self.seed,
            "grade": self.grade,
            "items": [it.to_dict() for it in self.items],
        }


def is_v2_exam(raw: Any) -> bool:
    return isinstance(raw, dict) and str(raw.get("engine") or "") == ENGINE


def load_state(raw: Any) -> ExamState | None:
    if not is_v2_exam(raw):
        return None
    items_raw = raw.get("items") if isinstance(raw.get("items"), list) else []
    items: list[ExamItem] = []
    for idx, it in enumerate(items_raw[:20]):
        if not isinstance(it, dict):
            continue
        status = str(it.get("status") or "unanswered")
        status = status if status in ("unanswered", "graded", "skipped") else "unanswered"
        items.append(ExamItem(
            item_id=str(it.get("item_id") or f"item_{idx+1}")[:40],
            question=str(it.get("question") or "")[:300],
            expected_display=str(it.get("expected_display") or "")[:120],
            status=status,
            student_answer=(str(it.get("student_answer"))[:200] if it.get("student_answer") else None),
            verdict=(str(it.get("verdict")) if it.get("verdict") else None),
            correct=(bool(it.get("correct")) if it.get("correct") is not None else None),
            help_count=int(it.get("help_count") or 0),
        ))
    if not items:
        return None
    exam_status = str(raw.get("exam_status") or "active")
    exam_status = exam_status if exam_status in ("active", "completed") else "active"
    ci = raw.get("current_item_index")
    current_index = None
    if exam_status == "active":
        try:
            current_index = max(0, min(int(ci), len(items) - 1))
        except (TypeError, ValueError):
            current_index = next((i for i, it in enumerate(items) if it.status != "graded"), 0)
    return ExamState(
        exam_id=str(raw.get("exam_id") or f"exam_{uuid.uuid4().hex}")[:80],
        exam_status=exam_status, current_index=current_index, items=items,
        oblast=str(raw.get("oblast") or "")[:120],
        tema=str(raw.get("tema") or "")[:120],
        topic_covered=bool(raw.get("topic_covered", True)),
        seed=str(raw.get("seed") or "")[:64],
        grade=raw.get("grade", 6),
    )


# --------------------------------------------------------------------------- #
# Turn result                                                                  #
# --------------------------------------------------------------------------- #
@dataclass
class ExamTurnResult:
    answer: str
    exam_state: dict | None             # None = V2 state released (drain/rollback)
    verdict: str | None = None          # correct | incorrect | None
    verdict_detail: str | None = None
    exam_status: str = "active"         # active | completed | released
    expected_user_action: str = "answer_task"


# --------------------------------------------------------------------------- #
# Routing                                                                      #
# --------------------------------------------------------------------------- #
def should_handle(*, prev_exam: Any, mode: Any, has_active_image: bool = False) -> bool:
    """Does the V2 engine own this turn?

    ``on``    — existing V2 exam, or a fresh exam-mode turn with no exam at all
                (it never takes over a LEGACY exam mid-flight).
    ``drain`` — ONLY an existing V2 exam (so it can finish); no new V2 exams.
    ``off``   — never.
    """
    m = exam_mode()
    if m == "off" or has_active_image:
        return False
    if is_v2_exam(prev_exam):
        return True                     # continue an exam V2 already owns
    if m == "drain":
        return False                    # draining: do not start anything new
    if prev_exam:                       # a legacy exam is in flight → leave it alone
        return False
    return _fold(mode) in ("exam", "kontrolni")


# --------------------------------------------------------------------------- #
# Item presentation / grading helpers                                          #
# --------------------------------------------------------------------------- #
def _present_all(state: ExamState, *, intro: str) -> str:
    lines = [intro]
    if not state.topic_covered and (state.oblast or state.tema):
        topic = state.tema or state.oblast
        lines.append(
            f"Napomena: za izabranu temu „{topic}” još nemam automatski kontrolni, "
            f"pa je ovo OPŠTI kontrolni za vježbu (nije iz te teme)."
        )
    lines.append("")
    for i, it in enumerate(state.items, start=1):
        lines.append(f"{i}. {it.question}")
    lines += ["", "Odgovaraj jedan po jedan. Kreni od 1. zadatka."]
    return "\n".join(lines)


def _grade(question: str, answer: str) -> tuple[str, bool | None]:
    result = check_practice_answer(question, answer)
    if not result.checkable or not result.items:
        return "unverified", None
    verdict = authoritative_verdict(result)
    if verdict == "correct":
        return "correct", True
    if verdict in ("incorrect", "incomplete", "mixed"):
        return "incorrect", False
    # step/partial/unknown on a single exam item → treat as not yet correct
    return "unverified", None


def _summary(state: ExamState) -> str:
    lines = [f"Kontrolni je završen. Rezultat: {state.score}/{state.total}.", ""]
    for i, it in enumerate(state.items, start=1):
        if it.status == "skipped":
            mark = "— preskočeno"
        else:
            mark = "✓ tačno" if it.correct else "✗ netačno"
        exp = f" (tačan odgovor: {it.expected_display})" if it.expected_display else ""
        lines.append(f"{i}. {mark}{exp}")
    lines += ["", "Za objašnjenje nekog zadatka reci npr. „objasni drugi”. "
              "Za novi kontrolni reci „novi kontrolni”."]
    return "\n".join(lines)


def _next_prompt(state: ExamState) -> str:
    it = state.items[state.current_index]
    return f"Sada riješi {state.current_index + 1}. zadatak: {it.question}"


# --------------------------------------------------------------------------- #
# Transitions                                                                  #
# --------------------------------------------------------------------------- #
def start_exam(seed: str, count: int = DEFAULT_ITEM_COUNT, *,
               grade: Any = 6, oblast: Any = "", tema: Any = "") -> ExamState:
    """Build a topic-aware exam. If templates cover the selected grade/oblast/tema,
    all items are generated from that topic. If a topic IS requested but has no
    template, fall back to a clearly-labeled GENERIC exam (topic_covered=False) —
    never a silent unrelated substitution. If no topic is requested, the generic
    pool is exactly what was asked for (topic_covered=True)."""
    oblast_s, tema_s = str(oblast or ""), str(tema or "")
    topic_requested = bool(oblast_s.strip() or tema_s.strip())

    generated = task_templates.generate_batch(grade, oblast_s, tema_s, count=count, seed=seed)
    if generated:
        items = [
            ExamItem(item_id=f"item_{i+1}", question=g.question,
                     expected_display=g.expected_display)
            for i, g in enumerate(generated)
        ]
        covered = True
    else:
        # No template for the requested topic → explicit generic fallback.
        questions = _select_items(seed, count)
        items = [
            ExamItem(item_id=f"item_{i+1}", question=q, expected_display=_expected_display(q))
            for i, q in enumerate(questions)
        ]
        covered = not topic_requested        # generic is on-target only if none requested

    return ExamState(exam_id=f"exam_{uuid.uuid4().hex}", exam_status="active",
                     current_index=0, items=items, oblast=oblast_s[:120],
                     tema=tema_s[:120], topic_covered=covered,
                     seed=str(seed)[:64], grade=grade)


#: Topic-specific first-tier support. Keyed by a marker in the QUESTION, so the
#: child gets the actual rule rather than "sjeti se pravila". Never the answer.
_HELP_CUES: tuple[tuple[str, str], ...] = (
    ("djeljiv sa 6", "Podsjeti se pravila djeljivosti sa 6: broj mora biti "
                     "djeljiv i sa 2 i sa 3."),
    ("djeljiv sa 4", "Kod djeljivosti sa 4 gledaš samo posljednje dvije cifre."),
    ("djeljiv sa 3", "Kod djeljivosti sa 3 sabereš cifre pa gledaš je li zbir "
                     "djeljiv sa 3."),
    ("djeljiv sa 9", "Kod djeljivosti sa 9 sabereš cifre i gledaš je li zbir "
                     "djeljiv sa 9."),
    ("djeljiv sa 5", "Kod djeljivosti sa 5 gledaš samo posljednju cifru."),
    ("djeljiv sa 2", "Kod djeljivosti sa 2 gledaš je li posljednja cifra parna."),
    ("proste faktore", "Rastavljanje počinje od najmanjeg prostog broja: probaj "
                       "redom 2, 3, 5, 7."),
    ("nzd", "NZD je najveći broj kojim se OBA broja dijele bez ostatka."),
    ("nzs", "NZS je najmanji broj koji je djeljiv sa OBA broja."),
    ("prosiri", "Proširivanje znači da i brojnik i nazivnik množiš ISTIM brojem."),
    ("skrati", "Skraćivanje znači da i brojnik i nazivnik dijeliš ISTIM brojem."),
)

#: Second tier: where to look, still without doing the work.
_HELP_TIER2 = ("Zapiši šta ti je poznato, pa primijeni pravilo na prvi dio "
               "zadatka. Ne moraš odmah do kraja.")


def _expects_boolean(it: "ExamItem") -> bool:
    """True when the item asks a yes/no question, so a bare „ne” is an ANSWER."""
    exp = derive_expected(it.question)
    return exp is not None and getattr(exp, "expected_boolean", None) is not None


def _help_cue(question: str, tier: int) -> str:
    """Progressive support text for the exam's own topic."""
    if tier >= 3:
        return ("Suzi na jedan detalj i provjeri samo njega. Možeš i preskočiti "
                "ovaj zadatak ili predati kontrolni.")
    if tier == 2:
        return _HELP_TIER2
    folded = _fold(question)
    for marker, cue in _HELP_CUES:
        if marker in folded:
            return cue
    return "Sjeti se pravila koje vrijedi za ovaj tip zadatka."


def _help_reply(state: ExamState, it: ExamItem) -> str:
    """Progressive, NON-revealing support, rendered through the shared layer."""
    n = state.current_index + 1
    tier = min(max(it.help_count, 1), 3)
    ctx = render.RenderContext(
        mode="exam", intent="help", grade=state.grade,
        seed=f"{state.seed}|help|{n}|{tier}", help_level=tier,
        help_text=_help_cue(it.question, tier), may_reveal=False)
    return f"{render.help_phrase(ctx)}\nZadatak {n}: {it.question}"


def _advance(state: ExamState, verdict: str, detail: str,
             seed_salt: str) -> ExamTurnResult:
    """Move to the next unanswered item (or finish) and render the transition.

    Every exam transition funnels through here, so wording, state advance and
    the completion check can no longer disagree.
    """
    remaining = [i for i, x in enumerate(state.items) if x.status == "unanswered"]
    finished = not remaining
    if remaining:
        state.current_index = remaining[0]
        nxt = state.items[state.current_index]
        next_q, next_i = nxt.question, state.current_index + 1
    else:
        state.exam_status = "completed"
        state.current_index = None
        next_q, next_i = "", None
    ctx = render.RenderContext(
        mode="exam", verdict=verdict, grade=state.grade,
        seed=f"{state.seed}|{seed_salt}", next_question=next_q,
        next_index=next_i, exam_finished=finished, may_reveal=finished,
        summary=_summary(state) if finished else "")
    return ExamTurnResult(
        answer=render.exam_transition(ctx), exam_state=state.to_dict(),
        verdict=(verdict if verdict in ("correct", "incorrect") else None),
        verdict_detail=detail,
        exam_status="completed" if finished else "active",
        expected_user_action="none" if finished else "answer_task")


def _handle_active(state: ExamState, message: str) -> ExamTurnResult:
    it = state.items[state.current_index]
    # ONE classification for the whole turn — the exam no longer keeps its own
    # private notion of what "ne znam" or "predaj" means.
    ti = turn_intent.classify(message, expects_boolean=_expects_boolean(it))

    # Explicit SKIP — the ONLY non-answer that may advance. Recorded as skipped,
    # never as the student's answer.
    if ti.intent == turn_intent.Intent.SKIP:
        it.status = "skipped"
        it.verdict = "skipped"
        it.correct = False
        it.student_answer = None
        return _advance(state, "skipped", "exam_skipped",
                        f"skip|{state.current_index}")

    # Explicit submit — grade what we have, remaining stay unanswered (incorrect).
    if ti.intent == turn_intent.Intent.SUBMIT:
        for rem in state.items:
            if rem.status == "unanswered":
                rem.status = "graded"
                rem.verdict = "incorrect"
                rem.correct = False
        state.exam_status = "completed"
        state.current_index = None
        return ExamTurnResult(answer=_summary(state), exam_state=state.to_dict(),
                              verdict=None, verdict_detail="exam_submitted",
                              exam_status="completed", expected_user_action="none")

    # Help / explanation / non-answer — NEVER stored as an answer, NEVER advances.
    if ti.is_non_answer:
        it.help_count += 1
        if ti.intent == turn_intent.Intent.UNKNOWN:
            n = state.current_index + 1
            return ExamTurnResult(
                answer=("Nisam siguran da je to odgovor na zadatak. Napiši svoj "
                        "odgovor, možeš preskočiti zadatak ili predati kontrolni.\n"
                        f"Zadatak {n}: {it.question}"),
                exam_state=state.to_dict(), verdict=None,
                verdict_detail="exam_needs_answer", exam_status="active",
                expected_user_action="answer_task")
        return ExamTurnResult(answer=_help_reply(state, it), exam_state=state.to_dict(),
                              verdict=None, verdict_detail="exam_help",
                              exam_status="active", expected_user_action="answer_task")

    # Grade the CURRENT item only (one answer -> one item).
    verdict, correct = _grade(it.question, message)
    it.status = "graded"
    it.student_answer = message[:200]
    it.verdict = verdict
    it.correct = correct
    return _advance(
        state,
        "correct" if correct else "incorrect" if correct is False else "partial",
        f"exam_{verdict}", f"grade|{state.current_index}|{verdict}")


def _explain_item(state: ExamState, index0: int) -> str:
    it = state.items[index0]
    verdict = "tačno" if it.correct else "netačno"
    exp = f" Tačan odgovor je {it.expected_display}." if it.expected_display else ""
    ans = f" Ti si odgovorio: {it.student_answer}." if it.student_answer else ""
    return (f"{index0 + 1}. zadatak: {it.question}\nRiješio si ga {verdict}.{ans}{exp}")


def _handle_completed(state: ExamState, message: str, grade: Any = 6) -> ExamTurnResult:
    folded = _fold(message)

    # Explicit new exam — the ONLY way to start a fresh one from a completed exam.
    if _NEW_EXAM_RE.search(folded):
        if not accepts_new_exams():
            # DRAIN: this exam is finished, so release the V2 state entirely.
            # The next turn carries no V2 exam and goes to the legacy path.
            return ExamTurnResult(
                answer=("Ovaj kontrolni je završen. Novi kontrolni ću pripremiti "
                        "na uobičajen način — samo mi reci iz koje oblasti."),
                exam_state=None, verdict=None, verdict_detail="exam_released",
                exam_status="released", expected_user_action="none")
        fresh = start_exam(seed=uuid.uuid4().hex, grade=grade,
                           oblast=state.oblast, tema=state.tema)
        intro = "Evo novog kontrolnog. Sretno!"
        return ExamTurnResult(answer=_present_all(fresh, intro=intro),
                              exam_state=fresh.to_dict(), verdict=None,
                              verdict_detail="exam_started", exam_status="active",
                              expected_user_action="answer_task")

    # Explain a specific item — never reopens the exam.
    if _EXPLAIN_RE.search(folded):
        idx = None
        for tok in re.findall(r"[a-z0-9.]+", folded):
            if tok in _ORDINALS and _ORDINALS[tok] <= state.total:
                idx = _ORDINALS[tok] - 1
                break
        if idx is None:
            wrong = [i for i, it in enumerate(state.items) if it.correct is False]
            idx = wrong[0] if wrong else 0
        return ExamTurnResult(answer=_explain_item(state, idx), exam_state=state.to_dict(),
                              verdict=None, verdict_detail="exam_explained",
                              exam_status="completed", expected_user_action="none")

    # Anything else — restate the result; do NOT reopen or generate new items.
    answer = (f"Kontrolni je već završen (rezultat {state.score}/{state.total}). "
              f"Za objašnjenje reci npr. „objasni drugi”, a za novi reci „novi kontrolni”.")
    return ExamTurnResult(answer=answer, exam_state=state.to_dict(), verdict=None,
                          verdict_detail="exam_completed_noop", exam_status="completed",
                          expected_user_action="none")


def process(*, prev_exam: Any, mode: Any, message: Any, seed: str,
            grade: Any = 6, oblast: Any = "", tema: Any = "",
            item_count: int = DEFAULT_ITEM_COUNT) -> ExamTurnResult:
    """Single deterministic exam turn. Never calls a model."""
    state = load_state(prev_exam)
    msg = str(message or "").strip()

    if state is None:
        # Fresh start — topic-aware item generation (explicit fallback if uncovered).
        state = start_exam(seed=seed or uuid.uuid4().hex, count=item_count,
                           grade=grade, oblast=oblast, tema=tema)
        # If the very first message is already an answer, grade it against item 1;
        # otherwise just present the items.
        if msg and re.search(r"[\d/=]", msg) and _fold(msg) not in ("kontrolni", "exam"):
            return _handle_active(state, msg)
        intro = "Počinjemo kontrolni. Riješi sve zadatke, jedan po jedan."
        return ExamTurnResult(answer=_present_all(state, intro=intro),
                              exam_state=state.to_dict(), verdict=None,
                              verdict_detail="exam_started", exam_status="active",
                              expected_user_action="answer_task")

    if state.exam_status == "completed":
        return _handle_completed(state, msg, grade=grade)
    return _handle_active(state, msg)
