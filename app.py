"""ReleaseRadar — a single-user dashboard for tracking new software versions.

Run:  python app.py
Then open http://127.0.0.1:5000 in your browser.

The app only listens on localhost by default and is gated behind one password,
so it's safe to run on your work machine.
"""

import json
import secrets

from flask import (
    Flask, flash, redirect, render_template, request, session, url_for
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
    )

    register_routes(app)
    return app


def _form_to_item(form):
    return {
        "name": form.get("name", "").strip(),
        "platform": form.get("platform", "windows").strip(),
        "current_version": form.get("current_version", "").strip(),
        "winget_id": form.get("winget_id", "").strip(),
        "choco_id": form.get("choco_id", "").strip(),
        "patchmypc_name": form.get("patchmypc_name", "").strip(),
        "homebrew_cask": form.get("homebrew_cask", "").strip(),
        "rss_url": form.get("rss_url", "").strip(),
        "notes": form.get("notes", "").strip(),
    }


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
            if not data["name"]:
                flash("Name is required.", "error")
            elif not any(data[c] for c in REGISTRY):
                flash("Add at least one source identifier (winget, choco, etc.).", "error")
            else:
                database.add_item(data)
                flash(f"Added {data['name']}.", "success")
                return redirect(url_for("dashboard"))
            return render_template("form.html", item=data, mode="add")
        return render_template("form.html", item={}, mode="add")

    @app.route("/edit/<int:item_id>", methods=["GET", "POST"])
    @auth.login_required
    def edit(item_id):
        item = database.get_item(item_id)
        if not item:
            flash("Item not found.", "error")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            data = _form_to_item(request.form)
            if not data["name"]:
                flash("Name is required.", "error")
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
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/check-all", methods=["POST"])
    @auth.login_required
    def check_all():
        items = database.list_items()
        updates = 0
        for item in items:
            result = check_item(item)
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
            return redirect(request.referrer or url_for("dashboard"))

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
        return redirect(request.referrer or url_for("dashboard"))


# --- Helpers --------------------------------------------------------------

def _parse_json(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return {}


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
