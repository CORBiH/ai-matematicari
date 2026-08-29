"""Faza 3C — orkestracija mjesečnog izvještaja za roditelja.

TOK, i redoslijed je bitan:

    determinističke činjenice  (report_input → report_facts)
        ↓
    JEDAN model poziv          (llm.report_turn)
        ↓
    serverska provjera         (report_validation)
        ↓
    sačuvani nacrt             (reporting_db.monthly_reports)
        ↓
    administratorska izmjena
        ↓
    PDF                        (report_pdf)

MODEL JE UNUTAR TOKA, NE NA NJEGOVOM VRHU. Sve brojke postoje prije poziva i ne
mijenjaju se poslije njega; model dodaje samo prozu. Zato pad modela ne obara
izvještaj — administrator i dalje vidi sve činjenice, samo bez teksta (Dio 13).

TAČNO JEDAN PLAĆENI POZIV PO GENERISANJU. Nema Reviewera, nema popravke, nema
retryja. Otvaranje stranice, snimanje izmjena i pravljenje PDF-a ne zovu model
NIKAD (Dio 32) — to nije optimizacija nego ugovor: administrator koji uređuje
tekst ne smije slučajno trošiti novac po kliku.
"""
import json
import logging

from matbot import report_facts, report_prompt, report_validation, reporting_db
from matbot import report_input

logger = logging.getLogger("matbot.parent_report")

# Ono što administrator vidi kad model zakaže. Nikad kod, nikad trag greške.
SAFE_AI_ERROR = "AI sažetak trenutno nije moguće generisati."

NARRATIVE_FIELDS = ("summary", "strengths", "focus_areas",
                    "next_month_recommendations")


class ReportGenerationError(RuntimeError):
    """AI nacrt nije napravljen. `code` je INTERNI kod za log, ne za ekran."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def empty_narrative():
    return {"summary": "", "strengths": [], "focus_areas": [],
            "next_month_recommendations": []}


def _clean_items(values, limit_items, limit_chars):
    """Stringovi, orezani, bez praznih. Šema je već garantovala tip."""
    items = []
    for value in list(values or [])[:limit_items]:
        text = (value or "").strip()
        if text:
            items.append(text[:limit_chars])
    return items


def normalize_narrative(raw):
    """Izlaz modela → oblik koji se sprema. Bez tumačenja sadržaja."""
    from matbot import schema as output_schema

    return {
        "summary": (raw.get("summary") or "").strip()[
            :output_schema.MAX_REPORT_SUMMARY_CHARS],
        "strengths": _clean_items(raw.get("strengths"),
                                  output_schema.MAX_REPORT_ITEMS,
                                  output_schema.MAX_REPORT_ITEM_CHARS),
        "focus_areas": _clean_items(raw.get("focus_areas"),
                                    output_schema.MAX_REPORT_ITEMS,
                                    output_schema.MAX_REPORT_ITEM_CHARS),
        "next_month_recommendations": _clean_items(
            raw.get("next_month_recommendations"),
            output_schema.MAX_REPORT_ITEMS,
            output_schema.MAX_REPORT_ITEM_CHARS),
    }


def build_facts(student_id, report_month, database=None):
    """Determinističke činjenice za jedan (učenik, mjesec). Bez modela."""
    payload = report_input.build_report_input(student_id, report_month,
                                              database=database)
    return payload, report_facts.build_ai_facts(payload)


def generate_narrative(facts, llm):
    """JEDAN poziv modela. Vrati provjeren narativ ili baci `ReportGenerationError`.

    Svaki neuspjeh — mreža, rok, neispravan JSON, pala serverska provjera —
    završava isto: bez nacrta. Nikad se ne vraća djelimičan ni popravljen tekst,
    jer bi to bio serverski izmišljen izvještaj o djetetu."""
    from matbot import llm as llm_module

    try:
        result = llm.report_turn(report_prompt.SYSTEM_PROMPT,
                                 report_prompt.build_input_text(facts))
    except llm_module.LLMError as error:
        # Dijagnostika ide u log; poruka za ekran je uvijek ista i bezopasna.
        logger.info("report_ai_failed stage=call code=%s",
                    type(error).__name__)
        raise ReportGenerationError("report_ai_call_failed:"
                                    + type(error).__name__) from None

    # `LLMResult.output` je već validiran pydantic model (`ReportNarrativeOutput`);
    # `_structured_turn` bi ranije bacio da nije. Test dvojnici smiju vratiti
    # običan rječnik, pa se podržavaju oba oblika.
    output = getattr(result, "output", None)
    if hasattr(output, "model_dump"):
        raw = output.model_dump()
    elif isinstance(output, dict):
        raw = output
    else:
        raw = None
    if not isinstance(raw, dict):
        logger.info("report_ai_failed stage=shape")
        raise ReportGenerationError("report_ai_bad_shape")
    for field in NARRATIVE_FIELDS:
        if field not in raw:
            logger.info("report_ai_failed stage=missing_field")
            raise ReportGenerationError("report_ai_missing_field:" + field)

    narrative = normalize_narrative(raw)
    problems = report_validation.validate_narrative(narrative, facts)
    if problems:
        # Kodovi su interni (pravilo 7) — nikad ne idu u HTML.
        logger.info("report_ai_rejected problems=%s", ";".join(problems))
        raise ReportGenerationError("report_ai_rejected:" + problems[0])
    return narrative


def metrics_snapshot(facts, *, model, prompt_version):
    """Ono što se sprema kao `metrics_json`.

    Nosi činjenice OD KOJIH je izvještaj nastao plus minimalne metapodatke o
    nastanku (Dio 31). Bez sirovih razgovora, bez CSV-a, bez e-maila — snimak
    je izveden iz `report_facts`, koji ih po konstrukciji nema.

    VRIJEME GENERISANJA OVDJE NAMJERNO NE STOJI: izmjerena produkcijska tabela
    ima kolonu `generated_at`, pa bi drugi žig u JSON-u bio drugi izvor iste
    istine — a dva žiga se prije ili kasnije raziđu. Model i verzija prompta
    ostaju ovdje jer za njih kolone nema."""
    return {
        "facts": facts,
        "generated_by": {"model": model, "prompt_version": prompt_version},
    }


def load_saved(student_id, report_month, database=None):
    """Sačuvani nacrt u obliku koji UI i PDF koriste, ili None."""
    target = database or reporting_db.get_database()
    row = target.fetch_monthly_report(student_id, report_month)
    if row is None:
        return None
    narrative = empty_narrative()
    if row.get("ai_summary"):
        try:
            stored = json.loads(row["ai_summary"])
            if isinstance(stored, dict):
                narrative = normalize_narrative(stored)
        except (ValueError, TypeError):
            # Nečitljiv zapis se tretira kao „nema teksta", ne kao greška:
            # činjenice i komentar instruktora su i dalje upotrebljivi.
            logger.info("report_saved_narrative_unreadable")
    snapshot = {}
    if row.get("metrics_json"):
        try:
            parsed = json.loads(row["metrics_json"])
            if isinstance(parsed, dict):
                snapshot = parsed
        except (ValueError, TypeError):
            logger.info("report_saved_metrics_unreadable")
    return {
        "id": row["id"],
        "status": row.get("status") or "draft",
        "narrative": narrative,
        "instructor_comment": row.get("instructor_comment") or "",
        "snapshot": snapshot,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        # Vrijeme POSLJEDNJEG AI generisanja — mijenja se samo kad je model zvan.
        "generated_at": row.get("generated_at"),
    }


def utc_now():
    """Kanonski UTC žig, isti oblik kao `CURRENT_TIMESTAMP` (19 znakova)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")


def save_narrative(student_id, report_month, narrative, snapshot,
                   generated_at=None, database=None):
    """Spremi AI tekst i snimak činjenica. NE dira komentar instruktora.

    Zove se SAMO nakon stvarnog poziva modela, pa ovdje `generated_at` uvijek
    dobija novu vrijednost — i pri prvom generisanju i pri ponovnom."""
    target = database or reporting_db.get_database()
    return target.save_monthly_report(
        student_id=student_id, report_month=report_month,
        metrics_json=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
        ai_summary=json.dumps(narrative, ensure_ascii=False, sort_keys=True),
        instructor_comment=None,
        generated_at=generated_at or utc_now())


def save_edits(student_id, report_month, narrative, instructor_comment,
               database=None):
    """Spremi ono što je administrator uredio. NE zove model (Dio 32).

    `generated_at` se NE prosljeđuje: ručna ispravka rečenice nije novo AI
    generisanje i ne smije tako izgledati u reviziji."""
    target = database or reporting_db.get_database()
    return target.save_monthly_report(
        student_id=student_id, report_month=report_month,
        metrics_json=None,
        ai_summary=json.dumps(narrative, ensure_ascii=False, sort_keys=True),
        instructor_comment=instructor_comment or "")
