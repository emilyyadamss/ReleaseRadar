"""Version-source plugins for ReleaseRadar.

Each source exposes ``fetch(identifier) -> SourceResult`` where ``identifier`` is
whatever the watchlist stores for that source (a winget package id, a choco id,
etc.). Sources never raise to the caller — they return a SourceResult carrying
either a version, or an error string.
"""

from dataclasses import dataclass, field


@dataclass
class SourceResult:
    source: str
    version: str | None = None
    notes_url: str | None = None
    notes_text: str | None = None
    error: str | None = None
    # Authoritative security-vs-feature signal when a source provides one
    # (e.g. PatchMyPC labels each update). None = no opinion.
    security_hint: bool | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.version is not None and self.error is None


from . import winget, chocolatey, patchmypc, homebrew, rss, manual  # noqa: E402

# Maps the watchlist column name -> the source module that consumes it.
# `manual` is last so public-catalog versions win exact-version ties; it's the
# only source set for purely internal tools, so it stands alone there.
REGISTRY = {
    "winget_id": winget,
    "choco_id": chocolatey,
    "patchmypc_name": patchmypc,
    "homebrew_cask": homebrew,
    "rss_url": rss,
    "manual_version": manual,
}

__all__ = ["SourceResult", "REGISTRY", "winget", "chocolatey", "patchmypc", "homebrew", "rss", "manual"]
