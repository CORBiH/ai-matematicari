"""MAT-BOT — sitne čiste pomoćne funkcije (prikaz imena fajla + bajtovi slike).

Bez Flask/IO zavisnosti. Izdvojeno iz app.py (refactor) — ponašanje NEPROMIJENJENO.
"""
import os, base64, html
from urllib.parse import urlparse


def _short_name_for_display(name: str, maxlen: int = 60) -> str:
    n = os.path.basename(name or "").strip() or "nepoznato"
    if len(n) > maxlen:
        n = n[:maxlen-3] + "..."
    return html.escape(n)

def _name_from_url(u: str) -> str:
    try:
        p = urlparse(u)
        base = os.path.basename(p.path) or ""
        return _short_name_for_display(base if base else u.split("?")[0].split("/")[-1] or u)
    except Exception:
        return _short_name_for_display(u)


def _sniff_image_mime(raw: bytes) -> str:
    if len(raw) >= 12:
        if raw.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
        if raw[:3] == b"\xff\xd8\xff": return "image/jpeg"
        if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"): return "image/gif"
        if raw.startswith(b"BM"): return "image/bmp"
        if raw.startswith(b"II*\x00") or raw.startswith(b"MM\x00*"): return "image/tiff"
        if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP": return "image/webp"
    return "image/jpeg"

def _bytes_to_data_url(raw: bytes, mime_hint: str | None = None) -> str:
    mime = mime_hint if (mime_hint and mime_hint.startswith("image/")) else _sniff_image_mime(raw)
    b64 = base64.b64encode(raw).decode()
    return f"data:{mime};base64,{b64}"
