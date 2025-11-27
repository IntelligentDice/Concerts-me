# playlist_builder.py
from setlistfm_api import SetlistFM
from fuzzywuzzy import fuzz
from utils.logging_utils import log, warn
from spotify_api import search_track, get_artist_top_tracks, get_album_tracks, best_match_track  # best_match_track may not exist - we will fall back to local logic
from typing import List


def _spotify_top_tracks_fallback(artist_name: str, limit: int = 5):
    # search for popular tracks for the artist and return URIs
    q = f"artist:{artist_name}"
    results = search_track(q, limit=40)
    if not results:
        results = search_track(artist_name, limit=40)
    items = sorted(results, key=lambda it: it.get("popularity", 0), reverse=True)
    uris = [it.get("uri") for it in items if it.get("uri")]
    return uris[:limit]


def _best_spotify_match_for_song(song_title: str, artist_hint: str):
    # use search_track and fuzzy matching
    try:
        # prefer a combined query
        q = f"{song_title} {artist_hint}"
        results = search_track(q, limit=12)
    except Exception:
        results = []

    best_uri = None
    best_score = -1
    for item in results:
        uri = item.get("uri")
        name = item.get("name", "")
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        score = (fuzz.token_set_ratio(song_title, name) + fuzz.token_set_ratio(artist_hint or "", artists)) / 2
        if score > best_score:
            best_score = score
            best_uri = uri

    # fallback broader search if nothing found
    if not best_uri:
        results = search_track(song_title, limit=8)
        for item in results:
            uri = item.get("uri")
            name = item.get("name", "")
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            score = (fuzz.token_set_ratio(song_title, name) + fuzz.token_set_ratio(artist_hint or "", artists)) / 2
            if score > best_score:
                best_score = score
                best_uri = uri

    return best_uri, best_score


class PlaylistBuilder:
    def __init__(self, spotify_client, sheets_client, setlist_api_key: str, debug: bool = False):
        self.spotify = spotify_client
        self.sheets = sheets_client
        self.setlist = SetlistFM(setlist_api_key, verbose=debug)
        self.debug = debug

    def _log(self, *a):
        if self.debug:
            print(*a)
        else:
            log(*a)

    def _build_playlist_for_event(self, user_id: str, playlist_name: str, track_uris: List[str]):
        log(f"Creating playlist {playlist_name} with {len(track_uris)} tracks")
        pid = self.spotify.create_playlist(user_id, playlist_name, public=False, description="Auto-generated concert playlist")
        if not pid:
            raise RuntimeError("Failed to create playlist")
        ok = self.spotify.add_tracks_to_playlist(pid, track_uris)
        if not ok:
            warn("Failed to add tracks to playlist")
        return pid

    def run(self):
        events = self.sheets.read_events()
        for idx, ev in enumerate(events):
            artist = ev.get("artist")
            date = ev.get("date")
            venue = ev.get("venue")
            city = ev.get("city")

            if not artist or not date:
                warn(f"Skipping row {idx}: missing artist/date")
                continue

            self._log(f"[INFO] Looking up setlist for {artist} on {date} @ {venue}, {city}")
            event_data = self.setlist.find_event_setlist(artist=artist, venue=venue, city=city, date=date)
            if not event_data:
                warn(f"[WARN] No matching setlist found for {artist} on {date} @ {venue}, {city}")
                continue

            headliner = event_data["headliner"]
            headliner_songs = event_data["headliner_songs"] or []
            openers = event_data.get("openers", []) or []

            # Build ordered pairs: openers first, then headliner
            pairs = []
            for op in openers:
                name = op.get("name")
                songs = op.get("songs", []) or []
                if songs:
                    self._log(f"[INFO] Opener {name} has {len(songs)} songs")
                    for s in songs:
                        pairs.append((s, name))
                else:
                    # fallback to spotify top tracks
                    self._log(f"[INFO] Opener {name} has no songs in setlist; using spotify fallback")
                    uris = _spotify_top_tracks_fallback(name, limit=5)
                    for u in uris:
                        pairs.append((u, None))  # URI pass-through

            # Add headliner songs
            if headliner_songs:
                self._log(f"[INFO] Headliner {headliner} songs count: {len(headliner_songs)}")
                for s in headliner_songs:
                    pairs.append((s, headliner))
            else:
                warn(f"[WARN] No headliner songs found for {headliner} — skipping")
                continue

            # Resolve to URIs
            track_uris = []
            seen = set()
            for title_or_uri, artist_hint in pairs:
                if artist_hint is None and isinstance(title_or_uri, str) and title_or_uri.startswith("spotify:"):
                    if title_or_uri not in seen:
                        track_uris.append(title_or_uri)
                        seen.add(title_or_uri)
                    continue

                uri, score = _best_spotify_match_for_song(title_or_uri, artist_hint or headliner)
                if uri and uri not in seen:
                    track_uris.append(uri)
                    seen.add(uri)
                    self._log(f"[DEBUG] Matched '{title_or_uri}' -> {uri} (score={score})")
                else:
                    self._log(f"[WARN] Could not match '{title_or_uri}' ({artist_hint})")

            if not track_uris:
                warn(f"No tracks resolved for {artist} on {date}")
                continue

            # create and populate playlist
            try:
                user_id = self.spotify.get_current_user_id() or "me"
            except Exception:
                user_id = "me"

            playlist_name = f"{artist} - {date}"
            pid = self._build_playlist_for_event(user_id, playlist_name, track_uris)
            log(f"Playlist created: {playlist_name} (id={pid})")
