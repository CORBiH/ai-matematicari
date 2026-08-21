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
    # „faktor“, ne „činilac“: KS_2018 (plan koji projekat prati) koristi
    # „faktor“ 56 puta, a „činilac“ nijednom — taj oblik dolazi samo iz
    # RS_2014. Vidi matbot/terminology.py, pravilo 11.
    "unknown_factor": ("nepoznati faktor",
                       "nepoznati faktor = proizvod podijeljen poznatim faktorom"),
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
        "nepoznati faktor dobiješ tako što proizvod podijeliš poznatim faktorom",
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
# ZAŠTO JE ZABRANA RANIJE VAŽILA SAMO ZA 6. RAZRED — i zašto više ne važi tako.
#
# Zatečena granica je bila `(6,)`, uz izričito obrazloženje: mjereno nad 2698
# zamrznutih objavljenih paketa, 6. razred je imao TRI paketa s korijenom i sva
# tri su bila ISTI defekt (FW-G06), dok je 7. razred imao 14 paketa na 4
# lekcije, a 37 od 122 lekcije 7. razreda već dobija geometrijski blok prompta
# koji SAM uči formule s korijenom. Zaključak je tada bio da bi zabrana u
# 7. razredu „protivrječila zatečenom promptu“ i da joj „učinak nije dokazan“.
#
# OBA RAZLOGA SU SADA RIJEŠENA, mjerenjem, ne mišljenjem:
#
#   1) UČINAK JE DOKAZAN. Živi cross-curriculum audit (120 poziva, slučaj
#      G7-E4, lekcija 7. razreda o povrsini kvadrata „Površina pravougaonika i kvadrata“) pitao je
#      koliko iznosi stranica kvadrata površine $20\,\text{cm}^2$ i dobio
#      $a=\sqrt{20}=2\sqrt{5}\approx4,47$. Matematika je tačna, ali zapis
#      7. razred nije sreo — tačno klasa FW-G06, samo jedan razred više.
#
#   2) PROTIVRJEČNOST JE, MEĐUTIM, STVARNA — i zato granica OSTAJE na (6,).
#      Geometrijska strana jeste bezopasna: izmjereno nad svih 122 lekcije
#      7. razreda, filter izostavlja isključivo formule koje traže Pitagorinu
#      teoremu ($d=\sqrt{a^2+b^2}$, $a\sqrt{2}$, Heron, pravilni šestougao),
#      dakle gradivo 8. razreda. ALI postoji druga, legitimna upotreba znaka
#      koju paušalna zabrana ruši: lekcija 7. razreda o SKUPU RACIONALNIH
#      BROJEVA Q nosi kanonski ishod „shvatiti potrebu PROŠIRIVANJA skupa racionalnih
#      brojeva“, a to se predaje tako što se pokaže broj koji u Q NIJE — dakle
#      $\sqrt{2}$ ili $\pi$ kao NEPRIMJER. Zatečeni paketi to i rade (14 paketa
#      na 4 lekcije), a `tests/test_fast_single_call_route.py` upravo takav
#      paket koristi kao svoj standardni fixture.
#
#      Razlika koju paušalna zabrana ne vidi: 7. razred smije PRIKAZATI
#      iracionalan broj kao objekat prepoznavanja, ali ne smije RAČUNATI
#      korijen kao postupak. `find_radical_notation` je regex nad matematičkim
#      segmentima i tu razliku ne može napraviti, pa bi zabrana oborila i
#      ispravan zadatak o skupu Q.
#
# ZAKLJUČAK: nalaz G7-E4 je stvaran (7. razred je dobio $\sqrt{20}$ kao redovan
# postupak), ali blanket-zabrana nije njegov lijek. Pravi lijek traži novu
# razliku u politici — „zapis smije biti prikazan“ naspram „odgovor ne smije
# tražiti korijen“ — i to je zaseban, mjeren zahvat, ne popravka ove veličine.
# Do tada granica ostaje tamo gdje je dokazano bezopasna.
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


def equation_method_rule_text(policy=None):
    """Metoda rješavanja/objašnjavanja jednačina, upućena SAMOM OBJAŠNJAVANJU.

    ZAŠTO POSTOJI (forenzički trag modova, 2026-08-20): tabela relacija iznad
    je do sada u prompt ulazila samo kroz razredni blok `rules._GRADE_RULES[6]`,
    i to u dva oblika koja su joj oduzimala snagu baš tamo gdje je najpotrebnija:
    formulisana je kao pravilo o tome kako se jednačina „rješava“ (a ne kako se
    OBJAŠNJAVA), i vezana zagradom za JEDNU oblast („Jednačine, nejednačine i
    izrazi u Q+“) iako je ograničenje razredno. Šestaš koji jednačinu spomene u
    lekciji iz druge oblasti dobijao je pravilo koje doslovno izgleda kao da se
    na njegovo pitanje ne odnosi.

    Quick je ISTI nalaz već imao i riješio ga RUČNO PREPISANOM tabelom
    (`prompts._QUICK_GRADE_METHOD`) — a ta kopija se od kanonske već razišla:
    nije imala ulogu `unknown_minuend`. Tačno to je klasa kvara zbog koje ovaj
    modul postoji, pa formulacija upućena objašnjavanju živi OVDJE, gradi se iz
    `UNKNOWN_ROLE_RELATIONS` i nema drugu kopiju.

    Razred čija politika metodu nepoznatog člana ne traži (7-9, `transposition`)
    ne dobija nijedan dodatni red — prompt mu ostaje bajt za bajt kao prije."""
    if policy is None or getattr(policy, "equation_method", "") != METHOD_UNKNOWN_MEMBER:
        return ""
    grade = getattr(policy, "grade", 6)
    return (
        f"METODA ZA {grade}. RAZRED (obavezno kad OBJAŠNJAVAŠ, pokazuješ "
        "primjer ili rješavaš jednačinu ili nejednačinu — pravilo je RAZREDNO i "
        "važi za svaku takvu poruku učenika, bez obzira na to iz koje je oblasti "
        "izabrana lekcija):\n"
        "- PRVI korak je uvijek: odredi KOJE MJESTO nepoznata zauzima u računskoj "
        "operaciji (koji je član nepoznat), imenuj tu ulogu učeniku, pa primijeni "
        "VEZU MEĐU ČLANOVIMA RAČUNSKIH OPERACIJA:\n"
        + "\n".join(unknown_member_rule_lines()) + "\n"
        "- Postupak NIKAD ne objašnjavaj „prebacivanjem preko znaka jednakosti“, "
        "riječima „prebaci na drugu stranu“ ni „uradi isto s obje strane“ — to je "
        "metoda starijih razreda i učenik je nije učio; ne pominji je ni kao "
        "usputnu alternativu.\n"
        "- Ne uvodi negativne brojeve samo da bi izveo takvo prebacivanje: "
        "postupak ovog razreda ostaje unutar nenegativnih brojeva.\n"
        "- Isto važi i za nejednačinu: prvo ime uloge nepoznatog člana, pa ista "
        "veza među članovima, pa granica rješenja.\n"
    )


def unknown_member_role_mentions(text):
    """Uloge nepoznatog člana koje se STVARNO pominju u tekstu (uzlazni signal).

    ZAŠTO POSTOJI: `find_forbidden_method_prose` je LEKSIČKI detektor i ne
    razlikuje „Prebacimo član na drugu stranu“ od „NE prebacujemo član na drugu
    stranu“ — oba sadrže stem „prebac“. Proširivanje tog uzorka negacijom bi
    OSLABILO živu kapiju Vježbajma (rečenica „Ne zaboravi: prebacimo član…“
    prošla bi), pa se uzorak namjerno NE dira.

    Umjesto toga se mjeri NEZAVISAN, POZITIVAN signal: imenuje li tekst uopšte
    ijednu ulogu nepoznatog člana. Kombinacija „leksički pogodak + nijedna
    uloga“ i „leksički pogodak + imenovana uloga“ razdvaja te dvije rečenice u
    dijagnostici, bez ijednog novog pravila i bez ijedne nove tabele — imena
    uloga se čitaju iz `UNKNOWN_ROLE_RELATIONS`.

    GRANICA MJERENJA, izričito: traže se KANONSKI oblici (puno ime uloge i goli
    nominativ imenice). Kosi padeži („umanjiocem“, „sabirka“) se NE hvataju, pa
    je rezultat DONJA granica — signal je dijagnostika, nikad dokaz."""
    lowered = (text or "").lower()
    found = []
    for key, (role_name, _relation) in UNKNOWN_ROLE_RELATIONS.items():
        noun = role_name.split()[-1]
        if role_name.lower() in lowered or re.search(
                r"\b" + re.escape(noun) + r"\b", lowered):
            found.append(key)
    return tuple(found)


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

# Kodovi iz `text_policy_failures` razvrstani po TIPU DOKAZA, ne po težini.
# STRUKTURNI kod dolazi iz matematičkog segmenta (zapis koji tamo ili jeste ili
# nije); LEKSIČKI kod dolazi iz proze i zato može pogoditi i rečenicu koja isto
# pravilo zapravo UČI (vidi `unknown_member_role_mentions`). Podjela postoji da
# bi dijagnostika mogla reći KOJU vrstu dokaza ima — nijedan kod ovim ne postaje
# blaži i nijedna postojeća kapija ovo ne konsumira.
STRUCTURAL_POLICY_CODES = frozenset({VISIBLE_DOMAIN_CODE, GRADE_CAPABILITY_CODE})
LEXICAL_POLICY_CODES = frozenset({FORBIDDEN_METHOD_CODE, ADVANCED_SCOPE_CODE})


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
