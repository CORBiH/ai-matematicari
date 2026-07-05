"""Phase 6 — detekcija teme za free_chat (heuristike + opcioni LLM klasifikator).

Cilj: učenik može samo upisati pitanje bez biranja teme. Ako je tema jasna,
koristi se ``detected_topic``; ako nije, vraća se ``"unknown"`` — tema se NIKAD
ne izmišlja (svaki rezultat se validira protiv mastera preko
``validate_detected_topic``).

Redoslijed:
1. ``detect_topic_heuristic`` — sigurne leksičke heuristike. Obrasci su statični,
   ali kandidat-teme se prihvataju SAMO ako postoje u masteru (Excel ostaje izvor
   istine). Široki pojmovi (npr. "razlomci") mapiraju se na PRVU temu tog prefiksa
   u redoslijedu TOPICS sheeta — data-driven, bez hardkodiranog sadržaja.
2. ``detect_topic_llm`` — jeftin klasifikator kroz injektovani ``openai_chat``
   (isti kao app._openai_chat). Vraća isključivo JSON; sve nevalidno → unknown.
   U testovima se mockira — nikad stvarni API poziv.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from matbot.content_loader import get_master, get_thinkific_map, normalize_value
from matbot.topic_lookup import validate_detected_topic

log = logging.getLogger("matbot.topic_detector")

UNKNOWN = "unknown"

# Signal da je poruka konkretan matematički zadatak/pitanje (brojevi, operatori,
# tipični glagoli) — bez ovoga i bez tematskih ključnih riječi poruka je "vague".
_MATH_SIGNAL_RE = re.compile(
    r"\d|[+\-*/=<>%^√·×÷]|\b(izra[čc]unaj|rije[šs]i|zadatak|koliko|jedna[čc]in\w*|nejedna[čc]in\w*)\b",
    re.IGNORECASE,
)

# (pattern, kandidati po tačnom id-u, prefiks za prvu temu iz sheet-a)
# Redoslijed je bitan: specifičnije prije širih.
_RULES: tuple[tuple[re.Pattern, tuple[str, ...], str], ...] = (
    (re.compile(r"cijel\w*\s+broj|sabir\w*\s+cijel|oduzim\w*\s+cijel", re.I), ("cijeli_sabiranje_oduzimanje",), "cijeli_"),
    (re.compile(r"mno[Ĺľz]en\w*\s+cijel|dijeljen\w*\s+cijel", re.I), ("cijeli_mnozenje_dijeljenje",), "cijeli_"),
    (re.compile(r"racionaln\w*\s+broj", re.I), (), "racionalni_"),
    (re.compile(r"\bvektor", re.I), (), "vektori_"),
    (re.compile(r"izometrij|translacij|rotacij|osn\w*\s+simetrij|centraln\w*\s+simetrij", re.I), (), "izometrije_"),
    (re.compile(r"trougl", re.I), (), "trougao_"),
    (re.compile(r"[ÄŤc]etverougl|paralelogram|trapez|romb|pravougaonik|kvadrat", re.I), (), "cetverougao_"),
    (re.compile(r"aritmeti[čc]k\w*\s+sredin\w*|\bprosjek\w*", re.I), ("aritmeticka_sredina",), ""),
    (re.compile(r"\bnzd\b|najve[ćc]\w* zajedni[čc]k\w* djel", re.I), ("djeljivost_NZD",), "djeljivost_"),
    (re.compile(r"\bnzs\b|najmanj\w* zajedni[čc]k\w* sadr", re.I), ("djeljivost_NZS",), "djeljivost_"),
    (re.compile(r"prost\w*\s+broj", re.I), ("djeljivost_prosti",), "djeljivost_"),
    (re.compile(r"djelitelj|djelioc|djelilac|djeljiv|sadr[žz]ilac|sadr[žz]ioc", re.I), (), "djeljivost_"),
    (re.compile(r"\bdecimaln", re.I), (), "decimalni_"),
    (re.compile(r"razlom(ak|k|c|c[ie])", re.I), (), "razlomci_"),
    (re.compile(r"\b\d+\s*/\s*\d+\b"), (), "razlomci_"),
    (re.compile(r"\bkomplement", re.I), ("skupovi_komplement",), "skupovi_"),
    (re.compile(r"\bunij[aiu]|\bpresjek", re.I), ("skupovi_operacije",), "skupovi_"),
    (re.compile(r"\bpodskup|prazan\s+skup", re.I), ("skupovi_prazan",), "skupovi_"),
    (re.compile(r"\bvenn", re.I), ("skupovi_iz_vennovog_dijagrama",), "skupovi_"),
    (re.compile(r"\bskup(ovi|ova|u|a)?\b|\belement\w*\s+skup", re.I), (), "skupovi_"),
    (re.compile(r"kru[žz]nic|krug\b|krugov", re.I), ("kruznica_i_krug", "elementi_kruznice"), ""),
    (re.compile(r"\btetiv|tangent", re.I), ("centralni_ugao_luk_tetiva", "konstrukcija_tangente"), ""),
    (re.compile(r"\bug(ao|la|lu|lovi|love|lova)\b|uglomjer", re.I), ("ugao_pojam_elementi", "vrste_uglova"), ""),
)


def is_vague_message(text: Any) -> bool:
    """True ako je poruka preopćenita za odgovor (npr. "Ne razumijem", "Pomozi").

    Konkretna = sadrži matematički signal (brojevi/operatori/glagoli) ILI pogađa
    neku tematsku heuristiku ("decimalni brojevi", "razlomci", ...)."""
    t = normalize_value(text)
    if not t:
        return True
    # matematički signal PRIJE provjere dužine — i "5-1" je konkretan zadatak
    if _MATH_SIGNAL_RE.search(t):
        return False
    if len(t) < 4:
        return True
    for pattern, _cands, _prefix in _RULES:
        if pattern.search(t):
            return False
    return True


def _first_topic_with_prefix(prefix: str, master: dict) -> str | None:
    """Prva tema datog prefiksa u redoslijedu TOPICS sheeta (data-driven izbor)."""
    if not prefix:
        return None
    for row in master.get("topics", []):
        tid = row.get("topic", "")
        if tid.startswith(prefix):
            return tid
    return None


def detect_topic_heuristic(message: Any, master: dict | None = None) -> str:
    """Leksička detekcija. Vraća topic id iz mastera ili ``"unknown"``."""
    master = master if master is not None else get_master()
    text = normalize_value(message)
    if not text:
        return UNKNOWN
    topic_ids = master.get("topic_ids", set())
    for pattern, candidates, prefix in _RULES:
        if not pattern.search(text):
            continue
        for cand in candidates:
            if cand in topic_ids:
                return cand
        by_prefix = _first_topic_with_prefix(prefix, master)
        if by_prefix:
            return by_prefix
    return UNKNOWN


_JSON_RE = re.compile(r"\{[^{}]*\}")


def detect_topic_llm(
    message: Any,
    master: dict,
    tmap: dict,
    openai_chat: Callable,
    model: str,
    timeout: float | None = None,
) -> str:
    """Jeftin LLM klasifikator: smije vratiti SAMO postojeću temu ili unknown.

    Svaki izlaz se validira kroz ``validate_detected_topic``; garbage/izmišljeno
    → unknown. Nikad ne baca izuzetak."""
    try:
        text = normalize_value(message)[:1000]
        topics_list = "\n".join(
            f"- {t['topic']} ({t.get('display_name', '')})" for t in master.get("topics", [])
        )
        messages = [
            {
                "role": "system",
                "content": (
                    f"Ti si klasifikator tema za matematiku {master.get('grade', 6)}. razreda (BiH). "
                    "Odgovori ISKLJUČIVO JSON-om oblika "
                    '{"detected_topic": "<topic_id>"} ili {"detected_topic": "unknown"}. '
                    "Dozvoljene su SAMO teme sa liste. Ako nisi siguran, vrati unknown. "
                    "Ne dodaji nikakav drugi tekst."
                ),
            },
            {
                "role": "user",
                "content": f"Poruka učenika:\n{text}\n\nDozvoljene teme:\n{topics_list}",
            },
        ]
        resp = openai_chat(model, messages, timeout=timeout, max_tokens=60)
        raw = resp.choices[0].message.content or ""
        m = _JSON_RE.search(raw)
        if not m:
            return UNKNOWN
        tid = normalize_value(json.loads(m.group(0)).get("detected_topic"))
        verdict = validate_detected_topic(tid, master, tmap)
        return verdict["topic"] if verdict["status"] == "found" else UNKNOWN
    except Exception:
        log.warning("topic_detector: LLM klasifikacija nije uspjela → unknown", exc_info=True)
        return UNKNOWN


def detect_topic(
    message: Any,
    master: dict | None = None,
    tmap: dict | None = None,
    openai_chat: Callable | None = None,
    model: str = "gpt-5-mini",
    timeout: float | None = None,
) -> dict:
    """Heuristike pa (opciono) LLM. Vraća ``{"detected_topic": ..., "method":
    "heuristic"|"llm"|"none"}``; tema je uvijek validna ili unknown."""
    master = master if master is not None else get_master()
    tmap = tmap if tmap is not None else get_thinkific_map()

    tid = detect_topic_heuristic(message, master)
    if tid != UNKNOWN:
        return {"detected_topic": tid, "method": "heuristic"}

    if openai_chat is not None and not is_vague_message(message):
        tid = detect_topic_llm(message, master, tmap, openai_chat, model, timeout)
        if tid != UNKNOWN:
            return {"detected_topic": tid, "method": "llm"}

    return {"detected_topic": UNKNOWN, "method": "none"}
