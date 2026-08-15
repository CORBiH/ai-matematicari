from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import logging

from matbot import auth, config, release_config
from matbot.api import REQUEST_TOO_LARGE_MESSAGE, ai_tutor_bp
from matbot.request_limits import BoundedInMemoryRequest
from matbot.topics import topics_response

config.require_secret_key(config.SECRET_KEY)

# JEDAN red na startu s EFEKTIVNOM konfiguracijom — nikad tajna.
#
# ZAŠTO POSTOJI: produkcija je radila bez dvije zastavice arhitekture, dok su
# release gate-ovi mjerili drugu konfiguraciju. (Jedna od njih je u
# međuvremenu povučena zajedno sa starim Practice motorom.) Ništa u logu to nije pokazivalo, pa je odstupanje otkriveno tek
# ručnim testom. Sadržaj reda je zatvorena lista ne-tajnih vrijednosti iz
# matbot/release_config.py.
logging.getLogger("matbot.startup").info(
    "matbot_effective_configuration %s", release_config.format_effective_configuration()
)

# ODSTUPANJE PADA ZATVORENO — ali samo u produkcijskom izdanju.
#
# Raniji oblik je odstupanje prijavljivao kao WARNING i nastavljao, uz komentar
# da o padu zatvoreno odlučuje „deploy provjera“. Takva provjera nije
# postojala: `require_release_configuration` nije zvao NIKO — ni start, ni
# deploy workflow, ni pre-push, ni ijedan test. Zbog toga je pogrešna
# konfiguracija i dalje mogla tiho posluživati učenike, jer WARNING u logu
# kontejnera niko ne gleda dok je `/healthz` zelen.
#
# `MATBOT_RELEASE_ENFORCEMENT=enabled` razdvaja produkcijsko izdanje od običnog
# lokalnog/testnog uvoza: bez tog razdvajanja bi svaki lokalni `import app`
# padao, pa bi guard morao biti isključen — a isključen guard je tačno stanje
# koje je i dovelo do tihog odstupanja. Da ni sam prekidač ne bi mogao tiho
# nestati, deploy dodatno zove `python -m matbot.release_config --require`
# BEZUSLOVNO, a sam prekidač je jedna od obaveznih vrijednosti.
if release_config.release_enforcement_enabled():
    release_config.require_release_configuration()
else:
    for _problem in release_config.release_configuration_problems():
        logging.getLogger("matbot.startup").warning(
            "matbot_release_configuration_mismatch %s", _problem
        )

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# Tvrda granica veličine HTTP tijela + upload isključivo u memoriji (bez
# Werkzeug spoolinga na disk) — vidi matbot/request_limits.py. Granica važi za
# SVE endpointe; tekstualni zahtjevi su tri reda veličine manji, pa ih ne dira.
app.request_class = BoundedInMemoryRequest
app.config["MAX_CONTENT_LENGTH"] = config.MAX_REQUEST_BYTES

app.register_blueprint(ai_tutor_bp)


@app.errorhandler(RequestEntityTooLarge)
def _request_too_large(_e):
    """413 u POSTOJEĆEM JSON ugovoru grešaka ({"error","detail"}), koji
    frontend već zna prikazati (applyTutorResponse čita `detail`). Odgovor
    NEMA `status`, pa frontend zadržava priloženu sliku i svoje stanje."""
    return jsonify({"error": "REQUEST_TOO_LARGE", "detail": REQUEST_TOO_LARGE_MESSAGE}), 413

# Produkcijski VPS potvrđen (2026): Nginx je JEDINI reverse proxy ispred ove
# aplikacije, vezane na 127.0.0.1:8080, i postavlja X-Forwarded-For te
# X-Forwarded-Proto. x_for=1/x_proto=1 znači "vjeruj tačno JEDNOM hopu" —
# request.remote_addr (koji matbot/api.py._client_ip već koristi za IP rate
# limit) će nakon ovoga predstavljati stvarnu klijentsku adresu umjesto
# nginx-ove. Namjerno BEZ x_host/x_port/x_prefix — ništa u ovoj aplikaciji ne
# treba te vrijednosti, a vjerovanje njima bez potvrđene potrebe širi napadnu
# površinu bez koristi.
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
)


@app.route("/")
def index():
    # Kratkotrajni potpisani token (anonimna zaštita API-ja, NE Thinkific
    # identitet) — frontend ga već čita iz ove meta tag vrijednosti i šalje
    # kao X-Tutor-Token na svaki /api/ai-tutor/* poziv (templates/index.html).
    return render_template("index.html", embed_token=auth.issue_token())


@app.route("/healthz")
def healthz():
    return {"ok": True}, 200


@app.route("/_healthz")
def _healthz():
    return {"ok": True}, 200


@app.route("/api/ai-tutor/topics")
def ai_tutor_topics():
    grade = request.args.get("grade", "")
    return jsonify(topics_response(grade))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
