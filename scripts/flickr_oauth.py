import os
import sys
import time
import urllib.parse
import requests
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flickr_api

api_key, api_secret = flickr_api._load_env()

REQUEST_TOKEN_URL = "https://www.flickr.com/services/oauth/request_token"
AUTHORIZE_URL = "https://www.flickr.com/services/oauth/authorize"
ACCESS_TOKEN_URL = "https://www.flickr.com/services/oauth/access_token"
CALLBACK = "oob"  # out-of-band, user copies verifier manually

def get_request_token():
    params = flickr_api._oauth_params(api_key, {"oauth_callback": CALLBACK})
    params["oauth_signature"] = flickr_api._sign("GET", REQUEST_TOKEN_URL, params, api_secret)
    
    resp = requests.get(REQUEST_TOKEN_URL, params=params)
    if resp.status_code != 200:
        print(f"Error: {resp.status_code} - {resp.text}", file=sys.stderr)
        sys.exit(1)
    
    data = dict(urllib.parse.parse_qsl(resp.text))
    if data.get("oauth_callback_confirmed") != "true":
        print(f"Callback not confirmed: {resp.text}", file=sys.stderr)
        sys.exit(1)
    
    return data["oauth_token"], data["oauth_token_secret"]

if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Step 1: Get request token
        token, token_secret = get_request_token()
        auth_url = f"{AUTHORIZE_URL}?oauth_token={token}&perms=write"
        print(json.dumps({
            "oauth_token": token,
            "oauth_token_secret": token_secret,
            "authorize_url": auth_url
        }))
    elif sys.argv[1] == "exchange":
        # Step 2: Exchange verifier for access token
        token = sys.argv[2]
        token_secret = sys.argv[3]
        verifier = sys.argv[4]
        
        params = flickr_api._oauth_params(api_key, {
            "oauth_token": token,
            "oauth_verifier": verifier,
        })
        params["oauth_signature"] = flickr_api._sign("GET", ACCESS_TOKEN_URL, params, api_secret, token_secret)
        
        resp = requests.get(ACCESS_TOKEN_URL, params=params)
        if resp.status_code != 200:
            print(f"Error: {resp.status_code} - {resp.text}", file=sys.stderr)
            sys.exit(1)
        
        data = dict(urllib.parse.parse_qsl(resp.text))
        print(json.dumps(data))
