"""Validacija i normalizacija JEDNE slike zadatka za mod „Samo rezultat“.

OBIM: samo Rezultat/Quick. Practice i Objasni mi ovaj modul ne koriste.

GARANCIJE (svaka je pokrivena testom u tests/test_result_image.py):
  • Odluka se donosi nad STVARNO DEKODIRANIM bajtima — ime fajla, ekstenzija i
    browser Content-Type se NIKAD ne koriste kao dokaz formata.
  • Ništa se ne piše na disk (upload stream je BytesIO — vidi
    matbot/request_limits.py; ovdje se radi isključivo u memoriji).
  • Nijedan bajt slike, base64, data URL ni EXIF ne ulazi u log, u poruku
    izuzetka niti u odgovor učeniku. Logujemo samo ograničene metapodatke.
  • Neispravan upload podiže ImageRejected PRIJE ijednog poziva modela.

STRATEGIJA NORMALIZACIJE (svjesno različita po vrsti sadržaja, jer je cilj
čitljivost sitnog matematičkog teksta, ne najmanji fajl):
  • PNG/WebP ulaz (screenshot, oštre ivice, linijski tekst) → PNG izlaz.
    Bez gubitaka, jer JPEG artefakti oko tankih crnih linija na bijeloj
    podlozi najviše štete upravo indeksima, eksponentima i razlomačkim crtama.
  • JPEG ulaz (fotografija) → JPEG izlaz, quality=90, subsampling isključen
    (4:4:4). Fotografija je već lossy, pa ponovno enkodiranje bez agresivne
    kompresije zadržava čitljivost rukopisa/štampe.
  • Transparentnost: RGBA/LA/P se čuva kada izlaz ide u PNG. Kada zbog
    veličine moramo preći na JPEG, alfa se prvo kompozituje na BIJELU podlogu
    (tamni tekst na prozirnoj podlozi inače postane crno-na-crno).
  • Resize: samo SMANJIVANJE (nikad uvećavanje) na najviše
    MAX_IMAGE_DIMENSION po stranici, LANCZOS filterom.
  • Ako normalizovani izlaz pređe MAX_NORMALIZED_IMAGE_BYTES, ide ograničen
    niz sigurnih koraka smanjivanja (PNG→JPEG q=85, pa q=75, pa dimenzije
    /1.5, /2). Ako ni tada ne stane — odbijamo, ne šaljemo modelu ništa.
"""
import base64
import io
import logging
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from matbot import config

logger = logging.getLogger("matbot.imageinput")

# Pillow-ova vlastita zaštita od decompression bombe. Postavljamo je EKSPLICITNO
# na našu granicu: iznad nje Pillow baca DecompressionBombError još tokom
# dekodiranja, dakle prije nego što alocira pun buffer piksela.
Image.MAX_IMAGE_PIXELS = config.MAX_IMAGE_PIXELS

IMAGE_FIELD = "image"

# Prijateljske bosanske poruke. Namjerno ne sadrže ime fajla, dimenzije ni
# ijedan detalj sadržaja — samo šta učenik treba uraditi.
MESSAGES = {
    "unsupported_format": (
        "Ovaj format slike nije podržan. Pošalji JPG, PNG ili WebP sliku zadatka."
    ),
    "too_large": (
        "Slika je prevelika. Pošalji manju sliku (do 8 MB) ili je slikaj u nižoj rezoluciji."
    ),
    "corrupt": (
        "Sliku nije moguće otvoriti — izgleda da je oštećena ili nepotpuna. "
        "Slikaj zadatak ponovo pa pošalji."
    ),
    "unreadable": (
        "Sliku nije moguće obraditi. Pokušaj ponovo s drugom slikom zadatka."
    ),
    "too_many": (
        "Pošalji samo jednu sliku po poruci."
    ),
    "empty": (
        "Slika je prazna. Izaberi sliku zadatka pa pošalji ponovo."
    ),
}

# HTTP status po kategoriji odbijanja (Phase 4: 400 ili 413).
_STATUS = {
    "too_large": 413,
}


class ImageRejected(Exception):
    """Upload odbijen PRIJE modela. `category` ide u log, `message` učeniku.

    Poruka izuzetka je namjerno samo kategorija — nikad bajtovi, base64, EXIF
    ni tekst originalne Pillow greške (koja može sadržavati dijelove sadržaja).
    """

    def __init__(self, category, detail=""):
        super().__init__(category)
        self.category = category
        self.detail = detail  # kratak, bezbjedan tehnički trag za log
        self.message = MESSAGES.get(category, MESSAGES["unreadable"])
        self.http_status = _STATUS.get(category, 400)


class ValidatedImage:
    """Rezultat validacije. `data_url` NIKAD ne ide u log ni u __repr__."""

    __slots__ = ("data_url", "image_format", "width", "height",
                 "original_bytes", "normalized_bytes")

    def __init__(self, data_url, image_format, width, height,
                 original_bytes, normalized_bytes):
        self.data_url = data_url
        self.image_format = image_format
        self.width = width
        self.height = height
        self.original_bytes = original_bytes
        self.normalized_bytes = normalized_bytes

    def log_metadata(self):
        """Ograničeni metapodaci za log — bez ijednog bajta sadržaja."""
        return (
            f"format={self.image_format} width={self.width} height={self.height} "
            f"original_bytes={self.original_bytes} normalized_bytes={self.normalized_bytes}"
        )

    def __repr__(self):  # zaštita od slučajnog logovanja cijelog objekta
        return f"<ValidatedImage {self.log_metadata()}>"


def count_uploaded_files(files):
    """Broj SVIH file polja u zahtjevu (ne samo 'image').

    Napadač smije poslati bilo koje ime polja; zato brojimo sve i tražimo da
    postoji tačno jedno, i to baš `image`."""
    return sum(len(files.getlist(key)) for key in files.keys())


def extract_single_image(files):
    """Vrati FileStorage iz polja `image` ili podigni ImageRejected.

    Pravilo: tačno jedan fajl u cijelom zahtjevu i to u polju `image`. Svako
    dodatno file polje (bilo kojeg imena) je odbijeno prije modela."""
    total = count_uploaded_files(files)
    if total == 0:
        return None
    if total > 1:
        raise ImageRejected("too_many", "multiple_file_fields")
    storage = files.get(IMAGE_FIELD)
    if storage is None:
        # Jedan fajl, ali u neočekivanom polju — ne prihvatamo ga.
        raise ImageRejected("unsupported_format", "unexpected_file_field")
    return storage


def _read_bounded(storage):
    """Pročitaj najviše MAX_IMAGE_BYTES+1 bajt: jedan bajt viška je dovoljan
    dokaz da je upload prevelik, a više od toga ne alociramo."""
    limit = config.MAX_IMAGE_BYTES
    try:
        data = storage.read(limit + 1)
    except Exception as e:
        raise ImageRejected("unreadable", f"read_failed:{type(e).__name__}") from None
    if not data:
        raise ImageRejected("empty", "zero_bytes")
    if len(data) > limit:
        raise ImageRejected("too_large", "over_byte_limit")
    return data


def _open_verify_and_load(data):
    """verify() → ponovo otvori → load() pod pixel limitom.

    verify() troši objekat (Pillow zahtijeva ponovno otvaranje), ali hvata
    krnje/oštećene fajlove prije punog dekodiranja. Tek load() stvarno
    dekodira piksele i tada Pillow može podići DecompressionBombError."""
    # DecompressionBombWarning se u OVOM toku tretira kao greška.
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            probe = Image.open(io.BytesIO(data))
            declared_format = (probe.format or "").upper()
            probe.verify()
        except Image.DecompressionBombWarning:
            raise ImageRejected("too_large", "decompression_bomb_warning") from None
        except Image.DecompressionBombError:
            raise ImageRejected("too_large", "decompression_bomb") from None
        except UnidentifiedImageError:
            raise ImageRejected("unsupported_format", "unidentified") from None
        except (OSError, ValueError, SyntaxError) as e:
            raise ImageRejected("corrupt", f"verify_failed:{type(e).__name__}") from None

        if declared_format not in config.ALLOWED_IMAGE_FORMATS:
            # Format je pročitan iz DEKODIRANIH bajta (Pillow sniffing),
            # nikad iz imena fajla ili Content-Type headera. Ovdje padaju
            # SVG, GIF, HEIC, BMP, TIFF, PDF i spoofovani ne-slika sadržaj.
            raise ImageRejected("unsupported_format", f"format:{declared_format or 'unknown'}")

        try:
            image = Image.open(io.BytesIO(data))
            width, height = image.size
            if width * height > config.MAX_IMAGE_PIXELS:
                raise ImageRejected("too_large", "pixel_limit")
            if width < config.MIN_IMAGE_DIMENSION or height < config.MIN_IMAGE_DIMENSION:
                raise ImageRejected("unreadable", "too_small")
            image.load()  # potpuno dekodiranje pod limitom
        except ImageRejected:
            raise
        except Image.DecompressionBombWarning:
            raise ImageRejected("too_large", "decompression_bomb_warning") from None
        except Image.DecompressionBombError:
            raise ImageRejected("too_large", "decompression_bomb") from None
        except UnidentifiedImageError:
            raise ImageRejected("unsupported_format", "unidentified") from None
        except (OSError, ValueError, SyntaxError) as e:
            raise ImageRejected("corrupt", f"load_failed:{type(e).__name__}") from None

    return image, declared_format


def _apply_orientation(image):
    """EXIF orijentacija se PRIMJENJUJE na piksele; sam EXIF blok se time i
    odbacuje (exif_transpose vraća sliku bez orientation taga)."""
    try:
        fixed = ImageOps.exif_transpose(image)
    except Exception as e:
        raise ImageRejected("unreadable", f"exif_failed:{type(e).__name__}") from None
    return fixed or image


def _downscale(image, max_side):
    """Samo smanjivanje (nikad uvećavanje), LANCZOS."""
    width, height = image.size
    longest = max(width, height)
    if longest <= max_side:
        return image
    scale = max_side / float(longest)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.LANCZOS)


def _flatten_on_white(image):
    """Alfa/paleta → RGB na bijeloj podlozi (za JPEG izlaz)."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def _encode_png(image):
    """PNG bez gubitaka; transparentnost se čuva. Sav metadata (EXIF, ICC,
    tekstualni tagovi) otpada jer enkodiramo iz čistog piksel buffera."""
    if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _encode_jpeg(image, quality):
    """JPEG bez chroma subsamplinga (4:4:4) — tanke linije i sitni indeksi
    ostaju čitljivi. Bez exif= parametra → nijedan metapodatak ne izlazi."""
    buffer = io.BytesIO()
    _flatten_on_white(image).save(
        buffer, format="JPEG", quality=quality, subsampling=0, optimize=True
    )
    return buffer.getvalue()


def _normalize(image, source_format):
    """Vrati (bytes, format_str). Vidi STRATEGIJU u docstringu modula."""
    image = _downscale(image, config.MAX_IMAGE_DIMENSION)

    if source_format == "JPEG":
        payload, out_format = _encode_jpeg(image, 90), "JPEG"
    else:
        payload, out_format = _encode_png(image), "PNG"

    if len(payload) <= config.MAX_NORMALIZED_IMAGE_BYTES:
        return payload, out_format

    # Ograničen, deterministički niz koraka smanjivanja — bez petlje „dok ne
    # stane“ i bez neograničenog PNG izlaza.
    for quality in (85, 75):
        payload = _encode_jpeg(image, quality)
        if len(payload) <= config.MAX_NORMALIZED_IMAGE_BYTES:
            return payload, "JPEG"
    for divisor in (1.5, 2.0):
        smaller = _downscale(image, int(config.MAX_IMAGE_DIMENSION / divisor))
        payload = _encode_jpeg(smaller, 75)
        if len(payload) <= config.MAX_NORMALIZED_IMAGE_BYTES:
            return payload, "JPEG"

    raise ImageRejected("too_large", "normalized_over_limit")


def validate_image_upload(storage):
    """Jedini ulaz. Vraća ValidatedImage ili podiže ImageRejected.

    Poziva se TEK nakon auth-a, rate limita i concurrency locka (vidi
    matbot/api.py) — neautorizovan ili prigušen zahtjev nikad ne troši CPU na
    dekodiranje slike."""
    if storage is None:
        raise ImageRejected("empty", "no_file")

    data = _read_bounded(storage)
    original_bytes = len(data)

    image, source_format = _open_verify_and_load(data)
    image = _apply_orientation(image)

    try:
        payload, out_format = _normalize(image, source_format)
    except ImageRejected:
        raise
    except Exception as e:
        raise ImageRejected("unreadable", f"encode_failed:{type(e).__name__}") from None
    finally:
        try:
            image.close()
        except Exception:
            pass

    encoded = base64.b64encode(payload).decode("ascii")
    mime = "image/jpeg" if out_format == "JPEG" else "image/png"
    data_url = f"data:{mime};base64,{encoded}"
    if len(data_url) > config.MAX_IMAGE_DATA_URL_CHARS:
        raise ImageRejected("too_large", "data_url_over_limit")

    width, height = _decoded_size(payload)
    return ValidatedImage(
        data_url=data_url,
        image_format=out_format,
        width=width,
        height=height,
        original_bytes=original_bytes,
        normalized_bytes=len(payload),
    )


def _decoded_size(payload):
    """Dimenzije STVARNO enkodiranog izlaza (a ne pretpostavljene)."""
    with Image.open(io.BytesIO(payload)) as out:
        return out.size
