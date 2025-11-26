import json
import spotipy
from spotipy.oauth2 import SpotifyOAuth

def get_spotify_client():
    with open("config.json") as f:
        cfg = json.load(f)

    oauth = SpotifyOAuth(
        client_id=cfg["spotify_client_id"],
        client_secret=cfg["spotify_client_secret"],
        redirect_uri=cfg["redirect_uri"],
        scope="playlist-modify-private playlist-modify-public",
    )

    token = oauth.refresh_access_token(cfg["spotify_refresh_token"])
    sp = spotipy.Spotify(auth=token["access_token"])
    return sp

def create_playlist(sp, name, description=""):
    user = sp.current_user()["id"]
    playlist = sp.user_playlist_create(user, name, public=False, description=description)
    return playlist["id"]

def add_tracks(sp, playlist_id, track_ids):
    if track_ids:
        sp.playlist_add_items(playlist_id, track_ids)
