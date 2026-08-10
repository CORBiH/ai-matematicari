"""Generator talasa F6H — ŽIVA VALIDACIJA ARHITEKTURE POMOĆI (Faza 2).

    python tools/practice_eval/scenarios/family/wave_hint2.jsonl.py

ZAŠTO POSTOJI. Dva objavljena blokatora izdanja došla su IZ PUTA POMOĆI i oba su
pala samo na ručni pregled (FW-X03 nagovještaj 1 i 3, TR-B1 nagovještaj 1 i 2).
Faza 2 je taj put prepisala: vrh ljestvice i puno rješenje sastavlja server iz
provjerenog artefakta objave, propozicione nagovještaje 1–2 sastavlja server iz
šablona koji ne prepisuje opcije, a računske nagovještaje 1–2 model piše kroz
uske deterministe. Ovaj talas to mjeri ŽIVO.

ŠTA JE OVDJE NOVO U ODNOSU NA RANIJE TALASE:
  • `hint_proposition_no_leak`          — otkrivanje označene TVRDNJE (uvezano);
  • `hint_top_from_verified_solution`   — provenijencija vrha ljestvice i punog
                                          rješenja (bajt za bajt);
  • `help_has_task_scaffold`            — pomoć bez ijednog koraka;
  • `help_notation_in_scope`            — napredna mašinerija van zadatka.

BUDŽET (`expect_calls` je GORNJA granica troška, ne tvrdnja): 12 scenarija.
Deklarisani maksimum je 46 poziva, ali je STVARNI trošak znatno manji: svaka
propoziciona ljestvica potroši samo 2 poziva (objava), jer su nagovještaji 1–3
serverski. Realno očekivanje je ~30–38 poziva. Tvrde granice po koraku nose
`calls_at_most:N` i `zero_calls`.

POKRIVENOST GRANA (hardening, Problem C): oznake „recognition“/„computational“
NISU dokaz. Svaki scenario nosi `task_class:<klasa>` koja bilježi STVARNU
serversku klasu poslije objave. Traži se najmanje 3 propozicione + 3 računske
pune ljestvice i najmanje 1 KRATKO-SIMBOLIČKI propozicioni zadatak; presudu
donosi `release_contract.hint_branch_coverage` nad snimljenim zapisima.

ROUTE PREFLIGHT: sve lekcije su MODEL-lekcije (nemaju semantički ugovor porodice
s potpunim determinističkim generatorom, i nisu K1/K3 legacy lekcije), pa turnovi
stvarno voze Tutor+Reviewer put i model-ljestvicu pomoći.

KLASA ZADATKA SE NE PRETPOSTAVLJA. Klasu (računski / tvrdnja) određuje server iz
OBJAVLJENIH opcija, pa se ne može znati prije poziva. Zato nagovještaji 1 i 2
nose `calls_at_most:1` (0 kad server sastavlja, 1 kad piše model), a nikad tvrdu
tvrdnju o broju poziva. Vrh ljestvice i puno rješenje su serverski po
konstrukciji na SVAKOJ klasi — tamo `zero_calls` jeste tvrda tvrdnja.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from matbot.contracts import registry as contract_registry     # noqa: E402
from matbot.hint_policy import COMPUTATIONAL as COMPUTATIONAL_CLASS  # noqa: E402
from matbot.hint_policy import PROPOSITIONAL as PROPOSITIONAL_CLASS  # noqa: E402
from matbot.semantics import contracts as semantic_contracts    # noqa: E402
from matbot.topics import lesson_info                           # noqa: E402
from matbot.tutor import lesson_context as lesson_context_module  # noqa: E402
from matbot.tutor import pipeline as tutor_pipeline             # noqa: E402

OUT = Path(__file__).resolve().parent / "wave_hint2.jsonl"

# (topic_id, grade, zašto). Prva grupa su lekcije čiji naslov nosi
# PREPOZNAVANJE/definiciju — tamo se CILJA propoziciona klasa; druga grupa su
# lekcije s računskim odgovorom.
#
# OZNAKA NIJE DOKAZ (hardening, Problem C). Oblik opcija bira model pri
# generisanju, pa se klasa zna TEK poslije objave. Svaki scenario zato nosi
# provjeru `task_class:<klasa>` koja bilježi STVARNU serversku klasu; kad se ne
# poklopi, to je RUPA U POKRIVENOSTI (SKIP → REVIEW → COVERAGE_GAP), nikad kvar
# proizvoda. Zbog toga su grupe NAMJERNO veće od minimuma koji se traži:
# potrebno je 3 propozicione i 3 računske pune ljestvice, a nudi se 6 + 5, pa je
# dovoljno da se polovina cilja poklopi.
RECOGNITION = (
    ("9-02-006", 9, "Doslovna lekcija FW-X03/TR-B1: prava i ravan, odgovor je tvrdnja."),
    ("9-02-005", 9, "Ista porodica kao TR-B1: međusobni položaj dvije prave."),
    ("9-02-007", 9, "Ista porodica: međusobni položaj dvije ravni — često SIMBOLIČKE opcije."),
    ("7-05-005", 7, "Uslovi da je četverougao paralelogram — kriterij JE odgovor."),
    ("7-05-004", 7, "Paralelogram: definicija i svojstva — prepoznavanje svojstva."),
    ("6-09-001", 6, "Pojam ugla i označavanje — definicijsko prepoznavanje, 6. razred."),
)
# ZAVRŠNA POPRAVKA KLASIFIKATORA: računska klasa je dokaziva iz oblika SAMO za
# ČISTU VELIČINU (broj, broj s jedinicom, razlomak, uređeni par). Za RELACIJSKI
# odgovor (`$x>3$`, `$P=24$ cm`) klasa zavisi od toga hoće li serverski orakl
# sam riješiti zadatak, pa takva lekcija nije pouzdan cilj računske grane i
# NAMJERNO se ovdje ne koristi (lekcija o skupu rješenja je zato ispala).
# Ciljevi ispod su lekcije čiji je odgovor po prirodi brojevna veličina.
COMPUTATIONAL = (
    ("8-04-013", 8, "Udaljenost tačaka u koordinatnom sistemu — brojevni rezultat."),
    ("6-13-006", 6, "Čitanje podataka iz tabela i dijagrama — brojevni rezultat, 6. razred."),
    ("7-05-025", 7, "Složene figure: obim i površina — brojevni rezultat s jedinicom."),
    ("6-10-002", 6, "Brojevna osa i koordinate tačaka — brojevni rezultat."),
    ("8-05-021", 8, "Složeni zadaci s prizmama i piramidama — brojevni rezultat s jedinicom."),
    ("8-02-002", 8, "Koordinate tačke i uređeni par — puno rješenje bez ijednog hinta."),
)

PUBLISH_CHECKS = [
    "published", "task_published", "task_self_contained", "response_schema",
    "options_ok", "package_clean", "no_leak", "no_control_chars", "math_safe",
    "numeric_consistent", "geometry_ok", "terminology_clean", "bosnian",
    "lesson_matches", "stays_in_lesson", "no_verdict", "calls_at_most:2",
]

# Grana arhitekture se bilježi TAMO GDJE JE PRVI PUT DOKAZIVA — na objavi, iz
# objavljenih opcija i serverskog `correct_option_id`. `symbolic_marked_answer`
# stoji uz nju da bi se moglo dokazati da je Problem A (kratke SIMBOLIČKE opcije
# na propozicionoj grani) živo zatvoren.
def _branch_checks(target_class):
    return [f"task_class:{target_class}", "symbolic_marked_answer"]

# Nagovještaji 1 i 2. `hint_no_leak` (vrijednost) i `hint_proposition_no_leak`
# (tvrdnja) stoje ZAJEDNO — dvije različite forme odgovora, dva vlasnika.
HINT_CHECKS = [
    "published", "not_safe_error", "no_fallback_text", "response_schema",
    "help_nonempty", "help_has_task_scaffold", "help_notation_in_scope",
    "hint_no_leak", "hint_proposition_no_leak", "hint_differs",
    "no_new_task", "task_preserved", "task_not_completed", "correct_option_stable",
    "reveal_absent", "no_leak", "no_control_chars", "math_safe",
    "numeric_consistent", "geometry_ok", "terminology_clean", "bosnian",
    "lesson_matches", "stays_in_lesson", "no_verdict", "calls_at_most:1",
]

# Vrh ljestvice: rezultat je DOZVOLJEN, ali provenijencija je obavezna.
TOP_HINT_CHECKS = [
    "published", "not_safe_error", "no_fallback_text", "response_schema",
    "help_nonempty", "help_has_task_scaffold", "help_notation_in_scope",
    "hint_top_from_verified_solution", "hint_differs",
    "no_new_task", "task_preserved", "correct_option_stable",
    "no_leak", "no_control_chars", "math_safe", "numeric_consistent",
    "geometry_ok", "terminology_clean", "bosnian", "lesson_matches",
    "stays_in_lesson", "no_verdict", "zero_calls", "calls_at_most:0",
]

SOLUTION_CHECKS = [
    "published", "not_safe_error", "no_fallback_text", "response_schema",
    "help_nonempty", "help_has_task_scaffold", "help_notation_in_scope",
    "hint_top_from_verified_solution", "reveal_present", "task_completed",
    "no_new_task", "task_preserved", "no_leak", "no_control_chars", "math_safe",
    "numeric_consistent", "geometry_ok", "terminology_clean", "bosnian",
    "lesson_matches", "stays_in_lesson", "no_verdict", "zero_calls",
    "calls_at_most:0",
]

PUBLISH_RUBRICS = ["clarity", "grade_fit", "lesson_alignment"]
HELP_RUBRICS = ["clarity", "grade_fit", "hint_usefulness", "hint_safety", "pedagogy"]


def _publish_step(target_class):
    return {"kind": "text", "message": "Daj mi zadatak.", "expect_calls": 2,
            "checks": PUBLISH_CHECKS + _branch_checks(target_class),
            "rubrics": list(PUBLISH_RUBRICS)}


def _hint_step(top=False):
    return {"kind": "text", "message": "Ne znam.", "intent": "hint_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "collect_help": True,
            "expect_calls": 0 if top else 1,
            "checks": list(TOP_HINT_CHECKS if top else HINT_CHECKS),
            "rubrics": list(HELP_RUBRICS)}


def _solution_step():
    return {"kind": "text", "message": "Uradi ga ti.", "intent": "solution_request",
            "interaction_phase": "practice_help", "send_last_task": True,
            "requires_active_task": True, "collect_help": True,
            "expect_calls": 0, "checks": list(SOLUTION_CHECKS),
            "rubrics": list(HELP_RUBRICS)}


def _scenario(index, topic_id, grade, reason, tags, steps):
    lesson = lesson_info(grade, topic_id)
    if lesson is None:
        raise SystemExit(f"PREFLIGHT: {topic_id} nije kanonska lekcija {grade}. razreda")
    # ROUTE PREFLIGHT koristi ISTE produkcijske predikate, ne aproksimaciju:
    # lekcija s POTPUNIM determinističkim generatorom pomoć servira iz pohranjene
    # ljestvice paketa (Faza 4H) i ne bi mjerila ništa iz Faze 2.
    context = lesson_context_module.build(grade, topic_id)
    if tutor_pipeline._deterministic_generator_for(context) is not None:
        raise SystemExit(f"ROUTE PREFLIGHT: {topic_id} vozi determinističku strategiju")
    if (contract_registry.contract_for(topic_id) is not None
            and semantic_contracts.contract_for(topic_id) is None):
        raise SystemExit(f"ROUTE PREFLIGHT: {topic_id} je K1/K3 legacy lekcija")
    return {
        "id": f"H{index:02d}", "wave": "F6H", "importance": "critical",
        "grade": grade, "oblast": lesson["oblast"], "topic_id": topic_id,
        "reason": reason, "tags": list(tags), "steps": steps,
    }


def build():
    scenarios = []
    index = 1
    # 1) Puna ljestvica 0→1→2→3 na lekcijama prepoznavanja (cilj: klasa tvrdnje).
    for topic_id, grade, reason in RECOGNITION:
        scenarios.append(_scenario(
            index, topic_id, grade, reason,
            ("hint_ladder", "recognition", "phase2"),
            [_publish_step(PROPOSITIONAL_CLASS), _hint_step(), _hint_step(),
             _hint_step(top=True)]))
        index += 1
    # 2) Puna ljestvica na računskim lekcijama (cilj: model piše nivoe 1 i 2).
    for topic_id, grade, reason in COMPUTATIONAL[:-1]:
        scenarios.append(_scenario(
            index, topic_id, grade, reason,
            ("hint_ladder", "computational", "phase2"),
            [_publish_step(COMPUTATIONAL_CLASS), _hint_step(), _hint_step(),
             _hint_step(top=True)]))
        index += 1
    # 3) Puno rješenje ODMAH (bez ijednog nagovještaja) — provenijencija i reveal.
    topic_id, grade, reason = COMPUTATIONAL[-1]
    scenarios.append(_scenario(
        index, topic_id, grade,
        reason + " Puno rješenje bez prethodnog nagovještaja.",
        ("full_solution", "computational", "phase2"),
        [_publish_step(COMPUTATIONAL_CLASS), _solution_step()]))
    return scenarios


def main():
    scenarios = build()
    lines = [json.dumps(scenario, ensure_ascii=False) for scenario in scenarios]
    # Bajtovi, ne `write_text`: `.gitattributes` za ovaj repo traži LF, a
    # `write_text` na Windowsu prevodi `\n` u `\r\n` i time pravi lažni diff.
    OUT.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    budget = sum(step.get("expect_calls", 0)
                 for scenario in scenarios for step in scenario["steps"])
    print(f"{len(scenarios)} scenarios, budget <= {budget} model calls → {OUT}")


if __name__ == "__main__":
    main()
