# Concerts-me
Code to create playlists for concerts I've attended
# Setlist → Apple Music Playlists

Creates one Apple Music playlist per concert listed in `events.csv` (or a Google Sheet).

## Requirements
- GitHub repo with Actions enabled
- Setlist.fm API key (https://api.setlist.fm/docs)
- Apple Developer Token (server JWT) and MusicKit User Token for your account
- events.csv in repo (columns: artist,event_name,venue,city,date) OR Google Sheet

## Install Apple tokens
1. **Developer token**: create an Apple Music API key in your Apple Developer account. Create a JWT (developer token). Store as `APPLE_DEVELOPER_TOKEN`.
2. **User token**: use MusicKit JS or an existing flow to obtain a MusicKit user token for your Apple ID. Store as `APPLE_USER_TOKEN`.
  - You can generate a user token through a front-end MusicKit auth flow. (This token is required to create playlists in the user's library.)

## GitHub Secrets
Add these:
- `SETLIST_API_KEY` — your setlist.fm API key
- `APPLE_DEVELOPER_TOKEN` — your server JWT
- `APPLE_USER_TOKEN` — MusicKit user token
- Optional for Google Sheets:
  - `USE_GOOGLE_SHEET` = "true"
  - `GOOGLE_SHEET_ID`
  - `GOOGLE_SERVICE_ACCOUNT_JSON` — the raw JSON (if you want Actions to authenticate with service account)

## Usage
1. Add events to `events.csv` or Google Sheet.
2. In GitHub Actions, run the **Generate Apple Music Playlists (Manual)** workflow.
3. The job will create one playlist per concert and add tracks found on Apple Music.

## Notes & troubleshooting
- Matching is fuzzy; if you see mismatches, edit your CSV to include exact artist names or alternate venues.
- Apple Music matching can fail for live-only tracks or rare tracks; the script will skip unmatched songs.
- No scheduling is configured; the workflow runs only when manually triggered.

