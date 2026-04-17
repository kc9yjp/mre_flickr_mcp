import hashlib
import hmac
import time
import urllib.parse
import requests
import json
import sys

# Load credentials
api_key = "b5451bada0439dceca3abf51686605ff"
api_secret = "b357569f898fdf9d"

REQUEST_TOKEN_URL = "https://www.flickr.com/services/oauth/request_token"
AUTHORIZE_URL = "https://www.flickr.com/services/oauth/authorize"
ACCESS_TOKEN_URL = "https://www.flickr.com/services/oauth/access_token"
CALLBACK = "oob"  # out-of-band, user copies verifier manually

def sign_request(method, url, params, secret, token_secret=""):
    """Generate OAuth 1.0a signature"""
    sorted_params = urllib.parse.urlencode(sorted(params.items()))
    base_string = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(sorted_params, safe='')}"
    signing_key = f"{urllib.parse.quote(secret, safe='')}&{urllib.parse.quote(token_secret, safe='')}"
    signature = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1)
    import base64
    return base64.b64encode(signature.digest()).decode()

def get_request_token():
    params = {
        "oauth_nonce": hashlib.md5(str(time.time()).encode()).hexdigest(),
        "oauth_timestamp": str(int(time.time())),
        "oauth_consumer_key": api_key,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_version": "1.0",
        "oauth_callback": CALLBACK,
    }
    params["oauth_signature"] = sign_request("GET", REQUEST_TOKEN_URL, params, api_secret)
    
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
        
        params = {
            "oauth_nonce": hashlib.md5(str(time.time()).encode()).hexdigest(),
            "oauth_timestamp": str(int(time.time())),
            "oauth_consumer_key": api_key,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_version": "1.0",
            "oauth_token": token,
            "oauth_verifier": verifier,
        }
        params["oauth_signature"] = sign_request("GET", ACCESS_TOKEN_URL, params, api_secret, token_secret)
        
        resp = requests.get(ACCESS_TOKEN_URL, params=params)
        if resp.status_code != 200:
            print(f"Error: {resp.status_code} - {resp.text}", file=sys.stderr)
            sys.exit(1)
        
        data = dict(urllib.parse.parse_qsl(resp.text))
        print(json.dumps(data))
