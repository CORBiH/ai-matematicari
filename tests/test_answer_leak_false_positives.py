r"""Sloj 3 ne smije proglasiti curenjem svaki broj jednak committed odgovoru.

OFFLINE AUDIT commita 89c114d nad istim živim runom (postStabilityFixes).
Ponovnim izvršavanjem STAROG i NOVOG detektora nad svih 119 uporedivih
odgovora, commit novo blokira ČETIRI koraka u tri scenarija:

    B53 korak 1, 2   → TAČNO otkriveno (to je i bio cilj commita)
    B46 korak 1      → FALSE POSITIVE
    B59 korak 1      → FALSE POSITIVE

Oba false positive-a su u živom runu prošla sve provjere (`failed_checks: []`),
pa bi ih commit degradirao u `LEAK_BLOCKED_REPLY` — dobar odgovor zamijenjen
generičkim tekstom.

  • B46 (djeljivost sa 25, committed `75`): tutor navodi PRAVILO lekcije —
    „završava se sa $00$, $25$, $50$ ili $75$“. Broj je član nabrajanja, ne
    tvrdnja o rješenju.
  • B59 (procenti, committed `$4$`): hint piše `$25\%=\frac{25}{100}=\frac{1}{4}$`
    i izričito kaže „ne izračunavaj još rezultat“. Četvorka je NAZIVNIK.

Uzrok: sloj 3 je brojao SVAKI broj u svakom `$…$` segmentu, bez obzira na
ulogu. Dvije determinističke razlike ga vraćaju na ono što se može dokazati:

  1. broj u STRUKTURNOJ poziciji (eksponent `^`, indeks `_`, argument u
     vitičastim zagradama — nazivnik/brojnik) je parametar zapisa, nikad
     tvrđena vrijednost;
  2. objavljen rezultat i provjera committed rješenja UVIJEK stoje u tvrđenoj
     JEDNAKOSTI (`x=\frac{8}{2}=4`, `2\cdot4=8`, `2(4)=8`). Segment bez `=`
     je operacija koju učenik tek treba izvesti (`$8:2$`), ne rezultat.

Tri uloge broja koje detektor razlikuje:
  • broj u operaciji            → math segment BEZ `=`, ili strukturna pozicija
                                  → dozvoljeno (slojevi 1–2 i dalje vrijede)
  • broj objavljen kao rezultat → otkrivajuća fraza (sloj 2) ILI tvrđena
                                  jednakost (sloj 3) → blokirano
  • broj koji provjerava rješenje → supstitucija u tvrđenoj jednakosti (sloj 3)
                                  → blokirano
"""
import pytest

from matbot import feedback

# --- ŽIVI STRINGOVI — ne mijenjati, oni su dokaz ---------------------------

B46_TASK = "Koji od sljedećih brojeva je djeljiv sa 25?"
B46_COMMITTED = "75"
B46_REPLY = (r"Netačno. Broj 68 nije djeljiv sa 25. Pravilo: broj je djeljiv sa 25 "
             r"samo ako se završava sa $00$, $25$, $50$ ili $75$. 68 se ne završava "
             r"nijednim od tih parova cifara. Probaj ponovo ili zatraži hint.")

B59_TASK = r"Koliko je $25\%$ od $16$?"
B59_COMMITTED = r"$4$"
B59_REPLY = (r"Evo pomoći za prvi korak." "\n\n"
             r"Prvi korak: procent se pretvori u razlomak ili decimalu, pa se pomnoži "
             r"sa danim brojem. Tako je $25\%=\frac{25}{100}=\frac{1}{4}$, pa primijeni "
             r"množenje $16\cdot\frac{1}{4}$ (ne izračunavaj još rezultat).")

B53_TASK = r"Riješi jednačinu: $2(x)=8$"
B53_COMMITTED = r"$x=4$"
B53_STEP1 = (r"Dobro što si pokušao. Provjerimo: ako je $x=3$, onda je "
             r"$2(x)=2\cdot3=6$, što nije $8$. Zato $x=3$ nije tačno. Prvi korak "
             r"rješavanja je podijeliti obje strane jednačine sa $2$ (jer je lijeva "
             r"strana $2$ puta $x$). Dakle, $x=\frac{8}{2}=4$. Možeš to provjeriti: "
             r"$2\cdot4=8$.")
B53_STEP2 = (r"Nije tačno. Ako je $x=100$, onda je $2(100)=200$, a to nije $8$. Prvi "
             r"korak pravilnog rješavanja je podijeliti obje strane jednačine sa $2$ "
             r"(jer je lijeva strana $2$ puta $x$). Dakle $x=\frac{8}{2}=4$. "
             r"Provjera: $2(4)=8$, što je tačno.")


def leaks(text, committed, task):
    return feedback.leaks_answer(text, committed, committed, task_text=task)


# --- ŽIVI FALSE POSITIVE-I -------------------------------------------------

def test_live_b46_rule_enumeration_is_not_a_leak():
    """Nabrajanje pravila lekcije nije objava rješenja."""
    assert not leaks(B46_REPLY, B46_COMMITTED, B46_TASK)


def test_live_b59_denominator_is_not_a_leak():
    """`\\frac{1}{4}` je nazivnik; hint izričito ne računa rezultat."""
    assert not leaks(B59_REPLY, B59_COMMITTED, B59_TASK)


# --- 1. BROJ KAO LEGITIMNA OPERACIJA ---------------------------------------

@pytest.mark.parametrize("text", [
    r"Podijeli obje strane sa $4$.",
    r"Pomnoži obje strane sa $4$.",
    r"Podijeli obje strane jednačine sa $4$, jer je $x$ pomnožen sa $4$.",
])
def test_an_operation_on_both_sides_is_not_a_disclosure(text):
    assert not leaks(text, r"$x=4$", r"Riješi jednačinu: $x:4=1$")


# --- 2. STRUKTURNE POZICIJE ------------------------------------------------

STRUCTURAL = {
    "koeficijent": r"Zapis $4x$ znači četiri puta $x$.",
    "eksponent": r"Sjeti se šta znači $x^4$.",
    "eksponent u vitičastim": r"Pogledaj $10^{4}$.",
    "nazivnik": r"Uporedi to sa $\frac{1}{4}$.",
    "brojnik": r"Uporedi to sa $\frac{4}{9}$.",
    "indeks": r"Označi dijagonalu $d_4$.",
    "redni broj koraka u prozi": "Korak 4: primijeni pravilo iz lekcije.",
    "redni broj koraka u mathu": r"$4.$ korak je najvažniji.",
}


@pytest.mark.parametrize("label,text", sorted(STRUCTURAL.items()))
def test_a_structural_position_is_not_a_disclosure(label, text):
    assert not leaks(text, r"$4$", r"Izračunaj: $\sqrt{16}-\sqrt{9}$"), label


def test_a_wrong_student_attempt_may_be_quoted_and_checked():
    assert not leaks(r"Napisao si $x=3$, ali $2\cdot 3=6$, a treba $8$.",
                     B53_COMMITTED, B53_TASK)


# --- 3. ČESTI ODGOVORI 0, 1, 2, 4 ------------------------------------------

COMMON = {
    ("0", r"Sjeti se da je $a^0=1$ za svaki $a\neq 0$."),
    ("1", r"Svaki broj podijeljen samim sobom daje $\frac{a}{a}$."),
    ("2", r"Kvadriranje se piše $x^2$, dakle broj množiš sa samim sobom."),
    ("4", r"Kvadrat ima $4$ jednake stranice."),
    ("2", r"Sljedeći korak je $8:2$."),
    ("1", r"Uporedi $\frac{1}{2}$ i $\frac{1}{3}$."),
}


@pytest.mark.parametrize("value,text", sorted(COMMON))
def test_common_short_answers_do_not_produce_blanket_false_positives(value, text):
    assert not leaks(text, "$%s$" % value, r"Izračunaj: $\sqrt{36}-\sqrt{25}$")


# --- 4. SIGURAN HINT: OPERACIJA BEZ IZRAČUNATOG REZULTATA ------------------

def test_a_hint_naming_the_operation_without_the_result_stays_allowed():
    """`$8:2$` je operacija koju učenik tek izvodi — detektor ništa ne računa."""
    assert not leaks(r"Sljedeći korak je $8:2$.", r"$4$", B53_TASK)
    assert not leaks(r"Sljedeći korak je $8:2$.", r"$4$", r"Izračunaj količnik.")


# --- 5. BROJ KOJI JE VEĆ U TEKSTU ZADATKA ----------------------------------

def test_a_number_already_in_the_task_stays_allowed():
    assert not leaks(r"Pogledaj $8$ i $2$ u zadatku, to su ti dati brojevi.",
                     r"$8$", B53_TASK)
    # …a isti broj u zadatku ne otvara vrata drugoj vrijednosti.
    assert leaks(r"Dakle $x=\frac{8}{2}=4$.", B53_COMMITTED, B53_TASK)


# --- ŠTA MORA OSTATI BLOKIRANO (B53 zaštita se ne slabi) -------------------

MUST_STAY_BLOCKED = {
    "lanac racuna": r"Dakle, $x=\frac{8}{2}=4$.",
    "lanac s razmacima": r"Dakle $ x = \frac{8}{2} = 4 $.",
    "provjera mnozenjem": r"Možeš to provjeriti: $2\cdot4=8$.",
    "provjera zagradom": r"Provjera: $2(4)=8$, što je tačno.",
    "doslovno rjesenje": r"Tačno rješenje je $x=4$.",
    "proza rjesenje je": "Rješenje je 4.",
    "proza dobijes": "Podijeli 8 sa 2 i dobiješ 4.",
    "proza tacan odgovor": "Tačan odgovor je 4.",
    "B53 korak 1 (doslovno)": B53_STEP1,
    "B53 korak 2 (doslovno)": B53_STEP2,
}


@pytest.mark.parametrize("label,text", sorted(MUST_STAY_BLOCKED.items()))
def test_the_b53_protection_is_not_weakened(label, text):
    assert leaks(text, B53_COMMITTED, B53_TASK), label


def test_a_full_verification_of_the_committed_solution_is_blocked():
    """„Kompletna provjera committed rješenja“ — supstitucija u jednakost."""
    assert leaks(r"Uvrsti: $2\cdot 4=8$, dakle jednakost vrijedi.",
                 B53_COMMITTED, B53_TASK)
    assert leaks(r"Provjerimo: lijeva strana $2(4)=8$, desna strana $8$.",
                 B53_COMMITTED, B53_TASK)
