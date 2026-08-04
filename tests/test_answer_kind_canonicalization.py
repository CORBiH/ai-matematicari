"""Kanonizacija `answer_kind` — treći slučaj klase „deklarisana oznaka modela
korištena kao dokaz“ (prva dva: task_form i student_must_find).

ŽIVI NALAZ (canary s PRAVIM modelom, lekcija 6-03-004 „Pravila djeljivosti…“,
prelaz Nivo 1→2, zahtjev „teži“): Tutor i recenzent su potrošili tačno dva
poziva, recenzent je vratio decision=correct sa SVIM provjerama tačnim
(math_correct, tests_exact_lesson, answer_correct, marked_option_correct,
options_unique, grade_appropriate, solvable_and_unambiguous,
difficulty_level_appropriate, difficulty_direction_correct), a objava je ipak
pala zatvoreno na:

    family_contract_mismatch: direct_computation:
    answer_kind=option_label u suprotnosti sa stvarnim tipom tačnog odgovora
    (integer)

Zadatku matematički ni pedagoški ništa nije falilo — jedina „greška“ je bila
opisna oznaka: model je mislio „učenik bira ponuđenu opciju“ i napisao
`option_label`, dok je vidljiva tačna opcija bila cijeli broj (npr. „138“).
Nivo težine je ispravno ostao na 1 (bez mutacije), ali je učenik ostao bez
zadatka uz oba potrošena poziva.

Popravka je GENERIČKA: kad server tip vrijednosti može objektivno izmjeriti iz
teksta tačne opcije, ta izmjerena vrijednost je kanonska i deklaracija se
zanemaruje. Bez grananja po lekciji, porodici ili domenu.
"""
import copy
import re
from pathlib import Path

import pytest

from matbot.practice import (SAFE_ERROR_MESSAGE, _HARDER_TASK_INTRO,
                             _NEW_TASK_INTRO, _next_state, run_practice_turn)
from matbot.session_store import SessionStore
from matbot.task_family_validation import (FamilyContractError,
                                           canonical_answer_kind,
                                           validate_task_family)
from tests.conftest import (FakeLLM, make_fidelity_review, make_options,
                            make_output, make_task, queue_generation)

ROOT = Path(__file__).resolve().parent.parent

DIVISIBILITY = ("6-03-004", 6)          # the reported canary lesson
INTEGER_ADD = ("7-02-008", 7)


def _enable_levels(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")


def _turn(topic, grade, session_id, **changes):
    payload = {
        "session_id": session_id, "grade": grade, "selected_topic": topic,
        "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


# ---------------------------------------------------------------------------
# 1) ČISTO PRAVILO — canonical_answer_kind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("declared,correct_option,expected_canonical,expected_normalized", [
    # Živi canary slučaj: deklarisana oznaka opcije, stvarno cijeli broj.
    ("option_label", "138", "integer", True),
    ("option_label", "$138$", "integer", True),
    # Već dosljedna deklaracija se ne dira.
    ("integer", "138", "integer", False),
    # Deklaracija izostane — server je ionako izvodi sam.
    (None, "138", "integer", False),
    ("", "138", "integer", False),
    # Ostali mehanički prepoznatljivi tipovi.
    ("integer", "$0,5$", "decimal", True),
    ("fraction", "(2,3)", "ordered_pair", True),
    ("integer", "$\\frac{3}{4}$", "fraction", True),
    # NIJE mehanički prepoznatljivo → deklaracija ostaje, bez kanonizacije.
    ("integer", "A", "integer", False),
    ("option_label", "Nije djeljiv sa 9", "option_label", False),
    ("short_text", "Da", "short_text", False),
])
def test_canonical_answer_kind_rule(declared, correct_option, expected_canonical,
                                    expected_normalized):
    canonical, normalized = canonical_answer_kind(declared, correct_option)
    assert canonical == expected_canonical
    assert normalized is expected_normalized


def test_canonicalization_is_family_agnostic():
    """Isto pravilo za SVAKU porodicu koja može imati opcije — nema grananja
    po porodici, lekciji ni domenu."""
    for family in ("direct_computation", "solve_system", "fraction_operation",
                   "compare_or_order", "word_problem", "find_missing_value"):
        canonical, normalized = canonical_answer_kind("option_label", "138")
        assert (canonical, normalized) == ("integer", True), family


# ---------------------------------------------------------------------------
# 2) VALIDATOR — deklaracija više ne odbija, struktura i dalje odbija
# ---------------------------------------------------------------------------

def test_canary_declaration_no_longer_rejects_a_structurally_valid_task():
    validate_task_family(
        "direct_computation", question="Koji od brojeva je djeljiv sa 6?",
        option_texts=["138", "139", "140", "141"], correct_option_index=0,
        expected_answer="138",
        declared={"task_family": "direct_computation", "answer_kind": "option_label"},
    )  # ne smije baciti


def test_declared_task_family_mismatch_still_fails_closed():
    """Identitet porodice ostaje STROG — kanonizacija ga ne dira."""
    with pytest.raises(FamilyContractError, match="drugu porodicu"):
        validate_task_family(
            "direct_computation", question="Koji od brojeva je djeljiv sa 6?",
            option_texts=["138", "139", "140", "141"], correct_option_index=0,
            declared={"task_family": "compare_or_order", "answer_kind": "integer"},
        )


def test_ambiguous_option_mapping_still_fails_closed():
    """Nedvosmisleno kontradiktorna STRUKTURA (indeks tačne opcije van opsega)
    i dalje pada zatvoreno — tačno kao i prije."""
    with pytest.raises(FamilyContractError, match="neispravan indeks"):
        validate_task_family(
            "direct_computation", question="Koji od brojeva je djeljiv sa 6?",
            option_texts=["138", "139"], correct_option_index=7,
            declared={"answer_kind": "integer"},
        )
    with pytest.raises(FamilyContractError, match="neispravan indeks"):
        validate_task_family(
            "direct_computation", question="Koji od brojeva je djeljiv sa 6?",
            option_texts=[], correct_option_index=0,
            declared={"answer_kind": "option_label"},
        )


def test_visible_family_contract_still_authoritative():
    """Kanonizacija metapodatka NE otvara vrata zadatku koji krši VIDLJIVI
    ugovor porodice (ovdje: tekstualni zadatak bez životnog konteksta)."""
    with pytest.raises(FamilyContractError, match="ima_zivotni_kontekst"):
        validate_task_family(
            "fraction_word_problem",
            question="Izračunaj $\\frac{2}{8} + \\frac{3}{8}$.",
            option_texts=["$\\frac{5}{8}$", "$\\frac{5}{16}$", "$\\frac{6}{8}$", "$\\frac{1}{8}$"],
            correct_option_index=0, expected_answer="$\\frac{5}{8}$",
            declared={"task_family": "fraction_word_problem", "answer_kind": "option_label"},
        )


# ---------------------------------------------------------------------------
# 3) END-TO-END REPRODUKCIJA CANARY PADA (FakeLLM)
# ---------------------------------------------------------------------------

_FRESH_L1 = ("Je li broj 47 djeljiv sa 5?", ("Ne", "Da", "Samo sa 2", "Ne može se odrediti"))
# Tačna opcija je CIJELI BROJ, a deklaracija kaže „option_label“ — tačno onaj
# nesklad koji je uživo srušio objavu.
_HARDER_L2 = ("Koji od ponuđenih brojeva je djeljiv i sa 2 i sa 3?",
              ("138", "139", "140", "141"))
# These tests exercise metadata and API option transport on a fresh session,
# so they deliberately use a measurable Level-1 one-rule task.  The two-rule
# fixture above remains the Level-2 transition fixture.
_NUMERIC_SINGLE_RULE_L1 = ("Koji od ponuđenih brojeva je djeljiv sa 6?",
                           ("138", "139", "140", "141"))


def _mc_task(text, options, answer_kind, correct_index=0):
    return make_task(
        text=text, options=make_options(*options), correct_option_index=correct_index,
        expected=options[correct_index], answer_kind=answer_kind,
        task_family="direct_computation",
    )


def test_canary_reproduction_level_1_then_level_2_publishes(monkeypatch):
    topic, grade = DIVISIBILITY
    session_id = "ak-canary"
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()

    # --- Turn 1: svjež Nivo 1 (kao uživo — objavljen bez problema) ---------
    queue_generation(fake, _mc_task(*_FRESH_L1, answer_kind="short_text"))
    first = run_practice_turn(store, fake, _turn(topic, grade, session_id))
    assert first["status"] == "ready"
    assert store.peek(session_id)["difficulty_level"] == 1
    assert first["answer"].startswith(_NEW_TASK_INTRO)

    calls_after_first = fake.call_count
    assert calls_after_first == 2

    # --- Turn 2: „teži“ → Nivo 2, recenzent vraća KOMPLETAN ispravljen
    # zadatak s tačnom opcijom „138“ ALI deklarisan kao „option_label“ ------
    corrected = _mc_task(*_HARDER_L2, answer_kind="option_label")
    fake.queue(make_output(reply="Evo zadatka.",
                           new_task=_mc_task(*_HARDER_L2, answer_kind="option_label")))
    fake.queue(make_fidelity_review(decision="correct", corrected_task=corrected))
    second = run_practice_turn(store, fake, _turn(
        topic, grade, session_id,
        student_message="Daj mi teži zadatak.", difficulty_request="harder"))

    # Objavljeno — deterministička kanonizacija je uklonila lažni nesklad.
    assert second["status"] == "ready", second
    # Brojčane opcije stižu kroz next_state (NE kroz `answer`, koji nosi samo
    # uvod + tekst pitanja — vidi test o API odgovoru ispod).
    assert {o["text"] for o in second["next_state"]["task"]["options"]} == set(_HARDER_L2[1])
    # Nivo je commitovan 1 → 2.
    session = store.peek(session_id)
    assert session["difficulty_level"] == 2
    assert session["difficulty"] == "standard"      # LEVEL_TO_LABEL[2]
    # Vidljivi uvod je istinit za stvarnu promjenu nivoa.
    assert second["answer"].startswith(_HARDER_TASK_INTRO)
    # Tačno dva poziva na ovom turnu — nikad treći.
    assert fake.call_count - calls_after_first == 2


def test_canary_reproduction_keeps_options_in_the_api_response(monkeypatch):
    """Provjera nalaza iz canary izvještaja: opcije JESU u stvarnom API
    odgovoru (canary terminal je štampao samo tekst zadatka), pa frontend
    nema šta da mijenja."""
    topic, grade = DIVISIBILITY
    session_id = "ak-options"
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _mc_task(*_NUMERIC_SINGLE_RULE_L1, answer_kind="option_label"))
    response = run_practice_turn(store, fake, _turn(topic, grade, session_id))

    assert response["status"] == "ready"
    task = response["next_state"]["task"]
    # Sve četiri opcije su STVARNO u API odgovoru (redoslijed je promiješan,
    # pa se poredi skup, ne lista).
    assert {o["text"] for o in task["options"]} == set(_NUMERIC_SINGLE_RULE_L1[1])
    assert len(task["options"]) == 4
    assert {o["id"] for o in task["options"]} == {"a", "b", "c", "d"}
    # `answer` nosi samo uvod + pitanje — zato je canary terminal, koji je
    # štampao isključivo `answer`, izgledao kao da opcija nema.
    assert "138" not in response["answer"]
    # Tačan ID se NIKAD ne šalje browseru prije reveala.
    assert "correct_option_id" not in response["next_state"]
    assert all("correct" not in option for option in task["options"])


def test_inverse_mismatch_genuine_option_label_is_left_untouched(monkeypatch):
    """Obrnuti nesklad: stvarni odgovor JESTE oznaka opcije („A“), a model
    deklariše „integer“. Tip nije mehanički prepoznatljiv, pa se kanonizacija
    NE izvodi — i dalje nema odbijanja (ponašanje nepromijenjeno)."""
    topic, grade = DIVISIBILITY
    session_id = "ak-inverse"
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    task = _mc_task("Koji odgovor je tačan za djeljivost broja 12 sa 4?",
                    ("A", "B", "C", "D"), answer_kind="integer")
    queue_generation(fake, task)
    response = run_practice_turn(store, fake, _turn(topic, grade, session_id))
    assert response["status"] == "ready", response
    assert canonical_answer_kind("integer", "A") == ("integer", False)


def test_already_consistent_numeric_declaration_is_not_normalized(monkeypatch):
    """Nemultiple-choice/brojčani odgovori s ISPRAVNOM deklaracijom prolaze
    bajt za bajt kao i ranije — kanonizacija ih ne dira."""
    topic, grade = DIVISIBILITY
    session_id = "ak-consistent"
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _mc_task(*_NUMERIC_SINGLE_RULE_L1, answer_kind="integer"))
    response = run_practice_turn(store, fake, _turn(topic, grade, session_id))
    assert response["status"] == "ready"
    assert canonical_answer_kind("integer", "138") == ("integer", False)


def test_normalization_is_logged_with_declared_and_canonical(monkeypatch, caplog):
    topic, grade = DIVISIBILITY
    session_id = "ak-log"
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _mc_task(*_NUMERIC_SINGLE_RULE_L1, answer_kind="option_label"))
    with caplog.at_level("INFO", logger="matbot.practice"):
        response = run_practice_turn(store, fake, _turn(topic, grade, session_id))
    assert response["status"] == "ready"
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "practice_answer_kind_normalized" in logged
    assert "declared=option_label" in logged
    assert "canonical=integer" in logged


# ---------------------------------------------------------------------------
# 4) NEPROMIJENJENE ZAŠTITE
# ---------------------------------------------------------------------------

def test_real_rejection_at_level_2_does_not_mutate_difficulty_level(monkeypatch):
    """Zadatak koji padne iz STVARNOG razloga (ovdje: recenzent fail_closed)
    ostavlja commitovan nivo netaknutim — kanonizacija tu ništa ne mijenja."""
    topic, grade = DIVISIBILITY
    session_id = "ak-reject"
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _mc_task(*_FRESH_L1, answer_kind="short_text"))
    run_practice_turn(store, fake, _turn(topic, grade, session_id))
    before = copy.deepcopy(store.peek(session_id))
    calls_before = fake.call_count

    fake.queue(make_output(reply="Evo zadatka.",
                           new_task=_mc_task(*_HARDER_L2, answer_kind="option_label")))
    fake.queue(make_fidelity_review(decision="fail_closed", fail_reason_code="wrong_lesson",
                                    tests_exact_lesson=False))
    response = run_practice_turn(store, fake, _turn(
        topic, grade, session_id,
        student_message="Daj mi teži zadatak.", difficulty_request="harder"))

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) == before
    assert store.peek(session_id)["difficulty_level"] == 1
    assert fake.call_count - calls_before == 2


def test_duplicate_options_still_rejected_despite_canonicalization(monkeypatch):
    """Jedinstvenost opcija je nepromijenjena i i dalje obara zadatak."""
    topic, grade = DIVISIBILITY
    session_id = "ak-dup"
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _mc_task(
        "Koji od ponuđenih brojeva je djeljiv sa 3?", ("138", "138", "140", "141"),
        answer_kind="option_label"))
    response = run_practice_turn(store, fake, _turn(topic, grade, session_id))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) is None


def test_reviewer_approve_contradiction_rules_unchanged(monkeypatch):
    """`approve` uz oborenu obaveznu provjeru i BEZ zamjenskog zadatka i dalje
    pada zatvoreno — kanonizacija ne dira normalizaciju odluke recenzenta."""
    topic, grade = DIVISIBILITY
    session_id = "ak-approve"
    _enable_levels(monkeypatch)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(reply="Evo zadatka.",
                           new_task=_mc_task(*_HARDER_L2, answer_kind="option_label")))
    fake.queue(make_fidelity_review(decision="approve", corrected_task=None,
                                    math_correct=False))
    response = run_practice_turn(store, fake, _turn(topic, grade, session_id))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek(session_id) is None
    assert fake.call_count == 2


def test_feature_off_behaviour_is_unaffected(monkeypatch):
    """Kanonizacija je nezavisna od kontrolera težine — radi i dok je
    zastavica isključena, bez ijedne promjene ostalog ponašanja."""
    monkeypatch.delenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", raising=False)
    topic, grade = DIVISIBILITY
    session_id = "ak-off"
    store, fake = SessionStore(), FakeLLM()
    queue_generation(fake, _mc_task(*_HARDER_L2, answer_kind="option_label"))
    response = run_practice_turn(store, fake, _turn(topic, grade, session_id))
    assert response["status"] == "ready"
    assert store.peek(session_id)["difficulty_level"] == 1   # netaknut default
    assert fake.call_count == 2


def test_no_lesson_id_branching_introduced():
    topic_re = re.compile(r"\b\d-\d{2}-\d{3}\b")
    for name in ("matbot/task_family_validation.py", "matbot/practice.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        offenders = [line.strip() for line in source.splitlines() if topic_re.search(line)]
        assert not offenders, f"{name}: {offenders}"


def test_canonicalization_never_asserts_answer_correctness():
    """Kanonizacija govori SAMO o TIPU vrijednosti, nikad o tačnosti. Dokaz:
    ista kanonska vrijednost za tačan i za netačan cijeli broj — ispravnost
    ostaje posao mathcheck-a, opcija i recenzenta."""
    assert canonical_answer_kind("option_label", "138") == ("integer", True)
    assert canonical_answer_kind("option_label", "139") == ("integer", True)
