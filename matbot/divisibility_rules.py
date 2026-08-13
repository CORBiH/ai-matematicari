"""PRAVILA DJELJIVOSTI kao PODATAK — i nagovještaj skrojen za konkretan zadatak.

ZAŠTO POSTOJI (ručni nalaz iz produkcije, potvrđen offline revizijom svih
0-pozivnih lekcija): lekcija o pravilima djeljivosti davala je za SVAKI zadatak
isti nagovještaj —

    „Primijeni pravilo djeljivosti za svaki navedeni broj — gledaj posljednju
     cifru, zbir cifara ili posljednje dvije cifre.“

— bez obzira traži li zadatak djeljivost sa $4$, sa $15$ ili sa $25$. Mjereno:
JEDAN nagovještaj na 43 različita zadatka. Učenik koji je kliknuo „Ne znam“
najčešće ne zna baš to KOJE pravilo ide uz KOJI djelilac, pa mu nabrajanje tri
kategorije pravila ne pomaže da krene.

Generator djelioce zna tačno (bira ih sam), pa je to serverska činjenica koja
mora doći do učenika. Ovdje su pravila zapisana kao zatvorena tabela, a
nagovještaj se sastavlja SAMO od pravila koja zadatak stvarno traži.

GRANICE:
  • nagovještaj nikad ne imenuje tačan ponuđeni broj i ne računa ostatke —
    daje pravilo i uputu da ga učenik sam primijeni;
  • djelilac bez pravila u tabeli ne dobija izmišljeno objašnjenje: tada se
    vraća prazno i pozivalac zadržava zatečeni tekst (guard koji ne može
    dokazati mora skipovati, ne nagađati).
"""

# Zatvorena tabela — isti skup djelilaca koji podržava i uski orakl djeljivosti
# (`mcq_integrity.SUPPORTED_DIVISORS`); test to drži u koraku.
RULES = {
    2: "posljednja cifra je parna ($0$, $2$, $4$, $6$ ili $8$)",
    3: "zbir cifara je djeljiv sa $3$",
    4: "posljednje dvije cifre čine broj djeljiv sa $4$",
    5: "posljednja cifra je $0$ ili $5$",
    6: ("mora biti djeljiv i sa $2$ i sa $3$ — parna posljednja cifra "
        "i zbir cifara djeljiv sa $3$"),
    9: "zbir cifara je djeljiv sa $9$",
    10: "posljednja cifra je $0$",
    15: ("mora biti djeljiv i sa $3$ i sa $5$ — zbir cifara djeljiv sa $3$ "
         "i posljednja cifra $0$ ili $5$"),
    25: "posljednje dvije cifre su $00$, $25$, $50$ ili $75$",
}


def rule_for(divisor):
    """Tekst pravila za jedan djelilac, ili prazno kad ga tabela ne zna."""
    return RULES.get(int(divisor), "")


def hint_for(divisors):
    """Nagovještaj skrojen za DJELIOCE KOJE ZADATAK STVARNO TRAŽI.

    Prazno kad ijedan djelilac nema pravilo — pozivalac tada zadržava zatečeni
    tekst umjesto da isporuči nepotpunu uputu."""
    chosen = [int(divisor) for divisor in (divisors or ())]
    if not chosen or any(value not in RULES for value in chosen):
        return ""
    parts = [f"sa ${value}$ — {RULES[value]}" for value in chosen]
    text = "Podsjetnik na pravila: " + "; ".join(parts) + "."
    if len(parts) > 1:
        text += " Traženi broj mora zadovoljiti SVA navedena pravila istovremeno."
    text += " Primijeni te provjere na svaki ponuđeni broj."
    return text
