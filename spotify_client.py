"""
Spotify API client module.
Handles authentication, track searching, and playlist management.
"""

import os
import re
import requests
from rapidfuzz import fuzz


class SpotifyClient:
    """Client for interacting with Spotify Web API."""
    
    def __init__(self, dry_run=False):
        """Initialize Spotify client."""
        self.client_id = os.getenv("SPOTIFY_CLIENT_ID")
        self.client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
        self.refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")
        self.dry_run = dry_run
        
        if not all([self.client_id, self.client_secret, self.refresh_token]):
            raise ValueError("Missing Spotify credentials")
        
        self.access_token = None
        self.cache = {"song_to_spotify": {}}
        
        # Refresh access token on init
        self._refresh_access_token()
    
    def _refresh_access_token(self):
        """Refresh the Spotify access token using refresh token."""
        print("[DEBUG] Refreshing Spotify access token...")
        
        token_url = "https://accounts.spotify.com/api/token"
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        
        try:
            response = requests.post(token_url, data=data, timeout=10)
            response.raise_for_status()
            token_data = response.json()
            
            self.access_token = token_data["access_token"]
            print("[INFO] Spotify access token refreshed successfully")
            
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to refresh Spotify token: {e}")
            raise
    
    def _make_request(self, method, endpoint, **kwargs):
        """Make an authenticated request to Spotify API."""
        if not self.access_token:
            self._refresh_access_token()
        
        url = f"https://api.spotify.com/v1{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.request(method, url, headers=headers, timeout=10, **kwargs)
            
            # Handle token expiration
            if response.status_code == 401:
                print("[DEBUG] Token expired, refreshing...")
                self._refresh_access_token()
                headers["Authorization"] = f"Bearer {self.access_token}"
                response = requests.request(method, url, headers=headers, timeout=10, **kwargs)
            
            response.raise_for_status()
            return response.json() if response.content else {}
            
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Spotify API request failed: {e}")
            return None
    
    def clean_song_title(self, title):
        """Clean song title for better matching."""
        # Remove content in parentheses
        title = re.sub(r'\([^)]*\)', '', title)
        # Remove content in brackets
        title = re.sub(r'\[[^\]]*\]', '', title)
        # Remove extra whitespace
        title = ' '.join(title.split())
        return title.strip()
    
    def search_track(self, song_name, artist_name=""):
        """
        Search for a track on Spotify with fuzzy matching.
        
        Args:
            song_name: Name of the song
            artist_name: Optional artist name for better matching
        
        Returns:
            Spotify track URI or None
        """
        # Check cache first
        cache_key = f"{song_name}|{artist_name}".lower()
        if cache_key in self.cache["song_to_spotify"]:
            print(f"[DEBUG] Cache hit for: {song_name}")
            return self.cache["song_to_spotify"][cache_key]
        
        # Try multiple search strategies
        search_queries = [
            f"{song_name} {artist_name}".strip(),
            self.clean_song_title(song_name) + f" {artist_name}".strip(),
            song_name,
            self.clean_song_title(song_name)
        ]
        
        best_match = None
        best_score = 0
        
        for query in search_queries:
            if not query.strip():
                continue
            
            print(f"[DEBUG] Searching Spotify for: {query}")
            
            params = {
                "q": query,
                "type": "track",
                "limit": 10
            }
            
            data = self._make_request("GET", "/search", params=params)
            
            if not data or "tracks" not in data:
                continue
            
            tracks = data["tracks"]["items"]
            
            for track in tracks:
                track_name = track["name"]
                track_artist = track["artists"][0]["name"] if track["artists"] else ""
                track_uri = track["uri"]
                
                # Calculate fuzzy match score
                name_score = fuzz.ratio(song_name.lower(), track_name.lower())
                
                # Bonus for artist match
                artist_score = 0
                if artist_name:
                    artist_score = fuzz.ratio(artist_name.lower(), track_artist.lower())
                
                total_score = name_score + (artist_score * 0.3)
                
                print(f"[DEBUG] Match candidate: {track_name} by {track_artist} (score: {total_score:.1f})")
                
                if total_score > best_score:
                    best_score = total_score
                    best_match = track_uri
            
            # If we found a good match, stop searching
            if best_score >= 80:
                break
        
        # Cache the result
        if best_match and best_score >= 80:
            self.cache["song_to_spotify"][cache_key] = best_match
            print(f"[INFO] Matched '{song_name}' with score {best_score:.1f}")
            return best_match
        else:
            print(f"[WARN] No good match found for '{song_name}' (best score: {best_score:.1f})")
            self.cache["song_to_spotify"][cache_key] = None
            return None
    
    def get_user_id(self):
        """Get the current user's Spotify ID."""
        data = self._make_request("GET", "/me")
        if data:
            return data.get("id")
        return None
    
    def get_user_playlists(self):
        """Get all playlists for the current user."""
        user_id = self.get_user_id()
        if not user_id:
            print("[ERROR] Could not get user ID for playlist retrieval")
            return []
        
        print(f"[DEBUG] Fetching playlists for user: {user_id}")
        
        playlists = []
        url = f"https://api.spotify.com/v1/me/playlists"
        params = {"limit": 50}
        page = 1
        
        while url:
            print(f"[DEBUG] Fetching playlist page {page} from: {url}")
            
            try:
                response = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    params=params if page == 1 else None,
                    timeout=10
                )
                
                print(f"[DEBUG] Response status: {response.status_code}")
                
                response.raise_for_status()
                data = response.json()
                
                print(f"[DEBUG] Response keys: {list(data.keys())}")
                print(f"[DEBUG] Total playlists (from API): {data.get('total', 'N/A')}")
                
                items = data.get("items", [])
                print(f"[DEBUG] Items in response: {len(items)}")
                
                # Check if we have permission issues
                if data.get("total", 0) > 0 and len(items) == 0:
                    print("[ERROR] ============================================")
                    print("[ERROR] SPOTIFY PERMISSION ERROR DETECTED!")
                    print("[ERROR] The API reports playlists exist but returns 0 items.")
                    print("[ERROR] This means your refresh token is missing required scopes.")
                    print("[ERROR] ")
                    print("[ERROR] Required scopes:")
                    print("[ERROR]   - playlist-read-private")
                    print("[ERROR]   - playlist-read-collaborative")
                    print("[ERROR]   - playlist-modify-private")
                    print("[ERROR]   - playlist-modify-public")
                    print("[ERROR] ")
                    print("[ERROR] You need to regenerate your SPOTIFY_REFRESH_TOKEN")
                    print("[ERROR] with these scopes included.")
                    print("[ERROR] ============================================")
                    return []
                
                if items:
                    print(f"[DEBUG] First playlist: {items[0].get('name', 'NO NAME')}")
                
                playlists.extend(items)
                print(f"[DEBUG] Page {page}: Retrieved {len(items)} playlists")
                
                # Check for next page
                url = data.get("next")
                page += 1
                
                if not items:
                    break
                
            except Exception as e:
                print(f"[ERROR] Failed to fetch playlists: {e}")
                import traceback
                traceback.print_exc()
                break
        
        print(f"[DEBUG] Total playlists retrieved: {len(playlists)}")
        return playlists
    
    def find_playlist_by_name(self, name):
        """Find an existing playlist by exact name match."""
        print(f"[DEBUG] ==========================================")
        print(f"[DEBUG] Searching for existing playlist: '{name}'")
        print(f"[DEBUG] Name length: {len(name)} characters")
        playlists = self.get_user_playlists()
        
        print(f"[DEBUG] Checking against {len(playlists)} playlists...")
        
        # Show first few playlist names for debugging
        if playlists:
            print(f"[DEBUG] Sample of existing playlist names:")
            for i, p in enumerate(playlists[:5]):
                print(f"[DEBUG]   {i+1}. '{p['name']}'")
        
        for playlist in playlists:
            playlist_name = playlist["name"]
            if playlist_name == name:
                print(f"[DEBUG] ✓ MATCH FOUND: '{playlist_name}' (ID: {playlist['id']})")
                print(f"[DEBUG] ==========================================")
                return playlist["id"]
            elif name in playlist_name or playlist_name in name:
                print(f"[DEBUG] ✗ Partial match (not exact): '{playlist_name}'")
        
        print(f"[DEBUG] ✗ NO MATCH FOUND for: '{name}'")
        print(f"[DEBUG] ==========================================")
        return None
    
    def create_playlist(self, name, description="", track_uris=None):
        """
        Create a new Spotify playlist.
        
        Args:
            name: Playlist name
            description: Playlist description
            track_uris: List of Spotify track URIs
        
        Returns:
            Playlist ID or None
        """
        if self.dry_run:
            print(f"[DRY RUN] Would create playlist: {name}")
            print(f"[DRY RUN] Description: {description}")
            print(f"[DRY RUN] Tracks: {len(track_uris) if track_uris else 0}")
            return "dry_run_playlist_id"
        
        user_id = self.get_user_id()
        if not user_id:
            print("[ERROR] Could not get user ID")
            return None
        
        # Create playlist
        # Spotify has a 300 character limit on descriptions
        if len(description) > 300:
            description = description[:297] + "..."
        
        payload = {
            "name": name,
            "description": description,
            "public": False
        }
        
        try:
            response = requests.post(
                f"https://api.spotify.com/v1/users/{user_id}/playlists",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=10
            )
            
            # Handle token expiration
            if response.status_code == 401:
                print("[DEBUG] Token expired, refreshing...")
                self._refresh_access_token()
                response = requests.post(
                    f"https://api.spotify.com/v1/users/{user_id}/playlists",
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Content-Type": "application/json"
                    },
                    json=payload,
                    timeout=10
                )
            
            response.raise_for_status()
            result = response.json()
            
            playlist_id = result["id"]
            print(f"[INFO] Created playlist: {name} (ID: {playlist_id})")
            
            # Add tracks if provided
            if track_uris:
                self.add_tracks_to_playlist(playlist_id, track_uris)
            
            return playlist_id
            
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Failed to create playlist: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"[ERROR] Response: {e.response.text}")
            return None
    
    def update_playlist(self, playlist_id, track_uris):
        """
        Update an existing playlist with new tracks.
        
        Args:
            playlist_id: Spotify playlist ID
            track_uris: List of Spotify track URIs
        """
        if self.dry_run:
            print(f"[DRY RUN] Would update playlist {playlist_id} with {len(track_uris)} tracks")
            return
        
        # Clear existing tracks
        self._make_request("PUT", f"/playlists/{playlist_id}/tracks", json={"uris": []})
        
        # Add new tracks
        self.add_tracks_to_playlist(playlist_id, track_uris)
    
    def add_tracks_to_playlist(self, playlist_id, track_uris):
        """Add tracks to a playlist (max 100 at a time)."""
        if self.dry_run:
            print(f"[DRY RUN] Would add {len(track_uris)} tracks to playlist {playlist_id}")
            return
        
        # Spotify allows max 100 tracks per request
        for i in range(0, len(track_uris), 100):
            batch = track_uris[i:i+100]
            self._make_request("POST", f"/playlists/{playlist_id}/tracks", json={"uris": batch})
        
        print(f"[INFO] Added {len(track_uris)} tracks to playlist")
    
    def create_or_update_playlist(self, name, description, track_uris):
        """
        Create a new playlist or update existing one if it exists.
        
        Args:
            name: Playlist name
            description: Playlist description
            track_uris: List of Spotify track URIs
        
        Returns:
            Playlist ID or None
        """
        if not track_uris:
            print("[WARN] No tracks to add to playlist")
            return None
        
        # Check if playlist exists
        existing_id = self.find_playlist_by_name(name)
        
        if existing_id:
            print(f"[INFO] Playlist '{name}' already exists, updating...")
            self.update_playlist(existing_id, track_uris)
            return existing_id
        else:
            print(f"[INFO] Creating new playlist: {name}")
            return self.create_playlist(name, description, track_uris)
