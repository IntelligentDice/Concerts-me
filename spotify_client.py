# spotify_client.py
import requests
from typing import Dict, Any, List
from spotify_auth import get_access_token

BASE = "https://api.spotify.com/v1"

def _auth_header():
    token = get_access_token()
    return {"Authorization": f"Bearer {token}"}

def _request(method: str, path: str, **kwargs) -> Any:
    url = BASE + path
    headers = kwargs.pop("headers", {})
    headers.update(_auth_header())
    r = requests.request(method, url, headers=headers, timeout=15, **kwargs)

    # retry once on 401
    if r.status_code == 401:
        headers.update(_auth_header())  # refresh and retry
        r = requests.request(method, url, headers=headers, timeout=15, **kwargs)

    r.raise_for_status()
    if r.status_code == 204:
        return None
    return r.json()

def get(path: str, params: Dict[str, Any] = None):
    return _request("GET", path, params=params)

def post(path: str, payload: Dict[str, Any]):
    return _request("POST", path, json=payload)

def put(path: str, payload: Dict[str, Any]):
    return _request("PUT", path, json=payload)
