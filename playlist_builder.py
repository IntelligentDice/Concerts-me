# playlist_builder.py
from spotify_api import get_current_user_id, create_playlist, add_tracks_to_playlist, search_track
from setlistfm_api import find_event_setlist
from fuzzywuzzy import fuzz
from utils.logging_utils import log, warn

def _best_spotify_match_for_song(song_title: str, artist_hint: str):
    # try queries in order for better matches
    queries = [
        f"{song_title} {artist_hint}",
        f"{song_title}"
    ]
    best_uri = None
    best_score = -1
    for q in queries:
        results = search_track(q, limit=8)
        for t in results:
            score = fuzz.partial_ratio(t["name"].lower(), song_title.lower())
            # prefer exact artist matches slightly
            if artist_hint and t.get("artists"):
                if any(artist_hint.lower() in a["name"].lower() for a in t["artists"]):
                    score += 10
            if score > best_score:
                best_score = score
                best_uri = t["uri"]
    return best_uri, best_score

def build_playlist_for_event(event_row: dict, setlistfm_key: str, opener_top_tracks: int = 5):
    """
    event_row expected fields: artist, venue, city, date
    """
    artist = event_row.get("artist")
    venue = event_row.get("venue")
    city = event_row.get("city")
    date = event_row.get("date")

    log(f"Finding setlist for {artist} on {date} at {venue}, {city}")
    setlist = find_event_setlist(artist, venue, date, setlistfm_key)
    if not setlist:
        warn(f"No setlist found for {artist} {date}")
        return None

    # Extract songs (simple approach: iterate sets -> songs)
    songs = []
    sets = setlist.get("sets", {}).get("set", [])
    if not isinstance(sets, list):
        sets = [sets]
    for s in sets:
        song_entries = s.get("song", []) or []
        if not isinstance(song_entries, list):
            song_entries = [song_entries]
        for se in song_entries:
            name = se.get("name") if isinstance(se, dict) else se
            if name:
                songs.append((name, setlist.get("artist", {}).get("name", artist)))

    if not songs:
        warn("No songs extracted from setlist")
        return None

    user_id = get_current_user_id()
    playlist_name = f"{artist} — {venue}, {city} ({date})"
    description = f"Imported setlist for {artist} at {venue} on {date}"
    playlist_id = create_playlist(user_id, playlist_name, description)
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
