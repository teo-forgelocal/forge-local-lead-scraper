# Forge Local — Lead Scraper Agent

Automated daily agent that scrapes local business leads with weak or missing websites.

## What it does

Each day, the agent:
1. Picks a city (population 50k–250k) in the current target state
2. Scrapes local businesses in the current target niche using Google Places API
3. Checks each business's website (or lack thereof) and scores quality 1–10
4. Writes results to a Google Sheet, ranked by lead opportunity
5. Emails the sheet link as a daily summary

## Weekly rhythm

- One state per week
- One niche per week
- Seven cities per week (one per day)

## Status

🚧 Under construction