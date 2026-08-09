"""Deterministička provjera KOHERENTNOSTI scenarija — prije ijednog poziva.

ZAŠTO POSTOJI (korijenski uzrok RC11, talas discovery-100 na 8a8f04d):
22 od 100 scenarija imala su ZAMRZNUTU poruku učenika koja je nespojiva s
IZABRANOM LEKCIJOM. Bot je slijedio lekciju — što je ISPRAVNO produktno
ponašanje (lekcija je vlasnik onoga što se vježba) — pa je očekivanje
scenarija bilo nevaljano, a ne proizvod. Dokazani primjeri:

    A007  nejednačina `x>-2`      na lekciji „Jednačine sa sabiranjem…“
    A013  interval `-1/2<=x<3/4`  na lekciji „Jednačine sa zagradama…“
    B012  lanac `-4<=3x-1<8`      na lekciji „Jednačina sa razlomcima“
    E008  nejednačina             na lekciji „Podudarnost trouglova - SSU“
    F005  koordinatni zadatak     na lekciji „Mreža trostrane prizme“
    C009  koordinatni zadatak     na lekciji „Tabele, stupčasti i kružni dijagrami“

Petina talasa je time potrošena na nevaljana očekivanja.

ŠTA OVAJ MODUL JESTE: offline provjera koja DOKAZUJE nespojivost prije nego
što se potroši ijedan živi poziv. Koristi ISKLJUČIVO postojeće zatvorene
gramatike i podatke proizvoda — kanonski naslov lekcije iz `data/topics.json`,
čitač „riješi“ izjave iz `matbot.mcq_integrity` (Task 3) i semantičke ugovore
vježbe iz `matbot.semantic_practice` (Task 2). Nema nove matematike i nema
modela.

ŠTA OVAJ MODUL NIJE: prosuditelj namjere. Kad se nespojivost NE MOŽE dokazati,
vraća se prazno — „nedokazano“ nikad ne postaje „nevaljan scenario“. Zato
scenario mora sam deklarisati šta očekuje (`request_alignment`), a modul
provjerava samo onu deklaraciju koja se može opovrgnuti.

DVIJE VRSTE SCENARIJA (i to je suština RC11 ispravke):

  request_alignment = "must_follow"      (podrazumijevano)
      Zahtjev učenika je predmet testa: bot ga MORA ispuniti. Takav scenario
      je NEVALJAN ako je zahtjev dokazano nespojiv s izabranom lekcijom.

  request_alignment = "lesson_overrides"
      Namjerna adversarijalna sonda: zahtjev je VAN lekcije i bot ga NE smije
      poslušati. Nespojivost je tada SVRHA scenarija, ne greška — i takav
      scenario se ovdje nikad ne prijavljuje. (Živi E006: lekcija
      „Podudarnost trouglova - SUS“, zahtjev van lekcije, bot ostao na
      lekciji — evaluator je to pogrešno brojao kao produktni kvar.)
"""
from __future__ import annotations

import re

from matbot import mcq_integrity, semantic_practice, topics

# Deklaracije poravnanja zahtjeva i lekcije.
ALIGNMENT_MUST_FOLLOW = "must_follow"
ALIGNMENT_LESSON_OVERRIDES = "lesson_overrides"
ALIGNMENTS = (ALIGNMENT_MUST_FOLLOW, ALIGNMENT_LESSON_OVERRIDES)

# Interni kodovi nespojivosti (samo izvještaj/logovi).
RELATION_KIND_CONFLICT = "relation_kind_conflict"
LESSON_CONTRACT_CONFLICT = "lesson_contract_conflict"
NON_SOLVE_LESSON_CONFLICT = "non_solve_lesson_conflict"
UNSUPPORTED_REQUEST_FAMILY = "unsupported_request_family"

LESSON_EQUATION = "equation"
LESSON_INEQUALITY = "inequality"
LESSON_UNKNOWN = ""

# Kanonski naslov je JEDINI izvor vrste lekcije. „NEjednačina“ sadrži
# „jednačina“ kao podniz, pa se negirani oblik MORA čitati prvi — ista zamka
# koju Task 3 rješava na strani poruke.
_TITLE_INEQUALITY_RE = re.compile(r"nejedna[čc]", re.IGNORECASE)
_TITLE_EQUATION_RE = re.compile(r"jedna[čc]", re.IGNORECASE)

# ZATVOREN spisak naslova koji dokazano NE predaju rješavanje (ne)jednačina.
# Namjerno sitan i vezan za DOKAZANE žive nalaze — svaki drugi naslov ostaje
# „ne mogu dokazati“, jer bi širi spisak počeo pogađati kurikulum.
#   • „Skupovi N, Z, Q, I i R…“ (A015) — lekcija o SKUPOVIMA brojeva;
#   • „Realni brojevi i brojevna osa“ (A016), „Uređenost i poređenje realnih
#     brojeva“ (A017) — prikaz i poređenje, ne rješavanje;
#   • „Tabele, stupčasti i kružni dijagrami“ (C009) — čitanje podataka.
_NON_SOLVE_TITLE_MARKERS = (
    "skupovi", "brojevna osa", "uređenost i poređenje", "uredenost i poredenje",
    "dijagram", "tabele",
)

# Relacijski token bez zahtjeva za rješivošću — `2(x-1)=2x+1` je DOKAZANO
# jednačina po obliku iako nema jedinstveno rješenje (živi A024).
_RELATION_TOKEN_RE = re.compile(r"<=|>=|<|>|=")

# ---------------------------------------------------------------------------
# POLITIKA BROJEVNIH DOMENA — PRIKOVANA UZ PROIZVOD
# ---------------------------------------------------------------------------
# Konvencija ovog repozitorija je jednoznačna i evaluator je NE smije tumačiti
# drugačije od servera (živi A020: traženo N, objavljeno Z, i označeni $\{0\}$
# je tačan nad Z a nad N je skup PRAZAN):
#
#     N  = {1, 2, 3, ...}      — NULA NIJE PRIRODAN BROJ
#     N0 = {0, 1, 2, 3, ...}
#     Z, Q, R  — cijeli, racionalni, realni
#
# Vrijednosti ispod su DONJE GRANICE diskretnih domena i moraju se poklapati s
# `matbot.mcq_integrity._SOLVE_DISCRETE_DOMAIN_MIN` (test to i dokazuje). Time
# nijedno očekivanje scenarija ne može koristiti dvosmislenu konvenciju
# „prirodnih brojeva s nulom“.
DOMAIN_POLICY = {"N": 1, "N0": 0, "Z": None}
SUPPORTED_DOMAINS = ("N", "N0", "Z", "Q", "R")


def domain_policy_matches_product() -> bool:
    """True kad evaluatorova konvencija N/N0/Z odgovara serverskoj."""
    return dict(mcq_integrity._SOLVE_DISCRETE_DOMAIN_MIN) == DOMAIN_POLICY


def domain_policy_problems(scenarios) -> list:
    """Scenario koji imenuje domen mora koristiti podržanu, jednoznačnu oznaku."""
    problems = []
    if not domain_policy_matches_product():
        problems.append(
            "evaluator domain policy drifted from the product: "
            f"{DOMAIN_POLICY} vs {dict(mcq_integrity._SOLVE_DISCRETE_DOMAIN_MIN)}")
    for scenario in scenarios:
        spec = getattr(scenario, "discovery_spec", None) or {}
        domain = spec.get("domain") if isinstance(spec, dict) else None
        if domain is not None and domain not in SUPPORTED_DOMAINS:
            problems.append(
                f"{scenario.id}: domain {domain!r} is not one of {SUPPORTED_DOMAINS}")
    return problems


def lesson_title(grade, topic_id) -> str:
    """Kanonski naslov iz data/topics.json, ili prazno kad lekcija ne postoji."""
    try:
        info = topics.lesson_info(int(grade), str(topic_id))
    except Exception:
        return ""
    return (info or {}).get("title") or ""


def lesson_relation_kind(title: str) -> str:
    """Vrsta koju naslov lekcije DOKAZANO imenuje, ili prazno."""
    if _TITLE_INEQUALITY_RE.search(title or ""):
        return LESSON_INEQUALITY
    if _TITLE_EQUATION_RE.search(title or ""):
        return LESSON_EQUATION
    return LESSON_UNKNOWN


def requested_relation_kind(message: str) -> str:
    """Vrsta koju PORUKA dokazano traži, ili prazno.

    Prvo se koristi čitač izjave iz Task 3 (izričita riječ ili riješena
    relacija). Kad relacija postoji ali NIJE rješiva (identitet, kontradikcija
    — živi A024 `2(x-1)=2x+1`), vrsta se i dalje dokazuje iz samih operatora."""
    statement = mcq_integrity.read_solve_statement(message or "")
    kind = statement.stated_kind or statement.relation_kind
    if kind:
        return kind
    for candidate in mcq_integrity._request_relation_candidates(message or ""):
        normalized = mcq_integrity._normalize_solve_segment(candidate)
        if normalized is None:
            continue
        tokens = _RELATION_TOKEN_RE.findall(normalized)
        if not tokens:
            continue
        return (mcq_integrity.RELATION_EQUATION if set(tokens) == {"="}
                else mcq_integrity.RELATION_INEQUALITY)
    return ""


def _message_of(step) -> str:
    return str((step or {}).get("message") or "")


def scenario_messages(scenario) -> list:
    return [_message_of(step) for step in scenario.steps if _message_of(step).strip()]


def alignment_of(scenario) -> str:
    return getattr(scenario, "request_alignment", ALIGNMENT_MUST_FOLLOW)


def coherence_problems(scenario) -> list:
    """Vrati DOKAZANE nespojivosti poruke i lekcije; prazno = nije dokazano.

    Prazna lista NIJE dokaz da je scenario dobar — samo da nespojivost nije
    dokaziva (isti princip kao svi validatori u `matbot/`)."""
    if alignment_of(scenario) == ALIGNMENT_LESSON_OVERRIDES:
        # Nespojivost je SVRHA takvog scenarija (E006 klasa) — nikad nalaz.
        return []
    problems = []
    title = lesson_title(scenario.grade, scenario.topic_id)
    lesson_kind = lesson_relation_kind(title)
    lowered = (title or "").lower()
    contract = semantic_practice.contract_for(scenario.topic_id)

    for index, step in enumerate(scenario.steps):
        message = _message_of(step)
        if not message.strip():
            continue
        where = f"{scenario.id} step{index}"

        # 1) JEDNAČINA vs NEJEDNAČINA — najveći dokazani klaster
        #    (A007, A013, A022, A024, B010, B011, B012).
        request_kind = requested_relation_kind(message)
        if request_kind and lesson_kind and request_kind != lesson_kind:
            problems.append(
                f"{where}: {RELATION_KIND_CONFLICT} — the message asks for an "
                f"{request_kind} but the lesson teaches {lesson_kind}s "
                f"({title!r})")

        # 2) SEMANTIČKI UGOVOR VJEŽBE (Task 2) — kad lekcija ima blokirajući
        #    ugovor, zahtjev koji ga ne može zadovoljiti je nevaljan scenario
        #    (E008, F005, F007, F008). Mjeri se ISTIM detektorom kojim server
        #    mjeri objavljen paket.
        # `SemanticPracticeContract` nosi `enforcement`, ne `blocking` — ugovor
        # porodice (Faza 4A) je DRUGI tip i ima `blocking`. Zamjena to dvoje
        # znači detektor koji nikad ne okine.
        if contract is not None and getattr(contract, "enforcement", "") == "blocking":
            failures = semantic_practice.fidelity_failures(contract, message)
            if failures:
                problems.append(
                    f"{where}: {LESSON_CONTRACT_CONFLICT} — following this "
                    f"request cannot satisfy the lesson contract "
                    f"{contract.requirement_type} ({','.join(failures)})")

        # 3) LEKCIJA KOJA NE PREDAJE RJEŠAVANJE (A015, A016, A017, C009).
        if request_kind and any(marker in lowered
                                for marker in _NON_SOLVE_TITLE_MARKERS):
            problems.append(
                f"{where}: {NON_SOLVE_LESSON_CONFLICT} — the message asks to "
                f"solve a relation but the lesson is not a solving lesson "
                f"({title!r})")

        # 4) ZAHTJEV VAN PODRŽANE PORODICE: scenario koji očekuje
        #    determinističku presudu skupa rješenja ne smije tražiti
        #    nelinearan izraz (A016/A017: `x^2=2`), jer ga nijedan orakl ne
        #    može presuditi. Provjerava se SAMO kad scenario to izričito
        #    očekuje (oznaka `solve_set_adjudicated`).
        if "solve_set_adjudicated" in (scenario.tags or ()):
            statement = mcq_integrity.read_solve_statement(message)
            if not statement.has_relation:
                problems.append(
                    f"{where}: {UNSUPPORTED_REQUEST_FAMILY} — the scenario "
                    "expects deterministic solve-set adjudication but the "
                    "requested relation is outside the supported linear family")
    return problems


def validate_wave(scenarios) -> list:
    """Sve dokazane nekoherentnosti u talasu — koristi se u `--dry-run`."""
    problems = []
    for scenario in scenarios:
        problems.extend(coherence_problems(scenario))
    return problems
