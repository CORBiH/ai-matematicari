"""Autorizacija privatne administratorske stranice izvještaja.

ZAŠTO POSTOJI NOV MEHANIZAM: repozitorij ga do sada NIJE imao. `matbot/auth.py`
kuje ANONIMAN kratkotrajni token (`purpose` + `nonce`) čiji vlastiti docstring
kaže da to „NIJE Thinkific identitet učenika niti prava autentifikacija
korisnika" — on dokazuje samo da je klijent nedavno učitao `GET /`. Taj token
zato NIKAD ne smije otvarati administratorsku stranicu: svaki učenik ga ima.
`DIAG_TOKEN` iz `.env.example` pripada UKLONJENOM starom backendu i nijedan red
današnjeg koda ga ne čita.

Stranica prikazuje napredak stvarnih učenika, pa zaštita opskurnošću (nepoznat
URL) nije zaštita. Ovdje je najmanji robustan mehanizam koji se uklapa u
postojeću arhitekturu — bez nove zavisnosti, bez baze korisnika, bez tuđe
biblioteke:

  • LOZINKA JE SERVERSKA KONFIGURACIJA (`MATBOT_ADMIN_PASSWORD`), kao i svaka
    druga tajna ovog projekta — živi samo u `.env` na VPS-u, nikad u repou,
    nikad u HTML-u, JS-u ili URL-u;
  • PRAZNA LOZINKA = ADMIN JE ISKLJUČEN. Isti obrazac kao `reporting_db_configured`
    i `thinkific_identity`: polovična konfiguracija znači „ugašeno", nikad
    „propusti pa vidi". Bez toga bi zaboravljena varijabla otvorila stranicu
    svakome;
  • POREĐENJE JE U KONSTANTNOM VREMENU (`hmac.compare_digest`) — obično `==`
    curi koliko se početnih znakova poklopilo;
  • SESIJA JE FLASK-OV POTPISAN KOLAČIĆ, potpisan istim `FLASK_SECRET_KEY`
    kojim se već potpisuje `X-Tutor-Token`. Nema novog kripto koda i nema
    serverskog stanja koje bi restart izgubio;
  • CSRF token živi U SESIJI i provjerava se na svakom POST-u — bez njega bi
    tuđa stranica mogla natjerati prijavljenog administratora da uveze fajl;
  • PRIJAVA JE OGRANIČENA PO STOPI, kroz postojeći `matbot/ratelimit.py`, pa
    lozinka nije podložna brzom pogađanju.

TVRDA GRANICA: ovaj modul ne dodiruje nijedan tutorski put. Pad ovdje ne može
promijeniti Practice, Explain, Quick ni Kontrolni.
"""
import hmac
import logging
import os
import secrets

from flask import redirect, request, session, url_for

logger = logging.getLogger("matbot.admin_auth")

SESSION_KEY = "matbot_admin"
CSRF_SESSION_KEY = "matbot_admin_csrf"
CSRF_FORM_FIELD = "csrf_token"

# Minimalna dužina lozinke koju uopšte prihvatamo kao konfiguraciju. Kratka
# lozinka na javnom URL-u je isto što i nikakva, pa se odbija PRI STARTU
# provjere, a ne tek pri prvom napadu.
MIN_ADMIN_PASSWORD_CHARS = 12


def admin_password():
    return (os.environ.get("MATBOT_ADMIN_PASSWORD", "") or "").strip()


def admin_enabled():
    """Administratorska stranica postoji SAMO uz konfigurisanu, dovoljno dugu
    lozinku. Sve ostalo (prazno, prekratko) znači potpuno isključeno."""
    return len(admin_password()) >= MIN_ADMIN_PASSWORD_CHARS


def verify_password(candidate):
    """Poređenje u konstantnom vremenu. `False` kad je admin isključen."""
    expected = admin_password()
    if not admin_enabled() or not isinstance(candidate, str) or not candidate:
        return False
    return hmac.compare_digest(expected, candidate)


def start_session():
    """Označi sesiju kao administratorsku i iskuj svjež CSRF token."""
    session.clear()
    session[SESSION_KEY] = True
    session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    session.permanent = False        # kolačić traje do zatvaranja pregledača
    return session[CSRF_SESSION_KEY]


def end_session():
    session.clear()


def is_authenticated():
    """Prijavljen SAMO ako je admin uopšte uključen.

    Redoslijed je bitan: gašenje lozinke u `.env`-u mora ODMAH obesmisliti i već
    izdate kolačiće, umjesto da stara sesija nadživi isključenje."""
    return bool(admin_enabled() and session.get(SESSION_KEY) is True)


def csrf_token():
    """Token iz sesije; kuje se ako nedostaje (npr. poslije restarta)."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_valid(submitted):
    expected = session.get(CSRF_SESSION_KEY)
    if not expected or not isinstance(submitted, str) or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)


def require_admin(view):
    """Dekorator: svaka administratorska ruta prolazi ovuda.

    Neprijavljen zahtjev se PREUSMJERAVA na prijavu (GET) ili odbija sa 403
    (POST) — nikad se ne obrađuje. Kad je admin isključen konfiguracijom, ruta
    se ponaša kao da ne postoji (404), da se ni postojanje stranice ne otkriva."""
    from functools import wraps

    @wraps(view)
    def guarded(*args, **kwargs):
        if not admin_enabled():
            logger.info("admin_route_disabled path=%s", request.path)
            return ("Nije dostupno.", 404)
        if not is_authenticated():
            logger.info("admin_auth_required path=%s method=%s",
                        request.path, request.method)
            if request.method == "GET":
                return redirect(url_for("admin_reports.login"))
            return ("Prijava je istekla. Osvježi stranicu i prijavi se ponovo.", 403)
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if not csrf_valid(request.form.get(CSRF_FORM_FIELD)):
                logger.info("admin_csrf_rejected path=%s", request.path)
                return ("Sigurnosna provjera nije prošla. Osvježi stranicu.", 400)
        return view(*args, **kwargs)

    return guarded


def apply_cookie_hardening(app):
    """Postavke kolačića sesije. Zovu se JEDNOM, pri sastavljanju aplikacije.

    `Secure` se traži samo kad aplikacija stvarno radi preko HTTPS-a: u
    produkciji iza nginxa da, ali lokalni `http://127.0.0.1` razvoj bi s tom
    zastavicom prestao raditi bez ikakve dobiti."""
    # IZRICITO PRIDRUZIVANJE, NE `setdefault`: Flask te kljuceve VEC definise
    # (`SAMESITE=None`, `SECURE=False`), pa `setdefault` ne bi promijenio nista
    # i kolacic bi ostao bez ijedne od ovih zastita. Izmjereno pri sastavljanju.
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # `Secure` je PODRAZUMIJEVANO ukljucen (produkcija radi preko HTTPS-a iza
    # nginxa). Lokalni razvoj i testovi ga gase izricito, jer testni klijent
    # postuje zastavicu i ne bi slao kolacic preko http.
    app.config["SESSION_COOKIE_SECURE"] = (
        (os.environ.get("MATBOT_ADMIN_COOKIE_SECURE", "") or "")
        .strip().lower() != "disabled")
    return app
