"""Migracija K1/K3 ugovornih lekcija na brzu Luna rutu.

Ugovor je PODATAK o zadatku, ne razlog za vlastiti izvrsni put. Ovi testovi
cuvaju ono sto migracija NE SMIJE izgubiti: ugovorna ogranicenja u promptu i
determinsticku serversku provjeru objave.
"""
import pytest

from matbot.contracts import registry as contract_registry
from matbot.tutor import lesson_context, package_preflight as preflight
from matbot.tutor import prompts as tutor_prompts

CONTRACT_LESSONS = ("6-04-005", "6-04-006")


def _fraction(numerator, denominator):
    return r"$\frac{%d}{%d}$" % (numerator, denominator)


class _Option:
    def __init__(self, option_id, text):
        self.id = option_id
        self.text = text


class _Task:
    def __init__(self, text, marked, others):
        self.text = text
        self.options = [_Option("a", marked)] + [
            _Option(cid, opt) for cid, opt in zip("bcd", others)]
        self.correct_option_index = 0


def _package(marked, text, others=(("1", "2"), ("3", "5"), ("5", "7"))):
    return _Task(text, marked, [_fraction(int(a), int(b)) for a, b in others])


# ---------------------------------------------------------------------------
# 5) UGOVORNA METADATA STIZE DO BRZOG PROMPTA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lesson_id", CONTRACT_LESSONS)
def test_every_authoritative_contract_field_reaches_the_prompt(lesson_id):
    contract = contract_registry.contract_for(lesson_id)
    block = tutor_prompts._lesson_block(lesson_context.build(6, lesson_id))
    assert contract.skill in block
    assert all(op in block for op in contract.allowed_operations)
    assert all(a in block for a in contract.allowed_task_archetypes)
    assert all(t in block for t in contract.operand_types)
    # Smjer skaliranja je jedina razlika izmedju prosirivanja i skracivanja.
    assert str(contract.operand_constraints["scaling_direction"]) in block
    assert str(contract.operand_constraints["integer_range"]) in block
    assert all(c in block for c in contract.error_category_set)


def test_required_answer_form_is_spelled_out_for_the_lesson_that_declares_it():
    reduce_block = tutor_prompts._lesson_block(lesson_context.build(6, "6-04-006"))
    expand_block = tutor_prompts._lesson_block(lesson_context.build(6, "6-04-005"))
    assert "NESVODIV" in reduce_block          # skracivanje: oblik JE vjestina
    assert "NESVODIV" not in expand_block      # prosirivanje ga ne trazi


# ---------------------------------------------------------------------------
# 6) UGOVOR SE I DALJE PROVJERAVA SERVERSKI
# ---------------------------------------------------------------------------

def test_reducible_marked_answer_is_rejected_where_the_contract_requires_irreducible():
    context = lesson_context.build(6, "6-04-006")
    issues = preflight.contract_package_issues(
        _package(_fraction(4, 6), "Skrati razlomak " + _fraction(8, 12) + "."), context)
    assert [i.code for i in issues] == [preflight.CONTRACT_ANSWER_FORM_CODE]


def test_fully_reduced_marked_answer_passes():
    context = lesson_context.build(6, "6-04-006")
    assert preflight.contract_package_issues(
        _package(_fraction(2, 3), "Skrati razlomak " + _fraction(8, 12) + "."), context) == ()


@pytest.mark.parametrize("lesson_id", CONTRACT_LESSONS)
def test_marked_option_must_equal_the_fraction_in_the_task(lesson_id):
    context = lesson_context.build(6, lesson_id)
    issues = preflight.contract_package_issues(
        _package(_fraction(5, 6), "Zadatak s razlomkom " + _fraction(2, 3) + "."), context)
    assert preflight.CONTRACT_EQUIVALENCE_CODE in [i.code for i in issues]


def test_negated_question_is_skipped_because_it_cannot_be_proven():
    """„Ne mogu dokazati“ NIKAD ne znaci „prekrsaj“."""
    context = lesson_context.build(6, "6-04-005")
    assert preflight.contract_package_issues(
        _package(_fraction(1, 3), "Koji razlomak NIJE jednak " + _fraction(2, 3) + "?"),
        context) == ()


def test_lesson_without_a_contract_is_never_judged_by_contract_rules():
    context = lesson_context.build(6, "6-04-001")
    assert not context.has_contract
    assert preflight.contract_package_issues(
        _package(_fraction(4, 6), "Bilo koji tekst."), context) == ()


def test_unprovable_marked_answer_is_skipped_not_guessed():
    context = lesson_context.build(6, "6-04-006")
    task = _Task("Skrati razlomak " + _fraction(8, 12) + ".", "dvije trecine",
                 [_fraction(1, 2), _fraction(3, 5), _fraction(5, 7)])
    assert preflight.contract_package_issues(task, context) == ()


def test_contract_issues_travel_through_the_shared_preflight_entry_point():
    """Nalaz mora stici do iste tacke kroz koju prolaze svi ostali validatori —
    inace ne bi bio popravljiv recenzentom."""
    context = lesson_context.build(6, "6-04-006")
    task = _package(_fraction(4, 6), "Skrati razlomak " + _fraction(8, 12) + ".")
    codes = [i.code for i in preflight.collect_package_issues(
        task, lesson_constraints=context)]
    assert preflight.CONTRACT_ANSWER_FORM_CODE in codes


def test_reviewer_is_told_how_to_repair_both_contract_findings():
    rule = tutor_prompts._REVIEWER_PREFLIGHT_RULE
    assert preflight.CONTRACT_ANSWER_FORM_CODE in rule
    assert preflight.CONTRACT_EQUIVALENCE_CODE in rule
    assert "irreducible" in rule and "SAME VALUE" in rule


# ---------------------------------------------------------------------------
# 7-9) PLAFON POZIVA, SERVERSKA TEZINA, PROVJERA ODGOVORA
# ---------------------------------------------------------------------------

@pytest.fixture
def production_route(monkeypatch):
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_FAST_SINGLE_CALL_SCOPE", "model_backed")


@pytest.mark.parametrize("lesson_id", CONTRACT_LESSONS)
def test_migrated_lesson_never_makes_a_third_call(lesson_id, production_route):
    from matbot import practice
    from matbot.session_store import SessionStore
    from tests.conftest import FakeLLM

    class Counter(FakeLLM):
        def __init__(self):
            super().__init__()
            self.stages = []

        def _record(self, name):
            self.stages.append(name)
            assert len(self.stages) <= 2, self.stages   # plafon iz CLAUDE.md
            raise RuntimeError("stop")

        def fast_turn(self, instructions, input_text, timeout_s=None):
            return self._record("fast_turn")

        def practice_turn(self, instructions, input_text):
            return self._record("practice_turn")

        def tutor_turn(self, instructions, input_text):
            return self._record("tutor_turn")

    store, fake = SessionStore(), Counter()
    try:
        practice.run_practice_turn(store, fake, {
            "session_id": f"cap-{lesson_id}", "grade": 6, "selected_topic": lesson_id,
            "selected_oblast": "", "student_message": "Daj mi zadatak.", "intent": "",
            "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
            "interaction_type": "student_question", "selected_option_id": "",
            "client_turn_id": "",
        })
    except RuntimeError:
        pass
    assert fake.stages == ["fast_turn"], fake.stages
    assert "practice_turn" not in fake.stages


@pytest.mark.parametrize("lesson_id", CONTRACT_LESSONS)
def test_difficulty_target_stays_server_owned_for_migrated_lessons(lesson_id):
    """Server imenuje ciljni nivo; model ga ne bira i ne moze ga nadglasati."""
    from matbot import difficulty_level

    transition = difficulty_level.transition(1, "harder")
    assert transition.target_level == 2
    assert difficulty_level.transition(3, "harder").target_level == 3   # plafon
    assert difficulty_level.transition(1, "easier").target_level == 1   # pod


@pytest.mark.parametrize("lesson_id", CONTRACT_LESSONS)
def test_answer_verification_still_rejects_a_wrong_marked_option(lesson_id):
    """Objava i dalje odbija paket u kojem oznacen odgovor nije tacan."""
    context = lesson_context.build(6, lesson_id)
    task = _package(_fraction(5, 6), "Zadatak s razlomkom " + _fraction(2, 3) + ".")
    codes = [i.code for i in preflight.contract_package_issues(task, context)]
    assert preflight.CONTRACT_EQUIVALENCE_CODE in codes


# ---------------------------------------------------------------------------
# OBLIK KAO RAZLIKOVNA OSOBINA (zivi nalaz migracije)
# ---------------------------------------------------------------------------

def test_equivalent_value_options_are_legitimate_where_form_is_the_skill():
    """ZIVI NALAZ: za „koji je nesvodivi oblik od 12/18?“ distraktori 4/6 i 6/9
    JESU jednaki po vrijednosti — i to je poenta zadatka. Opste pravilo
    „ista vrijednost = duplikat“ obaralo je 4 od 8 turnova te lekcije."""
    context = lesson_context.build(6, "6-04-006")
    assert preflight.form_is_the_discriminator(context)
    task = _Task("Koji je nesvodivi oblik razlomka " + _fraction(12, 18) + "?",
                 _fraction(2, 3), [_fraction(4, 6), _fraction(6, 9), _fraction(3, 4)])
    codes = [i.code for i in preflight.collect_package_issues(
        task, lesson_constraints=context)]
    assert preflight.SEMANTIC_DUPLICATE_CODE not in codes
    assert preflight.CONTRACT_ANSWER_FORM_CODE not in codes


def test_form_rule_applies_only_where_the_contract_declares_it():
    assert not preflight.form_is_the_discriminator(lesson_context.build(6, "6-04-005"))
    assert not preflight.form_is_the_discriminator(lesson_context.build(6, "6-04-001"))


def test_literal_duplicates_still_fail_even_where_form_discriminates():
    """Jedinstvenost se ne gubi — samo se mjeri zapisom."""
    context = lesson_context.build(6, "6-04-006")
    task = _Task("Koji je nesvodivi oblik razlomka " + _fraction(12, 18) + "?",
                 _fraction(2, 3), [_fraction(2, 3), _fraction(4, 6), _fraction(3, 4)])
    codes = [i.code for i in preflight.collect_package_issues(
        task, lesson_constraints=context)]
    assert "duplicate_option_text" in codes


def test_two_correct_answers_are_rejected_where_form_discriminates():
    """Dvije opcije jednake izvoru I nesvodive znace dva tacna odgovora."""
    context = lesson_context.build(6, "6-04-006")
    task = _Task("Koji je nesvodivi oblik razlomka " + _fraction(12, 18) + "?",
                 _fraction(2, 3), [_fraction(4, 6), _fraction(6, 9), _fraction(-2, -3)])
    codes = [i.code for i in preflight.contract_package_issues(task, context)]
    assert preflight.CONTRACT_SINGLE_IRREDUCIBLE_CODE in codes


def test_irreducible_distractor_of_a_different_value_is_allowed():
    """Nesvodiv razlomak DRUGE vrijednosti je valjan distraktor."""
    context = lesson_context.build(6, "6-04-006")
    task = _Task("Koji je nesvodivi oblik razlomka " + _fraction(12, 18) + "?",
                 _fraction(2, 3), [_fraction(3, 4), _fraction(4, 5), _fraction(5, 7)])
    assert preflight.CONTRACT_SINGLE_IRREDUCIBLE_CODE not in [
        i.code for i in preflight.contract_package_issues(task, context)]
