import os
import spotify_api


class SpotifyClient:
    """
    Thin wrapper so PlaylistBuilder can use object-style calls.
    """

    def __init__(self, client_id, client_secret, refresh_token, redirect_uri=None):

        os.environ["SPOTIFY_CLIENT_ID"] = client_id
        os.environ["SPOTIFY_CLIENT_SECRET"] = client_secret
        os.environ["SPOTIFY_REFRESH_TOKEN"] = refresh_token

        self.redirect_uri = redirect_uri

    def get_current_user_id(self):
        return spotify_api.get_current_user_id()

    def search_track(self, q, limit=8):
        return spotify_api.search_track(q, limit)

    def create_playlist(self, user_id, name, public=False, description=""):
        return spotify_api.create_playlist(user_id, name, public, description)

    def add_tracks(self, playlist_id, uris):
        return spotify_api.add_tracks_to_playlist(playlist_id, uris)
