"""Engine V2 — Phase 7 evaluation corpus.

Multi-turn scenarios covering every required category, plus a PERMANENT
regression fixture for each previously observed production bug (``regression_of``).

``applies_to`` names the flag configs where the scenario's expectations hold;
elsewhere the scenario still runs and the universal invariants (no crash, no
language leak, no verdict/prose contradiction, clean telemetry, one Sheets row)
are enforced.
"""
from __future__ import annotations

from matbot.eval_v2 import Scenario, Turn

_G6 = {"grade": 6, "mode": "practice", "selected_topic": "6-04-031"}
_EXAM = {"grade": 6, "mode": "exam", "session_id": "eval-exam"}
ALL = ()                     # expectations hold in every config
PRACTICE = ("practice", "all_v2")
EXAM = ("exam", "all_v2")

SCENARIOS: list[Scenario] = [

    # ---------------- exact correct / equivalent forms ----------------------
    Scenario(
        id="exact-correct-fraction", category="exact_correct", payload=_G6,
        seed_task="Izračunaj: 1/4 + 1/4.",
        turns=[Turn("1/2", phase="answer", reply="Tačno.",
                    expect={"verdict": "correct"})],
    ),
    Scenario(
        id="equiv-unreduced", category="equivalent_form", payload=_G6,
        seed_task="Izračunaj: 1/4 + 1/4.",
        turns=[Turn("2/4", phase="answer", reply="Tačno.",
                    expect={"verdict_not": "incorrect"})],
    ),
    Scenario(
        id="equiv-mixed-3/2-vs-1½", category="equivalent_form", payload=_G6,
        regression_of="3/2 vs 1 1/2", seed_task="Izračunaj: 3/4 + 3/4.",
        turns=[Turn("1 1/2", phase="answer", reply="Tačno.",
                    expect={"verdict": "correct"})],
    ),
    Scenario(
        id="equiv-improper-3/2", category="equivalent_form", payload=_G6,
        regression_of="3/2 vs 1 1/2", seed_task="Izračunaj: 3/4 + 3/4.",
        turns=[Turn("3/2", phase="answer", reply="Tačno.",
                    expect={"verdict": "correct"})],
    ),
    Scenario(
        id="units-bare-number-15", category="equivalent_form", payload=_G6,
        regression_of="15 vs 15 cm", seed_task="Pretvori 15 dm u cm.",
        turns=[Turn("150", phase="answer", reply="Tačno.",
                    expect={"verdict": "correct"})],
    ),
    Scenario(
        id="degrees-bare-40", category="equivalent_form", payload=_G6,
        regression_of="40 vs 40°",
        seed_task="U trouglu su dva ugla 70° i 70°. Odredi treći ugao.",
        turns=[Turn("40", phase="answer", reply="Tačno.",
                    expect={"verdict": "correct"})],
    ),
    Scenario(
        id="set-union-correct", category="exact_correct", payload=_G6,
        regression_of="correct set union",
        seed_task="Odredi A ∪ B ako je A={1,2}, B={2,3}.",
        turns=[Turn("{1,2,3}", phase="answer", reply="Tačno.",
                    expect={"verdict": "correct"})],
    ),

    # ---------------- partial / intermediate reasoning ----------------------
    Scenario(
        id="common-denominator-partial", category="partial_reasoning", payload=_G6,
        regression_of="common denominator statement as partial step",
        seed_task="Izračunaj: 1/2 + 1/3.",
        turns=[Turn("Zajednički nazivnik je 6", phase="answer",
                    reply="Dobar korak.", gpt_verdict="partial",
                    expect={"verdict_not": "incorrect", "task_status": "active"})],
    ),
    Scenario(
        id="inequality-incomplete", category="partial_reasoning", payload=_G6,
        regression_of="incomplete inequality x>3 submitted as 4",
        seed_task="Riješi nejednačinu: x - 3 > 0.",
        turns=[Turn("4", phase="answer", reply="Skoro.",
                    expect={"verdict_not": "correct"})],
    ),
    Scenario(
        id="wrong-intermediate-right-final", category="wrong_intermediate", payload=_G6,
        seed_task="Izračunaj: 1/2 · 5/9.",
        turns=[Turn("prvo sam pomnozio brojnike pa je 5/18", phase="answer",
                    reply="Pogledajmo postupak.", gpt_verdict="partial",
                    expect={"verdict_not": "correct"})],
    ),
    Scenario(
        id="ambiguous-answer", category="ambiguous", payload=_G6,
        seed_task="Izračunaj: 1/4 + 1/4.",
        turns=[Turn("možda nešto", phase="answer", reply="Nejasno mi je.",
                    expect={"verdict_not": "correct"})],
    ),

    # ---------------- prose must never be grading evidence ------------------
    Scenario(
        id="prose-tacno-not-evidence", category="prose_not_evidence", payload=_G6,
        regression_of="prose 'Tačno' never grading evidence",
        seed_task="Izračunaj: 1/4 + 1/4.",
        turns=[Turn("9/9", phase="answer", reply="Tačno! Odlično si to uradio.",
                    expect={"verdict": "incorrect"})],
    ),
    Scenario(
        id="prose-netacno-not-evidence", category="prose_not_evidence", payload=_G6,
        regression_of="prose 'Netačno' never grading evidence",
        seed_task="Izračunaj: 1/4 + 1/4.",
        turns=[Turn("1/2", phase="answer", reply="Netačno, pogriješio si.",
                    expect={"verdict": "correct"})],
    ),

    # ---------------- Practice Step Engine (240 ÷ 6) ------------------------
    Scenario(
        id="div6-guided-completion", category="practice_step_completion", payload=_G6,
        regression_of="240 divisibility-by-6 guided flow", applies_to=PRACTICE,
        seed_task="Provjeri da li je broj 240 djeljiv sa 6. Obrazloži svoj odgovor.",
        turns=[
            Turn("da je djeljiv sa 2", phase="answer", reply="Tačno, idemo dalje.",
                 expect={"verdict": "partial", "active_step": "div3",
                         "step_complete": False, "task_status": "active",
                         "streak": 0, "last_task_nonempty": True}),
            Turn("2+4+0 = 6, i 6 je djeljivo sa 3", phase="answer", reply="Odlično.",
                 expect={"verdict": "partial", "active_step": "final",
                         "step_complete": False, "streak": 0}),
            Turn("da, djeljiv je sa 6 jer je djeljiv i sa 2 i sa 3", phase="answer",
                 reply="Bravo!",
                 expect={"verdict": "correct", "step_complete": True,
                         "task_status": "completed", "streak": 1}),
        ],
    ),
    Scenario(
        id="correct-intermediate-no-streak", category="correct_intermediate", payload=_G6,
        regression_of="correct intermediate must not end task or bump streak",
        applies_to=PRACTICE,
        seed_task="Provjeri da li je broj 240 djeljiv sa 6. Obrazloži svoj odgovor.",
        turns=[Turn("da je djeljiv sa 2", phase="answer", reply="Tačno.",
                    expect={"verdict": "partial", "task_status": "active",
                            "step_complete": False, "streak": 0,
                            "last_task_nonempty": True})],
    ),
    Scenario(
        id="help-preserves-cursor", category="ne_znam_hint", payload=_G6,
        applies_to=PRACTICE,
        seed_task="Riješi jednačinu: 2x + 3 = 11.",
        turns=[
            Turn("ne znam", phase="answer", reply="Evo mali savjet.",
                 expect={"active_step": "isolate", "step_complete": False,
                         "verdict": None, "wrong_attempts": 0,
                         "task_status": "active"}),
            Turn("8", phase="answer", reply="Tačno.",
                 expect={"verdict": "partial", "active_step": "final"}),
        ],
    ),
    Scenario(
        id="task-id-stable-across-steps", category="task_id_persistence", payload=_G6,
        regression_of="task ID and cursor persistence", applies_to=PRACTICE,
        seed_task="Riješi jednačinu: 2x + 3 = 11.",
        turns=[
            Turn("ne znam", phase="answer", reply="Savjet."),
            Turn("8", phase="answer", reply="Tačno."),
            Turn("x=4", phase="answer", reply="Bravo!",
                 expect={"same_task_id": True, "verdict": "correct"}),
        ],
    ),

    # ---------------- short follow-ups / mode preservation ------------------
    Scenario(
        id="short-followup-keeps-task", category="short_followup", payload=_G6,
        seed_task="Izračunaj: 1/2 + 1/3.",
        turns=[Turn("šta dalje", phase="answer", reply="Evo savjeta.",
                    expect={"last_task_nonempty": True})],
    ),

    # ---------------- Exam Engine ------------------------------------------
    Scenario(
        id="exam-lifecycle", category="exam_lifecycle",
        payload=dict(_EXAM, selected_oblast="Razlomci"), applies_to=EXAM,
        turns=[
            Turn("daj mi kontrolni", expect={"exam_status": "active",
                                             "exam_index": 0, "mode": "exam",
                                             "topic_covered": True}),
        ],
    ),
    Scenario(
        id="exam-one-answer-one-item", category="item_attribution",
        payload=dict(_EXAM, selected_oblast="Djeljivost brojeva"),
        regression_of="one exam answer grades exactly one item", applies_to=EXAM,
        turns=[
            Turn("kontrolni"),
            Turn("potpuno pogrešan odgovor 999",
                 expect={"exam_index": 1,
                         "graded_flags": [False, None, None]}),
        ],
    ),
    Scenario(
        id="exam-mode-never-drifts", category="mode_preservation",
        payload=dict(_EXAM, selected_oblast="Razlomci"), applies_to=EXAM,
        turns=[
            Turn("kontrolni", expect={"mode": "exam"}),
            Turn("objasni mi ovo umjesto toga", expect={"mode": "exam",
                                                        "exam_status": "active"}),
        ],
    ),
    Scenario(
        id="exam-help-no-reveal", category="ne_znam_hint",
        payload=dict(_EXAM, selected_oblast="Razlomci"), applies_to=EXAM,
        turns=[
            Turn("kontrolni"),
            Turn("ne znam", expect={"exam_index": 0, "exam_status": "active",
                                    "verdict": None}),
        ],
    ),
    Scenario(
        id="exam-unsupported-topic-fallback", category="unsupported_fallback",
        payload={"grade": 7, "mode": "exam", "session_id": "eval-vek",
                 "selected_oblast": "Vektori"},
        regression_of="unsupported topic never silently gets unrelated exam",
        applies_to=EXAM,
        turns=[Turn("daj mi kontrolni",
                    expect={"topic_covered": False,
                            "answer_contains": ["napomena"]})],
    ),
    Scenario(
        id="exam-selected-tema-stays-on-topic", category="unsupported_fallback",
        payload=dict(_EXAM, selected_oblast="Djeljivost brojeva"),
        regression_of="selected tema not collapsing to broader oblast",
        applies_to=EXAM,
        turns=[Turn("kontrolni", expect={"topic_covered": True})],
    ),

    # ---------------- post-exam --------------------------------------------
    Scenario(
        id="post-exam-never-reopens", category="post_exam",
        payload=dict(_EXAM, selected_oblast="Razlomci", session_id="eval-post"),
        regression_of="completed exam never reopening", applies_to=EXAM,
        turns=[
            Turn("kontrolni"),
            Turn("predaj", expect={"exam_status": "completed"}),
            Turn("objasni drugi", expect={"exam_status": "completed",
                                          "mode": "exam"}),
            Turn("hvala", expect={"exam_status": "completed"}),
        ],
    ),

    # ---------------- ungradeable rejection ---------------------------------
    Scenario(
        id="ungradeable-task-rejected", category="ungradeable_rejection", payload=_G6,
        turns=[Turn("daj mi zadatak",
                    reply="Zadatak: Izmjeri dužinu tangente t na crtežu.",
                    # The ungradeable ask ("izmjeri dužinu tangente") must never
                    # become active: it is dropped or replaced by a VALIDATED
                    # fallback. The contract is "no task activates unvalidated".
                    expect={"answer_not_contains": ["izmjeri dužinu"],
                            "last_task_not_contains": ["izmjeri dužinu"],
                            "task_validated": True})],
    ),

    # ---------------- production defects (permanent fixtures) ----------------
    Scenario(
        id="prod-144-verified-not-downgraded", category="grader_conflict", payload=_G6,
        regression_of="structured GPT downgraded verified deterministic (144 / 3 and 4)",
        applies_to=("grading", "all_v2"),
        seed_task="Provjeri je li broj 144 djeljiv sa 3 i 4. Obrazloži svoje odgovore.",
        turns=[Turn("Jest jer je zbir cifara djeljiv sa 3 i zadnja dva broja su djeljiva sa 4.",
                    phase="answer", reply="Pogledajmo.", gpt_verdict="partial",
                    expect={"verdict": "correct",
                            "answer_not_contains": ["nije djeljiv sa 4"]})],
    ),
    Scenario(
        id="prod-common-denominator-still-partial", category="grader_conflict", payload=_G6,
        regression_of="common denominator must stay partial after the 144 fix",
        applies_to=("grading", "all_v2"),
        seed_task="Izračunaj: 1/2 + 1/3.",
        turns=[Turn("Zajednički nazivnik je 6", phase="answer", reply="Dobar korak.",
                    gpt_verdict="partial", expect={"verdict": "partial"})],
    ),
    Scenario(
        id="prod-tema-expansion-not-collapsed", category="tema_preservation",
        payload={"grade": 6, "mode": "practice", "selected_oblast": "Razlomci",
                 "selected_topic": "6-04-035", "lesson_title": "Proširivanje razlomaka"},
        regression_of="selected tema collapsed to broader oblast",
        applies_to=PRACTICE,
        turns=[
            Turn("daj mi zadatak", reply="Zadatak: LEGACY-MODEL",
                 expect={"answer_contains": ["proširi"]}),
            Turn("Daj mi teži zadatak iz iste teme.", reply="Zadatak: LEGACY-MODEL",
                 expect={"answer_contains": ["proširi"],
                         "answer_not_contains": ["·"]}),
        ],
    ),

    Scenario(
        id="prod-multi-divisor-30-bare-da", category="multi_condition", payload=_G6,
        regression_of="multi-condition divisibility collapsed to one boolean (30 / 5 and 3)",
        applies_to=("grading", "all_v2"),
        seed_task="Provjeri da li je broj 30 djeljiv sa 5 i sa 3. Obrazloži svoj odgovor za oba broja.",
        turns=[Turn("da", phase="answer", reply="Pogledajmo.", gpt_verdict="correct",
                    expect={"verdict": "partial", "task_status": "active",
                            "streak": 0, "last_task_nonempty": True})],
    ),
    Scenario(
        id="prod-multi-divisor-240-jeste", category="multi_condition", payload=_G6,
        regression_of="multi-condition divisibility collapsed to one boolean (240 / 10 and 15)",
        applies_to=("grading", "all_v2"),
        seed_task="Provjeri da li je broj 240 djeljiv sa 10 i sa 15. Obrazloži svoj odgovor za oba broja.",
        turns=[Turn("jeste djeljivo", phase="answer", reply="Pogledajmo.",
                    expect={"verdict": "partial", "task_status": "active",
                            "streak": 0})],
    ),
    Scenario(
        id="prod-multi-divisor-full-explanation", category="multi_condition", payload=_G6,
        regression_of="full multi-divisor explanation must complete the task",
        applies_to=("grading", "all_v2"),
        seed_task="Provjeri da li je broj 30 djeljiv sa 5 i sa 3. Obrazloži svoj odgovor za oba broja.",
        turns=[Turn("da, sa 5 jer se završava nulom, i sa 3 jer je zbir cifara 3 djeljiv sa 3",
                    phase="answer", reply="Bravo!",
                    expect={"verdict": "correct"})],
    ),

    # ---------------- prod round 3: mode sync / exam intents / topic id ------
    Scenario(
        id="prod-explanation-request-in-stale-quick", category="mode_sync",
        payload={"grade": 6, "mode": "quick", "selected_topic": "6-04-031"},
        regression_of=("UI showed Objašnjenje while backend ran Quick and replied "
                       "with the bare result 1"),
        turns=[Turn("Provjeri da li je broj 30 djeljiv sa 5 i sa 3. Obrazloži.",
                    reply="Pogledajmo oba uslova.",
                    expect={"mode": "explain",
                            "answer_not_contains": ["rezultat: 1", "= 1\n"]})],
    ),
    Scenario(
        id="prod-boolean-never-rendered-as-1", category="mode_sync",
        payload={"grade": 6, "mode": "quick"},
        regression_of="boolean result rendered as raw 1/0 instead of Da/Ne",
        turns=[Turn("Da li je 30 djeljivo sa 5 i sa 3?", reply="Da.",
                    expect={"answer_not_contains": ["rezultat: 1", "rezultat: 0"]})],
    ),
    Scenario(
        id="prod-genuine-quick-stays-quick", category="mode_sync",
        payload={"grade": 6, "mode": "quick"},
        regression_of="explanation promotion must not swallow genuine Quick",
        turns=[Turn("12 - 23x = 4x", reply="x = 12/27.",
                    expect={"mode": "quick"})],
    ),
    Scenario(
        id="prod-exam-help-does-not-advance", category="exam", payload=_EXAM,
        regression_of=("exam stored „objasni ti” / „ne znam” as the answer and "
                       "advanced to the next item"),
        applies_to=EXAM,
        turns=[
            Turn("daj mi kontrolni", reply="Krećemo.",
                 expect={"exam_status": "active", "exam_index": 0}),
            Turn("ne znam", reply="U redu.",
                 expect={"exam_status": "active", "exam_index": 0,
                         "graded_flags": [None, None, None]}),
            Turn("objasni ti", reply="U redu.",
                 expect={"exam_status": "active", "exam_index": 0}),
            Turn("pomozi", reply="U redu.",
                 expect={"exam_status": "active", "exam_index": 0}),
        ],
    ),
    Scenario(
        id="prod-exam-skip-advances-as-skipped", category="exam", payload=_EXAM,
        regression_of="only an explicit skip may advance an unanswered item",
        applies_to=EXAM,
        turns=[
            Turn("daj mi kontrolni", reply="Krećemo."),
            Turn("preskoči", reply="U redu.",
                 expect={"exam_status": "active", "exam_index": 1}),
        ],
    ),
    Scenario(
        id="prod-exam-review-never-quotes-help", category="exam", payload=_EXAM,
        regression_of="post-exam review claimed a help request was the answer",
        applies_to=EXAM,
        turns=[
            Turn("daj mi kontrolni", reply="Krećemo."),
            Turn("objasni ti", reply="U redu."),
            Turn("predaj", reply="Gotovo.", expect={"exam_status": "completed"}),
            Turn("objasni prvi zadatak", reply="Evo.",
                 expect={"answer_not_contains": ["objasni ti"]}),
        ],
    ),
    Scenario(
        id="prod-runtime-topic-id-stays-on-tema", category="topic_identity",
        payload={"grade": 6, "mode": "practice", "selected_oblast": "Razlomci",
                 "selected_topic": "999999"},
        regression_of=("unmapped RUNTIME topic id (12880) under „Proširivanje "
                       "razlomaka” produced a linear equation"),
        applies_to=PRACTICE,
        turns=[Turn("daj mi zadatak", reply="Zadatak: Riješi jednačinu: 3x + 2 = 14.",
                    expect={"last_task_not_contains": ["jednačin", "jednacin"],
                            "task_validated": True, "last_task_nonempty": True})],
    ),
    Scenario(
        id="prod-no-trivial-gcd-item", category="task_quality", payload=_G6,
        regression_of="exam produced the trivial item „Odredi NZD(32, 32)”",
        seed_task="Odredi NZD(32, 32).",
        turns=[Turn("32", phase="answer", reply="Tačno.", expect={})],
    ),

    # ---------------- language ----------------------------------------------
    Scenario(
        id="language-engine-output", category="language", payload=_G6,
        seed_task="Izračunaj: 1/4 + 1/4.",
        turns=[Turn("1/2", phase="answer",
                    reply="Tacno. Sledeci put probaj i vezbu. Razumem?",
                    expect={"answer_not_contains": ["tacno", "sledeci", "vezb",
                                                    "razumem"]})],
    ),
]
