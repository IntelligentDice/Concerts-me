# setlistfm_api.py
import requests
from rapidfuzz import fuzz
from datetime import datetime
from typing import Optional, List, Dict

BASE_URL = "https://api.setlist.fm/rest/1.0"


# ------------------------------
# Text normalization helpers
# ------------------------------
def _norm_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return "".join(
        ch for ch in s.lower().strip()
        if ch.isalnum() or ch.isspace()
    ).strip()


def _parse_time_or_none(t: Optional[str]):
    if not t:
        return None
    try:
        parts = [int(x) for x in t.split(":")]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except Exception:
        return None


# ================================================================
#                      SetlistFM CLASS
# ================================================================
class SetlistFM:
    """
    Robust Setlist.fm client with two clean pathways:

    1. Normal single-artist shows
    2. Festivals (multiple artists on the same venue/date)
    """

    def __init__(self, api_key: str, verbose: bool = False):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}
        self.verbose = verbose

    # --------------------------
    # Logging wrapper
    # --------------------------
    def _log(self, *a):
        if self.verbose:
            print(*a)

    # --------------------------
    # Date format helper
    # --------------------------
    def _to_setlistfm_date(self, iso_date: str) -> str:
        dt = datetime.fromisoformat(iso_date)
        return dt.strftime("%d-%m-%Y")

    # --------------------------
    # Song extraction
    # --------------------------
    def _extract_songs(self, entry: dict) -> List[str]:
        songs = []
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

    # --------------------------
    # Normalization of a raw entry
    # --------------------------
    def _gather_basic_entry(self, entry: dict) -> Dict:
        name = (entry.get("artist", {}) or {}).get("name", "")
        songs = self._extract_songs(entry)

        # Discover earliest startTime if available
        start_time = None
        sets = entry.get("sets", {}).get("set", [])
        if isinstance(sets, list) and sets:
            for s in sets:
                st = s.get("startTime") or s.get("start")
                if st:
                    start_time = st
                    break

        # top-level fallback
        if not start_time:
            start_time = entry.get("startTime") or entry.get("start")

        stage = entry.get("venue", {}).get("stage") or entry.get("stage") or None
        last_updated = entry.get("lastUpdated") or entry.get("lastUpdatedAt") or None

        return {
            "name": name,
            "songs": songs,
            "startTime": start_time,
            "stage": stage,
            "lastUpdated": last_updated,
            "_raw": entry,
        }

    # ================================================================
    #          NEW — CLEAN FESTIVAL-MODE SETLIST FETCH
    # ================================================================
    def search_setlists_festival_mode(self, venue: str, city: str, date: str) -> Optional[List[Dict]]:
        """
        Festival-oriented setlist lookup:
        Returns list of raw setlist entries for all artists who performed
        at the same venue/city/date.
        """
        self._log(f"[INFO] FESTIVAL MODE lookup: venue='{venue}', city='{city}', date='{date}'")

        date_ddmm = self._to_setlistfm_date(date)

        try:
            resp = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"venueName": venue, "cityName": city, "date": date_ddmm},
                timeout=15,
            )
        except Exception as e:
            self._log("[WARN] Festival-mode network error:", e)
            return None

        if resp.status_code != 200:
            self._log("[WARN] Festival-mode HTTP", resp.status_code, resp.text[:200])
            return None

        entries = resp.json().get("setlist", []) or []
        self._log(f"[DEBUG] festival-mode returned {len(entries)} entries")

        return entries or None

    # ================================================================
    #               MAIN ENTRYPOINT — CLEAN VERSION
    # ================================================================
    def find_event_setlist(
        self,
        artist: str,
        venue: Optional[str],
        city: Optional[str],
        date: str,
        headliner_threshold: int = 80
    ) -> Optional[Dict]:

        self._log(f"[INFO] Searching setlists for {artist} {date} @ {venue} {city}")

        date_ddmm = self._to_setlistfm_date(date)

        # -------------------------
        # 1. Try artist+date first
        # -------------------------
        try:
            resp = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"artistName": artist, "date": date_ddmm},
                timeout=15,
            )
        except Exception as e:
            self._log("[WARN] artist-date lookup failed:", e)
            resp = None

        artist_entries = []
        if resp and resp.status_code == 200:
            artist_entries = resp.json().get("setlist", []) or []

        # find exac
