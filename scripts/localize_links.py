#!/usr/bin/env python3
"""Rewrite href/src/action URLs to local files; fetch missing targets from Wayback."""

from __future__ import annotations

import hashlib
import re
import subprocess
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAYBACK_TS = "20071222020159"
PHONO_PAGES = frozenset(
    {"index.html", "program.html", "omphono.html", "frivillig.html", "bilder.html"}
)


def wayback_url(original: str, flavor: str = "") -> str:
    """Build a Wayback Machine URL for fetching *original*."""
    prefix = f"https://web.archive.org/web/{WAYBACK_TS}"
    if flavor:
        prefix += flavor
    return f"{prefix}/{original}"


def url_to_local_path(url: str) -> Path:
    if url.startswith("mailto:"):
        email = url[7:].strip()
        return ROOT / "contact" / f"{email}.html"

    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "unknown").lower()
    if host.startswith("www."):
        host = host[4:]

    # Phonofestivalen assets and pages
    if host in ("phonofestivalen.no",):
        path = parsed.path.lstrip("/")
        if path in ("", "index.html"):
            return ROOT / "index.html"
        if path == "2006":
            return ROOT / "links" / "phonofestivalen.no" / "2006.html"
        if path.startswith("img/"):
            return ROOT / path
        if path.startswith("html/"):
            name = Path(path).name
            mapping = {
                "program.html": "program.html",
                "omphono.html": "omphono.html",
                "frivillig.html": "frivillig.html",
                "bilder.html": "bilder.html",
            }
            return ROOT / mapping.get(name, name)
        return ROOT / "links" / "phonofestivalen.no" / path.replace("/", "__")

    # Other HTTP(S) targets live under links/<host>/...
    path = parsed.path or "/"
    if parsed.query:
        stem = (path.rstrip("/").split("/")[-1] or "index")
        safe_q = hashlib.sha1(parsed.query.encode()).hexdigest()[:10]
        leaf = f"{stem}__{safe_q}.html"
        sub = path.lstrip("/").rsplit("/", 1)[0]
        return ROOT / "links" / host / sub / leaf
    if path.endswith("/") or "." not in Path(path).name:
        sub = path.lstrip("/")
        return ROOT / "links" / host / sub / "index.html"
    return ROOT / "links" / host / path.lstrip("/")


def rel_href(from_file: Path, target: Path) -> str:
    rel = Path(urllib.parse.unquote(str(target.relative_to(ROOT))))
    return rel.as_posix()


def ensure_contact_page(path: Path, email: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    body = f"""<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="iso-8859-1">
<title>{email}</title>
</head>
<body>
<p>E-post: <strong>{email}</strong> (historisk kontaktadresse fra Phonofestivalen 2007)</p>
<p><a href="../index.html">Tilbake til Phonofestivalen</a></p>
</body>
</html>
"""
    path.write_bytes(body.encode("latin-1", errors="replace"))


def fetch_binary(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    flavor = "im_" if ext in {".gif", ".jpg", ".jpeg", ".png", ".pdf", ".m3u"} else ""
    wb = wayback_url(url, flavor)
    try:
        data = subprocess.check_output(
            ["curl", "-fsSL", "--max-time", "60", wb],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return False
    if not data or b"Wayback Machine" in data[:500] and ext in {".jpg", ".gif"}:
        return False
    dest.write_bytes(data)
    return True


def ensure_http_target(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if ext in {".gif", ".jpg", ".jpeg", ".png", ".pdf", ".m3u", ".css"}:
        if fetch_binary(url, dest):
            return
    wb = wayback_url(url)
    try:
        data = subprocess.check_output(
            ["curl", "-fsSL", "--max-time", "90", wb],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        data = b""
    if not data:
        stub = f"""<!DOCTYPE html>
<html lang="no">
<head><meta charset="utf-8"><title>{urllib.parse.urlparse(url).netloc}</title></head>
<body>
<p>Arkivert lenke (ikke tilgjengelig i Wayback):</p>
<p><code>{url}</code></p>
<p><a href="../../../index.html">Tilbake til Phonofestivalen</a></p>
</body>
</html>
"""
        dest.write_bytes(stub.encode("utf-8"))
        return
    if dest.suffix.lower() in {".html", ""} or "text/html" in str(dest):
        dest.write_bytes(data)
    else:
        dest.write_bytes(data)


def rewrite_phono_navigation(text: str) -> str:
    subs = [
        (r'href="Phonofestivalen%202007%20-%20program\.html"', 'href="program.html"'),
        (r'href="Phonofestivalen%202007%20-%20om\.html"', 'href="omphono.html"'),
        (r'href="Phonofestivalen%202007%20-%20frivillig\.html"', 'href="frivillig.html"'),
        (r'href="Phonofestivalen%202007%20-%20Bilder\.html"', 'href="bilder.html"'),
        (r'href="program\.html"(?!["\?])', 'href="program.html"'),  # bilder relative ok
    ]
    for pat, rep in subs:
        text = re.sub(pat, rep, text)
    text = re.sub(
        r'href="http://(?:www\.)?phonofestivalen\.no/index\.html"',
        'href="index.html"',
        text,
        flags=re.I,
    )
    return text


def localize_file(html_path: Path) -> None:
    text = html_path.read_bytes().decode("latin-1")
    text = rewrite_phono_navigation(text)

    def replace_attr(m: re.Match) -> str:
        attr, quote, url = m.group(1), m.group(2), m.group(3)
        if url.startswith("#") or url.startswith("?"):
            return m.group(0)
        if not url.startswith("http") and not url.startswith("mailto:"):
            return m.group(0)
        local = url_to_local_path(url)
        if url.startswith("mailto:"):
            ensure_contact_page(local, url[7:])
        else:
            ensure_http_target(url, local)
        href = rel_href(html_path, local)
        return f'{attr}={quote}{href}{quote}'

    text = re.sub(
        r'(href|src|action)=(["\'])(https?://[^"\']+|mailto:[^"\']+)\2',
        replace_attr,
        text,
        flags=re.I,
    )
    html_path.write_bytes(text.encode("latin-1"))


def localize_studentportal(html_path: Path) -> None:
    text = html_path.read_bytes().decode("latin-1")

    def replace_attr(m: re.Match) -> str:
        attr, quote, url = m.group(1), m.group(2), m.group(3)
        if url.startswith("#") or url.startswith("?"):
            return m.group(0)
        if url.startswith("mailto:"):
            local = url_to_local_path(url)
            ensure_contact_page(local, url[7:])
            return f'{attr}={quote}{rel_href(html_path, local)}{quote}'
        if not url.startswith("http"):
            return m.group(0)
        # phonofestivalen ? local site
        if "phonofestivalen.no" in url:
            local = ROOT / "index.html"
            return f'{attr}={quote}{rel_href(html_path, local)}{quote}'
        local = url_to_local_path(url)
        ensure_http_target(url, local)
        return f'{attr}={quote}{rel_href(html_path, local)}{quote}'

    text = re.sub(
        r'(href|src|action)=(["\'])(https?://[^"\']+|mailto:[^"\']+)\2',
        replace_attr,
        text,
        flags=re.I,
    )
    # CSS url(...) in inline styles
    def replace_css_url(m: re.Match) -> str:
        url = m.group(1)
        if not url.startswith("http"):
            return m.group(0)
        local = url_to_local_path(url)
        ensure_http_target(url, local)
        return f"url({rel_href(html_path, local)})"

    text = re.sub(r"url\((https?://[^)]+)\)", replace_css_url, text, flags=re.I)
    html_path.write_bytes(text.encode("latin-1"))


def main() -> None:
    for name in sorted(PHONO_PAGES):
        p = ROOT / name
        if p.exists():
            localize_file(p)
            print("localized", name)
    sp_matches = list(ROOT.glob("Studentportalen*.html"))
    if sp_matches:
        sp = sp_matches[0]
        localize_studentportal(sp)
        print("localized studentportal")


if __name__ == "__main__":
    main()
