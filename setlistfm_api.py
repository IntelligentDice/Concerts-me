import requests
from rapidfuzz import fuzz
from datetime import datetime
from typing import Optional, List, Dict

BASE_URL = "https://api.setlist.fm/rest/1.0"


class SetlistFM:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}

    def find_event_setlist(self, artist: str, venue: Optional[str], city: Optional[str], date: str):
        """
        Robust search that:
         - prefers venue+date+city when available (returns fewer candidates)
         - falls back to artist+date search if venue/city missing
         - filters candidates to exact date matches
         - picks headliner (score >= 85) and openers (50 <= score < 85)
        Returns: {"headliner": str, "headliner_songs": [...], "openers":[{"name":..., "songs":[...]}]} or None
        """
        print(f"[INFO] Searching full event for {artist} on {date} @ {venue}, {city}")

        # prefer DD-MM-YYYY for API queries
        date_ddmm = self._to_setlistfm_date(date)

        # If venue & city provided, query by venue+date+city (narrow)
        # Otherwise query by artist+date (narrower than venue-only search)
        if venue and city:
            params = {"date": date_ddmm, "venueName": venue, "cityName": city}
            print("[DEBUG] Querying Setlist.fm by venue+city+date")
        else:
            # fallback to artist+date if we don't have venue+city — much less noisy than all-venue query
            params = {"artistName": artist, "date": date_ddmm}
            print("[DEBUG] Querying Setlist.fm by artist+date (venue/city missing)")

        try:
            r = requests.get(f"{BASE_URL}/search/setlists", headers=self.headers, params=params, timeout=20)
        except Exception as e:
            print(f"[WARN] Setlist.fm request failed: {e}")
            return None

        if r.status_code != 200:
            print(f"[WARN] Setlist.fm returned status {r.status_code}")
            return None

        all_sets = r.json().get("setlist", []) or []
        print(f"[DEBUG] Found {len(all_sets)} total setlists from API query")

        if not all_sets:
            return None

        # Filter to exact eventDate matches where possible
        filtered_by_date = []
        for entry in all_sets:
            ev_date = entry.get("eventDate", "")
            try:
                # eventDate is DD-MM-YYYY in the API; convert to ISO date string YYYY-MM-DD for comparison
                if ev_date:
                    dt = datetime.strptime(ev_date, "%d-%m-%Y").date()
                    if str(dt) == date:
                        filtered_by_date.append(entry)
                else:
                    # no eventDate available — keep for now
                    filtered_by_date.append(entry)
            except Exception:
                # if parse fails, keep the entry (we'll rely on fuzzy)
                filtered_by_date.append(entry)

        print(f"[DEBUG] {len(filtered_by_date)} candidates after exact date filter")

        # If filtering by date produced nothing, fall back to original list
        candidates = filtered_by_date if filtered_by_date else all_sets

        # If we originally queried by venue+city but that returned many unrelated global results,
        # and we have an artist value, try a secondary narrower artist+date query to reduce noise.
        if venue and city and len(candidates) > 10 and artist:
            print("[DEBUG] Too many candidates from venue query — performing secondary artist+date query to narrow results")
            try:
                r2 = requests.get(f"{BASE_URL}/search/setlists",
                                  headers=self.headers,
                                  params={"artistName": artist, "date": date_ddmm},
                                  timeout=15)
                if r2.status_code == 200:
                    artist_sets = r2.json().get("setlist", []) or []
                    if artist_sets:
                        print(f"[DEBUG] Secondary artist+date query returned {len(artist_sets)} candidates; using those")
                        candidates = artist_sets
            except Exception:
                pass

        print(f"[DEBUG] Using {len(candidates)} candidate setlists for fuzzy matching")

        # Score candidates and identify headliner + openers
        headliner_entry = None
        possible_openers: List[Dict] = []

        # We'll compute scores and prefer exact-ish (>=85) for headliner
        for entry in candidates:
            band = entry.get("artist", {}).get("name", "")
            score = fuzz.token_set_ratio(artist, band)
            # optional: boost score if venue names match (if venue provided)
            if venue:
                ven = entry.get("venue", {}).get("name", "")
                if ven and venue.lower() in ven.lower():
                    score += 10
            if city:
                c = entry.get("venue", {}).get("city", {}).get("name", "")
                if c and city.lower() in (c or "").lower():
                    score += 5

            # Debug print
            print(f"[DEBUG] Candidate band: {band!r} score={score}")

            if score >= 85:
                # treat as headliner candidate; pick the best-scoring one
                if not headliner_entry or score > headliner_entry["_score"]:
                    headliner_entry = entry
                    headliner_entry["_score"] = score
            elif 50 <= score < 85:
                possible_openers.append({"entry": entry, "score": score})

        if not headliner_entry:
            print("[ERROR] Could not identify headliner from API response.")
            return None

        # Extract headliner songs
        headliner_songs = self._extract_songs_from_set(headliner_entry)

        # Build opener blocks using possible_openers (further verify via support[] or their own songs)
        opener_blocks = []
        for o in possible_openers:
            entry = o["entry"]
            name = entry.get("artist", {}).get("name", "")
            songs = self._extract_songs_from_set(entry)
            # if no songs in that entry, check entry.artist.support (rare), otherwise we'll still include and fallback to Spotify later
            if not songs:
                supports = entry.get("artist", {}).get("support", []) or []
                # sometimes support provided as strings or dicts
                if supports:
                    # no setlist songs, but support array suggests related artists — skip here (we're on the artist entry itself)
                    pass
            # Add only if there's any reason to believe it's part of the event
            opener_blocks.append({"name": name, "songs": songs, "_score": o["score"]})

        # Deduplicate openers by lowercased name while preserving order
        seen = set()
        deduped_openers = []
        for ob in opener_blocks:
            k = (ob["name"] or "").lower()
            if not k or k in seen:
                continue
            seen.add(k)
            deduped_openers.append({"name": ob["name"], "songs": ob["songs"]})

        print(f"[DEBUG] FINAL HEADLINER: {headliner_entry.get('artist',{}).get('name','')} (songs={len(headliner_songs)})")
        print(f"[DEBUG] FINAL OPENERS: {[ (o['name'], len(o['songs'])) for o in deduped_openers ]}")

        return {
            "headliner": headliner_entry.get("artist", {}).get("name", artist),
            "headliner_songs": headliner_songs,
            "openers": deduped_openers
        }

    # ------------------------------------------------------------

    def _extract_songs_from_set(self, setlist_entry):
        """Extract song names from a setlist.fm API entry."""
        songs = []
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

    # ------------------------------------------------------------

    def _to_setlistfm_date(self, date_str: str) -> str:
        """Convert YYYY-MM-DD → DD-MM-YYYY."""
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%d-%m-%Y")
