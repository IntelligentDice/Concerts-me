import json
import logging
import gspread
from google.oauth2.service_account import Credentials


class GoogleSheets:
    """
    Google Sheets wrapper that exclusively loads service account credentials
    from a JSON string stored in the environment variable
    GOOGLE_SERVICE_ACCOUNT_JSON.
    """

    def __init__(self, sheet_id: str, service_account_json: str):
        if not sheet_id:
            raise ValueError("sheet_id is required")

        try:
            creds_info = json.loads(service_account_json)
        except Exception as e:
            raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON contains invalid JSON") from e

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)

        self.sheet = client.open_by_key(sheet_id).sheet1

    def read_events(self):
        """
        Expected sheet format:
         artist | date | songs
        songs = JSON list OR comma/semicolon separated.
        """
        rows = self.sheet.get_all_records()
        events = []

        for r in rows:
            artist = r.get("artist")
            date = r.get("date")
            songs = r.get("songs", "")

            if isinstance(songs, str):
                try:
                    parsed = json.loads(songs)
                    if isinstance(parsed, list):
                        songs = parsed
                    else:
                        songs = [str(parsed)]
                except Exception:
                    sep = ";" if ";" in songs else ","
                    songs = [s.strip() for s in songs.split(sep) if s.strip()]

            events.append({"artist": artist, "date": date, "songs": songs})

        return events

    def write_playlist_link(self, event_index: int, playlist_url: str):
        header = self.sheet.row_values(1)
        try:
            col = header.index("playlist") + 1
        except ValueError:
            col = len(header) + 1
            self.sheet.update_cell(1, col, "playlist")

        self.sheet.update_cell(event_index + 2, col, playlist_url)
