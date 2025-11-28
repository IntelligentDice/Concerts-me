# setlistfm_api.py
"""
Clean, production-ready SetlistFM wrapper with:
  • Normal event lookup
  • Festival mode detection
  • Ordered lineup extraction
  • Headliners + openers parsing
  • Protective parsing for inconsistent Setlist.fm data
  • Verbose & debug-friendly logging
"""

import requests
import logging
from datetime import datetime
from typing import Optional, List, Dict, Tuple


LOG = logging.getLogger("setlistfm")


class SetlistFM:
    BASE_URL = "https://api.setlist.fm/rest/1.0"

    def __init__(self, api_key: str, verbose: bool = False):
        self.api_key = api_key
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "x-api-key": api_key,
            "User-Agent": "ConcertPlaylistBuilder/1.0"
        })

    # ----------------------------------------------------------------------
    # UTILITIES
    # ----------------------------------------------------------------------
    def _log(self, msg: str):
        if self.verbose:
            print(msg)
        else:
            LOG.info(msg)

    def _warn(self, msg: str):
        if self.verbose:
            print("[WARN]", msg)
        else:
            LOG.warning(msg)

    def _api_get(self, endpoint: str, params: Dict[str, str] = None) -> Optional[dict]:
        """GET with graceful fallback."""
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        try:
            resp = self.session.get(url, params=params, timeout=12)
            if resp.status_code == 200:
                return resp.json()
            else:
                self._warn(f"SetlistFM GET {url} failed ({resp.status_code}) {resp.text[:120]}")
        except Exception as e:
            self._warn(f"SetlistFM GET error: {e}")
        return None

    def _convert_date_to_api_format(self, date_str: str) -> str:
        """
        Accepts YYYY-MM-DD and returns DD-MM-YYYY (API format).
        If already in DD-MM-YYYY, returns unchanged.
        """
        try:
            if "-" not in date_str:
                return date_str

            parts = date_str.split("-")
            if len(parts[0]) == 4:
                # YYYY-MM-DD -> DD-MM-YYYY
                yyyy, mm, dd = parts
                return f"{dd}-{mm}-{yyyy}"
            else:
                return date_str
        except Exception:
            return date_str

    # ----------------------------------------------------------------------
    # HIGH-LEVEL PUBLIC METHOD
    # ----------------------------------------------------------------------
    def find_event_setlist(self, artist: str, venue: Optional[str], city: Optional[str], date: str) -> Optional[dict]:
        """
        MAIN ENTRYPOINT.

        Returns:
        {
            "is_festival": bool,
            "festival_name": str,
            "lineup": [ { name, songs, startTime, lastUpdated } ],
            "venue": str,
            "city": str,
            "headliner": str,
            "headliner_songs": [],
            "openers": []
        }
        """

        api_date = self._convert_date_to_api_format(date)
        self._log(f"[INFO] Searching setlists for {artist} {date} @ {venue} {city}")

        params = {
            "artistName": artist,
            "date": api_date
        }

        raw = self._api_get("search/setlists", params=params)
        if not raw or "setlist" not in raw:
            self._warn(f"No setlist results for {artist} on {date}")
            return None

        sets = raw.get("setlist", [])
        if not sets:
            self._warn(f"No setlists returned for {artist} on {date}")
            return None

        # Try to locate the most relevant event using venue/city hints if available.
        event = self._select_best_event(sets, venue, city)
        if not event:
            self._warn("No matching event found after filtering")
            return None

        return self._extract_event_data(event)

    # ----------------------------------------------------------------------
    # EVENT SELECTION
    # ----------------------------------------------------------------------
    def _select_best_event(self, events: List[dict], venue: Optional[str], city: Optional[str]) -> Optional[dict]:
        """
        Choose best event:
           • exact venue+city match wins
           • fallback to city match
           • else highest 'lastUpdated' wins
        """
        if venue:
            venue_lower = venue.lower()
        if city:
            city_lower = city.lower()

        best = None
        best_score = -1

        for ev in events:
            v = (ev.get("venue") or {}).get("name", "") or ""
            c = (ev.get("venue") or {}).get("city", {}).get("name", "") or ""
            v_l = v.lower()
            c_l = c.lower()

            score = 0
            if venue and venue_lower in v_l:
                score += 3
            if city and city_lower in c_l:
                score += 2

            # If used any scoring or if nothing has been chosen yet
            if score > best_score:
                best = ev
                best_score = score

        # If multiple matches but score ties, pick the most recently updated
        if best:
            last_updated = best.get("lastUpdated")
            if last_updated:
                best_dt = self._parse_last_updated(last_updated)
                for ev in events:
                    if ev is best:
                        continue
                    ev_lu = self._parse_last_updated(ev.get("lastUpdated"))
                    if ev_lu > best_dt:
                        best = ev
                        best_dt = ev_lu
        return best

    def _parse_last_updated(self, s: Optional[str]) -> datetime:
        if not s:
            return datetime.min
        try:
            return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return datetime.min

    # ----------------------------------------------------------------------
    # EVENT EXTRACTION (festival OR normal)
    # ----------------------------------------------------------------------
    def _extract_event_data(self, event: dict) -> dict:
        """Detect if festival; extract openers/headliner; return structured dict."""

        venue = (event.get("venue") or {}).get("name") or ""
        city = (event.get("venue") or {}).get("city", {}).get("name") or ""
        festival = (event.get("eventInfo") or {}).get("festivalName")

        # ---------------------------
        # FESTIVAL DETECTED
        # ---------------------------
        if festival:
            self._log(f"[INFO] FESTIVAL MODE lookup: venue='{venue}', city='{city}', date='{event.get('eventDate')}'")
            return self._extract_festival(event, festival, venue, city)

        # ---------------------------
        # NORMAL SHOW
        # ---------------------------
        return self._extract_normal_show(event, venue, city)

    # ----------------------------------------------------------------------
    # FESTIVAL PARSER
    # ----------------------------------------------------------------------
    def _extract_festival(self, event: dict, festival_name: str, venue: str, city: str) -> dict:
        """
        Returns:
        {
            "is_festival": True,
            "festival_name": str,
            "lineup": [ {name, songs[], startTime, lastUpdated} ],
            "venue": str,
            "city": str
        }
        """
        lineup = []

        # Setlist.fm festival structure:
        # event["sets"]["set"] = [
        #   { "artist": { "name": "Band" }, "song": [..], "info": "..", ... }
        # ]
        sets = (event.get("sets") or {}).get("set", [])

        for s in sets:
            artist_name = (s.get("artist") or {}).get("name")
            if not artist_name:
                continue

            songs = [x.get("name") for x in (s.get("song") or []) if x.get("name")]
            start_time = s.get("info")  # may contain "start time: HH:MM"
            last_updated = (s.get("lastUpdated") or event.get("lastUpdated"))

            lineup.append({
                "name": artist_name,
                "songs": songs,
                "startTime": start_time,
                "lastUpdated": last_updated,
                "_raw": s
            })

        return {
            "is_festival": True,
            "festival_name": festival_name,
            "lineup": lineup,
            "venue": venue,
            "city": city,
            "eventDate": event.get("eventDate")
        }

    # ----------------------------------------------------------------------
    # NORMAL SHOW PARSER
    # ----------------------------------------------------------------------
    def _extract_normal_show(self, event: dict, venue: str, city: str) -> dict:
        """
        For a normal show:
        Extract headliner + openers based on Setlist.fm order.
        """
        sets = (event.get("sets") or {}).get("set", [])
        if not sets:
            self._warn("No sets found in normal show")
            return {
                "is_festival": False,
                "headliner": None,
                "headliner_songs": [],
                "openers": [],
                "venue": venue,
                "city": city,
                "eventDate": event.get("eventDate")
            }

        # Heuristic:
        #   • Last set is headliner (they appear last)
        #   • All sets before it are openers
        headliner_block = sets[-1]
        openers_blocks = sets[:-1]

        headliner_name = (headliner_block.get("artist") or {}).get("name")
        headliner_songs = [x.get("name") for x in (headliner_block.get("song") or []) if x.get("name")]
        headliner_start = headliner_block.get("info")
        headliner_last_updated = headliner_block.get("lastUpdated") or event.get("lastUpdated")

        openers = []
        for op in openers_blocks:
            name = (op.get("artist") or {}).get("name")
            songs = [x.get("name") for x in (op.get("song") or []) if x.get("name")]
            op_start = op.get("info")
            op_last_updated = op.get("lastUpdated") or event.get("lastUpdated")
            openers.append({
                "name": name,
                "songs": songs,
                "startTime": op_start,
                "lastUpdated": op_last_updated
            })

        return {
            "is_festival": False,
            "eventDate": event.get("eventDate"),
            "venue": venue,
            "city": city,
            "headliner": headliner_name,
            "headliner_songs": headliner_songs,
            "headliner_startTime": headliner_start,
            "headliner_lastUpdated": headliner_last_updated,
            "openers": openers
        }
