"""
The Sheets Writer — creates a color-coded Google Sheet from scored leads.

Uses OAuth user credentials (not service account) so the resulting sheet
lives in the user's own Google Drive and uses their storage quota.

On first run: pops a browser window asking you to authorize the script
to access your Google Drive. Click Allow, browser closes, token gets
saved to credentials/oauth-token.json. From then on, no browser needed.

Takes a list of ScoredBusiness objects from scorer.py and writes them
to a brand new Google Sheet, formatted with:

  - Three sections (🔴 HOT / 🟠 WARM / 🟡 COOL) with fixed bold headers
  - Color-coded rows by bucket
  - Hyperlinked Google Maps URLs
  - Frozen header row for scrolling

Returns the URL of the created sheet.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Load environment variables
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from scorer import ScoredBusiness, Bucket


# OAuth scopes — what permissions we'll request from the user
# Must include Gmail send too, so a single token covers all our needs.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.send",
]

# Tag applied to every exported lead so the manual CSV import into GHL fires the
# cold-scraper-intake workflow. Keep this string EXACT — spelling, casing, the
# "flag:" prefix, and the hyphens all must match the GHL workflow trigger.
INTAKE_TAG = "flag:cold-scraper-intake"

# Column layout — order matters for the spreadsheet
COLUMNS = [
    ("Bucket", 90),
    ("Score", 60),
    ("Name", 220),
    ("Address", 280),
    ("Phone", 130),
    ("Email", 220),
    ("Website", 280),
    ("Reason", 320),
    ("Rating", 70),
    ("Reviews", 80),
    ("Tags", 200),
]

# Background colors for each bucket (Google Sheets accepts RGB 0-1 floats)
BUCKET_COLORS = {
    Bucket.HOT:  {"red": 1.00, "green": 0.85, "blue": 0.85},
    Bucket.WARM: {"red": 1.00, "green": 0.93, "blue": 0.80},
    Bucket.COOL: {"red": 1.00, "green": 1.00, "blue": 0.85},
}

HEADER_BG = {"red": 0.20, "green": 0.20, "blue": 0.22}
HEADER_FG = {"red": 1.00, "green": 1.00, "blue": 1.00}


# ───────────────────────── Authentication ─────────────────────────

def _get_client() -> gspread.Client:
    """
    Get an authenticated gspread client using OAuth user credentials.

    First run: opens browser, asks user to allow, saves token to disk.
    Subsequent runs: reads saved token, refreshes if expired.
    """
    client_path = os.environ.get("GOOGLE_OAUTH_CLIENT")
    token_path = os.environ.get("GOOGLE_OAUTH_TOKEN")

    if not client_path or not token_path:
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT and GOOGLE_OAUTH_TOKEN must be set in .env"
        )

    full_client_path = PROJECT_ROOT / client_path
    full_token_path = PROJECT_ROOT / token_path

    if not full_client_path.exists():
        raise FileNotFoundError(
            f"OAuth client file not found: {full_client_path}"
        )

    creds = None

    # If we have a saved token, try to load it
    if full_token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(full_token_path), SCOPES
            )
        except Exception as e:
            print(f"⚠️  Could not load saved token ({e}); will re-authorize.")
            creds = None

    # If no valid creds, run the auth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            print("🔐 First-time authorization needed. Opening browser...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(full_client_path), SCOPES
            )
            # run_local_server opens a browser and listens on localhost:0
            # for the callback. User clicks Allow → token comes back.
            # prompt="select_account" forces Google's account chooser so the
            # user can't silently re-auth as the wrong account.
            creds = flow.run_local_server(port=0, prompt="select_account")

        # Save token for next time
        full_token_path.parent.mkdir(parents=True, exist_ok=True)
        full_token_path.write_text(creds.to_json())
        print(f"✅ Token saved to {full_token_path}")

    return gspread.authorize(creds)


# ───────────────────────── Sheet creation ─────────────────────────

def create_leads_sheet(
    scored_leads: list[ScoredBusiness],
    niche: str,
    city: str,
    state: str,
    tier: int,
    share_with: str = None,  # kept for API compatibility, but OAuth user already owns it
    verbose: bool = True,
) -> str:
    if verbose:
        print(f"📊 Creating Google Sheet for {len(scored_leads)} leads...")

    client = _get_client()

    today = datetime.now().strftime("%Y-%m-%d")
    state_abbrev = state[:2].upper()
    title = f"Forge Local — {today} — {niche} in {city}, {state_abbrev} (T{tier})"

    if verbose:
        print(f"   Title: {title}")

    # Create the spreadsheet — inside the shared Drive folder if configured.
    # With OAuth, the sheet is owned by the user, so the storage quota issue
    # we hit with service accounts doesn't apply.
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    spreadsheet = None
    if folder_id:
        try:
            spreadsheet = client.create(title, folder_id=folder_id)
            if verbose:
                print(f"   Sheet created in Drive folder. ID: {spreadsheet.id}")
        except Exception as e:
            # A bad/inaccessible GOOGLE_DRIVE_FOLDER_ID — e.g. a folder owned by
            # a different Google account than the one authorized — must NOT kill
            # the whole run after the scrape + API spend. Fall back to My Drive.
            print(f"   ⚠️  GOOGLE_DRIVE_FOLDER_ID not usable ({type(e).__name__}: "
                  f"folder missing or owned by another account) — saving to My Drive instead.")
            spreadsheet = None
    if spreadsheet is None:
        spreadsheet = client.create(title)
        if verbose:
            print(f"   Sheet created in My Drive. ID: {spreadsheet.id}")

    ws = spreadsheet.sheet1
    ws.update_title("Leads")

    # Build rows
    rows_to_write = [_header_row()]

    by_bucket = {Bucket.HOT: [], Bucket.WARM: [], Bucket.COOL: []}
    for sb in scored_leads:
        by_bucket[sb.bucket].append(sb)

    section_ranges = []
    current_row = 2

    for bucket in (Bucket.HOT, Bucket.WARM, Bucket.COOL):
        items = by_bucket[bucket]
        if not items:
            continue
        items.sort(key=lambda s: -s.score)
        start_row = current_row
        for sb in items:
            rows_to_write.append(_data_row(sb))
            current_row += 1
        section_ranges.append((bucket, start_row, current_row - 1))

    # Resize and write
    needed_rows = len(rows_to_write)
    needed_cols = len(COLUMNS)
    if needed_rows > ws.row_count or needed_cols > ws.col_count:
        ws.resize(rows=max(needed_rows, ws.row_count), cols=needed_cols)

    if verbose:
        print(f"   Writing {len(rows_to_write)} rows ({needed_cols} columns)...")
    ws.update(values=rows_to_write, range_name=f"A1:{_col_letter(needed_cols)}{needed_rows}")

    if verbose:
        print(f"   Applying formatting...")
    _apply_formatting(spreadsheet, ws, section_ranges, needed_cols)

    sheet_url = spreadsheet.url
    if verbose:
        print(f"✅ Sheet ready: {sheet_url}")

    return sheet_url


# ───────────────────────── Row building ─────────────────────────

def _header_row() -> list[str]:
    return [col[0] for col in COLUMNS]


def _data_row(sb: ScoredBusiness) -> list:
    b = sb.business
    return [
        sb.bucket.value,
        sb.score,
        b.name,
        b.address,
        b.phone or "",
        b.email or "",
        b.website or "",
        sb.reason,
        b.rating if b.rating else "",
        b.review_count if b.review_count else "",
        INTAKE_TAG,
    ]


# ───────────────────────── Formatting ─────────────────────────

def _apply_formatting(spreadsheet, ws, section_ranges, num_cols):
    sheet_id = ws.id
    requests_payload = []

    # Header row formatting
    requests_payload.append({
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
                "startColumnIndex": 0,
                "endColumnIndex": num_cols,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": HEADER_BG,
                    "textFormat": {
                        "foregroundColor": HEADER_FG,
                        "bold": True,
                        "fontSize": 11,
                    },
                    "horizontalAlignment": "LEFT",
                    "verticalAlignment": "MIDDLE",
                },
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
        }
    })

    # Freeze the header row
    requests_payload.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 1},
            },
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # Column widths
    for col_idx, (_, width) in enumerate(COLUMNS):
        requests_payload.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_idx,
                    "endIndex": col_idx + 1,
                },
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    # Color each bucket section
    for bucket, start_row, end_row in section_ranges:
        color = BUCKET_COLORS[bucket]
        requests_payload.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": color,
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    },
                },
                "fields": "userEnteredFormat(backgroundColor,verticalAlignment,wrapStrategy)",
            }
        })

    spreadsheet.batch_update({"requests": requests_payload})


def _col_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


# ───────────────────────── CLI entry point ─────────────────────────

if __name__ == "__main__":
    if len(sys.argv) != 6:
        print("Usage: python src/sheets.py <niche> <city> <state> <max_results> <your_email>")
        print('Example: python src/sheets.py "barbers" "Fayetteville" "Arkansas" 10 you@gmail.com')
        sys.exit(1)

    from scraper import scrape_businesses
    from scorer import score_businesses
    from email_finder import enrich_with_emails

    niche = sys.argv[1]
    city = sys.argv[2]
    state = sys.argv[3]
    max_results = int(sys.argv[4])
    user_email = sys.argv[5]

    print("─" * 60)
    print(f"PHASE 1: Scraping")
    print("─" * 60)
    businesses = scrape_businesses(niche, city, state, max_results=max_results)

    print()
    print("─" * 60)
    print(f"PHASE 1.5: Finding emails")
    print("─" * 60)
    found = enrich_with_emails(businesses, verbose=False)
    with_site = sum(1 for b in businesses if b.website)
    print(f"   Found {found} emails among {with_site} businesses with a website.")

    print()
    print("─" * 60)
    print(f"PHASE 2: Scoring")
    print("─" * 60)
    scored = score_businesses(businesses, verbose=False)

    by_bucket = {Bucket.HOT: 0, Bucket.WARM: 0, Bucket.COOL: 0}
    for s in scored:
        by_bucket[s.bucket] += 1
    print(f"   🔴 HOT: {by_bucket[Bucket.HOT]}  "
          f"🟠 WARM: {by_bucket[Bucket.WARM]}  "
          f"🟡 COOL: {by_bucket[Bucket.COOL]}")

    print()
    print("─" * 60)
    print(f"PHASE 3: Creating Google Sheet")
    print("─" * 60)
    url = create_leads_sheet(
        scored_leads=scored,
        niche=niche,
        city=city,
        state=state,
        tier=1,
        share_with=user_email,
    )

    print()
    print(f"🎉 Open your sheet here:")
    print(f"   {url}")