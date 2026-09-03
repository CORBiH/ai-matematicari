from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import logging
import os

from matbot import admin_auth, auth, config, release_config, student_identity
from matbot.admin_reports import admin_reports_bp
from matbot.admin_sessions import admin_sessions_bp
from matbot.admin_students import admin_students_bp
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

# PRIVATNA administratorska stranica mjesecnih izvjestaja (Faza 3B).
# Potpuno odvojen blueprint: ne dijeli stanje ni put izvrsavanja s tutorskim
# rutama, pa njegov kvar ne moze promijeniti Practice, Explain, Quick ni
# Kontrolni. Bez `MATBOT_ADMIN_PASSWORD` sve njene rute vracaju 404 -- stranica
# se tada ponasa kao da ne postoji.
admin_auth.apply_cookie_hardening(app)
# Navigacija se iscrtava po SERVERSKOM stanju prijave, a svaki
# administratorski odgovor nosi `no-store`. Vidi docstring.
admin_auth.install_admin_hardening(app)
app.register_blueprint(admin_reports_bp)
# Faza 3D: registar učenika i evidencija časova — ista admin autentikacija.
app.register_blueprint(admin_students_bp)
app.register_blueprint(admin_sessions_bp)


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
    # Kratkotrajni potpisani token — frontend ga već čita iz ove meta tag
    # vrijednosti i šalje kao X-Tutor-Token na svaki /api/ai-tutor/* poziv
    # (templates/index.html).
    #
    # OVO JE JEDINO MJESTO GDJE IZVJEŠTAJNI IDENTITET ULAZI U MAT-BOT.
    # Thinkific Multimedia Lesson otvara app kao
    # `https://bot.matematicari.com/?thinkific_email={{email}}` i sam zamijeni
    # `{{email}}` adresom prijavljenog učenika. Ovdje se adresa normalizuje i
    # UGRADI u token koji server potpisuje; od te tačke pripisivanje putuje
    # kanalom koji klijent ne može izmijeniti, pa nijedan API endpoint ne mora
    # — i ne smije — gledati e-mail iz tijela zahtjeva.
    #
    # GRANICA: ovo je pripisivanje za izvještaj, NE autentifikacija. Puna
    # napomena i šta se s njom smije, a šta ne — `matbot/student_identity.py`.
    #
    # Bez parametra ili s neispravnom adresom token se kuje kao i do sada, BEZ
    # identiteta: učenik radi normalno, anonimno.
    identity = student_identity.reporting_identity_from_request(request.args)
    return render_template("index.html", embed_token=auth.issue_token(identity))


def _health_payload():
    """`version` = APP_VERSION koju deploy upisuje u .env (kratki commit SHA).

    POSTOJI ZBOG IZMJERENOG TIHOG KVARA (2026-08-15): produkcija je 30+
    deployova posluživala zamrznutu staru verziju dok je `{"ok": true}` bio
    zelen — zdravlje BEZ identiteta nije dokaz deploya. Deploy workflow sada
    poredi ovu vrijednost sa pushanim commitom PREKO JAVNOG DOMENA, pa
    divergencija domena i deploy mete pada glasno umjesto da bude nevidljiva.
    Kratki SHA javnog repozitorija nije tajna."""
    return {"ok": True, "version": os.environ.get("APP_VERSION", "")}


@app.route("/healthz")
def healthz():
    return _health_payload(), 200


@app.route("/_healthz")
def _healthz():
    return _health_payload(), 200


@app.route("/api/ai-tutor/topics")
def ai_tutor_topics():
    grade = request.args.get("grade", "")
    return jsonify(topics_response(grade))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
