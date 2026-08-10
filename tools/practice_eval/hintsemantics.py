"""MJERENJE otkrivanja TVRDNJE u nagovještaju 1/2 (arhitektonska Faza 0).

ZAŠTO POSTOJI. `feedback.leaks_answer` — jedini orakl curenja koji i proizvod i
evaluator koriste — traži VRIJEDNOST: doslovnu podnisku dužu od tri znaka,
otkrivajuću frazu uz kratak odgovor, ili tvrđen broj unutar `$…$`. Kad je
označena opcija REČENICA (prepoznavanje definicije, svojstva, tvrdnje), odgovor
nije vrijednost nego iskaz, pa nijedan od ta tri sloja ne može ništa dokazati.
Živi ishod: FW-X03, nagovještaj 1 doslovno izriče označenu tvrdnju, a
`hint_no_leak` vraća PASS.

ŠTA OVAJ MODUL JESTE: uzak STRUKTURNI mjerač nad SERVER-VLASNIČKIM činjenicama
(tekst zadatka, označena opcija, distraktori). Pita samo jedno: pokriva li
nagovještaj sadržajne tokene BAŠ označene opcije, uključujući one koje nijedan
distraktor nema, a nijedan token svojstven nekom distraktoru se ne pojavljuje.
To je struktura „nagovještaj bira odgovor umjesto učenika“.

ŠTA OVAJ MODUL NIJE: sinonimi, korjenovanje, lematizacija, semantička sličnost,
model. Poređenje je poklapanje TAČNIH tokena. Zato promašuje parafrazu i
promijenjen padež — i to se NE krpi u ovoj fazi nego se izričito mjeri i
zamrzava kao poznata slijepa tačka (vidi `tests/test_evaluator_hint_blind_spots.py`,
TR-B1).

DVA IZUZETKA, oba preuzeta od postojećeg pravila projekta, nikad nova:

  1. Token koji već stoji u TEKSTU ZADATKA nije dokaz — to je prepričavanje, ne
     otkrivanje. Isti izuzetak `feedback.leaks_answer` ima kroz `task_text`, a
     `tutor/pipeline.py::_reveals_committed_answer` ga primjenjuje u produkciji.
  2. Vrh ljestvice (treći nagovještaj) je po ugovoru SMIJE otkriti rezultat
     (`_HINT_LEVEL_GUIDANCE[2]`, `is_hint_ladder_top`), pa se ovdje nikad ne
     mjeri. Pozivalac prosljeđuje samo nivoe 1 i 2.

KALIBRACIJA (izmjereno nad SVIM lokalnim kampanjskim artefaktima, 156 turnova
nagovještaja nivoa 1–2 s poznatim opcijama): 148 `NOT_APPLICABLE` (označena
opcija je vrijednost, ne tvrdnja — tamo ostaje `feedback.leaks_answer`),
7 `NOT_PROVEN`, 1 `DISCLOSED` — tačno FW-X03 nagovještaj 1. Nula lažnih
pozitiva na tom korpusu.

OVAJ MODUL NIJE UVEZAN NI U JEDNU PROVJERU (`checks._CHECKS`) i ne mijenja
nijedan rezultat kampanje. Faza 0 mjeri; uvezivanje je Faza 2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- jačina dokaza (isti rječnik kao release_contract) ---------------------
DISCLOSED = "DISCLOSED"            # dokazano otkrivanje označene tvrdnje
NOT_PROVEN = "NOT_PROVEN"          # ništa nije dokazano — nikad „čisto“
NOT_APPLICABLE = "NOT_APPLICABLE"  # označena opcija nije tvrdnja (vrijednost)

VERDICTS = (DISCLOSED, NOT_PROVEN, NOT_APPLICABLE)

# --- pragovi (zamrznuti kalibracijom iznad) --------------------------------
# Označena opcija mora imati bar toliko SADRŽAJNIH tokena koji NE stoje u
# tekstu zadatka da bi se uopšte mogla zvati tvrdnjom. Ispod toga je odgovor
# vrijednost („75“, „$\frac{5}{12}$“, „10“) i vlasnik ostaje leaks_answer.
MIN_PROPOSITION_TOKENS = 5
# Udio sadržajnih tokena označene opcije koje nagovještaj mora ponoviti.
MARKED_COVERAGE_THRESHOLD = 0.90
# Svi tokeni koje ima SAMO označena opcija moraju biti prisutni…
MARKED_ONLY_COVERAGE_THRESHOLD = 1.0
# …a nijedan token svojstven bilo kojem distraktoru ne smije biti prisutan.
MAX_DISTRACTOR_ONLY_COVERAGE = 0.0

# Zatvorena lista bosanskih funkcijskih riječi. NIJE rječnik i ne opisuje
# matematiku — samo uklanja veznike/zamjenice koje bi svaki tekst dijelio sa
# svakim. Lista se ne širi bez ponovne kalibracije nad artefaktima.
_STOPWORDS = frozenset("""
ako je su na u i ili se da to tada onda koja koji koje kroz iz za sa te ta taj
kod li ne nije bilo svaka svaki svako neku neki neka vec već samo ovo ova ovaj
prvo dakle npr tako kada kad pa ali sto što sve svih toj tom tim ga joj im ih
nam vam mi vi on ona ono oni one bude biti bio bila jer no nego tj kao
""".split())

_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")
_NON_WORD_RE = re.compile(r"[^0-9a-zčćžšđ]+")


def content_tokens(text: str) -> frozenset:
    """Sadržajni tokeni jednog vidljivog teksta.

    Matematički zapis se NE odbacuje: `$…$` granice padaju, a LaTeX komanda
    postaje običan token (`\\alpha` → `alpha`), pa se `$\\alpha$` u zadatku i u
    opciji poklope. Poređenje ostaje doslovno."""
    lowered = _COMMAND_RE.sub(lambda m: " " + m.group(0)[1:] + " ", (text or "").replace("$", " "))
    words = _NON_WORD_RE.sub(" ", lowered.lower()).split()
    return frozenset(word for word in words
                     if len(word) >= 2 and word not in _STOPWORDS)


@dataclass(frozen=True)
class Disclosure:
    """Nalaz nad jednim nagovještajem. `detail` nikad ne nosi sadržaj."""

    verdict: str
    marked_coverage: float = 0.0
    marked_only_coverage: float = 0.0
    distractor_only_coverage: float = 0.0
    proposition_tokens: int = 0
    detail: str = ""

    @property
    def disclosed(self) -> bool:
        return self.verdict == DISCLOSED


def measure(hint_text: str, marked_option_text: str, distractor_texts,
            task_text: str = "") -> Disclosure:
    """Izmjeri otkriva li nagovještaj označenu TVRDNJU.

    Vraća `NOT_APPLICABLE` kad odgovor nije tvrdnja, `NOT_PROVEN` kad se ništa
    ne može dokazati (uključujući svaku parafrazu — vidi docstring modula), i
    `DISCLOSED` samo kad je struktura „nagovještaj bira odgovor“ dokazana."""
    from_task = content_tokens(task_text)
    marked = content_tokens(marked_option_text) - from_task
    if len(marked) < MIN_PROPOSITION_TOKENS:
        return Disclosure(
            NOT_APPLICABLE, proposition_tokens=len(marked),
            detail=(f"marked option carries {len(marked)} content tokens outside the "
                    f"task text; below {MIN_PROPOSITION_TOKENS} the answer is a value, "
                    "not a proposition — feedback.leaks_answer stays the owner"))

    distractors = [tokens for tokens in
                   (content_tokens(text) - from_task for text in distractor_texts)
                   if tokens]
    if not distractors:
        return Disclosure(NOT_PROVEN, proposition_tokens=len(marked),
                          detail="no distractor content survives the task-text exception")

    shared = set().union(*distractors)
    marked_only = marked - shared
    if not marked_only:
        return Disclosure(NOT_PROVEN, proposition_tokens=len(marked),
                          detail="options are not token-separable; nothing can be proven")

    hint = content_tokens(hint_text) - from_task
    marked_coverage = len(marked & hint) / len(marked)
    marked_only_coverage = len(marked_only & hint) / len(marked_only)
    distractor_only_coverage = max(
        (len((tokens - marked) & hint) / len(tokens - marked)) if (tokens - marked) else 0.0
        for tokens in distractors)

    disclosed = (marked_coverage >= MARKED_COVERAGE_THRESHOLD
                 and marked_only_coverage >= MARKED_ONLY_COVERAGE_THRESHOLD
                 and distractor_only_coverage <= MAX_DISTRACTOR_ONLY_COVERAGE)
    return Disclosure(
        DISCLOSED if disclosed else NOT_PROVEN,
        marked_coverage=marked_coverage,
        marked_only_coverage=marked_only_coverage,
        distractor_only_coverage=distractor_only_coverage,
        proposition_tokens=len(marked),
        detail=("the hint covers the marked proposition and no distractor's own content"
                if disclosed else "no disclosure structure could be proven"))
