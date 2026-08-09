"""PP-1 LIVE-150, nalaz F008 — uski orakl RJEŠAVANJA linearne (ne)jednačine.

ZAŠTO POSTOJI: u živom talasu od 150 scenarija objavljen je MCQ
    „Riješi nejednačinu: $-3< x+1 < -1$“
s opcijama $x<-3$ / $x>-3$ / $x=-3$ / $x=-2$ i označenom opcijom $x=-3$.
Skup rješenja je $-4<x<-2$; $x=-3$ je samo JEDAN ČLAN skupa, ne rješenje.
Tutor je paket sastavio, recenzent ga ODOBRIO, a nijedan deterministički sloj
nije ni pokušao matematiku: orakli u mcq_integrity pokrivali su djeljivost,
direktan račun i poređenje — „riješi (ne)jednačinu“ nije imao nijedan.

GRANICE (namjerno uske, isti princip kao ostali orakli):
  • proza mora izričito tražiti rješavanje; superlativ, „koliko“, negacija i
    ograničenje domena („u skupu“, prirodni/cijeli brojevi) isključuju orakl;
  • svaki matematički segment mora biti pročitan zatvorenom gramatikom, a
    TAČNO JEDAN smije nositi relaciju — nepročitano znači ćutanje, ne pogađanje;
  • sva aritmetika je egzaktna (`Fraction`); dvije relacije su isti odgovor
    SAMO kad opisuju ISTI skup rješenja (tačka ≠ zrak ≠ interval).

ZERO model poziva: sve je čist deterministički kod.
"""
from matbot import mcq_integrity
from matbot.mcq_integrity import evaluate_linear_solve_mcq, publication_failure

# Tačan živi zadatak i opcije iz PP1-F008 (scenario je sačuvan u artefaktu
# scratchpad/practice_eval/pp1_post_release_live_150/).
F008_TASK = r"Riješi nejednačinu: $-3< x+1 < -1$"
F008_OPTIONS = (r"$x<-3$", r"$x>-3$", r"$x=-3$", r"$x=-2$")
F008_MARKED = 2   # opcija c, $x=-3$


def evaluate(question, options):
    return evaluate_linear_solve_mcq(question, options)


# ---------------------------------------------------------------------------
# 1) PP1-F008 — tačna živa regresija
# ---------------------------------------------------------------------------

def test_pp1_f008_exact_live_regression_is_rejected():
    result = evaluate(F008_TASK, F008_OPTIONS)
    assert result.applicable
    assert not result.valid
    assert result.reason_code == "no_correct_option"
    # Server nezavisno izvodi CIO skup rješenja, egzaktno.
    assert result.solution_display == "-4 < x < -2"
    assert result.correct_indices == ()


def test_pp1_f008_publication_failure_blocks_the_package():
    failure, result = publication_failure(
        F008_TASK, F008_OPTIONS, F008_MARKED, F008_OPTIONS[F008_MARKED])
    assert failure == "no_correct_option"
    assert result.solution_display == "-4 < x < -2"


def test_pp1_f008_satisfying_member_is_not_the_solution_set():
    """$x=-3$ zadovoljava nejednačinu, ali tačka nije interval — upravo
    razlika koju je živi defekt objavio kao „tačan odgovor“."""
    result = evaluate(F008_TASK, F008_OPTIONS)
    # x=-3 je pročitan kao tačka (vidljivo u dijagnostici), a rješenje je interval.
    assert "x = -3" in result.option_displays
    assert result.solution_display == "-4 < x < -2"
    assert result.reason_code == "no_correct_option"


def test_pp1_f008_positive_control_full_interval_option_passes():
    options = (r"$x<-3$", r"$x>-3$", r"$-4<x<-2$", r"$x=-2$")
    result = evaluate(F008_TASK, options)
    assert result.applicable and result.valid
    assert result.correct_index == 2
    failure, _ = publication_failure(F008_TASK, options, 2, options[2])
    assert failure == ""


# ---------------------------------------------------------------------------
# 2) Jednostavne nejednačine i jednačine
# ---------------------------------------------------------------------------

def test_simple_upper_ray():
    result = evaluate(r"Riješi nejednačinu: $x+3<8$",
                      (r"$x<5$", r"$x>5$", r"$x<11$", r"$x=5$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x < 5"
    assert result.correct_index == 0


def test_closed_ray_and_strictness_is_not_ignored():
    """$x\\ge 6$ i $x>6$ NISU isti skup — striktnost granice se poredi."""
    result = evaluate(r"Riješi nejednačinu: $x-4\ge 2$",
                      (r"$x\ge 6$", r"$x>6$", r"$x\le 6$", r"$x=6$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x >= 6"
    assert result.correct_indices == (0,)


def test_negative_coefficient_reverses_the_inequality_direction():
    result = evaluate(r"Riješi nejednačinu: $-3x>9$",
                      (r"$x<-3$", r"$x>-3$", r"$x<3$", r"$x>3$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x < -3"
    assert result.correct_index == 0


def test_simple_equation_with_bare_value_options():
    result = evaluate(r"Riješi jednačinu: $x+4=9$", ("$5$", "$3$", "$13$", "$4$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x = 5"
    assert result.correct_index == 0


def test_decimal_coefficient_equation_is_exact():
    result = evaluate(r"Riješi jednačinu: $2,5\cdot x=7,5$",
                      ("$3$", "$2$", "$5$", "$30$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x = 3"
    assert result.correct_index == 0


def test_fraction_constants_are_exact_rational_arithmetic():
    """Istorijska porodica x - 1/2 > -3/14: egzaktno 2/7, nikad float."""
    result = evaluate(
        r"Riješi nejednačinu: $x-\frac{1}{2}>-\frac{3}{14}$",
        (r"$x>\frac{2}{7}$", r"$x<\frac{2}{7}$", r"$x>\frac{4}{7}$",
         r"$x=\frac{2}{7}$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x > 2/7"
    assert result.correct_index == 0


def test_fraction_coefficient_on_the_unknown():
    result = evaluate(
        r"Riješi nejednačinu: $\frac{x}{2}>\frac{1}{3}$",
        (r"$x>\frac{2}{3}$", r"$x<\frac{2}{3}$", r"$x>\frac{1}{6}$",
         r"$x=\frac{2}{3}$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x > 2/3"
    assert result.correct_index == 0


# ---------------------------------------------------------------------------
# 3) Lančane nejednačine
# ---------------------------------------------------------------------------

def test_reversed_greater_than_chain():
    result = evaluate(r"Riješi nejednačinu: $5> x+1>3$",
                      (r"$2<x<4$", r"$3<x<5$", r"$x>2$", r"$x<4$"))
    assert result.applicable and result.valid
    assert result.solution_display == "2 < x < 4"
    assert result.correct_index == 0


def test_mixed_strictness_chain_keeps_the_boundary_kinds():
    result = evaluate(r"Riješi nejednačinu: $-3\le x+1<-1$",
                      (r"$-4\le x<-2$", r"$-4<x<-2$", r"$x\ge -4$", r"$x<-2$"))
    assert result.applicable and result.valid
    assert result.solution_display == "-4 <= x < -2"
    assert result.correct_indices == (0,)


# ---------------------------------------------------------------------------
# 4) Kodovi kvara kroz publication_failure (postojeći vokabular, bez novih)
# ---------------------------------------------------------------------------

def test_marked_wrong_option_yields_marked_option_math_mismatch():
    question = r"Riješi jednačinu: $x+4=9$"
    options = ("$5$", "$3$", "$13$", "$4$")
    failure, result = publication_failure(question, options, 1, options[1])
    assert failure == "marked_option_math_mismatch"
    assert result.correct_index == 0


def test_two_equivalent_correct_options_are_rejected():
    """$x<5$ i $5>x$ opisuju ISTI skup — dvije tačne opcije nisu MCQ."""
    result = evaluate(r"Riješi nejednačinu: $x+3<8$",
                      (r"$x<5$", r"$5>x$", r"$x>5$", r"$x=5$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"
    assert result.correct_indices == (0, 1)


# ---------------------------------------------------------------------------
# 5) KONZERVATIVNOST — nepodržano ćuti (applicable=False), nikad ne pogađa
# ---------------------------------------------------------------------------

def _silent(question, options):
    result = evaluate(question, options)
    assert not result.applicable, (question, result)
    # a ni dispatch ne smije prijaviti kvar
    failure, _ = publication_failure(question, options, 0, options[0])
    assert failure == ""


def test_nonlinear_task_stays_silent():
    _silent(r"Riješi nejednačinu: $x^2>4$",
            (r"$x>2$", r"$x<2$", r"$x=2$", r"$x>4$"))


def test_absolute_value_stays_silent():
    _silent(r"Riješi nejednačinu: $|x|<3$",
            (r"$x<3$", r"$x>3$", r"$x=3$", r"$x<9$"))


def test_division_by_unknown_stays_silent():
    _silent(r"Riješi nejednačinu: $1/x<2$",
            (r"$x>\frac{1}{2}$", r"$x<\frac{1}{2}$", r"$x=2$", r"$x>2$"))


def test_bare_value_options_on_an_inequality_stay_silent():
    """„Koja vrijednost zadovoljava…“ oblik: gola vrijednost ne opisuje skup,
    pa orakl NE presuđuje — ne smije lažno oboriti valjan zadatak članstva."""
    _silent(r"Riješi nejednačinu: $x+3<8$", ("4", "5", "2", "1"))


def test_prose_option_disables_the_oracle():
    _silent(r"Riješi nejednačinu: $x+3<8$",
            (r"$x<5$", "nema rješenja", r"$x>5$", r"$x=5$"))


def test_without_solve_directive_stays_silent():
    _silent(r"Za koje $x$ vrijedi $x+3<8$?",
            (r"$x<5$", r"$x>5$", r"$x<11$", r"$x=5$"))


def test_negated_question_stays_silent():
    _silent(r"Koja opcija nije rješenje nejednačine $x+3<8$?",
            (r"$x=9$", r"$x=1$", r"$x=2$", r"$x=3$"))


def test_superlative_question_stays_silent():
    _silent(r"Koje je najveće rješenje nejednačine $x+3<8$?",
            ("$4$", "$3$", "$2$", "$1$"))


def test_domain_restricted_question_stays_silent():
    """Nad N/Z „$x<5$“ i „$x\\le 4$“ postaju isti skup — orakl nad Q to ne
    smije presuđivati, pa domen isključuje cio orakl."""
    _silent(r"Riješi nejednačinu $x+3<8$ u skupu prirodnih brojeva.",
            (r"$x<5$", r"$x>5$", r"$x=4$", r"$x<4$"))


def test_two_relation_segments_stay_silent():
    _silent(r"Riješi: $x+1=3$ i $x-1=0$",
            (r"$x=2$", r"$x=1$", r"$x=0$", r"$x=3$"))


def test_unreadable_extra_segment_stays_silent():
    """$x\\in\\mathbb{Z}$ nosi uslov koji mijenja rješenje — nepročitan
    segment znači ćutanje cijelog orakla."""
    _silent(r"Riješi nejednačinu: $x+3<8$, $x\in\mathbb{Z}$",
            (r"$x<5$", r"$x>5$", r"$x=5$", r"$x<3$"))


def test_identity_equation_stays_silent():
    _silent(r"Riješi jednačinu: $x+1=x+3$",
            (r"$x=1$", r"$x=2$", r"$x=0$", r"$x=3$"))


def test_parenthesised_equation_stays_silent():
    _silent(r"Riješi jednačinu: $2(x+3)=10$",
            (r"$x=2$", r"$x=1$", r"$x=4$", r"$x=8$"))


def test_option_in_a_different_variable_stays_silent():
    _silent(r"Riješi nejednačinu: $x+3<8$",
            (r"$y<5$", r"$x>5$", r"$x=5$", r"$x<11$"))


def test_ambiguous_slash_coefficient_stays_silent():
    """„3/4x“ bez \\frac je dvosmisleno ((3/4)x ili 3/(4x)) — ne tumači se."""
    _silent(r"Riješi jednačinu: $3/4x=6$",
            (r"$x=8$", r"$x=2$", r"$x=4$", r"$x=6$"))


def test_mixed_number_option_silences_the_oracle():
    """Mješovit broj ($9\\frac{4}{5}$ = 9 + 4/5) nije podržan: prosta zamjena
    razlomka nadovezala bi cifre (94/5 ≠ 49/5) i orakl bi POGREŠNO oborio
    ispravan paket — uhvaćeno na determinističkim bulk testovima (6-07 porodica
    jednačina). Cifra uz razlomak znači ćutanje, nikad pogađanje."""
    _silent(r"Riješi jednačinu: $7x = \frac{693}{10}$",
            (r"$9\frac{9}{10}$", r"$-\frac{99}{10}$", "$10$", r"$9\frac{4}{5}$"))


def test_mixed_number_in_the_task_silences_the_oracle():
    _silent(r"Riješi jednačinu: $x + 1 = 2\frac{1}{2}$",
            (r"$x=\frac{3}{2}$", r"$x=\frac{5}{2}$", "$x=1$", "$x=2$"))


# ---------------------------------------------------------------------------
# 6) DISPATCH — novi orakl ne otima postojećim oraklima
# ---------------------------------------------------------------------------

def test_solve_oracle_does_not_claim_computation_tasks():
    result = evaluate(r"Izračunaj: $\frac{2}{7}+\frac{3}{7}$",
                      (r"$\frac{5}{7}$", r"$\frac{6}{7}$", r"$\frac{2}{7}$",
                       r"$\frac{1}{7}$"))
    assert not result.applicable


def test_divisibility_dispatch_is_untouched():
    question = "Koji od sljedećih brojeva je djeljiv sa 25?"
    options = ("75", "30", "40", "60")
    assert not evaluate(question, options).applicable
    failure, result = publication_failure(question, options, 0, "75")
    assert failure == ""             # djeljivost i dalje presuđuje svoj oblik
    assert result.applicable         # (rezultat je divisibility orakla)


def test_comparison_dispatch_is_untouched():
    question = r"Uporedi brojeve: koji znak stoji između $\frac{2}{3}$ i $\frac{3}{4}$?"
    options = ("$<$", "$>$", "$=$")
    failure, result = publication_failure(question, options, 0, "$<$")
    assert failure == ""
    assert not evaluate(question, options).applicable   # nema direktive rješavanja


# ---------------------------------------------------------------------------
# 7) PREFLIGHT — nalaz stiže recenzentu sa SERVERSKI izvedenim skupom
# ---------------------------------------------------------------------------

def test_preflight_issue_carries_the_server_derived_solution_set():
    """Isti razlog kao F4E nalaz: goli kod bez serverske derivacije recenzent
    ne zna popraviti. Detalj mora nositi izveden skup rješenja."""
    from matbot.tutor import package_preflight as preflight
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    task = TaskPayload(
        selected_lesson_id="7-02-019",
        selected_lesson_title="Nejednačine sa sabiranjem i oduzimanjem u Z",
        target_difficulty_level=1, text=F008_TASK, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(F008_OPTIONS)],
        correct_option_index=F008_MARKED, correct_option_id="c",
        expected_answer=F008_OPTIONS[F008_MARKED],
        solution=r"Oduzmi $1$ od svih strana pa je $-4<x<-2$.",
        difficulty="easy",
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        task_signature=TaskSignature(
            task_family="linear_inequality", operation_or_relation="solve",
            normalized_parameters=[SignatureParameter(name="case", value="f008")],
            required_conditions=[], relevant_objects=["nejednakost"],
            answer_type="multiple_choice"))
    issues = preflight.collect_package_issues(task)
    found = [issue for issue in issues if issue.code == "no_correct_option"]
    assert found, [issue.code for issue in issues]
    assert "server solved: -4 < x < -2" in found[0].detail
