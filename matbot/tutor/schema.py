"""Jedna šema odgovora za SVIH 534 lekcija i sve namjere učenika.

Dva strict oblika:
  • `TutorDraft`    — izlaz PRVOG poziva (nacrt),
  • `ReviewerFinal` — izlaz DRUGOG poziva (nezavisna provjera + KONAČAN payload).

PRAVILO POLJA PO NAMJERI (jedino pravilo, provjerava ga `validate_final`):
polje koje namjera ne traži mora biti `None`. Time „hint bez hinta“ ili „novi
zadatak bez zadatka“ pada na serveru, a ne pred učenikom.

Nijedno interno polje (dijagnostika težine, nezavisno rješenje recenzenta,
`lesson_focus`) NIKAD ne ide u browser — vidi matbot/tutor/pipeline.py.
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from matbot import config

# Zatvoren skup namjera. Model bira TAČNO jednu; server iz nje izvodi šta smije
# biti popunjeno i šta se smije desiti sa stanjem sesije.
INTENTS = (
    "generate_task",
    "easier_task",
    "harder_task",
    "next_task",
    "answer_attempt",
    "hint_request",
    "explanation_request",
    "full_solution_request",
    "clarification",
    "off_topic",
)

# Namjere koje SMIJU (i moraju) donijeti nov zadatak.
TASK_INTENTS = frozenset({"generate_task", "easier_task", "harder_task", "next_task"})
# Namjere koje mijenjaju težinu u odnosu na prethodni zadatak.
DIFFICULTY_SHIFT_INTENTS = frozenset({"easier_task", "harder_task"})

DIMENSION_MOVES = ("lower", "same", "higher")

FAIL_REASON_CODES = (
    "math_incorrect",
    "wrong_marked_option",
    "outside_lesson",
    "ambiguous_task",
    "unsolvable_task",
    "difficulty_not_changed",
    "intent_mishandled",
    "invalid_mathjax",
    "language_not_age_appropriate",
    "unsafe_or_unverifiable",
)


class TutorOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class DifficultyDiagnostics(BaseModel):
    """INTERNO: kako se novi zadatak odnosi na prethodni.

    Postoji da „lakše/teže“ ne bi bilo puka oznaka: model mora imenovati koje
    dimenzije je pomjerio, recenzent to provjerava, a server loguje. Učenik ovo
    NIKAD ne vidi."""

    model_config = ConfigDict(extra="forbid")

    number_magnitude: Literal[DIMENSION_MOVES]
    number_of_steps: Literal[DIMENSION_MOVES]
    representation_complexity: Literal[DIMENSION_MOVES]
    sign_complexity: Literal[DIMENSION_MOVES]
    scaffolding: Literal[DIMENSION_MOVES]
    distractor_closeness: Literal[DIMENSION_MOVES]
    reasoning_depth: Literal[DIMENSION_MOVES]
    rationale: str


class TaskPayload(BaseModel):
    """Zadatak koji učenik vidi. Uvijek 4 opcije — frontend ugovor se ne mijenja."""

    model_config = ConfigDict(extra="forbid")

    text: str
    options: list[TutorOption]
    correct_option_index: int
    expected_answer: str
    difficulty: Literal["easy", "standard", "hard"]


class TutorDraft(BaseModel):
    """Nacrt prvog poziva. NIJE objavljiv sam po sebi — mora proći recenzenta."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[INTENTS]
    reply: str
    # INTERNO: koju tačno vještinu izabrane lekcije ovaj turn cilja.
    lesson_focus: str
    new_task: Optional[TaskPayload] = None
    hint: Optional[str] = None
    worked_solution: Optional[str] = None
    grading: Optional[Literal["correct", "partially_correct", "incorrect"]] = None
    difficulty_diagnostics: Optional[DifficultyDiagnostics] = None


class ReviewerChecks(BaseModel):
    """Deset nezavisnih provjera iz specifikacije. Svaka je eksplicitna:
    `false` uz `decision='approve'` je kontradikcija koju server odbija."""

    model_config = ConfigDict(extra="forbid")

    math_correct: bool
    marked_option_correct: bool
    inside_lesson: bool
    intent_handled: bool
    difficulty_direction_correct: bool
    response_addresses_student: bool
    task_solvable_and_unambiguous: bool
    mathjax_valid: bool
    language_age_appropriate: bool
    # Nezavisno rješenje: recenzent MORA sam riješiti zadatak prije odobrenja.
    independently_solved: bool
    independent_answer: str


class ReviewerFinal(BaseModel):
    """Konačan payload. Nema trećeg poziva — ovo je ono što se objavljuje."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "correct", "fail_closed"]
    checks: ReviewerChecks
    fail_reason_code: Optional[Literal[FAIL_REASON_CODES]] = None
    # Popunjen za approve/correct; kod 'correct' je to ISPRAVLJENA verzija.
    final: Optional[TutorDraft] = None


class UnifiedOutputError(ValueError):
    """Konačan payload je strukturno validan, ali sadržajno neupotrebljiv."""


def _require(condition, message):
    if not condition:
        raise UnifiedOutputError(message)


def validate_task(task: TaskPayload) -> None:
    _require((task.text or "").strip(), "zadatak bez teksta")
    _require(len(task.text) <= config.MAX_TASK_CHARS, "predug tekst zadatka")
    _require((task.expected_answer or "").strip(), "zadatak bez očekivanog odgovora")
    _require(len(task.expected_answer) <= config.MAX_EXPECTED_ANSWER_CHARS,
             "predug očekivani odgovor")
    _require(len(task.options) == 4, "mora postojati tačno 4 opcije")
    _require(0 <= task.correct_option_index < 4, "correct_option_index van opsega")
    for option in task.options:
        text = (option.text or "").strip()
        _require(text, "prazna opcija")
        _require(len(text) <= config.MAX_OPTION_TEXT_CHARS, "preduga opcija")


def validate_final(draft: TutorDraft, has_active_task: bool) -> None:
    """PRAVILO POLJA PO NAMJERI — jedina tabela koja odlučuje šta smije postojati.

    `has_active_task` je serverska činjenica (ne modelova): bez aktivnog zadatka
    nema šta da se ocijeni, riješi ni pojasni."""
    _require(draft.intent in INTENTS, f"nepoznata namjera '{draft.intent}'")
    _require((draft.reply or "").strip(), "prazan reply")
    _require(len(draft.reply) <= config.MAX_REPLY_CHARS, "predug reply")

    if draft.intent in TASK_INTENTS:
        _require(draft.new_task is not None, f"namjera '{draft.intent}' traži new_task")
        validate_task(draft.new_task)
    else:
        _require(draft.new_task is None,
                 f"namjera '{draft.intent}' ne smije nositi new_task")

    if draft.intent in DIFFICULTY_SHIFT_INTENTS:
        _require(draft.difficulty_diagnostics is not None,
                 f"namjera '{draft.intent}' traži dijagnostiku težine")
    if draft.intent == "hint_request":
        _require((draft.hint or "").strip(), "hint_request bez hinta")
    if draft.intent == "full_solution_request":
        _require((draft.worked_solution or "").strip(),
                 "full_solution_request bez postupka")
    if draft.intent == "answer_attempt":
        _require(draft.grading is not None, "answer_attempt bez ocjene")
    else:
        _require(draft.grading is None,
                 f"namjera '{draft.intent}' ne smije nositi ocjenu")

    # Namjere koje se oslanjaju na aktivan zadatak ne smiju se pojaviti bez njega.
    if draft.intent in ("answer_attempt", "hint_request", "full_solution_request"):
        _require(has_active_task,
                 f"namjera '{draft.intent}' bez aktivnog zadatka")

    for field_name in ("hint", "worked_solution"):
        value = getattr(draft, field_name)
        if value is not None:
            _require(len(value) <= config.MAX_REPLY_CHARS, f"predug {field_name}")


def validate_reviewer(reviewer: ReviewerFinal) -> None:
    """Recenzentov ishod mora biti interno dosljedan.

    Odobrenje uz oborenu provjeru je kontradikcija — takav payload se tretira
    kao pad, ne kao odobrenje."""
    if reviewer.decision == "fail_closed":
        _require(reviewer.fail_reason_code is not None,
                 "fail_closed bez razloga")
        return

    _require(reviewer.final is not None,
             f"odluka '{reviewer.decision}' bez konačnog payloada")
    checks = reviewer.checks
    failed = [
        name for name in (
            "math_correct", "marked_option_correct", "inside_lesson",
            "intent_handled", "task_solvable_and_unambiguous", "mathjax_valid",
            "language_age_appropriate", "response_addresses_student",
        )
        if not getattr(checks, name)
    ]
    _require(not failed, f"odobreno uprkos oborenim provjerama: {failed}")

    if reviewer.final.intent in TASK_INTENTS:
        _require(checks.independently_solved,
                 "zadatak odobren bez nezavisnog rješavanja")
        _require((checks.independent_answer or "").strip(),
                 "nezavisno rješenje je prazno")
    if reviewer.final.intent in DIFFICULTY_SHIFT_INTENTS:
        _require(checks.difficulty_direction_correct,
                 "promjena težine odobrena bez potvrđenog smjera")
