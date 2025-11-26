from spotify_api import create_playlist, add_tracks
from fuzzywuzzy import fuzz
import spotipy

def fuzzy_song_match(sp, artist, title):
    results = sp.search(q=f"{artist} {title}", type="track", limit=5)
    if not results["tracks"]["items"]:
        return None

    best = None
    best_score = 0

    for track in results["tracks"]["items"]:
        score = fuzz.partial_ratio(track["name"].lower(), title.lower())
        if score > best_score:
            best_score = score
            best = track["id"]

    return best

def build_playlist(sp, event, setlist):
    playlist_name = f"{event['artist']} — {event['venue']}, {event['city']} ({event['date']})"
    playlist_id = create_playlist(sp, playlist_name)

    track_ids = []

    for artist_block in setlist["sets"]["set"]:
        songs = artist_block.get("song", [])
        for s in songs:
            title = s.get("name", "")
            track = fuzzy_song_match(sp, event["artist"], title)
            if track:
                track_ids.append(track)

    add_tracks(sp, playlist_id, track_ids)
