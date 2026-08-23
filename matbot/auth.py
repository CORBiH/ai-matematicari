"""Potpisani kratkotrajni frontend token — anonimna zaštita javnog API-ja od
direktnih automatizovanih poziva bez prethodnog učitavanja MAT-BOT stranice.

VAŽNO: ovo NIJE Thinkific identitet učenika niti prava autentifikacija korisnika.
To je samo dokaz da je klijent nedavno učitao GET / (gdje se token kuje), potpisan
postojećim FLASK_SECRET_KEY koristeći itsdangerous (već tranzitivna Flask
zavisnost — nema nove kriptografije, nema nove zavisnosti u requirements.txt).
"""
import logging
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from matbot import config

logger = logging.getLogger("matbot.auth")

TOKEN_HEADER = "X-Tutor-Token"
TOKEN_PURPOSE = "matbot_api"
_SALT = "matbot-embed-token"


class TokenError(Exception):
    """code: 'MISSING' | 'INVALID' | 'EXPIRED' | 'BAD_PURPOSE' — za interne
    logove/telemetriju; klijentu se uvijek vraća ista prijateljska poruka."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _serializer():
    return URLSafeTimedSerializer(config.SECRET_KEY, salt=_SALT)


# --- Izvještajni identitet u tokenu --------------------------------------
# ŠTA JE OVO, A ŠTA NIJE (granica proizvoda, ne samo koda):
#
# Thinkific Multimedia Lesson zamjenjuje `{{email}}` u URL-u lekcije e-mailom
# PRIJAVLJENOG učenika, pa MAT-BOT na `GET /` dobije adresu bez ijednog
# dogovora s Thinkificom — bez App Section-a, OIDC-a, SSO-a i dijeljene tajne.
#
# TO NIJE KRIPTOGRAFSKI DOKAZ IDENTITETA. Učenik koji ručno otvori
# `.../?thinkific_email=neko@drugi.com` pripisaće svoju aktivnost tuđoj adresi.
# Taj rizik je IZRIČITO prihvaćen, i to samo za: statistiku korištenja,
# pripisivanje aktivnosti i mjesečni izvještaj.
#
# ZATO OVA VRIJEDNOST NIKAD NE SMIJE POSTATI OVLAŠTENJE. Ne smije otvoriti tuđ
# izvještaj, dati prava, odlučivati o ocjeni, pristupu ili plaćanju, niti
# vratiti ijedan privatan podatak. Ako ikad zatreba išta od toga, tada — i samo
# tada — treba prava Thinkific autentifikacija.
#
# ŠTA POTPIS OVDJE IPAK RJEŠAVA: e-mail se čita SAMO na `GET /` i odmah veže u
# token koji server potpisuje `FLASK_SECRET_KEY`-em. Poslije toga nijedan
# tutorski zahtjev ne može promijeniti pripisivanje — ni poljem u JSON-u, ni
# query parametrom, ni izmjenom tokena. Dakle: ne štiti od učenika koji na
# početku upiše tuđu adresu, ali štiti od toga da BILO KOJI zahtjev poslije
# učitavanja stranice tiho preusmjeri tuđu aktivnost.
REPORTING_IDENTITY_CLAIM = "reporting_identity"
# Verzija oblika tvrdnje. Postoji da bi prelazak na drugi izvor identiteta
# (npr. `thinkific_user_id`) mogao teći uporedo sa starim tokenima koji su još
# u opticaju — stari token se tada odbaci kao nepoznata verzija, ne pogrešno
# protumači.
REPORTING_IDENTITY_VERSION = 1
# Zatvorena lista dozvoljenih provajdera. Token je naš i potpisan, ali ovo
# sprečava da buduća greška upiše provajdera koji izvještajni sloj ne poznaje.
ALLOWED_REPORTING_PROVIDERS = frozenset({"thinkific_email"})


def issue_token(reporting_identity=None):
    """Kuje token na GET /. Sadrži purpose + nasumični nonce — nikad API ključ,
    expected answers, session state ni prompt.

    `reporting_identity` je jedini korisnički podatak koji smije ući: rječnik
    `{"provider": ..., "external_user_id": ...}` koji je pozivalac već
    normalizovao. Bez njega se kuje tačno onakav anoniman token kakav je i do
    sada postojao.

    POŠTENA GRANICA: itsdangerous POTPISUJE, ali ne ŠIFRUJE. Učenik koji pogleda
    izvor SVOJE stranice vidi SVOJ e-mail — podatak koji je Thinkific lekcija
    koja ga ugrađuje ionako imala u URL-u. Tuđ e-mail se ne može ni pročitati ni
    podmetnuti bez `FLASK_SECRET_KEY`."""
    payload = {"purpose": TOKEN_PURPOSE, "nonce": secrets.token_urlsafe(16)}
    claim = _normalized_identity_claim(reporting_identity)
    if claim is not None:
        payload[REPORTING_IDENTITY_CLAIM] = claim
    return _serializer().dumps(payload)


def _normalized_identity_claim(reporting_identity):
    """Rječnik -> tvrdnja spremna za potpis, ili `None`. Nikad ne baca."""
    if not isinstance(reporting_identity, dict):
        return None
    provider = reporting_identity.get("provider")
    external = reporting_identity.get("external_user_id")
    if provider not in ALLOWED_REPORTING_PROVIDERS:
        return None
    if not isinstance(external, str) or not external:
        return None
    return {"v": REPORTING_IDENTITY_VERSION,
            "provider": provider,
            "external_user_id": external}


def reporting_identity(data):
    """Izvještajni identitet iz VEĆ provjerenog tokena, ili `None`.

    Ulaz mora biti povratna vrijednost `verify_token` — nikad sirovi payload
    zahtjeva, nikad query string API endpointa. Ovo je JEDINI način na koji
    ostatak aplikacije smije saznati kome se aktivnost pripisuje.

    Nepoznata verzija ili provajder van zatvorene liste tretiraju se kao
    ODSUSTVO identiteta (anonimno), nikad kao greška koja bi obarala turn."""
    if not isinstance(data, dict):
        return None
    claim = data.get(REPORTING_IDENTITY_CLAIM)
    if not isinstance(claim, dict):
        return None
    if claim.get("v") != REPORTING_IDENTITY_VERSION:
        return None
    provider = claim.get("provider")
    external = claim.get("external_user_id")
    if provider not in ALLOWED_REPORTING_PROVIDERS:
        return None
    if not isinstance(external, str) or not external:
        return None
    return {"provider": provider, "external_user_id": external}


def verify_token(token):
    """Baca TokenError na svaki neuspjeh. Nikad ne otkriva detalje potpisa."""
    if not token:
        raise TokenError("MISSING")
    try:
        data = _serializer().loads(token, max_age=config.TOKEN_TTL_SECONDS)
    except SignatureExpired:
        raise TokenError("EXPIRED")
    except BadSignature:
        raise TokenError("INVALID")
    except Exception:
        raise TokenError("INVALID")
    if not isinstance(data, dict) or data.get("purpose") != TOKEN_PURPOSE:
        raise TokenError("BAD_PURPOSE")
    return data
