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

Vjernost zahtjevu NE nadjačava lekciju: semantički ugovori vježbe (Task 2)
i dalje odbijaju zadatak van lekcije čak i kad je učenikov zahtjev vjerno
prepisan — te dvije kapije su nezavisne i obje moraju proći.
"""
from matbot import mcq_integrity

# Interni kod nalaza — kao svi ostali, ide ISKLJUČIVO u log i recenzentov
# ulaz, nikad u browser (CLAUDE.md, pravilo 7).
REQUEST_FIDELITY_CODE = "request_fidelity_violation"

DOMAIN_MISMATCH = "domain_mismatch"
RELATION_MISMATCH = "relation_mismatch"
TASK_TYPE_MISMATCH = "task_type_mismatch"

# Direktiva rješavanja u PORUCI — ponovo se koristi zatvoreni uzorak orakla,
# da se granica „ovo je zahtjev za zadatak“ ne izmišlja po drugi put.
_SOLVE_DIRECTIVE_RE = mcq_integrity._SOLVE_DIRECTIVE_RE


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
