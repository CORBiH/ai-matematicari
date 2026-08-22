# -*- coding: utf-8 -*-
"""Objavljen REZULTAT zabranjene operacije, bez zabranjenog simbola.

MJERENA KAMPANJA (20 živih poziva, 5 propusta) koja je ovo iznudila — svi su
prošli i ulaznu i notacijsku kapiju:

    6. razred: „hipotenuza je $5\\,\\text{cm}$. Postupak … uči se kasnije."
    7. razred: „Kasnije se dobija da je udaljenost $5$ cm."
    7. razred: „dužina hipotenuze iznosi $5\\,\\text{cm}$ … poznata trojka"
    6. razred: „stranica je približno $4,5$ cm … oko $4,47$ cm"
    7. razred: na „Šta je hipotenuza?" — „ako su katete 3 i 4, hipotenuza
               $c=5\\,\\text{cm}$"

Posljednji je i dokaz zašto ulazna kapija ovo NE MOŽE riješiti: zahtjev je
čisto pojmovan i mora proći.

Dokaz je serverski i egzaktan (a²+b²=c², odnosno v²≈P za nepotpun kvadrat).
Modelova tvrdnja o postupku se nikad ne čita.
"""
import pytest

from matbot import answer_operations, practice_policy
from matbot.explain import run_explain_turn
from matbot.practice import SAFE_ERROR_MESSAGE

POLICY = {grade: practice_policy.resolve(grade=grade) for grade in (6, 7, 8, 9)}


class CountingLLM:
    def __init__(self, reply):
        self.calls = 0
        self.reply = reply

    def explain_turn(self, instructions, input_text):
        self.calls += 1

        class _Out:
            reply = self.reply

        class _Res:
            output = _Out()
            latency_ms = 1
            usage = {}

        return _Res()


def _turn(grade, topic, message):
    return {"session_id": "eo", "grade": grade, "selected_topic": topic,
            "selected_oblast": "", "student_message": message, "intent": "",
            "difficulty_request": "", "interaction_phase": "",
            "last_tutor_task": "", "last_tutor_message": "",
            "conversation_history": []}


# ---------------------------------------------------------------------------
# ŽIVI PROPUSTI — svaki je stvarno objavljen prije ove kapije
# ---------------------------------------------------------------------------

LIVE_LEAKS = (
    ("Najduža stranica pravouglog trougla zove se hipotenuza. Za katete dužina "
     "$3\\,\\text{cm}$ i $4\\,\\text{cm}$, hipotenuza je $5\\,\\text{cm}$. "
     "Postupak računanja uči se kasnije."),
    ("Udaljenost između tačaka je hipotenuza, jer su pomjeranja od $3$ cm i "
     "$4$ cm katete. Računanje se uči u $8.$ razredu. Kasnije se dobija da je "
     "udaljenost $5$ cm."),
    ("Za katete od $3\\,\\text{cm}$ i $4\\,\\text{cm}$, dužina hipotenuze "
     "iznosi $5\\,\\text{cm}$; u 7. razredu to prepoznajemo kao poznatu trojku."),
    ("Ako su katete $a=3\\,\\text{cm}$ i $b=4\\,\\text{cm}$, stranica nasuprot "
     "pravom uglu je hipotenuza $c=5\\,\\text{cm}$."),
    ("Tražimo broj koji pomnožen sam sa sobom daje $20$. Zato je stranica "
     "između $4$ i $5$ cm. Tačnija procjena je oko $4,47\\,\\text{cm}$."),
)


@pytest.mark.parametrize("reply", LIVE_LEAKS)
@pytest.mark.parametrize("grade", (6, 7))
def test_live_leaks_are_now_blocked(reply, grade):
    assert answer_operations.executed_operation_failures(POLICY[grade], reply)


@pytest.mark.parametrize("reply", LIVE_LEAKS)
def test_the_same_derivations_publish_where_the_operation_is_available(reply):
    for grade in (8, 9):
        assert answer_operations.executed_operation_failures(POLICY[grade], reply) == ()


# ---------------------------------------------------------------------------
# TVRDI NEGATIVI — pojam, granica i navod NISU izvršenje
# ---------------------------------------------------------------------------

HARD_NEGATIVES = (
    # pojmovne definicije bez rezultata
    "Hipotenuza je stranica nasuprot pravom uglu i najduža je stranica trougla.",
    "U pravouglom trouglu katete su stranice koje grade pravi ugao.",
    "Pitagorina teorema se uči u 8. razredu, pa je ovdje ne koristimo.",
    "Korjenovanje ćeš učiti kasnije, u 8. razredu.",
    "Ovaj zadatak se kasnije povezuje s Pitagorinom teoremom.",
    # granica navedena, rezultat NIJE dat
    ("Ovdje su katete $a=3\\,\\text{cm}$ i $b=4\\,\\text{cm}$, pa je $c$ "
     "hipotenuza. Njena dužina se računa tek u 8. razredu."),
    ("Za $P=20\\,\\text{cm}^2$ stranica je veća od $4\\,\\text{cm}$, jer je "
     "$4^2=16$, a manja od $5\\,\\text{cm}$, jer je $5^2=25$."),
    ("Zapisali bismo $a=\\sqrt{20}\\,\\text{cm}$, ali korjenovanje se uči "
     "kasnije, pa ne računamo dalje."),
    # potpun kvadrat — DOZVOLJEN put množenja
    "Pošto je $4\\cdot4=16$, stranica kvadrata je $4\\,\\text{cm}$.",
    "Za $P=25\\,\\text{cm}^2$ stranica je $5\\,\\text{cm}$, jer je $5^2=25$.",
    # brojevi bez veze koju teorema pravi
    "Trougao ima stranice $3$ cm, $4$ cm i $6$ cm. Obim je $13$ cm.",
    "Pravougaonik ima stranice $3$ i $4$ cm, pa je površina $12\\,\\text{cm}^2$.",
    "Ugao od $45^\\circ$ je oštar, a ugao od $120^\\circ$ je tup.",
    "Zbir uglova u trouglu je $180^\\circ$.",
    # obična aritmetika s decimalama
    "Zbir je $2,5+1,5=4$.",
    "Polovina od $9$ je $4,5$.",
)


@pytest.mark.parametrize("reply", HARD_NEGATIVES)
@pytest.mark.parametrize("grade", (6, 7, 8, 9))
def test_hard_negatives_never_block(reply, grade):
    assert answer_operations.executed_operation_failures(POLICY[grade], reply) == (), reply


def test_mentioning_a_later_concept_is_not_execution():
    for reply in ("Korjenovanje ćeš učiti kasnije.",
                  "Ovaj zadatak se može povezati s Pitagorinom teoremom kasnije."):
        assert answer_operations.find_pythagorean_result(reply) is None
        assert answer_operations.find_root_approximation(reply) is None


def test_perfect_square_route_is_never_treated_as_root_extraction():
    """Repozitorijski generator gradi baš takve zadatke (P = a·a), pa put
    množenja mora ostati čist."""
    for reply in ("Pošto je $4\\cdot4=16$, stranica je $4\\,\\text{cm}$.",
                  "Za $P=36$ stranica je $6$, jer je $6\\cdot6=36$."):
        assert answer_operations.find_root_approximation(reply) is None


def test_triple_without_the_target_word_is_not_claimed():
    """3, 4 i 5 u tekstu bez imenovane tražene veličine nisu tvrdnja."""
    assert answer_operations.find_pythagorean_result(
        "Brojevi $3$, $4$ i $5$ su uzastopni prirodni brojevi.") is None


# ---------------------------------------------------------------------------
# KROZ STVARNI EXPLAIN PUT
# ---------------------------------------------------------------------------

CONCEPTUAL_LEAK = ("Hipotenuza je nasuprot pravom uglu. Na primjer, ako su "
                   "katete $a=3\\,\\text{cm}$ i $b=4\\,\\text{cm}$, hipotenuza "
                   "je $c=5\\,\\text{cm}$.")


@pytest.mark.parametrize("grade,lesson", ((6, "6-09-003"), (7, "7-04-019")))
def test_conceptual_question_reaches_the_model_but_leak_is_blocked(grade, lesson):
    """Ulazna kapija SMIJE propustiti pojmovno pitanje — i propušta ga —
    a izlazna kapija zaustavi rezultat koji je model sam dodao."""
    llm = CountingLLM(CONCEPTUAL_LEAK)
    result = run_explain_turn(llm, _turn(grade, lesson, "Šta je hipotenuza?"))
    assert llm.calls == 1, "pojmovno pitanje se ne smije blokirati na ulazu"
    assert result["answer"] == SAFE_ERROR_MESSAGE


@pytest.mark.parametrize("grade,lesson", ((8, "8-04-001"), (9, "9-07-003")))
def test_same_answer_publishes_where_operation_is_available(grade, lesson):
    llm = CountingLLM(CONCEPTUAL_LEAK)
    result = run_explain_turn(llm, _turn(grade, lesson, "Šta je hipotenuza?"))
    assert llm.calls == 1
    assert result["status"] == "ready"
    assert result["answer"] != SAFE_ERROR_MESSAGE


@pytest.mark.parametrize("grade,lesson", ((6, "6-09-003"), (7, "7-04-019")))
def test_clean_conceptual_answer_still_publishes(grade, lesson):
    llm = CountingLLM("Hipotenuza je najduža stranica, nasuprot pravom uglu.")
    result = run_explain_turn(llm, _turn(grade, lesson, "Šta je hipotenuza?"))
    assert result["status"] == "ready"
    assert result["answer"] != SAFE_ERROR_MESSAGE


# ---------------------------------------------------------------------------
# AUTORITET
# ---------------------------------------------------------------------------

def test_gate_reads_no_model_claim_and_no_grade_literal():
    import re
    source = open(answer_operations.__file__, encoding="utf-8").read()
    for banned in ("task_type", "answer_type", "operations_used", "method_used"):
        assert banned not in source, banned
    assert not re.search(r"grade\s*[<>=]=?\s*\d", source)
    assert not re.search(r"[\"']\d-\d\d-\d\d\d[\"']", source)


def test_permission_comes_from_capability_policy_only():
    """Ista tvrdnja, ista rečenica — odluku mijenja SAMO sposobnost razreda."""
    reply = LIVE_LEAKS[0]
    assert answer_operations.executed_operation_failures(POLICY[6], reply)
    assert answer_operations.executed_operation_failures(POLICY[8], reply) == ()


def test_unknown_evidence_fails_open():
    assert answer_operations.executed_operation_failures(POLICY[6], "") == ()
    assert answer_operations.executed_operation_failures(None, "bilo šta") == ()
    assert answer_operations.executed_operation_failures(
        POLICY[6], "Ovo je obična rečenica bez brojeva.") == ()
