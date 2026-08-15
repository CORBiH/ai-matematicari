"""Taksonomija ISHODA scenarija — šta je zaista dokazano, a šta samo izgleda tako.

ZAŠTO POSTOJI (korijenski uzrok RC11): u talasu discovery-100 sirovi
PASS/FAIL status nije razlikovao četiri suštinski različite stvari:

  • proizvod je objavio POGREŠAN sadržaj                → pravi kvar;
  • proizvod je SIGURNO odbio objavu                    → ispravno ponašanje;
  • scenario je bio nevaljan (poruka protiv lekcije)    → kvar harnessa;
  • kasniji korak je pao ZBOG ranijeg sigurnog odbijanja → posljedica, ne kvar.

Zbog toga su tri klase pogrešnih zaključaka ušle u forenziku:

  1. E006: lekcija „Podudarnost trouglova - SUS“, zahtjev VAN lekcije, bot je
     ostao na lekciji — što je namjerno produktno ponašanje (lekcija je
     vlasnik vježbe). Evaluator je to brojao kao `SEMANTIC_FALSE_ACCEPT`.
  2. B012: scenario JESTE bio nekoherentan, ali je objavljen paket NEZAVISNO
     sadržavao stvaran kvar — opcije `2` i `{2}` su isti odgovor. Da je
     nekoherentnost scenarija automatski poništila cio zapis, taj dokaz bi
     bio izgubljen. (Kvar je stvarno postojao i zatvoren je u Task 1.)
  3. C001/C002: sankcionisana jednopozivna ruta nije imala isti strukturni
     zapis paketa kao univerzalna, pa je izgledala kao rupa u pokrivenosti.

PRAVILA KOJA IZ TOGA SLIJEDE (i koja ovaj modul provodi):

  • Dokaz na NIVOU PAKETA preživljava nevaljan scenario. Nekoherentnost
    obara OČEKIVANJE scenarija, ne mjerenje objavljenog sadržaja.
  • Sigurno odbijanje objave NIKAD ne postaje „pogrešan sadržaj“.
  • Kad zahtjev namjerno izlazi iz lekcije, a bot ostane na lekciji, to je
    ISPUNJEN ugovor — nikad semantički kvar.
  • Poslije prvog sigurnog odbijanja kasnija odstupanja stanja su POSLJEDICA
    i označavaju se kao takva; ne broje se kao nezavisni kvarovi.

Modul je čist: prima već snimljene zapise i vraća klasifikaciju. Ne poziva
model, ne dira proizvod i ne mijenja nijedno očekivanje.
"""
from __future__ import annotations

from tools.practice_eval import coherence

# --- klase ishoda ----------------------------------------------------------
PRODUCT_CORRECTNESS_FAILURE = "PRODUCT_CORRECTNESS_FAILURE"
COVERAGE_GAP = "COVERAGE_GAP"
SAFE_FAIL_CLOSED = "SAFE_FAIL_CLOSED"
HARNESS_INVALID_SCENARIO = "HARNESS_INVALID_SCENARIO"
CASCADE_ONLY = "CASCADE_ONLY"
EVALUATOR_MISCLASSIFICATION = "EVALUATOR_MISCLASSIFICATION"
INFRA_SDK = "INFRA_SDK"
TIMEOUT = "TIMEOUT"
CLEAN = "CLEAN"

OUTCOME_CLASSES = (
    PRODUCT_CORRECTNESS_FAILURE, COVERAGE_GAP, SAFE_FAIL_CLOSED,
    HARNESS_INVALID_SCENARIO, CASCADE_ONLY, EVALUATOR_MISCLASSIFICATION,
    INFRA_SDK, TIMEOUT, CLEAN,
)

# --- rute izvršavanja (istinito knjigovodstvo poziva) ----------------------
ROUTE_UNIVERSAL_TWO_CALL = "universal_two_call"
ROUTE_DETERMINISTIC = "deterministic_zero_call"
ROUTE_NO_MODEL_TURN = "no_model_turn"
ROUTES = (ROUTE_UNIVERSAL_TWO_CALL, ROUTE_DETERMINISTIC,
          ROUTE_NO_MODEL_TURN)

# Provjere koje mjere SADRŽAJ OBJAVLJENOG PAKETA. Njihov pad je dokaz o
# proizvodu i preživljava nevaljan scenario (pravilo B012).
PACKAGE_LEVEL_CHECKS = frozenset({
    "options_ok", "package_clean", "task_self_contained", "numeric_consistent",
    "math_safe", "geometry_ok", "terminology_clean", "no_leak",
    "no_control_chars", "bosnian", "request_equivalent_reformulation",
})

# Provjere koje padaju kad turn NIJE objavio. Same po sebi ne dokazuju
# pogrešan SADRŽAJ — samo da objave nije bilo.
_PUBLICATION_CHECKS = frozenset({
    "published", "task_published", "not_safe_error", "no_fallback_text",
})

# Provjere stanja sesije/težine: poslije ranijeg sigurnog odbijanja njihovo
# odstupanje je posljedica, ne nezavisan kvar.
_STATE_CHECKS = frozenset({
    "task_preserved", "no_new_task", "task_completed", "task_not_completed",
    "correct_option_stable", "state_unchanged", "task_differs",
    "hint_differs", "help_nonempty", "reveal_present", "reveal_absent",
    "solution_complete", "verdict_correct", "verdict_incorrect",
})


def _is_level_check(name: str) -> bool:
    return str(name).startswith("level:")


def turn_route(turn) -> str:
    """Stvarna ruta jednog turna — iz SNIMLJENIH vrsta poziva, nikad iz plana.

    Knjigovodstvo ostaje istinito: nula poziva se nikad ne predstavlja kao
    dva, a treći poziv se ne skriva (vidi `third_call_violations`)."""
    kinds = tuple((turn or {}).get("sdk_call_kinds") or ())
    calls = int((turn or {}).get("sdk_calls") or 0)
    if calls == 0:
        # Preskočen preduslov nije ruta izvršavanja.
        if (turn or {}).get("precondition_unmet"):
            return ROUTE_NO_MODEL_TURN
        return ROUTE_DETERMINISTIC
    if "tutor_turn" in kinds or "reviewer_turn" in kinds:
        return ROUTE_UNIVERSAL_TWO_CALL
    return ROUTE_NO_MODEL_TURN


def third_call_violations(record) -> list:
    """Turnovi koji su potrošili više od dva modela poziva — nikad se ne krije.

    Dvopozivna granica je produktno pravilo (CLAUDE.md, pravilo 4); evaluator
    ga mjeri iz stvarno snimljenih poziva."""
    violations = []
    for turn in (record or {}).get("turns") or ():
        calls = int(turn.get("sdk_calls") or 0)
        if calls > 2:
            violations.append({"step": turn.get("step_index"), "sdk_calls": calls,
                               "kinds": list(turn.get("sdk_call_kinds") or ())})
    return violations


def _failed_checks(record) -> list:
    return list((record or {}).get("failed_checks") or ())


def _publication_blocked_steps(record) -> set:
    """Koraci na kojima objava NIJE prošla (sigurno odbijanje ili pad)."""
    blocked = set()
    for turn in (record or {}).get("turns") or ():
        for result in turn.get("check_results") or ():
            if (result.get("name") in _PUBLICATION_CHECKS
                    and result.get("outcome") == "fail"):
                blocked.add(turn.get("step_index"))
    return blocked


def package_evidence(record) -> list:
    """Padovi koji mjere SADRŽAJ uhvaćenog kandidata/paketa.

    ŽIVI B012: scenario je bio nekoherentan, ali su objavljene opcije `2` i
    `{2}` bile isti odgovor. Taj dokaz se NE SMIJE izgubiti zato što je drugo
    polje scenarija bilo pokvareno. FINAL40: nalaz o kandidatu koji je sigurno
    odbijen prije objave takođe ostaje vidljiv, ali sam po sebi više ne tvrdi
    da je pogrešan sadržaj stigao do učenika."""
    turns_by_step = {
        turn.get("step_index"): turn
        for turn in (record or {}).get("turns") or ()
    }
    evidence = []
    for entry in _failed_checks(record):
        if entry.get("check") not in PACKAGE_LEVEL_CHECKS:
            continue
        turn = turns_by_step.get(entry.get("step"))
        # New records state this explicitly. Keep older artifacts that predate
        # `package_captured` readable, but never turn an SDK failure with an
        # explicitly absent package into product-package evidence.
        if turn is not None and turn.get("package_captured") is False:
            continue
        evidence.append(entry)
    return evidence


def _turn_has_committed_task_state(turn) -> bool:
    """Je li odbijeni turn ipak ostavio dokaz objavljenog/aktivnog zadatka.

    Klasifikacija je namjerno konzervativna. Bez snimka praznog stanja i
    odgovora bez objavljenog zadatka kandidat se ne proglašava sigurnim samo
    zato što je jedna publikacijska provjera pala.
    """
    turn = turn or {}
    if "response" not in turn or "session_after_summary" not in turn:
        return True
    response = turn.get("response") or {}
    if response.get("status") == "ready" or response.get("last_tutor_task"):
        return True
    summary = turn.get("session_after_summary") or {}
    if int(summary.get("current_task_chars") or 0) > 0:
        return True
    return any(summary.get(key) for key in (
        "task_signature_hash", "correct_option_id", "marked_option_text",
        "expected_answer",
    ))


def safely_rejected_package_steps(record, evidence=None) -> set:
    """Koraci s lošim kandidatom koji dokazivo nisu ni objavili ni commitovali.

    Potrebni su svi dokazi FINAL40 ugovora: paket je uhvaćen, publikacija je
    pala i poslije turna nema aktivnog/objavljenog zadatka. Nalaz o paketu se
    NE briše; ova funkcija samo razlikuje kandidata od objavljenog paketa.
    """
    record = record or {}
    evidence = package_evidence(record) if evidence is None else evidence
    evidence_steps = {entry.get("step") for entry in evidence}
    blocked_steps = _publication_blocked_steps(record)
    turns_by_step = {
        turn.get("step_index"): turn for turn in record.get("turns") or ()
    }
    safe = set()
    for step in evidence_steps & blocked_steps:
        turn = turns_by_step.get(step) or {}
        if turn.get("package_captured") is not True:
            continue
        if _turn_has_committed_task_state(turn):
            continue
        # Protivrječan zapis (npr. `published=pass` i drugi publication FAIL)
        # nije dovoljan dokaz sigurnog odbijanja.
        if any(result.get("name") in ("published", "task_published")
               and result.get("outcome") == "pass"
               for result in turn.get("check_results") or ()):
            continue
        safe.add(step)
    return safe


def cascade_split(record) -> dict:
    """Podijeli padove na KORIJENSKE i POSLJEDIČNE.

    Posljedica je pad provjere stanja/težine na koraku KASNIJEM od prvog
    koraka na kojem objava nije prošla. Takav pad nije nezavisan kvar —
    zadatak koji je sigurno odbijen ne može kasnije nositi očekivano stanje.
    Dokaz na nivou PAKETA nikad nije posljedica: on mjeri sadržaj koji je
    stvarno objavljen na tom koraku."""
    blocked = _publication_blocked_steps(record)
    root_step = min(blocked) if blocked else None
    root, cascade = [], []
    for entry in _failed_checks(record):
        step = entry.get("step")
        name = entry.get("check")
        is_state = name in _STATE_CHECKS or _is_level_check(name)
        if (root_step is not None and isinstance(step, int) and step > root_step
                and is_state and name not in PACKAGE_LEVEL_CHECKS):
            cascade.append(entry)
        else:
            root.append(entry)
    return {"root_step": root_step, "root": root, "cascade": cascade}


def lesson_priority_honoured(record) -> bool:
    """True kad je bot OSTAO na izabranoj lekciji na svakom izvršenom koraku.

    ŽIVI E006: zahtjev je bio van lekcije, bot je ostao na lekciji, i to je
    ISPUNJEN ugovor — `lesson_matches`/`stays_in_lesson` moraju biti čisti."""
    saw_check = False
    for turn in (record or {}).get("turns") or ():
        for result in turn.get("check_results") or ():
            if result.get("name") in ("stays_in_lesson", "lesson_matches"):
                if result.get("outcome") == "fail":
                    return False
                if result.get("outcome") == "pass":
                    saw_check = True
    return saw_check


def classify(record, scenario=None) -> dict:
    """Vrati strukturisanu klasifikaciju jednog `ScenarioRecord` rječnika.

    `scenario` (opcionalno) omogućava provjeru koherentnosti; bez njega se
    nekoherentnost ne tvrdi."""
    record = record or {}
    status = str(record.get("status") or "")
    coherence_issues = (coherence.coherence_problems(scenario)
                        if scenario is not None else [])
    alignment = (coherence.alignment_of(scenario) if scenario is not None
                 else coherence.ALIGNMENT_MUST_FOLLOW)
    evidence = package_evidence(record)
    split = cascade_split(record)
    routes = sorted({turn_route(turn) for turn in record.get("turns") or ()})
    third_calls = third_call_violations(record)
    safe_package_steps = safely_rejected_package_steps(record, evidence)
    published_package_evidence = [
        entry for entry in evidence if entry.get("step") not in safe_package_steps
    ]
    independent_roots = [
        entry for entry in split["root"]
        if entry.get("step") not in safe_package_steps
    ]

    notes = []
    if status == "TIMEOUT":
        outcome = TIMEOUT
    elif status in ("INFRA_ERROR", "RATE_LIMITED"):
        outcome = INFRA_SDK
    elif third_calls:
        # Prekoračena granica poziva je uvijek produktni kvar i nikad se ne
        # sakriva iza nevaljanog scenarija.
        outcome = PRODUCT_CORRECTNESS_FAILURE
        notes.append("call budget exceeded — more than two model calls in a turn")
    elif published_package_evidence:
        # PRAVILO B012: dokaz o paketu ima prednost nad svime osim infrastrukture
        # i prekoračenja poziva — čak i kad je scenario nevaljan.
        outcome = PRODUCT_CORRECTNESS_FAILURE
        if coherence_issues:
            notes.append("scenario is invalid, but the published package "
                         "independently fails package-level checks")
    elif coherence_issues:
        outcome = HARNESS_INVALID_SCENARIO
        notes.extend(coherence_issues[:4])
    elif evidence and safe_package_steps and not independent_roots:
        outcome = SAFE_FAIL_CLOSED
        notes.append(
            "candidate package findings preserved; publication was rejected "
            "before task/session state was committed")
    elif not split["root"] and split["cascade"]:
        outcome = CASCADE_ONLY
        notes.append(f"all failures follow the safe block at step {split['root_step']}")
    elif split["root"]:
        # Preostali korijenski padovi bez dokaza o paketu: ako su svi
        # publikacijski, objava je odbijena — sigurno, ne pogrešan sadržaj.
        publication_only = all(entry.get("check") in _PUBLICATION_CHECKS
                               for entry in split["root"])
        if publication_only:
            outcome = SAFE_FAIL_CLOSED
            notes.append("publication was blocked; no wrong content reached the student")
        else:
            outcome = PRODUCT_CORRECTNESS_FAILURE
    elif status == "REVIEW":
        outcome = COVERAGE_GAP
        notes.append("nothing could be proven — skipped checks or rubrics only")
    else:
        outcome = CLEAN

    # ISPRAVKA E006: zahtjev van lekcije + bot ostao na lekciji NIJE semantički
    # kvar. Zabilježi to izričito da izvještaj ne može ponovo pogriješiti.
    if (alignment == coherence.ALIGNMENT_LESSON_OVERRIDES
            and lesson_priority_honoured(record)):
        notes.append("lesson priority honoured over an off-lesson request — "
                     "this is the intended contract, never a semantic false accept")

    return {
        "outcome_class": outcome,
        "routes": routes,
        "third_call_violations": third_calls,
        "package_evidence": evidence,
        "root_failures": split["root"],
        "cascade_failures": split["cascade"],
        "root_step": split["root_step"],
        "coherence_problems": coherence_issues,
        "request_alignment": alignment,
        "lesson_priority_honoured": lesson_priority_honoured(record),
        "notes": notes,
    }


def semantic_false_accept(record, scenario=None) -> bool:
    """Je li ovo DOKAZAN semantički kvar (bot napustio lekciju)?

    ISPRAVKA E006: sonda u kojoj bot ostane na lekciji NIKAD nije kvar; pravi
    drift (živi E009 — bot je poslušao zahtjev van lekcije) i dalje jeste, jer
    ga hvata pad `stays_in_lesson`/`lesson_matches` ili semantički ugovor
    lekcije u `package_clean`."""
    for entry in _failed_checks(record):
        if entry.get("check") in ("stays_in_lesson", "lesson_matches"):
            return True
        if entry.get("check") == "package_clean" and "semantic_fidelity_violation" in str(
                entry.get("detail") or ""):
            return True
    return False
