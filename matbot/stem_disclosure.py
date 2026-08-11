"""Tekst zadatka NE SMIJE sam reći koja je opcija tačna (FINAL-40, FW-G03).

ŽIVI BLOKATOR IZDANJA (FINAL40 na 2fe5636, scenario FW-G03, lekcija 6. razreda
o pojmu ugla, traženo „Ne rješavaj zadatak učeniku."):

    Objavljeni tekst:  „… zrak $\\overrightarrow{BD}$ LEŽI IZMEĐU zraka
                       $\\overrightarrow{BA}$ i $\\overrightarrow{BC}$, dok
                       zrak $\\overrightarrow{BE}$ NE leži između njih. Koji od
                       navedenih zraka dijeli ugao $\\angle ABC$ na dva dijela
                       (tj. LEŽI IZMEĐU zraka $\\overrightarrow{BA}$ i
                       $\\overrightarrow{BC}$)?"
    Opcije:            BA, BC, BD, BE      Označeno: BD

Zadatak je matematički tačan, opcije su različite, tačno jedna je tačna — i
zato ga nijedna postojeća kapija nije imala čime oboriti. Ali sâm tekst tvrdi
BAŠ ONU osobinu koju pita, i to BAŠ za označenu opciju: učenik ne rasuđuje,
nego prepisuje. To je istovremeno pedagoški promašaj i prekršaj izričitog
učenikovog zahtjeva da se zadatak ne riješi umjesto njega.

ZAŠTO POSTOJEĆA ZAŠTITA OD CURENJA OVO NE VIDI
----------------------------------------------
`matbot/feedback.leaks_answer` (kroz `pipeline._guard_answer_leak`) štiti
ODGOVOR TUTORA na pomoć/hint turnu i mjeri ga prema serverski committed
odgovoru. Ona IZRIČITO izuzima sve što već stoji u tekstu zadatka:

    if feedback.leaks_answer(task, marked, expected):
        return False            # „prepričavanje, ne otkrivanje"

Taj izuzetak je tačan za hint (hint smije citirati zadatak), ali znači da
NIJEDAN sloj nikad ne pita da li SAM ZADATAK otkriva svoj odgovor. Evaluatorski
`no_leak` mjeri nešto treće — interne markere i sirov JSON u odgovoru.

ŠTA OVAJ MODUL JESTE
--------------------
Uzak deterministički detektor JEDNE klase: MCQ IZBORA ENTITETA (sve četiri
opcije su atomarni entiteti — zrak, tačka, oznaka, vrijednost), u kojem
DEKLARATIVNI dio teksta tvrdi ISTU osobinu koju pita UPITNA rečenica, i to
za TAČNO JEDAN entitet — označeni.

Dokaz je strukturan, ne leksički: traži se NAJDUŽI ZAJEDNIČKI NEPREKINUTI niz
tokena između upitne rečenice i jedne deklarativne klauze (osobina), pa se
gleda koji je entitet SUBJEKT te klauze (posljednji entitet PRIJE osobine).
Entiteti UNUTAR osobine su dio osobine, ne subjekt — zato „…BD leži između BA
i BC" ima tačno jedan subjekt (BD), a ne tri.

ŠTA OVAJ MODUL NIJE
-------------------
Nije zabrana da se tačna opcija pojavi u tekstu. Entitet smije biti podatak:
„Dati su zraci BD i BE. Koji leži između BA i BC?" je ispravan zadatak i ovdje
prolazi netaknut — tekst uvodi BD, ali ne tvrdi traženu osobinu o njemu.

Nije ni prepoznavač prirodnog jezika: bez dokazane strukture (upitna rečenica
s izbornom zamjenicom, četiri atomarna entiteta, dovoljno dug zajednički niz,
jedinstven nenegiran subjekt) modul ĆUTI. Preskočeno NIJE dokaz ispravnosti —
semantiku i dalje nezavisno sudi recenzent (doktrina cijelog projekta).
"""
import re

from matbot import mathsegments

# Interni kod nalaza — logovi i recenzentov ulaz, nikad browser (pravilo 7).
STEM_ANSWER_DISCLOSURE_CODE = "stem_answer_disclosure"

# Najkraći zajednički niz koji se prihvata kao DOKAZ iste osobine. Mjereno nad
# 2698 zamrznutih objavljenih paketa svih kampanja: na 3 tokena jedini pogodak
# u cijelom korpusu je FW-G03 (vidi kalibraciju u testovima). Niže bi hvatalo
# usputna preklapanja („leži između" bez subjekta), više bi propustilo kraće
# formulacije iste greške.
MIN_SHARED_SPAN = 3
# Koliko od tih tokena mora biti STVARNI sadržaj osobine — ni entitet, ni
# funkcijska riječ. Bez ovoga bi niz sastavljen samo od imena entiteta
# („BA i BC") lažno dokazivao osobinu.
MIN_PROPERTY_TOKENS = 2

# Izborna zamjenica: bez nje upitna rečenica nije izbor entiteta („Koliko
# iznosi…" je račun, ne izbor) i klasa se ne prepoznaje.
_SELECTOR_RE = re.compile(
    r"\b(?:koji|koja|koje|kojeg|kojem|kojoj|kojih|kojim|kojima)\b",
    re.IGNORECASE)

# Negacija u istoj klauzi ispred osobine — „BE NE leži između njih" nije
# tvrdnja o osobini nego njeno poricanje.
_NEGATIONS = frozenset({"ne", "nije", "nisu", "niti", "ni", "nema"})

# Funkcijske riječi koje ne nose osobinu. Namjerno KRATAK zatvoren skup: ovo
# nije stemmer ni rječnik, samo zaštita od toga da veznici i prijedlozi sami
# dokazuju osobinu.
_FUNCTION_WORDS = frozenset({
    "i", "ili", "a", "ali", "je", "su", "da", "se", "od", "do", "na", "u",
    "za", "sa", "s", "te", "pa", "li", "bi", "ga", "ih", "im", "mu", "joj",
    "to", "ta", "taj", "ti", "the", "of",
})

# Kraj rečenice traži VELIKO SLOVO (ili početak matematike) iza tačke —
# inače skraćenica „tj." raskine upravo onu upitnu rečenicu koja je predmet
# provjere (živi FW-G03: „…(tj. leži između…)?" je pucalo na „tj.").
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZČĆĐŠŽ$\\])")
# Klauza NIKAD ne prelazi granicu rečenice; dodatno je lome kontrastni
# veznici. Usitnjavanje je KONZERVATIVNO (kraće klauze → kraći zajednički
# nizovi → rjeđi nalaz).
_CLAUSE_SPLIT_RE = re.compile(
    r"[.!?;:]|,\s*(?=\bdok\b|\ba\b|\bali\b)|\bdok\b|\bali\b", re.IGNORECASE)

# Unutar matematike LaTeX kontrolne riječi su ZAPIS, ne sadržaj:
# `\overrightarrow{BD}` je entitet BD, `\angle ABC` je entitet ABC.
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
_WORD_RE = re.compile(r"[0-9A-Za-zČčĆćĐđŠšŽž]+")


def _tokenize(text):
    """Niz normalizovanih tokena; matematika se svodi na gole identifikatore."""
    tokens = []
    for kind, content in mathsegments.tokenize_math(text or ""):
        if kind != mathsegments.TEXT:
            content = _LATEX_COMMAND_RE.sub(" ", content)
        tokens.extend(match.group(0).lower()
                      for match in _WORD_RE.finditer(content))
    return tokens


def _entity_key(option_text):
    """Atomarni entitet opcije, ili prazno kad opcija nije jedan entitet."""
    tokens = _tokenize(option_text)
    return tokens[0] if len(tokens) == 1 else ""


def _split_ask(task_text):
    """(kontekst, upitna rečenica) ili (None, None) kad klasa nije prepoznata."""
    text = (task_text or "").strip()
    sentences = [part for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    ask_index = next((index for index in range(len(sentences) - 1, -1, -1)
                      if "?" in sentences[index]), None)
    if ask_index is None or ask_index == 0:
        return None, None                # nema pitanja, ili nema deklarativnog dijela
    return " ".join(sentences[:ask_index]), sentences[ask_index]


def _clauses(context):
    return [clause for clause in _CLAUSE_SPLIT_RE.split(context or "")
            if clause and clause.strip()]


def _longest_common_span(clause_tokens, ask_tokens):
    """(početak_u_klauzi, dužina) najdužeg zajedničkog NEPREKINUTOG niza."""
    best_start, best_len = 0, 0
    previous = [0] * (len(ask_tokens) + 1)
    for i, token in enumerate(clause_tokens, start=1):
        current = [0] * (len(ask_tokens) + 1)
        for j in range(1, len(ask_tokens) + 1):
            if token == ask_tokens[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > best_len:
                    best_len = current[j]
                    best_start = i - current[j]
        previous = current
    return best_start, best_len


def _property_token_count(span_tokens, entities):
    return sum(1 for token in span_tokens
               if token not in entities and token not in _FUNCTION_WORDS)


def _asserted_entity(clause_tokens, span_start, span_end, entities):
    """Entitet-SUBJEKT klauze, ili prazno kad ga nema ili je osobina negirana.

    Subjekt je posljednji entitet PRIJE osobine. Entiteti unutar osobine su
    dio same osobine („leži između BA i BC"), pa nikad nisu subjekt — bez tog
    razlikovanja bi jedna klauza „dokazala" osobinu za tri različite opcije."""
    subject_index = next(
        (index for index in range(span_start - 1, -1, -1)
         if clause_tokens[index] in entities), None)
    if subject_index is None:
        return ""
    if any(clause_tokens[index] in _NEGATIONS
           for index in range(subject_index + 1, span_start)):
        return ""
    if span_start and clause_tokens[span_start - 1] in _NEGATIONS:
        return ""
    return clause_tokens[subject_index]


def stem_answer_disclosure(task_text, option_texts, marked_index):
    """Detalj dokazanog otkrivanja odgovora u tekstu zadatka, ili prazno.

    Prazno znači „nije dokazano" — uključujući svaki paket koji nije MCQ
    izbora entiteta. To NIJE dokaz da zadatak ne otkriva odgovor."""
    options = list(option_texts or ())
    if len(options) != 4 or not isinstance(marked_index, int):
        return ""
    if not 0 <= marked_index < len(options):
        return ""

    keys = [_entity_key(option) for option in options]
    if not all(keys) or len(set(keys)) != len(keys):
        return ""                        # nije klasa izbora atomarnih entiteta
    entities = frozenset(keys)
    marked = keys[marked_index]

    context, ask = _split_ask(task_text)
    if not context or not ask or not _SELECTOR_RE.search(ask):
        return ""

    ask_tokens = _tokenize(ask)
    if not ask_tokens:
        return ""

    clause_tokens = [tokens for tokens in
                     (_tokenize(clause) for clause in _clauses(context)) if tokens]

    # PROLAZ 1 — najjača nenegirana tvrdnja o osobini, po entitetu.
    asserted = {}
    for tokens in clause_tokens:
        start, length = _longest_common_span(tokens, ask_tokens)
        if length < MIN_SHARED_SPAN:
            continue
        span = tokens[start:start + length]
        if _property_token_count(span, entities) < MIN_PROPERTY_TOKENS:
            continue
        subject = _asserted_entity(tokens, start, start + length, entities)
        if not subject:
            continue
        if length > asserted.get(subject, (0, ()))[0]:
            asserted[subject] = (length, tuple(span))

    if marked not in asserted or len(asserted) != 1:
        # Nema tvrdnje o označenom entitetu, ili tekst istu osobinu tvrdi i za
        # neku drugu opciju — tada ona ne izdvaja jedinstveno tačan odgovor.
        return ""

    length, span = asserted[marked]

    # PROLAZ 2 — JEDINSTVENOST. Prolaz 1 po klauzi uzima samo NAJDUŽI niz, pa
    # bi rečenica koja istu osobinu nabroji dvaput u JEDNOJ klazi („…BD leži
    # između BA i BC, i da BE leži između BA i BC“) dala tvrdnju samo za prvi
    # subjekt. Tekst koji istu osobinu pripisuje dvama entitetima NE izdvaja
    # tačan odgovor i ovdje se mora prešutjeti: traži se SVAKO pojavljivanje
    # ISTOG niza u SVIM klauzama, bez obzira na granicu klauze.
    for tokens in clause_tokens:
        for start in range(len(tokens) - len(span) + 1):
            if tuple(tokens[start:start + len(span)]) != span:
                continue
            subject = _asserted_entity(tokens, start, start + len(span), entities)
            if subject and subject != marked:
                return ""
    return (f"{STEM_ANSWER_DISCLOSURE_CODE}: the stem states of the marked "
            f"option the very property the question asks the student to "
            f"determine (shared span of {length} tokens: "
            f"'{' '.join(span)[:80]}')")
