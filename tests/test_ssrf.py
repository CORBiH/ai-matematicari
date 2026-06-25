"""SSRF zaštita za image_url."""
import json

import app as matbot


def test_rejects_non_http_schemes():
    assert not matbot.is_safe_external_url("file:///etc/passwd")
    assert not matbot.is_safe_external_url("ftp://example.com/a")
    assert not matbot.is_safe_external_url("gopher://example.com")
    assert not matbot.is_safe_external_url("")
    assert not matbot.is_safe_external_url(None)

def test_rejects_loopback_and_localhost():
    assert not matbot.is_safe_external_url("http://127.0.0.1/x")
    assert not matbot.is_safe_external_url("http://localhost/x")
    assert not matbot.is_safe_external_url("http://[::1]/x")

def test_rejects_private_and_linklocal_ranges():
    assert not matbot.is_safe_external_url("http://10.0.0.5/a")
    assert not matbot.is_safe_external_url("http://192.168.1.10/a")
    assert not matbot.is_safe_external_url("http://172.16.5.5/a")
    assert not matbot.is_safe_external_url("http://169.254.169.254/computeMetadata/v1/")

def test_rejects_metadata_hostname():
    assert not matbot.is_safe_external_url("http://metadata.google.internal/computeMetadata/v1/")
    assert not matbot.is_safe_external_url("http://metadata/computeMetadata/v1/")

def test_accepts_public_host(monkeypatch):
    def fake_getaddrinfo(host, port, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port))]
    monkeypatch.setattr(matbot.socket, "getaddrinfo", fake_getaddrinfo)
    assert matbot.is_safe_external_url("https://example.com/slika.png")

def test_rejects_public_hostname_resolving_to_private(monkeypatch):
    # DNS rebinding scenarij: javno ime → privatna adresa
    def fake_getaddrinfo(host, port, **kwargs):
        return [(2, 1, 6, "", ("10.1.2.3", port))]
    monkeypatch.setattr(matbot.socket, "getaddrinfo", fake_getaddrinfo)
    assert not matbot.is_safe_external_url("https://zlonamjerni.example.com/a")

def test_allow_private_escape_hatch(monkeypatch):
    monkeypatch.setattr(matbot, "ALLOW_PRIVATE_IMAGE_URLS", True)
    assert matbot.is_safe_external_url("http://127.0.0.1/x")


def test_submit_blocks_internal_image_url(client, fake_openai, sync_enqueue):
    """End-to-end: image_url ka internoj adresi mora biti blokiran PRIJE fetch-a.

    Autouse fixture blokira requests.get — da guard nije odradio svoje,
    job bi završio na vision_url fallbacku, a ne na blocked_url.
    """
    r = client.post(
        "/submit",
        data=json.dumps({"razred": "7", "mode": "async",
                         "image_url": "http://169.254.169.254/computeMetadata/v1/"}),
        content_type="application/json",
    )
    assert r.status_code == 202
    job_id = r.get_json()["job_id"]

    s = client.get(f"/status/{job_id}")
    data = s.get_json()
    assert data["status"] == "done"
    assert data["result"]["path"] == "blocked_url"
    assert "Link slike nije prihvaćen" in data["result"]["html"]
