"""Regresije iz živog produkcijskog audita 2026-08-18.

Svaki test nosi TAČAN živi ulaz koji je izazvao nalaz, ne rekonstrukciju.
Sirovi zapisi kampanje: `scratchpad/live_product_audit_2026_08_18/`.

Pokriveno:
  * B-04 / C-09 — stil zapisa je tiho gasio nezavisnu provjeru slike;
  * B-06        — gol `$HH:MM$` je objavljen unutar matematičkog segmenta;
  * A5-10/A5-07 — lažna premisa učenika je preuzeta kao zadatost (ugovor prompta);
  * D-K3        — popravka kontrolnog nije spominjala ekvivalentne KORIJENE.
"""
import pytest

from matbot import imagecheck, prompts, quick
from matbot.schema import QuickImageTurnOutput
from tests.conftest import FakeLLM, make_quick_output


# ---------------------------------------------------------------------------
# B-04 / C-09 — normalizacija dokaznog zapisa sa slike
# ---------------------------------------------------------------------------

def _image_output(**overrides):
    base = {
        "reply": "$x=-\\frac{19}{30}$",
        "readability": "clear",
        "all_required_symbols_visible": True,
        "task_type": "linear_equation",
        "visible_math": "$(x-1\\frac{1}{6})+3,2=1\\frac{2}{5}$",
        "visible_problem_text": "Riješiti jednačinu.",
        "requested_quantity": "value_of_unknown",
        "visible_values": [],
        "unit": "",
        "answer_confidence": "high",
        "uncertainty_reason": "",
        "math_content_uncertain": False,
        "detected_tasks": [],
    }
    base.update(overrides)
    return QuickImageTurnOutput.model_validate(base)


def test_dollar_delimited_evidence_no_longer_disables_verification():
    """B-04: `$…$` oko `visible_math` je davalo `image_equation_unparsable`.

    Kod `unparsable` znači „provjera se NIJE izvršila“, pa je odgovor po
    doktrini legitimno išao dalje NEPROVJEREN — a modul je izgledao kao da radi.
    """
    verification = imagecheck.verify_image_answer(_image_output())
    assert verification.supported
    assert verification.engaged, "provjera se mora izvršiti, ne preskočiti"
    assert verification.code != "image_equation_unparsable"


def test_ascii_mixed_numbers_no_longer_disable_verification():
    """C-09: ista slika kao B-01, ali transkribovana kao `4 5/8` umjesto LaTeX-a.

    B-01 se uredno provjerio; C-09 je pao na `image_math_source_unparsable`.
    Porodica je ista — otkazivao je iskljucivo stil zapisa.
    """
    out = _image_output(
        task_type="fraction_expression",
        visible_math="(4 5/8 + 2 2/5) - (3 1/2 + 1 1/6)",
        reply="$2\\frac{43}{120}$",
        requested_quantity="numeric_result",
    )
    verification = imagecheck.verify_image_answer(out)
    assert verification.engaged and verification.verified
    assert verification.may_publish


def test_normalization_is_narrow_and_value_preserving():
    normalize = imagecheck.normalize_visible_math
    # granice se skidaju, vrijednost ostaje ista
    assert normalize("$1+2$") == "1+2"
    assert normalize("$$1+2$$") == "1+2"
    # mješoviti broj se prevodi samo kad je zaista mješoviti broj
    assert normalize("4 5/8") == "(4+5/8)"
    assert normalize("4 + 5/8") == "4 + 5/8", "operator razdvaja — nije mješoviti broj"
    assert normalize("\\frac{2}{15}+\\frac{3}{20}") == "\\frac{2}{15}+\\frac{3}{20}"
    assert normalize("") == ""


def test_contradicted_answer_still_blocks_after_normalization():
    """Normalizacija NE smije oslabiti blokadu: pogrešan račun na ispravno
    pročitanom dokazu i dalje mora pasti."""
    out = _image_output(
        task_type="fraction_expression",
        visible_math="(4 5/8 + 2 2/5) - (3 1/2 + 1 1/6)",
        reply="$99$",
        requested_quantity="numeric_result",
    )
    verification = imagecheck.verify_image_answer(out)
    assert verification.engaged and not verification.verified
    assert not verification.may_publish


def test_unknown_notation_still_fails_closed():
    """Ništa se ne pogađa: nerazumljiv zapis i dalje ostaje neizvršena provjera."""
    out = _image_output(visible_math="$x + \\text{nesto} = \\heartsuit$")
    verification = imagecheck.verify_image_answer(out)
    assert not verification.verified


# ---------------------------------------------------------------------------
# B-06 — gol `$HH:MM$` nikad ne izlazi kao matematički segment
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reply,expected", [
    ("$10:51$", "10:51"),
    ("$$10:51$$", "10:51"),
    ("$10:51$.", "10:51"),
    ("  $09:05$  ", "09:05"),
    ("$23:59$", "23:59"),
    # R2-01: `{:}` je ISPRAVAN LaTeX zapis dvotačke kao interpunkcije — prva
    # verzija popravke ga je promašila i vrijeme je opet izašlo kao matematika.
    ("$10{:}51$", "10:51"),
    ("$$07{:}05$$", "07:05"),
    ("$10{:}51$.", "10:51"),
])
def test_bare_math_clock_is_unwrapped(reply, expected):
    assert quick.unwrap_bare_math_clock_time(reply) == expected


@pytest.mark.parametrize("reply", [
    "$60:15$",                 # 60 nije validan sat — ovo je dijeljenje
    "$60{:}15$",               # isto i u LaTeX zapisu dvotačke
    "$4$",                     # rezultat dijeljenja 60:15
    "$12:30=0,4$",             # dijeljenje S rezultatom — ne dira se
    "Autobus stiže u 10:51.",  # čist tekst, već ispravan
    "$10:51$ i $11:56$",       # dva vremena — nije gol odgovor
    "$24:00$",                 # 24 nije validan sat
    "",
])
def test_non_bare_clock_replies_are_untouched(reply):
    assert quick.unwrap_bare_math_clock_time(reply) is None


def test_image_task_clock_result_reaches_student_as_plain_text():
    """B-06: zadatak SA SLIKE je vratio tačno vrijeme, ali u `$…$`.

    Poruka učenika je „Riješi zadatak sa slike“, pa se stara zamjena (koja
    traži sat u PORUCI) nije okidala.
    """
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$10:51$"))
    result = quick.run_quick_turn(fake, {
        "session_id": "audit-b06", "grade": 6, "selected_topic": "",
        "selected_oblast": "", "student_message": "Riješi zadatak sa slike. Samo rezultat.",
        "conversation_history": [],
    })
    assert "10:51" in result["answer"]
    assert "$10:51$" not in result["answer"]
    assert fake.call_count == 1, "zamjena je serverska — nikad drugi poziv"


def test_direct_clock_question_keeps_its_fuller_answer():
    """Postojeće ponašanje se ne mijenja: direktno pitanje o satu i dalje dobija
    punu serversku rečenicu, ne golo vrijeme."""
    fake = FakeLLM()
    fake.queue(make_quick_output(reply="$12:30$"))
    result = quick.run_quick_turn(fake, {
        "session_id": "audit-clock", "grade": 6, "selected_topic": "",
        "selected_oblast": "", "student_message": "Sastanak je u 12:30. Koliko je sati?",
        "conversation_history": [],
    })
    assert result["answer"] == quick.clock_time_answer("12:30")
    assert fake.call_count == 1


# ---------------------------------------------------------------------------
# A5-10 / A5-07 — lažna premisa učenika (ugovor prompta)
# ---------------------------------------------------------------------------

def test_quick_instructions_forbid_computing_from_a_false_premise():
    instructions = prompts.build_quick_instructions(7)
    assert "TVRDNJA UČENIKA O MATEMATICI SE PROVJERAVA, NE PREUZIMA" in instructions
    assert "NIKAD ne računaj iz netačne tvrdnje" in instructions
    # tačna činjenica se mora ponuditi, ne samo odbiti zadatak
    assert "180" in instructions and "200" in instructions


def test_false_premise_rule_does_not_replace_missing_data_rule():
    """Dva različita pravila — nedostajući podaci su mjereni 15/15 i ostaju."""
    instructions = prompts.build_quick_instructions(6)
    assert "NE izmišljaj podatke" in instructions
    assert "ne na zadate podatke jednog zadatka" in instructions


def test_wrong_constant_and_wrong_identity_are_named_explicitly():
    """R1-04/R1-05: prvo pravilo je propustilo netačnu konstantu i identitet."""
    instructions = prompts.build_quick_instructions(8)
    assert "NETAČNA KONSTANTA I NETAČAN IDENTITET SU ISTA GREŠKA" in instructions
    assert "\\pi\\approx3,14" in instructions
    assert "(a+b)^2=a^2+2ab+b^2" in instructions
    # zaokruživanje ostaje dozvoljeno, ali kao približnost — ne kao jednakost
    assert "ZAOKRUŽENOM" in instructions
    # tačan konačan rezultat ne oslobađa obaveze da se tvrdnja označi
    assert "NE oslobađa" in instructions


def test_false_premise_rule_reaches_every_quick_intent_and_grade():
    for grade in (6, 7, 8, 9):
        for intent in ("result", "explain", "verify", "subtask"):
            instructions = prompts.build_quick_instructions(grade, intent=intent)
            assert "NE PREUZIMA" in instructions, (grade, intent)


# ---------------------------------------------------------------------------
# D-K3 — popravka kontrolnog mora imenovati i ekvivalentne korijene/stepene
# ---------------------------------------------------------------------------

def test_equivalent_options_repair_hint_names_radical_and_power_forms():
    hint = prompts.kontrolni_repair_hint("equivalent_options")
    assert "\\sqrt{8}" in hint and "2\\sqrt{2}" in hint
    assert "2^{3}" in hint
    # postojeći primjeri ostaju
    assert "0,5" in hint


def test_repair_hint_matches_the_after_repair_code_too():
    """Živi log je drugi put prijavio `slot2:equivalent_options_after_repair`;
    ista uputa mora pokriti oba oblika koda."""
    assert (prompts.kontrolni_repair_hint("equivalent_options_after_repair")
            == prompts.kontrolni_repair_hint("equivalent_options"))
