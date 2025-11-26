import json
import time
import base64
import requests
from pathlib import Path

# Path to your config file (adjust if needed)
CONFIG_PATH = Path("config.json")

# Cached token so your app doesn't hit Spotify every time
_cached_access_token = None
_cached_expiry = 0


def _load_config():
    """Load client credentials and refresh token from config.json."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"config.json not found at: {CONFIG_PATH.resolve()}"
        )

    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    required = ["client_id", "client_secret", "refresh_token"]
    for key in required:
        if key not in config or not config[key]:
            raise ValueError(f"Missing required field in config.json: '{key}'")

    return config


def _request_new_access_token(client_id, client_secret, refresh_token):
    """Call Spotify and exchange refresh token for new access token."""
    token_url = "https://accounts.spotify.com/api/token"

    # Spotify requires client_id:client_secret base64 encoded
    auth_header = base64.b64encode(
        f"{client_id}:{client_secret}".encode()
    ).decode()

    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }

    response = requests.post(token_url, headers=headers, data=data)

    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to refresh Spotify token: {response.status_code} — {response.text}"
        )

    payload = response.json()

    if "access_token" not in payload:
        raise RuntimeError(
            f"Spotify response missing access token: {payload}"
        )

    access_token = payload["access_token"]
    expires_in = payload.get("expires_in", 3600)  # seconds

    return access_token, expires_in


def get_access_token():
    """
    Returns a valid Spotify access token.

    - Uses cached token if still val
