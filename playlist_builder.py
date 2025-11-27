# playlist_builder.py
from setlistfm_api import SetlistFM
from fuzzywuzzy import fuzz
from utils.logging_utils import log, warn
from spotify_api import search_track  # per-song lookups
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
    def __init__(self, spotify_client, sheets_client, setlist_api_key: str, debug: bool = False, dry_run: bool = False):
        self.spotify = spotify_client
        self.sheets = sheets_client
        self.setlist = SetlistFM(setlist_api_key, verbose=debug)
        self.debug = debug
        self.dry_run = dry_run

    def _log(self, *a):
        if self.debug:
            print(*a)
        else:
            log(*a)

    def _build_playlist_for_event(self, user_id: str, playlist_name: str,
                                  track_uris: List[str], description: str = "Auto-generated concert playlist"):
        # Dry-run reports, no Spotify changes
        if self.dry_run:
            log(f"[DRY-RUN] Would create playlist '{playlist_name}' with {len(track_uris)} tracks")
            for u in track_uris:
                log(f"[DRY-RUN]   Track -> {u}")
            return "dry-run-playlist-id"

        log(f"Creating playlist {playlist_name} with {len(track_uris)} tracks")

        pid = self.spotify.create_playlist(
            user_id,
            playlist_name,
            public=False,
            description=description
        )

        if not pid:
            raise RuntimeError("Failed to create playlist")

        # spotify_client historically has add_tracks or add_tracks_to_playlist depending on version.
        if hasattr(self.spotify, "add_tracks"):
            ok = self.spotify.add_tracks(pid, track_uris)
        elif hasattr(self.spotify, "add_tracks_to_playlist"):
            ok = self.spotify.add_tracks_to_playlist(pid, track_uris)
        else:
            # last resort: ask spotify client to expose raw api or raise
            raise RuntimeError("Spotify client does not implement add_tracks or add_tracks_to_playlist")

        if not ok:
            warn("Failed to add tracks to playlist")

        return pid

    # helper: parse HH:MM or HH:MM:ss into tuple sortable key
    @staticmethod
    def _parse_time_or_none(t):
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

    def run(self):
        events = self.sheets.read_events()
        for idx, ev in enumerate(events):
            artist = ev.get("artist")
            date = ev.get("date")
            venue = ev.get("venue")
            city = ev.get("city")
            event_name = ev.get("event_name") or ev.get("eventName") or ev.get("event")  # festival day label

            if not artist or not date:
                warn(f"Skipping row {idx}: missing artist/date")
                continue

            self._log(f"[INFO] Looking up setlist for {artist} on {date} @ {venue}, {city}")
            event_data = self.setlist.find_event_setlist(artist=artist, venue=venue, city=city, date=date)
            if not event_data:
                warn(f"[WARN] No matching setlist found for {artist} on {date} @ {venue}, {city}")
                continue

            # ----------------------------------------------
            # FESTIVAL MODE
            # ----------------------------------------------
            if event_data.get("is_festival"):
                self._log(f"[INFO] Festival mode detected for {artist} on {date}")
                lineup = event_data.get("lineup", []) or []

                # sort lineup using startTime -> stage -> lastUpdated -> name
                def _lineup_key(b):
                    st = self._parse_time_or_none(b.get("startTime"))
                    stage = (b.get("stage") or "").lower() if b.get("stage") else ""
                    lu = b.get("lastUpdated") or ""
                    nm = (b.get("name") or "").lower()
                    st_key = st if st else (99, 99, 99)
                    lu_key = lu if lu else "9999-99-99T99:99:99Z"
                    return (st_key, stage, lu_key, nm)

                ordered = sorted(lineup, key=_lineup_key)

                # Build pairs for the whole festival day (all bands)
                pairs = []
                for band in ordered:
                    name = band.get("name")
                    songs = band.get("songs", []) or []
                    if songs:
                        self._log(f"[INFO] Festival band {name} has {len(songs)} songs")
                        for s in songs:
                            pairs.append((s, name))
                    else:
                        self._log(f"[INFO] Festival band {name} has no songs in setlist; using spotify fallback")
                        uris = _spotify_top_tracks_fallback(name, limit=5)
                        for u in uris:
                            pairs.append((u, None))

                if not pairs:
                    warn(f"No tracks discovered for festival {artist} on {date}")
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

                    uri, score = _best_spotify_match_for_song(title_or_uri, artist_hint or artist)
                    if uri and uri not in seen:
                        track_uris.append(uri)
                        seen.add(uri)
                        self._log(f"[DEBUG] Matched '{title_or_uri}' -> {uri} (score={score})")
                    else:
                        self._log(f"[WARN] Could not match '{title_or_uri}' ({artist_hint})")

                if not track_uris:
                    warn(f"No tracks resolved for festival {artist} on {date}")
                    continue

                # Playlist name choice: user selected "Option C": use event_name only
                playlist_name = event_name if event_name else f"{artist} - {date}"
                # Short description
                description = (
                    f"Festival setlists from {artist} — {event_name}. "
                    f"Recorded at {venue or 'Unknown venue'} in {city or 'Unknown city'} on {date}."
                )

                # check duplicates
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

                # done with festival entry
                continue

            # ----------------------------------------------
            # NORMAL EVENT MODE
            # ----------------------------------------------
            headliner = event_data.get("headliner")
            headliner_songs = event_data.get("headliner_songs", []) or []
            openers = event_data.get("openers", []) or []

            # --- Sort openers before building pairs ---
            def _sort_key_for_opener(op):
                st = self._parse_time_or_none(op.get("startTime"))
                lu = op.get("lastUpdated")
                nm = (op.get("name") or "").lower()
                st_key = st if st else (99, 99, 99)
                lu_key = lu if lu else "9999-99-99T99:99:99Z"
                return (st_key, lu_key, nm)

            openers = sorted(openers, key=_sort_key_for_opener)

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

            # Build smart playlist description (short)
            date_str = date
            venue_str = venue or "Unknown venue"
            city_str = city or "Unknown city"

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

            playlist_name = f"{artist} - {date}"

            # Prevent duplicate playlists
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
