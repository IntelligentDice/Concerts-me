# playlist_builder.py
from spotify_api import search_track
from spotify_api import get_artist_top_tracks  # may return [] if artist_id unknown; we fallback below
from spotify_api import get_album_tracks
from spotify_api import best_match_track
from setlistfm_api import SetlistFM
from fuzzywuzzy import fuzz
from utils.logging_utils import log, warn


def _spotify_top_tracks_fallback(artist_name: str, limit: int = 5):
    """
    Fallback: search tracks for the artist name and return top N URIs (by Spotify popularity).
    """
    # Search broadly for tracks matching the artist (many tracks will include artist in artist list)
    q = f"artist:{artist_name}"
    results = search_track(q, limit=40)
    if not results:
        # try a general search
        results = search_track(artist_name, limit=40)
    # sort by popularity if present
    items = sorted(results, key=lambda it: it.get("popularity", 0), reverse=True)
    uris = []
    for it in items[:limit]:
        uri = it.get("uri")
        if uri:
            uris.append(uri)
    return uris


def _best_spotify_match_for_song(song_title: str, artist_hint: str):
    """
    Use best_match_track from spotify_api if present, else fallback to local search logic.
    Returns (uri, score)
    """
    try:
        res = best_match_track(song_title, artist_hint)
        if res:
            uri, score = res
            return uri, score
    except Exception:
        pass

    # fallback simple approach
    queries = [f"{song_title} {artist_hint}", f"{song_title}"]
    best_uri = None
    best_score = -1
    for q in queries:
        results = search_track(q, limit=8)
        for item in results:
            uri = item.get("uri")
            name = item.get("name", "")
            artists = item.get("artists", [])
            artist_name = artists[0]["name"] if artists else ""
            # use fuzzy token set ratio
            score = (fuzz.token_set_ratio(song_title, name) + fuzz.token_set_ratio(artist_hint, artist_name)) / 2
            if score > best_score:
                best_score = score
                best_uri = uri
    return best_uri, best_score


class PlaylistBuilder:
    """
    PlaylistBuilder using Setlist.fm Approach A (date -> filter by venue/city -> collect artists).
    Creates one Spotify playlist per event (openers first, headliner last).
    """

    def __init__(self, spotify_client, sheets_client, setlist_api_key, debug: bool = False):
        self.spotify = spotify_client
        self.sheets = sheets_client
        self.setlist = SetlistFM(setlist_api_key, verbose=debug)
        self.debug = debug

    def _log(self, *a):
        if self.debug:
            print(*a)
        else:
            log(*a)

    def _build_playlist_for_event(self, user_id, playlist_name, track_uris):
        log(f"Creating playlist {playlist_name} with {len(track_uris)} tracks")
        pid = self.spotify.create_playlist(user_id, playlist_name, public=False, description="Auto-generated concert playlist")
        if track_uris:
            # spotify client method add_tracks_to_playlist may accept chunks; assume it exists
            self.spotify.add_tracks_to_playlist(pid, track_uris)
        return pid

    def run(self):
        events = self.sheets.read_events()

        for idx, event in enumerate(events):
            artist = event.get("artist")
            date = event.get("date")
            venue = event.get("venue", None)
            city = event.get("city", None)

            if not artist or not date:
                warn(f"Skipping row {idx}: missing artist/date")
                continue

            self._log(f"[INFO] Looking up setlist for {artist} on {date} @ {venue}, {city}")

            event_data = self.setlist.find_event_setlist(artist=artist, venue=venue, city=city, date=date)
            if not event_data:
                warn(f"No matching setlist found for {artist} on {date}")
                continue

            headliner = event_data["headliner"]
            headliner_songs = event_data["headliner_songs"]
            openers = event_data["openers"]

            # Build ordered track list: openers first, then headliner
            song_pairs = []  # (song_title, artist_hint)
            if openers:
                for op in openers:
                    op_name = op.get("name")
                    op_songs = op.get("songs", []) or []
                    if op_songs:
                        self._log(f"[INFO] Adding opener {op_name} ({len(op_songs)} songs from setlist)")
                        for s in op_songs:
                            song_pairs.append((s, op_name))
                    else:
                        self._log(f"[INFO] Opener {op_name} has no setlist songs — will use Spotify fallback")
                        # fallback: top spotify tracks for that opener
                        fallback_uris = _spotify_top_tracks_fallback(op_name, limit=5)
                        if fallback_uris:
                            # convert URIs to pseudo-song pairs so they are later added directly
                            # we treat them specially by putting (uri, None) where uri starts with "spotify:"
                            for u in fallback_uris:
                                song_pairs.append((u, None))  # URI direct
                        else:
                            self._log(f"[WARN] No fallback tracks found for opener {op_name}")
            else:
                self._log("[INFO] No openers detected for this event")

            # add headliner songs
            if headliner_songs:
                self._log(f"[INFO] Adding headliner {headliner} ({len(headliner_songs)} songs)")
                for s in headliner_songs:
                    song_pairs.append((s, headliner))
            else:
                self._log(f"[WARN] No headliner songs found for {headliner} — skipping event")
                continue

            # Resolve song_pairs into spotify URIs
            track_uris = []
            seen = set()
            for title_or_uri, artist_hint in song_pairs:
                # if artist_hint is None and title_or_uri looks like a spotify URI, add directly
                if artist_hint is None and isinstance(title_or_uri, str) and title_or_uri.startswith("spotify:"):
                    if title_or_uri not in seen:
                        track_uris.append(title_or_uri)
                        seen.add(title_or_uri)
                    continue

                # otherwise it's a title -> search
                uri, score = _best_spotify_match_for_song(title_or_uri, artist_hint or headliner)
                if uri:
                    if uri not in seen:
                        track_uris.append(uri)
                        seen.add(uri)
                        self._log(f"[DEBUG] Matched '{title_or_uri}' -> {uri} (score={score})")
                else:
                    self._log(f"[WARN] Could not match '{title_or_uri}' ({artist_hint}) on Spotify")

            if not track_uris:
                warn(f"No tracks resolved for {artist} on {date}")
                continue

            # create playlist
            user_id = None
            try:
                user_id = self.spotify.get_current_user_id()
            except Exception:
                user_id = "me"
            playlist_name = f"{artist} - {date}"
            pid = self._build_playlist_for_event(user_id, playlist_name, track_uris)
            log(f"Playlist created: {playlist_name} (id={pid})")
