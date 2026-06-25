"""MAT-BOT — historija razgovora: sanitizacija ulaza klijenta i gradnja poruka.

Bot HTML -> čisti tekst za kontekst modelu; sve neispravno se tiho odbacuje.
Izdvojeno iz app.py (refactor) — ponašanje NEPROMIJENJENO.
"""
import os, re, html


# ---------------- Historija razgovora ----------------
HISTORY_MAX_TURNS = int(os.getenv("HISTORY_MAX_TURNS", "5"))        # koliko parova čuvamo iz klijenta
HISTORY_MAX_CHARS = int(os.getenv("HISTORY_MAX_CHARS", "2000"))     # cap po poruci (kontrola tokena)
HISTORY_CONTEXT_TURNS = int(os.getenv("HISTORY_CONTEXT_TURNS", "5"))  # koliko parova ide modelu

_HTML_TAG_RE = re.compile(r"<[^>]+>")

def strip_html_to_text(s: str) -> str:
    """HTML odgovora bota → čisti tekst za kontekst modelu (bez tagova, sa novim redovima)."""
    s = re.sub(r"<br\s*/?>", "\n", s or "", flags=re.IGNORECASE)
    s = re.sub(r"</p>\s*<p>", "\n", s, flags=re.IGNORECASE)
    s = _HTML_TAG_RE.sub("", s)
    return html.unescape(s).strip()

def sanitize_history(raw) -> list:
    """Validira historiju iz klijenta: lista {user, bot} stringova, ograničene dužine,
    bot HTML pretvoren u čisti tekst. Sve neispravno se tiho odbacuje."""
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw[-HISTORY_MAX_TURNS:]:
        if not isinstance(item, dict):
            continue
        u, b = item.get("user"), item.get("bot")
        if not isinstance(u, str) or not isinstance(b, str):
            continue
        u = u.strip()[:HISTORY_MAX_CHARS]
        b = strip_html_to_text(b)[:HISTORY_MAX_CHARS]
        if u or b:
            out.append({"user": u, "bot": b})
    return out

def _append_history_messages(messages: list, history):
    for msg in (history or [])[-HISTORY_CONTEXT_TURNS:]:
        if not isinstance(msg, dict):
            continue
        u = msg.get("user") or ""
        b = strip_html_to_text(msg.get("bot") or "")
        if not (u or b):
            continue
        messages.append({"role": "user", "content": u})
        messages.append({"role": "assistant", "content": b})
