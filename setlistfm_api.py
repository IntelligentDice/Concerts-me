# setlistfm_api.py
import requests
from rapidfuzz import fuzz
from datetime import datetime
from typing import Optional, List, Dict

BASE_URL = "https://api.setlist.fm/rest/1.0"


class SetlistFM:
    """
    Approach A implementation:
    1) Query all setlists on the given date
    2) Filter by venue/city fuzzy match
    3) Extract per-artist setlists for that venue/date
    4) Identify headliner (strong fuzzy match to CSV artist or longest set)
    5) Return headliner + headliner_songs + openers[]
    """

    def __init__(self, api_key: str, verbose: bool = False):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}
        self.verbose = verbose

    def _log(self, *args):
        if self.verbose:
            print(*args)

    def _to_setlistfm_date(self, date_str: str) -> str:
        """Convert YYYY-MM-DD → DD-MM-YYYY for Setlist.fm API"""
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%d-%m-%Y")

    def _artist_name(self, entry: dict) -> str:
        return entry.get("artist", {}).get("name", "") or ""

    def _venue_name(self, entry: dict) -> str:
        return entry.get("venue", {}).get("name", "") or ""

    def _city_name(self, entry: dict) -> str:
        return entry.get("venue", {}).get("city", {}).get("name", "") or ""

    def _extract_songs_from_set(self, setlist_entry: dict) -> List[str]:
        songs: List[str] = []
        sets = setlist_entry.get("sets", {}).get("set", [])
        if not isinstance(sets, list):
            sets = [sets] if sets else []

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

    def find_event_setlist(
        self,
        artist: str,
        venue: Optional[str],
        city: Optional[str],
        date: str,
        venue_threshold: int = 70,
        city_threshold: int = 60,
        headliner_threshold: int = 85,
        opener_threshold_low: int = 50,
        opener_threshold_high: int = 85,
    ) -> Optional[Dict]:
        """
        Main function to reconstruct the full event:
        - artist, venue, city, date (YYYY-MM-DD)
        Returns:
          {
            "headliner": str,
            "headliner_songs": [...],
            "openers": [ {"name": str, "songs": [...], "_score": float}, ... ]
          }
        or None if not found.
        """

        self._log(f"[INFO] Searching full event for {artist} on {date} @ {venue}, {city}")

        date_ddmm = self._to_setlistfm_date(date)

        # Query by date only (returns all setlists for that date)
        params = {"date": date_ddmm}
        self._log("[DEBUG] Querying Setlist.fm by date:", date_ddmm)

        try:
            r = requests.get(f"{BASE_URL}/search/setlists", headers=self.headers, params=params, timeout=20)
        except Exception as e:
            self._log("[WARN] Setlist.fm request failed:", e)
            return None

        if r.status_code != 200:
            self._log(f"[WARN] Setlist.fm returned status {r.status_code}")
            return None

        all_sets = r.json().get("setlist", []) or []
        self._log(f"[DEBUG] Found {len(all_sets)} total setlists on {date_ddmm}")

        if not all_sets:
            return None

        # Filter by venue + city (if provided) using fuzzy matching
        candidates = []
        for entry in all_sets:
            ven = self._venue_name(entry)
            cit = self._city_name(entry)
            keep = True

            if venue:
                venue_score = fuzz.token_set_ratio(venue, ven) if ven else 0
                if venue_score < venue_threshold:
                    keep = False
                    self._log(f"[TRACE] Reject by venue: '{ven}' (score={venue_score})")
            if city and keep:
                city_score = fuzz.token_set_ratio(city, cit) if cit else 0
                if city_score < city_threshold:
                    keep = False
                    self._log(f"[TRACE] Reject by city: '{cit}' (score={city_score})")
            if keep:
                candidates.append(entry)

        # If venue/city filtering left us with zero candidates, relax to city or venue individually,
        # then fallback to original all_sets if still empty.
        if not candidates:
            self._log("[DEBUG] No candidates after strict venue+city filter — trying relaxed filters")
            relaxed = []
            for entry in all_sets:
                ven = self._venue_name(entry)
                cit = self._city_name(entry)
                vs = fuzz.token_set_ratio(venue, ven) if (venue and ven) else 0
                cs = fuzz.token_set_ratio(city, cit) if (city and cit) else 0
                if venue and vs >= (venue_threshold - 10):
                    relaxed.append(entry)
                    continue
                if city and cs >= (city_threshold - 10):
                    relaxed.append(entry)
                    continue
            if relaxed:
                candidates = relaxed
                self._log(f"[DEBUG] Relaxed filter yielded {len(candidates)} candidates")
            else:
                # fallback to ANY set on the date (useful if venue not specified or data missing)
                candidates = all_sets
                self._log(f"[DEBUG] Falling back to all {len(candidates)} candidates for the date")

        self._log(f"[DEBUG] Using {len(candidates)} candidate setlists for this event after filtering")

        # Build a list of unique artists from candidates with their songs
        artist_blocks: Dict[str, Dict] = {}
        for entry in candidates:
            name = self._artist_name(entry)
            if not name:
                continue
            if name.lower() in artist_blocks:
                # merge songs if we already saw this artist (possible duplicate entries)
                existing = artist_blocks[name.lower()]
                existing_songs = existing.get("songs", [])
                new_songs = self._extract_songs_from_set(entry)
                # append while preserving order, avoid duplicates
                for s in new_songs:
                    if s not in existing_songs:
                        existing_songs.append(s)
                existing["songs"] = existing_songs
            else:
                artist_blocks[name.lower()] = {
                    "name": name,
                    "songs": self._extract_songs_from_set(entry),
                    "_entry": entry
                }

        # If no artist_blocks, bail
        if not artist_blocks:
            self._log("[WARN] No artist blocks extracted from candidates")
            return None

        # Score each artist vs the provided headliner name to find headliner
        scored = []
        for key, blk in artist_blocks.items():
            band = blk["name"]
            score = fuzz.token_set_ratio(artist, band)
            self._log(f"[TRACE] Candidate band: '{band}' score={score} songs={len(blk['songs'])}")
            scored.append((score, band, blk["songs"]))

        # pick headliner by highest score >= headliner_threshold or fallback to longest set
        scored.sort(key=lambda x: x[0], reverse=True)
        headliner_band = None
        headliner_songs = []
        if scored and scored[0][0] >= headliner_threshold:
            headliner_band = scored[0][1]
            headliner_songs = scored[0][2]
            self._log(f"[DEBUG] Selected headliner by fuzzy match: {headliner_band} (score={scored[0][0]})")
        else:
            # fallback — choose the artist with the largest number of songs (most likely headliner)
            scored_by_len = sorted(scored, key=lambda x: len(x[2]), reverse=True)
            headliner_band = scored_by_len[0][1]
            headliner_songs = scored_by_len[0][2]
            self._log(f"[DEBUG] No strong fuzzy headliner; selected by longest set: {headliner_band}")

        # Build openers by taking other artists and only those with reasonable scores
        openers = []
        for score, band, songs in scored:
            if band == headliner_band:
                continue
            # require at least an opener_threshold_low to consider, and not exceed opener_threshold_high
            if opener_threshold_low <= score < opener_threshold_high or (songs and score >= 30):
                openers.append({"name": band, "songs": songs, "_score": score})
                self._log(f"[DEBUG] Added opener candidate: {band} score={score} songs={len(songs)}")
            else:
                self._log(f"[TRACE] Ignored band {band} score={score} (not an opener)")

        # dedupe openers by name-preserving order
        seen = set()
        deduped_openers = []
        for o in openers:
            key = (o["name"] or "").lower()
            if key and key not in seen:
                deduped_openers.append({"name": o["name"], "songs": o["songs"]})
                seen.add(key)

        self._log(f"[DEBUG] FINAL HEADLINER: {headliner_band} (songs={len(headliner_songs)})")
        self._log(f"[DEBUG] FINAL OPENERS: {[ (o['name'], len(o['songs'])) for o in deduped_openers ]}")

        return {
            "headliner": headliner_band,
            "headliner_songs": headliner_songs,
            "openers": deduped_openers
        }
