# generate_playlists.py
import os
import csv
import argparse
import time
from setlistfm import SetlistFM
from apple_music import AppleMusic
from rapidfuzz import fuzz

USE_GOOGLE_SHEET = os.getenv("USE_GOOGLE_SHEET", "false").lower() in ("1","true","yes")
GSHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
OPENER_TOP_TRACKS = int(os.getenv("OPENER_TOP_TRACKS", "5"))  # fallback count

def read_events_from_csv(path="events.csv"):
    events = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for r in reader:
            events.append({
                "artist": r.get("artist","").strip(),
                "event_name": r.get("event_name","").strip(),
                "venue": r.get("venue","").strip(),
                "city": r.get("city","").strip(),
                "date": r.get("date","").strip()
            })
    return events

# (Google Sheets helper omitted here - same as before; keep if you use GS)

def dedupe_preserve_order(items):
    seen = set()
    out = []
    for it in items:
        key = it.lower() if isinstance(it, str) else str(it).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out

def best_apple_match_for_song(am, song_name, artist_name):
    # try exact-ish search first
    try:
        cid = am.search_track(song_name, artist_name)
        return cid
    except Exception:
        return None

def gather_tracks_for_event(ev, sl: SetlistFM, am: AppleMusic):
    """
    Returns list of tuples: (catalog_id, source, song_title, artist_for_song)
    where source is 'setlist' or 'apple_top'
    """
    results = []

    candidates = sl.search_setlists(artist=ev['artist'], date=ev['date'])
    best = sl.pick_best_setlist(candidates, ev)
    if not best:
        print("  No good setlist match found — skipping")
        return results, None, []

    # Get openers (list of {name, songs})
    openers = sl.extract_openers(best)  # may be empty

    # For each opener:
    for opener in openers:
        opener_name = opener.get("name")
        opener_songs = opener.get("songs", []) or []
        if opener_songs:
            # use setlist-provided songs
            for song in opener_songs:
                time.sleep(0.15)
                cid = best_apple_match_for_song(am, song, opener_name)
                if cid:
                    results.append((cid, "setlist", song, opener_name))
                else:
                    print(f"   - Openerset song not found in Apple Music: {opener_name} — {song}")
        else:
            # fallback: fetch top tracks for opener
            print(f"   - No setlist for opener {opener_name}; falling back to top {OPENER_TOP_TRACKS} Apple tracks")
            try:
                artists = am.search_artist(opener_name, limit=3)
                if artists:
                    # choose best-fuzzy match of returned artists
                    best_artist = None
                    best_score = -1
                    for a in artists:
                        score = fuzz.token_sort_ratio(opener_name, a['name'])
                        if score > best_score:
                            best_score = score
                            best_artist = a
                    if best_artist and best_score >= 60:
                        top_ids = am.get_artist_top_tracks(best_artist['id'], limit=OPENER_TOP_TRACKS)
                        for tid in top_ids:
                            results.append((tid, "apple_top", None, best_artist['name']))
                    else:
                        print(f"     - No good Apple Music artist match for opener: {opener_name}")
                else:
                    print(f"     - No artists found on Apple Music for opener: {opener_name}")
            except Exception as e:
                print("     - Error fetching top tracks:", e)

    # Headliner songs (from main/headliner sets)
    headliner_songs = sl.extract_songs(best)
    for song in headliner_songs:
        time.sleep(0.15)
        cid = best_apple_match_for_song(am, song, ev['artist'])
        if cid:
            results.append((cid, "setlist", song, ev['artist']))
        else:
            print(f"   - Headliner song not found in Apple Music: {song}")

    # dedupe by catalog id preserving order
    seen = set()
    deduped = []
    for cid, src, song_title, who in results:
        if cid in seen:
            continue
        seen.add(cid)
        deduped.append((cid, src, song_title, who))

    # Build summary lists for logging
    # reorganize into structured buckets
    opener_tracks = {}
    for opener in openers:
        name = opener["name"]
        opener_tracks[name] = []

    for cid, src, song_title, who in deduped:
        if who.lower() == ev['artist'].lower():
            continue  # headliner; handled below
        # opener
        if who not in opener_tracks:
            opener_tracks[who] = []
        opener_tracks[who].append((cid, src, song_title, who))

    # headliner
    headliner_tracks = []
    for cid, src, song_title, who in deduped:
        if who.lower() == ev['artist'].lower():
            headliner_tracks.append((cid, src, song_title, who))

    return {
        "openers": opener_tracks,
        "headliner": headliner_tracks
    }, best, openers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-file", default="events.csv")
    args = parser.parse_args()

    setlist_key = os.getenv("SETLIST_API_KEY")
    dev_token = os.getenv("APPLE_DEVELOPER_TOKEN")
    user_token = os.getenv("APPLE_USER_TOKEN")
    storefront = os.getenv("APPLE_STOREFRONT", "us")

    if not setlist_key:
        raise SystemExit("SETLIST_API_KEY must be set")
    if not dev_token or not user_token:
        raise SystemExit("APPLE_DEVELOPER_TOKEN and APPLE_USER_TOKEN must be set")

    events = read_events_from_csv(args.events_file)
    sl = SetlistFM(setlist_key)
    am = AppleMusic(dev_token, user_token, storefront=storefront)

    for ev in events:
        print(f"Processing: {ev['artist']} — {ev['date']} @ {ev['venue']}, {ev['city']}")
        try:
            tracks, setlist_obj, openers = gather_tracks_for_event(ev, sl, am)
            if not tracks:
                print("  No matched tracks found — skipping playlist creation")
                continue

            playlist_name = f"{ev['artist']} — {ev['date']} @ {ev['venue']}"
            description_lines = [
                f"Setlist.fm import for {ev['artist']} on {ev['date']} at {ev['venue']}.",
                "Includes openers where available."
            ]
            # add opener names to description
            if openers:
                opener_names = ", ".join([o['name'] for o in openers])
                description_lines.append(f"Openers detected: {opener_names}")
            description = " ".join(description_lines)

            print(f"  Creating playlist: {playlist_name}")
            res = am.create_playlist(playlist_name, description)
            playlist_id = res.get("data", [{}])[0].get("id")
            if not playlist_id:
                print("  Failed to get playlist id from response.")
                continue

            PLAYLIST_ORDER = os.getenv("PLAYLIST_ORDER", "openers_first")
	    OPENERS_ORDER  = os.getenv("OPENERS_ORDER", "as_detected")

	    openers_dict = tracks["openers"]
	    headliner_list = tracks["headliner"]
	    
	    # order openers
	    if OPENERS_ORDER == "as_detected":
    	        ordered_openers = [o["name"] for o in openers]
	    else:
	        # placeholder for future custom logic
	        ordered_openers = [o["name"] for o in openers]

	    # flatten tracks according to PLAYLIST_ORDER
	    final_track_ids = []

	    if PLAYLIST_ORDER == "openers_first":
	        # 1. Add openers in detected order
	        for opener_name in ordered_openers:
	            band_tracks = openers_dict.get(opener_name, [])
	            final_track_ids.extend([t[0] for t in band_tracks])
	        # 2. Add headliner
	        final_track_ids.extend([t[0] for t in headliner_list])

	    elif PLAYLIST_ORDER == "headliner_first":
	        final_track_ids.extend([t[0] for t in headliner_list])
	        for opener_name in ordered_openers:
	            band_tracks = openers_dict.get(opener_name, [])
	            final_track_ids.extend([t[0] for t in band_tracks])

	    else:
	        print(f"  Unknown PLAYLIST_ORDER={PLAYLIST_ORDER}; defaulting to openers_first")
	        for opener_name in ordered_openers:
	            final_track_ids.extend([t[0] for t in openers_dict.get(opener_name, [])])
	        final_track_ids.extend([t[0] for t in headliner_list])

	    # Remove duplicates while preserving order
	    seen = set()
	    ordered_unique_ids = []
	    for cid in final_track_ids:
	        if cid not in seen:
	            ordered_unique_ids.append(cid)
	            seen.add(cid)

	    print(f"  Adding {len(ordered_unique_ids)} tracks to playlist in controlled order")
	    am.add_tracks_to_playlist(playlist_id, ordered_unique_ids)

            # print small summary
            setlist_count = sum(1 for t in tracks if t[1] == "setlist")
            top_count = sum(1 for t in tracks if t[1] == "apple_top")
            print(f"  Done. (from setlist: {setlist_count}, fallback top-tracks: {top_count})")

        except Exception as e:
            print("  Error:", e)

if __name__ == "__main__":
    main()
