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

    def find_event_setlist(
        self,
        artist: str,
        venue: Optional[str],
        city: Optional[str],
        date: str
    ) -> Optional[Dict]:
        """
        New Approach A:
        1. Search setlists by artist (not date).
        2. Pick the one with matching date.
        3. Use that setlist's venue/city/eventId.
        4. Pull all artists from that event to detect openers.
        """

        self._log(f"[INFO] Searching setlists for artist='{artist}', date='{date}'")

        # 1) Step 1: Query by artist
        try:
            resp = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"artistName": artist},
                timeout=20,
            )
        except Exception as e:
            self._log("[WARN] Setlist.fm network error:", e)
            return None

        if resp.status_code != 200:
            self._log(f"[WARN] Setlist.fm returned status {resp.status_code}: {resp.text[:200]}")
            return None

        results = resp.json().get("setlist", []) or []
        self._log(f"[DEBUG] total setlists returned for artist search: {len(results)}")

        if not results:
            self._log("[WARN] No setlists found for that artist")
            return None

        # 2) Step 2: Match exact date (Setlist.fm uses DD-MM-YYYY)
        target_ddmm = self._to_setlistfm_date(date)
        self._log(f"[DEBUG] matching against date={target_ddmm}")

        matched = None
        for item in results:
            event_date = item.get("eventDate")  # format: "22-11-2025"
            if event_date == target_ddmm:
                matched = item
                break

        if not matched:
            self._log("[WARN] Artist has no setlist for that date")
            return None

        # Extract details of the matched concert
        event_id = matched.get("id")
        venue_name = matched.get("venue", {}).get("name", "")
        city_name = matched.get("venue", {}).get("city", {}).get("name", "")

        self._log(f"[INFO] Matched eventId={event_id}, venue='{venue_name}', city='{city_name}'")

        # Extract headliner songs from this specific show
        headliner_songs = self._extract_songs(matched)

        # 3) Step 3: Now load the full event by eventId to get all artists
        try:
            full_resp = requests.get(
                f"{BASE_URL}/setlist/{event_id}",
                headers=self.headers,
                timeout=20,
            )
        except Exception as e:
            self._log("[WARN] Failed to fetch full event:", e)
            return {
                "headliner": artist,
                "headliner_songs": headliner_songs,
                "openers": []
            }

        if full_resp.status_code != 200:
            self._log(f"[WARN] Full event fetch status {full_resp.status_code}: {full_resp.text[:200]}")
            return {
                "headliner": artist,
                "headliner_songs": headliner_songs,
                "openers": []
            }

        full_event = full_resp.json()

        # 4) Step 4: Build opener list
        openers = []
        for s in full_event.get("setlist", {}).get("set", []):
            pass  # ignore — this section is not where openers are listed

        # Instead check "artist" block of matching setlists
        # The event-level "sets" array is repeated per performer
        all_performers = full_event.get("sets", {}).get("set", [])
        if not isinstance(all_performers, list):
            all_performers = [all_performers]

        performers = {}  # name -> list of songs

        for block in all_performers:
            raw_artist = block.get("artist", {}) or {}
            name = raw_artist.get("name")
            if not name:
                continue

            songs = []
            raw_song_list = block.get("song", [])
            if not isinstance(raw_song_list, list):
                raw_song_list = [raw_song_list]

            for s in raw_song_list:
                if isinstance(s, dict):
                    nm = s.get("name")
                    if nm:
                        songs.append(nm)

            if name not in performers:
                performers[name] = []

            performers[name].extend(songs)

        # Convert to opener structure
        openers_list = []
        for name, songs in performers.items():
            if name.lower() == artist.lower():
                continue  # headliner
            openers_list.append({"name": name, "songs": songs})

        self._log(f"[DEBUG] headliner_songs={len(headliner_songs)} openers_found={len(openers_list)}")

        return {
            "headliner": artist,
            "headliner_songs": headliner_songs,
            "openers": openers_list
        }

