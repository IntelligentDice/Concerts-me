# spotify_client.py
import os
import time
import logging
from typing import List, Optional, Any, Callable

import spotify_api  # existing module in repo (thin wrapper over Spotify HTTP calls)

LOG = logging.getLogger("spotify_client")


def _retryable(max_attempts=4, initial_backoff=1.0, backoff_factor=2.0, retry_on=(Exception,)):
    """
    Simple retry decorator with exponential backoff. Catches listed exception types.
    """
    def deco(fn: Callable):
        def wrapper(*args, **kwargs):
            attempt = 0
            backoff = initial_backoff
            last_exc = None
            while attempt < max_attempts:
                try:
                    return fn(*args, **kwargs)
                except retry_on as e:
                    last_exc = e
                    attempt += 1
                    if attempt >= max_attempts:
                        LOG.exception("Retry exhausted for %s", fn.__name__)
                        raise
                    LOG.warning("Transient failure in %s: %s — retrying in %.1fs (attempt %d/%d)",
                                fn.__name__, e, backoff, attempt, max_attempts)
                    time.sleep(backoff)
                    backoff *= backoff_factor
            # if loop falls through
            raise last_exc
        return wrapper
    return deco


class SpotifyClient:
    """
    Thin wrapper so PlaylistBuilder can use object-style calls.
    Adds:
      - retrying for transient errors
      - bulk add (100 tracks per request)
      - helpful diagnostics passthroughs
    Assumes an underlying `spotify_api` module is present and implements:
      - get_current_user_id()
      - search_track(q, limit)
      - create_playlist(user_id, name, public, description)
      - add_tracks_to_playlist(playlist_id, uris)    (may accept <=100 uris)
      - find_playlist_by_name(name) -> playlist_id or None  (optional)
    """

    def __init__(self, client_id: str, client_secret: str, refresh_token: str, redirect_uri: Optional[str] = None):
        # keep environment variables for modules that expect them
        os.environ["SPOTIFY_CLIENT_ID"] = client_id
        os.environ["SPOTIFY_CLIENT_SECRET"] = client_secret
        os.environ["SPOTIFY_REFRESH_TOKEN"] = refresh_token
        if redirect_uri:
            os.environ["SPOTIFY_REDIRECT_URI"] = redirect_uri
        self.redirect_uri = redirect_uri

    # ---------- passthroughs with light retry ----------
    @_retryable(max_attempts=4, initial_backoff=1.0, backoff_factor=2.0, retry_on=(Exception,))
    def get_current_user_id(self) -> str:
        if hasattr(spotify_api, "get_current_user_id"):
            return spotify_api.get_current_user_id()
        raise RuntimeError("spotify_api.get_current_user_id not implemented")

    @_retryable(max_attempts=4, initial_backoff=0.8, backoff_factor=2.0, retry_on=(Exception,))
    def search_track(self, q: str, limit: int = 8) -> List[dict]:
        if hasattr(spotify_api, "search_track"):
            return spotify_api.search_track(q, limit)
        # fallback to generic /search if available
        if hasattr(spotify_api, "_api"):
            # best-effort: call /search endpoint
            params = {"q": q, "type": "track", "limit": limit}
            return spotify_api._api("GET", "/search", params=params).get("tracks", {}).get("items", [])
        raise RuntimeError("spotify_api.search_track not available")

    @_retryable(max_attempts=4, initial_backoff=1.0, backoff_factor=2.0, retry_on=(Exception,))
    def create_playlist(self, user_id: str, name: str, public: bool = False, description: str = "") -> Optional[str]:
        if hasattr(spotify_api, "create_playlist"):
            return spotify_api.create_playlist(user_id, name, public, description)
        # fallback to raw _api if available
        if hasattr(spotify_api, "_api"):
            body = {"name": name, "public": public, "description": description}
            resp = spotify_api._api("POST", f"/users/{user_id}/playlists", json=body)
            return resp.get("id")
        raise RuntimeError("spotify_api.create_playlist not available")

    def _chunked(self, seq: List[Any], n: int):
        for i in range(0, len(seq), n):
            yield seq[i:i + n]

    @_retryable(max_attempts=4, initial_backoff=1.0, backoff_factor=2.0, retry_on=(Exception,))
    def add_tracks_to_playlist(self, playlist_id: str, uris: List[str]) -> bool:
        """
        Add tracks in chunks of 100 (Spotify limit). Returns True if all requests succeed.
        """
        if not uris:
            return True

        # Prefer high-level function if available
        add_fn = None
        if hasattr(spotify_api, "add_tracks_to_playlist"):
            add_fn = spotify_api.add_tracks_to_playlist
        elif hasattr(spotify_api, "_api"):
            # we'll implement an ad-hoc POST to /playlists/{playlist_id}/tracks
            def add_fn(pid, chunk):
                return spotify_api._api("POST", f"/playlists/{pid}/tracks", json={"uris": chunk})
        else:
            raise RuntimeError("spotify_api.add_tracks_to_playlist or _api required for adding tracks")

        success = True
        for chunk in self._chunked(uris, 100):
            try:
                res = add_fn(playlist_id, chunk)
                # spotify_api.add_tracks_to_playlist may return truthy or the response dict
                if res is False or res is None:
                    LOG = logging.getLogger("spotify_client")
                    LOG.warning("add_tracks chunk returned falsy: %s", res)
                    success = False
                # otherwise assume ok
            except Exception as e:
                logging.getLogger("spotify_client").exception("Failed to add chunk to playlist: %s", e)
                raise
        return success

    # Backwards-compat convenience
    def add_tracks(self, playlist_id: str, uris: List[str]) -> bool:
        return self.add_tracks_to_playlist(playlist_id, uris)

    # Optional helper used by PlaylistBuilder to avoid duplicate playlists
    def find_playlist_by_name(self, name: str) -> Optional[str]:
        """
        Try to use spotify_api.find_playlist_by_name if available, otherwise try a best-effort scan
        (may be slower). Returns playlist id or None.
        """
        if hasattr(spotify_api, "find_playlist_by_name"):
            return spotify_api.find_playlist_by_name(name)

        # best-effort scan via current user's playlists
        try:
            if hasattr(spotify_api, "_api"):
                # fetch current user id
                user = self.get_current_user_id()
                # iterate user's playlists pages (50 per page)
                limit = 50
                offset = 0
                while True:
                    resp = spotify_api._api("GET", f"/users/{user}/playlists", params={"limit": limit, "offset": offset})
                    items = resp.get("items", []) or []
                    if not items:
                        break
                    for p in items:
                        if p.get("name") == name:
                            return p.get("id")
                    if len(items) < limit:
                        break
                    offset += limit
        except Exception:
            logging.getLogger("spotify_client").exception("find_playlist_by_name fallback failed")
        return None
