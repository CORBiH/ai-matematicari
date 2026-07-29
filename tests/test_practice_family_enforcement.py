"""End-to-end: ugovor porodice se PRIMJENJUJE u run_practice_turn.

Jedinični testovi (test_task_family_contracts.py) dokazuju da validator radi.
Ovi testovi dokazuju ono što je uživo zakazalo: da neispravan zadatak stvarno
biva odbijen PRIJE mutacije stanja, bez drugog AI poziva, i da učenik dobije
sigurnu poruku umjesto zadatka pogrešne vrste.
"""
import json

from tests.conftest import FakeLLM, make_options, make_output, make_task, make_task_for_family
from matbot import task_families as tf
from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
from matbot.session_store import SessionStore
from matbot.topics import lesson_info

FRACTION_TOPIC = "6-04-005"   # Razlomci — „Proširivanje razlomaka“ (živi nalaz)
SESSION = "sess-enforce"


def payload(msg="Daj mi novi zadatak.", **kw):
    base = {
        "session_id": SESSION, "grade": 6, "selected_topic": FRACTION_TOPIC,
        "selected_oblast": "", "student_message": msg, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "", "selected_option_id": "", "client_turn_id": "",
    }
    base.update(kw)
    return base


def assigned_family(store, session_id=SESSION):
    info = lesson_info(6, FRACTION_TOPIC)
    sess = store.peek(session_id)
    return tf.select_family(
        tf.applicable_families(6, info["oblast"], info["title"]),
        recently_used=sess["recently_used_families"] if sess else [],
        completed_families=sess["correctly_completed_families"] if sess else [],
        retry_required=sess["retry_required"] if sess else False,
        current_family=sess["current_family"] if sess else "",
    )


def bootstrap(store, fake):
    """Prvi (ispravan) zadatak — porodica expand_to_given_denominator."""
    fake.queue(make_output(reply="Evo zadatka.",
                            new_task=make_task_for_family(assigned_family(store))))
    return run_practice_turn(store, fake, payload())


# ---------------------------------------------------------------------------
# Odbijanje neispravne porodice
# ---------------------------------------------------------------------------

def test_wrong_family_task_is_rejected_and_student_gets_safe_message():
    """Živi scenarij: dodijeljeno find_expansion_factor, model vrati zadatak
    proširivanja. Učenik NE smije dobiti taj zadatak."""
    store, fake = SessionStore(), FakeLLM()
    bootstrap(store, fake)
    assert assigned_family(store) == "find_expansion_factor"

    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$. Koja opcija je tačna?",
        expected="$\\frac{8}{20}$",
        options=make_options("$\\frac{8}{20}$", "$\\frac{2}{20}$",
                              "$\\frac{6}{20}$", "$\\frac{4}{10}$"))))
    r = run_practice_turn(store, fake, payload())

    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in r
    assert "next_state" not in r


def test_rejected_task_makes_exactly_one_llm_call():
    store, fake = SessionStore(), FakeLLM()
    bootstrap(store, fake)
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$.",
        expected="x",
        options=make_options("$\\frac{8}{20}$", "$\\frac{2}{20}$",
                              "$\\frac{6}{20}$", "$\\frac{4}{10}$"))))
    run_practice_turn(store, fake, payload())
    assert fake.call_count == 2, "Odbijanje ne smije izazvati popravni AI poziv"


def test_rejected_task_does_not_mutate_any_progression_state():
    store, fake = SessionStore(), FakeLLM()
    bootstrap(store, fake)
    before = store.peek(SESSION)

    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$.",
        expected="x",
        options=make_options("$\\frac{8}{20}$", "$\\frac{2}{20}$",
                              "$\\frac{6}{20}$", "$\\frac{4}{10}$"))))
    run_practice_turn(store, fake, payload())
    after = store.peek(SESSION)

    assert after["current_family"] == before["current_family"]
    assert after["recently_used_families"] == before["recently_used_families"]
    assert after["correctly_completed_families"] == before["correctly_completed_families"]
    assert after["retry_required"] == before["retry_required"]
    assert after["current_task"] == before["current_task"]
    assert after["recent_task_signatures"] == before["recent_task_signatures"]
    assert after["current_options"] == before["current_options"]
    assert after["correct_option_id"] == before["correct_option_id"]


def test_declared_family_mismatch_is_rejected_end_to_end():
    """Model tvrdi drugu porodicu nego što je server dodijelio."""
    store, fake = SessionStore(), FakeLLM()
    before_calls = fake.call_count
    family = assigned_family(store)
    task = make_task_for_family(family)
    task.task_family = "detect_student_error"   # laž o porodici
    fake.queue(make_output(reply="Evo zadatka.", new_task=task))
    r = run_practice_turn(store, fake, payload())

    assert r["answer"] == SAFE_ERROR_MESSAGE
    assert fake.call_count == before_calls + 1
    assert store.peek(SESSION) is None  # ništa nije spremljeno


def test_compliant_task_is_accepted_and_activates():
    store, fake = SessionStore(), FakeLLM()
    r = bootstrap(store, fake)
    assert r["status"] == "ready"
    sess = store.peek(SESSION)
    assert sess["current_family"] == "expand_to_given_denominator"
    assert sess["current_task"]


def test_long_rotation_never_rejects_a_compliant_task():
    """Model koji POŠTUJE dodjelu prolazi kroz cijelu rotaciju bez ijednog
    odbijanja — dokaz da ugovori nisu previše strogi. Ne tvrdimo da svih 8
    porodica padne u 8 poteza: recently_used_families je namjerno ograničen na
    config.MAX_RECENT_FAMILIES (6), pa LRU ne garantuje savršen ciklus."""
    store, fake = SessionStore(), FakeLLM()
    info = lesson_info(6, FRACTION_TOPIC)
    applicable = tf.applicable_families(6, info["oblast"], info["title"])

    seen = []
    for index in range(len(applicable)):
        family = assigned_family(store)
        fake.queue(make_output(reply="Evo zadatka.",
                                new_task=make_task_for_family(family, suffix=f" (var {index})")))
        r = run_practice_turn(store, fake, payload())
        assert r.get("status") == "ready", f"Odbijen validan zadatak za {family}"
        seen.append(family)

    assert seen[0] == "expand_to_given_denominator"
    assert len(set(seen)) >= len(applicable) - 1
    for previous, current in zip(seen, seen[1:]):
        assert previous != current, "Porodica se ponovila odmah uzastopno"


def test_every_family_template_satisfies_its_own_contract():
    """Za SVAKU porodicu iz kataloga postoji zadatak koji njen ugovor prihvata —
    dokaz da nijedan ugovor nije nemoguće zadovoljiti."""
    from matbot.task_family_validation import CONTRACTS, validate_task_family
    from tests.conftest import _FAMILY_TASK_TEMPLATES

    for family_id in sorted(_FAMILY_TASK_TEMPLATES):
        assert family_id in CONTRACTS, family_id
        task = make_task_for_family(family_id)
        validate_task_family(
            family_id,
            question=task.text,
            option_texts=[o.text for o in task.options],
            correct_option_index=task.correct_option_index,
            expected_answer=task.expected_answer,
            declared={"task_family": task.task_family},
        )  # ne smije baciti


# ---------------------------------------------------------------------------
# Pedagoški oblik kroz cijeli tok
# ---------------------------------------------------------------------------

def test_same_shape_from_a_different_family_is_rejected_end_to_end():
    """Čak i kad bi zadatak nekako prošao ugovor, isti pedagoški oblik iz
    druge porodice se odbija (sekundarna zaštita)."""
    store, fake = SessionStore(), FakeLLM()
    bootstrap(store, fake)
    before = store.peek(SESSION)

    # Ista struktura kao prethodni zadatak, samo drugi brojevi, a porodica je
    # sada find_expansion_factor — ovo je tačno živi propust.
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Proširi razlomak $\\frac{2}{5}$ tako da nazivnik bude $20$.",
        expected="x",
        options=make_options("$\\frac{8}{20}$", "$\\frac{2}{20}$",
                              "$\\frac{6}{20}$", "$\\frac{4}{10}$"))))
    run_practice_turn(store, fake, payload())

    after = store.peek(SESSION)
    assert after["recent_task_signatures"] == before["recent_task_signatures"]


def test_retry_may_reuse_the_same_broad_structure():
    """Nakon netačnog odgovora ista porodica smije ponoviti isti OBLIK s novim
    vrijednostima — to je svrha provjere iste vještine."""
    store, fake = SessionStore(), FakeLLM()
    bootstrap(store, fake)
    sess = store.peek(SESSION)
    wrong = next(o["id"] for o in sess["current_options"] if o["id"] != sess["correct_option_id"])

    fake.queue(make_output(reply="Komentar.", hint="Provjeri prvi korak."))
    run_practice_turn(store, fake, payload(
        msg="[klik]", interaction_type="choice_answer",
        selected_option_id=wrong, client_turn_id="t1"))
    assert store.peek(SESSION)["retry_required"] is True

    # Isti oblik, druge vrijednosti, ISTA porodica → mora proći.
    fake.queue(make_output(reply="Evo nove provjere.", new_task=make_task(
        text="Proširi razlomak $\\frac{5}{6}$ tako da nazivnik bude $30$.",
        expected="$\\frac{25}{30}$",
        options=make_options("$\\frac{25}{30}$", "$\\frac{5}{30}$",
                              "$\\frac{10}{30}$", "$\\frac{15}{30}$"))))
    r = run_practice_turn(store, fake, payload())
    assert r.get("status") == "ready", "Retry s istim oblikom mora biti dozvoljen"


# ---------------------------------------------------------------------------
# Browser ugovor: interni metapodaci nikad ne izlaze
# ---------------------------------------------------------------------------

def test_internal_task_specification_never_reaches_the_browser():
    store, fake = SessionStore(), FakeLLM()
    family = assigned_family(store)
    task = make_task_for_family(family)
    task.student_must_find = "expanded_fraction"
    task.answer_kind = "fraction"
    task.task_form = "direct_calculation"
    fake.queue(make_output(reply="Evo zadatka.", new_task=task))
    r = run_practice_turn(store, fake, payload())

    raw = json.dumps(r, ensure_ascii=False)
    for leaked in ("task_family", "student_must_find", "answer_kind", "task_form",
                   "expand_to_given_denominator", "expanded_fraction"):
        assert leaked not in raw, f"Interno polje procurilo u browser: {leaked}"


def test_expected_answer_still_never_reaches_the_browser():
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
        text="Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
        expected="TAJNO-RJESENJE-9-24",
        options=make_options("$\\frac{9}{24}$", "$\\frac{3}{24}$",
                              "$\\frac{9}{8}$", "$\\frac{6}{24}$"))))
    r = run_practice_turn(store, fake, payload())
    raw = json.dumps(r, ensure_ascii=False)
    assert "TAJNO-RJESENJE" not in raw
    assert "correct_option_id" not in raw


def test_correct_option_id_survives_validation_and_shuffle():
    """Validacija radi na PRE-shuffle listi; nakon miješanja server mora i
    dalje pokazivati na isti tekst tačne opcije."""
    for _ in range(15):
        store, fake = SessionStore(), FakeLLM()
        fake.queue(make_output(reply="Evo zadatka.", new_task=make_task(
            text="Proširi razlomak $\\frac{3}{8}$ tako da nazivnik bude $24$.",
            expected="$\\frac{9}{24}$",
            options=make_options("$\\frac{9}{24}$", "$\\frac{3}{24}$",
                                  "$\\frac{9}{8}$", "$\\frac{6}{24}$"),
            correct_option_index=0)))
        run_practice_turn(store, fake, payload())
        sess = store.peek(SESSION)
        correct_text = next(o["text"] for o in sess["current_options"]
                            if o["id"] == sess["correct_option_id"])
        assert correct_text == "$\\frac{9}{24}$"
