# main.py
import logging
from config import load_config
from spotify_client import SpotifyClient
from setlistfm_client import SetlistFMClient
from google_sheets import GoogleSheets

logging.basicConfig(level=logging.INFO)

def main():
    cfg = load_config()

    spotify = SpotifyClient(
        client_id=cfg["spotify"]["client_id"],
        client_secret=cfg["spotify"]["client_secret"],
        refresh_token=cfg["spotify"]["refresh_token"],
        redirect_uri=cfg["spotify"]["redirect_uri"]
    )

    setlistfm = SetlistFMClient(api_key=cfg["setlistfm"]["api_key"])

    sheets = GoogleSheets(
        sheet_id=cfg["google"]["sheet_id"],
        service_account_json=cfg["google"]["service_account_json"]
    )

    logging.info("Fetching artist list from Google Sheets…")
    artists = sheets.get_artists()

    logging.info("Collecting songs from Setlist.fm…")
    songs = setlistfm.get_songs_from_artists(artists)

    logging.info("Ensuring tracks exist on Spotify…")
    track_ids = spotify.ensure_tracks_exist(songs)

    logging.info("Updating Spotify playlist…")
    spotify.update_playlist(track_ids)

    logging.info("Process completed successfully.")

if __name__ == "__main__":
    main()
