"""Generator KONSOLIDOVANOG ZAVRŠNOG TALASA (40 scenarija) — wave_final40.jsonl.

ZAŠTO POSTOJI: discovery-100 je potrošio 22 scenarija na ZAMRZNUTE poruke koje
su bile nespojive s izabranom lekcijom (korijenski uzrok RC11). Ovaj talas je
KOHERENTAN PO KONSTRUKCIJI: svaka poruka je uparena s lekcijom koja tu vrstu
zadatka stvarno predaje, a `tools/practice_eval/coherence.py` to dokazuje u
`--dry-run` prije ijednog živog poziva.

POPRAVKA 22 NEKOHERENTNA SCENARIJA (fikstura, nikad očekivanje proizvoda):

  A007  nejednačina na lekciji jednačina   → ista nejednačina na 7-02-019
                                             („Nejednačine sa sabiranjem…“)
  A013  interval na lekciji jednačina      → interval na 7-03-019 („Nejednačine u Q“)
  A022  nejednačina na „Jednačina sa razlomcima“
                                           → 9-04-015 („Nejednačina sa razlomcima“)
  A024  jednačina na „Nejednačina sa razlomcima“
                                           → 9-04-004 („Jednačina sa razlomcima“)
  B010  nejednačina na „Ekvivalentne jednačine“
                                           → 9-04-014 („Nejednačina sa zagradama“)
  B011  lanac na „Jednačina sa zagradama“  → 9-04-014
  B012  lanac na „Jednačina sa razlomcima“ → JEDNAČINA s razlomkom na istoj
                                             lekciji (dokaz o paketu se čuva
                                             odvojeno — vidi FW-S02)
  A015  rješavanje na lekciji o SKUPOVIMA  → rješavanje na 9-04-002
  A016  x^2=2 (nelinearno)                 → linearna relacija u istom domenu
  A017  x^2=2 (nelinearno)                 → linearna relacija u istom domenu
  C009  koordinatni zadatak na lekciji o dijagramima
                                           → koordinatni zadatak na 6-10-004
  C012/C014/C015, F001–F004                → zamijenjeni ciljanim geometrijskim
                                             scenarijima na lekcijama koje tu
                                             radnju stvarno predaju
  E008  nejednačina na lekciji podudarnosti
                                           → nejednačina na 7-02-019; sonda van
                                             lekcije zadržana ZASEBNO kao
                                             `lesson_overrides` (FW-X01)
  F005/F007/F008  koordinatni zadatak na lekcijama o MREŽI
                                           → zadatak o mreži na istim lekcijama

RASPORED (traženo §11): 12 domen · 8 zapis/duplikat · 6 funkcijska tabela ·
6 geometrija · 4 vjernost zahtjevu · 4 regresija.

Pokretanje:
    python tools/practice_eval/scenarios/family/wave_final40.jsonl.py
"""
from __future__ import annotations

import json
from pathlib import Path

WAVE = "FFINAL40"
OUT = Path(__file__).with_suffix("")          # …/wave_final40.jsonl

# Zajednički rep poruke: traži se POTPUN skup rješenja i tačno jedna tačna
# opcija. Namjerno kratak — dugačka uputstva su u discovery talasu gušila
# lekcijski kontekst.
SET_TAIL = ("Traži cijeli skup rješenja i osiguraj da je tačno jedna opcija "
            "matematički tačna u navedenom domenu. Ne rješavaj zadatak učeniku.")

SOLVE_CHECKS = ["published", "task_published", "task_self_contained",
                "options_ok", "package_clean", "lesson_matches",
                "stays_in_lesson", "math_safe", "numeric_consistent",
                "terminology_clean", "bosnian", "no_leak", "no_verdict",
                "calls_at_most:2"]


def solve_step(message, checks=None, rubrics=None):
    return {"kind": "text", "message": message, "expect_calls": 2,
            "checks": list(checks or SOLVE_CHECKS),
            "rubrics": list(rubrics or ["lesson_alignment"])}


def scenario(sid, grade, oblast, topic_id, reason, tags, steps,
             alignment="must_follow", targets=(), spec=None):
    row = {
        "id": sid, "wave": WAVE, "importance": "critical", "grade": grade,
        "oblast": oblast, "topic_id": topic_id, "reason": reason,
        "tags": list(tags), "request_alignment": alignment, "steps": steps,
    }
    if targets:
        row["targets_wave_a_findings"] = list(targets)
    if spec:
        row["discovery_spec"] = spec
    return row


# Lekcije koje su DOKAZANO spojive sa svakom vrstom zahtjeva (kanonski naslovi
# iz data/topics.json; `coherence.py` provjerava svaki par).
EQ_G9_EQUIV = (9, "Linearne jednačine i nejednačine", "9-04-002")   # Ekvivalentne jednačine
EQ_G9_PAREN = (9, "Linearne jednačine i nejednačine", "9-04-003")   # Jednačina sa zagradama
EQ_G9_FRAC = (9, "Linearne jednačine i nejednačine", "9-04-004")    # Jednačina sa razlomcima
IN_G9_PAREN = (9, "Linearne jednačine i nejednačine", "9-04-014")   # Nejednačina sa zagradama
IN_G9_FRAC = (9, "Linearne jednačine i nejednačine", "9-04-015")    # Nejednačina sa razlomcima
IN_G7_ADD = (7, "Cijeli brojevi", "7-02-019")                       # Nejednačine sa sabiranjem i oduzimanjem u Z
IN_G7_MUL = (7, "Cijeli brojevi", "7-02-020")                       # Nejednačine sa množenjem i dijeljenjem u Z
IN_G7_Q = (7, "Racionalni brojevi", "7-03-019")                     # Nejednačine u Q
IN_G6_FRAC = (6, "Jednačine, nejednačine i izrazi u Q+", "6-07-003")


def build():
    rows = []

    # ------------------------------------------------------------------
    # 1) DOMEN (12) — Q/R/Z/N/N0 pod lekcijom koja tu vrstu stvarno predaje
    # ------------------------------------------------------------------
    domain_cases = [
        ("FW-D01", EQ_G9_EQUIV, "N",
         "riješi jednačinu $x+2=2$ isključivo u skupu prirodnih brojeva "
         "$N=\\{1,2,3,...\\}$",
         "A020 replay: N excludes zero, so the solution set is empty.",
         ("A020",), {"domain": "N", "expected_solution": "empty"}),
        ("FW-D02", EQ_G9_EQUIV, "N0",
         "riješi jednačinu $x+2=2$ isključivo u skupu $N_0=\\{0,1,2,...\\}$",
         "N0 includes zero, so the same equation has solution {0} — N and N0 "
         "must never be treated as the same set.",
         ("A020",), {"domain": "N0", "expected_solution": "{0}"}),
        ("FW-D03", EQ_G9_PAREN, "Z",
         "riješi jednačinu $2(x-3)=x+5$ isključivo u skupu cijelih brojeva",
         "Single-level parentheses over Z must be adjudicated, not skipped.",
         ("B007",), {"domain": "Z", "expected_solution": "{11}"}),
        ("FW-D04", IN_G7_ADD, "Z",
         "riješi nejednačinu $-2<x+1<2$ isključivo u skupu cijelih brojeva",
         "A009 repaired: the same chained inequality now sits on an "
         "inequality lesson.",
         ("A009",), {"domain": "Z", "expected_solution": "{-2,-1,0}"}),
        ("FW-D05", IN_G7_MUL, "Z",
         "riješi nejednačinu $-3<-2x<-1$ isključivo u skupu cijelih brojeva",
         "A010 repaired: negative coefficient reverses direction; over Z the "
         "set is {1}.",
         ("A010",), {"domain": "Z", "expected_solution": "{1}"}),
        ("FW-D06", IN_G7_Q, "Q",
         "riješi nejednačinu $\\frac{1}{3}<x\\le\\frac{5}{6}$ isključivo u "
         "skupu racionalnih brojeva",
         "A023 repaired: fraction endpoints and openness stay continuous "
         "over Q.",
         ("A023",), {"domain": "Q", "expected_solution": "(1/3,5/6]"}),
        ("FW-D07", IN_G9_PAREN, "R",
         "riješi nejednačinu $2(x-1)<x+4$ isključivo u skupu realnih brojeva",
         "R must stay continuous and must never be discretised.",
         ("B017",), {"domain": "R", "expected_solution": "x<6"}),
        ("FW-D08", IN_G9_FRAC, "Z",
         "riješi nejednačinu $\\frac{x}{2}>\\frac{3}{2}$ isključivo u skupu "
         "cijelih brojeva",
         "A022 repaired: an inequality request now sits on the inequality "
         "lesson; over Z the ray starts at 4.",
         ("A022",), {"domain": "Z", "expected_solution": "x>=4"}),
        ("FW-D09", IN_G6_FRAC, "N",
         "riješi nejednačinu $x+1<4$ isključivo u skupu prirodnih brojeva "
         "$N=\\{1,2,3,...\\}$",
         "Grade 6 N boundary: the set is {1,2} and never contains zero.",
         ("A020",), {"domain": "N", "expected_solution": "{1,2}"}),
        ("FW-D10", IN_G7_ADD, "Z",
         "riješi nejednačinu $-1<x+1<1$ isključivo u skupu cijelih brojeva",
         "E02 replay: over Z the interval collapses to the singleton {-1}, so "
         "x=-1 and -2<x<0 are the same answer.",
         ("E02",), {"domain": "Z", "expected_solution": "{-1}"}),
        ("FW-D11", IN_G7_Q, "Q",
         "riješi nejednačinu $-3x<6$ isključivo u skupu racionalnih brojeva",
         "A012 replay: over Q the interval notation and the set-builder form "
         "are the same solution set.",
         ("A012",), {"domain": "Q", "expected_solution": "x>-2"}),
        ("FW-D12", IN_G6_FRAC, "N0",
         "riješi nejednačinu $x+1<4$ isključivo u skupu $N_0=\\{0,1,2,...\\}$",
         "N0 boundary control against FW-D09: the same inequality gains zero.",
         ("A020",), {"domain": "N0", "expected_solution": "{0,1,2}"}),
    ]
    for sid, (grade, oblast, topic), domain, ask, reason, targets, spec in domain_cases:
        rows.append(scenario(
            sid, grade, oblast, topic, reason,
            ["group_domain", "real_model", "final40", "domain_restricted",
             domain, "solve_set_adjudicated"],
            [solve_step(f"Kreiraj samostalan MCQ sa četiri opcije: {ask}. {SET_TAIL}")],
            targets=targets, spec=dict(spec, domain=domain,
                                       natural_policy="N excludes zero; N0 includes zero"),
        ))

    # ------------------------------------------------------------------
    # 2) ZAPIS / DUPLIKAT (8) — ekvivalentni zapisi ne smiju biti dvije opcije
    # ------------------------------------------------------------------
    representation_cases = [
        ("FW-S01", EQ_G9_PAREN,
         "riješi jednačinu $2(x-3)=x+5$",
         "B007: bare 11 and singleton {11} are the same answer and must never "
         "both be offered.", ("B007",)),
        ("FW-S02", EQ_G9_FRAC,
         "riješi jednačinu $\\frac{2x+1}{3}=\\frac{5}{3}$",
         "B012 repaired: the original paired a chained inequality with an "
         "equation lesson; the package-level defect (2 vs {2}) is kept as a "
         "coherent equation scenario on the same lesson.", ("B012",)),
        ("FW-S03", EQ_G9_FRAC,
         "riješi jednačinu $\\frac{x+1}{2}=\\frac{7}{3}$",
         "B013: linear numerator over a numeric denominator must be "
         "deterministically verified.", ("B013",)),
        ("FW-S04", EQ_G9_FRAC,
         "riješi jednačinu $\\frac{1}{\\frac{2}{3}}x=9$",
         "B014: the nested numeric coefficient reduces to 3/2, and 6 vs {6} "
         "are the same answer.", ("B014",)),
        ("FW-S05", IN_G9_PAREN,
         "riješi nejednačinu $2(x-1)<x+4$",
         "B017: a parenthesised inequality must engage the oracle; {6} is a "
         "member, not the solution set.", ("B017",)),
        ("FW-S06", IN_G7_ADD,
         "riješi nejednačinu $-3<x+1<-1$",
         "F008 class: a satisfying member must never publish as the complete "
         "solution set.", ("F008",)),
        ("FW-S07", IN_G7_ADD,
         "riješi nejednačinu $-5<x+1<-3$",
         "Singleton replay: {-5} is a member of (-6,-4), never the set.",
         ("F008",)),
        ("FW-S08", IN_G7_Q,
         "riješi nejednačinu $2x>19$ i zapiši skup rješenja intervalom",
         "Mixed-number and interval notation must be read exactly "
         "(9 1/2 is 19/2, never 91/2).", ("F008",)),
    ]
    for sid, (grade, oblast, topic), ask, reason, targets in representation_cases:
        rows.append(scenario(
            sid, grade, oblast, topic, reason,
            ["group_representation", "real_model", "final40",
             "solve_set_adjudicated", "duplicate_options"],
            [solve_step(
                f"Kreiraj samostalan MCQ sa četiri opcije: {ask}. Opcije smiju "
                f"miješati relacije, intervale, gole brojeve i skupove, ali "
                f"dva zapisa ISTOG skupa rješenja ne smiju biti dvije opcije. "
                f"{SET_TAIL}")],
            targets=targets,
        ))

    # ------------------------------------------------------------------
    # 3) FUNKCIJSKA TABELA (6) — C008 klasa (jedinstvenost slike ≠ injektivnost)
    # ------------------------------------------------------------------
    # Lekcija 6-10-007 nosi ugovor `graph_semantics`, pa poruka mora imenovati
    # GRAFIČKI sadržaj (tačke/koordinatni sistem) — što je i vjernije lekciji.
    FUNC = (6, "Relacije, preslikavanja i koordinatni sistem", "6-10-007")
    function_cases = [
        ("FW-F01",
         "je li slika elementa $1$ jedinstvena",
         "C008 replay: input 1 occurs once so f(1)=2 is unique; f(1)=f(3)=2 "
         "is a failure of injectivity, not of uniqueness.", ("C008",)),
        ("FW-F02",
         "kolika je vrijednost $f(3)$",
         "Direct image lookup from an explicit table of points.", ("C008",)),
        ("FW-F03",
         "predstavlja li ta tabela funkciju",
         "A relation is a function iff no input has two different images; two "
         "inputs sharing an image is allowed.", ("C008",)),
        ("FW-F04",
         "je li slika elementa $2$ jedinstvena",
         "Uniqueness of a second input, as a control against FW-F01.",
         ("C008",)),
        ("FW-F05",
         "koja je slika elementa $4$",
         "Image of the last listed input — the table must be read exactly.",
         ("C008",)),
        ("FW-F06",
         "predstavlja li ta tabela funkciju",
         "Function-vs-injectivity control: distinct inputs with the same image "
         "must still be a function.", ("C008",)),
    ]
    tables = ["$(1,2)$, $(2,3)$, $(3,2)$, $(4,5)$",
              "$(1,2)$, $(2,3)$, $(3,2)$, $(4,5)$",
              "$(1,2)$, $(2,3)$, $(3,2)$",
              "$(1,4)$, $(2,7)$, $(3,4)$, $(4,9)$",
              "$(1,2)$, $(2,3)$, $(3,2)$, $(4,5)$",
              "$(1,5)$, $(2,5)$, $(3,6)$"]
    for (sid, ask, reason, targets), table in zip(function_cases, tables):
        grade, oblast, topic = FUNC
        rows.append(scenario(
            sid, grade, oblast, topic, reason,
            ["group_function", "real_model", "final40", "function_table"],
            [solve_step(
                f"Kreiraj samostalan MCQ sa četiri opcije za lekciju o prikazu "
                f"funkcije: funkcija je zadana tačkama u koordinatnom sistemu "
                f"{table}. Pitaj {ask}. Osiguraj da je tačno jedna opcija "
                f"matematički tačna. Ne rješavaj zadatak učeniku.")],
            targets=targets,
        ))

    # ------------------------------------------------------------------
    # 4) GEOMETRIJA (6) — D005 klasa (koherentnost tvrdnje o djeliocu ugla)
    # ------------------------------------------------------------------
    ANGLE = (6, "Uglovi", "6-09-001")
    BISECTOR = (6, "Izometrijske transformacije i konstrukcije", "6-12-004")
    geometry_cases = [
        ("FW-G01", ANGLE,
         "Traži da učenik imenuje tjeme i krakove ugla $\\angle ABC$.",
         "Angle notation must be read correctly: vertex B, arms BA and BC.",
         ("D005",)),
        ("FW-G02", ANGLE,
         "Traži da učenik prepozna koji krak polazi iz tjemena ugla "
         "$\\angle BAC$.",
         "D005 step 2 class: a ray that does not start at the vertex can never "
         "divide that angle.", ("D005",)),
        ("FW-G03", ANGLE,
         "Traži da učenik odredi koji krak dijeli ugao $\\angle ABC$ na dva "
         "dijela.",
         "D005 step 4 class: a boundary arm of the angle cannot also be its "
         "interior divider.", ("D005",)),
        ("FW-G04", (7, "Ugao i trougao", "7-04-023"),
         "Traži da učenik izabere koji skup podataka jednoznačno određuje "
         "trougao za konstrukciju do kongruentnosti.",
         "E010 mitigation boundary: the blocking semantic contract must avoid "
         "or reject this known ambiguous construction-determination archetype. "
         "This probe does not claim a complete oracle for uniqueness of all "
         "triangle constructions.", ("E010",)),
        ("FW-G05", BISECTOR,
         "Traži zadatak o simetrali ugla i tački u kojoj se simetrale sijeku.",
         "E009 lesson content stays available: bisector and incenter tasks are "
         "legitimate here.", ("E009",)),
        ("FW-G06", BISECTOR,
         "Traži zadatak o udaljenosti centra upisane kružnice od stranica "
         "trougla.",
         "Second legitimate form of the same lesson — the contract must not "
         "over-block it.", ("E009",)),
    ]
    geometry_checks = ["published", "task_published", "task_self_contained",
                       "options_ok", "package_clean", "lesson_matches",
                       "stays_in_lesson", "geometry_ok", "math_safe",
                       "terminology_clean", "bosnian", "no_leak", "no_verdict",
                       "calls_at_most:2"]
    for sid, (grade, oblast, topic), ask, reason, targets in geometry_cases:
        is_e010 = sid == "FW-G04"
        checks = (geometry_checks if not is_e010 else
                  ["response_schema", "lesson_matches", "stays_in_lesson",
                   "package_clean", "geometry_ok", "math_safe",
                   "terminology_clean", "bosnian", "no_leak",
                   "calls_at_most:2"])
        tags = ["group_geometry", "real_model", "final40", "geometry_notation"]
        if is_e010:
            tags.extend(["e010_mitigation", "mitigation_not_full_oracle"])
        rows.append(scenario(
            sid, grade, oblast, topic, reason,
            tags,
            [solve_step(
                f"Kreiraj samostalan MCQ sa četiri opcije za izabranu lekciju. "
                f"{ask} Osiguraj da je tačno jedna opcija tačna. Ne rješavaj "
                f"zadatak učeniku.", checks=checks,
                rubrics=["refusal_quality"] if is_e010 else None)],
            alignment="lesson_overrides" if is_e010 else "must_follow",
            targets=targets,
        ))

    # ------------------------------------------------------------------
    # 5) VJERNOST ZAHTJEVU (4) — izričit uslov mora preživjeti do objave
    # ------------------------------------------------------------------
    fidelity_cases = [
        ("FW-R01", EQ_G9_EQUIV,
         "riješi jednačinu $x+2=2$ isključivo u skupu prirodnih brojeva "
         "$N=\\{1,2,3,...\\}$",
         "A020 request fidelity: the published task must keep N and must not "
         "silently switch to Z.", ("A020",)),
        ("FW-R02", IN_G7_Q,
         "riješi nejednačinu $x>3$, ali je u tekstu zadatka obavezno "
         "preoblikuj dodavanjem iste nenulte cijele konstante na obje strane; "
         "ne prepisuj relaciju doslovno",
         "Task 3 positive control: a syntactically distinct reformulation with "
         "the same canonical solution set must not be rejected as request "
         "drift. Exact A009 replay remains covered by FW-D04.", ("B003",)),
        ("FW-R03", IN_G7_Q,
         "riješi nejednačinu $\\frac{1}{3}<x\\le\\frac{5}{6}$ isključivo u "
         "skupu racionalnih brojeva",
         "A023 request fidelity: an inequality request must not be answered "
         "with an equation.", ("A023",)),
        ("FW-R04", IN_G7_MUL,
         "riješi nejednačinu $-2<x<0$ isključivo u skupu cijelih brojeva",
         "A010 request fidelity: the exact requested relation and domain must "
         "both survive.", ("A010",)),
    ]
    for sid, (grade, oblast, topic), ask, reason, targets in fidelity_cases:
        positive_equivalence = sid == "FW-R02"
        checks = list(SOLVE_CHECKS)
        if positive_equivalence:
            checks.append("request_equivalent_reformulation")
        tags = ["group_request_fidelity", "real_model", "final40",
                "request_fidelity", "solve_set_adjudicated"]
        if positive_equivalence:
            tags.append("request_equivalence_positive")
        rows.append(scenario(
            sid, grade, oblast, topic, reason,
            tags,
            [solve_step(f"Kreiraj samostalan MCQ sa četiri opcije: {ask}. "
                        f"{SET_TAIL}", checks=checks)],
            targets=targets,
        ))

    # ------------------------------------------------------------------
    # 6) REGRESIJA (4) — lekcijski prioritet, ruta i knjigovodstvo poziva
    # ------------------------------------------------------------------
    # E009/E006 klasa: zahtjev NAMJERNO izlazi iz lekcije. Bot mora ostati na
    # lekciji, i to je ISPUNJEN ugovor — nikad semantički kvar (E006 ispravka).
    rows.append(scenario(
        "FW-X01", 7, "Ugao i trougao", "7-04-022",
        "E009/E006 class: the student explicitly asks for congruence criteria "
        "on the angle-bisector lesson. The bot must stay on the lesson; "
        "staying is the contract, never a semantic false accept.",
        ["group_regression", "real_model", "final40", "lesson_priority"],
        [solve_step(
            "Napravi zadatak o podudarnosti trouglova i zahtijevaj "
            "odgovarajući kriterij, ne samo jednake površine.",
            checks=["response_schema", "lesson_matches", "stays_in_lesson",
                    "no_leak", "bosnian", "calls_at_most:2"],
            rubrics=["lesson_alignment", "refusal_quality"])],
        alignment="lesson_overrides", targets=("E009", "E006"),
    ))
    # Kanonska deterministička ruta: ovaj reprezentativni zadatak i sve njegove
    # provjere moraju ostati na tačno nula modelskih poziva. Raniji C001/C002
    # jednopozivni replay je redundantno pokriven posebnim live gateom i
    # klasifikacijskim testovima, pa ustupa mjesto izričito traženoj zero-call
    # kontroli bez povećanja broja scenarija.
    rows.append(scenario(
        "FW-X02", 6, "Djeljivost brojeva", "6-03-004",
        "Canonical deterministic divisibility generation must record the real "
        "zero-call route: no Tutor, no Reviewer and no fabricated package calls.",
        ["group_regression", "deterministic", "final40", "zero_call_control"],
        [{"kind": "text", "message": "Daj mi jedan zadatak za vježbu iz ove teme.",
          "expect_calls": 0,
          "checks": ["response_schema", "not_safe_error", "no_fallback_text",
                     "no_leak", "no_control_chars", "math_safe",
                     "terminology_clean", "bosnian", "published",
                     "task_published", "task_self_contained", "lesson_matches",
                     "stays_in_lesson", "options_ok", "numeric_consistent",
                     "package_clean", "no_verdict", "task_not_completed",
                     "zero_calls"],
          "rubrics": []}],
        targets=("deterministic_zero_call",),
    ))
    # Kompaktna trostepena ljestvica. Sintetički root/cascade scenario koji je
    # ranije bio ovdje već ima direktne offline klasifikacijske regresije; ova
    # zamjena čuva četiri regresijska scenarija i dodaje nedostajući live ugovor.
    rows.append(scenario(
        "FW-X03", 9, "Tačka, prava i ravan", "9-02-006",
        "Three-step hint contract: hints 1 and 2 must not reveal the final "
        "result; hint 3 must give a complete procedure and final result while "
        "preserving the active task. Each help turn uses at most one real call.",
        ["group_regression", "real_model", "final40", "hint_ladder"],
        [solve_step("Daj mi jedan samostalan MCQ zadatak iz ove lekcije."),
         {"kind": "text", "message": "Ne znam, daj mi prvi hint.",
          "intent": "hint_request", "interaction_phase": "practice_help",
          "send_last_task": True, "requires_active_task": True,
          "expect_calls": 1, "collect_help": True,
          "checks": ["response_schema", "not_safe_error", "no_fallback_text",
                     "no_leak", "math_safe", "terminology_clean", "bosnian",
                     "no_new_task", "task_preserved", "help_nonempty",
                     "hint_no_leak", "reveal_absent", "task_not_completed",
                     "calls_at_most:1"], "rubrics": []},
         {"kind": "text", "message": "Daj mi drugi hint.",
          "intent": "hint_request", "interaction_phase": "practice_help",
          "send_last_task": True, "requires_active_task": True,
          "expect_calls": 1, "collect_help": True,
          "checks": ["response_schema", "not_safe_error", "no_fallback_text",
                     "no_leak", "math_safe", "terminology_clean", "bosnian",
                     "no_new_task", "task_preserved", "help_nonempty",
                     "hint_no_leak", "hint_differs", "reveal_absent",
                     "task_not_completed", "calls_at_most:1"], "rubrics": []},
         {"kind": "text", "message": "Daj mi treći hint.",
          "intent": "hint_request", "interaction_phase": "practice_help",
          "send_last_task": True, "requires_active_task": True,
          "expect_calls": 1, "collect_help": True,
          "checks": ["response_schema", "not_safe_error", "no_fallback_text",
                     "no_leak", "math_safe", "terminology_clean", "bosnian",
                     "no_new_task", "task_preserved", "help_nonempty",
                     "solution_complete", "hint_differs", "reveal_absent",
                     "task_not_completed", "calls_at_most:1"], "rubrics": []}],
        targets=("hint_ladder_contract",),
    ))
    # Obični nastavak ne smije naslijediti raniji izričit uslov (Task 3, §10).
    rows.append(scenario(
        "FW-X04", 7, "Cijeli brojevi", "7-02-019",
        "Request fidelity must not become sticky: an explicit domain in turn 1 "
        "must not be enforced against an ordinary follow-up in turn 2.",
        ["group_regression", "real_model", "final40", "no_sticky_constraint"],
        [solve_step("Kreiraj samostalan MCQ sa četiri opcije: riješi "
                    "nejednačinu $x+1<4$ isključivo u skupu cijelih brojeva. "
                    + SET_TAIL),
         {"kind": "text", "message": "Daj mi drugi zadatak.", "expect_calls": 2,
          "requires_active_task": True,
          "checks": ["published", "task_published", "task_differs",
                     "package_clean", "calls_at_most:2"],
          "rubrics": ["lesson_alignment"]}],
        targets=("A020",),
    ))
    return rows


def main():
    rows = build()
    OUT.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8")
    print(f"wrote {len(rows)} scenarios to {OUT}")


if __name__ == "__main__":
    main()
