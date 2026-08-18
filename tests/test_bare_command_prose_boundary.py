"""Granica skenera golih komandi: `\\text{…}` je proza, ne izgubljen backslash.

Živi nalaz (A/B modela, 2026-08-18, zadatak UNI-2): na „Pretvori 45 minuta u
sate.“ model je vratio matematički TAČAN odgovor

    $45\\,\\text{min}=0,75\\,\\text{h}$

a server ga je odbio kao `bare_command_in_math:min` i objavio kanonsku poruku.
Uzrok: skener izgubljenog backslasha čitao je SADRŽAJ `\\text{…}`, gdje je
`min` standardna oznaka za minutu, a ne funkcija $\\min$.

Popravka je granica parsiranja — nikad izuzetak za konkretnu riječ. Ovi testovi
čuvaju OBJE strane: da proza više ne pada, i da se stvarne gole komande i dalje
hvataju.
"""
import pytest

from matbot.mathsafe import (find_unsafe_math_issues,
                             sanitize_and_validate_math_text)


# ---------------------------------------------------------------------------
# 1. DOKAZANA REPRODUKCIJA
# ---------------------------------------------------------------------------

def test_live_uni2_answer_is_no_longer_rejected():
    answer = "$45\\,\\text{min}=0,75\\,\\text{h}$"
    issues = find_unsafe_math_issues(answer)
    assert "bare_command_in_math:min" not in issues
    assert issues == []
    text, is_safe = sanitize_and_validate_math_text(answer)
    assert is_safe, text


# ---------------------------------------------------------------------------
# 2. PROZNI SADRŽAJ SE NE TUMAČI KAO KOMANDA
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    "min", "sin", "max", "cos", "log", "mod", "det", "h", "cm", "kg",
    "minuta", "sati", "km/h", "dio knjige",
])
def test_prose_payload_is_never_read_as_a_command(payload):
    assert find_unsafe_math_issues("$\\text{%s}$" % payload) == []


@pytest.mark.parametrize("command", ["mathrm", "operatorname"])
def test_other_prose_argument_commands_share_the_boundary(command):
    """Granica važi za SVE prozne argumente, ne samo `\\text`.

    Mjeri se BAŠ granica: sadržaj se ne čita kao gola komanda. Da li je sama
    komanda na bijeloj listi je ZASEBNO pitanje (`operatorname` nije, i to se
    ovom popravkom namjerno ne mijenja)."""
    issues = find_unsafe_math_issues("$12\\,\\%s{min}$" % command)
    assert "bare_command_in_math:min" not in issues
    assert "unrepaired_bare_command_in_math" not in issues


def test_prose_payload_boundary_survives_several_groups_in_one_segment():
    answer = "$1\\,\\text{min}+2\\,\\text{min}=3\\,\\text{min}$"
    assert find_unsafe_math_issues(answer) == []


# ---------------------------------------------------------------------------
# 3. STVARNE GOLE KOMANDE SE I DALJE HVATAJU (van proznog konteksta)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer,expected", [
    ("$min(a,b)$", "bare_command_in_math:min"),
    ("$max(a,b)$", "bare_command_in_math:max"),
    ("$sin x$", "bare_command_in_math:sin"),
    ("$2 cdot 3$", "bare_command_in_math:cdot"),
])
def test_bare_function_names_outside_text_remain_detected(answer, expected):
    assert expected in find_unsafe_math_issues(answer)


def test_broken_text_without_backslash_is_still_rejected():
    """`text{min}` NEMA backslash — to je upravo defekt koji skener i traži.
    Maska pokriva samo PROPISNO napisane prozne argumente, pa ovo mora pasti."""
    issues = find_unsafe_math_issues("$text{min}$")
    assert "unrepaired_bare_command_in_math" in issues
    assert issues, "pokvaren zapis nikad ne smije proći"


def test_bare_command_next_to_a_legitimate_text_group_is_still_caught():
    """Maska ne smije zasjeniti golu komandu IZVAN prozne grupe u istom segmentu."""
    issues = find_unsafe_math_issues("$2 cdot 3\\,\\text{min}$")
    assert "bare_command_in_math:cdot" in issues


# ---------------------------------------------------------------------------
# 4. ISPRAVAN LaTeX OSTAJE ISPRAVAN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "$\\sin x$", "$\\min(a,b)$", "$\\max(a,b)$", "$\\cos 2x$",
    "$\\frac{1}{2}$", "$\\sqrt{2}$", "$2 \\cdot 3$",
    "$45\\,\\text{min}$", "$0,75\\,\\text{h}$",
])
def test_proper_commands_remain_valid(answer):
    assert find_unsafe_math_issues(answer) == []


# ---------------------------------------------------------------------------
# 5. OSTALE PROVJERE SIGURNOSTI SU NEPROMIJENJENE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer,expected_issue", [
    ("$x = 5\\", "dangling_terminal_backslash"),
    ("$\\ty{2}$", None),                      # nepoznata komanda — samo mora pasti
    ("$\\text{min}\x10$", "control_character_in_math"),
])
def test_unrelated_safety_checks_still_fire(answer, expected_issue):
    issues = find_unsafe_math_issues(answer)
    assert issues, "očekivan bar jedan problem: %r" % answer
    if expected_issue:
        assert expected_issue in issues


def test_unknown_command_inside_text_payload_is_still_unknown():
    """Maskira se SADRŽAJ, ne komande — nepoznata komanda i dalje pada."""
    assert find_unsafe_math_issues("$\\tyx{min}$")


def test_masking_preserves_segment_length_and_structure():
    from matbot.mathsafe import _mask_prose_argument_payloads
    segment = "45\\,\\text{min}=0,75\\,\\text{h}"
    masked = _mask_prose_argument_payloads(segment)
    assert len(masked) == len(segment)
    assert masked.count("{") == segment.count("{")
    assert masked.count("}") == segment.count("}")
    assert "\\text{" in masked
    assert "min" not in masked
