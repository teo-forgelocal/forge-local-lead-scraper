"""
The Picker — decides what to scrape today.

Reads config files, agent state, and the city database, then returns the
target city/state/niche/tier for the current run. Also handles the auto-
advance logic:

  - Same niche → same state → same tier, until exhausted
  - Tier exhausted in current state? → escalate tier (if auto_tier_advance)
  - All tiers exhausted in state? → advance to next state, reset to user's original tier
  - All states exhausted with this niche? → flag "country exhausted"

User overrides in config/today.yaml always win over the auto-advance logic.

This module has no external API dependencies — pure file I/O. That makes it
safe to test exhaustively before wiring up Google Places.
"""

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import yaml

# Paths — all relative to the project root
PROJECT_ROOT = Path(__file__).parent.parent
CITIES_DB_PATH = PROJECT_ROOT / "data" / "us_cities.json"
NICHES_PATH = PROJECT_ROOT / "config" / "niches.txt"
STATES_PATH = PROJECT_ROOT / "config" / "states.txt"
TODAY_YAML_PATH = PROJECT_ROOT / "config" / "today.yaml"
AGENT_STATE_PATH = PROJECT_ROOT / "state" / "agent_state.json"


@dataclass
class PickResult:
    """What the picker returns — everything the scraper needs to run."""
    state: str
    niche: str
    tier: int
    city: str
    population: int
    notes: list      # human-readable notes about what happened (e.g. "advanced from Arkansas to Arizona")
    country_exhausted: bool = False  # if True, no more work to do; email user

    def to_summary(self) -> str:
        """A one-line summary for logs/emails."""
        if self.country_exhausted:
            return f"⚠️ Country exhausted for niche '{self.niche}' — no more cities to scrape."
        return (f"Today's target: {self.niche} in {self.city}, {self.state} "
                f"(pop {self.population:,}, Tier {self.tier})")


# ───────────────────────── File loaders ─────────────────────────

def load_cities_db() -> dict:
    """Load the master city database from data/us_cities.json."""
    if not CITIES_DB_PATH.exists():
        raise FileNotFoundError(
            f"City database not found at {CITIES_DB_PATH}. "
            f"Run `python src/build_city_database.py` first."
        )
    with open(CITIES_DB_PATH) as f:
        return json.load(f)


def load_queue_file(path: Path) -> list:
    """Read a queue file (niches.txt or states.txt), ignoring comments and blanks."""
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def load_today_config() -> dict:
    """Load config/today.yaml. Returns empty dict if file missing or empty."""
    if not TODAY_YAML_PATH.exists():
        return {}
    with open(TODAY_YAML_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data


def load_agent_state() -> dict:
    """Load state/agent_state.json. Returns the default schema if missing."""
    if not AGENT_STATE_PATH.exists():
        return _default_agent_state()
    with open(AGENT_STATE_PATH) as f:
        data = json.load(f)
    # Backfill any missing keys (helps if the schema evolves)
    defaults = _default_agent_state()
    for key, value in defaults.items():
        data.setdefault(key, value)
    return data


def save_agent_state(state: dict) -> None:
    """Persist agent state to disk."""
    AGENT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AGENT_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _default_agent_state() -> dict:
    return {
        "current_state": None,
        "current_niche": None,
        "current_tier": None,
        "user_original_tier": None,
        "cities_used": {},
        "last_run_date": None,
        "last_run_status": None,
    }


# ───────────────────────── Core picker logic ─────────────────────────

def pick_today(dry_run: bool = False) -> PickResult:
    """
    Decide what to scrape today.

    Args:
        dry_run: If True, do not persist any state changes to disk.
                 Useful for testing.

    Returns:
        A PickResult describing today's target (or signaling country exhausted).
    """
    cities_db = load_cities_db()
    today_cfg = load_today_config()
    agent_state = load_agent_state()
    niche_queue = load_queue_file(NICHES_PATH)
    state_queue = load_queue_file(STATES_PATH)
    notes = []

    # ── Step 1: Determine the niche ──
    # Precedence: today.yaml override > agent's current niche > first in queue
    chosen_niche = (
        today_cfg.get("niche")
        or agent_state.get("current_niche")
        or (niche_queue[0] if niche_queue else None)
    )
    if not chosen_niche:
        raise ValueError("No niche configured. Add one to config/niches.txt or config/today.yaml.")

    # Detect if user changed niche → reset cities_used for that niche's cycle
    if agent_state.get("current_niche") and agent_state["current_niche"] != chosen_niche:
        notes.append(f"Niche changed: '{agent_state['current_niche']}' → '{chosen_niche}'. "
                     f"Resetting city history for fresh cycle.")
        agent_state["cities_used"] = {}

    # ── Step 2: Determine the user's original tier ──
    # This is the tier the user explicitly chose (in today.yaml or queue defaults).
    # Auto-advance may temporarily escalate above it, but state advance resets to it.
    user_tier = today_cfg.get("tier") or agent_state.get("user_original_tier") or 1
    user_tier = int(user_tier)

    # ── Step 3: Determine the starting state ──
    chosen_state = (
        today_cfg.get("state")
        or agent_state.get("current_state")
        or (state_queue[0] if state_queue else None)
    )
    if not chosen_state:
        raise ValueError("No state configured. Add one to config/states.txt or config/today.yaml.")

    # ── Step 4: Determine the working tier ──
    # If user explicitly set a tier in today.yaml or it's a fresh cycle, use that.
    # Otherwise, use whatever tier the agent was on (which may have auto-escalated).
    if today_cfg.get("tier"):
        working_tier = int(today_cfg["tier"])
    elif agent_state.get("current_tier"):
        working_tier = int(agent_state["current_tier"])
    else:
        working_tier = user_tier

    auto_tier_advance = today_cfg.get("auto_tier_advance", True)

    # ── Step 5: Find a city to scrape ──
    # Walk through: current state, current tier → next tier (if auto-advance) → next state → ...
    result = _find_next_city(
        cities_db=cities_db,
        state_queue=state_queue,
        cities_used=agent_state.get("cities_used", {}),
        starting_state=chosen_state,
        starting_tier=working_tier,
        user_tier=user_tier,
        auto_tier_advance=auto_tier_advance,
        notes=notes,
    )

    if result is None:
        # Country exhausted for this niche
        return PickResult(
            state=chosen_state,
            niche=chosen_niche,
            tier=working_tier,
            city="",
            population=0,
            notes=notes + [f"All states exhausted for niche '{chosen_niche}'. "
                           f"Pick a new niche in config/today.yaml or config/niches.txt."],
            country_exhausted=True,
        )

    final_state, final_tier, city_entry = result

    # ── Step 6: Update agent state ──
    new_state = dict(agent_state)
    new_state["current_state"] = final_state
    new_state["current_niche"] = chosen_niche
    new_state["current_tier"] = final_tier
    new_state["user_original_tier"] = user_tier

    cities_used = new_state.get("cities_used", {})
    cities_used.setdefault(final_state, []).append(city_entry["city"])
    new_state["cities_used"] = cities_used

    if not dry_run:
        save_agent_state(new_state)

    return PickResult(
        state=final_state,
        niche=chosen_niche,
        tier=final_tier,
        city=city_entry["city"],
        population=city_entry["population"],
        notes=notes,
    )


def _find_next_city(
    cities_db: dict,
    state_queue: list,
    cities_used: dict,
    starting_state: str,
    starting_tier: int,
    user_tier: int,
    auto_tier_advance: bool,
    notes: list,
) -> Optional[tuple]:
    """
    Walk through states/tiers to find an unused city.

    Returns (state, tier, city_dict) or None if everything's exhausted.
    """
    # Build the iteration order: start from current state, advance alphabetically,
    # then wrap around from the beginning of the list (skipping the start state
    # to avoid infinite loops if it's already been fully exhausted).
    if starting_state not in state_queue:
        # User chose a state not in our queue (e.g. typo); just try that one.
        states_to_try = [starting_state]
    else:
        start_idx = state_queue.index(starting_state)
        # Slice from start to end, then from beginning to start (wrap),
        # but don't include start_idx twice.
        states_to_try = state_queue[start_idx:] + state_queue[:start_idx]

    for state_idx, state in enumerate(states_to_try):
        if state not in cities_db:
            continue  # state has no cities in our 50k-500k range

        # Determine starting tier for this state
        # If this is the first state we're trying, use the working tier.
        # If we've advanced past the first state, reset to user's original tier.
        if state_idx == 0:
            tier_to_try = starting_tier
        else:
            tier_to_try = user_tier
            if state != starting_state:
                notes.append(f"Advanced from {starting_state} to {state} "
                             f"(reset to Tier {user_tier}).")

        # Walk tiers within this state
        while tier_to_try <= 3:
            city = _find_unused_city_in_tier(
                cities_db[state],
                tier=tier_to_try,
                used_cities=cities_used.get(state, []),
            )
            if city:
                if tier_to_try != starting_tier and state == starting_state:
                    notes.append(f"Tier {starting_tier} exhausted in {state}. "
                                 f"Escalated to Tier {tier_to_try}.")
                return (state, tier_to_try, city)

            # No city found at this tier in this state
            if not auto_tier_advance:
                break  # don't escalate; move to next state
            tier_to_try += 1

        # All tiers exhausted in this state — fall through to next state

    # Nothing found anywhere
    return None


def _find_unused_city_in_tier(state_cities: list, tier: int, used_cities: list):
    """Return the first city in this tier that hasn't been used yet, or None."""
    for city in state_cities:
        if city["tier"] != tier:
            continue
        if city["city"] in used_cities:
            continue
        return city
    return None

# ───────────────────────── Multi-city quota fill ─────────────────────────

def pick_cities_for_quota(
    target_count: int,
    typical_results_per_city: int = 15,
    dry_run: bool = False,
) -> tuple[list[dict], str, int, list[str]]:
    """
    Pick a SEQUENCE of cities to scrape until we expect to hit `target_count` leads.

    Walks through tiers and states using the same auto-advance logic as
    pick_today(), but returns multiple cities at once instead of just one.

    Args:
        target_count: total lead quota for the day (e.g. 200).
        typical_results_per_city: heuristic for how many results Google
            typically returns per city query. Used to estimate how many
            cities we need. Defaults to 30 — conservative, errs on the
            side of picking too many (which is harmless, we stop scraping
            when we hit target_count).
        dry_run: if True, do not persist any state changes.

    Returns:
        (city_targets, niche, user_tier, notes)
        where city_targets is a list of dicts like:
        [
          {"city": "Fayetteville", "state": "Arkansas", "tier": 1, "population": 78960},
          {"city": "Fort Smith", "state": "Arkansas", "tier": 1, "population": 87650},
          ...
        ]
        These cities will all be scraped in order, and city_targets[i] is
        the i-th city to visit.
    """
    cities_db = load_cities_db()
    today_cfg = load_today_config()
    agent_state = load_agent_state()
    niche_queue = load_queue_file(NICHES_PATH)
    state_queue = load_queue_file(STATES_PATH)
    notes = []

    # Determine niche
    chosen_niche = (
        today_cfg.get("niche")
        or agent_state.get("current_niche")
        or (niche_queue[0] if niche_queue else None)
    )
    if not chosen_niche:
        raise ValueError("No niche configured.")

    # Reset cities_used if niche changed
    if agent_state.get("current_niche") and agent_state["current_niche"] != chosen_niche:
        notes.append(f"Niche changed: '{agent_state['current_niche']}' → '{chosen_niche}'. "
                     f"Resetting city history.")
        agent_state["cities_used"] = {}

    # User's original tier
    user_tier = today_cfg.get("tier") or agent_state.get("user_original_tier") or 1
    user_tier = int(user_tier)

    # Starting state
    starting_state = (
        today_cfg.get("state")
        or agent_state.get("current_state")
        or (state_queue[0] if state_queue else None)
    )
    if not starting_state:
        raise ValueError("No state configured.")

    # Starting tier (might be escalated from prior runs)
    if today_cfg.get("tier"):
        working_tier = int(today_cfg["tier"])
    elif agent_state.get("current_tier"):
        working_tier = int(agent_state["current_tier"])
    else:
        working_tier = user_tier

    auto_tier_advance = today_cfg.get("auto_tier_advance", True)

    # Estimate how many cities we need
    # Estimate, then pad by 50% to have fallbacks when individual cities don't fill.
    # Also enforce a minimum of 3 cities for any non-trivial target.
    base_estimate = -(-target_count // typical_results_per_city)  # ceiling div
    cities_needed = max(3, int(base_estimate * 1.5))

    # Walk through state queue (with wrap-around) collecting cities
    if starting_state in state_queue:
        start_idx = state_queue.index(starting_state)
        states_to_try = state_queue[start_idx:] + state_queue[:start_idx]
    else:
        states_to_try = [starting_state] + state_queue

    cities_used = dict(agent_state.get("cities_used", {}))
    picked: list[dict] = []
    # Working state for tracking what tier/state we end on (for state persistence)
    final_state = starting_state
    final_tier = working_tier

    for state_idx, state in enumerate(states_to_try):
        if state not in cities_db:
            continue

        # First state uses working_tier (possibly escalated), subsequent reset to user_tier
        tier_to_try = working_tier if state_idx == 0 else user_tier

        while tier_to_try <= 3:
            # Find ALL unused cities in this tier (not just first one)
            cities_in_tier = [
                c for c in cities_db[state]
                if c["tier"] == tier_to_try
                and c["city"] not in cities_used.get(state, [])
            ]

            for city in cities_in_tier:
                picked.append({
                    "city": city["city"],
                    "state": state,
                    "tier": tier_to_try,
                    "population": city["population"],
                })
                cities_used.setdefault(state, []).append(city["city"])
                final_state = state
                final_tier = tier_to_try

                if len(picked) >= cities_needed:
                    break

            if len(picked) >= cities_needed:
                break

            if not auto_tier_advance:
                break
            tier_to_try += 1

        if len(picked) >= cities_needed:
            break

        # If we advanced past the starting state, note it
        if state != starting_state and state_idx > 0 and state_idx == 1:
            notes.append(f"Advanced from {starting_state} to {state} (reset to Tier {user_tier}).")

    if not picked:
        notes.append(f"⚠️ No more cities available for '{chosen_niche}'. "
                     f"Edit config/today.yaml or config/niches.txt.")
        return [], chosen_niche, user_tier, notes

    # Persist the final state we ended on
    new_state = dict(agent_state)
    new_state["current_state"] = final_state
    new_state["current_niche"] = chosen_niche
    new_state["current_tier"] = final_tier
    new_state["user_original_tier"] = user_tier
    new_state["cities_used"] = cities_used

    if not dry_run:
        save_agent_state(new_state)

    notes.append(f"Picked {len(picked)} cities to reach ~{target_count} leads "
                 f"(est. {typical_results_per_city}/city).")
    return picked, chosen_niche, user_tier, notes

# ───────────────────────── CLI entry point ─────────────────────────

if __name__ == "__main__":
    """Run the picker and print today's target. Use --dry-run to avoid persisting state."""
    dry_run = "--dry-run" in sys.argv
    result = pick_today(dry_run=dry_run)
    print(result.to_summary())
    if result.notes:
        print("\nNotes:")
        for note in result.notes:
            print(f"  - {note}")
    if dry_run:
        print("\n(dry run — agent state was NOT updated)")