"""Server-vlasnički SEMANTIČKI UGOVOR VJEŽBE po lekciji (Vježbajmo V1, F5K).

ZAŠTO POSTOJI (150-turn real-state audit, 14 P1 nalaza na 7 lekcija):
model je objavljivao MATEMATIČKI ISPRAVNE zadatke koji NE ispituju izabranu
lekciju — „Grafik linearne funkcije“ sveden na uvrštavanje u formulu, „Mreža
prizme“ na formulu zapremine, „Tekstualni zadaci“ na goli izraz, „Dokaz
identiteta s algebarskim razlomcima“ na proširivanje brojevnog razlomka,
„SSU“ na zadatak sa zahvaćenim uglom (što je SUS). Recenzentov
`inside_lesson` je pripadnost mjerio „zvuči srodno“, a server nije imao
nikakav semantički autoritet.

ARHITEKTURA:  LEKCIJA = PODACI,  SEMANTIČKI ZAHTJEV = PODACI,
VALIDACIJA = GENERIČKI KOD.

  • data/semantic_practice_contracts.json — tipovi zahtjeva (required /
    forbidden osobine + prompt linije + vizuelna politika) i redovi po
    lekciji s kurikulumskim dokazom (Faza-1/2 NPP stavke);
  • ovaj modul — biblioteka GENERIČKIH provjerivača osobina nad vidljivim
    tekstom zadatka i opcijama (ograničeni, zatvoreni obrasci — nikad veliki
    NL parser) + učitavanje/keširanje ugovora;
  • matbot/tutor/package_preflight.py — ISTA provjera dvaput: nad Tutorovim
    nacrtom (nalaz recenzentu) i nad recenzentovim KONAČNIM paketom (blokada
    objave prije ijedne mutacije stanja).

PRINCIP DOKAZIVOSTI: osobina se potvrđuje samo iz STRUKTURE koju server sam
vidi (slovna promjenljiva u math segmentu, koordinatni par, zatvoreni
leksikon pitanja o grafiku/mreži/dokazu). Model ne može „reći“ da graf
postoji — provjerava se sadržaj. Gdje deterministički dokaz nije moguć,
lekcija NEMA blokirajući ugovor (fail-closed prema aktivaciji, ne prema
učeniku). Nijedna grana po ID-ju lekcije ne postoji u kodu.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from matbot.mathsegments import TEXT, tokenize_math

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "semantic_practice_contracts.json"

_lock = threading.Lock()
_cache = None
_override = None


class SemanticPracticeError(ValueError):
    """Artefakt ugovora je neupotrebljiv — nikad tiha degradacija."""


# ---------------------------------------------------------------------------
# POMOĆNE PROJEKCIJE VIDLJIVOG SADRŽAJA
# ---------------------------------------------------------------------------

_TEXT_MACRO_RE = re.compile(r"\\text\{[^{}]*\}")
_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")
_WORD_RE = re.compile(r"[a-zčćžšđA-ZČĆŽŠĐ]+")

# Zatvoren rječnik instrukcijskih riječi — NE nose kontekst priče.
_INSTRUCTION_WORDS = frozenset({
    "izračunaj", "izracunaj", "pojednostavi", "odredi", "skrati", "riješi",
    "rijesi", "nađi", "nadji", "napiši", "napisi", "vrijednost", "vrijednosti",
    "izraza", "izraz", "izrazi", "za", "i", "a", "u", "na", "je", "li", "da",
    "se", "od", "do", "sa", "s", "iz", "kad", "kada", "ako", "zatim", "pa",
    "njegovu", "njegovog", "njenu", "koji", "koja", "koje", "kojem", "kojoj",
    "tačan", "tacan", "tačna", "tacna", "tačno", "tacno", "odgovor",
    "ponuđenih", "ponudjenih", "izaberi", "odaberi", "koliko", "kolika",
    "koliki", "iznosi", "dati", "dat", "data", "dato", "date", "dana", "dan",
    "ovaj", "ova", "ovo", "taj", "ta", "to", "sljedećih", "sljedecih",
    "među", "medju", "zadatak", "rezultat", "broj", "brojeva",
})

# Lažno pozivanje na sliku koja NE postoji u tekstualnom UI-ju.
_FAKE_VISUAL_RE = re.compile(
    r"na slici|sa slike|kao na crtežu|na crtežu|prikazan[oa]? na slici|"
    r"pogledaj (?:graf|sliku|crtež)", re.IGNORECASE)


def _math_segments(text):
    return [content for kind, content in tokenize_math(text or "")
            if kind != TEXT]


def _prose(text):
    return " ".join(content for kind, content in tokenize_math(text or "")
                    if kind == TEXT)


def _math_letters(text):
    """Slova-promjenljive u math segmentima (bez \\text{...} i komandi)."""
    letters = []
    for segment in _math_segments(text):
        cleaned = _TEXT_MACRO_RE.sub(" ", segment)
        cleaned = _COMMAND_RE.sub(" ", cleaned)
        letters.extend(ch for ch in cleaned if ch.isalpha())
    return letters


# ---------------------------------------------------------------------------
# BIBLIOTEKA OSOBINA — generički, ograničeni provjerivači
# ---------------------------------------------------------------------------

_COORD_PAIR_RE = re.compile(r"\(\s*-?\d+(?:[.,]\d+)?\s*,\s*-?\d+(?:[.,]\d+)?\s*\)")
_GRAPH_NOUN_RE = re.compile(
    r"grafik|grafiku|grafika|koordinat|os[aiu]\b|prav(?:a|e|i|ih|oj|u)\b|"
    r"tačk|tack", re.IGNORECASE)
_GRAPH_ACTION_RE = re.compile(
    r"pripada|leži|lezi|prolazi|siječe|sijece|presjek|presijeca|očitaj|"
    r"ocitaj|tabel|nacrta|ucrta|crta", re.IGNORECASE)
_NET_NOUN_RE = re.compile(r"mrež|mrez", re.IGNORECASE)
_NET_STRUCTURE_RE = re.compile(
    r"sastoji|čine|cine|sklapanjem|sklopi|presavij|koliko\s+\w*\s*"
    r"(?:strana|pravougaonik|trougl|kvadrat|figur)|bočn|bocn|osnov|"
    r"stran[aei]|figur|oblik|dimenzij|susjedn|raspored", re.IGNORECASE)
_PROOF_RE = re.compile(r"dokaži|dokazi\b|dokazati|dokaz\b|pokaži da|pokazi da|"
                       r"obrazloži|obrazlozi", re.IGNORECASE)
# Stem „podudar“, NE „podudarn“: muški rod jednine ima nepostojano a
# („podudaran“), pa ga duži stem ne hvata. ŽIVI F5L NALAZ (S01 forenzika):
# vjerni SSU zadatak „Koji trougao je podudaran sa...“ bio je lažno označen
# kao semantic_missing i NIJE mogao biti objavljen ni nakon recenzentove
# ispravke — zagarantovan fail-close na najprirodnijoj formulaciji.
_CONGRUENCE_RE = re.compile(r"podudar", re.IGNORECASE)
_VOLUME_RE = re.compile(r"zapremin|volumen", re.IGNORECASE)
_INCLUDED_ANGLE_RE = re.compile(
    r"(?:ugao|ugla|uglu)[^.?!]{0,80}(?:između|izmedju|zahvaćen|zahvacen)|"
    r"(?:između|izmedju)[^.?!]{0,50}(?:stranica|strana|stranama)",
    re.IGNORECASE)


def _feature_variables_present(task_text, options_text):
    return bool(_math_letters(task_text))


def _feature_variable_identity_present(task_text, options_text):
    for segment in _math_segments(task_text):
        if "=" not in segment:
            continue
        left, _eq, right = segment.partition("=")
        if _math_letters(f"${left}$") and _math_letters(f"${right}$"):
            return True
    return False


def _feature_proof_request_present(task_text, options_text):
    return bool(_PROOF_RE.search(task_text or ""))


def _feature_story_context_present(task_text, options_text):
    words = [word.lower() for word in _WORD_RE.findall(_prose(task_text))]
    content = [word for word in words if word not in _INSTRUCTION_WORDS]
    return len(content) >= 4


def _feature_graph_semantics_present(task_text, options_text):
    haystack = f"{task_text or ''} {options_text or ''}"
    if not _GRAPH_NOUN_RE.search(haystack):
        return False
    return bool(_GRAPH_ACTION_RE.search(haystack)
                or _COORD_PAIR_RE.search(haystack))


def _feature_net_semantics_present(task_text, options_text):
    text = task_text or ""
    return bool(_NET_NOUN_RE.search(text) and _NET_STRUCTURE_RE.search(text))


def _feature_congruence_semantics_present(task_text, options_text):
    return bool(_CONGRUENCE_RE.search(f"{task_text or ''} {options_text or ''}"))


def _feature_volume_request(task_text, options_text):
    return bool(_VOLUME_RE.search(task_text or ""))


_SIDE_EQUALITY_RE = re.compile(r"\b([A-Z]{2})\s*=")
_ANGLE_TOKEN_RE = re.compile(r"\\angle\s*([A-Z]{3})")
_ANGLE_VERTEX_RE = re.compile(r"\\angle\s*([A-Z])(?![A-Z])")


def _included_angle_by_notation(text):
    """Zahvaćen ugao iskazan NOTACIJOM (živi F5K nalaz K07): kad su date
    dvije stranice sa ZAJEDNIČKIM tjemenom (npr. $AB$ i $AC$), ugao čije je
    srednje slovo upravo to tjeme ($\\angle BAC$) JESTE ugao između njih —
    bez ijedne riječi „između“. Ugao naspram stranice ($\\angle ABC$ uz iste
    podatke) ima drugo srednje slovo i ne pogađa se.

    ŽIVI F5L NALAZ (S01 forenzika): isti zahvaćeni ugao model piše i JEDNIM
    slovom — $AB=5$, $AC=7$ i $\\angle A=60^\\circ$ — gdje je slovo upravo
    zajedničko tjeme datih stranica. Ta forma je promicala K07 detektoru
    (tražio je tačno tri slova) i SUS raspored proglašen „po SSU“ bio bi
    objavljen. Ugao u tjemenu koje NIJE zajedničko ($\\angle B$ uz $AB$,
    $AC$) i dalje se ne pogađa."""
    segments = " ".join(_math_segments(text or ""))
    sides = [side for side in _SIDE_EQUALITY_RE.findall(segments)]
    shared_vertices = set()
    for first, second in zip(sides, sides[1:]):
        common = set(first) & set(second)
        if len(common) == 1 and first != second:
            shared_vertices.add(next(iter(common)))
    if not shared_vertices:
        return False
    for angle in _ANGLE_TOKEN_RE.findall(segments):
        if angle[1] in shared_vertices:
            return True
    for vertex in _ANGLE_VERTEX_RE.findall(segments):
        if vertex in shared_vertices:
            return True
    return False


def _feature_included_angle_with_sides(task_text, options_text):
    """Zabranjena zahvaćenost: frazom (tekst ILI opcije — živi K07 nalaz:
    „ugao između njih … po SSU“ stajao je u OZNAČENOJ OPCIJI) ili notacijom."""
    haystack = f"{task_text or ''} {options_text or ''}"
    if _INCLUDED_ANGLE_RE.search(haystack):
        return True
    return _included_angle_by_notation(task_text)


# ŽIVI DISC NALAZ (E010, lekcija o visinama trougla i ortocentru — ID nosi
# ISKLJUČIVO red u data/semantic_practice_contracts.json, nikad ovaj modul):
# objavljen je MCQ „Koji od navedenih skupova podataka jednoznačno … određuje
# trougao ABC…“ u kojem su DVIJE opcije istovremeno jednoznačno određivale
# trougao (osnovica + stopa visine + dužina visine, ali i osnovica + oba
# nalegla ugla — USU). Arhetip „koji skup podataka jednoznačno određuje /
# dovoljan je za konstrukciju“ suštinski poziva na više tačnih odgovora, a
# potpuna deterministička presuda bi tražila geometrijski motor koji je van
# opsega ovog izdanja (odluka globalnog forenzičkog plana). Osobina je zato
# ZABRANA ARHETIPA na nivou ugovora lekcije: pali se SAMO kad proza spaja
# jednoznačnost/dovoljnost s određivanjem/konstrukcijom trougla — direktna
# pitanja o visinama, ortocentru i sama uputstva „konstruiši visinu“ NIKAD
# ne okidaju (nemaju jednoznačn/dovoljn).
_CONSTRUCTION_DETERMINATION_RE = re.compile(
    r"(?:jednozna[čc]n\w*|dovoljn\w*)[^.?!]{0,120}(?:odre[dđ]\w*|konstru\w*)"
    r"|(?:odre[dđ]\w*|konstru\w*)[^.?!]{0,120}(?:jednozna[čc]n\w*|dovoljn\w*)",
    re.IGNORECASE)
_TRIANGLE_WORD_RE = re.compile(r"trougl\w*|trougao", re.IGNORECASE)


def _feature_construction_determination_request(task_text, options_text):
    haystack = f"{task_text or ''} {options_text or ''}"
    if not _TRIANGLE_WORD_RE.search(haystack):
        return False
    return bool(_CONSTRUCTION_DETERMINATION_RE.search(haystack))


FEATURE_CHECKS = MappingProxyType({
    "variables_present": _feature_variables_present,
    "variable_identity_present": _feature_variable_identity_present,
    "proof_request_present": _feature_proof_request_present,
    "story_context_present": _feature_story_context_present,
    "graph_semantics_present": _feature_graph_semantics_present,
    "net_semantics_present": _feature_net_semantics_present,
    "congruence_semantics_present": _feature_congruence_semantics_present,
    "volume_request": _feature_volume_request,
    "included_angle_with_sides": _feature_included_angle_with_sides,
    "construction_determination_request": _feature_construction_determination_request,
})

VISUAL_POLICIES = ("visual_essential", "textual_structured_equivalent",
                   "conceptual_text")


# ---------------------------------------------------------------------------
# UGOVOR
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticPracticeContract:
    lesson_id: str
    requirement_type: str
    required_features: tuple
    forbidden_features: tuple
    visual_policy: str
    enforcement: str            # "blocking" — jedini aktivni režim za sada
    prompt_lines: tuple
    evidence: str
    contract_version: str

    def prompt_block(self) -> str:
        return "\n".join(self.prompt_lines)


def _build_contract(row, types, version):
    requirement = row.get("requirement_type", "")
    spec = types.get(requirement)
    if spec is None:
        raise SemanticPracticeError(
            f"{row.get('lesson_id')}: nepoznat tip zahtjeva {requirement!r}")
    required = tuple(spec.get("required_features", ())) \
        + tuple(row.get("extra_required_features", ()))
    forbidden = tuple(spec.get("forbidden_features", ())) \
        + tuple(row.get("extra_forbidden_features", ()))
    for feature in (*required, *forbidden):
        if feature not in FEATURE_CHECKS:
            raise SemanticPracticeError(
                f"{row.get('lesson_id')}: nepoznata osobina {feature!r}")
    policy = spec.get("visual_policy", "conceptual_text")
    if policy not in VISUAL_POLICIES:
        raise SemanticPracticeError(
            f"{row.get('lesson_id')}: nepoznata vizuelna politika {policy!r}")
    evidence = str(row.get("evidence", "")).strip()
    if not evidence:
        raise SemanticPracticeError(
            f"{row.get('lesson_id')}: blokirajući ugovor bez dokaza")
    return SemanticPracticeContract(
        lesson_id=str(row["lesson_id"]),
        requirement_type=requirement,
        required_features=required,
        forbidden_features=forbidden,
        visual_policy=policy,
        enforcement=str(row.get("enforcement", "blocking")),
        prompt_lines=tuple(spec.get("prompt_lines", ())),
        evidence=evidence,
        contract_version=version,
    )


def _load():
    global _cache
    if _override is not None:
        return _override
    if _cache is None:
        with _lock:
            if _cache is None:
                if not DATA_PATH.exists():
                    _cache = {}
                else:
                    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
                    version = str(payload.get("contract_version", ""))
                    types = payload.get("requirement_types", {})
                    contracts = {}
                    for row in payload.get("contracts", ()):
                        contract = _build_contract(row, types, version)
                        if contract.lesson_id in contracts:
                            raise SemanticPracticeError(
                                f"dupli ugovor: {contract.lesson_id}")
                        contracts[contract.lesson_id] = contract
                    _cache = contracts
    return _cache


def contract_for(lesson_id):
    """Semantički ugovor vježbe za kanonski ID, ili None."""
    if not lesson_id:
        return None
    return _load().get(str(lesson_id))


def all_contracts():
    return dict(_load())


def reset_cache():
    global _cache
    with _lock:
        _cache = None


class override_contracts:
    """Privremena zamjena registra (testovi)."""

    def __init__(self, contracts):
        self._value = dict(contracts)
        self._previous = None

    def __enter__(self):
        global _override
        self._previous = _override
        _override = self._value
        return self._value

    def __exit__(self, *exc):
        global _override
        _override = self._previous
        return False


# ---------------------------------------------------------------------------
# PROVJERA PAKETA — kodovi su interni (samo logovi), zatvoren skup
# ---------------------------------------------------------------------------

def fidelity_failures(contract, task_text, options_text=""):
    """Torka internih kodova prekršaja ugovora; prazna = nema DOKAZANOG
    prekršaja (to nije dokaz vjernosti — semantiku i dalje čuva recenzent)."""
    if contract is None:
        return ()
    failures = []
    for feature in contract.required_features:
        if not FEATURE_CHECKS[feature](task_text, options_text):
            failures.append(f"semantic_missing:{feature}")
    for feature in contract.forbidden_features:
        if FEATURE_CHECKS[feature](task_text, options_text):
            failures.append(f"semantic_forbidden:{feature}")
    return tuple(failures)


def fake_visual_reference(task_text):
    """GLOBALNA zabrana (sve lekcije): pozivanje na sliku/crtež koji u
    tekstualnom UI-ju ne postoji. Vraća True kad je pozivanje dokazano."""
    return bool(_FAKE_VISUAL_RE.search(task_text or ""))
