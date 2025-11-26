# Concert Playlist Builder

This project automatically creates Spotify playlists for concerts you've attended by:
1. Reading your concert history from a Google Sheet  
2. Fetching full setlists from Setlist.fm  
3. Building one playlist per concert in Spotify  
4. Preserving the order of bands and songs played  
5. Applying fuzzy matching to handle naming differences  

## Features
- Google Sheets → Python ingestion  
- Setlist.fm event → full multi-artist setlist extraction  
- Spotify OAuth with refresh token for long-term use  
- Automatic creation of playlists named:  
  **{Artist} — {Venue}, {City} ({Date})**
- Validation of events with warnings for missing setlist data  
- Multi-artist support (openers + headliners)

---

# Setup Instructions

## 1. Google Sheets Setup
Create a Google Sheet with columns:

```
artist,event_name,venue,city,date
```

Ensure you share the Sheet with your Google Cloud service account email once created.

---

## 2. Create Spotify API App
Go to: https://developer.spotify.com/dashboard  

Create an app, then fill these fields:

- **Client ID**
- **Client Secret**
- Add a redirect URI: `http://localhost:8080/callback`

Copy them into `config.json`.

---

## 3. Setlist.fm API
Create an API key here:  
https://www.setlist.fm/account/settings/api  

Copy your key into `config.json`.

---

## 4. Install Dependencies
```
pip install -r requirements.txt
```

---

## 5. Run OAuth Flow to Get Long-Term Spotify Refresh Token
```
python oauth.py
```

Follow instructions. Copy the refresh token into `config.json`.

---

## 6. Main Script
```
python main.py
```

---

# File Structure

```
.
│── README.md
│── requirements.txt
│── main.py
│── playlist_builder.py
│── spotify_api.py
│── setlistfm_api.py
│── google_sheets.py
│── oauth.py
│── config.example.json
│── utils/
│     ├── logging_utils.py
│     └── fuzzy_utils.py
│── .gitignore
```
