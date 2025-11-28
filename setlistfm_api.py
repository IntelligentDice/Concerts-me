# setlistfm_api.py
import requests
from rapidfuzz import fuzz
from datetime import datetime
from typing import Optional, List, Dict

BASE_URL = "https://api.setlist.fm/rest/1.0"


def _norm_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return "".join(ch for ch in s.lower().strip() if ch.isalnum() or ch.isspace()).strip()


def _parse_time_or_none(t: Optional[str]):
    if not t:
        return None
    try:
        parts = t.split(":")
        parts = [int(p) for p in parts]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except Exception:
        return None


class SetlistFM:
    """
    Minimal, robust Setlist.fm client.

    Public methods:
      - find_event_setlist(artist, venue, city, date, ...)
      - search_setlists_festival_mode(venue, city, date)

    NOTE: This class intentionally does not auto-enable festival mode. Festival handling
    is controlled by the sheet boolean `is_festival` or by calling search_setlists_festival_mode directly.
    """

    def __init__(self, api_key: str, verbose: bool = False):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}
        self.verbose = verbose

    def _log(self, *a):
        if self.verbose:
            print(*a)

    def _to_setlistfm_date(self, iso_date: str) -> str:
        # input YYYY-MM-DD -> output DD-MM-YYYY
        dt = datetime.fromisoformat(iso_date)
        return dt.strftime("%d-%m-%Y")

    def _extract_songs(self, entry: dict) -> List[str]:
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

    def _gather_basic_entry(self, entry: dict) -> Dict:
        """Normalize a setlist entry into {name,songs,startTime,stage,lastUpdated,_raw}"""
        name = (entry.get("artist", {}) or {}).get("name", "")
        songs = self._extract_songs(entry)

        start_time = None
        sets = entry.get("sets", {}).get("set", [])
        if isinstance(sets, list) and sets:
            for s in sets:
                st = s.get("startTime") or s.get("start")
                if st:
                    start_time = st
                    break
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

    # ------------------------------------------------------------------
    # Festival helper: fetch setlists for a venue+city+date (useful for festival rows)
    # ------------------------------------------------------------------
    def search_setlists_festival_mode(self, venue: str, city: str, date: str) -> List[Dict]:
        """Return list of normalized basic entries for the specified venue+city+date.

        This is deliberately small and predictable: it only queries Setlist.fm for
        venueName + cityName + date and returns the normalized entries.
        """
        date_ddmm = self._to_setlistfm_date(date)
        try:
            resp = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"venueName": venue, "cityName": city, "date": date_ddmm},
                timeout=20,
            )
        except Exception as e:
            self._log("[WARN] Setlist.fm festival search network error:", e)
            return []

        if resp.status_code != 200:
            self._log("[WARN] Setlist.fm returned", resp.status_code, resp.text[:200])
            return []

        raw = resp.json().get("setlist", []) or []
        out = [self._gather_basic_entry(e) for e in raw]
        return out

    # ------------------------------------------------------------------
    # Main entrypoint used by PlaylistBuilder
    # ------------------------------------------------------------------
    def find_event_setlist(
        self,
        artist: str,
        venue: Optional[str],
        city: Optional[str],
        date: str,
        venue_threshold: int = 60,
        city_threshold: int = 60,
        headliner_threshold: int = 80,
    ) -> Optional[Dict]:
        """
        Two-step approach (artist-first):

        1) Query Setlist.fm by artistName + date to find the headliner entry.
        2) If found, resolve venue/city from that entry and query venueName+cityName+date
           to gather all entries (openers + headliner).

        Returns dict (non-festival):
          { is_festival: False, headliner, headliner_songs, openers: [{name,songs,startTime,lastUpdated}], venue, city, date }

        Returns None if no usable match.
        """
        self._log("[INFO] Searching setlists for", artist, date, "@", venue, city)
        date_ddmm = self._to_setlistfm_date(date)

        # 1) Find headliner via artist search
        try:
            resp = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"artistName": artist, "date": date_ddmm},
                timeout=15,
            )
        except Exception as e:
            self._log("[WARN] Setlist.fm network error (artist search):", e)
            return None

        if resp.status_code != 200:
            self._log("[WARN] Setlist.fm returned", resp.status_code, resp.text[:200])
            return None

        artist_sets = resp.json().get("setlist", []) or []
        self._log(f"[DEBUG] total setlists returned for artist search: {len(artist_sets)}")

        headliner_entry = None
        for e in artist_sets:
            if e.get("eventDate") == date_ddmm:
                headliner_entry = e
                break

        if not headliner_entry:
            self._log("[WARN] No headliner setlist found for exact date (artist-first)")
            return None

        # Resolve venue/city from the headliner entry
        ev = headliner_entry.get("venue", {})
        resolved_venue = ev.get("name", "") or venue or ""
        resolved_city = ev.get("city", {}).get("name", "") or city or ""

        self._log(f"[INFO] Matched eventId={headliner_entry.get('id')}, venue='{resolved_venue}', city='{resolved_city}'")

        # Fetch all entries for the resolved venue+city+date
        try:
            resp2 = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"venueName": resolved_venue, "cityName": resolved_city, "date": date_ddmm},
                timeout=15,
            )
        except Exception as e:
            self._log("[WARN] Setlist.fm network error (venue+date search):", e)
            return None

        if resp2.status_code != 200:
            self._log("[WARN] Setlist.fm returned", resp2.status_code, resp2.text[:200])
            return None

        all_entries = resp2.json().get("setlist", []) or []
        self._log(f"[DEBUG] opener+headliner search returned {len(all_entries)} entries")

        # Build artists map
        artists_map: Dict[str, Dict] = {}
        for c in all_entries:
            nm = (c.get("artist", {}) or {}).get("name", "")
            if not nm:
                continue
            key = nm.lower()
            if key not in artists_map:
                artists_map[key] = {"name": nm, "songs": [], "startTime": None, "lastUpdated": None}
            songs = self._extract_songs(c)
            for s in songs:
                if s not in artists_map[key]["songs"]:
                    artists_map[key]["songs"].append(s)
            sets = c.get("sets", {}).get("set", [])
            if not isinstance(sets, list):
                sets = [sets] if sets else []
            for s in sets:
                st = s.get("startTime") or s.get("start")
                if st and not artists_map[key]["startTime"]:
                    artists_map[key]["startTime"] = st
            lu = c.get("lastUpdated") or c.get("lastUpdatedAt")
            if lu and not artists_map[key]["lastUpdated"]:
                artists_map[key]["lastUpdated"] = lu

        if not artists_map:
            self._log("[WARN] after venue query no artist setlists were extracted")
            return None

        # Score artists against provided headliner to pick headliner
        scored = []
        norm_target_artist = _norm_text(artist)
        for k, v in artists_map.items():
            name = v["name"]
            score = fuzz.token_set_ratio(norm_target_artist, _norm_text(name))
            scored.append((score, name, v))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Decide headliner using threshold fallback
        if scored and scored[0][0] >= headliner_threshold:
            headliner_name = scored[0][1]
            headliner_songs = scored[0][2]["songs"]
            self._log(f"[DEBUG] headliner selected by fuzzy: {headliner_name} (score={scored[0][0]})")
        else:
            scored_by_len = sorted(scored, key=lambda x: len(x[2]["songs"]), reverse=True)
            if not scored_by_len:
                return None
            headliner_name = scored_by_len[0][1]
            headliner_songs = scored_by_len[0][2]["songs"]
            self._log(f"[DEBUG] headliner selected by longest set: {headliner_name}")

        # Collect openers (other artists)
        openers = []
        for s in scored:
            nm = s[1]
            if nm == headliner_name:
                continue
            rec = s[2]
            openers.append({
                "name": rec["name"],
                "songs": rec["songs"],
                "startTime": rec.get("startTime"),
                "lastUpdated": rec.get("lastUpdated"),
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
