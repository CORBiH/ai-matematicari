"""Faza 3D — aktivnost na času je ANGAŽMAN, nikad ocjena. Ni u poricanju.

ŽIVI NALAZ (sintetički reporting smoke, kandidat 93a9460): model je napisao

    „Aktivnost na časovima prosječno je ocijenjena sa 4,0, što predstavlja
     angažman u radu, a ne ocjenu znanja."

Značenje je TAČNO — rečenica sama kaže da to nije ocjena znanja. Ali roditelj
koji preleti izvještaj zapamti „ocijenjena 4,0" i pročita ga kao školsku ocjenu;
poricanje na kraju rečenice ne stigne. Zato se zabranjuje i sam RJEČNIK ocjene,
uključujući poricanje: metrika se opisuje onim ŠTO JEST.

DRUGA GRANICA, JEDNAKO VAŽNA: „procjena" je DOZVOLJENA i prompt je izričito
traži kod slabog dokaza („za pouzdaniju procjenu potrebno je više zadataka").
Goli podniz `ocjen` pogađa `pr-OCJEN-a`, pa provjera mora biti na granici
riječi. Ovaj fajl dokazuje oba smjera.

PII: sve rečenice su sintetičke.
"""
import pytest

from matbot import (parent_report, report_facts, report_prompt,
                    report_validation)

from tests.test_parent_report import FakeReportLLM, good_narrative, payload


# ---------------------------------------------------------------------------
# 1) ODBIJA JEZIK OCJENE
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sentence, expected", [
    ("Aktivnost na času ocijenjena je sa 4,0.", "ocijenjena"),
    ("Prosječna ocjena aktivnosti je 4,0.", "ocjena"),
    ("Aktivnost je dobila ocjenu 4.", "ocjenu"),
    # PORICANJE JE TAKOĐE PREKRŠAJ — to je cijeli smisao ove provjere.
    ("Aktivnost je 4,0, ali to nije ocjena znanja.", "ocjena"),
    ("Učenik je ocijenjen sa 4 za aktivnost.", "ocijenjen"),
    ("Ocjenjivanje aktivnosti je redovno.", "ocjenjivanje"),
    ("Nastavnik ocjenjuje angažman.", "ocjenjuje"),
    ("Ocjenom se ne mjeri angažman.", "ocjenom"),
    ("Ocjene nisu dio ovog izvještaja.", "ocjene"),
    ("Treba ocijeniti napredak.", "ocijeniti"),
])
def test_grade_language_is_detected(sentence, expected):
    assert expected in report_validation.grade_language_violations(sentence)


# ---------------------------------------------------------------------------
# 2) NE DIRA „PROCJENA" — riječ koju prompt sam traži
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sentence", [
    "Prosječna aktivnost na časovima bila je 4,0 / 5.",
    "Prosječan nivo angažmana bio je 4,0 / 5.",
    "Tokom evidentiranih časova prosječan nivo aktivnosti bio je 4,0 / 5.",
    "Učenik je na časovima pokazivao dobar nivo angažmana.",
    "Za pouzdaniju procjenu potrebno je više zadataka.",
    "Procjena se zasniva na ograničenom broju zadataka.",
    "Samoprocjena nije dio ovog izvještaja.",
    "Potrebno je procijeniti dodatne zadatke.",
    "Procjenom rada obuhvaćeno je više izvora.",
])
def test_allowed_wording_is_never_flagged(sentence):
    assert report_validation.grade_language_violations(sentence) == []


def test_the_check_uses_a_word_boundary_not_a_substring():
    """Goli podniz bi oborio `procjena` — to je već jednom bio kvar."""
    pattern = report_validation._GRADE_LANGUAGE_RE.pattern
    assert pattern.startswith("\\b"), pattern
    # Nikad doslovni bajt 0x08 umjesto granice riječi (izmjerena greška alata).
    assert "\x08" not in pattern


# ---------------------------------------------------------------------------
# 3) KROZ CIJELI VALIDATOR
# ---------------------------------------------------------------------------
def _facts():
    return report_facts.build_ai_facts(payload())


def test_validator_rejects_the_exact_sentence_from_the_live_smoke():
    """Rečenica koja je stvarno nastala — sada pada zatvoreno."""
    facts = _facts()
    bad = good_narrative(summary=(
        "Aktivnost na časovima prosječno je ocijenjena sa 4,0, što predstavlja "
        "angažman u radu, a ne ocjenu znanja."))
    problems = report_validation.validate_narrative(bad, facts)
    assert any(p.startswith("report_activity_grade_language") for p in problems)


def test_validator_accepts_the_recommended_wording():
    facts = _facts()
    ok = good_narrative(summary=(
        "Tokom evidentiranih časova prosječan nivo aktivnosti bio je dobar, a "
        "učenik je u radu pokazivao samostalnost."))
    assert report_validation.validate_narrative(ok, facts) == []


def test_generation_fails_closed_on_grade_language():
    facts = _facts()
    bad = good_narrative(summary="Aktivnost je ocijenjena kao vrlo dobra.")
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))
    assert "activity_grade_language" in caught.value.code


def test_rejection_still_costs_exactly_one_call():
    """Odbijanje NIKAD ne plaća drugi poziv (pravilo 4 projekta)."""
    facts = _facts()
    llm = FakeReportLLM(output=good_narrative(summary="Ocjena aktivnosti je 4."))
    with pytest.raises(parent_report.ReportGenerationError):
        parent_report.generate_narrative(facts, llm)
    assert llm.calls == 1


def test_hedging_wording_still_generates_normally():
    """Slab dokaz i dalje smije reći „za pouzdaniju procjenu"."""
    facts = _facts()
    ok = good_narrative(
        focus_areas=["Za pouzdaniju procjenu potrebno je više zadataka."])
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


def test_grade_language_is_checked_across_every_narrative_field():
    facts = _facts()
    for field in ("strengths", "focus_areas", "next_month_recommendations"):
        bad = good_narrative(**{field: ["Aktivnost je dobila ocjenu 4."]})
        problems = report_validation.validate_narrative(bad, facts)
        assert any(p.startswith("report_activity_grade_language")
                   for p in problems), field


# ---------------------------------------------------------------------------
# 4) PROMPT 3d-2
# ---------------------------------------------------------------------------
def test_prompt_version_is_3d_2():
    assert report_prompt.REPORT_PROMPT_VERSION == "3d-2"


def test_prompt_describes_activity_as_engagement():
    # Prijelomi redova se normalizuju: fraza smije biti prelomljena preko dva
    # reda, a i dalje je ista uputa.
    prompt = " ".join(report_prompt.SYSTEM_PROMPT.split())
    assert "ANGAŽMAN NA ČASU" in prompt
    for wording in ("aktivnost", "angažman", "nivo aktivnosti", "učešće u radu"):
        assert wording in prompt, wording


def test_prompt_forbids_grade_vocabulary_even_in_a_denial():
    prompt = report_prompt.SYSTEM_PROMPT
    assert "Zabrana važi I U PORICANJU" in prompt
    assert "Reci šta metrika JEST, ne šta nije." in prompt
    # I izričito štiti „procjenu" da model ne pomisli da je i ona zabranjena.
    assert '„procjena" i „procijeniti" su dozvoljene' in prompt


def test_the_guard_is_not_applied_to_the_prompt_itself():
    """Prompt SMIJE imenovati zabranjene riječi — time ih i zabranjuje.

    Provjera se primjenjuje na NARATIV, ne na uputu; da nije tako, sama zabrana
    bi obarala svaki izvještaj."""
    assert report_validation.grade_language_violations(
        report_prompt.SYSTEM_PROMPT), "prompt ih navodi (očekivano)"
    facts = _facts()
    assert report_validation.validate_narrative(good_narrative(), facts) == []
