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
import re
import unicodedata
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
    # Reviewer computes this independently; the server transfers it into the
    # authoritative final task package.
    reviewed_difficulty_evidence: Optional[DifficultyEvidence] = None


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
        # KALIBRACIJA (tri žive kampanje, 110 pokušaja u posljednjoj): 22 od 24
        # odbijanja bila su na nivou 1, a 18 od tih 22 imalo je najminimalniji
        # mogući dokaz — jedan korak rezonovanja i jedan uslov, uz jednu promjenu
        # zapisa (14x) ili dvije operacije (4x). „Koji razlomak je jednak $0,5$?“
        # ima tačno jednu promjenu zapisa, a ta promjena JESTE vještina lekcije;
        # „uvrsti pa izračunaj“ ima dvije operacije u jednom koraku.
        #
        # Zato se pomjeraju TAČNO DVA praga: `representation_change_count <= 1`
        # (bilo `== 0`) i `operation_count <= 2` (bilo `<= 1`). Broj koraka
        # rezonovanja i broj uslova ostaju na 1, i SVAKA zastavica i dalje
        # diskvalifikuje nivo 1 — višekorakan, višeuslovni, dokazni i
        # konstruktivni zadatak ostaju blokirani, kao i tri operacije.
        if (evidence.reasoning_steps > 1 or evidence.condition_count > 1
                # operation_count counts meaningful connected mathematical
                # operations, not every token or arithmetic symbol.
                or evidence.operation_count > 2
                or evidence.representation_change_count > 1
                or evidence.requires_explanation or evidence.requires_comparison
                or evidence.requires_construction or evidence.requires_proof_or_justification
                or evidence.combines_concepts):
            errors.append("level_1_is_not_direct_introductory_application")
    elif target_level == 2:
        if not (evidence.reasoning_steps >= 2 or evidence.condition_count >= 2
                or evidence.operation_count >= 2
                or evidence.representation_change_count >= 1
                or evidence.requires_explanation or evidence.requires_comparison
                or (evidence.combines_concepts and (
                    evidence.reasoning_steps >= 2 or evidence.condition_count >= 2
                    or evidence.operation_count >= 2))):
            errors.append("level_2_lacks_connected_reasoning_or_explanation")
        # Level 2 accepts a bounded pair of related rules/concepts, simple
        # comparison, and one manageable representation change. Advanced
        # depth is identified by a specific dimension rather than by the
        # generic combines_concepts flag.
        if evidence.requires_construction:
            errors.append("level_2_requires_construction")
        if evidence.requires_proof_or_justification:
            errors.append("level_2_requires_proof")
        if evidence.reasoning_steps > 2:
            errors.append("level_2_has_too_many_reasoning_steps")
        if evidence.condition_count > 2:
            errors.append("level_2_has_too_many_conditions")
        if evidence.operation_count > 2:
            errors.append("level_2_has_too_many_operations")
        if evidence.representation_change_count > 1:
            errors.append("level_2_has_advanced_representation_change")
    elif target_level == 3:
        if not (evidence.requires_construction or evidence.requires_proof_or_justification
                or evidence.condition_count >= 3 or evidence.operation_count >= 3
                or evidence.reasoning_steps >= 3
                or evidence.representation_change_count >= 2):
            errors.append("level_3_lacks_advanced_requirement")
    else:
        errors.append("invalid_target_difficulty_level")
    return tuple(errors)


def validate_difficulty_evidence(task: TaskPayload) -> None:
    errors = difficulty_evidence_errors(task.difficulty_evidence, task.target_difficulty_level)
    _require(not errors, "difficulty evidence: " + ",".join(errors))


# Bounded, sigurna dijagnostika kontradiktorne recenzentove odluke. Sadrži SAMO
# strukturisane brojeve i kodove validatora — nikad prompt, tekst zadatka,
# skriveno rezonovanje ni sirov izlaz modela (vidi CLAUDE.md, pravilo 7).
REVIEWER_EVIDENCE_OUTSIDE_TARGET = "reviewer_approved_difficulty_evidence_outside_target"


def evidence_diagnostics(evidence: DifficultyEvidence) -> str:
    """Kratak, ograničen opis dokaza — staje u granicu reda u logu.

    JAVNA je da bi je i preflight nad Tutorovim nacrtom koristio doslovno istu
    (matbot/tutor/package_preflight.py): jedan format dokaza u cijelom projektu,
    pa se nalaz prije i poslije drugog poziva poredi bez prevođenja."""
    flags = ",".join(name for name, value in (
        ("explanation", evidence.requires_explanation),
        ("comparison", evidence.requires_comparison),
        ("construction", evidence.requires_construction),
        ("proof", evidence.requires_proof_or_justification),
        ("combines", evidence.combines_concepts),
    ) if value) or "-"
    return (f"steps={evidence.reasoning_steps} "
            f"conditions={evidence.condition_count} "
            f"operations={evidence.operation_count} "
            f"representation_changes={evidence.representation_change_count} "
            f"flags={flags}")


def _require(condition, message):
    if not condition:
        raise UnifiedOutputError(message)


# ---------------------------------------------------------------------------
# NEPOTPUN TEKST ZADATKA (živa kampanja Talas A + B)
# ---------------------------------------------------------------------------
# ŽIVI NALAZ — četiri OBJAVLJENA zadatka bez ijednog matematičkog objekta
# (scenariji A25, B30, B02 i B42 dvije žive kampanje; lekcije se namjerno ne
# navode jer ovaj modul ne smije poznavati nijednu):
#
#   „Izračunaj vrijednost izraza:“       recenzent: approve   (dva puta)
#   „Riješi jednačinu:“                  recenzent: correct
#   „Riješi sistem linearnih jednačina:“ recenzent: correct
#
# Svaki je imao četiri numeričke opcije i označenu tačnu — a učenik nije imao
# šta da riješi. Nijedan postojeći sloj to nije mogao vidjeti: `mathsafe` nema
# šta da sanitizuje, `mathcheck` nema jednakost, `option_equivalence` vidi
# četiri različite vrijednosti, `mcq_integrity` nije primjenjiv, a recenzent je
# vratio `task_solvable_and_unambiguous=true`.
#
# ZAŠTO NE „svaki tekst koji završava dvotačkom“: legitiman MCQ smije završiti
# dvotačkom kad OPCIJE same dopunjuju pitanje — „Odaberi tačnu tvrdnju:“,
# „Označi ispravan zapis:“, „Koji je od ponuđenih odgovora tačan:“. Široki
# uslov bi odbio ispravne zadatke, a to je gore od promašaja.
#
# ZAŠTO NI „fraza + nema cifre i nema $…$ igdje u tekstu“: ni to nije dokaz.
# Cifra, varijabla ili nevezan matematički segment mogu stajati u tekstu a da
# traženi objekat i dalje NIJE prikazan — „Zadatak 2. Riješi jednačinu:“,
# „Posmatraj $x$. Riješi jednačinu:“. Takav uslov bi te slučajeve tiho pustio,
# pa bi pravilo tvrdilo više nego što može dokazati.
#
# Zato je pravilo ANKEROVANO NA CIJELI NORMALIZOVAN TEKST: odbija se samo kad
# se cio tekst zadatka sastoji ISKLJUČIVO od imperativne fraze, uz najviše
# završnu dvotačku ili tačku. Tada je nedostatak objekta dokazan strukturno,
# bez ijedne pretpostavke o matematici.
#
# Tekstovi poput „Zadatak 2. Riješi jednačinu:“ ostaju semantički sumnjivi, ali
# ih OVO pravilo namjerno ne dira — dokazivanje da duži tekst ne sadrži potpunu
# jednačinu traži širu analizu koja nije predmet ove izmjene. Što se ne može
# dokazati, preskače se; preskočeno nije dokaz ispravnosti.
INCOMPLETE_TASK_TEXT_CODE = "incomplete_task_text"

# Cio tekst = samo imperativ + eventualna završna interpunkcija. Ništa drugo.
_INCOMPLETE_TASK_TEXT_RE = re.compile(
    r"(?i)\A(?:"
    r"izra[čc]unaj(?:te)?\s+(?:vrijednost\s+)?izraz\w*"
    r"|rije[šs]i(?:te)?\s+(?:nejedna[čc]in\w*|jedna[čc]in\w*"
    r"|sistem(?:\s+linearnih)?(?:\s+jedna[čc]in\w*)?)"
    r"|pojednostavi(?:te)?\s+izraz\w*"
    r"|uprosti(?:te)?\s+izraz\w*"
    r")\s*[:.]?\s*\Z"
)


def _normalized_task_text(text) -> str:
    """NFKC + sažimanje razmaka: model varira formatiranje, ne suštinu.

    NFKC svodi kompatibilne oblike (široka dvotačka, nerazdvojni razmak) i
    sastavlja dekomponovane dijakritike, pa „Riješi“ pisano na dva načina daje
    isti rezultat."""
    body = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", body).strip()


def incomplete_task_request(text) -> str:
    """Vrati normalizovan tekst kad se zadatak sastoji SAMO od imperativa.

    Prazan string znači „ne može se dokazati da nešto nedostaje“ — isti princip
    kao svi ostali validatori u projektu: što se ne može dokazati, preskače se."""
    body = _normalized_task_text(text)
    return body if body and _INCOMPLETE_TASK_TEXT_RE.match(body) else ""


def validate_task(task: TaskPayload) -> None:
    _require((task.selected_lesson_id or "").strip(), "task without lesson ID")
    _require((task.selected_lesson_title or "").strip(), "task without lesson title")
    _require((task.text or "").strip(), "zadatak bez teksta")
    _require((task.task_type or "").strip(), "task without type")
    _require(len(task.text) <= config.MAX_TASK_CHARS, "predug tekst zadatka")
    missing = incomplete_task_request(task.text)
    _require(not missing, f"{INCOMPLETE_TASK_TEXT_CODE}: tekst traži „{missing}“, "
                          "a nijedan izraz, jednačina ni sistem nije prikazan")
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
    has_final_task = reviewer.final.new_task is not None
    if has_final_task:
        _require(reviewer.reviewed_difficulty_evidence is not None,
                 "approved task without independent reviewer difficulty evidence")
    else:
        _require(reviewer.reviewed_difficulty_evidence is None,
                 "reviewer difficulty evidence without a new task")
    if checks.difficulty_evidence_valid:
        _require(reviewer.reviewed_difficulty_evidence is not None,
                 "reviewer claims valid difficulty evidence without a result")
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

    # ------------------------------------------------------------------
    # DOSLJEDNOST ODLUKE S VLASTITIM MJERODAVNIM DOKAZOM (živi gate cb80b92)
    # ------------------------------------------------------------------
    # Živi pad: recenzent je NEZAVISNO izračunao dokaz (steps=3, operations=4,
    # representation_changes=1) za zadatak čiji je traženi nivo 1, pa ipak
    # vratio `approve` uz `difficulty_evidence_valid=true`. Payload je bio
    # interno kontradiktoran, a server ga je hvatao TEK u objavi
    # (stage=publication) — dakle prekasno da recenzent upotrijebi ono što već
    # umije: `correct` s kompletnim zamjenskim zadatkom u ISTOM drugom pozivu.
    #
    # Zato se ovdje pokreće ISTI zajednički validator (`difficulty_evidence_errors`)
    # nad recenzentovim vlastitim mjerodavnim dokazom i nivoom koji je sam
    # deklarisao na konačnom zadatku. Prag težine se NE mijenja — mijenja se
    # samo trenutak kad se kontradikcija otkrije. Pravilo je univerzalno: nema
    # ni lekcije, ni oblasti, ni geometrije u njemu.
    if has_final_task:
        target_level = reviewer.final.new_task.target_difficulty_level
        evidence_errors = difficulty_evidence_errors(
            reviewer.reviewed_difficulty_evidence, target_level
        )
        _require(not evidence_errors, (
            f"{REVIEWER_EVIDENCE_OUTSIDE_TARGET}: decision={reviewer.decision} "
            f"target_level={target_level} errors={','.join(evidence_errors)} "
            f"evidence_valid={checks.difficulty_evidence_valid} "
            + evidence_diagnostics(reviewer.reviewed_difficulty_evidence)
        ))

    if reviewer.final.intent in TASK_INTENTS:
        _require(checks.independently_solved,
                 "zadatak odobren bez nezavisnog rješavanja")
        _require((checks.independent_answer or "").strip(),
                 "nezavisno rješenje je prazno")
    if reviewer.final.intent in DIFFICULTY_SHIFT_INTENTS:
        _require(checks.difficulty_direction_correct,
                 "promjena težine odobrena bez potvrđenog smjera")
