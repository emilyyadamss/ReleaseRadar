"""Version parsing and comparison.

Vendor version strings are messy (1.2.3, 1.2.3.4567, 120.0.6099.110, v2.1,
2024.1, 3.0-rc1). We parse the leading dotted-numeric portion into a tuple of
ints for ordering, and fall back to a string compare when that fails. Good
enough to answer "is B newer than A?" for packaging triage.
"""

import re

_NUM_RE = re.compile(r"\d+")


def parse(version):
    """Return a comparable tuple of ints from a version string."""
    if not version:
        return ()
    # Strip a leading v/V and anything after the first space.
    v = str(version).strip().lstrip("vV").split()[0]
    parts = _NUM_RE.findall(v)
    return tuple(int(p) for p in parts) if parts else ()


def compare(a, b):
    """Return -1 if a < b, 0 if equal, 1 if a > b."""
    pa, pb = parse(a), parse(b)
    # Pad shorter tuple with zeros first so 1.2 == 1.2.0.
    length = max(len(pa), len(pb))
    pa += (0,) * (length - len(pa))
    pb += (0,) * (length - len(pb))
    if pa == pb:
        return 0
    return -1 if pa < pb else 1


def is_newer(candidate, baseline):
    """True if ``candidate`` is a strictly newer version than ``baseline``."""
    if not candidate:
        return False
    if not baseline:
        return True
    return compare(candidate, baseline) > 0


def latest(versions):
    """Return the highest version from an iterable of strings."""
    best = None
    for v in versions:
        if best is None or compare(v, best) > 0:
            best = v
    return best
