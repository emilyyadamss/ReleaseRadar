"""Run version checks for watchlist items and assemble a result.

For one item we query every configured source, take the highest version found,
compare it to the version the enterprise currently packages, collect whatever
release notes a source returned, and run the offline classifier.
"""

import classifier
import versions
from sources import REGISTRY, rss


def check_item(item):
    """Query all configured sources for one watchlist row.

    Returns a result dict suitable for database.save_check_result, plus a
    per-source detail list for the UI.
    """
    source_results = {}      # source name -> version string (for storage)
    details = []             # full SourceResult-ish dicts (for the UI)
    best_version = None
    best_source = None
    notes_text = None
    notes_url = None
    any_ok = False
    errors = []
    security_hint = None     # True if any source flags security; False only if a
                             # source explicitly says non-security and none flag it.
    vendor_notes_url = None  # vendor release-notes page to deep-fetch if borderline

    for column, module in REGISTRY.items():
        identifier = (item.get(column) or "").strip()
        if not identifier:
            continue

        result = module.fetch(identifier)
        detail = {
            "source": result.source,
            "version": result.version,
            "error": result.error,
            "notes_url": result.notes_url,
        }
        details.append(detail)

        if result.ok:
            any_ok = True
            source_results[result.source] = result.version
            if best_version is None or versions.compare(result.version, best_version) > 0:
                best_version = result.version
                best_source = result.source
            # Keep the richest (longest) release notes for display/classification.
            if result.notes_text and len(result.notes_text) > len(notes_text or ""):
                notes_text = result.notes_text
            if result.notes_url and not notes_url:
                notes_url = result.notes_url
            # An authoritative security flag from any source wins; never let a
            # "not security" signal override one that says it is.
            if result.security_hint is True:
                security_hint = True
            elif result.security_hint is False and security_hint is None:
                security_hint = False
            # Remember a curated vendor release-notes link for borderline cases.
            if not vendor_notes_url:
                vendor_notes_url = result.extra.get("vendor_notes_url")
        elif result.error:
            errors.append(f"{result.source}: {result.error}")

    current = (item.get("current_version") or "").strip()

    # Determine status.
    if not any_ok:
        status = "error"
    elif versions.is_newer(best_version, current):
        status = "update_available"
    else:
        status = "up_to_date"

    # Classify only when there's actually a newer version worth triaging.
    if status == "update_available":
        heur = classifier.heuristic_classify(notes_text)
        if security_hint is True or heur["update_type"] == "security":
            # Either a source labelled it security, or the notes show security
            # signals — the safer call wins for patch triage.
            update_type = "security"
            if security_hint is True:
                classify_method = "catalog"
                classify_reason = "PatchMyPC catalog labels this a security update."
            else:
                classify_method = heur["method"]
                classify_reason = heur["reason"]
        elif heur["update_type"] == "ui":
            update_type = "ui"
            classify_method = heur["method"]
            classify_reason = heur["reason"]
        elif security_hint is False:
            # Catalog explicitly says non-security and notes show nothing scarier.
            update_type = "ui"
            classify_method = "catalog"
            classify_reason = "PatchMyPC catalog labels this a regular (non-security) update."
        else:
            update_type = "unknown"
            classify_method = heur["method"]
            classify_reason = heur["reason"]

        # Borderline cases: anything not already flagged security, when we have a
        # curated vendor release-notes link. Fetch the full page and double-check
        # — a "non-security" catalog label can hide a security fix, and an
        # "unknown" can often be resolved from the real notes. Safety-first: this
        # only ever *upgrades* toward security or resolves an unknown.
        if update_type != "security" and vendor_notes_url:
            deep = rss.fetch(vendor_notes_url)
            if deep.notes_text:
                notes_text = deep.notes_text  # richer notes for display too
                if classifier.security_signal(deep.notes_text):
                    update_type = "security"
                    classify_method = "auto-notes"
                    classify_reason = ("Vendor release notes contain security "
                                       "indicators (caught by deep-fetch).")
                elif update_type == "unknown":
                    deep_cls = classifier.heuristic_classify(deep.notes_text)
                    if deep_cls["update_type"] != "unknown":
                        update_type = deep_cls["update_type"]
                        classify_method = "auto-notes"
                        classify_reason = (
                            f"Fetched vendor release notes: {deep_cls['reason']}"
                        )
    else:
        update_type = "n/a" if status == "up_to_date" else "unknown"
        classify_method = "none"
        classify_reason = ""

    return {
        "latest_version": best_version,
        "latest_source": best_source,
        "status": status,
        "update_type": update_type,
        "classify_method": classify_method,
        "release_notes": notes_text,
        "notes_url": notes_url,
        "source_results": source_results,
        "details": details,
        "errors": errors,
        "classify_reason": classify_reason,
    }
