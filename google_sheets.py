import json
import logging
import gspread
from google.oauth2.service_account import Credentials


class GoogleSheets:
    """
    Google Sheets wrapper that loads events from a sheet with the format:

        artist | event_name | venue | city | date

    It also handles optional columns safely.
    """

    REQUIRED_COLUMNS = ["artist", "event_name", "venue", "city", "date"]

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

    # ------------------------------------------------------------------
    # Read events from the sheet (FULL DATA)
    # ------------------------------------------------------------------
    def read_events(self):
        """
        Reads rows from the sheet and returns a list of event dictionaries:

        {
            "artist": "Blind Guardian",
            "event_name": "...",
            "venue": "The Underground",
            "city": "Charlotte",
            "date": "2025-11-22"
        }
        """

        rows = self.sheet.get_all_records()
        events = []

        for row in rows:
            # Normalize keys (Google Sheets sometimes changes capitalization)
            normalized = {k.strip().lower(): v for k, v in row.items()}

            event = {
                "artist": normalized.get("artist"),
                "event_name": normalized.get("event_name"),
                "venue": normalized.get("venue"),
                "city": normalized.get("city"),
                "date": normalized.get("date"),
            }

            # Validate required fields
            if not event["artist"] or not event["date"]:
                logging.warning(f"Skipping row due to missing required fields: {row}")
                continue

            events.append(event)

        logging.info(f"[INFO] Loaded {len(events)} events from Google Sheets")
        return events

    # ------------------------------------------------------------------
    # Write playlist URL
    # ------------------------------------------------------------------
    def write_playlist_link(self, event_index: int, playlist_url: str):
        header = self.sheet.row_values(1)

        try:
            col = header.index("playlist") + 1
        except ValueError:
            col = len(header) + 1
            self.sheet.update_cell(1, col, "playlist")

        self.sheet.update_cell(event_index + 2, col, playlist_url)
