"""Chocolatey community feed source.

Queries the Chocolatey community OData feed for the latest published version of
a package, plus its release notes (handy for security-vs-UI classification).

Identifier: the Chocolatey package id, e.g. ``googlechrome`` or ``firefox``.
"""

import re
import xml.etree.ElementTree as ET

import classifier
import versions
from . import _http
from sources import SourceResult

# FindPackagesById returns every published version (newest first). We take the
# highest version ourselves — the feed's IsLatestVersion flag is unreliable
# (it's set true on many historical entries).
_FEED = "https://community.chocolatey.org/api/v2/FindPackagesById()"


def _local(tag):
    """Strip an XML namespace from a tag name."""
    return tag.rsplit("}", 1)[-1]


def _find_prop(entry, name):
    for el in entry.iter():
        if _local(el.tag) == name:
            return (el.text or "").strip()
    return None


def fetch(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return SourceResult("chocolatey", error="no identifier")

    params = {"id": f"'{identifier}'"}
    try:
        resp = _http.get(_FEED, params=params)
    except Exception as exc:
        return SourceResult("chocolatey", error=f"request failed: {exc}")

    if resp.status_code != 200:
        return SourceResult("chocolatey", error=f"HTTP {resp.status_code}")

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        return SourceResult("chocolatey", error=f"bad XML: {exc}")

    entries = [el for el in root.iter() if _local(el.tag) == "entry"]
    if not entries:
        return SourceResult("chocolatey", error="package not found")

    # Pick the entry with the highest Version (usually there's exactly one).
    best_version = None
    best_entry = None
    for entry in entries:
        v = _find_prop(entry, "Version")
        if v and (best_version is None or versions.compare(v, best_version) > 0):
            best_version = v
            best_entry = entry

    if not best_version:
        return SourceResult("chocolatey", error="no version in feed")

    notes_text = _find_prop(best_entry, "ReleaseNotes") if best_entry is not None else None

    # Chocolatey has no explicit security label, but its release notes often name
    # CVEs / security fixes. Derive a positive hint from them; never assert
    # "not security" — sparse notes don't prove the absence of a security fix.
    security_hint = True if classifier.security_signal(notes_text) else None

    # The ReleaseNotes field is frequently just a vendor URL. Expose it so the
    # checker can deep-fetch the real notes when classification is borderline.
    extra = {}
    if notes_text and re.fullmatch(r"https?://\S+", notes_text.strip()):
        extra["vendor_notes_url"] = notes_text.strip()

    return SourceResult(
        "chocolatey",
        version=best_version,
        notes_url=f"https://community.chocolatey.org/packages/{identifier}",
        notes_text=notes_text or None,
        security_hint=security_hint,
        extra=extra,
    )
