# main.py
import json
from google_sheets import load_sheet
from playlist_builder import build_playlist_for_event
from pathlib import Path

CFG_PATH = Path("config.json")

def load_config():
    return json.loads(CFG_PATH.read_text(encoding="utf8"))

def main():
    cfg = load_config()
    sheet_id = cfg.get("google", {}).get("sheet_id")
    if not sheet_id:
        raise SystemExit("Please set google.sheet_id in config.json")

    rows = load_sheet(sheet_id)  # returns list of dicts with artist,venue,city,date
    setlist_key = cfg.get("setlistfm", {}).get("api_key")
    if not setlist_key:
        raise SystemExit("Please set setlistfm.api_key in config.json")

    for row in rows:
        build_playlist_for_event(row, setlist_key)

if __name__ == "__main__":
    main()
