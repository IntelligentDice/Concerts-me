from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

def load_sheet(sheet_id):
    creds = service_account.Credentials.from_service_account_file(
        "google_service_account.json", scopes=SCOPES
    )

    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()

    result = sheet.values().get(
        spreadsheetId=sheet_id, range="Sheet1"
    ).execute()

    rows = result.get("values", [])
    if not rows:
        return []

    headers = rows[0]
    data = [dict(zip(headers, row)) for row in rows[1:]]
    return data
