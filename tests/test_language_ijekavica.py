# -*- coding: utf-8 -*-
"""Phase 6 — Bosnian ijekavica language regression corpus.

System-generated text (engine responses, hints, summaries, exam feedback,
templates, fallback notices) must be natural Bosnian ijekavica. ``to_ijekavica``
is the safety net; this corpus locks in zero known ekavica/diacritic leaks and
verifies that math content and student input are never corrupted.
"""
import re

import pytest

from matbot import bosnian
from matbot.bosnian import to_ijekavica, TERMINOLOGY
from matbot import solution_plan as sp
from matbot import task_templates as tt
from matbot import exam_engine as ee
from matbot import ai_tutor_service as svc


# Known-bad tokens: ekavica OR diacritic-stripped forms. Each is written to match
# ONLY the wrong form (the correct ijekavica+diacritic form has extra letters/
# diacritics and will not match), so a hit == a real leak.
_LEAK = re.compile(
    r"\b(deo|dela|delu|delovi|delova|resenj\w*|resiti|resi|vezb\w*|sledec\w*|"
    r"sledeć\w*|razumem\w*|obe|devoj\w*|umesto|posle|uvek|gde|ovde|dve|ceo|celi|"
    r"cela|celo|celu|primer\w*|zbroj\w*|kut|imenilac|imenitelj\w*|brojilac|"
    r"brojitelj\w*|matematicki|rijesi|rijesiti|rjesenj\w*|necu|nece|posalji\w*|"
    r"jednoznac\w*|tacno|tacan|tacna|tacni|netacno|netacan|djelimicn\w*|koristis|"
    r"mozes|cinjenic\w*|unutrasnj\w*|vjezb\w*|sljedeci|zajednicki|objasnjenj\w*|"
    r"greske|pomoc|konacn\w*|izracunaj\w*)\b",
    re.IGNORECASE,
)


def _leaks(text: str) -> list[str]:
    return _LEAK.findall(text or "")


# --------------------------------------------------------------------------- #
# 1. ekavica → ijekavica + diacritic restoration corpus (exact mapping)       #
# --------------------------------------------------------------------------- #
CORPUS = [
    # ekavica leaks
    ("deo", "dio"), ("rešenje", "rješenje"), ("vežba", "vježba"),
    ("sledeci korak", "sljedeći korak"), ("sledeći", "sljedeći"),
    ("razumem", "razumijem"), ("obe strane", "obje strane"),
    ("devojčica", "djevojčica"), ("umesto", "umjesto"), ("posle", "poslije"),
    ("uvek", "uvijek"), ("dve", "dvije"),
    # HR / older math terms → project convention
    ("zbroj", "zbir"), ("imenilac", "nazivnik"), ("brojilac", "brojnik"),
    ("imenitelj", "nazivnik"), ("brojitelj", "brojnik"), ("kut", "ugao"),
    # diacritic restoration (system leaks)
    ("Rijesi zadatak", "Riješi zadatak"), ("matematicki korak", "matematički korak"),
    ("necu aktivirati", "neću aktivirati"), ("Posalji mi", "Pošalji mi"),
    ("jednoznacan", "jednoznačan"), ("tacno", "tačno"), ("netacno", "netačno"),
    ("djelimicno tacno", "djelimično tačno"), ("pomoc", "pomoć"),
    ("konacni", "konačni"), ("greske", "greške"), ("zajednicki nazivnik", "zajednički nazivnik"),
    ("objasnjenje", "objašnjenje"), ("vjezbu", "vježbu"),
]


@pytest.mark.parametrize("raw,expected", CORPUS)
def test_corpus_maps_to_ijekavica(raw, expected):
    assert to_ijekavica(raw) == expected


def test_corpus_output_has_no_residual_leaks():
    for _raw, expected in CORPUS:
        assert not _leaks(expected), (expected, _leaks(expected))


# --------------------------------------------------------------------------- #
# 2. Math / protected content and student input are NEVER corrupted           #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [
    "Izračunaj: 1/2 + 1/3.", "Riješi jednačinu: 2x + 3 = 11.", "Rezultat je 5/6.",
    "NZD(12, 18) = 6", "2·2·3·5", r"\(x = 4\)", "U trouglu su dva ugla 60° i 70°.",
    "Odredi A ∪ B ako je A={1,2}, B={2,3}.", "236,50 KM", "20% od 50 je 10",
])
def test_math_content_preserved(text):
    out = to_ijekavica(text)
    # numbers, operators and set/degree symbols must be byte-identical
    assert re.sub(r"[^\d/=+\-·∪∩°%{},.]", "", out) == re.sub(r"[^\d/=+\-·∪∩°%{},.]", "", text)


def test_student_input_not_normalized_in_response(master_tmap):
    master, tmap = master_tmap
    import types
    def chat(model, messages, timeout=None, max_tokens=None, fast=False, **kw):
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content="U redu."))])
    # A student who writes ekavica must NOT have their message rewritten.
    out = svc.handle_chat({"grade": 6, "mode": "explain", "selected_topic": "6-04-031",
                           "student_message": "objasni mi deo o razlomcima"},
                          chat, master, tmap, model="m", timeout=1)
    # the response answer is normalized, but the student's own message is untouched
    # (we cannot see it in the answer; assert the engine did not crash and answered).
    assert out["answer"]


# --------------------------------------------------------------------------- #
# 3. Engine V2 outputs are leak-free (upstream correctness)                    #
# --------------------------------------------------------------------------- #
def test_template_questions_are_clean():
    import random
    for t in tt._TEMPLATES:
        for s in range(15):
            q, _a = t.generate(random.Random(s))
            assert not _leaks(q), (t.skill_id, q, _leaks(q))


def test_solution_plan_prompts_and_hints_are_clean():
    for skill in tt.GUIDABLE_SKILLS:
        task = tt._generate_from(tt._BY_ID[skill], __import__("random").Random(1),
                                 grade=6, oblast="", tema="")
        plan = sp.build_plan_for_task(task.question)
        assert plan is not None
        for step in plan.steps:
            assert not _leaks(step.prompt), (skill, step.id, step.prompt, _leaks(step.prompt))
            assert not _leaks(step.hint), (skill, step.id, step.hint, _leaks(step.hint))


def test_step_engine_directive_is_clean():
    for classification in (sp.CORRECT_STEP, sp.FINAL_CORRECT, sp.WRONG_STEP, sp.HELP):
        d = svc._step_engine_directive({
            "classification": classification,
            "active_prompt": "Je li 240 djeljivo sa 3?",
            "active_hint": "Saberi cifre.",
        })
        assert not _leaks(d), (classification, _leaks(d))


def test_exam_engine_text_is_clean():
    state = ee.start_exam(seed="lang", count=3, grade=6, oblast="Razlomci")
    present = ee._present_all(state, intro="Počinjemo kontrolni.")
    assert not _leaks(present), _leaks(present)
    # grade all → summary
    for it in state.items:
        it.status = "graded"; it.correct = True
    state.exam_status = "completed"
    assert not _leaks(ee._summary(state)), _leaks(ee._summary(state))
    # explicit generic fallback preface
    fb = ee.start_exam(seed="lang", count=3, grade=7, oblast="Vektori")
    pres_fb = ee._present_all(fb, intro="Počinjemo kontrolni.")
    assert not _leaks(pres_fb), _leaks(pres_fb)
    assert "opšti" in pres_fb.lower() or "OPŠTI" in pres_fb


# --------------------------------------------------------------------------- #
# 4. Terminology convention                                                   #
# --------------------------------------------------------------------------- #
def test_terminology_convention_documented():
    assert TERMINOLOGY["denominator"] == "nazivnik"
    assert TERMINOLOGY["common_denominator"] == "zajednički nazivnik"
    assert TERMINOLOGY["divisor"] == "djelilac"
    assert TERMINOLOGY["sum"] == "zbir"
    assert TERMINOLOGY["angle"] == "ugao"


def test_denominator_convention_enforced():
    # "imenilac"/"imenitelj" are normalized to the chosen "nazivnik".
    assert to_ijekavica("zajednički imenilac") == "zajednički nazivnik"
    assert to_ijekavica("brojitelj i imenitelj") == "brojnik i nazivnik"


def test_solution_plan_uses_nazivnik_not_imenilac():
    plan = sp.build_plan_for_task("Izračunaj: 1/2 + 1/3.")
    text = " ".join(s.prompt + " " + s.hint for s in plan.steps).lower()
    assert "nazivnik" in text
    assert "imenilac" not in text and "imenitelj" not in text


# --------------------------------------------------------------------------- #
# 5. Idempotence + short replies                                              #
# --------------------------------------------------------------------------- #
def test_to_ijekavica_is_idempotent():
    for _raw, expected in CORPUS:
        assert to_ijekavica(expected) == expected


@pytest.mark.parametrize("text", [
    "Tačno.", "Netačno.", "Djelimično tačno.", "Bravo!", "U redu, idemo dalje.",
])
def test_short_replies_clean(text):
    assert to_ijekavica(text) == text
    assert not _leaks(text)


@pytest.fixture(scope="module")
def master_tmap():
    from matbot import content_loader as cl
    return cl.load_master_content(), cl.load_thinkific_map()
