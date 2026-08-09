"""TASK 1 — SOLVE-SET MATHEMATICS (DISC-100 + LSP0 talasi na 8a8f04d).

Tri dokazana P0 korijena, zamrznuta kao tačne istorijske regresije:

  RC1: matematički ekvivalentni zapisi ISTOG odgovora objavljeni kao dvije
       „različite“ opcije (B007: 11 i {11}; B012: 2 i {2}; B014: 6 i {6};
       A012: (-2,∞) i {x∈Q : x>-2}; E02 nad Z: x=-1 i -2<x<0);
  RC2: uobičajena školska sintaksa zadatka van dometa orakla (2(x-3)=x+5,
       \\frac{2x+1}{3}, \\frac{1}{\\frac{2}{3}}x, \\displaystyle);
  RC3: izričit domen Q/R/Z/N/N0 gasio je orakl umjesto da se presuđuje POD
       domenom (Q/R kontinuirano; Z/N/N0 presjek s cijelim brojevima;
       KONVENCIJA PROJEKTA: N={1,2,...}, N0={0,1,...}).

ZERO model poziva: sve je čist deterministički kod.
"""
import pytest

from matbot import mcq_integrity
from matbot.mcq_integrity import evaluate_linear_solve_mcq, publication_failure
from matbot.option_equivalence import (find_equivalent_option_pairs,
                                       options_are_equivalent)


def evaluate(question, options):
    return evaluate_linear_solve_mcq(question, options)


# ---------------------------------------------------------------------------
# 1) TAČNE ISTORIJSKE REGRESIJE — objavljeni paketi koji sada moraju pasti
# ---------------------------------------------------------------------------

B007_TASK = r"Riješi jednačinu $2(x-3)=x+5$ i izaberi tačno rješenje."
B007_OPTIONS = (r"$\{11\}$", "$11$", "$-11$", r"$10\frac{1}{2}$")


def test_disc_b007_bare_and_singleton_duplicate_is_blocked():
    """DISC-B007: 2(x-3)=x+5, opcije $\\{11\\}$ i $11$ su ISTA tačka —
    objavljen MCQ s dvije tačne opcije. RC2 čita zagradu, RC1 vidi duplikat."""
    result = evaluate(B007_TASK, B007_OPTIONS)
    assert result.applicable, "zagrada 2(x-3) više ne smije gasiti orakl"
    assert not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.solution_display == "x = 11"
    assert result.correct_indices == (0, 1)
    failure, _ = publication_failure(B007_TASK, B007_OPTIONS, 1, B007_OPTIONS[1])
    assert failure == "multiple_correct_options"


def test_disc_b007_publication_layer_also_sees_the_duplicate():
    """RC1, dubinska odbrana: i domen-slijepa publikacijska kapija
    (option_equivalence) sada čita \\{11\\} ≡ 11 — dvije nezavisne brave."""
    assert find_equivalent_option_pairs(list(B007_OPTIONS)) == [(0, 1)]


B012_TASK = (r"Riješi jednačinu: $\frac{2x+1}{3}=\frac{5}{3}$. "
             r"Koja je vrijednost $x$?")
B012_OPTIONS = ("$2$", r"$\frac{3}{2}$", "$1$", r"$\{2\}$")


def test_disc_b012_linear_numerator_fraction_duplicate_is_blocked():
    result = evaluate(B012_TASK, B012_OPTIONS)
    assert result.applicable, r"\frac{2x+1}{3} više ne smije gasiti orakl"
    assert not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.solution_display == "x = 2"
    assert result.correct_indices == (0, 3)
    failure, _ = publication_failure(B012_TASK, B012_OPTIONS, 0, B012_OPTIONS[0])
    assert failure == "multiple_correct_options"


B013_TASK = (r"Riješi jednačinu $\frac{x+1}{2}=\frac{7}{3}$ i izaberi "
             r"tačan vrijednost za $x$.")
B013_OPTIONS = (r"$3\frac{1}{3}$", "$3$", r"$\frac{11}{3}$",
                r"$(-\infty,\,\frac{11}{3})$")


def test_disc_b013_fraction_task_now_gets_deterministic_verification():
    """DISC-B013: ISPRAVAN paket koji je ranije prolazio BEZ ijedne
    matematičke provjere — sada dobija punu deterministički potvrdu
    (x=11/3; mješoviti broj 10/3, tačka 3 i zrak su dokazano različiti)."""
    result = evaluate(B013_TASK, B013_OPTIONS)
    assert result.applicable and result.valid
    assert result.solution_display == "x = 11/3"
    assert result.option_displays == ("x = 10/3", "x = 3", "x = 11/3",
                                      "x < 11/3")
    assert result.correct_index == 2
    failure, _ = publication_failure(B013_TASK, B013_OPTIONS, 2, B013_OPTIONS[2])
    assert failure == ""


B014_TASK = ("Riješi jednačinu i izaberi tačan odgovor:\n\n"
             + r"$\displaystyle \frac{1}{\frac{2}{3}}x=9$")
B014_OPTIONS = (r"$\frac{3}{2}$", "$6$", r"$\{6\}$", "$5$")


def test_disc_b014_nested_numeric_coefficient_duplicate_is_blocked():
    r"""DISC-B014: \frac{1}{\frac{2}{3}} = 3/2 EGZAKTNO (Fraction, nikad
    tekstualno nadovezan 1§2§3); x=6, a opcije 6 i {6} su isti odgovor."""
    result = evaluate(B014_TASK, B014_OPTIONS)
    assert result.applicable, r"\displaystyle + ugniježden brojčani koeficijent"
    assert not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.solution_display == "x = 6"
    assert result.correct_indices == (1, 2)
    failure, _ = publication_failure(B014_TASK, B014_OPTIONS, 1, B014_OPTIONS[1])
    assert failure == "multiple_correct_options"


B017_TASK = (r"Riješi nejednačinu: $2(x-1)<x+4$. Označi tačan zapis "
             r"skupa rješenja.")
B017_OPTIONS = (r"$x>6$", r"$\{6\}$", r"$x<6$", r"$(-\infty,6]$")


def test_disc_b017_parenthesised_task_engages_the_oracle():
    """DISC-B017: ISPRAVAN paket — 2(x-1)<x+4 → x<6; {6} je tačka (član),
    (-∞,6] ima pogrešnu granicu. Orakl se angažuje i potvrđuje."""
    result = evaluate(B017_TASK, B017_OPTIONS)
    assert result.applicable and result.valid
    assert result.solution_display == "x < 6"
    assert result.option_displays == ("x > 6", "x = 6", "x < 6", "x <= 6")
    assert result.correct_index == 2
    failure, _ = publication_failure(B017_TASK, B017_OPTIONS, 2, B017_OPTIONS[2])
    assert failure == ""


A012_TASK = (r"Riješi nejednačinu $-3x<6$ isključivo u domenu racionalnih "
             r"brojeva. Izaberi cijeli skup rješenja.")
A012_OPTIONS = (r"$(-2,\infty)$", r"$\{x\in\mathbb{Q} : x\le-2\}$",
                r"$\{x\in\mathbb{Q} : x>-2\}$", r"$x<-2$")


def test_disc_a012_q_domain_set_builder_duplicate_is_blocked():
    """DISC-A012: nad Q su $(-2,\\infty)$ i $\\{x\\in\\mathbb{Q} : x>-2\\}$
    ISTI skup rješenja — objavljen MCQ s dvije tačne opcije. RC3 čita domen Q
    (kontinuirano, nikad diskretizovano) i set-builder zapis."""
    result = evaluate(A012_TASK, A012_OPTIONS)
    assert result.applicable, "izričit Q domen više ne gasi orakl"
    assert not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.solution_display == "x > -2"
    assert result.correct_indices == (0, 2)
    failure, _ = publication_failure(A012_TASK, A012_OPTIONS, 2, A012_OPTIONS[2])
    assert failure == "multiple_correct_options"


E02_TASK = (r"Riješi nejednačinu $-1 < x + 1 < 1$. Izaberi tačan skup "
            r"rješenja (cijeli brojevi).")
E02_OPTIONS = (r"$x<0$", r"$x=-1$", r"$x\in\{-2,-1\}$", r"$-2<x<0$")


def test_lsp0_e02_integer_domain_duplicate_is_blocked():
    """LSP0-E02 (release-blocking): nad Z je -2<x<0 tačno {-1}, pa su x=-1 i
    -2<x<0 ISTI potpuni odgovor — objavljen MCQ s dvije tačne opcije. Nad Q/R
    to NISU isti skupovi (kontrola ispod) — jednakost važi samo s dokazom
    domena."""
    result = evaluate(E02_TASK, E02_OPTIONS)
    assert result.applicable, "„(cijeli brojevi)“ više ne gasi orakl"
    assert not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.solution_display == "{-1}"
    assert result.option_displays == ("x <= -1 [cijeli]", "{-1}", "{-2, -1}",
                                      "{-1}")
    assert result.correct_indices == (1, 3)
    failure, _ = publication_failure(E02_TASK, E02_OPTIONS, 1, E02_OPTIONS[1])
    assert failure == "multiple_correct_options"


def test_e02_pair_is_not_equivalent_without_domain_evidence():
    """x=-1 i -2<x<0 NAD Q/R nisu isti skup: domen-slijepa publikacijska
    kapija ih NE smije proglasiti duplikatom, a kontinuirani zadatak ih
    presuđuje kao tačku (pogrešnu) i interval (tačan). Konačan skup
    `x∈{-2,-1}` bez diskretnog domena ostaje neprovjerljiv (fail closed),
    pa kontrolni zadatak nosi čitljive kontinuirane opcije."""
    assert not options_are_equivalent(r"$x=-1$", r"$-2<x<0$")
    question = r"Riješi nejednačinu $-1 < x + 1 < 1$."
    result = evaluate(question, (r"$x<0$", r"$x=-1$", r"$x=0$", r"$-2<x<0$"))
    assert result.applicable and result.valid
    assert result.solution_display == "-2 < x < 0"
    assert result.correct_index == 3
    # a tačan E02 paket bez domenske rečenice pada zatvoreno na
    # neprovjerljivom konačnom skupu — nikad tiho, nikad pogođen
    undomained = evaluate(question, E02_OPTIONS)
    assert undomained.applicable and not undomained.valid
    assert undomained.reason_code == "unverifiable_solution_option"


# ---------------------------------------------------------------------------
# 2) DIREKTNE DOMENSKE KONTROLE (zahtjev §8)
# ---------------------------------------------------------------------------

def test_over_z_strict_and_inclusive_rays_canonicalize_identically():
    """Nad Z: x>3 == x>=4 i x<6 == x<=5 — dvije takve opcije su duplikat."""
    result = evaluate(r"Riješi nejednačinu $x-1>2$ u skupu cijelih brojeva.",
                      (r"$x\ge 4$", r"$x>3$", r"$x\ge 3$", r"$x<4$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.correct_indices == (0, 1)
    assert result.solution_display == "x >= 4 [cijeli]"

    result = evaluate(r"Riješi nejednačinu $x+1<7$ u skupu cijelih brojeva.",
                      (r"$x\le 5$", r"$x<6$", r"$x\le 6$", r"$x>5$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.correct_indices == (0, 1)
    assert result.solution_display == "x <= 5 [cijeli]"


def test_over_z_strict_ray_differs_from_inclusive_at_same_bound():
    """Nad Z: x>3 NIJE x>=3 — granice 4 i 3 su različiti kanonski zapisi."""
    result = evaluate(r"Riješi nejednačinu $x-1>2$ u skupu cijelih brojeva.",
                      (r"$x\ge 4$", r"$x\ge 3$", r"$x\le 2$", r"$x<4$"))
    assert result.applicable and result.valid
    assert result.correct_index == 0
    assert result.option_displays[1] == "x >= 3 [cijeli]"


def test_over_n_bounded_ray_materializes_from_one():
    """Nad N (={1,2,...}): x<3 je tačno {1,2}."""
    result = evaluate(r"Riješi nejednačinu $x+1<4$ u skupu prirodnih brojeva.",
                      (r"$\{1,2\}$", r"$\{0,1,2\}$", r"$\{2,3\}$", r"$x>3$"))
    assert result.applicable and result.valid
    assert result.solution_display == "{1, 2}"
    assert result.correct_index == 0


def test_over_n0_bounded_ray_includes_zero():
    """Nad N0 (={0,1,2,...}): x<3 je tačno {0,1,2}."""
    result = evaluate(r"Riješi nejednačinu $x+1<4$ u skupu N0.",
                      (r"$\{0,1,2\}$", r"$\{1,2\}$", r"$\{0,1,2,3\}$", r"$x>3$"))
    assert result.applicable and result.valid
    assert result.solution_display == "{0, 1, 2}"
    assert result.correct_index == 0


def test_over_z_fractional_bounds_materialize_exactly():
    """Nad Z: 1/2 < x < 5/2 je tačno {1,2} — egzaktan Fraction floor/ceil."""
    result = evaluate(
        r"Riješi nejednačinu $\frac{1}{2} < x < \frac{5}{2}$ u skupu "
        r"cijelih brojeva.",
        (r"$\{1,2\}$", r"$\{0,1,2\}$", r"$\{1\}$", r"$\{2\}$"))
    assert result.applicable and result.valid
    assert result.solution_display == "{1, 2}"
    assert result.correct_index == 0


def test_q_and_r_domains_stay_continuous_never_discretized():
    """Nad Q/R x<5 i x<=4 NISU isti skup — kontinuitet se nikad ne gubi."""
    for wording in (r"u skupu racionalnih brojeva", r"u skupu realnih brojeva"):
        question = rf"Riješi nejednačinu $x+3<8$ {wording}."
        options = (r"$x<5$", r"$x\le 4$", r"$x\le 5$", r"$x=5$")
        result = evaluate(question, options)
        assert result.applicable and result.valid, wording
        assert result.solution_display == "x < 5"
        assert result.correct_index == 0
        failure, _ = publication_failure(question, options, 0, options[0])
        assert failure == "", wording


def test_noninteger_point_over_z_is_the_empty_set():
    """Nad Z jednačina 2x=1 nema rješenja — ∅ je jedina tačna opcija."""
    result = evaluate(r"Riješi jednačinu $2x=1$ u skupu cijelih brojeva.",
                      (r"$\varnothing$", r"$\{1\}$", r"$\{0\}$", r"$x=3$"))
    assert result.applicable and result.valid
    assert result.solution_display == "∅"
    assert result.correct_index == 0


def test_duplicate_empty_representations_over_z_are_blocked():
    """Tačka 1/2 nad Z diskretizuje se u ISTI prazan skup kao ∅ — dva takva
    zapisa među opcijama su duplikat i MCQ pada zatvoreno."""
    result = evaluate(r"Riješi jednačinu $2x=1$ u skupu cijelih brojeva.",
                      (r"$\varnothing$", r"$\{1\}$", r"$\{0\}$",
                       r"$x=\frac{1}{2}$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.correct_indices == (0, 3)


# ---------------------------------------------------------------------------
# 3) RC1 — publikacijska (domen-slijepa) ekvivalencija zapisa
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pair", [
    ("$11$", r"$\{11\}$"),          # B007 (escaped vitičaste — živi promašaj)
    ("$2$", r"$\{2\}$"),            # B012
    ("$6$", r"$\{6\}$"),            # B014
    (r"$x=11$", r"$\{11\}$"),       # x=c ≡ {c}
    (r"$x=11$", "$11$"),            # x=c ≡ c
    (r"$(-2,\infty)$", r"$x>-2$"),  # interval ≡ relacija (isti skup)
    (r"$x<5$", r"$5>x$"),           # obrnut zapis iste relacije
    (r"$(-\infty,5)$", r"$x<5$"),
])
def test_equivalent_solve_set_representations_are_publication_duplicates(pair):
    assert options_are_equivalent(*pair), pair
    assert find_equivalent_option_pairs(["$99$", pair[0], "$98$", pair[1]]) \
        == [(1, 3)], pair


@pytest.mark.parametrize("pair", [
    (r"$x<5$", r"$x\le 5$"),        # striktnost granice nosi značenje
    (r"$x<5$", r"$x\le 4$"),        # jednako tek nad Z — bez domena NIKAD
    (r"$x=-1$", r"$-2<x<0$"),       # E02 par — domen-osjetljiv, ne tvrdi se
    (r"$x>3$", r"$y>3$"),           # tuđa nepoznata nije isti odgovor
    (r"$\{2\}$", r"$\{3\}$"),
    (r"$(-2,\infty)$", r"$[-2,\infty)$"),
    (r"$\{2,5\}$", "$2,5$"),        # decimalni zarez u skupu se NE pogađa
])
def test_distinct_or_unprovable_representations_are_never_claimed_equal(pair):
    assert not options_are_equivalent(*pair), pair


def test_full_practice_path_rejects_the_b007_duplicate_package():
    """Integracija: tačan istorijski B007 paket kroz run_practice_turn —
    odbijen PRIJE mutacije sesije, bez drugog poziva vrh dva dozvoljena."""
    from matbot.practice import SAFE_ERROR_MESSAGE, run_practice_turn
    from matbot.session_store import SessionStore
    from tests.conftest import FakeLLM, make_options, make_output, make_task
    from tests.test_practice import turn_payload

    options = make_options(*B007_OPTIONS)
    store, fake = SessionStore(), FakeLLM()
    fake.queue(make_output(
        reply="Evo zadatka.",
        new_task=make_task(text=B007_TASK, expected="$11$",
                           options=options, correct_option_index=1),
    ))
    result = run_practice_turn(store, fake, turn_payload())
    assert result["answer"] == SAFE_ERROR_MESSAGE
    session = store.peek("sess-1")
    assert session is None or not session.get("current_task")


# ---------------------------------------------------------------------------
# 4) GRAMATIKA OPCIJA POD DOMENOM (§7) i zatvorene granice
# ---------------------------------------------------------------------------

def test_set_builder_over_z_adjudicates_and_normalizes_bounds():
    """{x∈Z : x>3} i x≥4 su nad Z isti skup — set-builder dobija presudu."""
    question = r"Riješi nejednačinu $x-1>2$ u skupu cijelih brojeva."
    options = (r"$\{x\in\mathbb{Z} : x>3\}$", r"$\{x\in\mathbb{Z} : x\ge 3\}$",
               r"$\{x\in\mathbb{Z} : x<4\}$", r"$\{x\in\mathbb{Z} : x\le 2\}$")
    result = evaluate(question, options)
    assert result.applicable and result.valid
    assert result.correct_index == 0
    assert result.option_displays[0] == "x >= 4 [cijeli]"


def test_set_builder_pipe_separator_and_n_domain():
    result = evaluate(r"Riješi nejednačinu $x+1<4$ u skupu prirodnih brojeva.",
                      (r"$\{x\in\mathbb{N} | x<3\}$", r"$\{0,1,2\}$",
                       r"$\{2,3\}$", r"$x>3$"))
    assert result.applicable and result.valid
    assert result.correct_index == 0
    assert result.option_displays[0] == "{1, 2}"


def test_member_prefix_set_option_is_read_under_the_domain():
    """LSP0-E02 opcija `x\\in\\{-2,-1\\}`: prefiks pripadnosti + konačan skup."""
    result = evaluate(E02_TASK,
                      (r"$x\in\{-2,-1\}$", r"$x\in\{-1\}$", r"$x\in\{0\}$",
                       r"$x<-1$"))
    assert result.applicable and result.valid
    assert result.option_displays == ("{-2, -1}", "{-1}", "{0}",
                                      "x <= -2 [cijeli]")
    assert result.correct_index == 1


def test_unreadable_option_on_a_supported_domain_task_fails_closed():
    """Podržan Z zadatak + nečitljiva opcija = zatvoreno padanje, ne ćutanje
    (ista arhitektonska granica kao targeted live verifikacija)."""
    question = r"Riješi nejednačinu $x-1>2$ u skupu cijelih brojeva."
    for bad in ("nema rješenja", r"$\{x\in\mathbb{P} : x>3\}$",
                r"$\{1,\frac{1}{2}\}$", r"$x\in 5$"):
        options = (r"$x\ge 4$", bad, r"$x\le 3$", r"$x=4$")
        result = evaluate(question, options)
        assert result.applicable, bad
        assert result.reason_code == "unverifiable_solution_option", bad
        assert result.option_displays[1] == "?", bad
        failure, _ = publication_failure(question, options, 0, options[0])
        assert failure == "unverifiable_solution_option", bad


def test_finite_sets_under_continuous_domain_still_fail_closed():
    """Bez diskretnog domena višečlan skup ostaje NEPROVJERLJIV zapis
    (decimalni zarez se ne pogađa) — postojeća doktrina netaknuta."""
    result = evaluate(r"Riješi nejednačinu: $x+3<8$",
                      (r"$x<5$", r"$\{1,2\}$", r"$x>5$", r"$x=5$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "unverifiable_solution_option"


def test_duplicate_distractor_sets_are_blocked_even_when_marked_is_unique():
    """RC1 u oraklu: dva zapisa ISTOG (pogrešnog) skupa među opcijama — MCQ
    pada s postojećim publikacijskim kodom semantičkih duplikata."""
    result = evaluate(r"Riješi jednačinu $x+1=1$ u skupu cijelih brojeva.",
                      ("$0$", r"$x>3$", r"$x\ge 4$", "$5$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "semantically_duplicate_options"
    failure, _ = publication_failure(
        r"Riješi jednačinu $x+1=1$ u skupu cijelih brojeva.",
        ("$0$", r"$x>3$", r"$x\ge 4$", "$5$"), 0, "$0$")
    assert failure == "semantically_duplicate_options"


def test_huge_bounded_range_is_never_materialized():
    """Modelove granice NIKAD ne alociraju velik raspon: 0<x<10000 nad Z se
    poredi simbolički (int_range), a doslovno nabrojan mali skup mu nije
    jednak — presuda ostaje matematički ispravna bez enumeracije."""
    question = (r"Riješi nejednačinu $0 < x < 10000$ u skupu cijelih "
                r"brojeva.")
    options = (r"$\{1,2,3\}$", r"$x\ge 1$", r"$\{0,1,2\}$", r"$x>10000$")
    result = evaluate(question, options)
    assert result.applicable and not result.valid
    # nijedna opcija nije cio skup {1,…,9999} → matematički kod, ne pad servera
    assert result.reason_code == "no_correct_option"
    assert result.solution_display == "{1, …, 9999}"


# ---------------------------------------------------------------------------
# 5) PREFLIGHT — nalaz s domenom stiže recenzentu prije drugog poziva
# ---------------------------------------------------------------------------

def _preflight_task(text, options, marked_index):
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    return TaskPayload(
        selected_lesson_id="7-02-019",
        selected_lesson_title="Nejednačine sa sabiranjem i oduzimanjem u Z",
        target_difficulty_level=1, text=text, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=marked_index, correct_option_id="abcd"[marked_index],
        expected_answer=options[marked_index],
        solution="Serverski test.", difficulty="easy",
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        task_signature=TaskSignature(
            task_family="linear_inequality", operation_or_relation="solve",
            normalized_parameters=[SignatureParameter(name="case", value="t1")],
            required_conditions=[], relevant_objects=["nejednakost"],
            answer_type="multiple_choice"))


def test_preflight_carries_the_e02_domain_finding_to_the_reviewer():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _preflight_task(E02_TASK, E02_OPTIONS, 1))
    found = [issue for issue in issues
             if issue.code == "multiple_correct_options"]
    assert found, [issue.code for issue in issues]
    assert "server solved: {-1}" in found[0].detail


def test_preflight_carries_the_a012_finding_to_the_reviewer():
    from matbot.tutor import package_preflight as preflight
    issues = preflight.collect_package_issues(
        _preflight_task(A012_TASK, A012_OPTIONS, 2))
    found = [issue for issue in issues
             if issue.code == "multiple_correct_options"]
    assert found, [issue.code for issue in issues]
    assert "server solved: x > -2" in found[0].detail
