import requests
from spotify_auth import get_access_token
from typing import Dict, Any, List

BASE_URL = "https://api.spotify.com/v1"


def _auth_header(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(method: str, url: str, **kwargs) -> Any:
    """
    Universal request wrapper:
    - Injects access token
    - Automatically refreshes token on 401
    """

    # First attempt with cached token
    token = get_access_token()
    headers = kwargs.pop("headers", {})
    headers.update(_auth_header(token))

    response = requests.request(method, url, headers=headers, **kwargs)

    # Token expired → refresh & retry once
    if response.status_code == 401:
        token = get_access_token()  # forces refresh
        headers.update(_auth_header(token))
        response = requests.request(method, url, headers=headers, **kwargs)

    # Any other failure
    if not response.ok:
        raise RuntimeError(
            f"Spotify API error {response.status_code}: {response.text}"
        )

    if response.status_code == 204:
        return None
    return response.json()


# -------------------------------------------------------
# PUBLIC API
# -------------------------------------------------------

def spotify_get(path: str, params: Dict[str, Any] = None):
    return _request("GET", f"{BASE_URL}{path}", params=params)


def spotify_post(path: str, payload: Dict[str, Any]):
    return _request("POST", f"{BASE_URL}{path}", json=payload)


def spotify_put(path: str, payload: Dict[str, Any]):
    return _request("PUT", f"{BASE_URL}{path}", json=payload)


# -------------------------------------------------------
# HIGH-LEVEL HELPERS FOR YOUR PROJECT
# -------------------------------------------------------

def search_track(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Perform a Spotify track search.
    Used for fuzzy matching from setlist.fm song names.
    """
    results = spotify_get("/search", params={
        "q": query,
        "type": "track",
        "limit": limit
    })
    return results.get("tracks", {}).get("items", [])


def create_playlist(user_id: str, name: str, description: str = "") -> str:
    """
    Create a new playlist for a concert.
    Returns playlist_id.
    """
    payload = {
        "name": name,
        "description": description,
        "public": False
    }
    res = spotify_post(f"/users/{user_id}/playlists", payload)
    return res["id"]


def add_tracks_to_playlist(playlist_id: str, track_uris: List[str]):
    """
    Add a list of track URIs to a playlist.
    Spotify wants them in groups of 100.
    """
    for i in range(0, len(track_uris), 100):
        batch = track_uris[i:i+100]
        spotify_post(f"/playlists/{playlist_id}/tracks", {"uris": batch})
