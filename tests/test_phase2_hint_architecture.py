r"""ARHITEKTURA POMOĆI — ARHITEKTONSKA FAZA 2. ZERO poziva modela, ZERO mreže.

ŠTA SE OVDJE ZAKLJUČAVA. Do Faze 2 put pomoći nije imao nezavisnog semantičkog
vlasnika: jedan poziv modela, bez recenzenta, bez preflighta, bez ugovora
lekcije, a jedini orakl curenja traži VRIJEDNOST. Dva živa blokatora izdanja
dokazuju cijelu klasu (doslovni tekstovi su zamrznuti u
`tools/practice_eval/hint_evidence.py`):

  FW-X03 · nagovještaj 1 je doslovno izrekao definiciju koja glasi kao označena
           opcija, a `hint_no_leak` je vratio PASS;
         · nagovještaj 3 je do TAČNOG zaključka došao preko NETAČNE
           međutvrdnje, jer je vrh ljestvice bio svježa, neprovjerena proza.
  TR-B1  · nagovještaj 1 je isti kriterij procurio kao PARAFRAZU u drugom
           padežu — što mjerenje po tačnim tokenima dokazano NE dosiže;
         · nagovještaj 2 je lekciji 9. razreda osnovne škole ponudio
           parametarski oblik prave i skalarni proizvod.

ARHITEKTURA (matbot/hint_policy.py + matbot/tutor/pipeline.py):

  KLASA ZADATKA         iz oblika OBJAVLJENIH opcija (server-vlasnički), nikad
                        iz proze modela i nikad iz ID-ja lekcije;
  VRH LJESTVICE i
  PUNO RJEŠENJE         serverska kompozicija PROVJERENOG artefakta objave
                        (`solution_summary` + `expected_answer_summary`) → nula
                        poziva, nijedan nov izvod;
  TVRDNJA/PREPOZNAVANJE nagovještaji 1 i 2 su serverski šabloni koji NE
                        prepisuju nijedno slovo iz opcija → otkrivanje označene
                        tvrdnje je nemoguće PO KONSTRUKCIJI;
  RAČUNSKI ZADATAK      nagovještaje 1 i 2 i dalje piše model, ali kroz uske
                        deterministe: proporcionalnost zapisa, prazna pomoć,
                        mjerač otkrivanja tvrdnje i zatečeni anti-leak gate.

Broj poziva se ovim samo SMANJUJE; trećeg poziva nema ni na jednoj grani.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import pytest

from matbot import config, feedback, hint_policy, terminology
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor import prompts as tutor_prompts
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from tests.conftest import FakeLLM, make_task_payload, make_tutor_draft, queue_two_call
from tools.practice_eval import checks as check_lib
from tools.practice_eval import hint_evidence


@pytest.fixture(autouse=True)
def _runtime(monkeypatch):
    """Tačno ona konfiguracija koju kampanja vozi, uz model-rutu.

    `MATBOT_DETERMINISTIC_PRACTICE=disabled` je ISTI mehanizam koji služi kao
    produkcijski rollback: model-strategija time ostaje trajno testirana."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")


# ---------------------------------------------------------------------------
# MATRICA PREDMETA (dijagnostika, tačka 20)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    label: str
    lesson: str
    grade: int
    task: str
    options: tuple
    correct: int
    expected: str
    solution: str
    task_class: str


COMPUTATIONAL_CASES = (
    Case("aritmetika", "7-02-007", 7,
         "Izračunaj: $-7+12$.",
         ("$5$", "$-5$", "$19$", "$-19$"), 0, "$5$",
         "Od $12$ oduzmemo $7$, a rezultat je pozitivan: $-7+12=5$.",
         hint_policy.COMPUTATIONAL),
    Case("razlomci", "6-04-009", 6,
         r"Izračunaj: $\frac{2}{7}+\frac{3}{7}$.",
         (r"$\frac{5}{7}$", r"$\frac{5}{14}$", r"$\frac{6}{7}$", r"$\frac{1}{7}$"),
         0, r"$\frac{5}{7}$",
         r"Nazivnici su isti, pa saberemo brojnike: $\frac{2}{7}+\frac{3}{7}=\frac{5}{7}$.",
         hint_policy.COMPUTATIONAL),
    Case("jednacine", "9-04-003", 9,
         "Riješi jednačinu: $x+4=9$.",
         ("$x=5$", "$x=13$", "$x=4$", "$x=9$"), 0, "$x=5$",
         "Oduzmemo $4$ na obje strane: $x=9-4=5$.",
         hint_policy.COMPUTATIONAL),
    Case("funkcije", "9-03-001", 9,
         "Za funkciju $f(x)=2x+1$ izračunaj $f(3)$.",
         ("$7$", "$6$", "$5$", "$8$"), 0, "$7$",
         "Uvrstimo $x=3$ u pravilo: $f(3)=2\\cdot 3+1=7$.",
         hint_policy.COMPUTATIONAL),
    Case("skupovi", "6-01-006", 6,
         r"Neka je $A=\{1,2\}$ i $B=\{2,3\}$. Odredi $A\cup B$.",
         (r"$\{1,2,3\}$", r"$\{2\}$", r"$\{1,3\}$", r"$\{1,2\}$"), 0, r"$\{1,2,3\}$",
         r"Unija sadrži svaki element bar jednom: $A\cup B=\{1,2,3\}$.",
         hint_policy.COMPUTATIONAL),
    Case("geometrija", "7-05-016", 7,
         "Trougao ima stranice $3$ cm, $4$ cm i $5$ cm. Koliki je obim $O$?",
         ("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"), 0, "$12$ cm",
         "Obim je zbir stranica: $O=3+4+5=12$ cm.",
         hint_policy.COMPUTATIONAL),
)


PROPOSITIONAL_CASES = (
    Case("aritmetika", "7-02-002", 7,
         "Koja od sljedećih tvrdnji o cijelim brojevima je tačna?",
         ("Svaki prirodan broj je istovremeno i cijeli broj.",
          "Svaki cijeli broj je istovremeno i prirodan broj.",
          "Nula nije cijeli broj, jer nije ni pozitivna ni negativna.",
          "Cijeli brojevi su samo brojevi manji od nule."),
         0, "Svaki prirodan broj je istovremeno i cijeli broj.",
         "Prirodni brojevi su dio cijelih brojeva, pa je prva tvrdnja tačna.",
         hint_policy.PROPOSITIONAL),
    Case("jednacine", "9-04-013", 9,
         "Koja od sljedećih tvrdnji o linearnoj nejednačini je tačna?",
         ("Ako obje strane pomnožimo negativnim brojem, znak nejednakosti se mijenja.",
          "Ako obje strane pomnožimo negativnim brojem, znak nejednakosti ostaje isti.",
          "Ako obje strane saberemo s istim brojem, znak nejednakosti se mijenja.",
          "Nejednačina uvijek ima tačno jedno rješenje, kao i jednačina."),
         0, "Ako obje strane pomnožimo negativnim brojem, znak nejednakosti se mijenja.",
         "Množenje negativnim brojem mijenja smjer nejednakosti, pa je prva tvrdnja tačna.",
         hint_policy.PROPOSITIONAL),
    Case("funkcije", "8-03-004", 8,
         "Koja od sljedećih tvrdnji opisuje direktnu proporcionalnost?",
         ("Ako se jedna veličina poveća dva puta, druga se također poveća dva puta.",
          "Ako se jedna veličina poveća dva puta, druga se smanji dva puta.",
          "Ako se jedna veličina poveća, druga ostaje uvijek jednaka.",
          "Ako se jedna veličina poveća dva puta, druga se poveća za dva."),
         0, "Ako se jedna veličina poveća dva puta, druga se također poveća dva puta.",
         "Kod direktne proporcionalnosti odnos veličina ostaje jednak, pa je prva tvrdnja tačna.",
         hint_policy.PROPOSITIONAL),
    Case("skupovi", "6-01-006", 6,
         "Koja tvrdnja o uniji skupova je tačna?",
         ("Unija sadrži sve elemente oba skupa.",
          "Unija sadrži samo zajedničke elemente oba skupa.",
          "Unija je uvijek prazan skup kada su skupovi različiti.",
          "Unija sadrži samo elemente prvog skupa."),
         0, "Unija sadrži sve elemente oba skupa.",
         "Unija po definiciji sadrži svaki element bar jednog od skupova.",
         hint_policy.PROPOSITIONAL),
    Case("geometrija", "7-04-007", 7,
         "Koja od sljedećih tvrdnji o vrstama trouglova je tačna?",
         ("Trougao koji ima jedan tup ugao ne može imati i pravi ugao.",
          "Trougao može imati dva prava ugla ako je jednakokraki.",
          "Trougao koji ima jedan pravi ugao ima i jedan tup ugao.",
          "Trougao može imati tri tupa ugla ako su stranice različite."),
         0, "Trougao koji ima jedan tup ugao ne može imati i pravi ugao.",
         "Zbir uglova trougla je $180^\\circ$, pa tup i pravi ugao ne mogu stajati zajedno.",
         hint_policy.PROPOSITIONAL),
)

ALL_CASES = COMPUTATIONAL_CASES + PROPOSITIONAL_CASES

# Nagovještaji koje model piše na računskoj ljestvici. Namjerno KONKRETNI i
# međusobno različiti — usmjereni na objekte zadatka, bez rezultata.
MODEL_HINTS = {
    "aritmetika": ("Pogledaj znakove: jedan broj je negativan, a drugi pozitivan, pa "
                   "se traži razlika njihovih apsolutnih vrijednosti.",
                   "Sada uporedi apsolutne vrijednosti $7$ i $12$ i odredi koji znak "
                   "ima rezultat — račun ostavi za posljednji korak."),
    "razlomci": ("Prvo provjeri nazivnike oba razlomka: kad su isti, sabiranje ide "
                 "samo preko brojnika.",
                 "Napiši sabiranje brojnika u jednom razlomku, a nazivnik prepiši "
                 "nepromijenjen — vrijednost brojnika izračunaj sam."),
    "jednacine": ("Prepoznaj koja operacija stoji uz $x$ na lijevoj strani i koja je "
                  "njena obrnuta operacija.",
                  "Primijeni tu obrnutu operaciju na obje strane jednačine, pa napiši "
                  "šta ostaje uz $x$ — vrijednost izračunaj sam."),
    "funkcije": ("Uvrštavanje znači da svako $x$ u pravilu funkcije zamijeniš datim "
                 "brojem $3$.",
                 "Napiši izraz $2\\cdot 3+1$ i pazi na redoslijed operacija — "
                 "množenje ide prije sabiranja."),
    "skupovi": ("Unija se gradi tako da prepišeš elemente prvog skupa, pa dodaš one "
                "iz drugog koje još nemaš.",
                "Prepiši elemente skupa $A$, pa provjeri element po element skupa $B$ "
                "i dopiši samo one koji se ne ponavljaju."),
    "geometrija": ("Obim se za trougao dobija iz njegovih stranica, a sve tri su "
                   "date u zadatku.",
                   "Napiši $O=3+4+5$ i pazi na mjernu jedinicu — zbir izračunaj sam."),
}


def _turn(case, message, session_id, **changes):
    payload = {
        "session_id": session_id, "grade": case.grade, "selected_topic": case.lesson,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def _publish(store, fake, case, session_id):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=case.task, options=case.options,
                                   correct_option_index=case.correct,
                                   expected=case.expected, solution=case.solution)))
    response = run_practice_turn(
        store, fake, _turn(case, "Daj mi zadatak.", session_id))
    assert response["status"] == "ready", response.get("answer")
    return response


def _press_hint(store, fake, case, session_id, index, model_hint=None):
    """Jedan pritisak na „Ne znam — daj mi hint“. Vraća (odgovor, broj_poziva)."""
    if model_hint is not None:
        fake.queue(make_tutor_draft(intent="hint_request", new_task=None,
                                    reply="Idemo korak po korak.", hint=model_hint))
    before = fake.call_count
    response = run_practice_turn(store, fake, _turn(
        case, "Ne znam.", session_id, intent="hint_request",
        interaction_phase="practice_help", client_turn_id=f"{session_id}-h{index}"))
    return response, fake.call_count - before


def _ladder(store, fake, case, session_id):
    """Cijela ljestvica preko DUGMETA. Vraća listu (tekst, broj_poziva)."""
    served = []
    hints = MODEL_HINTS.get(case.label, ("", ""))
    for index in range(1, config.MAX_HINT_LEVEL + 1):
        author = hint_policy.hint_author(case.task_class, index, config.MAX_HINT_LEVEL)
        model_hint = (hints[index - 1]
                      if author == hint_policy.MODEL and index <= len(hints) else None)
        response, calls = _press_hint(store, fake, case, session_id, index, model_hint)
        assert response["status"] == "ready", (case.label, index, response.get("answer"))
        assert response["answer"] != SAFE_ERROR_MESSAGE, (case.label, index)
        served.append((response["answer"], calls))
    return served


def _task_state(session):
    return {
        "task": session["current_task"],
        "identity": session["current_task_identity"],
        "options": [dict(option) for option in session["current_options"]],
        "correct": session["correct_option_id"],
        "expected": session["expected_answer_summary"],
        "solution": session["solution_summary"],
        "lesson": session["lesson_id"],
        "level": session["difficulty_level"],
        "signature": session["current_task_signature"],
    }


# ---------------------------------------------------------------------------
# 1. KLASIFIKACIJA — SERVER-VLASNIČKA I STABILNA (dijagnostika, tačka 6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", ALL_CASES,
                         ids=lambda case: f"{case.task_class[:4]}-{case.label}")
def test_the_visible_option_shape_decides_the_task_class(case):
    """Klasa se izvodi iz OBJAVLJENOG ZADATKA i njegovih opcija, ne iz naslova
    lekcije ni iz `task_type` koji model o sebi deklariše."""
    assert hint_policy.effective_task_class(
        case.task, list(case.options), case.correct) == case.task_class, case.label


def test_the_same_lesson_can_carry_both_classes():
    """DOKAZ da klasifikacija nije prerušena grana po lekciji: ISTA lekcija
    (skupovi) daje računsku klasu za vrijednosni odgovor i propozicionu za
    iskaz."""
    computational = next(c for c in COMPUTATIONAL_CASES if c.label == "skupovi")
    propositional = next(c for c in PROPOSITIONAL_CASES if c.label == "skupovi")
    assert computational.lesson == propositional.lesson
    assert hint_policy.effective_task_class(
        computational.task, list(computational.options),
        computational.correct) == hint_policy.COMPUTATIONAL
    assert hint_policy.effective_task_class(
        propositional.task, list(propositional.options),
        propositional.correct) == hint_policy.PROPOSITIONAL


@pytest.mark.parametrize("task,options,marked,expected", [
    # ČISTE VELIČINE — dokaz iz oblika, neovisno o tekstu zadatka.
    ("Izračunaj: $75+75$.", ("150", "60", "75", "90"), 0, hint_policy.COMPUTATIONAL),
    ("Koliki je obim trougla?", ("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"), 0,
     hint_policy.COMPUTATIONAL),
    ("Koji je broj veći: $-5$ ili $-3$?",
     ("$-3$", "$-5$", "Jednaki su.", "Nije moguće odrediti."), 0,
     hint_policy.COMPUTATIONAL),
    # RELACIJSKI oblik — računski SAMO kad serverski orakl sam riješi zadatak.
    ("Riješi nejednačinu: $x-1>2$.",
     ("$x>3$", "$x\\ge 4$", "$x>5$", "$x>1$"), 0, hint_policy.COMPUTATIONAL),
    # …a bez tog dokaza isti oblik ide konzervativno.
    ("Koja nejednačina opisuje sve brojeve desno od $3$?",
     ("$x>3$", "$x\\ge 4$", "$x>5$", "$x>1$"), 0, hint_policy.PROPOSITIONAL),
    # nedokaziv oblik → KONZERVATIVNO propoziciono (ne izriče kriterij odluke)
    ("Šta je centar opisane kružnice trougla?",
     ("sjecište simetrala stranica", "sjecište visina", "sjecište težišnica",
      "sjecište simetrala uglova"), 0, hint_policy.PROPOSITIONAL),
])
def test_classification_is_stable_for_representative_answer_shapes(task, options,
                                                                   marked, expected):
    assert hint_policy.effective_task_class(task, list(options), marked) == expected


def test_an_unknown_marked_option_falls_back_conservatively():
    """Bez serverskog `correct_option_id` klasa NE SMIJE biti računska: tada bi
    model dobio ljestvicu koja izriče pravilo, a pravilo može BITI odgovor."""
    assert hint_policy.classify_visible_task("Zadatak.", ["a", "b"], -1) == \
        hint_policy.UNCERTAIN
    assert hint_policy.effective_task_class("Zadatak.", ["a", "b"], -1) == \
        hint_policy.PROPOSITIONAL
    assert hint_policy.effective_task_class("Zadatak.", [], 0) == \
        hint_policy.PROPOSITIONAL


def test_the_hint_author_table_is_server_owned_and_total():
    for task_class in (hint_policy.COMPUTATIONAL, hint_policy.PROPOSITIONAL):
        for level in range(1, config.MAX_HINT_LEVEL + 1):
            author = hint_policy.hint_author(task_class, level, config.MAX_HINT_LEVEL)
            assert author in (hint_policy.SERVER, hint_policy.MODEL)
            if level >= config.MAX_HINT_LEVEL:
                assert author == hint_policy.SERVER, (task_class, level)
            elif task_class == hint_policy.PROPOSITIONAL:
                assert author == hint_policy.SERVER, (task_class, level)
            else:
                assert author == hint_policy.MODEL, (task_class, level)


# ---------------------------------------------------------------------------
# 2. RAČUNSKA LJESTVICA (tačke 1–3, 7)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", COMPUTATIONAL_CASES, ids=lambda case: case.label)
def test_computational_ladder_is_safe_useful_and_progressive(case):
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, f"comp-{case.label}")
    served = _ladder(store, fake, case, f"comp-{case.label}")
    (first, first_calls), (second, second_calls), (third, third_calls) = served

    # H1 i H2 su modelovi i troše TAČNO jedan poziv; H3 je serverski i nula.
    assert first_calls == 1 and second_calls == 1, case.label
    assert third_calls == 0, case.label

    # SIGURNOST: nivoi 1 i 2 ne otkrivaju committed odgovor.
    for text in (first, second):
        assert case.expected not in text, case.label
        assert not feedback.leaks_answer(text, case.options[case.correct],
                                         case.expected, task_text=case.task), case.label

    # UPOTREBLJIVOST: skela, ne podsticaj; nivoi se materijalno razlikuju.
    for text in (first, second):
        assert hint_policy.empty_help_code(text) == "", case.label
        assert len(text) >= 40, case.label
    assert first != second, case.label
    assert check_lib._token_overlap(first, second) < 0.85, case.label

    # H3 JE serverska kompozicija PROVJERENOG artefakta.
    assert third.startswith(hint_policy.TOP_HINT_INTRO), case.label
    assert case.solution in third, case.label


@pytest.mark.parametrize("case", COMPUTATIONAL_CASES, ids=lambda case: case.label)
def test_computational_hint_one_receives_the_shaped_help_prompt(case):
    """Prompt oblikovanja mora STVARNO stići (CLAUDE.md „kako mijenjati“)."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, f"prompt-{case.label}")
    _press_hint(store, fake, case, f"prompt-{case.label}", 1,
                MODEL_HINTS[case.label][0])

    instructions, input_text = fake.tutor_calls[-1]
    assert "LJESTVICA POMOĆI — RAČUNSKI ZADATAK" in instructions
    assert "ZAPIS U `hint` I `worked_solution`" in instructions
    assert "STRUCTURED TASK PACKAGE" not in instructions
    assert "POLAZNA SLOŽENOST" not in instructions
    assert f"KLASA ZADATKA (serverska činjenica): {hint_policy.COMPUTATIONAL}" in input_text
    assert "TRAŽENA AKCIJA" in input_text
    assert "SLJEDEĆI HINT JE NIVO 1" in input_text


# ---------------------------------------------------------------------------
# 3. PROPOZICIONA LJESTVICA (tačke 4–6, 8)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", PROPOSITIONAL_CASES, ids=lambda case: case.label)
def test_propositional_ladder_never_discloses_the_deciding_criterion(case):
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, f"prop-{case.label}")
    served = _ladder(store, fake, case, f"prop-{case.label}")
    (first, first_calls), (second, second_calls), (third, third_calls) = served

    # CIJELA propoziciona ljestvica je serverska — nula poziva na svakom nivou.
    assert (first_calls, second_calls, third_calls) == (0, 0, 0), case.label

    marked = case.options[case.correct]
    distractors = [text for index, text in enumerate(case.options)
                   if index != case.correct]
    for text in (first, second):
        # KONSTRUKCIJA: nijedno slovo iz ponuđenih opcija nije prepisano.
        assert marked not in text, case.label
        for distractor in distractors:
            assert distractor not in text, case.label
        # I mjerač otkrivanja tvrdnje ne nalazi strukturu „hint bira odgovor“.
        assert not hint_policy.proposition_disclosure(
            text, marked, distractors, case.task).disclosed, case.label
        assert not feedback.leaks_answer(text, marked, case.expected,
                                         task_text=case.task), case.label
        # UPOTREBLJIVOST: skela, ne podsticaj.
        assert hint_policy.empty_help_code(text) == "", case.label
        assert len(text) >= 120, case.label

    assert first != second, case.label
    assert check_lib._token_overlap(first, second) < 0.85, case.label
    assert third.startswith(hint_policy.TOP_HINT_INTRO), case.label
    assert case.solution in third, case.label


def test_the_propositional_scaffold_copies_nothing_from_the_options():
    """MEHANIČKI DOKAZ KONSTRUKCIJE, nezavisan od bilo koje lekcije.

    Šablon se sastavlja SAMO iz naslova lekcije i jedne strukturne činjenice
    dokazane nad SVIM opcijama. Zato ni jedan sadržajni token svojstven samo
    označenoj opciji ne može ući u tekst — bez obzira šta u opcijama piše."""
    marked = "Neponovljiva zebrasta hipotenuza uvijek presijeca kvadraturu."
    distractors = ["Prva pogrešna tvrdnja o kvadraturi.",
                   "Druga pogrešna tvrdnja o kvadraturi.",
                   "Treća pogrešna tvrdnja o kvadraturi."]
    options = [marked] + distractors
    for level in (1, 2):
        text = hint_policy.compose_propositional_hint(level, "Naslov lekcije", options)
        assert "zebrasta" not in text and "hipotenuza" not in text
        assert marked not in text
        assert not hint_policy.proposition_disclosure(text, marked, distractors).disclosed


def test_the_propositional_scaffold_adapts_to_the_shared_option_structure():
    """Jedina strukturna činjenica koju šablon smije koristiti je ona koju NOSE
    SVE opcije — takva činjenica po definiciji ne izdvaja ni jednu."""
    conditional = [f"Ako je uslov {index}, onda vrijedi zaključak." for index in range(4)]
    plain = [f"Tvrdnja broj {index} o istom pojmu." for index in range(4)]
    assert hint_policy.implication_shaped(conditional) is True
    assert hint_policy.implication_shaped(plain) is False
    with_note = hint_policy.compose_propositional_hint(1, "Lekcija", conditional)
    without_note = hint_policy.compose_propositional_hint(1, "Lekcija", plain)
    assert "ako … onda" in with_note
    assert "ako … onda" not in without_note
    # Jedna opcija bez uslovnog oblika ukida dodatak — dodatak bi tada IZDVAJAO.
    mixed = conditional[:3] + plain[:1]
    assert hint_policy.implication_shaped(mixed) is False


def test_the_propositional_scaffold_names_the_lesson_and_the_comparison():
    """Sigurnost bez upotrebljivosti nije rješenje: šablon mora imenovati
    lekciju i tražiti konkretno poređenje, a nivo 2 mora biti JAČI od nivoa 1.

    HARDENING (Problem B): „jačina“ se više NE postiže protivprimjerom (vidi
    `test_the_propositional_level_two_prescribes_no_invalid_method`), nego
    poređenjem sa ZADANIM uslovima i s definicijom lekcije, uz izričit uslov za
    odbacivanje."""
    options = [f"Tvrdnja {index}." for index in range(4)]
    first = hint_policy.compose_propositional_hint(1, "Unija skupova", options)
    second = hint_policy.compose_propositional_hint(2, "Unija skupova", options)
    assert "Unija skupova" in first and "Unija skupova" in second
    assert "upored" in first.lower()          # traži konkretno poređenje opcija
    assert "upored" in second.lower()
    # Nivo 1: opisno poređenje po objektima, odnosu, uslovima i jačini tvrdnje.
    for expected in ("objektima", "uslove", "SVAKI"):
        assert expected in first, expected
    # Nivo 2: DVA izvora poređenja + izričit uslov odbacivanja — nova radnja.
    for expected in ("uslovima koji su navedeni u samom zadatku", "definicijom",
                     "odbaci SAMO kad nađeš konkretan sukob"):
        assert expected in second, expected
    assert first != second


# ---------------------------------------------------------------------------
# 4. VRH LJESTVICE I PUNO RJEŠENJE (tačke 12, 13)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", ALL_CASES,
                         ids=lambda case: f"{case.task_class[:4]}-{case.label}")
def test_the_ladder_top_is_the_verified_artifact_and_costs_no_call(case):
    store, fake = SessionStore(), FakeLLM()
    session_id = f"top-{case.task_class}-{case.label}"
    _publish(store, fake, case, session_id)
    session = store.peek(session_id)
    assert session["solution_summary"] == case.solution
    assert session["expected_answer_summary"] == case.expected

    session["hint_level"] = config.MAX_HINT_LEVEL - 1
    store.save(session)
    calls_before = fake.call_count

    response, calls = _press_hint(store, fake, case, session_id, 3)

    assert calls == 0 and fake.call_count == calls_before
    assert response["answer"] == hint_policy.compose_top_hint(
        case.solution, case.expected, case.task_class)
    assert store.peek(session_id)["hint_level"] == config.MAX_HINT_LEVEL


@pytest.mark.parametrize("case", ALL_CASES,
                         ids=lambda case: f"{case.task_class[:4]}-{case.label}")
def test_the_full_solution_is_the_verified_artifact_and_costs_no_call(case):
    store, fake = SessionStore(), FakeLLM()
    session_id = f"sol-{case.task_class}-{case.label}"
    _publish(store, fake, case, session_id)
    calls_before = fake.call_count

    response = run_practice_turn(store, fake, _turn(
        case, "Uradi ga ti.", session_id, intent="solution_request",
        interaction_phase="practice_help", client_turn_id=f"{session_id}-s1"))

    assert fake.call_count == calls_before
    assert response["answer"] == hint_policy.compose_full_solution(
        case.solution, case.expected, case.task_class)
    session = store.peek(session_id)
    assert session["task_completed"] is True
    assert session["last_result"] == "full_solution"
    assert response["revealed_correct_option_id"] == session["correct_option_id"]


def test_a_fresh_unverified_proof_can_never_reach_the_ladder_top():
    """KLASA FW-X03/3 NESTAJE PO KONSTRUKCIJI, ne po provjeri izvoda.

    Model dobije priliku da napiše svjež „cijeli postupak“ na kucanoj poruci —
    tamo server prije poziva ne zna namjeru. Poziv je potrošen, ali njegov tekst
    se NE OBJAVLJUJE: vidljivi tekst je serverska kompozicija provjerenog
    artefakta."""
    case = COMPUTATIONAL_CASES[2]
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, "fresh-proof")
    session = store.peek("fresh-proof")
    session["hint_level"] = config.MAX_HINT_LEVEL - 1
    store.save(session)

    fresh = ("Cijeli postupak: pošto je jednakost simetrična, iz $x+4=9$ odmah "
             "slijedi da je svaki broj koji zadovoljava jednakost jednak $5$, "
             "dakle $x=5$.")
    fake.queue(make_tutor_draft(intent="hint_request", new_task=None,
                                reply="Evo cijelog postupka.", hint=fresh))
    response = run_practice_turn(store, fake, _turn(
        case, "Ne znam ni sada.", "fresh-proof", client_turn_id="fresh-h3"))

    assert response["status"] == "ready"
    assert fresh not in response["answer"]
    assert "simetrična" not in response["answer"]
    assert response["answer"] == hint_policy.compose_top_hint(
        case.solution, case.expected, case.task_class)


def test_a_missing_verified_artifact_fails_closed_on_both_top_and_solution():
    """Bez provjerenog artefakta se NIKAD ne traži svjež izvod — pada zatvoreno."""
    case = COMPUTATIONAL_CASES[1]
    for index, ui in enumerate(("hint_request", "solution_request")):
        store, fake = SessionStore(), FakeLLM()
        session_id = f"noartifact-{index}"
        _publish(store, fake, case, session_id)
        session = store.peek(session_id)
        session["solution_summary"] = ""
        session["hint_level"] = config.MAX_HINT_LEVEL - 1
        store.save(session)
        before = copy.deepcopy(store.peek(session_id))
        calls_before = fake.call_count

        response = run_practice_turn(store, fake, _turn(
            case, "Pomozi.", session_id, intent=ui,
            interaction_phase="practice_help", client_turn_id=f"{session_id}-x"))

        assert "status" not in response, ui
        assert response["answer"] == SAFE_ERROR_MESSAGE, ui
        assert response["task_preserved"] is True, ui
        assert store.peek(session_id) == before, ui
        assert fake.call_count == calls_before, ui


def test_the_top_hint_appends_the_result_only_when_the_artifact_omits_it():
    """Kompozicija ne izmišlja matematiku: rezultat se dopisuje samo kad ga
    provjereno rješenje već ne iznosi. Orakl je ISTI `feedback.leaks_answer`
    kojim deterministička ruta odlučuje isto."""
    with_result = "Oduzmemo $4$ na obje strane, pa je rješenje $x=5$."
    without_result = "Oduzmi $4$ na obje strane jednačine i sredi lijevu stranu."
    assert hint_policy.states_answer(with_result, "$x=5$") is True
    assert hint_policy.states_answer(without_result, "$x=5$") is False
    assert "Konačan rezultat je" not in hint_policy.compose_top_hint(
        with_result, "$x=5$", hint_policy.COMPUTATIONAL)
    assert "Konačan rezultat je $x=5$." in hint_policy.compose_top_hint(
        without_result, "$x=5$", hint_policy.COMPUTATIONAL)
    # Za tvrdnju „rezultat“ nije vrijednost nego iskaz — rečenica mora biti tačna.
    proposition = "Tačna tvrdnja je: Unija sadrži sve elemente oba skupa."
    assert proposition in hint_policy.compose_top_hint(
        "Unija po definiciji uzima svaki element bar jednog skupa.",
        "Unija sadrži sve elemente oba skupa.", hint_policy.PROPOSITIONAL)


def test_the_composition_is_empty_without_a_solution_artifact():
    assert hint_policy.compose_top_hint("", "$5$", hint_policy.COMPUTATIONAL) == ""
    assert hint_policy.compose_full_solution("   ", "$5$",
                                             hint_policy.COMPUTATIONAL) == ""


# ---------------------------------------------------------------------------
# 5. FW-X03 — DOSLOVNA REGRESIJA (tačka 18)
# ---------------------------------------------------------------------------

FW = hint_evidence.FW_X03
FW_CASE = Case("fw-x03", FW.topic_id, FW.grade, FW.task_text,
               tuple(text for _oid, text in FW.options),
               [index for index, (oid, _t) in enumerate(FW.options)
                if oid == FW.marked_option_id][0],
               FW.marked_option_text,
               "Po definiciji okomitosti prave i ravni tvrdnja u označenoj opciji "
               "vrijedi za svaku pravu iz te ravni kroz datu tačku.",
               hint_policy.PROPOSITIONAL)


def test_fw_x03_task_is_classified_as_a_proposition_task():
    assert hint_policy.effective_task_class(
        FW_CASE.task, list(FW_CASE.options),
        FW_CASE.correct) == hint_policy.PROPOSITIONAL


def test_fw_x03_hint1_is_now_rejected_by_the_wired_measure():
    """A) Zatečeni izlaz bi SADA bio dokazano otkrivanje — mjerač je uvezan."""
    result = hint_policy.proposition_disclosure(
        FW.hint(1), FW.marked_option_text, FW.distractor_texts, FW.task_text)
    assert result.disclosed is True
    observation = _fw_observation(FW.hint(1), hint_level_before=0)
    assert check_lib.check_hint_proposition_no_leak(observation).outcome == check_lib.FAIL
    # Zatečena vrijednosna provjera i dalje ne vidi ništa — zato mjerač postoji.
    assert check_lib.check_hint_no_leak(observation).outcome == check_lib.PASS


def test_fw_x03_hint1_is_unreachable_by_construction():
    """B) Novi ugovor nivoa 1 NE izriče tvrdnju koja bira označenu opciju.

    Jače od odbijanja: za ovu klasu zadatka model uopšte nije autor, pa taj
    tekst ne može ni nastati kroz dugme pomoći."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, FW_CASE, "fwx03")
    response, calls = _press_hint(store, fake, FW_CASE, "fwx03", 1)

    assert calls == 0
    answer = response["answer"]
    assert answer != FW.hint(1)
    assert "okomita na svaku pravu" not in answer
    assert FW.marked_option_text not in answer
    assert not hint_policy.proposition_disclosure(
        answer, FW.marked_option_text, FW.distractor_texts, FW.task_text).disclosed


def test_fw_x03_hint2_still_helps_without_identifying_the_marked_option():
    """C) Nivo 2 daje jaču, konkretnu pomoć — bez jedinstvenog izbora opcije."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, FW_CASE, "fwx03-2")
    first, _ = _press_hint(store, fake, FW_CASE, "fwx03-2", 1)
    second, calls = _press_hint(store, fake, FW_CASE, "fwx03-2", 2)

    assert calls == 0
    answer = second["answer"]
    assert answer != first["answer"]
    assert hint_policy.empty_help_code(answer) == ""
    assert FW.marked_option_text not in answer
    for distractor in FW.distractor_texts:
        assert distractor not in answer
    assert not hint_policy.proposition_disclosure(
        answer, FW.marked_option_text, FW.distractor_texts, FW.task_text).disclosed
    # Uslovni oblik SVIH opcija je dozvoljena strukturna činjenica.
    assert hint_policy.implication_shaped(list(FW_CASE.options)) is True
    assert "ako … onda" in answer


def test_fw_x03_hint3_false_reasoning_class_is_impossible():
    """D) Vrh ljestvice ne može objaviti svjež nedokazan izvod.

    Zatečeni nagovještaj 3 je nosio DOKAZANO netačnu međutvrdnju i prošao svaki
    deterministički prag (zapis, terminologija, brojevna dosljednost). Sada je
    izvor teksta serverska kompozicija provjerenog artefakta, pa taj tekst nema
    kako doći do učenika — i evaluator to poredi bajt za bajt."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, FW_CASE, "fwx03-3")
    served = _ladder(store, fake, FW_CASE, "fwx03-3")
    third = served[2][0]

    assert hint_evidence.FW_X03_FALSE_INFERENCE not in third
    assert third == hint_policy.compose_top_hint(
        FW_CASE.solution, FW_CASE.expected, hint_policy.PROPOSITIONAL)
    session = store.peek("fwx03-3")
    observation = _fw_observation(third, hint_level_before=config.MAX_HINT_LEVEL - 1,
                                 session=session)
    assert check_lib.check_hint_top_from_verified_solution(
        observation).outcome == check_lib.PASS
    # A zatečeni netačni izvod bi na istom mjestu bio dokazan kao STRANA proza.
    fresh = _fw_observation(FW.hint(3),
                            hint_level_before=config.MAX_HINT_LEVEL - 1,
                            session=session)
    assert check_lib.check_hint_top_from_verified_solution(
        fresh).outcome == check_lib.FAIL


def _fw_observation(answer, hint_level_before, session=None):
    """`TurnObservation` istog oblika koji runner gradi, nad FW-X03 paketom."""
    base = session or {
        "lesson_id": FW.topic_id,
        "current_task": FW.task_text,
        "current_options": [{"id": oid, "text": text} for oid, text in FW.options],
        "correct_option_id": FW.marked_option_id,
        "expected_answer_summary": FW.marked_option_text,
        "solution_summary": FW_CASE.solution,
        "task_completed": False,
    }
    before = dict(base, hint_level=hint_level_before)
    after = dict(base, hint_level=hint_level_before + 1)
    return check_lib.TurnObservation(
        scenario_id=FW.scenario_id, step_index=hint_level_before + 1, step_kind="text",
        topic_id=FW.topic_id, grade=FW.grade,
        request_payload={"student_message": "Ne znam.", "intent": "hint_request",
                         "interaction_phase": "practice_help"},
        http_status=200,
        response={"status": "ready", "answer": answer, "session_mode": "practice",
                  "effective_topic": FW.topic_id},
        session_before=before, session_after=after, sdk_calls=0)


# ---------------------------------------------------------------------------
# 6. TR-B1 — DOSLOVNA REGRESIJA (tačka 19)
# ---------------------------------------------------------------------------

TR = hint_evidence.TR_B1
TR_CASE = Case("tr-b1", TR.topic_id, TR.grade, TR.task_text,
               tuple(text for _oid, text in TR.options),
               [index for index, (oid, _t) in enumerate(TR.options)
                if oid == TR.marked_option_id][0],
               TR.marked_option_text,
               "Po definiciji iz lekcije, označena opcija ispravno opisuje "
               "međusobni položaj prave i ravni koji zaista postoji.",
               hint_policy.PROPOSITIONAL)


def test_tr_b1_paraphrase_stays_out_of_reach_of_every_token_measure():
    """Polazna činjenica: ni produkcijski mjerač ovo NE dosiže — i to se ne krpi.

    Stemming bi ovaj slučaj i dalje promašio, a razgradio bi dokaz za FW-X03
    (`svaku`/`svakoj`, `pravu`/`pravom` se sliju). Zato je vlasnik ove klase
    KONSTRUKCIJA, ne mjerenje — vidi test ispod."""
    result = hint_policy.proposition_disclosure(
        TR.hint(1), TR.marked_option_text, TR.distractor_texts, TR.task_text)
    assert result.verdict == hint_policy.NOT_APPLICABLE
    assert result.disclosed is False


def test_tr_b1_hint1_paraphrase_class_is_unreachable_by_construction():
    """A) i B) Zatečena parafraza kriterija ne može biti objavljena.

    Ovo je POSLJEDNJI korak zamrznute preostale slijepe tačke Faze 0
    (`tests/test_evaluator_hint_blind_spots.py::
    test_phase0_measure_does_not_reach_the_tr_b1_disclosure`): klasa se zatvara
    time što model za propozicioni zadatak NIJE autor nagovještaja 1, a serverski
    šablon ne prepisuje nijedno slovo iz opcija — dakle nikakva parafraza
    kriterija nije ni moguća, bez ijednog stemminga i bez ijednog sinonima."""
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, TR_CASE, "trb1")
    response, calls = _press_hint(store, fake, TR_CASE, "trb1", 1)

    assert calls == 0
    answer = response["answer"]
    assert answer != TR.hint(1)
    assert "zajednička tačka" not in answer
    assert "zajedničkih tačaka" not in answer
    assert TR.marked_option_text not in answer
    # Kriterij se ne pojavljuje ni u bilo kojem padežu, jer se opcije ne čitaju.
    assert "zajedni" not in answer


def test_tr_b1_hint2_advanced_notation_class_is_prevented():
    """C) Proporcionalnost razredu i lekciji — bez kurikularnih podataka Faze 3.

    Mjerilo su artefakti koji su VEĆ prošli objavu: tekst zadatka, opcije,
    očekivani odgovor i odobreno rješenje. Vektorski zapis se u njima ne javlja,
    pa je u pomoći nova tehnika, a ne pomoć."""
    approved = (TR.task_text, TR.marked_option_text) + TR.distractor_texts
    codes = hint_policy.out_of_scope_notation_codes(TR.hint(2), approved)
    assert codes == ("out_of_scope_notation:vec",)

    # Isti nalaz u produkciji obara turn — a stanje ostaje netaknuto.
    store, fake = SessionStore(), FakeLLM()
    computational = COMPUTATIONAL_CASES[2]
    _publish(store, fake, computational, "trb1-notation")
    before = copy.deepcopy(store.peek("trb1-notation"))
    response, calls = _press_hint(store, fake, computational, "trb1-notation", 1,
                                  TR.hint(2))

    assert calls == 1                      # poziv je potrošen, tekst nije objavljen
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("trb1-notation") == before


def test_the_scope_gate_does_not_punish_notation_the_task_itself_uses():
    """Kapija ne smije zabraniti zapis koji lekcija stvarno koristi — inače bi
    vektorska lekcija ostala bez pomoći."""
    approved = (r"Dati su vektori $\vec{a}$ i $\vec{b}$.",)
    assert hint_policy.out_of_scope_notation_codes(
        r"Saberi $\vec{a}+\vec{b}$ po pravilu trougla.", approved) == ()
    assert hint_policy.out_of_scope_notation_codes(
        r"Izračunaj $\frac{1}{2}\cdot 4$.", (r"$\frac{1}{2}$",)) == ()


def test_the_scope_gate_is_a_closed_narrow_set():
    """Skup napredne mašinerije je ZATVOREN i uzak: obična školska notacija
    nikad nije pogodak."""
    for command in ("frac", "sqrt", "cdot", "angle", "perp", "parallel", "pi",
                    "le", "ge", "neq", "approx", "in", "mid", "text", "mathbb"):
        assert command not in hint_policy.ADVANCED_MACHINERY_COMMANDS, command
    for command in ("vec", "overrightarrow", "int", "sum", "lim", "partial",
                    "nabla", "pmatrix"):
        assert command in hint_policy.ADVANCED_MACHINERY_COMMANDS, command


# ---------------------------------------------------------------------------
# 7. STANJE, IDENTITET, BUDŽET POZIVA (tačke 11–16)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", (COMPUTATIONAL_CASES[1], PROPOSITIONAL_CASES[3]),
                         ids=lambda case: f"{case.task_class[:4]}-{case.label}")
def test_the_hint_state_machine_advances_exactly_once_per_accepted_hint(case):
    store, fake = SessionStore(), FakeLLM()
    session_id = f"state-{case.task_class}"
    _publish(store, fake, case, session_id)
    assert store.peek(session_id)["hint_level"] == 0

    hints = MODEL_HINTS.get(case.label, ("", ""))
    for index in range(1, config.MAX_HINT_LEVEL + 1):
        author = hint_policy.hint_author(case.task_class, index, config.MAX_HINT_LEVEL)
        model_hint = hints[index - 1] if author == hint_policy.MODEL else None
        _press_hint(store, fake, case, session_id, index, model_hint)
        assert store.peek(session_id)["hint_level"] == index, index
        assert store.peek(session_id)["current_task_had_hint"] is True

    # Četvrti zahtjev ostaje na vrhu ljestvice (nivo je ograničen).
    response, calls = _press_hint(store, fake, case, session_id, 4)
    assert response["status"] == "ready"
    assert calls == 0
    assert store.peek(session_id)["hint_level"] == config.MAX_HINT_LEVEL


@pytest.mark.parametrize("case", (COMPUTATIONAL_CASES[1], PROPOSITIONAL_CASES[3]),
                         ids=lambda case: f"{case.task_class[:4]}-{case.label}")
def test_help_never_changes_the_task_identity_or_the_session_difficulty(case):
    store, fake = SessionStore(), FakeLLM()
    session_id = f"identity-{case.task_class}"
    _publish(store, fake, case, session_id)
    before = _task_state(store.peek(session_id))

    _ladder(store, fake, case, session_id)

    after = _task_state(store.peek(session_id))
    assert after == before
    assert store.peek(session_id)["task_completed"] is False


def test_a_rejected_unsafe_hint_does_not_corrupt_state_and_retry_works():
    """Odbijena pomoć NE pomjera ljestvicu, a sljedeći uredan zahtjev radi."""
    case = COMPUTATIONAL_CASES[1]
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, "reject")
    before = copy.deepcopy(store.peek("reject"))

    rejected, calls = _press_hint(store, fake, case, "reject", 1,
                                  r"Koristi $\ty{2}{7}$ ovdje.")
    assert calls == 1
    assert rejected["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in rejected
    assert store.peek("reject") == before

    accepted, calls = _press_hint(store, fake, case, "reject", 2,
                                  MODEL_HINTS[case.label][0])
    assert calls == 1
    assert accepted["status"] == "ready"
    assert store.peek("reject")["hint_level"] == 1


def test_a_repeated_help_request_with_the_same_turn_id_is_idempotent():
    """Ponovljen isti `client_turn_id` NE preskače nivo i vraća isti odgovor."""
    case = PROPOSITIONAL_CASES[3]
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, "idem")

    first = run_practice_turn(store, fake, _turn(
        case, "Ne znam.", "idem", intent="hint_request",
        interaction_phase="practice_help", client_turn_id="idem-h1"))
    level_after_first = store.peek("idem")["hint_level"]
    retry = run_practice_turn(store, fake, _turn(
        case, "Ne znam.", "idem", intent="hint_request",
        interaction_phase="practice_help", client_turn_id="idem-h1"))

    assert retry == first
    assert store.peek("idem")["hint_level"] == level_after_first == 1


@pytest.mark.parametrize("case", (COMPUTATIONAL_CASES[1], PROPOSITIONAL_CASES[3]),
                         ids=lambda case: f"{case.task_class[:4]}-{case.label}")
def test_no_help_turn_ever_spends_more_than_one_call(case):
    """Nema trećeg poziva, nema recenzenta na pomoći, i nijedan skriveni poziv."""
    store, fake = SessionStore(), FakeLLM()
    session_id = f"budget-{case.task_class}"
    _publish(store, fake, case, session_id)
    reviewer_before = len(fake.reviewer_calls)

    for text, calls in _ladder(store, fake, case, session_id):
        assert calls <= 1, text[:40]
    solution = run_practice_turn(store, fake, _turn(
        case, "Uradi ga ti.", session_id, intent="solution_request",
        interaction_phase="practice_help", client_turn_id=f"{session_id}-s"))

    assert solution["status"] == "ready"
    assert len(fake.reviewer_calls) == reviewer_before


def test_help_never_publishes_a_new_task_even_when_the_model_asks_for_one():
    case = COMPUTATIONAL_CASES[1]
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, "notask")
    before = _task_state(store.peek("notask"))

    fake.queue(make_tutor_draft(
        intent="next_task", reply="Evo sljedećeg zadatka.",
        new_task=make_task_payload(text="Izračunaj: $1+1$.",
                                   options=("$2$", "$1$", "$3$", "$0$"),
                                   correct_option_index=0, expected="$2$")))
    response, calls = _press_hint(store, fake, case, "notask", 1)

    assert "status" not in response
    assert calls == 1
    assert _task_state(store.peek("notask")) == before


# ---------------------------------------------------------------------------
# 8. ZATEČENE KAPIJE OSTAJU AKTIVNE (tačka 17)
# ---------------------------------------------------------------------------

def test_the_existing_answer_leak_guard_is_still_active_on_a_model_hint():
    """`feedback.leaks_answer` gate se NE slabi zbog nove arhitekture."""
    from matbot.tutor import pipeline as tutor_pipeline

    case = COMPUTATIONAL_CASES[2]
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, "leakguard")
    leaking = "Prebaci $4$ na desnu stranu i dobiješ $x=5$."
    response, calls = _press_hint(store, fake, case, "leakguard", 1, leaking)

    assert calls == 1
    assert response["answer"] == tutor_pipeline.LEAK_BLOCKED_REPLY
    assert "x=5" not in response["answer"].replace(" ", "")


def test_mathsafe_and_terminology_still_guard_every_help_surface():
    case = COMPUTATIONAL_CASES[1]
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, case, "gates")

    # Nepoznata komanda unutar $...$ i dalje obara turn.
    unsafe, _calls = _press_hint(store, fake, case, "gates", 1,
                                 r"Saberi $\bogus{2}{7}$ i $3$.")
    assert unsafe["answer"] == SAFE_ERROR_MESSAGE

    # Serverski sastavljeni tekstovi su jezički čisti po ISTOM normalizatoru.
    for level in (1, 2):
        text = hint_policy.compose_propositional_hint(level, "Unija skupova",
                                                      list(case.options))
        assert terminology.normalize_terminology(text) == text
        assert terminology.contains_forbidden_term(text) is False


def test_a_generic_non_hint_is_rejected_before_it_reaches_the_student():
    """UPOTREBLJIVOST (tačka 21): pomoć bez ijednog skela pada zatvoreno."""
    case = COMPUTATIONAL_CASES[1]
    for index, useless in enumerate(("Razmisli još malo.", "Prisjeti se gradiva.",
                                     "Provjeri opcije.")):
        store, fake = SessionStore(), FakeLLM()
        session_id = f"useless-{index}"
        _publish(store, fake, case, session_id)
        before = copy.deepcopy(store.peek(session_id))
        response, calls = _press_hint(store, fake, case, session_id, 1, useless)
        assert calls == 1
        assert response["answer"] == SAFE_ERROR_MESSAGE, useless
        assert store.peek(session_id) == before


def test_a_short_but_real_hint_is_never_mistaken_for_an_empty_one():
    """Prag je ANKEROVAN na blokove — kratak stvaran nagovještaj prolazi."""
    for real in ("Saberi brojnike, nazivnik ostaje isti.",
                 "Provjeri je li broj djeljiv sa $25$.",
                 "Prebaci član na drugu stranu.",
                 "Idemo korak po korak.\n\nSaberi brojnike, nazivnik ostaje isti."):
        assert hint_policy.empty_help_code(real) == "", real


def test_a_generic_intro_cannot_rescue_a_contentless_hint():
    """Vidljiva pomoć nastaje iz `reply` + `hint`, pa najava ne smije „popuniti“
    prazan podsticaj — inače bi gate bio zaobiđen jednom uljudnom rečenicom."""
    for hollow in ("Razmisli još malo.",
                   "Idemo korak po korak.\n\nRazmisli još malo.",
                   "U redu.\nProvjeri opcije.",
                   "Evo pomoći.\n\nPrisjeti se gradiva."):
        assert hint_policy.empty_help_code(hollow) == hint_policy.EMPTY_HELP_CODE, hollow


# ---------------------------------------------------------------------------
# 9. DETERMINISTIČKA RUTA POMOĆI OSTAJE NETAKNUTA (tačka 14)
# ---------------------------------------------------------------------------

def test_the_deterministic_help_route_keeps_priority_and_its_own_ladder(monkeypatch):
    """Deterministička ruta ima VLASTITU pohranjenu ljestvicu i mora ostati prva.

    Faza 2 ne dira ni jedan generator: kad postoji `deterministic_task` dodatak
    za AKTIVNI identitet, pomoć ide kroz `_run_deterministic_help_turn` — isti
    kod i isti tekstovi kao prije."""
    from matbot.tutor import pipeline as tutor_pipeline

    monkeypatch.delenv("MATBOT_DETERMINISTIC_PRACTICE", raising=False)
    lesson, grade = "6-04-009", 6
    store, fake = SessionStore(), FakeLLM()
    payload = {
        "session_id": "det", "grade": grade, "selected_topic": lesson,
        "selected_oblast": "", "student_message": "Daj mi jedan zadatak za vježbu iz ove teme.",
        "intent": "", "difficulty_request": "", "interaction_phase": "",
        "last_tutor_task": "", "interaction_type": "student_question",
        "selected_option_id": "", "client_turn_id": "",
    }
    assert run_practice_turn(store, fake, payload)["status"] == "ready"
    assert fake.call_count == 0
    annex = store.peek("det")["deterministic_task"]
    assert annex and annex["hints"]

    served = []
    for index in range(1, config.MAX_HINT_LEVEL + 1):
        response = run_practice_turn(store, fake, dict(
            payload, student_message="Ne znam.", intent="hint_request",
            interaction_phase="practice_help", client_turn_id=f"det-h{index}"))
        served.append(response["answer"])

    assert fake.call_count == 0
    # Pohranjeni nagovještaji se služe DOSLOVNO (vrh dobija dopisan rezultat).
    assert served[0] == annex["hints"][0]
    assert served[1] == annex["hints"][1]
    assert served[2].startswith(annex["hints"][2])
    assert annex["answer_reply"] in served[2]
    # Ruta se bira PRIJE serverske kompozicije Faze 2.
    assert tutor_pipeline._deterministic_active_annex(store.peek("det")) is not None


# ---------------------------------------------------------------------------
# 10. IZOLACIJA — POLITIKA POMOĆI JE NEUTRALNA I ČISTA
# ---------------------------------------------------------------------------

def test_the_hint_policy_module_knows_no_lesson_and_reaches_no_model():
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "matbot" / "hint_policy.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for forbidden in ("openai", "requests", "httpx", "urllib", "socket",
                      "matbot.llm", "matbot.tutor", "tools", "tools.practice_eval"):
        assert not any(name == forbidden or name.startswith(forbidden + ".")
                       for name in imported), (forbidden, imported)


def test_the_wired_evaluator_checks_are_all_resolvable_and_grouped():
    """Provjere Faze 2 moraju biti dosežne PO IMENU i imati root-cause grupu —
    inače bi ih kampanja prijavila kao „unknown check name“."""
    for name in ("hint_proposition_no_leak", "hint_top_from_verified_solution",
                 "help_has_task_scaffold", "help_notation_in_scope"):
        assert check_lib.resolve(name) is not None, name
        assert check_lib.root_cause(name) != "other", name
    assert "hint_safety" in check_lib.RUBRICS


# ---------------------------------------------------------------------------
# 11. ŽIVI TALAS POMOĆI — KONFIGURACIJA (bez ijednog poziva)
# ---------------------------------------------------------------------------

def _hint_wave():
    from pathlib import Path

    from tools.practice_eval.scenario import load_scenarios
    path = (Path(__file__).resolve().parent.parent / "tools" / "practice_eval"
            / "scenarios" / "family" / "wave_hint2.jsonl")
    return load_scenarios(path)


def test_the_hint_wave_is_structurally_valid_and_isolated():
    from tools.practice_eval.scenario import validate_scenarios

    scenarios = _hint_wave()
    assert validate_scenarios(scenarios) == []
    # Talas je proširen u hardeningu (Problem C): grana se ne može naručiti, pa
    # se nudi više ciljeva od minimuma koji pokrivenost traži.
    assert len(scenarios) == 12
    assert len({scenario.topic_id for scenario in scenarios}) == 12
    assert all(scenario.wave == "F6H" for scenario in scenarios)


def test_the_hint_wave_bounds_its_own_call_budget():
    """`expect_calls` je GORNJA granica troška; tvrde granice nose provjere."""
    scenarios = _hint_wave()
    assert sum(scenario.max_model_calls for scenario in scenarios) <= 46
    for scenario in scenarios:
        for step in scenario.steps:
            limits = [name for name in step["checks"]
                      if name.startswith("calls_at_most:")]
            assert len(limits) == 1, (scenario.id, step.get("message"))
            limit = int(limits[0].split(":")[1])
            assert step["expect_calls"] <= limit, (scenario.id, step.get("message"))


def test_every_hint_wave_help_step_carries_the_phase2_checks():
    scenarios = _hint_wave()
    help_steps = 0
    for scenario in scenarios:
        for step in scenario.steps:
            intent = step.get("intent") or ""
            if intent not in ("hint_request", "solution_request"):
                continue
            help_steps += 1
            checks = set(step["checks"])
            assert {"help_has_task_scaffold", "help_notation_in_scope",
                    "no_new_task", "task_preserved"} <= checks, scenario.id
            assert "hint_usefulness" in step["rubrics"], scenario.id
            assert "hint_safety" in step["rubrics"], scenario.id
            if step["expect_calls"] == 0:
                # Vrh ljestvice / puno rješenje: serverski po konstrukciji.
                assert {"zero_calls", "hint_top_from_verified_solution"} <= checks
            else:
                assert {"hint_no_leak", "hint_proposition_no_leak"} <= checks
    assert help_steps == 3 * 11 + 1       # jedanaest punih ljestvica + jedno rješenje


def test_the_hint_wave_only_uses_model_route_lessons():
    """Deterministička ruta ima VLASTITU pohranjenu ljestvicu, pa ne bi mjerila
    ništa iz Faze 2 — talas mora voziti model-strategiju."""
    from matbot.contracts import registry as contract_registry
    from matbot.semantics import contracts as semantic_contracts
    from matbot.tutor import lesson_context as lesson_context_module
    from matbot.tutor import pipeline as tutor_pipeline

    for scenario in _hint_wave():
        context = lesson_context_module.build(scenario.grade, scenario.topic_id)
        assert context is not None, scenario.topic_id
        assert tutor_pipeline._deterministic_generator_for(context) is None, \
            scenario.topic_id
        legacy = (contract_registry.contract_for(scenario.topic_id) is not None
                  and semantic_contracts.contract_for(scenario.topic_id) is None)
        assert not legacy, scenario.topic_id


def test_the_frozen_final40_wave_is_still_untouched_by_phase_two():
    """Faza 2 dodaje NOV talas; zamrznuti FINAL40 se ne dira ni za bajt."""
    import hashlib
    from pathlib import Path

    from tests.test_phase0_isolation import FINAL40_WAVE_SHA256
    path = (Path(__file__).resolve().parent.parent / "tools" / "practice_eval"
            / "scenarios" / "family" / "wave_final40.jsonl")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FINAL40_WAVE_SHA256


def test_the_help_prompt_state_block_withholds_nothing_the_model_needs():
    """Za računsku pomoć interni odgovor OSTAJE u ulazu (samoprovjera modela), ali
    uz izričitu zabranu da ga napiše — to je zatečeno ponašanje koje radi i koje
    Faza 2 namjerno ne degradira."""
    from matbot.tutor import lesson_context as lesson_context_module

    context = lesson_context_module.build(6, "6-04-009")
    session = {
        "current_task": r"Izračunaj: $\frac{2}{7}+\frac{3}{7}$.",
        "current_options": [{"id": "a", "text": r"$\frac{5}{7}$"},
                            {"id": "b", "text": r"$\frac{5}{14}$"}],
        "correct_option_id": "a", "expected_answer_summary": r"$\frac{5}{7}$",
        "hint_level": 0, "recent_turns": [],
    }
    hint_input = tutor_prompts.build_help_input(
        context, session, "Ne znam.", "hint_request", hint_policy.COMPUTATIONAL)
    assert "NE SMIJEŠ ga napisati" in hint_input
    solution_input = tutor_prompts.build_help_input(
        context, session, "Uradi ga ti.", "full_solution_request",
        hint_policy.COMPUTATIONAL)
    assert "postupak mora završiti tačno na njemu" in solution_input
