import requests
import logging
from datetime import datetime


def find_event_setlist(artist, date, api_key):
    """
    Query Setlist.fm for an artist + date.
    Returns: list of {"title": song_title}
    """

    base = "https://api.setlist.fm/rest/1.0/search/setlists"
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json"
    }

    # Convert date to datetime
    if isinstance(date, str):
        try:
            date_obj = datetime.fromisoformat(date)
        except Exception:
            logging.warning(f"Invalid date format passed to find_event_setlist: {date}")
            return None
    else:
        date_obj = date

    # Required Setlist.fm format = DD-MM-YYYY
    formatted_date = date_obj.strftime("%d-%m-%Y")

    params = {
        "artistName": artist,
        "date": formatted_date
    }

    try:
        resp = requests.get(base, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        logging.warning(f"Setlist.fm request failed for {artist} on {formatted_date}: {e}")
        return None

    data = resp.json()

    if "setlist" not in data or not data["setlist"]:
        return None

    # Get first matching setlist
    s = data["setlist"][0]
    songs_out = []

    # Navigate: setlist -> sets -> set -> song
    sets = s.get("sets", {}).get("set", [])

    for set_block in sets:
        for song in set_block.get("song", []):
            title = song.get("name")
            if title:
                songs_out.append({"title": title})

    return songs_out
