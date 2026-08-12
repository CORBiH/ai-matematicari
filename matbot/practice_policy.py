"""Razriješena pedagoška politika Vježbajmo turna — JEDNA istina za sve puteve.

ZAŠTO POSTOJI (audit ovlašćenja pravila, 2026-08-09): MAT-BOT je imao DVA
pedagoška autoriteta. Prompt pravila (matbot/rules.py) vezala su samo
model-turnove, a 27 determinističkih motora nosilo je ~500 lokalnih
instrukcijskih stringova koji ne konsumiraju ni razred, ni rules.py, ni
terminologiju. Produkcijski dokaz: lekcija 6. razreda (Q+) dobila je
deterministički hint „prebaci poznati član sa suprotnim predznakom" — metodu
koju kurikulum 6. razreda izričito ne poznaje — uz vidljivu negativnu desnu
stranu ($x - \\frac{1}{2} > -\\frac{3}{14}$).

PRINCIP: važenje pravila NE SMIJE zavisiti od toga da li lekcija koristi
deterministički generator, Tutora, Recenzenta, svjež zadatak, hint ili
rješenje. Politika se razrješava JEDNOM, serverski, iz pouzdanog kurikularnog
konteksta, pa je:
  • KONSUMIRAJU deterministički motori (izbor metode, konstrukcija hintova),
  • RENDERUJE rules.py u OBA prompta (Tutor i Recenzent — bajt za bajt isto),
  • PROVJERAVAJU validatori (kandidat prije objave + objava za oba puta).

PRVENSTVO (od najjačeg):
  1. matematički tačne serverske invarijante (smjer nejednakosti se mijenja
     SAMO pri množenju/dijeljenju negativnim brojem — nikad po ulozi člana);
  2. autoritativni ugovor lekcije (number_domain, shapes — data smije SUZITI
     širu razrednu politiku, npr. lekcija o POJMU jednačine u 6. razredu
     izričito deklariše `integer` domen za klasifikaciju zapisa);
  3. politika razreda (metoda rješavanja jednačina/nejednačina);
  4. politika porodice/metode; 5. zapis/terminologija/reprezentacija.

Historijski promptovi su dokazni materijal, ne automatski autoritet: pravila
„6. razred = skup Z" i „znak se okreće jer je nepoznata umanjilac/djelilac"
NAMJERNO nisu obnovljena (vidi audit, stale_rules.json).

Sve ovdje je lokalno (Python + podaci) — razrješenje i validacija politike ne
prave NIJEDAN poziv modela; deterministički strukturisani turnovi ostaju 0 SDK.
"""
import re
from dataclasses import dataclass

from matbot import mathsegments

POLICY_VERSION = "PP-1"

# ---------------------------------------------------------------------------
# METODA RJEŠAVANJA JEDNAČINA/NEJEDNAČINA PO RAZREDU
# ---------------------------------------------------------------------------

METHOD_UNKNOWN_MEMBER = "unknown_member"
METHOD_TRANSPOSITION = "transposition"

# Uloge nepoznatog člana — JEDINA formulacija metode 6. razreda u projektu.
# rules.py renderuje OVE redove u oba prompta; deterministički motor jednačina
# iz OVIH relacija izvodi hintove i rješenja. Ne postoji druga kopija.
UNKNOWN_ROLE_RELATIONS = {
    "unknown_addend": ("nepoznati sabirak",
                       "nepoznati sabirak = zbir minus poznati sabirak"),
    "unknown_minuend": ("nepoznati umanjenik",
                        "nepoznati umanjenik = razlika plus umanjilac"),
    "unknown_subtrahend": ("nepoznati umanjilac",
                           "nepoznati umanjilac = umanjenik minus razlika"),
    "unknown_factor": ("nepoznati činilac",
                       "nepoznati činilac = proizvod podijeljen poznatim činiocem"),
    "unknown_dividend": ("nepoznati djeljenik",
                         "nepoznati djeljenik = količnik puta djelilac"),
    "unknown_divisor": ("nepoznati djelilac",
                        "nepoznati djelilac = djeljenik podijeljen količnikom"),
}

# ISTA METODA, ali kao GOTOVA ŠKOLSKA REČENICA za tekst koji učenik čita.
#
# ZAŠTO POSTOJI (predizdanje, ciljana popravka): relacije iznad su zapisane u
# `=` obliku jer tako idu u prompt i u interno rezonovanje. Deterministički
# motor nejednačina ih je do sada pretvarao u prozu isječkom
# `_role_sentence(role).split("=")[1]` i kalemio dobijeni fragment u DRUGU
# rečenicu, čiji je subjekat bio interna riječ „granica“ (ime promjenljive
# `bound`). Nastajalo je npr. „pa je granica količnik puta djelilac“ — imenska
# fraza kojoj je subjekat obrisan, u rečenici o pojmu koji učenik 6. razreda
# nigdje nije sreo. To NIJE stvar ukusa nego pokvarena rečenična konstrukcija.
#
# Zato ovdje stoji potpuna rečenica po ulozi, autorski napisana, koja se u
# tekst ubacuje CIJELA. Tabela je TOTALNA (isti ključevi kao relacije iznad) —
# time parnost ključeva postaje provjerljiva invarijanta
# (`tests/test_role_sentence_integrity.py`), što s djelimičnom tabelom ne bi
# bilo moguće iskazati. Metoda je i dalje veza među članovima (6. razred):
# nijedna rečenica ne pominje prebacivanje preko znaka jednakosti.
#
# `UNKNOWN_ROLE_RELATIONS` se NE dira — `unknown_member_rule_lines()` renderuje
# isključivo nju, pa se prompt Tutora i Recenzenta ne mijenja ni za jedan znak.
UNKNOWN_ROLE_EXPLANATIONS = {
    "unknown_addend":
        "nepoznati sabirak dobiješ tako što od zbira oduzmeš poznati sabirak",
    "unknown_minuend":
        "nepoznati umanjenik dobiješ tako što razlici dodaš umanjilac",
    "unknown_subtrahend":
        "nepoznati umanjilac dobiješ tako što od umanjenika oduzmeš razliku",
    "unknown_factor":
        "nepoznati činilac dobiješ tako što proizvod podijeliš poznatim činiocem",
    "unknown_dividend":
        "nepoznati djeljenik dobiješ tako što količnik pomnožiš djeliocem",
    "unknown_divisor":
        "nepoznati djelilac dobiješ tako što djeljenik podijeliš količnikom",
}

_EQUATION_METHOD_BY_GRADE = {
    6: METHOD_UNKNOWN_MEMBER,
    7: METHOD_TRANSPOSITION,
    8: METHOD_TRANSPOSITION,
    9: METHOD_TRANSPOSITION,
}

# balance_both_sides: „primijeni operaciju na obje strane" — u 6. razredu
# jednako zabranjen kao prebacivanje (kurikulum traži vezu među članovima).
_FORBIDDEN_METHODS_BY_GRADE = {
    6: (METHOD_TRANSPOSITION, "balance_both_sides"),
    7: (), 8: (), 9: (),
}

# ---------------------------------------------------------------------------
# VIDLJIVI BROJEVNI DOMEN
# ---------------------------------------------------------------------------
# Autoritet je ugovor LEKCIJE (number_domain), ne gola pretpostavka razreda:
# `natural` i `rational_nonneg` su nenegativni po definiciji; `decimal` je
# nenegativan u 6. razredu (Q+ kurikulum — decimalni zapis razlomka). Lekcija
# koja izričito deklariše predznačen domen (integer/rational) time SUŽAVA
# razrednu politiku i vidljivi minus joj je dozvoljen. Uzorkovanje nenegativnog
# RJEŠENJA ne dokazuje da su svi PRIKAZANI brojevi nenegativni (živi slučaj
# `bound - offset`), pa vidljivi domen ima vlastitu provjeru ispod.

_ALWAYS_NONNEG_DOMAINS = frozenset({"natural", "rational_nonneg"})

VISIBLE_DOMAIN_NONNEGATIVE = "nonnegative"
VISIBLE_DOMAIN_ANY = "any"

# ---------------------------------------------------------------------------
# NAPREDNE OPERACIJE VAN KURIKULUMA 6-9
# ---------------------------------------------------------------------------
# Obnovljeno IZGUBLJENO pravilo (74a6cc0 brisanje repozitorija): osnovna škola
# u BiH nema trigonometriju ni logaritme. Ovo je KURIKULARNA granica, ne
# parser-zabrana: mathsafe i dalje zna prikazati \sin (učenikov unos se nikad
# ne sakati) — server samo ne dozvoljava da ih VJEŽBAJMO UVEDE u zadatak,
# opcije ili rješenje lekcije čija ih politika ne dozvoljava.
ADVANCED_SCOPE_OPERATIONS = ("sin", "cos", "tan", "tg", "ctg", "cot", "log", "ln")

_ADVANCED_MATH_RE = re.compile(
    r"\\(?:sin|cos|tan|cot|sec|csc|log|ln)\b"
    r"|\b(?:sin|cos|tan|tg|ctg|log|ln)\s*\("
    r"|\b(?:sin|cos|tan|tg|ctg|log|ln)\b",
)
_ADVANCED_PROSE_RE = re.compile(
    r"\b(?:sinus\w*|kosinus\w*|tangens\w*|kotangens\w*|logarit\w*)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# KURIKULARNA SPOSOBNOST RAZREDA — ZAPIS KOJI RAZRED JOŠ NIJE UPOZNAO
# ---------------------------------------------------------------------------
# ŽIVI BLOKATOR IZDANJA (FINAL40, FW-G06 na lekciji 6. razreda o simetrali
# ugla i konstrukciji): objavljen je zadatak „Jednakostraničan trougao ima
# stranicu $6$. Kolika je udaljenost centra upisane kružnice od svake
# stranice?" s označenim $\\sqrt{3}$. Matematika je TAČNA i nijedan postojeći
# validator nije imao šta prijaviti — ali kvadratni korijen 6. razred nije ni
# sreo, pa zadatak nije rješiv sredstvima koja učenik ima.
#
# Ovo je ISTA vrsta granice kao ADVANCED_SCOPE_OPERATIONS iznad (trigonometrija
# i logaritmi nisu gradivo 6-9), samo finija: nije „van osnovne škole" nego
# „još nije u ovom razredu". Zato dijeli isti mehanizam — jednom razriješena
# politika koju konzumiraju prompt, deterministički motor i validatori.
#
# KURIKULARNI DOKAZ (data/topics.json, kanonski izvor lekcija):
#   • 6. razred (119 lekcija) i 7. razred (122): NIJEDNA lekcija ne uvodi
#     korijen, iracionalne brojeve ni Pitagorinu teoremu — nijedna njihova
#     oblast ih ni ne imenuje;
#   • korijen se uvodi u 8. razredu, i to vlastitom oblašću („Realni brojevi,
#     korijeni i stepeni", s lekcijama o iracionalnim brojevima, kvadratnom
#     korijenu nenegativnog racionalnog broja, korijenu proizvoda i količnika
#     i približnim vrijednostima korijena); Pitagorina teorema je takođe
#     8. razred.
#
# ZAŠTO ZABRANA VAŽI SAMO ZA 6. RAZRED, iako kurikulum korijen uvodi tek u 8:
# mjereno nad 2698 zamrznutih objavljenih paketa svih kampanja —
#   • 6. razred: TRI paketa s korijenom u cijelom korpusu, i sva tri su ISTI
#     ovaj defekt (FW-G06, ponovljen u tri odvojene FINAL40 kampanje:
#     c04d2a1, c17538a, 2fe5636). Nijedan legitiman slučaj ne postoji;
#   • 7. razred: 14 paketa na 4 lekcije, a 37 od 122 lekcije 7. razreda već
#     dobija geometrijski blok prompta koji SAM uči formule s korijenom
#     (matbot/geometry_rules.py). Zabrana u 7. razredu bi protivrječila
#     zatečenom promptu i njen učinak nije dokazan.
# Granica se zato postavlja tačno tamo gdje je dokazana. Šire važenje je
# posao kurikularne faze, ne popravke blokatora.
RADICAL_CURRICULUM_GRADE = 8
_RADICAL_FORBIDDEN_GRADES = (6,)


def radical_notation_allowed_for_grade(grade):
    """JEDINA tabela razred → smije li zapis korijena.

    Zove je i `resolve()` (za polje politike) i `rules.py` (da geometrijski
    blok prompta ne bi ponudio formulu koju ista politika zabranjuje). Dvije
    kopije ovog odgovora su upravo ono što je proizvelo protivrječnost koju
    ova funkcija zatvara."""
    try:
        return int(grade) not in _RADICAL_FORBIDDEN_GRADES
    except (TypeError, ValueError):
        return True

# Korijen se traži SAMO u matematičkim segmentima: prozna riječ „korijen" u
# rečenici („korijen jednačine") nije zapis i nikad nije pogodak.
_RADICAL_RE = re.compile(r"\\sqrt\b|\\radical\b|√")

# ---------------------------------------------------------------------------
# ZABRANJENA METODSKA PROZA (samo kad politika zabranjuje prebacivanje)
# ---------------------------------------------------------------------------
# Stemovi hvataju sve oblike: prebaci/prebacimo/prebacuj/prebacivanjem...
# „obje strane" pokriva i „na obje strane" i „s obje strane". Provjera je
# UVIJEK uslovljena politikom lekcije (6. razred, jednačine) — u 7-9. razredu
# ista proza je legitimna školska metoda i nikad se ne skenira.
_FORBIDDEN_METHOD_PROSE_RE = re.compile(
    r"prebac|na drugu stranu|obje strane|suprotnim predznakom",
    re.IGNORECASE,
)

# Vidljiv NEGATIVAN literal u matematičkom segmentu: minus koji otvara segment
# ili slijedi relaciju/operator/zagradu, neposredno ispred broja ili \frac.
# Binarno oduzimanje (`a - b`) NIKAD ne pogađa: tamo minus slijedi operand.
_NEGATIVE_LITERAL_RE = re.compile(
    r"(?:^|[=<>(+,:]|\\cdot\b|\\le[q]?\b|\\ge[q]?\b)\s*-\s*(?=\d|\\frac)"
)


@dataclass(frozen=True)
class ResolvedPracticePolicy:
    """Nepromjenjiva, jednom razriješena politika jednog (grade, lesson) para.

    Tutor prompt, Recenzent prompt, deterministički motor i validatori dobiju
    OVAJ ISTI objekat — divergencija dvije kopije istog pravila (uzrok
    grade-6 defekta) time postaje strukturno nemoguća."""

    policy_version: str
    grade: int
    lesson_id: str
    family_id: str
    equation_method: str          # unknown_member | transposition
    forbidden_method_ids: tuple
    visible_number_domain: str    # nonnegative | any
    advanced_scope_allowed: tuple # () — buduća lekcija smije izričito dozvoliti
    arrow_method_lesson: bool     # metoda strelica je školska metoda lekcije
    # Kurikularna sposobnost razreda: smije li objavljeni sadržaj uopšte
    # koristiti zapis korijena (vidi RADICAL_CURRICULUM_GRADE iznad).
    radical_notation_allowed: bool = True
    # Metodska proza se skenira SAMO nad lekcijama o jednačinama/nejednačinama
    # kojima je politika zabranila prebacivanje: riječ „prebaci“ u lekciji o
    # mjernim jedinicama ili konstrukciji nije metodski prekršaj i ne smije
    # oboriti paket (uska primjena — isti princip kao geometry_scope).
    scan_method_prose: bool = False


def resolve(grade, lesson_id="", family_id="", parameters=None,
            lesson_title="", oblast=""):
    """Razriješi politiku iz POUZDANIH serverskih činjenica (nikad iz proze).

    `parameters` su parametri kompajliranog semantičkog ugovora lekcije ili
    None — ugovor je lekcijski autoritet koji SUŽAVA razrednu politiku."""
    parameters = parameters or {}
    grade = int(grade) if grade else 6
    number_domain = parameters.get("number_domain") or ""

    if number_domain in _ALWAYS_NONNEG_DOMAINS:
        visible = VISIBLE_DOMAIN_NONNEGATIVE
    elif number_domain == "decimal" and grade == 6:
        visible = VISIBLE_DOMAIN_NONNEGATIVE
    else:
        visible = VISIBLE_DOMAIN_ANY

    haystack = f"{oblast or ''} {lesson_title or ''}".lower()
    arrow = ("proporcionalnost" in haystack
             and "funkcij" not in haystack and "grafik" not in haystack)

    forbidden = _FORBIDDEN_METHODS_BY_GRADE.get(grade, ())
    equation_lesson = (
        family_id in ("linear_equation_direct", "simple_quadratic_equation")
        or "jedna" in haystack)  # pokriva i „nejedna...“ (sadrži „jedna“)

    return ResolvedPracticePolicy(
        policy_version=POLICY_VERSION,
        grade=grade,
        lesson_id=lesson_id or "",
        family_id=family_id or "",
        equation_method=_EQUATION_METHOD_BY_GRADE.get(
            grade, METHOD_UNKNOWN_MEMBER),
        forbidden_method_ids=forbidden,
        visible_number_domain=visible,
        advanced_scope_allowed=(),
        arrow_method_lesson=arrow,
        radical_notation_allowed=radical_notation_allowed_for_grade(grade),
        scan_method_prose=bool(forbidden) and equation_lesson,
    )


def resolve_for_context(context):
    """Politika iz server-vlasničkog LessonContexta (jedina runtime tačka)."""
    contract = getattr(context, "semantic_contract", None)
    return resolve(
        grade=getattr(context, "grade", 6),
        lesson_id=getattr(context, "topic_id", ""),
        family_id=getattr(contract, "family_id", "") if contract else "",
        parameters=getattr(contract, "parameters", None) if contract else None,
        lesson_title=getattr(context, "title", ""),
        oblast=getattr(context, "oblast", ""),
    )


# ---------------------------------------------------------------------------
# RENDER ZA PROMPT — rules.py gradi razredni blok IZ ovih redova, pa Tutor i
# Recenzent (koji dijele build_shared_math_rules) dobiju identičan tekst.
# ---------------------------------------------------------------------------

def unknown_member_rule_lines():
    """Redovi metode nepoznatog člana, tačno kako idu u prompt 6. razreda."""
    return tuple(f"  • {relation}"
                 for _, relation in UNKNOWN_ROLE_RELATIONS.values())


def advanced_scope_rule_text():
    """Kurikularna granica naprednih operacija — ide u domenska pravila."""
    return (
        "- Trigonometrijske funkcije (sin, cos, tg) i logaritmi NISU dio "
        "kurikuluma 6-9. razreda: NIKAD ih ne uvodi u zadatak, opcije, hint ni "
        "rješenje. Ako učenik sam spomene takav pojam, smiješ kratko reći da to "
        "nije gradivo osnovne škole — bez rješavanja tim metodama.\n"
    )


def radical_capability_rule_text(policy=None):
    """Granica zapisa korijena za razred — prazno kad razred korijen poznaje."""
    if policy is not None and policy.radical_notation_allowed:
        return ""
    return (
        "- Kvadratni korijen ($\\sqrt{\\;}$) NIJE gradivo ovog razreda "
        f"(uvodi se u {RADICAL_CURRICULUM_GRADE}. razredu): NIKAD ga ne "
        "uvodi u tekst zadatka, opcije, hint ni rješenje, ni kao međukorak. "
        "Ako bi tačan odgovor tražio korijen, zadatak nije za ovaj razred — "
        "napravi drugi zadatak iste lekcije čiji je odgovor izraziv bez "
        "korijena (pojmovno pitanje, konstrukcijski korak, svojstvo).\n")


def grade_capability_repair_text():
    """Recept za recenzenta uz `GRADE_CAPABILITY_CODE` — JEDNA istina.

    Živi u OVOM modulu, uz samu granicu, iz dva razloga: da recept i pravilo
    ne mogu da se raziđu, i da motor paketa (`matbot/tutor/package_preflight.py`)
    ostane bez ijednog konkretnog matematičkog zapisa — što njegova
    arhitektonska kapija i traži."""
    return (
        f"For `{GRADE_CAPABILITY_CODE}` the named field uses notation this "
        "GRADE has not met yet — a square root, which the curriculum "
        f"introduces only in grade {RADICAL_CURRICULUM_GRADE}. The mathematics "
        "may be perfectly correct and still be unsolvable with the tools this "
        "student has. Rounding the root away to a decimal does NOT fix it: the "
        "derivation itself is out of grade. REPLACE THE TASK with one that "
        "exercises the SAME lesson but whose answer is expressible without any "
        "root — a conceptual property question, a recognition question, or a "
        "construction step. If the lesson's content cannot be exercised at "
        "this grade without a root, return `fail_closed`. ")


def modular_curriculum_rule_text():
    """Obnovljeno IZGUBLJENO pravilo modularnog kurikuluma (BiH specifično)."""
    return (
        "- Nastavni planovi u BiH se razlikuju po kantonima/entitetima i "
        "programima: NIKAD ne reci učeniku da „kasni“, da je „trebao već "
        "znati“ ili da je gradivo „odavno pređeno“ — redoslijed tema nije "
        "isti u svakoj školi. Jednostavno objasni ono što se pita.\n"
    )


# ---------------------------------------------------------------------------
# DETERMINISTIČKE PROVJERE POLITIKE (bez modela, bez izuzetaka)
# ---------------------------------------------------------------------------

def _math_and_prose(text):
    """(matematički segmenti, prozni segmenti) — dijeljeni tokenizator."""
    math_parts, prose_parts = [], []
    for kind, content in mathsegments.tokenize_math(text or ""):
        if kind == mathsegments.TEXT:
            prose_parts.append(content)
        else:
            math_parts.append(content)
    return math_parts, prose_parts


def find_visible_negative_literals(text):
    """Vidljivi negativni literali u matematičkim segmentima teksta.

    Vraća listu pogođenih isječaka (interna dijagnostika, nikad učeniku).
    Uzorak je NAMJERNO uzak — minus koji otvara segment ili slijedi
    relaciju/operator — pa binarno oduzimanje nikad nije pogodak i provjera
    smije raditi i nad model-paketom bez lažnih pozitiva."""
    found = []
    math_parts, _ = _math_and_prose(text)
    for part in math_parts:
        for match in _NEGATIVE_LITERAL_RE.finditer(part):
            found.append(part[match.start():match.start() + 24])
    return found


def find_forbidden_method_prose(text):
    """Transpoziciona/balansna proza — smisleno SAMO uz politiku koja je
    zabranjuje; pozivalac je dužan provjeriti policy prije poziva."""
    _, prose_parts = _math_and_prose(text)
    found = []
    for part in prose_parts:
        match = _FORBIDDEN_METHOD_PROSE_RE.search(part)
        if match:
            found.append(match.group(0).lower())
    return found


def find_radical_notation(text):
    """Zapisi korijena u matematičkim segmentima teksta (interna dijagnostika).

    Skenira se SAMO matematika: prozna riječ „korijen" nije zapis, a učenikova
    poruka nikad ne prolazi ovuda (vidi `find_advanced_scope_violations`)."""
    found = []
    math_parts, _ = _math_and_prose(text)
    for part in math_parts:
        found.extend(match.group(0) for match in _RADICAL_RE.finditer(part))
    return found


def find_advanced_scope_violations(text, policy=None):
    """Napredne operacije koje politika lekcije ne dozvoljava.

    Skenira SAMO sadržaj koji server/model proizvodi (zadatak, opcije,
    rješenje, hintovi) — učenikova poruka se nikad ne skenira ovim putem."""
    allowed = set(getattr(policy, "advanced_scope_allowed", ()) or ())
    found = []
    math_parts, prose_parts = _math_and_prose(text)
    for part in math_parts:
        for match in _ADVANCED_MATH_RE.finditer(part):
            token = match.group(0).lstrip("\\").rstrip("(").strip()
            if token not in allowed:
                found.append(token)
    for part in prose_parts:
        for match in _ADVANCED_PROSE_RE.finditer(part):
            found.append(match.group(0).lower())
    return found


# Interni kodovi (CLAUDE.md pravilo 7: samo logovi/recenzent, nikad browser).
FORBIDDEN_METHOD_CODE = "forbidden_method_language"
VISIBLE_DOMAIN_CODE = "visible_value_outside_domain"
ADVANCED_SCOPE_CODE = "advanced_scope_violation"
METHOD_PROVENANCE_CODE = "method_provenance_mismatch"
# Zapis/mašinerija koju izabrani RAZRED još nije upoznao (FINAL40 FW-G06).
# Namjerno NIJE `wrong_math` ni `advanced_scope_violation`: matematika je
# tačna i nije van osnovne škole — samo je van ovog razreda.
GRADE_CAPABILITY_CODE = "grade_capability_mismatch"


def text_policy_failures(policy, text):
    """Kodovi prekršaja politike za JEDNU učeniku vidljivu površinu."""
    if policy is None or not text:
        return ()
    failures = []
    if (policy.scan_method_prose
            and METHOD_TRANSPOSITION in policy.forbidden_method_ids
            and find_forbidden_method_prose(text)):
        failures.append(FORBIDDEN_METHOD_CODE)
    if (policy.visible_number_domain == VISIBLE_DOMAIN_NONNEGATIVE
            and find_visible_negative_literals(text)):
        failures.append(VISIBLE_DOMAIN_CODE)
    if find_advanced_scope_violations(text, policy):
        failures.append(ADVANCED_SCOPE_CODE)
    if not policy.radical_notation_allowed and find_radical_notation(text):
        failures.append(GRADE_CAPABILITY_CODE)
    return tuple(failures)


def package_policy_failures(policy, question, option_texts=(), hints=(),
                            solution="", method_id=""):
    """Kodovi prekršaja za CIO deterministički kandidat-paket, prije objave.

    Metodska proza se skenira nad SVIM površinama (zadatak, hintovi,
    rješenje) — kontinuitet metode: hint ne smije učiti metodu koju zadatak
    ne smije koristiti. `method_id` je strukturna provenijencija generatora:
    zabranjena metoda pada i kad joj proza ne oda nijednu riječ."""
    if policy is None:
        return ()
    failures = []
    if method_id and method_id in policy.forbidden_method_ids:
        failures.append(METHOD_PROVENANCE_CODE)
    surfaces = [question or "", solution or ""]
    surfaces.extend(option_texts or ())
    surfaces.extend(hints or ())
    for surface in surfaces:
        for code in text_policy_failures(policy, surface):
            if code not in failures:
                failures.append(code)
    return tuple(failures)
