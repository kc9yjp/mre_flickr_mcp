#!/usr/bin/env python3
"""Flickr CLI — login, status, logout"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import webbrowser

import requests

# Credentials saved outside the repo so they're never committed
CREDENTIALS_FILE = os.path.expanduser("~/.flickr_mcp/credentials.json")
ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

REQUEST_TOKEN_URL = "https://www.flickr.com/services/oauth/request_token"
AUTHORIZE_URL = "https://www.flickr.com/services/oauth/authorize"
ACCESS_TOKEN_URL = "https://www.flickr.com/services/oauth/access_token"
API_URL = "https://api.flickr.com/services/rest/"


def load_env():
    """Load FLICKR_API_KEY and FLICKR_API_SECRET from .env or environment."""
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
        print("Error: FLICKR_API_KEY and FLICKR_API_SECRET must be set in .env", file=sys.stderr)
        sys.exit(1)

    return api_key, api_secret


def sign_request(method, url, params, api_secret, token_secret=""):
    sorted_params = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    base_string = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    signing_key = f"{urllib.parse.quote(api_secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
    sig = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1)
    return base64.b64encode(sig.digest()).decode()


def load_credentials():
    if not os.path.exists(CREDENTIALS_FILE):
        return None
    with open(CREDENTIALS_FILE) as f:
        return json.load(f)


def save_credentials(data):
    os.makedirs(os.path.dirname(CREDENTIALS_FILE), exist_ok=True)
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def oauth_params(api_key, extra=None):
    params = {
        "oauth_nonce": hashlib.md5(str(time.time()).encode()).hexdigest(),
        "oauth_timestamp": str(int(time.time())),
        "oauth_consumer_key": api_key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
    }
    if extra:
        params.update(extra)
    return params


# --- Commands ---

def cmd_login(args):
    api_key, api_secret = load_env()

    # Step 1: get request token
    params = oauth_params(api_key, {"oauth_callback": "oob"})
    params["oauth_signature"] = sign_request("GET", REQUEST_TOKEN_URL, params, api_secret)
    resp = requests.get(REQUEST_TOKEN_URL, params=params)
    if resp.status_code != 200:
        print(f"Error getting request token: {resp.text}", file=sys.stderr)
        sys.exit(1)
    token_data = dict(urllib.parse.parse_qsl(resp.text))
    request_token = token_data["oauth_token"]
    request_token_secret = token_data["oauth_token_secret"]

    # Step 2: direct user to authorize
    auth_url = f"{AUTHORIZE_URL}?oauth_token={request_token}&perms=write"
    print("Opening Flickr authorization page in your browser...")
    print(f"If it doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    verifier = input("Paste the verification code from Flickr: ").strip()

    # Step 3: exchange verifier for access token
    params = oauth_params(api_key, {"oauth_token": request_token, "oauth_verifier": verifier})
    params["oauth_signature"] = sign_request("GET", ACCESS_TOKEN_URL, params, api_secret, request_token_secret)
    resp = requests.get(ACCESS_TOKEN_URL, params=params)
    if resp.status_code != 200:
        print(f"Error exchanging token: {resp.text}", file=sys.stderr)
        sys.exit(1)
    access = dict(urllib.parse.parse_qsl(resp.text))

    creds = {
        "oauth_token": access["oauth_token"],
        "oauth_token_secret": access["oauth_token_secret"],
        "user_nsid": access.get("user_nsid", ""),
        "username": access.get("username", ""),
        "fullname": access.get("fullname", ""),
    }
    save_credentials(creds)
    print(f"\nLogged in as {creds['fullname']} (@{creds['username']})")


def cmd_status(args):
    api_key, api_secret = load_env()
    creds = load_credentials()

    if not creds:
        print("Not logged in. Run: python scripts/flickr.py login")
        return

    params = oauth_params(api_key, {
        "oauth_token": creds["oauth_token"],
        "method": "flickr.test.login",
        "format": "json",
        "nojsoncallback": "1",
    })
    params["oauth_signature"] = sign_request("GET", API_URL, params, api_secret, creds["oauth_token_secret"])
    resp = requests.get(API_URL, params=params)
    data = resp.json()

    if data.get("stat") == "ok":
        user = data["user"]
        print(f"Logged in as: {user['username']['_content']} (NSID: {user['id']})")
    else:
        print(f"Session invalid: {data.get('message', 'unknown error')}")
        print("Run: python scripts/flickr.py login")


def cmd_logout(args):
    if os.path.exists(CREDENTIALS_FILE):
        os.remove(CREDENTIALS_FILE)
        print("Logged out.")
    else:
        print("Not logged in.")


# --- Entry point ---

def main():
    parser = argparse.ArgumentParser(prog="flickr", description="Flickr CLI")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("login", help="Authenticate with Flickr via OAuth")
    sub.add_parser("status", help="Show current login status")
    sub.add_parser("logout", help="Clear saved credentials")

    args = parser.parse_args()

    commands = {
        "login": cmd_login,
        "status": cmd_status,
        "logout": cmd_logout,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
