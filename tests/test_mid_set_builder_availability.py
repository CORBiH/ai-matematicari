r"""SET-BUILDER SA `\mid` — dokazano uobičajen zapis koji je padao zatvoreno.

ŽIVI FINAL-40 NALAZ (5 od 13 sigurnih odbijanja nose isti kod; dva paketa su
pala i u nacrtu i u recenzentovom FINALU):

    nacrt   `unsafe_option_notation: … (unknown_mathjax_command:mid)`
    final   `unsafe_option_notation: … (unknown_mathjax_command:mid)`
    nacrt   `unsafe_option_notation: … (raw_latex_command_outside_math;
             unknown_mathjax_command_outside_math:mid)`
    nacrt   `unsafe_solution_notation: (solution unknown_mathjax_command:mid)`
    nacrt   `unsafe_expected_answer_notation: (unknown_mathjax_command:mid)`

Dvije nezavisne rupe s istim korijenom:

  1. `\mid` (obična TeX relacijska crta, bez argumenata, koju MathJax v3
     `tex-mml-chtml` podržava u bazi) nije bila na bijeloj listi komandi, pa je
     SVAKA opcija s njom obarala cio paket;
  2. `_solve_set_builder` je separator čitao ISKLJUČIVO kao goli znak `:`/`|`,
     pa je `\{x\in\mathbb{Q}\mid x>-2\}` — zapis koji je taj čitač već trebao
     podržavati — ostajao `unverifiable_solution_option`.

GRANICA SE NE POMJERA: `\mid` IZVAN $...$ i dalje pada zatvoreno (u svim živim
slučajevima je značio da je cio set-builder iscurio iz matematike), a nijedan
drugi zapis separatora se ne pogađa.

ZERO poziva modela.
"""
import pytest

from matbot import mcq_integrity
from matbot.mathsafe import (MATHJAX_COMMAND_ALLOWLIST, find_unsafe_math_issues,
                             sanitize_and_validate_math_text)

# ---------------------------------------------------------------------------
# 1) BIJELA LISTA — unutar matematike da, izvan matematike ne
# ---------------------------------------------------------------------------

def test_mid_is_an_allowlisted_mathjax_command():
    assert "mid" in MATHJAX_COMMAND_ALLOWLIST


@pytest.mark.parametrize("text", [
    r"$\{x\in\mathbb{Z}\mid x>3\}$",
    r"Skup rješenja je $\{x\in\mathbb{Q}\mid \frac{1}{3}<x\le\frac{5}{6}\}$.",
    r"$\{n\in\mathbb{N}\mid n\le 4\}$",
])
def test_mid_inside_math_is_safe(text):
    _cleaned, safe = sanitize_and_validate_math_text(text)
    assert safe, text


def test_raw_mid_outside_math_is_still_rejected():
    """Živi oblik: cio set-builder bez ijednog $ delimitera. Usko umotavanje
    samog simbola ostavilo bi ostatak izraza kao goli tekst — pada zatvoreno."""
    issues = find_unsafe_math_issues(r"\{x\in\mathbb{Z}\mid x>3\}")
    assert "raw_latex_command_outside_math" in issues
    _cleaned, safe = sanitize_and_validate_math_text(
        r"\{x\in\mathbb{Z}\mid x>3\}", allow_whole_expression_wrap=True)
    assert not safe


def test_mid_alone_outside_math_is_rejected_not_wrapped():
    from matbot.mathsafe import _STANDALONE_SYMBOL_COMMANDS

    assert "mid" not in _STANDALONE_SYMBOL_COMMANDS
    _cleaned, safe = sanitize_and_validate_math_text(r"Skup je x \mid x>3.")
    assert not safe


@pytest.mark.parametrize("unknown", [
    r"$\{x\in\mathbb{Z}\vert x>3\}$", r"$a\Vert b$", r"$2\tdot3$", r"$\midx$",
])
def test_unrelated_unknown_commands_remain_rejected(unknown):
    _cleaned, safe = sanitize_and_validate_math_text(unknown)
    assert not safe, unknown


# ---------------------------------------------------------------------------
# 2) ORAKL RJEŠAVANJA — novi separator, isti kanonski skupovi
# ---------------------------------------------------------------------------

Q_INTERVAL_TASK = (
    r"Riješi nejednačinu $\frac{1}{3}<x\le\frac{5}{6}$ isključivo u skupu "
    r"racionalnih brojeva. Koji je cijeli skup rješenja?")
Q_RAY_TASK = (r"Riješi nejednačinu $-3x<6$ isključivo u skupu racionalnih "
              r"brojeva. Koji je cijeli skup rješenja?")
Z_RAY_TASK = (r"Riješi nejednačinu $\frac{x}{2}>\frac{3}{2}$ isključivo u "
              r"skupu cijelih brojeva. Koji je cijeli skup rješenja?")


def test_q_interval_set_builder_options_are_now_verifiable():
    options = (
        r"\{x\in\mathbb{Q}\mid \frac{1}{3}<x\le\frac{5}{6}\}",
        r"\{x\in\mathbb{Q}\mid \frac{1}{3}\le x<\frac{5}{6}\}",
        r"\{x\in\mathbb{Q}\mid x>\frac{1}{3}\}",
        r"x=\frac{5}{6}",
    )
    result = mcq_integrity.evaluate_linear_solve_mcq(Q_INTERVAL_TASK, options)
    assert result.applicable and result.valid
    assert result.solution_display == "1/3 < x <= 5/6"
    assert result.correct_indices == (0,)


def test_q_ray_set_builder_options_are_now_verifiable():
    options = (
        r"\{x\in\mathbb{Q}\mid x>-2\}", r"\{x\in\mathbb{Q}\mid x<-2\}",
        r"\{x\in\mathbb{Q}\mid x\ge -2\}", r"x=-2",
    )
    result = mcq_integrity.evaluate_linear_solve_mcq(Q_RAY_TASK, options)
    assert result.applicable and result.valid
    assert result.solution_display == "x > -2"
    assert result.correct_indices == (0,)


def test_integer_domain_set_builder_is_discretized_like_every_other_form():
    """Nad Z su `x>3` i `x\\ge 4` ISTI skup — set-builder ne dobija posebnu
    semantiku, samo se čita."""
    options = (
        r"\{x\in\mathbb{Z}\mid x>3\}", r"\{x\in\mathbb{Z}\mid x\ge 3\}",
        r"\{x\in\mathbb{Z}\mid x<3\}", r"x=4",
    )
    result = mcq_integrity.evaluate_linear_solve_mcq(Z_RAY_TASK, options)
    assert result.applicable and result.valid
    assert result.correct_indices == (0,)
    assert result.option_displays[0] == "x >= 4 [cijeli]"


def test_equivalent_mid_and_colon_writings_are_one_answer():
    """Dva zapisa ISTOG skupa moraju ostati DOKAZIVO ista opcija — inače bi
    novi separator otvorio MCQ s dvije tačne opcije."""
    options = (
        r"\{x\in\mathbb{Q}\mid x>-2\}", r"\{x\in\mathbb{Q} : x>-2\}",
        r"x<-2", r"x=-2",
    )
    result = mcq_integrity.evaluate_linear_solve_mcq(Q_RAY_TASK, options)
    assert result.applicable and not result.valid
    assert result.reason_code == "multiple_correct_options"


def test_latex_spacing_inside_the_set_builder_is_not_meaning():
    options = (
        r"\{\,x\in\mathbb{Q}\ \mid\ x>-2\,\}", r"\{x\in\mathbb{Q}\mid x<-2\}",
        r"\{x\in\mathbb{Q}\mid x\ge -2\}", r"x=-2",
    )
    result = mcq_integrity.evaluate_linear_solve_mcq(Q_RAY_TASK, options)
    assert result.applicable and result.valid
    assert result.correct_indices == (0,)


@pytest.mark.parametrize("exotic", [
    # drugi zapis crte — NE pogađa se
    r"\{x\in\mathbb{Q}\vert x>-2\}",
    # bez deklaracije domena lijevo od separatora
    r"\{x\mid x>-2\}",
    # deklarisana DRUGA nepoznata
    r"\{y\in\mathbb{Q}\mid y>-2\}",
    # nelinearna relacija desno
    r"\{x\in\mathbb{Q}\mid x^2>4\}",
])
def test_exotic_or_ambiguous_set_writings_still_fail_closed(exotic):
    options = (exotic, r"x<-2", r"x=-2", r"x\ge 5")
    result = mcq_integrity.evaluate_linear_solve_mcq(Q_RAY_TASK, options)
    assert result.applicable and not result.valid
    assert result.reason_code == mcq_integrity.UNVERIFIABLE_SOLUTION_OPTION_CODE


def test_option_equivalence_stays_domain_blind_for_set_builders():
    """Set-builder nosi VLASTITI domen, pa domen-slijepa ekvivalencija opcija
    (matbot/option_equivalence) o njemu i dalje NIŠTA ne tvrdi."""
    assert mcq_integrity.continuous_answer_set(
        r"\{x\in\mathbb{Q}\mid x>-2\}") is None
