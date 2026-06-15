"""ReleaseRadar — a single-user dashboard for tracking new software versions.

Run:  python app.py
Then open http://127.0.0.1:5000 in your browser.

The app only listens on localhost by default and is gated behind one password,
so it's safe to run on your work machine.
"""

import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import (
    Flask, Response, abort, flash, redirect, render_template, request,
    session, url_for
)

import auth
import classifier
import database
from checker import check_item
from config import HOST, PORT, SECRET_KEY
from sources import REGISTRY


def create_app():
    database.init_db()
    auth.bootstrap()

    app = Flask(__name__)

    # Stable session-signing key: env var, else a persisted random one so logins
    # survive restarts without hard-coding a secret.
    key = SECRET_KEY or database.get_setting("secret_key")
    if not key:
        key = secrets.token_hex(32)
        database.set_setting("secret_key", key)
    app.secret_key = key

    # Harden the session cookie.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,  # bounds the JSON-import upload
    )

    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            token = session.get("csrf_token", "")
            sent = request.form.get("csrf_token", "")
            if not token or not secrets.compare_digest(token, sent):
                abort(400, description="CSRF token missing or invalid. "
                                       "Reload the page and try again.")

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": _csrf_token}

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        return resp

    @app.errorhandler(413)
    def payload_too_large(_exc):
        flash("Upload too large (max 2 MB).", "error")
        return redirect(url_for("import_json"))

    @app.template_filter("timeago")
    def timeago(value):
        """Render an SQLite UTC timestamp as a friendly relative time."""
        if not value:
            return "never"
        try:
            dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return value
        seconds = int((datetime.now(timezone.utc) - dt).total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60} min ago"
        if seconds < 86400:
            return f"{seconds // 3600} h ago"
        return f"{seconds // 86400} d ago"

    register_routes(app)
    return app


def _csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


def _safe_referrer():
    """The referrer's path when it points at this app, else the dashboard.

    Redirecting to raw request.referrer would let a crafted Referer header
    bounce the browser to an arbitrary external site.
    """
    ref = urlparse(request.referrer or "")
    if (not ref.netloc or ref.netloc == request.host) and ref.path:
        return ref.path
    return url_for("dashboard")


ITEM_FIELDS = (
    "name", "platform", "current_version",
    "winget_id", "choco_id", "patchmypc_name", "homebrew_cask", "rss_url",
    "manual_version", "notes",
)


def _form_to_item(form):
    item = {f: form.get(f, "").strip() for f in ITEM_FIELDS}
    item["platform"] = item["platform"] or "windows"
    return item


def _item_error(data):
    """Validation error for a normalised item dict, or None if it's fine."""
    if not data["name"]:
        return 'missing "name"'
    if not any(data[c] for c in REGISTRY):
        return "no source identifier (winget_id, choco_id, …)"
    if data["rss_url"] and not data["rss_url"].lower().startswith(("http://", "https://")):
        return "the vendor RSS URL must start with http:// or https://"
    return None


def _json_to_item(obj):
    """Normalise one JSON object into a watchlist item.

    Accepts the same keys as the add form. Missing keys default to blank and
    every value is coerced to a trimmed string, so a numeric version in the
    JSON works as well as a quoted one.
    """
    def value(key):
        raw = obj.get(key)
        return "" if raw is None else str(raw).strip()

    item = {f: value(f) for f in ITEM_FIELDS}
    item["platform"] = item["platform"] or "windows"
    return item


def register_routes(app):

    # --- First-run setup --------------------------------------------------
    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if auth.password_is_set():
            return redirect(url_for("login"))
        if request.method == "POST":
            pw = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            if len(pw) < 8:
                flash("Password must be at least 8 characters.", "error")
            elif pw != confirm:
                flash("Passwords do not match.", "error")
            else:
                auth.set_password(pw)
                auth.login_session()
                flash("Password set. You're logged in.", "success")
                return redirect(url_for("dashboard"))
        return render_template("setup.html")

    # --- Auth -------------------------------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if not auth.password_is_set():
            return redirect(url_for("setup"))
        if auth.is_authenticated():
            return redirect(url_for("dashboard"))

        locked, remaining = auth.is_locked()
        if request.method == "POST":
            locked, remaining = auth.is_locked()
            if locked:
                flash(f"Account locked. Try again in {remaining} seconds.", "error")
            elif auth.verify(request.form.get("password", "")):
                auth.login_session()
                return redirect(url_for("dashboard"))
            else:
                locked, remaining = auth.is_locked()
                if locked:
                    flash(f"Too many attempts. Locked for {remaining} seconds.", "error")
                else:
                    flash("Incorrect password.", "error")
        return render_template("login.html", locked=locked, remaining=remaining)

    @app.route("/logout", methods=["POST"])
    def logout():
        auth.logout_session()
        flash("Logged out.", "success")
        return redirect(url_for("login"))

    # --- Dashboard --------------------------------------------------------
    @app.route("/")
    @auth.login_required
    def dashboard():
        items = database.list_items()
        for it in items:
            it["source_results"] = _parse_json(it.get("source_results"))
        summary = {
            "total": len(items),
            "update_available": sum(1 for i in items if i["status"] == "update_available"),
            "security": sum(1 for i in items if i["update_type"] == "security"),
            "errors": sum(1 for i in items if i["status"] == "error"),
        }
        return render_template(
            "dashboard.html",
            items=items,
            summary=summary,
            ai_available=classifier.ai_available(),
        )

    # --- Item detail ------------------------------------------------------
    @app.route("/item/<int:item_id>")
    @auth.login_required
    def item_detail(item_id):
        item = database.get_item(item_id)
        if not item:
            flash("Item not found.", "error")
            return redirect(url_for("dashboard"))
        item["source_results"] = _parse_json(item.get("source_results"))
        return render_template(
            "detail.html",
            item=item,
            ai_available=classifier.ai_available(),
        )

    # --- Add / edit / delete ---------------------------------------------
    @app.route("/add", methods=["GET", "POST"])
    @auth.login_required
    def add():
        if request.method == "POST":
            data = _form_to_item(request.form)
            error = _item_error(data)
            if error:
                flash(f"Cannot add: {error}.", "error")
            else:
                database.add_item(data)
                flash(f"Added {data['name']}.", "success")
                return redirect(url_for("dashboard"))
            return render_template("form.html", item=data, mode="add")
        return render_template("form.html", item={}, mode="add")

    @app.route("/import", methods=["GET", "POST"])
    @auth.login_required
    def import_json():
        if request.method == "GET":
            return render_template("import.html")

        raw = _read_import_payload(request)
        if not raw.strip():
            flash("Choose a JSON file or paste JSON to import.", "error")
            return render_template("import.html")

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            flash(f"Could not parse JSON: {exc}", "error")
            return render_template("import.html")

        entries = _import_entries(payload)
        if entries is None:
            flash("JSON must be a software object, a list of them, "
                  "or {\"items\": [ ... ]}.", "error")
            return render_template("import.html")

        added, errors = 0, []
        for i, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                errors.append(f"Entry {i}: not a JSON object.")
                continue
            data = _json_to_item(entry)
            error = _item_error(data)
            if error:
                label = f" ({data['name']})" if data["name"] else ""
                errors.append(f"Entry {i}{label}: {error}.")
            else:
                database.add_item(data)
                added += 1

        if added:
            flash(f"Imported {added} item(s).", "success")
        if errors:
            shown = "; ".join(errors[:10])
            extra = "" if len(errors) <= 10 else f" (+{len(errors) - 10} more)"
            flash(f"Skipped {len(errors)} entr"
                  f"{'y' if len(errors) == 1 else 'ies'}: {shown}{extra}", "error")
        if added:
            return redirect(url_for("dashboard"))
        return render_template("import.html")

    @app.route("/export")
    @auth.login_required
    def export_json():
        """Download the watchlist as a JSON file that /import can read back."""
        items = [
            {f: (it.get(f) or "") for f in ITEM_FIELDS}
            for it in database.list_items()
        ]
        payload = json.dumps({"items": items}, indent=2, ensure_ascii=False)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return Response(
            payload,
            mimetype="application/json",
            headers={
                "Content-Disposition":
                    f'attachment; filename="releaseradar-export-{stamp}.json"',
            },
        )

    @app.route("/edit/<int:item_id>", methods=["GET", "POST"])
    @auth.login_required
    def edit(item_id):
        item = database.get_item(item_id)
        if not item:
            flash("Item not found.", "error")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            data = _form_to_item(request.form)
            error = _item_error(data)
            if error:
                flash(f"Cannot save: {error}.", "error")
                return render_template("form.html", item={**item, **data}, mode="edit")
            database.update_item(item_id, data)
            flash(f"Updated {data['name']}.", "success")
            return redirect(url_for("dashboard"))
        return render_template("form.html", item=item, mode="edit")

    @app.route("/delete/<int:item_id>", methods=["POST"])
    @auth.login_required
    def delete(item_id):
        item = database.get_item(item_id)
        if item:
            database.delete_item(item_id)
            flash(f"Deleted {item['name']}.", "success")
        return redirect(url_for("dashboard"))

    # --- Version checks ---------------------------------------------------
    @app.route("/check/<int:item_id>", methods=["POST"])
    @auth.login_required
    def check_one(item_id):
        item = database.get_item(item_id)
        if not item:
            flash("Item not found.", "error")
            return redirect(url_for("dashboard"))
        result = check_item(item)
        database.save_check_result(item_id, result)
        flash(_check_summary(item["name"], result), _flash_level(result))
        return redirect(_safe_referrer())

    @app.route("/check-all", methods=["POST"])
    @auth.login_required
    def check_all():
        items = database.list_items()
        # Checks are network-bound and independent — run them concurrently,
        # then write the results from this thread (SQLite connections here
        # are per-call, but keeping writes serial avoids lock contention).
        if items:
            with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
                results = list(pool.map(check_item, items))
        else:
            results = []
        updates = 0
        for item, result in zip(items, results):
            database.save_check_result(item["id"], result)
            if result["status"] == "update_available":
                updates += 1
        flash(f"Checked {len(items)} item(s); {updates} have updates available.", "success")
        return redirect(url_for("dashboard"))

    # --- AI classification ------------------------------------------------
    @app.route("/classify/<int:item_id>", methods=["POST"])
    @auth.login_required
    def classify_ai(item_id):
        item = database.get_item(item_id)
        if not item:
            flash("Item not found.", "error")
            return redirect(url_for("dashboard"))
        if not classifier.ai_available():
            flash("AI classification is off. Set ANTHROPIC_API_KEY to enable it.", "error")
            return redirect(_safe_referrer())

        result = classifier.ai_classify(
            item["name"], item.get("latest_version"), item.get("release_notes")
        )
        # Persist the AI verdict over the heuristic one.
        database.save_check_result(item_id, {
            "latest_version": item.get("latest_version"),
            "latest_source": item.get("latest_source"),
            "status": item.get("status", "unknown"),
            "update_type": result["update_type"],
            "classify_method": "ai",
            "release_notes": item.get("release_notes"),
            "source_results": _parse_json(item.get("source_results")),
        })
        flash(
            f"AI classified {item['name']} as '{result['update_type']}' "
            f"({result['confidence']:.0%}): {result['reason']}",
            "success" if result["update_type"] != "unknown" else "error",
        )
        return redirect(_safe_referrer())


# --- Helpers --------------------------------------------------------------

def _parse_json(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}


def _read_import_payload(req):
    """Raw JSON text from an upload, falling back to a pasted-in textarea."""
    upload = req.files.get("file")
    if upload and upload.filename:
        return upload.read().decode("utf-8", errors="replace")
    return req.form.get("pasted", "")


def _import_entries(payload):
    """Coerce a parsed JSON payload into a list of entry dicts.

    Accepts one software object, a bare list of them, or a wrapper object with
    an ``items`` (or ``software``) list. Returns None if the shape is unusable.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "software"):
            if isinstance(payload.get(key), list):
                return payload[key]
        return [payload]
    return None


def _check_summary(name, result):
    if result["status"] == "update_available":
        return (f"{name}: update available → {result['latest_version']} "
                f"(currently {result.get('latest_source', '?')}), "
                f"type: {result['update_type']}.")
    if result["status"] == "up_to_date":
        return f"{name}: up to date ({result['latest_version']})."
    if result["status"] == "error":
        return f"{name}: check failed — " + "; ".join(result["errors"][:3])
    return f"{name}: checked."


def _flash_level(result):
    return "error" if result["status"] == "error" else "success"


app = create_app()


if __name__ == "__main__":
    print(f"ReleaseRadar running at http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False)
