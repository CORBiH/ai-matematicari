"""Ograničena AI-eskalacija zbog RAZNOLIKOSTI — pilot za jednu lekciju.

ŽIVI QA NALAZ (direktor škole, tekstualni zadaci s razlomcima u 6. razredu):
traženje sve težih zadataka vraća isti matematički arhetip s kozmetičkim
zamjenama — drugo ime, drugi predmet, drugi brojevi. Mjereno nad 3600
determinističkih paketa: 2960 različitih tekstova, ali samo 12 rečeničnih
kostura i TAČNO 2 strukturna arhetipa, ista na sva tri nivoa. Naivna mjera
„jedinstven tekst“ zato ne opisuje ono što učenik doživljava.

ARHITEKTURA (namjerno mala):
  • obična progresija ostaje DETERMINISTIČKA i nula-poziva;
  • model se uključuje TEK kao eskalacija — kad je deterministička ljestvica
    iscrpljena (traži se teže na maksimumu) ili kad učenik izričito traži
    drugačiji tip zadatka;
  • eskalacija koristi POSTOJEĆI Tutor → Recenzent put i njegove validatore;
    nema trećeg poziva, nema ponovnog generisanja.

ŠTA JE OVDJE VLASNIŠTVO SERVERA (nikad modela):
  • KADA se eskalira (`decide`);
  • KOJI arhetip se traži (`select_target` nad ugovorom lekcije i historijom);
  • ŠTA je nedavno viđeno (`recent_archetypes` iz `recent_task_signatures`).
Model dobija cilj i granice; on bira samo prozu i brojeve unutar njih.

GRANICA PILOTA: ugovor lekcije (`creative_escalation`) — uključena je tačno
jedna lekcija; nijedna druga ne mijenja ponašanje. Arhetipi NISU
izmišljeni — to su `problem_types` iz kompajliranog ugovora lekcije, isti
enum koji deterministički generator već koristi.
"""
from dataclasses import dataclass

# Pilot se uključuje ISKLJUČIVO PODACIMA — `creative_escalation: "enabled"` u
# ugovoru lekcije. Nikad ID lekcije u Pythonu (matbot/tutor/ to izričito
# zabranjuje), i nikad cijela porodica: porodica `structured_word_problem` ima
# devet lekcija u razredima 6–9, a pilot mijenja ponašanje tačno JEDNE dok se
# ne potvrdi uživo. Širenje = jedan red u dodjeli lekcije, bez izmjene koda.
_PILOT_PARAMETER = "creative_escalation"
_PILOT_ENABLED = "enabled"

# Porodica i dalje mora nositi strukturisan enum arhetipa — bez njega server
# nema čime da bira cilj, pa eskalacija ne bi imala smisla.
_ARCHETYPE_PARAMETER = "problem_types"
# KREATIVNI POOL JE ODVOJEN OD NIVOA. Obična ljestvica se sužava po nivou
# (`problem_types_by_level`), a eskalacija se javlja TEK na maksimumu i postoji
# upravo zato da ponudi strukturu koju niži nivoi ne nose. Kad bi cilj birala
# iz nivo-pool-a, ispravka težine bi ponovo izgladnjela eskalaciju — tačno
# stanje zbog kojeg je enum i proširivan.
_CREATIVE_ARCHETYPE_PARAMETER = "creative_problem_types"

# Koliko unazad gledamo kad biramo „nešto drugo“. Namjerno malo: dovoljno da
# se izbjegne neposredno ponavljanje, premalo da zaključa ponudu kad lekcija
# ima samo dva-tri arhetipa.
RECENT_WINDOW = 3

# Razlozi eskalacije — zatvoren skup, jedan vlasnik rutiranja.
REASON_MAX_LEVEL_HARDER = "max_level_harder"
REASON_EXPLICIT_VARIETY = "explicit_variety"


@dataclass(frozen=True)
class CreativeEscalationDecision:
    """Serverska odluka: eskaliraj, i to na TAJ arhetip."""

    reason: str
    target_archetype: str
    supported_archetypes: tuple
    recent_archetypes: tuple
    level: int
    # Značenje svakog dozvoljenog arhetipa, iz ugovora lekcije. Identifikator
    # sam po sebi modelu ne kazuje ništa — a cilj mora ostati mašinski
    # provjerljiv identifikator, ne opis.
    definitions: tuple = ()

    @property
    def diversity_possible(self) -> bool:
        """False kad lekcija ima jedan arhetip — tada AI ne može stvoriti
        strukturnu raznolikost i to se ne smije lažno prijavljivati."""
        return len(self.supported_archetypes) > 1


def _contract_parameters(context) -> dict:
    contract = getattr(context, "semantic_contract", None)
    if contract is None:
        return {}
    return dict(getattr(contract, "parameters", {}) or {})


def _contract_archetypes(context) -> tuple:
    parameters = _contract_parameters(context)
    creative = tuple(parameters.get(_CREATIVE_ARCHETYPE_PARAMETER) or ())
    return creative or tuple(parameters.get(_ARCHETYPE_PARAMETER) or ())


def is_pilot_lesson(context) -> bool:
    """Lekcija je u pilotu SAMO ako je ugovor izričito uključi i ako nosi
    strukturisan enum arhetipa iz kojeg server bira cilj."""
    parameters = _contract_parameters(context)
    if parameters.get(_PILOT_PARAMETER) != _PILOT_ENABLED:
        return False
    return len(_contract_archetypes(context)) >= 1


def recent_archetypes(session, lesson_id, limit=RECENT_WINDOW,
                      supported=()) -> tuple:
    """Nedavno objavljeni arhetipi ove lekcije, najnoviji POSLJEDNJI.

    Čita se POSTOJEĆA serverska historija (`recent_task_signatures`) — pilot
    ne uvodi nijedno novo stanje sesije. `operation_or_relation` je isti enum
    koji ugovor deklariše kao `problem_types`.

    FILTRIRANJE PRI ČITANJU (živi nalaz ciljane kampanje): Tutor je jednom
    upisao slobodan tekst („successive subtraction of fractions of an initial
    quantity“) u potpis, pa je ta vrijednost završila u historiji i zauzela
    mjesto u prozoru od tri. Objava to od sada odbija, ali ZATEČENE sesije
    mogu i dalje nositi takvu vrijednost. Zato se pri čitanju zadržavaju samo
    vrijednosti koje lekcija stvarno deklariše — nepoznato se PRESKAČE, ne
    ruši planer i ne troši prozor. Historija se NE prepisuje: filtriranje pri
    čitanju je dovoljno i ne dira tuđe stanje."""
    import json

    allowed = frozenset(supported or ())
    found = []
    for record in (session.get("recent_task_signatures") or []):
        if record.get("lesson_id") != lesson_id:
            continue
        try:
            signature = json.loads(record.get("structured_signature") or "{}")
        except (TypeError, ValueError):
            continue
        archetype = signature.get("operation_or_relation") or ""
        if not archetype:
            continue
        if allowed and archetype not in allowed:
            continue          # slobodan tekst iz zatečene sesije — ignoriši
        found.append(archetype)
    return tuple(found[-limit:]) if limit else tuple(found)


# ---------------------------------------------------------------------------
# SERVERSKA VALIDACIJA ARHETIPA OBJAVLJENOG PAKETA
# ---------------------------------------------------------------------------
# ŽIVI NALAZ (ciljana kampanja): server je tražio `fraction_remainder`, Tutor je
# vratio paket čiji je `operation_or_relation` bio slobodan tekst, a recenzent
# ga je odobrio. Paket je objavljen i historija je zagađena.
#
# GRANICA KOJU OVO ZATVARA: model smije pisati SADRŽAJ, ali ne smije
# redefinisati serversku taksonomiju. Enum dolazi iz ugovora lekcije, pa ova
# provjera nigdje ne poznaje ni jednu konkretnu vrijednost.
ARCHETYPE_NOT_IN_CONTRACT = "creative_archetype_not_in_contract"
ARCHETYPE_NOT_TARGET = "creative_archetype_not_target"


def archetype_failure(decision, operation_or_relation) -> str:
    """Kod greške ili "" — čisto deterministička, recenzent je ne može nadjačati.

    Dvije odvojene tvrdnje: (1) vrijednost uopšte pripada enumu lekcije,
    (2) i to baš onom arhetipu koji je server izabrao."""
    if decision is None:
        return ""
    value = (operation_or_relation or "").strip()
    if value not in decision.supported_archetypes:
        return ARCHETYPE_NOT_IN_CONTRACT
    if value != decision.target_archetype:
        return ARCHETYPE_NOT_TARGET
    return ""


# ---------------------------------------------------------------------------
# EGZAKTNA PROVJERA ODGOVORA KREATIVNOG PAKETA
# ---------------------------------------------------------------------------
# Živi nalaz je pokazao da recenzentov boolean sam po sebi nije dovoljan da
# paket bude ispravan. Za porodice čiji IR server poznaje (strukturisani
# tekstualni zadaci) paket u potpisu nosi iste veličine koje deterministički
# generator ionako upisuje, pa ih server može PRERAČUNATI egzaktnim rješavačem
# i uporediti s označenom opcijom. Nijedna proza se ne parsira.
ANSWER_NOT_VERIFIABLE = "creative_answer_not_verifiable"
ANSWER_MISMATCH = "creative_answer_mismatch"
ANSWER_NOT_UNIQUE = "creative_answer_not_unique"


def _option_value(text):
    from fractions import Fraction
    body = (text or "").strip()
    if body.startswith("$") and body.endswith("$"):
        body = body[1:-1]
    try:
        return Fraction(body.strip())
    except (ValueError, ZeroDivisionError, ArithmeticError):
        return None


def answer_failure(decision, task) -> str:
    """Kod greške ili "" — egzaktna serverska provjera odgovora i jedinstvenosti.

    Vraća "" i kad tip nema rekonstrukciju iz potpisa (tada je autoritet
    ostatak postojećih validatora) — ali NE kad tip jeste podržan, a paket
    činjenice ne nosi ili se ne slaže. Tada pada zatvoreno."""
    from matbot.mathkernel import wordfacts

    if decision is None or task is None:
        return ""
    if decision.target_archetype not in wordfacts.UNKNOWN_BY_TYPE:
        return ""
    parameters = {p.name: p.value
                  for p in task.task_signature.normalized_parameters}
    # RAZLIKA KOJA SE MORA ČUVATI: „nije ni pokušao dati činjenice“ nije isto
    # što i „dao ih je, ali pogrešne“. Paket koji ne nosi NIJEDNU veličinu
    # traženog tipa nema šta da se provjeri — tada ovaj sloj ĆUTI i autoritet
    # ostaju postojeći validatori i recenzent (inače bi svaki model-paket bez
    # IR-a padao, što je šira promjena nego što ovo proširenje nosi). Ali čim
    # paket TVRDI neku od traženih veličina, mora se i preračunati: činjenice
    # drugog arhetipa tada padaju, a ne prolaze kao „nema šta da se provjeri“.
    required = set(wordfacts.REQUIRED_FACTS.get(decision.target_archetype, ()))
    if not (required & set(parameters)):
        return ""
    try:
        truth = wordfacts.solve_from_parameters(decision.target_archetype,
                                                parameters)
    except Exception:                       # noqa: BLE001 — kernel greška
        return ANSWER_NOT_VERIFIABLE
    values = [_option_value(option.text) for option in task.options]
    marked = values[task.correct_option_index]
    if marked is None or marked != truth:
        return ANSWER_MISMATCH
    if sum(1 for value in values if value == truth) != 1:
        return ANSWER_NOT_UNIQUE
    return ""


def select_target(supported, recent) -> str:
    """Arhetip koji NIJE nedavno viđen; inače najdavnije viđeni.

    Čisto serverska aritmetika nad zatvorenim enumom — model nikad ne odlučuje
    da li su posljednja tri ista."""
    supported = tuple(supported)
    if not supported:
        return ""
    unseen = [name for name in supported if name not in recent]
    if unseen:
        return unseen[0]
    # Svi su viđeni: uzmi onaj čija je posljednja pojava NAJSTARIJA.
    def last_seen(name):
        for offset, seen in enumerate(reversed(recent)):
            if seen == name:
                return offset
        return len(recent)

    return max(supported, key=last_seen)


def decide(context, session, deterministic_intent, transition,
           explicit_variety=False):
    """Jedina tačka odluke o eskalaciji. Vrati odluku ili None.

    `transition` je serverska tranzicija nivoa (matbot/difficulty_level.py);
    `boundary_reason == "at_maximum"` znači „traženo je teže, a već smo na
    maksimumu“ — tačno stanje u kojem je deterministička ljestvica iscrpljena.
    Nivo se pri tome NE mijenja: eskalacija je promjena RUTE, ne izmišljanje
    četvrtog nivoa."""
    if not is_pilot_lesson(context):
        return None

    reason = ""
    if explicit_variety:
        reason = REASON_EXPLICIT_VARIETY
    elif (deterministic_intent == "harder_task" and transition is not None
            and transition.boundary_reason == "at_maximum"):
        reason = REASON_MAX_LEVEL_HARDER
    if not reason:
        return None

    supported = _contract_archetypes(context)
    if not supported:
        return None
    # Historija se čita FILTRIRANO kroz enum lekcije — zatečena zagađena
    # vrijednost ne smije ni ući u izbor cilja.
    recent = recent_archetypes(session, getattr(context, "topic_id", ""),
                               supported=supported)
    level = int(getattr(transition, "target_level", 0) or
                session.get("difficulty_level", 1))
    definitions = dict(getattr(
        getattr(context, "semantic_contract", None), "archetype_definitions", {})
        or {})
    return CreativeEscalationDecision(
        reason=reason,
        target_archetype=select_target(supported, recent),
        supported_archetypes=supported,
        recent_archetypes=recent,
        level=min(max(level, 1), 3),
        definitions=tuple((name, definitions[name]) for name in supported
                          if name in definitions),
    )


# ---------------------------------------------------------------------------
# JEDAN RENDERER ZA OBA PROMPTA — Tutor i Recenzent vide ISTE činjenice.
# ---------------------------------------------------------------------------

_REASON_TEXT = {
    REASON_MAX_LEVEL_HARDER:
        "učenik traži teže, a već je na najvišem nivou ove lekcije",
    REASON_EXPLICIT_VARIETY:
        "učenik izričito traži drugačiji tip zadatka",
}


def _required_facts(archetype) -> tuple:
    from matbot.mathkernel import wordfacts
    return tuple(wordfacts.REQUIRED_FACTS.get(archetype, ()))


def prompt_block(decision) -> str:
    """Blok koji ide u OBA prompta; prazan string kad eskalacije nema."""
    if decision is None:
        return ""
    lines = [
        "ZAHTJEV ZA RAZNOLIKOŠĆU (serverska odluka, nije prijedlog):",
        f"- razlog: {_REASON_TEXT.get(decision.reason, decision.reason)}",
        f"- nivo težine OSTAJE {decision.level} — ne pravi teži nivo od "
        "najvišeg koji lekcija ima",
        f"- CILJNI tip zadatka (obavezan): {decision.target_archetype}",
        f"- tipovi koje ova lekcija uopšte dozvoljava: "
        f"{', '.join(decision.supported_archetypes)}",
    ]
    for name, meaning in decision.definitions:
        marker = "  ← CILJ" if name == decision.target_archetype else ""
        lines.append(f"    • {name}: {meaning}{marker}")
    if decision.recent_archetypes:
        lines.append("- nedavno već viđeni tipovi (izbjegni ih): "
                     + ", ".join(decision.recent_archetypes))
    required = _required_facts(decision.target_archetype)
    if required:
        lines.append(
            "- OBAVEZNO u `task_signature.normalized_parameters` upiši "
            "egzaktne veličine zadatka: "
            + ", ".join(required)
            + " (i `type` = ciljni tip). Server iz njih SAM preračunava "
              "odgovor i odbija paket ako se ne slaže s označenom opcijom.")
    lines.extend([
        "- zadatak mora biti SUŠTINSKI drugačije matematičke strukture od "
        "nedavnih, a ne isti zadatak s drugim imenom, predmetom ili brojevima",
        "- ostani STROGO unutar izabrane lekcije i njenog ugovora; ne uvodi "
        "gradivo koje lekcija nema samo da bi zadatak bio teži",
    ])
    if not decision.diversity_possible:
        # ISKRENOST PREMA ARHITEKTURI: kad lekcija ima jedan arhetip, tražiti
        # „drugu strukturu“ znači tražiti izlazak iz lekcije. Tada se traži
        # bogatiji kontekst, a raznolikost se NE lažira.
        lines.append(
            "- NAPOMENA: ova lekcija ima samo jedan dozvoljen tip zadatka, pa "
            "napravi bogatiji, kontekstualno drugačiji primjer TOG tipa — "
            "nikad drugi tip.")
    return "\n".join(lines)


def reviewer_requires_variety(decision) -> bool:
    """Da li recenzentova presuda o raznolikosti smije oboriti paket.

    Samo kad eskalacija stvarno traži DRUGI arhetip — kod lekcije s jednim
    arhetipom to bi bio nemoguć zahtjev."""
    return decision is not None and decision.diversity_possible


def reviewer_requires_target_match(decision) -> bool:
    """Presuda „odgovara li STRUKTURA zadatka izabranom arhetipu“ traži se na
    SVAKOJ eskalaciji — i kad lekcija ima samo jedan arhetip, jer tačna
    oznaka ne dokazuje tačnu matematičku strukturu (živi nalaz: model može
    napisati ispravan enum, a zadatak graditi po drugom obrascu)."""
    return decision is not None
