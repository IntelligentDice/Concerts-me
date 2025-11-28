# playlist_builder.py
from setlistfm_api import SetlistFM
from fuzzywuzzy import fuzz
from utils.logging_utils import log, warn
from spotify_api import search_track
from typing import List


# ---------------------------------------------------------------
# Helper: Spotify top tracks fallback
# ---------------------------------------------------------------
def _spotify_top_tracks_fallback(artist_name: str, limit: int = 5):
    q = f"artist:{artist_name}"
    results = search_track(q, limit=40)
    if not results:
        results = search_track(artist_name, limit=40)

    items = sorted(results, key=lambda it: it.get("popularity", 0), reverse=True)
    uris = [it.get("uri") for it in items if it.get("uri")]
    return uris[:limit]


# ---------------------------------------------------------------
# Helper: Match a song to a Spotify URI
# ---------------------------------------------------------------
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
            best_score = score
            best_uri = uri

    # fallback broad search
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
                best_score = score
                best_uri = uri

    return best_uri, best_score


# ---------------------------------------------------------------
# Playlist Builder
# ---------------------------------------------------------------
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

    # -----------------------------------------------------------
    # Create playlist wrapper
    # -----------------------------------------------------------
    def _build_playlist_for_event(self, user_id: str, playlist_name: str,
                                  track_uris: List[str], description: str):

        if self.dry_run:
            log(f"[DRY-RUN] Would create playlist '{playlist_name}' ({len(track_uris)} tracks)")
            for u in track_uris:
                log(f"[DRY-RUN]  Track -> {u}")
            return "dry-run-playlist-id"

        log(f"Creating playlist {playlist_name} ({len(track_uris)} tracks)")

        pid = self.spotify.create_playlist(
            user_id,
            playlist_name,
            public=False,
            description=description
        )

        if not pid:
            raise RuntimeError("Failed to create playlist")

        # handle clients with .add_tracks vs .add_tracks_to_playlist
        if hasattr(self.spotify, "add_tracks"):
            ok = self.spotify.add_tracks(pid, track_uris)
        else:
            ok = self.spotify.add_tracks_to_playlist(pid, track_uris)

        if not ok:
            warn("Failed to add tracks to playlist")

        return pid

    # Sort-helper for start times
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

    # -----------------------------------------------------------
    # RUN
    # -----------------------------------------------------------
    def run(self):
        events = self.sheets.read_events()

        for idx, ev in enumerate(events):
            artist = ev.get("artist")
            date = ev.get("date")
            venue = ev.get("venue")
            city = ev.get("city")
            event_name = ev.get("event_name") or ev.get("eventName") or ev.get("event")

            if not artist or not date:
                warn(f"Skipping row {idx}: missing artist or date")
                continue

            self._log(f"[INFO] Looking up setlist for {artist} on {date} @ {venue}, {city}")

            event_data = self.setlist.find_event_setlist(
                artist=artist, venue=venue, city=city, date=date
            )

            if not event_data:
                warn(f"[WARN] No matching setlist found for {artist} on {date} @ {venue}, {city}")
                continue

            # -----------------------------------------------------------
            # FESTIVAL MODE
            # -----------------------------------------------------------
            is_festival = str(ev.get("is_festival", "")).strip().lower() in ("true", "yes", "1")

            if is_festival:
                self._log(f"[INFO] Festival detected: {event_name}  ({date})")

                festival_sets = self.setlist.search_setlists_festival_mode(
                    venue=venue, city=city, date=date
                )

                if not festival_sets:
                    warn(f"[WARN] No festival sets found on {date}")
                    continue

                # Convert festival bands to uniform entries
                band_entries = []
                for e in festival_sets:
                    band = e.get("artist", {}).get("name")
                    if not band:
                        continue

                    band_entries.append({
                        "name": band,
                        "songs": e.get("songs", []) or [],
                        "startTime": e.get("startTime"),
                        "lastUpdated": e.get("lastUpdated"),
                    })

                # Sort festival acts
                def _fest_sort(b):
                    st = self._parse_time_or_none(b.get("startTime"))
                    lu = b.get("lastUpdated")
                    name = (b.get("name") or "").lower()

                    st_k = st if st else (99, 99, 99)
                    lu_k = lu if lu else "9999-99-99T99:99:99Z"
                    return (st_k, lu_k, name)

                band_entries = sorted(band_entries, key=_fest_sort)

                # Build track list (songs or fallback top tracks)
                pairs = []
                for b in band_entries:
                    nm = b["name"]
                    songs = b["songs"]

                    if songs:
                        for s in songs:
                            pairs.append((s, nm))
                    else:
                        fallback = _spotify_top_tracks_fallback(nm, limit=5)
                        for uri in fallback:
                            pairs.append((uri, None))

                # Resolve to URIs
                track_uris = []
                seen = set()
                for title_or_uri, a_hint in pairs:
                    if a_hint is None and isinstance(title_or_uri, str) and title_or_uri.startswith("spotify:"):
                        if title_or_uri not in seen:
                            track_uris.append(title_or_uri)
                            seen.add(title_or_uri)
                        continue

                    uri, score = _best_spotify_match_for_song(title_or_uri, a_hint or "")
                    if uri and uri not in seen:
                        track_uris.append(uri)
                        seen.add(uri)

                if not track_uris:
                    warn(f"[WARN] No tracks resolved for festival day {event_name}")
                    continue

                # Playlist name + description
                playlist_name = f"{event_name} - {date}"
                description = f"Recorded live at {venue}, {city} on {date}. Festival day: {event_name}."

                try:
                    user_id = self.spotify.get_current_user_id() or "me"
                except Exception:
                    user_id = "me"

                pid = self._build_playlist_for_event(user_id, playlist_name, track_uris, description)

                if self.dry_run:
                    log(f"[DRY-RUN] Playlist NOT created: {playlist_name}")
                else:
                    log(f"[INFO] Playlist created: {playlist_name} (id={pid})")

                continue  # don't fall into normal mode

            # -----------------------------------------------------------
            # NORMAL MODE
            # -----------------------------------------------------------
            openers = event_data.get("openers", []) or []

            # restore previous behavior exactly:
            # keep names exactly as returned (even None)
            event_data["openers"] = openers

            headliner = event_data.get("headliner")
            headliner_songs = event_data.get("headliner_songs", []) or []

            if not headliner:
                # previous logic fallback
                if openers:
                    fallback = openers[-1]
                    headliner = fallback.get("name")
                    headliner_songs = fallback.get("songs", []) or []
                    warn(f"[WARN] Missing headliner; using fallback '{headliner}'")
                else:
                    warn(f"[WARN] No valid artists found for {artist} on {date}")
                    continue

            # Sort openers (preserve original behavior)
            def _sort_key(op):
                st = self._parse_time_or_none(op.get("startTime"))
                lu = op.get("lastUpdated")
                nm = (op.get("name") or "").lower()

                st_key = st if st else (99, 99, 99)
                lu_key = lu if lu else "9999-99-99T99:99:99Z"
                return (st_key, lu_key, nm)

            openers = sorted(openers, key=_sort_key)

            # Build song list (openers first → headliner)
            pairs = []
            for op in openers:
                nm = op.get("name")
                songs = op.get("songs", [])

                if songs:
                    for s in songs:
                        pairs.append((s, nm))
                else:
                    fallback = _spotify_top_tracks_fallback(nm, limit=5)
                    for uri in fallback:
                        pairs.append((uri, None))

            for s in headliner_songs:
                pairs.append((s, headliner))

            # Resolve to URIs
            track_uris = []
            seen = set()

            for title_or_uri, hint in pairs:
                if hint is None and isinstance(title_or_uri, str) and title_or_uri.startswith("spotify:"):
                    if title_or_uri not in seen:
                        track_uris.append(title_or_uri)
                        seen.add(title_or_uri)
                    continue

                uri, score = _best_spotify_match_for_song(title_or_uri, hint or headliner)
                if uri and uri not in seen:
                    track_uris.append(uri)
                    seen.add(uri)

            if not track_uris:
                warn(f"[WARN] No tracks resolved for {artist} on {date}")
                continue

            # Description (short form)
            venue_str = venue or "Unknown venue"
            city_str = city or "Unknown city"

            if openers:
                if len(openers) == 1:
                    opener_str = f"with opener {openers[0]['name']}"
                else:
                    names = ", ".join(o.get("name") for o in openers)
                    opener_str = f"with openers {names}"
            else:
                opener_str = ""

            if opener_str:
                description = (
                    f"Live setlist from {headliner} {opener_str} — "
                    f"recorded at {venue_str}, {city_str} on {date}."
                )
            else:
                description = (
                    f"Live setlist from {headliner} — recorded at {venue_str}, {city_str} on {date}."
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
                    log(f"[DRY-RUN] Playlist already exists: {playlist_name} (id={existing}) — skipping creation")
                else:
                    log(f"[INFO] Playlist already exists: {playlist_name} (id={existing}) — skipping creation")
                continue

            try:
                user_id = self.spotify.get_current_user_id() or "me"
            except Exception:
                user_id = "me"

            pid = self._build_playlist_for_event(
                user_id, playlist_name, track_uris, description
            )

            if self.dry_run:
                log(f"[DRY-RUN] Playlist NOT created: {playlist_name}")
            else:
                log(f"[INFO] Playlist created: {playlist_name} (id={pid})")
