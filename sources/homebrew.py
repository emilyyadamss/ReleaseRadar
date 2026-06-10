"""Homebrew Cask source (macOS).

Homebrew's public formulae API serves a JSON document per cask containing the
current version. This is the most reliable single source for macOS app versions,
which is why it's wired in alongside the Windows-focused sources — your Mac
packaging needs the same "is there a newer version?" signal.

Identifier: the cask token, e.g. ``google-chrome``, ``firefox``, ``zoom``.
"""

from . import _http
from sources import SourceResult

_API = "https://formulae.brew.sh/api/cask"


def fetch(identifier):
    identifier = (identifier or "").strip()
    if not identifier:
        return SourceResult("homebrew", error="no identifier")

    url = f"{_API}/{identifier}.json"
    try:
        resp = _http.get(url)
    except Exception as exc:
        return SourceResult("homebrew", error=f"request failed: {exc}")

    if resp.status_code == 404:
        return SourceResult("homebrew", error="cask not found")
    if resp.status_code != 200:
        return SourceResult("homebrew", error=f"HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        return SourceResult("homebrew", error="invalid JSON")

    version = data.get("version")
    if not version or version == "latest":
        return SourceResult("homebrew", error="no pinned version (rolling release)")

    homepage = data.get("homepage")
    return SourceResult(
        "homebrew",
        version=version,
        notes_url=homepage,
        extra={"name": (data.get("name") or [None])[0]},
    )
