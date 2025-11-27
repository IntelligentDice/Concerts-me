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
        # expected "HH:MM" or "HH:MM:ss"
        parts = t.split(":")
        parts = [int(p) for p in parts]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except Exception:
        return None


class SetlistFM:
    """
    Minimal robust Setlist.fm client:
    - find_event_setlist(...) returns either a normal event dict or a festival dict
    - festival detection triggers a 'is_festival': True response with 'lineup' list
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
        """
        Normalize a setlist entry into a simple dict:
        { name, songs, startTime, stage, lastUpdated }
        """
        name = (entry.get("artist", {}) or {}).get("name", "")
        songs = self._extract_songs(entry)
        # try different possible fields for startTime / stage / lastUpdated
        # Setlist.fm sometimes puts meta in the set blocks, but top-level is common.
        start_time = None
        sets = entry.get("sets", {}).get("set", [])
        if isinstance(sets, list) and sets:
            # prefer the earliest non-empty startTime found in set blocks
            for s in sets:
                st = s.get("startTime") or s.get("start")
                if st:
                    start_time = st
                    break
        # also check top-level
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
            # keep original entry for debugging if needed
            "_raw": entry,
        }

    def find_event_setlist(self, artist: str, venue: Optional[str], city: Optional[str], date: str,
                           venue_threshold: int = 60, city_threshold: int = 60, headliner_threshold: int = 80) -> Optional[Dict]:
        """
        Primary entrypoint used by PlaylistBuilder.

        Returns either:
        - normal event:
            {
                "is_festival": False,
                "headliner": <name>,
                "headliner_songs": [...],
                "openers": [{name,songs,startTime,lastUpdated}, ...],
                "venue": <venue>,
                "city": <city>,
                "date": <YYYY-MM-DD>
            }

        - festival:
            {
                "is_festival": True,
                "festival_name": <artist from sheet>,
                "event_name": <event_name from sheet (if available)>,
                "venue": <venue>,
                "city": <city>,
                "date": <YYYY-MM-DD>,
                "lineup": [{name,songs,startTime,stage,lastUpdated,_raw}, ...]
            }
        """

        self._log("[INFO] Searching setlists for", artist, date, "@", venue, city)
        date_ddmm = self._to_setlistfm_date(date)

        # 1) First, try to find setlists by artist+date (fast path)
        try:
            resp = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"artistName": artist, "date": date_ddmm},
                timeout=15,
            )
        except Exception as e:
            self._log("[WARN] Network error during artist search:", e)
            return None

        if resp.status_code != 200:
            self._log("[WARN] Setlist.fm returned", resp.status_code, resp.text[:200])
            # fall through to broader date search? For strictness, return None
            return None

        artist_sets = resp.json().get("setlist", []) or []
        self._log(f"[DEBUG] total setlists returned for artist search: {len(artist_sets)}")

        # Must match the same date for a headliner entry
        headliner_entry = None
        for e in artist_sets:
            if e.get("eventDate") == date_ddmm:
                headliner_entry = e
                break

        # If no headliner entry, try a date+venue+city query to capture festival lineups
        if not headliner_entry:
            # As an alternate, query by venue+city+date (may return all bands for that event)
            try:
                resp2 = requests.get(
                    f"{BASE_URL}/search/setlists",
                    headers=self.headers,
                    params={"venueName": venue, "cityName": city, "date": date_ddmm},
                    timeout=15,
                )
            except Exception as e:
                self._log("[WARN] Network error during venue/date search:", e)
                return None

            if resp2.status_code != 200:
                self._log("[WARN] Setlist.fm returned", resp2.status_code, resp2.text[:200])
                return None

            venue_sets = resp2.json().get("setlist", []) or []
            self._log(f"[DEBUG] total setlists returned for venue+date search: {len(venue_sets)}")

            # If venue_sets looks like many artists for the same venue+date, treat it as festival-mode
            if len(venue_sets) >= 3:
                # Build festival lineup from venue_sets
                lineup = []
                for e in venue_sets:
                    # sometimes multiple entries represent the same artist across different stages;
                    # normalize using _gather_basic_entry
                    lineup.append(self._gather_basic_entry(e))
                # deduplicate by artist name preserving order
                seen = set()
                deduped = []
                for b in lineup:
                    nm = (b.get("name") or "").strip()
                    if not nm:
                        continue
                    key = nm.lower()
                    if key in seen:
                        continue
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

            # Not many entries => no match
            return None

        # If we found a headliner entry — extract its venue/city and decide if this is a festival
        ev = headliner_entry.get("venue", {})
        resolved_venue = ev.get("name", "") or venue or ""
        resolved_city = ev.get("city", {}).get("name", "") or city or ""

        # If the headliner entry claims to be a festival (eventType) or the searched 'artist' looks like a festival name,
        # we switch into festival mode and fetch all setlists for that venue+date.
        event_type = headliner_entry.get("eventType", "") or ""
        maybe_festival = (str(event_type).lower() == "festival")

        # Also if the artist we searched (sheet) is not present in the setlists returned for the venue+date, consider festival
        # (we will fetch venue entries next and examine)
        try:
            resp3 = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"venueName": resolved_venue, "cityName": resolved_city, "date": date_ddmm},
                timeout=15,
            )
        except Exception as e:
            self._log("[WARN] Network error during final venue+date search:", e)
            return None

        if resp3.status_code != 200:
            self._log("[WARN] Setlist.fm returned", resp3.status_code, resp3.text[:200])
            return None

        all_entries = resp3.json().get("setlist", []) or []
        self._log(f"[DEBUG] opener+headliner search returned {len(all_entries)} entries")

        # If the top-level eventType suggests festival OR many entries exist, treat as festival
        if maybe_festival or len(all_entries) >= 3:
            # Build festival lineup for this date
            lineup = []
            for e in all_entries:
                lineup.append(self._gather_basic_entry(e))
            # dedupe preserving order
            seen = set()
            deduped = []
            for b in lineup:
                nm = (b.get("name") or "").strip()
                if not nm:
                    continue
                key = nm.lower()
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(b)

            return {
                "is_festival": True,
                "festival_name": artist,  # the sheet's 'artist' is festival title
                "event_name": None,
                "venue": resolved_venue,
                "city": resolved_city,
                "date": date,
                "lineup": deduped,
            }

        # Otherwise: normal single-event flow
        # Build openers by scanning all_entries for other artists at the same event (same venue+date)
        # Build a map artist -> songs (aggregate across entries)
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
            # collect startTime / lastUpdated if available (take the earliest found)
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

        # Score artists against provided headliner to pick headliner
        scored = []
        norm_target_artist = _norm_text(artist)
        for k, v in artists_map.items():
            name = v["name"]
            score = fuzz.token_set_ratio(norm_target_artist, _norm_text(name))
            scored.append((score, name, v))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Decide headliner
        if scored and scored[0][0] >= headliner_threshold:
            headliner_name = scored[0][1]
            headliner_songs = scored[0][2]["songs"]
            self._log(f"[DEBUG] headliner selected by fuzzy: {headliner_name} (score={scored[0][0]})")
        else:
            # fallback: artist with longest songs list
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
