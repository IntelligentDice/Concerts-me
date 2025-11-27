# setlistfm_api.py
import requests
from rapidfuzz import fuzz
from datetime import datetime
from typing import Optional, List, Dict

BASE_URL = "https://api.setlist.fm/rest/1.0"


def _norm_text(s: Optional[str]) -> str:
    """Lowercase, remove punctuation except spaces, trim."""
    if not s:
        return ""
    return "".join(ch for ch in s.lower().strip() if ch.isalnum() or ch.isspace()).strip()


class SetlistFM:
    """
    Strict Approach A implementation:
      - Query setlists by date (DD-MM-YYYY)
      - Filter by venue + city fuzzy matches
      - Aggregate per-artist setlists for that venue/date
      - Pick headliner by fuzzy match to requested artist (or longest set as fallback)
    """

    def __init__(self, api_key: str, verbose: bool = False):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}
        self.verbose = verbose

    def _log(self, *args):
        if self.verbose:
            print(*args)

    def _to_setlistfm_date(self, iso_date: str) -> str:
        """Convert YYYY-MM-DD -> DD-MM-YYYY (Setlist.fm format)."""
        dt = datetime.fromisoformat(iso_date)
        return dt.strftime("%d-%m-%Y")

    def _extract_songs(self, entry: dict) -> List[str]:
        """Extract ordered song names from a setlist.fm set entry."""
        songs: List[str] = []
        sets = entry.get("sets", {}).get("set", [])
        if not isinstance(sets, list):
            sets = [sets] if sets else []
        for block in sets:
            raw = block.get("song", []) or []
            if not isinstance(raw, list):
                raw = [raw]
            for s in raw:
                if isinstance(s, dict):
                    name = s.get("name")
                    if name:
                        songs.append(name)
                elif isinstance(s, str):
                    songs.append(s)
        return songs

    def find_event_setlist(self, artist: str, venue: Optional[str], city: Optional[str], date: str):
    """
    Two-stage search:
    1. Search for the headliner by artist + date
    2. Use the resolved venue + city + event date to pull all bands (openers + headliner)
    """
    self._log(f"[INFO] Searching setlists for artist='{artist}', date='{date}'")

    date_ddmm = self._to_setlistfm_date(date)

    # --- 1. Find the headliner event via artist search ---
    try:
        resp = requests.get(
            f"{BASE_URL}/search/setlists",
            headers=self.headers,
            params={"artistName": artist, "date": date_ddmm},
            timeout=15,
        )
    except Exception as e:
        self._log("[WARN] Setlist.fm network error:", e)
        return None

    if resp.status_code != 200:
        self._log("[WARN] Setlist.fm returned", resp.status_code)
        return None

    artist_sets = resp.json().get("setlist", []) or []
    self._log(f"[DEBUG] total setlists returned for artist search: {len(artist_sets)}")

    # Must match the same date
    headliner_entry = None
    for e in artist_sets:
        if e.get("eventDate") == date_ddmm:
            headliner_entry = e
            break

    if not headliner_entry:
        self._log("[WARN] No headliner setlist found for exact date")
        return None

    ev = headliner_entry.get("venue", {})
    resolved_venue = ev.get("name", "")
    resolved_city = ev.get("city", {}).get("name", "")
    self._log(f"[INFO] Matched eventId={headliner_entry.get('id')}, venue='{resolved_venue}', city='{resolved_city}'")

    headliner_songs = self._extract_songs(headliner_entry)
    self._log(f"[DEBUG] headliner_songs={len(headliner_songs)}")

    # --- 2. Fetch all bands that played the same venue/city/date ---
    try:
        resp2 = requests.get(
            f"{BASE_URL}/search/setlists",
            headers=self.headers,
            params={"venueName": resolved_venue, "cityName": resolved_city, "date": date_ddmm},
            timeout=15,
        )
    except Exception as e:
        self._log("[WARN] Setlist.fm network error (openers search):", e)
        return {"headliner": artist, "headliner_songs": headliner_songs, "openers": []}

    all_entries = resp2.json().get("setlist", []) or []
    self._log(f"[DEBUG] opener+headliner search returned {len(all_entries)} entries")

    openers = []
    for e in all_entries:
        name = e.get("artist", {}).get("name")
        if not name or name.lower() == artist.lower():
            continue  # skip headliner

        songs = self._extract_songs(e)
        openers.append({"name": name, "songs": songs})

    self._log(f"[DEBUG] openers_found={len(openers)}")

    return {
        "headliner": artist,
        "headliner_songs": headliner_songs,
        "openers": openers,
    }
