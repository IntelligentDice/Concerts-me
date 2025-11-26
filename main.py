import json
from google_sheets import load_sheet
from setlistfm_api import find_event_setlist
from spotify_api import get_spotify_client
from playlist_builder import build_playlist
from utils.logging_utils import log, warn

def main():
    log("Loading config...")
    with open("config.json") as f:
        cfg = json.load(f)

    log("Loading Google Sheet...")
    events = load_sheet(cfg["google_sheet_id"])

    log(f"Loaded {len(events)} events")

    sp = get_spotify_client()

    for ev in events:
        log(f"Processing: {ev['artist']} — {ev['date']}")

        setlist = find_event_setlist(
            ev["artist"], ev["venue"], ev["date"], cfg["setlistfm_api_key"]
        )

        if not setlist:
            warn(f"No setlist found for {ev['artist']} on {ev['date']}")
            continue

        build_playlist(sp, ev, setlist)

    log("Done!")

if __name__ == "__main__":
    main()
