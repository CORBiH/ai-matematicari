"""Orkestracija JEDNOG Explain turna („Objasni mi“): prompt → jedan AI poziv →
validacija → sanitizacija → response u frontend ugovoru.

Namjerno STATELESS na serveru: Explain nema aktivni zadatak, hint nivo, streak
ni session store — sav potreban kontekst je conversation_history koju frontend
već šalje (max 5 poruka; ovdje koristimo najviše 3 razmjene). Greška AI poziva
vraća kratku sigurnu poruku BEZ 'status' i BEZ 'next_state' (frontend tada čuva
svoje stanje — isti mehanizam kao practice).
"""
import logging
import uuid

from matbot import (config, geometry_rules, geometrycheck, lesson_relevance,
                    practice_policy, prompts)
from matbot.llm import LLMError, failure_diagnostics_kv
from matbot.mathcheck import find_numeric_inconsistencies
from matbot.mathsafe import normalize_result_math_transport, sanitize_and_validate_math_text
from matbot.practice import SAFE_ERROR_MESSAGE
from matbot.schema import InvalidOutputError, validate_explain_output
from matbot.terminology import normalize_terminology
from matbot.topics import lesson_info

logger = logging.getLogger("matbot.explain")

MAX_HISTORY_MESSAGES = 6  # 3 razmjene (učenik + tutor)

# Živi nalaz C-2 (docs/CURRENT_STATE.md): raniji rez na 400 znakova po stavci
# sjekao je i NAJNOVIJI odgovor tutora — koji smije biti do MAX_EXPLAIN_REPLY_CHARS
# (4000) — na svega 400 znakova, PRIJE nego što je uopšte stigao do
# prompts.build_explain_input. To je brisalo tačno onaj dio (konačan rezultat,
# posljednji korak) koji follow-up pitanje ("Zašto si u drugom koraku..."/
# "Odakle je došla formula...") najčešće traži.
#
# Ovdje se SADA ne odsjeca ništa novo — koristi se ISTA gornja granica koju
# validation.py._check_history VEĆ garantuje za svaku stavku (config.
# MAX_HISTORY_CHARS_PER_ITEM, podrazumijevano 3000): ta provjera odbija CIO
# zahtjev (400) prije nego što ijedan red ovog fajla uopšte izvrši, pa
# ponavljanje istog broja ovdje NIJE aktivno sječenje u normalnom API putu —
# to je samo defense-in-depth granica za pozivaoce koji zaobiđu API sloj
# (npr. testovi koji direktno zovu run_explain_turn). Konačan, po-poziciji-
# zavisan budžet (najnoviji odgovor tutora dobija više prostora, uz čuvanje
# KRAJA teksta gdje je obično rezultat) primjenjuje prompts.build_explain_input
# — ono odlučuje finije sječenje, ovaj gornji rez samo osigurava da sirovi
# sadržaj STIGNE do te odluke necjelovito odsječen s POČETKA.
HISTORY_ITEM_RAW_CHARS = config.MAX_HISTORY_CHARS_PER_ITEM


def _clean_history(raw_history):
    """Frontend šalje [{'role','content'}, ...]. Zadrži samo validne stavke,
    isjeci sadržaj i uzmi najviše zadnje 3 razmjene. Klijentski sadržaj se
    tretira kao nepouzdan tekst (ide samo u prompt, nikad u stanje).

    Konačan, po-poziciji-zavisan budžet (najnoviji odgovor tutora dobija više
    prostora od starijih razmjena) primjenjuje prompts.build_explain_input —
    ovdje se samo ograničava GORNJA granica sirovog teksta koji uopšte ulazi
    u tu odluku."""
    if not isinstance(raw_history, list):
        return []
    cleaned = []
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        cleaned.append({"role": role, "content": content.strip()[:HISTORY_ITEM_RAW_CHARS]})
    return cleaned[-MAX_HISTORY_MESSAGES:]


def _observe_method_policy(answer, grade, lesson_title, oblast, lesson_id,
                           request_id):
    """MJERENJE, NE KAPIJA — kurikularna politika (PP-1) nad Explain odgovorom.

    ZAŠTO SAMO MJERENJE (forenzički trag modova, 2026-08-20): Vježbajmo istu
    politiku SPROVODI (`tutor/pipeline.py`), a Explain je do sada nije ni
    gledao. Ali detektori su građeni nad PAKETOM zadatka, ne nad slobodnom
    prozom objašnjenja, i nad ovom površinom imaju dvije dokazane klase lažnog
    pozitiva:

      • leksički: `find_forbidden_method_prose` traži stem „prebac“, pa jednako
        pogađa „Prebacimo član na drugu stranu“ i „NE prebacujemo član na drugu
        stranu“ — a druga rečenica UČI tačno pravilo. Uzorak se namjerno NE
        proširuje negacijom: isti uzorak je živa kapija Vježbajma i takva
        izmjena bi je oslabila („Ne zaboravi: prebacimo član…“ bi prošlo).
      • opseg: Explain po dokazanom nalazu D35-3 SMIJE odgovoriti na pitanje iz
        druge teme, pa šestaš koji pita za Pitagorinu teoremu legitimno vidi
        $\\sqrt{\\;}$ — isti zapis koji je u Practice paketu prekršaj razreda.

    Zato ovaj poziv NIŠTA ne odbija i ne mijenja odgovor. Loguje kodove (samo
    interno — CLAUDE.md pravilo 7), razdvojene po TIPU dokaza, i uz njih
    NEZAVISAN pozitivan signal: koje je uloge nepoznatog člana odgovor imenovao.
    Kombinacija „leksički pogodak + nijedna uloga“ je kandidat za stvarni
    prekršaj; „leksički pogodak + imenovana uloga“ je kandidat za negaciju.
    Tvrda kapija se smije uvesti tek kad TO mjerenje postoji."""
    policy = practice_policy.resolve(
        grade=grade, lesson_id=lesson_id, lesson_title=lesson_title,
        oblast=oblast,
    )
    if not policy.scan_method_prose:
        return
    codes = practice_policy.text_policy_failures(policy, answer)
    roles = practice_policy.unknown_member_role_mentions(answer)
    if not codes and not roles:
        return
    logger.info(
        "explain_method_policy_observed request_id=%s grade=%s topic=%s "
        "method=%s lexical=%s structural=%s roles_named=%s enforcement=log_only",
        request_id, policy.grade, lesson_id or "-", policy.equation_method,
        ",".join(c for c in codes
                 if c in practice_policy.LEXICAL_POLICY_CODES) or "-",
        ",".join(c for c in codes
                 if c in practice_policy.STRUCTURAL_POLICY_CODES) or "-",
        ",".join(roles) or "-",
    )


def run_explain_turn(llm, turn):
    """turn: očišćeni dict iz api.py. Vraća JSON-spreman dict."""
    request_id = uuid.uuid4().hex[:12]

    # Canonical podaci iz topics.json — klijentskom selected_oblast se NE
    # vjeruje (ista politika kao practice).
    lesson = lesson_info(turn["grade"], turn["selected_topic"])
    lesson_id = lesson["id"] if lesson else (turn["selected_topic"] or "")
    lesson_title = lesson["title"] if lesson else ""
    oblast = lesson["oblast"] if lesson else (turn["selected_oblast"] or "")

    # Živi nalaz D35-3: izabrana lekcija je ranije BEZUSLOVNO ulazila u prompt
    # kao tema koju treba predati, pa je pitanje o uglovima trougla dobilo uvod
    # iz lekcije o razlomcima. Odluka je determinističa i bez AI poziva; kad se
    # ne može dokazati da je poruka iz druge teme, ostaje ranije ponašanje.
    lesson_context_strong = lesson_relevance.lesson_context_is_strong(
        turn["student_message"], lesson_title, oblast
    )

    instructions = prompts.build_explain_instructions(
        turn["grade"], lesson_title=lesson_title, oblast=oblast,
        lesson_context_strong=lesson_context_strong,
    )
    input_text = prompts.build_explain_input(
        lesson_title=lesson_title,
        oblast=oblast,
        history=_clean_history(turn.get("conversation_history")),
        student_message=turn["student_message"],
        interaction_phase=turn["interaction_phase"],
        last_tutor_message=turn.get("last_tutor_message", ""),
        lesson_context_strong=lesson_context_strong,
    )

    try:
        result = llm.explain_turn(instructions, input_text)
        validate_explain_output(result.output)
    except LLMError as e:
        logger.warning(
            "explain_turn request_id=%s category=%s mode=explain %s",
            request_id, e.category, failure_diagnostics_kv(e),
        )
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    except InvalidOutputError as e:
        logger.warning("explain_turn request_id=%s category=invalid_output detail=%s", request_id, e)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    except Exception:
        logger.exception("explain_turn request_id=%s unexpected_error", request_id)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

    # Transport popravka PRIJE centralnog safety boundary-a (isto što Quick
    # već radi, matbot/quick.py) — živi nalaz C-10 (docs/CURRENT_STATE.md):
    # model ponekad preescapeuje MathJax delimitere same (\$...\$) ili ostavi
    # dupli backslash pred poznatom komandom nakon JSON parsiranja. Explain je
    # RANIJE takav (inače ispravan) odgovor odbijao cio, umjesto da ga popravi
    # kao Quick — učenik je gubio cio odgovor bez razloga.
    transported, transport_safe = normalize_result_math_transport(result.output.reply.strip())

    # Isti centralni safety boundary kao Practice/Quick (matbot/mathsafe.py) —
    # allow_whole_expression_wrap ostaje False: Explain je proza, nikad se ne
    # umata cio odgovor u $...$, samo se pokušava usko/sigurno popraviti
    # (izolovan \frac{a}{b}, doslovni "\n"). Ako i dalje nebezbjedno nakon
    # toga → odbij CIO odgovor, isti sigurni fallback kao za LLMError/
    # InvalidOutputError iznad, bez drugog AI poziva.
    answer, is_safe = sanitize_and_validate_math_text(transported)
    if not transport_safe or not is_safe:
        logger.warning("explain_turn request_id=%s category=unsafe_math_output", request_id)
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}
    answer = normalize_terminology(answer)

    # Numerička dosljednost lanca jednakosti (matbot/mathcheck.py) — živi nalaz
    # je bio baš u Explain modu ($\frac{3\cdot16\sqrt{3}}{2}=48\sqrt{3}$).
    # Bez drugog AI poziva: dokazana nedosljednost → isti sigurni fallback.
    numeric_issues = find_numeric_inconsistencies(answer)
    if numeric_issues:
        logger.warning("explain_turn request_id=%s category=invalid_output detail=%s",
                       request_id, numeric_issues[0])
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

    # Geometrijska notacija (matbot/geometrycheck.py). Explain odgovor je UVIJEK
    # autoritativan (nema distraktora ni namjerno pogrešnih tvrdnji), pa se
    # provjerava bez izuzetka. Opseg i figure dolaze iz CANONICAL lekcije, nikad
    # iz učenikove poruke. Bez drugog AI poziva: povreda → isti sigurni fallback.
    geometry_scope, geometry_figures = geometry_rules.route_geometry_topic(oblast, lesson_title)
    geometry_issues = geometrycheck.find_geometry_issues(answer, geometry_scope, geometry_figures)
    if geometry_issues:
        logger.warning("explain_turn request_id=%s category=invalid_output detail=geometry_notation:%s",
                       request_id, ",".join(geometry_issues))
        return {"answer": SAFE_ERROR_MESSAGE, "last_tutor_task": ""}

    _observe_method_policy(answer, turn["grade"], lesson_title, oblast,
                           lesson_id, request_id)

    logger.info(
        "explain_turn request_id=%s ok latency_ms=%s usage=%s",
        request_id, result.latency_ms, result.usage,
    )

    return {
        "status": "ready",
        "answer": answer,
        "answer_verdict": None,     # Explain NIKAD ne ocjenjuje
        "last_tutor_task": "",      # Explain NIKAD nema aktivni zadatak
        "next_state": {},           # bez Practice stanja (frontend ovo bezbjedno guta)
        "session_mode": "explain",
        "effective_topic": lesson_id or "",
    }
