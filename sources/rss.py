"""Vendor RSS / release-notes source.

Points at a vendor's RSS/Atom feed or a release-notes page. Its main value is the
release-notes *text*, which feeds the security-vs-UI classifier — vendor wording
is far more reliable for that than a bare version number. When the latest entry's
title carries a version, we surface that too.

Identifier: a URL, e.g. https://www.mozilla.org/firefox/releases/ or a project's
GitHub releases Atom feed (https://github.com/owner/repo/releases.atom).
"""

import re
import xml.etree.ElementTree as ET

from . import _http
from sources import SourceResult

_VERSION_RE = re.compile(r"\b\d+(?:\.\d+){1,3}\b")
_TAG_RE = re.compile(r"<[^>]+>")
# Remove the *contents* of these blocks, not just their tags — otherwise CSS/JS
# text floods the extracted notes and buries the real release content.
_NOISE_RE = re.compile(r"<(script|style|head)\b[^>]*>.*?</\1>",
                       re.DOTALL | re.IGNORECASE)
MAX_NOTES = 6000


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _strip_html(text):
    if not text:
        return ""
    text = _NOISE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _first_entry(root):
    """Return the first <item> (RSS) or <entry> (Atom) element, or None."""
    for el in root.iter():
        if _local(el.tag) in ("item", "entry"):
            return el
    return None


def _child_text(entry, names):
    for el in entry:
        if _local(el.tag) in names:
            return (el.text or "").strip()
    return None


def fetch(identifier):
    url = (identifier or "").strip()
    if not url:
        return SourceResult("rss", error="no URL")
    # Guard the scheme here too: fetch() is also called on vendor-notes links
    # taken from external catalogs, not just on user-entered feed URLs.
    if not url.lower().startswith(("http://", "https://")):
        return SourceResult("rss", error="unsupported URL scheme")

    try:
        resp = _http.get(url)
    except Exception as exc:
        return SourceResult("rss", error=f"request failed: {exc}")

    if resp.status_code != 200:
        return SourceResult("rss", error=f"HTTP {resp.status_code}")

    body = resp.content

    # Try to parse as an RSS/Atom feed first.
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        root = None

    if root is not None:
        entry = _first_entry(root)
        if entry is not None:
            title = _child_text(entry, ("title",)) or ""
            summary = _child_text(entry, ("description", "summary", "content")) or ""
            notes = _strip_html(f"{title}. {summary}")[:MAX_NOTES]
            vmatch = _VERSION_RE.search(title) or _VERSION_RE.search(summary)
            return SourceResult(
                "rss",
                version=vmatch.group(0) if vmatch else None,
                notes_url=url,
                notes_text=notes or None,
            )

    # Fall back to treating the response as a release-notes web page.
    text = _strip_html(resp.text)[:MAX_NOTES]
    if not text:
        return SourceResult("rss", error="no readable content")
    vmatch = _VERSION_RE.search(text)
    return SourceResult(
        "rss",
        version=vmatch.group(0) if vmatch else None,
        notes_url=url,
        notes_text=text,
    )
