import os
import time
import requests
from typing import Optional, Tuple, List

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"


# -----------------------------------------------------------
#  Token Refresh (with retries + diagnostics)
# -----------------------------------------------------------
_cached_token = None
_cached_expiry = 0


def get_access_token(max_retries=3, backoff=1.5) -> str:
    """
    Refresh Spotify API token.
    GitHub Actions does not allow browser redirect, so we rely
    on a permanent refresh token only.
    """

    global _cached_token, _cached_expiry

    now = time.time()
    if _cached_token and now < _cached_expiry - 30:
        return _cached_token

    auth_header = (SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": SPOTIFY_REFRESH_TOKEN,
    }

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(TOKEN_URL, data=payload, auth=auth_header, timeout=15)

            if resp.status_code != 200:
                print(
                    f"[ERROR] Spotify token refresh failed "
                    f"(attempt {attempt}/{max_retries}): status={resp.status_code} body={resp.text}"
                )
                time.sleep(backoff * attempt)
                continue

            data = resp.json()
            access_token = data["access_token"]
            expires_in = data.get("expires_in", 3600)

            _cached_token = access_token
            _cached_expiry = now + expires_in

            print(f"[DEBUG] Spotify token refreshed successfully (expires in {expires_in}s)")
            return access_token

        except Exception as e:
            print(
                f"[ERROR] Exception refreshing Spotify token "
                f"(attempt {attempt}/{max_retries}): {e}"
            )
            time.sleep(backoff * attempt)

    raise RuntimeError("Spotify token refresh failed after all retries")


# -----------------------------------------------------------
#  Internal API request wrapper
# -----------------------------------------------------------
def _api(method: str, path: str, params=None, json=None, max_retries=3):
    """
    Internal helper for calling Spotify Web API with retry logic.
    """
    url = f"{API_BASE}{path}"

    for attempt in range(1, max_retries + 1):
        try:
            token = get_access_token()
            headers = {"Authorization": f"Bearer {token}"}

            resp = requests.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=15,
            )

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "3"))
                print(f"[WARN] Spotify rate limit hit, sleeping for {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code in (500, 502, 503, 504):
                print(
                    f"[WARN] Spotify server error {resp.status_code}, retrying "
                    f"({attempt}/{max_retries})"
                )
                time.sleep(1.5 * attempt)
                continue

            if resp.status_code != 200:
                print(
                    f"[ERROR] Spotify API error on {method} {path}: "
                    f"status={resp.status_code} body={resp.text}"
                )
                return None

            return resp.json()

        except Exception as e:
            print(
                f"[ERROR] Exception in Spotify API call ({method} {path}) "
                f"(attempt {attempt}/{max_retries}): {e}"
            )
            time.sleep(1.5 * attempt)

    print(f"[ERROR] Spotify API failed after all retries for {method} {path}")
    return None


# -----------------------------------------------------------
#  SEARCH: Track
# -----------------------------------------------------------
def search_track(query: str, limit: int = 10) -> List[dict]:
    params = {"q": query, "type": "track", "limit": limit}
    data = _api("GET", "/search", params=params)
    if not data:
        return []
    return data.get("tracks", {}).get("items", [])


# -----------------------------------------------------------
#  ARTIST top tracks
# -----------------------------------------------------------
def get_artist_top_tracks(artist_id: str) -> List[dict]:
    data = _api("GET", f"/artists/{artist_id}/top-tracks", params={"market": "US"})
    if not data:
        return []
    return data.get("tracks", [])


# -----------------------------------------------------------
#  ALBUM → all tracks
# -----------------------------------------------------------
def get_album_tracks(album_id: str) -> List[dict]:
    data = _api("GET", f"/albums/{album_id}/tracks")
    if not data:
        return []
    return data.get("items", [])


# -----------------------------------------------------------
#  Helper for PlaylistBuilder
# -----------------------------------------------------------
def best_match_track(track_name: str, artist_hint: Optional[str] = None) -> Optional[Tuple[str, float]]:
    """
    Returns (track_uri, confidence_score).
    PlaylistBuilder will pick the best match.
    """
    from rapidfuzz import fuzz

    query = f"{track_name} {artist_hint}" if artist_hint else track_name
    results = search_track(query)

    best_uri = None
    best_score = 0

    for item in results:
        name = item["name"]
        artists = ", ".join(a["name"] for a in item["artists"])

        score = (
            fuzz.token_set_ratio(track_name, name)
            + (fuzz.partial_ratio(artist_hint, artists) if artist_hint else 0)
        )

        if score > best_score:
            best_score = score
            best_uri = item["uri"]

    if best_uri:
        print(f"[DEBUG] Best match for '{track_name}' → {best_score} ({best_uri})")

    return (best_uri, best_score) if best_uri else None
