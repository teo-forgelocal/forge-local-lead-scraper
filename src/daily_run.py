"""
The daily entry point — runs the agent end-to-end.

Three modes:

  Default:       python src/daily_run.py
                 Uses today.yaml or queue files. Fully automatic.

  Configure:     python src/daily_run.py --configure
                 Interactive prompts for state/niche/tier.
                 Saves answers to today.yaml. Then runs.

  Dry run:       python src/daily_run.py --dry-run
                 Show what would happen. No scrape, no sheet, no email.

The Mac app launcher wraps this script. You can also call it directly
from the terminal.
"""

import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Local imports
sys.path.insert(0, str(Path(__file__).parent))
from picker import (
    pick_today,
    pick_cities_for_quota,
    load_queue_file,
    load_today_config,
    NICHES_PATH,
    STATES_PATH,
    TODAY_YAML_PATH,
)
from scraper import scrape_businesses
from email_finder import enrich_with_emails
from scorer import score_businesses, Bucket
from sheets import create_leads_sheet
from emailer import send_daily_report
from ghl import push_leads


# ───────────────────────── Pretty CLI helpers ─────────────────────────

def _banner(text: str) -> None:
    print()
    print("─" * 64)
    print(text)
    print("─" * 64)


def _info(text: str) -> None:
    print(f"   {text}")


# ───────────────────────── Interactive configure ─────────────────────────

def _prompt_choice(prompt: str, options: list[str], default: str = None) -> str:
    """Prompt user to pick from a numbered list. Returns the chosen string."""
    print()
    print(prompt)
    for i, opt in enumerate(options, 1):
        marker = " (default)" if opt == default else ""
        print(f"  {i:2d}. {opt}{marker}")

    while True:
        raw = input(f"\nEnter number 1-{len(options)} (or press Enter for default): ").strip()
        if not raw and default:
            return default
        try:
            n = int(raw)
            if 1 <= n <= len(options):
                return options[n - 1]
        except ValueError:
            pass
        print(f"   ⚠️  Please enter a number between 1 and {len(options)}.")


def _prompt_tier(default: int = 1) -> int:
    """Prompt for tier 1/2/3."""
    print()
    print("Select market tier:")
    print("  1. Tier 1 (50k-150k) — underserved markets, lowest competition")
    print("  2. Tier 2 (150k-300k) — mid-size markets")
    print("  3. Tier 3 (300k-500k) — larger markets")
    while True:
        raw = input(f"\nEnter 1, 2, or 3 (press Enter for {default}): ").strip()
        if not raw:
            return default
        if raw in ("1", "2", "3"):
            return int(raw)
        print("   ⚠️  Please enter 1, 2, or 3.")


def _prompt_max_leads(default: int = 200) -> int:
    """Prompt for daily lead cap."""
    print()
    raw = input(f"Max leads to scrape today (press Enter for {default}): ").strip()
    if not raw:
        return default
    try:
        n = int(raw)
        if 1 <= n <= 500:
            return n
    except ValueError:
        pass
    print(f"   Using default of {default}.")
    return default


def configure_today_yaml() -> dict:
    """Walk the user through configuring today's run. Saves to today.yaml."""
    _banner("⚙️  CONFIGURE TODAY'S RUN")

    # Load options from queue files
    states = load_queue_file(STATES_PATH)
    niches = load_queue_file(NICHES_PATH)

    # Load current today.yaml to use as defaults
    current = load_today_config()
    default_state = current.get("state") or (states[0] if states else None)
    default_niche = current.get("niche") or (niches[0] if niches else None)
    default_tier = current.get("tier") or 1
    default_max = current.get("max_leads") or 200

    # State
    state = _prompt_choice("Select state:", states, default=default_state)

    # Niche
    niche = _prompt_choice("Select niche:", niches, default=default_niche)

    # Tier
    tier = _prompt_tier(default=int(default_tier))

    # Max leads
    max_leads = _prompt_max_leads(default=int(default_max))

    # Write to today.yaml
    config = {
        "state": state,
        "niche": niche,
        "tier": tier,
        "max_leads": max_leads,
        "auto_tier_advance": current.get("auto_tier_advance", True),
    }

    with open(TODAY_YAML_PATH, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

    print()
    print(f"✅ Saved to {TODAY_YAML_PATH.relative_to(PROJECT_ROOT)}")
    return config


# ───────────────────────── Mode: dry run ─────────────────────────

def run_dry() -> None:
    """Show what the agent would do. No scrape, no sheet, no email."""
    _banner("🔍 DRY RUN — Planning today's target")

    pick = pick_today(dry_run=True)
    print()
    print(pick.to_summary())
    if pick.notes:
        print("\nNotes:")
        for note in pick.notes:
            print(f"  - {note}")
    print("\n(Nothing was scraped, no sheet created, no email sent.)")


# ───────────────────────── Mode: full run ─────────────────────────

def run_full(to_email: str) -> None:
    """The real thing: pick multiple cities, scrape until quota, score, sheet, email."""
    max_leads = int(load_today_config().get("max_leads") or 200)

    # Phase 0 — Pick a sequence of cities
    _banner("🎯 PHASE 0: Planning today's run")
    city_targets, niche, tier, notes = pick_cities_for_quota(
        target_count=max_leads,
        dry_run=False,
    )

    for note in notes:
        _info(f"• {note}")

    if not city_targets:
        print()
        print("⚠️  No cities available. Edit config/today.yaml or config/niches.txt.")
        return

    print()
    _info(f"Niche:     {niche}")
    _info(f"Target:    {max_leads} leads")
    _info(f"Cities planned ({len(city_targets)}):")
    for c in city_targets:
        _info(f"   • {c['city']}, {c['state']} (pop {c['population']:,}, T{c['tier']})")

    # Phase 1 — Scrape cities until we hit the lead cap
    _banner("🔎 PHASE 1: Scraping")
    all_businesses = []
    cities_scraped = []

    for c in city_targets:
        remaining = max_leads - len(all_businesses)
        if remaining <= 0:
            break

        print()
        _info(f"📍 {c['city']}, {c['state']} (T{c['tier']}) — need {remaining} more leads")
        results = scrape_businesses(
            niche=niche,
            city=c["city"],
            state=c["state"],
            max_results=remaining,
            verbose=False,
        )
        _info(f"   → got {len(results)} leads from {c['city']}")
        all_businesses.extend(results)
        cities_scraped.append(c)

        if not results:
            # Google returned nothing — niche is dead in this city, skip
            continue

    if not all_businesses:
        print()
        print("⚠️  No businesses returned across any city. Aborting.")
        return

    print()
    _info(f"Total leads scraped: {len(all_businesses)} across {len(cities_scraped)} cities.")

    # Phase 1.5 — Enrich with emails (scrape each business's own website)
    # Places gives us phone but not email; CRM import needs email where it exists.
    _banner("✉️  PHASE 1.5: Finding emails")
    with_site = sum(1 for b in all_businesses if b.website)
    _info(f"Visiting {with_site} business websites to extract contact emails...")
    found = enrich_with_emails(all_businesses, verbose=False)
    rate = (found / with_site * 100) if with_site else 0
    _info(f"Found {found} emails ({rate:.0f}% of {with_site} with a website).")
    _info(f"{len(all_businesses) - with_site} have no website — reached by phone instead.")

    # Phase 2 — Score
    _banner("⚖️  PHASE 2: Scoring websites")
    scored = score_businesses(all_businesses, verbose=False)
    by_bucket = {Bucket.HOT: 0, Bucket.WARM: 0, Bucket.COOL: 0}
    for s in scored:
        by_bucket[s.bucket] += 1
    _info(f"temp:hot: {by_bucket[Bucket.HOT]}   "
          f"temp:warm: {by_bucket[Bucket.WARM]}   "
          f"temp:cool: {by_bucket[Bucket.COOL]}")

    # Phase 3 — Sheet
    _banner("📊 PHASE 3: Creating Google Sheet")
    # For sheet title, use the primary (first) city if just one, or "[N cities]" if multiple
    if len(cities_scraped) == 1:
        primary_city = cities_scraped[0]["city"]
        primary_state = cities_scraped[0]["state"]
    else:
        primary_city = f"{len(cities_scraped)} cities"
        primary_state = cities_scraped[0]["state"]

    sheet_url = create_leads_sheet(
        scored_leads=scored,
        niche=niche,
        city=primary_city,
        state=primary_state,
        tier=tier,
        share_with=to_email,
    )

    # Phase 4 — Email
    _banner("📧 PHASE 4: Sending email")
    send_daily_report(
        scored=scored,
        niche=niche,
        city=primary_city,
        state=primary_state,
        tier=tier,
        sheet_url=sheet_url,
        to_email=to_email,
    )

    # Phase 5 — Push to GHL (opt-in; skipped automatically if GHL_API_TOKEN isn't set)
    _banner("📥 PHASE 5: Pushing to GHL")
    push_leads(scored, state=primary_state, niche=niche, verbose=True)

    # Done
    print()
    print("─" * 64)
    print(f"🎉 Daily run complete!")
    print(f"   Niche:    {niche}")
    print(f"   Cities:   {len(cities_scraped)} ({', '.join(c['city'] for c in cities_scraped)})")
    print(f"   Scraped:  {len(all_businesses)} leads")
    print(f"   Sheet:    {sheet_url}")
    print(f"   Email:    sent to {to_email}")
    print("─" * 64)


# ───────────────────────── CLI ─────────────────────────

def _get_default_email() -> str:
    """Get the user's email from env or prompt."""
    email = os.environ.get("REPORT_EMAIL")
    if email:
        return email
    print()
    print("⚠️  REPORT_EMAIL not found in .env.")
    email = input("   Enter the email to send reports to: ").strip()
    return email


def main():
    args = sys.argv[1:]

    if "--dry-run" in args:
        run_dry()
        return

    if "--configure" in args:
        configure_today_yaml()
        # After configuring, also do the real run
        print()
        proceed = input("Run the agent now with this config? [Y/n]: ").strip().lower()
        if proceed in ("", "y", "yes"):
            email = _get_default_email()
            run_full(to_email=email)
        else:
            print("OK, skipping run. Just call `python src/daily_run.py` later to launch.")
        return

    # Default: just run with current config
    email = _get_default_email()
    run_full(to_email=email)


if __name__ == "__main__":
    main()