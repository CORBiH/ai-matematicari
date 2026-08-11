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
Uzak deterministički detektor DVIJE srodne klase izbora entiteta. Prva je MCQ
IZBORA ENTITETA (sve četiri
opcije su atomarni entiteti — zrak, tačka, oznaka, vrijednost), u kojem
DEKLARATIVNI dio teksta tvrdi ISTU osobinu koju pita UPITNA rečenica, i to
za TAČNO JEDAN entitet — označeni.

Dokaz je strukturan, ne leksički: traži se NAJDUŽI ZAJEDNIČKI NEPREKINUTI niz
tokena između upitne rečenice i jedne deklarativne klauze (osobina), pa se
gleda koji je entitet SUBJEKT te klauze (posljednji entitet PRIJE osobine).
Entiteti UNUTAR osobine su dio osobine, ne subjekt — zato „…BD leži između BA
i BC" ima tačno jedan subjekt (BD), a ne tri.

Druga je još uži geometrijski most dokazan živim FW-G03 na 38b7880: imenovani
ugao daje tjeme B, tekst izričito kaže da TAČKA D leži unutar/između njegovih
krakova, a opcija je ZRAK BD. Samo kad pitanje traži unutrašnji zrak koji dijeli
ugao (ne simetralu), svi entiteti su eksplicitni i TAČNO JEDNA opcija tako
izdvojena, položaj tačke D direktno dokazuje položaj zraka BD.

ŠTA OVAJ MODUL NIJE
-------------------
Nije zabrana da se tačna opcija pojavi u tekstu. Entitet smije biti podatak:
„Dati su zraci BD i BE. Koji leži između BA i BC?" je ispravan zadatak i ovdje
prolazi netaknut — tekst uvodi BD, ali ne tvrdi traženu osobinu o njemu.

Nije ni opći geometrijski zaključivač: tačka se nikad ne prevodi u pravu, duž,
kružnicu ili ravan; čak ni „tačka D je na BA" ne dokazuje da je BD simetrala.
Bez kompletne dokazane strukture modul ĆUTI. Preskočeno NIJE dokaz ispravnosti
— semantiku i dalje nezavisno sudi recenzent (doktrina cijelog projekta).
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

# Uski point→ray most. Ovi skupovi nisu jezički model nego zatvorena gramatika
# tačno dokazane klase. Generički `_entity_key` ostaje netaknut.
_ANGLE_LATEX_RE = re.compile(
    r"\\angle\s*\{?\s*([A-Za-z])\s*([A-Za-z])\s*([A-Za-z])",
    re.IGNORECASE)
_ANGLE_WORDS = frozenset({"ugao", "ugla", "uglu"})
_POINT_WORDS = frozenset({"tačka", "tačke", "tacka", "tacke",
                          "točka", "točke", "tocka", "tocke",
                          "point", "points"})
_LIE_WORDS = frozenset({"leži", "leže", "lezi", "leze",
                        "nalazi", "nalaze"})
_BETWEEN_WORDS = frozenset({"između", "izmedju"})
_INSIDE_WORDS = frozenset({"unutar"})
_RAY_WORD_PREFIXES = ("krak", "zrak", "poluprav", "ray", "arm")
_EQUAL_DIVISION_PREFIXES = ("jednak", "simetral", "polovi", "bisekt")
_POINT_ASSERTION_SPLIT_RE = re.compile(
    r"[.!?;:,]|\bdok\b|\bali\b", re.IGNORECASE)


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


def _named_angle(text):
    """Jedini eksplicitni troslovni ugao kao (krak1, tjeme, krak2)."""
    found = {tuple(part.lower() for part in match.groups())
             for match in _ANGLE_LATEX_RE.finditer(text or "")}
    tokens = _tokenize(text)
    for index, token in enumerate(tokens[:-1]):
        label = tokens[index + 1]
        if token in _ANGLE_WORDS and re.fullmatch(r"[a-z]{3}", label):
            found.add(tuple(label))
    return next(iter(found)) if len(found) == 1 else None


def _ray_option_key(option_text):
    """Eksplicitni dvoslovni zrak opcije, uključujući `krak BD`."""
    tokens = _tokenize(option_text)
    if len(tokens) == 2 and tokens[0].startswith(_RAY_WORD_PREFIXES):
        tokens = tokens[1:]
    if len(tokens) != 1 or not re.fullmatch(r"[a-z]{2}", tokens[0]):
        return None
    start, endpoint = tokens[0]
    if start == endpoint:
        return None
    return start, endpoint


def _asks_for_interior_dividing_ray(ask):
    """Pita li se za unutrašnji zrak, ali NE za simetralu/jednake dijelove."""
    tokens = _tokenize(ask)
    if not any(token.startswith(_RAY_WORD_PREFIXES) for token in tokens):
        return False
    if any(token.startswith(_EQUAL_DIVISION_PREFIXES) for token in tokens):
        return False
    has_angle = bool(_named_angle(ask))
    if has_angle and any(token in {"dijeli", "deli"} for token in tokens):
        return True
    if (any(token in _BETWEEN_WORDS for token in tokens)
            and any(token in _LIE_WORDS for token in tokens)):
        return True
    return has_angle and any(token.startswith(("unutraš", "unutras", "interior"))
                             for token in tokens)


def _point_labels_before(tokens, stop):
    """Eksplicitne oznake uz `tačka/točka/point` prije glagola relacije."""
    labels = set()
    for index in range(stop):
        if tokens[index] not in _POINT_WORDS:
            continue
        cursor = index + 1
        while cursor < stop:
            token = tokens[cursor]
            if re.fullmatch(r"[a-z]", token):
                labels.add(token)
                cursor += 1
                continue
            if token in {"i", "and"} or token in _POINT_WORDS:
                cursor += 1
                continue
            break
    return labels


def _explicit_interior_points(context, angle):
    """Tačke izričito smještene u TAJ ugao ili između TAČNO njegovih krakova."""
    first, vertex, last = angle
    boundaries = {vertex + first, vertex + last}
    angle_label = "".join(angle)
    points = set()
    for raw_clause in _POINT_ASSERTION_SPLIT_RE.split(context or ""):
        tokens = _tokenize(raw_clause)
        if not tokens:
            continue
        for relation_index, relation in enumerate(tokens):
            if relation not in _BETWEEN_WORDS | _INSIDE_WORDS:
                continue
            lie_index = next(
                (index for index in range(relation_index - 1, -1, -1)
                 if tokens[index] in _LIE_WORDS), None)
            if lie_index is None:
                continue
            labels = _point_labels_before(tokens, lie_index)
            if not labels:
                continue
            if any(token in _NEGATIONS for token in tokens[:relation_index]):
                continue
            suffix = tokens[relation_index + 1:]
            if relation in _BETWEEN_WORDS:
                has_ray_word = any(token.startswith(_RAY_WORD_PREFIXES)
                                   for token in suffix)
                stated_rays = {token for token in suffix
                               if re.fullmatch(r"[a-z]{2}", token)}
                if has_ray_word and boundaries <= stated_rays:
                    points.update(labels)
            else:
                has_angle_word = (any(token in _ANGLE_WORDS for token in suffix)
                                  or "\\angle" in raw_clause)
                if has_angle_word and angle_label in suffix:
                    points.update(labels)
    return points


def _point_to_ray_disclosure(context, ask, options, marked_index):
    """Uzak dokaz: unutrašnja tačka jednoznačno otkriva zrak iz tjemena."""
    angle = _named_angle(ask)
    if not angle or not _asks_for_interior_dividing_ray(ask):
        return ""
    points = _explicit_interior_points(context, angle)
    if not points:
        return ""
    vertex = angle[1]
    identified = set()
    for index, option in enumerate(options):
        ray = _ray_option_key(option)
        if ray and ray[0] == vertex and ray[1] in points:
            identified.add(index)
    if identified != {marked_index}:
        return ""                    # nijedan, pogrešan ili više kandidata
    return (f"{STEM_ANSWER_DISCLOSURE_CODE}: the stem explicitly places the "
            "endpoint of exactly one option ray inside the named angle, which "
            "directly identifies that ray as the requested interior divider "
            "(point-to-ray angular-interior implication)")


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

    context, ask = _split_ask(task_text)
    if not context or not ask or not _SELECTOR_RE.search(ask):
        return ""

    point_bridge = _point_to_ray_disclosure(
        context, ask, options, marked_index)
    if point_bridge:
        return point_bridge

    keys = [_entity_key(option) for option in options]
    if not all(keys) or len(set(keys)) != len(keys):
        return ""                        # nije klasa izbora atomarnih entiteta
    entities = frozenset(keys)
    marked = keys[marked_index]

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
