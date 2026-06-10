"""Manual version source — the catch-all for internal/homegrown tools.

Internal tools usually have no public catalog id (no winget/choco/Homebrew) and
often no release feed at all. For those, you simply type the latest version you
know about — from a build pipeline, a release email, whatever — and ReleaseRadar
compares it against the version you've packaged like any other source.

Identifier: the latest known version string, e.g. ``2.5.1``. No network call is
made. There are no release notes, so an item tracked this way alone classifies as
``unknown``; pair it with a Vendor RSS / release-notes URL if you want a
security-vs-feature verdict.
"""

from sources import SourceResult


def fetch(identifier):
    version = (identifier or "").strip()
    if not version:
        return SourceResult("manual", error="no version entered")
    return SourceResult("manual", version=version)
