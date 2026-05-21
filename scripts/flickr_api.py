"""Flickr OAuth 1.0a signing and HTTP helpers."""

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

CREDENTIALS_FILE = os.path.expanduser("~/.flickr_mcp/credentials.json")
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
API_URL = "https://api.flickr.com/services/rest/"
HTTP_TIMEOUT = int(os.environ.get("FLICKR_HTTP_TIMEOUT", 30))


def _load_env():
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


def _load_credentials():
    if not os.path.exists(CREDENTIALS_FILE):
        raise RuntimeError("Not logged in. Visit http://localhost:8000/login to authenticate.")
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def _sign(method, url, params, api_secret, token_secret=""):
    sorted_params = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    base = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
    sig = hmac.new(key.encode(), base.encode(), hashlib.sha1)
    return base64.b64encode(sig.digest()).decode()


def _oauth_params(api_key, extra=None):
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


def _api_get(method, extra=None):
    api_key, api_secret = _load_env()
    creds = _load_credentials()
    params = _oauth_params(api_key, {
        "oauth_token": creds["oauth_token"],
        "method": method,
        "format": "json",
        "nojsoncallback": "1",
    })
    if extra:
        params.update(extra)
    params["oauth_signature"] = _sign("GET", API_URL, params, api_secret, creds["oauth_token_secret"])
    try:
        resp = requests.get(API_URL, params=params, timeout=HTTP_TIMEOUT)
    except requests.exceptions.Timeout:
        logging.error("GET %s timed out after %ss", method, HTTP_TIMEOUT)
        raise RuntimeError(f"Flickr API request timed out ({method})")
    except requests.exceptions.RequestException as e:
        logging.error("GET %s failed: %s", method, e)
        raise RuntimeError(f"Flickr API request failed ({method}): {e}")
    if resp.status_code == 429:
        logging.error("GET %s rate limited (HTTP 429)", method)
        raise RuntimeError(f"Flickr rate limit hit ({method})")
    if not resp.ok:
        logging.error("GET %s HTTP %s", method, resp.status_code)
        raise RuntimeError(f"Flickr API HTTP {resp.status_code} ({method})")
    data = resp.json()
    if data.get("stat") != "ok":
        raise RuntimeError(f"Flickr API error: {data.get('message', 'unknown')}")
    return data


def _api_post(method, extra=None):
    api_key, api_secret = _load_env()
    creds = _load_credentials()
    params = _oauth_params(api_key, {
        "oauth_token": creds["oauth_token"],
        "method": method,
        "format": "json",
        "nojsoncallback": "1",
    })
    if extra:
        params.update(extra)
    params["oauth_signature"] = _sign("POST", API_URL, params, api_secret, creds["oauth_token_secret"])
    try:
        resp = requests.post(API_URL, data=params, timeout=HTTP_TIMEOUT)
    except requests.exceptions.Timeout:
        logging.error("POST %s timed out after %ss", method, HTTP_TIMEOUT)
        raise RuntimeError(f"Flickr API request timed out ({method})")
    except requests.exceptions.RequestException as e:
        logging.error("POST %s failed: %s", method, e)
        raise RuntimeError(f"Flickr API request failed ({method}): {e}")
    if resp.status_code == 429:
        logging.error("POST %s rate limited (HTTP 429)", method)
        raise RuntimeError(f"Flickr rate limit hit ({method})")
    if not resp.ok:
        logging.error("POST %s HTTP %s", method, resp.status_code)
        raise RuntimeError(f"Flickr API HTTP {resp.status_code} ({method})")
    data = resp.json()
    if data.get("stat") != "ok":
        raise RuntimeError(f"Flickr API error: {data.get('message', 'unknown')}")
    return data
