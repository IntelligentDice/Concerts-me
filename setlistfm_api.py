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
    Strict Approach A implementation:
    - Query setlists by date (DD-MM-YYYY)
    - Filter by strict venue + city fuzzy matches (configurable thresholds)
    - Return the artists (openers + headliner) that actually played at that venue/date
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

    def find_event_setlist(
        self,
        artist: str,
        venue: Optional[str],
        city: Optional[str],
        date: str,
        venue_threshold: int = 75,
        city_threshold: int = 80,
        headliner_threshold: int = 85,
    ) -> Optional[Dict]:
        """
        Return { headliner, headliner_songs, openers: [{name,songs}] } or None.
        Strict filters: same date, venue fuzzy >= venue_threshold, city fuzzy >= city_threshold.
        """
        self._log("[INFO] Searching setlists for", artist, date, "@", venue, city)
        # date for API
        date_ddmm = self._to_setlistfm_date(date)

        # Query all setlists for that date (this returns all events worldwide on date)
        try:
            resp = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"date": date_ddmm},
                timeout=20,
            )
        except Exception as e:
            self._log("[WARN] Setlist.fm network error:", e)
            return None

        if resp.status_code != 200:
            self._log("[WARN] Setlist.fm returned status", resp.status_code, resp.text[:200])
            return None

        all_sets = resp.json().get("setlist", []) or []
        self._log(f"[DEBUG] total setlists on {date_ddmm}: {len(all_sets)}")

        if not all_sets:
            return None

        # normalize search terms for comparison
        norm_target_venue = _norm_text(venue) if venue else ""
        norm_target_city = _norm_text(city) if city else ""
        norm_target_artist = _norm_text(artist)

        # Filter to candidate entries matching BOTH venue and city strictly
        candidates = []
        for e in all_sets:
            ev = (e.get("venue", {}) or {}).get("name", "")
            ec = (e.get("venue", {}) or {}).get("city", {}).get("name", "")
            if not ev or not ec:
                continue

            # Compute fuzzy scores
            ven_score = fuzz.token_set_ratio(norm_target_venue, _norm_text(ev)) if norm_target_venue else 0
            city_score = fuzz.token_set_ratio(norm_target_city, _norm_text(ec)) if norm_target_city else 0

            # Emit detailed scoring per candidate
            self._log(f"[DEBUG] Candidate event: {e.get('artist',{}).get('name')} @ {ev}, {ec}")
            self._log(f"[DEBUG]   Scores => venu_

        # If we have zero candidates, abort (Option A demands strictness)
        if not candidates:
            self._log("[WARN] No candidates matched strict venue+city filter")
            return None

        # Build a map of artist -> aggregate songs (some artists may appear multiple times)
        artists_map: Dict[str, Dict] = {}
        for c in candidates:
            name = (c.get("artist", {}) or {}).get("name", "")
            if not name:
                continue
            key = name.lower()
            if key not in artists_map:
                artists_map[key] = {"name": name, "songs": []}
            # append unique songs preserving order
            songs = self._extract_songs(c)
            for s in songs:
                if s not in artists_map[key]["songs"]:
                    artists_map[key]["songs"].append(s)

        if not artists_map:
            self._log("[WARN] after candidates there are no artist setlists extracted")
            return None

        # Score artists against provided headliner to pick headliner
        scored = []
        for k, v in artists_map.items():
            name = v["name"]
            score = fuzz.token_set_ratio(norm_target_artist, _norm_text(name))
            scored.append((score, name, v["songs"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        # require strong headliner match
        if scored and scored[0][0] >= headliner_threshold:
            headliner_name = scored[0][1]
            headliner_songs = scored[0][2]
            self._log(f"[DEBUG] headliner selected by fuzzy: {headliner_name} (score={scored[0][0]})")
        else:
            # fallback: artist with longest songs list
            scored_by_len = sorted(scored, key=lambda x: len(x[2]), reverse=True)
            headliner_name = scored_by_len[0][1]
            headliner_songs = scored_by_len[0][2]
            self._log(f"[DEBUG] headliner selected by longest set: {headliner_name}")

        # openers are the other artists
        openers = []
        for s in scored:
            if s[1] == headliner_name:
                continue
            openers.append({"name": s[1], "songs": s[2]})

        self._log(f"[DEBUG] Final headliner: {headliner_name} songs={len(headliner_songs)}")
        self._log(f"[DEBUG] Final openers: {[ (o['name'], len(o['songs'])) for o in openers ]}")

        return {"headliner": headliner_name, "headliner_songs": headliner_songs, "openers": openers}
