"""
Setlist.fm API integration module.
Handles querying setlists for concerts and festivals.
"""

import os
import requests
import time
from datetime import datetime
from rapidfuzz import fuzz


BASE_URL = "https://api.setlist.fm/rest/1.0"

# Rate limiting: Setlist.fm allows ~2 requests per second
LAST_REQUEST_TIME = 0
MIN_REQUEST_INTERVAL = 0.5  # 500ms between requests


def rate_limit():
    """Enforce rate limiting for Setlist.fm API."""
    global LAST_REQUEST_TIME
    current_time = time.time()
    time_since_last = current_time - LAST_REQUEST_TIME
    
    if time_since_last < MIN_REQUEST_INTERVAL:
        sleep_time = MIN_REQUEST_INTERVAL - time_since_last
        print(f"[DEBUG] Rate limiting: sleeping {sleep_time:.2f}s")
        time.sleep(sleep_time)
    
    LAST_REQUEST_TIME = time.time()


def fuzzy_match_score(str1, str2):
    """Calculate fuzzy match score between two strings."""
    if not str1 or not str2:
        return 0
    return fuzz.ratio(str1.lower(), str2.lower())


def get_setlist_for_event(event):
    """
    Fetch setlist data for a given event.
    
    Args:
        event: Dictionary with artist, date, venue, city, is_festival, event_name
    
    Returns:
        Dictionary with:
            headliner: {name, songs}
            openers: [{name, songs}, ...]
            is_festival: bool
            festival_name: str
            festival_day_label: str
            lineup: [artist_names] (for festivals)
    """
    api_key = os.getenv("SETLISTFM_API_KEY")
    if not api_key:
        raise ValueError("Missing SETLISTFM_API_KEY")
    
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json"
    }
    
    artist = event["artist"]
    date = event["date"]
    venue = event["venue"]
    city = event["city"]
    is_festival = event["is_festival"]
    event_name = event.get("event_name", "")
    
    # Convert date from YYYY-MM-DD to DD-MM-YYYY for API
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        api_date = date_obj.strftime("%d-%m-%Y")
    except ValueError:
        print(f"[ERROR] Invalid date format for {artist}: {date}")
        return None
    
    # Search for setlists at this venue/city/date to find ALL artists
    print(f"[DEBUG] Searching for all setlists on {api_date} in {city}")
    
    search_url = f"{BASE_URL}/search/setlists"
    params = {
        "cityName": city,
        "date": api_date
    }
    
    try:
        rate_limit()  # Rate limit before making request
        response = requests.get(search_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        setlists = data.get("setlist", [])
        
        if not setlists:
            print(f"[WARN] No setlists found for {city} on {date}")
            return None
        
        # Filter setlists by venue match
        matching_setlists = []
        for setlist in setlists:
            setlist_venue = setlist.get("venue", {}).get("name", "")
            setlist_city = setlist.get("venue", {}).get("city", {}).get("name", "")
            
            venue_score = fuzzy_match_score(venue, setlist_venue) if venue else 100
            city_score = fuzzy_match_score(city, setlist_city)
            
            # Must match venue and city reasonably well
            if venue_score >= 70 and city_score >= 70:
                matching_setlists.append(setlist)
                print(f"[DEBUG] Found setlist: {setlist.get('artist', {}).get('name', 'Unknown')} at {setlist_venue}")
        
        if not matching_setlists:
            print(f"[WARN] No matching setlists found for venue: {venue}")
            return None
        
        # Parse setlists to build lineup
        return parse_multi_artist_setlists(matching_setlists, artist, is_festival, event)
        
    except requests.exceptions.RequestException as e:
        if "429" in str(e):
            print(f"[WARN] Rate limited by Setlist.fm API, waiting 2 seconds and retrying...")
            time.sleep(2)
            try:
                response = requests.get(search_url, headers=headers, params=params, timeout=10)
                response.raise_for_status()
                data = response.json()
                setlists = data.get("setlist", [])
            except Exception as retry_error:
                print(f"[ERROR] Retry failed for {artist}: {retry_error}")
                return None
        else:
            print(f"[ERROR] API request failed for {artist}: {e}")
            return None
    except Exception as e:
        print(f"[ERROR] Unexpected error fetching setlist for {artist}: {e}")
        return None


def parse_multi_artist_setlists(setlists, headliner_name, is_festival, event):
    """
    Parse multiple setlists from the same show to identify headliner and openers.
    
    Args:
        setlists: List of setlist objects from the same venue/date
        headliner_name: Expected headliner name
        is_festival: Boolean
        event: Event dictionary
    
    Returns:
        Dictionary with headliner, openers, etc.
    """
    result = {
        "headliner": {"name": "", "songs": []},
        "openers": [],
        "is_festival": is_festival,
        "festival_name": "",
        "festival_day_label": "",
        "lineup": []
    }
    
    all_artists = []
    
    for setlist in setlists:
        artist_name = setlist.get("artist", {}).get("name", "")
        sets = setlist.get("sets", {}).get("set", [])
        
        all_songs = []
        for set_data in sets:
            songs = set_data.get("song", [])
            song_names = [song.get("name", "") for song in songs if song.get("name")]
            all_songs.extend(song_names)
        
        if all_songs:
            all_artists.append({
                "name": artist_name,
                "songs": all_songs
            })
            print(f"[INFO] Found artist: {artist_name} with {len(all_songs)} songs")
    
    if not all_artists:
        return None
    
    if is_festival:
        festival_name = event.get("artist", "")
        result["festival_name"] = festival_name
        result["festival_day_label"] = festival_name
        result["lineup"] = [a["name"] for a in all_artists]
        
        # Headliner is last, rest are openers
        result["headliner"] = all_artists[-1]
        result["openers"] = all_artists[:-1] if len(all_artists) > 1 else []
    else:
        # Find the headliner by fuzzy matching
        headliner_idx = -1
        best_match_score = 0
        
        for idx, artist_data in enumerate(all_artists):
            score = fuzzy_match_score(headliner_name, artist_data["name"])
            if score > best_match_score:
                best_match_score = score
                headliner_idx = idx
        
        if headliner_idx >= 0:
            result["headliner"] = all_artists[headliner_idx]
            result["openers"] = [a for i, a in enumerate(all_artists) if i != headliner_idx]
        else:
            # Default to last artist as headliner
            result["headliner"] = all_artists[-1]
            result["openers"] = all_artists[:-1] if len(all_artists) > 1 else []
    
    return result
