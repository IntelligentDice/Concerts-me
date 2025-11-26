# spotify_api.py
from spotify_client import get, post
from typing import List, Dict

def get_current_user_id() -> str:
    res = get("/me")
    return res["id"]

def search_track(query: str, limit: int = 5) -> List[Dict]:
    res = get("/search", params={"q": query, "type": "track", "limit": limit})
    return res.get("tracks", {}).get("items", [])

def create_playlist(user_id: str, name: str, description: str = "") -> str:
    payload = {"name": name, "description": description, "public": False}
    res = post(f"/users/{user_id}/playlists", payload)
    return res["id"]

def add_tracks_to_playlist(playlist_id: str, track_uris: List[str]):
    # Spotify accepts up to 100 at a time
    for i in range(0, len(track_uris), 100):
        batch = track_uris[i:i+100]
        post(f"/playlists/{playlist_id}/tracks", {"uris": batch})
