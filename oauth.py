import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json

def run_oauth():
    with open("config.json") as f:
        cfg = json.load(f)

    sp_oauth = SpotifyOAuth(
        client_id=cfg["spotify_client_id"],
        client_secret=cfg["spotify_client_secret"],
        redirect_uri=cfg["redirect_uri"],
        scope="playlist-modify-public playlist-modify-private",
    )

    token_info = sp_oauth.get_access_token(as_dict=True)
    print("\n=== COPY THIS REFRESH TOKEN INTO config.json ===")
    print(token_info["refresh_token"])
    print("===============================================\n")

if __name__ == "__main__":
    run_oauth()
