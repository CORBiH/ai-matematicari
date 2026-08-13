"""ARHETIP zadatka — koju misaonu radnju učenik mora uraditi.

ZAŠTO POSTOJI (nalaz iz ručnog i živog QA): raznolikost ŠABLONA nije
raznolikost VJEŽBE. Lekcija o brojevnim izrazima s decimalnim brojevima imala
je 12 različitih rečenica, a sve su bile ista priča: kupovina, ukupna cijena,
kusur — samo s drugim imenima, robom i cijenama. Mjera „12 šablona“ ju je
proglasila raznolikom, a učenik je dobijao istu vježbu.

    šablon  = kako rečenica izgleda
    arhetip = šta učenik mora URADITI

Ovaj modul uvodi drugu mjeru: zatvorenu listu pedagoških arhetipa i
DETERMINISTIČKI klasifikator koji arhetip čita iz OBJAVLJENOG teksta.

GRANICE (iste kao kod svakog verifikatora ovdje):
  • ovo NIJE mjera tačnosti i nikad ne odlučuje o objavi zbog matematike;
  • model smije deklarisati svoj arhetip, ali njegova tvrdnja NIJE dokaz —
    server klasifikuje sam iz vidljivog teksta;
  • nepoznato se svrstava u `DIRECT_COMPUTE` (najčešći, najneutralniji oblik),
    nikad se ne izmišlja egzotičan arhetip da bi metrika izgledala bolje.
"""
import re

# ---------------------------------------------------------------------------
# ZATVORENA LISTA ARHETIPA
# ---------------------------------------------------------------------------
DIRECT_COMPUTE = "DIRECT_COMPUTE"
FIND_MISSING_VALUE = "FIND_MISSING_VALUE"
ERROR_ANALYSIS = "ERROR_ANALYSIS"
CHOOSE_CORRECT_REASONING = "CHOOSE_CORRECT_REASONING"
COMPARE_RESULTS = "COMPARE_RESULTS"
COMPLETE_MISSING_STEP = "COMPLETE_MISSING_STEP"
TRANSLATE_REPRESENTATION = "TRANSLATE_REPRESENTATION"
MULTI_STEP_APPLICATION = "MULTI_STEP_APPLICATION"
CLASSIFY_CASE = "CLASSIFY_CASE"

ALL_ARCHETYPES = (
    DIRECT_COMPUTE, FIND_MISSING_VALUE, ERROR_ANALYSIS, CHOOSE_CORRECT_REASONING,
    COMPARE_RESULTS, COMPLETE_MISSING_STEP, TRANSLATE_REPRESENTATION,
    MULTI_STEP_APPLICATION, CLASSIFY_CASE,
)

# Kratak, čitljiv opis koji ide U PROMPT kad server bira sljedeći arhetip.
DESCRIPTIONS = {
    DIRECT_COMPUTE: "direktno izračunaj traženu vrijednost",
    FIND_MISSING_VALUE: "iz poznatog rezultata pronađi NEDOSTAJUĆU veličinu (obrnut smjer)",
    ERROR_ANALYSIS: "prikaži tuđe rješenje s greškom i traži da učenik nađe/ispravi grešku",
    CHOOSE_CORRECT_REASONING: "ponudi tvrdnje/obrazloženja i traži ono koje tačno opisuje pojam",
    COMPARE_RESULTS: "uporedi dvije vrijednosti ili dva postupka i traži odnos među njima",
    COMPLETE_MISSING_STEP: "prikaži započet postupak i traži da učenik dopuni korak koji nedostaje",
    TRANSLATE_REPRESENTATION: "traži prevod iz jednog zapisa u drugi (riječi ↔ simboli, razlomak ↔ decimala, jedinice)",
    MULTI_STEP_APPLICATION: "kratak tekstualni zadatak iz svakodnevne situacije s više koraka",
    CLASSIFY_CASE: "traži prepoznavanje/razvrstavanje: koji primjer pripada pojmu, koje je vrste",
}

# ---------------------------------------------------------------------------
# KLASIFIKATOR — redoslijed je namjeran: od najspecifičnijeg ka najopštijem
# ---------------------------------------------------------------------------
_RULES = (
    (ERROR_ANALYSIS, re.compile(
        r"(pogrije[šs]i|gre[šs]k|neta[čc]no je rije[šs]|ispravk|"
        r"u[čc]enik (?:je )?(?:zapisa|rije[šs]i|ra[čc]una|tvrdi|rekao|poku[šs]a)|"
        r"gdje je gre[šs]ka|koja ispravka|kako treba ispravno)", re.IGNORECASE)),
    (COMPLETE_MISSING_STEP, re.compile(
        r"(nastavi (?:skra[ćc]iv|postupak|ra[čc]un)|sljede[ćc]i korak|"
        r"koji korak (?:nedostaje|slijedi)|dopuni (?:korak|postupak)|"
        r"zapo[čc]et postupak|nedostaje korak)", re.IGNORECASE)),
    (FIND_MISSING_VALUE, re.compile(
        r"(koji broj nedostaje|nedostaju[ćc]|treba upisati|umjesto\s*\$?x|"
        r"\\square|koliki je nepoznat|odredi nepoznat|"
        r"koja vrijednost .{0,24}(?:daje|zadovoljava)|"
        r"da bi (?:razlomci|jednakost|tvrdnja) (?:bil[ai]|bio))", re.IGNORECASE)),
    (COMPARE_RESULTS, re.compile(
        r"(uporedi|upore[đd]|za koliko je (?:ve[ćc]|manj)|koja je ve[ćc]a|"
        r"koji je ve[ćc]i|koje je manje|koliko je ve[ćc]i|"
        r"koja (?:dva )?(?:ra[čc]una|rezultata) .{0,20}(?:jednak|razlik))",
        re.IGNORECASE)),
    (TRANSLATE_REPRESENTATION, re.compile(
        r"(izra[žz]eno u|zapi[šs]i u obliku|pretvori|u decimalnom (?:zapisu|obliku)|"
        r"koliko procenata|u obliku razlomka|kao razlomak|"
        r"prika[žz]i .{0,20}(?:u obliku|kao)|preraцunaj|preračunaj)", re.IGNORECASE)),
    (CHOOSE_CORRECT_REASONING, re.compile(
        r"(koja (?:tvrdnja|izjava|jednakost) .{0,30}(?:ta[čc]n|opisuje)|"
        r"koji opis .{0,20}ta[čc]n|koje obrazlo[žz]enje|"
        r"koji korak .{0,20}obrazla[žz]e|koja tvrdnja va[žz]i)", re.IGNORECASE)),
    (CLASSIFY_CASE, re.compile(
        r"(koji od (?:ponu[đd]j?enih|navedenih|sljede[ćc]ih) .{0,40}"
        r"(?:je|pripada|jesu|mogu)|kako se zove|koje je vrste|"
        r"koji .{0,20}(?:pripada|ne pripada) skupu|je li .{0,20}(?:prost|slo[žz]en))",
        re.IGNORECASE)),
)

# Tekstualni zadatak: osoba/situacija + više brojeva. Provjerava se TEK ako
# nijedno specifičnije pravilo nije pogodilo, jer priča može nositi bilo koji
# arhetip (npr. greška u tuđem računu je i dalje ERROR_ANALYSIS).
_STORY_RE = re.compile(
    r"(kupu|pla[ćc]a|ko[šs]ta|cijena|nov[čc]anic|kusur|štedn|kamat|"
    r"podijelj|bazen|vrt|soba|put(?:uje|ovanje)|brzin|radnik|u[čc]enik[ai]\b|"
    r"prodavnic|trgovin)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def classify(task_text, option_texts=()):
    """Arhetip objavljenog zadatka — deterministički, iz vidljivog teksta."""
    text = " ".join(str(task_text or "").split())
    if not text:
        return ""
    for archetype, pattern in _RULES:
        if pattern.search(text):
            return archetype
    if _STORY_RE.search(text) and len(_NUMBER_RE.findall(text)) >= 2:
        return MULTI_STEP_APPLICATION
    return DIRECT_COMPUTE


def describe(archetype):
    return DESCRIPTIONS.get(archetype, "")
