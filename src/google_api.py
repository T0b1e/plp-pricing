"""Thin wrappers over the three Google Maps Platform endpoints we use."""
import os
import time

import requests
from dotenv import load_dotenv

from .paths import ROOT

load_dotenv(ROOT / ".env")

# Thailand bounding box
TH_LOW = {"latitude": 5.5, "longitude": 97.3}
TH_HIGH = {"latitude": 20.5, "longitude": 105.7}


RETRY_STATUS = (429, 500, 502, 503, 504)


class ApiDisabled(RuntimeError):
    """The API is not enabled on the Google Cloud project behind the key."""


class MissingKey(RuntimeError):
    """No GOOGLE_MAPS_API_KEY; stops the whole run rather than failing every lookup."""


class ApiError(RuntimeError):
    """One request failed after retries (network, quota, bad response). Safe to skip and retry later."""


def api_key() -> str:
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key:
        raise MissingKey(
            "GOOGLE_MAPS_API_KEY is not set. Put it in .env:\n"
            "    GOOGLE_MAPS_API_KEY=AIza...\n"
            "and enable Places API (New), Geocoding API and Routes API on the project."
        )
    return key


def in_thailand(lat, lon) -> bool:
    return TH_LOW["latitude"] <= lat <= TH_HIGH["latitude"] and TH_LOW["longitude"] <= lon <= TH_HIGH["longitude"]


def _request(method, url, retries=3, **kw) -> requests.Response:
    """Send with backoff on network errors and 429/5xx. Returns the last response otherwise."""
    for attempt in range(retries):
        try:
            r = requests.request(method, url, timeout=30, **kw)
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise ApiError(f"{url.split('/')[2]}: {type(e).__name__}: {str(e)[:200]}") from e
        if r.status_code in RETRY_STATUS and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue
        return r


def _json(r: requests.Response, url: str) -> dict:
    try:
        return r.json()
    except ValueError:
        raise ApiError(f"{url} -> HTTP {r.status_code}, not JSON: {r.text[:200]}") from None


def _post(url, body, field_mask, retries=3):
    r = _request("POST", url, retries, json=body,
                 headers={"X-Goog-Api-Key": api_key(), "X-Goog-FieldMask": field_mask})
    if r.status_code == 403 and ("has not been used" in r.text or "disabled" in r.text):
        raise ApiDisabled(url.split("/")[2])
    if r.status_code >= 400:
        raise ApiError(f"{url} -> HTTP {r.status_code}: {r.text[:300]}")
    return _json(r, url)


def places_text_search(query: str) -> dict:
    return _post(
        "https://places.googleapis.com/v1/places:searchText",
        {
            "textQuery": query,
            "languageCode": "th",
            "regionCode": "TH",
            "pageSize": 3,
            "locationRestriction": {"rectangle": {"low": TH_LOW, "high": TH_HIGH}},
        },
        "places.id,places.displayName,places.formattedAddress,places.location,places.types",
    )


def geocode(query: str) -> dict:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    r = _request("GET", url, params={"address": query, "key": api_key(), "language": "th", "region": "th",
                                     "components": "country:TH"})
    if r.status_code >= 400:
        raise ApiError(f"{url} -> HTTP {r.status_code}: {r.text[:300]}")
    data = _json(r, url)
    status = data.get("status")
    if status == "REQUEST_DENIED":
        raise ApiDisabled(f"geocoding: {data.get('error_message', '')[:150]}")
    # OK and ZERO_RESULTS are answers; anything else (quota, UNKNOWN_ERROR) must not be cached
    if status not in ("OK", "ZERO_RESULTS"):
        raise ApiError(f"geocoding '{query}': {status} {data.get('error_message', '')[:150]}")
    return data


def route_distance(o_lat, o_lon, d_lat, d_lon) -> dict:
    point = lambda lat, lon: {"location": {"latLng": {"latitude": lat, "longitude": lon}}}
    return _post(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        {
            "origin": point(o_lat, o_lon),
            "destination": point(d_lat, d_lon),
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_UNAWARE",
            "units": "METRIC",
        },
        "routes.distanceMeters,routes.duration",
    )
