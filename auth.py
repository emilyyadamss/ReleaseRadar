"""Single-user authentication.

The app is for exactly one person. There's no user table — just one password
hash stored in settings. Protections:

  - No default password. On first run the owner sets one (via the RELEASERADAR_
    PASSWORD env var, or the /setup page). Until then, login is impossible.
  - Password stored only as a salted PBKDF2 hash (werkzeug).
  - Brute-force lockout after MAX_FAILED_LOGINS attempts for LOCKOUT_SECONDS.
  - Constant-time hash comparison via werkzeug's check_password_hash.
"""

import secrets
import time
from functools import wraps

from flask import redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import database
from config import INITIAL_PASSWORD, MAX_FAILED_LOGINS, LOCKOUT_SECONDS


def bootstrap():
    """Seed the password from the env var on first run, if provided."""
    if not database.get_setting("password_hash") and INITIAL_PASSWORD:
        set_password(INITIAL_PASSWORD)


def password_is_set():
    return bool(database.get_setting("password_hash"))


def set_password(password):
    database.set_setting("password_hash", generate_password_hash(password))
    # Clear any lockout state when the password is (re)set.
    database.set_setting("failed_count", "0")
    database.set_setting("locked_until", "0")


def is_locked():
    locked_until = float(database.get_setting("locked_until", "0") or "0")
    remaining = locked_until - time.time()
    return (remaining > 0), int(max(0, remaining))


def verify(password):
    """Check a password, applying lockout bookkeeping. Returns True on success."""
    locked, _ = is_locked()
    if locked:
        return False

    stored = database.get_setting("password_hash")
    if stored and check_password_hash(stored, password):
        database.set_setting("failed_count", "0")
        database.set_setting("locked_until", "0")
        return True

    # Record the failure and possibly lock the account.
    failed = int(database.get_setting("failed_count", "0") or "0") + 1
    database.set_setting("failed_count", str(failed))
    if failed >= MAX_FAILED_LOGINS:
        database.set_setting("locked_until", str(time.time() + LOCKOUT_SECONDS))
        database.set_setting("failed_count", "0")
    return False


def login_session():
    """Mark the current session as authenticated."""
    session["authenticated"] = True
    session["token"] = secrets.token_hex(16)


def logout_session():
    session.clear()


def is_authenticated():
    return session.get("authenticated") is True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped
