# spotify_client.py
import os
import spotify_api


class SpotifyClient:
    """
    Thin wrapper so PlaylistBuilder can use object-style calls
    while the actual Spotify API logic lives in spotify_api.py.
    """

    def __init__(self, client_id, client_secret, refresh_token, redirect_uri=None):
        # Set environment variables so spotify_api can read them
        os.environ["SPOTIFY_CLIENT_ID"] = client_id
        os.environ["SPOTIFY_CLIENT_SECRET"] = client_secret
        os.environ["SPOTIFY_REFRESH_TOKEN"] = refresh_token

        self.redirect_uri = redirect_uri

    # ----------------------------
    # User identity
    # ----------------------------
    def get_current_user_id(self):
        return spotify_api.get_current_user_id()

    # ----------------------------
    # Search
    # ----------------------------
    def search_track(self, q, limit=8):
        return spotify_api.search_track(q, limit)

    # ----------------------------
    # Playlist creation
    # ----------------------------
    def create_playlist(self, user_id, name, public=False, description=""):
        return spotify_api.create_playlist(
            user_id,
            name,
            public,
            description
        )

    # ----------------------------
    # Adding tracks
    # ----------------------------
    def add_tracks(self, playlist_id, uris):
        return spotify_api.add_tracks_to_playlist(playlist_id, uris)

    def add_tracks_to_playlist(self, playlist_id, uris):
        return spotify_api.add_tracks_to_playlist(playlist_id, uris)

    # ----------------------------
    # Dedup prevention helper
    # ----------------------------
    def find_playlist_by_name(self, name: str):
        """
        Returns playlist_id or None.
        """
        return spotify_api.find_playlist_by_name(name)
