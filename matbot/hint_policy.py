"""SERVER-VLASNIČKA POLITIKA POMOĆI (arhitektonska Faza 2).

ZAŠTO POSTOJI. Do Faze 2 put pomoći (hint / „uradi ga ti“) nije imao nijednog
nezavisnog semantičkog vlasnika: jedan poziv modela, bez recenzenta, bez
preflighta, bez ugovora lekcije, a jedini orakl curenja (`feedback.leaks_answer`)
traži VRIJEDNOST. Dva živa nalaza dokazuju cijelu klasu:

  FW-X03 (final40_c17538a; lekcija se namjerno ne navodi — ovaj modul ne smije
          poznavati nijedan ID lekcije)
      • nagovještaj 1 je DOSLOVNO izrekao definiciju koja glasi kao označena
        opcija — `hint_no_leak` je vratio PASS jer odgovor nije bio vrijednost
        nego REČENICA;
      • nagovještaj 3 je do TAČNOG zaključka došao preko NETAČNE međutvrdnje,
        a vrh ljestvice je bio svježa, nikad provjerena proza modela.

  TR-B1 (targeted_finalfix_b5692c0, ista porodica lekcija)
      • nagovještaj 1 je isti kriterij procurio kao PARAFRAZU u drugom padežu,
        koju mjerenje po tačnim tokenima ne dosiže (Faza 0 je to izričito
        zamrznula kao poznatu slijepu tačku);
      • nagovještaj 2 je lekciji 9. razreda osnovne škole ponudio parametarski
        oblik prave i skalarni proizvod — matematiku koje nema ni u zadatku ni
        u provjerenom rješenju.

ZAKLJUČAK ARHITEKTURE: detekcija poslije čina ne može zatvoriti ni jedan od ta
dva slučaja (drugi je dokazano van dosega poređenja tokena). Zato ovaj modul
NOSI KONSTRUKCIJU, a mjerači su drugi sloj:

  1. KLASIFIKACIJA ZADATKA iz SERVER-VLASNIČKE strukture (oblik objavljenih
     opcija, ništa iz proze modela): računski zadatak ili tvrdnja/prepoznavanje.
  2. ZA TVRDNJU/PREPOZNAVANJE nagovještaje 1 i 2 SASTAVLJA SERVER. Šablon
     NIKAD ne prepisuje ni jedno slovo iz ponuđenih opcija, pa je otkrivanje
     označene tvrdnje (i doslovno i parafrazom) nemoguće PO KONSTRUKCIJI.
  3. VRH LJESTVICE i puno rješenje se sastavljaju iz PROVJERENOG artefakta
     objave (`solution` i `expected_answer` koje je recenzent odobrio i koje su
     prošle sve serverske validatore) — nikad iz svježe proze modela.
  4. Za računski zadatak model ostaje autor nagovještaja 1 i 2 (ta ljestvica
     radi i mjeri se determinističkim vrijednosnim oraklom), ali svaki njegov
     tekst prolazi kroz uske deterministe ovog modula.

ŠTA OVAJ MODUL NIJE: nije prover, nije semantički sud o matematici, ne poziva
model, ne zna nijedan ID lekcije i ne uvozi nijedan orkestracijski modul. Sve
funkcije su čiste, pa ih evaluator (`tools/practice_eval/`) poziva DOSLOVNO iste
— proizvod i mjerenje se ne mogu razići.
"""
import re
from dataclasses import dataclass

from matbot.mathsegments import TEXT, math_contents, tokenize_math

# ---------------------------------------------------------------------------
# 1. KLASA ZADATKA — server-vlasnička, iz OBLIKA objavljenih opcija
# ---------------------------------------------------------------------------
# Odluka se izvodi ISKLJUČIVO iz onoga što je server sam objavio (sanitizovani
# tekstovi opcija i serverski `correct_option_id`), nikad iz `task_type`,
# `answer_type` ni bilo kojeg polja koje o sebi deklariše model: klasa bira
# ljestvicu pomoći, pa model ne smije birati vlastiti režim nadzora.
#
# ZAŠTO `task_signature.answer_type` NIJE OVLAŠĆEN (provjereno u kodu, ne
# pretpostavljeno): `validate_task` traži samo da `task_family` i
# `operation_or_relation` nisu prazni — VRIJEDNOST polja `answer_type` nijedan
# serverski validator ne provjerava, a `checks.task_signature_consistent` je
# samoprijava modela koja po `matbot/tutor/reviewer_authority.py` nikad nije
# dokaz. Polje je dakle model-proza u strukturisanom omotu i ne smije birati
# režim nadzora. Isto važi za `task_type`.
#
# ZAŠTO NI PORODICA LEKCIJE (`primary_family`, naslov) NIJE KLASIFIKATOR: ona
# opisuje TIPIČAN oblik zadatka lekcije, a ne oblik OVOG odgovora. Ista lekcija
# smije proizvesti i računski i propozicioni zadatak — ta invarijanta je
# izričito zaključana testom i ne smije se pokvariti da bi talas bio „uredniji“.
COMPUTATIONAL = "computational"
PROPOSITIONAL = "propositional"
UNCERTAIN = "uncertain"

TASK_CLASSES = (COMPUTATIONAL, PROPOSITIONAL, UNCERTAIN)

# KALIBRACIJA (izmjereno nad zamrznutim živim paketima i nad svim porodicama iz
# tests/conftest.py): označena opcija s najmanje toliko PROZNIH riječi izvan
# `$…$` je iskaz, ne vrijednost. Ista granica u drugom obliku već je kalibrisana
# u Fazi 0 (`hintsemantics.MIN_PROPOSITION_TOKENS`).
MIN_PROPOSITION_PROSE_WORDS = 5
# Vrijednosni oblik smije nositi najviše toliko PROZNIH riječi (mjerna jedinica
# ili imenica uz broj: „$12$ cm“, „$5$ učenika“). Sam po sebi ovaj prag NIJE
# dokaz vrijednosti — vidi `value_shaped`.
MAX_VALUE_PROSE_WORDS = 2
# Klasa „tvrdnja“ traži da oblik nosi CIJELI skup, ne samo označenu opciju:
# inače bi jedna duga tačna opcija među tri brojevne promijenila režim.
MIN_PROPOSITION_OPTIONS = 3

_WORD_RE = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def _prose(text):
    """Samo dio IZVAN `$…$` / `$$…$$` — matematika se nikad ne mjeri jezički."""
    return "".join(content for kind, content in tokenize_math(text or "")
                   if kind == TEXT)


def prose_word_count(text):
    """Broj proznih riječi (bez cifara, bez jednoslovnih oznaka) izvan matematike."""
    return len(_WORD_RE.findall(_prose(text)))


# ---------------------------------------------------------------------------
# 1a. POZITIVAN DOKAZ VRIJEDNOSNOG OBLIKA (hardening prije živog talasa)
# ---------------------------------------------------------------------------
# NALAZ REVIEWA (Problem A): prvi oblik klasifikacije je govorio „najviše dvije
# prozne riječi ⇒ RAČUNSKI“. To je bilo NEDOVOLJNO: kratka SIMBOLIČKA opcija ne
# dokazuje da je odgovor izračunata vrijednost. Legitimni zadaci prepoznavanja
# imaju upravo takve opcije:
#
#     $p \perp \alpha$   $p \parallel \alpha$   $p \subset \alpha$
#     $A \subset B$      $A = B$                $A \cap B = \varnothing$
#     $\mathbb{N}$       $\mathbb{Z}$           $\alpha + \beta = 180^\circ$
#
# Sve bi to prošlo kao RAČUNSKI i dobilo model-autorske nagovještaje 1 i 2 —
# dakle upravo klasu koju je Faza 2 zatvorila samo za prozne tvrdnje.
#
# ZATO SE PRAVILO OKREĆE: `COMPUTATIONAL` se bira SAMO kad server POZITIVNO
# dokaže da je označeni odgovor VRIJEDNOST/REZULTAT. Sve što se ne može dokazati
# ide na konzervativnu serversku ljestvicu. Sigurnost je iznad dostupnosti.
#
# ZNANE KONZERVATIVNE ŽRTVE (svjesne, ne propuštene): računata unija/presjek čije
# opcije IMENUJU skupove (`$A\cap B=\{2\}$`), čisto simbolički algebarski
# rezultat bez ijedne cifre (`$a+b$`), i domenska klasifikacija
# (`$\mathbb{Q}$`). Sve to dobija serversku ljestvicu — upotrebljivu pomoć, ne
# odbijanje.

# Komande koje SAME PO SEBI tvrde ODNOS između imenovanih objekata (prava,
# ravan, skup, figura), a ne nose izračunatu vrijednost. Zatvorena i uska lista.
RECOGNITION_RELATION_COMMANDS = frozenset({
    "perp", "parallel", "nparallel",
    "subset", "subseteq", "supset", "supseteq", "nsubseteq", "nsupseteq",
    "notin", "ni", "cong", "sim", "simeq", "equiv", "propto",
    "varnothing", "emptyset",
    "Rightarrow", "Leftrightarrow", "implies", "iff", "forall", "exists",
    "land", "lor", "neg", "nmid",
})

# Grčka slova su u ovom projektu IMENA objekata (ugao $\alpha$, ravan $\alpha$),
# pa se u analizi ponašaju kao promjenljive, ne kao strukturne komande.
_GREEK_VARIABLE_COMMANDS = frozenset("""
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa
lambda mu nu xi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega
Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
""".split())

# Komande koje NOSE brojevnu vrijednost i zato zadovoljavaju „ima broj“.
_NUMERIC_CONSTANT_COMMANDS = frozenset({"pi", "infty"})

# Komande čiji je ARGUMENT oznaka/naziv, a ne izraz — njihov sadržaj se ne broji
# kao promjenljiva ($\mathbb{Q}$ nije promjenljiva Q, $\text{cm}$ nije c i m).
_LABEL_ARGUMENT_RE = re.compile(
    r"\\(?:mathbb|mathbf|mathcal|mathfrak|mathrm|mathsf|text|textrm|textit"
    r"|operatorname)\s*\{[^{}]*\}")

_COMMAND_TOKEN_RE = re.compile(r"\\([A-Za-z]+)")
_DIGIT_RE = re.compile(r"\d")
_IDENTIFIER_RE = re.compile(r"[A-Za-z]+")
# Relacijski operatori: oni razlikuju VELIČINU (`$\frac{3}{4}$`) od TVRDNJE O
# ODNOSU (`$x>3$`), a ta razlika odlučuje treba li dokaz o samom zadatku.
_RELATION_SYMBOL_RE = re.compile(r"[=<>≤≥≠]")
_RELATION_COMMANDS = frozenset({"le", "leq", "ge", "geq", "neq", "ne", "approx",
                                "equiv", "lt", "gt", "ll", "gg", "doteq"})
# Konjunkcije na kojima se vrijednosni odgovor smije razdvojiti na dijelove
# („$x=2,\ y=3$“ je rješenje sistema, dakle DVIJE vrijednosti, ne tvrdnja).
_VALUE_PART_SPLIT_RE = re.compile(r"[,;]|\\quad|\\qquad|\\land|\\wedge| i ")


def _math_source(text):
    """Spojen sadržaj SVIH `$…$` segmenata date opcije."""
    return " ".join(math_contents(tokenize_math(text or "")))


def _identifier_names(math_source):
    """Imena objekata/promjenljivih unutar matematike, bez oznaka i komanda.

    `\\mathbb{Q}` i `\\text{cm}` se odbacuju s argumentom; grčka slova postaju
    obično ime; ostale komande nestaju. `\\angle ABC` daje JEDNO ime `ABC`."""
    stripped = _LABEL_ARGUMENT_RE.sub(" ", math_source or "")
    names = []

    def _keep_greek(match):
        command = match.group(1)
        if command in _GREEK_VARIABLE_COMMANDS:
            return " " + command + " "
        return " "

    stripped = _COMMAND_TOKEN_RE.sub(_keep_greek, stripped)
    for name in _IDENTIFIER_RE.findall(stripped):
        if name not in names:
            names.append(name)
    return tuple(names)


def _carries_numeric_literal(text, math_source):
    """Ima li opcija bilo gdje broj, ili brojevnu konstantu unutar matematike."""
    if _DIGIT_RE.search(text or ""):
        return True
    return any(command in _NUMERIC_CONSTANT_COMMANDS
               for command in _COMMAND_TOKEN_RE.findall(math_source or ""))


def value_shaped(option_text):
    """POZITIVAN dokaz da je opcija VRIJEDNOST / REZULTAT.

    Sva četiri uslova moraju biti ispunjena; nijedan nije dovoljan sam:

      1. najviše `MAX_VALUE_PROSE_WORDS` proznih riječi izvan matematike
         (dozvoljava mjernu jedinicu i imenicu uz broj);
      2. opcija nosi BROJ (cifru bilo gdje, ili $\\pi$ / $\\infty$) — čisto
         simbolički zapis („$\\mathbb{Z}$“, „$A\\cup B$“) nije rezultat;
      3. nijedna komanda iz `RECOGNITION_RELATION_COMMANDS` se ne pojavljuje —
         one tvrde odnos imenovanih objekata, a ne vrijednost;
      4. nijedan dio odgovora (razdvojen zapetom/konjunkcijom) ne imenuje DVA
         različita objekta — „$\\alpha+\\beta=180^\\circ$“ je tvrdnja o odnosu
         dva ugla, dok je „$x=2,\\ y=3$“ dvije vrijednosti."""
    text = option_text or ""
    if prose_word_count(text) > MAX_VALUE_PROSE_WORDS:
        return False
    math_source = _math_source(text)
    if not _carries_numeric_literal(text, math_source):
        return False
    commands = set(_COMMAND_TOKEN_RE.findall(math_source))
    if commands & RECOGNITION_RELATION_COMMANDS:
        return False
    for part in _VALUE_PART_SPLIT_RE.split(math_source) or (math_source,):
        if len(_identifier_names(part)) > 1:
            return False
    return True


def _relation_operators(math_source):
    """Relacijski operatori unutar matematike (znakovi i komande)."""
    found = list(_RELATION_SYMBOL_RE.findall(math_source or ""))
    found += [command for command in _COMMAND_TOKEN_RE.findall(math_source or "")
              if command in _RELATION_COMMANDS]
    return tuple(found)


def quantity_shaped(option_text):
    """POZITIVAN dokaz da je opcija ČISTA VELIČINA, a ne tvrdnja o odnosu.

    Veličina ne može biti iskaz: `150`, `$12$ cm`, `$\\frac{3}{4}$`,
    `$\\sqrt{2}$`, `$4\\pi$`, `$\\{1,2,3\\}$`, `$(2,3)$` se ne mogu „prepoznati“
    kao tvrdnja — one su rezultat, bez obzira šta zadatak pita. Zato je ovo
    JEDINI oblik za koji je računska ljestvica dokaziva iz same opcije.

    Sve što nosi relacijski operator ili imenuje objekat (`$x=3$`, `$x>3$`,
    `$\\alpha=60^\\circ$`, `$P=24$ cm`, `$y=2x+1$`) je RELACIJSKI oblik i traži
    dokaz o SAMOM ZADATKU — vidi `_solve_task_proven`."""
    text = option_text or ""
    if not value_shaped(text):
        return False
    math_source = _math_source(text)
    if _relation_operators(math_source):
        return False
    return not _identifier_names(math_source)


def marked_answer_is_short_symbolic(option_text):
    """Označeni odgovor je KRATAK SIMBOLIČKI zapis (matematika bez proze).

    Postoji za dokaz pokrivenosti: talas mora dokazati da je Problem A ŽIVO
    zatvoren, dakle da je bar jedan objavljen zadatak imao simboličke opcije i
    ipak dobio serversku ljestvicu."""
    text = option_text or ""
    return bool(_math_source(text).strip()) and prose_word_count(text) == 0


# ---------------------------------------------------------------------------
# 1b. DOKAZ DA ZADATAK TRAŽI IZVOĐENJE (završna popravka prije živog talasa)
# ---------------------------------------------------------------------------
# NALAZ ZAVRŠNOG PREGLEDA: oblik opcije sam po sebi NE MOŽE odlučiti klasu za
# relacijske odgovore, jer ISTI zapis znači dvije različite stvari:
#
#     opcije $x>3$ / $x>5$ / $x>1$ / $x\ge 4$
#       A) „Riješi nejednačinu $x-1>2$.“                → REZULTAT (izvedi ga)
#       B) „Na brojevnoj pravoj su svi brojevi desno od $3$, bez $3$.
#           Koja nejednačina to opisuje?“               → PREPOZNAVANJE zapisa
#
# Prije ove popravke oba su bila `COMPUTATIONAL`, jer je odluka gledala samo
# `(option_texts, marked_index)`. Za B je to model-autorski nagovještaj nad
# zadatkom u kojem je sam zapis odgovor — ista klasa kao FW-X03.
#
# ZATO relacijski oblik traži POZITIVAN dokaz O ZADATKU, a jedini takav dokaz
# koji ovaj repozitorij može ISTINITO dati je serverski orakl koji zadatak SAM
# RIJEŠI: `mcq_integrity.evaluate_linear_solve_mcq` čita relaciju iz objavljenog
# teksta zatvorenom gramatikom, izračuna skup rješenja egzaktnom `Fraction`
# aritmetikom i uporedi ga sa svakom opcijom. Kad on potvrdi da je označena
# opcija UPRAVO izvedeno rješenje, dokazano je da zadatak traži izvođenje.
#
# NEMA NOVE MATEMATIKE I NEMA NOVOG KATALOGA RIJEČI: orakl već postoji i već
# odlučuje o OBJAVI (`mcq_integrity.publication_failure`). Kad orakl ćuti —
# skupovni zapis, površina, ugao, sistem — dokaz ne postoji i klasa pada na
# konzervativnu propozicionu ljestvicu. To je svjesna razmjena dostupnosti za
# sigurnost (Faza 3/4 vraća bogatiju klasifikaciju kroz jače ugovore lekcija).


def _solve_task_proven(task_text, option_texts, marked_index):
    """Je li DOKAZANO da zadatak traži izvođenje označenog rezultata."""
    from matbot import mcq_integrity

    texts = list(option_texts or ())
    if not texts or not (0 <= marked_index < len(texts)):
        return False
    try:
        result = mcq_integrity.evaluate_linear_solve_mcq(task_text or "", texts)
    except Exception:                       # orakl nikad ne smije oboriti turn
        return False
    return bool(result.applicable and result.valid
                and result.correct_index == marked_index)


def classify_visible_task(task_text, option_texts, marked_index):
    """Klasa zadatka za ljestvicu pomoći, iz OBJAVLJENOG ZADATKA i opcija.

    HIJERARHIJA DOKAZA (sigurnost iznad dostupnosti):
      1. pozitivno dokazan oblik TVRDNJE (prozne rečenice kroz cio skup)
         → `PROPOSITIONAL`;
      2. označena opcija je dokazana ČISTA VELIČINA (nema relacijskog operatora
         ni imenovanog objekta) → `COMPUTATIONAL`; veličina ne može biti iskaz,
         pa je taj dokaz neovisan o tekstu zadatka;
      3. označena opcija je RELACIJSKI vrijednosni oblik I serverski orakl je
         sam riješio zadatak i potvrdio da je ta opcija izvedeno rješenje
         → `COMPUTATIONAL`;
      4. sve ostalo → `UNCERTAIN`.

    `UNCERTAIN` je pun i namjeran ishod, ne greška: pozivalac tada uzima
    konzervativnu ljestvicu (vidi `effective_task_class`), koja ne izriče
    nijedan kriterij odluke."""
    texts = [text or "" for text in (option_texts or ())]
    if not texts or not isinstance(marked_index, int):
        return UNCERTAIN
    if not (0 <= marked_index < len(texts)):
        return UNCERTAIN

    counts = [prose_word_count(text) for text in texts]
    marked = counts[marked_index]

    # 1. TVRDNJA: označena opcija je rečenica i to nosi CIO skup opcija.
    propositions = sum(1 for count in counts if count >= MIN_PROPOSITION_PROSE_WORDS)
    if marked >= MIN_PROPOSITION_PROSE_WORDS and propositions >= MIN_PROPOSITION_OPTIONS:
        return PROPOSITIONAL

    # Jedna prozna tvrdnja među opcijama ukida svaki vrijednosni dokaz.
    if propositions:
        return UNCERTAIN
    if not value_shaped(texts[marked_index]):
        return UNCERTAIN

    # 2. ČISTA VELIČINA — dokaz iz oblika, neovisno o tekstu zadatka.
    if quantity_shaped(texts[marked_index]):
        return COMPUTATIONAL

    # 3. RELACIJSKI oblik — traži dokaz da zadatak traži izvođenje.
    if _solve_task_proven(task_text, texts, marked_index):
        return COMPUTATIONAL

    # 4. Ništa se ne može dokazati.
    return UNCERTAIN


def effective_task_class(task_text, option_texts, marked_index):
    """Klasa koja STVARNO bira ljestvicu: nedokaziv oblik ide konzervativno.

    Konzervativno = ljestvica za tvrdnju/prepoznavanje, jer ona ne izriče
    kriterij koji bira označenu opciju. Suprotan izbor bi na nedokazanom obliku
    dopustio FW-X03 klasu, njenu simboličku varijantu i — bez dokaza o zadatku
    — relacijsku varijantu („prepoznaj koja nejednakost opisuje uslov“)."""
    task_class = classify_visible_task(task_text, option_texts, marked_index)
    return COMPUTATIONAL if task_class == COMPUTATIONAL else PROPOSITIONAL


def session_task_class(session):
    """JEDINA ulazna tačka za klasu AKTIVNOG zadatka iz serverske sesije.

    ZAŠTO POSTOJI OVDJE, a ne u pozivaocu: i produkcijski put pomoći
    (`matbot/tutor/`) i evaluator (`tools/practice_eval/`) moraju izvesti klasu
    iz DOSLOVNO istog konteksta. Kad bi svaki sam sastavljao argumente, jedan bi
    mogao zaboraviti tekst zadatka i tiho mjeriti slabiju klasifikaciju od one
    koju produkcija koristi."""
    session = session or {}
    options = session.get("current_options") or []
    texts = [option.get("text", "") for option in options if isinstance(option, dict)]
    correct_id = session.get("correct_option_id") or ""
    marked = next((index for index, option in enumerate(options)
                   if isinstance(option, dict) and option.get("id") == correct_id), -1)
    return effective_task_class(session.get("current_task") or "", texts, marked)


def session_marked_answer(session):
    """Konačan odgovor vezan za TRENUTNI serverski skup opcija.

    ŽIVI H12 (talas F6H): odgovor koji se dopisuje na vrh ljestvice mora doći iz
    opcije koju server SADA drži tačnom, ne iz polja koje je model napisao PRIJE
    miješanja. Objava dokazuje da su ta dva teksta jednaka
    (`expected answer does not match marked option` pada zatvoreno), pa je ovo u
    normalnom radu bajt za bajt isti niz — ali kad se ikad raziđu, mjerodavna je
    TRENUTNA označena opcija. `expected_answer_summary` ostaje rezerva za sesiju
    bez opcija (npr. stariji zapis)."""
    session = session or {}
    correct_id = session.get("correct_option_id") or ""
    for option in session.get("current_options") or ():
        if isinstance(option, dict) and option.get("id") == correct_id:
            return option.get("text") or ""
    return session.get("expected_answer_summary") or ""


# ---------------------------------------------------------------------------
# 2. LJESTVICA — ko je AUTOR kojeg nagovještaja
# ---------------------------------------------------------------------------
SERVER = "server"
MODEL = "model"


def served_hint_level(hint_level_before, max_hint_level):
    """Nivo koji OVAJ turn služi. `hint_level` je broj RANIJE datih hintova."""
    try:
        before = int(hint_level_before or 0)
    except (TypeError, ValueError):
        before = 0
    return max(1, min(before + 1, int(max_hint_level)))


def is_ladder_top(hint_level_before, max_hint_level):
    return served_hint_level(hint_level_before, max_hint_level) >= int(max_hint_level)


def hint_author(task_class, served_level, max_hint_level):
    """Autor nagovještaja — serverska odluka, poznata PRIJE ijednog poziva.

    Vrh ljestvice je uvijek serverski (nema svježeg nedokazanog izvoda), a za
    klasu tvrdnje/prepoznavanja serverski su i nivoi 1 i 2 (nema izrečenog
    kriterija odluke). Računska ljestvica 1–2 ostaje modelu."""
    if int(served_level) >= int(max_hint_level):
        return SERVER
    return SERVER if task_class != COMPUTATIONAL else MODEL


# ---------------------------------------------------------------------------
# 3. SERVERSKI SASTAVLJENI TEKSTOVI
# ---------------------------------------------------------------------------
# Šabloni za klasu tvrdnje/prepoznavanja. NE SADRŽE nijedan znak prepisan iz
# ponuđenih opcija ni iz internog odgovora — jedini varijabilni sadržaj je
# NASLOV LEKCIJE (koji učenik ionako vidi u UI-u jer ju je sam izabrao) i jedna
# STRUKTURNA činjenica dokazana nad SVIM opcijama (dakle po definiciji
# nediskriminativna). Zato nijedan od njih ne može otkriti označenu tvrdnju, ni
# doslovno ni parafrazom — to je konstrukcija, ne mjerenje.

_PROPOSITIONAL_LEVEL_1 = (
    "Idemo korak po korak — u ovom koraku samo upoređuješ, još ne biraš.\n\n"
    "Sve ponuđene opcije govore o istom pojmu iz lekcije „{title}“, ali tvrde "
    "različite stvari. Pročitaj ih jednu po jednu i za svaku sebi zapiši: o "
    "kojim objektima ili veličinama govori, koji odnos ili svojstvo im "
    "pripisuje, koje uslove pri tome pretpostavlja i tvrdi li to za SVAKI "
    "slučaj ili samo za neki. Kad to zapišeš za sve opcije, uporedi ih po tim "
    "istim stavkama — tek tada ima smisla odlučivati."
)

# NALAZ REVIEWA (Problem B): prvi oblik nivoa 2 je nalagao PROTIVPRIMJER —
# „zamisli situaciju u kojoj opcija NE bi vrijedila; jedan protivprimjer je
# dovoljan da tvrdnja padne“. To je valjano SAMO za univerzalne tvrdnje, a nije
# valjano za:
#   • egzistencijalne opcije („prava i ravan MOGU imati …“) — jedna situacija u
#     kojoj se to ne dogodi ne obara tvrdnju da se MOŽE dogoditi. Upravo takav
#     distraktor stoji u živom TR-B1 paketu, dakle server bi na TOJ lekciji
#     učio pogrešnu metodu;
#   • tvrdnje o KONKRETNOJ konfiguraciji zadatka — primjer iz druge
#     konfiguracije ne dokazuje ništa o zadanoj;
#   • prepoznavanje definicije ili zapisa — „protivprimjer“ tu nije kategorija.
# Isti nalaz važio je i za bivši dodatak nivoa 2 („zamijeni pretpostavku i
# zaključak pa vidi vrijedi li i tada“): obrat implikacije nije test istinitosti
# originalne implikacije, pa je učenik mogao odbaciti tačnu tvrdnju čiji obrat
# ne vrijedi.
#
# SERVER NIKAD NE SMIJE UČITI NEVALJANU METODU. Zato nivo 2 sada nalaže samo
# ono što je univerzalno tačno: uporedi opciju sa ZADANIM uslovima i s
# definicijom/pravilom lekcije, i odbaci je samo kad nađeš KONKRETAN sukob s
# tim izvorima. Specijalizacija protivprimjera je UKLONJENA U CIJELOSTI —
# `implication_shaped` dokazuje samo OBLIK opcija, nikad da je zadatak o
# univerzalnoj implikaciji, pa dokaz za tu metodu ne postoji.
_PROPOSITIONAL_LEVEL_2 = (
    "Idemo dalje — sada suzi izbor, ali zaključak ostavi sebi.\n\n"
    "Uzmi ono što si u prvom koraku zapisao i svaku opciju uporedi s DVA "
    "izvora: sa svim uslovima koji su navedeni u samom zadatku i s definicijom "
    "ili pravilom koje uvodi lekcija „{title}“. Za svaku opciju provjeri da li "
    "mijenja objekat o kojem se govori, svojstvo koje mu pripisuje, jačinu "
    "tvrdnje („svaki“ ili „neki“, „uvijek“ ili „može“) ili neki od zadanih "
    "uslova. Opciju odbaci SAMO kad nađeš konkretan sukob s onim što zadatak "
    "daje ili s tom definicijom — nagađanje nije provjera. Konačan izbor "
    "napiši sam."
)

# Dodatak koji se dopisuje SAMO kad SVE opcije nose uslovni oblik. To je
# struktura zajednička svima, pa ne izdvaja ni jednu — a upravo je smjer
# implikacije bio ono što je FW-X03 razdvajalo od distraktora.
#
# OBA DODATKA SU ČISTO POMOĆ ZA ČITANJE STRUKTURE i ne izriču nijedno pravilo
# zaključivanja: govore ŠTA razdvojiti i ŠTA uporediti, nikad „ako X onda
# odbaci“. Zamijenjen smjer se opisuje kao DRUGA tvrdnja koju treba provjeriti
# zasebno — nikad kao dokaz da je opcija netačna (obrat tačne implikacije
# ponekad i sam vrijedi).
_IMPLICATION_NOTE_1 = (
    "\n\nSve opcije su oblika „ako … onda“, pa posebno pazi ŠTA je u svakoj "
    "pretpostavka, a ŠTA zaključak."
)
_IMPLICATION_NOTE_2 = (
    "\n\nKod opcija oblika „ako … onda“ razdvoji pretpostavku od zaključka i "
    "uporedi taj smjer sa smjerom koji lekcija uvodi. Opcija s obrnutim smjerom "
    "je DRUGA tvrdnja i mora se provjeriti zasebno, po istim uslovima iz "
    "zadatka."
)

_IMPLICATION_RE = re.compile(r"(?i)\bako\b")

TOP_HINT_INTRO = (
    "Idemo do kraja. Evo cijelog postupka za ovaj zadatak — pročitaj ga korak "
    "po korak."
)
FULL_SOLUTION_INTRO = (
    "Evo rješenja ovog zadatka u cijelosti. Prati postupak, pa ga uporedi s "
    "onim što si sam pokušao."
)


def implication_shaped(option_texts):
    """True kad SVE ponuđene opcije nose uslovni oblik („ako … onda“)."""
    texts = [text or "" for text in (option_texts or ())]
    return bool(texts) and all(_IMPLICATION_RE.search(text) for text in texts)


def compose_propositional_hint(served_level, lesson_title, option_texts=()):
    """Serverski nagovještaj za klasu tvrdnje/prepoznavanja (nivo 1 ili 2)."""
    title = (lesson_title or "").strip()
    template = _PROPOSITIONAL_LEVEL_1 if int(served_level) <= 1 else _PROPOSITIONAL_LEVEL_2
    note = _IMPLICATION_NOTE_1 if int(served_level) <= 1 else _IMPLICATION_NOTE_2
    text = template.format(title=title)
    if implication_shaped(option_texts):
        text += note
    return text


def states_answer(solution, expected_answer):
    """Iznosi li provjereno rješenje već i konačan odgovor.

    Koristi ISTI orakl (`feedback.leaks_answer`) kojim deterministička ruta
    odlučuje da li dopisati rezultat na vrh ljestvice
    (`pipeline._deterministic_top_hint`) — nikad novi kriterij. Golo poređenje
    podniske ovdje ne bi bilo dovoljno: za jednocifren odgovor („5“) svaka
    slučajna pojava te cifre bi lažno tvrdila da je rezultat već rečen."""
    from matbot import feedback

    answer = (expected_answer or "").strip()
    if not answer:
        return True     # nema šta dopisati
    return feedback.leaks_answer(solution or "", answer, answer)


def _result_sentence(expected_answer, task_class):
    answer = (expected_answer or "").strip()
    if not answer:
        return ""
    if task_class == COMPUTATIONAL:
        return f"Konačan rezultat je {answer}."
    # Za tvrdnju „rezultat“ nije vrijednost nego iskaz — isti sadržaj, tačna
    # rečenica. Označena opcija je već sanitizovan objavljen tekst.
    suffix = "" if answer.endswith((".", "!", "?")) else "."
    return f"Tačna tvrdnja je: {answer}{suffix}"


def _compose_from_artifact(intro, solution, expected_answer, task_class):
    body = (solution or "").strip()
    if not body:
        return ""
    parts = [intro, body]
    if not states_answer(body, expected_answer):
        sentence = _result_sentence(expected_answer, task_class)
        if sentence:
            parts.append(sentence)
    return "\n\n".join(parts)


def compose_top_hint(solution, expected_answer, task_class):
    """VRH LJESTVICE iz PROVJERENOG artefakta objave. Prazno = nema artefakta.

    Prazan rezultat NIJE tekst za učenika — pozivalac tada pada zatvoreno.
    Nikad se ne poziva model: time klasa „tačan zaključak preko netačnog
    izvoda“ (FW-X03 nagovještaj 3) nestaje po konstrukciji."""
    return _compose_from_artifact(TOP_HINT_INTRO, solution, expected_answer, task_class)


def compose_full_solution(solution, expected_answer, task_class):
    """Puno rješenje („uradi ga ti“) iz ISTOG provjerenog artefakta."""
    return _compose_from_artifact(FULL_SOLUTION_INTRO, solution, expected_answer,
                                  task_class)


# JEDINA ULAZNA TAČKA ZA KOMPOZICIJU IZ SESIJE — isti razlog kao kod
# `session_task_class`: produkcija (`matbot/tutor/pipeline.py`) i evaluator
# (`tools/practice_eval/checks.py`) moraju sastavljati iz DOSLOVNO istog
# konteksta. Kad bi svaki sam birao izvor konačnog odgovora, jedan bi mogao
# uzeti `expected_answer_summary`, a drugi trenutnu označenu opciju — i
# provenijencijska provjera bi prijavila lažan razlaz.

def compose_top_hint_for_session(session):
    """Vrh ljestvice iz artefakta objave, vezan za TRENUTNO stanje sesije."""
    session = session or {}
    return compose_top_hint(session.get("solution_summary") or "",
                            session_marked_answer(session),
                            session_task_class(session))


def compose_full_solution_for_session(session):
    """Puno rješenje iz artefakta objave, vezano za TRENUTNO stanje sesije."""
    session = session or {}
    return compose_full_solution(session.get("solution_summary") or "",
                                 session_marked_answer(session),
                                 session_task_class(session))


# ---------------------------------------------------------------------------
# DRUGAČIJE OBJAŠNJENJE — obećanje nije objašnjenje
# ---------------------------------------------------------------------------
# ŽIVI NALAZ (direktor škole): uz aktivan zadatak učenik je pisao „objasni na
# drugi način“ i dobio SAMO najavu — „Dobro, objasniću na drugi način, korak po
# korak i kratko.“ — bez ijedne brojke. Na sljedeću poruku („pa objasni“) opet
# najavu. Uzrok je bio asimetrija ugovora polja: `hint_request` mora donijeti
# `hint`, `full_solution_request` mora donijeti `worked_solution`, a
# `explanation_request` nema NIJEDNO obavezno polje sadržaja — sve živi u
# `reply`, pa je najava prolazila kao ispravan odgovor.
#
# SERVER IMA ČIME DA ODGOVORI I BEZ NOVOG POZIVA: objavljeni zadatak već nosi
# PROVJEREN artefakt (`solution_summary` + označeni odgovor) — isti onaj iz
# kojeg se sastavljaju vrh ljestvice i puno rješenje. Zato se prazno obećanje
# dopunjuje kompozicijom iz TOG artefakta, a ne novom derivacijom.
EXPLANATION_INTRO = (
    "Dobro — evo istog zadatka objašnjenog drugim riječima, korak po korak."
)

# MJERA JE USKA I DOKAZIVA, ne jezički sud: „nema nijednog matematičkog
# segmenta i nijedne cifre“. Objašnjenje računskog zadatka bez ijednog broja ne
# postoji; čim tekst nosi matematiku, model se NE dira.
_MATH_SEGMENT_RE = re.compile(r"\$[^$]+\$")
_ANY_DIGIT_RE = re.compile(r"\d")


def is_meta_only_explanation(text) -> bool:
    """True kad vidljivi tekst ne nosi NIJEDAN dokaziv trag matematike."""
    body = (text or "").strip()
    if not body:
        return True
    if _MATH_SEGMENT_RE.search(body):
        return False
    return not _ANY_DIGIT_RE.search(body)


def alternative_explanation_may_reveal(session) -> bool:
    """Smije li objašnjenje nositi i REZULTAT — samo kad on više nije tajna.

    Provjereni artefakt sadrži i konačan odgovor, pa bi njegovo sastavljanje
    nad NERIJEŠENIM zadatkom bilo otkrivanje rješenja — tačno ono što
    `_guard_answer_leak` s pravom obara. Zato se kompozicija nudi tek kad je
    zadatak završen ili je rješenje već prikazano."""
    session = session or {}
    if session.get("task_completed"):
        return True
    return session.get("last_result") == "full_solution"


METHOD_EXPLANATION_INTRO = (
    "Dobro — evo istog zadatka objašnjenog drugim riječima. Prati postupak, a "
    "rezultat izračunaj sam."
)


def _method_prefix(solution, marked_answer):
    """Dio postupka PRIJE nego što se pojavi rezultat, ili ''.

    Rez je DOKAZIV (pretraga stringa), ne procjena: tekst se skraćuje tačno na
    mjestu gdje se prvi put pojavi označeni odgovor. Ono što ostane mora i
    dalje nositi matematiku, inače nije objašnjenje nego uvod."""
    body = (solution or "").strip()
    answer = (marked_answer or "").strip()
    if not body:
        return ""
    if answer and answer in body:
        body = body[:body.index(answer)].strip()
        # Rez ostavlja odsječenu rečenicu („… Rezultat je“), pa se vraćamo na
        # posljednju CIJELU rečenicu — pola rečenice nije objašnjenje.
        end = body.rfind(".")
        if end != -1:
            body = body[:end + 1]
    body = body.rstrip(" ,;:-–—=").strip()
    if not body or is_meta_only_explanation(body):
        return ""
    return body


def compose_alternative_explanation_for_session(session):
    """Objašnjenje aktivnog zadatka iz PROVJERENOG artefakta, ili ''.

    DVA REŽIMA, jer artefakt sadrži i konačan odgovor:
      • zadatak je završen (ili je rješenje već prikazano) → cijelo objašnjenje;
      • zadatak je OTVOREN → samo POSTUPAK do rezultata, i to tek nakon što
        postojeći mjerač (`feedback.leaks_answer`) DOKAŽE da tekst ne odaje
        odgovor. Ako se to ne može dokazati, vraća se prazno i pozivalac
        ostavlja zatečeno ponašanje.

    Prazan rezultat NIKAD nije tekst za učenika."""
    from matbot import feedback

    session = session or {}
    if not (session.get("current_task") or "").strip():
        return ""
    solution = session.get("solution_summary") or ""
    marked = session_marked_answer(session)
    if alternative_explanation_may_reveal(session):
        return _compose_from_artifact(EXPLANATION_INTRO, solution, marked,
                                      session_task_class(session))
    method = _method_prefix(solution, marked)
    if not method:
        return ""
    composed = "\n\n".join([METHOD_EXPLANATION_INTRO, method])
    if feedback.leaks_answer(composed, marked, session.get("expected_answer_summary") or "",
                             task_text=session.get("current_task") or ""):
        return ""
    return composed


# ---------------------------------------------------------------------------
# 4. MJERAČ OTKRIVANJA TVRDNJE (drugi sloj, nad tekstom MODELA)
# ---------------------------------------------------------------------------
# Vjeran port mjerača iz Faze 0 (`tools/practice_eval/hintsemantics.py`) u
# proizvod, s NEPROMIJENJENOM semantikom i pragovima — kalibracija (156 živih
# turnova, 1 DISCLOSED, 0 lažnih pozitiva) time ostaje na snazi. Razlika je
# ISKLJUČIVO u vlasništvu: sada je uvezan u produkcijski put (nivoi 1 i 2) i u
# evaluator, pa oba mjere DOSLOVNO isto.
#
# GRANICA KOJA SE NE SKRIVA: poređenje je po TAČNIM tokenima. Parafraza u drugom
# padežu (TR-B1) ostaje van dosega i NE zatvara se stemmingom — mjereno je da
# skraćivanje tokena istovremeno RAZGRADI dokaz za FW-X03 (svaku/svakoj,
# pravu/pravom se sliju), pa bi „popravka“ oborila jedini slučaj koji mjerač
# stvarno dokazuje. TR-B1 klasu zato zatvara konstrukcija iznad (serverski
# sastavljen nagovještaj za tvrdnje), ne ovaj mjerač.
DISCLOSED = "DISCLOSED"
NOT_PROVEN = "NOT_PROVEN"
NOT_APPLICABLE = "NOT_APPLICABLE"

DISCLOSURE_VERDICTS = (DISCLOSED, NOT_PROVEN, NOT_APPLICABLE)

MIN_PROPOSITION_TOKENS = 5
MARKED_COVERAGE_THRESHOLD = 0.90
MARKED_ONLY_COVERAGE_THRESHOLD = 1.0
MAX_DISTRACTOR_ONLY_COVERAGE = 0.0

PROPOSITION_LEAK_CODE = "hint_discloses_marked_proposition"

# Zatvorena lista bosanskih funkcijskih riječi — nije rječnik i ne opisuje
# matematiku. Doslovno ista lista kao u Fazi 0; ne širi se bez ponovne
# kalibracije nad živim artefaktima.
_STOPWORDS = frozenset("""
ako je su na u i ili se da to tada onda koja koji koje kroz iz za sa te ta taj
kod li ne nije bilo svaka svaki svako neku neki neka vec već samo ovo ova ovaj
prvo dakle npr tako kada kad pa ali sto što sve svih toj tom tim ga joj im ih
nam vam mi vi on ona ono oni one bude biti bio bila jer no nego tj kao
""".split())

_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")
_NON_WORD_RE = re.compile(r"[^0-9a-zčćžšđ]+")


def content_tokens(text):
    """Sadržajni tokeni jednog VIDLJIVOG teksta.

    Matematički zapis se ne odbacuje: `$` granice padaju, a LaTeX komanda
    postaje običan token (`\\alpha` → `alpha`), pa se `$\\alpha$` u zadatku i u
    opciji poklope."""
    lowered = _COMMAND_RE.sub(lambda m: " " + m.group(0)[1:] + " ",
                              (text or "").replace("$", " "))
    words = _NON_WORD_RE.sub(" ", lowered.lower()).split()
    return frozenset(word for word in words
                     if len(word) >= 2 and word not in _STOPWORDS)


@dataclass(frozen=True)
class Disclosure:
    """Nalaz nad JEDNIM nagovještajem. `detail` nikad ne nosi sadržaj."""

    verdict: str
    marked_coverage: float = 0.0
    marked_only_coverage: float = 0.0
    distractor_only_coverage: float = 0.0
    proposition_tokens: int = 0
    detail: str = ""

    @property
    def disclosed(self):
        return self.verdict == DISCLOSED


def proposition_disclosure(hint_text, marked_option_text, distractor_texts,
                           task_text=""):
    """Otkriva li nagovještaj označenu TVRDNJU (drugi sloj zaštite).

    Dva izuzetka, oba preuzeta od postojećeg pravila projekta:
      • token koji već stoji u TEKSTU ZADATKA nije dokaz (prepričavanje) — isti
        izuzetak `feedback.leaks_answer` ima kroz `task_text`;
      • vrh ljestvice se nikad ne mjeri, jer po ugovoru SMIJE dati rezultat;
        pozivalac zato prosljeđuje samo nivoe 1 i 2."""
    from_task = content_tokens(task_text)
    marked = content_tokens(marked_option_text) - from_task
    if len(marked) < MIN_PROPOSITION_TOKENS:
        return Disclosure(
            NOT_APPLICABLE, proposition_tokens=len(marked),
            detail=(f"marked option carries {len(marked)} content tokens outside the "
                    f"task text; below {MIN_PROPOSITION_TOKENS} the answer is a value, "
                    "not a proposition — feedback.leaks_answer stays the owner"))

    distractors = [tokens for tokens in
                   (content_tokens(text) - from_task for text in (distractor_texts or ()))
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


# ---------------------------------------------------------------------------
# 5. PROPORCIONALNOST RAZREDU I LEKCIJI (živi TR-B1 nagovještaj 2)
# ---------------------------------------------------------------------------
# ŽIVI NALAZ: lekcija 9. razreda OSNOVNE škole dobila je u nagovještaju 2
# parametarski oblik prave i skalarni proizvod ($\\vec{r}=\\vec{r_0}+t\\vec{v}$,
# $\\vec{n}\\cdot(\\ldots)$). Nijedna kapija to nije mogla vidjeti: `mathsafe`
# `\\vec` ima na allowlistu (i tamo ostaje — pitanje „je li zapis bezbjedan“ nije
# pitanje „pripada li ovom zadatku“), terminologija je čista, a brojevnog lanca
# nema.
#
# Faza 3 tek donosi pun lekcijski opseg. Ovdje se koristi dokaz koji VEĆ
# POSTOJI i koji je prošao cijeli put objave: tekst zadatka, tekstovi opcija,
# očekivani odgovor i recenzentovo odobreno rješenje. Napredna mašinerija koja
# se NE javlja ni u jednom od tih artefakata je nova tehnika, ne pomoć — i pada
# zatvoreno. Skup je ZATVOREN i namjerno uzak; sve ostalo prolazi netaknuto.
ADVANCED_MACHINERY_COMMANDS = frozenset({
    "vec", "overrightarrow", "overline",
    "int", "iint", "iiint", "oint", "sum", "prod", "lim", "limsup", "liminf",
    "partial", "nabla", "grad", "div",
    "binom", "choose", "det", "matrix", "pmatrix", "bmatrix", "vmatrix",
    "begin", "end", "cases", "substack",
})

OUT_OF_SCOPE_NOTATION_PREFIX = "out_of_scope_notation:"

_MATH_COMMAND_RE = re.compile(r"(?<!\\)\\([A-Za-z]+)")


def math_commands(text):
    """Imena LaTeX komanda UNUTAR matematičkih segmenata datog teksta."""
    found = []
    for content in math_contents(tokenize_math(text or "")):
        for match in _MATH_COMMAND_RE.finditer(content):
            name = match.group(1)
            if name not in found:
                found.append(name)
    return tuple(found)


def out_of_scope_notation_codes(text, approved_texts):
    """Napredna mašinerija koje NEMA u odobrenim artefaktima zadatka.

    Vraća zatvorene interne kodove (samo ime komande — CLAUDE.md pravilo 7),
    prazna torka kad se ništa ne može dokazati."""
    approved = set()
    for approved_text in (approved_texts or ()):
        approved.update(math_commands(approved_text))
    codes = []
    for name in math_commands(text):
        if name in ADVANCED_MACHINERY_COMMANDS and name not in approved:
            code = OUT_OF_SCOPE_NOTATION_PREFIX + name[:24]
            if code not in codes:
                codes.append(code)
    return tuple(codes)


# ---------------------------------------------------------------------------
# 6. UPOTREBLJIVOST — nagovještaj bez ijednog skela
# ---------------------------------------------------------------------------
# Sigurnost sama nije dovoljna: „Razmisli još malo.“ je bezbjedno i beskorisno.
# Pravilo je namjerno ANKEROVANO NA CIJELI tekst, isti obrazac koji
# `schema.incomplete_task_request` već koristi: odbija se samo kad se CIO
# nagovještaj sastoji od generičnog podsticaja bez ijednog koraka. Zbog toga
# kratak ali stvaran nagovještaj („Saberi brojnike, nazivnik ostaje isti.“)
# nikad nije pogodak — a što se ne može dokazati, propušta se.
EMPTY_HELP_CODE = "help_without_task_scaffold"

_EMPTY_HELP_RE = re.compile(
    r"(?i)\A(?:"
    r"razmisli(?:\s+(?:jo[šs]|ponovo|opet|dobro))?(?:\s+malo)?"
    r"|probaj(?:\s+(?:jo[šs]|ponovo|opet))?(?:\s+malo)?"
    r"|poku[šs]aj(?:\s+(?:jo[šs]|ponovo|opet))?(?:\s+(?:malo|jednom))?"
    r"|potrudi\s+se(?:\s+malo)?"
    r"|prisjeti\s+se(?:\s+(?:gradiva|lekcije|pravila|definicije))?"
    r"|sjeti\s+se(?:\s+(?:gradiva|lekcije|pravila|definicije))?"
    r"|(?:provjeri|pogledaj|pro[čc]itaj)(?:\s+(?:pa[žz]ljivo|jo[šs]\s+jednom))?"
    r"(?:\s+(?:opcije|ponu[đd]ene\s+opcije|zadatak|tekst\s+zadatka|gradivo))?"
    r"|pa[žz]ljivo\s+(?:pro[čc]itaj|pogledaj)(?:\s+(?:zadatak|tekst\s+zadatka|opcije))?"
    r")\s*[.!?]*\s*\Z"
)


# Vidljiva pomoć se sastavlja iz DVA polja modela (`reply` + `hint`), pa je
# „Idemo korak po korak.\n\nRazmisli još malo.“ i dalje pomoć bez sadržaja.
# Zato se sudi po BLOKOVIMA: nalaz postoji samo kad je SVAKI blok ili prazna
# najava ili generičan podsticaj. Jedan blok sa stvarnim korakom sve propušta.
_GENERIC_INTRO_RE = re.compile(
    r"(?i)\A(?:"
    r"(?:u\s+redu|dobro|ok|super|naravno|nema\s+problema)?[\s,.!—-]*"
    r"(?:idemo|hajde(?:mo)?|krenimo|nastavimo)?"
    r"(?:\s+(?:korak\s+po\s+korak|dalje|redom|zajedno|na\s+zadatak))?"
    r"|evo\s+(?:pomo[ćc]i|upute|uputstva|savjeta|hinta)"
    r")\s*[.!:…]*\s*\Z"
)


def _help_blocks(text):
    """Vidljiv tekst pomoći razložen na blokove (jedan red = jedan blok)."""
    parts = re.split(r"\n+", text or "")
    return [re.sub(r"\s+", " ", part).strip() for part in parts if part.strip()]


def empty_help_code(text):
    """`EMPTY_HELP_CODE` kad NIJEDAN blok ne nosi korak, inače ''."""
    blocks = _help_blocks(text)
    if not blocks:
        return EMPTY_HELP_CODE
    for block in blocks:
        if _EMPTY_HELP_RE.match(block) or _GENERIC_INTRO_RE.match(block):
            continue
        return ""
    return EMPTY_HELP_CODE
