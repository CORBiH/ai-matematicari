"""Language guards for the teacher-comment reporting contract."""
import pytest

from matbot import parent_report, report_facts, report_prompt, report_validation
from tests.test_parent_report import FakeReportLLM, good_narrative, payload


@pytest.mark.parametrize("sentence, expected", [
    ("Aktivnost na času ocijenjena je sa 4,0.", "ocijenjena"),
    ("Prosječna ocjena aktivnosti je 4,0.", "ocjena"),
    ("To nije ocjena znanja.", "ocjena"),
    ("Nastavnik ocjenjuje angažman.", "ocjenjuje"),
])
def test_grade_language_is_detected(sentence, expected):
    assert expected in report_validation.grade_language_violations(sentence)


@pytest.mark.parametrize("sentence", [
    "Prosječna aktivnost na časovima bila je 4,0 / 5.",
    "Angažman je bio 4 od 5.",
    "4/5 je numerička aktivnost na času.",
    "Aktivnost na času je 4.",
    "Angažman: 3,0.",
    "Učešće u radu bilo je 5.",
    "Aktivnost na času prati se od 1 do 5.",
])
def test_numeric_teacher_rating_wording_is_detected(sentence):
    assert report_validation.teacher_rating_violations(sentence)


@pytest.mark.parametrize("sentence", [
    "Prema zapažanju instruktora, rad je bio samostalan.",
    "Za pouzdaniju procjenu potrebno je više zadataka.",
    "Procjena se zasniva na ograničenom broju zadataka.",
])
def test_qualitative_wording_is_allowed(sentence):
    assert report_validation.grade_language_violations(sentence) == []
    assert report_validation.teacher_rating_violations(sentence) == []


def test_generation_fails_closed_on_numeric_teacher_rating():
    facts = report_facts.build_ai_facts(payload())
    llm = FakeReportLLM(output=good_narrative(
        summary="Aktivnost na časovima bila je 4 / 5."))
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, llm)
    assert "teacher_numeric_rating" in caught.value.code
    assert llm.calls == 1


def test_prompt_forbids_numeric_teacher_ratings_and_direct_quote_attribution():
    prompt = " ".join(report_prompt.SYSTEM_PROMPT.split())
    assert "Ne prikazuj niti izmišljaj numeričku procjenu nastavnika" in prompt
    assert "ne stavljaj AI prepričavanje pod navodnike" in prompt
    assert report_prompt.REPORT_PROMPT_VERSION == "3d-4"


def test_internal_teacher_comment_field_cannot_leak_to_parent_text():
    assert "internal:teacher_comments" in report_validation.markup_violations(
        "Podaci iz teacher_comments pokazuju napredak.")
