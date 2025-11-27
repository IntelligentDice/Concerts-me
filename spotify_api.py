# spotify_api.py
import os
import time
import requests
import base64
from typing import List, Optional

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")  # optional; your flow may differ

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"

_session = requests.Session()

# --- token caching ---
_cached_token = None
_cached_expiry = 0


def _log(msg: str):
    print(f"[SPOTIFY] {msg}")


def get_access_token(max_retries: int = 3) -> str:
    global _cached_token, _cached_expiry
    now = time.time()
    if _cached_token and now < _cached_expiry - 30:
        return _cached_token

    if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
        # Use client credentials if refresh token not present
        if SPOTIFY_REFRESH_TOKEN:
            # if you use refresh token flow, use refresh_token grant
            for attempt in range(1, max_retries + 1):
                try:
                    resp = _session.post(
                        TOKEN_URL,
                        data={
                            "grant_type": "refresh_token",
                            "refresh_token": SPOTIFY_REFRESH_TOKEN
                        },
                        auth=(SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET),
                        timeout=15,
                    )
                except Exception as e:
                    _log(f"token request exception: {e}")
                    time.sleep(attempt)
                    continue

                if resp.status_code != 200:
                    _log(f"token request non-200: {resp.status_code} {resp.text[:200]}")
                    time.sleep(attempt)
                    continue

                data = resp.json()
                _cached_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                _cached_expiry = now + expires_in
                _log("Token acquired via refresh_token")
                return _cached_token
            raise RuntimeError("Spotify refresh token flow failed")
        else:
            # fallback to Client Credentials (no user scopes)
            for attempt in range(1, max_retries + 1):
                try:
                    auth_header = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
                    resp = _session.post(
                        TOKEN_URL,
                        data={"grant_type": "client_credentials"},
                        headers={"Authorization": f"Basic {auth_header}"},
                        timeout=15,
                    )
                except Exception as e:
                    _log(f"token request exception: {e}")
                    time.sleep(attempt)
                    continue

                if resp.status_code != 200:
                    _log(f"client credentials token non-200: {resp.status_code} {resp.text[:200]}")
                    time.sleep(attempt)
                    continue

                data = resp.json()
                _cached_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)
                _cached_expiry = now + expires_in
                _log("Token acquired via client_credentials")
                return _cached_token
            raise RuntimeError("Spotify client_credentials flow failed")
    else:
        raise RuntimeError("Missing Spotify credentials in environment")


def _call_api(method: str, path: str, params=None, json_body=None, retries: int = 2) -> Optional[dict]:
    url = f"{API_BASE}{path}"
    for attempt in range(1, retries + 1):
        token = get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = _session.request(method, url, headers=headers, params=params, json=json_body, timeout=15)
        except Exception as e:
            _log(f"Network error calling Spotify API: {e}")
            time.sleep(attempt)
            continue

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "2"))
            _log(f"Rate limited; sleeping {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code >= 500:
            _log(f"Server error {resp.status_code}; retrying")
            time.sleep(attempt)
            continue

        if resp.status_code not in (200, 201):
            _log(f"Spotify API returned {resp.status_code}: {resp.text[:400]}")
            return None

        try:
            return resp.json()
        except Exception:
            _log("Spotify JSON decode failure")
            return None
    return None

def find_playlist_by_name(name):
    """
    Returns playlist ID if a user's playlist with that exact name exists.
    Otherwise returns None.
    """
    token = ensure_token()
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}

    # Spotify paging: we search up to 50 playlists (can be increased)
    url = "https://api.spotify.com/v1/me/playlists?limit=50"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
    except Exception:
        return None

    if resp.status_code != 200:
        return None

    items = resp.json().get("items", [])
    for pl in items:
        if pl.get("name", "").strip().lower() == name.strip().lower():
            return pl.get("id")

    return None

# ----- helper functions expected by spotify_client/playlist_builder -----
def search_track(query: str, limit: int = 10) -> List[dict]:
    params = {"q": query, "type": "track", "limit": limit}
    data = _call_api("GET", "/search", params=params)
    if not data:
        return []
    return data.get("tracks", {}).get("items", [])


def get_artist_top_tracks(artist_id: str, market: str = "US") -> List[dict]:
    data = _call_api("GET", f"/artists/{artist_id}/top-tracks", params={"market": market})
    if not data:
        return []
    return data.get("tracks", [])


def get_album_tracks(album_id: str) -> List[dict]:
    data = _call_api("GET", f"/albums/{album_id}/tracks")
    if not data:
        return []
    return data.get("items", [])


def get_current_user_id() -> Optional[str]:
    data = _call_api("GET", "/me")
    if not data:
        return None
    return data.get("id")


def create_playlist(user_id: str, name: str, public: bool = False, description: str = "") -> Optional[str]:
    body = {"name": name, "public": public, "description": description}
    # POST /users/{user_id}/playlists
    data = _call_api("POST", f"/users/{user_id}/playlists", json_body=body)
    if not data:
        return None
    return data.get("id")


def add_tracks_to_playlist(playlist_id: str, uris: List[str]) -> bool:
    # Spotify accepts up to 100 URIs per request
    if not uris:
        return True
    CHUNK = 100
    for i in range(0, len(uris), CHUNK):
        chunk = uris[i : i + CHUNK]
        body = {"uris": chunk}
        res = _call_api("POST", f"/playlists/{playlist_id}/tracks", json_body=body)
        if res is None:
            return False
    return True
