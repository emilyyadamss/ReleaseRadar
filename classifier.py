"""Classify an update as a security fix vs a regular UI/feature update.

Two methods:

  - heuristic_classify(): keyword scan over release notes. Fully offline, fast,
    no API cost. Used automatically on every check.

  - ai_classify(): sends the notes to Claude for a judgement call. Used on demand
    via the "Analyze with AI" button for ambiguous cases. Requires
    ANTHROPIC_API_KEY; otherwise it reports that it's unavailable.

Both return a dict: {"update_type": "security"|"ui"|"unknown",
                     "confidence": float, "reason": str, "method": str}
"""

import re

from config import ANTHROPIC_API_KEY, AI_MODEL, HTTPS_PROXY, tls_verify

# Strong signals that an update addresses a vulnerability.
SECURITY_KEYWORDS = [
    r"\bCVE-\d{4}-\d+\b",
    r"\bsecurity (?:fix|update|patch|advisory|release|improvement)\b",
    r"\bvulnerabilit(?:y|ies)\b",
    r"\bexploit(?:ed|able)?\b",
    r"\bzero[- ]day\b",
    r"\bremote code execution\b", r"\bRCE\b",
    r"\bprivilege escalation\b",
    r"\bbuffer overflow\b",
    r"\buse[- ]after[- ]free\b",
    r"\bcross[- ]site scripting\b", r"\bXSS\b",
    r"\bSQL injection\b",
    r"\bCVSS\b",
    r"\bpatched a (?:flaw|hole|bug) that\b",
    r"\bsecurity[- ]critical\b",
    r"\bhardening\b",
]

# Signals that point at routine UI/feature work (used to break ties only).
UI_KEYWORDS = [
    r"\bnew (?:feature|design|UI|interface|theme|icon)s?\b",
    r"\bredesign(?:ed)?\b",
    r"\bperformance improvement\b",
    r"\bbug ?fix(?:es)?\b",
    r"\bUI (?:update|change|refresh|tweak)\b",
    r"\busability\b",
    r"\bquality[- ]of[- ]life\b",
    r"\baccessibility\b",
]

_SEC_RES = [re.compile(p, re.IGNORECASE) for p in SECURITY_KEYWORDS]
_UI_RES = [re.compile(p, re.IGNORECASE) for p in UI_KEYWORDS]


def security_signal(text):
    """True if the text contains any credible security indicator.

    Shared so a source (e.g. Chocolatey) can derive a security hint from its own
    release notes without duplicating the keyword list.
    """
    text = text or ""
    return any(r.search(text) for r in _SEC_RES)


def heuristic_classify(notes):
    notes = notes or ""
    # Capture the actual matched text (e.g. "CVE-2024-1234"), not the regex.
    sec_hits = [m.group(0) for r in _SEC_RES if (m := r.search(notes))]
    ui_hits = [m.group(0) for r in _UI_RES if (m := r.search(notes))]

    if not notes.strip():
        return {
            "update_type": "unknown",
            "confidence": 0.0,
            "reason": "No release notes available to classify.",
            "method": "heuristic",
        }

    if sec_hits:
        # Any credible security signal wins — that's the safer default for triage.
        conf = min(0.95, 0.6 + 0.1 * len(sec_hits))
        sample = ", ".join(dict.fromkeys(sec_hits))  # unique, preserve order
        return {
            "update_type": "security",
            "confidence": round(conf, 2),
            "reason": f"Security indicators in notes: {sample[:160]}.",
            "method": "heuristic",
        }

    if ui_hits:
        conf = min(0.8, 0.5 + 0.1 * len(ui_hits))
        return {
            "update_type": "ui",
            "confidence": round(conf, 2),
            "reason": "Notes describe UI/feature/bug-fix work with no security indicators.",
            "method": "heuristic",
        }

    return {
        "update_type": "unknown",
        "confidence": 0.2,
        "reason": "No clear security or UI indicators in the notes.",
        "method": "heuristic",
    }


def ai_available():
    return bool(ANTHROPIC_API_KEY)


def ai_classify(name, version, notes):
    """Classify with Claude. Returns the same dict shape as heuristic_classify."""
    if not ai_available():
        return {
            "update_type": "unknown",
            "confidence": 0.0,
            "reason": "AI classification unavailable (set ANTHROPIC_API_KEY).",
            "method": "ai",
        }

    if not (notes or "").strip():
        return {
            "update_type": "unknown",
            "confidence": 0.0,
            "reason": "No release notes available to send to the model.",
            "method": "ai",
        }

    # Imported lazily so the app runs fine without the SDK installed when AI is off.
    import anthropic
    from pydantic import BaseModel  # bundled with the anthropic SDK

    class Classification(BaseModel):
        update_type: str          # "security" | "ui" | "unknown"
        confidence: float         # 0.0 - 1.0
        reason: str

    # Route the Claude call through the same corporate proxy / CA bundle as the
    # source lookups. The Anthropic SDK uses httpx, so we hand it a configured
    # httpx client when a proxy or custom CA is in play; otherwise we let the SDK
    # build its own default client untouched.
    client_kwargs = {"api_key": ANTHROPIC_API_KEY}
    verify = tls_verify()
    if HTTPS_PROXY or verify is not True:
        import inspect

        import httpx

        # httpx renamed the proxy kwarg from `proxies` (<=0.27) to `proxy`
        # (>=0.26, sole option in >=0.28); pick whichever this version accepts.
        httpx_kwargs = {"verify": verify, "trust_env": True}
        if HTTPS_PROXY:
            param = "proxy" if "proxy" in inspect.signature(httpx.Client).parameters else "proxies"
            httpx_kwargs[param] = HTTPS_PROXY
        client_kwargs["http_client"] = httpx.Client(**httpx_kwargs)

    client = anthropic.Anthropic(**client_kwargs)

    system = (
        "You classify software release notes for an enterprise patch-management "
        "team. Decide whether an update is primarily a SECURITY update (fixes a "
        "vulnerability/CVE or otherwise security-motivated) or a regular UI/feature "
        "update (features, redesigns, non-security bug fixes, performance). If a "
        "release contains any security fixes, classify it as 'security' — that drives "
        "patch urgency. Use 'unknown' only when the notes truly don't say. Respond "
        "via the structured schema only."
    )
    user = (
        f"Software: {name}\nVersion: {version}\n\nRelease notes:\n{notes[:6000]}"
    )

    try:
        resp = client.messages.parse(
            model=AI_MODEL,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=Classification,
        )
        result = resp.parsed_output
        update_type = result.update_type if result.update_type in ("security", "ui", "unknown") else "unknown"
        return {
            "update_type": update_type,
            "confidence": round(float(result.confidence), 2),
            "reason": result.reason,
            "method": "ai",
        }
    except Exception as exc:
        return {
            "update_type": "unknown",
            "confidence": 0.0,
            "reason": f"AI classification failed: {exc}",
            "method": "ai",
        }
