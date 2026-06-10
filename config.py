"""Configuration for ReleaseRadar.

Everything that touches secrets, ports, or the AI model is centralised here and
read from the environment so nothing sensitive is hard-coded into the source.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "releaseradar.db"

# --- Security -------------------------------------------------------------
# The app is single-user. The owner's password is set on first run (or via the
# RELEASERADAR_PASSWORD env var) and stored only as a salted hash in the DB.
INITIAL_PASSWORD = os.environ.get("RELEASERADAR_PASSWORD")

# Flask session signing key. A random key is generated and persisted on first
# run if one isn't supplied, so logins survive restarts.
SECRET_KEY = os.environ.get("RELEASERADAR_SECRET_KEY")

# Only listen on localhost by default — the app never accepts remote
# connections unless the operator deliberately overrides HOST.
HOST = os.environ.get("RELEASERADAR_HOST", "127.0.0.1")
PORT = int(os.environ.get("RELEASERADAR_PORT", "5000"))

# Lock the account after this many consecutive failed logins, for this long.
MAX_FAILED_LOGINS = int(os.environ.get("RELEASERADAR_MAX_FAILED_LOGINS", "5"))
LOCKOUT_SECONDS = int(os.environ.get("RELEASERADAR_LOCKOUT_SECONDS", "300"))

# --- AI classification ----------------------------------------------------
# Optional. When ANTHROPIC_API_KEY is set, the "Analyze with AI" button uses
# Claude to classify ambiguous release notes. Model is overridable; default to
# the most capable model and let the operator drop to Haiku for lower cost.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
AI_MODEL = os.environ.get("RELEASERADAR_AI_MODEL", "claude-opus-4-8")

# Network timeout (seconds) for every outbound source lookup.
HTTP_TIMEOUT = int(os.environ.get("RELEASERADAR_HTTP_TIMEOUT", "20"))

# A descriptive UA — some source sites reject generic/empty agents.
USER_AGENT = "ReleaseRadar/1.0 (enterprise software version tracker)"


# --- Outbound / corporate proxy ------------------------------------------
# Many enterprise networks only reach the internet through an HTTP(S) proxy.
# *Every* outbound call ReleaseRadar makes — the version-source lookups and the
# optional Claude classification alike — is routed through the proxy configured
# here, so the app works on a locked-down corporate network out of the box.
#
# We honour the conventional HTTPS_PROXY / HTTP_PROXY / NO_PROXY variables (the
# same ones curl, pip and git read), with RELEASERADAR_-prefixed overrides
# taking precedence so the radar can use a different proxy than the rest of the
# shell. Put credentials in the URL when the proxy needs auth:
#     export HTTPS_PROXY=http://user:pass@proxy.corp.example:8080

def _env(*names):
    """First non-empty value among the given environment variable names."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


HTTP_PROXY = _env("RELEASERADAR_HTTP_PROXY", "HTTP_PROXY", "http_proxy")
# All of our endpoints are https; fall back to the http proxy if only it is set,
# since a single proxy commonly serves both schemes.
HTTPS_PROXY = _env("RELEASERADAR_HTTPS_PROXY", "HTTPS_PROXY", "https_proxy") or HTTP_PROXY
NO_PROXY = _env("RELEASERADAR_NO_PROXY", "NO_PROXY", "no_proxy")


def proxies():
    """A requests-style proxies mapping, or None when no proxy is configured."""
    mapping = {}
    if HTTP_PROXY:
        mapping["http"] = HTTP_PROXY
    if HTTPS_PROXY:
        mapping["https"] = HTTPS_PROXY
    return mapping or None


# Corporate proxies frequently intercept TLS with their own root CA, which makes
# certificate verification fail until that CA is trusted — the most common
# "it won't connect" symptom behind a proxy. Point this at the proxy's PEM
# bundle (or set the standard REQUESTS_CA_BUNDLE / SSL_CERT_FILE) so verification
# keeps working. As a last resort, RELEASERADAR_SSL_VERIFY=0 turns verification
# off entirely — avoid it unless you truly have no CA bundle to hand.
_CA_BUNDLE = _env("RELEASERADAR_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE")
_VERIFY_DISABLED = os.environ.get("RELEASERADAR_SSL_VERIFY", "1").strip().lower() in (
    "0", "false", "no", "off",
)


def tls_verify():
    """Value for the ``verify`` argument of requests / httpx.

    A CA-bundle path (str) when one is configured, ``False`` when verification
    is explicitly disabled, otherwise ``True`` (use the default trust store).
    """
    if _VERIFY_DISABLED:
        return False
    if _CA_BUNDLE:
        return _CA_BUNDLE
    return True
