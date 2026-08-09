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

    # 2) RELACIJA — poredi se KANONSKI SKUP RJEŠENJA. Kad su domeni saglasni i
    #    diskretni, poredi se i presjek s tim domenom, pa preformulacija koja
    #    nad traženim domenom daje isti skup ostaje dozvoljena.
    if explicit_relation and task.has_relation:
        shared_domain = (requested_domain
                         if requested_domain and task.domain == requested_domain
                         else "")
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
