# google_sheets.py
import json
import gspread
from google.oauth2.service_account import Credentials

class GoogleSheets:
    def __init__(self, sheet_id, service_account_json):
        self.sheet_id = sheet_id
        creds_dict = json.loads(service_account_json)

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        self.sheet = client.open_by_key(sheet_id).sheet1

    def get_artists(self):
        data = self.sheet.col_values(1)
        return [a for a in data if a.strip()]
