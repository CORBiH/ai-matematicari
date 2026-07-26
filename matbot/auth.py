"""Potpisani kratkotrajni frontend token — anonimna zaštita javnog API-ja od
direktnih automatizovanih poziva bez prethodnog učitavanja MAT-BOT stranice.

VAŽNO: ovo NIJE Thinkific identitet učenika niti prava autentifikacija korisnika.
To je samo dokaz da je klijent nedavno učitao GET / (gdje se token kuje), potpisan
postojećim FLASK_SECRET_KEY koristeći itsdangerous (već tranzitivna Flask
zavisnost — nema nove kriptografije, nema nove zavisnosti u requirements.txt).
"""
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from matbot import config

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


def issue_token():
    """Kuje token na GET /. Sadrži SAMO purpose + nasumični nonce — nikad
    API ključ, expected answers, session state, prompt ili korisničke podatke."""
    payload = {"purpose": TOKEN_PURPOSE, "nonce": secrets.token_urlsafe(16)}
    return _serializer().dumps(payload)


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
