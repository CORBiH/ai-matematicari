"""Faza 3C — A4 PDF mjesečnog izvještaja, iz SAČUVANOG nacrta.

ULAZ JE ONO ŠTO JE SPREMLJENO, NIKAD SVJEŽ ODGOVOR MODELA. Zato je PDF
determinističan u odnosu na nacrt: isti sačuvani zapis daje isti dokument, a
administrator koji je tekst uredio dobija tačno svoj tekst. Ovdje se model ne
zove nikad (Dio 32).

FONT: DejaVu Sans, VENDOROVAN U REPO (`matbot/assets/fonts/`). Ugrađeni
Helvetica ne bi poslužio — njegovo standardno kodiranje nema č, ć, ž, š ni đ,
pa bi izvještaj na bosanskom bio pun praznina.

ZAŠTO NE `Vera.ttf` KOJI STIŽE S REPORTLABOM. Vera nema glif za veliko `Đ`
(U+0110) i iscrtavala ga je kao prazan kvadratić — a to pogađa stvarna imena
(Đurđević, Đozić, Đemal), pa nije kozmetika. Kvar je nađen VIZUELNOM kontrolom,
jer je prvo mjerenje bilo pogrešno: `ord(znak) in face.charToGlyph` vraća True i
kad ključ pokazuje na glif 0 (`.notdef`), a `pypdf` izvuče znak jednako u oba
slučaja — dakle i tekstualni sloj PDF-a i tadašnji test su tvrdili da je slovo
tu. Ispravno mjerenje traži NENULTI identifikator glifa; to radi
`missing_glyphs()` i na njemu stoji test.

ZAŠTO VENDOROVAN, A NE SISTEMSKI (`fonts-dejavu-core` u Dockerfileu). Lokalni
razvoj, testovi, Docker i produkcija moraju imati BAJT ZA BAJT isti fajl.
Razlika lokalno/produkcija je tačno ona klasa greške koja je ovaj kvar i
napravila, pa se ne uvodi nova. Porijeklo, verzija i kontrolne sume stoje u
`matbot/assets/fonts/README.md`, licenca u `LICENSE-DejaVu.txt` pored fontova.

FONT SE NE TRAŽI PO SISTEMU I NE ZAVISI OD RADNOG DIREKTORIJA: put se računa iz
`__file__` ovog modula, pa je svejedno odakle su gunicorn ili pytest pokrenuti.
Ako asset nedostaje, pravi se VIDLJIVA greška (`ReportFontMissing`) — nikad se
tiho ne pada na font s nepotpunim pokrivanjem, jer bi to vratilo upravo kvar
zbog kojeg ovaj fajl i postoji.

Ime učenika se NIKAD ne prepisuje (Đ→Dj) da bi stalo u font: ime u službenom
dokumentu nije naše da ga mijenjamo.

DVIJE STRANE SU TVRDA GRANICA. Dokument koji bi prešao dvije strane znači da je
predložak pogriješio u procjeni, pa se pravi VIDLJIVA greška umjesto tihog
odsijecanja teksta (Dio 18) — roditelj nikad ne smije dobiti rečenicu prekinutu
na pola.

PRIVATNOST: u dokument ulaze samo ime, razred, mjesec, mjere za roditelja,
odobrena proza i komentar instruktora. E-mail, `student_id`, Thinkific ID,
sirova pitanja i interni kodovi ne ulaze nikad (Dio 20).
"""
import io
import os

MAX_PAGES = 2

# Bosanski nazivi mjeseci — datum u izvještaju za roditelja ne smije biti
# „2026-08", a ni engleski „August".
MONTH_NAMES = ("januar", "februar", "mart", "april", "maj", "juni",
               "juli", "august", "septembar", "oktobar", "novembar", "decembar")

BRAND = "MATEMATIČARI"
DOC_TITLE = "Mjesečni izvještaj"

# Registruje se pod PRAVIM imenom fonta. Licenca dopušta preimenovanje samo
# IZMIJENJENIH kopija, a ove su neizmijenjene — pa nema razloga da se u PDF-u
# predstavljaju kao nešto drugo.
_FONT_REGULAR = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"
_fonts_ready = False

# Put se izvodi iz `__file__`, NE iz radnog direktorija: gunicorn i pytest se
# pokreću odakle stignu, a dokument roditelju ne smije zavisiti od toga.
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "fonts")
FONT_FILES = {_FONT_REGULAR: "DejaVuSans.ttf",
              _FONT_BOLD: "DejaVuSans-Bold.ttf"}

# Slova koja izvještaj na bosanskom MORA imati. Drži se kao PODATAK, a ne kao
# komentar, da bi test mogao tvrditi „svako od ovih ima stvarni glif". Latinica
# i cifre su tu kao kontrola zdravlja mjerenja — kad bi mjerenje bilo pokvareno,
# palo bi i na njima, pa prazan rezultat ne bi značio „sve je u redu".
REQUIRED_GLYPHS = frozenset("čćžšđČĆŽŠĐ"
                            "abcdefghijklmnopqrstuvwxyz"
                            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                            "0123456789")


class PdfTooLong(RuntimeError):
    """Dokument ne stane u `MAX_PAGES`. Vidljiva greška, ne odsječen tekst."""


class ReportFontMissing(RuntimeError):
    """Vendorovani font nedostaje ili je neupotrebljiv.

    Namjerno se NE pada na reportlabovu Veru: ona nema `Đ`, pa bi tihi fallback
    vratio kvar zbog kojeg je font i vendorovan — samo bez ijednog traga."""


def font_path(font_name):
    """Apsolutni put do vendorovanog fajla, bez traženja po sistemu."""
    return os.path.join(FONT_DIR, FONT_FILES[font_name])


def missing_glyphs(font_name, chars=REQUIRED_GLYPHS):
    """Slova bez STVARNOG glifa u datom fontu; prazan skup znači puno pokriće.

    MJERI SE NENULTI IDENTIFIKATOR GLIFA. `ord(znak) in face.charToGlyph` je
    pogrešna provjera: ključ postoji i kad pokazuje na glif 0 (`.notdef`), pa je
    jednom već tvrdio da `Đ` postoji dok se u PDF-u iscrtavao prazan kvadratić."""
    from reportlab.pdfbase.ttfonts import TTFont

    face = TTFont(font_name, font_path(font_name)).face
    return {ch for ch in chars if face.charToGlyph.get(ord(ch), 0) == 0}


def _ensure_fonts():
    """Registruj vendorovani DejaVu jednom po procesu."""
    global _fonts_ready
    if _fonts_ready:
        return
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for name in (_FONT_REGULAR, _FONT_BOLD):
        path = font_path(name)
        if not os.path.isfile(path):
            raise ReportFontMissing("nedostaje vendorovani font: " + path)
        try:
            pdfmetrics.registerFont(TTFont(name, path))
        except Exception as exc:
            raise ReportFontMissing(
                "neupotrebljiv font %s: %s" % (path, type(exc).__name__)) from None
    pdfmetrics.registerFontFamily(_FONT_REGULAR, normal=_FONT_REGULAR,
                                  bold=_FONT_BOLD)
    _fonts_ready = True


def month_label(report_month):
    """„2026-08" → „August 2026". Pada nazad na ulaz ako oblik nije očekivan."""
    try:
        year, month = report_month.split("-")
        name = MONTH_NAMES[int(month) - 1]
        return name.capitalize() + " " + year
    except (ValueError, IndexError, AttributeError):
        return report_month or ""


def _fmt_percent(value):
    if value is None:
        return "nema podatka"
    if float(value) == int(float(value)):
        return "%d%%" % int(float(value))
    return ("%.1f%%" % float(value)).replace(".", ",")


def _fmt_delta(value):
    """Promjena u procentnim poenima. `None` znači: nema osnove za poređenje."""
    if value is None:
        return None
    number = float(value)
    sign = "+" if number > 0 else ""
    text = ("%d" % int(number)) if number == int(number) else ("%.1f" % number)
    return "%s%s p.p." % (sign, text.replace(".", ","))


def _styles():
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib import colors

    ink = colors.HexColor("#1f2937")
    muted = colors.HexColor("#4b5563")
    accent = colors.HexColor("#1d4ed8")
    return {
        "brand": ParagraphStyle("brand", fontName=_FONT_BOLD, fontSize=15,
                                leading=18, textColor=accent, alignment=TA_LEFT),
        "doctitle": ParagraphStyle("doctitle", fontName=_FONT_REGULAR, fontSize=10.5,
                                   leading=13, textColor=muted, spaceAfter=8),
        "meta": ParagraphStyle("meta", fontName=_FONT_REGULAR, fontSize=9.5,
                               leading=13, textColor=ink),
        "h2": ParagraphStyle("h2", fontName=_FONT_BOLD, fontSize=9.5, leading=12,
                             textColor=accent, spaceBefore=9, spaceAfter=4),
        "body": ParagraphStyle("body", fontName=_FONT_REGULAR, fontSize=9.2,
                               leading=12.6, textColor=ink, spaceAfter=3),
        # `bulletFontName` se postavlja IZRIČITO: platypus inače za tačku uzme
        # Helveticu, pa bi dokument i dalje zavisio od fonta bez bosanskog
        # pokrivanja — bez ijednog vidljivog traga dok se ne promijeni znak.
        "bullet": ParagraphStyle("bullet", fontName=_FONT_REGULAR, fontSize=9.2,
                                 leading=12.6, textColor=ink, leftIndent=10,
                                 bulletIndent=2, spaceAfter=2,
                                 bulletFontName=_FONT_REGULAR, bulletFontSize=9.2),
        "note": ParagraphStyle("note", fontName=_FONT_REGULAR, fontSize=8.6,
                               leading=11.4, textColor=muted, spaceAfter=2),
        "cell": ParagraphStyle("cell", fontName=_FONT_REGULAR, fontSize=9,
                               leading=11.6, textColor=ink),
        "cellhead": ParagraphStyle("cellhead", fontName=_FONT_BOLD, fontSize=9,
                                   leading=11.6, textColor=ink),
    }


def _escape(text):
    """Platypus čita mini-markup, pa se ulaz mora neutralisati.

    Tekst je već prošao `report_validation.markup_violations`, ali predložak se
    ne oslanja na to: administrator smije ručno unijeti bilo šta u komentar."""
    return ((text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _metric_table(rows, styles, widths):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    data = [[Paragraph(_escape(label), styles["cellhead"]),
             Paragraph(_escape(value), styles["cell"])] for label, value in rows]
    table = Table(data, colWidths=widths, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e5e7eb")),
    ]))
    return table


def _practice_rows(facts):
    """Redovi o radu s MAT-BOT-om, s terminologijom iz Dijela 23.

    „Prikazano" i „odgovoreno" su odvojeni redovi upravo zato da 37 i 24 ne bi
    izgledali kao kontradikcija."""
    matbot = facts.get("matbot") or {}
    practice = matbot.get("practice") or {}
    answered = practice.get("answers_total") or 0
    accuracy = practice.get("accuracy_percent")
    rows = [
        ("Aktivnih dana", str(matbot.get("active_days") or 0)),
        ("Zadataka prikazano", str(practice.get("tasks_presented") or 0)),
        ("Zadataka odgovoreno", str(answered)),
        ("Tačnih / netačnih", "%d / %d" % (practice.get("correct") or 0,
                                           practice.get("incorrect") or 0)),
        # Bez imenioca nema mjere. „0 % tačnosti" bi bila izmišljena tvrdnja o
        # učeniku koji nije odgovarao ni na jedan zadatak.
        ("Tačnost među odgovorenim",
         _fmt_percent(accuracy) if answered else "nema odgovorenih zadataka"),
        ("Nagovještaji / gotova rješenja",
         "%d / %d" % (practice.get("hints_used") or 0,
                      practice.get("full_solutions_shown") or 0)),
        ("Objašnjenja / Rezultat",
         "%d / %d" % (matbot.get("explain_count") or 0,
                      matbot.get("quick_count") or 0)),
    ]
    return rows


def _story(facts, narrative, instructor_comment, label, styles, content_width):
    from reportlab.platypus import KeepTogether, Paragraph, Spacer

    story = [
        Paragraph(_escape(BRAND), styles["brand"]),
        Paragraph(_escape(DOC_TITLE), styles["doctitle"]),
    ]

    grade = facts.get("grade")
    meta = [("Učenik", label),
            ("Razred", ("%d. razred" % int(grade)) if grade else "nije poznat"),
            ("Period", month_label(facts.get("report_month")))]
    story.append(_metric_table(meta, styles, [content_width * 0.28,
                                              content_width * 0.72]))

    if (narrative.get("summary") or "").strip():
        story.append(Paragraph("SAŽETAK MJESECA", styles["h2"]))
        story.append(Paragraph(_escape(narrative["summary"]), styles["body"]))

    # --- Thinkific -------------------------------------------------------
    story.append(Paragraph("NAPREDAK NA PLATFORMI", styles["h2"]))
    thinkific = facts.get("thinkific") or {}
    if not thinkific.get("available"):
        story.append(Paragraph(
            "Thinkific Progress podaci nisu dostupni za ovaj mjesec.",
            styles["body"]))
    else:
        rows = [("Pregledano sadržaja", _fmt_percent(thinkific.get("percent_viewed"))),
                ("Završeno kursa", _fmt_percent(thinkific.get("percent_completed")))]
        if thinkific.get("previous_available"):
            for title, key in (("Promjena — pregledano", "delta_percent_viewed"),
                               ("Promjena — završeno", "delta_percent_completed")):
                delta = _fmt_delta(thinkific.get(key))
                if delta:
                    rows.append((title, delta))
        story.append(_metric_table(rows, styles, [content_width * 0.42,
                                                  content_width * 0.58]))
        if not thinkific.get("previous_available"):
            story.append(Paragraph(
                "Prethodni mjesec nije dostupan za poređenje.", styles["note"]))
        sections = thinkific.get("sections") or []
        if sections:
            story.append(Paragraph("Napredak po oblastima kursa:", styles["note"]))
            # ISTE širine kao ostale tabele mjera: vrijednosti u dokumentu
            # moraju stajati u jednoj vertikali. Ranije 0,62/0,38 — pri čemu su
            # procenti sekcija „bježali" udesno u odnosu na sve druge brojeve.
            story.append(_metric_table(
                [(s["name"], _fmt_percent(s.get("current_percent")))
                 for s in sections],
                styles, [content_width * 0.42, content_width * 0.58]))

    # --- MAT-BOT ---------------------------------------------------------
    story.append(Paragraph("RAD SA MAT-BOT-OM", styles["h2"]))
    matbot = facts.get("matbot") or {}
    if not matbot.get("any_activity"):
        story.append(Paragraph(
            "Nema zabilježene MAT-BOT aktivnosti u ovom mjesecu.", styles["body"]))
    else:
        story.append(_metric_table(_practice_rows(facts), styles,
                                   [content_width * 0.42, content_width * 0.58]))

    # --- Kontrolni -------------------------------------------------------
    story.append(Paragraph("KONTROLNI", styles["h2"]))
    kontrolni = (matbot.get("kontrolni") or {})
    if not kontrolni.get("attempts"):
        story.append(Paragraph(
            "U ovom mjesecu nije zabilježen nijedan kontrolni.", styles["body"]))
    else:
        story.append(_metric_table([
            ("Urađenih kontrolnih", str(kontrolni.get("attempts") or 0)),
            ("Prosječan rezultat",
             _fmt_percent(kontrolni.get("average_score_percent"))),
            ("Tačnih odgovora", "%d od %d" % (kontrolni.get("correct_total") or 0,
                                              kontrolni.get("question_total") or 0)),
        ], styles, [content_width * 0.42, content_width * 0.58]))

    # --- AI proza --------------------------------------------------------
    for title, key, fallback in (
            ("ŠTA IDE DOBRO", "strengths",
             "Za pouzdaniju procjenu jakih strana potrebno je više riješenih zadataka."),
            ("NA ČEMU TREBA RADITI", "focus_areas",
             "Trenutno nema dovoljno podataka za pouzdan zaključak."),
            ("PREPORUKA ZA NAREDNI MJESEC", "next_month_recommendations", None)):
        items = narrative.get(key) or []
        if not items and fallback is None:
            continue
        story.append(Paragraph(title, styles["h2"]))
        if items:
            for item in items:
                story.append(Paragraph(_escape(item), styles["bullet"],
                                       bulletText="•"))
        else:
            story.append(Paragraph(_escape(fallback), styles["body"]))

    if (instructor_comment or "").strip():
        story.append(Paragraph("KOMENTAR INSTRUKTORA", styles["h2"]))
        story.append(Paragraph(_escape(instructor_comment), styles["body"]))

    return story


def _page_decoration(canvas, doc):
    """Broj strane SAMO kad ih ima više od jedne (Dio 18).

    Zna se tek na kraju gradnje, pa se prva strana ostavi bez broja i dopiše se
    naknadno u `render_report_pdf`."""
    canvas.saveState()
    canvas.setFont(_FONT_REGULAR, 7.5)
    canvas.setFillGray(0.45)
    if doc.page > 1:
        canvas.drawRightString(doc.pagesize[0] - 15 * 2.83465, 12 * 2.83465,
                               "Strana %d" % doc.page)
    canvas.restoreState()


def render_report_pdf(facts, narrative, instructor_comment, label):
    """Sačuvani nacrt → bajtovi PDF-a. Nikad ne zove model."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    _ensure_fonts()
    styles = _styles()
    buffer = io.BytesIO()
    margin = 16 * mm
    doc = BaseDocTemplate(buffer, pagesize=A4,
                          leftMargin=margin, rightMargin=margin,
                          topMargin=14 * mm, bottomMargin=14 * mm,
                          title=DOC_TITLE, author=BRAND,
                          # Bez `subject`/`keywords`: ništa interno u metapodacima.
                          creator=BRAND)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="body", leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame],
                                       onPage=_page_decoration)])
    story = _story(facts, narrative, instructor_comment, label, styles, doc.width)
    doc.build(story)
    if doc.page > MAX_PAGES:
        # Radije vidljiv kvar nego dokument s odsječenom rečenicom.
        raise PdfTooLong("izvještaj zauzima %d strane (najviše %d)"
                         % (doc.page, MAX_PAGES))
    return buffer.getvalue()


def pdf_filename(label, report_month):
    """Naziv fajla bez dijakritike i bez ijednog internog identifikatora."""
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", (label or "izvjestaj").lower())
    folded = folded.replace("đ", "dj")
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-") or "izvjestaj"
    return "izvjestaj-%s-%s.pdf" % (slug, report_month or "")
