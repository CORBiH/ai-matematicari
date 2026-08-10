"""Deterministička provjera da OBJAVLJEN zadatak poštuje IZRIČIT matematički
zahtjev učenika (Task 3; živi DISC talas A009/A010/A020/A023).

ŽIVI NALAZ (pre-release discovery 100 na 8a8f04d, četiri potvrđena drifta):
učenik postavi jednoznačan matematički uslov, Tutor napravi zadatak koji taj
uslov TIHO promijeni, recenzent ga odobri, i objavi se paket koji je iznutra
matematički ispravan — ali ne odgovara na ono što je traženo.

    A020 (najteži):  „…riješi x+2=2 isključivo u domenu N={1,2,3,...}“
                     objavljeno: „…u domeni $\\mathbb{Z}$“, označeno $\\{0\\}$.
                     Nad Z je $\\{0\\}$ tačno; nad TRAŽENIM N je skup PRAZAN,
                     jer po konvenciji ovog projekta N počinje od 1.
    A009:            traženo $-2<x<2$, objavljeno $-2<x+1<2$ (skup $-3<x<1$).
    A010:            traženo $-2<x<0$, objavljeno $-3<-2x<-1$ (skup $1/2<x<3/2$).
    A023:            tražena nejednačina $1/3<x\\le 5/6$, objavljena JEDNAČINA
                     $2x+3=7$.

Nijedan deterministički sloj nije poredio zahtjev sa zadatkom.

ŠTA OVAJ MODUL JESTE: uzak poređivač DVIJE „riješi“ izjave koje čita ISTA
zatvorena gramatika iz `mcq_integrity.read_solve_statement` — dakle ona koju
Task 1 već koristi za orakl rješavanja. Nema drugog parsera jednačina ni
domena, nema simboličke algebre, nema modela.

ŠTA OVAJ MODUL NIJE: prepoznavač namjere iz prirodnog jezika. Angažuje se
ISKLJUČIVO kad je zahtjev strukturno dokaziv; sve ostalo se PRESKAČE (isti
princip kao mathcheck.py — nedokazivo nije dokaz prekršaja).

GRANICE (namjerno uske):
  • poruka mora nositi direktivu rješavanja („riješi…“, „…rješenje…“) ILI
    izričitu relaciju — bez toga se usputno pominjanje brojevnog skupa
    („ne razumijem cijele brojeve“) NIKAD ne čita kao uslov zadatka;
  • dvije različite relacije ili dva različita domena u zahtjevu znače
    NEJEDNOZNAČNO → preskoči, nikad ne pogađaj koji je „pravi“;
  • relacije se porede po KANONSKOM SKUPU RJEŠENJA, ne po zapisu: $x>3$ i
    $x+2>5$ su ISTI zahtjev (globalni forenzički zaključak B003 — preformulacija
    s istim skupom rješenja NIJE drift koji blokira izdanje);
  • zadatak koji nema pročitljivu relaciju ne može dokazati prekršaj relacije;
  • NEMA LJEPLJIVOG STANJA: čita se isključivo PORUKA TEKUĆEG TURNA, pa
    obični nastavci („teže“, „drugi zadatak“, „objasni“) ne nasljeđuju stari
    uslov.

DRUGI NALAZ — RELACIJA KOJU ZADATAK NIKAD NE NAPIŠE
---------------------------------------------------
Živi FINAL-40 lažni prolaz (jedini objavljen paket s prekršajem vjernosti u
cijeloj kampanji): učenik je dao $x>3$ i tražio da se relacija u tekstu
preoblikuje dodavanjem iste nenulte cijele konstante na obje strane. Objavljeno
je „Na obje strane originalne nejednačine dodan je isti nenulti cijeli broj 2.
Riješite dobijenu nejednačinu…“ — a NIJEDNA nejednačina u tekstu ne stoji.
Zadatak nije samostalan (učenik nema šta riješiti), a među opcijama je stajalo
i doslovno $x>3$.

Provjera relacije iznad je tu ĆUTALA po vlastitom pravilu („zadatak bez
pročitljive relacije ne dokazuje prekršaj“) — i to pravilo ostaje tačno za
zapis koji parser ne ume da pročita ($x^2>4$). Ovdje se dokazuje nešto drugo i
strukturno: tekst se DEIKTIČKI poziva na relaciju (originalna/dobijena/nastala
nejednačina ili jednačina) a NIJEDAN relacijski znak se u njemu ne pojavljuje.
Tada nije riječ o nepročitanoj relaciji nego o relaciji koje NEMA.

Sva tri uslova moraju vrijediti istovremeno, pa nijedan pozitivni kontrolni
slučaj ne pada: zahtjev nosi tačno jednu čitljivu relaciju, tekst zadatka traži
rješavanje i deiktički imenuje relaciju, a u cijelom tekstu nema ni jednog
relacijskog znaka ($<$, $>$, $=$, \\le, \\ge, ≤, ≥…). Preformulisan zadatak koji
relaciju STVARNO napiše ($x+2>5$) prolazi ovdje netaknut i dalje se sudi samo
po kanonskom skupu rješenja.

TREĆI NALAZ — „DOBIJENA“ RELACIJA KOJA NIJE ISTI SKUP RJEŠENJA
--------------------------------------------------------------
Živi ciljani nalaz (jedini blokator te kampanje): učenik je dao $x>3$ i tražio
preoblikovanje u kojem se lijevoj strani dodaje 2, a desnoj 4. Objavljeno je

    „Početna nejednačina je $x>3$. Dodajemo $2$ s lijeve strane i $4$ s desne
     strane pa dobijamo $x+2>7$. Riješi dobijenu nejednačinu $x+2>7$…“

s označenim $(5,\\infty)$ — tačnim za $x+2>7$, ali NE za traženo $x>3$.

Zašto je prošlo: `read_solve_statement` po ugovoru vraća NAJVIŠE JEDNU
relaciju, a dvije RAZLIČITE proglašava nejednoznačnošću. To je tačno za
ZAHTJEV (server ne smije birati koji je uslov „pravi“), ali za TEKST ZADATKA
je slijepo — zadatak koji preoblikuje NAMJERNO nosi dvije: polaznu i dobijenu.
Zbog `relation_ambiguous=True` provjera relacije nije ni pokrenuta, a nova
provjera iz drugog nalaza traži da relacije NEMA — pa je i ona ćutala.

Rješenje NE širi matematiku nego ČITANJE: `mcq_integrity.read_solve_relations`
vraća SVE dokazivo pročitane relacije REDOM, s rasponom u tekstu, istom
gramatikom i istim egzaktnim računom. Zadatak koji SAM tvrdi preoblikovanje
(zatvoren skup markera: dobi…, nastal…, preoblik…, transform…, rezult…,
izmijenj…, nova + imenica relacije) mora imati OPERATIVNU relaciju — prvu koja
počinje IZA markera — s ISTIM kanonskim skupom rješenja kao zahtjev.

Kad iza markera nema pročitljive relacije, ništa se ne tvrdi.

ČETVRTI NALAZ — „DOBIJENA“ RELACIJA KOJA JE SAMO PREPISANA POLAZNA
------------------------------------------------------------------
Živi FINAL-40 lažni prolaz (jedini objavljen prekršaj vjernosti u toj
kampanji): učenik je dao $x>3$ i IZRIČITO tražio „preoblikuj dodavanjem iste
nenulte cijele konstante na obje strane; ne prepisuj relaciju doslovno“.
Objavljeno je

    „Riješi nejednačinu DOBIJENU dodavanjem iste nenulte cijele konstante na
     obje strane originalne relacije $x>3$…“

— dakle tekst tvrdi dobijenu nejednačinu, a jedina relacija koju napiše je
DOSLOVNO tražena polazna. Označeno je $\\{x\\in Q\\mid x>3\\}$, matematički
tačno za $x>3$, pa nijedna postojeća kapija nije imala šta da prijavi:
relacija JESTE zapisana (drugi nalaz ćuti), zadatak nosi tačno JEDNU relaciju
pa `has_relation` nije nejednoznačno (treći nalaz ćuti), a skup rješenja je
identičan traženom (provjera relacije ćuti — i s pravom, jer poredi skupove).

Nedostajala je jedina stvar koju je učenik tražio: DRUGI ZAPIS. Zato ovaj
nalaz jedini gleda SINTAKSU, i to samo kad su sva četiri uslova istovremeno
ispunjena:

  1. poruka nosi tačno jednu dokazivo pročitanu relaciju;
  2. poruka IZRIČITO traži drugačiji zapis (zatvoren skup: preoblik…,
     transform…, „ne prepisuj/nemoj prepisivati/ne kopiraj“, „u drugom
     obliku/zapisu“) — obično „Riješi x>3“ time nikad ne postaje zahtjev za
     preoblikovanjem;
  3. tekst zadatka SAM tvrdi preoblikovanje (isti zatvoreni skup markera kao
     treći nalaz) i traži rješavanje;
  4. svaka relacija u tekstu je dokazivo pročitana i nijedna nije zapisana
     drugačije od one koju je učenik već napisao.

Uslov 4 nosi i konzervativnost: nepročitan relacijski znak bilo gdje u tekstu
($x^2>9$) znači „nešto je zapisano a ne zna se šta“ — tada se ništa ne tvrdi.
Poređenje je nad kanonskim ZAPISOM (`_normalize_solve_segment`), pa razmak i
LaTeX kozmetika ne prave lažnu razliku, a $3<x$ ili $x+2>5$ jesu drugi zapis.

Ova tri nalaza su međusobno isključiva po konstrukciji: drugi traži da
relacije NEMA, treći da ih ima više i da je „dobijena“ drugog skupa rješenja,
četvrti da postoji ali da nijedna nije nova.

Vjernost zahtjevu NE nadjačava lekciju: semantički ugovori vježbe (Task 2)
i dalje odbijaju zadatak van lekcije čak i kad je učenikov zahtjev vjerno
prepisan — te dvije kapije su nezavisne i obje moraju proći.
"""
import re

from matbot import mcq_integrity

# Interni kod nalaza — kao svi ostali, ide ISKLJUČIVO u log i recenzentov
# ulaz, nikad u browser (CLAUDE.md, pravilo 7).
REQUEST_FIDELITY_CODE = "request_fidelity_violation"

DOMAIN_MISMATCH = "domain_mismatch"
RELATION_MISMATCH = "relation_mismatch"
TASK_TYPE_MISMATCH = "task_type_mismatch"
MISSING_REQUESTED_RELATION = "missing_requested_relation"
TRANSFORMED_RELATION_MISMATCH = "transformed_relation_mismatch"
MISSING_DISTINCT_TRANSFORMED_RELATION = "missing_distinct_transformed_relation"

# Direktiva rješavanja u PORUCI — ponovo se koristi zatvoreni uzorak orakla,
# da se granica „ovo je zahtjev za zadatak“ ne izmišlja po drugi put.
_SOLVE_DIRECTIVE_RE = mcq_integrity._SOLVE_DIRECTIVE_RE

# ---------------------------------------------------------------------------
# ZATVORENA GRAMATIKA DEIKTIČKOG POZIVANJA NA RELACIJU
# ---------------------------------------------------------------------------
# Namjerno NIJE opšti razumijevač prirodnog jezika: samo pridjev iz zatvorenog
# skupa neposredno uz imenicu relacije. „Nejednačina“ SADRŽI „jednačinu“ kao
# podniz, pa negirani oblik stoji PRVI u alternaciji (isti razlog kao u
# mcq_integrity._REQUEST_INEQUALITY_WORD_RE).
_RELATION_NOUN = (r"(?:nejedna[čc]in\w*|jedna[čc]in\w*|nejednakost\w*"
                  r"|jednakost\w*|relacij\w*)")
_RELATION_DEICTIC_ADJECTIVE = (
    r"(?:original\w*|polazn\w*|po[čc]etn\w*|prvobitn\w*|prethodn\w*"
    r"|dobijen\w*|dobiven\w*|nastal\w*|rezultuju[čć]\w*|dobijenoj?"
    r"|transformisan\w*|transformisan[eoj]\w*|preoblikovan\w*"
    r"|izmijenjen\w*|izmenjen\w*|nov\w*)")
_DEICTIC_RELATION_RE = re.compile(
    r"\b" + _RELATION_DEICTIC_ADJECTIVE + r"\s+" + _RELATION_NOUN
    + r"|\b" + _RELATION_NOUN + r"\s+" + _RELATION_DEICTIC_ADJECTIVE,
    re.IGNORECASE)

# BILO KOJI relacijski znak — golim znakom, LaTeX komandom ili Unicode-om.
# Prisustvo makar jednog znači „relacija je zapisana“ i novi nalaz ĆUTI:
# dokazivanje da je zapisana relacija POGREŠNA je posao provjere relacije
# iznad, a nedokaziva relacija ($x^2>4$) se po doktrini projekta preskače.
_ANY_RELATION_TOKEN_RE = re.compile(
    r"[<>=≤≥≠]|\\(?:leq|le|geq|ge|lt|gt|neq|ne|equiv|approx)(?![A-Za-z])")


def _refers_to_a_relation_it_never_states(task_text):
    """True SAMO kad tekst traži rješavanje relacije koju uopšte ne pokazuje."""
    text = task_text or ""
    if _ANY_RELATION_TOKEN_RE.search(text):
        return False                     # relacija JESTE zapisana — nije naš nalaz
    if not _SOLVE_DIRECTIVE_RE.search(text):
        return False                     # tekst uopšte ne traži rješavanje
    return bool(_DEICTIC_RELATION_RE.search(text))


# ---------------------------------------------------------------------------
# ZATVORENA GRAMATIKA TVRDNJE O PREOBLIKOVANJU
# ---------------------------------------------------------------------------
# Ista porodica riječi kao deiktički pridjevi iznad, ali u GLAGOLSKOM/
# participskom obliku kojim tekst zadatka stvarno najavljuje rezultat
# („…pa dobijamo“, „nastaje“, „preoblikovanjem“, „transformisana“,
# „rezultujuća“). Marker je USKI LOKALNI KONTEKST, ništa više: on samo bira
# KOJA je od pročitanih relacija ona koju učenik treba riješiti. Namjerno se
# NE hvataju pridjevi polazne strane (original/početn/polazn/prvobitn) — oni
# pokazuju na polaznu relaciju, koja je po definiciji ono što je traženo.
#
# „nov…“ je jedini oblik koji traži imenicu uz sebe: samostalno bi hvatao
# „novčić“, „novembar“ i slično.
_TRANSFORMED_RELATION_MARKER_RE = re.compile(
    r"\bdobi\w*"
    r"|\bnastal\w*|\bnastaj\w*|\bnastan\w*"
    r"|\bpreoblik\w*"
    r"|\btransform\w*"
    r"|\brezult\w*"
    r"|\bizmijenj\w*|\bizmenj\w*"
    r"|\bnov\w*\s+" + _RELATION_NOUN,
    re.IGNORECASE)


def _non_equivalent_transformed_relation(request, task_text, shared_domain):
    """Prva DOKAZANO neekvivalentna „dobijena“ relacija, ili None.

    None znači „ništa se ne može dokazati“ — uključujući slučaj kad iza
    markera uopšte nema PROČITLJIVE relacije. Preskočeno nije dokaz
    ispravnosti (doktrina cijelog projekta): kad se operativna relacija ne
    može bezbjedno izolovati, ponašanje ostaje konzervativno."""
    text = task_text or ""
    markers = list(_TRANSFORMED_RELATION_MARKER_RE.finditer(text))
    if not markers:
        return None                      # zadatak ne tvrdi nikakvo preoblikovanje
    relations = mcq_integrity.read_solve_relations(text)
    if not relations:
        return None                      # nijedna relacija nije pročitljiva

    # PRIDRUŽIVANJE: za svaki marker uzima se PRVA relacija koja počinje iza
    # njega — „relacija neposredno iza fraze“, nikad slijepo poređenje svake
    # relacije u tekstu. Relacije prije prvog markera (npr. citirana polazna)
    # se time ne diraju.
    operative = []
    for marker in markers:
        following = next((relation for relation in relations
                          if relation.start >= marker.end()), None)
        if following is not None and following not in operative:
            operative.append(following)

    for relation in operative:
        if not mcq_integrity.solution_sets_match(
                request.solution, relation.solution, shared_domain):
            return relation
    return None


# ---------------------------------------------------------------------------
# ZATVORENA GRAMATIKA IZRIČITOG ZAHTJEVA ZA DRUGAČIJIM ZAPISOM
# ---------------------------------------------------------------------------
# Ovo je JEDINI prekidač četvrtog nalaza: bez njega obično „Riješi $x>3$“ nikad
# ne postaje zahtjev da se relacija prepiše u drugom obliku, pa zadatak koji
# doslovno rješava napisanu relaciju ostaje ispravan (i najčešći) slučaj.
# Skup je zatvoren i traži IMPERATIV/IMENICU preoblikovanja ili IZRIČITU
# zabranu prepisivanja — nikad puko pominjanje riječi „oblik“.
_REQUESTED_REFORMULATION_RE = re.compile(
    r"\bpreoblik\w*"
    r"|\btransform\w*"
    r"|\bne\s+prepi\w*|\bnemoj\s+prepisiv\w*|\bbez\s+prepisiv\w*"
    r"|\bne\s+kopira\w*|\bnemoj\s+kopira\w*"
    r"|\b(?:drug\w*|druk[čc]ij\w*)\s+(?:oblik\w*|zapis\w*)",
    re.IGNORECASE)


def _requests_a_distinct_reformulation(student_message):
    """True SAMO kad poruka izričito traži DRUGAČIJI ZAPIS iste relacije."""
    return bool(_REQUESTED_REFORMULATION_RE.search(student_message or ""))


def _normalized_relation_sources(text):
    """Kanonski ZAPIS svake dokazivo pročitane relacije, redom, bez duplikata.

    Namjerno se ne poredi skup rješenja (to rade provjere iznad) nego zapis:
    četvrti nalaz pita „je li išta napisano DRUGAČIJE“, a ne „je li tačno“.
    Normalizacija je ista koju čitač relacija ionako pravi, pa razmak i LaTeX
    kozmetika ($x \\ge 4$ / $x\\ge4$) nikad ne prave lažnu razliku."""
    raw = text or ""
    sources = []
    for relation in mcq_integrity.read_solve_relations(raw):
        value = mcq_integrity._normalize_solve_segment(
            raw[relation.start:relation.end])
        if value and value not in sources:
            sources.append(value)
    return sources


def _every_relation_token_is_readable(text, relations):
    """True kad SVAKI relacijski znak pripada dokazano pročitanoj relaciji.

    Zapisana ali nepročitana relacija ($x^2>9$, skupovni zapis) znači da se ne
    zna šta je napisano — a četvrti nalaz upravo tvrdi da NIŠTA novo nije
    napisano. Bez ovog uslova bi nedokazivo postalo presuda."""
    spans = tuple((relation.start, relation.end) for relation in relations)
    for match in _ANY_RELATION_TOKEN_RE.finditer(text or ""):
        if not any(start <= match.start() and match.end() <= end
                   for start, end in spans):
            return False
    return True


def _only_restates_the_requested_relation(student_message, task_text):
    """True kad zadatak tvrdi preoblikovanje, a ne napiše NIJEDAN novi zapis."""
    text = task_text or ""
    if not _TRANSFORMED_RELATION_MARKER_RE.search(text):
        return False                     # zadatak ne tvrdi nikakvo preoblikovanje
    if not _SOLVE_DIRECTIVE_RE.search(text):
        return False                     # tekst uopšte ne traži rješavanje
    relations = mcq_integrity.read_solve_relations(text)
    if not relations:
        return False                     # relacije nema — to je DRUGI nalaz
    if not _every_relation_token_is_readable(text, relations):
        return False                     # nešto je zapisano a nepročitano
    requested = _normalized_relation_sources(student_message)
    if not requested:
        return False
    return all(source in requested
               for source in _normalized_relation_sources(text))


def _requested_kind(request):
    """Izričito imenovana vrsta ima prednost nad vrstom izvedenom iz relacije."""
    return request.stated_kind or request.relation_kind


def _task_kind(task):
    """Kod zadatka je STRUKTURA mjerodavna: šta se stvarno rješava."""
    return task.relation_kind or task.stated_kind


def request_fidelity_failures(student_message, task_text):
    """Torka detalja o DOKAZANIM prekršajima vjernosti zahtjevu.

    Prazna torka znači „nema dokazanog prekršaja“ — što uključuje i sve
    slučajeve u kojima zahtjev nije strukturno čitljiv. To NIJE dokaz da je
    zadatak vjeran zahtjevu, nego odsustvo dokazanog prekršaja."""
    message = student_message or ""
    if not message.strip() or not (task_text or "").strip():
        return ()
    request = mcq_integrity.read_solve_statement(message)

    # ANGAŽMAN: bez izričite relacije, poruka mora bar tražiti rješavanje.
    # „Daj mi teži zadatak“ i „objasni“ time nemaju šta da sačuvaju.
    explicit_relation = request.has_relation
    if not explicit_relation and not _SOLVE_DIRECTIVE_RE.search(message):
        return ()
    requested_domain = (request.domain
                        if request.domain_status == "supported" else "")
    requested_kind = _requested_kind(request)
    if not explicit_relation and not requested_domain and not requested_kind:
        return ()

    task = mcq_integrity.read_solve_statement(task_text)
    failures = []

    # 1) DOMEN. Traženi domen mora biti DOKAZIVO sačuvan: „nije spomenut“ i
    #    „nedokaziv“ se tretiraju isto kao pogrešan domen, jer bez izričitog
    #    dokaza server ne može tvrditi da zadatak radi u traženom skupu.
    #    N i N0 su RAZLIČITI skupovi (N počinje od 1) i nikad se ne izjednačavaju.
    if requested_domain:
        if task.domain_status == "supported":
            if task.domain != requested_domain:
                failures.append(
                    f"{DOMAIN_MISMATCH}: requested {requested_domain}, "
                    f"task {task.domain}")
        else:
            failures.append(
                f"{DOMAIN_MISMATCH}: requested {requested_domain}, "
                "task states no provable domain")

    # 2a) RELACIJA KOJU ZADATAK NIKAD NE NAPIŠE (vidi docstring modula).
    #     Zahtjev nosi tačno jednu čitljivu relaciju, tekst zadatka se na
    #     relaciju deiktički poziva i traži da je učenik riješi — a u cijelom
    #     tekstu nema nijednog relacijskog znaka. Takav zadatak nije samostalan
    #     i ne izvršava traženu preformulaciju, pa se ne smije objaviti.
    if explicit_relation and _refers_to_a_relation_it_never_states(task_text):
        failures.append(
            f"{MISSING_REQUESTED_RELATION}: the task refers to a relation it "
            f"never writes; requested '{request.solution_display()}'")

    shared_domain = (requested_domain
                     if requested_domain and task.domain == requested_domain
                     else "")

    # 2b) „DOBIJENA“ RELACIJA KOJA NIJE ISTI SKUP RJEŠENJA (vidi docstring).
    #     Angažuje se SAMO kad zadatak nosi VIŠE različitih čitljivih relacija
    #     (tada `task.has_relation` po ugovoru ćuti kao „nejednoznačno“) i kad
    #     SAM tvrdi preoblikovanje. Kad zadatak ima tačno jednu relaciju,
    #     odluku i dalje donosi provjera 2 ispod — bez dvostrukog nalaza.
    if (explicit_relation and not task.has_relation
            and _SOLVE_DIRECTIVE_RE.search(task_text)):
        drifted = _non_equivalent_transformed_relation(
            request, task_text, shared_domain)
        if drifted is not None:
            failures.append(
                f"{TRANSFORMED_RELATION_MISMATCH}: requested "
                f"'{request.solution_display()}', task's transformed relation "
                f"'{drifted.display()}'")

    # 2c) TVRDNJA O PREOBLIKOVANJU BEZ IJEDNOG NOVOG ZAPISA (vidi docstring).
    #     Jedini nalaz koji gleda SINTAKSU, i to samo kad je učenik izričito
    #     tražio drugačiji zapis: zadatak koji tvrdi „dobijenu“ relaciju a
    #     napiše samo doslovno traženu polaznu nije uradio ono što je traženo,
    #     iako mu je skup rješenja tačan. Bez uslova o izričitom zahtjevu ovo bi
    #     oborilo svaki obični „riješi napisanu relaciju“ zadatak.
    if (explicit_relation and _requests_a_distinct_reformulation(message)
            and _only_restates_the_requested_relation(message, task_text)):
        failures.append(
            f"{MISSING_DISTINCT_TRANSFORMED_RELATION}: the task announces a "
            f"transformed relation but writes no relation distinct from the "
            f"requested one; requested '{request.solution_display()}'")

    # 2) RELACIJA — poredi se KANONSKI SKUP RJEŠENJA. Kad su domeni saglasni i
    #    diskretni, poredi se i presjek s tim domenom, pa preformulacija koja
    #    nad traženim domenom daje isti skup ostaje dozvoljena.
    if explicit_relation and task.has_relation:
        if not mcq_integrity.solution_sets_match(
                request.solution, task.solution, shared_domain):
            failures.append(
                f"{RELATION_MISMATCH}: requested '{request.solution_display()}', "
                f"task '{task.solution_display()}'")

    # 3) VRSTA ZADATKA — samo kad su OBJE strane dokazivo jednoznačne.
    task_kind = _task_kind(task)
    if requested_kind and task_kind and requested_kind != task_kind:
        failures.append(
            f"{TASK_TYPE_MISMATCH}: requested {requested_kind}, task {task_kind}")

    return tuple(failures)
