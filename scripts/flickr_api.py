"""Flickr OAuth 1.0a signing, HTTP helpers, and credential management.

Credentials are stored per-user under ``~/.flickr_mcp/{nsid}/credentials.json``
so that multiple Flickr accounts can coexist on the same server.  The legacy
flat path ``~/.flickr_mcp/credentials.json`` (``CREDENTIALS_FILE``) is kept as
a fallback and for test patching.

In multi-user mode, ``_load_credentials()`` resolves the active user from the
``db._current_user`` ContextVar when no explicit ``nsid`` is given — meaning
``_api_get()`` and ``_api_post()`` require no call-site changes.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse

import requests


class FlickrAPIError(RuntimeError):
    """Flickr application-level error with numeric error code preserved."""
    def __init__(self, code: int, message: str):
        super().__init__(f"Flickr API error {code}: {message}")
        self.code = code
        self.flickr_message = message


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CREDS_BASE = os.path.expanduser("~/.flickr_mcp")

# Legacy single-user path — kept for test patching and backward compatibility.
CREDENTIALS_FILE = os.path.join(_CREDS_BASE, "credentials.json")

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
API_URL = "https://api.flickr.com/services/rest/"
HTTP_TIMEOUT = int(os.environ.get("FLICKR_HTTP_TIMEOUT", 30))
_API_MAX_RETRIES = 3


def credentials_file(nsid: str) -> str:
    """Return the credentials file path for *nsid*.

    Each user's credentials live at ``~/.flickr_mcp/{nsid}/credentials.json``.
    """
    return os.path.join(_CREDS_BASE, nsid, "credentials.json")


# ---------------------------------------------------------------------------
# Environment / credentials
# ---------------------------------------------------------------------------

def _load_env():
    """Load ``FLICKR_API_KEY`` and ``FLICKR_API_SECRET`` from ``.env`` or environment.

    Returns ``(api_key, api_secret)`` or raises ``RuntimeError`` if either is missing.
    """
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    api_key = env.get("FLICKR_API_KEY") or os.environ.get("FLICKR_API_KEY")
    api_secret = env.get("FLICKR_API_SECRET") or os.environ.get("FLICKR_API_SECRET")
    if not api_key or not api_secret:
        raise RuntimeError("FLICKR_API_KEY and FLICKR_API_SECRET must be set in .env")
    return api_key, api_secret


def _load_credentials(nsid: str | None = None) -> dict:
    """Load OAuth credentials for the given user.

    Resolution order:
    1. ``nsid`` argument (explicit, used by sync scripts).
    2. ``db._current_user`` ContextVar (set per SSE connection by the web layer).
    3. Legacy single-user ``CREDENTIALS_FILE`` (fallback / test patching).

    Raises ``RuntimeError`` if no credentials file is found.
    """
    if nsid is None:
        try:
            from db import _current_user
            user = _current_user.get()
            if user:
                nsid = user["nsid"]
        except ImportError:
            pass

    path = credentials_file(nsid) if nsid else CREDENTIALS_FILE
    if not os.path.exists(path):
        raise RuntimeError("Not logged in. Visit http://localhost:8000/login to authenticate.")
    with open(path) as f:
        return json.load(f)


def _save_credentials(data: dict, nsid: str) -> None:
    """Persist *data* as the credentials file for *nsid*.

    Creates ``~/.flickr_mcp/{nsid}/`` with mode 0o700 if needed and writes the
    JSON file with mode 0o600 (owner read/write only).
    """
    path = credentials_file(nsid)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(data, f, indent=2)


def _all_known_users() -> list[dict]:
    """Return a list of ``{"nsid": ..., "username": ...}`` for every registered user.

    Scans ``~/.flickr_mcp/*/credentials.json`` at call time.  Used by the
    background refresh loop to iterate over all users.
    """
    users: list[dict] = []
    if not os.path.isdir(_CREDS_BASE):
        return users
    for entry in os.scandir(_CREDS_BASE):
        if not entry.is_dir():
            continue
        cpath = os.path.join(entry.path, "credentials.json")
        if not os.path.exists(cpath):
            continue
        try:
            with open(cpath) as f:
                creds = json.load(f)
            nsid = creds.get("user_nsid")
            username = creds.get("username")
            if nsid and username:
                users.append({"nsid": nsid, "username": username})
        except Exception:
            pass
    return users


def _resolve_api_key(api_key: str) -> dict | None:
    """Return ``{"nsid": ..., "username": ...}`` for the given MCP API key, or None.

    Scans all per-user credential files for a matching ``mcp_api_key``.
    """
    if not os.path.isdir(_CREDS_BASE):
        return None
    for entry in os.scandir(_CREDS_BASE):
        if not entry.is_dir():
            continue
        cpath = os.path.join(entry.path, "credentials.json")
        if not os.path.exists(cpath):
            continue
        try:
            with open(cpath) as f:
                creds = json.load(f)
            if creds.get("mcp_api_key") == api_key:
                nsid = creds.get("user_nsid")
                username = creds.get("username")
                if nsid and username:
                    return {"nsid": nsid, "username": username}
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def _sign(method, url, params, api_secret, token_secret=""):
    """Return the HMAC-SHA1 OAuth signature for a request."""
    sorted_params = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    base = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
    sig = hmac.new(key.encode(), base.encode(), hashlib.sha1)
    return base64.b64encode(sig.digest()).decode()


def _oauth_params(api_key, extra=None):
    """Return a base set of OAuth 1.0a parameters, optionally merged with *extra*."""
    p = {
        "oauth_nonce": secrets.token_hex(16),
        "oauth_timestamp": str(int(time.time())),
        "oauth_consumer_key": api_key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
    }
    if extra:
        p.update(extra)
    return p


# ---------------------------------------------------------------------------
# API wrappers
# ---------------------------------------------------------------------------

def _api_call(verb: str, method: str, params_factory) -> dict:
    """Send a signed Flickr API request with exponential-backoff retry.

    ``params_factory`` is called on every attempt so that each request gets a
    fresh OAuth nonce and timestamp.  Retries on network errors, timeouts,
    HTTP 429 (rate limit), and HTTP 5xx.  Other HTTP errors and Flickr
    application-level errors are raised immediately.

    Raises ``RuntimeError`` on permanent failure.
    """
    for attempt in range(_API_MAX_RETRIES):
        params = params_factory()
        try:
            if verb == "GET":
                resp = requests.get(API_URL, params=params, timeout=HTTP_TIMEOUT)
            else:
                resp = requests.post(API_URL, data=params, timeout=HTTP_TIMEOUT)
        except requests.exceptions.Timeout:
            if attempt < _API_MAX_RETRIES - 1:
                wait = 2 ** attempt
                logging.warning("%s %s timed out, retrying in %ds", verb, method, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Flickr API timed out ({method})")
        except requests.exceptions.RequestException as e:
            if attempt < _API_MAX_RETRIES - 1:
                wait = 2 ** attempt
                logging.warning("%s %s failed (%s), retrying in %ds", verb, method, e, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Flickr API request failed ({method}): {e}")

        if resp.status_code == 429:
            if attempt < _API_MAX_RETRIES - 1:
                wait = int(resp.headers.get("Retry-After", 60))
                logging.warning("Rate limited (HTTP 429) on %s, waiting %ds", method, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Flickr rate limit hit ({method})")

        if resp.status_code >= 500:
            if attempt < _API_MAX_RETRIES - 1:
                wait = 2 ** attempt
                logging.warning("HTTP %s on %s, retrying in %ds", resp.status_code, method, wait)
                time.sleep(wait)
                continue
            raise RuntimeError(f"Flickr API HTTP {resp.status_code} ({method})")

        if not resp.ok:
            raise RuntimeError(f"Flickr API HTTP {resp.status_code} ({method})")

        data = resp.json()
        if data.get("stat") != "ok":
            raise FlickrAPIError(data.get("code", 0), data.get("message", "unknown"))
        return data


def _api_get(method, extra=None):
    """Perform a signed OAuth GET against the Flickr REST API.

    Credentials are loaded via ``_load_credentials()`` which resolves the active
    user from the ``db._current_user`` ContextVar automatically.
    Retries up to ``_API_MAX_RETRIES`` times on transient errors.
    Raises ``RuntimeError`` on permanent failure.
    """
    api_key, api_secret = _load_env()
    creds = _load_credentials()

    def _make_params():
        p = _oauth_params(api_key, {
            "oauth_token": creds["oauth_token"],
            "method": method,
            "format": "json",
            "nojsoncallback": "1",
        })
        if extra:
            p.update(extra)
        p["oauth_signature"] = _sign("GET", API_URL, p, api_secret, creds["oauth_token_secret"])
        return p

    return _api_call("GET", method, _make_params)


def _api_post(method, extra=None):
    """Perform a signed OAuth POST against the Flickr REST API.

    Credentials are loaded via ``_load_credentials()`` which resolves the active
    user from the ``db._current_user`` ContextVar automatically.
    Retries up to ``_API_MAX_RETRIES`` times on transient errors.
    Raises ``RuntimeError`` on permanent failure.
    """
    api_key, api_secret = _load_env()
    creds = _load_credentials()

    def _make_params():
        p = _oauth_params(api_key, {
            "oauth_token": creds["oauth_token"],
            "method": method,
            "format": "json",
            "nojsoncallback": "1",
        })
        if extra:
            p.update(extra)
        p["oauth_signature"] = _sign("POST", API_URL, p, api_secret, creds["oauth_token_secret"])
        return p

    return _api_call("POST", method, _make_params)
