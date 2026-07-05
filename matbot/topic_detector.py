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

Robusnost na dijakritike: ulaz se prije poklapanja normalizuje kroz
``fold_diacritics`` (č/ć→c, đ→d, š→s, ž→z + lowercase), pa svi obrasci u
``_RULES`` žive u ASCII prostoru. Učenici često pišu bez kvačica; ranije su
obrasci sa dijakriticima bili i korumpirani (mojibake), pa npr. "četverougao"
nije bio prepoznat.
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

# BiH dijakritici → ASCII; obuhvata i velika slova (poslije ide .lower()).
_DIACRITIC_MAP = str.maketrans({
    "č": "c", "ć": "c", "đ": "d", "š": "s", "ž": "z",
    "Č": "c", "Ć": "c", "Đ": "d", "Š": "s", "Ž": "z",
})


def fold_diacritics(text: Any) -> str:
    """Normalizuj tekst za heuristike: dijakritici → ASCII + lowercase.

    "Šta je četverougao?" → "sta je cetverougao?" — isti obrazac pokriva
    i pisanje sa i bez kvačica."""
    return normalize_value(text).translate(_DIACRITIC_MAP).lower()


# Signal da je poruka konkretan matematički zadatak/pitanje (brojevi, operatori,
# tipični glagoli) — bez ovoga i bez tematskih ključnih riječi poruka je "vague".
# NAPOMENA: obrazac je u ASCII prostoru — poklapa se na fold_diacritics(tekst).
_MATH_SIGNAL_RE = re.compile(
    r"\d|[+\-*/=<>%^√·×÷]|\b(izracunaj|rijesi|zadatak|koliko|jednacin\w*|nejednacin\w*)\b"
)

# (pattern, kandidati po tačnom id-u, prefiks za prvu temu iz sheet-a)
# Obrasci se poklapaju na FOLDANOM tekstu (bez dijakritika, lowercase).
# Redoslijed je bitan: SPECIFIČNIJE PRIJE ŠIRIH — npr. "množenje cijelih" mora
# doći prije generičkog "cijeli brojevi", inače pitanje o množenju završi na
# temi sabiranja.
_RULES: tuple[tuple[re.Pattern, tuple[str, ...], str], ...] = (
    # --- 8. razred: specifični pojmovi mapirani na postojeće topic ID-eve ---
    (re.compile(r"\b(?:eksponent\w*|baz[aeu]\b|stepenovanj\w*|stepen(?:i|e)\b|stepen(?:i|a|om)?\s+(?:broj|izraz|pravila|sa)|pravila\s+stepen\w*|na\s+(?:kvadrat|kub)|\w+\s*(?:\^|²|³))"),
     ("stepeni_pravila_i_pojasnjenja_stepeni",), "stepeni_"),
    (re.compile(r"\b(?:korijen\w*|korjen\w*|sqrt|√)"),
     ("realni_korijeni_pravila_za_racunske_operacije",), "realni_"),
    (re.compile(r"\b(?:iracionaln\w*|realn\w*\s+broj\w*)"),
     ("realni_iracionalni_brojevi_i_skup_realnih_brojeva",), "realni_"),
    (re.compile(r"pitagor|hipotenuz|katet|pravougl\w*\s+trougl"),
     ("pitagora_pitagorina_teorema_osnovno",), "pitagora_"),
    (re.compile(r"kvadrat\w*\s+binom"),
     ("polinomi_kvadrat_binoma",), "polinomi_"),
    (re.compile(r"\b(?:polinom|monom|binom)\w*|slicn\w*\s+monom|razlik\w*\s+kvadrat|rastavlj\w*\s+polinom"),
     ("polinomi_sta_je_to_polinom_a_sta_nije",), "polinomi_"),
    (re.compile(r"algebarsk\w*\s+razlom|definicion\w*\s+podruc|domen[aeu]?\b|imenilac\s+(?:ne\s+smije|ne\s+sme|nije)\s+biti\s+nula|imenilac\s*(?:!=|≠)\s*0"),
     ("alg_razlomci_definiciono_podrucje_domena_i_nula_razlomljene_racionalne_funkcije",), "alg_razlomci_"),
    (re.compile(r"kruznic|\bkrug\b|krugov|poluprecnik|precnik|sekant|tangent"),
     ("kruznica_krug_prava_i_kruznica_centralna_udaljenost_prave_sekanta_tangenta",), "kruznica_krug_"),
    (re.compile(r"tales|slicnost\w*\s+trougl|slicn\w*\s+trougl"),
     ("tales_slicnost_talesova_teorema",), "tales_slicnost_"),
    (re.compile(r"razmjer|omjer|mjerilo|proporcij|proporcionalnost"),
     ("proporcije_razmjera_omjer_mjerilo",), "proporcije_"),
    (re.compile(r"koordinat|dekart|kvadrant|grafik|graf\s+linearn|linearn\w*\s+funkc|[xy]\s+os[aei]"),
     ("koordinatni_funkcija_pravougli_dekartov_koordinatni_sistem",), "koordinatni_funkcija_"),
    (re.compile(r"mnogougl|mnogouga|sestougl|sestouga|petougl|petouga|dijagonal\w*\s+mnogougl"),
     ("mnogougao_sta_je_mnogougao_a_sta_nije",), "mnogougao_"),
    (re.compile(r"\bvaljak|cilindar"),
     ("tijela_valjak_osnove",), "tijela_valjak"),
    (re.compile(r"\bkupa\b|\bkupe\b"),
     ("tijela_kupa_osnove",), "tijela_kupa"),
    (re.compile(r"\blopta\b|\blopte\b"),
     ("tijela_lopta_zadatak_1",), "tijela_lopta"),
    (re.compile(r"\bkock[aeu]\b"),
     ("tijela_kocka_zadatak_1",), "tijela_kocka"),
    (re.compile(r"\bkvadar\b|\bkvadra\b"),
     ("tijela_kvadar_zadatak_1",), "tijela_kvadar"),
    (re.compile(r"\bprizm"),
     ("tijela_prizme_osnove",), "tijela_prizme"),
    (re.compile(r"\bpiramid"),
     ("tijela_piramide_osnove",), "tijela_piramide"),
    (re.compile(r"geometrijsk\w*\s+tijel|zapremin"),
     ("tijela_koraci_kod_rjesavanja_zadataka_geometrijska_tijela_pdf",), "tijela_"),

    # --- cijeli brojevi (7. razred): operacije PRIJE generičkog pojma ---
    (re.compile(r"(?:mnoz|pomnoz|dijel|podijel)\w*\s+cijel"), ("cijeli_mnozenje_dijeljenje",), "cijeli_"),
    (re.compile(r"(?:sabir|saber|oduzim|oduzm|zbir)\w*\s+cijel"), ("cijeli_sabiranje_oduzimanje",), "cijeli_"),
    (re.compile(r"cijel\w*\s+broj"), (), "cijeli_"),
    (re.compile(r"racionaln\w*\s+broj"), (), "racionalni_"),
    (re.compile(r"\bvektor"), (), "vektori_"),
    (re.compile(r"izometrij|translacij|rotacij|osn\w*\s+simetrij|centraln\w*\s+simetrij"), (), "izometrije_"),
    (re.compile(r"\btroug"), (), "trougao_"),
    (re.compile(r"cetveroug|cetvoroug|paralelogram|trapez|romb|pravougaonik|kvadrat"), (), "cetverougao_"),
    (re.compile(r"aritmetick\w*\s+sredin\w*|\bprosjek\w*"), ("aritmeticka_sredina",), ""),
    (re.compile(r"\bnzd\b|najvec\w*\s+zajednick\w*\s+djel"), ("djeljivost_NZD",), "djeljivost_"),
    (re.compile(r"\bnzs\b|najmanj\w*\s+zajednick\w*\s+sadr"), ("djeljivost_NZS",), "djeljivost_"),
    (re.compile(r"prost\w*\s+broj"), ("djeljivost_prosti",), "djeljivost_"),
    (re.compile(r"djelitelj|djelioc|djelilac|djeljiv|sadrzilac|sadrzioc"), (), "djeljivost_"),
    (re.compile(r"\bdecimaln"), (), "decimalni_"),
    (re.compile(r"\b\d+\s*/\s*\d+\s*(?:[*xX]|\u00b7|\u00d7)\s*\d+\s*/\s*\d+\b"),
     ("razlomci_mnozenje_razlomkom_svojstva",), "razlomci_mnozenje"),
    (re.compile(r"(?:(?:mnoz|pomnoz)\w*(?:\s+\w+){0,4}\s+razlom\w*|razlom\w*(?:\s+\w+){0,4}\s+(?:mnoz|pomnoz)\w*)"),
     ("razlomci_mnozenje_razlomkom_svojstva",), "razlomci_mnozenje"),
    (re.compile(r"razlom(ak|k|c|c[ie])"), (), "razlomci_"),
    (re.compile(r"\b\d+\s*/\s*\d+\b"), (), "razlomci_"),
    (re.compile(r"\bkomplement"), ("skupovi_komplement",), "skupovi_"),
    (re.compile(r"\bunij[aiu]|\bpresjek"), ("skupovi_operacije",), "skupovi_"),
    (re.compile(r"\bpodskup|prazan\s+skup"), ("skupovi_prazan",), "skupovi_"),
    (re.compile(r"\bvenn"), ("skupovi_iz_vennovog_dijagrama",), "skupovi_"),
    (re.compile(r"\bskup(ovi|ova|u|a)?\b|\belement\w*\s+skup"), (), "skupovi_"),
    (re.compile(r"kruznic|\bkrug\b|krugov"), ("kruznica_i_krug", "elementi_kruznice"), ""),
    (re.compile(r"\btetiv|tangent"), ("centralni_ugao_luk_tetiva", "konstrukcija_tangente"), ""),
    (re.compile(r"\bug(ao|la|lu|lovi|love|lova)\b|uglomjer"), ("ugao_pojam_elementi", "vrste_uglova"), ""),
)


def is_vague_message(text: Any) -> bool:
    """True ako je poruka preopćenita za odgovor (npr. "Ne razumijem", "Pomozi").

    Konkretna = sadrži matematički signal (brojevi/operatori/glagoli) ILI pogađa
    neku tematsku heuristiku ("decimalni brojevi", "razlomci", ...)."""
    t = fold_diacritics(text)
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
    text = fold_diacritics(message)
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
