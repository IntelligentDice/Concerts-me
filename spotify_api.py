import os
import time
import base64
import requests

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

SESSION = requests.Session()


def _log(msg):
    print(f"[SPOTIFY] {msg}")


def get_access_token():
    """
    Fetch Spotify access token with retries and full diagnostic logging.
    """

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise RuntimeError("SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET missing!")

    auth_header = base64.b64encode(
        f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    ).decode()

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    payload = {"grant_type": "client_credentials"}

    # Retry loop
    for attempt in range(1, 4):
        try:
            _log(f"Requesting token (attempt {attempt}/3)")

            resp = SESSION.post(
                SPOTIFY_TOKEN_URL,
                headers=headers,
                data=payload,
                timeout=10,
            )

            # If Spotify responds:
            if resp.status_code != 200:
                _log(f"Non-200 response: {resp.status_code}")
                _log(f"Response text: {resp.text}")

                if resp.status_code in (400, 401):
                    raise RuntimeError(
                        f"Spotify rejected credentials: {resp.text}"
                    )

                # Retry on 429, 500, 502, 503, 504
                if resp.status_code >= 500 or resp.status_code == 429:
                    time.sleep(1 * attempt)
                    continue

                raise RuntimeError(f"Spotify token error: {resp.text}")

            # Try parsing JSON
            try:
                data = resp.json()
            except Exception as ex:
                _log(f"JSON decode failure: {ex}")
                _log(f"Raw response: {resp.text}")
                time.sleep(1 * attempt)
                continue

            token = data.get("access_token")
            if not token:
                _log(f"No access_token in payload: {data}")
                time.sleep(1 * attempt)
                continue

            _log("Token acquired successfully.")
            return token

        except requests.exceptions.RequestException as ex:
            _log(f"Network error on attempt {attempt}: {repr(ex)}")
            time.sleep(1 * attempt)

    # If we get here, all retries failed
    raise RuntimeError("Spotify token refresh failed after 3 attempts.")


def _api(method, path, params=None, json=None):
    """
    Call Spotify API with token, retry token on 401, and log issues.
    """

    token = get_access_token()

    headers = {"Authorization": f"Bearer {token}"}

    full_url = f"https://api.spotify.com/v1{path}"

    try:
        resp = SESSION.request(
            method,
            full_url,
            params=params,
            json=json,
            headers=headers,
            timeout=10,
        )

    except requests.exceptions.RequestException as ex:
        raise RuntimeError(f"Spotify API network error: {repr(ex)}")

    # Refresh token if expired
    if resp.status_code == 401:
        _log("Token expired — refreshing...")
        token = get_access_token()
        headers["Authorization"] = f"Bearer {token}"

        resp = SESSION.request(
            method,
            full_url,
            params=params,
            json=json,
            headers=headers,
            timeout=10,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Spotify API error {resp.status_code}: {resp.text}"
        )

    try:
        return resp.json()
    except Exception as ex:
        raise RuntimeError(f"Invalid JSON from Spotify: {resp.text}")
