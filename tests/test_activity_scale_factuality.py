"""Reporting — GRANICE SKALE ANGAŽMANA SU ČINJENICA, NE IZMIŠLJEN BROJ.

ŽIVI NALAZ KOJI OVAJ FAJL ČUVA (izdanje 1ed172c, sintetički reporting smoke):
prompt 3d-2 IZRIČITO traži rečenicu

    „Prosječna aktivnost na časovima bila je 4,0 / 5."

a provjera činjeničnosti je peticu odbijala kao izmišljen broj — jer se nijedna
IZMJERENA vrijednost tog mjeseca nije slučajno poklopila s njom. Mjesec sa četiri
časa (3 prisutna, 1 izostanak, prosjek 4,0 preko 3 ocijenjena časa) je zato pao
zatvoreno uz `report_unsupported_number:5`. Prompt i validator su tvrdili
suprotno; ranija izdanja su prolazila samo zato što im je `sessions_total` bio 5.

To je bila NEDOSTUPNOST, ne netačnost — nijedan pogrešan izvještaj nije mogao
proći. Ispravka je zato uska: granice skale postaju dopušteni brojevi SAMO kad
angažman stvarno postoji, i to iz kanonskih konstanti, ne iz nove magične cifre.

ŠTA OVAJ FAJL NE TVRDI: da validator veže broj za tvrdnju. Ne veže — to je
skupovna provjera i takva je bila i prije. Vidi
`test_the_validator_is_set_membership_not_semantic_binding`.
"""
import pytest

from matbot import (parent_report, report_facts, report_prompt,
                    report_validation, student_sessions)

from tests.test_parent_report import FakeReportLLM, good_narrative

# Tačan brojčani oblik koji je pao u smoke-u kandidata 1ed172c.
FAILED_SHAPE = {
    "available": True,
    "sessions_total": 4,
    "present_count": 3,
    "absent_count": 1,
    "activity_average": 4.0,
    "activity_rated_sessions": 3,
    "homework_assigned": 2,
    "homework_done": 1,
    "homework_not_done": 1,
    "areas_worked": ["Cijeli brojevi"],
    "lessons_worked": ["Skup cijelih brojeva Z"],
    "signals": ["strong_class_engagement"],
}

NO_ACTIVITY_SHAPE = dict(FAILED_SHAPE, activity_average=None,
                         activity_rated_sessions=0)


EMPTY_PAYLOAD = {
    "student_id": 1,
    "report_month": "2026-09",
    "profile": {"display_name": "Sintetitcki Ucenik", "grade": 7},
    # Thinkific i MAT-BOT su NAMJERNO prazni: da nijedan drugi izvor ne unese
    # broj koji bi slučajno „pokrio" peticu i učinio test bezvrijednim.
    "thinkific": {"snapshot_missing": True},
    "matbot": {},
}


def facts_with(instruction):
    """Činjenice u kojima SAMO časovi nose brojeve.

    `instruction` je već SPLJOŠTEN oblik (onaj koji `_instruction_facts` vraća),
    pa se ubacuje POSLIJE normalizacije — tako test opisuje tačno one brojeve
    koje je smoke i vidio, bez ponovnog prolaska kroz mjesečni sažetak."""
    facts = report_facts.build_ai_facts(dict(EMPTY_PAYLOAD))
    facts["instruction"] = dict(instruction)
    facts["grade"] = 7
    return facts


def allowed(instruction):
    return report_facts.allowed_numbers(facts_with(instruction))


def unsupported(text, instruction):
    return report_validation.unsupported_numbers(text, allowed(instruction))


# ===========================================================================
# 1-2) KANONSKA SKALA — JEDAN IZVOR ISTINE
# ===========================================================================
def test_1_canonical_activity_minimum_is_one():
    assert student_sessions.ACTIVITY_MIN == 1


def test_2_canonical_activity_maximum_is_five():
    assert student_sessions.ACTIVITY_MAX == 5


def test_2b_the_bounds_are_not_duplicated_as_literals_in_report_facts():
    """Skala živi u `student_sessions`; `report_facts` je samo uvozi.

    Dvije kopije iste skale bi se razišle prvom izmjenom, a izvještaj bi tiho
    dopustio broj koji evidencija časova više ne priznaje."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "matbot"
              / "report_facts.py").read_text(encoding="utf-8")
    body = source.split("def allowed_numbers(")[1]
    assert "student_sessions.ACTIVITY_MIN" in body
    assert "student_sessions.ACTIVITY_MAX" in body
    # Nema prepisane petice/jedinice kao gole vrijednosti u toj grani.
    branch = body.split("activity_rated_sessions")[1].split("add(facts.get")[0]
    assert "add(5" not in branch and "add(1)" not in branch


# ===========================================================================
# 3-6) DOKAZ O ANGAŽMANU ČINI GRANICE ČINJENIČNIM
# ===========================================================================
def test_3_activity_evidence_makes_the_scale_bounds_factual():
    values = allowed(FAILED_SHAPE)
    assert 5.0 in values and 1.0 in values


def test_4_the_exact_failed_scenario_now_passes():
    """Tačna rečenica koju prompt 3d-2 i traži."""
    text = "Prosječna aktivnost na časovima bila je 4,0 / 5."
    assert unsupported(text, FAILED_SHAPE) == []


def test_4b_the_whole_report_pipeline_accepts_the_failed_shape():
    facts = facts_with(FAILED_SHAPE)
    narrative = good_narrative(
        summary=("Na časovima je zabilježen redovan rad. Prosječna aktivnost "
                 "na časovima bila je 4,0 / 5."))
    llm = FakeReportLLM(output=narrative)
    assert parent_report.generate_narrative(facts, llm)
    assert llm.calls == 1


def test_5_the_one_to_five_wording_passes_with_activity_evidence():
    text = "Aktivnost na času se prati na skali od 1 do 5."
    assert unsupported(text, FAILED_SHAPE) == []


def test_6_no_measured_fact_needs_to_equal_five():
    """Nijedna izmjerena vrijednost nije 5 — a petica je i dalje dopuštena."""
    measured = {k: v for k, v in FAILED_SHAPE.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)}
    assert 5 not in measured.values() and 5.0 not in measured.values()
    assert 5.0 in allowed(FAILED_SHAPE)


# ===========================================================================
# 7-10) NEDOPUŠTENI BROJEVI I DALJE PADAJU
# ===========================================================================
@pytest.mark.parametrize("value", ["6", "8", "10", "97", "100", "12", "45"])
def test_7_to_10_unsupported_numbers_still_fail(value):
    text = "Rezultat je %s." % value
    assert unsupported(text, FAILED_SHAPE) == [value]


def test_7b_unsupported_percentages_still_fail():
    assert unsupported("Tačnost je 97 %.", FAILED_SHAPE) == ["97"]
    assert unsupported("Pregledano je 100 % gradiva.", FAILED_SHAPE) == ["100"]


def test_7c_an_arbitrary_count_still_fails_through_the_whole_pipeline():
    facts = facts_with(FAILED_SHAPE)
    bad = good_narrative(summary="Urađeno je 12 kontrolnih zadataka.")
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))
    assert "unsupported_number:12" in caught.value.code


def test_the_validator_is_set_membership_not_semantic_binding():
    """POSTOJEĆA ARHITEKTURA, izričito zapisana — ovaj zakrpa je NE rješava.

    Jednom kad je broj dopuštena činjenica bilo gdje, validator ga prihvata i
    drugdje: „Učenik je uradio 5 kontrolnih" prolazi brojčanu provjeru jer je 5
    granica skale. To je bilo tačno i prije ove izmjene za svaki drugi broj (npr.
    `sessions_total`), i nije regresija — vezivanje broja za tvrdnju traži
    semantičku analizu koja ovdje svjesno ne postoji.

    Zapisano je da se ne bi kasnije čitalo kao garancija koje nema."""
    assert unsupported("Učenik je uradio 5 kontrolnih.", FAILED_SHAPE) == []
    # Isti oblik s brojem koji NIJE nijedna činjenica i dalje pada.
    assert unsupported("Učenik je uradio 6 kontrolnih.", FAILED_SHAPE) == ["6"]


def test_the_widening_is_exactly_two_values():
    """Ni jedan broj više od dvije kanonske granice."""
    before = report_facts.allowed_numbers(facts_with(NO_ACTIVITY_SHAPE))
    after = report_facts.allowed_numbers(facts_with(FAILED_SHAPE))
    # Razlika dolazi i od izmjerenih vrijednosti koje NO_ACTIVITY nema (4.0, 3),
    # pa se poredi nad istim mjerenjima: dodaje se samo ono što skala nosi.
    same_measurements = dict(FAILED_SHAPE)
    baseline = set()
    for key in ("sessions_total", "present_count", "absent_count",
                "activity_average", "activity_rated_sessions",
                "homework_assigned", "homework_done", "homework_not_done"):
        baseline.add(float(same_measurements[key]))
    baseline.add(7.0)
    extra = after - baseline - report_facts.allowed_numbers(
        facts_with(NO_ACTIVITY_SHAPE))
    assert extra <= {1.0, 5.0}
    assert before  # sanity: prazna osnova bi test obesmislila


# ===========================================================================
# 11) BEZ DOKAZA O ANGAŽMANU — STROŽE PONAŠANJE
# ===========================================================================
def test_11_no_activity_evidence_does_not_manufacture_the_scale():
    values = allowed(NO_ACTIVITY_SHAPE)
    assert 5.0 not in values, "skala je uvedena bez ijednog ocijenjenog časa"


def test_11b_no_activity_evidence_still_rejects_the_scale_sentence():
    text = "Prosječna aktivnost na časovima bila je 4,0 / 5."
    assert "5" in unsupported(text, NO_ACTIVITY_SHAPE)


def test_11c_a_single_rated_session_is_enough():
    """Prag je DOKAZ, ne količina: jedan ocijenjen čas je mjerenje."""
    one = dict(FAILED_SHAPE, activity_rated_sessions=1, activity_average=3.0)
    assert 5.0 in allowed(one)


def test_11d_an_empty_instruction_section_gains_nothing():
    assert 5.0 not in allowed({})
    assert 5.0 not in allowed({"available": False})


# ===========================================================================
# 12-13) JEZIČKE ZAŠTITE NEPROMIJENJENE
# ===========================================================================
def test_12_the_grade_language_guard_is_unchanged():
    assert report_validation.grade_language_violations(
        "Aktivnost je ocijenjena sa 4,0.") == ["ocijenjena"]
    assert report_validation.grade_language_violations(
        "To nije ocjena znanja.") == ["ocjena"]


def test_13_procjena_wording_is_still_accepted():
    for phrase in ("procjena", "procjenu", "procjene", "pouzdaniju procjenu",
                   "procijeniti napredak"):
        assert report_validation.grade_language_violations(phrase) == [], phrase


def test_13b_the_scale_sentence_carries_no_grade_language():
    text = "Prosječna aktivnost na časovima bila je 4,0 / 5."
    assert report_validation.grade_language_violations(text) == []


# ===========================================================================
# 14) NAZIVI LEKCIJA — NETAKNUTO
# ===========================================================================
def test_14_trusted_label_masking_is_untouched():
    facts = facts_with(dict(FAILED_SHAPE,
                            lessons_worked=["Konstrukcije uglova 60°, 30°, "
                                            "90° i 45°"]))
    labels = report_facts.trusted_labels(facts)
    assert "Konstrukcije uglova 60°, 30°, 90° i 45°" in labels
    values = report_facts.allowed_numbers(facts)
    # Cifre iz naziva NE postaju globalno dopuštene mjere.
    assert 60.0 not in values and 90.0 not in values
    masked = report_validation.mask_trusted_labels(
        "Rađeno je gradivo Konstrukcije uglova 60°, 30°, 90° i 45°.", labels)
    assert report_validation.unsupported_numbers(masked, values) == []
    # Isti broj IZVAN naziva i dalje pada.
    assert report_validation.unsupported_numbers("Tačnost je 60 %.", values) \
        == ["60"]


# ===========================================================================
# 15-20) OSTATAK UGOVORA NEPROMIJENJEN
# ===========================================================================
def test_15_raw_class_comments_stay_out_of_the_ai_payload():
    from matbot import report_input

    data = dict(EMPTY_PAYLOAD)
    data["instruction"] = dict(FAILED_SHAPE)
    data["instruction"]["parent_comments"] = [
        {"date": "2026-09-01", "comment": "TAJNO ZAPAZANJE"}]
    facts = report_facts.build_ai_facts(data)
    import json

    assert "TAJNO ZAPAZANJE" not in json.dumps(facts, ensure_ascii=False)
    assert "parent_comments" not in facts["instruction"]
    assert report_input  # modul i dalje postoji u lancu


def test_16_thinkific_mastery_guard_wording_is_unchanged():
    instructions = report_prompt.SYSTEM_PROMPT
    assert "zna X posto" in instructions
    assert "NIJE znanje" in instructions


def test_17_attendance_semantics_unchanged():
    instructions = report_prompt.SYSTEM_PROMPT
    assert "PRISUSTVO: činjenica, nikad moralni sud" in instructions


def test_18_homework_denominator_semantics_unchanged():
    rows = [
        {"attendance": "present", "activity_rating": 4,
         "homework_status": "done", "session_date": "2026-09-01"},
        {"attendance": "present", "activity_rating": 5,
         "homework_status": "not_done", "session_date": "2026-09-02"},
        {"attendance": "absent", "activity_rating": None,
         "homework_status": "not_assigned", "session_date": "2026-09-03"},
    ]
    summary = student_sessions.build_monthly_summary(rows)
    assert summary["homework"]["assigned_count"] == 2
    assert summary["homework"]["not_assigned_count"] == 1
    assert summary["activity"]["average"] == 4.5


def test_19_report_prompt_version_is_still_3d_2():
    """Validator je naučio postojeću skalu — prompt se nije mijenjao."""
    assert report_prompt.REPORT_PROMPT_VERSION == "3d-2"


def test_19b_the_prompt_still_asks_for_the_scale_sentence():
    """Dokaz da je ispravka bila na PRAVOJ strani: prompt je i tražio 4,0 / 5."""
    instructions = report_prompt.SYSTEM_PROMPT
    assert "4,0 / 5" in instructions


def test_20_one_call_fail_closed_architecture_unchanged():
    facts = facts_with(FAILED_SHAPE)
    bad = good_narrative(summary="Tačnost je 97 %.")
    llm = FakeReportLLM(output=bad)
    with pytest.raises(parent_report.ReportGenerationError):
        parent_report.generate_narrative(facts, llm)
    assert llm.calls == 1, "odbijanje je potrošilo dodatni poziv"
