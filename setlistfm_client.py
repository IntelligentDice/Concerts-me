# setlistfm_client.py
import requests

class SetlistFMClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def get_songs_from_artists(self, artists):
        songs = []

        for artist in artists:
            r = requests.get(
                f"https://api.setlist.fm/rest/1.0/search/setlists",
                headers={"x-api-key": self.api_key, "Accept": "application/json"},
                params={"artistName": artist}
            )

            if r.status_code != 200:
                continue

            data = r.json()

            for setlist in data.get("setlist", []):
                for s in setlist.get("sets", {}).get("set", []):
                    for song in s.get("song", []):
                        songs.append({
                            "artist": artist,
                            "title": song["name"]
                        })

        return songs
