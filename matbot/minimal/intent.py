# -*- coding: utf-8 -*-
"""Concept 1 of 5: **TurnIntent** — what the student's turn means.

Deliberately tiny. The V2 classifier grew thirteen intents because it served
every mode; this engine supports Practice only, so a turn is one of four things.
Anything it cannot place is ``OTHER`` and is answered honestly rather than
guessed at.

Pure text → label. Reads no state, mutates nothing, and cannot be a second
source of truth about tasks, verdicts or progression.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Any


class TurnIntent(str, Enum):
    ANSWER = "answer"
    HELP = "help"                       # stuck on THIS task
    NEW_TASK = "new_task"
    HARDER = "harder"                   # a new task, one band up
    EASIER = "easier"                   # a new task, one band down
    CONCEPT_QUESTION = "concept_question"   # a real question about the maths
    SOLUTION_REQUEST = "solution_request"   # "uradi ti", "daj rjesenje"
    OTHER = "other"


#: The only labels the OpenAI fallback classifier may return.
CLASSIFIER_LABELS = ("ANSWER", "HELP", "NEW_TASK", "HARDER", "EASIER",
                     "CONCEPT_QUESTION", "SOLUTION_REQUEST", "OTHER")


#: HARDER/EASIER are NEW_TASK with a direction — callers that only care about
#: "does this ask for a new task?" should use this set.
NEW_TASK_INTENTS = frozenset({TurnIntent.NEW_TASK, TurnIntent.HARDER,
                              TurnIntent.EASIER})


def fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


#: Replies to a yes/no question the TUTOR asked. Matched only when a
#: confirmation is actually pending — never used to guess intent otherwise.
_AFFIRM_RE = re.compile(
    r"^\s*(da|moze|mo[zž]e|ho[cčć]u|hajde|va[zž]i|ok|okej|naravno|jeste|"
    r"idemo|daj|moze\s+moze|da\s+molim)\s*[.!]?\s*$")
_DECLINE_RE = re.compile(
    r"^\s*(ne|ne[cčć]u|nemoj|ne\s+hvala|ne\s+treba|dosta|stani)\s*[.!]?\s*$")


def is_affirmation(message: Any) -> bool:
    return bool(_AFFIRM_RE.match(fold(message)))


def is_decline(message: Any) -> bool:
    return bool(_DECLINE_RE.match(fold(message)))


#: Replies to "Da li želiš novi zadatak ili objašnjenje?" — matched only when
#: that confirmation is pending.
_WANTS_EXPLANATION_RE = re.compile(r"\bobja[sš]nj\w*|\bobjasni\w*|\bpravilo\b")
_WANTS_TASK_RE = re.compile(r"\bzadat\w*|\bvjezb\w*|\bprimjer\w*")


def confirmation_choice(message: Any) -> str:
    """"task" | "explanation" | "" for a task-or-explanation clarification."""
    text = fold(message)
    if _WANTS_EXPLANATION_RE.search(text):
        return "explanation"
    if _WANTS_TASK_RE.search(text):
        return "task"
    return ""


#: HELP phrases a typo may mangle. Matched by closeness ONLY, and only while a
#: task is active — production sent "ne znmam" after two hints and got OTHER.
_HELP_TYPO_TARGETS = (
    "ne znam", "neznam", "ne razumijem", "nerazumijem", "pomozi", "pomoc",
)
#: Anything mathematical disqualifies a message from being a typo of "ne znam".
_ANSWERISH_RE = re.compile(r"[0-9=<>+\-/*^]")
_MAX_TYPO_WORDS = 3
_MAX_TYPO_CHARS = 22
#: 0.78 accepts "ne znmam"/"nezanm" and rejects unrelated short words.
_TYPO_RATIO = 0.78


def is_help_typo(message: Any) -> bool:
    """A short message that is clearly a mangled HELP phrase.

    Deliberately conservative: short, non-mathematical text only, compared by
    similarity against a fixed list. This is NOT general fuzzy classification —
    it never runs unless an ActiveTask exists.
    """
    import difflib

    text = fold(message)
    if not text or _ANSWERISH_RE.search(text):
        return False
    if len(text) > _MAX_TYPO_CHARS or len(text.split()) > _MAX_TYPO_WORDS:
        return False
    squeezed = text.replace(" ", "")
    for target in _HELP_TYPO_TARGETS:
        goal = target.replace(" ", "")
        if difflib.SequenceMatcher(None, squeezed, goal).ratio() >= _TYPO_RATIO:
            return True
    return False


#: An explicit request for the WORKED SOLUTION, not a nudge. Checked before
#: HELP: "ne znam uradi ti" is both, and the stronger request wins.
_SOLUTION_REQUEST_RE = re.compile(
    r"\buradi\s+(ti|mi)\b|\brije[sš]i\s+(ti|mi)\b|\bdaj\s+rje[sš]enj\w*"
    r"|\bpoka[zž]i\s+(cijel\w*\s+)?(rje[sš]enj\w*|postup\w*)"
    r"|\bobjasni\s+(cijel\w*\s+)?postup\w*|\buradi\s+i\s+objasni\b"
    r"|\bkompletno\s+rje[sš]enj\w*|\bcijeli\s+postup\w*"
    r"|\bdaj\s+mi\s+odgovor\b|\breci\s+mi\s+rje[sš]enj\w*"
    # "hajde ga TI URADI": the pronoun comes FIRST, so the "uradi ti" branch
    # above cannot match it. Production read this as HELP and repeated a hint.
    r"|\bti\s+(ga\s+|to\s+)?(uradi|rije[sš]i|izra[cč]unaj)\b"
    r"|\bhajde\s+(ga\s+|to\s+)?ti\b"
    r"|\bmo[zž]e[sš]\s+li\s+(ga\s+|to\s+)?ti\s+rije[sš]iti\b")

#: "I am stuck on THIS task." Note that bare "zašto" is NOT here — a substantive
#: "zašto…?" is a question about the maths, which is a CONCEPT_QUESTION.
_HELP_RE = re.compile(
    r"\b(ne\s*znam|nemam\s+pojma|pomo[cčć]\w*|pomozi|hint|savjet\w*|help|"
    r"ne\s+razumijem|ne\s+kapiram|ne\s+kontam|zapeo\s+sam|zapela\s+sam|"
    r"objasni\w*|obrazloz\w*|kako\s+se\s+radi|kako\s+da|odakle\s+da\s+krenem|"
    r"ne\s+umijem|ne\s+mogu)\b")

#: A real question about the mathematics. Matched on interrogative OPENERS so
#: typos in the content ("nazvivnik") cannot defeat it — production sent
#: "a reci mi sta ako imamo isti brojnik i isti nazvivnik…" and it became a task.
_CONCEPT_RE = re.compile(
    r"\b[sš]ta\s+ako\b|\b[sš]ta\s+zna[cč]i\b|\b[sš]ta\s+je\b|\b[sš]ta\s+se\s+de[sš]ava\b"
    r"|\bza[sš]to\b|\bzbog\s+[cč]ega\b|\bmo[zž]e\s+li\b|\bmo[zž]e\s+li\s+se\b"
    r"|\bda\s+li\s+(mogu|moze|se|je|treba|uvijek|ikad)\b|\bje\s+li\s+(tacno|uvijek|to)\b"
    r"|\bkako\s+(to|se\s+de[sš]ava|zna[sš]|funkcioni[sš]e|bi)\b"
    r"|\b[sš]ta\s+bi\s+bilo\b|\bkoja\s+je\s+razlika\b|\bvrijedi\s+li\b")

#: Deliberately narrow: only the FIRST word of an interrogative message is
#: tested, only against "zasto", and never when the message carries arithmetic.
#: Production sent "DZašto moramo uraditi istu operaciju na obje strane
#: jednačine?" and the leading typo pushed it out of CONCEPT_QUESTION.
_WHY_TARGET = "zasto"
_WHY_TYPO_RATIO = 0.75


def is_why_typo(message: Any) -> bool:
    """True for a mistyped "zašto" opening a short interrogative question."""
    import difflib

    text = " ".join(fold(message).split())
    if not text or _ANSWERISH_RE.search(text):
        return False              # never reinterpret a numeric answer
    words = text.split()
    if len(words) < 3 or len(words) > 14:
        return False              # not a bare token, not an essay
    if not text.rstrip().endswith("?"):
        return False              # interrogative only
    head = words[0].strip("?!.,")
    if head == _WHY_TARGET or len(head) > len(_WHY_TARGET) + 3:
        return False              # exact match is already handled by _CONCEPT_RE
    return difflib.SequenceMatcher(
        None, head, _WHY_TARGET).ratio() >= _WHY_TYPO_RATIO


#: "Proširi 3/5 brojem 7." — an instruction to DEMONSTRATE, not an answer to an
#: active task (an answer is a bare value like "21/35", never an imperative).
_EXPAND_INSTRUCTION_RE = re.compile(
    r"\bpro[sš]ir\w*\s+\d+\s*/\s*\d+\s*(?:brojem|sa|s|na\s+nazivnik|puta)\s*\d+")

#: Words that mean the message is ABOUT the subject matter, used to decide that
#: a trailing "?" is a real question rather than noise.
_TOPIC_WORDS_RE = re.compile(
    r"\bbrojnik\w*|\bnazivnik\w*|\bnazvivnik\w*|\brazlom\w*|\bprosir\w*|\bskrat\w*"
    r"|\bjednacin\w*|\bdjeljiv\w*|\bfaktor\w*|\bcifr\w*|\bmnoz\w*|\bdijel\w*"
    r"|\bsabir\w*|\bzbir\w*|\bjedinic\w*|\bcijeli\s+broj\w*")

#: Any mention of wanting a task. Deliberately broad on the NOUN rather than
#: enumerating verb+adjective combinations: production sent "Daj mi teži zadatak
#: iz iste teme", which no adjacency pattern matched, so it was read as a
#: non-answer and the student got the same task back.
#: Difficulty words are matched but NOT honoured — harder/easier is out of
#: scope, so such a request yields a normal new task rather than a wrong one.
_NEW_TASK_RE = re.compile(
    r"\b(zadat\w*|vjezb\w*|primjer\w*|jos\s+jedan|idemo\s+dalje|dalje)\b")

# --------------------------------------------------------------------------- #
# Task-SHAPE requests: "daj mi zadatak oblika a-x=b" describes a whole         #
# EQUATION SHAPE, never a computed value, so it is never a valid ANSWER.      #
#                                                                              #
# Two structurally different things must NOT be conflated:                    #
#  * a LITERAL PLACEHOLDER shape ("a-x=b", using the letters a/x/b) has no    #
#    other reading — it identifies a requested shape on its own.             #
#  * a CONCRETE NUMERIC equation ("5/6 - x = 1/3") is exactly what a student  #
#    mid-task might submit as a transformed equation or intermediate working #
#    step. Earlier this module treated any such equation as a shape request  #
#    outright, which would have misrouted a genuine working step during an   #
#    active task. A concrete equation is now a requested shape ONLY when the #
#    message ALSO contains an explicit task-request cue ("daj mi primjer",   #
#    "zelim probati", "u ovom obliku", ...); bare, it identifies only as a   #
#    RECOGNISED EQUATION STATEMENT (see ``is_bare_equation_statement``),      #
#    which the caller may route to a clarification when no task is active,  #
#    or leave for the ordinary answer/working-step path when one is.         #
# --------------------------------------------------------------------------- #
_NUM = r"\d+(?:\s*/\s*\d+)?"
#: Literal placeholder shapes: "x-a=b", "a+x=b", accepting the Unicode minus.
_SHAPE_X_LITERAL_RE = re.compile(r"\bx\s*([+\-−])\s*a\s*=\s*b\b")
_SHAPE_A_LITERAL_RE = re.compile(r"\ba\s*([+\-−])\s*x\s*=\s*b\b")
#: Concrete numeric equations of the same two shapes, x still unresolved.
_SHAPE_X_NUMERIC_RE = re.compile(
    rf"\bx\s*([+\-−])\s*{_NUM}\s*=\s*{_NUM}\b")
_SHAPE_A_NUMERIC_RE = re.compile(
    rf"\b{_NUM}\s*([+\-−])\s*x\s*=\s*{_NUM}\b")
#: "razlomak minus x", or bare "minus x" — names the a - x = b shape in prose.
#: Prose-only, so it carries no risk of matching a restated equation.
_SHAPE_PHRASE_RE = re.compile(r"\bminus\s+x\b")

#: An explicit REQUEST for a task/example — the cue that turns a concrete
#: equation into a shape request rather than working the student submitted.
_TASK_REQUEST_CUE_RE = re.compile(
    r"\bdaj\s+mi\s+(zadat\w*|primjer\w*)\b|\b[zž]elim\s+(probat\w*|jednacin\w*|"
    r"jednadzb\w*)\b|\bho[cč]u\s+zadat\w*\b|\bnapravi\s+zadat\w*\b"
    r"|\bu\s+ovom\s+obliku\b|\bove\s+forme\b|\boblika\b|\bu\s+formi\b")

#: Public shape vocabulary. Kept distinct from the generator's internal form
#: names (see ``skills.EQUATION_FORMS``) so this module stays skill-agnostic.
SHAPE_X_PLUS_A = "x_plus_a"
SHAPE_A_PLUS_X = "a_plus_x"
SHAPE_X_MINUS_A = "x_minus_a"
SHAPE_A_MINUS_X = "a_minus_x"


def _numeric_shape(text: str) -> str | None:
    match = _SHAPE_A_NUMERIC_RE.search(text)
    if match:
        return SHAPE_A_MINUS_X if match.group(1) in "-−" else SHAPE_A_PLUS_X
    match = _SHAPE_X_NUMERIC_RE.search(text)
    if match:
        return SHAPE_X_MINUS_A if match.group(1) in "-−" else SHAPE_X_PLUS_A
    return None


def parse_shape_request(raw_message: Any) -> str | None:
    """One of the four public shape names, or ``None``.

    Shared, skill-agnostic parsing: this function knows nothing about
    ``fraction_equation_additive`` or any other skill. Whether a requested
    shape is honoured or politely declined is the caller's decision.
    """
    text = fold(raw_message)
    if not text:
        return None
    match = _SHAPE_A_LITERAL_RE.search(text)
    if match:
        return SHAPE_A_MINUS_X if match.group(1) in "-−" else SHAPE_A_PLUS_X
    match = _SHAPE_X_LITERAL_RE.search(text)
    if match:
        return SHAPE_X_MINUS_A if match.group(1) in "-−" else SHAPE_X_PLUS_A
    # A CONCRETE equation only counts as a request with an explicit cue —
    # bare, it is indistinguishable from a transformed working step.
    if _TASK_REQUEST_CUE_RE.search(text):
        shape = _numeric_shape(text)
        if shape is not None:
            return shape
    if _SHAPE_PHRASE_RE.search(text):
        return SHAPE_A_MINUS_X
    return None


def is_bare_equation_statement(raw_message: Any) -> bool:
    """True for a concrete numeric equation with NO task-request cue.

    Used only to distinguish "the student stated an equation" from "no
    mathematical content at all" — what to DO with that (leave it for the
    ordinary answer route while a task is active; offer a clarification when
    none is) is entirely the caller's decision.
    """
    text = fold(raw_message)
    if not text or _TASK_REQUEST_CUE_RE.search(text):
        return False           # a cued message is a request, not a statement
    return _numeric_shape(text) is not None

#: Direction words. They modify a NEW_TASK request; they are never a mode.
_HARDER_RE = re.compile(r"\btez\w*|\bteski\w*|\bkomplikovanij\w*|\bizazovnij\w*")
_EASIER_RE = re.compile(r"\blaks\w*|\bjednostavnij\w*|\blagan\w*")

#: Something to compute or decide. A message with no mathematical content is
#: not an answer, however confident it sounds.
_MATH_RE = re.compile(r"\d|[=<>+\-/*^]|π|\bpi\b|\{|\}")
#: A bare yes/no is an answer only when it stands alone — embedded, "da" is an
#: ordinary Bosnian conjunction ("šta DA probam").
_BARE_BOOL_RE = re.compile(r"^(da|ne|jeste|nije|jest|tacno|netacno)\s*[.!]?$")


@dataclass(frozen=True)
class Classification:
    intent: TurnIntent
    matched: str = ""


def classify(raw_message: Any, *, has_active_task: bool = False) -> Classification:
    """Classify one turn. ``raw_message`` is never modified."""
    text = fold(raw_message)
    if not text:
        return Classification(TurnIntent.OTHER, "empty")
    # A request for the WHOLE solution outranks a plain "ne znam" — the
    # production message was "NE ZNAM URADI I OBJASNI POSTUPAK".
    if _SOLUTION_REQUEST_RE.search(text):
        return Classification(TurnIntent.SOLUTION_REQUEST, "solution_request")
    # HELP: "ne znam" must never be read as the answer "ne".
    if _HELP_RE.search(text):
        return Classification(TurnIntent.HELP, "help")
    # A question ABOUT the maths, before the answer/new-task checks — otherwise
    # "šta ako … proširiti brojem 7" reads as a task request because it contains
    # both a topic word and a digit.
    if _EXPAND_INSTRUCTION_RE.search(text):
        return Classification(TurnIntent.CONCEPT_QUESTION, "expand_instruction")
    if _CONCEPT_RE.search(text):
        return Classification(TurnIntent.CONCEPT_QUESTION, "concept")
    if is_why_typo(raw_message):
        return Classification(TurnIntent.CONCEPT_QUESTION, "concept_why_typo")
    if text.rstrip().endswith("?") and len(text.split()) >= 4 \
            and _TOPIC_WORDS_RE.search(text):
        return Classification(TurnIntent.CONCEPT_QUESTION, "concept_question_mark")
    # A task REQUEST carries no mathematical content; "zadatak 5/20" is an
    # answer that happens to name the task, so math wins.
    if _NEW_TASK_RE.search(text) and not _MATH_RE.search(text):
        # Direction is part of the request, not a separate mode.
        if _HARDER_RE.search(text):
            return Classification(TurnIntent.HARDER, "harder")
        if _EASIER_RE.search(text):
            return Classification(TurnIntent.EASIER, "easier")
        return Classification(TurnIntent.NEW_TASK, "new_task")
    # A message describing a whole EQUATION SHAPE ("a-x=b", "5/6 - x = 1/3")
    # is full of digits and operators, which would otherwise fall to ANSWER
    # below — that is exactly how two production shape requests were graded
    # as wrong answers. Checked before the math gate for that reason.
    if parse_shape_request(text) is not None:
        return Classification(TurnIntent.NEW_TASK, "shape_request")
    if _MATH_RE.search(text) or _BARE_BOOL_RE.match(text):
        return Classification(TurnIntent.ANSWER, "answer")
    # LAST resort, and only with a task open: a mistyped cry for help.
    if has_active_task and is_help_typo(text):
        return Classification(TurnIntent.HELP, "help_typo")
    return Classification(TurnIntent.OTHER, "no_signal")


_CLASSIFIER_SYSTEM = (
    "Klasifikuj poruku učenika u TAČNO JEDNU oznaku. Vrati SAMO oznaku, "
    "bez ikakvog drugog teksta.\n"
    "ANSWER — učenik daje odgovor na zadatak (broj, razlomak, jednakost).\n"
    "HELP — učenik je zapeo i traži pomoć oko trenutnog zadatka.\n"
    "NEW_TASK — učenik traži novi zadatak.\n"
    "HARDER — učenik traži teži zadatak.\n"
    "EASIER — učenik traži lakši zadatak.\n"
    "CONCEPT_QUESTION — učenik postavlja pitanje o samoj matematici "
    "(pojam, pravilo, „šta ako”, „zašto”).\n"
    "SOLUTION_REQUEST — učenik traži da mu se zadatak riješi i objasni.\n"
    "OTHER — ništa od navedenog ili nejasno.\n"
    "Ne rješavaj zadatak. Ne ocjenjuj. Vrati samo oznaku."
)


def classify_with_model(raw_message: Any, *, openai_chat, model: str = "",
                        timeout: float | None = None) -> TurnIntent | None:
    """Constrained fallback classifier. Returns None on ANY doubt.

    Hard limits, by construction:
      * it sees only the student's message — no task, no state, no answer;
      * its output is matched against ``CLASSIFIER_LABELS``; anything else is
        discarded, so it cannot invent a label;
      * it returns a LABEL, never text shown to the student, so it cannot grade,
        mutate state, or leak a solution.
    ``None`` leaves the deterministic OTHER in place, which is the safe answer.
    """
    text = str(raw_message or "").strip()
    if openai_chat is None or not text:
        return None
    try:
        response = openai_chat(
            model,
            [{"role": "system", "content": _CLASSIFIER_SYSTEM},
             {"role": "user", "content": text[:400]}],
            timeout=timeout, max_tokens=8,
        )
        label = (response.choices[0].message.content or "").strip().upper()
    except Exception:
        return None
    label = re.sub(r"[^A-Z_]", "", label)
    if label not in CLASSIFIER_LABELS:
        return None
    return TurnIntent[label]


def classify_turn(raw_message: Any, *, openai_chat=None, model: str = "",
                  timeout: float | None = None,
                  has_active_task: bool = False) -> Classification:
    """Deterministic rules first; the model only breaks a genuine tie.

    The classifier is consulted ONLY when the deterministic pass returns OTHER,
    so a recognised message never costs an API call.
    """
    decided = classify(raw_message, has_active_task=has_active_task)
    if decided.intent is not TurnIntent.OTHER:
        return decided
    guessed = classify_with_model(raw_message, openai_chat=openai_chat,
                                  model=model, timeout=timeout)
    if guessed is None or guessed is TurnIntent.OTHER:
        return decided
    return Classification(guessed, "model_classifier")
