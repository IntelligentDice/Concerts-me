# main.py
import os
from spotify_client import SpotifyClient
from google_sheets import GoogleSheets
from playlist_builder import PlaylistBuilder

def get_env(name, default=None, required=False):
    val = os.getenv(name, default)
    if required and val is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val

def load_config():
    return {
        "spotify": {
            "client_id": get_env("SPOTIFY_CLIENT_ID", required=True),
            "client_secret": get_env("SPOTIFY_CLIENT_SECRET", required=True),
            "refresh_token": get_env("SPOTIFY_REFRESH_TOKEN", required=True),
            "redirect_uri": get_env("SPOTIFY_REDIRECT_URI", "http://localhost:8080/callback"),
        },
        "google": {
            "sheet_id": get_env("GOOGLE_SHEET_ID"),
            "service_account_json": get_env("GOOGLE_SERVICE_ACCOUNT_JSON"),
        },
        "setlistfm": {
            "api_key": get_env("SETLIST_FM_API_KEY", required=True)
        }
    }

def main():
    config = load_config()

    spotify = SpotifyClient(
        client_id=config["spotify"]["client_id"],
        client_secret=config["spotify"]["client_secret"],
        refresh_token=config["spotify"]["refresh_token"],
        redirect_uri=config["spotify"]["redirect_uri"]
    )

    gs = GoogleSheets(
        service_account_json=config["google"]["service_account_json"],
        sheet_id=config["google"]["sheet_id"]
    )

    pb = PlaylistBuilder(spotify, gs, config["setlistfm"]["api_key"])
    pb.run()

if __name__ == "__main__":
    main()
