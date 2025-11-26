# apple_music.py
import requests
import time

APPLE_MUSIC_BASE = "https://api.music.apple.com/v1"

class AppleMusic:
    def __init__(self, developer_token, user_token, storefront="us"):
        self.developer_token = developer_token
        self.user_token = user_token
        self.storefront = storefront
        self.auth_headers = {
            "Authorization": f"Bearer {self.developer_token}"
        }
        self.user_headers = {
            "Authorization": f"Bearer {self.developer_token}",
            "Music-User-Token": self.user_token,
            "Content-Type": "application/json"
        }

    def search_track(self, song_name, artist_name, limit=1):
        q = f"{song_name} {artist_name}"
        url = f"{APPLE_MUSIC_BASE}/catalog/{self.storefront}/search"
        params = {"term": q, "types": "songs", "limit": limit}
        r = requests.get(url, params=params, headers=self.auth_headers)
        r.raise_for_status()
        data = r.json()
        songs = data.get("results", {}).get("songs", {}).get("data", [])
        if not songs:
            return None
        return songs[0]["id"]

    def search_artist(self, artist_name, limit=3):
        """
        Search catalog for artist and return list of artist objects (id, name) ordered by relevance.
        """
        url = f"{APPLE_MUSIC_BASE}/catalog/{self.storefront}/search"
        params = {"term": artist_name, "types": "artists", "limit": limit}
        r = requests.get(url, params=params, headers=self.auth_headers)
        r.raise_for_status()
        data = r.json()
        artists = data.get("results", {}).get("artists", {}).get("data", [])
        # return list of {"id": id, "name": name}
        return [{"id": a["id"], "name": a["attributes"].get("name", "")} for a in artists]

    def get_artist_top_tracks(self, artist_id, limit=5):
        """
        Fetch top songs for an artist from catalog.
        Uses endpoint: /catalog/{storefront}/artists/{id}/top-songs
        """
        url = f"{APPLE_MUSIC_BASE}/catalog/{self.storefront}/artists/{artist_id}/top-songs"
        params = {"limit": limit}
        r = requests.get(url, params=params, headers=self.auth_headers)
        if r.status_code != 200:
            return []
        data = r.json()
        songs = data.get("data", [])
        return [s["id"] for s in songs[:limit]]

    def create_playlist(self, name, description=""):
        url = f"{APPLE_MUSIC_BASE}/me/library/playlists"
        payload = {
            "attributes": {
                "name": name,
                "description": description
            }
        }
        r = requests.post(url, json=payload, headers=self.user_headers)
        r.raise_for_status()
        return r.json()

    def add_tracks_to_playlist(self, playlist_id, catalog_ids):
        url = f"{APPLE_MUSIC_BASE}/me/library/playlists/{playlist_id}/tracks"
        resources = [{"id": cid, "type": "songs"} for cid in catalog_ids]
        payload = {"data": resources}
        r = requests.post(url, json=payload, headers=self.user_headers)
        r.raise_for_status()
        return r.json()
