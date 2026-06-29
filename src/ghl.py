"""
The GHL Pusher — sends scored leads into GoHighLevel.

After the scraper scores a batch, this module upserts each lead as a GHL
contact (with the scraper's data mapped onto custom fields) and drops an
opportunity into the prospecting pipeline at the "New (Scraped)" stage.

Designed for the per-state sub-account model:
  - One GHL sub-account per state. The Colorado director's run pushes Colorado
    leads into the Colorado sub-account, etc.
  - Pipeline, stage, and custom fields are resolved BY NAME / FIELD KEY at run
    time — never hard-coded IDs — so the SAME code works against every state's
    sub-account, as long as each is cloned from the same GHL snapshot.

Dedup:
  - In-batch: collapse duplicate leads (same place_id / phone / email) before
    sending.
  - Cross-run: handled by GHL itself when the sub-account is set to block
    duplicate contacts (email/phone) and duplicate opportunities — which the
    Forge Local snapshot is. Re-runs update instead of duplicating.

Opt-in: if GHL_API_TOKEN isn't set in .env, the whole step is skipped and the
run behaves exactly as before (sheet + email only). Nothing breaks for users
who haven't set up GHL yet.

Auth: a GHL Private Integration Token for the sub-account (Settings → Private
Integrations), scoped to contacts + opportunities. Uses the LeadConnector v2
API.
"""

import os
import sys
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from scorer import ScoredBusiness, Bucket

# LeadConnector v2 (GHL) API
API_BASE = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"
REQUEST_TIMEOUT = 20

# Defaults — overridable via .env so a snapshot that renames things still works.
DEFAULT_PIPELINE_MATCH = os.environ.get("GHL_PIPELINE_NAME", "Prospecting")
DEFAULT_STAGE_MATCH = os.environ.get("GHL_STAGE_NAME", "New (Scraped)")
LEAD_SOURCE_VALUE = "Cold Scraper - Google Maps"

# Scraper niche strings → GHL "Niche" picklist values.
NICHE_PICKLIST = {
    "barber": "Barbershop", "barbers": "Barbershop", "barber shop": "Barbershop",
    "barbershop": "Barbershop",
    "salon": "Salon", "hair salon": "Salon",
    "med spa": "Med Spa", "medspa": "Med Spa",
    "dentist": "Dentist", "dentists": "Dentist", "dental": "Dentist",
    "restaurant": "Restaurant", "restaurants": "Restaurant",
    "roofer": "Roofer", "roofing": "Roofer", "roofers": "Roofer",
    "hvac": "HVAC", "hvac contractor": "HVAC", "air conditioning": "HVAC",
    "plumber": "Plumber", "plumbing": "Plumber", "plumbers": "Plumber",
    "landscaper": "Landscaper", "landscaping": "Landscaper",
    "painter": "Painter", "painting": "Painter", "painters": "Painter",
    "car detailing": "Car Detailing", "auto detailing": "Car Detailing",
    "auto repair": "Auto Repair", "mechanic": "Auto Repair",
    "cleaning service": "Cleaning Service", "cleaning": "Cleaning Service",
}

# Custom fields we populate, keyed by GHL fieldKey (stable across snapshot clones).
# Values are filled per-lead in _contact_custom_fields().
CUSTOM_FIELD_KEYS = [
    "contact.lead_score",
    "contact.google_rating",
    "contact.review_count",
    "contact.niche",
    "contact.website_exists",
    "contact.qualification_reason",
    "contact.lead_source__detailed",
    "contact.google_business_profile",
]


def _enabled() -> bool:
    """GHL push only runs when a token is configured. Otherwise it's skipped."""
    return bool(os.environ.get("GHL_API_TOKEN"))


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GHL_API_TOKEN']}",
        "Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ───────────────────────── Lookups (resolve by name, not ID) ─────────────────

def _resolve_pipeline_stage(location_id: str) -> tuple[Optional[str], Optional[str], str]:
    """
    Find the prospecting pipeline + the 'New (Scraped)' stage BY NAME.
    Returns (pipeline_id, stage_id, detail). Substring + case-insensitive so
    emoji/renames don't break it (e.g. '🔍 Prospecting' matches 'Prospecting').
    """
    resp = requests.get(
        f"{API_BASE}/opportunities/pipelines",
        headers=_headers(),
        params={"locationId": location_id},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    pipelines = resp.json().get("pipelines", [])

    want_pipe = DEFAULT_PIPELINE_MATCH.lower()
    want_stage = DEFAULT_STAGE_MATCH.lower()

    for p in pipelines:
        if want_pipe in p.get("name", "").lower():
            for s in p.get("stages", []):
                if want_stage in s.get("name", "").lower():
                    return p["id"], s["id"], f"{p['name']} → {s['name']}"
            # pipeline matched but stage didn't — fall back to first stage
            stages = p.get("stages", [])
            if stages:
                return p["id"], stages[0]["id"], f"{p['name']} → {stages[0]['name']} (fallback)"
    return None, None, f"no pipeline matching '{DEFAULT_PIPELINE_MATCH}'"


def _custom_field_id_map(location_id: str) -> dict:
    """Map fieldKey -> custom field ID for this sub-account (IDs differ per account)."""
    resp = requests.get(
        f"{API_BASE}/locations/{location_id}/customFields",
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    fields = resp.json().get("customFields", [])
    return {f.get("fieldKey"): f.get("id") for f in fields if f.get("fieldKey")}


# ───────────────────────── Payload building ─────────────────────────

def _niche_to_picklist(niche: str) -> str:
    return NICHE_PICKLIST.get((niche or "").strip().lower(), "Other")


def _website_exists(site_status: str) -> str:
    if site_status == "no_website":
        return "No"
    if site_status:
        return "Yes"
    return "Unknown"


def _short_bucket(bucket: Bucket) -> str:
    return {Bucket.HOT: "hot", Bucket.WARM: "warm", Bucket.COOL: "cool"}.get(bucket, "")


def _contact_custom_fields(sb: ScoredBusiness, niche: str, field_map: dict) -> list:
    """Build the customFields array, resolving fieldKey -> id and skipping unknowns."""
    b = sb.business
    raw = {
        "contact.lead_score": sb.score,
        "contact.google_rating": b.rating if b.rating is not None else "",
        "contact.review_count": b.review_count if b.review_count is not None else "",
        "contact.niche": _niche_to_picklist(niche),
        "contact.website_exists": _website_exists(sb.site_status),
        "contact.qualification_reason": sb.reason,
        "contact.lead_source__detailed": LEAD_SOURCE_VALUE,
    }
    out = []
    for key, value in raw.items():
        fid = field_map.get(key)
        if fid and value != "":
            out.append({"id": fid, "field_value": value})
    return out


def _contact_payload(sb: ScoredBusiness, location_id: str, state: str, niche: str,
                     field_map: dict, assigned_to: Optional[str]) -> dict:
    b = sb.business
    tags = ["scraped-lead", LEAD_SOURCE_VALUE.lower(),
            _niche_to_picklist(niche).lower(), f"temp-{_short_bucket(sb.bucket)}"]
    if state:
        tags.append(state.lower())
    payload = {
        "locationId": location_id,
        "name": b.name,
        "companyName": b.name,
        "source": LEAD_SOURCE_VALUE,
        "tags": [t for t in tags if t and not t.endswith("-")],
        "customFields": _contact_custom_fields(sb, niche, field_map),
    }
    if b.phone:
        payload["phone"] = b.phone
    if b.email:
        payload["email"] = b.email
    if b.website:
        payload["website"] = b.website
    if b.address:
        payload["address1"] = b.address
    if assigned_to:
        payload["assignedTo"] = assigned_to
    return payload


def _opportunity_payload(sb: ScoredBusiness, location_id: str, pipeline_id: str,
                         stage_id: str, contact_id: str, assigned_to: Optional[str]) -> dict:
    payload = {
        "locationId": location_id,
        "pipelineId": pipeline_id,
        "pipelineStageId": stage_id,
        "name": sb.business.name,
        "status": "open",
        "contactId": contact_id,
    }
    if assigned_to:
        payload["assignedTo"] = assigned_to
    return payload


# ───────────────────────── Dedup ─────────────────────────

def _dedup(scored: list[ScoredBusiness]) -> list[ScoredBusiness]:
    """Collapse duplicate leads within the batch by place_id, then phone, then email."""
    seen_place, seen_phone, seen_email = set(), set(), set()
    out = []
    for sb in scored:
        b = sb.business
        pid = (b.place_id or "").strip()
        phone = (b.phone or "").strip()
        email = (b.email or "").strip().lower()
        if pid and pid in seen_place:
            continue
        if phone and phone in seen_phone:
            continue
        if email and email in seen_email:
            continue
        if pid:
            seen_place.add(pid)
        if phone:
            seen_phone.add(phone)
        if email:
            seen_email.add(email)
        out.append(sb)
    return out


# ───────────────────────── Public API ─────────────────────────

def push_leads(scored: list[ScoredBusiness], state: str, niche: str,
               verbose: bool = True, dry_run: bool = False) -> dict:
    """
    Push scored leads into the state's GHL sub-account.

    Skipped entirely (returns a 'skipped' result) when GHL_API_TOKEN is not set,
    so non-GHL users are unaffected. Set dry_run=True (or GHL_DRY_RUN=1) to build
    and print payloads without making any API calls.
    """
    dry_run = dry_run or os.environ.get("GHL_DRY_RUN") == "1"

    if not _enabled() and not dry_run:
        if verbose:
            print("   GHL push skipped (no GHL_API_TOKEN set).")
        return {"status": "skipped", "pushed": 0, "deduped_out": 0}

    location_id = os.environ.get("GHL_LOCATION_ID", "<GHL_LOCATION_ID>")
    assigned_to = os.environ.get("GHL_ASSIGNED_USER_ID") or None

    leads = _dedup(scored)
    deduped_out = len(scored) - len(leads)
    if verbose:
        print(f"   {len(leads)} leads to push ({deduped_out} in-batch duplicates collapsed).")

    # ── Dry run: resolve nothing live; just build + show payloads ──
    if dry_run:
        sample_fields = {k: f"<id:{k}>" for k in CUSTOM_FIELD_KEYS}
        sample = leads[0] if leads else None
        print(f"   [DRY RUN] target: pipeline ~'{DEFAULT_PIPELINE_MATCH}' → stage ~'{DEFAULT_STAGE_MATCH}'")
        if sample:
            import json
            c = _contact_payload(sample, location_id, state, niche, sample_fields, assigned_to)
            print("   [DRY RUN] sample contact payload:")
            print("   " + json.dumps(c, indent=2).replace("\n", "\n   "))
        return {"status": "dry_run", "pushed": 0, "would_push": len(leads),
                "deduped_out": deduped_out}

    # ── Live: resolve targets once, then push each lead ──
    pipeline_id, stage_id, detail = _resolve_pipeline_stage(location_id)
    if not pipeline_id:
        print(f"   ⚠️  GHL push aborted — {detail}. Check the sub-account's pipeline names.")
        return {"status": "error", "pushed": 0, "error": detail}
    field_map = _custom_field_id_map(location_id)
    if verbose:
        print(f"   Target: {detail}")

    pushed, opps, errors = 0, 0, 0
    for sb in leads:
        try:
            r = requests.post(f"{API_BASE}/contacts/upsert", headers=_headers(),
                              json=_contact_payload(sb, location_id, state, niche, field_map, assigned_to),
                              timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            contact_id = (r.json().get("contact") or {}).get("id") or r.json().get("id")
            pushed += 1
            if contact_id:
                ro = requests.post(f"{API_BASE}/opportunities/", headers=_headers(),
                                   json=_opportunity_payload(sb, location_id, pipeline_id, stage_id, contact_id, assigned_to),
                                   timeout=REQUEST_TIMEOUT)
                if ro.status_code < 300:
                    opps += 1
        except requests.RequestException as e:
            errors += 1
            if verbose:
                print(f"   ⚠️  {sb.business.name}: {type(e).__name__}")

    if verbose:
        print(f"   ✅ Pushed {pushed} contacts, {opps} opportunities ({errors} errors).")
    return {"status": "ok", "pushed": pushed, "opportunities": opps,
            "errors": errors, "deduped_out": deduped_out}


# ───────────────────────── CLI: dry-run validation ─────────────────────────

if __name__ == "__main__":
    """Build payloads from a sample lead and print them — no API calls, no token needed."""
    from scraper import Business

    sample = [
        ScoredBusiness(
            business=Business(place_id="abc123", name="Joe's Barber Co.",
                              address="123 Main St, Denver, CO 80202",
                              phone="(303) 555-0147", email="joe@joesbarberco.com",
                              website="https://joesbarberco.com", rating=4.6, review_count=88),
            bucket=Bucket.HOT, score=100, reason="No website listed on Google",
            site_status="no_website"),
        # duplicate place_id — should be collapsed by dedup
        ScoredBusiness(
            business=Business(place_id="abc123", name="Joe's Barber Co. (dup)",
                              address="123 Main St", phone="(303) 555-0147"),
            bucket=Bucket.HOT, score=100, reason="dup", site_status="no_website"),
    ]
    print("─" * 60)
    print("GHL push — DRY RUN validation")
    print("─" * 60)
    result = push_leads(sample, state="Colorado", niche="barbers", dry_run=True)
    print(f"\n   result: {result}")
