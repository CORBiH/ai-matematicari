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
import pytest

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


# ---------------------------------------------------------------------------
# 1b) PP1-F008, DRUGA ŽIVA POJAVA — gole brojčane opcije na nejednačini
# ---------------------------------------------------------------------------
# Završni LIVE-150 (poslije prve F008 ispravke) objavio je ISTU klasu greške u
# drugom zapisu: opcije više nisu bile relacije nego GOLI BROJEVI, a orakl je
# tada ćutao (`applicable=False`) jer je golu vrijednost primao samo kad je
# izvedeno rješenje tačka. Nijedan deterministički nalaz nije postojao,
# recenzent je odobrio, i nevažeći MCQ je objavljen.
F008B_TASK = r"Riješi nejednačinu: $-1 < x+1 < 1$"
F008B_OPTIONS = ("1", "-2", "-1", "0")
F008B_MARKED = 2   # opcija c, „-1“


def test_pp1_f008_live_bare_option_regression_is_rejected():
    result = evaluate(F008B_TASK, F008B_OPTIONS)
    assert result.applicable, "gole opcije više ne smiju ugasiti orakl"
    assert not result.valid
    assert result.reason_code == "no_correct_option"
    assert result.solution_display == "-2 < x < 0"
    assert result.correct_indices == ()


def test_pp1_f008_live_bare_options_parse_as_point_candidates():
    """Svaka gola vrijednost je KANDIDAT-TAČKA; tačka nikad nije interval."""
    result = evaluate(F008B_TASK, F008B_OPTIONS)
    assert result.option_displays == ("x = 1", "x = -2", "x = -1", "x = 0")
    assert result.solution_display == "-2 < x < 0"
    # označeni „-1“ JESTE član skupa, ali član nije rješenje
    assert "x = -1" in result.option_displays


def test_pp1_f008_live_bare_option_package_cannot_publish():
    failure, result = publication_failure(
        F008B_TASK, F008B_OPTIONS, F008B_MARKED, F008B_OPTIONS[F008B_MARKED])
    assert failure == "no_correct_option"
    assert result.solution_display == "-2 < x < 0"


def test_pp1_f008_live_positive_control_interval_option_passes():
    options = ("$1$", r"$-2<x<0$", "$-1$", "$0$")
    result = evaluate(F008B_TASK, options)
    assert result.applicable and result.valid
    assert result.correct_index == 1
    failure, _ = publication_failure(F008B_TASK, options, 1, options[1])
    assert failure == ""


def test_pp1_f008_positive_control_full_interval_option_passes():
    options = (r"$x<-3$", r"$x>-3$", r"$-4<x<-2$", r"$x=-2$")
    result = evaluate(F008_TASK, options)
    assert result.applicable and result.valid
    assert result.correct_index == 2
    failure, _ = publication_failure(F008_TASK, options, 2, options[2])
    assert failure == ""


# ---------------------------------------------------------------------------
# 1c) TARGETED LIVE VERIFIKACIJA — jednočlani skupovi kao opcije
# ---------------------------------------------------------------------------
# 24-scenarijska real-model verifikacija objavila je DVA pogrešna paketa iste
# klase: „riješi nejednačinu“ s opcijama u zapisu jednočlanog skupa ({-5} za
# skup -6<x<-4; {-1} za skup -2<x<0). Parser nije poznavao vitičaste zagrade,
# JEDNA nepročitljiva opcija je gasila CIO orakl i deterministička zaštita je
# nestala baš na paketu koji ju je najviše trebao.

# Regresija A: rješenje je interval -6 < x < -4, označen singleton {-5}.
A01_TASK = r"Riješi nejednačinu: $-5 < x+1 < -3$"
A01_OPTIONS = (r"$\{-5\}$", r"$\{-6\}$", r"$\{-4\}$", r"$\{0\}$")

# Regresija B: tačan živi F008 zadatak sa singleton opcijama, označeno {-1}.
F008C_OPTIONS = ("{1}", "{-2}", "{-1}", "{0}")
F008C_MARKED = 2


def test_live_singleton_member_of_interval_is_rejected():
    result = evaluate(A01_TASK, A01_OPTIONS)
    assert result.applicable and not result.valid
    assert result.solution_display == "-6 < x < -4"
    # {-5} je pročitan kao tačka — član intervala, ne interval
    assert result.option_displays == ("x = -5", "x = -6", "x = -4", "x = 0")
    assert result.reason_code == "no_correct_option"
    failure, _ = publication_failure(A01_TASK, A01_OPTIONS, 0, A01_OPTIONS[0])
    assert failure == "no_correct_option"


def test_live_f008_singleton_options_are_rejected():
    result = evaluate(F008B_TASK, F008C_OPTIONS)
    assert result.applicable and not result.valid
    assert result.solution_display == "-2 < x < 0"
    assert result.option_displays == ("x = 1", "x = -2", "x = -1", "x = 0")
    assert result.reason_code == "no_correct_option"
    failure, _ = publication_failure(
        F008B_TASK, F008C_OPTIONS, F008C_MARKED, F008C_OPTIONS[F008C_MARKED])
    assert failure == "no_correct_option"


def test_singleton_set_is_understood_so_no_correct_option_is_the_right_code():
    """`no_correct_option` smije stajati SAMO kad su sve opcije shvaćene —
    singleton jeste shvaćen (tačka), pa je kod matematički, ne sintaksni."""
    result = evaluate(F008B_TASK, F008C_OPTIONS)
    assert "?" not in result.option_displays
    assert result.reason_code == "no_correct_option"


def test_escaped_and_spaced_singleton_forms_parse_identically():
    for option_c in (r"$\{-1\}$", r"$\{ -1 \}$", "{-1}", "{ -1 }"):
        options = ("{1}", "{-2}", option_c, "{0}")
        result = evaluate(F008B_TASK, options)
        assert result.applicable, option_c
        assert result.option_displays[2] == "x = -1", option_c


def test_singleton_positive_control_interval_option_still_wins():
    options = ("{1}", r"$-2<x<0$", "{-1}", "{0}")
    result = evaluate(F008B_TASK, options)
    assert result.applicable and result.valid
    assert result.correct_index == 1
    failure, _ = publication_failure(F008B_TASK, options, 1, options[1])
    assert failure == ""


def test_equation_singleton_set_is_the_complete_solution():
    """Kod jednačine je skup rješenja TAČNO {c}, pa je `{5}` puni odgovor."""
    result = evaluate(r"Riješi jednačinu: $x+4=9$",
                      (r"$\{5\}$", r"$\{3\}$", r"$\{13\}$", r"$\{4\}$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x = 5"
    assert result.correct_index == 0


def test_fraction_and_negative_fraction_singletons_are_exact():
    result = evaluate(r"Riješi jednačinu: $x-\frac{1}{2}=\frac{3}{14}$",
                      (r"$\{\frac{5}{7}\}$", r"$\{\frac{2}{7}\}$",
                       r"$\{\frac{4}{7}\}$", r"$\{1\}$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x = 5/7"
    assert result.correct_index == 0
    result = evaluate(r"Riješi jednačinu: $x+\frac{1}{2}=\frac{1}{7}$",
                      (r"$\{-\frac{5}{14}\}$", r"$\{\frac{5}{14}\}$",
                       r"$\{-\frac{9}{14}\}$", r"$\{0\}$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x = -5/14"
    assert result.correct_index == 0


def test_multi_element_set_is_never_parsed_as_a_singleton():
    """`{1,2}` NIJE tačka — prosto skidanje zagrada bi pogodilo pogrešnu
    vrijednost. Višečlan skup je neprovjerljiv zapis i pada zatvoreno."""
    question = r"Riješi nejednačinu: $x+3<8$"
    options = (r"$x<5$", r"$\{1,2\}$", r"$x>5$", r"$x=5$")
    result = evaluate(question, options)
    assert result.applicable and not result.valid
    assert result.reason_code == "unverifiable_solution_option"
    assert result.option_displays[1] == "?"
    # isto za tri člana i tačku-zarez
    for bad in (r"$\{-1,0,1\}$", r"$\{1;2\}$"):
        result = evaluate(question, (r"$x<5$", bad, r"$x>5$", r"$x=5$"))
        assert result.reason_code == "unverifiable_solution_option", bad


def test_set_builder_notation_fails_closed_not_silent():
    result = evaluate(r"Riješi nejednačinu: $x+3<8$",
                      (r"$\{x<5\}$", r"$x>5$", r"$x=5$", r"$x<11$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "unverifiable_solution_option"


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


def test_membership_question_with_bare_options_stays_silent():
    """PITANJE O ČLANSTVU nije pitanje o skupu rješenja: „Koja vrijednost
    zadovoljava…“ ima potpuno legitimne gole brojčane opcije, pa ga orakl NE
    smije presuđivati kao da traži cio skup."""
    _silent(r"Koja vrijednost zadovoljava nejednačinu $x+1<3$?", ("0", "2", "3", "4"))


@pytest.mark.parametrize("question", [
    r"Koji broj je rješenje nejednačine $x+1<3$?",
    r"Koja vrijednost je rješenje nejednačine $x+1<3$?",
    r"Koji od brojeva zadovoljava nejednačinu $x+1<3$?",
])
def test_membership_wording_blocks_bare_values_even_with_a_solve_word(question):
    """Riječ „rješenje“ sama po sebi ne pretvara članstvo u traženje skupa —
    zato blokada gleda formulaciju pitanja, ne samo prisustvo direktive."""
    _silent(question, ("0", "2", "3", "4"))


def test_membership_question_with_singleton_options_stays_silent():
    """Jednočlan skup se ponaša kao gola vrijednost i na granici ČLANSTVA:
    kandidat-vrijednosti na pitanju o članstvu nisu posao ovog orakla, i takvo
    pitanje se NE smije novo-oboriti zbog singleton zapisa."""
    _silent(r"Koji broj je rješenje nejednačine $x+1<3$?",
            ("{0}", "{2}", "{3}", "{4}"))


def test_domain_restricted_task_with_singleton_options_stays_silent():
    """Živa verifikacija je imala VALJAN slučaj: nejednačina sužena na cijele
    brojeve s objavljenim {-3}. Domenski sužen zadatak ostaje van orakla —
    singleton podrška ga ne smije lažno oboriti."""
    _silent(r"Riješi nejednačinu $-3<x+1<-1$ u skupu cijelih brojeva.",
            (r"$\{-3\}$", r"$\{-2\}$", r"$\{-4\}$", r"$\{0\}$"))


def test_generator_style_mixed_number_options_are_now_adjudicated():
    """ZATVARANJE POSLJEDNJE RUPE: mješovit broj se čita EGZAKTNO (49§5 = 49/5),
    pa generatorski paket s mješovitim opcijama više NE gasi orakl — presuđuje
    se, i ISPRAVAN paket uredno prolazi (bulk testovi to dokazuju na svih 352
    lekcije). `$9\\frac{4}{5}$` je 49/5, nikad slijepljeno 94/5."""
    result = evaluate(r"Riješi jednačinu: $7x = \frac{693}{10}$",
                      (r"$9\frac{9}{10}$", r"$-\frac{99}{10}$", "$10$",
                       r"$9\frac{4}{5}$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x = 99/10"
    assert result.option_displays == ("x = 99/10", "x = -99/10", "x = 10", "x = 49/5")
    assert result.correct_index == 0


def test_membership_wording_does_not_disable_relation_options():
    """Blokada je USKA: gasi SAMO tumačenje golih vrijednosti. MCQ s
    opcijama-relacijama se i dalje presuđuje, pa se nijedan već pokriven
    oblik ne gubi (npr. živi F009 sufiks „Koja od ponuđenih…“)."""
    result = evaluate(
        r"Riješi nejednačinu: $-3x>9$. Koja od ponuđenih je tačna rješenja?",
        (r"$x<-3$", r"$x>-3$", r"$x<3$", r"$x>3$"))
    assert result.applicable and result.valid
    assert result.correct_index == 0


def test_prose_option_on_a_known_task_fails_closed():
    """ARHITEKTONSKA ISPRAVKA (targeted live verifikacija): zadatak je DOKAZANO
    u dometu i skup rješenja je izveden — nečitljiva opcija više ne gasi orakl
    (to je ćutanje dva puta objavilo pogrešnu matematiku), nego pada zatvoreno
    dok recenzent ne vrati provjerljiv zapis."""
    question = r"Riješi nejednačinu: $x+3<8$"
    options = (r"$x<5$", "nema rješenja", r"$x>5$", r"$x=5$")
    result = evaluate(question, options)
    assert result.applicable and not result.valid
    assert result.reason_code == "unverifiable_solution_option"
    assert result.solution_display == "x < 5"
    assert result.option_displays == ("x < 5", "?", "x > 5", "x = 5")
    assert result.correct_indices == ()
    failure, _ = publication_failure(question, options, 0, options[0])
    assert failure == "unverifiable_solution_option"


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


def test_option_in_a_different_variable_fails_closed():
    """Relacija u TUĐOJ nepoznatoj na zadatku u dometu nije dokaziva kao
    odgovor — pada zatvoreno, ne gasi orakl."""
    question = r"Riješi nejednačinu: $x+3<8$"
    options = (r"$y<5$", r"$x>5$", r"$x=5$", r"$x<11$")
    result = evaluate(question, options)
    assert result.applicable and not result.valid
    assert result.reason_code == "unverifiable_solution_option"
    failure, _ = publication_failure(question, options, 0, options[0])
    assert failure == "unverifiable_solution_option"


def test_ambiguous_slash_coefficient_stays_silent():
    """„3/4x“ bez \\frac je dvosmisleno ((3/4)x ili 3/(4x)) — ne tumači se."""
    _silent(r"Riješi jednačinu: $3/4x=6$",
            (r"$x=8$", r"$x=2$", r"$x=4$", r"$x=6$"))


# ---------------------------------------------------------------------------
# 5b) MJEŠOVITI BROJEVI — EGZAKTNO čitanje (zatvaranje posljednje poznate rupe)
# ---------------------------------------------------------------------------
# Mješovit broj je ranije NAMJERNO ćutao (naivna zamjena je nadovezivala cifre:
# 94/5 umjesto 49/5), pa je „riješi“ zadatak s mješovitim opcijama ostajao bez
# ijedne determinističke provjere — posljednja poznata uobičajena reprezentacija
# koja je gasila zaštitu. Sada se ugovorno valjan oblik K\frac{p}{q}
# (K ≥ 1, 1 ≤ p < q) čita egzaktno kao K + p/q kroz `Fraction`.

def test_mixed_number_exact_value_and_school_sign_convention():
    """$9\\frac{4}{5}$ = 49/5, а `-9\\frac{4}{5}` = -(9+4/5) = -49/5 —
    NIKAD -9 + 4/5 = -41/5. Distraktor $-\\frac{41}{5}$ dokazuje razliku."""
    result = evaluate(r"Riješi jednačinu: $x = 9\frac{4}{5}$",
                      (r"$9\frac{4}{5}$", r"$\frac{94}{5}$", r"$9\frac{1}{5}$",
                       "$10$"))
    assert result.applicable and result.valid
    assert result.solution_display == "x = 49/5"
    # 9\frac{1}{5} = 46/5 (sabiranje), ne 91/5 (nadovezivanje cifara)
    assert result.option_displays == ("x = 49/5", "x = 94/5", "x = 46/5", "x = 10")
    assert result.correct_index == 0

    negative = evaluate(r"Riješi jednačinu: $x = -9\frac{4}{5}$",
                        (r"$-9\frac{4}{5}$", r"$9\frac{4}{5}$",
                         r"$-\frac{41}{5}$", "$-9$"))
    assert negative.applicable and negative.valid
    assert negative.solution_display == "x = -49/5"
    assert negative.option_displays[0] == "x = -49/5"
    assert negative.option_displays[2] == "x = -41/5"
    assert negative.correct_index == 0


def test_mixed_number_dfrac_spacing_and_singleton_forms():
    """$2\\frac{1}{3}$ = 7/3 u \\dfrac obliku, s razmakom i u jednočlanom skupu."""
    for option_a in (r"$2\frac{1}{3}$", r"$2\dfrac{1}{3}$", r"$2 \frac{1}{3}$",
                     r"$\{2\frac{1}{3}\}$"):
        result = evaluate(r"Riješi jednačinu: $3x = 7$",
                          (option_a, "$2$", r"$\frac{3}{7}$", r"$2\frac{2}{3}$"))
        assert result.applicable and result.valid, option_a
        assert result.solution_display == "x = 7/3"
        assert result.correct_index == 0, option_a


def test_mixed_number_bounds_in_relational_and_chained_options():
    """Mješovita granica zraka i lanca kanonizuje se egzaktno (49/5; -5/2, 13/4)."""
    ray = evaluate(r"Riješi nejednačinu: $x < 9\frac{4}{5}$",
                   (r"$x<9\frac{4}{5}$", r"$x>9\frac{4}{5}$",
                    r"$x=9\frac{4}{5}$", "$x<9$"))
    assert ray.applicable and ray.valid
    assert ray.solution_display == "x < 49/5"
    assert ray.correct_index == 0

    chain = evaluate(r"Riješi nejednačinu: $-2\frac{1}{2} < x < 3\frac{1}{4}$",
                     (r"$-2\frac{1}{2}<x<3\frac{1}{4}$", r"$x<3\frac{1}{4}$",
                      "$x=1$", "$x>0$"))
    assert chain.applicable and chain.valid
    assert chain.solution_display == "-5/2 < x < 13/4"
    assert chain.correct_index == 0


def test_mixed_number_member_points_still_lose_to_the_interval():
    result = evaluate(r"Riješi nejednačinu: $-2\frac{1}{2} < x < 3\frac{1}{4}$",
                      ("$1$", "$2$", "$3$", "$0$"))
    assert result.applicable and not result.valid
    assert result.reason_code == "no_correct_option"
    assert result.solution_display == "-5/2 < x < 13/4"


def test_wrongly_marked_mixed_number_yields_marked_option_math_mismatch():
    question = r"Riješi jednačinu: $7x = \frac{693}{10}$"
    options = (r"$9\frac{9}{10}$", r"$-\frac{99}{10}$", "$10$", r"$9\frac{4}{5}$")
    failure, result = publication_failure(question, options, 3, options[3])
    assert failure == "marked_option_math_mismatch"
    assert result.correct_index == 0


def test_malformed_mixed_number_fails_closed_never_silent_never_crash():
    """Van ugovora (nepravi sufiks, q=0, K=0, p=0, slovo/cifra iza, tekstualni
    „9 4/5“ koji bi se slijepio u 94/5): fail closed, NE applicable=False."""
    question = r"Riješi nejednačinu: $x+3<8$"
    for bad in (r"$9\frac{7}{5}$", r"$9\frac{4}{0}$", r"$0\frac{1}{2}$",
                r"$9\frac{0}{5}$", r"$9\frac{4}{5}x$", r"$9\frac{4}{5}2$",
                "9 4/5"):
        result = evaluate(question, (r"$x<5$", bad, r"$x>5$", r"$x=5$"))
        assert result.applicable, bad
        assert result.reason_code == "unverifiable_solution_option", bad
        assert result.option_displays[1] == "?", bad


def test_membership_question_with_mixed_number_options_stays_silent():
    """Mješovit broj je vrijednost, pa na pitanju o ČLANSTVU ostaje ista
    semantička granica kao gola vrijednost: ćutanje, ne presuđivanje."""
    _silent(r"Koji broj je rješenje nejednačine $x+1<3$?",
            (r"$1\frac{1}{2}$", "$2$", "$3$", "$4$"))


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


def test_preflight_carries_the_unverifiable_option_code_to_the_reviewer():
    """Novi kod mora stići recenzentu PRIJE drugog poziva, s izvedenim skupom,
    i mora postojati recept za popravku u instrukcijama."""
    from matbot.tutor import package_preflight as preflight
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    options = (r"$x<5$", "nema rješenja", r"$x>5$", r"$x=5$")
    task = TaskPayload(
        selected_lesson_id="7-02-019",
        selected_lesson_title="Nejednačine sa sabiranjem i oduzimanjem u Z",
        target_difficulty_level=1,
        text=r"Riješi nejednačinu: $x+3<8$", task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(options)],
        correct_option_index=0, correct_option_id="a",
        expected_answer=options[0],
        solution=r"Oduzmi $3$ od obje strane pa je $x<5$.",
        difficulty="easy",
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        task_signature=TaskSignature(
            task_family="linear_inequality", operation_or_relation="solve",
            normalized_parameters=[SignatureParameter(name="case", value="unver")],
            required_conditions=[], relevant_objects=["nejednakost"],
            answer_type="multiple_choice"))
    issues = preflight.collect_package_issues(task)
    found = [issue for issue in issues
             if issue.code == "unverifiable_solution_option"]
    assert found, [issue.code for issue in issues]
    assert "server solved: x < 5" in found[0].detail
    # recept za popravku postoji u recenzentskom ulazu
    assert "unverifiable_solution_option" in preflight.format_for_reviewer(issues)


def test_preflight_catches_the_live_bare_option_package_too():
    """Ista kapija mora uhvatiti i drugu živu pojavu (gole opcije) — nalaz
    stiže recenzentu PRIJE drugog poziva, s izvedenim skupom rješenja."""
    from matbot.tutor import package_preflight as preflight
    from matbot.tutor.schema import (DifficultyEvidence, SignatureParameter,
                                     TaskPayload, TaskSignature, TutorOption)
    task = TaskPayload(
        selected_lesson_id="7-02-019",
        selected_lesson_title="Nejednačine sa sabiranjem i oduzimanjem u Z",
        target_difficulty_level=1, text=F008B_TASK, task_type="multiple_choice",
        options=[TutorOption(id="abcd"[i], text=value)
                 for i, value in enumerate(F008B_OPTIONS)],
        correct_option_index=F008B_MARKED, correct_option_id="c",
        expected_answer=F008B_OPTIONS[F008B_MARKED],
        solution=r"Oduzmi $1$ od svih strana pa je $-2<x<0$.",
        difficulty="easy",
        difficulty_evidence=DifficultyEvidence(
            reasoning_steps=1, condition_count=1, operation_count=1,
            representation_change_count=0, requires_explanation=False,
            requires_comparison=False, requires_construction=False,
            requires_proof_or_justification=False, combines_concepts=False),
        task_signature=TaskSignature(
            task_family="linear_inequality", operation_or_relation="solve",
            normalized_parameters=[SignatureParameter(name="case", value="f008b")],
            required_conditions=[], relevant_objects=["nejednakost"],
            answer_type="multiple_choice"))
    issues = preflight.collect_package_issues(task)
    found = [issue for issue in issues if issue.code == "no_correct_option"]
    assert found, [issue.code for issue in issues]
    assert "server solved: -2 < x < 0" in found[0].detail
