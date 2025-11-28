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

        # find exact date match
        headliner_entry = None
        for e in artist_entries:
            if e.get("eventDate") == date_ddmm:
                headliner_entry = e
                break

        # -------------------------
        # 2. If no headliner found → FESTIVAL MODE
        # -------------------------
        if not headliner_entry:
            festival_entries = self.search_setlists_festival_mode(venue, city, date)
            if festival_entries and len(festival_entries) >= 3:
                lineup = []
                for e in festival_entries:
                    lineup.append(self._gather_basic_entry(e))

                # dedupe by artist name
                seen = set()
                deduped = []
                for b in lineup:
                    nm = (b.get("name") or "").strip().lower()
                    if nm and nm not in seen:
                        seen.add(nm)
                        deduped.append(b)

                return {
                    "is_festival": True,
                    "festival_name": artist,
                    "event_name": None,
                    "venue": venue,
                    "city": city,
                    "date": date,
                    "lineup": deduped,
                }

            return None

        # -------------------------
        # 3. Normal show: use venue+date query to find full lineup
        # -------------------------
        resolved_venue = headliner_entry.get("venue", {}).get("name", "") or venue
        resolved_city = headliner_entry.get("venue", {}).get("city", {}).get("name", "") or city

        festival_entries = self.search_setlists_festival_mode(resolved_venue, resolved_city, date)

        if not festival_entries:
            return None

        # If 3+ entries → still a festival, even though the artist was found
        if len(festival_entries) >= 3:
            lineup = []
            for e in festival_entries:
                lineup.append(self._gather_basic_entry(e))

            seen = set()
            deduped = []
            for b in lineup:
                nm = (b.get("name") or "").strip().lower()
                if nm and nm not in seen:
                    seen.add(nm)
                    deduped.append(b)

            return {
                "is_festival": True,
                "festival_name": artist,
                "event_name": None,
                "venue": resolved_venue,
                "city": resolved_city,
                "date": date,
                "lineup": deduped,
            }

        # -------------------------
        # 4. NOT festival → Single event logic
        # -------------------------
        # Build map: artist → songs, meta
        artists_map = {}
        for entry in festival_entries:
            nm = entry.get("artist", {}).get("name", "")
            if not nm:
                continue
            key = nm.lower()

            if key not in artists_map:
                artists_map[key] = {
                    "name": nm,
                    "songs": [],
                    "startTime": None,
                    "lastUpdated": None,
                }

            # songs
            songs = self._extract_songs(entry)
            for s in songs:
                if s not in artists_map[key]["songs"]:
                    artists_map[key]["songs"].append(s)

            # metadata
            sets = entry.get("sets", {}).get("set", [])
            if not isinstance(sets, list):
                sets = [sets] if sets else []
            for s in sets:
                st = s.get("startTime") or s.get("start")
                if st and not artists_map[key]["startTime"]:
                    artists_map[key]["startTime"] = st
            lu = entry.get("lastUpdated") or entry.get("lastUpdatedAt")
            if lu and not artists_map[key]["lastUpdated"]:
                artists_map[key]["lastUpdated"] = lu

        # Determine headliner
        norm_target = _norm_text(artist)
        scored = []
        for rec in artists_map.values():
            score = fuzz.token_set_ratio(norm_target, _norm_text(rec["name"]))
            scored.append((score, rec["name"], rec))

        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            return None

        if scored[0][0] >= headliner_threshold:
            headliner_name = scored[0][1]
            headliner_songs = scored[0][2]["songs"]
        else:
            # fallback: longest set
            scored.sort(key=lambda x: len(x[2]["songs"]), reverse=True)
            headliner_name = scored[0][1]
            headliner_songs = scored[0][2]["songs"]

        # Collect openers
        openers = []
        for sc, nm, rec in scored:
            if nm != headliner_name:
                openers.append({
                    "name": rec["name"],
                    "songs": rec["songs"],
                    "startTime": rec["startTime"],
                    "lastUpdated": rec["lastUpdated"],
                })

        return {
            "is_festival": False,
            "headliner": headliner_name,
            "headliner_songs": headliner_songs,
            "openers": openers,
            "venue": resolved_venue,
            "city": resolved_city,
            "date": date,
        }
