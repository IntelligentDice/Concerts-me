# playlist_builder.py
from setlistfm_api import SetlistFM
from fuzzywuzzy import fuzz
from utils.logging_utils import log, warn
from spotify_api import search_track
from typing import List


# -------------------------------------------------------
# Helper functions outside the class
# -------------------------------------------------------
def _spotify_top_tracks_fallback(artist_name: str, limit: int = 5):
    q = f"artist:{artist_name}"
    results = search_track(q, limit=40)
    if not results:
        results = search_track(artist_name, limit=40)
    items = sorted(results, key=lambda it: it.get("popularity", 0), reverse=True)
    uris = [it.get("uri") for it in items if it.get("uri")]
    return uris[:limit]


def _best_spotify_match_for_song(song_title: str, artist_hint: str):
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
        score = (
            fuzz.token_set_ratio(song_title, name)
            + fuzz.token_set_ratio(artist_hint or "", artists)
        ) / 2

        if score > best_score:
            best_uri = uri
            best_score = score

    if not best_uri:
        results = search_track(song_title, limit=8)
        for item in results:
            uri = item.get("uri")
            name = item.get("name", "")
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            score = (
                fuzz.token_set_ratio(song_title, name)
                + fuzz.token_set_ratio(artist_hint or "", artists)
            ) / 2

            if score > best_score:
                best_uri = uri
                best_score = score

    return best_uri, best_score


# -------------------------------------------------------
# PlaylistBuilder Class
# -------------------------------------------------------
class PlaylistBuilder:
    def __init__(self, spotify_client, sheets_client, setlist_api_key: str,
                 debug: bool = False, dry_run: bool = False):

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

    # ----------------------------------------------
    # Build playlist wrapper
    # ----------------------------------------------
    def _build_playlist_for_event(self, user_id: str, playlist_name: str,
                                  track_uris: List[str],
                                  description: str = "Auto-generated concert playlist"):

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

        if hasattr(self.spotify, "add_tracks"):
            ok = self.spotify.add_tracks(pid, track_uris)
        elif hasattr(self.spotify, "add_tracks_to_playlist"):
            ok = self.spotify.add_tracks_to_playlist(pid, track_uris)
        else:
            raise RuntimeError("Spotify client missing track add method")

        if not ok:
            warn("Failed to add tracks to playlist")

        return pid

    # HH:MM:ss → tuple for sorting
    @staticmethod
    def _parse_time_or_none(t):
        if not t:
            return None
        try:
            parts = [int(p) for p in t.split(":")]
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts[:3])
        except Exception:
            return None

    # ----------------------------------------------
    # Main execution
    # ----------------------------------------------
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
            event_data = self.setlist.find_event_setlist(
                artist=artist,
                venue=venue,
                city=city,
                date=date
            )

            if not event_data:
                warn(f"[WARN] No matching setlist found for {artist} on {date} @ {venue}, {city}")
                continue

            # =========================================================
            # 🟪 FESTIVAL MODE — new unified logic
            # =========================================================
            if event_data.get("is_festival"):
                festival_name = event_data.get("festival_name") or artist
                festival_day_name = event_name or festival_name
                lineup = event_data.get("lineup") or []

                self._log(f"[INFO] Festival mode: {festival_day_name} with {len(lineup)} artists")

                # Sort lineup by startTime → lastUpdated → name
                def _sort_band(entry):
                    st = self._parse_time_or_none(entry.get("startTime"))
                    lu = entry.get("lastUpdated") or "9999-99-99T99:99:99Z"
                    nm = (entry.get("name") or "").lower()
                    st_key = st if st else (99, 99, 99)
                    return (st_key, lu, nm)

                lineup = sorted(lineup, key=_sort_band)

                # Build ordered track pairs
                pairs = []
                for b in lineup:
                    name = b["name"]
                    songs = b.get("songs", []) or []

                    if songs:
                        self._log(f"[INFO] Festival act {name} has {len(songs)} songs")
                        for s in songs:
                            pairs.append((s, name))
                    else:
                        self._log(f"[INFO] Festival act {name} missing songs → fallback")
                        uris = _spotify_top_tracks_fallback(name, limit=5)
                        for u in uris:
                            pairs.append((u, None))

                # Resolve all to URIs
                track_uris = []
                seen = set()

                for title_or_uri, artist_hint in pairs:
                    if artist_hint is None and isinstance(title_or_uri, str) and title_or_uri.startswith("spotify:"):
                        if title_or_uri not in seen:
                            track_uris.append(title_or_uri)
                            seen.add(title_or_uri)
                        continue

                    uri, score = _best_spotify_match_for_song(title_or_uri, artist_hint or "")
                    if uri and uri not in seen:
                        track_uris.append(uri)
                        seen.add(uri)
                        self._log(f"[DEBUG] '{title_or_uri}' → {uri} (score={score})")
                    else:
                        self._log(f"[WARN] No match for '{title_or_uri}' ({artist_hint})")

                if not track_uris:
                    warn(f"[WARN] No tracks resolved for festival day {festival_day_name}")
                    continue

                # Build playlist name + short description
                playlist_name = f"{festival_day_name} - {date}"

                venue_str = venue or "Unknown venue"
                city_str = city or "Unknown city"

                description = (
                    f"Recorded live at {venue_str}, {city_str} on {date}. "
                    f"Festival day: {festival_day_name}."
                )

                try:
                    user_id = self.spotify.get_current_user_id() or "me"
                except Exception:
                    user_id = "me"

                pid = self._build_playlist_for_event(
                    user_id,
                    playlist_name,
                    track_uris,
                    description
                )

                if self.dry_run:
                    log(f"[DRY-RUN] Playlist NOT created: {playlist_name}")
                else:
                    log(f"Playlist created: {playlist_name} (id={pid})")

                continue
            # =========================================================
            # END FESTIVAL MODE
            # =========================================================

            # ---------------------------------------------------------
            # NORMAL EVENT MODE
            # ---------------------------------------------------------
            headliner = event_data.get("headliner")
            headliner_songs = event_data.get("headliner_songs", []) or []
            openers = event_data.get("openers", []) or []

            # Opener sorting
            def _sort_opener(op):
                st = self._parse_time_or_none(op.get("startTime"))
                lu = op.get("lastUpdated") or "9999-99-99T99:99:99Z"
                nm = (op.get("name") or "").lower()
                return (st if st else (99, 99, 99), lu, nm)

            openers = sorted(openers, key=_sort_opener)

            # Build song pairs: openers first, then headliner
            pairs = []
            for op in openers:
                oname = op["name"]
                songs = op.get("songs", []) or []

                if songs:
                    for s in songs:
                        pairs.append((s, oname))
                else:
                    uris = _spotify_top_tracks_fallback(oname, limit=5)
                    for u in uris:
                        pairs.append((u, None))

            # Add headliner songs
            for s in headliner_songs:
                pairs.append((s, headliner))

            # Resolve URIs
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
                else:
                    self._log(f"[WARN] Could not match '{title_or_uri}' ({artist_hint})")

            if not track_uris:
                warn(f"No tracks resolved for {artist} on {date}")
                continue

            # Build description
            venue_str = venue or "Unknown venue"
            city_str = city or "Unknown city"

            if openers:
                if len(openers) == 1:
                    opener_text = f"with opener {openers[0]['name']}"
                else:
                    names = ", ".join(op["name"] for op in openers)
                    opener_text = f"with openers {names}"
            else:
                opener_text = ""

            if opener_text:
                description = (
                    f"Live setlist from {headliner} {opener_text} — recorded at "
                    f"{venue_str}, {city_str} on {date}."
                )
            else:
                description = (
                    f"Live setlist from {headliner} — recorded at "
                    f"{venue_str}, {city_str} on {date}."
                )

            playlist_name = f"{artist} - {date}"

            # Prevent duplicates
            existing = None
            try:
                if hasattr(self.spotify, "find_playlist_by_name"):
                    existing = self.spotify.find_playlist_by_name(playlist_name)
            except Exception:
                existing = None

            if existing:
                if self.dry_run:
                    log(f"[DRY-RUN] Playlist already exists: {playlist_name} (id={existing}) — skipping")
                else:
                    log(f"[INFO] Playlist already exists: {playlist_name} (id={existing}) — skipping")
                continue

            try:
                user_id = self.spotify.get_current_user_id() or "me"
            except Exception:
                user_id = "me"

            pid = self._build_playlist_for_event(
                user_id,
                playlist_name,
                track_uris,
                description
            )

            if self.dry_run:
                log(f"[DRY-RUN] Playlist NOT created: {playlist_name}")
            else:
                log(f"Playlist created: {playlist_name} (id={pid})")
