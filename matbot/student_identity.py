"""Izvještajni identitet učenika: Thinkific e-mail → jedan `students.id`.

JEDAN IZVOR ISTINE o tome šta je „isti učenik“ u izvještajima. Ovdje živi
normalizacija e-maila i poziv izvještajnom sloju; potpisivanje/čitanje tvrdnje
je u `matbot/auth.py`, a SQL u `matbot/reporting_db.py`.

================================ GRANICA POVJERENJA =========================
OVO NIJE AUTENTIFIKACIJA I NE SMIJE POSTATI OVLAŠTENJE.

Thinkific Multimedia Lesson zamjenjuje `{{email}}` u URL-u lekcije e-mailom
prijavljenog učenika, pa MAT-BOT na `GET /` dobije adresu bez ijednog dogovora
s Thinkificom — bez App Section-a, OIDC-a, SSO-a i dijeljene tajne. Zato je i
izabrano: ovo je najjednostavniji mehanizam koji uopšte postoji.

Cijena je poznata i prihvaćena: učenik koji ručno otvori
`https://bot.matematicari.com/?thinkific_email=neko@drugi.com` pripisaće svoju
aktivnost tuđoj adresi. Nema načina da server to razlikuje od stvarnog ulaska
kroz lekciju, jer nema potpisa Thinkific strane.

SMIJE se koristiti za: statistiku korištenja, pripisivanje aktivnosti,
mjesečni izvještaj.

NE SMIJE se koristiti za: prikaz tuđeg izvještaja, ovlaštenja, prava na nalogu,
odlučivanje o ocjeni, kontrolu pristupa ili plaćanja, i uopšte za vraćanje bilo
kojeg privatnog podatka. Prvi zahtjev koji bi tražio išta od toga je znak da
treba prava Thinkific autentifikacija — ne proširenje ovog mehanizma.
=============================================================================

ZAŠTO E-MAIL, A NE THINKIFIC user_id: odluka ove faze. Šema to podnosi bez
izmjene — `student_accounts` već ima `UNIQUE(provider, external_user_id)`, pa je
`provider = "thinkific_email"` zaseban prostor imena i kasniji prelazak na
`thinkific_user_id` može dodati DRUGI red uz ISTI `students.id`, bez diranja
istorije aktivnosti.

NORMALIZACIJA JE NAMJERNO GLUPA. Samo `strip()` + `lower()`. Nikakva
transformacija specifična za provajdera: tačke se NE uklanjaju, `+tagovi` se NE
skidaju, aliasi se NE pogađaju. Razlog je jednosmjernost štete — spojiti dva
učenika u jedan izvještaj je nepopravljivo (tuđa aktivnost u tvom izvještaju),
dok je razdvojiti jednog učenika na dva zapisa samo neuredno i popravljivo.
`john.smith@gmail.com` i `johnsmith@gmail.com` zato ostaju DVA identiteta, iako
ih Gmail isporučuje istoj osobi.

MALA SLOVA su jedini izuzetak i on je siguran: domen je po standardu neosjetljiv
na veličinu slova, a nijedan ozbiljan provajder ne razlikuje `Student@` od
`student@` u lokalnom dijelu. Bez toga bi isti učenik dobio novi identitet čim
Thinkific vrati adresu s velikim početnim slovom.
"""
import logging
import re

from matbot import reporting_db

logger = logging.getLogger("matbot.student_identity")

# Prostor imena u `student_accounts.provider`. NIJE isto što i budući
# "thinkific_user_id" — vidi napomenu o migraciji u docstringu.
PROVIDER_THINKIFIC_EMAIL = "thinkific_email"

# Jedini query parametar koji `GET /` uopšte gleda. Ime je isto kao u Thinkific
# lekciji: `https://bot.matematicari.com/?thinkific_email={{email}}`.
QUERY_PARAM = "thinkific_email"

# Gornja granica dužine. RFC dozvoljava 254 znaka za cijelu adresu; duže je
# sigurno smeće i ne smije ući u identitetsku kolonu.
MAX_EMAIL_CHARS = 254
MAX_LOCAL_PART_CHARS = 64

# „Sintaksno uvjerljiv“, NE potpuni RFC 5322 parser. Namjerno konzervativno:
# tačno jedan `@`, neprazan lokalni dio bez razmaka i navodnika, domen s bar
# jednom tačkom i TLD-om od bar dva slova. Cilj nije prihvatiti svaku egzotičnu
# adresu nego odbiti sve što nije očigledno adresa — pogrešan red u tabeli
# identiteta niko kasnije ne može razriješiti.
_EMAIL_RE = re.compile(
    r"\A[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}\Z"
)


def normalize_email(raw):
    """Kanonski oblik e-maila, ili `None`.

    `None` znači „ovo se ne smije koristiti kao identitet“ — pozivalac tada
    nastavlja anonimno, nikad s nekom zamjenskom vrijednošću.

        "  Student@Example.com  "  ->  "student@example.com"
        "student@example.com"      ->  "student@example.com"   (isti identitet)
        "john.smith@gmail.com"     ->  ostaje odvojen od "johnsmith@gmail.com"
        "user+1@example.com"       ->  ostaje odvojen od "user@example.com"

    NEZAMIJENJEN `{{email}}` (učenik nije prijavljen ili lekcija nije
    Multimedia) ne prolazi provjeru i vraća `None` — bez posebnog slučaja.
    """
    if not isinstance(raw, str):
        return None
    # Uklanja se i „nevidljivi“ razmak (NBSP, tab, novi red) koji zna doći kroz
    # kopiranje iz tuđeg sistema; unutrašnji razmak i dalje obara adresu.
    email = raw.strip().strip(" ​").strip()
    if not email or len(email) > MAX_EMAIL_CHARS:
        return None
    email = email.lower()
    if not _EMAIL_RE.match(email):
        return None
    local, _, _domain = email.partition("@")
    if len(local) > MAX_LOCAL_PART_CHARS:
        return None
    return email


def reporting_identity_from_request(args):
    """Pročita `?thinkific_email=` na `GET /`. Vraća tvrdnju ili `None`.

    JEDINO mjesto u aplikaciji gdje identitet ulazi iz klijentskog ulaza. Sve
    poslije toga čita ISKLJUČIVO potpisani token (`auth.reporting_identity`), pa
    nijedan tutorski zahtjev ne može promijeniti pripisivanje.

    Nikad ne baca — `GET /` mora poslužiti stranicu i anonimnom posjetiocu."""
    try:
        raw = args.get(QUERY_PARAM)
    except Exception:
        return None
    if not raw:
        return None
    email = normalize_email(raw)
    if email is None:
        # SAMO kod. Sirova vrijednost se ne loguje ni kad je neispravna — i
        # neispravna adresa je i dalje tuđi podatak.
        logger.info("reporting_identity_rejected code=email_unusable")
        return None
    return {"provider": PROVIDER_THINKIFIC_EMAIL, "external_user_id": email}


def fingerprint(external_user_id):
    """Nepovratan otisak za logove — sirovi e-mail nikad ne ide u log."""
    return reporting_db.fingerprint_subject(PROVIDER_THINKIFIC_EMAIL,
                                            external_user_id or "")


def resolve_student(identity, grade=None):
    """Tvrdnja iz potpisanog tokena -> `students.id`, ili `None`.

    NIKAD ne baca i nikad ne blokira duže od izvještajnog roka — poziva se s
    tutorskog puta, pa važi ista invarijanta kao za cijeli izvještajni sloj:
    nedostupna baza ne smije promijeniti nijedan odgovor učeniku.

    `grade` NIJE dio identiteta. Dolazi iz padajućeg menija u MAT-BOT-u, dakle
    bira ga učenik, i upisuje se samo kao podatak o profilu pri PRVOM susretu.
    Traženje učenika ide isključivo po (provider, external_user_id), pa promjena
    razreda ne može napraviti drugog učenika.

    `display_name` se NAMJERNO ne prosljeđuje: ime nije identitet, a MAT-BOT ga
    nema — iz e-maila se NE izvodi. Ostaje prazno dok ne dođe iz Thinkifica ili
    od nastavnika."""
    if not isinstance(identity, dict):
        return None
    provider = identity.get("provider")
    external = identity.get("external_user_id")
    if provider != PROVIDER_THINKIFIC_EMAIL or not external:
        return None
    return reporting_db.resolve_student(provider, external, grade=grade)
