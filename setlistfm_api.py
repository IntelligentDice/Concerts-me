# setlistfm_api.py
import requests
from rapidfuzz import fuzz
from datetime import datetime


BASE_URL = "https://api.setlist.fm/rest/1.0"


class SetlistFM:
    """
    Minimal Setlist.fm client optimized for:
    - Finding the correct event for (artist + date)
    - Extracting openers + headliner
    - Extracting song lists per act
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}

    # -----------------------------
    # Core: Search by artist + date
    # -----------------------------
    def find_event_setlist(self, artist: str, date: str):
        """
        Search Setlist.fm for the correct event for this artist + date.
        Returns structured dict:
        {
            "headliner": "Band Name",
            "headliner_songs": [...],
            "openers": [
                {"name": opener_name, "songs": [...]},
                ...
            ]
        }
        """

        search_params = {
            "artistName": artist,
            "date": self._to_setlistfm_date(date),  # DD-MM-YYYY
        }

        r = requests.get(f"{BASE_URL}/search/setlists",
                         headers=self.headers, params=search_params, timeout=15)

        if r.status_code != 200:
            return None

        data = r.json()
        candidates = data.get("setlist", [])
        if not candidates:
            return None

        best = self._pick_best_match(candidates, artist, date)
        if not best:
            return None

        # Extract headliner + opener content
        return self._extract_full_event(best)

    # ---------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------
    def _to_setlistfm_date(self, date_str: str) -> str:
        """Convert YYYY-MM-DD → DD-MM-YYYY."""
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%d-%m-%Y")

    def _pick_best_match(self, candidates, artist, date):
        """
        Fuzzy rank all candidate setlists to find the best matching event.
        """
        best_score = -1
        best_item = None

        for c in candidates:
            score = 0

            # Compare artists
            cand_artist = c.get("artist", {}).get("name", "")
            score += fuzz.token_set_ratio(artist, cand_artist) * 2

            # Compare date
            event_date = c.get("eventDate", "")
            if event_date:
                try:
                    dt = datetime.strptime(event_date, "%d-%m-%Y").date()
                    if str(dt) == date:
                        score += 50
                except Exception:
                    pass

            if score > best_score:
                best_score = score
                best_item = c

        # Require a basic confidence threshold
        if best_score < 40:
            return None

        return best_item

    # ---------------------------------------------------------
    # Extract full event details: opener names + songs, headliner songs
    # ---------------------------------------------------------
    def _extract_full_event(self, setlist):
        """
        Extracts:
        - headliner name
        - headliner song list
        - opener names + songs (if present)
        - Adds full debug output so user can see what was detected
        """

        artist_name = setlist.get("artist", {}).get("name", "")
        print(f"[DEBUG] Headliner detected: {artist_name}")

        sets = setlist.get("sets", {}).get("set", [])
        if not isinstance(sets, list):
            sets = [sets]

        headliner_songs = []
        openers = []
        opener_seen = set()

        for block in sets:
            block_name = (block.get("name") or "").strip()
            block_songs = []

            raw = block.get("song", []) or []
            if not isinstance(raw, list):
                raw = [raw]

            for s in raw:
                if isinstance(s, dict) and s.get("name"):
                    block_songs.append(s["name"])
                elif isinstance(s, str):
                    block_songs.append(s)

            # --- OPENERS ---
            # Rule:
            #   If block_name appears to be an ACTUAL BAND NAME
            #   (and not "Main Set", "Encore", "Intro")
            #
            # Heuristic improvement: If block_name == headliner, treat as headliner.
            #
            if block_name:
                low = block_name.lower()

                if low not in ("main", "main set", "encore", "intro") \
                   and fuzz.token_set_ratio(block_name, artist_name) < 70:
                    print(f"[DEBUG] Opener block detected: {block_name}")
                    print(f"[DEBUG]   Songs: {block_songs}")

                    if low not in opener_seen:
                        openers.append({
                            "name": block_name,
                            "songs": block_songs
                        })
                        opener_seen.add(low)
                    continue

            # --- HEADLINER ---
            print(f"[DEBUG] Headliner block found (name='{block_name}')")
            print(f"[DEBUG]   Songs: {block_songs}")
            headliner_songs.extend(block_songs)

        # Additional opener names from support[]
        supports = setlist.get("artist", {}).get("support", []) or []
        for sup in supports:
            sup_name = sup.get("name", "").strip()
            if sup_name and sup_name.lower() not in opener_seen:
                print(f"[DEBUG] Support opener detected: {sup_name}")
                openers.append({"name": sup_name, "songs": []})
                opener_seen.add(sup_name.lower())

        print(f"[DEBUG] FINAL OPENERS: {openers}")
        print(f"[DEBUG] FINAL HEADLINER SONGS COUNT: {len(headliner_songs)}")

        return {
            "headliner": artist_name,
            "headliner_songs": headliner_songs,
            "openers": openers
        }
