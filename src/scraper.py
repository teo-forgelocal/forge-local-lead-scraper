"""
The Scraper — fetches local businesses from Google Places API (New).

Given a target city/state/niche, queries Google Places API and returns
a list of Business objects with all the data we need to score them later.

This module is purely a data-fetching layer. It does NOT:
  - Score websites
  - Check for broken URLs
  - Make any judgments about lead quality

It just pulls the raw business data. Scoring happens in scorer.py.
"""

import os
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Load environment variables from .env (the GOOGLE_MAPS_API_KEY)
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Google Places API (New) endpoint
PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Fields we want returned for each business.
# IMPORTANT: each field listed here counts toward billing tier.
# Basic fields (name, formatted address, place_id, types) are cheap.
# Contact fields (phone, website) are mid-tier.
# Atmosphere fields (rating, reviews) are most expensive.
# We need all three tiers, but we deliberately do NOT request fields we don't use.
PLACES_FIELDS = ",".join([
    # Basic
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.googleMapsUri",
    "places.businessStatus",
    # Contact
    "places.internationalPhoneNumber",
    "places.nationalPhoneNumber",
    "places.websiteUri",
    # Atmosphere
    "places.rating",
    "places.userRatingCount",
])


@dataclass
class Business:
    """A single business returned from the Places API."""
    place_id: str
    name: str
    address: str
    google_maps_url: str
    phone: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    business_status: Optional[str] = None  # OPERATIONAL / CLOSED_TEMPORARILY / CLOSED_PERMANENTLY

    @classmethod
    def from_places_api(cls, place: dict) -> "Business":
        """Build a Business from a Places API response object."""
        return cls(
            place_id=place.get("id", ""),
            name=place.get("displayName", {}).get("text", ""),
            address=place.get("formattedAddress", ""),
            google_maps_url=place.get("googleMapsUri", ""),
            phone=place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber"),
            website=place.get("websiteUri"),
            rating=place.get("rating"),
            review_count=place.get("userRatingCount"),
            business_status=place.get("businessStatus"),
        )


def scrape_businesses(
    niche: str,
    city: str,
    state: str,
    max_results: int = 200,
    verbose: bool = True,
) -> list[Business]:
    """
    Scrape businesses matching `niche` in `city, state` via Google Places API.

    Args:
        niche: e.g. "barbers", "dentists"
        city: e.g. "Fayetteville"
        state: e.g. "Arkansas"
        max_results: hard cap on businesses returned (default 200)
        verbose: print progress

    Returns:
        List of Business objects (may be shorter than max_results if the
        city doesn't have that many businesses in this niche).
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_MAPS_API_KEY not found in environment. "
            "Make sure .env exists in the project root and contains the key."
        )

    query = f"{niche} in {city}, {state}"
    if verbose:
        print(f"🔎 Searching: {query}")
        print(f"   Max results: {max_results}")

    businesses: list[Business] = []
    next_page_token: Optional[str] = None
    api_calls = 0

    # Places API (New) paginates with a nextPageToken. Each call returns up to 20 results.
    # We loop until we hit max_results, run out of results, or hit the API's pagination limit.
    while len(businesses) < max_results:
        payload = {
            "textQuery": query,
            "pageSize": min(20, max_results - len(businesses)),  # max 20 per call
        }
        if next_page_token:
            payload["pageToken"] = next_page_token

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": PLACES_FIELDS,
        }

        try:
            response = requests.post(
                PLACES_TEXT_SEARCH_URL,
                json=payload,
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as e:
            print(f"⚠️  Network error: {e}", file=sys.stderr)
            break

        api_calls += 1

        if response.status_code != 200:
            # Common reasons: invalid key, restricted key, billing not active
            print(f"⚠️  API error {response.status_code}: {response.text[:500]}",
                  file=sys.stderr)
            break

        data = response.json()
        page_places = data.get("places", [])

        if not page_places:
            if verbose:
                print(f"   No more results (got {len(businesses)} total).")
            break

        for place in page_places:
            businesses.append(Business.from_places_api(place))
            if len(businesses) >= max_results:
                break

        if verbose:
            print(f"   API call {api_calls}: +{len(page_places)} results "
                  f"(running total: {len(businesses)})")

        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

        # Google requires a brief pause between paginated calls (their docs say ~2 sec
        # for the page token to become valid). Be polite.
        time.sleep(2)

    if verbose:
        print(f"✅ Done. {len(businesses)} businesses in {api_calls} API calls.")
        # Rough cost estimate for the user's awareness
        est_cost = api_calls * 0.032
        print(f"   Estimated cost: ${est_cost:.3f} (will be absorbed by $200 free monthly credit)")

    return businesses


# ───────────────────────── CLI entry point for testing ─────────────────────────

if __name__ == "__main__":
    """
    Run the scraper directly for testing.

    Usage:
        python src/scraper.py "barbers" "Fayetteville" "Arkansas" 10

    Args (positional, all required for CLI mode):
        niche, city, state, max_results
    """
    if len(sys.argv) != 5:
        print("Usage: python src/scraper.py <niche> <city> <state> <max_results>")
        print('Example: python src/scraper.py "barbers" "Fayetteville" "Arkansas" 10')
        sys.exit(1)

    niche = sys.argv[1]
    city = sys.argv[2]
    state = sys.argv[3]
    max_results = int(sys.argv[4])

    businesses = scrape_businesses(niche, city, state, max_results=max_results)

    print("\n────────── Results ──────────")
    for i, biz in enumerate(businesses, 1):
        print(f"\n{i}. {biz.name}")
        print(f"   Address:  {biz.address}")
        print(f"   Phone:    {biz.phone or '(none listed)'}")
        print(f"   Website:  {biz.website or '⚠️  NO WEBSITE'}")
        print(f"   Rating:   {biz.rating or 'n/a'} ({biz.review_count or 0} reviews)")
        print(f"   Status:   {biz.business_status or 'unknown'}")