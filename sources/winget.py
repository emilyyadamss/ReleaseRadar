"""Winget source.

Microsoft's winget package manifests live in the public GitHub repo
``microsoft/winget-pkgs`` under
``manifests/<first-letter>/<Publisher>/<Package>/<Version>/``. To find the
latest version of a package we list the version directories via the GitHub
contents API and pick the highest one.

Identifier: the PackageIdentifier, e.g. ``Google.Chrome`` or
``Microsoft.VisualStudioCode``.

Set GITHUB_TOKEN in the environment to raise the GitHub API rate limit from 60
to 5000 requests/hour — useful when checking a large watchlist.
"""

import os

import versions
from . import _http
from sources import SourceResult

_API = "https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests"


def _manifest_path(identifier):
    parts = identifier.split(".")
    first_letter = parts[0][0].lower()
    return "/".join([first_letter] + parts)


def _headers():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return SourceResult("winget", error="no identifier")

    url = f"{_API}/{_manifest_path(identifier)}"
    try:
        resp = _http.get(url, headers=_headers())
    except Exception as exc:  # network failures, etc.
        return SourceResult("winget", error=f"request failed: {exc}")

    if resp.status_code == 404:
        return SourceResult("winget", error="package not found in winget-pkgs")
    if resp.status_code == 403:
        return SourceResult("winget", error="GitHub rate limit hit (set GITHUB_TOKEN)")
    if resp.status_code != 200:
        return SourceResult("winget", error=f"GitHub HTTP {resp.status_code}")

    try:
        entries = resp.json()
    except ValueError:
        return SourceResult("winget", error="invalid GitHub response")

    if not isinstance(entries, list):
        return SourceResult("winget", error="unexpected GitHub response shape")

    version_dirs = [e["name"] for e in entries if e.get("type") == "dir"]
    if not version_dirs:
        return SourceResult("winget", error="no versions listed")

    best = versions.latest(version_dirs)
    notes = f"https://github.com/microsoft/winget-pkgs/tree/master/manifests/{_manifest_path(identifier)}/{best}"
    return SourceResult("winget", version=best, notes_url=notes)
