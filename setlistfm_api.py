"""
Setlist.fm API integration module.
Handles querying setlists for concerts and festivals.
"""

import os
import requests
from datetime import datetime
from rapidfuzz import fuzz


BASE_URL = "https://api.setlist.fm/rest/1.0"


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
    
    # Convert date from YYYY-MM-DD to MM-DD-YYYY for API
    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        api_date = date_obj.strftime("%d-%m-%Y")
    except ValueError:
        print(f"[ERROR] Invalid date format for {artist}: {date}")
        return None
    
    # Search for setlists
    print(f"[DEBUG] Searching setlists for {artist} on {api_date}")
    
    search_url = f"{BASE_URL}/search/setlists"
    params = {
        "artistName": artist,
        "date": api_date
    }
    
    try:
        response = requests.get(search_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        setlists = data.get("setlist", [])
        
        if not setlists:
            print(f"[WARN] No setlists found for {artist} on {date}")
            return None
        
        # Filter and match setlists
        best_match = None
        best_score = 0
        
        for setlist in setlists:
            score = 0
            
            # Match artist name
            setlist_artist = setlist.get("artist", {}).get("name", "")
            artist_score = fuzzy_match_score(artist, setlist_artist)
            score += artist_score * 2  # Artist match is most important
            
            # Match venue
            setlist_venue = setlist.get("venue", {}).get("name", "")
            if venue:
                venue_score = fuzzy_match_score(venue, setlist_venue)
                score += venue_score
            
            # Match city
            setlist_city = setlist.get("venue", {}).get("city", {}).get("name", "")
            if city:
                city_score = fuzzy_match_score(city, setlist_city)
                score += city_score
            
            # Match event name (if provided)
            setlist_event_name = setlist.get("eventName", "")
            if event_name:
                event_score = fuzzy_match_score(event_name, setlist_event_name)
                score += event_score
            
            print(f"[DEBUG] Setlist match score: {score:.1f} for {setlist_artist} at {setlist_venue}")
            
            if score > best_score:
                best_score = score
                best_match = setlist
        
        if not best_match:
            print(f"[WARN] No matching setlist found for {artist}")
            return None
        
        print(f"[INFO] Best match: {best_match.get('artist', {}).get('name', 'Unknown')} (score: {best_score:.1f})")
        
        # Parse setlist
        return parse_setlist(best_match, is_festival, event)
        
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] API request failed for {artist}: {e}")
        return None
    except Exception as e:
        print(f"[ERROR] Unexpected error fetching setlist for {artist}: {e}")
        return None


def parse_setlist(setlist, is_festival, event):
    """
    Parse a setlist into a unified format.
    
    Returns:
        Dictionary with headliner, openers, festival info, etc.
    """
    sets = setlist.get("sets", {}).get("set", [])
    
    if not sets:
        print("[WARN] No sets found in setlist")
        return None
    
    result = {
        "headliner": {"name": "", "songs": []},
        "openers": [],
        "is_festival": is_festival,
        "festival_name": "",
        "festival_day_label": "",
        "lineup": []
    }
    
    if is_festival:
        # Festival mode: extract all artists and their songs
        festival_name = event.get("artist", "")
        result["festival_name"] = festival_name
        result["festival_day_label"] = f"{festival_name}"
        
        all_artists = []
        
        for set_data in sets:
            set_name = set_data.get("name", "")
            songs = set_data.get("song", [])
            
            # Check if this set has an encore attribute or is labeled as an artist
            if songs:
                # For festivals, each set typically represents a different artist
                artist_name = set_name if set_name else "Unknown Artist"
                song_list = [song.get("name", "") for song in songs if song.get("name")]
                
                if song_list:
                    all_artists.append({
                        "name": artist_name,
                        "songs": song_list
                    })
                    result["lineup"].append(artist_name)
        
        # Assign headliner as last artist, rest as openers
        if all_artists:
            result["headliner"] = all_artists[-1]
            result["openers"] = all_artists[:-1] if len(all_artists) > 1 else []
        
    else:
        # Normal concert mode
        main_artist = setlist.get("artist", {}).get("name", event.get("artist", "Unknown"))
        all_songs = []
        
        for set_data in sets:
            songs = set_data.get("song", [])
            song_names = [song.get("name", "") for song in songs if song.get("name")]
            all_songs.extend(song_names)
        
        result["headliner"] = {
            "name": main_artist,
            "songs": all_songs
        }
    
    return result
