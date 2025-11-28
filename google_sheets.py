"""
Google Sheets integration module.
Fetches event data from a Google Sheet using the Sheets API.
"""

import os
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build


def fetch_events_from_sheet():
    """
    Fetch events from Google Sheets.
    
    Returns:
        list: List of event dictionaries with keys:
              artist, event_name, venue, city, date, is_festival
    """
    try:
        # Load credentials from environment variable
        credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        sheet_id = os.getenv("GOOGLE_SHEET_ID")
        
        if not credentials_json or not sheet_id:
            raise ValueError("Missing Google Sheets credentials or sheet ID")
        
        # Parse credentials JSON
        credentials_dict = json.loads(credentials_json)
        
        # Create credentials object
        credentials = Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
        )
        
        # Build the Sheets API service
        service = build('sheets', 'v4', credentials=credentials)
        
        # Fetch data from the sheet (assuming data is in Sheet1, starting from A1)
        sheet_range = "Sheet1!A:F"
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=sheet_range
        ).execute()
        
        values = result.get('values', [])
        
        if not values:
            print("[WARN] No data found in Google Sheet")
            return []
        
        # Assume first row is headers
        headers = values[0]
        rows = values[1:]
        
        # Map headers to lowercase for flexible matching
        header_map = {h.lower().strip(): i for i, h in enumerate(headers)}
        
        events = []
        for row_num, row in enumerate(rows, start=2):
            # Skip empty rows
            if not row or all(not cell.strip() for cell in row):
                continue
            
            # Pad row to match header length
            row = row + [''] * (len(headers) - len(row))
            
            try:
                # Extract required fields
                artist = row[header_map.get('artist', 0)].strip()
                event_name = row[header_map.get('event_name', 1)].strip()
                venue = row[header_map.get('venue', 2)].strip()
                city = row[header_map.get('city', 3)].strip()
                date = row[header_map.get('date', 4)].strip()
                is_festival_str = row[header_map.get('is_festival', 5)].strip().upper()
                
                # Validate required fields
                if not artist or not date:
                    print(f"[WARN] Row {row_num}: Missing required fields (artist or date), skipping")
                    continue
                
                # Convert is_festival to boolean
                is_festival = is_festival_str == "TRUE"
                
                event = {
                    "artist": artist,
                    "event_name": event_name,
                    "venue": venue,
                    "city": city,
                    "date": date,
                    "is_festival": is_festival
                }
                
                events.append(event)
                print(f"[DEBUG] Loaded event: {artist} on {date} (festival: {is_festival})")
                
            except Exception as e:
                print(f"[WARN] Row {row_num}: Error parsing row - {e}")
                continue
        
        return events
        
    except Exception as e:
        print(f"[ERROR] Failed to fetch events from Google Sheets: {e}")
        raise
