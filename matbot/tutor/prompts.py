"""JEDAN univerzalni prompt za svih 534 lekcije — bez teksta po lekciji.

Identitet lekcije ulazi kao KONTEKST (LessonContext), nikad kao posebna grana i
nikad kao ručno napisan prompt po lekciji. Zajednička matematička/jezička
pravila se ne dupliraju: dolaze iz `matbot/rules.py`, isto kao i ranije, pa
terminologija i notacija ostaju identične u sva tri moda.

Tri prompta:
  • `build_tutor_*`    — nacrt (namjera + odgovor + eventualan zadatak),
  • `build_reviewer_*` — NEZAVISNA provjera i konačan payload,
  • `build_help_*`     — SAMO pomoć nad AKTIVNIM zadatkom (arhitektonska Faza 2).

Recenzent NAMJERNO ne dobija „odobri ako izgleda dobro“ ton: traži se da sam
riješi zadatak prije nego što išta odobri.

ZAŠTO POSTOJI TREĆI PROMPT (Faza 2). Turn pomoći je do sada dobijao DOSLOVNO
isti sistemski prompt kao izrada zadatka: ugovor MCQ paketa, strukturisani
potpis, ciljani nivo težine, polazna složenost, pravila smjera težine — ništa
od toga ne opisuje nagovještaj. Kad server DETERMINISTIČKI zna da je turn pomoć
(učenik je pritisnuo dugme, `pipeline._explicit_ui_action`), prompt se sužava na
ono što pomoć stvarno traži. Ugovor izrade zadatka ostaje bajt za bajt
nepromijenjen — kucana poruka i dalje ide kroz `build_tutor_*`, jer prije poziva
namjera nije serverska činjenica.
"""
from matbot import archetype_support, config, difficulty_profiles, difficulty_target
from matbot import hint_policy, task_archetypes
from matbot.lesson_fidelity import semantic_task_requirement
from matbot.mcq_integrity import explicit_compound_divisor_request
from matbot.rules import build_shared_math_rules
from matbot.tutor.schema import INTENTS

# JEDAN server-vlasnički opis aktivnog cilja težine (F5J): numerički pragovi
# renderovani iz ISTIH konstanti koje čita validator + kalibrisana semantika
# brojanja. DOSLOVNO isti tekst ide i Tutoru i Recenzentu; lekcija se u njemu
# ne pominje, pa smije stajati u stabilnom (keširanom) prefiksu oba prompta.
_SHARED_TARGET_BLOCK = difficulty_target.shared_target_block()

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
#
# ARHITEKTONSKA FAZA 2 — VRH LJESTVICE VIŠE NIJE MODELOV. Zatečeni nivo 3 je
# tražio „CIJELI postupak i konačan rezultat“ od modela, bez recenzenta i bez
# ijednog preflighta — tako je FW-X03 nagovještaj 3 objavio TAČAN zaključak
# preko NETAČNE međutvrdnje. Nivo 3 sada sastavlja server iz PROVJERENOG
# rješenja objavljenog zadatka (matbot/hint_policy.py::compose_top_hint), pa
# tekst ovog nivoa opisuje TU činjenicu — prompt ne smije obećavati autorstvo
# koje model više nema.
_HINT_LEVEL_GUIDANCE = {
    0: ("SLJEDEĆI HINT JE NIVO 1: usmjeri na PRVI KORAK (koje pravilo ili "
        "operacija se primjenjuje), BEZ računa i BEZ rezultata."),
    1: ("SLJEDEĆI HINT JE NIVO 2: daj KONKRETAN međukorak — tačno koji račun "
        "treba izvesti — ali JOŠ BEZ konačnog rezultata."),
    2: ("SLJEDEĆI HINT JE NIVO 3: cijeli postupak i konačan rezultat sastavlja "
        "SERVER iz već provjerenog rješenja ovog zadatka — ne izmišljaj nov "
        "izvod. U `hint` napiši samo kratku uputu za posljednji korak."),
}

# LJESTVICA ZA TVRDNJU/PREPOZNAVANJE (Faza 2). Kad je odgovor ISKAZ, a ne
# vrijednost, generična uputa „koje pravilo se primjenjuje“ je nebezbjedna: to
# pravilo JE odgovor. Živi FW-X03 nagovještaj 1 je zato doslovno izrekao
# označenu tvrdnju, a TR-B1 nagovještaj 1 njen kriterij kao parafrazu. Za tu
# klasu nagovještaje 1 i 2 sastavlja server
# (matbot/hint_policy.py::compose_propositional_hint), pa ovaj tekst modelu
# govori tačno to i traži uputu koja NE izriče kriterij odluke.
_PROPOSITIONAL_HINT_LEVEL_GUIDANCE = {
    0: ("SLJEDEĆI HINT JE NIVO 1: odgovor na ovaj zadatak je TVRDNJA, ne "
        "vrijednost, pa nagovještaj ovog nivoa sastavlja SERVER. Ne izriči "
        "definiciju, kriterij ni svojstvo po kojem se bira tačna opcija; u "
        "`hint` napiši samo ŠTA učenik treba uporediti među ponuđenim opcijama."),
    1: ("SLJEDEĆI HINT JE NIVO 2: odgovor je TVRDNJA, pa i ovaj nivo sastavlja "
        "SERVER. Ne izriči tvrdnju koja jedinstveno bira tačnu opciju; u `hint` "
        "napiši samo kako se izbor sužava provjerom svih opcija."),
    2: ("SLJEDEĆI HINT JE NIVO 3: postupak i konačan odgovor sastavlja SERVER "
        "iz već provjerenog rješenja ovog zadatka — ne izmišljaj nov izvod."),
}


def _hint_level_guidance(hint_level, task_class=hint_policy.COMPUTATIONAL):
    table = (_HINT_LEVEL_GUIDANCE if task_class == hint_policy.COMPUTATIONAL
             else _PROPOSITIONAL_HINT_LEVEL_GUIDANCE)
    guidance = table.get(hint_level, table[2])
    return (guidance + " Svaki hint mora donijeti NOVU informaciju u odnosu na "
            "prethodni — nikad ne ponavljaj raniji hint drugim riječima.")


def session_task_class(session):
    """Klasa AKTIVNOG zadatka iz SERVER-VLASNIČKE sesije (nikad iz proze modela).

    Tanak alias nad `hint_policy.session_task_class` — sastavljanje konteksta
    (tekst zadatka + objavljene opcije + serverski `correct_option_id`) živi na
    JEDNOM mjestu, pa produkcija i evaluator ne mogu mjeriti različitu klasu."""
    return hint_policy.session_task_class(session)


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
        # UGOVOR LEKCIJE KAO OGRANIČENJE, NE KAO DRUGA RUTA (migracija K1/K3).
        # Ova lekcija je ranije imala vlastiti modelski put čiji je prompt nosio
        # sva ugovorna ograničenja. Put je uklonjen, pa ograničenja moraju stići
        # ovdje — inače bi model morao pogađati ono što server već zna i što
        # objava deterministički provjerava.
        lines.append(f"- deklarisana vještina: {context.skill}")
        if context.allowed_operations:
            lines.append("- dozvoljene operacije: " + ", ".join(context.allowed_operations))
        if getattr(context, "operand_types", ()):
            lines.append("- tipovi operanada: " + ", ".join(context.operand_types))
        for key, value in sorted(context.operand_constraints.items()):
            lines.append(f"- ograničenje: {key} = {value}")
        if getattr(context, "task_archetypes", ()):
            lines.append("- dozvoljen oblik zadatka (arhetip): "
                         + ", ".join(context.task_archetypes))
        if getattr(context, "prohibited_archetypes", ()):
            lines.append("- ZABRANJEN oblik zadatka: "
                         + ", ".join(context.prohibited_archetypes))
        answer_form = (getattr(context, "representation_constraints", {}) or {}).get(
            "answer_form", "")
        if answer_form == "irreducible":
            # SERVER OVO DETERMINISTIČKI PROVJERAVA PRI OBJAVI. Za lekciju o
            # skraćivanju to nije stilski zahtjev nego sama vještina.
            lines.append("- OBAVEZAN OBLIK ODGOVORA: tačna opcija mora biti "
                         "NESVODIV razlomak (brojnik i nazivnik bez zajedničkog "
                         "djelioca većeg od 1). Skraćen do kraja, ne djelimično.")
        elif answer_form:
            lines.append(f"- obavezan oblik odgovora: {answer_form}")
        if getattr(context, "error_categories", ()):
            lines.append("- pogrešne opcije neka budu tipične greške ovog "
                         "gradiva: " + ", ".join(context.error_categories))
    if context.lesson_scope:
        lines.append(f"- lesson scope/objectives: {context.lesson_scope}")
    elif context.objectives:
        # PRIMARNA VJEŠTINA, NE SAMO TEMA (živi nalaz: semantički zanos).
        # Pojmovna lekcija o jednom skupu brojeva dobijala je zadatke čija je
        # stvarna vještina sabiranje razlomaka: brojevi jesu bili iz lekcije,
        # ali se ispitivala SUSJEDNA vještina. Ishodi ispod dolaze iz kanonskog
        # mapiranja NPP-a na lekciju i do sada uopšte nisu stizali do modela.
        if getattr(context, "objectives_source", "authored") == "authored":
            lines.append("- PRIMARNA VJEŠTINA OVE LEKCIJE (zadatak mora ciljati "
                         "baš nju): " + "; ".join(context.objectives))
        else:
            # AUTOMATSKI IZVUČEN ISHOD NIJE NAREDBA (živi nalaz, val 2).
            # Kanonsko mapiranje je grubo: jedna algebarska lekcija o kvadratu
            # binoma dobila je kao „primarnu vještinu“ i vjerovatnoću i jednu
            # geometrijsku konstrukciju — istinite NPP rečenice, prenesene s
            # drugih lekcija. Kao OBAVEZAN cilj takva rečenica gura generisanje
            # s lekcije. Kanonski NASLOV je provjeren podatak i ostaje cilj;
            # izvučeni ishodi su okvir koji ga pojašnjava, ne zamjenjuje.
            lines.append("- kurikularni okvir ove lekcije (NPP ishodi vezani za "
                         "nju; okvir, ne spisak dozvoljenih zadataka): "
                         + "; ".join(context.objectives))
            lines.append("- CILJ ostaje ono što naslov lekcije imenuje. Ako se "
                         "neki ishod iznad ne odnosi na ovaj naslov, zanemari ga.")
        lines.append("- ono što učenik mora POKAZATI da zna jeste ta vještina; "
                     "brojevi i pojmovi iz lekcije sami po sebi NISU dovoljni")
    else:
        lines.append("- scope note: title-only lesson; grade, area and exact title are the minimum semantic anchor. Do not broaden to the entire area.")
    supporting = getattr(context, "supporting_concepts", ())
    if supporting:
        lines.append("- pomoćni pojmovi (smiju se koristiti kao alat, ali NE "
                     "smiju postati ono što se ispituje): " + "; ".join(supporting))
    if context.exclusions:
        lines.append("- SUSJEDNE VJEŠTINE — nikad cilj ovog zadatka: "
                     + "; ".join(context.exclusions))
    return "\n".join(lines)


def _state_block(session, student_message, trusted_verdict=None, ui_action=""):
    """Trenutno stanje vježbe — serverska istina, ne modelovo sjećanje.

    `ui_action` je namjera koju je učenik izričito tražio DUGMETOM nad aktivnim
    zadatkom („Daj mi hint“, „Uradi ga ti“). Prazno je za kucanu
    poruku i tada se ulaz ne mijenja ni za jedan znak. Ovo je samo saopštenje
    modelu — zabranu objave novog zadatka server ionako sprovodi deterministički
    (matbot/tutor/pipeline.py), pa ne ovisi o tome hoće li je model poslušati."""
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
        lines.append("- " + _hint_level_guidance(session["hint_level"],
                                                 session_task_class(session)))
    else:
        lines.append("- AKTIVNI ZADATAK: ne postoji (učenik još nije dobio zadatak)")

    lines.append(f"- SERVER COMMITTED DIFFICULTY LEVEL: {session.get('difficulty_level', 1)}")

    # Faza 4G (živi F4G talas, G03/G05; ranije F4F F13–F15): poruka koja SAMA
    # deterministički traži složen uslov djeljivosti definiciono je zadatak
    # nivoa 2, a cilj na nivou 1 je takve turnove obavezno obarao. Server ovdje
    # SAOPŠTAVA podignut cilj; isti floor deterministički sprovodi
    # pipeline._target_level_for, pa je linija istinita — ne sugestija modelu.
    if explicit_compound_divisor_request(student_message):
        lines.append(
            "- EXPLICIT REQUEST NOTICE: the student's own message asks for a task "
            "combining SEVERAL divisibility conditions. For a NEW task in this turn "
            "the server target difficulty level is 2 (never 1): generate exactly the "
            "requested compound task, declare target_difficulty_level=2, and make the "
            "difficulty evidence honestly measure that task."
        )

    if session["recent_tasks"]:
        lines.append("- NEDAVNI ZADACI (ne ponavljaj iste brojeve ni isti obrazac):")
        for task in session["recent_tasks"][-_MAX_HISTORY_TURNS:]:
            lines.append(f"  • {_clip(task)}")

    # STRUKTURE KOJE SU VEĆ ISKORIŠTENE (zahtjev iz produkcije: „isti zadatak s
    # drugim brojevima“ nije nova vježba). Šalje se SAŽETAK strukture, ne cijela
    # historija — model treba znati šta NE smije ponoviti, ne cijeli razgovor.
    structures = [entry.get("text", "") for entry in
                  (session.get("recent_structures") or [])[-_MAX_HISTORY_TURNS:]
                  if isinstance(entry, dict)]
    if structures:
        lines.append("- VEĆ ISKORIŠTENI OBLICI ZADATKA U OVOJ SESIJI:")
        for text in structures:
            lines.append(f"  • {_clip(text)}")
        # SERVER IMENUJE SLJEDEĆI OBLIK (mjereno: bez toga se model vraća na
        # `DIRECT_COMPUTE` u 68 % slučajeva). Preferencija nije dozvola da se
        # izađe iz lekcije — semantičke kapije ostaju iznad nje.
        preferred = archetype_support.preferred_archetype(
            session.get("lesson_id") or "",
            [entry.get("archetype") for entry in
             (session.get("recent_structures") or []) if isinstance(entry, dict)])
        if preferred:
            lines.append(
                f"- TRAŽENI OBLIK SLJEDEĆEG ZADATKA: {preferred} — "
                + task_archetypes.describe(preferred)
                + ". Ostani STROGO unutar ove lekcije; ako taj oblik za nju nije "
                  "smislen, izaberi drugi oblik koji jeste, ali NIKAD ne izlazi "
                  "iz lekcije. Smiješ ponoviti raniji oblik samo ako se stvarno "
                  "promijeni ŠTA se traži — isti zahtjev s drugim brojevima, "
                  "imenima ili predmetima nije nov zadatak.")
        lines.append(
            "- ZA SLJEDEĆI ZADATAK izaberi DRUGU vrstu vježbe unutar iste lekcije. "
            "Promjena brojeva, imena, predmeta ili redoslijeda opcija NIJE dovoljna. "
            "Promijeni ono što se traži: umjesto direktnog računanja traži "
            "nedostajuću veličinu, prepoznavanje greške u ponuđenom postupku, "
            "poređenje dva rezultata, prevod između zapisa ili primjenu u kratkom "
            "tekstualnom zadatku — ali NIKAD izvan opsega ove lekcije.")

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
    if ui_action:
        lines.append(
            f"- UČENIK JE PRITISNUO DUGME ČIJA JE NAMJERA: {ui_action}. To je "
            "serverska činjenica i ne preispituje se: polje `intent` mora biti "
            "tačno ta vrijednost. U ovom turnu se NE SMIJE izdati novi zadatak "
            "— odgovaraj isključivo na AKTIVNI ZADATAK naveden iznad."
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
konkretnu uputu koja pomjera učenika za jedan korak.

OBJAŠNJENJE SE PIŠE ODMAH, NE NAJAVLJUJE (živi nalaz):
`explanation_request` NEMA zasebno polje sadržaja — sve što učenik pročita
jeste `reply`. Zato `reply` mora u ISTOM odgovoru sadržavati STVARNO
objašnjenje aktivnog zadatka: konkretne veličine, korake i rezultat. Rečenice
tipa „Dobro, objasniću ti drugim riječima“ ili „objasniću korak po korak“ NISU
objašnjenje — ako je to sve što napišeš, učenik nije dobio ništa i server
odgovor mora zamijeniti vlastitom kompozicijom. Kad učenik traži DRUGI način,
objasni isti zadatak drugim putem ili drugim riječima, ali NE mijenjaj zadatak
i NE mijenjaj tačan odgovor."""

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


# ŽIVI FINAL-40 NALAZ (ljestvica nagovještaja, lekcija o pravoj i ravni): drugi
# nagovještaj je odbijen u objavi kodom `unknown_mathjax_command:tdot` — komanda
# `\tdot` ne postoji ni u jednoj MathJax bazi. Server je vratio sigurnu poruku,
# `hint_level` je ISPRAVNO ostao na 1, pa sljedeći zahtjev nije mogao dati treći
# (puni) nagovještaj. Šta je model tim zapisom MISLIO nije dokazivo iz artefakta
# i NE POGAĐA SE: sirov izlaz se nigdje ne čuva (pravilo 7), a nepoznata komanda
# se po CLAUDE.md nikad ne prepravlja. Zato se ne dira validator nego UGOVOR
# ZAPISA za polja pomoći — do sada su `hint` i `worked_solution` bili jedina
# vidljiva površina za koju prompt nigdje nije rekao da prolazi kroz ISTI
# deterministički prag kao tekst zadatka.
#
# ARHITEKTONSKA FAZA 2 — JEDNA NADVLADANA REČENICA UKLONJENA. Blok je do sada
# kao primjer nosio `$\\vec{v}\\cdot\\vec{n}$` i `\\vec`/`\\overrightarrow` u
# listi „koristi ove komande“. Taj primjer je uveden zbog `\tdot` nalaza (kako
# se piše skalarni proizvod), ali je time DOSLOVNO pozivao na zapis koji je u
# istoj porodici lekcija proizveo drugi živi blokator: TR-B1 nagovještaj 2 je
# lekciji 9. razreda osnovne škole ponudio parametarski oblik prave i skalarni
# proizvod. `\\vec` OSTAJE na mathsafe allowlisti — „je li zapis bezbjedan“ i
# „pripada li ovom zadatku“ su dva različita pitanja (isti razlog kao za
# MATHJAX_COMMAND_ALLOWLIST u CLAUDE.md) — a proporcionalnost sada sudi
# deterministički (matbot/hint_policy.py::out_of_scope_notation_codes). Dužnost
# „ne izmišljaj komandu“ iz izvornog nalaza ostaje netaknuta.
_HELP_NOTATION_RULE = """ZAPIS U `hint` I `worked_solution` (isti prag kao za zadatak):
- Oba polja prolaze kroz ISTU determinističku provjeru zapisa kao tekst zadatka.
  Jedna nepoznata LaTeX komanda unutar $...$ obara CIO turn: učenik ne dobije
  nikakvu pomoć, poziv je potrošen, a ljestvica nagovještaja ostaje na istom
  nivou — sljedeći zahtjev zato NE može dati završno rješenje.
- Koristi ISKLJUČIVO komande koje ovaj projekat već koristi: \\frac, \\sqrt,
  \\cdot, \\times, \\div, \\le, \\ge, \\neq, \\approx, \\in, \\mid, \\angle,
  \\perp, \\parallel, \\pi i grčka slova, \\text.
- Ako komanda nije na toj listi, NE IZMIŠLJAJ je i ne skraćuj joj ime: napiši
  isti korak riječima ili već dozvoljenom komandom. Izmišljena komanda je za
  server isto što i pokvaren zapis — ne pogađa se šta je trebala značiti.
- Množenje se piše kao $\\cdot$ (npr. $3\\cdot 4$) — nikad nekom drugom „tačka“
  komandom.
- NE UVODI MATEMATIKU KOJE NEMA U ZADATKU. Vektorski zapis, integral, suma,
  granična vrijednost, matrica i slična napredna mašinerija smiju se pojaviti u
  pomoći SAMO ako ih sam zadatak, njegove opcije ili njegovo rješenje već
  koriste. Server to provjerava deterministički i odbija pomoć koja uvodi novu
  tehniku — pomoć mora ostati u okviru postupka kojim je zadatak riješen.
- Backslash svake komande mora biti ispravno JSON-escapeovan (dupli backslash),
  da poslije parsiranja ostane komanda (\\cdot), a ne kontrolni znak (TAB, form
  feed) iza kojeg vise gola slova."""


# SERVERSKE GRANICE SE IMENUJU, NE POGAĐAJU (isti obrazac kao ciljni nivo
# težine): dužina opcije je tvrda serverska granica, a prompt je nikad nije
# izgovorio — pa je „preduga opcija“ postala vodeći uzrok eskalacije nakon što
# je nesklad ciljnog nivoa uklonjen. Broj se čita iz konfiguracije da granica
# ostane na JEDNOM mjestu.
_TASK_RULE = """KAD PRAVIŠ ZADATAK:
- zadatak mora ispitivati BAŠ izabranu lekciju, ne samo istu oblast
- tačno 4 opcije; TAČNO JEDNA je tačna; nijedne dvije ne smiju značiti istu vrijednost
- svaka opcija mora biti KRAĆA od @@MAX_OPT@@ znakova: opcija je
  odgovor, ne objašnjenje. Ako ti opcija bježi u rečenicu, skrati je na sam
  odgovor — obrazloženje ide u `solution`, nikad u opciju
- „tačno jedna tačna OPCIJA“ nije isto što i „zadatak ima tačno jedno RJEŠENJE“:
  zadatak smije imati beskonačan skup rješenja, a i dalje samo jednu tačnu opciju
- svaka pogrešna opcija mora biti NETAČNA kao cjelina pod konačnim tekstom i
  domenom. Drugačija formulacija ne pretvara drugu TAČNU tvrdnju u valjan
  distraktor: dva različita, oba tačna obrazloženja istog zaključka su DVIJE
  tačne opcije. Pogrešna opcija smije sadržati tačan međukorak samo ako joj je
  presudna tvrdnja netačna.
- `correct_option_index` je indeks tačne opcije (0-3) u nizu koji si napisao
- `expected_answer` je tačan odgovor, isti kao tekst tačne opcije
- pogrešne opcije moraju biti uvjerljive greške, ne nasumični brojevi
- NE otkrivaj koja je opcija tačna u tekstu `reply`
- TEKST ZADATKA SMIJE DATI PODATKE, ALI NE I ODGOVOR: nikad — ni doslovno ni
  parafrazom — ne napiši da jedna od opcija već ima baš onu osobinu koju
  pitanje traži da učenik utvrdi. („Zraka $BD$ prolazi između $BA$ i $BC$“ uz
  pitanje „koji krak dijeli ugao?“ je otkrivanje, samo drugim riječima.) Umjesto
  toga daj neutralne podatke — mjere, položaje, koordinate, definiciju — iz
  kojih učenik mora izračunati, uporediti, zaključiti ili prepoznati odgovor.
- u `solution` i `expected_answer` NIKAD ne imenuj slovo opcije („opcija a“,
  „odgovor b“, „pod c)“) NI njen redni broj ili položaj („prva opcija“, „treća
  tvrdnja“): server opcije izmiješa POSLIJE tebe i tek onda dodjeljuje slova, pa
  svaka takva oznaka postaje netačna i cijeli paket biva odbijen. Objasni SADRŽAJ
  tačnog odgovora — matematiku, ne oznaku. Imenovanje matematičkih objekata
  (`tačka A`, `duž AB`, `funkcija f`) nije oznaka opcije i ostaje dozvoljeno.
- KAD SU OPCIJE TVRDNJE („Koja tvrdnja o razlomku … je tačna?“), tačan odgovor
  je sama TVRDNJA, pa je PONOVI SVOJIM RIJEČIMA i objasni zašto vrijedi. Nikad
  ne počinji rečenicu oznakom te tvrdnje: „Tvrdnja pod a) je tačna.“ i „Tačan
  odgovor je b) jer …“ su odbijeni paketi, a „U razlomku $\\frac{3}{5}$ brojnik
  je $3$, a nazivnik $5$.“ je isto objašnjenje bez ijedne oznake.""".replace("@@MAX_OPT@@", str(config.MAX_OPTION_TEXT_CHARS))

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


# ŽIVI T1 RECHECK (b38d08a, 6 od 6 nacrta s MCQ nalazom): u svakom nacrtu tog
# talasa server je našao defekt opcija — tri puta `unverifiable_solution_option`,
# tri puta `multiple_correct_options` — pa su i OBA tražena pozitivna scenarija
# (Q i Z) pala zatvoreno iako je matematika bila tačna. Uzrok nije validator
# nego UGOVOR AUTORSTVA: prompt do sada nigdje nije rekao ŠTA opcija „riješi
# ne/jednačinu“ zadatka mora značiti (cio skup rješenja), koji su zapisi
# serverski DOKAZIVI, ni da odgovor zavisi od domena. Isti obrazac kao
# `_HELP_NOTATION_RULE`: vidljiva površina koju deterministički sloj sudi, a
# prompt je nikad nije opisao.
#
# Nabrojani zapisi su IZMJERENI nad `mcq_integrity._solve_option_set` — ovdje se
# ne obećava ništa što server ne ume da pročita. Pravilo NE popušta nijednu
# provjeru; ono samo traži oblike koje provjera može dokazati.
_SOLVE_TASK_AUTHORING_RULE = """KAD ZADATAK TRAŽI SKUP RJEŠENJA (ne/jednačine):
- Server SAM riješi relaciju iz zadatka i uporedi je sa SVAKOM opcijom po
  ZNAČENJU skupa. Zapis koji server ne može pročitati obara cio turn
  (`unverifiable_solution_option`) i učenik ne dobije ništa — zato sve četiri
  opcije moraju biti u dokazivom zapisu, ne samo tačna.
- DOKAZIVI ZAPISI:
  • relacija: $x>3$, $x\\ge 4$, $-2<x<0$;
  • interval: $(3,\\infty)$, $[4,\\infty)$;
  • skup s IZRIČITIM domenom: $\\{x\\in\\mathbb{Q}\\mid x>3\\}$;
  • jedna vrijednost ili jednočlan skup: $3$, $\\{3\\}$ — za jednačine;
  • nabrajanje cijelih brojeva: $\\{4,5,6,\\dots\\}$ — samo uz cjelobrojni domen.
- $\\{x\\mid x>3\\}$ BEZ domena NIJE dokaziv zapis: uvijek napiši i skup lijevo od
  crte ($x\\in\\mathbb{Q}$, $\\mathbb{Z}$, $\\mathbb{N}$, $\\mathbb{R}$). Ne ukrašavaj
  opciju ($\\subset$, dva uslova u jednoj opciji, riječi umjesto zapisa) i ne piši
  nabrajanje bez vitičastih zagrada.
- DOMEN ODLUČUJE koji je zapis TAČAN:
  • Q, R ili domen koji zadatak ne navodi: nabrajanje cijelih brojeva NIKAD nije
    cio skup — između $3$ i $4$ leži $7/2$. Koristi relaciju, interval ili skup
    s domenom.
  • Z, N ili N0: najjednostavnija je relacija ($x\\ge 4$); nabrajanje
    $\\{4,5,6,\\dots\\}$ je tu dozvoljeno i tačno.
- TAČNO JEDNA opcija smije značiti cio skup rješenja. Ostale tri moraju značiti
  RAZLIČITE skupove (npr. $x>5$, $x\\ge 3$, $x>1$): dva zapisa ISTOG skupa su
  dvije tačne opcije i turn propada.
- Kad učenik traži DRUGAČIJI, ali ekvivalentan oblik relacije, u tekstu zadatka
  mora STVARNO stajati ta nova relacija: za $x>3$ i korak „+2 na obje strane“
  napiši $x+2>5$. Opis koraka bez napisane nove relacije ne ispunjava zahtjev, a
  napisana nova relacija mora imati ISTI skup rješenja (nikad $x+2>7$)."""


# IZRIČIT ZAHTJEV — JEDAN blok, DOSLOVNO isti za Tutora i Recenzenta.
#
# ŽIVI CILJANI NALAZ (FW-G05, ponovljen na 2012b31): učenik je tražio „zadatak o
# simetrali ugla I TAČKI u kojoj se simetrale sijeku“, a objavljen je zadatak o
# jednom konstrukcijskom koraku — tražena komponenta je tiho nestala. Isti oblik
# je ranije (FW-G04) proizveo potpunu zamjenu zadatka drugom vještinom.
#
# ZAŠTO JE OVO PROMPT, A NE SERVERSKA KAPIJA: „koje su komponente izričito
# tražene“ nije dokazivo zatvorenom gramatikom bez opšteg razumijevača
# prirodnog jezika (vidi analizu uz `matbot/request_fidelity.py` — tamo živi
# ONO što server STVARNO ume dokazati: domen, relacija, vrsta zadatka).
# Ovaj blok je zato pomoć modelu, a ne dokaz; evaluator ga i dalje vodi kao
# MANUAL_SEMANTIC_REVIEW_REQUIRED i ništa ovdje ne tvrdi determinističku
# potpunost.
_EXPLICIT_REQUEST_RULE = """IZRIČIT ZAHTJEV UČENIKA SE NE SMIJE TIHO SMANJITI:
- Kad poruka izričito imenuje VIŠE komponenti („traži X i Y“, „uključi X“,
  „koristi X“, „odredi X“), objavljen zadatak mora nositi SVAKU komponentu koja
  je spojiva s izabranom lekcijom — ne samo onu koju je najlakše napisati.
- Zamjena traženog zadatka susjednim, lakšim ili samo djelimičnim zadatkom NIJE
  ispravka nego tiho ispuštanje zahtjeva, i strogo je zabranjena.
- Ako se cio izričit zahtjev ne može vjerno ispuniti unutar izabrane lekcije, NE
  pravi djelimičnu ni zamjensku verziju — vrati `fail_closed`.
- LEKCIJA I DALJE IMA PREDNOST: komponenta koja je van izabrane lekcije se ne
  ubacuje na silu; tada je ispravan ishod `fail_closed`, nikad tiha zamjena.
- TUTOR: prije nego što vratiš `new_task`, provjeri da je svaka izričito tražena
  i s lekcijom spojiva komponenta stvarno u tekstu zadatka.
- RECENZENT: prije `approve` ili `correct` uporedi KONAČAN zadatak s porukom
  učenika. Ako je ijedna takva komponenta nestala, ne odobravaj — vrati je u
  ISTOM (drugom i posljednjem) pozivu ili vrati `fail_closed`."""


_STRUCTURED_TASK_RULE = """STRUCTURED TASK PACKAGE (required for every `new_task`):
- selected_lesson_id sadrži SAMO goli kanonski ID lekcije (tačno onaj ID iz reda "- lekcija:" u ulazu, bez naslova) — nikad "Naslov (ID)" i nikad naslov u tom polju; naslov ide isključivo u selected_lesson_title, but the server owns and canonicalizes that display copy;
- target_difficulty_level is 1 for the first task, shifts one bounded step for easier/harder, and otherwise stays at the committed level;
- options use unique IDs a, b, c, d; correct_option_id identifies the correct visible option and agrees with correct_option_index;
- for a multiple-choice task, expected_answer is an exact copy of that marked option's text. Put explanation, derivation, unit commentary, and reasoning only in solution;
- provide task_type, expected_answer, complete solution, difficulty_evidence, and task_signature;
- every equality you write inside $...$ must hold numerically; when the whole point of the solution is a FALSE equality (a contradiction proof, a system with no solutions), state in the SAME sentence that it is false — for example `$3=5$, što nije tačno` — a bare false equality is rejected as an arithmetic error;
- difficulty_evidence describes the actual task: Level 1 is one direct introductory application and may be yes/no, recognition, direct calculation, classification, substitution, or selection. Do not count choosing a visible option by one rule as comparison or a second reasoning step. Level 2 permits two connected conditions/operations or two related rules/concepts, straightforward explanation/comparison, or one manageable representation change; `combines_concepts` alone is not Level 3. Level 3 requires construction, proof, or three-or-more connected requirements/operations, advanced representation change, or comparable depth;
- task_signature describes mathematical structure (family, operation/relation, normalized parameters, conditions, objects, answer type), not wording. normalized_parameters is a list of entries with exactly name and value; values are canonical strings, never arbitrary metadata. Rewording, option order, or parameter-list order must not change it."""


# CILJANI NIVO — univerzalno pravilo, isto za svih 534 lekcije.
# ZAŠTO POSTOJI (živi gate cb80b92): za traženi nivo 1 model je napravio zadatak
# koji iz tri zadate stranice izvodi poluprečnik opisane kružnice — višekorakan
# račun koji je samo OZNAČEN kao nivo 1. Recenzent je nezavisno izračunao
# steps=3/operations=4 i ipak vratio `approve`, pa je turn propao. Pravilo je
# zato apsolutno i bez ijedne riječi o konkretnoj lekciji, figuri ili oblasti:
# nivo 1 je DIREKTNA primjena, a izvedeni višekorakan račun tu ne spada.
#
# ARHITEKTONSKA FAZA 1 — UKLONJENA NADVLADANA REČENICA. Ovaj blok je do sada
# nosio i „…one operation, no representation change.“ (uvedeno 00bbd45,
# 2026-08-04) i „Level 1 tolerates one change of representation and up to two
# connected operations…“ (uvedeno 24d629f, 2026-08-05, DAN kasnije, upravo da
# popravi živi pad koji je prva rečenica izazvala). Mjerodavan je
# `GLOBAL_LEVEL1_MAX` (operation_count <= 2, representation_change_count <= 1)
# — ista konstanta iz koje se renderuje `_SHARED_TARGET_BLOCK` i po kojoj sudi
# `difficulty_evidence_errors`. Prva rečenica je dakle modelu slala prag STROŽI
# od presude, u istom pasusu s tačnim pragom. Uklonjena su ISKLJUČIVO ta dva
# nadvladana ograničenja; „one reasoning step“ i „one condition“ ostaju jer se
# poklapaju s mjerodavnim granicama. Nijedan validator se ne dira.
_TARGET_LEVEL_RULE = """TARGET DIFFICULTY LEVEL (universal; identical for every lesson):
Level 1 must be a GENUINELY DIRECT introductory task built on the selected lesson:
recognizing a definition, identifying a named object or property, applying one
directly stated fact, one simple calculation, or a one-rule classification or
selection. One reasoning step and one condition.
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


# LEKCIJSKI-RELATIVNI PROFIL TEŽINE (Faza F5G). Tekst dolazi GOTOV iz
# data/difficulty_profiles.json — Tutor i Recenzent doslovno ne mogu dobiti
# dvije različite verzije istih granica, a server te iste granice
# deterministički sprovodi (matbot/difficulty_profiles.py). Lekcija bez
# profila vraća prazan string i prompt ostaje bajt za bajt kao prije.
def _difficulty_profile_block(context):
    profile = difficulty_profiles.resolve_for_context(context)
    if profile is None:
        return ""
    return profile.prompt_block() + "\n\n"


# SEMANTIČKI UGOVOR VJEŽBE (Vježbajmo V1, F5K). Tekst dolazi GOTOV iz
# data/semantic_practice_contracts.json i IDENTIČAN ide i Tutoru i
# Recenzentu; server iste zahtjeve deterministički provjerava prije objave
# (matbot/semantic_practice.py). Lekcija bez ugovora vraća prazan string.
def _practice_contract_block(context):
    contract = getattr(context, "practice_contract", None)
    if contract is None:
        return ""
    return contract.prompt_block() + "\n\n"


def build_tutor_instructions(context):
    """Sistemski prompt prvog poziva — isti za svih 534 lekcije.

    Faza 4H (Workstream K): STABILAN, za sve lekcije IDENTIČAN prefiks stoji
    PRVI (OpenAI prompt keš radi nad tačnim prefiksom — ranije je sadržaj
    zavisan od razreda/lekcije stajao u drugom pasusu, pa je zajednički prefiks
    među lekcijama bio svega ~1,3–2,2 K znakova). Sav sadržaj koji zavisi od
    razreda/lekcije/oblasti dolazi TEK NA KRAJU instrukcija; sadržaj pravila se
    ne mijenja ni za jedan znak."""
    shared = build_shared_math_rules(
        context.grade, context.title, context.oblast, mode="practice"
    )
    return (
        "Ti si iskusan nastavnik matematike u osnovnoj školi u Bosni i Hercegovini. "
        "Vodiš mod „Vježbaj sa mnom“: daješ po jedan zadatak i pomažeš učeniku da "
        "ga sam riješi.\n\n"
        f"{_INTENT_GUIDE}\n\n"
        f"{_FIELD_RULE}\n\n"
        f"{_TASK_RULE}\n\n"
        f"{_EXPLICIT_REQUEST_RULE}\n\n"
        f"{_SOLVE_TASK_AUTHORING_RULE}\n\n"
        f"{_SCALED_DIVISION_RULE}\n\n"
        f"{_HELP_NOTATION_RULE}\n\n"
        f"{_STRUCTURED_TASK_RULE}\n\n"
        f"{_TARGET_LEVEL_RULE}\n\n"
        f"{_SHARED_TARGET_BLOCK}\n\n"
        f"{_STARTING_COMPLEXITY_RULE}\n\n"
        f"{_DIFFICULTY_RULE}\n\n"
        "TON: obraćaj se učeniku direktno, toplo i kratko. Nikad ne spominji "
        "interna polja, „namjeru“, recenzenta ni to da si model.\n\n"
        # --- dinamički (po lekciji) dio TEK OD OVE TAČKE (Workstream K) ---
        f"{shared}\n\n"
        f"{_semantic_requirement_block(context)}"
        f"{_semantic_contract_block(context)}"
        f"{_difficulty_profile_block(context)}"
        f"{_practice_contract_block(context)}"
    )


# SERVERSKA NADLEŽNOST NAD VRSTOM TURNA (živi nalaz širokog audita 6.–7.
# razreda): na maksimumu težine model je dva puta odgovorio razgovorno —
# „već si na najtežem nivou, hoćeš li drugi iste težine?“ — umjesto da objavi
# zadatak. Odluku o tome DA LI turn nosi zadatak donosi server (zatvoren skup
# poruka + UI polje), pa se ta činjenica saopštava modelu izričito. Blok je
# ADITIVAN: bez njega je ulaz bajt-identičan zatečenom.
_REQUIRED_TASK_INTENT_TEXT = {
    "generate_task": "učenik traži PRVI zadatak",
    "next_task": "učenik traži NOV zadatak iste težine",
    "harder_task": "učenik traži TEŽI zadatak",
    "easier_task": "učenik traži LAKŠI zadatak",
}


def _required_task_block(required_task_intent, target_level=None):
    if not required_task_intent:
        return ""
    reason = _REQUIRED_TASK_INTENT_TEXT.get(required_task_intent,
                                            "učenik traži zadatak")
    # TAČAN CILJNI BROJ, NE NAGAĐANJE (živi nalaz, val 5): 12 od 18 eskalacija
    # bilo je `difficulty_target_mismatch` — model je morao POGODITI nivo koji
    # server već zna. Stanje iznad nosi POČETNI nivo, a ne cilj ovog turna, pa
    # je „teže“ redovno deklarisano kao isti nivo. Server ga sada imenuje.
    target_line = ""
    if target_level:
        target_line = (
            f"- `target_difficulty_level` mora biti TAČNO {target_level}. To je "
            "serverska odluka, ne procjena: napravi zadatak koji STVARNO "
            f"pripada nivou {target_level} i deklariši baš taj broj;\n")
    return (target_line + (
        "SERVERSKA ODLUKA O OVOM TURNU (nije prijedlog):\n"
        f"- {reason}, pa `intent` mora biti tačno `{required_task_intent}`, a "
        "`new_task` OBAVEZAN;\n"
        "- CILJNI NIVO TEŽINE ODREĐUJE SERVER i naveden je u stanju iznad. Ako "
        "je učenik već na najvišem nivou koji ova lekcija ima, to NIJE razlog "
        "da izostaviš zadatak: napravi NOV zadatak na tom najjačem dozvoljenom "
        "nivou;\n"
        "- NIKAD ne odgovaraj rečenicom tipa „već si na najtežem nivou, hoćeš "
        "li drugi iste težine?“ — takav odgovor server odbija i učenik ostaje "
        "bez zadatka."
    ))


def build_tutor_input(context, session, student_message, trusted_verdict=None,
                      ui_action="", escalation_block="", required_task_intent="",
                      target_level=None):
    """`escalation_block` je serverski zahtjev za raznolikošću (pilot) ili "".

    Prazan string znači da se ulaz ne mijenja ni za jedan znak u odnosu na
    raniji oblik — obična Practice traffic ostaje bajt-identična. Isto važi i
    za `required_task_intent`: prazno = nepromijenjen ulaz."""
    blocks = [
        _lesson_block(context),
        _state_block(session, student_message, trusted_verdict, ui_action),
    ]
    required_block = _required_task_block(required_task_intent, target_level)
    if required_block:
        blocks.append(required_block)
    if escalation_block:
        blocks.append(escalation_block)
    blocks.append("Vrati strukturisan odgovor prema šemi.")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# PROMPT POMOĆI (arhitektonska Faza 2)
# ---------------------------------------------------------------------------
# Sadrži SAMO ono što pomoć nad aktivnim zadatkom stvarno traži. Ne nosi ugovor
# MCQ paketa, strukturisani potpis, ciljani nivo težine, polaznu složenost,
# pravila smjera težine ni ugovore autorstva zadatka — ništa od toga ne opisuje
# nagovještaj, a mjerenje je pokazalo da je turn pomoći do sada dobijao cijeli
# prompt za IZRADU zadatka. Ugovor izrade se ne dira: ovaj prompt se koristi
# ISKLJUČIVO kad je server deterministički utvrdio da je turn pomoć
# (`pipeline._explicit_ui_action`).

_HELP_FIELD_RULE = """PRAVILO POLJA ZA TURN POMOĆI (server odbija payload koji ga prekrši):
- `intent` mora biti TAČNO ona vrijednost koju server navodi kao TRAŽENU AKCIJU;
- `new_task` mora biti null — u ovom turnu se NE SMIJE izdati nov zadatak, ni
  lakši, ni teži, ni sljedeći. Zahtjev za novim zadatkom server ovdje odbija;
- `grading` mora biti null — ovaj turn ne ocjenjuje učenika;
- `difficulty_diagnostics` mora biti null — težina se u ovom turnu ne mijenja;
- za `hint_request` popuni `hint`; za `full_solution_request` popuni `worked_solution`;
- `lesson_focus` popuni: koju tačno vještinu izabrane lekcije ovaj korak pomoći cilja;
- `reply` je kratak uvod, a `hint` / `worked_solution` nosi STVARNU pomoć —
  server ih dopisuje uz `reply`, pa najava bez sadržaja učeniku ne pomaže."""


_HINT_LADDER_COMPUTATIONAL = """LJESTVICA POMOĆI — RAČUNSKI ZADATAK (odgovor je vrijednost ili izraz):
- NIVO 1: imenuj PRVI KORAK — koje pravilo, operacija, formula ili zapis se
  primjenjuje na brojeve i objekte IZ OVOG zadatka. Bez računanja i bez rezultata.
- NIVO 2: izvedi JEDAN konkretan međukorak (svođenje na zajednički nazivnik,
  prebacivanje člana, uvrštavanje, skraćivanje) i ostavi učeniku posljednji
  korak. Još bez konačnog rezultata.
- Konačan rezultat i tekst označene opcije NE SMIJU se pojaviti na nivou 1 ni 2
  — ni kao vrijednost, ni unutar računa, ni kao „dakle dobiješ …“. Server to
  provjerava deterministički i takav tekst zamjenjuje neutralnim korakom, pa
  učenik izgubi pomoć koju si mu htio dati.
- Svaki nivo mora donijeti NOVU informaciju: nivo 2 je STROGO jači od nivoa 1,
  nikad njegova parafraza.
- Pomoć mora biti vezana za brojeve i objekte iz aktivnog zadatka. Generični
  podsticaj („razmisli još malo“, „prisjeti se gradiva“, „provjeri opcije“)
  server odbija kao pomoć bez sadržaja."""


_HINT_LADDER_PROPOSITIONAL = """LJESTVICA POMOĆI — PREPOZNAVANJE TVRDNJE (odgovor je iskaz, ne vrijednost):
- Ovdje je „pravilo koje se primjenjuje“ ISTOVREMENO i odgovor, pa je uputa
  „primijeni definiciju“ otkrivanje rješenja, a ne pomoć.
- NIVO 1: reci ŠTA treba uporediti među SVIM ponuđenim opcijama — o kojim
  objektima govore, koje svojstvo im pripisuju, važi li to u svakom slučaju ili
  samo u nekom. NE izriči definiciju, kriterij, svojstvo ni teoremu po kojoj se
  bira tačna opcija i NE parafraziraj označenu opciju.
- NIVO 2: suzi izbor — reci kako se opcija obara protivprimjerom ili kako se
  provjerava smjer implikacije. I dalje ne smiješ jedinstveno odrediti tačnu opciju.
- Nijedno svojstvo koje ima SAMO tačna opcija, a samo po sebi odlučuje pitanje,
  ne smije se pojaviti u nagovještaju nivoa 1 ili 2 — ni doslovno ni parafrazom.
- Za ovu klasu zadatka nagovještaj po pravilu sastavlja server iz strukture
  ponuđenih opcija; tvoj tekst se koristi samo kad server nije mogao unaprijed
  znati da je ovaj turn pomoć."""


def _help_state_block(session, student_message, ui_action, task_class):
    """Stanje potrebno ISKLJUČIVO za pomoć — bez težine, historije zadataka i
    ugovora izrade."""
    lines = ["AKTIVNI ZADATAK (pomoć se odnosi ISKLJUČIVO na njega):",
             f"- pitanje: {session['current_task']}"]
    if session.get("current_options"):
        lines.append("- PONUĐENE OPCIJE (učenik ih vidi ovim redom):")
        for option in session["current_options"]:
            lines.append(f"  {option['id']}) {option['text']}")
    if session.get("expected_answer_summary"):
        if ui_action == "hint_request":
            lines.append(
                "- INTERNI TAČAN ODGOVOR (učenik ga NE vidi; služi ti SAMO za "
                "samoprovjeru i NE SMIJEŠ ga napisati ni u kojem obliku): "
                f"{session['expected_answer_summary']}")
        else:
            lines.append(
                "- INTERNI TAČAN ODGOVOR (postupak mora završiti tačno na njemu): "
                f"{session['expected_answer_summary']}")
    lines.append(f"- KLASA ZADATKA (serverska činjenica): {task_class} — "
                 "primijeni ljestvicu pomoći za TU klasu.")
    lines.append(f"- BROJ VEĆ DATIH HINTOVA: {session.get('hint_level', 0)}")
    lines.append("- " + _hint_level_guidance(session.get("hint_level", 0), task_class))
    lines.append("- TRAŽENA AKCIJA (serverska činjenica; polje `intent` mora biti "
                 f"tačno ta vrijednost): {ui_action}")
    if session.get("recent_turns"):
        lines.append("- POMOĆ KOJU SI VEĆ DAO (ne ponavljaj je i ne parafraziraj):")
        for turn in session["recent_turns"][-_MAX_HISTORY_TURNS:]:
            lines.append(f"  Ti: {_clip(turn['tutor'])}")
    lines.append(f"- NOVA PORUKA UČENIKA: „{_clip(student_message, 400)}“")
    return "\n".join(lines)


def build_help_instructions(context):
    """Sistemski prompt turna POMOĆI. Stabilan prefiks, identičan za svih 534.

    Oba ugovora ljestvice stoje u prefiksu (i računski i propozicioni), pa je
    prefiks bajt za bajt isti za sve lekcije i keš prompta radi; koja ljestvica
    važi kaže server u ulazu."""
    shared = build_shared_math_rules(
        context.grade, context.title, context.oblast, mode="practice"
    )
    return (
        "Ti si iskusan nastavnik matematike u osnovnoj školi u Bosni i "
        "Hercegovini. Učenik ima AKTIVAN zadatak i tražio je pomoć. U ovom turnu "
        "NE praviš nov zadatak — pomažeš mu da sam riješi onaj koji već stoji "
        "pred njim.\n\n"
        f"{_HELP_FIELD_RULE}\n\n"
        f"{_HINT_LADDER_COMPUTATIONAL}\n\n"
        f"{_HINT_LADDER_PROPOSITIONAL}\n\n"
        f"{_SCALED_DIVISION_RULE}\n\n"
        f"{_HELP_NOTATION_RULE}\n\n"
        "TON: obraćaj se učeniku direktno, toplo i kratko. Nikad ne spominji "
        "interna polja, „namjeru“, recenzenta ni to da si model.\n\n"
        # --- dinamički (po lekciji) dio TEK OD OVE TAČKE ---
        f"{shared}\n"
    )


def build_help_input(context, session, student_message, ui_action="hint_request",
                     task_class=hint_policy.COMPUTATIONAL):
    return "\n\n".join([
        _lesson_block(context),
        _help_state_block(session, student_message, ui_action, task_class),
        "Vrati strukturisan odgovor prema šemi.",
    ])


# ODLUKA MORA PRATITI VLASTITI DOKAZ (živi gate cb80b92).
# Recenzent je nezavisno izračunao dokaz koji NE zadovoljava traženi nivo, pa
# ipak vratio `approve` — server je to morao odbiti i turn je propao iako je
# recenzent već imao sve što treba da vrati `correct` sa zamjenskim zadatkom u
# ISTOM (drugom i posljednjem) pozivu. Ovaj blok mu to izričito nalaže.
_REVIEWER_TARGET_LEVEL_RULE = """TARGET LEVEL DECISION RULE (the server enforces this deterministically):
- First independently calculate `reviewed_difficulty_evidence` from the visible task alone,
  counting EXACTLY by the HOW TO COUNT rules above.
- Then check your OWN numbers against the ACTIVE DIFFICULTY TARGETS above (or the
  lesson-relative profile when one is present) for the requested level of the final task.
- `approve` is permitted ONLY when the final package is valid AND your OWN evidence
  satisfies that target. If your own count is outside the target, you MUST NOT approve:
  return `correct` with a complete replacement that genuinely satisfies the target, or
  `fail_closed` if you cannot do that safely. The server runs the same validator on your
  evidence and rejects a contradictory approval, so approving a task you measured as
  outside the target only loses the turn.
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
#
# ARHITEKTONSKA FAZA 1 — DVIJE ISPRAVKE NAD OVIM BLOKOM:
#
#   1) BLOK NIJE BIO POSLAT. Uveden je u c7552b8, ali nikad nije bio uvezan u
#      `build_reviewer_instructions` — statička kapija Faze 0
#      (`tests/test_prompt_architecture_gate.py`) ga je izmjerila kao JEDINI
#      nedosežan blok pravila u sva tri prompt modula. Klasa kvara koju je
#      trebao spriječiti je ostala živa: FW-D04 (final40_c17538a) je pao kodom
#      `odobreno uprkos oborenim provjerama: ['inside_lesson']` uz odluku
#      `correct`. Zatečeni `_REVIEWER_DECISION_RULE` tu kontradikciju izričito
#      opisuje SAMO za `approve` („A single false check with `approve` is a
#      contradiction“), a `validate_reviewer` obara i `correct` — pa recenzentu
#      pravilo koje pokriva njegov stvarni slučaj nikad nije stiglo.
#
#   2) PRVA REČENICA JE USKLAĐENA S KOMPAKTNIM ODOBRENJEM (523dfce, koji je
#      došao POSLIJE c7552b8). Od tada recenzent na `approve` polje `final`
#      IZOSTAVLJA, a server objavljuje nepromijenjen nacrt. Doslovan zatečeni
#      tekst („the package you return in `final`“) bi na `approve` bio
#      neistinit i protivrječio bi bloku ODLUKA u istom promptu. Mijenja se
#      samo referent provjera, nikad njihov autoritet: `validate_reviewer`
#      već sudi nad `basis` (nacrt na `approve`, `final` na `correct`).
_REVIEWER_CHECK_SEMANTICS_RULE = """WHAT `checks.*` DESCRIBE (unambiguous):
- Every `checks.*` field describes the package the server will publish: for
  `correct` that is the CORRECTED task you return in `final`, and for `approve`
  it is the unchanged draft. Never report a defect you already fixed: if you
  repaired it, the check is true for what will be published.
- A false value in a mandatory check contradicts BOTH `approve` and `correct`.
  The server rejects such a payload whichever of the two you chose, so a
  correction whose checks still describe the old draft loses the whole turn.
- The server re-runs its own validators on your final package, so a check you
  report is never accepted as proof and never replaces those validators.
- Report honestly. `math_correct`, `marked_option_correct`, `inside_lesson`,
  `task_solvable_and_unambiguous`, `stem_requires_student_reasoning` and
  `exactly_one_option_correct` are the
  ones the server cannot verify for every
  lesson: a false value there fails the turn closed, which is the correct outcome.
  If you cannot make them true, return `fail_closed` instead of a package.
- `marked_option_correct` asks ONLY about the marked option; it says nothing
  about the other three. Whether some OTHER option is also correct is
  `exactly_one_option_correct`, and both must be judged on the FINAL package.
- Do not lower a check merely because you are unsure about tone or wording."""


# SEMANTIČKO SAMOOTKRIVANJE — vlasništvo RECENZENTA (živi ciljani recheck 7c13eb9).
#
# Objavljen je zadatak: „Tačka D leži unutar tog ugla (tj. zraka BD PROLAZI
# IZMEĐU BA i BC). Koji krak DIJELI ugao ABC na dva dijela?“, označeno BD.
# Geometrija je koherentna, nijedan ograničeni detektor nema šta dokazati —
# stem parafrazira traženu osobinu drugim riječima, pa mjerač preklapanja
# tokena po svojoj doktrini ćuti. Dokazivanje ekvivalencije parafraze traži
# razumijevač prirodnog jezika i izričito je van arhitekture ovog projekta.
#
# Zato ovu klasu sudi RECENZENT, kroz obaveznu provjeru koja mu već postoji u
# šemi. Serverski detektori ostaju mjerodavni za svoje dokazane klase.
_REVIEWER_STEM_REASONING_RULE = """`stem_requires_student_reasoning` (mandatory, model judgement):
- Ask yourself: does the task text itself already tell the student — in the same
  words OR IN A PARAPHRASE — that one specific option has the exact property or
  relationship the question asks them to identify? If yes, the check is FALSE.
- It is disclosure even when the wording differs. „ray BD passes between BA and
  BC" answers „which ray divides angle ABC"; „D is the only point between the
  arms" answers „which is the interior ray"; „a function is given" answers „is
  this a function". Different words, same decisive fact.
- It is NOT disclosure when the stem merely supplies the DATA the student needs:
  numerical angle measures to compare, a table of points to read, a construction
  set-up, or a named object whose OTHER property is being asked (a function is
  given and you ask for f(3); a triangle is given and you ask whether it is
  isosceles). The student must still calculate, compare, infer or recognise.
- REPAIR, when false: delete the sentence or clause that states the decisive
  property and put NEUTRAL data in its place, from which the answer follows —
  measures, positions, coordinates, a definition to apply. Keep the lesson, the
  question, the mathematics and exactly one correct option. Rewording the same
  decisive fact is NOT a repair. If the property cannot be made derivable
  without stating it, return `fail_closed`."""


# TAČNO JEDNA TAČNA OPCIJA — vlasništvo RECENZENTA (živi FINAL40 FW-F03, faf7a81).
#
# Objavljen je MCQ „Relacija je zadana tačkama … Predstavlja li ovaj skup tačaka
# funkciju?“ u kojem su DVIJE opcije bile tačne: „Da, jer svaki x ima tačno jedan
# y“ i „Da, i dozvoljeno je da se isti y ponavlja za različite x“. Obje ispravno
# odgovaraju, pa je učenik koji izabere drugu označen kao netačan bez greške.
#
# Nijedan deterministi to nije mogao vidjeti: `option_equivalence` i
# `mcq_integrity` dokazuju EKVIVALENCIJU, a te dvije opcije nisu ekvivalentne
# nego nezavisno TAČNE. `marked_option_correct` je bilo istinito — ono je
# jednostrana tvrdnja o označenoj opciji.
#
# KRITIČNA PROVENIJENCIJA: defekt je nastao u RECENZENTOVOJ ISPRAVCI drugog
# nalaza. Zato pravilo izričito traži PONOVNU ocjenu nad KONAČNIM paketom.
_REVIEWER_OPTION_UNIQUENESS_RULE = """`exactly_one_option_correct` (mandatory, model judgement):
- Judge the COMPLETE mathematical meaning of EVERY option against the FINAL
  stem, the FINAL domain and any stated assumptions. Exactly one complete option
  may be a mathematically correct answer to that exact question.
- TWO OPTIONS DO NOT HAVE TO LOOK ALIKE TO BOTH BE CORRECT. They need not be
  equal, equivalent, paraphrases, or even share vocabulary. If option C gives one
  valid reason the answer is „yes" and option D gives a DIFFERENT but also valid
  reason for the same „yes", then two complete options are correct and this check
  is FALSE — however differently they are worded.
- Every distractor must be FALSE as a complete option. Different wording, a
  different method, or a different true fact does not make an option a valid
  distractor. A distractor MAY contain a true intermediate statement as long as
  its decisive claim or conclusion makes the COMPLETE option false.
- Do NOT set this false merely because two options begin with the same word,
  share vocabulary, discuss the same concept, use related reasoning, or have
  similar shape — nor because one distractor contains a true intermediate step.
  The only question is: are two or more COMPLETE options mathematically correct
  answers to this exact final task?
- AFTER ANY REPAIR you make — to the stem, the options, the marked answer, the
  domain, the constraints or the mathematics — RE-EVALUATE this from scratch
  against the FINAL package. Never carry over the judgement from the package you
  started with: repairing one defect can itself create a second valid answer, and
  that is exactly how this defect reached a student.
- If you cannot confidently establish that exactly one complete option is
  correct, set it FALSE and return `fail_closed`. Uncertainty about multiple
  valid answers is never a reason to approve."""


# IDENTITET OPCIJE JE SERVERSKI — vlasništvo RECENZENTA nad KONAČNIM paketom.
#
# ŽIVA KAPIJA IZDANJA (canonical live release gate na 2fa9507, scenario težeg
# zadatka na prelazu nivoa 1→2; lekcija se namjerno ne navodi): recenzent je
# vratio `correct` s kompletno prepravljenim paketom, a njegov KONAČAN
# `solution` je imenovao slovo opcije. Server je odbio objavu
# (`solution_option_label_claim [solution]`) — dakle sigurno, ali scenario nije
# objavio ništa i kapija je pala.
#
# Zabrana je i ranije POSTOJALA, ali zakopana u sredini numerisane tačke 11,
# koja je inače o dokazima težine. Ovdje stoji kao ZASEBAN blok, i to zato što
# je živi dokaz da se prekršaj dešava BAŠ U ISPRAVCI: pravilo mora izričito
# tražiti ponovnu provjeru nad KONAČNIM tekstom, isto kao
# `_REVIEWER_OPTION_UNIQUENESS_RULE`. Jedna kanonska formulacija, ne dvije koje
# se mogu raziđu — zato je rečenica iz tačke 11 premještena, a ne duplirana.
#
# Deterministička kapija se NE dira: ona ostaje backstop koji pada zatvoreno.
# Ovo je popravka GENERISANJA, da backstop ne mora ni da se oglasi.
_REVIEWER_OPTION_IDENTITY_RULE = """OPTION IDENTITY IS THE SERVER'S, NEVER YOURS:
- The server shuffles the options AFTER you answer and only then assigns the
  letters a/b/c/d. Any letter or position you write is therefore a claim you
  cannot know, and the server rejects the whole package rather than publish it.
- In `solution` and `expected_answer` never identify the answer by option
  letter („opcija a", „odgovor je b", „pod c)"), by option number, or by
  position ("prva opcija", "treća tvrdnja", "posljednji odgovor"). Do not write
  "izaberi …" pointing at an option either.
- Say the MATHEMATICS instead. „Brojnik pokazuje koliko dijelova uzimamo, a
  nazivnik na koliko je jednakih dijelova cjelina podijeljena." is correct;
  „Tačan odgovor je b)." is not. Naming the correct answer's CONTENT is always
  allowed — it is only the server-owned label or position that is forbidden.
- WHEN THE OPTIONS ARE STATEMENTS („Koja tvrdnja … je tačna?"), the correct
  answer IS a statement, so RESTATE that statement in your own words and say why
  it holds. Never let the label be the subject of the sentence: „Tvrdnja pod a)
  je tačna." and „Tačan odgovor je b) jer …" are rejected packages, while „U
  razlomku $\\frac{3}{5}$ brojnik je $3$, a nazivnik $5$." carries the same
  explanation with no label at all.
- Ordinary mathematical names are NOT option labels and stay welcome: `tačka A`,
  `duž AB`, `ugao ABC`, `funkcija f`, `skup B`.
- AFTER ANY REPAIR you make, re-read your FINAL `solution` and
  `expected_answer` and remove every such label — including one you merely
  copied over from the draft. The package that is judged is the one you return."""


# VJEŽBAJMO V1 (F5K): `inside_lesson` više NIJE „zvuči srodno“. Živi audit:
# 14 objavljenih, matematički ispravnih zadataka POGREŠNE lekcije (grafik →
# uvrštavanje; mreža → zapremina; tekstualni → goli izraz; dokaz → brojevni
# razlomak). Server sada deterministički provjerava semantički ugovor lekcije
# i odbija prekršaj — ovaj blok recenzentu izričito kaže isto značenje.
_REVIEWER_LESSON_FIDELITY_RULE = """WHAT `inside_lesson` MEANS (the server enforces the same contract deterministically):
- `inside_lesson` is true ONLY when the task actually EXERCISES the selected
  lesson's own skill — for lessons with a SEMANTIC CONTRACT block below, that
  means satisfying that contract. "Same broad topic" is NOT enough.
- A mathematically correct task that substitutes a neighbouring skill (plain
  evaluation instead of a graph operation, a volume formula instead of net
  reasoning, a bare expression instead of a word problem, numeric fractions
  instead of an algebraic identity proof, a different congruence criterion)
  makes `inside_lesson` FALSE and `approve` FORBIDDEN.
- In that case return `correct` with a REPLACEMENT task that genuinely
  performs the required lesson action, or `fail_closed` if you cannot do that
  safely. Never reference a picture that does not exist in the task text."""


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
- For a reported over-long option (`task_structure_invalid`), SHORTEN the offending
  option to the bare answer — every option must stay under the server's character
  limit stated in the task rules. An option is an answer, not an explanation: move
  the reasoning into `solution`. Returning the same long option loses the turn.
- For `task_too_similar` the student asked for a NEW task and you returned the same
  exercise with different numbers or names. Do NOT merely change values, names or
  option order. Change the TASK ARCHETYPE or the reasoning structure while staying
  strictly inside the exact selected lesson: e.g. ask for a missing quantity instead
  of a direct result, ask which of several worked steps is wrong, ask to compare two
  results, ask to translate between representations, or move between a symbolic and a
  word-problem form. If the lesson genuinely supports only one task structure, keep
  the structure and return `fail_closed` rather than leaving the lesson.
- For `solution_answer_divergence` the marked option carries a value that your own
  `solution` never arrives at. One of the two is wrong. RE-DERIVE the arithmetic,
  then make the marked option, `expected_answer` and the final value of `solution`
  the SAME number. Do not "fix" this by rewriting the explanation around the old
  marked value, and do not silently change the task so the wrong value becomes
  right — the student is graded against the marked option.
- For `contract_answer_form_violation` the lesson's own contract requires the marked
  answer in a specific written form (an irreducible fraction: numerator and
  denominator share no divisor greater than 1). Reduce it fully — 4/6 is not
  acceptable where 2/3 is required — and recompute `expected_answer`, the marked
  option and `solution` to match. Changing only the prose is rejected.
- For `contract_multiple_irreducible_options` more than one option is BOTH equal in
  value to the fraction in the task AND already fully reduced, i.e. the task has two
  correct answers. Replace the extra one with a fraction that is either a different
  value or not yet reduced. Where the lesson tests the reduced FORM, distractors of
  the same value in unreduced form are correct and welcome — do not remove those.
- For `contract_equivalence_violation` the lesson's archetype requires the marked
  option to have the SAME VALUE as the fraction stated in the task text. Either mark
  the option that truly equals it, or rewrite the task so the marked option is that
  value; never leave a marked option of a different value.
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
    # Faza 4H (Workstream K): STABILAN, za sve lekcije IDENTIČAN prefiks stoji
    # PRVI (OpenAI prompt keš radi nad tačnim prefiksom), a sav sadržaj koji
    # zavisi od razreda/lekcije/oblasti dolazi TEK NA KRAJU instrukcija.
    shared = build_shared_math_rules(
        context.grade, context.title, context.oblast, mode="practice"
    )
    return (
        "Ti si stroga nezavisna kontrola kvaliteta za nastavni odgovor iz "
        "matematike (osnovna škola, Bosna i Hercegovina). Dobijaš NACRT drugog "
        "nastavnika i moraš ga provjeriti prije nego što ga učenik vidi.\n\n"
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
        f"{_SCALED_DIVISION_RULE}\n\n"
        # Isti ugovor autorstva kao Tutor: na `correct` RECENZENT piše opcije
        # koje se objavljuju, pa mora znati iste dokazive zapise (živi T1
        # recheck — recenzent je popravio jedan nalaz i ostavio drugi).
        f"{_SOLVE_TASK_AUTHORING_RULE}\n\n"
        f"{_SHARED_TARGET_BLOCK}\n\n"
        f"{_REVIEWER_LESSON_FIDELITY_RULE}\n\n"
        # ISTI blok koji Tutor dobija, bajt za bajt: jedna kanonska formulacija
        # umjesto dvije koje mogu da se raziđu (isti princip kao ugovori težine).
        f"{_EXPLICIT_REQUEST_RULE}\n\n"
        f"{_REVIEWER_DECISION_RULE}\n\n"
        # Faza 1: značenje `checks.*` stoji ODMAH uz pravilo odluke — to su dvije
        # polovine iste invarijante (`validate_reviewer`: odluka + provjere nad
        # istim `basis`). Ostaje u STABILNOM prefiksu (Workstream K), pa se keš
        # prompta ne mijenja.
        f"{_REVIEWER_CHECK_SEMANTICS_RULE}\n\n"
        f"{_REVIEWER_STEM_REASONING_RULE}\n\n"
        f"{_REVIEWER_OPTION_UNIQUENESS_RULE}\n\n"
        # Živa kapija izdanja (2fa9507): prekršaj je nastao u ISPRAVCI, pa
        # pravilo stoji odmah uz drugo pravilo koje traži ponovnu ocjenu nad
        # konačnim paketom.
        f"{_REVIEWER_OPTION_IDENTITY_RULE}\n\n"
        f"{_REVIEWER_TARGET_LEVEL_RULE}\n\n"
        f"{_REVIEWER_PREFLIGHT_RULE}\n\n"
        # --- dinamički (po lekciji) dio TEK OD OVE TAČKE (Workstream K) ---
        f"{shared}\n\n"
        f"{_semantic_contract_block(context)}"
        f"{_difficulty_profile_block(context)}"
        f"{_practice_contract_block(context)}"
        "ODLUKA:\n"
        # Faza 4H (Workstream J): eho paketa na odobrenju je bio najveći
        # pojedinačni trošak izlaza (medijalno ~1400 tokena ≈ 15+ s
        # generisanja). Server na `approve` objavljuje UPRAVO odobreni nacrt,
        # pa je eho i suvišan i nemoćan: eventualni poslani `final` se ignoriše.
        "- `approve` — nacrt je ispravan; vrati SAMO `decision`, `checks` i "
        "`reviewed_difficulty_evidence`, a polje `final` IZOSTAVI. Server "
        "objavljuje tačno nacrt koji si odobrio, bajt za bajt — `final` uz "
        "`approve` se ignoriše i ne može ništa izmijeniti;\n"
        "- `correct` — nacrt je popravljiv; u `final` vrati KOMPLETAN ispravljen "
        "payload (to je konačan odgovor koji učenik vidi). To uključuje i "
        "KOMPLETNU ZAMJENU zadatka kad je težina pogrešna za traženi nivo. "
        "Polje `selected_lesson_id` sadrži SAMO goli kanonski ID lekcije, "
        "nikad naslov i nikad \"Naslov (ID)\";\n"
        "- `fail_closed` — ne može se sigurno objaviti; navedi `fail_reason_code`.\n\n"
        "Ako matematika nije sigurna ili je zadatak dvosmislen, biraj "
        "`fail_closed`. Bolje bez odgovora nego pogrešan odgovor.\n"
        "Ne postoji treći poziv: kod `correct` je tvoj `final` ono što se "
        "objavljuje, a kod `approve` se objavljuje nepromijenjen nacrt."
    )


def build_reviewer_input(context, session, student_message, draft_json,
                         trusted_verdict=None, preflight_block="", ui_action="",
                         escalation_block=""):
    """`preflight_block` su DETERMINISTIČKI serverski nalazi o nacrtu.

    Prazan string kad server nije dokazao nijedan defekt — tada se ulaz ne
    mijenja ni za jedan znak u odnosu na raniji oblik."""
    blocks = [
        _lesson_block(context),
        _state_block(session, student_message, trusted_verdict, ui_action),
        "NACRT DRUGOG NASTAVNIKA (provjeri ga, ne vjeruj mu):\n" + draft_json,
    ]
    if preflight_block:
        blocks.append(preflight_block)
    if escalation_block:
        # ISTI blok koji je dobio Tutor — recenzent sudi o raznolikosti prema
        # istim serverskim činjenicama, nikad prema vlastitoj pretpostavci.
        blocks.append(escalation_block + "\n"
                      "- U `checks.matches_target_archetype` upiši da li "
                      "STVARNA matematička struktura zadatka odgovara CILJNOM "
                      "tipu iznad. Gledaj šta se traži i kojim koracima se "
                      "dolazi do rezultata, NE kako je zadatak nazvan u "
                      "potpisu — oznaka koja se slaže, a struktura koja se ne "
                      "slaže, znači false.\n"
                      "- U `checks.substantially_different_from_recent` upiši "
                      "da li je zadatak SUŠTINSKI druge matematičke strukture "
                      "od nedavnih. Isti tip s drugim imenima, predmetima ili "
                      "brojevima NIJE drugačiji — tada upiši false.")
    blocks.append("Vrati strukturisanu odluku prema šemi.")
    return "\n\n".join(blocks)


def intent_vocabulary():
    """Za testove: prompt mora nabrojati SVAKU namjeru iz šeme."""
    return tuple(INTENTS)
