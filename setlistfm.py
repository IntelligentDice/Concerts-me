# setlistfm.py
import requests
from rapidfuzz import fuzz
from dateutil.parser import parse as parse_date

API_BASE = "https://api.setlist.fm/rest/1.0"

class SetlistFM:
    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {"x-api-key": api_key, "Accept": "application/json"}

    def search_setlists(self, artist=None, date=None, venue=None, city=None, max_results=5):
        params = {}
        if artist:
            params["artistName"] = artist
        if date:
            params["date"] = date
        url = f"{API_BASE}/search/setlists"
        r = requests.get(url, params=params, headers=self.headers)
        r.raise_for_status()
        data = r.json()
        return data.get("setlist", [])

    def pick_best_setlist(self, candidates, target):
        if not candidates:
            return None

        scores = []
        for c in candidates:
            score = 0
            cand_artist = c.get("artist", {}).get("name", "")
            score += fuzz.token_sort_ratio(target.get("artist",""), cand_artist) * 2

            cand_venue = c.get("venue", {}).get("name", "")
            score += fuzz.token_sort_ratio(target.get("venue",""), cand_venue)

            cand_city = c.get("venue", {}).get("city", {}).get("name", "")
            score += fuzz.token_sort_ratio(target.get("city",""), cand_city)

            cand_event_date = c.get("eventDate", "")
            try:
                if cand_event_date:
                    cand_date = parse_date(cand_event_date, dayfirst=True).date().isoformat()
                    if target.get("date") == cand_date:
                        score += 50
            except Exception:
                pass

            scores.append((score, c))

        scores.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scores[0]
        if best_score < 60:
            return None
        return best

    def extract_songs(self, setlist):
        """
        Return ordered list of songs for the headliner portions (not openers).
        Many setlist 'set' blocks include a 'name' field which identifies the performer.
        This function will extract songs from sets where name is empty, 'Main', or looks like headliner.
        Use extract_openers() to get opener-specific info.
        """
        songs = []
        sets = setlist.get("sets", {}).get("set", [])
        if not isinstance(sets, list):
            sets = [sets]
        for s in sets:
            # If set has a name and the name looks like an opener, skip here (headliner extraction focuses on main/headliner sets)
            set_name = s.get("name", "") or ""
            # Heuristic: treat sets without a distinct name or with 'Main'/'Encore' as headliner
            if set_name.strip() and set_name.lower() not in ("main set", "main", "encore", ""):
                # This is likely an opener — skip for headliner
                continue
            song_entries = s.get("song", [])
            if not isinstance(song_entries, list):
                song_entries = [song_entries]
            for se in song_entries:
                if isinstance(se, dict):
                    name = se.get("name")
                    if name:
                        songs.append(name)
                elif isinstance(se, str):
                    songs.append(se)
        return songs

    def extract_openers(self, setlist):
        """
        Returns a list of opener dicts in order found:
        [
          {"name": "Johnny Marr", "songs": [..] or []},
          {"name": "Blossoms", "songs": []}
        ]
        Logic:
        - Inspect each set block that has a 'name' (and that name looks like a band)
        - Also inspect setlist['artist'].get('support',
