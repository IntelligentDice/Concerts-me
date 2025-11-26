# spotify_client.py
import requests
import logging
import base64

class SpotifyClient:
    def __init__(self, client_id, client_secret, refresh_token, redirect_uri):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.redirect_uri = redirect_uri
        self.access_token = None

    def _refresh_access_token(self):
        logging.info("Refreshing Spotify access token…")

        auth_header = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()

        response = requests.post(
            "https://accounts.spotify.com/api/token",
            headers={"Authorization": f"Basic {auth_header}"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token
            }
        )

        if response.status_code != 200:
            logging.error("Failed to refresh token: %s", response.text)
            raise RuntimeError("Spotify token refresh failed")

        data = response.json()
        self.access_token = data["access_token"]

    def _auth_headers(self):
        if not self.access_token:
            self._refresh_access_token()
        return {"Authorization": f"Bearer {self.access_token}"}

    def search_track(self, artist, title):
        query = f"track:{title} artist:{artist}"
        response = requests.get(
            "https://api.spotify.com/v1/search",
            headers=self._auth_headers(),
            params={"q": query, "type": "track", "limit": 1}
        )

        items = response.json().get("tracks", {}).get("items", [])
        if items:
            return items[0]["id"]
        return None

    def ensure_tracks_exist(self, songs):
        track_ids = []

        for song in songs:
            tid = self.search_track(song["artist"], song["title"])
            if tid:
                track_ids.append(tid)

        return track_ids

    def update_playlist(self, track_ids):
        playlist_id = "YOUR_PLAYLIST_ID"  # Hard-coded or move to env if needed
        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

        response = requests.put(
            url,
            headers=self._auth_headers(),
            json={"uris": [f"spotify:track:{tid}" for tid in track_ids]}
        )

        if response.status_code not in (200, 201):
            logging.error("Failed to update playlist: %s", response.text)
            raise RuntimeError("Playlist update failed")

        logging.info("Playlist updated successfully.")
