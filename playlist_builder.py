# playlist_builder.py
from typing import List, Optional
from setlistfm_api import SetlistFM
from utils.logging_utils import log, warn
from fuzzywuzzy import fuzz
from spotify_client import SpotifyClient

# NOTE:
# PlaylistBuilder expects a SpotifyClient instance (thin wrapper above).
# This file intentionally uses spotify_client methods (self.spotify.search_track, etc.)
# — avoids importing repo-specific spotify_api functions directly.

def _spotify_top_tracks_fallback_via_client(spotify: SpotifyClient, artist_name: str, limit: int = 5):
    """
    Use the provided Spotify client to find popular tracks for an artist.
    Caches should be handled by PlaylistBuilder instance to reduce repeated calls.
    Returns list of URIs.
    """
    # SpotifyClient.search_track returns list-of-track-dicts
    q = f"artist:{artist_name}"
    results = spotify.search_track(q, limit=40) or []
    if not results:
        results = spotify.search_track(artist_name, limit=40) or []
    items = sorted(results, key=lambda it: it.get("popularity", 0), reverse=True)
    uris = [it.get("uri") for it in items if it.get("uri")]
    return uris[:limit]


def _best_spotify_match_for_song_via_client(spotify: SpotifyClient, song_title: str, artist_hint: str, fuzzy_threshold: float = 30.0):
    """
    Use the spotify client to search and fuzzy-match best track.
    Returns (uri, score) or (None, 0).
    """
    # try combined query first
    queries = [f"{song_title} {artist_hint}", f"{song_title}", f"{artist_hint} {song_title}"]

    best_uri = None
    best_score = -1

    for q in queries:
        try:
            results = spotify.search_track(q, limit=12) or []
        except Exception:
            results = []

        for item in results:
            uri = item.get("uri")
            name = item.get("name", "") or ""
            artists = ", ".join(a.get("name", "") for a in item.get("artists", []) or [])
            score = (fuzz.token_set_ratio(song_title, name) + fuzz.token_set_ratio(artist_hint or "", artists)) / 2
            if score > best_score:
                best_score = score
                best_uri = uri

        if best_uri and best_score >= fuzzy_threshold:
            break

    # broader fallback
    if not best_uri:
        try:
            results = spotify.search_track(song_title, limit=8) or []
        except Exception:
            results = []
        for item in results:
            uri = item.get("uri")
            name = item.get("name", "") or ""
            artists = ", ".join(a.get("name", "") for a in item.get("artists", []) or [])
            score = (fuzz.token_set_ratio(song_title, name) + fuzz.token_set_ratio(artist_hint or "", artists)) / 2
            if score > best_score:
                best_score = score
                best_uri = uri

    return best_uri, best_score


class PlaylistBuilder:
    def __init__(self, spotify_client: SpotifyClient, sheets_client, setlist_api_key: str,
                 debug: bool = False, dry_run: bool = False):
        self.spotify: SpotifyClient = spotify_client
        self.sheets = sheets_client
        self.setlist = SetlistFM(setlist_api_key, verbose=debug)
        self.debug = debug
        self.dry_run = dry_run

        # simple in-memory caches for this run
        self._track_cache = {}         # (song_title, artist_hint) -> (uri, score)
        self._artist_top_cache = {}    # artist_name -> [uris]

    def _log(self, *a):
        if self.debug:
            print(*a)
        else:
            log(*a)

    def _parse_time_or_none(self, t: Optional[str]):
        if not t:
            return None
        try:
            parts = t.split(":")
            parts = [int(p) for p in parts]
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts[:3])
        except Exception:
            return None

    def _build_playlist_for_event(self, user_id: str, playlist_name: str,
                                  track_uris: List[str], description: str = "Auto-generated concert playlist"):
        if self.dry_run:
            log(f"[DRY-RUN] Would create playlist '{playlist_name}' with {len(track_uris)} tracks")
            for u in track_uris:
                log(f"[DRY-RUN]   Track -> {u}")
            return "dry-run-playlist-id"

        log(f"Creating playlist {playlist_name} with {len(track_uris)} tracks")
        pid = self.spotify.create_playlist(user_id, playlist_name, public=False, description=description)
        if not pid:
            raise RuntimeError("Failed to create playlist")

        # Add tracks in chunks via spotify_client
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
            event_name = ev.get("event_name") or ev.get("eventName") or ev.get("event")

            if not artist or not date:
                warn(f"Skipping row {idx}: missing artist/date")
                continue

            self._log(f"[INFO] Looking up setlist for {artist} on {date} @ {venue}, {city}")

            # Use SetlistFM to fetch event-level info
            event_data = self.setlist.find_event_setlist(artist=artist, venue=venue, city=city, date=date)
            if not event_data:
                warn(f"[WARN] No matching setlist found for {artist} on {date} @ {venue}, {city}")
                continue

            # if sheet specifies is_festival truthy, force festival handling
            sheet_is_festival = str(ev.get("is_festival", "")).strip().lower() in ("true", "yes", "1")

            # ----------------------------
            # FESTIVAL MODE (either reported by setlist or flagged in sheet)
            # ----------------------------
            if event_data.get("is_festival") or sheet_is_festival:
                # event_data["lineup"] expected
                lineup = event_data.get("lineup", []) or []
                festival_name = event_data.get("festival_name") or artist
                festival_day_label = event_name or f"{festival_name} Day {date}"
                self._log(f"[INFO] Festival mode: {festival_day_label} with {len(lineup)} artists")

                # Build band entries (normalize using setlist-provided fields)
                band_entries = []
                for b in lineup:
                    name = b.get("name") or ""
                    songs = b.get("songs") or []
                    start_time = b.get("startTime") or b.get("_raw", {}).get("startTime")
                    last_updated = b.get("lastUpdated") or b.get("_raw", {}).get("lastUpdated")
                    band_entries.append({"name": name, "songs": songs, "startTime": start_time, "lastUpdated": last_updated})

                # sort by startTime -> lastUpdated -> name (headliner ordering not used here)
                def _sort_band(e):
                    st = self._parse_time_or_none(e.get("startTime"))
                    lu = e.get("lastUpdated")
                    nm = (e.get("name") or "").lower()
                    st_key = st if st else (99, 99, 99)
                    lu_key = lu if lu else "9999-99-99T99:99:99Z"
                    return (st_key, lu_key, nm)

                band_entries = sorted(band_entries, key=_sort_band)

                # Build title/artist pairs (openers / acts in order). If act missing songs -> top-tracks fallback
                pairs = []
                for b in band_entries:
                    name = b["name"]
                    songs = b.get("songs", []) or []
                    if songs:
                        self._log(f"[INFO] Festival act {name} has {len(songs)} songs")
                        for s in songs:
                            pairs.append((s, name))
                    else:
                        # fallback: use cached artist top tracks or fetch
                        self._log(f"[INFO] Festival act {name} missing setlist -> using spotify fallback")
                        cached = self._artist_top_cache.get(name)
                        if cached is None:
                            cached = _spotify_top_tracks_fallback_via_client(self.spotify, name, limit=5)
                            self._artist_top_cache[name] = cached
                        for u in cached:
                            pairs.append((u, None))

                # Resolve pairs -> URIs (use track cache)
                track_uris = []
                seen = set()
                for title_or_uri, artist_hint in pairs:
                    if artist_hint is None and isinstance(title_or_uri, str) and title_or_uri.startswith("spotify:"):
                        if title_or_uri not in seen:
                            track_uris.append(title_or_uri)
                            seen.add(title_or_uri)
                        continue

                    key = (title_or_uri, artist_hint or "")
                    cached = self._track_cache.get(key)
                    if cached:
                        uri, score = cached
                    else:
                        uri, score = _best_spotify_match_for_song_via_client(self.spotify, title_or_uri, artist_hint or "")
                        self._track_cache[key] = (uri, score)
                    if uri and uri not in seen:
                        track_uris.append(uri)
                        seen.add(uri)
                        self._log(f"[DEBUG] Matched '{title_or_uri}' -> {uri} (score={score})")
                    else:
                        self._log(f"[WARN] Could not match '{title_or_uri}' ({artist_hint})")

                if not track_uris:
                    warn(f"[WARN] No tracks resolved for festival day {festival_day_label}")
                    continue

                # Playlist name (Option A): festival day name - date
                playlist_name = f"{festival_day_label} - {date}"

                # short description (C chosen earlier) — keep concise
                venue_str = venue or event_data.get("venue") or "Unknown venue"
                city_str = city or event_data.get("city") or "Unknown city"
                description = f"Recorded live at {venue_str}, {city_str} on {date}. Festival day: {festival_day_label}."

                try:
                    user_id = self.spotify.get_current_user_id() or "me"
                except Exception:
                    user_id = "me"

                pid = self._build_playlist_for_event(user_id, playlist_name, track_uris, description=description)
                if self.dry_run:
                    log(f"[DRY-RUN] Playlist NOT created: {playlist_name}")
                else:
                    log(f"Playlist created: {playlist_name} (id={pid})")
                # skip normal flow for this row
                continue

            # ----------------------------
            # NORMAL EVENT MODE
            # ----------------------------
            headliner = event_data.get("headliner")
            headliner_songs = event_data.get("headliner_songs", []) or []
            openers = event_data.get("openers", []) or []

            # sort openers by time/lastUpdated/name
            def _sort_key_for_opener(op):
                st = self._parse_time_or_none(op.get("startTime"))
                lu = op.get("lastUpdated")
                nm = (op.get("name") or "").lower()
                st_key = st if st else (99, 99, 99)
                lu_key = lu if lu else "9999-99-99T99:99:99Z"
                return (st_key, lu_key, nm)

            openers = sorted(openers, key=_sort_key_for_opener)

            # Build ordered pairs: openers first then headliner
            pairs = []
            for op in openers:
                name = op.get("name")
                songs = op.get("songs", []) or []
                if songs:
                    self._log(f"[INFO] Opener {name} has {len(songs)} songs")
                    for s in songs:
                        pairs.append((s, name))
                else:
                    self._log(f"[INFO] Opener {name} has no songs in setlist; using spotify fallback")
                    cached = self._artist_top_cache.get(name)
                    if cached is None:
                        cached = _spotify_top_tracks_fallback_via_client(self.spotify, name, limit=5)
                        self._artist_top_cache[name] = cached
                    for u in cached:
                        pairs.append((u, None))

            # Add headliner songs (in order)
            if headliner_songs:
                self._log(f"[INFO] Headliner {headliner} songs count: {len(headliner_songs)}")
                for s in headliner_songs:
                    pairs.append((s, headliner))
            else:
                warn(f"[WARN] No headliner songs found for {headliner} — skipping")
                continue

            # Resolve pairs -> URIs (cached)
            track_uris = []
            seen = set()
            for title_or_uri, artist_hint in pairs:
                if artist_hint is None and isinstance(title_or_uri, str) and title_or_uri.startswith("spotify:"):
                    if title_or_uri not in seen:
                        track_uris.append(title_or_uri)
                        seen.add(title_or_uri)
                    continue

                key = (title_or_uri, artist_hint or headliner or "")
                cached = self._track_cache.get(key)
                if cached:
                    uri, score = cached
                else:
                    uri, score = _best_spotify_match_for_song_via_client(self.spotify, title_or_uri, artist_hint or headliner or "")
                    self._track_cache[key] = (uri, score)

                if uri and uri not in seen:
                    track_uris.append(uri)
                    seen.add(uri)
                    self._log(f"[DEBUG] Matched '{title_or_uri}' -> {uri} (score={score})")
                else:
                    self._log(f"[WARN] Could not match '{title_or_uri}' ({artist_hint})")

            if not track_uris:
                warn(f"No tracks resolved for {artist} on {date}")
                continue

            # Build smart short playlist description (Option C short form)
            date_str = date
            venue_str = venue or event_data.get("venue") or "Unknown venue"
            city_str = city or event_data.get("city") or "Unknown city"

            if openers:
                if len(openers) == 1:
                    opener_part = f"with opener {openers[0]['name']}"
                else:
                    opener_names = ", ".join(op['name'] for op in openers)
                    opener_part = f"with openers {opener_names}"
            else:
                opener_part = ""

            if opener_part:
                description = (
                    f"Live setlist from {headliner} {opener_part} — recorded at "
                    f"{venue_str}, {city_str} on {date_str}."
                )
            else:
                description = (
                    f"Live setlist from {headliner} — recorded at "
                    f"{venue_str}, {city_str} on {date_str}."
                )

            # Playlist name (Option A): headliner - date
            playlist_name = f"{artist} - {date}"

            # Prevent duplicate playlists (best-effort)
            existing = None
            try:
                if hasattr(self.spotify, "find_playlist_by_name"):
                    existing = self.spotify.find_playlist_by_name(playlist_name)
            except Exception:
                existing = None

            if existing:
                if self.dry_run:
                    log(f"[DRY-RUN] Playlist already exists: {playlist_name} (id={existing}) — skipping creation")
                else:
                    log(f"[INFO] Playlist already exists: {playlist_name} (id={existing}) — skipping creation")
                continue

            try:
                user_id = self.spotify.get_current_user_id() or "me"
            except Exception:
                user_id = "me"

            pid = self._build_playlist_for_event(user_id, playlist_name, track_uris, description=description)

            if self.dry_run:
                log(f"[DRY-RUN] Playlist NOT created: {playlist_name}")
            else:
                log(f"Playlist created: {playlist_name} (id={pid})")
