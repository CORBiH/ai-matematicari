r"""DVA POSLJEDNJA OBJAVLJENA DEFEKTA CILJANOG ŽIVOG RECHECKA (T1 i T3).

Poruke, objavljeni zadaci, opcije i označeni odgovori ispod su DOSLOVNI zapisi
iz `scratchpad/practice_eval/targeted_last_41e39f8_20260810-125542/results.jsonl`.

T1 — nepotpun skup rješenja nad kontinuiranim domenom
-----------------------------------------------------
    zadatak: „Polazna nejednačina je $x>3$. U zadatku je napisana drugačija,
             ali ekvivalentna preoblikovana nejednačina dobijena tako što je na
             obje strane dodan isti nenulti cijeli broj: $x+2>5$. Nađi cijeli
             skup rješenja ove nejednačine.“
    opcije:  $\{2,3,4,\dots\}$ · $\{5,6,7,\dots\}$ · $\{3,4,5,\dots\}$ ·
             $\{4,5,6,\dots\}$      (označeno $\{4,5,6,\dots\}$)

Preoblikovanje je TAČNO ($x+2>5$ jeste $x>3$), ali lekcija radi nad
racionalnim brojevima, a nijedno cjelobrojno nabrajanje nije cio skup rješenja:
$7/2$ zadovoljava $x>3$ i ne stoji ni u jednoj opciji. Objavljen je MCQ bez
ijedne tačne opcije i s pogrešnim označenim odgovorom.

DVA nezavisna uzroka morala su se poklopiti da bi to prošlo:
  1. orakl rješavanja se GASIO čim tekst nosi dvije relacije — a pošteno
     preoblikovan zadatak ih nosi po definiciji, pa opcije nikad nisu ni
     provjerene;
  2. „dodan isti nenulti cijeli BROJ“ (jednina, opis DODANE KONSTANTE) čitalo
     se kao deklaracija domena Z, pa se rješenje diskretizovalo i nabrajanje je
     ispalo tačno.

T3 — objavljena neistinita izvedba
-----------------------------------
    poruka:  „…Polazna nejednačina je $x>3$. Pokušaj je preoblikovati
             dodavanjem 2 na obje strane i u tekstu zadatka predstavi $x+2>7$
             kao dobijenu relaciju koju treba riješiti…“
    zadatak: „Polazna nejednačina je $x>3$. Neko je pokušao preoblikovati
             dodavanjem 2 na obje strane i zapisao dobijenu relaciju $x+2>7$
             koju treba riješiti. Koji je cijeli skup rješenja te relacije?“
    opcije:  $x>5$ · $x>7$ · $x\ge5$ · $x>3$      (označeno $x>5$)

Dodavanjem 2 na obje strane od $x>3$ dobija se $x+2>5$, ne $x+2>7$. Zadatak
sedmom razredu objavljuje netačan korak kao ispravan.

ZAŠTO POSTOJEĆA ZAŠTITA NIJE PRORADILA: `transformed_relation_mismatch` poredi
zadatak sa ZAHTJEVOM i zato traži da zahtjev nosi tačno jednu čitljivu
relaciju — a ova poruka citira OBJE ($x>3$ i $x+2>7$), pa je zahtjev
nejednoznačan i sve provjere vezane za njega su ćutale.

ZERO poziva modela: sve je čist deterministički kod ili FakeLLM.
"""
import pytest

from matbot import mcq_integrity
from matbot.request_fidelity import (MISSING_DISTINCT_TRANSFORMED_RELATION,
                                     MISSING_REQUESTED_RELATION,
                                     RELATION_MISMATCH, REQUEST_FIDELITY_CODE,
                                     TRANSFORMED_RELATION_MISMATCH,
                                     TRANSFORMED_RELATION_NOT_EQUIVALENT,
                                     request_fidelity_failures)

DISCRETE_FOR_CONTINUOUS = (
    mcq_integrity.DISCRETE_OPTIONS_FOR_CONTINUOUS_SOLUTION_CODE)

# ---------------------------------------------------------------------------
# TAČNI ŽIVI ZAPISI
# ---------------------------------------------------------------------------

T1_MSG = (
    "Kreiraj samostalan MCQ sa četiri opcije. Polazna nejednačina je $x>3$. U "
    "tekstu zadatka obavezno napiši DRUGAČIJU, ali ekvivalentnu preoblikovanu "
    "nejednačinu tako što ćeš dodati isti nenulti cijeli broj na obje strane; "
    "nemoj samo prepisati polaznu relaciju $x>3$. Traži cijeli skup rješenja, "
    "osiguraj da je tačno jedna opcija matematički tačna i ne rješavaj zadatak "
    "učeniku.")
T1_TASK = (
    r"Polazna nejednačina je $x>3$. U zadatku je napisana drugačija, ali "
    r"ekvivalentna preoblikovana nejednačina dobijena tako što je na obje "
    r"strane dodan isti nenulti cijeli broj: $x+2>5$. Nađi cijeli skup "
    r"rješenja ove nejednačine.")
T1_OPTIONS = (r"$\{2,3,4,\dots\}$", r"$\{5,6,7,\dots\}$",
              r"$\{3,4,5,\dots\}$", r"$\{4,5,6,\dots\}$")
T1_MARKED = 3

# Ispravka koju recenzent MORA napraviti: cio skup nad domenom lekcije.
T1_REPAIRED_OPTIONS = (r"$x>5$", r"$x\ge3$", r"$x>3$", r"$x>2$")
T1_REPAIRED_MARKED = 2

T3_MSG = (
    "Kreiraj samostalan MCQ sa četiri opcije. Polazna nejednačina je $x>3$. "
    "Pokušaj je preoblikovati dodavanjem 2 na obje strane i u tekstu zadatka "
    "predstavi $x+2>7$ kao dobijenu relaciju koju treba riješiti. Traži cijeli "
    "skup rješenja, osiguraj da je tačno jedna opcija matematički tačna i ne "
    "rješavaj zadatak učeniku.")
T3_TASK = (
    r"Polazna nejednačina je $x>3$. Neko je pokušao preoblikovati dodavanjem 2 "
    r"na obje strane i zapisao dobijenu relaciju $x+2>7$ koju treba riješiti. "
    r"Koji je cijeli skup rješenja te relacije?")
T3_OPTIONS = (r"$x>5$", r"$x>7$", r"$x\ge5$", r"$x>3$")
T3_MARKED = 0

T3_REPAIRED_TASK = (
    r"Polazna nejednačina je $x>3$. Dodavanjem 2 na obje strane dobijamo "
    r"relaciju $x+2>5$ koju treba riješiti. Koji je cijeli skup rješenja te "
    r"relacije?")
T3_REPAIRED_OPTIONS = (r"$x>3$", r"$x>7$", r"$x\ge5$", r"$x>5$")
T3_REPAIRED_MARKED = 0

T3_DETAIL = (f"{TRANSFORMED_RELATION_NOT_EQUIVALENT}: the task derives 'x > 5' "
             "from its own 'x > 3', which is a different solution set")


# ===========================================================================
# T1 — KOMPLETNOST SKUPA RJEŠENJA NAD DOMENOM
# ===========================================================================

def test_t1_live_package_is_rejected():
    failure, _result = mcq_integrity.publication_failure(
        T1_TASK, T1_OPTIONS, T1_MARKED, T1_OPTIONS[T1_MARKED])
    assert failure == DISCRETE_FOR_CONTINUOUS


def test_t1_root_cause_one_the_oracle_now_judges_a_reformulated_task():
    """Prvi uzrok: orakl se gasio na DVIJE relacije, pa opcije nisu provjerene.
    Sada operativnu relaciju bira zajednička strukturna asocijacija."""
    original, operative = mcq_integrity.transformation_relations(T1_TASK)
    assert original is not None and original.display() == "x > 3"
    assert operative is not None
    assert T1_TASK[operative.start:operative.end] == "x+2>5"
    result = mcq_integrity.evaluate_linear_solve_mcq(T1_TASK, T1_OPTIONS)
    assert result.applicable, "orakl mora suditi preoblikovan zadatak"
    assert not result.valid and result.correct_indices == ()


def test_t1_root_cause_two_a_singular_number_is_not_a_domain():
    """Drugi uzrok: „dodan isti nenulti cijeli BROJ“ opisuje DODANU KONSTANTU,
    a čitalo se kao domen Z — pa se rješenje diskretizovalo i nabrajanje je
    ispalo tačno. Ime brojevnog SKUPA je u ovom korpusu u množini."""
    assert mcq_integrity.read_solve_statement(T1_TASK).domain_status == "none"
    result = mcq_integrity.evaluate_linear_solve_mcq(T1_TASK, T1_OPTIONS)
    assert result.solution_display == "x > 3", "skup ostaje kontinuiran"


@pytest.mark.parametrize("phrase,expected", [
    # deklaracije domena koje MORAJU i dalje raditi (množina / prijedlog)
    ("u skupu cijelih brojeva", "Z"),
    ("(cijeli brojevi)", "Z"),
    ("skup svih cijelih brojeva", "Z"),
    ("u skupu racionalnih brojeva", "Q"),
    ("u skupu realnih brojeva", "R"),
    ("u skupu prirodnih brojeva", "N"),
    ("cjelobrojna rješenja", "Z"),
])
def test_t1_real_domain_declarations_still_read(phrase, expected):
    status, domain, _variables = mcq_integrity._resolve_solve_domain(phrase)
    assert (status, domain) == ("supported", expected), phrase


@pytest.mark.parametrize("phrase", [
    "dodan isti nenulti cijeli broj",
    "dodaj isti cijeli broj na obje strane",
    "neki racionalan broj",
])
def test_t1_a_singular_value_never_declares_a_domain(phrase):
    status, _domain, _variables = mcq_integrity._resolve_solve_domain(phrase)
    assert status == "none", phrase


# --- Q / R / Z kontrolna matrica (uputa §5.A–§5.E) -------------------------

_ENUMERATION_OPTIONS = (r"$\{4,5,6,\dots\}$", r"$\{5,6,7,\dots\}$",
                        r"$\{3,4,5,\dots\}$", r"$\{2,3,4,\dots\}$")


@pytest.mark.parametrize("domain_phrase", [
    "u skupu racionalnih brojeva",     # §5.A — Q
    "u skupu realnih brojeva",         # §5.B — R
])
def test_t1_integer_enumeration_is_invalid_over_a_continuous_domain(domain_phrase):
    question = rf"Riješi nejednačinu $x>3$ {domain_phrase}."
    result = mcq_integrity.evaluate_linear_solve_mcq(question, _ENUMERATION_OPTIONS)
    assert result.applicable and not result.valid
    assert result.reason_code == DISCRETE_FOR_CONTINUOUS
    assert result.correct_indices == ()
    assert result.solution_display == "x > 3", "skup ostaje kontinuiran"


def test_t1_undeclared_domain_is_treated_as_continuous():
    """Nedokazan domen NIKAD ne postaje cjelobrojni — to je tačno stanje živog
    T1 zadatka, i zato nabrajanje tamo mora pasti."""
    result = mcq_integrity.evaluate_linear_solve_mcq(
        r"Riješi nejednačinu $x>3$.", _ENUMERATION_OPTIONS)
    assert result.reason_code == DISCRETE_FOR_CONTINUOUS


@pytest.mark.parametrize("domain_phrase", [
    "u skupu cijelih brojeva",         # §5.C — Z
    "u skupu prirodnih brojeva",       # N: x>3 daje isti zrak {4,5,6,…}
])
def test_t1_integer_enumeration_is_valid_over_a_discrete_domain(domain_phrase):
    question = rf"Riješi nejednačinu $x>3$ {domain_phrase}."
    result = mcq_integrity.evaluate_linear_solve_mcq(question, _ENUMERATION_OPTIONS)
    assert result.applicable and result.valid, result.reason_code
    assert result.correct_indices == (0,), result.option_displays


@pytest.mark.parametrize("marked_option", [
    r"$\{x\in Q\mid x>3\}$",           # §5.D
    r"$(3,\infty)$",                   # §5.E
    r"$x>3$",
])
def test_t1_complete_continuous_answers_are_valid_over_q(marked_option):
    question = r"Riješi nejednačinu $x>3$ u skupu racionalnih brojeva."
    options = (marked_option, r"$x>5$", r"$x\ge3$", r"$x>2$")
    result = mcq_integrity.evaluate_linear_solve_mcq(question, options)
    assert result.applicable and result.valid, result.reason_code
    assert result.correct_indices == (0,)


def test_t1_interval_and_equivalent_set_builder_may_not_both_be_offered():
    """§5.F — dva zapisa ISTOG skupa nikad ne smiju biti dvije opcije.

    Kad su OBA i tačan odgovor, prvi se javi stroži nalaz (`multiple_correct_
    options`); kad su oba distraktori, javi se nalaz o duplikatima. Bitno je da
    nijedan takav MCQ ne prolazi."""
    both_correct = mcq_integrity.evaluate_linear_solve_mcq(
        r"Riješi nejednačinu $x>3$ u skupu racionalnih brojeva.",
        (r"$(3,\infty)$", r"$\{x\in Q\mid x>3\}$", r"$x>5$", r"$x\ge3$"))
    assert both_correct.applicable and not both_correct.valid
    assert both_correct.reason_code == "multiple_correct_options"
    assert both_correct.correct_indices == (0, 1)

    both_distractors = mcq_integrity.evaluate_linear_solve_mcq(
        r"Riješi nejednačinu $x>3$ u skupu racionalnih brojeva.",
        (r"$x>3$", r"$(5,\infty)$", r"$\{x\in Q\mid x>5\}$", r"$x\ge3$"))
    assert both_distractors.applicable and not both_distractors.valid
    assert both_distractors.reason_code == (
        mcq_integrity.EQUIVALENT_SOLUTION_OPTIONS_CODE)


# --- §5.G: postojeća semantika golih vrijednosti i konačnih skupova --------

@pytest.mark.parametrize("option,domain,expected_kind", [
    (r"$\{5\}$", "", "point"),               # jednočlan skup = tačka
    (r"$5$", "", "point"),                   # gola vrijednost
    (r"$\{1,2,3\}$", "Z", "finite"),         # zatvoren konačan skup nad Z
    (r"$\{1,2,3\}$", "", None),              # i dalje nečitljiv nad Q/R
    (r"$(3,\infty)$", "", "ray"),
])
def test_t1_existing_option_semantics_are_unchanged(option, domain, expected_kind):
    parsed = mcq_integrity._solve_option_set(option, "x", allow_bare_value=True,
                                             domain=domain)
    assert (None if parsed is None else parsed.kind) == expected_kind, option


@pytest.mark.parametrize("option", [
    r"$\{2,4,6,\dots\}$",          # korak nije 1 — aritmetičke progresije se ne modeluju
    r"$\{4,\dots\}$",              # jedan član ne dokazuje korak
    r"$\{\dots,1,2,3\}$",          # vodeće tri tačke se ne pogađaju
    r"$\{4,6,5,\dots\}$",          # nesortirano
])
def test_t1_unprovable_enumerations_stay_unreadable(option):
    assert mcq_integrity._solve_option_set(
        option, "x", allow_bare_value=True, domain="Z") is None, option


def test_t1_enumeration_below_the_domain_minimum_is_not_reinterpreted():
    """`{-1,0,1,…}` nad N nije „isto što i {1,2,3,…}“ — napisani članovi ne
    pripadaju domenu, pa se zapis ne tumači umjesto modela."""
    assert mcq_integrity._solve_option_set(
        r"$\{-1,0,1,\dots\}$", "x", allow_bare_value=True, domain="N") is None
    assert mcq_integrity._solve_option_set(
        r"$\{-1,0,1,\dots\}$", "x", allow_bare_value=True, domain="Z") is not None


def test_t1_enumeration_uses_exact_fractions_not_sampling():
    """Kanonski oblik je egzaktan `int_ray`, isti koji `_discretize` proizvodi
    za $x>3$ nad Z — jednakost je jednakost SKUPOVA, bez ijednog uzorkovanja."""
    from fractions import Fraction
    parsed = mcq_integrity._solve_option_set(
        r"$\{4,5,6,\dots\}$", "x", allow_bare_value=True, domain="Z")
    assert parsed.kind == "int_ray" and parsed.op == ">="
    assert parsed.value == Fraction(4)
    assert parsed == mcq_integrity._discretize(
        mcq_integrity._SolutionSet.ray(">", Fraction(3)), "Z")
    # ...a nad kontinuiranim skupom NIKAD nije jednak zraku.
    assert parsed != mcq_integrity._SolutionSet.ray(">", Fraction(3))


# ===========================================================================
# T3 — NEISTINITA IZVEDBA U SAMOM ZADATKU
# ===========================================================================

def test_t3_live_package_is_a_deterministic_finding():
    assert request_fidelity_failures(T3_MSG, T3_TASK) == (T3_DETAIL,)


def test_t3_root_cause_the_request_itself_quoted_both_relations():
    """Zašto je stara zaštita ćutala: poruka nosi OBJE relacije, pa je kao
    zahtjev nejednoznačna i sve provjere vezane za zahtjev su isključene."""
    request = mcq_integrity.read_solve_statement(T3_MSG)
    assert not request.has_relation and request.relation_ambiguous
    assert [relation.display()
            for relation in mcq_integrity.read_solve_relations(T3_MSG)] == [
        "x > 3", "x > 5"]


def test_t3_operative_relation_is_associated_structurally():
    original, operative = mcq_integrity.transformation_relations(T3_TASK)
    assert original is not None and T3_TASK[original.start:original.end] == "x>3"
    assert operative is not None and T3_TASK[operative.start:operative.end] == "x+2>7"


def test_t3_repaired_task_publishes_clean():
    assert request_fidelity_failures(T3_MSG, T3_REPAIRED_TASK) == ()


def test_t3_finding_does_not_depend_on_the_request_at_all():
    """Neistinita izvedba je pogrešna matematika bez obzira na poruku."""
    for message in ("", "Daj mi jedan zadatak.", "Ne znam.", "teže"):
        assert request_fidelity_failures(message, T3_TASK) == (T3_DETAIL,), message


# --- uputa §9: strukturna asocijacija, ne poređenje svake relacije ---------

def test_t3_section9_good_example_is_allowed():
    task = (r"Početna je $x>3$. Dodamo 2 na obje strane i dobijemo $x+2>5$. "
            r"Riješi dobijenu nejednačinu.")
    assert request_fidelity_failures("Daj mi jedan zadatak.", task) == ()


def test_t3_section9_bad_example_is_blocked():
    """Ova rečenica NE sadrži nijednu riječ iz porodice „dobijena/nastala“ —
    tvrdnju o preoblikovanju nosi ZAHVAT NAD STRANAMA. Upravo ta klasa je bila
    slijepa mrlja poslije prvog ciljanog prolaza."""
    task = (r"Početna je $x>3$. Na lijevu stranu dodamo 2, a na desnu 4, pa "
            r"imamo $x+2>7$. Riješi tu nejednačinu.")
    failures = request_fidelity_failures("Daj mi jedan zadatak.", task)
    assert len(failures) == 1
    assert failures[0].startswith(TRANSFORMED_RELATION_NOT_EQUIVALENT)


# --- uputa §11: matrica ---------------------------------------------------

@pytest.mark.parametrize("task", [
    # GOOD: samo preoblikovana relacija
    r"Riješi nejednačinu $x+2>5$ i nađi cijeli skup rješenja.",
    # GOOD: polazna + ekvivalentna dobijena
    r"Polazna je $x>3$. Dodavanjem 2 na obje strane dobijamo $x+2>5$. Riješi je.",
    # GOOD: jednačina, isti kanonski skup
    r"Polazna je $2x=6$, pa dijeljenjem obje strane dobijamo $x=3$. Riješi je.",
    # GOOD: obična relacija bez ijedne tvrdnje o preoblikovanju
    r"Riješi nejednačinu $x>3$ i nađi cijeli skup rješenja.",
    # GOOD: množenje obje strane istim brojem
    r"Polazna je $x>3$. Pomnožimo obje strane s 2 pa dobijamo $2x>6$. Riješi je.",
])
def test_t3_good_shapes_stay_allowed(task):
    assert request_fidelity_failures("Daj mi jedan zadatak.", task) == (), task


@pytest.mark.parametrize("task", [
    # BAD: polazna + neekvivalentna dobijena
    r"Polazna je $x>3$. Dodavanjem 2 na obje strane dobijamo $x+2>7$. Riješi je.",
    # BAD: rezultat naveden direktno kao drugi skup
    r"Polazna je $x>3$, pa preoblikovanjem dobijamo $x>5$. Riješi dobijenu nejednačinu.",
    # BAD: množenje negativnim brojem bez okretanja znaka
    r"Polazna je $x>3$. Pomnožimo obje strane s $-1$ pa dobijamo $-x>-3$. Riješi je.",
])
def test_t3_bad_shapes_are_blocked(task):
    failures = request_fidelity_failures("Daj mi jedan zadatak.", task)
    assert [detail for detail in failures
            if detail.startswith(TRANSFORMED_RELATION_NOT_EQUIVALENT)], task


@pytest.mark.parametrize("task", [
    # nelinearna „dobijena“ relacija — ne čita se, ne pogađa se
    r"Polazna je $x>3$. Kvadriranjem obje strane dobijamo $x^2>9$. Riješi je.",
    # iza markera nema relacije
    r"Polazna je $x>3$. Preoblikovanjem dobijamo traženo. Riješi to.",
    # nema tvrdnje o preoblikovanju
    r"Riješi nejednačinu $x>3$. Uporedi je sa $x>10$ iz prošlog časa.",
    # tekst ne traži rješavanje
    r"Polazna je $x>3$, a dobijena $x+2>7$. Koja tvrdnja opisuje taj korak?",
    # rezultat naveden PRIJE polazne — par se ne formira
    r"Dobijena nejednačina je $x+2>7$, a polazna je bila $x>3$. Riješi je.",
])
def test_t3_unprovable_shapes_stay_conservative(task):
    assert request_fidelity_failures("Daj mi jedan zadatak.", task) == (), task


def test_t3_discrete_domain_equivalence_still_holds():
    """Nad Z su $x>3$ i $x\\ge 4$ ISTI skup — izvedba je tačna i prolazi."""
    assert request_fidelity_failures(
        "Daj mi jedan zadatak.",
        r"Polazna je $x>3$ u skupu cijelih brojeva. Preoblikovanjem dobijamo "
        r"$x\ge 4$. Riješi dobijenu nejednačinu.") == ()


def test_t3_geometry_sides_never_enter_the_grammar():
    """`stranica` (mnogougla) nije `strana` (relacije) — geometrijski zadatak
    ne smije ući u gramatiku preoblikovanja."""
    task = (r"Trougao ima stranice $a=3$ i $b=4$. Ako stranicama dodamo po "
            r"$1$ cm, koliki je obim? Riješi zadatak.")
    assert request_fidelity_failures("Daj mi jedan zadatak.", task) == ()


# ===========================================================================
# ČETIRI KLASE VJERNOSTI ZAHTJEVU OSTAJU RAZLUČIVE (uputa §10)
# ===========================================================================

_CLASS_A_MSG = ("Riješi nejednačinu $x>3$, ali je preoblikuj; ne prepisuj "
                "relaciju doslovno.")
_CLASS_A_TASK = (r"Na obje strane originalne nejednačine dodan je isti broj "
                 r"$2$. Riješite dobijenu nejednačinu.")
_CLASS_B_TASK = (r"Riješi nejednačinu dobijenu dodavanjem istog broja na obje "
                 r"strane originalne relacije $x>3$.")
_CLASS_C_MSG = "Riješi x>3"
_CLASS_C_TASK = (r"Početna nejednačina je $x>3$. Dodajemo $2$ s lijeve strane "
                 r"i $4$ s desne strane pa dobijamo $x+2>7$. Riješi dobijenu "
                 r"nejednačinu $x+2>7$.")
_CLASS_D_TASK = r"Riješi nejednačinu $x+2>7$."


@pytest.mark.parametrize("message,task,expected_code", [
    (_CLASS_A_MSG, _CLASS_A_TASK, MISSING_REQUESTED_RELATION),
    (_CLASS_A_MSG, _CLASS_B_TASK, MISSING_DISTINCT_TRANSFORMED_RELATION),
    (_CLASS_C_MSG, _CLASS_C_TASK, TRANSFORMED_RELATION_MISMATCH),
    (_CLASS_C_MSG, _CLASS_D_TASK, RELATION_MISMATCH),
    ("Daj mi jedan zadatak.", _CLASS_C_TASK, TRANSFORMED_RELATION_NOT_EQUIVALENT),
])
def test_each_fidelity_class_reports_exactly_its_own_code(message, task,
                                                          expected_code):
    failures = request_fidelity_failures(message, task)
    assert len(failures) == 1, failures
    assert failures[0].startswith(expected_code), failures


def test_the_request_anchored_finding_wins_when_the_request_is_readable():
    """Isti zadatak, dva sidra: kad zahtjev nosi jednu čitljivu relaciju
    presuđuje poređenje sa ZAHTJEVOM, inače poređenje sa SAMIM zadatkom.
    Nikad oba koda za isti defekt."""
    with_request = request_fidelity_failures(_CLASS_C_MSG, _CLASS_C_TASK)
    without_request = request_fidelity_failures("Daj mi zadatak.", _CLASS_C_TASK)
    assert len(with_request) == 1 and len(without_request) == 1
    assert with_request[0].startswith(TRANSFORMED_RELATION_MISMATCH)
    assert without_request[0].startswith(TRANSFORMED_RELATION_NOT_EQUIVALENT)


# ===========================================================================
# PREFLIGHT, RECEPT I STVARAN DVOPOZIVNI PUT
# ===========================================================================

def _task_payload(text, options, marked, lesson_id="7-03-019",
                  lesson_title="Nejednačine u skupu Q"):
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    return TaskPayload(
        selected_lesson_id=lesson_id, selected_lesson_title=lesson_title,
        target_difficulty_level=1, text=text, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=marked, correct_option_id="abcd"[marked],
        expected_answer=options[marked],
        solution="Serverski test.", difficulty="easy",
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        task_signature=TaskSignature(
            task_family="linear_inequality", operation_or_relation="solve",
            normalized_parameters=[SignatureParameter(name="case", value="t13")],
            required_conditions=[], relevant_objects=["nejednačina"],
            answer_type="multiple_choice"))


def test_t1_preflight_reports_the_domain_completeness_defect():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(T1_TASK, T1_OPTIONS, T1_MARKED), student_message=T1_MSG)
    assert [issue for issue in issues
            if issue.code == DISCRETE_FOR_CONTINUOUS], [i.code for i in issues]


def test_t3_preflight_reports_the_false_derivation():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _task_payload(T3_TASK, T3_OPTIONS, T3_MARKED), student_message=T3_MSG)
    found = [issue for issue in issues if issue.code == REQUEST_FIDELITY_CODE]
    assert found, [issue.code for issue in issues]
    assert found[0].detail == T3_DETAIL


def test_reviewer_recipes_name_the_domain_and_derivation_rules():
    from matbot.tutor import package_preflight as preflight
    block = preflight.format_for_reviewer(
        preflight.collect_package_issues(
            _task_payload(T1_TASK, T1_OPTIONS, T1_MARKED), student_message=T1_MSG)
        + preflight.collect_package_issues(
            _task_payload(T3_TASK, T3_OPTIONS, T3_MARKED), student_message=T3_MSG))
    # T1 (uputa §12): sačuvaj domen i daj KOMPLETAN skup nad njim
    assert DISCRETE_FOR_CONTINUOUS in block
    assert "can NEVER be the complete solution set" in block
    assert "never change" in block and "domain" in block
    assert "7/2" in block
    # T3 (uputa §12): sačuvaj ekvivalentnost izvedbe
    assert TRANSFORMED_RELATION_NOT_EQUIVALENT in block
    assert "contradicts ITSELF" in block
    assert "$x+2>5$" in block and "$x+2>7$" in block
    assert "fail_closed" in block


def test_preflight_details_leak_no_visible_content():
    """CLAUDE.md pravilo 7: detalj nosi samo serverski izvedene činjenice."""
    from matbot.tutor import package_preflight as preflight
    described = preflight.describe_issues(
        preflight.collect_package_issues(
            _task_payload(T1_TASK, T1_OPTIONS, T1_MARKED), student_message=T1_MSG)
        + preflight.collect_package_issues(
            _task_payload(T3_TASK, T3_OPTIONS, T3_MARKED), student_message=T3_MSG))
    for leaked in ("Polazna nejednačina", "nenulti", "Neko je pokušao",
                   "Kreiraj samostalan MCQ", "Nađi cijeli skup"):
        assert leaked not in described, leaked


def _turn(session_id, message):
    return {
        "session_id": session_id, "grade": 7, "selected_topic": "7-03-019",
        "selected_oblast": "", "student_message": message, "intent": "",
        "difficulty_request": "", "interaction_phase": "", "last_tutor_task": "",
        "interaction_type": "student_question", "selected_option_id": "",
        "client_turn_id": "",
    }


def _run(monkeypatch, session_id, message, draft_task, *, decision="approve",
         final_task=None):
    from matbot.practice import run_practice_turn
    from matbot.session_store import SessionStore
    from matbot.tutor.schema import ReviewerChecks, ReviewerFinal, TutorDraft
    from tests.conftest import FakeLLM

    monkeypatch.setenv("MATBOT_PRACTICE_PIPELINE", "universal_two_call")
    monkeypatch.setenv("MATBOT_DETERMINISTIC_PRACTICE", "disabled")
    store, fake = SessionStore(), FakeLLM()
    draft = TutorDraft(intent="generate_task", reply="Evo zadatka.",
                       lesson_focus="nejednačine", new_task=draft_task)
    final_task = draft_task if final_task is None else final_task
    fake.queue(draft)
    fake.queue(ReviewerFinal(
        decision=decision,
        checks=ReviewerChecks(
            math_correct=True, marked_option_correct=True, inside_lesson=True,
            intent_handled=True, difficulty_direction_correct=True,
            response_addresses_student=True,
            task_solvable_and_unambiguous=True, mathjax_valid=True,
            language_age_appropriate=True, independently_solved=True,
            independent_answer="provjereno", task_package_consistent=True,
            difficulty_evidence_valid=True, task_signature_consistent=True,
 stem_requires_student_reasoning=True),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=final_task.difficulty_evidence))
    response = run_practice_turn(store, fake, _turn(session_id, message))
    return response, store.peek(session_id), fake


def test_t1_unchanged_reviewer_approval_fails_closed(monkeypatch):
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run(
        monkeypatch, "t1-bad", T1_MSG,
        _task_payload(T1_TASK, T1_OPTIONS, T1_MARKED))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None, "ništa se ne smije upisati u sesiju"
    assert fake.call_count == 2, "bez trećeg poziva"


def test_t1_reviewer_repair_to_the_complete_set_publishes(monkeypatch):
    response, session, fake = _run(
        monkeypatch, "t1-fixed", T1_MSG,
        _task_payload(T1_TASK, T1_OPTIONS, T1_MARKED),
        decision="correct",
        final_task=_task_payload(T1_TASK, T1_REPAIRED_OPTIONS, T1_REPAIRED_MARKED))
    assert response.get("status") == "ready"
    assert session is not None
    assert fake.call_count == 2
    # Označen odgovor je KOMPLETAN skup nad domenom, a ne nabrajanje. ID opcije
    # se NE provjerava: `practice._shuffle_options` ga namjerno randomizuje po
    # turnu, pa je tekst jedina stabilna tvrdnja.
    assert session["expected_answer_summary"] == r"$x>3$"
    assert not any(r"\dots" in option["text"]
                   for option in session["current_options"])
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert DISCRETE_FOR_CONTINUOUS in reviewer_input
    assert "complete solution set" in reviewer_input


def test_t3_unchanged_reviewer_approval_fails_closed(monkeypatch):
    from matbot.tutor.pipeline import SAFE_ERROR_MESSAGE

    response, session, fake = _run(
        monkeypatch, "t3-bad", T3_MSG,
        _task_payload(T3_TASK, T3_OPTIONS, T3_MARKED))
    assert response["answer"] == SAFE_ERROR_MESSAGE
    assert "status" not in response
    assert session is None
    assert fake.call_count == 2


def test_t3_reviewer_repair_of_the_derivation_publishes(monkeypatch):
    response, session, fake = _run(
        monkeypatch, "t3-fixed", T3_MSG,
        _task_payload(T3_TASK, T3_OPTIONS, T3_MARKED),
        decision="correct",
        final_task=_task_payload(T3_REPAIRED_TASK, T3_REPAIRED_OPTIONS,
                                 T3_REPAIRED_MARKED))
    assert response.get("status") == "ready"
    assert session is not None
    assert fake.call_count == 2
    assert "$x+2>5$" in session["current_task"]
    assert "$x+2>7$" not in session["current_task"]
    _instructions, reviewer_input = fake.reviewer_calls[0]
    assert TRANSFORMED_RELATION_NOT_EQUIVALENT in reviewer_input
    assert "contradicts ITSELF" in reviewer_input
