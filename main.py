"""
Main entry point for the automated playlist generator.
Orchestrates the entire workflow from reading events to creating playlists.
"""

import os
import sys
from google_sheets import fetch_events_from_sheet
from playlist_builder import process_events


def main():
    """Main execution function."""
    print("[INFO] Starting automated playlist generator...")
    
    # Validate required environment variables
    required_vars = [
        "SETLISTFM_API_KEY",
        "SPOTIFY_CLIENT_ID",
        "SPOTIFY_CLIENT_SECRET",
        "SPOTIFY_REFRESH_TOKEN",
        "GOOGLE_SHEETS_CREDENTIALS_JSON",
        "GOOGLE_SHEET_ID"
    ]
    
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        print(f"[ERROR] Missing required environment variables: {', '.join(missing_vars)}")
        sys.exit(1)
    
    # Check dry run mode
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    if dry_run:
        print("[INFO] Running in DRY RUN mode - no playlists will be created")
    
    try:
        # Fetch events from Google Sheets
        print("[INFO] Fetching events from Google Sheets...")
        events = fetch_events_from_sheet()
        
        if not events:
            print("[WARN] No events found in Google Sheets")
            return
        
        print(f"[INFO] Found {len(events)} events to process")
        
        # Process all events and create playlists
        process_events(events, dry_run=dry_run)
        
        print("[INFO] Playlist generation complete!")
        
    except Exception as e:
        print(f"[ERROR] Fatal error in main execution: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
