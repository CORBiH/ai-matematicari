"""Recenzentova odluka mora pratiti NJEGOV VLASTITI mjerodavan dokaz težine.

ŽIVI NALAZ (release gate cb80b92, scenario 10 od 12, lekcija 7. razreda
„Simetrale stranica i centar opisane kružnice“, traženi nivo 1):

    Tutor:     zadatak koji iz $a=5$, $b=6$, $c=7$ IZVODI poluprečnik opisane
               kružnice — višekorakan račun označen kao nivo 1
    Tutor dokaz:      steps=2 conditions=2 operations=3 representation=1
    Recenzent dokaz:  steps=3 conditions=2 operations=4 representation=1
    Recenzent odluka: approve  (uz difficulty_evidence_valid=true)
    Server:    odbio TEK u objavi → `level_1_is_not_direct_introductory_application`

Sve prije toga je prošlo: identitet lekcije, MCQ paket, potpis, dva poziva.
Payload je bio INTERNO KONTRADIKTORAN — recenzent je sam izmjerio da zadatak
nije nivo 1 pa ga svejedno odobrio — a server je to otkrivao prekasno da bi
recenzent upotrijebio ono što već umije: `correct` s KOMPLETNIM zamjenskim
zadatkom u istom (drugom i posljednjem) pozivu.

Ovaj modul dokazuje:
  • odobrenje s dokazom van traženog nivoa je DETERMINISTIČKI nemoguće;
  • ispravka sa zamjenskim zadatkom prolazi u ISTA DVA poziva;
  • pravilo je univerzalno — isti ishod na geometriji, algebri, razlomcima i
    vjerovatnoći, bez ijedne grane po lekciji u produkcijskom kodu;
  • odbijanje ostaje transakciono i nikad ne pravi treći poziv.
"""
import copy

import pytest

from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.lesson_context import build
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from matbot.tutor.schema import (REVIEWER_EVIDENCE_OUTSIDE_TARGET, DifficultyEvidence,
                                 ReviewerChecks, ReviewerFinal, SignatureParameter,
                                 TaskPayload, TaskSignature, TutorDraft, TutorOption,
                                 UnifiedOutputError, difficulty_evidence_errors,
                                 validate_reviewer)
from tests.conftest import FakeLLM

SESSION = "target-level"


def turn(grade, topic, message="Daj mi zadatak."):
    return {
        "session_id": SESSION, "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def evidence(**updates):
    """Ispravan dokaz nivoa 1; `updates` ga namjerno izvodi van nivoa."""
    values = dict(
        reasoning_steps=1, condition_count=1, operation_count=1,
        representation_change_count=0, requires_explanation=False,
        requires_comparison=False, requires_construction=False,
        requires_proof_or_justification=False, combines_concepts=False,
    )
    values.update(updates)
    return DifficultyEvidence(**values)


# Dokaz koji je recenzent NEZAVISNO prijavio u živom padu.
LIVE_REVIEWER_EVIDENCE = evidence(
    reasoning_steps=3, condition_count=2, operation_count=4,
    representation_change_count=1,
)
# Dokaz koji je Tutor prijavio za isti zadatak.
LIVE_TUTOR_EVIDENCE = evidence(
    reasoning_steps=2, condition_count=2, operation_count=3,
    representation_change_count=1,
)


def task(context, text, options, *, level=1, correct=0, signature="one",
         task_evidence=None):
    return TaskPayload(
        selected_lesson_id=context.topic_id, selected_lesson_title=context.title,
        target_difficulty_level=level, text=text, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=correct, correct_option_id="abcd"[correct],
        expected_answer=options[correct],
        solution=f"Tačan odgovor je: {options[correct]}",
        difficulty=("easy", "standard", "hard")[level - 1],
        difficulty_evidence=task_evidence if task_evidence is not None else evidence(),
        task_signature=TaskSignature(
            task_family="generic", operation_or_relation="recognition",
            normalized_parameters=[SignatureParameter(name="case", value=signature)],
            required_conditions=["valid"], relevant_objects=["concept"],
            answer_type="multiple_choice",
        ),
    )


def checks(**changes):
    base = dict(math_correct=True, marked_option_correct=True, inside_lesson=True,
                intent_handled=True, difficulty_direction_correct=True,
                response_addresses_student=True, task_solvable_and_unambiguous=True,
                mathjax_valid=True, language_age_appropriate=True,
                independently_solved=True, independent_answer="provjereno",
                task_package_consistent=True, difficulty_evidence_valid=True,
                task_signature_consistent=True,
                stem_requires_student_reasoning=True)
    base.update(changes)
    return ReviewerChecks(**base)


def queue(fake, draft_task, *, decision="approve", final_task=..., reviewed=...):
    """Pripremi TAČNO dva odgovora: Tutor nacrt pa recenzentovu odluku."""
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="izabrana lekcija", new_task=draft_task)
    final_task = draft_task if final_task is ... else final_task
    reviewed = final_task.difficulty_evidence if reviewed is ... else reviewed
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision=decision, checks=checks(),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=reviewed,
    ))
    return draft


# ---------------------------------------------------------------------------
# ŽIVI PODACI 10. SCENARIJA — kao ČISTI PODACI, nikad kao grana u produkciji
# ---------------------------------------------------------------------------

LIVE_GRADE, LIVE_TOPIC = 7, "7-04-021"
LIVE_TASK_TEXT = (
    "U trouglu su dužine stranica $a=5$, $b=6$ i $c=7$. "
    "Izračunaj poluprečnik opisane kružnice $r_o$. "
    "Koja od ponuđenih vrijednosti je tačna?"
)
LIVE_TASK_OPTIONS = ("$3,57$", "$2,50$", "$4,00$", "$5,00$")
LIVE_REPLACEMENT_TEXT = "Kako se zove tačka u kojoj se sijeku simetrale stranica trougla?"
LIVE_REPLACEMENT_OPTIONS = (
    "Centar opisane kružnice", "Težište trougla",
    "Centar upisane kružnice", "Ortocentar trougla",
)


# ---------------------------------------------------------------------------
# 1) MJESTO GDJE JE INVARIJANTA NEDOSTAJALA — na nivou šeme
# ---------------------------------------------------------------------------

def test_shared_validator_proves_the_live_evidence_is_not_level_one():
    assert difficulty_evidence_errors(LIVE_REVIEWER_EVIDENCE, 1) == (
        "level_1_is_not_direct_introductory_application",
    )
    # Isti dokaz je posve valjan za nivo 3 — prag se NE mijenja ovom izmjenom.
    assert difficulty_evidence_errors(LIVE_REVIEWER_EVIDENCE, 3) == ()


@pytest.mark.parametrize("decision", ["approve", "correct"])
def test_validate_reviewer_rejects_evidence_outside_declared_target(decision):
    """TAČNA kontradikcija iz živog pada: odluka protiv vlastitog dokaza."""
    context = build(LIVE_GRADE, LIVE_TOPIC)
    payload = task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                   task_evidence=LIVE_REVIEWER_EVIDENCE)
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="opisana kružnica", new_task=payload)
    reviewer = ReviewerFinal(decision=decision, checks=checks(), final=draft,
                             reviewed_difficulty_evidence=LIVE_REVIEWER_EVIDENCE)

    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(reviewer)
    assert REVIEWER_EVIDENCE_OUTSIDE_TARGET in str(error.value)


def test_validate_reviewer_still_accepts_evidence_that_satisfies_the_target():
    """Invarijanta ne smije odbiti ispravan paket — prag ostaje isti."""
    context = build(LIVE_GRADE, LIVE_TOPIC)
    payload = task(context, LIVE_REPLACEMENT_TEXT, LIVE_REPLACEMENT_OPTIONS)
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="opisana kružnica", new_task=payload)
    validate_reviewer(ReviewerFinal(
        decision="approve", checks=checks(), final=draft,
        reviewed_difficulty_evidence=evidence(),
    ))


def test_contradiction_diagnostic_carries_only_bounded_safe_fields():
    """Dijagnostika nosi tražena polja i NIŠTA od prompta/zadatka/rezonovanja."""
    context = build(LIVE_GRADE, LIVE_TOPIC)
    payload = task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                   task_evidence=LIVE_REVIEWER_EVIDENCE)
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="opisana kružnica", new_task=payload)
    with pytest.raises(UnifiedOutputError) as error:
        validate_reviewer(ReviewerFinal(
            decision="approve", checks=checks(), final=draft,
            reviewed_difficulty_evidence=LIVE_REVIEWER_EVIDENCE))
    detail = str(error.value)

    for required in ("decision=approve", "target_level=1",
                     "errors=level_1_is_not_direct_introductory_application",
                     "evidence_valid=True", "steps=3", "conditions=2",
                     "operations=4", "representation_changes=1", "flags=-"):
        assert required in detail, required
    # Nikad sadržaj: ni tekst zadatka, ni opcije, ni naslov lekcije.
    for leaked in (LIVE_TASK_TEXT, "3,57", context.title, "Evo zadatka",
                   "opisana kružnica"):
        assert leaked not in detail, leaked
    # Ostaje unutar granice reda u logu (matbot/tutor/pipeline.py `_clip`).
    assert len(detail) <= 300


# ---------------------------------------------------------------------------
# 2) TAČAN ŽIVI SCENARIO KROZ CIJELI DVOPOZIVNI PUT
# ---------------------------------------------------------------------------

def test_live_invalid_approval_is_rejected_as_reviewer_payload(monkeypatch, caplog):
    """A) Nevažeće odobrenje: odbija se PRIJE objave, bez mutacije stanja."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(LIVE_GRADE, LIVE_TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                     signature="live-circumradius",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="approve", reviewed=LIVE_REVIEWER_EVIDENCE)

    response = run_practice_turn(store, fake, turn(LIVE_GRADE, LIVE_TOPIC))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response          # frontend zadržava svoje stanje
    assert store.peek(SESSION) is None       # ništa nije objavljeno
    assert fake.call_count == 2              # bez retryja i bez trećeg poziva
    # Gate ovo mora klasifikovati kao reviewer_payload_rejection, NE publication.
    assert "stage=reviewer_payload" in caplog.text
    assert "stage=publication" not in caplog.text
    assert REVIEWER_EVIDENCE_OUTSIDE_TARGET in caplog.text


def test_live_reviewer_correction_publishes_in_the_same_two_calls(monkeypatch):
    """B) Ispravka: kompletan zamjenski zadatak nivoa 1 se objavljuje."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(LIVE_GRADE, LIVE_TOPIC), SessionStore(), FakeLLM()
    replacement = task(context, LIVE_REPLACEMENT_TEXT, LIVE_REPLACEMENT_OPTIONS,
                       signature="direct-recognition")
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                     signature="live-circumradius",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="correct", final_task=replacement, reviewed=evidence())

    response = run_practice_turn(store, fake, turn(LIVE_GRADE, LIVE_TOPIC))
    session = store.peek(SESSION)

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert len(fake.tutor_calls) == 1 and len(fake.reviewer_calls) == 1
    # Objavljen je RECENZENTOV zamjenski zadatak, ne Tutorov nacrt.
    assert session["current_task"] == LIVE_REPLACEMENT_TEXT
    assert LIVE_TASK_TEXT not in response["answer"]
    assert response["answer"].startswith("Evo zadatka.")
    assert session["lesson_id"] == LIVE_TOPIC
    assert session["difficulty_level"] == 1
    # Mjerodavan dokaz u sesiji je RECENZENTOV.
    assert session["current_task_difficulty_evidence"] == evidence().model_dump()
    assert set(option["text"] for option in session["current_options"]) == set(
        LIVE_REPLACEMENT_OPTIONS)


def test_live_reviewer_fail_closed_when_correction_is_impossible(monkeypatch):
    """Kad se ne može sigurno ispraviti — fail_closed, i dalje dva poziva."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(LIVE_GRADE, LIVE_TOPIC), SessionStore(), FakeLLM()
    draft = TutorDraft(
        intent="generate_task", reply="Evo zadatka.", lesson_focus="opisana kružnica",
        new_task=task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                      task_evidence=LIVE_TUTOR_EVIDENCE))
    fake.queue(draft)
    fake.queue(ReviewerFinal(decision="fail_closed", checks=checks(),
                             fail_reason_code="unsafe_or_unverifiable",
                             final=None, reviewed_difficulty_evidence=None))

    assert run_practice_turn(store, fake,
                             turn(LIVE_GRADE, LIVE_TOPIC))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2


def test_invalid_approval_preserves_a_previously_published_session(monkeypatch):
    """Odbijanje je transakciono: prethodno objavljen zadatak ostaje netaknut."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(LIVE_GRADE, LIVE_TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, LIVE_REPLACEMENT_TEXT, LIVE_REPLACEMENT_OPTIONS,
                     signature="committed-first"))
    assert run_practice_turn(store, fake, turn(LIVE_GRADE, LIVE_TOPIC))["status"] == "ready"
    before = copy.deepcopy(store.peek(SESSION))

    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                     signature="rejected-second", task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="approve", reviewed=LIVE_REVIEWER_EVIDENCE)
    response = run_practice_turn(store, fake, turn(LIVE_GRADE, LIVE_TOPIC))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) == before
    assert fake.call_count == 4   # 2 + 2, nikad 3 po turnu


# ---------------------------------------------------------------------------
# 3) ISTO PRAVILO KROZ RAZLIČITE OBLIKE LEKCIJA (bez grana u produkciji)
# ---------------------------------------------------------------------------
# Svaki red je ČIST PODATAK: (razred, lekcija, previše složen nacrt, direktna
# zamjena nivoa 1). Produkcijski kod ne zna nijednu od ovih lekcija.
CROSS_DOMAIN = [
    pytest.param(
        LIVE_GRADE, LIVE_TOPIC,
        (LIVE_TASK_TEXT, LIVE_TASK_OPTIONS),
        (LIVE_REPLACEMENT_TEXT, LIVE_REPLACEMENT_OPTIONS),
        id="geometry-derivation-to-recognition"),
    pytest.param(
        9, "9-04-004",
        ("Riješi jednačinu $\\frac{x}{2} + \\frac{x}{3} = 5$, zatim provjeri "
         "rješenje i izračunaj $2x$. Koja vrijednost je tačna?",
         ("$12$", "$6$", "$5$", "$10$")),
        ("Da li je $x=2$ rješenje jednačine $\\frac{x}{2}=1$?",
         ("Da", "Ne", "Samo za $x=0$", "Nije moguće odrediti")),
        id="algebra-multistep-to-direct-substitution"),
    # 6-04-014 je BEZ ugovora. Lekcije s ugovorom (6-04-005/006/009/010/011/012)
    # po projektnoj odluci zadržavaju deterministički jednopozivni generator kad
    # je kontroler nivoa uključen (matbot/practice.py), pa uopšte ne prolaze kroz
    # Tutor+Reviewer i ne bi ni mogle dokazati ovo pravilo.
    pytest.param(
        6, "6-04-014",
        ("Izračunaj $\\frac{1}{7} + \\frac{2}{7} + \\frac{3}{7}$, pa oduzmi "
         "$\\frac{4}{7}$ i skrati rezultat. Koja vrijednost je tačna?",
         ("$\\frac{2}{7}$", "$\\frac{6}{7}$", "$\\frac{1}{7}$", "$\\frac{3}{7}$")),
        ("Izračunaj $\\frac{1}{5} + \\frac{2}{5}$.",
         ("$\\frac{3}{5}$", "$\\frac{3}{10}$", "$\\frac{2}{5}$", "$\\frac{1}{5}$")),
        id="fractions-chained-to-single-operation"),
    pytest.param(
        8, "8-06-013",
        ("U kutiji su crvene i plave kuglice. Odredi vjerovatnoću da izvučena "
         "kuglica nije crvena, pa je uporedi s vjerovatnoćom da jeste crvena i "
         "objasni koja je veća. Koja tvrdnja je tačna?",
         ("Veća je da nije crvena.", "Veća je da jeste crvena.",
          "Jednake su.", "Ne može se odrediti.")),
        ("Vjerovatnoća događaja je $0,3$. Kolika je vjerovatnoća komplementarnog događaja?",
         ("$0,7$", "$0,3$", "$1,3$", "$0,5$")),
        id="probability-interpretation-to-property-recognition"),
]


@pytest.mark.parametrize("grade,topic,too_hard,direct", CROSS_DOMAIN)
def test_cross_domain_invalid_approval_always_rejects(monkeypatch, caplog, grade, topic,
                                                      too_hard, direct):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(grade, topic), SessionStore(), FakeLLM()
    queue(fake, task(context, too_hard[0], too_hard[1], signature=f"hard-{topic}",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="approve", reviewed=LIVE_REVIEWER_EVIDENCE)

    assert run_practice_turn(store, fake, turn(grade, topic))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2
    # Kontradikcija se hvata na RECENZENTU, ne u objavi — u svakoj oblasti.
    # Bez ovoga test ne bi razlikovao novu invarijantu od zatečene provjere u
    # objavi (koja je isti paket odbijala, ali prekasno i pogrešno klasifikovano).
    assert "stage=reviewer_payload" in caplog.text
    assert "stage=publication" not in caplog.text
    assert REVIEWER_EVIDENCE_OUTSIDE_TARGET in caplog.text


@pytest.mark.parametrize("grade,topic,too_hard,direct", CROSS_DOMAIN)
def test_cross_domain_reviewer_correction_always_publishes(monkeypatch, grade, topic,
                                                           too_hard, direct):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(grade, topic), SessionStore(), FakeLLM()
    replacement = task(context, direct[0], direct[1], signature=f"direct-{topic}")
    queue(fake, task(context, too_hard[0], too_hard[1], signature=f"hard-{topic}",
                     task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="correct", final_task=replacement, reviewed=evidence())

    response = run_practice_turn(store, fake, turn(grade, topic))
    session = store.peek(SESSION)

    assert response["status"] == "ready"
    assert fake.call_count == 2
    assert session["current_task"] == direct[0]
    assert session["difficulty_level"] == 1
    assert session["current_task_difficulty_evidence"] == evidence().model_dump()
    assert response["answer"].startswith("Evo zadatka.")


def test_no_lesson_specific_branch_backs_these_regressions():
    """Sve gore prolazi iz PODATAKA — produkcijski modul ne zna nijednu lekciju.

    Ne provjerava se prisustvo pojmova iz geometrije/vjerovatnoće: zajednička
    matematička pravila (matbot/rules.py) legitimno nose pojmovnik za razred i
    oblast, a bosanski komentari po konvenciji projekta citiraju živi nalaz.
    Ono što BI značilo granu po lekciji jeste ID lekcije — njega ovdje ne smije
    biti ni u jednom modulu univerzalnog puta."""
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parent.parent
    lesson_id = re.compile(r"\b\d-\d{2}-\d{3}\b")
    for path in (root / "matbot" / "tutor").rglob("*.py"):
        assert not lesson_id.search(path.read_text(encoding="utf-8")), path.name


@pytest.mark.parametrize("grade,topic,_hard,_direct", CROSS_DOMAIN)
def test_target_level_rules_are_byte_identical_for_every_lesson(grade, topic, _hard, _direct):
    """Isto pravilo nivoa stiže SVAKOJ lekciji — nema teksta krojenog po lekciji."""
    tutor = tutor_prompts.build_tutor_instructions(build(grade, topic))
    reviewer = tutor_prompts.build_reviewer_instructions(build(grade, topic))
    assert tutor_prompts._TARGET_LEVEL_RULE in tutor
    assert tutor_prompts._REVIEWER_TARGET_LEVEL_RULE in reviewer


# ---------------------------------------------------------------------------
# 4) ISPRAVKA SAMO METAPODATAKA NE SMIJE BITI PREČICA
# ---------------------------------------------------------------------------
# GRANICA KOJU OVDJE PRIZNAJEMO: server nema (i po odluci projekta ne smije
# imati) semantički parser za svih 534 lekcije, pa ne može DOKAZATI da je
# recenzent slagao brojeve o zadatku koji vidi. Ono što se deterministički
# drži jeste STRUKTURA: ispravljen paket prolazi kroz svaku zatečenu provjeru,
# potpis mora biti svjež, a objavljeni dokaz je doslovno recenzentova tvrdnja
# (pa je revizibilna). Poštenje brojeva nosi recenzentov prompt.

def test_reviewer_instructions_forbid_relabeling_and_dishonest_counts():
    instructions = tutor_prompts.build_reviewer_instructions(build(LIVE_GRADE, LIVE_TOPIC))
    lowered = instructions.lower()
    # F5J: ista zabrana, pojačana formulacija uz izričite pragove cilja.
    assert "you must not approve" in lowered
    assert "active difficulty targets" in lowered
    assert "do not merely relabel the same task" in lowered
    assert "never lower reasoning_steps" in lowered
    assert "replace the whole task" in lowered
    assert "fail_closed" in lowered


def test_tutor_instructions_forbid_multistep_task_labelled_level_one():
    instructions = tutor_prompts.build_tutor_instructions(build(LIVE_GRADE, LIVE_TOPIC))
    lowered = instructions.lower()
    assert "never derive a multi-step result and label it level 1" in lowered
    assert "honestly describe the task you actually wrote" in lowered
    # Univerzalno pravilo: nijedan ID lekcije ne smije ući u prompt kao grana.
    # (Pojmovnik geometrije dolazi iz zajedničkih pravila za razred i oblast i
    # legitimno postoji za SVAKU lekciju te oblasti — vidi test iznad.)
    assert "7-04-021" not in instructions


def test_metadata_only_correction_still_faces_every_structural_check(monkeypatch):
    """Spuštanje brojeva ne zaobilazi nijednu zatečenu provjeru paketa.

    Recenzent „popravi“ samo dokaz na valjan nivo 1, ali ostavi paket u kojem
    `expected_answer` više ne odgovara označenoj opciji. Paket i dalje pada —
    ispravka nije propusnica."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(LIVE_GRADE, LIVE_TOPIC), SessionStore(), FakeLLM()
    broken = task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                  signature="metadata-only").model_copy(
        update={"expected_answer": "$9,99$"})
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                     signature="metadata-only", task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="correct", final_task=broken, reviewed=evidence())

    assert run_practice_turn(store, fake,
                             turn(LIVE_GRADE, LIVE_TOPIC))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) is None
    assert fake.call_count == 2


def test_replacement_reusing_a_published_signature_is_rejected_as_duplicate(monkeypatch):
    """Zamjenski zadatak mora nositi SVJEŽ potpis, ne recikliran."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(LIVE_GRADE, LIVE_TOPIC), SessionStore(), FakeLLM()
    queue(fake, task(context, LIVE_REPLACEMENT_TEXT, LIVE_REPLACEMENT_OPTIONS,
                     signature="published-once"))
    assert run_practice_turn(store, fake, turn(LIVE_GRADE, LIVE_TOPIC))["status"] == "ready"
    before = copy.deepcopy(store.peek(SESSION))

    recycled = task(context, LIVE_REPLACEMENT_TEXT, LIVE_REPLACEMENT_OPTIONS,
                    signature="published-once")
    queue(fake, task(context, LIVE_TASK_TEXT, LIVE_TASK_OPTIONS,
                     signature="hard-draft", task_evidence=LIVE_TUTOR_EVIDENCE),
          decision="correct", final_task=recycled, reviewed=evidence())

    assert run_practice_turn(store, fake,
                             turn(LIVE_GRADE, LIVE_TOPIC))["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(SESSION) == before
    assert fake.call_count == 4


def test_published_evidence_is_verbatim_the_reviewer_claim(monkeypatch):
    """Objavljen dokaz je doslovno recenzentova tvrdnja — dakle revizibilna.

    Server ne „popravlja“ brojeve i ne uzima Tutorove: u sesiju ide tačno ono
    što je recenzent nezavisno prijavio, pa se svaka nepoštena tvrdnja vidi u
    zapisu umjesto da se stopi s Tutorovom."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    context, store, fake = build(LIVE_GRADE, LIVE_TOPIC), SessionStore(), FakeLLM()
    tutor_evidence = evidence(condition_count=1, operation_count=1)
    reviewer_evidence = evidence(reasoning_steps=1, condition_count=1,
                                 operation_count=1)
    queue(fake, task(context, LIVE_REPLACEMENT_TEXT, LIVE_REPLACEMENT_OPTIONS,
                     signature="verbatim", task_evidence=tutor_evidence),
          decision="approve", reviewed=reviewer_evidence)

    assert run_practice_turn(store, fake, turn(LIVE_GRADE, LIVE_TOPIC))["status"] == "ready"
    stored = store.peek(SESSION)["current_task_difficulty_evidence"]
    assert stored == reviewer_evidence.model_dump()


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija (Batch #2): unakrsno-domenski testovi ispituju
# MODEL-strategiju i na lekcijama koje produkcija sada rutira deterministički.
# Izričito isključenje je ISTI mehanizam kao produkcijski rollback
# (MATBOT_DETERMINISTIC_PRACTICE=disabled) — model-put ostaje trajno testiran.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
