import hashlib
import hmac
import time
import urllib.parse
import requests
import base64
import json

api_key = "b5451bada0439dceca3abf51686605ff"
api_secret = "b357569f898fdf9d"
oauth_token = "72157720966247839-dc6b7b2dabdac0fd"
oauth_token_secret = "e0d807fe5ac9502c"

API_URL = "https://api.flickr.com/services/rest/"

def sign_request(url, params, token_secret):
    # For OAuth 1.0a, all params (oauth + API) go into the signature base string
    sorted_params = urllib.parse.urlencode(sorted(params.items()), quote_via=urllib.parse.quote)
    base_string = "POST&{}&{}".format(
        urllib.parse.quote(url, safe=''),
        urllib.parse.quote(sorted_params, safe='')
    )
    signing_key = "{}&{}".format(
        urllib.parse.quote(api_secret, safe=''),
        urllib.parse.quote(token_secret, safe='')
    )
    sig = hmac.new(signing_key.encode('utf-8'), base_string.encode('utf-8'), hashlib.sha1)
    return base64.b64encode(sig.digest()).decode('utf-8')

photo_id = "55172722065"
title = "Purple Crocuses in March Sunlight"
description = "Purple crocuses bloom in the warm March sunlight, their vivid violet petals opening to reveal bright orange-yellow stamens. Nestled among green leaves and last season's fallen foliage - a sure sign that spring has arrived."

params = {
    "oauth_nonce": hashlib.md5(str(time.time()).encode()).hexdigest(),
    "oauth_timestamp": str(int(time.time())),
    "oauth_consumer_key": api_key,
    "oauth_signature_method": "HMAC-SHA1",
    "oauth_version": "1.0",
    "oauth_token": oauth_token,
    "method": "flickr.photos.setMeta",
    "format": "json",
    "nojsoncallback": "1",
    "photo_id": photo_id,
    "title": title,
    "description": description,
}

params["oauth_signature"] = sign_request(API_URL, params, oauth_token_secret)

resp = requests.post(API_URL, data=params)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.text[:500]}")
