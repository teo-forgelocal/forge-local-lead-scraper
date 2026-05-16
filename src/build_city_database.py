"""
One-time script to build the US city database.

Pulls city population data from a public GitHub-hosted dataset
(maintained by millbj92/US-Zip-Codes-JSON and similar open-source projects),
filters to cities with population between 50,000 and 500,000, tags each
with a market tier, and saves to data/us_cities.json.

Source: Plotly's public datasets repository on GitHub
  https://github.com/plotly/datasets — stable, MIT-style usage, no auth required.

Tier system (cities are tagged for strategic targeting):
  - Tier 1: 50k-150k    → underserved markets, lowest competition
  - Tier 2: 150k-300k   → mid-size, moderate competition
  - Tier 3: 300k-500k   → larger markets, higher competition

Run this script once. The agent reads the resulting JSON at runtime.
Re-run only when you want to refresh population data (yearly at most).
"""

import csv
import json
import sys
from io import StringIO
from pathlib import Path

import requests

# Population filter range
MIN_POPULATION = 50_000
MAX_POPULATION = 500_000

# Tier boundaries
TIER_1_MAX = 150_000
TIER_2_MAX = 300_000

# Public dataset of US cities with population, lat/lng, and state.
# Hosted by Plotly's datasets repo — stable URL, no auth required.
DATA_URL = "https://raw.githubusercontent.com/plotly/datasets/master/us-cities-top-1k.csv"

# Where to save the output
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "us_cities.json"


def fetch_csv():
    """Download the CSV of US cities."""
    print(f"Downloading city data from public GitHub dataset...")
    response = requests.get(DATA_URL, timeout=30)
    response.raise_for_status()
    print(f"Downloaded {len(response.text):,} characters of CSV data.")
    return response.text


def assign_tier(population):
    """Assign a market tier based on population."""
    if population <= TIER_1_MAX:
        return 1
    elif population <= TIER_2_MAX:
        return 2
    else:
        return 3


def parse_and_filter(csv_text):
    """
    Parse the CSV and filter to cities in our population range.

    CSV columns: City, State, Population, lat, lon
    """
    reader = csv.DictReader(StringIO(csv_text))

    cities_by_state = {}
    total_seen = 0
    total_kept = 0
    tier_counts = {1: 0, 2: 0, 3: 0}

    for row in reader:
        total_seen += 1
        try:
            population = int(float(row["Population"]))
        except (ValueError, KeyError, TypeError):
            continue

        if not (MIN_POPULATION <= population <= MAX_POPULATION):
            continue

        state_name = row.get("State", "").strip()
        city_name = row.get("City", "").strip()

        if not state_name or not city_name:
            continue

        tier = assign_tier(population)
        tier_counts[tier] += 1

        city_entry = {
            "city": city_name,
            "population": population,
            "tier": tier,
        }

        # Include lat/lng if present in the dataset
        try:
            city_entry["lat"] = float(row["lat"])
            city_entry["lng"] = float(row["lon"])
        except (KeyError, ValueError, TypeError):
            pass

        cities_by_state.setdefault(state_name, []).append(city_entry)
        total_kept += 1

    # Sort each state's cities by population, ascending (smallest first)
    for state in cities_by_state:
        cities_by_state[state].sort(key=lambda c: c["population"])

    print(f"Saw {total_seen:,} total cities, kept {total_kept:,} in our range.")
    print(f"Coverage: {len(cities_by_state)} states/territories.")
    print(f"Tier breakdown:")
    print(f"  Tier 1 (50k-150k):   {tier_counts[1]:,} cities — underserved markets")
    print(f"  Tier 2 (150k-300k):  {tier_counts[2]:,} cities — mid-size markets")
    print(f"  Tier 3 (300k-500k):  {tier_counts[3]:,} cities — larger markets")

    return cities_by_state


def save_json(data):
    """Write the city database to disk as pretty-printed JSON."""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved to {OUTPUT_PATH}")


def main():
    csv_text = fetch_csv()
    data = parse_and_filter(csv_text)
    save_json(data)
    print("\nDone! City database built successfully.")

    # Print a quick sample so you can sanity-check the result
    print("\nQuick sample (showing tier distribution in 3 states):")
    sample_states = ["Arkansas", "Colorado", "Nevada"]
    for state in sample_states:
        if state not in data:
            continue
        cities = data[state]
        by_tier = {1: 0, 2: 0, 3: 0}
        for c in cities:
            by_tier[c["tier"]] += 1
        print(f"  {state}: {len(cities)} cities total — "
              f"T1: {by_tier[1]}, T2: {by_tier[2]}, T3: {by_tier[3]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)