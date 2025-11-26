# spotify_api.py
import logging
from spotify_client import SpotifyClient


class SpotifyAPI:
    def __init__(self, client_id, client_secret, refresh_token, redirect_uri):
        self.client = SpotifyClient(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            redirect_uri=redirect_uri,
        )

    def search_track(self, artist, title):
        """Return a track ID or None."""
        return self.client.search_track(artist, title)

    def add_tracks_to_playlist(self, playlist_id, track_ids):
        """
        Replace the playlist contents with the provided track IDs.
        Track IDs should NOT include the 'spotify:track:' prefix.
        """
        if not track_ids:
            logging.warning("No track IDs supplied — playlist update skipped.")
            return

        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

        response = self.client._request(
            method="PUT",
            url=url,
            headers=self.client._auth_headers(),
            json={"uris": [f"spotify:track:{tid}" for tid in track_ids]},
        )

        if response.status_code not in (200, 201):
            raise RuntimeError(
                f"Failed to update playlist {playlist_id}: {response.text}"
            )

        logging.info("Playlist updated successfully.")

    def ensure_tracks_exist(self, songs):
        """
        Given a list of dicts:
        { "artist": "...", "title": "..." }
        return a list of valid Spotify track IDs.
        """
        return self.client.ensure_tracks_exist(songs)
