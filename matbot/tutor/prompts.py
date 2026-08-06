"""JEDAN univerzalni prompt za svih 534 lekcije — bez teksta po lekciji.

Identitet lekcije ulazi kao KONTEKST (LessonContext), nikad kao posebna grana i
nikad kao ručno napisan prompt po lekciji. Zajednička matematička/jezička
pravila se ne dupliraju: dolaze iz `matbot/rules.py`, isto kao i ranije, pa
terminologija i notacija ostaju identične u sva tri moda.

Dva prompta:
  • `build_tutor_*`    — nacrt (namjera + odgovor + eventualan zadatak),
  • `build_reviewer_*` — NEZAVISNA provjera i konačan payload.

Recenzent NAMJERNO ne dobija „odobri ako izgleda dobro“ ton: traži se da sam
riješi zadatak prije nego što išta odobri.
"""
from matbot.lesson_fidelity import semantic_task_requirement
from matbot.rules import build_shared_math_rules
from matbot.tutor.schema import INTENTS

_MAX_HISTORY_TURNS = 3
_CLIP = 220


def _clip(text, limit=_CLIP):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


# Šta SLJEDEĆI hint mora donijeti, s obzirom na broj već datih hintova.
# ZAŠTO POSTOJI (ručni test, 2026-08-03): prompt je modelu slao samo BROJ
# hintova, bez ijedne riječi o tome šta se na tom nivou očekuje — pa je
# ponovljeno „Ne znam“ vraćalo istu neupotrebljivu najavu. Zatečeni
# jednopozivni put je ovu ljestvicu imao (matbot/prompts.py); univerzalni ju je
# pri pivotu izgubio.
_HINT_LEVEL_GUIDANCE = {
    0: ("SLJEDEĆI HINT JE NIVO 1: usmjeri na PRVI KORAK (koje pravilo ili "
        "operacija se primjenjuje), BEZ računa i BEZ rezultata."),
    1: ("SLJEDEĆI HINT JE NIVO 2: daj KONKRETAN međukorak — tačno koji račun "
        "treba izvesti — ali JOŠ BEZ konačnog rezultata."),
    2: ("SLJEDEĆI HINT JE NIVO 3: pokaži CIJELI postupak i konačan rezultat; "
        "učenik je već dva puta zapeo."),
}


def _hint_level_guidance(hint_level):
    guidance = _HINT_LEVEL_GUIDANCE.get(hint_level, _HINT_LEVEL_GUIDANCE[2])
    return (guidance + " Svaki hint mora donijeti NOVU informaciju u odnosu na "
            "prethodni — nikad ne ponavljaj raniji hint drugim riječima.")


def _lesson_block(context):
    """Kanonski identitet + zatečeni metapodaci lekcije. Isti oblik za svih 534."""
    lines = [
        "KANONSKA LEKCIJA (ne izlazi iz nje ni pod kojim uslovom):",
        f"- razred: {context.grade}",
        f"- oblast: {context.oblast} ({context.oblast_id})",
        f"- lekcija: {context.title} ({context.topic_id})",
    ]
    if context.primary_family:
        label = context.family_description or context.primary_family
        lines.append(f"- tipičan oblik zadatka za ovu lekciju: {label}")
    if len(context.families) > 1:
        lines.append("- prihvatljivi oblici: " + ", ".join(context.families))
    if context.has_contract:
        lines.append(f"- deklarisana vještina: {context.skill}")
        if context.allowed_operations:
            lines.append("- dozvoljene operacije: " + ", ".join(context.allowed_operations))
        for key, value in sorted(context.operand_constraints.items()):
            lines.append(f"- ograničenje: {key} = {value}")
    if context.lesson_scope:
        lines.append(f"- lesson scope/objectives: {context.lesson_scope}")
    elif context.objectives:
        lines.append("- lesson objectives: " + "; ".join(context.objectives))
    else:
        lines.append("- scope note: title-only lesson; grade, area and exact title are the minimum semantic anchor. Do not broaden to the entire area.")
    if context.exclusions:
        lines.append("- exclusions: " + "; ".join(context.exclusions))
    return "\n".join(lines)


def _state_block(session, student_message, trusted_verdict=None):
    """Trenutno stanje vježbe — serverska istina, ne modelovo sjećanje."""
    lines = ["STANJE VJEŽBE:"]
    if session["current_task"]:
        lines.append(f"- AKTIVNI ZADATAK: {session['current_task']}")
        if session["current_options"]:
            lines.append("- PONUĐENE OPCIJE (učenik ih vidi ovim redom):")
            for option in session["current_options"]:
                lines.append(f"  {option['id']}) {option['text']}")
        if session["expected_answer_summary"]:
            lines.append(
                f"- INTERNI TAČAN ODGOVOR (učenik ga NE vidi): {session['expected_answer_summary']}"
            )
        lines.append(f"- TEŽINA AKTIVNOG ZADATKA: {session['difficulty']}")
        lines.append(f"- BROJ VEĆ DATIH HINTOVA: {session['hint_level']}")
        lines.append("- " + _hint_level_guidance(session["hint_level"]))
    else:
        lines.append("- AKTIVNI ZADATAK: ne postoji (učenik još nije dobio zadatak)")

    lines.append(f"- SERVER COMMITTED DIFFICULTY LEVEL: {session.get('difficulty_level', 1)}")

    if session["recent_tasks"]:
        lines.append("- NEDAVNI ZADACI (ne ponavljaj iste brojeve ni isti obrazac):")
        for task in session["recent_tasks"][-_MAX_HISTORY_TURNS:]:
            lines.append(f"  • {_clip(task)}")

    if session["recent_turns"]:
        lines.append("- KRATKA HISTORIJA RAZGOVORA:")
        for turn in session["recent_turns"][-_MAX_HISTORY_TURNS:]:
            lines.append(f"  Učenik: {_clip(turn['student'])}")
            lines.append(f"  Ti: {_clip(turn['tutor'])}")

    if trusted_verdict is not None:
        # Klik na opciju: tačnost je SERVERSKA činjenica, ne procjena modela.
        lines.append(
            f"- UČENIK JE KLIKNUO OPCIJU: „{trusted_verdict['selected_text']}“"
        )
        lines.append(
            f"- SERVER JE UTVRDIO DA JE TAJ IZBOR: "
            f"{'TAČAN' if trusted_verdict['is_correct'] else 'NETAČAN'} "
            f"(ranijih pogrešnih pokušaja: {trusted_verdict['wrong_attempts']}). "
            f"Ovu činjenicu NE SMIJEŠ osporiti."
        )
    lines.append(f"- NOVA PORUKA UČENIKA: „{_clip(student_message, 400)}“")
    return "\n".join(lines)


_INTENT_GUIDE = """ODREDI NAMJERU (tačno jedna vrijednost polja `intent`):
- generate_task — traži (prvi) zadatak, ili zadatak postoji ali traži drugi iste težine
- easier_task — izričito traži LAKŠI zadatak
- harder_task — izričito traži TEŽI zadatak
- next_task — riješio je i ide dalje
- answer_attempt — poruka JESTE pokušaj odgovora na aktivni zadatak
- hint_request — traži pomoć/uputu, uključujući „ne znam“, „ne razumijem“, „pomozi“
- explanation_request — traži objašnjenje pojma ili postupka, bez novog zadatka
- full_solution_request — izričito traži cijelo rješenje („uradi ga ti“, „pokaži postupak“)
- clarification — pita nešto o zadatku/lekciji što nije ništa od gornjeg
- off_topic — poruka nije o matematici ove lekcije

„NE ZNAM“ NIKAD NIJE `answer_attempt`. To je `hint_request`: daj sljedeći hint
po nivou, bez ocjenjivanja i bez otkrivanja rješenja."""

_FIELD_RULE = """PRAVILO POLJA (server odbija payload koji ga prekrši):
- generate_task / easier_task / harder_task / next_task → `new_task` OBAVEZAN
- svaka druga namjera → `new_task` mora biti null
- easier_task / harder_task → `difficulty_diagnostics` OBAVEZNA
- hint_request → `hint` obavezan; full_solution_request → `worked_solution` obavezan
- answer_attempt → `grading` obavezan; svaka druga namjera → `grading` null
- `lesson_focus` uvijek popuni: koju tačno vještinu izabrane lekcije ovaj turn cilja

KAKO SE HINT I RJEŠENJE PRIKAZUJU (bitno):
`hint` i `worked_solution` su POLJA KOJA UČENIK ČITA — server ih dopisuje uz
`reply`. Zato `hint` mora sadržavati STVARNU pomoć, a ne najavu. Ne piši
„evo ti uputa“ i ne ostavljaj `hint` prazan kad je zatražena pomoć: napiši
konkretnu uputu koja pomjera učenika za jedan korak."""

# ŽIVI NALAZ B45 (lekcija o dijeljenju decimalnih brojeva, treći hint):
# napisano je `$7,5\\cdot 10:5\\cdot 10 = 75:50$`. Po standardnom prioritetu to
# je `((7,5·10):5)·10 = 150`, pa je numerički verifikator ispravno odbio objavu.
# Model je mislio `(7,5·10):(5·10)`, ali to nije zapisao. Validator se ne dira —
# zapis mora biti nedvosmislen.
_SCALED_DIVISION_RULE = """WHEN YOU SCALE BOTH SIDES OF A DIVISION:
If you scale both the dividend and the divisor by the same number to remove a decimal
comma, write it with parentheses or as a fraction — never as a bare chain.
  correct:   $(7,5\\cdot 10):(5\\cdot 10) = 75:50$   or   $\\frac{7,5\\cdot 10}{5\\cdot 10}$
  rejected:  $7,5\\cdot 10:5\\cdot 10 = 75:50$
Without the parentheses the expression means $((7,5\\cdot 10):5)\\cdot 10 = 150$, the
server's numeric verifier reads exactly that, and the whole turn is discarded."""


_TASK_RULE = """KAD PRAVIŠ ZADATAK:
- zadatak mora ispitivati BAŠ izabranu lekciju, ne samo istu oblast
- tačno 4 opcije; TAČNO JEDNA je tačna; nijedne dvije ne smiju značiti istu vrijednost
- `correct_option_index` je indeks tačne opcije (0-3) u nizu koji si napisao
- `expected_answer` je tačan odgovor, isti kao tekst tačne opcije
- pogrešne opcije moraju biti uvjerljive greške, ne nasumični brojevi
- NE otkrivaj koja je opcija tačna u tekstu `reply`"""

# POLAZNA SLOŽENOST — namjerno kratko i apsolutno pravilo, ne sistem težine.
# ZAŠTO POSTOJI (ručni test, 2026-08-03): prvi zadatak uvodne lekcije 6. razreda
# dobio je nepotrebno velike brojeve. Relativna procjena recenzenta („je li teže
# nego prije“) tu ne pomaže — na PRVOM zadatku nema s čim porediti. Zato prag
# polazi apsolutno, a raste tek na zahtjev ili nakon uspjeha.
_STARTING_COMPLEXITY_RULE = """POLAZNA SLOŽENOST (obavezno):
- PRVI zadatak u lekciji i svaki `generate_task` bez tražene veće težine MORA
  biti JEDNOSTAVAN ULAZNI primjer: mali, školski brojevi (po pravilu do 20, a
  najviše do 100), jedan korak, bez nagomilanih uslova.
- Uvodne lekcije („pojam“, „prepoznavanje“, prvi susret s pravilom) počinju od
  najmanjih smislenih brojeva — cilj je da učenik prepozna pravilo, ne da računa
  velike brojeve.
- Složenost raste SAMO kad učenik izričito traži teže (`harder_task`) ili kad je
  iz historije vidljivo da je prethodne zadatke riješio tačno.
- Kad nisi siguran koliko veliko je previše — uzmi manje. Prejednostavan uvodni
  zadatak je bezopasan; prevelik odbija učenika."""

_DIFFICULTY_RULE = """KAD MIJENJAŠ TEŽINU (easier_task / harder_task):
Uporedi s PRETHODNIM zadatkom i u `difficulty_diagnostics` označi svaku dimenziju
kao lower/same/higher: number_magnitude, number_of_steps, representation_complexity,
sign_complexity, scaffolding, distractor_closeness, reasoning_depth.
- „lakše“ znači da BAR JEDNA dimenzija ide `lower`, nijedna `higher`
- „teže“ znači da BAR JEDNA ide `higher`, nijedna `lower`
- VJEŠTINA I LEKCIJA SE NE MIJENJAJU — mijenja se samo koliko je zahtjevno
- u `rationale` kratko napiši šta si konkretno promijenio
Ova dijagnostika je INTERNA: učenik je ne vidi."""


_STRUCTURED_TASK_RULE = """STRUCTURED TASK PACKAGE (required for every `new_task`):
- selected_lesson_id exactly matches the canonical lesson; include selected_lesson_title for readability, but the server owns and canonicalizes that display copy;
- target_difficulty_level is 1 for the first task, shifts one bounded step for easier/harder, and otherwise stays at the committed level;
- options use unique IDs a, b, c, d; correct_option_id identifies the correct visible option and agrees with correct_option_index;
- for a multiple-choice task, expected_answer is an exact copy of that marked option's text. Put explanation, derivation, unit commentary, and reasoning only in solution;
- provide task_type, expected_answer, complete solution, difficulty_evidence, and task_signature;
- difficulty_evidence describes the actual task: Level 1 is one direct introductory application and may be yes/no, recognition, direct calculation, classification, substitution, or selection. Do not count choosing a visible option by one rule as comparison or a second reasoning step. Level 2 permits two connected conditions/operations or two related rules/concepts, straightforward explanation/comparison, or one manageable representation change; `combines_concepts` alone is not Level 3. Level 3 requires construction, proof, or three-or-more connected requirements/operations, advanced representation change, or comparable depth;
- task_signature describes mathematical structure (family, operation/relation, normalized parameters, conditions, objects, answer type), not wording. normalized_parameters is a list of entries with exactly name and value; values are canonical strings, never arbitrary metadata. Rewording, option order, or parameter-list order must not change it."""


# CILJANI NIVO — univerzalno pravilo, isto za svih 534 lekcije.
# ZAŠTO POSTOJI (živi gate cb80b92): za traženi nivo 1 model je napravio zadatak
# koji iz tri zadate stranice izvodi poluprečnik opisane kružnice — višekorakan
# račun koji je samo OZNAČEN kao nivo 1. Recenzent je nezavisno izračunao
# steps=3/operations=4 i ipak vratio `approve`, pa je turn propao. Pravilo je
# zato apsolutno i bez ijedne riječi o konkretnoj lekciji, figuri ili oblasti:
# nivo 1 je DIREKTNA primjena, a izvedeni višekorakan račun tu ne spada.
_TARGET_LEVEL_RULE = """TARGET DIFFICULTY LEVEL (universal; identical for every lesson):
Level 1 must be a GENUINELY DIRECT introductory task built on the selected lesson:
recognizing a definition, identifying a named object or property, applying one
directly stated fact, one simple calculation, or a one-rule classification or
selection. One reasoning step, one condition, one operation, no representation change.
- Never derive a multi-step result and label it Level 1. When a task needs a chain
  of rules or formulas, several intermediate quantities, or a value computed from
  other computed values, it is NOT Level 1: replace it with a direct application of
  this lesson, or place it at the level it truly belongs to.
- A lesson whose title is conceptual is still introduced directly — ask what the
  named object or property IS, or apply the single stated fact once, instead of
  computing something derived from it.
Level 1 tolerates one change of representation and up to two connected operations when
they belong to that single direct application — converting $0,5$ into a fraction, or
substituting a value and computing once, is still introductory.
Level 2 is a bounded combination of up to two related rules, conditions, operations,
or one manageable representation change.
Level 3 requires construction, proof or justification, three or more connected
requirements/operations, or an advanced representation change.
A harder request moves ONE bounded step up from the committed level — Level 1 to Level 2,
never straight to a proof or a three-step derivation. Overshooting the requested level is
rejected exactly like undershooting it.
`difficulty_evidence` must honestly describe the task you actually wrote. An
independent reviewer recomputes it from the visible task and the server rejects the
turn when that evidence does not satisfy the requested level — writing above the
requested level loses the turn, it is not a shortcut."""


# ŽIVI RELEASE GATE (commit 0883e8c, scenario `fresh_level1`): za lekciju o
# pravilima djeljivosti Tutor je predložio „Koji od ponuđenih brojeva je djelilac
# broja 84?“ — traženje faktora umjesto primjene pravila. Zahtjev koji
# `lesson_fidelity` deterministički izvodi iz NASLOVA lekcije (nikad iz ID-a)
# legacy put je slao u prompt, a univerzalni ga pri pivotu nije preuzeo, pa Tutor
# za njega nije ni znao. Ovdje ulazi kao KONTEKST lekcije, isto kao ostali
# metapodaci — bez ijedne grane po lekciji.
#
# Recenzentov prompt NAMJERNO ostaje univerzalan: njemu isti zahtjev stiže samo
# kad je stvarno prekršen, kao precizan nalaz iz `package_preflight`
# (`divisibility_rules_not_required_by_visible_task` + objašnjenje). Tako se
# lekcijska proza ne ubacuje u svaki drugi poziv, a nalaz je konkretan.
def _semantic_requirement_block(context):
    requirement = semantic_task_requirement(context.title)
    return requirement.prompt_block + "\n\n" if requirement is not None else ""


# UGOVOR PORODICE (Faza 4A). Tekst se NE sastavlja ovdje — dolazi gotov iz
# kompajliranog artefakta, pa Tutor i Recenzent doslovno ne mogu dobiti dvije
# različito formulisane verzije istog ugovora. Lekcija bez ugovora vraća prazan
# string i prompt ostaje bajt za bajt kao prije.
def _semantic_contract_block(context):
    contract = getattr(context, "semantic_contract", None)
    if contract is None:
        return ""
    return contract.prompt_block() + "\n\n"


def build_tutor_instructions(context):
    """Sistemski prompt prvog poziva — isti za svih 534 lekcije."""
    shared = build_shared_math_rules(
        context.grade, context.title, context.oblast, mode="practice"
    )
    return (
        "Ti si iskusan nastavnik matematike u osnovnoj školi u Bosni i Hercegovini. "
        "Vodiš mod „Vježbaj sa mnom“: daješ po jedan zadatak i pomažeš učeniku da "
        "ga sam riješi.\n\n"
        f"{shared}\n\n"
        f"{_semantic_requirement_block(context)}"
        f"{_semantic_contract_block(context)}"
        f"{_INTENT_GUIDE}\n\n"
        f"{_FIELD_RULE}\n\n"
        f"{_TASK_RULE}\n\n"
        f"{_SCALED_DIVISION_RULE}\n\n"
        f"{_STRUCTURED_TASK_RULE}\n\n"
        f"{_TARGET_LEVEL_RULE}\n\n"
        f"{_STARTING_COMPLEXITY_RULE}\n\n"
        f"{_DIFFICULTY_RULE}\n\n"
        "TON: obraćaj se učeniku direktno, toplo i kratko. Nikad ne spominji "
        "interna polja, „namjeru“, recenzenta ni to da si model."
    )


def build_tutor_input(context, session, student_message, trusted_verdict=None):
    return "\n\n".join([
        _lesson_block(context),
        _state_block(session, student_message, trusted_verdict),
        "Vrati strukturisan odgovor prema šemi.",
    ])


# ODLUKA MORA PRATITI VLASTITI DOKAZ (živi gate cb80b92).
# Recenzent je nezavisno izračunao dokaz koji NE zadovoljava traženi nivo, pa
# ipak vratio `approve` — server je to morao odbiti i turn je propao iako je
# recenzent već imao sve što treba da vrati `correct` sa zamjenskim zadatkom u
# ISTOM (drugom i posljednjem) pozivu. Ovaj blok mu to izričito nalaže.
_REVIEWER_TARGET_LEVEL_RULE = """TARGET LEVEL DECISION RULE (the server enforces this deterministically):
- First independently calculate `reviewed_difficulty_evidence` from the visible task alone.
- Then compare it with the exact requested target difficulty level of the final task.
- NEVER return `approve` when your own evidence does not satisfy that target level. The
  server runs the same validator on your evidence and rejects a contradictory approval,
  so approving a task you measured as outside the target only loses the turn.
- A harder request moves ONE bounded step up from the committed level. A task that
  overshoots the requested level is as wrong as one that undershoots it: a Level 2 request
  answered with a three-step derivation or a proof must be corrected down, not approved.
- When the wording is usable but the task difficulty is wrong for the requested level,
  return `correct`. `correct` may REPLACE THE WHOLE TASK, not only fix a typo: put a
  complete replacement task in `final.new_task`.
- The replacement must keep the exact selected lesson, hit the requested target level,
  keep the four-option MCQ contract (unique IDs, exactly one correct option,
  correct_option_id agreeing with correct_option_index, expected_answer an exact copy of
  the marked option's text), be mathematically correct, and carry a fresh task_signature
  describing the replacement task.
- When you lower a task to the requested level, lower EVERY dimension that violates it,
  not only the easiest one. A replacement that drops the reasoning steps and the operation
  count but keeps a second independent condition is still rejected, and the turn is lost.
- Recompute `reviewed_difficulty_evidence` for the REPLACEMENT task you actually return.
- Do not merely relabel the same task, and never lower reasoning_steps, condition_count
  or operation_count below what the visible task truly requires. Dishonest counts are a
  worse failure than a rejected turn.
- If you cannot produce a safe, complete corrected package, return `fail_closed`."""


# ODLUKA MORA PRATITI VLASTITE PROVJERE (živa kampanja postIncompleteFix).
# U 100 scenarija recenzent je 9 puta vratio `approve`/`correct` iako je SAM
# prijavio problem: 6 puta uz oborenu obaveznu provjeru (`task_package_consistent`,
# `task_signature_consistent`, `marked_option_correct`, `mathjax_valid`), 3 puta
# uz ispravku koja je nosila ISTI dokazani MCQ defekt. Server je svih 9 puta
# ispravno pao zatvoreno — ali je učenik dobio tehničku poruku umjesto zadatka.
#
# Serverske invarijante se NE popuštaju. Ovaj blok samo recenzentu izričito
# kaže ono što validator ionako radi, jer je isti pristup već izmjerivo
# pomogao kod pravila o ciljanom nivou.
_REVIEWER_CHECK_SEMANTICS_RULE = """WHAT `checks.*` DESCRIBE (unambiguous):
- Every `checks.*` field describes the package you return in `final` — for
  `correct` that is the CORRECTED task, never the original draft. Never report a
  defect you already fixed: if you repaired it, the check is true for what you return.
- The server re-runs its own validators on your final package, so a check you
  report is never accepted as proof and never replaces those validators.
- Report honestly. `math_correct`, `marked_option_correct`, `inside_lesson` and
  `task_solvable_and_unambiguous` are the ones the server cannot verify for every
  lesson: a false value there fails the turn closed, which is the correct outcome.
  If you cannot make them true, return `fail_closed` instead of a package.
- Do not lower a check merely because you are unsure about tone or wording."""


_REVIEWER_DECISION_RULE = """DECISION CONSISTENCY RULE (the server enforces this deterministically):
- `approve` is allowed ONLY when every mandatory check you report is true AND the draft
  carries no unresolved server-detected issue. A single false check with `approve` is a
  contradiction: the server rejects the whole turn and the student gets nothing.
- `correct` is allowed ONLY when you return a NEW, complete, internally consistent package.
  Returning the draft unchanged, or a package that still carries the same proven defect, is
  rejected exactly like a contradictory approval.
- After correcting anything, RECOMPUTE and return consistently: the correct option
  (`correct_option_id` and `correct_option_index` selecting the same visible option), the
  expected answer (an exact copy of that option's text), the solution, the task signature,
  the difficulty evidence for the task you actually return, and every mandatory check.
- A correction must resolve EVERY reported issue and must not introduce a new defect.
  Changing the package is not enough: the server re-runs every validator on what you
  return, so a surviving equivalent option pair, a surviving numeric inconsistency, or
  freshly broken MathJax in the options loses the turn exactly like no correction at all.
- If you cannot produce a safe, complete, self-consistent package in this one call, return
  `fail_closed` with a `fail_reason_code`. There is no third call, and an honest
  `fail_closed` is strictly better than a contradictory approval."""


# SERVERSKI NALAZI O NACRTU (živi gate 00bbd45).
# Dvije vidljive opcije bile su različiti stringovi a ista vrijednost; server je
# to dokazao TEK u objavi, poslije oba poziva, pa recenzent nikad nije ni saznao
# šta treba popraviti. Sada nalaz stiže u ulazu drugog poziva — pravilo je
# univerzalno i govori samo o MCQ paketu, bez ijedne riječi o lekciji ili figuri.
_REVIEWER_PREFLIGHT_RULE = """SERVER-DETECTED DRAFT ISSUES (when that block is present in your input):
- Those findings are DETERMINISTIC SERVER FACTS, already proven by the server's own
  validators. They are not suggestions and you may not argue with them.
- `approve` is FORBIDDEN while any reported issue remains in the package.
- Return `correct` with a complete corrected package. For duplicate or semantically
  equivalent options, REPLACE the offending distractor(s) with genuinely different
  mathematical values so all four options are semantically distinct — never just
  reformat, re-round, or rewrite an equivalent value in another notation.
- For `difficulty_evidence_outside_target`, the server has already proven with its own
  shared validator that the draft does not belong at the requested level. REPLACE THE
  TASK with one that genuinely belongs there — for a target of Level 1 that means a
  single directly stated rule applied once, not a task that combines two rules or
  conditions. Never fix this by lowering the reported counts, clearing a flag, or
  relabelling the level while the task stays the same: the server recomputes the
  evidence you return and rejects a dishonest package.
- Keep the exact selected lesson in every correction, and recompute the difficulty
  evidence so it honestly describes the task you actually return.
- Exactly one visible option stays correct. Recompute correct_option_id,
  correct_option_index, expected_answer (an exact copy of the marked option's text),
  solution, and task_signature where structural parameters changed.
- Do not change parts of the task that are already valid.
- The server re-runs those same validators on YOUR final package: an unchanged package
  or a new equivalent pair is rejected and the turn is lost.
- If you cannot correct the package safely, return `fail_closed`."""


def build_reviewer_instructions(context):
    """Sistemski prompt drugog poziva.

    Recenzent je GLAVNA semantička kapija opsega lekcije i jedini nezavisan
    provjeravač matematike. Zato mu se izričito traži da sam riješi zadatak —
    „izgleda tačno“ nije provjera."""
    shared = build_shared_math_rules(
        context.grade, context.title, context.oblast, mode="practice"
    )
    return (
        "Ti si stroga nezavisna kontrola kvaliteta za nastavni odgovor iz "
        "matematike (osnovna škola, Bosna i Hercegovina). Dobijaš NACRT drugog "
        "nastavnika i moraš ga provjeriti prije nego što ga učenik vidi.\n\n"
        f"{shared}\n\n"
        "OBAVEZNO PROVJERI, tim redom:\n"
        "1. matematičku tačnost — SAM riješi zadatak od nule i upiši svoje "
        "rješenje u `checks.independent_answer`; ne vjeruj nacrtu na riječ;\n"
        "2. da je označena opcija zaista tačna i da je TAČNO JEDNA tačna;\n"
        "3. da zadatak ispituje BAŠ izabranu lekciju, a ne samo istu oblast;\n"
        "4. da je namjera ispravno prepoznata i obrađena;\n"
        "5. da je „lakše“ zaista lakše od prethodnog zadatka;\n"
        "6. da je „teže“ zaista teže od prethodnog zadatka;\n"
        "7. da „ne znam“, hint, objašnjenje i pokušaj odgovora dobiju primjeren "
        "odgovor (npr. „ne znam“ NIKAD ne smije biti ocijenjeno kao netačno);\n"
        "8. da je zadatak rješiv i jednoznačan;\n"
        "9. da je MathJax ispravan (samo $...$, poznate komande);\n"
        "10. da je bosanski prirodan i primjeren uzrastu.\n\n"
        "11. verify that lesson identity, level, text, options, marked answer, solution, difficulty evidence, and signature describe one task. Independently recompute `reviewed_difficulty_evidence` from only the visible task, answer requirements, options, and solution: ignore Tutor numerical counts. Count only actions the student must perform; four MCQ options are not four conditions or operations, selecting one option with one rule is one direct application, a solution explanation is not a student explanation requirement, and `combines_concepts` is true only when the student must combine distinct mathematical concepts. Return your independently calculated evidence even if the wording needs no textual correction. Level 1 may be a direct yes/no, recognition, calculation, classification, substitution, or one-rule selection; choosing a visible option is not by itself mathematical comparison or a second reasoning step. Level 2 permits a bounded pair of related rules/concepts, conditions, or operations, straightforward explanation/comparison, or one manageable representation change; `combines_concepts` alone is not Level 3. Level 3 requires construction, proof, three-or-more connected requirements/operations, advanced representation change, or comparable depth. For multiple choice, correct_option_id and correct_option_index must select the same visible option and expected_answer must be an exact copy of its text; explanation belongs only in solution. Signature parameters are only closed name/value entries with canonical string values: no arbitrary metadata, and order alone is never a new task. When correcting, update options, correct option ID/index, expected answer, solution, and the complete signature together, then return the complete fresh package. Set `task_package_consistent`, `difficulty_evidence_valid`, and `task_signature_consistent` accordingly.\n\n"
        f"{_semantic_contract_block(context)}"
        f"{_SCALED_DIVISION_RULE}\n\n"
        f"{_REVIEWER_DECISION_RULE}\n\n"
        f"{_REVIEWER_TARGET_LEVEL_RULE}\n\n"
        f"{_REVIEWER_PREFLIGHT_RULE}\n\n"
        "ODLUKA:\n"
        "- `approve` — nacrt je ispravan; prepiši ga nepromijenjen u `final`;\n"
        "- `correct` — nacrt je popravljiv; u `final` vrati KOMPLETAN ispravljen "
        "payload (to je konačan odgovor koji učenik vidi). To uključuje i "
        "KOMPLETNU ZAMJENU zadatka kad je težina pogrešna za traženi nivo;\n"
        "- `fail_closed` — ne može se sigurno objaviti; navedi `fail_reason_code`.\n\n"
        "Ako matematika nije sigurna ili je zadatak dvosmislen, biraj "
        "`fail_closed`. Bolje bez odgovora nego pogrešan odgovor.\n"
        "Ne postoji treći poziv: tvoj `final` je ono što se objavljuje."
    )


def build_reviewer_input(context, session, student_message, draft_json,
                         trusted_verdict=None, preflight_block=""):
    """`preflight_block` su DETERMINISTIČKI serverski nalazi o nacrtu.

    Prazan string kad server nije dokazao nijedan defekt — tada se ulaz ne
    mijenja ni za jedan znak u odnosu na raniji oblik."""
    blocks = [
        _lesson_block(context),
        _state_block(session, student_message, trusted_verdict),
        "NACRT DRUGOG NASTAVNIKA (provjeri ga, ne vjeruj mu):\n" + draft_json,
    ]
    if preflight_block:
        blocks.append(preflight_block)
    blocks.append("Vrati strukturisanu odluku prema šemi.")
    return "\n\n".join(blocks)


def intent_vocabulary():
    """Za testove: prompt mora nabrojati SVAKU namjeru iz šeme."""
    return tuple(INTENTS)
