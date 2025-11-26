"""
playlist_builder.py

Clean, fixed version. Works with updated Setlist.fm API and Spotify API.
"""

from spotify_api import (
    get_current_user_id,
    search_track,
    create_playlist,
    add_tracks_to_playlist
)
from setlistfm_api import find_event_setlist
from fuzzywuzzy import fuzz
from utils.logging_utils import log, warn


def _best_spotify_match_for_song(song_title: str, artist_hint: str):
    """
    Try multiple queries and use fuzzy matching to pick the best Spotify track.
    """
    queries = [
        f"{song_title} {artist_hint}",
        f"{song_title}"
    ]

    best_uri = None
    best_score = -1

    for q in queries:
        results = search_track(q, limit=8)
        for item in results:
            uri = item.get("uri")
            name = item.get("name", "")
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else ""

            score = (
                fuzz.token_set_ratio(song_title, name)
                + fuzz.token_set_ratio(artist_hint, artist_name)
            ) / 2

            if score > best_score:
                best_score = score
                best_uri = uri

    return best_uri, best_score


class PlaylistBuilder:
    """
    A proper class wrapper around the playlist-building logic.
    """

    def __init__(self, spotify_client, sheets_client, setlist_api_key):
        self.spotify = spotify_client
        self.sheets = sheets_client
        self.setlist_api_key = setlist_api_key

    def _build_playlist_for_event(self, artist, date, songs):
        log(f"Building playlist for {artist} - {date}")

        user_id = self.spotify.get_current_user_id()

        playlist_name = f"{artist} - {date}"
        playlist_id = create_playlist(
            user_id,
            playlist_name,
            public=False,
            description="Auto-generated concert playlist"
        )

        track_uris = []
        for title, artist_hint in songs:
            uri, score = _best_spotify_match_for_song(title, artist_hint)
            if uri:
                track_uris.append(uri)
            else:
                warn(f"Could not match: {title} (artist hint: {artist_hint})")

        if track_uris:
            add_tracks_to_playlist(playlist_id, track_uris)
            log(f"Created playlist {playlist_name} with {len(track_uris)} tracks")
            return playlist_id

        warn("No tracks added to playlist")
        return None

    def run(self):
        """
        Main entrypoint called by main.py
        """
        events = self.sheets.read_events()

        for index, event in enumerate(events):
            artist = event.get("artist")
            date = event.get("date")

            if not artist or not date:
                warn(f"Skipping row {index}: missing artist/date")
                continue

            # Query Setlist.fm ― returns list of {"title": ...}
            songs = find_event_setlist(artist, date, self.setlist_api_key)

            if not songs:
                warn(f"No setlist found for {artist} {date}")
                continue

            # Normalize to (song_title, artist_hint)
            songs = [(song["title"], artist) for song in songs]

            playlist_id = self._build_playlist_for_event(artist, date, songs)
            if playlist_id:
                playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"
                self.sheets.write_playlist_link(index, playlist_url)
