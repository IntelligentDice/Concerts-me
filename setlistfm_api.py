import requests
from rapidfuzz import fuzz
from datetime import datetime


BASE_URL = "https://api.setlist.fm/rest/1.0"


class SetlistFM:

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}

    # ------------------------------------------------------------
    # MAIN ENTRY: Find headliner + openers using venue + date
    # ------------------------------------------------------------
    def find_event_setlist(self, artist: str, venue: str, city: str, date: str):
        """
        Searches for ALL setlists at the same venue + date.
        This fixes the API limitation where openers are separate setlist entries.
        """

        print(f"[INFO] Searching full event for {artist} on {date} @ {venue}, {city}")

        date_ddmm = self._to_setlistfm_date(date)

        params = {
            "date": date_ddmm,
            "venueName": venue,
            "cityName": city,
        }

        r = requests.get(f"{BASE_URL}/search/setlists",
                         headers=self.headers, params=params, timeout=20)

        if r.status_code != 200:
            print("[WARN] Setlist.fm returned error")
            return None

        all_sets = r.json().get("setlist", [])
        if not all_sets:
            print("[WARN] No setlists returned for venue+date.")
            return None

        print(f"[DEBUG] Found {len(all_sets)} total setlists at this event.")

        # ------------------------------------------------------------
        # Identify headliner + openers by fuzzy score
        # ------------------------------------------------------------
        headliner = None
        openers = []

        for entry in all_sets:
            band = entry.get("artist", {}).get("name", "")

            score = fuzz.token_set_ratio(artist, band)

            if score > 90:
                print(f"[DEBUG] Matched HEADLINER: {band} (score={score})")
                headliner = entry
            elif score > 40:
                print(f"[DEBUG] Possible OPENER: {band} (score={score})")
                openers.append(entry)
            else:
                print(f"[DEBUG] Ignoring unrelated band: {band} (score={score})")

        if not headliner:
            print("[ERROR] Could not identify headliner from API response.")
            return None

        # Extract songs
        headliner_songs = self._extract_songs_from_set(headliner)

        opener_blocks = []
        for o in openers:
            name = o.get("artist", {}).get("name", "")
            songs = self._extract_songs_from_set(o)
            opener_blocks.append({
                "name": name,
                "songs": songs
            })

        print("[DEBUG] FINAL HEADLINER SONG COUNT:", len(headliner_songs))
        print("[DEBUG] FINAL OPENER LIST:", opener_blocks)

        return {
            "headliner": artist,
            "headliner_songs": headliner_songs,
            "openers": opener_blocks
        }

    # ------------------------------------------------------------

    def _extract_songs_from_set(self, setlist_entry):
        """Extract song names from a setlist.fm API entry."""
        songs = []
        sets = setlist_entry.get("sets", {}).get("set", [])
        if not isinstance(sets, list):
            sets = [sets]

        for s in sets:
            raw = s.get("song", []) or []
            if not isinstance(raw, list):
                raw = [raw]

            for item in raw:
                if isinstance(item, dict) and item.get("name"):
                    songs.append(item["name"])
                elif isinstance(item, str):
                    songs.append(item)

        return songs

    # ------------------------------------------------------------

    def _to_setlistfm_date(self, date_str: str) -> str:
        """Convert YYYY-MM-DD → DD-MM-YYYY."""
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%d-%m-%Y")
