# ReleaseRadar

A private, single-user dashboard for tracking new versions of the software your
enterprise supports — and telling you, for each update, whether it's a **security
update** or a **regular UI/feature update** so you can prioritize packaging work.

It checks multiple sources for the latest version of each app you track,
compares them against the version you currently package, and classifies the
release notes. Built for the manual Mac + Windows packaging workflow.

---

## What it does

- **One watchlist** of the software you support. Each item can pull from any mix
  of sources.
- **Five version sources**, queried independently per item:
  | Source | What it reads | Identifier you enter |
  |---|---|---|
  | **Winget** | `microsoft/winget-pkgs` manifests on GitHub | PackageIdentifier, e.g. `Google.Chrome` |
  | **Chocolatey** | community.chocolatey.org feed (incl. release notes) | package id, e.g. `googlechrome` |
  | **PatchMyPC** | public catalog release history (version + **its own security/feature label** + vendor notes link) | title, e.g. `Google Chrome` |
  | **Homebrew** | formulae.brew.sh cask API (**macOS**) | cask token, e.g. `google-chrome` |
  | **Vendor RSS** | any RSS/Atom feed or release-notes page | a URL |
- **Update classification** — *security* vs *UI/feature*:
  - **Keyword heuristics** run automatically on every check (offline, free). Any
    credible security signal — CVE ids, "vulnerability", "RCE", "CVSS", etc. —
    flags the update as **security**, which is the safe default for patch triage.
  - **AI analysis** (optional) — an "Analyze with AI" button on the detail page
    sends the release notes to Claude for a more nuanced call on ambiguous notes.
- **Single-user security** — one password, no default, brute-force lockout, and
  the server only listens on `127.0.0.1` (your machine) by default.

---

## Quick start

```bash
cd ReleaseRadar
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000**. On first run you'll set your password, then you
land on the dashboard.

1. **Add software** → give it a name and fill in whichever source identifiers
   apply (you don't need all of them — one is enough).
2. Set **Current packaged version** to whatever your enterprise currently ships.
3. Hit **Check all now** (or check a single item). New versions show up as
   *Update available* with a **Security** or **UI / Feature** badge.

---

## Configuration (environment variables)

All optional — sensible defaults are used.

| Variable | Purpose | Default |
|---|---|---|
| `RELEASERADAR_PASSWORD` | Pre-seed the password instead of using the setup page | — (use /setup) |
| `RELEASERADAR_SECRET_KEY` | Flask session signing key | random, persisted |
| `RELEASERADAR_HOST` | Bind address | `127.0.0.1` (localhost only) |
| `RELEASERADAR_PORT` | Port | `5000` |
| `RELEASERADAR_MAX_FAILED_LOGINS` | Failed logins before lockout | `5` |
| `RELEASERADAR_LOCKOUT_SECONDS` | Lockout duration | `300` |
| `ANTHROPIC_API_KEY` | Enables the "Analyze with AI" button | — (AI off) |
| `RELEASERADAR_AI_MODEL` | Claude model for AI classification | `claude-opus-4-8` |
| `GITHUB_TOKEN` | Raises the winget/GitHub API rate limit (60→5000/hr) | — |
| `RELEASERADAR_PATCHMYPC_YEAR` | Which PatchMyPC catalog-history year to read | current year |
| `HTTPS_PROXY` / `HTTP_PROXY` | Corporate proxy for **all** outbound calls (sources + AI) | — (direct) |
| `NO_PROXY` | Comma-separated hosts to reach without the proxy | — |
| `RELEASERADAR_CA_BUNDLE` | PEM bundle for a proxy that intercepts TLS | system trust store |
| `RELEASERADAR_SSL_VERIFY` | Set `0` to disable cert verification (last resort) | `1` (verify) |

For a large watchlist, setting `GITHUB_TOKEN` is recommended so winget lookups
don't hit GitHub's unauthenticated rate limit. To lower AI cost, set
`RELEASERADAR_AI_MODEL=claude-haiku-4-5`.

### Behind a corporate proxy

If your network only reaches the internet through a proxy, point ReleaseRadar at
it — every outbound call (the five version sources **and** the Claude AI call) is
routed through it:

```bash
export HTTPS_PROXY=http://user:pass@proxy.corp.example:8080   # creds optional
export NO_PROXY=localhost,127.0.0.1,.corp.example
python app.py
```

The standard `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` variables are honoured (the
same ones curl, pip and git use). `RELEASERADAR_HTTPS_PROXY` / `_HTTP_PROXY` /
`_NO_PROXY` override them if the radar needs a different proxy than the rest of
your shell.

Most corporate proxies intercept TLS with their own root CA. If you see
certificate-verification errors, export the proxy's PEM bundle so it's trusted:

```bash
export RELEASERADAR_CA_BUNDLE=/etc/ssl/certs/corp-root-ca.pem   # or REQUESTS_CA_BUNDLE
```

Only as a last resort, when no CA bundle is available, set
`RELEASERADAR_SSL_VERIFY=0` to skip verification entirely.

Example:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_TOKEN=ghp_...
python app.py
```

---

## How classification decides

On each check, ReleaseRadar gathers release notes from whichever source provides
them (Chocolatey and the vendor RSS feed are the richest), then:

1. **PatchMyPC's own label** is used when available — its catalog already tags
   every update as a *Security Update* or a regular *Update*, with a severity
   (Critical/Important/…). That authoritative label wins (badge: **PatchMyPC**).
2. **Chocolatey** contributes a security signal too: when its release notes name
   a CVE / vulnerability / security fix, that flags the update as **security**
   regardless of which source's notes are shown.
3. **Heuristics** scan any release-notes text for security indicators. A match →
   **security**. Otherwise UI/feature keywords → **UI / Feature**. A security
   signal in the notes always overrides a "non-security" catalog label, for safety.
4. **Deep-fetch verification** (badge: **vendor notes**) — for borderline updates
   (labelled non-security, or unresolved), ReleaseRadar follows the vendor's real
   release-notes link (from PatchMyPC, or a Chocolatey notes URL) and re-scans the
   full page. This only ever *upgrades* toward security — catching a fix the
   catalog lumped into a regular update. (It can't read pages that render their
   notes purely client-side via JavaScript, e.g. Google's Chrome Releases blog.)
5. **AI (on demand)** re-reads the notes with Claude and overwrites the verdict.
   The badge shows an **AI** tag when an AI verdict is in effect.

> Tip: add a **Vendor RSS / release-notes URL** for the apps you most care about
> — GitHub projects expose `…/releases.atom`, and most vendors publish a release
> notes feed. That gives the classifier real text to work with; a bare version
> number alone can only be classified as *unknown*.

---

## Security notes

- The app binds to `127.0.0.1` only. Don't change `RELEASERADAR_HOST` to `0.0.0.0`
  unless you intend to expose it, and never do so without putting it behind a
  TLS-terminating reverse proxy with its own auth.
- The password is stored only as a salted PBKDF2 hash. There is no account
  recovery — if you forget it, delete `data/releaseradar.db` and set a new one.
- This uses Flask's built-in dev server, which is fine for a personal,
  localhost-only tool. For anything beyond that, run it under a production WSGI
  server (gunicorn/waitress).

---

## Project layout

```
app.py            Flask app: routes, auth wiring, server entry point
auth.py           Single-user password auth + brute-force lockout
config.py         Environment-driven configuration
database.py       SQLite schema + helpers (settings + watchlist)
versions.py       Version parsing/comparison ("is B newer than A?")
checker.py        Runs all sources for an item and assembles the result
classifier.py     Security-vs-UI classification (heuristics + Claude)
sources/          One module per version source
  winget.py  chocolatey.py  patchmypc.py  homebrew.py  rss.py
templates/        Jinja2 views (dashboard, detail, form, login, setup)
static/style.css  Dark dashboard styling
data/             SQLite DB (gitignored — created on first run)
```

Data lives entirely in `data/releaseradar.db`. It's gitignored; nothing leaves
your machine except the outbound version lookups (and AI calls, if you enable them).
