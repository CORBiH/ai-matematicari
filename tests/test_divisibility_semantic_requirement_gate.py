r"""Univerzalni put mora nositi deterministički semantički zahtjev lekcije.

OBAVEZNI LIVE RELEASE GATE, commit 0883e8c, scenario `fresh_level1`
(`scratchpad/live_release_gate/0883e8ca3e4afa45990f34722978b87ab3501b8e.json`).

    lekcija      : „Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25“
    ciljani nivo : 1
    Tutor draft  : „Koji od ponuđenih brojeva je djelilac broja 84?“
    reviewer     : correct
    odbijeno     : stage=reviewer_payload,
                   „odobreno uprkos oborenim provjerama: ['language_age_appropriate']“
    objavljeno   : ne · sesija nepromijenjena · 2 poziva

„N je djelilac broja M“ NIJE „M je djeljiv sa N“ kao vidljivi zadatak ove
lekcije: traži se primjena PRAVILA djeljivosti, ne pronalaženje faktora,
količnika ili ostatka.

UZROK: `lesson_fidelity.semantic_task_requirement` deterministički izvodi taj
zahtjev iz NASLOVA lekcije (nikad iz ID-a) i tačno klasifikuje gate zadatak kao
neispravan. Legacy put ga koristi (`matbot/practice.py`), a i sam gate harness ga
računa za dijagnostiku — ali univerzalni dvopozivni put ga pri pivotu nije
preuzeo: ne šalje ga ni Tutoru ni Recenzentu i ne provjerava ga prije objave.
Tutor zato nije ni znao za zahtjev, a Recenzent je poslao interno protivrječan
paket (`correct` uz oborenu obaveznu provjeru).

Popravka ne duplicira i ne slabi validator — poziva postojeći na tri mjesta:
prompt Tutora, prompt Recenzenta i preflight paketa.
"""
import pytest

from matbot import lesson_fidelity
from matbot.tutor import lesson_context as lesson_context_module
from matbot.tutor import package_preflight
from matbot.tutor import pipeline as tutor_pipeline
from matbot.tutor import prompts as tutor_prompts
from tests.conftest import (make_reviewer_final, make_task_payload, make_tutor_draft,
                            queue_two_call)

LESSON = "6-03-004"
TITLE = "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25"
SESSION = "sem-1"
CODE = "divisibility_rules_not_required_by_visible_task"

# --- DOSLOVNI GATE ZADATAK — ne mijenjati ----------------------------------
GATE_TASK = "Koji od ponuđenih brojeva je djelilac broja 84?"
VALID_TASK = "Koji od ponuđenih brojeva je djeljiv sa 3?"
OPTIONS = (r"$36$", r"$41$", r"$50$", r"$68$")


# ---------------------------------------------------------------------------
# 1–2. ŠTA JE VALJAN ZAMJENSKI ZADATAK ZA OVU LEKCIJU
# ---------------------------------------------------------------------------

def _requirement():
    requirement = lesson_fidelity.semantic_task_requirement(TITLE)
    assert requirement is not None
    return requirement


def test_the_gate_task_is_not_a_valid_task_for_this_lesson():
    assert _requirement().failure_for(GATE_TASK) == CODE


NOT_A_SUBSTITUTE = {
    "djelilac (živi gate)": GATE_TASK,
    "faktori": "Koji su faktori broja 84?",
    "količnik": "Koliki je količnik brojeva 84 i 7?",
    "ostatak": "Koji je ostatak pri dijeljenju broja 84 sa 5?",
    "obično dijeljenje": "Izračunaj $84:4$.",
}


@pytest.mark.parametrize("label,text", sorted(NOT_A_SUBSTITUTE.items()))
def test_a_factor_or_quotient_task_is_never_a_substitute(label, text):
    """Recenzent ne smije samo preformulisati u drugi zadatak o faktorima."""
    assert _requirement().failure_for(text) == CODE, label


VALID_LEVEL1 = {
    "izbor djeljivog sa 3": VALID_TASK,
    "da/ne provjera sa 9": "Je li broj $45$ djeljiv sa 9?",
    "izbor djeljivog sa 25": "Koji od ponuđenih brojeva je djeljiv sa 25?",
    "izbor djeljivog sa 10": "Koji od ponuđenih brojeva je djeljiv sa 10?",
    "izbor djeljivog sa 5": "Koji od datih brojeva je djeljiv sa 5?",
}


@pytest.mark.parametrize("label,text", sorted(VALID_LEVEL1.items()))
def test_a_visible_single_rule_task_is_valid(label, text):
    assert _requirement().failure_for(text) is None, label


def test_the_requirement_is_derived_from_the_title_not_a_lesson_id():
    assert lesson_fidelity.semantic_task_requirement("Sabiranje razlomaka") is None
    assert lesson_fidelity.semantic_task_requirement(
        "Pravila djeljivosti sa 7 i 11") is not None


# ---------------------------------------------------------------------------
# 3. ZAHTJEV MORA STIĆI U OBA PROMPTA
# ---------------------------------------------------------------------------

def _instructions():
    context = lesson_context_module.build(6, LESSON)
    return (tutor_prompts.build_tutor_instructions(context),
            tutor_prompts.build_reviewer_instructions(context))


def test_the_tutor_prompt_carries_the_semantic_requirement():
    tutor, _ = _instructions()
    assert _requirement().prompt_block in tutor


def test_the_reviewer_receives_the_requirement_only_when_it_is_violated():
    """Recenzentov prompt ostaje univerzalan; zahtjev stiže kao konkretan nalaz.

    Tako lekcijska proza ne ulazi u svaki drugi poziv, a kad je zahtjev stvarno
    prekršen recenzent dobija i kod i objašnjenje."""
    _, reviewer = _instructions()
    assert _requirement().prompt_block not in reviewer

    violated = package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(_task()))
    assert CODE in violated
    assert _requirement().reviewer_instruction in violated

    assert package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(_task(text=VALID_TASK))) == ""


def test_an_unrelated_lesson_gets_no_semantic_block():
    """Zahtjev je uslovljen naslovom — druge lekcije ostaju netaknute."""
    context = lesson_context_module.build(6, "6-05-010")
    tutor = tutor_prompts.build_tutor_instructions(context)
    assert "SEMANTIČKI ZAHTJEV NOVOG ZADATKA" not in tutor


# ---------------------------------------------------------------------------
# 4. PREFLIGHT: PRECIZAN KOD ZA RECENZENTA
# ---------------------------------------------------------------------------

def _task(text=GATE_TASK, options=OPTIONS, marked=0, title=TITLE, **updates):
    task = make_task_payload(text=text, options=options, correct_option_index=marked,
                             expected=options[marked])
    task = task.model_copy(update={"selected_lesson_title": title})
    return task.model_copy(update=updates) if updates else task


def test_preflight_reports_the_semantic_issue_for_the_gate_task():
    codes = [issue.code for issue in package_preflight.collect_package_issues(_task())]
    assert CODE in codes


def test_preflight_accepts_a_valid_replacement():
    assert package_preflight.collect_package_issues(
        _task(text=VALID_TASK)) == ()


def test_the_reviewer_message_names_the_semantic_code():
    message = package_preflight.format_for_reviewer(
        package_preflight.collect_package_issues(_task()))
    assert CODE in message


def test_an_unrelated_lesson_is_not_judged_by_this_requirement():
    # Faza 4G: orakl direktnog računa sada STVARNO računa „Izračunaj $7,5:5$“,
    # pa opcije moraju sadržavati tačnu vrijednost 1,5 — ranije su naslijeđene
    # djeljivostne opcije činile fixture matematički neispravnim, što test o
    # naslovnom zahtjevu nije smio tvrditi da je čist paket.
    assert package_preflight.collect_package_issues(
        _task(text="Izračunaj $7,5:5$", options=(r"$1,5$", r"$2,5$", r"$0,5$", r"$3$"),
              title="Dijeljenje decimalnih brojeva")) == ()


# ---------------------------------------------------------------------------
# 5–9. CIJELI TURN
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _levels_on(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn():
    return {
        "session_id": SESSION, "grade": 6, "selected_topic": LESSON,
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "sem-t1",
    }


def _run(store, fake_llm, draft_task, final_task, decision="correct"):
    queue_two_call(
        fake_llm,
        draft=make_tutor_draft(intent="generate_task", new_task=draft_task),
        reviewer=make_reviewer_final(
            decision=decision,
            final=make_tutor_draft(intent="generate_task", new_task=final_task)))
    return tutor_pipeline.run_turn(store, fake_llm, _turn())


def _assert_fail_closed(response, store, fake_llm):
    assert response.get("status") is None
    assert response["answer"] == tutor_pipeline.SAFE_ERROR_MESSAGE
    assert fake_llm.call_count == 2            # nikad treći poziv
    assert store.peek(SESSION) is None         # sesija nepromijenjena


def test_an_uncorrected_semantic_violation_is_rejected(store, fake_llm):
    """`correct` koji vrati ISTI neispravan zadatak mora pasti zatvoreno."""
    broken = _task()
    _assert_fail_closed(_run(store, fake_llm, broken, broken), store, fake_llm)


def test_a_reformulated_factor_task_is_still_rejected(store, fake_llm):
    """Preformulacija u drugi zadatak o faktorima ne rješava nalaz."""
    _assert_fail_closed(
        _run(store, fake_llm, _task(), _task(text="Koji su faktori broja 84?")),
        store, fake_llm)


def test_an_approve_of_a_semantically_wrong_task_is_rejected(store, fake_llm):
    broken = _task()
    _assert_fail_closed(_run(store, fake_llm, broken, broken, decision="approve"),
                        store, fake_llm)


def test_a_real_semantic_correction_is_published(store, fake_llm):
    """Ispravka koja ukloni nalaz prolazi kroz sve preostale validatore."""
    response = _run(store, fake_llm, _task(), _task(text=VALID_TASK))

    assert response["status"] == "ready"
    assert "djeljiv sa 3" in response["answer"]
    assert fake_llm.call_count == 2
    assert store.peek(SESSION)["current_task"]


def test_a_clean_level_one_task_needs_no_correction(store, fake_llm):
    valid = _task(text=VALID_TASK)
    response = _run(store, fake_llm, valid, valid, decision="approve")
    assert response["status"] == "ready"
    assert fake_llm.call_count == 2


def test_the_published_package_passes_every_remaining_validator(store, fake_llm):
    """Nalaz 7: fidelity + preflight + difficulty evidence + MCQ integritet."""
    from matbot import mcq_integrity

    valid = _task(text=VALID_TASK)
    assert package_preflight.collect_package_issues(valid) == ()
    failure, result = mcq_integrity.mathematical_publication_failure(
        VALID_TASK, [option.text for option in valid.options],
        valid.correct_option_index)
    assert result.applicable is True and failure == ""

    response = _run(store, fake_llm, valid, valid, decision="approve")
    assert response["status"] == "ready"


# ---------------------------------------------------------------------------
# Kapacitetna ekspanzija: ovi testovi ispituju MODEL-strategiju (Tutor +
# Recenzent) i na lekcijama koje produkcija sada rutira deterministički
# (blocking ugovor + potpun generator). Izričito isključenje je ISTI mehanizam
# koji služi i kao produkcijski rollback (MATBOT_DETERMINISTIC_PRACTICE=
# disabled) — model-put time ostaje trajno testiran, bajt za bajt kakav je bio.
# ---------------------------------------------------------------------------
import pytest as _pytest_capex


@_pytest_capex.fixture(autouse=True)
def _model_route_only_capex(monkeypatch):
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
