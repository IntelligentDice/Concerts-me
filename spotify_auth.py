# spotify_auth.py
import json, time, base64, requests
from pathlib import Path

CONFIG_PATH = Path("config.json")
_cached_token = None
_cached_expiry = 0

def _load_spotify_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError("config.json not found; copy config.template.json -> config.json and fill credentials")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf8"))
    sp = cfg.get("spotify", {})
    req = ["client_id", "client_secret", "refresh_token"]
    for k in req:
        if not sp.get(k):
            raise ValueError(f"Missing spotify.{k} in config.json")
    return sp

def _refresh_access_token(client_id, client_secret, refresh_token):
    url = "https://accounts.spotify.com/api/token"
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    r = requests.post(url, data=data, headers=headers, timeout=15)
    r.raise_for_status()
    payload = r.json()
    return payload["access_token"], payload.get("expires_in", 3600)

def get_access_token():
    global _cached_token, _cached_expiry
    now = time.time()
    if _cached_token and now < _cached_expiry:
        return _cached_token

    sp = _load_spotify_config()
    token, expires_in = _refresh_access_token(sp["client_id"], sp["client_secret"], sp["refresh_token"])
    _cached_token = token
    _cached_expiry = now + expires_in - 30
    return _cached_token
