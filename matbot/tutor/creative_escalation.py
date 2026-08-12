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
import re
from dataclasses import dataclass

from matbot.difficulty_level import MAX_LEVEL

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
# Koliko NEOBJAVLJENIH pokušaja pamtimo za rotaciju cilja. Namjerno kratko:
# dovoljno da četiri uzastopna pada ne ciljaju isto, premalo da trajno
# zabrani arhetip koji je pao iz prolaznog razloga.
RECENT_TARGET_ATTEMPTS = 3

# Razlozi eskalacije — zatvoren skup, jedan vlasnik rutiranja.
REASON_MAX_LEVEL_HARDER = "max_level_harder"
REASON_EXPLICIT_VARIETY = "explicit_variety"
# PROIZVODNA ODLUKA (ručni test, 6. razred, tekstualni zadaci s razlomcima):
# na maksimumu „Daj mi novi zadatak“ je do sada ostajao na determinističkoj
# ruti. Nivo je pri tome bio ISPRAVAN (ostajao je 3) — mijenja se samo RUTA:
# kad je ljestvica iscrpljena, „još jedan, ali drugačiji“ znači isto što i
# „teže“ — jedini prostor koji je preostao je DRUGA STRUKTURA na istom nivou.
REASON_MAX_LEVEL_NEW = "max_level_new"


@dataclass(frozen=True)
class CreativeEscalationDecision:
    """Serverska odluka: eskaliraj, i to na TAJ arhetip."""

    reason: str
    target_archetype: str
    supported_archetypes: tuple
    # ŠTA JE UČENIK VIDIO — jedini ulaz za recenzentovu presudu o raznolikosti.
    recent_archetypes: tuple
    level: int
    # ŠTA JE GENERISANJE POKUŠALO A NIJE OBJAVILO — samo za izbor cilja.
    # Nikad se ne prosljeđuje recenzentu kao „viđeni zadaci“ (§16).
    attempted_archetypes: tuple = ()
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


def recent_target_attempts(session, lesson_id, limit=RECENT_TARGET_ATTEMPTS,
                           supported=()) -> tuple:
    """Nedavno POKUŠANI kreativni ciljevi koji NISU objavljeni.

    Odvojena projekcija od `recent_task_signatures` (§16): ovo nije ono što je
    učenik vidio, pa se nikad ne smije koristiti kao opis viđenih zadataka —
    isključivo planer bira po njemu. Isti filtar pri čitanju kao i kod objava:
    vrijednost van enuma lekcije se preskače."""
    allowed = frozenset(supported or ())
    found = []
    for record in (session.get("recent_creative_targets") or []):
        if not isinstance(record, dict):
            continue
        if record.get("lesson_id") != lesson_id:
            continue
        archetype = record.get("archetype") or ""
        if not archetype:
            continue
        if allowed and archetype not in allowed:
            continue
        found.append(archetype)
    return tuple(found[-limit:]) if limit else tuple(found)


# ---------------------------------------------------------------------------
# TAKSONOMIJA JE SERVERSKA — MODELOVA OZNAKA NIJE AUTORITET
# ---------------------------------------------------------------------------
# ŽIVI NALAZ #1 (ciljana kampanja): Tutor je u `operation_or_relation` upisao
# slobodan tekst, recenzent ga odobrio, paket objavljen, historija zagađena.
# Odgovor je tada bio: traži da model VRATI ciljni identifikator.
#
# ŽIVI NALAZ #2 (finalna kampanja): tako postavljena kapija dala je 0/4
# dostupnosti. Server je tražio `multi_fraction_remainder`, a model je isti
# pojam opisao prirodnim jezikom („take_multiple_fractions_then_remainder“).
# Sve četiri su ISPRAVNO odbijene po tadašnjem pravilu — ali pravilo je mjerilo
# PREPISIVANJE NISKE, a ne matematiku.
#
# ZAKLJUČAK: prepisivanje internog identifikatora nije sigurnosno svojstvo.
# Server je cilj već IZABRAO; nema šta da uči od modelove oznake. Kanonski
# arhetip je zato `decision.target_archetype`, a modelova oznaka postaje
# NEAUTORITATIVAN metapodatak koji nikad ne ulazi u objavu ni u historiju.
#
# Ono što JESTE sigurnosno svojstvo i ostaje netaknuto:
#   1. strukturisane činjenice moraju mehanički odgovarati CILJU (facts_failure);
#   2. egzaktan rješavač mora reprodukovati označeni odgovor (facts_failure);
#   3. recenzent mora presuditi da SEMANTIKA zadatka odgovara cilju
#      (matches_target_archetype) — jedina presuda koja vidi prozu.
# Tek kad sve troje prođe, server sam upisuje kanonski identifikator.


def canonical_task(task, decision):
    """Kopija zadatka čiji je `operation_or_relation` SERVERSKI cilj.

    Poziva se ISKLJUČIVO nakon što strukturisane činjenice prođu
    `facts_failure` — oznaka se ne poklanja, ona se ZARAĐUJE dokazom da je
    nacrt mehanički saglasan s ciljem. Vraća se NOVI objekat; nacrt se ne
    mutira, da nijedan drugi sloj ne vidi tiho izmijenjeno stanje."""
    if decision is None or task is None:
        return task
    signature = task.task_signature
    if signature.operation_or_relation == decision.target_archetype:
        return task
    return task.model_copy(update={
        "task_signature": signature.model_copy(update={
            "operation_or_relation": decision.target_archetype})})


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
FACTS_MISSING = "creative_facts_missing_for_target"
FACTS_NOT_CANONICAL = "creative_facts_not_canonical"

# ŽIVI NALAZ (finalna kampanja, turnovi 6 i 7): Tutor je mašinske činjenice
# upisao u PREZENTACIJSKOM obliku — `total='3\cdot48'`, `fraction='\tfrac{2}{3}'`.
# Rješavač ih je ispravno odbio, ali kao „nije egzaktan broj“, pa se greška
# NOTACIJE nije razlikovala od greške MATEMATIKE.
#
# GRANICA KOJU OVO POVLAČI: učeniku vidljiva matematika smije biti LaTeX;
# mašinske činjenice NE SMIJU. One su podatak, ne prikaz. Zato zatvoren oblik:
# cio broj (`48`) ili razlomak `p/q` — tačno ono što deterministički generator
# ionako upisuje, pa kreativni paket ima ISTI oblik potpisa kao serverski.
#
# NAMJERNO SE NIŠTA NE RAČUNA I NE PREVODI: izraz `3\cdot48` se ne evaluira,
# `\tfrac{2}{3}` se ne prevodi. Server prima atomske činjenice ili odbija —
# parser modelovih izraza se ovdje ne uvodi ni u kom obliku.
_INTEGER_FACT_RE = re.compile(r"^-?\d+$")
_RATIONAL_FACT_RE = re.compile(r"^-?\d+(?:/\d+)?$")
# Veličine koje moraju biti CIO broj; sve ostale tražene su racionalne.
_INTEGER_FACTS = frozenset({"total"})


def _fact_is_canonical(name, value) -> bool:
    text = (value or "").strip()
    if not text or text != (value or ""):
        return False                      # razmaci nisu kanonski oblik
    pattern = (_INTEGER_FACT_RE if name in _INTEGER_FACTS
               else _RATIONAL_FACT_RE)
    if not pattern.match(text):
        return False
    if "/" in text and text.split("/", 1)[1].lstrip("0") == "":
        return False                      # nazivnik 0
    return True


def _option_value(text):
    from fractions import Fraction
    body = (text or "").strip()
    if body.startswith("$") and body.endswith("$"):
        body = body[1:-1]
    try:
        return Fraction(body.strip())
    except (ValueError, ZeroDivisionError, ArithmeticError):
        return None


def facts_failure(decision, task) -> str:
    """Kod greške ili "" — jedina DETERMINISTIČKA kapija kreativnog nacrta.

    Mjeri se STRUKTURA, nikad oznaka: rješavač se bira po SERVERSKOM cilju, a
    nacrt mora ponuditi sve veličine koje taj cilj traži, one moraju biti
    rješive, i označena opcija mora biti baš taj broj — i to jedina takva.

    OBAVEZNA JE, ne uslovna: pošto modelova oznaka više nije autoritet, ovo je
    jedini sloj koji prije recenzenta može dokazati da nacrt uopšte JESTE
    traženi arhetip. Nacrt bez traženih činjenica zato pada zatvoreno na
    jednom pozivu — ranije je tu ćutao, jer je oznaka nosila tu tvrdnju."""
    from matbot.mathkernel import wordfacts

    if decision is None or task is None:
        return ""
    if decision.target_archetype not in wordfacts.UNKNOWN_BY_TYPE:
        return ""
    parameters = {p.name: p.value
                  for p in task.task_signature.normalized_parameters}
    required = set(wordfacts.REQUIRED_FACTS.get(decision.target_archetype, ()))
    if not required <= set(parameters):
        return FACTS_MISSING
    # NOTACIJA PRIJE MATEMATIKE: prezentacijski zapis se odbija SVOJIM kodom, da
    # se u dijagnostici ne miješa s pogrešnim računom. Provjeravaju se samo
    # veličine koje ciljni arhetip traži — ostali potpisi se ne diraju.
    for name in sorted(required):
        if not _fact_is_canonical(name, parameters.get(name)):
            return FACTS_NOT_CANONICAL
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


def select_target(supported, recent, attempted=()) -> str:
    """Arhetip koji NIJE nedavno viđen ni nedavno POKUŠAN; inače najdavniji.

    Dva ulaza s različitim značenjem (§16): `recent` su zadaci koje je učenik
    STVARNO VIDIO, `attempted` su ciljevi koje je generisanje nedavno pokušalo
    i koji NISU objavljeni. Bez drugog ulaza odbijen cilj se bira iznova u
    nedogled — živi nalaz: četiri uzastopna pokušaja na isti arhetip.

    Pokušaj NIJE trajna zabrana: kad ponestane svježih kandidata, pravilo
    najdavnije viđenog vrati i njega.

    TRI NIVOA, bez ijedne odluke po abecedi (živi nalaz: planer je iz
    „nepokušanih“ uzeo PRVI po enumu i tako ponovo ciljao arhetip koji je
    učenik upravo vidio, pa je recenzent s pravom oborio raznolikost):
      1. nikad nedavno objavljen NI pokušan;
      2. inače nepokušani, pa među njima NAJDAVNIJE OBJAVLJEN;
      3. inače svi, pa najdavnije korišten po objema historijama."""
    supported = tuple(supported)
    if not supported:
        return ""
    recent, attempted = tuple(recent), tuple(attempted)

    def _least_recent(names, history):
        """Ime čija je posljednja pojava u `history` NAJSTARIJA (nikad enum-red)."""
        def last_seen(name):
            for offset, seen in enumerate(reversed(history)):
                if seen == name:
                    return offset
            return len(history)
        return max(names, key=last_seen)

    # TIER 1 — potpuno svjež kandidat.
    fresh = [name for name in supported
             if name not in recent and name not in attempted]
    if fresh:
        return fresh[0]
    # TIER 2 — izbjegni ono što je upravo pokušano i palo, pa među preostalima
    # uzmi onaj koji je učenik NAJDAVNIJE vidio.
    unattempted = [name for name in supported if name not in attempted]
    if unattempted:
        return _least_recent(unattempted, recent)
    # TIER 3 — sve je i viđeno i pokušano: najdavnije korišteno ukupno.
    return _least_recent(supported, recent + attempted)


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
    elif (deterministic_intent == "next_task" and transition is not None
            and int(getattr(transition, "target_level", 0)) >= MAX_LEVEL):
        # NOVI ZADATAK NA MAKSIMUMU. Granica se ovdje NE čita iz
        # `boundary_reason` — ono se puni samo kad je smjer TRAŽEN a nivo se
        # nije pomjerio; „novi“ je zahtjev bez smjera, pa je jedini pošten
        # test sam ciljni nivo. Nivo ostaje isti (`same` prelaz), mijenja se
        # samo ruta. `generate_task` se namjerno NE hvata: on znači da aktivnog
        # zadatka nema, a tada nema ni od čega praviti „nešto drugo“.
        reason = REASON_MAX_LEVEL_NEW
    if not reason:
        return None

    supported = _contract_archetypes(context)
    if not supported:
        return None
    # Historija se čita FILTRIRANO kroz enum lekcije — zatečena zagađena
    # vrijednost ne smije ni ući u izbor cilja.
    lesson_id = getattr(context, "topic_id", "")
    recent = recent_archetypes(session, lesson_id, supported=supported)
    attempted = recent_target_attempts(session, lesson_id, supported=supported)
    level = int(getattr(transition, "target_level", 0) or
                session.get("difficulty_level", 1))
    definitions = dict(getattr(
        getattr(context, "semantic_contract", None), "archetype_definitions", {})
        or {})
    return CreativeEscalationDecision(
        reason=reason,
        target_archetype=select_target(supported, recent, attempted),
        supported_archetypes=supported,
        recent_archetypes=recent,
        attempted_archetypes=attempted,
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
    REASON_MAX_LEVEL_NEW:
        "učenik traži nov zadatak, a već je na najvišem nivou ove lekcije",
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
        integers = [name for name in required if name in _INTEGER_FACTS]
        rationals = [name for name in required if name not in _INTEGER_FACTS]
        lines.append(
            "- OBAVEZNO u `task_signature.normalized_parameters` upiši "
            "egzaktne veličine zadatka: "
            + ", ".join(required)
            + " (i `type` = ciljni tip). Server iz njih SAM preračunava "
              "odgovor i odbija paket ako se ne slaže s označenom opcijom.")
        # ŽIVI NALAZ (turnovi 6 i 7): upisano je `3\cdot48` i `\tfrac{2}{3}`.
        # Ovo je jedino mjesto gdje se oblik MAŠINSKIH činjenica propisuje —
        # namjerno ovdje, a ne kao opšta lekcija o formatiranju u svim promptima.
        lines.append(
            "- TE VELIČINE SU MAŠINSKI PODACI, NE PRIKAZ ZA UČENIKA: bez LaTeX-a, "
            "bez `$`, bez jedinica i bez računskih izraza. Cjelinu izračunaj "
            "PRIJE upisa.")
        if integers:
            lines.append(
                f"    • {', '.join(integers)}: cio broj, npr. `144` "
                "(NE `3\\cdot48`, NE `144 olovaka`)")
        if rationals:
            lines.append(
                f"    • {', '.join(rationals)}: razlomak oblika `p/q`, npr. "
                "`2/3` (NE `\\tfrac{2}{3}`, NE `\\frac{2}{3}`, NE `$2/3$`)")
        lines.append(
            "    • u TEKSTU zadatka i u rješenju LaTeX ostaje normalan i "
            "poželjan — ograničenje vrijedi SAMO za ove činjenice")
    lines.extend([
        "- zadatak mora biti SUŠTINSKI drugačije matematičke strukture od "
        "nedavnih, a ne isti zadatak s drugim imenom, predmetom ili brojevima",
        "- ostani STROGO unutar izabrane lekcije i njenog ugovora; ne uvodi "
        "gradivo koje lekcija nema samo da bi zadatak bio teži",
    ])
    # TEŽINA NA MAKSIMUMU (živi nalaz, turn 4): potpuno ispravan paket je pao s
    # `difficulty_not_changed`. Recenzent je slijedio globalno pravilo „teže =
    # jedan korak naviše od upisanog nivoa“, koje na maksimumu NIJE ispunjivo —
    # nivo po dizajnu ostaje najviši i četvrtog nivoa nema. Ovaj izuzetak vrijedi
    # SAMO za taj tačan slučaj; kod izričitog traženja drugog tipa na nivou 1/2
    # se NE dodaje, pa se njime ne može opravdati gradivo iznad tekućeg nivoa.
    if (decision.reason in (REASON_MAX_LEVEL_HARDER, REASON_MAX_LEVEL_NEW)
            and decision.level >= MAX_LEVEL):
        lines.extend([
            f"- TEŽINA NA MAKSIMUMU: nivo OSTAJE {decision.level} i to je "
            "ISPRAVNO, a ne greška. Ne postoji nivo iznad njega.",
            "  • pravilo „teži zahtjev pomjera nivo jedan korak naviše“ ovdje "
            "SE NE PRIMJENJUJE;",
            "  • `difficulty_not_changed` NIJE valjan razlog odbijanja u ovom "
            "turnu, niti je nepromijenjen broj nivoa sam po sebi nedostatak;",
            "  • veći izazov dolazi iz DRUGE STRUKTURE zadatka na istom nivou, "
            "pa dimenzije težine ne moraju sve rasti;",
            f"  • i dalje se provjerava: zadatak mora odgovarati nivou "
            f"{decision.level} ove lekcije, bez gradiva izvan lekcije i bez "
            "izmišljenog nivoa iznad najvišeg.",
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
