# -*- coding: utf-8 -*-
"""utils.py — bajtovi slike → data URL.

Jedini preostali helper poslije brisanja legacy /submit stacka (2026-07-14);
koristi ga tutor ruta kad učenik pošalje sliku zadatka.
"""
import base64

from utils import _bytes_to_data_url, _sniff_image_mime

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
GIF = b"GIF89a" + b"\x00" * 8
BMP = b"BM" + b"\x00" * 10
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 4


def test_sniff_png():
    assert _sniff_image_mime(PNG) == "image/png"


def test_sniff_jpeg():
    assert _sniff_image_mime(JPEG) == "image/jpeg"


def test_sniff_gif():
    assert _sniff_image_mime(GIF) == "image/gif"


def test_sniff_bmp():
    assert _sniff_image_mime(BMP) == "image/bmp"


def test_sniff_webp():
    assert _sniff_image_mime(WEBP) == "image/webp"


def test_sniff_fallback_is_jpeg():
    """Nepoznat/prekratak sadržaj → JPEG (Vision ga prihvata najšire)."""
    assert _sniff_image_mime(b"nonsense") == "image/jpeg"
    assert _sniff_image_mime(b"") == "image/jpeg"


def test_data_url_uses_sniffed_mime():
    url = _bytes_to_data_url(PNG)
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG


def test_data_url_honors_valid_mime_hint():
    assert _bytes_to_data_url(JPEG, mime_hint="image/webp").startswith("data:image/webp;base64,")


def test_data_url_ignores_bogus_mime_hint():
    """Hint koji nije image/* se ignoriše — inače bi se u data URL uvukao npr.
    'text/html' pa bi Vision dobio neispravan sadržaj."""
    assert _bytes_to_data_url(PNG, mime_hint="text/html").startswith("data:image/png;base64,")
    assert _bytes_to_data_url(PNG, mime_hint="").startswith("data:image/png;base64,")
