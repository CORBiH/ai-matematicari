"""Faza 3C — mjesečni izvještaj za roditelja: činjenice, AI, nacrt, PDF.

TVRDNJA KOJU OVAJ FAJL DOKAZUJE: model piše SAMO prozu. Svaki broj u izvještaju
postoji prije nego što je model pozvan, ne mijenja se poslije njega, i preživi
njegov pad. Sve ostalo su posljedice te jedne granice.

Testovi su grupisani po granici koju čuvaju:
  1. UGOVOR ČINJENICA — šta model smije vidjeti (i, važnije, šta ne smije).
  2. DOKAZNA POLITIKA — jedan promašaj nije nalaz o znanju.
  3. IZLAZ MODELA — shema, provjera činjeničnosti, pad zatvoreno.
  4. TRAJNOST — snimak se ne mijenja kad se izvorni podaci kasnije promijene.
  5. PDF — iz SAČUVANOG nacrta, jedna strana, bosanska slova, bez PII-a.

PII: svi učenici su sintetički.
"""
import io
import json

import pytest

from matbot import (parent_report, report_facts, report_pdf, report_prompt,
                    report_validation, reporting_db, reporting_schema)
from matbot.student_identity import PROVIDER_THINKIFIC_EMAIL

from tests.test_thinkific_progress_import import build_v1, migrate, rows

libsql = pytest.importorskip("libsql")
pypdf = pytest.importorskip("pypdf")


# ---------------------------------------------------------------------------
# Sintetičke činjenice — oblik koji `build_report_input` stvarno vraća
# ---------------------------------------------------------------------------
def payload(**over):
    """Realan izvještajni ulaz: 37 prikazano, 14 tačnih, 10 netačnih."""
    base = {
        "student_id": 7,
        "report_month": "2026-08",
        "profile": {"display_name": "Đžemal Šćepanović", "grade": 6},
        "thinkific": {
            "snapshot_missing": False,
            "percent_viewed": 12, "percent_completed": 1,
            "previous_percent_viewed": None, "delta_percent_viewed": None,
            "previous_percent_completed": None, "delta_percent_completed": None,
            "sections": [
                {"ordinal": 1, "section_name": "SKUPOVI",
                 "current_progress_percent": 14,
                 "previous_progress_percent": None, "delta_progress_percent": None},
                {"ordinal": 2, "section_name": "KRUŽNICA, KRUG, UGAO",
                 "current_progress_percent": 0,
                 "previous_progress_percent": None, "delta_progress_percent": None},
            ],
        },
        "matbot": {
            "active_days": 2, "practice_tasks": 37,
            "practice_correct": 14, "practice_incorrect": 10,
            "practice_accuracy": 58.3, "hints_used": 6,
            "full_solutions_shown": 7, "explain_count": 19, "quick_count": 1,
            "kontrolni_generated": 4, "kontrolni_attempts": 4,
            "kontrolni_average": 25.0, "kontrolni_correct": 5,
            "kontrolni_total": 20,
            "lesson_outcomes": [
                {"lesson_id": "L-djeljivost", "lesson_name": "Djeljivost sa 3",
                 "area_name": "Djeljivost", "difficulty": "standard",
                 "incorrect_items": 1, "evidence_items": 1, "low_evidence": True},
                {"lesson_id": "L-razlomci", "lesson_name": "Razlomci",
                 "area_name": "Razlomci", "difficulty": "standard",
                 "incorrect_items": 4, "evidence_items": 8, "low_evidence": False},
            ],
        },
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


def good_narrative(**over):
    base = {
        "summary": ("Tokom mjeseca zabilježena je redovna aktivnost u radu sa "
                    "MAT-BOT-om. Pregledan je početni dio sadržaja kursa. "
                    "Rezultati kontrolnih pokazuju da je potrebno dodatno "
                    "uvježbavanje gradiva."),
        "strengths": ["Zabilježen je kontinuiran rad kroz vježbu."],
        "focus_areas": ["Vrijedi dodatno uvježbati gradivo iz razlomaka."],
        "next_month_recommendations": ["Kraće ali redovnije vježbanje."],
    }
    base.update(over)
    return base


class FakeReportLLM:
    """Dvojnik koji BROJI pozive. Broj poziva je dio ugovora, ne detalj."""

    def __init__(self, output=None, error=None):
        self._output = output if output is not None else good_narrative()
        self._error = error
        self.calls = 0
        self.last_instructions = None
        self.last_input = None

    def report_turn(self, instructions, input_text):
        self.calls += 1
        self.last_instructions = instructions
        self.last_input = input_text
        if self._error is not None:
            raise self._error
        from matbot.llm import LLMResult

        return LLMResult(output=self._output)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "reporting.db")
    build_v1(path)
    migrate(path)
    conn = libsql.connect(path)
    # Šema v1 već sadrži PATRLJAK `monthly_reports (id INTEGER PRIMARY KEY)`, pa
    # `CREATE TABLE IF NOT EXISTS` nad njom tiho ne uradi ništa. To je tačno ona
    # zamka zbog koje `verify_monthly_reports_schema` uopšte postoji — ovdje se
    # patrljak izričito uklanja da bi tabela dobila ugovorni oblik.
    conn.execute("DROP TABLE IF EXISTS monthly_reports")
    conn.execute(reporting_schema.MONTHLY_REPORTS_DDL)
    conn.execute(reporting_schema.MONTHLY_REPORTS_INDEX_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token-not-real")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    reporting_db.set_database(database)
    yield path
    reporting_db.wait_for_pending_writes()
    reporting_db.set_database(None)


@pytest.fixture
def student(db):
    student_id = reporting_db.get_database().get_or_create_student(
        PROVIDER_THINKIFIC_EMAIL, "learner@example.com")
    # Potvrdu razreda pise ISKLJUCIVO administratorska radnja (verzija 4).
    reporting_db.get_database().set_student_grade(student_id, 6)
    return student_id


# ===========================================================================
# 1) UGOVOR ČINJENICA
# ===========================================================================
def test_presented_and_answered_are_distinct_numbers():
    facts = report_facts.build_ai_facts(payload())
    practice = facts["matbot"]["practice"]
    assert practice["tasks_presented"] == 37
    assert practice["answers_total"] == 24
    assert practice["answers_total"] == practice["correct"] + practice["incorrect"]
    assert practice["tasks_presented"] != practice["answers_total"]
    assert practice["presented_not_answered"] == 13


def test_accuracy_is_none_when_nothing_was_answered():
    facts = report_facts.build_ai_facts(payload(matbot={
        "practice_tasks": 5, "practice_correct": 0, "practice_incorrect": 0,
        "practice_accuracy": None}))
    practice = facts["matbot"]["practice"]
    assert practice["answers_total"] == 0
    # NIKAD 0 % — to bi bila izmišljena mjera o učeniku koji nije odgovarao.
    assert practice["accuracy_percent"] is None


def test_missing_previous_month_is_marked_explicitly():
    facts = report_facts.build_ai_facts(payload())
    assert facts["thinkific"]["previous_available"] is False
    assert facts["thinkific"]["delta_percent_viewed"] is None


def test_previous_month_present_is_marked_available():
    facts = report_facts.build_ai_facts(payload(thinkific={
        "previous_percent_viewed": 5, "delta_percent_viewed": 7.0}))
    assert facts["thinkific"]["previous_available"] is True


def test_low_evidence_is_propagated_into_the_ai_contract():
    facts = report_facts.build_ai_facts(payload())
    by_name = {row["lesson_name"]: row
               for row in facts["matbot"]["lesson_evidence"]}
    assert by_name["Djeljivost sa 3"]["evidence_level"] == report_facts.EVIDENCE_LIMITED
    assert by_name["Djeljivost sa 3"]["low_evidence"] is True
    assert by_name["Razlomci"]["evidence_level"] == report_facts.EVIDENCE_STRONG
    assert by_name["Razlomci"]["low_evidence"] is False


def test_stronger_evidence_is_ranked_before_dramatic_one_off():
    """1/1 netačnih izgleda gore od 4/8, a znači manje. Redoslijed to poštuje."""
    facts = report_facts.build_ai_facts(payload())
    assert facts["matbot"]["lesson_evidence"][0]["lesson_name"] == "Razlomci"


def test_ai_facts_carry_no_pii_or_internal_identifiers():
    facts = report_facts.build_ai_facts(payload())
    blob = json.dumps(facts, ensure_ascii=False)
    assert "@" not in blob
    assert "student_id" not in blob
    assert "Šćepanović" not in blob and "Đžemal" not in blob
    assert "lesson_id" not in blob and "L-razlomci" not in blob
    assert "course_key" not in blob


def test_prompt_input_carries_no_pii():
    facts = report_facts.build_ai_facts(payload())
    text = report_prompt.build_input_text(facts)
    assert "@" not in text
    assert "Đžemal" not in text and "Šćepanović" not in text


def test_zero_progress_sections_are_dropped_from_the_parent_view():
    facts = report_facts.build_ai_facts(payload())
    names = [s["name"] for s in facts["thinkific"]["sections"]]
    assert names == ["SKUPOVI"]


def test_thinkific_only_learner_reports_no_matbot_activity():
    facts = report_facts.build_ai_facts(payload(matbot={
        "active_days": 0, "practice_tasks": 0, "practice_correct": 0,
        "practice_incorrect": 0, "practice_accuracy": None, "hints_used": 0,
        "full_solutions_shown": 0, "explain_count": 0, "quick_count": 0,
        "kontrolni_attempts": 0, "kontrolni_average": None,
        "kontrolni_correct": 0, "kontrolni_total": 0, "lesson_outcomes": []}))
    assert facts["matbot"]["any_activity"] is False


def test_matbot_only_learner_reports_thinkific_unavailable():
    facts = report_facts.build_ai_facts(payload(
        thinkific={"snapshot_missing": True}))
    assert facts["thinkific"]["available"] is False
    assert facts["thinkific"]["previous_available"] is False


# ===========================================================================
# 2) DOKAZNA POLITIKA
# ===========================================================================
@pytest.mark.parametrize("items,expected", [
    (0, report_facts.EVIDENCE_INSUFFICIENT),
    (1, report_facts.EVIDENCE_LIMITED),
    (2, report_facts.EVIDENCE_LIMITED),
    (3, report_facts.EVIDENCE_MODERATE),
    (5, report_facts.EVIDENCE_MODERATE),
    (6, report_facts.EVIDENCE_STRONG),
    (30, report_facts.EVIDENCE_STRONG),
])
def test_evidence_level_thresholds(items, expected):
    assert report_facts.evidence_level(items) == expected


def test_single_kontrolni_stays_limited_regardless_of_question_count():
    facts = report_facts.build_ai_facts(payload(matbot={
        "kontrolni_attempts": 1, "kontrolni_total": 20, "kontrolni_correct": 5}))
    assert facts["matbot"]["kontrolni"]["evidence_level"] == \
        report_facts.EVIDENCE_LIMITED


def test_overall_evidence_insufficient_when_everything_is_thin():
    facts = report_facts.build_ai_facts(payload(matbot={
        "kontrolni_attempts": 1, "kontrolni_total": 2, "kontrolni_correct": 1,
        "lesson_outcomes": [{"lesson_id": "x", "lesson_name": "Nešto",
                             "area_name": "Oblast", "difficulty": "standard",
                             "incorrect_items": 1, "evidence_items": 1}]}))
    assert facts["overall_evidence_sufficient"] is False


def test_overall_evidence_sufficient_with_repeated_observations():
    assert report_facts.build_ai_facts(payload())["overall_evidence_sufficient"] \
        is True


# ===========================================================================
# 3) IZLAZ MODELA
# ===========================================================================
def test_valid_narrative_is_accepted_and_costs_exactly_one_call():
    facts = report_facts.build_ai_facts(payload())
    llm = FakeReportLLM()
    narrative = parent_report.generate_narrative(facts, llm)
    assert llm.calls == 1
    assert narrative["summary"].startswith("Tokom mjeseca")


def test_prompt_rules_are_actually_sent():
    facts = report_facts.build_ai_facts(payload())
    llm = FakeReportLLM()
    parent_report.generate_narrative(facts, llm)
    sent = llm.last_instructions
    for rule in ("NIŠTA ne računaš", "previous_available", "evidence_level",
                 "Ne pretpostavljaj pol", "bosanskom", "strukturirani JSON"):
        assert rule in sent, rule


def test_missing_required_field_is_rejected():
    facts = report_facts.build_ai_facts(payload())
    broken = good_narrative()
    del broken["focus_areas"]
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, FakeReportLLM(output=broken))
    assert "missing_field" in caught.value.code


def test_non_dict_output_is_rejected():
    facts = report_facts.build_ai_facts(payload())
    with pytest.raises(parent_report.ReportGenerationError):
        parent_report.generate_narrative(facts, FakeReportLLM(output="ne-json"))


def test_empty_summary_is_rejected():
    facts = report_facts.build_ai_facts(payload())
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(
            facts, FakeReportLLM(output=good_narrative(summary="   ")))
    assert "summary_empty" in caught.value.code


def test_llm_error_fails_closed_without_a_fabricated_summary():
    from matbot.llm import LLMTimeout

    facts = report_facts.build_ai_facts(payload())
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(
            facts, FakeReportLLM(error=LLMTimeout("timeout")))
    assert "call_failed" in caught.value.code


def test_invented_number_is_rejected():
    facts = report_facts.build_ai_facts(payload())
    bad = good_narrative(summary="Tokom mjeseca tačnost je iznosila 91 posto.")
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))
    assert "unsupported_number" in caught.value.code


def test_supplied_number_is_allowed():
    facts = report_facts.build_ai_facts(payload())
    ok = good_narrative(summary="Odgovoreno je 24 zadatka, od toga 14 tačno.")
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


# ---------------------------------------------------------------------------
# CIFRA U NAZIVU LEKCIJE NIJE IZMIŠLJENA MJERA
#
# ŽIVI NALAZ (sintetički reporting smoke, kandidat c95826b): model je ispravno
# napisao „Djeljivost sa 3", a provjera brojeva je „3" iz NAZIVA pročitala kao
# izmišljen procenat i odbila cijeli izvještaj. Pogađa 11 od 513 stvarnih
# naziva — dakle upravo lekcije koje prompt najviše i želi imenovati.
#
# Ispravka NE dodaje cifre iz naziva u dopuštene mjere: to bi propustilo
# „Tačnost je 60 %" samo zato što u kurikulumu postoji naslov o uglu od 60°.
# Naziv se maskira kao POUZDAN RASPON TEKSTA, i to samo pri traženju brojeva.
# ---------------------------------------------------------------------------

def _facts_with_lesson(name, area="Djeljivost", items=8, wrong=4):
    return report_facts.build_ai_facts(payload(matbot={"lesson_outcomes": [
        {"lesson_id": "L-1", "lesson_name": name, "area_name": area,
         "difficulty": "standard", "incorrect_items": wrong,
         "evidence_items": items, "low_evidence": False}]}))


def test_exact_lesson_label_with_a_digit_is_accepted():
    facts = _facts_with_lesson("Djeljivost sa 3")
    assert 3.0 not in report_facts.allowed_numbers(facts)      # NE globalno
    ok = good_narrative(
        focus_areas=["Vrijedi dodatno uvježbati lekciju Djeljivost sa 3."])
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


def test_the_same_digit_is_still_rejected_as_a_measurement():
    """Isti broj, izvan naziva — i dalje izmišljena mjera."""
    facts = _facts_with_lesson("Djeljivost sa 3")
    bad = good_narrative(summary="Tačnost je 3%.")
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))
    assert "unsupported_number:3" in caught.value.code


def test_exact_degree_label_is_accepted():
    facts = _facts_with_lesson("Konstrukcije uglova 60°, 30°, 90° i 45°",
                               area="Uglovi")
    ok = good_narrative(next_month_recommendations=[
        "Preporučuje se dodatno uvježbati Konstrukcije uglova 60°, 30°, 90° i 45°."])
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


def test_a_degree_number_from_that_label_is_not_a_valid_percentage():
    """TVRDNJA KOJU OVAJ TEST ČUVA: naslov ne pretvara 60 u mjeru."""
    facts = _facts_with_lesson("Konstrukcije uglova 60°, 30°, 90° i 45°",
                               area="Uglovi")
    bad = good_narrative(summary="Tačnost je 60%.")
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))
    assert "unsupported_number:60" in caught.value.code


def test_exact_section_name_with_a_digit_is_accepted():
    facts = report_facts.build_ai_facts(payload(thinkific={"sections": [
        {"ordinal": 1, "section_name": "Skupovi N i N0",
         "current_progress_percent": 14, "previous_progress_percent": None,
         "delta_progress_percent": None}]}))
    ok = good_narrative(
        strengths=["Pregledana je sekcija Skupovi N i N0."])
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


def test_exact_area_name_with_a_digit_is_accepted():
    facts = _facts_with_lesson("Razlomci", area="Djeljivost sa 9")
    ok = good_narrative(focus_areas=["Vrijedi vježbati Djeljivost sa 9."])
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


def test_a_paraphrased_label_still_fails_closed():
    """Prepričan naziv nije TAČAN naziv — v1 svjesno pada zatvoreno."""
    facts = _facts_with_lesson("Djeljivost sa 3")
    bad = good_narrative(focus_areas=["Vrijedi uvježbati djeljivost brojem 3."])
    with pytest.raises(parent_report.ReportGenerationError):
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))


def test_only_digit_bearing_labels_are_trusted_spans():
    facts = _facts_with_lesson("Djeljivost sa 3")
    labels = report_facts.trusted_labels(facts)
    assert "Djeljivost sa 3" in labels
    # Naziv bez cifre nema šta da maskira, pa se i ne prenosi.
    assert all(any(ch.isdigit() for ch in label) for label in labels)


def test_masking_never_hides_a_trend_claim():
    """Maskiranje važi SAMO za brojeve; trend i dalje gleda pun tekst."""
    facts = _facts_with_lesson("Napredak u 3 koraka")
    assert facts["thinkific"]["previous_available"] is False
    bad = good_narrative(summary="Zabilježen je napredak u odnosu na prošli mjesec.")
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))
    assert "trend_without_baseline" in caught.value.code


def test_masking_never_hides_markup():
    facts = _facts_with_lesson("Djeljivost sa 3")
    bad = good_narrative(summary="Djeljivost sa 3 <b>podebljano</b>.")
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))
    assert "markup_or_internal" in caught.value.code


def test_masking_costs_no_extra_model_call():
    facts = _facts_with_lesson("Djeljivost sa 3")
    llm = FakeReportLLM(output=good_narrative(
        focus_areas=["Vrijedi uvježbati Djeljivost sa 3."]))
    parent_report.generate_narrative(facts, llm)
    assert llm.calls == 1


def test_trusted_labels_never_carry_learner_identity():
    """Zatvoren izvor: samo nazivi gradiva, nikad ime, e-mail ni šifra."""
    facts = _facts_with_lesson("Djeljivost sa 3")
    blob = " ".join(report_facts.trusted_labels(facts))
    for forbidden in ("L-1", "student_id", "@", "Đžemal", "Šćepanović"):
        assert forbidden not in blob


@pytest.mark.parametrize("title", [
    "Djeljivost sa 3",                                        # prost cijeli broj
    "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",   # niz brojeva
    "Konstrukcije uglova 60°, 30°, 90° i 45°",                # stepeni
    "Množenje i dijeljenje u skupu N0",                       # alfanumerički
    "Jednostavna kvadratna jednačina ax² + bx = 0",           # formula u naslovu
])
def test_real_curriculum_titles_are_accepted_verbatim(title):
    """Stvarni naslovi iz `data/topics.json`, doslovno upotrijebljeni."""
    facts = _facts_with_lesson(title)
    ok = good_narrative(focus_areas=["Vrijedi dodatno uvježbati %s." % title])
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


@pytest.mark.parametrize("title", [
    "Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25",
    "Konstrukcije uglova 60°, 30°, 90° i 45°",
])
def test_those_titles_do_not_license_invented_percentages(title):
    """Ni jedan broj iz naslova ne postaje dopuštena mjera."""
    facts = _facts_with_lesson(title)
    for invented in ("15", "45"):
        bad = good_narrative(summary="Tačnost je %s%%." % invented)
        with pytest.raises(parent_report.ReportGenerationError) as caught:
            parent_report.generate_narrative(facts, FakeReportLLM(output=bad))
        assert "unsupported_number" in caught.value.code


def test_the_prompt_asks_for_verbatim_lesson_names():
    assert "PREPIŠI naziv TAČNO" in report_prompt.SYSTEM_PROMPT
    assert report_prompt.REPORT_PROMPT_VERSION == "3d-2"


@pytest.mark.parametrize("phrase", [
    "Zabilježen je napredak u odnosu na prošli mjesec.",
    "Rezultati pokazuju porast tokom mjeseca.",
    "Primjetan je pad u aktivnosti.",
    "Došlo je do poboljšanja u radu.",
])
def test_trend_claim_without_baseline_is_rejected(phrase):
    facts = report_facts.build_ai_facts(payload())
    assert facts["thinkific"]["previous_available"] is False
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(
            facts, FakeReportLLM(output=good_narrative(summary=phrase)))
    assert "trend_without_baseline" in caught.value.code


def test_trend_language_allowed_when_baseline_exists():
    facts = report_facts.build_ai_facts(payload(thinkific={
        "previous_percent_viewed": 5, "delta_percent_viewed": 7.0,
        "previous_percent_completed": 0, "delta_percent_completed": 1.0}))
    ok = good_narrative(summary="Zabilježen je napredak u odnosu na prošli mjesec.")
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


def test_explicitly_saying_comparison_is_impossible_is_allowed():
    facts = report_facts.build_ai_facts(payload())
    ok = good_narrative(
        summary="Prethodni mjesec nije dostupan pa napredak nije moguće procijeniti.")
    assert parent_report.generate_narrative(facts, FakeReportLLM(output=ok))


@pytest.mark.parametrize("bad", [
    "<b>Odličan</b> mjesec za vježbu.",
    "Vidi **rezultate** u nastavku.",
    "Rezultat je dobar &amp; stabilan.",
])
def test_markup_in_narrative_is_rejected(bad):
    facts = report_facts.build_ai_facts(payload())
    with pytest.raises(parent_report.ReportGenerationError) as caught:
        parent_report.generate_narrative(
            facts, FakeReportLLM(output=good_narrative(summary=bad)))
    assert "markup_or_internal" in caught.value.code


def test_internal_vocabulary_is_rejected():
    facts = report_facts.build_ai_facts(payload())
    bad = good_narrative(summary="Za ovu lekciju je evidence_level nizak.")
    with pytest.raises(parent_report.ReportGenerationError):
        parent_report.generate_narrative(facts, FakeReportLLM(output=bad))


def test_oversized_fields_are_trimmed_not_stored_whole():
    from matbot import schema as output_schema

    facts = report_facts.build_ai_facts(payload())
    long_summary = "Tokom mjeseca zabilježena je aktivnost. " * 200
    narrative = parent_report.generate_narrative(
        facts, FakeReportLLM(output=good_narrative(summary=long_summary)))
    assert len(narrative["summary"]) <= output_schema.MAX_REPORT_SUMMARY_CHARS


def test_item_lists_are_capped():
    from matbot import schema as output_schema

    facts = report_facts.build_ai_facts(payload())
    many = ["Redovnije vježbanje kroz sedmicu."] * 12
    narrative = parent_report.generate_narrative(
        facts, FakeReportLLM(output=good_narrative(
            next_month_recommendations=many)))
    assert len(narrative["next_month_recommendations"]) == \
        output_schema.MAX_REPORT_ITEMS


def test_schema_forbids_unknown_fields():
    import pydantic

    from matbot.schema import ReportNarrativeOutput

    with pytest.raises(pydantic.ValidationError):
        ReportNarrativeOutput(summary="x", strengths=[], focus_areas=[],
                              next_month_recommendations=[], grade=6)


# ===========================================================================
# 4) TRAJNOST
# ===========================================================================
def test_generated_draft_is_saved_and_reloaded(db, student):
    facts = report_facts.build_ai_facts(payload())
    narrative = parent_report.generate_narrative(facts, FakeReportLLM())
    snapshot = parent_report.metrics_snapshot(
        facts, model="test-model", prompt_version=report_prompt.REPORT_PROMPT_VERSION)
    parent_report.save_narrative(student, "2026-08", narrative, snapshot)

    saved = parent_report.load_saved(student, "2026-08")
    assert saved["narrative"]["summary"] == narrative["summary"]
    assert saved["status"] == "draft"
    assert saved["snapshot"]["generated_by"]["prompt_version"] == \
        report_prompt.REPORT_PROMPT_VERSION
    assert saved["snapshot"]["facts"]["matbot"]["practice"]["tasks_presented"] == 37


def test_same_student_and_month_never_duplicates(db, student):
    facts = report_facts.build_ai_facts(payload())
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version="v")
    for _ in range(3):
        parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot)
    assert rows(db, "SELECT COUNT(*) FROM monthly_reports")[0][0] == 1


def test_admin_edits_persist_and_survive_reopen(db, student):
    facts = report_facts.build_ai_facts(payload())
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version="v")
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot)

    edited = good_narrative(summary="Ručno uređen sažetak instruktora.")
    parent_report.save_edits(student, "2026-08", edited, "Komentar instruktora.")

    saved = parent_report.load_saved(student, "2026-08")
    assert saved["narrative"]["summary"] == "Ručno uređen sažetak instruktora."
    assert saved["instructor_comment"] == "Komentar instruktora."


def test_regeneration_replaces_ai_text_but_keeps_instructor_comment(db, student):
    facts = report_facts.build_ai_facts(payload())
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version="v")
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot)
    parent_report.save_edits(student, "2026-08", good_narrative(),
                             "Komentar koji mora preživjeti.")

    fresh = good_narrative(summary="Novi AI sažetak nakon ponovnog generisanja.")
    parent_report.save_narrative(student, "2026-08", fresh, snapshot)

    saved = parent_report.load_saved(student, "2026-08")
    assert saved["narrative"]["summary"].startswith("Novi AI sažetak")
    assert saved["instructor_comment"] == "Komentar koji mora preživjeti."


def test_saved_report_does_not_mutate_when_source_data_changes_later(db, student):
    """Snimak je razlog zašto `metrics_json` postoji (Dio 14)."""
    facts = report_facts.build_ai_facts(payload())
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version="v")
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot)

    # Izvorni podaci se kasnije promijene — sačuvani izvještaj se NE mijenja.
    later = report_facts.build_ai_facts(payload(matbot={"practice_tasks": 999}))
    assert later["matbot"]["practice"]["tasks_presented"] == 999

    saved = parent_report.load_saved(student, "2026-08")
    assert saved["snapshot"]["facts"]["matbot"]["practice"]["tasks_presented"] == 37


def test_metrics_json_contains_no_email_or_identifiers(db, student):
    facts = report_facts.build_ai_facts(payload())
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version="v")
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot)
    stored = rows(db, "SELECT metrics_json FROM monthly_reports")[0][0]
    assert "@" not in stored
    assert "Đžemal" not in stored and "Šćepanović" not in stored
    assert "lesson_id" not in stored


def test_failed_generation_leaves_an_existing_draft_untouched(db, student):
    from matbot.llm import LLMTimeout

    facts = report_facts.build_ai_facts(payload())
    snapshot = parent_report.metrics_snapshot(
        facts, model="m", prompt_version="v")
    parent_report.save_narrative(student, "2026-08", good_narrative(), snapshot)

    with pytest.raises(parent_report.ReportGenerationError):
        parent_report.generate_narrative(
            facts, FakeReportLLM(error=LLMTimeout("timeout")))

    saved = parent_report.load_saved(student, "2026-08")
    assert saved["narrative"]["summary"].startswith("Tokom mjeseca")


def test_write_is_refused_when_monthly_reports_is_unusable(tmp_path, monkeypatch):
    """Tabelu ovaj repo nikad nije kreirao — nepoznat oblik pada ZATVORENO."""
    path = str(tmp_path / "r.db")
    build_v1(path)
    migrate(path)
    conn = libsql.connect(path)
    # Šema v1 već NOSI ovakav patrljak — ovdje se samo potvrđuje da je zatečen
    # oblik neupotrebljiv, umjesto da se pravi novi.
    assert reporting_schema.verify_monthly_reports_schema(conn)
    conn.close()
    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://test.invalid")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "t")
    database = reporting_db.ReportingDatabase(
        connect_factory=lambda: libsql.connect(path, timeout=10.0,
                                               _check_same_thread=False))
    with pytest.raises(reporting_db.ReportingUnavailable) as caught:
        database.save_monthly_report(student_id=1, report_month="2026-08",
                                     ai_summary="{}")
    assert "monthly_reports_unusable" in caught.value.code


def test_schema_verifier_accepts_the_contract_table(db):
    conn = libsql.connect(db)
    try:
        assert reporting_schema.verify_monthly_reports_schema(conn) == []
    finally:
        conn.close()


# ===========================================================================
# 5) PDF
# ===========================================================================
def render(narrative=None, comment="", facts=None, label="Đžemal Šćepanović"):
    facts = facts if facts is not None else report_facts.build_ai_facts(payload())
    return report_pdf.render_report_pdf(
        facts, narrative or good_narrative(), comment, label)


def text_of(data):
    reader = pypdf.PdfReader(io.BytesIO(data))
    return len(reader.pages), "\n".join(p.extract_text() for p in reader.pages)


def test_pdf_is_one_page_for_a_normal_report():
    pages, _ = text_of(render())
    assert pages == 1


def test_pdf_contains_name_grade_and_month():
    _, text = text_of(render())
    assert "Đžemal Šćepanović" in text
    assert "6. razred" in text
    assert "august 2026." in text


def test_pdf_contains_deterministic_metrics_with_correct_terminology():
    """Faza 3D: roditelju ide SAŽET MAT-BOT odjeljak.

    Prikazani zadaci, nagovještaji i brojači Objašnjenja/Rezultata namjerno su
    izbačeni iz izvještaja (Dio 25) — načini rada, ne mjere učinka."""
    _, text = text_of(render())
    assert "Odgovorenih zadataka" in text and "24" in text
    assert "58,3%" in text
    assert "riješenih zadataka" not in text
    # Ono što je uklonjeno mora i OSTATI uklonjeno.
    assert "Zadataka prikazano" not in text
    assert "37" not in text


BOSNIAN_LETTERS = "čćžšđĐŠČĆŽ"


def test_pdf_renders_bosnian_diacritics():
    """Sva bosanska slova, i mala i VELIKA, u tekstu dokumenta."""
    _, text = text_of(render(comment="ČĆŽŠĐ — Čitanje, Ćirilica, Žuto, Šuma, Đak, rođendan, đačko."))
    for char in BOSNIAN_LETTERS:
        assert char in text, char


def test_report_font_actually_has_every_bosnian_glyph():
    """Izvučen tekst NIJE dokaz da se slovo vidjelo.

    Prvo mjerenje ovog svojstva bilo je pogrešno: provjeravalo je
    `ord(znak) in charToGlyph`, a taj rječnik sadrži ključ i kad pokazuje na
    glif 0 (`.notdef`) — prazan kvadratić. `pypdf` u oba slučaja vrati isti
    znak, pa je test prolazio nad dokumentom u kojem je slovo bilo kvadratić
    (nađeno vizuelnom kontrolom: „Đžemal" se iscrtavao kao „□žemal").
    Zato se ovdje traži STVARAN identifikator glifa, ne postojanje ključa."""
    for font_name in (report_pdf._FONT_REGULAR, report_pdf._FONT_BOLD):
        missing = report_pdf.missing_glyphs(font_name)
        # NIJEDNO slovo ne smije nedostajati — ni u običnom ni u podebljanom
        # rezu. Podebljani je bio jednako pokvaren, a nosi zaglavlja.
        assert missing == set(), \
            "%s: nedostaju glifovi %s" % (font_name, sorted(missing))


def test_required_glyphs_cover_bosnian_alphabet_and_plain_latin():
    """Skup koji se mjeri mora sadržavati ono zbog čega mjerenje postoji.

    Bez ovoga bi neko mogao suziti `REQUIRED_GLYPHS` dok test i dalje prolazi —
    a upravo je preusko mjerenje propustilo veliko `Đ` u produkcijski PDF."""
    for char in BOSNIAN_LETTERS:
        assert char in report_pdf.REQUIRED_GLYPHS, char
    # Kontrola zdravlja mjerenja: obična latinica i cifre.
    for char in "azAZ09":
        assert char in report_pdf.REQUIRED_GLYPHS, char


def test_uppercase_dje_specifically_has_a_real_glyph():
    """Regresija na tačno onaj znak koji je pao: veliko Đ (U+0110).

    Vera je za njega vraćala glif 0, pa se u PDF-u iscrtavao prazan kvadratić
    („Đžemal" → „□žemal"). Ime se ne prepisuje u „Dj" — traži se font."""
    for font_name in (report_pdf._FONT_REGULAR, report_pdf._FONT_BOLD):
        assert report_pdf.missing_glyphs(font_name, "Đ") == set()
        assert report_pdf.missing_glyphs(font_name, "đ") == set()


def test_missing_glyph_probe_can_actually_fail():
    """Mjerenje koje ne umije pasti ne dokazuje ništa.

    Vera je ovdje KORISNA kao negativna kontrola: za nju se pouzdano zna da
    nema U+0110, pa ako je i ona „potpuna", pokvareno je mjerenje, a ne font."""
    import os

    import reportlab
    from reportlab.pdfbase.ttfonts import TTFont

    face = TTFont("Probe", os.path.join(os.path.dirname(reportlab.__file__),
                                        "fonts", "Vera.ttf")).face
    assert face.charToGlyph.get(ord("Đ"), 0) == 0
    # Isti rječnik ipak SADRŽI ključ — dokaz da je stara provjera bila slijepa.
    assert ord("Đ") in face.charToGlyph


def test_fonts_are_vendored_in_the_repository():
    """Font mora doći IZ REPOA, ne iz sistema ni iz reportlaba.

    Put se izvodi iz `__file__`, pa PDF ne zavisi od toga odakle su gunicorn
    ili pytest pokrenuti."""
    import os

    for font_name in (report_pdf._FONT_REGULAR, report_pdf._FONT_BOLD):
        path = report_pdf.font_path(font_name)
        assert os.path.isabs(path)
        assert os.path.isfile(path)
        assert "site-packages" not in path.replace("\\", "/")
    package = os.path.dirname(os.path.abspath(report_pdf.__file__))
    assert report_pdf.FONT_DIR == os.path.join(package, "assets", "fonts")
    # Licenca mora putovati uz binarne fontove — tako traži i sama licenca.
    assert os.path.isfile(os.path.join(report_pdf.FONT_DIR, "LICENSE-DejaVu.txt"))


def test_missing_font_asset_fails_visibly_instead_of_falling_back(monkeypatch):
    """Bez fonta se pravi VIDLJIVA greška, nikad tihi povratak na Veru.

    Tihi fallback bi vratio kvar s velikim Đ, ali bez ijednog traga."""
    import os

    monkeypatch.setattr(report_pdf, "_fonts_ready", False)
    monkeypatch.setattr(report_pdf, "FONT_DIR", os.path.join("nepostojeci", "put"))
    with pytest.raises(report_pdf.ReportFontMissing):
        report_pdf._ensure_fonts()


def test_pdf_states_missing_baseline_instead_of_inventing_a_trend():
    """Bez prošlog mjeseca sekcije se samo NABRAJAJU, bez tvrdnje o napretku."""
    _, text = text_of(render())
    assert "Evidentirani sadržaji na platformi:" in text
    for forbidden in ("Promjena", "p.p.", "napredak"):
        assert forbidden not in text


def test_pdf_shows_no_accuracy_when_nothing_answered():
    facts = report_facts.build_ai_facts(payload(matbot={
        "practice_tasks": 5, "practice_correct": 0, "practice_incorrect": 0,
        "practice_accuracy": None}))
    _, text = text_of(render(facts=facts))
    # Bez odgovorenih zadataka NEMA tačnosti — nikad 0 %.
    assert "nema podatka" in text
    assert "0%" not in text


def test_pdf_contains_edited_narrative_and_instructor_comment():
    narrative = good_narrative(summary="Ručno uređena rečenica instruktora.")
    _, text = text_of(render(narrative, comment="Komentar instruktora ovdje."))
    assert "Ručno uređena rečenica instruktora." in text
    assert "KOMENTAR INSTRUKTORA" in text
    assert "Komentar instruktora ovdje." in text


def test_pdf_omits_instructor_section_when_comment_is_blank():
    _, text = text_of(render(comment=""))
    assert "KOMENTAR INSTRUKTORA" not in text


def test_pdf_contains_no_email_or_internal_identifiers():
    _, text = text_of(render())
    assert "@" not in text
    for token in ("student_id", "evidence_level", "low_evidence", "metrics_json",
                  "lesson_id", "course_key", "snapshot_missing", "insufficient"):
        assert token not in text, token


def test_pdf_reports_absent_matbot_activity_explicitly():
    facts = report_facts.build_ai_facts(payload(matbot={
        "active_days": 0, "practice_tasks": 0, "practice_correct": 0,
        "practice_incorrect": 0, "practice_accuracy": None, "hints_used": 0,
        "full_solutions_shown": 0, "explain_count": 0, "quick_count": 0,
        "kontrolni_attempts": 0, "kontrolni_average": None,
        "kontrolni_correct": 0, "kontrolni_total": 0, "lesson_outcomes": []}))
    _, text = text_of(render(facts=facts))
    assert "Nema zabilježene MAT-BOT aktivnosti u ovom mjesecu." in text


def test_pdf_reports_absent_thinkific_data_explicitly():
    facts = report_facts.build_ai_facts(payload(
        thinkific={"snapshot_missing": True}))
    _, text = text_of(render(facts=facts))
    assert "Thinkific podaci nisu dostupni za ovaj mjesec." in text


def test_large_valid_report_never_clips_and_stays_within_two_pages():
    from matbot import schema as output_schema

    long_item = "Redovnije i kraće vježbanje kroz sedmicu uz pregled gradiva. " * 4
    narrative = {
        "summary": "Tokom mjeseca zabilježena je aktivnost. " * 25,
        "strengths": [long_item] * output_schema.MAX_REPORT_ITEMS,
        "focus_areas": [long_item] * output_schema.MAX_REPORT_ITEMS,
        "next_month_recommendations": [long_item] * output_schema.MAX_REPORT_ITEMS,
    }
    narrative = parent_report.normalize_narrative(narrative)
    pages, text = text_of(render(narrative, comment="Komentar. " * 40))
    assert pages <= report_pdf.MAX_PAGES
    # Posljednji odjeljak mora STVARNO biti u dokumentu — ne odsječen.
    assert "KOMENTAR INSTRUKTORA" in text
    assert "PREPORUKA ZA NAREDNI MJESEC" in text


def test_page_number_appears_only_when_there_is_more_than_one_page():
    _, single = text_of(render())
    assert "Strana" not in single


def test_pdf_is_deterministic_for_the_same_saved_draft():
    first, second = render(), render()
    # Vremenski žig PDF-a se razlikuje, pa se poredi izvučeni TEKST.
    assert text_of(first)[1] == text_of(second)[1]


def test_pdf_filename_is_ascii_and_carries_no_identifier():
    name = report_pdf.pdf_filename("Đžemal Šćepanović-Čizmić", "2026-08")
    assert name.isascii() and name.endswith(".pdf")
    assert "@" not in name and "7" not in name.replace("2026", "")


def test_pdf_escapes_markup_typed_by_the_administrator():
    """Administrator smije otkucati bilo šta — predložak to ne smije izvršiti."""
    _, text = text_of(render(comment="Vidi <b>ovo</b> & ono."))
    assert "Vidi <b>ovo</b> & ono." in text


# ===========================================================================
# FOKUS ZA RODITELJA — kratak i potkrijepljen, ne dijagnostički spisak
# ===========================================================================
def _outcome(name, area, items, wrong):
    return {"lesson_id": "L-" + name, "lesson_name": name, "area_name": area,
            "difficulty": "standard", "incorrect_items": wrong,
            "evidence_items": items}


def _plan(outcomes):
    return report_facts.build_ai_facts(
        payload(matbot={"lesson_outcomes": outcomes}))["matbot"]["focus_plan"]


def test_focus_names_at_most_three_lessons():
    plan = _plan([_outcome("A", "O1", 8, 4), _outcome("B", "O2", 8, 4),
                  _outcome("C", "O3", 8, 4), _outcome("D", "O4", 8, 4),
                  _outcome("E", "O5", 8, 4)])
    assert len(plan["named_lessons"]) <= report_facts.MAX_NAMED_LESSONS == 3
    assert plan["max_focus_bullets"] == 3


def test_strong_and_moderate_outrank_a_single_wrong_item():
    """1/1 netačnih izgleda dramatično, a znači manje od 4/8."""
    plan = _plan([_outcome("Jednopitanje", "Oblast", 1, 1),
                  _outcome("Razlomci", "Razlomci", 8, 4)])
    assert plan["named_lessons"] == ["Razlomci"]
    assert "Jednopitanje" not in plan["named_lessons"]


def test_repeated_limited_signals_group_by_area():
    plan = _plan([_outcome("L1", "Geometrija", 1, 1),
                  _outcome("L2", "Geometrija", 2, 1),
                  _outcome("L3", "Geometrija", 1, 1)])
    assert plan["grouped_areas"] == [
        {"area_name": "Geometrija", "lesson_count": 3,
         "evidence_level": report_facts.EVIDENCE_LIMITED}]
    # Nijedna od tri se ne imenuje — signal je o OBLASTI.
    assert plan["named_lessons"] == []


def test_limited_only_month_prefers_two_bullets():
    plan = _plan([_outcome("L1", "Geometrija", 1, 1),
                  _outcome("L2", "Geometrija", 1, 1)])
    assert plan["max_focus_bullets"] == report_facts.MAX_FOCUS_BULLETS_LIMITED == 2


def test_an_isolated_limited_signal_is_named_only_when_nothing_stronger_exists():
    alone = _plan([_outcome("Samo ovo", "Oblast", 1, 1)])
    assert alone["named_lessons"] == ["Samo ovo"]
    withstronger = _plan([_outcome("Samo ovo", "Oblast", 1, 1),
                          _outcome("Razlomci", "Razlomci", 8, 4)])
    assert withstronger["named_lessons"] == ["Razlomci"]


def test_isolated_limited_signals_never_become_a_long_list():
    plan = _plan([_outcome("L1", "O1", 1, 1), _outcome("L2", "O2", 1, 1),
                  _outcome("L3", "O3", 1, 1), _outcome("L4", "O4", 1, 1)])
    assert len(plan["named_lessons"]) <= 1
    assert plan["max_focus_bullets"] == 2


def test_focus_plan_never_invents_a_signal():
    plan = _plan([])
    assert plan["named_lessons"] == []
    assert plan["grouped_areas"] == []


def test_full_evidence_still_reaches_the_admin_contract():
    """Plan je za roditelja; puni dokaz ostaje u činjenicama i u adminu."""
    facts = report_facts.build_ai_facts(payload())
    names = [row["lesson_name"] for row in facts["matbot"]["lesson_evidence"]]
    assert "Djeljivost sa 3" in names and "Razlomci" in names
    by_name = {row["lesson_name"]: row for row in facts["matbot"]["lesson_evidence"]}
    assert by_name["Djeljivost sa 3"]["evidence_level"] == \
        report_facts.EVIDENCE_LIMITED


def test_the_prompt_binds_naming_to_the_focus_plan():
    prompt = report_prompt.SYSTEM_PROMPT
    assert "focus_plan.named_lessons" in prompt
    assert "focus_plan.max_focus_bullets" in prompt
    assert "focus_plan.grouped_areas" in prompt


def test_focus_plan_costs_no_extra_model_call():
    facts = report_facts.build_ai_facts(payload())
    llm = FakeReportLLM()
    parent_report.generate_narrative(facts, llm)
    assert llm.calls == 1


def test_focus_plan_carries_no_identifiers():
    facts = report_facts.build_ai_facts(payload())
    blob = json.dumps(facts["matbot"]["focus_plan"], ensure_ascii=False)
    for forbidden in ("lesson_id", "L-razlomci", "student_id", "@"):
        assert forbidden not in blob


# ===========================================================================
# NASLOV POZITIVNOG ODJELJKA
# ===========================================================================
def test_pdf_uses_the_positive_habits_heading():
    _, text = text_of(render())
    assert "POZITIVNE NAVIKE U RADU" in text


def test_the_old_judgemental_heading_is_gone_from_the_parent_pdf():
    """„ŠTA IDE DOBRO" je zvučalo kao ocjena djeteta, ne izvještaj o radu."""
    _, text = text_of(render())
    assert "ŠTA IDE DOBRO" not in text


def test_month_label_is_bosnian():
    """Malo slovo i tačka iza godine — „August 2026" je bio engleski oblik."""
    assert report_pdf.month_label("2026-08") == "august 2026."
    assert report_pdf.month_label("2026-01") == "januar 2026."


@pytest.mark.parametrize("value, expected", [
    ("2026-01", "januar 2026."), ("2026-02", "februar 2026."),
    ("2026-03", "mart 2026."), ("2026-04", "april 2026."),
    ("2026-05", "maj 2026."), ("2026-06", "juni 2026."),
    ("2026-07", "juli 2026."), ("2026-08", "august 2026."),
    ("2026-09", "septembar 2026."), ("2026-10", "oktobar 2026."),
    ("2026-11", "novembar 2026."), ("2026-12", "decembar 2026."),
])
def test_every_bosnian_month_name(value, expected):
    assert report_pdf.month_label(value) == expected


@pytest.mark.parametrize("bad", ["2026-00", "2026-13", "2026", "", None,
                                 "kolovoz", "2026-xx"])
def test_month_label_falls_back_instead_of_guessing(bad):
    """`MONTH_NAMES[0 - 1]` bi tiho vratio „decembar" — zato provjera prije."""
    assert report_pdf.month_label(bad) == (bad or "")


def test_month_label_does_not_depend_on_os_locale(monkeypatch):
    """Imena su ugrađena; kontejner bez `bs_BA` ne smije ispisati engleski."""
    import locale

    monkeypatch.setattr(locale, "setlocale", lambda *a, **k: None)
    assert report_pdf.month_label("2026-08") == "august 2026."


def test_canonical_report_month_is_never_mutated():
    """Prikaz je prikaz; spremljena i poređena vrijednost ostaje YYYY-MM."""
    facts = report_facts.build_ai_facts(payload())
    assert facts["report_month"] == "2026-08"
    # Naziv fajla nosi kanonski oblik, ne lokalizovani.
    assert "2026-08" in report_pdf.pdf_filename("Neko Neko", "2026-08")
    assert "august" not in report_pdf.pdf_filename("Neko Neko", "2026-08")


# ===========================================================================
# Jezik i tumačenje
# ===========================================================================
def test_prompt_forbids_gender_assumption_and_thinkific_mastery():
    prompt = report_prompt.SYSTEM_PROMPT
    assert "Ne pretpostavljaj pol" in prompt
    assert "NIJE znanje" in prompt
    assert "a ne slabost" in prompt
    assert "medicinske ni psihološke" in prompt


def test_validation_flags_are_internal_only():
    problems = report_validation.validate_narrative(
        {"summary": "Tačnost je 91 posto.", "strengths": [], "focus_areas": [],
         "next_month_recommendations": []},
        report_facts.build_ai_facts(payload()))
    assert problems and all(":" in p or p.startswith("report_") for p in problems)
