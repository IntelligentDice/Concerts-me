import logging
import os
from spotify_client import SpotifyClient
from google_sheets import GoogleSheets
from playlist_builder import PlaylistBuilder

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

def get_env_required(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val

def main():
    # Get all credentials directly from GitHub Secrets (env vars)
    spotify_client_id = get_env_required("SPOTIFY_CLIENT_ID")
    spotify_client_secret = get_env_required("SPOTIFY_CLIENT_SECRET")
    spotify_refresh_token = get_env_required("SPOTIFY_REFRESH_TOKEN")

    google_sa_json = get_env_required("GOOGLE_SERVICE_ACCOUNT_JSON")
    google_sheet_id = get_env_required("GOOGLE_SHEET_ID")  # YOU MUST ADD THIS SECRET

    setlist_key = os.getenv("SETLIST_FM_API_KEY", "")

    raw_dry = os.getenv("DRY_RUN", "")
    dry_run = raw_dry.strip().lower() in ("1", "true", "yes", "y")
    print(f"[DEBUG] DRY_RUN mode = {dry_run} (raw='{raw_dry}')")

    spotify = SpotifyClient(
        client_id=spotify_client_id,
        client_secret=spotify_client_secret,
        refresh_token=spotify_refresh_token,
    )

    sheets = GoogleSheets(
        sheet_id=google_sheet_id,
        service_account_json=google_sa_json
    )

    
    # 🔥 Enable full Setlist.fm debug logging
    pb = PlaylistBuilder(spotify, sheets, setlist_key, debug=True, dry_run=dry_run)

    pb.run()


if __name__ == "__main__":
    main()
