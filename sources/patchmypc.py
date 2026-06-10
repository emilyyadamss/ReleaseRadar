"""PatchMyPC source (catalog release history).

Reads PatchMyPC's public *catalog release history* page, which is far fresher
than the old free Home Updater definitions and — crucially — carries PatchMyPC's
own classification of each update plus a link to the vendor's real release notes.

The page renders only the latest day's updates as a visible HTML table, but it
embeds the *entire* year's catalog (≈12k rows) as a TablePress DataTables payload:

    window.DT_TP_data['NN'] = JSON.parse('[[...],[...], ...]');

Each row is a 14-element array; the columns we use:

    [1]  category   -> "Security Updates" | "Updates"   (PatchMyPC's own label)
    [2]  severity   -> "<span ...>Critical</span>"
    [4]  date       -> "YYYY-MM-DD"
    [5]  notes      -> "<a href=...>Release Notes</a>"   (vendor's real notes)
    [7]  product    -> "Google Chrome"                   (clean name)
    [10] version    -> "149.0.7827.103"                  (clean version)

A product appears in many rows (per installer/locale/version), so we group by
product name and keep the highest version. We parse the whole payload once and
cache it briefly.

Identifier: a substring of the PatchMyPC product name, e.g. ``Google Chrome`` or
``Chrome``.

The page is year-scoped. We default to the current year; override with
RELEASERADAR_PATCHMYPC_YEAR (e.g. to read a previous year's catalog).
"""

import json
import os
import re
import time
from datetime import datetime

import versions
from . import _http
from sources import SourceResult

_YEAR = os.environ.get("RELEASERADAR_PATCHMYPC_YEAR") or str(datetime.now().year)
_URL = f"https://patchmypc.com/catalog-release-history/{_YEAR}-releases/"
_CACHE_TTL = 600  # seconds

# Row column indices in the DataTables payload.
_C_CATEGORY, _C_SEVERITY, _C_DATE, _C_NOTES, _C_NAME, _C_VERSION = 1, 2, 4, 5, 7, 10

# Matches JSON.parse('<single-quoted JS string literal>').
_PARSE_RE = re.compile(r"JSON\.parse\('((?:[^'\\]|\\.)*)'\)", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"')
# Reverse JS single-quote string escaping (\\ -> \, \' -> ', \" -> ") so the
# remaining \uXXXX / \n etc. are valid JSON for json.loads.
_JS_REPL = {"\\": "\\", "'": "'", '"': '"'}

_cache = {"fetched_at": 0.0, "catalog": None, "error": None}


def _text(html):
    return _TAG_RE.sub("", html or "").strip()


def _href(html):
    m = _HREF_RE.search(html or "")
    return m.group(1) if m else None


def _js_unescape(s):
    return re.sub(r"\\(.)", lambda m: _JS_REPL.get(m.group(1), "\\" + m.group(1)), s)


def _extract_rows(page_text):
    """Decode the largest DataTables JSON payload into a list of row arrays."""
    best = []
    for body in _PARSE_RE.findall(page_text):
        try:
            arr = json.loads(_js_unescape(body))
        except ValueError:
            continue
        if isinstance(arr, list) and len(arr) > len(best):
            best = arr
    return best


def _load_catalog():
    """Return {product_name_lower: entry} with short-lived caching.

    Each entry: {name, version, category, severity, date, notes_url, security},
    holding the highest version seen for that product.
    """
    now = time.time()
    if _cache["catalog"] is not None and now - _cache["fetched_at"] < _CACHE_TTL:
        return _cache["catalog"], _cache["error"]

    try:
        resp = _http.get(_URL)
    except Exception as exc:
        _cache.update(fetched_at=now, catalog=None, error=f"request failed: {exc}")
        return None, _cache["error"]

    if resp.status_code != 200:
        _cache.update(fetched_at=now, catalog=None, error=f"HTTP {resp.status_code}")
        return None, _cache["error"]

    rows = _extract_rows(resp.text)
    if not rows:
        _cache.update(fetched_at=now, catalog=None, error="could not parse catalog data")
        return None, _cache["error"]

    catalog = {}
    for row in rows:
        if len(row) <= _C_VERSION:
            continue
        name = (row[_C_NAME] or "").strip()
        version = (row[_C_VERSION] or "").strip()
        if not name or not version:
            continue

        is_security = "security" in (row[_C_CATEGORY] or "").lower()
        key = name.lower()
        existing = catalog.get(key)

        if existing is None or versions.compare(version, existing["version"]) > 0:
            catalog[key] = {
                "name": name,
                "version": version,
                "category": (row[_C_CATEGORY] or "").strip(),
                "severity": _text(row[_C_SEVERITY]),
                "date": (row[_C_DATE] or "").strip(),
                "notes_url": _href(row[_C_NOTES]),
                "security": is_security,
            }
        elif versions.compare(version, existing["version"]) == 0:
            # Same version, different installer/locale row — merge useful bits.
            existing["security"] = existing["security"] or is_security
            if not existing["notes_url"]:
                existing["notes_url"] = _href(row[_C_NOTES])
            if not existing["severity"]:
                existing["severity"] = _text(row[_C_SEVERITY])

    _cache.update(fetched_at=now, catalog=catalog, error=None)
    return catalog, None


def fetch(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return SourceResult("patchmypc", error="no identifier")

    catalog, error = _load_catalog()
    if catalog is None:
        return SourceResult("patchmypc", error=error or "catalog unavailable")

    term = identifier.lower()
    exact = [e for e in catalog.values() if e["name"].lower() == term]
    partial = [e for e in catalog.values() if term in e["name"].lower()]
    matches = exact or partial
    if not matches:
        return SourceResult("patchmypc", error=f"not found in PatchMyPC {_YEAR} catalog")

    # If several products match the substring, prefer the closest name (shortest
    # — "Zoom" over "ZoomText 2026"). Enter the precise PatchMyPC title to be sure.
    entry = min(matches, key=lambda e: len(e["name"]))

    # PatchMyPC's own label is authoritative for security-vs-feature. Surface it
    # both as a structured hint and as readable notes text.
    severity = f" (severity: {entry['severity']})" if entry["severity"] else ""
    notes_text = (
        f"PatchMyPC catalog: {entry['category'] or 'Update'}{severity}"
        f" — {entry['name']} {entry['version']} on {entry['date']}."
    )

    return SourceResult(
        "patchmypc",
        version=entry["version"],
        notes_url=entry["notes_url"] or _URL,
        notes_text=notes_text,
        security_hint=entry["security"],
        extra={
            "matched_title": entry["name"],
            "category": entry["category"],
            "severity": entry["severity"],
            "date": entry["date"],
            # The vendor's real release-notes page (when PatchMyPC links one),
            # used to deep-fetch full notes for borderline classifications.
            "vendor_notes_url": entry["notes_url"],
        },
    )
