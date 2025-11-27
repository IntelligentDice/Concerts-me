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
        date: str,
        venue_threshold: int = 55,
        city_threshold: int = 60,
        headliner_threshold: int = 80,
    ) -> Optional[Dict]:
        """
        Reconstruct the full event for the given artist/venue/city/date.

        Returns:
            {
              "headliner": str,
              "headliner_songs": [...],
              "openers": [{"name": "...", "songs": [...]}, ...]
            }
        or None if no matching event found.
        """

        # Basic info
        self._log(f"[INFO] Searching setlists for artist={artist!r}, date={date!r}, venue={venue!r}, city={city!r}")

        # Convert date to Setlist.fm format
        try:
            date_ddmm = self._to_setlistfm_date(date)
        except Exception as e:
            self._log(f"[ERROR] Invalid date format for {date}: {e}")
            return None

        # Query all setlists for that date
        try:
            resp = requests.get(
                f"{BASE_URL}/search/setlists",
                headers=self.headers,
                params={"date": date_ddmm},
                timeout=20,
            )
        except Exception as e:
            self._log(f"[WARN] Setlist.fm request failed: {e}")
            return None

        if resp.status_code != 200:
            self._log(f"[WARN] Setlist.fm returned status {resp.status_code}: {resp.text[:300]}")
            return None

        all_sets = resp.json().get("setlist", []) or []
        self._log(f"[DEBUG] total setlists returned for date {date_ddmm}: {len(all_sets)}")

        if not all_sets:
            return None

        # Normalize search terms
        norm_target_venue = _norm_text(venue) if venue else ""
        norm_target_city = _norm_text(city) if city else ""
        norm_target_artist = _norm_text(artist)

        # Collect candidates that match venue+city thresholds
        candidates: List[dict] = []
        for e in all_sets:
            ev = (e.get("venue", {}) or {}).get("name", "") or ""
            ec = (e.get("venue", {}) or {}).get("city", {}).get("name", "") or ""

            # skip incomplete entries
            if not ev or not ec:
                self._log(f"[TRACE] Skipping entry with missing venue/city: artist={e.get('artist',{}).get('name')!r}")
                continue

            # compute fuzzy scores (compare normalized strings)
            ven_score = fuzz.token_set_ratio(norm_target_venue, _norm_text(ev)) if norm_target_venue else 0
            city_score = fuzz.token_set_ratio(norm_target_city, _norm_text(ec)) if norm_target_city else 0

            # emit debug per-candidate
            self._log(f"[DEBUG] Candidate event: artist={e.get('artist',{}).get('name')!r} @ venue={ev!r}, city={ec!r}")
            self._log(
                f"[DEBUG]   Scores => venue:{ven_score}  city:{city_score}  "
                f"thresholds => venue:{venue_threshold}  city:{city_threshold}"
            )

            # Strict matching requires both venue and city thresholds when both provided
            if norm_target_venue and norm_target_city:
                if ven_score >= venue_threshold and city_score >= city_threshold:
                    self._log(f"[DEBUG]   ✅ Candidate accepted (venue & city match).")
                    candidates.append(e)
                else:
                    self._log(f"[TRACE]   ❌ Candidate rejected (venue/city below thresholds).")
                    continue
            elif norm_target_venue:
                if ven_score >= venue_threshold:
                    self._log(f"[DEBUG]   ✅ Candidate accepted (venue match).")
                    candidates.append(e)
                else:
                    self._log(f"[TRACE]   ❌ Candidate rejected (venue below threshold).")
                    continue
            elif norm_target_city:
                if city_score >= city_threshold:
                    self._log(f"[DEBUG]   ✅ Candidate accepted (city match).")
                    candidates.append(e)
                else:
                    self._log(f"[TRACE]   ❌ Candidate rejected (city below threshold).")
                    continue
            else:
                # No venue or city supplied: we must not accept global results in strict mode
                self._log("[WARN] No venue or city provided; strict mode requires venue or city.")
                return None

        self._log(f"[DEBUG] candidates after filtering: {len(candidates)}")
        if not candidates:
            self._log("[WARN] No candidates matched strict venue+city filter")
            return None

        # Aggregate artists -> songs across the selected candidates (some artists may appear multiple times)
        artists_map: Dict[str, Dict] = {}
        for c in candidates:
            name = (c.get("artist", {}) or {}).get("name", "")
            if not name:
                continue
            key = name.lower()
            if key not in artists_map:
                artists_map[key] = {"name": name, "songs": []}
            songs = self._extract_songs(c)
            for s in songs:
                if s not in artists_map[key]["songs"]:
                    artists_map[key]["songs"].append(s)

        if not artists_map:
            self._log("[WARN] No artist setlists extracted from candidates")
            return None

        # Score artists against the requested headliner string to pick the headliner
        scored: List[tuple] = []
        for k, v in artists_map.items():
            name = v["name"]
            score = fuzz.token_set_ratio(norm_target_artist, _norm_text(name))
            scored.append((score, name, v["songs"]))
            self._log(f"[TRACE] Artist candidate: {name!r} score_vs_requested_artist={score} songs={len(v['songs'])}")

        # Pick headliner: prefer strong fuzzy match; else fallback to longest set
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= headliner_threshold:
            headliner_name = scored[0][1]
            headliner_songs = scored[0][2]
            self._log(f"[DEBUG] Headliner selected by fuzzy match: {headliner_name} (score={scored[0][0]})")
        else:
            # fallback: choose the artist with most songs (likely headliner)
            scored_by_len = sorted(scored, key=lambda x: len(x[2]), reverse=True)
            headliner_name = scored_by_len[0][1]
            headliner_songs = scored_by_len[0][2]
            self._log(f"[DEBUG] Headliner selected by longest-set fallback: {headliner_name}")

        # Build openers list (all other artists)
        openers: List[Dict] = []
        for score, name, songs in scored:
            if name == headliner_name:
                continue
            openers.append({"name": name, "songs": songs})

        self._log(f"[DEBUG] Final headliner: {headliner_name} (songs={len(headliner_songs)})")
        self._log(f"[DEBUG] Final openers: {[ (o['name'], len(o['songs'])) for o in openers ]}")

        return {"headliner": headliner_name, "headliner_songs": headliner_songs, "openers": openers}
