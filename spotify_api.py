import os
import time
import requests
import logging

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

_cached_token = None
_cached_expiry = 0


def _ensure_creds():
    if not CLIENT_ID or not CLIENT_SECRET or not REFRESH_TOKEN:
        raise RuntimeError("Missing Spotify credentials in environment variables.")


def get_access_token():
    global _cached_token, _cached_expiry

    now = time.time()
    if _cached_token and now < _cached_expiry:
        return _cached_token

    _ensure_creds()

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
    }

    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data=payload,
        auth=(CLIENT_ID, CLIENT_SECRET),
        timeout=15
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Spotify token refresh failed: {resp.text}")

    data = resp.json()
    _cached_token = data["access_token"]
    _cached_expiry = now + data.get("expires_in", 3600) - 10

    return _cached_token


def _api(method, path, params=None, json_body=None):
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{SPOTIFY_API_BASE}{path}"

    resp = requests.request(
        method, url, headers=headers, params=params, json=json_body, timeout=15
    )

    if resp.status_code >= 400:
        raise RuntimeError(f"Spotify error {resp.status_code}: {resp.text}")

    return resp.json()


def get_current_user_id():
    return _api("GET", "/me")["id"]


def search_track(q, limit=8):
    params = {"q": q, "type": "track", "limit": limit}
    data = _api("GET", "/search", params=params)
    return data.get("tracks", {}).get("items", [])


def create_playlist(user_id, playlist_name, public=False, description=""):
    body = {"name": playlist_name, "public": public, "description": description}
    return _api("POST", f"/users/{user_id}/playlists", json_body=body)["id"]


def add_tracks_to_playlist(playlist_id, uris):
    if not uris:
        return

    CHUNK = 100
    for i in range(0, len(uris), CHUNK):
        chunk = uris[i:i + CHUNK]
        _api("POST", f"/playlists/{playlist_id}/tracks", json_body={"uris": chunk})
