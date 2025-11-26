import requests

def find_event_setlist(artist, venue, date, api_key):
    base = "https://api.setlist.fm/rest/1.0/search/setlists"
    headers = {"x-api-key": api_key, "Accept": "application/json"}

    params = {
        "artistName": artist,
        "venueName": venue,
        "date": date.replace("-", "")
    }

    r = requests.get(base, headers=headers, params=params)
    if r.status_code != 200:
        return None

    data = r.json()
    if "setlist" not in data or len(data["setlist"]) == 0:
        return None

    return data["setlist"][0]
