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
import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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

    id: Literal["a", "b", "c", "d"]
    text: str


class DifficultyEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_steps: int = Field(description=(
        "Meaningful connected mathematical inferences. Applying one stated rule and "
        "selecting its matching visible option is one direct step, not two."))
    condition_count: int = Field(description=(
        "Independent mathematical conditions that must all be used; answer options "
        "are not separate conditions."))
    operation_count: int = Field(description=(
        "Meaningful connected mathematical operations, not token count or every symbol."))
    representation_change_count: int = Field(description=(
        "Meaningful changes of representation required before answering."))
    requires_explanation: bool = Field(description=(
        "True only when the student must explain or justify, not when solution stores reasoning."))
    requires_comparison: bool = Field(description=(
        "True only for comparing mathematical results or properties; false for choosing "
        "one option by one directly stated rule."))
    requires_construction: bool
    requires_proof_or_justification: bool
    combines_concepts: bool


class SignatureParameter(BaseModel):
    """One closed, lesson-independent mathematical signature parameter."""

    model_config = ConfigDict(extra="forbid")

    name: str
    value: str


class TaskSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_family: str
    operation_or_relation: str
    normalized_parameters: list[SignatureParameter]
    required_conditions: list[str]
    relevant_objects: list[str]
    answer_type: str

    def canonical_json(self) -> str:
        """Stable duplicate fingerprint, independent of model list ordering.

        Parameter, condition, and object order is presentation metadata here;
        repeated values are retained so meaningful multiplicity is never lost.
        A mathematically ordered sequence must encode that order in a parameter
        value or role instead of relying on this transport list's position.
        """
        canonical = {
            "task_family": self.task_family.strip(),
            "operation_or_relation": self.operation_or_relation.strip(),
            "normalized_parameters": sorted(
                ({"name": parameter.name.strip(), "value": parameter.value.strip()}
                 for parameter in self.normalized_parameters),
                key=lambda parameter: (parameter["name"], parameter["value"]),
            ),
            "required_conditions": sorted(condition.strip()
                                          for condition in self.required_conditions),
            "relevant_objects": sorted(obj.strip() for obj in self.relevant_objects),
            "answer_type": self.answer_type.strip(),
        }
        return json.dumps(canonical, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"))

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


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

    selected_lesson_id: str
    selected_lesson_title: str
    target_difficulty_level: Literal[1, 2, 3]
    text: str
    task_type: str
    options: list[TutorOption]
    correct_option_index: int
    correct_option_id: Literal["a", "b", "c", "d"]
    expected_answer: str
    solution: str
    difficulty: Literal["easy", "standard", "hard"]
    difficulty_evidence: DifficultyEvidence
    task_signature: TaskSignature


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
    task_package_consistent: bool
    difficulty_evidence_valid: bool
    task_signature_consistent: bool


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


def difficulty_evidence_errors(evidence: DifficultyEvidence, target_level: int) -> tuple[str, ...]:
    """Shared, lesson-independent meaning of the structured 1--3 rubric.

    Level 1 deliberately examines mathematical evidence only: yes/no wording,
    recognition, classification, direct calculation, substitution, and selecting
    one option by one stated rule are equivalent direct introductory forms.
    """
    errors = []
    numeric = ("reasoning_steps", "condition_count", "operation_count",
               "representation_change_count")
    for field in numeric:
        if getattr(evidence, field) < 0:
            errors.append(f"negative_{field}")
    if target_level == 1:
        if (evidence.reasoning_steps > 1 or evidence.condition_count > 1
                # operation_count counts meaningful connected mathematical
                # operations, not every token or arithmetic symbol.
                or evidence.operation_count > 1
                or evidence.representation_change_count > 0
                or evidence.requires_explanation or evidence.requires_comparison
                or evidence.requires_construction or evidence.requires_proof_or_justification
                or evidence.combines_concepts):
            errors.append("level_1_is_not_direct_introductory_application")
    elif target_level == 2:
        if not (evidence.reasoning_steps >= 2 or evidence.condition_count >= 2
                or evidence.operation_count >= 2
                or evidence.representation_change_count >= 1
                or evidence.requires_explanation):
            errors.append("level_2_lacks_connected_reasoning_or_explanation")
        if (evidence.requires_comparison or evidence.requires_construction
                or evidence.requires_proof_or_justification or evidence.combines_concepts):
            errors.append("level_2_contains_level_3_requirement")
    elif target_level == 3:
        if not (evidence.requires_construction or evidence.requires_comparison
                or evidence.requires_proof_or_justification or evidence.combines_concepts
                or evidence.condition_count >= 3 or evidence.reasoning_steps >= 3):
            errors.append("level_3_lacks_advanced_requirement")
    else:
        errors.append("invalid_target_difficulty_level")
    return tuple(errors)


def validate_difficulty_evidence(task: TaskPayload) -> None:
    errors = difficulty_evidence_errors(task.difficulty_evidence, task.target_difficulty_level)
    _require(not errors, "difficulty evidence: " + ",".join(errors))


def _require(condition, message):
    if not condition:
        raise UnifiedOutputError(message)


def validate_task(task: TaskPayload) -> None:
    _require((task.selected_lesson_id or "").strip(), "task without lesson ID")
    _require((task.selected_lesson_title or "").strip(), "task without lesson title")
    _require((task.text or "").strip(), "zadatak bez teksta")
    _require((task.task_type or "").strip(), "task without type")
    _require(len(task.text) <= config.MAX_TASK_CHARS, "predug tekst zadatka")
    _require((task.expected_answer or "").strip(), "zadatak bez očekivanog odgovora")
    _require(len(task.expected_answer) <= config.MAX_EXPECTED_ANSWER_CHARS,
             "predug očekivani odgovor")
    _require(len(task.options) == 4, "mora postojati tačno 4 opcije")
    _require(0 <= task.correct_option_index < 4, "correct_option_index van opsega")
    option_ids = [option.id for option in task.options]
    _require(len(set(option_ids)) == len(option_ids), "duplicate option IDs")
    _require(task.correct_option_id in option_ids, "marked option does not exist")
    _require(task.options[task.correct_option_index].id == task.correct_option_id,
             "correct option ID/index mismatch")
    _require((task.solution or "").strip(), "task without solution")
    _require(bool((task.task_signature.task_family or "").strip()), "empty task family signature")
    _require(bool((task.task_signature.operation_or_relation or "").strip()),
             "empty operation signature")
    for field in ("reasoning_steps", "condition_count", "operation_count",
                  "representation_change_count"):
        _require(getattr(task.difficulty_evidence, field) >= 0,
                 f"negative difficulty evidence: {field}")
    for option in task.options:
        text = (option.text or "").strip()
        _require(text, "prazna opcija")
        _require(len(text) <= config.MAX_OPTION_TEXT_CHARS, "preduga opcija")


def normalize_for_intent(draft: TutorDraft) -> TutorDraft:
    """Očisti polja koja IZABRANA NAMJERA ne koristi — umjesto da ih odbiješ.

    ZAŠTO POSTOJI (ručni test, 2026-08-03): uredan `generate_task` je padao
    zatvoreno jer je Reviewer u konačnom payloadu ostavio `grading`, a pravilo
    polja je „višak“ tretiralo kao grešku. Matematika je bila ispravna, zadatak
    upotrebljiv, a učenik nije dobio ništa.

    Suvišno polje NE MOŽE proizvesti pogrešan odgovor — ono se ne čita. Prazan
    turn može. Zato se višak TIHO BRIŠE, a odbijanje ostaje samo za polje koje
    namjeri STVARNO nedostaje (to i dalje provjerava `validate_final`).

    Nikad ne dodaje sadržaj i nikad ne mijenja namjeru — samo prazni."""
    updates = {}
    if draft.intent not in TASK_INTENTS and draft.new_task is not None:
        updates["new_task"] = None
    if draft.intent != "answer_attempt" and draft.grading is not None:
        updates["grading"] = None
    if draft.intent not in DIFFICULTY_SHIFT_INTENTS and draft.difficulty_diagnostics is not None:
        updates["difficulty_diagnostics"] = None
    if not updates:
        return draft
    return draft.model_copy(update=updates)


def validate_final(draft: TutorDraft, has_active_task: bool) -> None:
    """PRAVILO POLJA PO NAMJERI — jedina tabela koja odlučuje šta smije postojati.

    `has_active_task` je serverska činjenica (ne modelova): bez aktivnog zadatka
    nema šta da se ocijeni, riješi ni pojasni."""
    _require(draft.intent in INTENTS, f"nepoznata namjera '{draft.intent}'")
    _require((draft.reply or "").strip(), "prazan reply")
    _require(len(draft.reply) <= config.MAX_REPLY_CHARS, "predug reply")

    # NAPOMENA: provjerava se samo ono što namjeri NEDOSTAJE. Višak polja je
    # već obrisan u `normalize_for_intent` — vidi tamo zašto se ne odbija.
    if draft.intent in TASK_INTENTS:
        _require(draft.new_task is not None, f"namjera '{draft.intent}' traži new_task")

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
            "task_package_consistent", "difficulty_evidence_valid",
            "task_signature_consistent",
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
