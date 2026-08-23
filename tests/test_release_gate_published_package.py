# -*- coding: utf-8 -*-
"""Kapija potpis poredi sa STVARNO OBJAVLJENIM paketom (release-gate instrumentacija).

ŽIVI NALAZ (mandatorna kapija, scenario `same_level_new`, lekcija o pojmu
razlomka): recenzentov zamjenski paket je ODBIJEN zbog pokvarenog zapisa
(`unknown_mathjax_command_outside_math:u`), Practice je izvršio dokumentovani
`fast_rotation_fallback` i objavio VEĆ VALIDAN tutorski nacrt, a sesija je
ispravno commitovala potpis tog nacrta.

Kapija je ipak birala „posljednji neprazan recenzentov paket" kao konačan
(`corrected_task or reviewer_final_task or tutor_task`) i prijavila LAŽAN
Class A pad `committed_signature_does_not_match_final_package`.

PROIZVOD JE BIO ISPRAVAN: `_publish_task` izvodi potpis iz `final.new_task` —
istog objekta koji objavljuje — pa commitovani potpis po konstrukciji odgovara
objavljenom paketu. Ovi testovi zato NE mijenjaju Practice, nego čuvaju da
kapija bira konačan paket iz NEZAVISNOG dokaza: teksta koji je stvarno objavljen.

Tvrdnja se NE slabi: dva negativna testa dokazuju da prava nedosljednost
stanja i dalje obara turn.
"""
import pytest

import scratchpad.run_difficulty_canary as canary
import tests.test_contradiction_solution_gate as fixtures
from matbot.tutor import lesson_context

GRADE, TOPIC = 6, "6-04-001"
TUTOR_TEXT = "Koji razlomak ima brojnik $4$ i nazivnik $9$?"
REVIEWER_TEXT = "Koji od sljedećih primjera ispravno pokazuje koji član razlomka je brojnik?"


class _Outputs:
    """Samo ono što helper čita s LLM adaptera."""

    def __init__(self, tutor_draft, reviewer_final):
        self.last_tutor_output = tutor_draft
        self.last_reviewer_output = reviewer_final


def _packages(decision):
    context = lesson_context.build(GRADE, TOPIC)
    draft_task = fixtures.task(
        context, solution=fixtures.DECLARED_FALSE_SOLUTION, signature="draft"
    ).model_copy(update={"text": TUTOR_TEXT})
    final_task = fixtures.task(
        context, solution=fixtures.DECLARED_FALSE_SOLUTION, signature="reviewed"
    ).model_copy(update={"text": REVIEWER_TEXT})
    draft = fixtures.TutorDraft(
        intent="generate_task", reply="Evo sljedećeg zadatka.",
        lesson_focus="izabrana lekcija", new_task=draft_task)
    reviewer = fixtures.ReviewerFinal(
        decision=decision, checks=fixtures.checks(),
        final=draft.model_copy(update={"new_task": final_task}),
        reviewed_difficulty_evidence=final_task.difficulty_evidence)
    return context, draft_task, final_task, _Outputs(draft, reviewer)


def _observe(decision, *, published, committed=None, llm_override=...):
    """Pokreni STVARNI helper kapije nad zadatim ishodom objave."""
    context, draft_task, final_task, llm = _packages(decision)
    if llm_override is not ...:
        llm = llm_override
    committed = published if committed is None else committed
    result = canary.TurnResult(
        scenario="same_level_new", lesson_id=TOPIC, lesson_title=context.title,
        path="non_contract", grade=GRADE, request_type="")
    result.published = True
    result.target_level = 1
    result.published_task_text = published.text
    after_session = {
        "current_task": published.text,
        "current_task_signature": {
            "lesson_id": TOPIC,
            "structured_signature": committed.task_signature.canonical_json(),
            "structured_signature_hash": committed.task_signature.digest(),
        },
        "current_options": [], "correct_option_id": "a",
    }
    canary._record_answer_metadata(
        result, {"status": "ready", "next_state": {"task": {"options": []}}},
        after_session, llm)
    return result, draft_task, final_task


# ---------------------------------------------------------------------------
# RUTE OBJAVE — kapija mora pratiti ono što je učenik STVARNO dobio
# ---------------------------------------------------------------------------

def test_fast_rotation_fallback_compares_against_the_published_draft():
    """ŽIVI PAD: recenzentov paket odbijen, objavljen nacrt."""
    _context, draft_task, _final, _llm = _packages("correct")
    result, _d, _f = _observe("correct", published=draft_task)
    assert result.committed_task_signature_matches_final is True
    assert result.published_structured_package_source == "tutor_task"


def test_reviewer_replacement_compares_against_the_reviewer_package():
    _context, _draft, final_task, _llm = _packages("correct")
    result, _d, _f = _observe("correct", published=final_task)
    assert result.committed_task_signature_matches_final is True
    assert result.published_structured_package_source == "reviewer_final_task"


def test_ignored_approve_echo_compares_against_the_published_draft():
    """Na `approve` proizvod eho IGNORIŠE i objavljuje nacrt."""
    _context, draft_task, _f, _llm = _packages("approve")
    result, _d, _fi = _observe("approve", published=draft_task)
    assert result.committed_task_signature_matches_final is True
    assert result.published_structured_package_source == "tutor_task"


def test_deterministic_publication_has_no_model_package_to_compare():
    """Server-generisan paket: modelsko poređenje potpisa se ne izmišlja."""
    _context, draft_task, _f, _llm = _packages("correct")
    result, _d, _fi = _observe("correct", published=draft_task,
                               llm_override=_Outputs(None, None))
    assert result.committed_task_signature_matches_final is None
    assert result.published_structured_package_source is None


# ---------------------------------------------------------------------------
# TVRDNJA SE NE SLABI — prava nedosljednost i dalje pada
# ---------------------------------------------------------------------------

def test_real_mismatch_still_fails_when_draft_was_published():
    _context, draft_task, final_task, _llm = _packages("correct")
    result, _d, _f = _observe("correct", published=draft_task, committed=final_task)
    assert result.committed_task_signature_matches_final is False


def test_real_mismatch_still_fails_when_reviewer_package_was_published():
    _context, draft_task, final_task, _llm = _packages("correct")
    result, _d, _f = _observe("correct", published=final_task, committed=draft_task)
    assert result.committed_task_signature_matches_final is False


def test_gate_still_raises_the_class_a_error_on_a_real_mismatch():
    """Kod greške ostaje netaknut — mijenja se samo ŠTA se poredi."""
    import tools.run_live_release_gate as gate
    assert "committed_signature_does_not_match_final_package" in \
        open(gate.__file__, encoding="utf-8").read()


# ---------------------------------------------------------------------------
# NORMALIZACIJA VIDLJIVOG TEKSTA — koristi PROIZVODNU implementaciju
# ---------------------------------------------------------------------------

def test_visible_text_uses_the_product_normalizer():
    """Objava normalizuje tekst (terminologija), pa i poređenje mora."""
    assert canary._visible_task_text("  Koji   razlomak  ima brojnik $4$?  ") == \
        canary._visible_task_text("Koji razlomak ima brojnik $4$?")


def test_no_candidate_match_returns_none_rather_than_guessing():
    _context, draft_task, final_task, _llm = _packages("correct")
    assert canary._package_actually_published("neki sasvim drugi zadatak",
                                              (draft_task, final_task)) is None
    assert canary._package_actually_published("", (draft_task, final_task)) is None


def test_published_source_field_is_separate_from_last_attempted_source():
    """`final_structured_package_source` zadržava staro značenje."""
    _context, draft_task, _f, _llm = _packages("correct")
    result, _d, _fi = _observe("correct", published=draft_task)
    assert result.final_structured_package_source == "reviewer_final_task"
    assert result.published_structured_package_source == "tutor_task"
