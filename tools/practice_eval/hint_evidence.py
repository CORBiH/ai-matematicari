"""ZAKUCANI ŽIVI DOKAZI O LJESTVICI NAGOVJEŠTAJA (arhitektonska Faza 0).

ZAŠTO POSTOJI. Dvije kampanje su objavile nagovještaj koji je pao samo na
RUČNI pregled, dok su sve automatske provjere bile zelene:

  FW-X03  (final40_c17538a_20260810-141121, lekcija 9-02-006)
          Nagovještaj 1 je definiciju okomitosti prave i ravni izrekao
          DOSLOVNO onako kako glasi označena opcija `a`, pa je prvi
          nagovještaj otkrio odgovor. `hint_no_leak` je vratio PASS.
          Nagovještaj 3 je do tačnog zaključka došao preko NETAČNE
          međutvrdnje (u ravni okomitost na jednu pravu kroz presjek ne
          povlači okomitost na svaku takvu pravu).

  TR-B1   (targeted_finalfix_b5692c0_20260810-012457, ista porodica lekcija)
          Isti oblik curenja DVIJE kampanje ranije: nagovještaj 1 je uputio
          na provjeru postojanja zajedničke tačke, a označena opcija upravo
          tim uslovom definiše paralelnost. Kampanjski audit je taj talas
          zapisao kao „answer leaks in Hints 1/2: 0“.

`scratchpad/practice_eval/` je u `.gitignore`, pa se artefakti NE MOGU čitati
iz testa na svježem checkoutu. Zato su tekstovi ovdje ZAKUCANI, uz punu
provenijenciju, a `local_artifact_mismatches()` ih — kad artefakt lokalno
postoji — poredi bajt za bajt s izvorom.

ŠTA OVDJE JESTE: tekst koji je UČENIK STVARNO VIDIO (`response.answer`),
tekst zadatka i vidljive opcije. Sve je već doslovno citirano u kampanjskim
`failures.md` izvještajima.

ŠTA OVDJE NIJE i nikad ne smije biti (CLAUDE.md, pravilo 7): nijedan prompt,
nijedan sirov strukturisani izlaz modela, nijedno interno polje paketa
(`difficulty_evidence`, `task_signature`, `checks.*`, `solution`).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACT_DIR = ROOT / "scratchpad" / "practice_eval"


@dataclass(frozen=True)
class HintEvidence:
    """Jedan zakucan zapis: zadatak, opcije, označena opcija, ljestvica."""

    scenario_id: str
    campaign: str
    candidate_commit: str
    topic_id: str
    grade: int
    task_text: str
    options: tuple            # ((option_id, text), ...) — redoslijed kao kod učenika
    marked_option_id: str
    hints: tuple              # ((hint_level_served, text), ...)

    @property
    def marked_option_text(self) -> str:
        return next(text for oid, text in self.options if oid == self.marked_option_id)

    @property
    def distractor_texts(self) -> tuple:
        return tuple(text for oid, text in self.options if oid != self.marked_option_id)

    def hint(self, level: int) -> str:
        return next(text for served, text in self.hints if served == level)


# ---------------------------------------------------------------------------
# FW-X03 — objavljeni blokator izdanja (kandidat c17538a)
# ---------------------------------------------------------------------------

FW_X03 = HintEvidence(
    scenario_id="FW-X03",
    campaign="final40_c17538a_20260810-141121",
    candidate_commit="c17538a8bd927586e01ec01c108f0cc787a34bbb",
    topic_id="9-02-006",
    grade=9,
    task_text=(
        "Neka prava $p$ siječe ravan $\\alpha$ u tački $A$. "
        "Koja od sljedećih tvrdnji je tačna?"
    ),
    options=(
        ("a", "Ako je prava $p$ okomita na ravan $\\alpha$ u tački $A$, tada je "
              "$p$ okomita na svaku pravu iz ravni $\\alpha$ koja prolazi kroz $A$."),
        ("b", "Ako je prava $p$ okomita na neku pravu $q$ koja leži u $\\alpha$, "
              "onda je $p$ nužno okomita na ravan $\\alpha$."),
        ("c", "Ako prava $p$ leži u ravni $\\alpha$ i prolazi kroz $A$, tada je "
              "$p$ okomita na $\\alpha$."),
        ("d", "Ako je prava $p$ okomita na ravan $\\alpha$ u tački $A$, tada je "
              "$p$ paralelna sa svakoj pravom iz ravni $\\alpha$ koja prolazi kroz $A$."),
    ),
    marked_option_id="a",
    hints=(
        (1, "Dobro, idemo korak po korak. Pogledaj pažljivo definiciju okomitosti "
            "prave i ravni.\n\nPrvi korak: sjeti se definicije — prava $p$ je "
            "okomita na ravan $\\alpha$ u tački $A$ ako i samo ako je $p$ okomita "
            "na svaku pravu iz ravni $\\alpha$ koja prolazi kroz $A$. Upotrijebi tu "
            "definiciju da provjeriš svaku ponuđenu tvrdnju."),
        (2, "Dobro — idemo dalje. Evo konkretan međukorak koji treba uraditi:\n\n"
            "Uzmemo proizvoljnu pravu $q$ iz ravni $\\alpha$ koja prolazi kroz $A$ "
            "i razmotrimo ravninu $\\beta$ koju određuju prava $p$ i ta prava $q$. "
            "U toj ravnini provjerimo ugao između $p$ i $q$: napiši da li je ugao "
            "između njih $90^\\circ$ ili nije, koristeći pretpostavku da je $p$ "
            "okomita na ravan $\\alpha$ (ne dajem konačni zaključak, samo izvedi "
            "ovaj korak provjere za proizvoljno izabranu $q$)."),
        (3, "Dobro, evo potpunog postupka koji vodi do zaključka — pažljivo "
            "pročitaj svaki korak.\n\nPretpostavimo da je prava $p$ okomita na "
            "ravan $\\alpha$ u tački $A$. Po definiciji (u uvodnom obliku koji "
            "koristimo u ovoj lekciji) to znači da postoje dvije različite prave "
            "$q_1$ i $q_2$ koje leže u $\\alpha$, prolaze kroz $A$ i obe su "
            "okomite na $p$. Uzmimo sada proizvoljnu pravu $q$ iz ravni $\\alpha$ "
            "koja prolazi kroz $A$. Razmotri ravninu $\\beta$ koju određuju prave "
            "$p$ i $q$. Očigledno je da je presečna linija ravnine $\\beta$ i "
            "ravni $\\alpha$ neka prava koja prolazi kroz $A$; ta prava leži u "
            "$\\beta$ i u $\\alpha$. U posebnom slučaju, ako izaberemo ulogu $q_1$ "
            "ili $q_2$ takvom presečnom pravom, znamo da je ta presečna prava "
            "okomita na $p$. Budući da su $p$ i ta presečna prava u istoj ravnini "
            "$\\beta$, i $q$ leži u istoj ravnini $\\beta$, u ravnini $\\beta$ "
            "pravilo je: ako je prava $p$ okomita na jednu pravu iz te ravnine "
            "koja prolazi kroz tačku sjecišta, onda je $p$ okomita i na svaku "
            "pravu iz te ravnine koja prolazi kroz tu tačku (u ravnini je "
            "okomitost jednako definisana kroz pravu koja čini ugao od "
            "$90^\\circ$). Dakle u ravnini $\\beta$ dobijamo da je $p$ okomita na "
            "$q$. Pošto je $q$ bila proizvoljna prava iz $\\alpha$ koja prolazi "
            "kroz $A$, zaključujemo da je $p$ okomita na svaku takvu pravu iz "
            "$\\alpha$ koja prolazi kroz $A$. Konačan rezultat: ako je $p$ okomita "
            "na ravan $\\alpha$ u tački $A$, onda je $p$ okomita na svaku pravu iz "
            "ravni $\\alpha$ koja prolazi kroz $A$."),
    ),
)

# Netačna međutvrdnja iz nagovještaja 3, izdvojena da se o njoj može pisati
# test bez ponavljanja cijelog teksta. U RAVNI okomitost na JEDNU pravu kroz
# presjek NE povlači okomitost na SVAKU pravu kroz tu tačku — u ravni kroz
# jednu tačku prolazi beskonačno mnogo pravih pod raznim uglovima.
FW_X03_FALSE_INFERENCE = (
    "ako je prava $p$ okomita na jednu pravu iz te ravnine koja prolazi kroz "
    "tačku sjecišta, onda je $p$ okomita i na svaku pravu iz te ravnine koja "
    "prolazi kroz tu tačku"
)

# ---------------------------------------------------------------------------
# TR-B1 — isti sistemski oblik, DVIJE kampanje ranije, prijavljen kao čist
# ---------------------------------------------------------------------------

TR_B1 = HintEvidence(
    scenario_id="TR-B1",
    campaign="targeted_finalfix_b5692c0_20260810-012457",
    candidate_commit="b5692c0",
    topic_id="9-02-006",
    grade=9,
    task_text="Koja izjava najbolje opisuje kada su prava i ravan paralelne?",
    options=(
        ("a", "Prava i ravan mogu imati tačno dvije zajedničke tačke."),
        ("b", "Prava i ravan su paralelne ako nemaju zajedničkih tačaka."),
        ("c", "Ako prava siječe ravan u jednoj tački, onda su paralelne."),
        ("d", "Prava je uvijek paralelna s ravni koja sadrži tu pravu."),
    ),
    marked_option_id="b",
    hints=(
        (1, "U redu — idemo korak po korak. Prvi korak je primijeniti definiciju "
            "paralelizma između prave i ravni.\n\nSjeti se pravila iz lekcije: da "
            "bi odlučio da li su paralelni, prvo provjeri postoji li bilo koja "
            "zajednička tačka između te prave i te ravni. Dakle, pokušaj zamisliti "
            "ili provjeriti: leži li prava potpuno u ravni, ima li barem jednu "
            "zajedničku tačku, ili nema nijednu."),
        (2, "Dobro — idemo dalje s konkretnim međukorakom.\n\nDa bi konkretno "
            "provjerio/la postoji li zajednička tačka, napiši jednačine koje "
            "opisuju pravu i ravan pa riješi njihov sistem. Tačan međukorak: "
            "postavi parametarski oblik prave (npr. $\\vec{r}=\\vec{r_0}+t\\vec{v}$) "
            "i jednačinu ravni (npr. $\\vec{n}\\cdot(\\vec{r}-\\vec{r_1})=0$), pa "
            "potraži vrijednost parametra $t$ za koju su koordinate iste u obje "
            "jednačine. Ako ne dobiješ nijedno rješenje, to znači da nema "
            "zajedničke tačke."),
    ),
)


# ---------------------------------------------------------------------------
# POZITIVNA KONTROLA — čisto skelovanje nad ISTIM zadatkom (FW-X03)
# ---------------------------------------------------------------------------
# Napisano za ovu fazu, nikad objavljeno. Usmjerava na ono što treba UPOREDITI
# i imenuje pojmove lekcije, ali NE izriče kriterij koji bira označenu opciju:
# ne kaže na koliko pravih okomitost mora vrijediti, ni u kojem smjeru
# implikacija ide. Detektor koji ovo proglasi curenjem odbija svaki upotrebljiv
# nagovještaj i time je neupotrebljiv.
CLEAN_PROPOSITIONAL_HINT = (
    "Dobro, idemo korak po korak.\n\nSve četiri tvrdnje govore o istoj situaciji, "
    "ali se razlikuju u DVA detalja: da li se nešto tvrdi za jednu ili za više "
    "pravih, i u kojem smjeru ide „ako … onda“. Uporedi tvrdnje baš po ta dva "
    "detalja i odbaci one kod kojih je smjer obrnut od onoga što lekcija "
    "definiše. Ne donosi još zaključak."
)

# Isti oblik kontrole za TR-B1: imenuje pojam iz lekcije, ali ne kaže koji
# uslov razdvaja paralelnost od ostalih položaja.
CLEAN_PROPOSITIONAL_HINT_TR_B1 = (
    "Dobro, idemo korak po korak.\n\nSve četiri izjave opisuju međusobni položaj "
    "prave i ravni. Prisjeti se koliko različitih položaja lekcija uopšte "
    "razlikuje i po čemu ih razlikuje. Zatim svaku izjavu uporedi s tim popisom "
    "položaja i vidi koja od njih opisuje položaj koji zaista postoji. Ne biraj "
    "još odgovor."
)


ALL_EVIDENCE = (FW_X03, TR_B1)


# ---------------------------------------------------------------------------
# PROVJERA PROTIV LOKALNOG ARTEFAKTA (kad postoji)
# ---------------------------------------------------------------------------

def _load_record(campaign: str, scenario_id: str):
    path = ARTIFACT_DIR / campaign / "results.jsonl"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("id") == scenario_id:
                return record
    return None


def local_artifact_available(evidence: HintEvidence) -> bool:
    return _load_record(evidence.campaign, evidence.scenario_id) is not None


def local_artifact_mismatches(evidence: HintEvidence) -> list:
    """Razlike između zakucanog teksta i lokalnog artefakta.

    Prazna lista = zakucani tekst je bajt za bajt onaj koji je kampanja
    stvarno zapisala. Kad artefakt lokalno ne postoji (svjež checkout —
    `scratchpad/` je u `.gitignore`), vraća se prazna lista i pozivalac
    scenario preskače: odsustvo artefakta nikad nije dokaz neslaganja."""
    record = _load_record(evidence.campaign, evidence.scenario_id)
    if record is None:
        return []

    problems = []
    turns = record.get("turns") or []
    first = turns[0] if turns else {}
    task = ((first.get("response") or {}).get("next_state") or {}).get("task") or {}

    if task.get("question") != evidence.task_text:
        problems.append("task_text")

    artifact_options = tuple(
        (option.get("id"), option.get("text"))
        for option in (task.get("options") or ())
    )
    if artifact_options != evidence.options:
        problems.append("options")

    summary = first.get("session_after_summary") or {}
    if summary.get("correct_option_id") != evidence.marked_option_id:
        problems.append("marked_option_id")

    served = {}
    for turn in turns[1:]:
        level = ((turn.get("session_after_summary") or {}).get("hint_level"))
        if isinstance(level, int):
            served[level] = (turn.get("response") or {}).get("answer") or ""
    for level, text in evidence.hints:
        if served.get(level) != text:
            problems.append(f"hint{level}")
    return problems
