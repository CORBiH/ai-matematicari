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
import math

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
    # ARITMETIČKA PODUDARNOST BEZ KONTEXTA — živi defekt nađen na izdanjnoj
    # kapiji: svaka od ovih rečenica zadovoljava v²≈P nad nepotpunim kvadratom
    # („1,5²=2,25" uz „2"), a sve su sasvim obična građa 6. razreda. Prije
    # popravke bilo je 8/8 lažnih blokada.
    "Olovka košta $2,5$ KM. Ana je kupila $6$ olovaka, pa je platila $15$ KM.",
    "Pola od $5$ je $2,5$. U razredu ima $6$ djevojčica.",
    "Trougao ima stranice $3,5$ cm i još $12$ cm žice ostaje.",
    "Traka je duga $4,5$ m, a u kutiji ima $20$ traka.",
    "Marko ima $1,5$ KM, a Ivana $2$ KM.",
    "Svaka strana je $8,5$ cm, a ukupno ih je $72$.",
    "Cijena je $9,5$ KM za $90$ grama.",
    "Prosjek je $4,8$, a ukupno bodova $23$.",
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

def test_arithmetic_coincidence_alone_never_triggers_the_root_detector():
    """Bez kvadratnog konteksta I imenovane dužine, veza v²≈P je slučajnost.

    Mjereno na izdanjnoj kapiji: gola aritmetika je obarala 8/8 svakodnevnih
    tekstualnih zadataka 6. razreda."""
    for reply in ("Marko ima $1,5$ KM, a Ivana $2$ KM.",
                  "Olovka košta $2,5$ KM za $6$ olovaka.",
                  "Prosjek je $4,8$, a bodova $23$."):
        assert answer_operations.find_root_approximation(reply) is None, reply


def test_root_detector_requires_both_square_context_and_length_target():
    square_only = r"Površina kvadrata je $20\,\text{cm}^2$, a cijena $4,5$ KM."
    length_only = "Stranica je $4,5$ cm, a ukupno ih je $20$."
    both = (r"Površina kvadrata je $20\,\text{cm}^2$, pa je stranica "
            r"približno $4,5$ cm.")
    assert answer_operations.find_root_approximation(square_only) is None
    assert answer_operations.find_root_approximation(length_only) is None
    assert answer_operations.find_root_approximation(both) is not None

# ---------------------------------------------------------------------------
# IZVEDENO NAPRAM DATO — vrijednost iz pitanja nije dokaz izvodjenja
# ---------------------------------------------------------------------------

def test_value_given_in_the_question_is_not_evidence_of_derivation():
    """ŽIVI DEFEKT NAĐEN NA IZDANJNOJ KAPIJI: „Koliki je obim trougla sa
    stranicama 3, 4 i 5 cm?" je legitiman zadatak, ali odgovor koji usput
    kaže „najduža stranica je 5 cm" nosi trojku 3-4-5. Ništa nije izvedeno —
    petica je DATA u pitanju."""
    request = "Koliki je obim trougla sa stranicama $3$, $4$ i $5$ cm?"
    answer = "Obim je $3+4+5=12$ cm, a najduža stranica je $5$ cm."
    assert answer_operations.executed_operation_failures(
        POLICY[7], answer, request=request) == ()
    # Bez tog pitanja isti odgovor OSTAJE dokaz izvođenja.
    assert answer_operations.executed_operation_failures(POLICY[7], answer)


def test_derived_value_absent_from_the_question_still_blocks():
    for request, answer in (
        ("Koliko je najduža stranica pravouglog trougla s katetama $3$ i $4$ cm?",
         "Za katete $3$ i $4$ cm, hipotenuza je $5$ cm."),
        ("Šta je hipotenuza?",
         "Ako su katete $a=3$ i $b=4$, hipotenuza je $c=5$ cm."),
        ("Kvadrat ima površinu $20$ cm². Kolika je stranica?",
         "Površina kvadrata je $20$ cm², pa je stranica približno $4,47$ cm."),
    ):
        assert answer_operations.executed_operation_failures(
            POLICY[6], answer, request=request), request


def test_request_argument_can_only_relax_never_tighten():
    """`request` služi SAMO da odbaci lažni dokaz — nikad da nešto zabrani."""
    answer = "Hipotenuza je najduža stranica trougla."
    for request in ("", "bilo šta", "$3$ $4$ $5$"):
        assert answer_operations.executed_operation_failures(
            POLICY[6], answer, request=request) == ()

# ---------------------------------------------------------------------------
# APROKSIMACIJA KORIJENA — EGZAKTAN TEST ZAOKRUZIVANJA, ne proizvoljan prozor
# ---------------------------------------------------------------------------

def _square_answer(area, value):
    return ("Povrsina kvadrata je $%s$ cm2, pa je stranica priblizno $%s$ cm."
            % (area, str(value).replace(".", ",")))


@pytest.mark.parametrize("area", (2, 3, 5, 8, 12, 18, 20, 27, 30, 32, 50,
                                  99, 200, 1000, 5000))
@pytest.mark.parametrize("decimals", (1, 2))
def test_rounded_roots_are_caught_at_every_magnitude(area, decimals):
    """ZIVI PROPUST (izdanjska kampanja, 6. razred): „stranica je priblizno
    4,2 cm" za P=18 je prosao, jer je |4,2^2-18| = 0,36 bilo van ranijeg
    fiksnog prozora od 0,35. Greska raste s korijenom iz P, pa nijedan
    APSOLUTAN prag ne moze biti tacan za sve P — zato se sada provjerava
    interval zaokruzivanja, egzaktno."""
    value = round(math.sqrt(area), decimals)
    reply = _square_answer(area, value)
    assert answer_operations.find_root_approximation(reply) is not None, (area, value)


def test_the_exact_live_release_leak_is_blocked():
    reply = (r"Za kvadrat vazi $P=a\cdot a$. Ako je $P=18$ cm2, stranica je "
             r"priblizno $4,2$ cm, jer je $4,2\cdot4,2=17,64$.")
    request = "Kvadrat ima povrsinu $18$ cm2. Kolika mu je stranica priblizno?"
    assert answer_operations.executed_operation_failures(
        POLICY[6], reply, request=request)


@pytest.mark.parametrize("area,value", ((6, 2.5), (2, 1.5), (7, 2.5), (13, 3.5)))
def test_decimals_that_do_not_round_to_the_root_are_not_claimed(area, value):
    """Blizina nije dokaz: 2,5 nije zaokruzen korijen iz 6, jer se 6 ne
    zaokruzuje na 2,5 ni na jednoj decimali."""
    assert answer_operations.find_root_approximation(
        _square_answer(area, value)) is None
