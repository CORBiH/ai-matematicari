r"""VEZANJE RJEŠENJA ZA OPCIJU — POPRAVKA POSLIJE ŽIVOG H12. ZERO poziva modela.

ŠTA SE OVDJE ZAKLJUČAVA. Talas F6H (`phase2_hint_live_510b1be`) je dokazao cijelu
arhitekturu pomoći iz Faze 2 i ostavio TAČNO JEDAN produktni blokator:

  H12 · objavljene opcije: `a=(2,3)`, `b=(3,-2)`, `c=(3,2)`, `d=(-3,2)`
      · serverski `correct_option_id = c`, `revealed_correct_option_id = c`,
        `marked_option_text = (3,2)`, `expected_answer = (3,2)`
      · a puno rješenje koje je učenik vidio kaže: „…su $(3,2)$, što je opcija a.“

  `hint_top_from_verified_solution` je vratio PASS — i to je bilo TAČNO: posluženi
  tekst JESTE bio bajt-za-bajt serverska kompozicija recenzentom odobrenog
  artefakta. Sam PROVJERENI ARTEFAKT je nosio zastarjelo slovo opcije.

ZAKLJUČAK ARHITEKTURE: provenijencija („odakle je tekst došao“) i vezanje za
opciju („pokazuje li tekst na tačnu opciju“) su DVA RAZLIČITA svojstva. Nijedno
ne implicira drugo, pa se mjere zasebno i nikad se ne spajaju u jednu provjeru.

GRANICA VLASNIŠTVA:
  MODEL   smije posjedovati matematiku i matematički odgovor;
  SERVER  posjeduje identitet opcije (a/b/c/d), i to TEK poslije miješanja.

ODBRANA (deterministička, bez ijednog dodatnog poziva):
  1. OBJAVA (`pipeline._bind_artifact_to_published_options`) — prvi trenutak u
     kojem konačan `correct_option_id` postoji, a sesija još nije mutirana:
     apozicijska MCQ klauzula se DOKAZIVO briše, sve ostalo pada zatvoreno;
  2. POMOĆ (`_finalize_help_answer`) — serverska kompozicija s oznakom pada
     zatvoreno, model-autorski nagovještaj s oznakom se zamjenjuje skelom;
  3. MJERENJE (`checks.solution_option_binding_consistent`) — zasebno od
     provenijencije.
"""
from __future__ import annotations

import copy

import pytest

from matbot import config, hint_policy, mcq_integrity
from matbot.practice import run_practice_turn
from matbot.session_store import SessionStore
from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE
from tests.conftest import FakeLLM, make_task_payload, make_tutor_draft, queue_two_call
from tools.practice_eval import checks as check_lib


@pytest.fixture(autouse=True)
def _runtime(monkeypatch):
    """Ista konfiguracija koju kampanja vozi, uz model-rutu."""
    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_PRACTICE_DIFFICULTY_LEVELS", "enabled")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    # Ljestvica nagovještaja je rollback put (vidi test_phase2_hint_architecture).
    monkeypatch.setenv("MATBOT_PRACTICE_SINGLE_HINT", "disabled")


# ---------------------------------------------------------------------------
# ŽIVI H12 — DOSLOVNI PAKET
# ---------------------------------------------------------------------------
# Tekst zadatka, opcije i rješenje su prepisani iz kampanjskog zapisa
# `phase2_hint_live_510b1be_20260811-014343` (`results.jsonl`, scenario H12).
# Predshuffle redoslijed je onaj u kojem je model tačnu opciju stavio PRVU —
# upravo zato je i napisao „opcija a“.
H12_GRADE = 8
H12_LESSON = "8-02-002"
H12_TASK = ("Tačka A se nalazi 3 jedinice desno i 2 jedinice gore od ishodišta. "
            "Koje su koordinate tačke A?")
H12_DRAFT_OPTIONS = ("(3,2)", "(2,3)", "(3,-2)", "(-3,2)")
H12_EXPECTED = "(3,2)"
H12_SOLUTION_WITH_STALE_LABEL = (
    "Ishodište ima koordinate $0,0$. Pomjeranje 3 jedinice desno povećava $x$ za "
    "3, pa je $x=0+3=3$. Pomjeranje 2 jedinice gore povećava $y$ za 2, pa je "
    "$y=0+2=2$. Dakle, koordinate tačke A su $(3,2)$, što je opcija a."
)
# Ista rečenica bez MCQ apozicije — matematika je netaknuta.
H12_SOLUTION_LABEL_FREE = (
    "Ishodište ima koordinate $0,0$. Pomjeranje 3 jedinice desno povećava $x$ za "
    "3, pa je $x=0+3=3$. Pomjeranje 2 jedinice gore povećava $y$ za 2, pa je "
    "$y=0+2=2$. Dakle, koordinate tačke A su $(3,2)$."
)
# Permutacija koja iz predshuffle redoslijeda pravi TAČNO objavljeni H12 skup.
H12_PERMUTATION = (1, 2, 0, 3)


def _freeze_shuffle(monkeypatch, order):
    """Zamrzni serversko miješanje na TAČNO zadatu permutaciju.

    Miješa se i dalje kroz `pipeline._shuffle_options` — mijenja se samo izvor
    slučajnosti, pa se dokazuje ponašanje objave, ne test-specifična grana."""
    def shuffle(items):
        items[:] = [items[index] for index in order]
    monkeypatch.setattr("matbot.tutor.pipeline.random.shuffle", shuffle)


def _turn(session_id, message, grade=H12_GRADE, lesson=H12_LESSON, **changes):
    payload = {
        "session_id": session_id, "grade": grade, "selected_topic": lesson,
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }
    payload.update(changes)
    return payload


def _publish(store, fake, session_id, *, options, correct_index, expected, solution,
             task=H12_TASK, grade=H12_GRADE, lesson=H12_LESSON):
    queue_two_call(fake, draft=make_tutor_draft(
        intent="generate_task",
        new_task=make_task_payload(text=task, options=options,
                                   correct_option_index=correct_index,
                                   expected=expected, solution=solution)))
    return run_practice_turn(store, fake, _turn(session_id, "Daj mi zadatak.",
                                                grade=grade, lesson=lesson))


def _ask_full_solution(store, fake, session_id, grade=H12_GRADE, lesson=H12_LESSON):
    before = fake.call_count
    response = run_practice_turn(store, fake, _turn(
        session_id, "Uradi ga ti.", grade=grade, lesson=lesson,
        intent="solution_request", interaction_phase="practice_help",
        client_turn_id=f"{session_id}-sol"))
    return response, fake.call_count - before


def _observation(session_id, request, response, session_before, session_after,
                 sdk_calls=0):
    return check_lib.TurnObservation(
        scenario_id=session_id, step_index=1, step_kind="text",
        topic_id=H12_LESSON, grade=H12_GRADE, request_payload=request,
        http_status=200, response=response, session_before=session_before,
        session_after=session_after, sdk_calls=sdk_calls)


# ---------------------------------------------------------------------------
# 1. H12 — TAČNA REGRESIJA
# ---------------------------------------------------------------------------

def test_h12_exact_regression_binds_the_published_solution_to_option_c(monkeypatch):
    """DOSLOVAN H12: predshuffle „opcija a“, objavljeno `c` — poslije popravke
    matematika ostaje $(3,2)$, a slovo `a` nestaje iz teksta koji učenik vidi."""
    _freeze_shuffle(monkeypatch, H12_PERMUTATION)
    store, fake = SessionStore(), FakeLLM()
    published = _publish(store, fake, "h12", options=H12_DRAFT_OPTIONS,
                         correct_index=0, expected=H12_EXPECTED,
                         solution=H12_SOLUTION_WITH_STALE_LABEL)
    assert published["status"] == "ready", published.get("answer")

    session = store.peek("h12")
    # Objavljeno stanje je BAJT ZA BAJT ono iz živog zapisa.
    assert [(option["id"], option["text"]) for option in session["current_options"]] == [
        ("a", "(2,3)"), ("b", "(3,-2)"), ("c", "(3,2)"), ("d", "(-3,2)")]
    assert session["correct_option_id"] == "c"
    assert session["expected_answer_summary"] == "(3,2)"
    # Provjereni artefakt je NORMALIZOVAN prije nego što je ušao u sesiju.
    assert session["solution_summary"] == H12_SOLUTION_LABEL_FREE
    assert mcq_integrity.option_label_claims(session["solution_summary"]) == ()

    identity_before = session["current_task_identity"]
    response, calls = _ask_full_solution(store, fake, "h12")

    assert calls == 0                                   # nula poziva pomoći
    answer = response["answer"]
    assert "opcija a" not in answer.lower()
    assert "$(3,2)$" in answer                          # matematika je preživjela
    assert response["revealed_correct_option_id"] == "c"
    after = store.peek("h12")
    assert after["correct_option_id"] == "c"
    assert after["current_task_identity"] == identity_before
    assert after["current_task"] == store.peek("h12")["current_task"]
    # Provenijencija ostaje puna: posluženo == serverska kompozicija artefakta.
    assert answer == hint_policy.compose_full_solution_for_session(after)


def test_h12_marked_option_and_expected_answer_still_agree_after_the_repair(monkeypatch):
    _freeze_shuffle(monkeypatch, H12_PERMUTATION)
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "h12-fields", options=H12_DRAFT_OPTIONS, correct_index=0,
             expected=H12_EXPECTED, solution=H12_SOLUTION_WITH_STALE_LABEL)
    response, _calls = _ask_full_solution(store, fake, "h12-fields")
    session = store.peek("h12-fields")

    marked = next(option["text"] for option in session["current_options"]
                  if option["id"] == session["correct_option_id"])
    assert marked == "(3,2)"
    assert session["expected_answer_summary"] == marked
    assert hint_policy.session_marked_answer(session) == marked
    assert response["revealed_correct_option_id"] == session["correct_option_id"]


def test_h12_would_have_failed_the_new_binding_check_before_the_repair():
    """Kontrola da nova provjera nije prazna: DOSLOVAN živi tekst pada."""
    live_answer = (
        "Evo rješenja ovog zadatka u cijelosti. Prati postupak, pa ga uporedi s "
        "onim što si sam pokušao.\n\n" + H12_SOLUTION_WITH_STALE_LABEL)
    session = {
        "current_task": H12_TASK,
        "current_options": [{"id": "a", "text": "(2,3)"}, {"id": "b", "text": "(3,-2)"},
                            {"id": "c", "text": "(3,2)"}, {"id": "d", "text": "(-3,2)"}],
        "correct_option_id": "c", "expected_answer_summary": "(3,2)",
        "solution_summary": H12_SOLUTION_WITH_STALE_LABEL,
    }
    obs = _observation("H12", {"intent": "solution_request"},
                       {"answer": live_answer, "revealed_correct_option_id": "c"},
                       session, session)

    binding = check_lib.check_solution_option_binding_consistent(obs)
    assert binding.outcome == check_lib.FAIL
    assert "option_label_binding_mismatch" in binding.detail

    # …a provenijencija je pri tome i dalje uredna. Dva različita svojstva.
    provenance = check_lib.check_hint_top_from_verified_solution(obs)
    assert provenance.outcome == check_lib.PASS


def test_the_repaired_turn_passes_both_checks(monkeypatch):
    _freeze_shuffle(monkeypatch, H12_PERMUTATION)
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "h12-checks", options=H12_DRAFT_OPTIONS, correct_index=0,
             expected=H12_EXPECTED, solution=H12_SOLUTION_WITH_STALE_LABEL)
    before = copy.deepcopy(store.peek("h12-checks"))
    response, _calls = _ask_full_solution(store, fake, "h12-checks")
    obs = _observation("H12", {"intent": "solution_request"}, response,
                       before, store.peek("h12-checks"))

    assert check_lib.check_solution_option_binding_consistent(obs).outcome == check_lib.PASS
    assert check_lib.check_hint_top_from_verified_solution(obs).outcome == check_lib.PASS


# ---------------------------------------------------------------------------
# 2. MATRICA MIJEŠANJA — SVA ČETIRI KONAČNA ID-JA × OBLICI ARTEFAKTA
# ---------------------------------------------------------------------------
# Permutacija koja tačnu opciju (predshuffle indeks 0) stavlja na svaki slot.
_PERMUTATION_FOR_SLOT = {
    0: (0, 1, 2, 3),
    1: (1, 0, 2, 3),
    2: (1, 2, 0, 3),
    3: (1, 2, 3, 0),
}

# (oznaka, opcije, expected, rješenje bez oznake) — proza, broj, simbol.
ANSWER_FORMS = (
    ("prose",
     ("Zbir uglova je $180^\\circ$ u svakom trouglu.",
      "Zbir uglova zavisi od dužina stranica trougla.",
      "Zbir uglova je $360^\\circ$ u svakom trouglu.",
      "Zbir uglova je $90^\\circ$ u svakom trouglu."),
     "Zbir uglova je $180^\\circ$ u svakom trouglu.",
     "Zbir unutrašnjih uglova trougla je uvijek isti i iznosi $180^\\circ$."),
    ("numeric",
     ("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"), "$12$ cm",
     "Obim je zbir stranica: $O=3+4+5=12$ cm."),
    ("symbolic",
     ("$p \\perp \\alpha$", "$p \\parallel \\alpha$", "$p \\subset \\alpha$",
      "$p \\cap \\alpha = \\varnothing$"),
     "$p \\perp \\alpha$",
     "Prava siječe ravan i s njom zaklapa prav ugao, pa vrijedi $p \\perp \\alpha$."),
)

# Kako artefakt imenuje opciju: nikako / tačno po PREDSHUFFLE slovu / apozicija.
LABEL_VARIANTS = ("none", "apposition", "parenthesis", "dash")


def _artifact(base_solution, variant):
    if variant == "none":
        return base_solution
    body = base_solution.rstrip(".")
    if variant == "apposition":
        return f"{body}, što je opcija a."
    if variant == "parenthesis":
        return f"{body} (opcija a)."
    return f"{body} — odgovor a."


@pytest.mark.parametrize("slot", (0, 1, 2, 3), ids=lambda slot: f"final-{'abcd'[slot]}")
@pytest.mark.parametrize("form", ANSWER_FORMS, ids=lambda form: form[0])
@pytest.mark.parametrize("variant", LABEL_VARIANTS)
def test_no_stale_option_letter_survives_any_shuffle(monkeypatch, slot, form, variant):
    """Za SVAKI konačan ID i SVAKI oblik odgovora: nijedno slovo ne preživi.

    Kad artefakt uopšte ne imenuje opciju, tekst mora ostati NETAKNUT — zaštita
    ne smije prepisivati tekst koji je već ispravan."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[slot])
    label, options, expected, base_solution = form
    solution = _artifact(base_solution, variant)
    store, fake = SessionStore(), FakeLLM()
    session_id = f"matrix-{label}-{variant}-{slot}"

    published = _publish(store, fake, session_id, options=options, correct_index=0,
                         expected=expected, solution=solution,
                         task="Koja tvrdnja je tačna za dati raspored?")
    assert published["status"] == "ready", published.get("answer")

    session = store.peek(session_id)
    committed = "abcd"[slot]
    assert session["correct_option_id"] == committed
    assert session["current_options"][slot]["text"] == expected
    assert session["solution_summary"] == base_solution
    if variant == "none":
        assert session["solution_summary"] == solution

    response, calls = _ask_full_solution(store, fake, session_id)
    assert calls == 0
    answer = response["answer"]
    assert mcq_integrity.option_label_claims(answer) == ()
    assert response["revealed_correct_option_id"] == committed
    assert answer == hint_policy.compose_full_solution_for_session(store.peek(session_id))


@pytest.mark.parametrize("slot", (0, 1, 2, 3), ids=lambda slot: f"final-{'abcd'[slot]}")
def test_a_matching_pre_shuffle_label_is_removed_too(monkeypatch, slot):
    """Čak i kad slovo SLUČAJNO ispadne tačno, ono se ne zadržava.

    Zadržati ga značilo bi da model povremeno posjeduje identitet opcije — a
    vlasništvo ne smije zavisiti od ishoda miješanja."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[slot])
    store, fake = SessionStore(), FakeLLM()
    session_id = f"matching-{slot}"
    _publish(store, fake, session_id, options=("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"),
             correct_index=0, expected="$12$ cm",
             solution="Obim je zbir stranica: $O=3+4+5=12$ cm, što je opcija a.",
             task="Trougao ima stranice $3$ cm, $4$ cm i $5$ cm. Koliki je obim $O$?")
    session = store.peek(session_id)
    assert session["solution_summary"] == "Obim je zbir stranica: $O=3+4+5=12$ cm."
    assert mcq_integrity.option_label_claims(session["solution_summary"]) == ()


# ---------------------------------------------------------------------------
# 3. SLOVA KAO IMENA MATEMATIČKIH OBJEKATA — NIKAD SE NE DIRAJU
# ---------------------------------------------------------------------------

MATH_OBJECT_TEXTS = (
    "Tačka A leži na pravoj a.",
    "Skup B sadrži ugao C.",
    "Prava a je paralelna s pravom b.",
    "Ugao C je nasuprot stranici c.",
    "Tačka D je presjek pravih a i b.",
    "Za tačku A i tačku B važi $|AB|=5$ cm.",
    "Pogledaj ponuđene opcije još jednom.",
    "U opcijama se nalaze četiri različita zapisa.",
    "Odgovori se razlikuju po smjeru implikacije.",
)


@pytest.mark.parametrize("text", MATH_OBJECT_TEXTS)
def test_letters_used_as_mathematical_object_names_are_never_claims(text):
    assert mcq_integrity.option_label_claims(text) == ()
    normalized, code = mcq_integrity.option_label_normalization(text)
    assert code == ""
    assert normalized == text


def test_an_object_named_solution_survives_publication_untouched(monkeypatch):
    """Isti dokaz na ŽIVOM putu objave, ne samo nad čistom funkcijom."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[2])
    solution = ("Tačka A je vrh ugla, a prava a prolazi kroz tačku B. "
                "Zato je traženi ugao kod tačke A jednak $60^\\circ$.")
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "objects", options=("$60^\\circ$", "$30^\\circ$",
                                              "$90^\\circ$", "$120^\\circ$"),
             correct_index=0, expected="$60^\\circ$", solution=solution,
             task="Koliki je ugao kod tačke A?")
    assert store.peek("objects")["solution_summary"] == solution


# ---------------------------------------------------------------------------
# 4. NEDOKAZIVO UKLONJIVA OZNAKA — PADA ZATVORENO, BEZ MUTACIJE SESIJE
# ---------------------------------------------------------------------------

def test_the_h04_pointer_sentence_shape_is_removed_and_the_answer_survives(monkeypatch):
    """DRUGI ŽIVI SLUČAJ IZ ISTOG TALASA (H04) — prošao je ručni pregled SAMO
    zato što je miješanje tačnu opciju slučajno ostavilo na `a`.

    Ista rečenica na drugom ishodu miješanja je H12. Vlasništvo nad slovom ne
    smije zavisiti od sreće, pa se i ovaj oblik uklanja — bez gubitka
    matematike, jer završna rečenica-pokazivač ne nosi ni `$` ni cifru, a
    konačan odgovor kompozicija ionako dopisuje iz serverskog stanja."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[0])
    artifact = (
        "Paralelogram se definiše kao četverougao kod kojeg su oba para "
        "naspramnih stranica međusobno paralelna. To je dovoljan uslov.\n\n"
        "Dakle, jedino tvrdnja iz opcije a) je opšti dovoljan uslov."
    )
    options = ("Oba para naspramnih stranica su međusobno paralelna.",
               "Dijagonale se sijeku pod pravim uglom.",
               "Dijagonale su jednake dužine.",
               "Zbir unutrašnjih uglova iznosi $360^\\circ$.")
    store, fake = SessionStore(), FakeLLM()
    published = _publish(store, fake, "h04", options=options, correct_index=0,
                         expected=options[0], solution=artifact,
                         task="Koji uslov je dovoljan da četverougao bude paralelogram?",
                         grade=7, lesson="7-05-005")
    assert published["status"] == "ready", published.get("answer")

    session = store.peek("h04")
    assert session["correct_option_id"] == "a"          # slovo je BILO tačno…
    assert mcq_integrity.option_label_claims(session["solution_summary"]) == ()
    assert "opcije a)" not in session["solution_summary"]   # …i svejedno ne ostaje
    assert "oba para naspramnih stranica međusobno paralelna" in \
        session["solution_summary"]                     # matematika je netaknuta

    response, calls = _ask_full_solution(store, fake, "h04", grade=7,
                                         lesson="7-05-005")
    assert calls == 0
    assert options[0] in response["answer"]             # odgovor i dalje stoji
    assert response["revealed_correct_option_id"] == "a"


def test_a_trailing_sentence_carrying_mathematics_is_never_deleted():
    """Kapija briše samo POKAZIVAČ, nikad rečenicu koja nosi račun."""
    text = ("Prvo saberi stranice. Zbir je $O=3+4+5=12$ cm, pa je to opcija a.")
    normalized, code = mcq_integrity.option_label_normalization(text)
    assert code == ""
    assert normalized == "Prvo saberi stranice. Zbir je $O=3+4+5=12$ cm."
    withmath = ("Prvo saberi stranice. Opcija a daje $12$ cm i to je rezultat.")
    _normalized, code = mcq_integrity.option_label_normalization(withmath)
    assert code == mcq_integrity.OPTION_LABEL_CLAIM_CODE


UNREMOVABLE_ARTIFACTS = (
    "Tačan odgovor je opcija a.",
    "Opcija a je tačna jer je zbir stranica $12$ cm.",
    "Zbir je $12$ cm i zato opcija a najbolje opisuje rezultat trougla.",
)


@pytest.mark.parametrize("solution", UNREMOVABLE_ARTIFACTS)
def test_an_unremovable_option_claim_fails_closed_before_any_mutation(monkeypatch, solution):
    """Rečenica se NIKAD ne prepravlja pogađanjem, a slovo se nikad ne mijenja
    u drugo slovo — to bi od modelove tvrdnje napravilo serversku."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[2])
    store, fake = SessionStore(), FakeLLM()
    calls_before = fake.call_count
    response = _publish(store, fake, "unremovable",
                        options=("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"),
                        correct_index=0, expected="$12$ cm", solution=solution,
                        task="Trougao ima stranice $3$ cm, $4$ cm i $5$ cm. Koliki je obim $O$?")

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("unremovable") is None            # nijedna mutacija sesije
    assert fake.call_count - calls_before == 2          # i dalje TAČNO dva poziva


def test_a_marked_option_that_names_an_option_letter_fails_closed(monkeypatch):
    """Označena opcija DEFINIŠE identitet, pa se ona nikad ne normalizuje."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[1])
    store, fake = SessionStore(), FakeLLM()
    response = _publish(store, fake, "marked-label",
                        options=("Isto što i opcija b.", "$10$ cm", "$7$ cm", "$60$ cm"),
                        correct_index=0, expected="Isto što i opcija b.",
                        solution="Traženi obim je $12$ cm.",
                        task="Koliki je obim trougla?")
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert store.peek("marked-label") is None


# ---------------------------------------------------------------------------
# 5. ODBRANA U DUBINI NA PUTU POMOĆI
# ---------------------------------------------------------------------------

def test_a_corrupted_stored_artifact_fails_closed_instead_of_being_repaired(monkeypatch):
    """Popravka na putu pomoći bi pokvarila provenijenciju (posluženo više ne bi
    bilo kompozicija pohranjenog artefakta), pa se tamo PADA ZATVORENO."""
    _freeze_shuffle(monkeypatch, H12_PERMUTATION)
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "corrupt", options=H12_DRAFT_OPTIONS, correct_index=0,
             expected=H12_EXPECTED, solution=H12_SOLUTION_LABEL_FREE)
    session = store.peek("corrupt")
    session["solution_summary"] = H12_SOLUTION_WITH_STALE_LABEL
    store.save(session)
    before = copy.deepcopy(store.peek("corrupt"))
    calls_before = fake.call_count

    response, calls = _ask_full_solution(store, fake, "corrupt")

    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert response["task_preserved"] is True
    assert store.peek("corrupt") == before
    assert calls == 0 and fake.call_count == calls_before


def test_a_model_hint_that_names_an_option_letter_is_replaced_by_the_server_scaffold(monkeypatch):
    """Model-autorski nagovještaj (računska klasa, nivo 1) ne smije imenovati
    slovo — ni pogrešno ni tačno. Turn ne propada i nema drugog poziva."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[2])
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "model-label",
             options=("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"), correct_index=0,
             expected="$12$ cm", solution="Obim je zbir stranica: $O=3+4+5=12$ cm.",
             task="Trougao ima stranice $3$ cm, $4$ cm i $5$ cm. Koliki je obim $O$?")
    assert hint_policy.session_task_class(store.peek("model-label")) == \
        hint_policy.COMPUTATIONAL

    leaky = "Saberi sve tri stranice; rezultat je opcija c."
    fake.queue(make_tutor_draft(intent="hint_request", new_task=None,
                                reply="Idemo korak po korak.", hint=leaky))
    calls_before = fake.call_count
    response = run_practice_turn(store, fake, _turn(
        "model-label", "Ne znam.", intent="hint_request",
        interaction_phase="practice_help", client_turn_id="model-label-h1"))

    assert response["status"] == "ready"
    assert fake.call_count - calls_before == 1          # bez drugog poziva
    assert "opcija c" not in response["answer"]
    assert mcq_integrity.option_label_claims(response["answer"]) == ()
    assert store.peek("model-label")["hint_level"] == 1


# ---------------------------------------------------------------------------
# 6. GRAMATIKA OZNAKE — ZATVORENA, USKA I DOKAZIVA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected_claims", (
    ("što je opcija a", ("a",)),
    ("Opcija B je tačna.", ("b",)),
    ("Odgovor: c", ("c",)),
    ("Odgovor je d.", ("d",)),
    ("Rješenje je pod a).", ("a",)),
    ("Izbor b najbolje opisuje odnos.", ("b",)),
    ("This is option a.", ("a",)),
    ("Opcije su poređane po veličini.", ()),
    ("Odgovori učenika se razlikuju.", ()),
    ("Nalazi se pod stolom.", ()),
    ("Ugao je $60^\\circ$, a tačka A je vrh.", ()),
))
def test_the_option_label_grammar_is_closed_and_narrow(text, expected_claims):
    assert mcq_integrity.option_label_claims(text) == expected_claims


def test_normalization_never_loses_a_digit_or_a_math_segment():
    """„Sadržajno očuvano“ se DOKAZUJE, ne tvrdi."""
    text = "Površina je $P=a\\cdot b=4\\cdot 3=12$ cm$^2$, što je opcija d."
    normalized, code = mcq_integrity.option_label_normalization(text)
    assert code == ""
    assert normalized == "Površina je $P=a\\cdot b=4\\cdot 3=12$ cm$^2$."
    from matbot.mathsegments import math_contents, tokenize_math
    assert math_contents(tokenize_math(text)) == math_contents(tokenize_math(normalized))
    assert [ch for ch in text if ch.isdigit()] == [ch for ch in normalized if ch.isdigit()]


MEASURED_LIVE_CONCLUSION_SHAPES = (
    # Doslovni završeci objavljenih tekstova iz kampanjskih zapisa. Prva tri su
    # oblici koje normalizacija DOKAZIVO čisti; posljednja dva su oblici koji
    # PADAJU ZATVORENO, i to je svjesna granica: oznaka tamo stoji u istoj
    # klauzuli s matematikom, pa se brisanje ne može dokazati.
    ("Zapremina valjka je $45\\pi$ cm, što odgovara opciji d).", True),
    ("Dakle, koordinate tačke A su $(3,2)$, što je opcija a.", True),
    ("Zbir je $12$ cm. Dakle, jedino tvrdnja iz opcije a) je dovoljan uslov.", True),
    ("Jedina ponuđena opcija koja zadovoljava pravilo je opcija b) $75$.", False),
    ("Oznaka koja odgovara rezultatu je opcija c) $1\\frac{3}{4}$.", False),
)


@pytest.mark.parametrize("text,normalizable", MEASURED_LIVE_CONCLUSION_SHAPES)
def test_the_normalizer_is_calibrated_against_measured_live_conclusions(text, normalizable):
    """Kalibracija je IZMJERENA nad kampanjskim zapisima, ne pretpostavljena.

    Granica je namjerna i dokumentovana: kad oznaka stoji u istoj klauzuli s
    matematikom, brisanje se ne može dokazati content-preserving, pa objava
    pada zatvoreno umjesto da server pogađa šta je rečenica htjela reći."""
    _normalized, code = mcq_integrity.option_label_normalization(text)
    assert (code == "") is normalizable, code


def test_option_binding_failure_only_fires_on_a_contradicting_letter():
    assert mcq_integrity.option_binding_failure("nema oznake ovdje", "c") == ""
    assert mcq_integrity.option_binding_failure("to je opcija c", "c") == ""
    assert mcq_integrity.option_binding_failure("to je opcija a", "c") == \
        mcq_integrity.OPTION_LABEL_BINDING_CODE


# ---------------------------------------------------------------------------
# 7. FAZA 2 OSTAJE NETAKNUTA
# ---------------------------------------------------------------------------

def test_the_hint_ladder_state_machine_is_unchanged_by_the_repair(monkeypatch):
    """0 → 1 → 2 → 3, vrh ljestvice serverski i bez poziva."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[3])
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "ladder",
             options=("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"), correct_index=0,
             expected="$12$ cm", solution="Obim je zbir stranica: $O=3+4+5=12$ cm.",
             task="Trougao ima stranice $3$ cm, $4$ cm i $5$ cm. Koliki je obim $O$?")
    assert store.peek("ladder")["hint_level"] == 0

    model_hints = ("Saberi sve tri date stranice trougla.",
                   "Napiši $O=3+4+5$ i saberi brojeve sam.")
    for level, hint in enumerate(model_hints, start=1):
        fake.queue(make_tutor_draft(intent="hint_request", new_task=None,
                                    reply="Idemo korak po korak.", hint=hint))
        run_practice_turn(store, fake, _turn(
            "ladder", "Ne znam.", intent="hint_request",
            interaction_phase="practice_help", client_turn_id=f"ladder-h{level}"))
        assert store.peek("ladder")["hint_level"] == level

    calls_before = fake.call_count
    response = run_practice_turn(store, fake, _turn(
        "ladder", "Ne znam.", intent="hint_request",
        interaction_phase="practice_help", client_turn_id="ladder-h3"))
    assert fake.call_count == calls_before              # vrh ljestvice: nula poziva
    assert store.peek("ladder")["hint_level"] == config.MAX_HINT_LEVEL
    assert response["answer"] == hint_policy.compose_top_hint_for_session(
        store.peek("ladder"))


def test_session_composition_matches_the_pure_composition_when_fields_agree(monkeypatch):
    """Nova ulazna tačka ne mijenja nijedan zatečeni tekst: dok su označena
    opcija i `expected_answer_summary` jednaki (a objava to dokazuje), obje
    kompozicije daju BAJT ZA BAJT isti niz."""
    _freeze_shuffle(monkeypatch, _PERMUTATION_FOR_SLOT[1])
    store, fake = SessionStore(), FakeLLM()
    _publish(store, fake, "same", options=("$12$ cm", "$10$ cm", "$7$ cm", "$60$ cm"),
             correct_index=0, expected="$12$ cm",
             solution="Obim je zbir stranica: $O=3+4+5=12$ cm.",
             task="Trougao ima stranice $3$ cm, $4$ cm i $5$ cm. Koliki je obim $O$?")
    session = store.peek("same")
    task_class = hint_policy.session_task_class(session)
    assert hint_policy.compose_top_hint_for_session(session) == \
        hint_policy.compose_top_hint(session["solution_summary"],
                                     session["expected_answer_summary"], task_class)
    assert hint_policy.compose_full_solution_for_session(session) == \
        hint_policy.compose_full_solution(session["solution_summary"],
                                          session["expected_answer_summary"], task_class)
