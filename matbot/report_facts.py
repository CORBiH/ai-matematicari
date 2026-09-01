"""Faza 3C — ČINJENICE koje model smije vidjeti, i DOKAZNA POLITIKA nad njima.

Ovaj modul stoji između `report_input.build_report_input` (potpuno determinističan
izvještajni ulaz) i modela koji piše prozu za roditelja. Radi tačno dvije stvari:

  1. SUZI — iz administratorskog objekta izvuče samo ona polja koja su potrebna
     da bi se napisala rečenica, i ništa više. Sve što je PII ili interni
     identifikator ostaje s ove strane granice.
  2. ODLUČI — svaki zaključak o dokaznoj snazi donese deterministički, u
     Pythonu, i modelu preda GOTOVU OZNAKU (`evidence_level`), ne sirove
     brojače iz kojih bi model sam procjenjivao koliko je nešto pouzdano.

ZAŠTO JE (2) NEPREGOVARAČKO: model koji dobije „1 netačan odgovor iz lekcije X"
napisaće „učenik ne zna X". To nije stvar tona nego mjerenja — jedno pitanje
nije uzorak. Prag zato živi ovdje, u kodu, a ne u uputi modelu.

NIJEDAN BROJ SE OVDJE NE RAČUNA IZNOVA. Sve aritmetike (tačnost, delte,
prosjeci) već su izvedene u `report_input`; ovaj modul ih samo prosljeđuje ili
označava. Jedini izuzeci su `answers_total = correct + incorrect` i
`tasks_presented - answers_total`, koji postoje da model ne bi sabirao sam.

NE ŠALJE SE MODELU: e-mail, `student_id`, Thinkific vanjski ID, `lesson_id`,
`course_key`, sirovi tekst pitanja/odgovora, razgovori, `display_name`. Ime
učenika ispisuje PDF predložak — model ga ne treba da bi napisao izvještaj, a
svako ime koje model ne vidi je ime koje ne može procuriti ni pogrešno sklonuti.
"""

# --- DOKAZNA POLITIKA ------------------------------------------------------
# Pragovi su NAMJERNO konzervativni i izraženi u broju OPAŽENIH pitanja, jer je
# to jedino što stvarno mjerimo. `report_input.MIN_EVIDENCE_ITEMS_FOR_WEAKNESS`
# (3) već označava red kao `low_evidence`; ovdje se ta binarna oznaka razlaže u
# četiri nivoa da bi predložak i model mogli razlikovati „nemamo ništa" od
# „imamo naznaku" i od „ovo se ponovilo".
EVIDENCE_INSUFFICIENT = "insufficient"   # 0 pitanja — nema šta da se tvrdi
EVIDENCE_LIMITED = "limited"             # 1–2 pitanja — samo oprezna naznaka
EVIDENCE_MODERATE = "moderate"           # 3–5 pitanja — smije se imenovati
EVIDENCE_STRONG = "strong"               # 6+ pitanja — smije se tvrditi

EVIDENCE_LIMITED_MIN = 1
EVIDENCE_MODERATE_MIN = 3
EVIDENCE_STRONG_MIN = 6

# Koliko lekcija najviše ide modelu i u PDF. Izvještaj za roditelja nije
# administratorska tabela — vidi Dio 21.
MAX_LESSON_ROWS = 5
# Koliko sekcija kursa ide u činjenice. Bira se po korisnosti, ne po redoslijedu.
MAX_SECTION_ROWS = 6

# Kontrolni kao CJELINA: jedan test je premali uzorak da bi se o mjesecu
# govorilo tvrdo, bez obzira što je 20 pitanja. Prati isti duh kao gore.
KONTROLNI_LIMITED_MAX_ATTEMPTS = 1


def evidence_level(evidence_items):
    """Broj opaženih pitanja → dokazna oznaka. Jedina tačka koja to odlučuje."""
    count = int(evidence_items or 0)
    if count < EVIDENCE_LIMITED_MIN:
        return EVIDENCE_INSUFFICIENT
    if count < EVIDENCE_MODERATE_MIN:
        return EVIDENCE_LIMITED
    if count < EVIDENCE_STRONG_MIN:
        return EVIDENCE_MODERATE
    return EVIDENCE_STRONG


def _lesson_rows(outcomes):
    """Lekcije sortirane po DOKAZNOJ težini, pa po broju grešaka.

    Namjerno se NE sortira samo po broju grešaka: lekcija s 1/1 netačnim
    izgleda dramatično, a ne znači ništa. Jače potkrijepljen nalaz ide prvi."""
    order = {EVIDENCE_STRONG: 0, EVIDENCE_MODERATE: 1,
             EVIDENCE_LIMITED: 2, EVIDENCE_INSUFFICIENT: 3}
    rows = []
    for outcome in outcomes or []:
        asked = int(outcome.get("evidence_items") or 0)
        wrong = int(outcome.get("incorrect_items") or 0)
        level = evidence_level(asked)
        rows.append({
            # `lesson_id` NE ide dalje — model piše o gradivu, ne o šifri.
            "lesson_name": outcome.get("lesson_name") or "",
            "area_name": outcome.get("area_name") or "",
            "incorrect_items": wrong,
            "correct_items": max(asked - wrong, 0),
            "evidence_items": asked,
            "evidence_level": level,
            # Zadržano zbog saglasnosti s Fazom 2 i zbog testova koji mjere da
            # se oznaka propagira sve do ugovora prema modelu.
            "low_evidence": level in (EVIDENCE_INSUFFICIENT, EVIDENCE_LIMITED),
        })
    rows.sort(key=lambda r: (order[r["evidence_level"]], -r["incorrect_items"],
                             r["lesson_name"]))
    return rows[:MAX_LESSON_ROWS]


def _section_rows(sections):
    """Sekcije koje roditelju nešto znače: one s napretkom ili s promjenom.

    Sedam redova „0 %" nije izvještaj nego buka (Dio 21). Ako baš nijedna
    sekcija nema napredak, vraća se prvih nekoliko da odjeljak ne bude prazan —
    činjenica „nigdje još nema napretka" je legitiman nalaz."""
    useful = []
    for section in sections or []:
        current = section.get("current_progress_percent")
        delta = section.get("delta_progress_percent")
        if (current or 0) > 0 or (delta or 0) != 0:
            useful.append(section)
    chosen = useful or list(sections or [])[:MAX_SECTION_ROWS]
    chosen = sorted(chosen,
                    key=lambda s: (-(s.get("current_progress_percent") or 0),
                                   s.get("ordinal") or 0))[:MAX_SECTION_ROWS]
    return [{
        "name": s.get("section_name") or "",
        "current_percent": s.get("current_progress_percent"),
        "previous_percent": s.get("previous_progress_percent"),
        "delta_percent": s.get("delta_progress_percent"),
    } for s in chosen]


# Koliko sekcija platforme ide RODITELJU. Sedam sekcija s nula posto nije
# izvještaj nego buka; puna lista ostaje administratoru.
MAX_PARENT_SECTIONS = 3


def _instruction_facts(instruction):
    """Faza 3D — časovi u ugovoru prema modelu.

    SLOBODAN TEKST NE ULAZI. `parent_comments` su zapažanja instruktora i mogu
    slučajno nositi lični podatak, pa ostaju u PDF-u i administratorskom
    pregledu, a modelu se ne šalju NIKAD (Dio 20). Model dobija samo brojeve,
    kanonske nazive gradiva i gotove signale."""
    instruction = instruction or {}
    activity = instruction.get("activity") or {}
    homework = instruction.get("homework") or {}
    return {
        "available": bool(instruction.get("available")),
        "sessions_total": int(instruction.get("sessions_total") or 0),
        "present_count": int(instruction.get("present_count") or 0),
        "absent_count": int(instruction.get("absent_count") or 0),
        # Prosjek ostaje None kad nema ocijenjenih časova — 0/5 bi bila
        # izmišljena mjera o učeniku koji nije ocijenjen.
        "activity_average": activity.get("average"),
        "activity_rated_sessions": int(activity.get("rated_sessions") or 0),
        "homework_assigned": int(homework.get("assigned_count") or 0),
        "homework_done": int(homework.get("done_count") or 0),
        "homework_not_done": int(homework.get("not_done_count") or 0),
        # SAMO KURIKULARNO GRADIVO. `build_monthly_summary` ručne teme drži
        # odvojeno (`custom_topics`) i one ovdje NAMJERNO ne ulaze: model ova
        # polja čita kao gradivo iz plana, pa bi „Uvodni čas" u spisku lekcija
        # mogao završiti kao „gradivo koje treba uvježbati". Čas se i dalje
        # broji u prisustvu, angažmanu i zadaći — samo se ne imenuje kao lekcija.
        "areas_worked": list(instruction.get("areas_worked") or [])[:MAX_LESSON_ROWS],
        "lessons_worked": list(instruction.get("lessons_worked") or [])[:MAX_LESSON_ROWS],
        # Signali su SERVERSKA odluka. Bez njih bi model sam procjenjivao da je
        # „prisustvo odlično" na osnovu dva časa.
        "signals": list(instruction.get("signals") or []),
    }


def _parent_sections(rows, previous_available):
    """Najviše tri sekcije koje roditelju stvarno nešto govore.

    Kad prošli mjesec postoji, prednost imaju sekcije s POZITIVNOM promjenom —
    to je jedino što se smije opisati kao rad u ovom mjesecu. Bez prethodnog
    snimka nema promjene, pa se biraju sekcije s najvećim tekućim napretkom i
    NE tvrdi se da su rađene baš ovog mjeseca."""
    rows = list(rows or [])
    if previous_available:
        moved = [r for r in rows if (r.get("delta_percent") or 0) > 0]
        if moved:
            moved.sort(key=lambda r: -(r.get("delta_percent") or 0))
            return moved[:MAX_PARENT_SECTIONS]
    active = [r for r in rows if (r.get("current_percent") or 0) > 0]
    active.sort(key=lambda r: -(r.get("current_percent") or 0))
    return active[:MAX_PARENT_SECTIONS]


def build_ai_facts(payload):
    """`build_report_input(...)` → objekat koji smije u prompt.

    Ulaz je administratorski izvještajni ulaz; izlaz je namjerno siromašniji.
    Sve što ovdje nije eksplicitno prepisano — ne postoji za model."""
    thinkific = payload.get("thinkific") or {}
    matbot = payload.get("matbot") or {}

    correct = int(matbot.get("practice_correct") or 0)
    incorrect = int(matbot.get("practice_incorrect") or 0)
    answers_total = correct + incorrect
    presented = int(matbot.get("practice_tasks") or 0)

    snapshot_missing = bool(thinkific.get("snapshot_missing"))
    # „Prethodni mjesec postoji" je ČINJENICA SERVERA, ne procjena modela.
    # Bez nje model nema pravo ni na jednu riječ o trendu (Dio 3 i Dio 11).
    previous_available = (not snapshot_missing
                          and thinkific.get("previous_percent_viewed") is not None)

    attempts = int(matbot.get("kontrolni_attempts") or 0)
    question_total = int(matbot.get("kontrolni_total") or 0)
    if attempts <= 0:
        kontrolni_evidence = EVIDENCE_INSUFFICIENT
    elif attempts <= KONTROLNI_LIMITED_MAX_ATTEMPTS:
        # Jedan test — bez obzira na broj pitanja — ostaje naznaka.
        kontrolni_evidence = EVIDENCE_LIMITED
    else:
        kontrolni_evidence = evidence_level(question_total)

    lessons = _lesson_rows(matbot.get("lesson_outcomes"))
    has_any_strong = any(row["evidence_level"] in (EVIDENCE_MODERATE, EVIDENCE_STRONG)
                         for row in lessons)

    sections = _section_rows(thinkific.get("sections"))
    facts = {
        "report_month": payload.get("report_month"),
        "grade": (payload.get("profile") or {}).get("grade"),
        # PRVI U UGOVORU jer je prvi i po prioritetu (Faza 3D).
        "instruction": _instruction_facts(payload.get("instruction")),
        "thinkific": {
            "available": not snapshot_missing,
            "percent_viewed": thinkific.get("percent_viewed"),
            "percent_completed": thinkific.get("percent_completed"),
            "previous_available": previous_available,
            "delta_percent_viewed": thinkific.get("delta_percent_viewed"),
            "delta_percent_completed": thinkific.get("delta_percent_completed"),
            "sections": sections,
            # Ono što roditelj STVARNO vidi: najviše tri sekcije. Ukupni godišnji
            # procenti kursa ostaju iznad (i u adminu), ali kao naslovna mjera
            # roditelju su zavodljivi — nisu znanje.
            "parent_sections": _parent_sections(sections, previous_available),
        },
        "matbot": {
            "any_activity": bool(matbot.get("active_days") or presented
                                 or attempts or matbot.get("explain_count")
                                 or matbot.get("quick_count")),
            "active_days": int(matbot.get("active_days") or 0),
            "practice": {
                "tasks_presented": presented,
                "answers_total": answers_total,
                "correct": correct,
                "incorrect": incorrect,
                # Tačnost ostaje None kad nema imenioca — 0 % bi bila izmišljena
                # mjera o učeniku koji nije odgovarao (Dio 23).
                "accuracy_percent": matbot.get("practice_accuracy"),
                "hints_used": int(matbot.get("hints_used") or 0),
                "full_solutions_shown": int(matbot.get("full_solutions_shown") or 0),
                # Razlika prikazanih i odgovorenih se PRENOSI kao broj, ali se
                # nigdje ne tumači: zadatak je mogao biti zamijenjen novim, pa
                # „napušteno" ne bi bilo mjerenje nego pretpostavka (Dio 4).
                "presented_not_answered": max(presented - answers_total, 0),
            },
            "explain_count": int(matbot.get("explain_count") or 0),
            "quick_count": int(matbot.get("quick_count") or 0),
            "kontrolni": {
                "attempts": attempts,
                "average_score_percent": matbot.get("kontrolni_average"),
                "correct_total": int(matbot.get("kontrolni_correct") or 0),
                "question_total": question_total,
                "evidence_level": kontrolni_evidence,
            },
            "lesson_evidence": lessons,
            # Puni dokaz ostaje IZNAD (model ga smije koristiti kao kontekst);
            # plan ispod kaže šta smije biti IMENOVANO roditelju.
            "focus_plan": _focus_plan(lessons),
        },
        # Zbirna zastavica: kad ništa nije dovoljno potkrijepljeno, model mora
        # to REĆI, a ne popuniti odjeljke izmišljenim jakim stranama (Dio 5).
        "overall_evidence_sufficient": bool(has_any_strong
                                            or kontrolni_evidence in
                                            (EVIDENCE_MODERATE, EVIDENCE_STRONG)),
    }
    return facts


# --- FOKUS ZA RODITELJA -----------------------------------------------------
# Roditelj ne čita dijagnostiku. Pet imenovanih lekcija, od kojih tri stoje na
# po jednom pitanju, čitaju se kao spisak propusta — a to nijedna od njih ne
# dokazuje. Zato se izbor pravi OVDJE, deterministički, PRIJE modela: model
# dobija gotov plan i nema šta da bira. Puni dokaz ostaje u administratorskom
# pregledu (`report_input`), koji ova funkcija ne dira.
MAX_NAMED_LESSONS = 3
# Dvije ograničene lekcije u istoj oblasti su signal o OBLASTI, ne o lekcijama.
LIMITED_GROUP_MIN = 2
MAX_FOCUS_BULLETS = 3
# Kad ničeg jačeg nema, kraće je poštenije: manje stavki, manje prividne težine.
MAX_FOCUS_BULLETS_LIMITED = 2


def _focus_plan(rows):
    """Šta smije biti IMENOVANO roditelju i koliko stavki fokus smije imati.

    Redoslijed je dokazni, ne dramaturški: strong pa moderate. Ograničeni nalazi
    se ne imenuju dok postoji išta jače — jedan netačan odgovor nije nalaz o
    znanju (Dio 5). Kad jačeg nema, imenuje se NAJVIŠE jedan, da izvještaj ne
    ostane bez ijednog konkretnog traga."""
    ranked = [row for row in rows
              if row["evidence_level"] in (EVIDENCE_STRONG, EVIDENCE_MODERATE)]
    limited = [row for row in rows if row["evidence_level"] == EVIDENCE_LIMITED]

    named = [row["lesson_name"] for row in ranked if row["lesson_name"]]
    named = named[:MAX_NAMED_LESSONS]

    by_area = {}
    for row in limited:
        by_area.setdefault(row["area_name"] or "", []).append(row)

    grouped, isolated = [], []
    for area, items in sorted(by_area.items()):
        if len(items) >= LIMITED_GROUP_MIN:
            # Jedna oprezna rečenica o oblasti umjesto tri imena lekcija.
            grouped.append({"area_name": area, "lesson_count": len(items),
                            "evidence_level": EVIDENCE_LIMITED})
        else:
            isolated.extend(items)

    if not named and isolated:
        first = isolated[0]["lesson_name"]
        named = [first] if first else []

    return {
        "named_lessons": named,
        "grouped_areas": grouped,
        "max_named_lessons": MAX_NAMED_LESSONS,
        "max_focus_bullets": (MAX_FOCUS_BULLETS if ranked
                              else MAX_FOCUS_BULLETS_LIMITED),
    }


def trusted_labels(facts):
    """TAČNI nazivi iz kurikuluma koje je server SAM poslao modelu.

    Postoji zbog žive greške: model je ispravno imenovao lekciju „Djeljivost sa
    3", a provjera brojeva je „3" iz NAZIVA pročitala kao izmišljenu mjeru i
    odbila cijeli izvještaj. Pogađa 11 od 513 stvarnih naziva (2,1 %), među
    njima „Pravila djeljivosti sa 2, 3, 4, 5, 6, 9, 10, 15 i 25" i
    „Konstrukcije uglova 60°, 30°, 90° i 45°" — dakle upravo one lekcije koje
    prompt najviše i želi imenovati.

    OVO NISU DOPUŠTENE VRIJEDNOSTI, NEGO POUZDANI RASPONI TEKSTA. Razlika je
    suština ispravke: kad bi „60" postalo globalno dopušteno samo zato što
    stoji u naslovu o uglovima, prošla bi i rečenica „Tačnost je 60 %" koja
    nikad nije izmjerena. Zato se naziv MASKIRA prije traženja brojeva, a skup
    dopuštenih mjera ostaje netaknut (`allowed_numbers`).

    Vraćaju se samo nazivi S CIFROM — jedini koji uopšte mogu uticati na
    traženje brojeva. Uži skup znači i užu površinu maskiranja.

    Izvor je zatvoren: `lesson_name`, `area_name` i naziv Thinkific sekcije —
    dakle tačno ona polja koja `build_ai_facts` šalje modelu. Tekst učenika,
    sirova pitanja i odgovori ovdje ne ulaze NIKAD."""
    labels = set()
    for section in (facts.get("thinkific") or {}).get("sections") or []:
        labels.add((section.get("name") or "").strip())
    for lesson in (facts.get("matbot") or {}).get("lesson_evidence") or []:
        labels.add((lesson.get("lesson_name") or "").strip())
        labels.add((lesson.get("area_name") or "").strip())
    # Faza 3D: nazivi gradiva s časa su isti kurikulum i imaju isti problem —
    # „Djeljivost sa 3" u prozi ne smije biti izmišljena mjera.
    instruction = facts.get("instruction") or {}
    for name in list(instruction.get("areas_worked") or []) +             list(instruction.get("lessons_worked") or []):
        labels.add((name or "").strip())
    return {label for label in labels
            if label and any(ch.isdigit() for ch in label)}


def allowed_numbers(facts):
    """Svi brojevi koje model SMIJE spomenuti. Osnova provjere činjeničnosti.

    Vraća skup float-ova. Sve numeričko u izlazu modela koje nije ovdje je
    izmišljeno — vidi `report_validation.unsupported_numbers`."""
    values = set()

    def add(value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            values.add(float(value))

    thinkific = facts.get("thinkific") or {}
    for key in ("percent_viewed", "percent_completed",
                "delta_percent_viewed", "delta_percent_completed"):
        add(thinkific.get(key))
    for section in thinkific.get("sections") or []:
        for key in ("current_percent", "previous_percent", "delta_percent"):
            add(section.get(key))

    matbot = facts.get("matbot") or {}
    add(matbot.get("active_days"))
    add(matbot.get("explain_count"))
    add(matbot.get("quick_count"))
    practice = matbot.get("practice") or {}
    for key in ("tasks_presented", "answers_total", "correct", "incorrect",
                "accuracy_percent", "hints_used", "full_solutions_shown",
                "presented_not_answered"):
        add(practice.get(key))
    kontrolni = matbot.get("kontrolni") or {}
    for key in ("attempts", "average_score_percent", "correct_total",
                "question_total"):
        add(kontrolni.get(key))
    for lesson in matbot.get("lesson_evidence") or []:
        for key in ("incorrect_items", "correct_items", "evidence_items"):
            add(lesson.get(key))

    # Faza 3D: brojke s časova su izmjerene isto kao i sve ostale.
    instruction = facts.get("instruction") or {}
    for key in ("sessions_total", "present_count", "absent_count",
                "activity_average", "activity_rated_sessions",
                "homework_assigned", "homework_done", "homework_not_done"):
        add(instruction.get(key))

    # GRANICE SKALE ANGAŽMANA SU ČINJENICA O MJERI, NE IZMIŠLJEN BROJ.
    #
    # ŽIVI NALAZ (izdanje 1ed172c): prompt 3d-2 IZRIČITO traži rečenicu
    # „Prosječna aktivnost na časovima bila je 4,0 / 5.", a ovaj validator je
    # peticu odbijao kao izmišljen broj kad se nijedna izmjerena vrijednost nije
    # slučajno poklopila s njom. Mjesec sa četiri časa (3 prisutna, prosjek 4,0)
    # je zato padao zatvoreno — prompt i provjera su tvrdili suprotno.
    # Nedostupnost, ne netačnost, ali svejedno kvar.
    #
    # `ACTIVITY_MIN`/`ACTIVITY_MAX` dolaze iz `student_sessions`, gdje skala i
    # živi — ovdje se namjerno NE prepisuju kao brojevi, da ne bi postojala dva
    # izvora istine o istoj skali.
    #
    # SAMO KAD ANGAŽMAN STVARNO POSTOJI. Bez ijednog ocijenjenog časa granice se
    # ne dodaju: proizvod podržava skalu uvijek, ali izvještaj bez mjerenja nema
    # o čemu da govori, pa mu ni brojevi skale nisu činjenica.
    if int(instruction.get("activity_rated_sessions") or 0) >= 1:
        from matbot import student_sessions

        add(student_sessions.ACTIVITY_MIN)
        add(student_sessions.ACTIVITY_MAX)

    add(facts.get("grade"))
    return values
