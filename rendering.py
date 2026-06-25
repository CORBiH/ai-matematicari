"""MAT-BOT — pretvaranje izlaza modela u HTML + detekcija grafova.

Jedino mjesto gdje sirovi output modela postaje HTML: render_model_html()
(escape -> LaTeX razlomci -> <br>). Bez Flask/IO zavisnosti.
Izdvojeno iz app.py (refactor) — ponašanje NEPROMIJENJENO.
"""
import re, html


def latexify_fractions(text):
    def zamijeni(m):
        return f"\\(\\frac{{{m.group(1)}}}{{{m.group(2)}}}\\)"
    return re.sub(r'\b(\d{1,4})/(\d{1,4})\b', zamijeni, text)

def add_plot_div_once(odgovor_html: str, expression: str) -> str:
    marker = f'class="plot-request"'
    expr_attr = f'data-expression="{html.escape(expression)}"'
    if (marker in odgovor_html) and (expr_attr in odgovor_html): return odgovor_html
    return odgovor_html + f'<div class="plot-request" data-expression="{html.escape(expression)}"></div>'

TRIGGER_PHRASES = [r"\bnacrtaj\b", r"\bnacrtati\b", r"\bcrtaj\b", r"\biscrtaj\b", r"\bskiciraj\b", r"\bgraf\b", r"\bgrafik\b", r"\bprika[žz]i\s+graf\b", r"\bplot\b", r"\bvizualizuj\b", r"\bnasrtaj\b"]
NEGATION_PHRASES = [r"\bbez\s+grafa\b", r"\bne\s+crt(a|aj)\b", r"\bnemoj\s+crtati\b", r"\bne\s+treba\s+graf\b"]
_trigger_re  = re.compile("|".join(TRIGGER_PHRASES), flags=re.IGNORECASE)
_negation_re = re.compile("|".join(NEGATION_PHRASES), flags=re.IGNORECASE)

def should_plot(text: str) -> bool:
    if not text: return False
    if _negation_re.search(text): return False
    return _trigger_re.search(text) is not None

_FUNC_PAT = re.compile(r"(?:y\s*=\s*[^;,\n]+)|(?:[fFgG]\s*\(\s*x\s*\)\s*=\s*[^;,\n]+)", flags=re.IGNORECASE)
def extract_plot_expression(user_text: str, razred: str = "", history=None) -> str | None:
    if not user_text: return None
    m = _FUNC_PAT.search(user_text)
    if m:
        expr = re.sub(r"\s+", " ", m.group(0).strip())
        return expr
    return None


def strip_ascii_graph_blocks(text: str) -> str:
    """Ukloni samo fenced blokove koji liče na ASCII grafove; ostale blokove zadrži."""
    fence = re.compile(r"```([\s\S]*?)```", flags=re.MULTILINE)
    def looks_like_ascii_graph(block: str) -> bool:
        sample = block.strip()
        if len(sample) == 0: return False
        allowed = set(" \t\r\n-_|*^><().,/\\0123456789xyXY")
        ratio_allowed = sum(c in allowed for c in sample) / len(sample)
        lines = sample.splitlines()
        return (ratio_allowed > 0.9) and (3 <= len(lines) <= 40)
    def repl(m):
        block = m.group(1)
        return "" if looks_like_ascii_graph(block) else m.group(0)
    return fence.sub(repl, text)

def render_model_html(raw: str) -> str:
    """Jedino mjesto gdje odgovor modela postaje HTML: escape → LaTeX razlomci → <br>.
    Sve putanje (tekst, Mathpix, Vision) MORAJU ići kroz ovo radi XSS zaštite."""
    raw = strip_ascii_graph_blocks(raw or "")
    return "<p>" + latexify_fractions(html.escape(raw)).replace("\n", "<br>") + "</p>"
