# playlist_builder.py

from spotify_api import (
    search_track,
    create_playlist,
    add_tracks_to_playlist
)
from setlistfm_api import SetlistFM
from fuzzywuzzy import fuzz
from utils.logging_utils import log, warn


def _best_spotify_match(song_title: str, artist_hint: str):
    """
    Fuzzy match a track on Spotify using title + artist hint.
    Returns (uri, score)
    """
    queries = [
        f"{song_title} {artist_hint}",
        f"{song_title}",
    ]

    best_uri = None
    best_score = -1

    for q in queries:
        results = search_track(q, limit=10)
        for item in results:
            uri = item.get("uri")
            name = item.get("name", "")
            artists = item.get("artists", [])
            primary_artist = artists[0]["name"] if artists else ""

            score = (
                fuzz.token_set_ratio(song_title, name)
                + fuzz.token_set_ratio(artist_hint, primary_artist)
            ) / 2

            if score > best_score:
                best_score = score
                best_uri = uri

    return best_uri, best_score


class PlaylistBuilder:
    """
    Final simplified Playlist Builder:
    - Reads events from sheet
    - Fetches openers + headliner from Setlist.fm
    - Builds playlist: openers first, headliner last
    - Writes nothing back to sheets
    """

    def __init__(self, spotify_client, sheets_client, setlist_api_key):
        self.spotify = spotify_client
        self.sheets = sheets_client
        self.setlist = SetlistFM(setlist_api_key)

    def run(self):
        events = self.sheets.read_events()

        for idx, event in enumerate(events):
            artist = event.get("artist")
            date = event.get("date")

            if not artist or not date:
                warn(f"Skipping row {idx}: missing artist/date")
                continue

            log(f"Looking up setlist for {artist} on {date}...")

            event_data = self.setlist.find_event_setlist(artist, date)
            if not event_data:
                warn(f"No matching setlist found for {artist} on {date}")
                continue

            headliner = event_data["headliner"]
            headliner_songs = event_data["headliner_songs"]
            openers = event_data["openers"]

            # Build final ordered list: openers → headliner
            full_song_list = []

            for o in openers:
                if o["songs"]:
                    log(f"Adding opener {o['name']} ({len(o['songs'])} songs)")
                    for s in o["songs"]:
                        full_song_list.append((s, o["name"]))
                else:
                    warn(f"Opener {o['name']} has no setlist songs listed")

            # Headliner last
            log(f"Adding headliner {headliner} ({len(headliner_songs)} songs)")
            for s in headliner_songs:
                full_song_list.append((s, headliner))

            # Match all songs to Spotify
            track_uris = []
            for title, artist_hint in full_song_list:
                uri, score = _best_spotify_match(title, artist_hint)
                if uri:
                    track_uris.append(uri)
                else:
                    warn(f"Could not match: {title} ({artist_hint})")

            if not track_uris:
                warn(f"No tracks found for event {artist} {date}, skipping.")
                continue

            # Create playlist
            playlist_name = f"{artist} - {date}"
            playlist_id = create_playlist(
                self.spotify.get_current_user_id(),
                playlist_name,
                public=False,
                description="Auto-generated concert playlist"
            )

            add_tracks_to_playlist(playlist_id, track_uris)
            log(f"Playlist created: {playlist_name} ({len(track_uris)} tracks)")
