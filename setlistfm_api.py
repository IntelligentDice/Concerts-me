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


class SetlistFM:
    """
    Setlist.fm client with:
    - Festival mode support
    - Normal event extraction
    - Fuzzy headliner matching
    """

    def __init__(self, api_key: str, verbose: bool = False):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}
        self.verbose = verbose

    def _log(self, *a):
        if self.verbose:
            print(*a)

    def _to_setlistfm_date(self, iso_date: str) -> str:
        # input YYYY-MM-DD -> DD-MM-YYYY
        dt = datetime.fromisoformat(iso_date)
        return dt.strftime("%d-%m-%Y")

    # ---------------------------------------------------------
    # SONG EXTRACTION
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # CRITICAL: FIXED NORMALIZATION OF ARTIST ENTRIES
    # ---------------------------------------------------------
    def _gather_basic_entry(self, entry: dict) -> Dict:
        """
        Normalize a setlist entry into a simple dict:
        { name, songs, startTime, stage, lastUpdated }
        """

        # --- FIX: always extract artist name as string ---
        artist_block = entry.get("artist") or {}
        name = artist_block.get("name") or ""
        if not isinstance(name, str):
            name = ""
        name = name.strip()

        songs = self._extract_songs(entry)

        # Extract start time
        start_time = None
        sets = entry.get("sets", {}).get("set", [])
        if isinstance(sets, list):
            for s in sets:
                st = s.get("startTime") or s.get("start")
                if st:
                    start_time = st
                    break

        if not start_time:
            start_time = entry.get("startTime") or entry.get("start")

        # Extract stage
        stage = (
            entry.get("stage")
            or entry.get("venue", {}).get("stage")
            or None
        )

        last_updated = entry.get("lastUpdated") or entry.get("lastUpdatedAt")

        return {
            "name": name,
            "songs": songs,
            "startTime": start_time,
            "stage": stage,
            "lastUpdated": last_updated,
            "_raw": entry,
        }

    # ---------------------------------------------------------
    # FESTIVAL-MODE DIRECT QUERY
    # ---------------------------------------------------------
    def search_setlists_festival_mode(self, venue: str, city: str, date: str) -> List[Dict]:
        date_ddmm = self._to_setlistfm_date(date)

        resp = requests.get(
            f"{BASE_URL}/search/setlists",
            headers=self.headers,
            params={"venueName": venue, "cityName": city, "date": date_ddmm},
            timeout=15,
        )

        if resp.status_code != 200:
            self._log(f"[WARN] SetlistFM GET festival-mode failed ({resp.status_code}) {resp.text[:200]}")
            return []

        raw = resp.json().get("setlist", []) or []
        results = []

        for entry in raw:
            results.append(self._gather_basic_entry(entry))

        return results

    # ---------------------------------------------------------
    # MAIN ENTRYPOINT: FIND EVENT SETLIST
    # ---------------------------------------------------------
    def find_event_setlist(
        self,
        artist: str,
        venue: Optional[str],
        city: Optional[str],
        date: str,
        headliner_threshold: int = 80,
    ) -> Optional[Dict]:

        self._log(f"[INFO] Searching setlists for {artist} {date} @ {venue} {city}")

        date_ddmm = self._to_setlistfm_date(date)

        # ---------------------------------------------------------
        # 1) Search by artist + date
        # ---------------------------------------------------------
        resp = requests.get(
            f"{BASE_URL}/search/setlists",
            headers=self.headers,
            params={"artistName": artist, "date": date_ddmm},
            timeout=15,
        )

        if resp.status_code != 200:
            self._log(f"[WARN] SetlistFM GET failed ({resp.status_code}) ***{resp.text[:200]}")
            return None

        artist_sets = resp.json().get("setlist", []) or []
        headliner_entry = None

        for e in artist_sets:
            if e.get("eventDate") == date_ddmm:
                headliner_entry = e
                break

        # ---------------------------------------------------------
        # 2) If no artist entry found → fallback to venue+city+date
        # ---------------------------------------------------------
        if not headliner_entry:
            resp2 = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"venueName": venue, "cityName": city, "date": date_ddmm},
                timeout=15,
            )

            if resp2.status_code != 200:
                self._log(f"[WARN] SetlistFM GET failed ({resp2.status_code}) ***{resp2.text[:200]}")
                return None

            venue_sets = resp2.json().get("setlist", []) or []

            # Festival condition
            if len(venue_sets) >= 3:
                lineup = [self._gather_basic_entry(e) for e in venue_sets]

                # Dedupe
                seen = set()
                deduped = []
                for b in lineup:
                    key = b["name"].lower()
                    if key not in seen and b["name"]:
                        seen.add(key)
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

        # ---------------------------------------------------------
        # 3) Now fetch full venue+date entries (normal or festival)
        # ---------------------------------------------------------
        ev = headliner_entry.get("venue", {})
        resolved_venue = ev.get("name") or venue or ""
        resolved_city = ev.get("city", {}).get("name") or city or ""

        resp3 = requests.get(
            f"{BASE_URL}/search/setlists",
            headers=self.headers,
            params={
                "venueName": resolved_venue,
                "cityName": resolved_city,
                "date": date_ddmm,
            },
            timeout=15,
        )

        if resp3.status_code != 200:
            self._log(f"[WARN] SetlistFM GET failed ({resp3.status_code}) ***{resp3.text[:200]}")
            return None

        all_entries = resp3.json().get("setlist", []) or []

        # Festival if >= 3 artists
        if len(all_entries) >= 3:
            lineup = [self._gather_basic_entry(e) for e in all_entries]

            seen = set()
            deduped = []
            for b in lineup:
                key = b["name"].lower()
                if key not in seen and b["name"]:
                    seen.add(key)
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

        # ---------------------------------------------------------
        # 4) NORMAL EVENT: EXTRACT OPENERS + HEADLINER
        # ---------------------------------------------------------
        artists_map: Dict[str, Dict] = {}

        for e in all_entries:
            nm = (e.get("artist") or {}).get("name") or ""
            nm = nm.strip()
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

            songs = self._extract_songs(e)
            for s in songs:
                if s not in artists_map[key]["songs"]:
                    artists_map[key]["songs"].append(s)

            sets = e.get("sets", {}).get("set", [])
            if not isinstance(sets, list):
                sets = [sets]

            for s in sets:
                st = s.get("startTime") or s.get("start")
                if st and not artists_map[key]["startTime"]:
                    artists_map[key]["startTime"] = st

            lu = e.get("lastUpdated") or e.get("lastUpdatedAt")
            if lu and not artists_map[key]["lastUpdated"]:
                artists_map[key]["lastUpdated"] = lu

        # ---------------------------------------------------------
        # 5) FIXED — FUZZY HEADLINER MATCHING
        # ---------------------------------------------------------
        scored = []
        target_norm = _norm_text(artist)

        for _, v in artists_map.items():
            nm = v["name"]
            score = fuzz.token_set_ratio(target_norm, _norm_text(nm))
            scored.append((score, nm, v))

        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            self._log(f"[WARN] No valid artists found for {artist} on {date}")
            return None

        top_score, top_name, top_rec = scored[0]

        if top_score >= headliner_threshold:
            headliner_name = top_name
            headliner_songs = top_rec["songs"]
        else:
            fallback = max(scored, key=lambda x: len(x[2]["songs"]))
            headliner_name = fallback[1]
            headliner_songs = fallback[2]["songs"]

        # Openers
        openers = []
        for score, nm, rec in scored:
            if nm == headliner_name:
                continue
            openers.append(rec)

        return {
            "is_festival": False,
            "headliner": headliner_name,
            "headliner_songs": headliner_songs,
            "openers": openers,
            "venue": resolved_venue,
            "city": resolved_city,
            "date": date,
        }
