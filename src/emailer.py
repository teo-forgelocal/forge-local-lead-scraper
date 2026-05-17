"""
The Emailer — sends the daily lead report via Gmail API.

Reuses the same OAuth token as sheets.py (after we added the gmail.send
scope), so no separate auth flow is needed. The user receives an HTML
email with a summary of today's run and a button to open the sheet.
"""

import base64
import os
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from scorer import ScoredBusiness, Bucket


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.send",
]


def _get_gmail_service():
    client_path = os.environ.get("GOOGLE_OAUTH_CLIENT")
    token_path = os.environ.get("GOOGLE_OAUTH_TOKEN")

    if not client_path or not token_path:
        raise RuntimeError(
            "GOOGLE_OAUTH_CLIENT and GOOGLE_OAUTH_TOKEN must be set in .env"
        )

    full_client_path = PROJECT_ROOT / client_path
    full_token_path = PROJECT_ROOT / token_path

    creds = None
    if full_token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(
                str(full_token_path), SCOPES
            )
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            print("🔐 OAuth re-authorization needed (Gmail scope added). Opening browser...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(full_client_path), SCOPES
            )
            creds = flow.run_local_server(port=0)

        full_token_path.parent.mkdir(parents=True, exist_ok=True)
        full_token_path.write_text(creds.to_json())
        print(f"✅ Token saved to {full_token_path}")

    return build("gmail", "v1", credentials=creds)


def _build_html_email(scored, niche, city, state, tier, sheet_url):
    by_bucket = {Bucket.HOT: [], Bucket.WARM: [], Bucket.COOL: []}
    for s in scored:
        by_bucket[s.bucket].append(s)

    today = datetime.now().strftime("%A, %B %d, %Y")

    hot_leads_html = ""
    hot_list = sorted(by_bucket[Bucket.HOT], key=lambda s: -s.score)[:5]
    if hot_list:
        hot_items = "".join(
            f"<li><b>{s.business.name}</b> — {s.reason} "
            f"<span style='color:#888;font-size:12px;'>({s.business.phone or 'no phone'})</span></li>"
            for s in hot_list
        )
        hot_leads_html = f"""
        <h3 style="margin-bottom:8px;color:#c0392b;">🔴 Hot leads to look at first</h3>
        <ol style="margin-top:0;padding-left:20px;">{hot_items}</ol>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width:640px; margin:0 auto; padding:24px; color:#2c2c2c;">
      <h1 style="font-size:22px;margin-bottom:4px;">Forge Local — Daily Report</h1>
      <p style="color:#666;margin-top:0;">{today}</p>
      <div style="background:#f5f5f5;border-radius:8px;padding:16px;margin:16px 0;">
        <p style="margin:0;font-size:14px;color:#555;">Today's run:</p>
        <p style="margin:4px 0 0 0;font-size:18px;"><b>{niche}</b> in <b>{city}, {state}</b> &nbsp;<span style="color:#888;font-size:14px;">(Tier {tier})</span></p>
      </div>
      <table style="border-collapse:collapse;margin:16px 0;width:100%;">
        <tr>
          <td style="padding:12px;border-radius:6px 0 0 6px;background:#ffe6e6;text-align:center;width:33%;">
            <div style="font-size:28px;font-weight:bold;color:#c0392b;">{len(by_bucket[Bucket.HOT])}</div>
            <div style="font-size:12px;color:#666;">🔴 HOT</div>
          </td>
          <td style="padding:12px;background:#fff0d6;text-align:center;width:33%;">
            <div style="font-size:28px;font-weight:bold;color:#d35400;">{len(by_bucket[Bucket.WARM])}</div>
            <div style="font-size:12px;color:#666;">🟠 WARM</div>
          </td>
          <td style="padding:12px;border-radius:0 6px 6px 0;background:#fffadd;text-align:center;width:33%;">
            <div style="font-size:28px;font-weight:bold;color:#a08020;">{len(by_bucket[Bucket.COOL])}</div>
            <div style="font-size:12px;color:#666;">🟡 COOL</div>
          </td>
        </tr>
      </table>
      {hot_leads_html}
      <p style="margin-top:24px;">
        <a href="{sheet_url}"
           style="display:inline-block;padding:12px 24px;background:#0b6b8a;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold;">
          Open full sheet →
        </a>
      </p>
      <p style="font-size:12px;color:#999;margin-top:32px;border-top:1px solid #eee;padding-top:16px;">
        Sent by the Forge Local agent. Total leads scraped: {len(scored)}.
      </p>
    </body>
    </html>
    """


def send_daily_report(scored, niche, city, state, tier, sheet_url, to_email, verbose=True):
    if verbose:
        print(f"📧 Sending daily report to {to_email}...")

    service = _get_gmail_service()

    today = datetime.now().strftime("%Y-%m-%d")
    state_abbrev = state[:2].upper()
    subject = f"Forge Local Daily — {niche} in {city}, {state_abbrev} (T{tier})"

    body_html = _build_html_email(scored, niche, city, state, tier, sheet_url)

    message = MIMEMultipart("alternative")
    message["to"] = to_email
    message["subject"] = subject
    message.attach(MIMEText(body_html, "html"))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    sent = service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()

    if verbose:
        print(f"✅ Email sent (id: {sent['id']})")

    return sent["id"]


if __name__ == "__main__":
    if len(sys.argv) != 6:
        print("Usage: python src/emailer.py <niche> <city> <state> <max_results> <your_email>")
        sys.exit(1)

    from scraper import scrape_businesses
    from scorer import score_businesses
    from sheets import create_leads_sheet

    niche = sys.argv[1]
    city = sys.argv[2]
    state = sys.argv[3]
    max_results = int(sys.argv[4])
    user_email = sys.argv[5]

    print("─" * 60)
    print("PHASE 1: Scraping")
    print("─" * 60)
    businesses = scrape_businesses(niche, city, state, max_results=max_results)

    print()
    print("─" * 60)
    print("PHASE 2: Scoring")
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
    print("PHASE 3: Creating Google Sheet")
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
    print("─" * 60)
    print("PHASE 4: Sending email")
    print("─" * 60)
    send_daily_report(
        scored=scored,
        niche=niche,
        city=city,
        state=state,
        tier=1,
        sheet_url=url,
        to_email=user_email,
    )

    print()
    print(f"🎉 Pipeline complete. Check your inbox at {user_email}.")