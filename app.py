from flask import Flask, jsonify, render_template, request

from matbot.api import ai_tutor_bp
from matbot.topics import topics_response

app = Flask(__name__)
app.register_blueprint(ai_tutor_bp)


@app.route("/")
def index():
    return render_template("index.html")


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
