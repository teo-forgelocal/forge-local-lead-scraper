"""
The Email Finder — extracts a business email from the business's own website.

The scraper pulls phone numbers from Google Places, but Places does NOT return
email addresses. For CRM import we need an email wherever one is publicly
available on the business's own site. This module fills that gap.

How it works (per business):
  1. Fetch the homepage.
  2. Pull emails from mailto: links first (most reliable), then from a regex
     scan of the page text.
  3. If the homepage has none, fetch ONE contact page (a "contact" link found
     on the page, else /contact) — capped at a single extra request. No crawl.
  4. Filter out junk (example.com, sentry, wixpress, retina image filenames
     like logo@2x.png, asset URLs, obvious placeholders).

Honest by design:
  - Returns None when there's no website (many local businesses have none —
    that's expected, not an error; those leads are reached by phone).
  - Returns None when the site is dead, blocks us, or simply has no email.
  - NEVER fabricates info@<domain> from the domain name. It only returns an
    email actually present on the page. An empty result is a valid, truthful
    result.

Expected real-world hit rate: roughly 40-70% on businesses that HAVE a website.

Like the scorer, this deliberately uses plain HTTP + parsing (no Selenium /
headless browser) so it stays fast and dependency-light across many businesses.
"""

import html as html_lib
import re
import sys
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# A single request must never hang the daily run over hundreds of businesses.
REQUEST_TIMEOUT = 5  # seconds, per request

# Realistic browser UA — many sites block default python-requests agents.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Don't waste time (or memory) parsing giant pages — emails live near the top.
MAX_BYTES = 2_000_000

# Core email shape. Intentionally conservative on the TLD (letters only) to
# avoid matching version strings, file paths, and the like.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")

# Retina/asset artifacts that look email-ish in raw HTML (e.g. "logo@2x.png").
RETINA_RE = re.compile(r"@\dx\b", re.I)
ASSET_RE = re.compile(
    r"\.(png|jpe?g|gif|svg|webp|bmp|ico|tiff?|css|js|mp4|webm|woff2?|pdf)(\b|$|@)",
    re.I,
)
ASSET_EXTS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico",
    "tif", "tiff", "css", "js", "mp4", "webm", "woff", "woff2", "pdf",
}

# Substrings in the DOMAIN that mean "not the business's real email".
JUNK_DOMAIN_SUBSTRINGS = (
    "example.com", "example.org", "example.net", "example.edu",
    "sentry",            # sentry.io, *.sentry.wixpress.com error tracking
    "wixpress.com",      # Wix internal
    "wix.com",
    "godaddy",
    "cloudflare",
    "schema.org",
    "w3.org",
    "sentry.io",
    "domain.com", "yourdomain.com", "yourcompany.com",
    "company.com", "website.com", "email.com", "yourwebsite.com",
    "mysite.com", "yoursite.com",   # Wix/website-builder default placeholders
    "sentry.wixpress.com",
)

# Mailbox names that are real but useless for sales outreach / CRM import.
SKIP_LOCALS = {
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "postmaster", "mailer-daemon", "abuse", "webmaster@local",
}

# Obvious template placeholders ("name@email.com", "you@example.com", ...).
PLACEHOLDER_LOCALS = {
    "you", "your", "email", "name", "username", "user",
    "example", "test", "sample", "firstname", "lastname", "yourname",
}


# ───────────────────────── Public API ─────────────────────────

def find_email(website_url: str) -> Optional[str]:
    """
    Find a business email by scraping its own website.

    Args:
        website_url: the business's website (may be falsy — many have none).

    Returns:
        A lowercased email string if one is found on the homepage or a single
        contact page, otherwise None. Never raises — a dead/blocking site
        returns None.
    """
    # Many businesses have no website. That's expected, not an error.
    if not website_url:
        return None

    base = website_url if "://" in website_url else "http://" + website_url
    site_host = _registrable_domain(base)

    # ── 1. Homepage ──
    html = _fetch(base)
    if html:
        email = _extract_best(html, site_host)
        if email:
            return email

    # ── 2. ONE contact page (capped at a single extra request, no crawl) ──
    contact_url = _pick_contact_url(html, base)
    if contact_url:
        html2 = _fetch(contact_url)
        if html2:
            email = _extract_best(html2, site_host)
            if email:
                return email

    return None


def enrich_with_emails(businesses, verbose: bool = False) -> int:
    """
    Populate `.email` on each Business that has a website. Mutates in place.

    Businesses with no website are skipped (left as None) — they're reached by
    phone, not email. Returns the count of emails found.
    """
    found = 0
    total = len(businesses)
    for i, b in enumerate(businesses, 1):
        if not b.website:
            if verbose:
                print(f"   [{i}/{total}] {b.name}: no website — skip")
            continue
        email = find_email(b.website)
        if email:
            b.email = email
            found += 1
        if verbose:
            print(f"   [{i}/{total}] {b.name}: {email or '(none found)'}")
    return found


# ───────────────────────── Fetching ─────────────────────────

def _fetch(url: str) -> Optional[str]:
    """
    GET a URL and return its HTML text, or None on any failure.

    A dead site, a timeout, a block, a non-HTML response — all return None.
    This function must never raise.
    """
    try:
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
            headers=HEADERS,
        )
    except requests.RequestException:
        return None
    except Exception:
        # Belt-and-suspenders: malformed URLs etc. must not crash the run.
        return None

    if resp.status_code >= 400:
        return None

    # Skip binaries (PDFs, images) — only parse HTML.
    content_type = resp.headers.get("Content-Type", "").lower()
    if content_type and "html" not in content_type and "xml" not in content_type:
        return None

    return resp.text[:MAX_BYTES]


# ───────────────────────── Extraction ─────────────────────────

def _extract_best(raw_html: str, site_host: str) -> Optional[str]:
    """
    Extract the best email from a page: mailto: links first, then page text.
    Prefer an address on the site's own domain over a third-party one.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    # mailto: links — the most reliable signal (explicit "email me" intent).
    mailto_candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            cleaned = _clean_email(href)
            if _is_valid_email(cleaned):
                mailto_candidates.append(cleaned)

    # Regex scan over (most of) the HTML, not just visible text: small-business
    # sites (Wix, Squarespace, GlossGenius, and JSON-LD LocalBusiness markup)
    # routinely stash the real email inside <script> JSON blobs that visible-text
    # extraction skips entirely. But first drop <style> blocks and HTML comments:
    # real contact emails never live in CSS or comments, whereas font/CSS license
    # credits (e.g. a typeface designer's address) do — and those are wrong-entity
    # false positives. <script> is kept on purpose. html.unescape then catches
    # entity-encoded addresses like "sean&#64;shop.com".
    scan = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw_html, flags=re.I | re.S)
    scan = re.sub(r"<!--.*?-->", " ", scan, flags=re.S)
    scan = html_lib.unescape(scan)
    text_candidates = []
    for match in EMAIL_RE.findall(scan):
        cleaned = _clean_email(match)
        if _is_valid_email(cleaned):
            text_candidates.append(cleaned)

    # Prefer mailto (explicit intent), then text. Within each group, prefer an
    # address on the business's own domain over a vendor/third-party address.
    for group in (mailto_candidates, text_candidates):
        ordered = list(dict.fromkeys(group))  # dedupe, preserve first-seen order
        same_domain = [e for e in ordered if site_host and e.endswith("@" + site_host)]
        if same_domain:
            return same_domain[0]
        if ordered:
            return ordered[0]

    return None


def _clean_email(raw: str) -> str:
    """Normalize a raw match or mailto href into a bare lowercased address."""
    e = raw.strip()
    if e.lower().startswith("mailto:"):
        e = e[len("mailto:"):]
    # Drop any mailto query string (?subject=...&cc=...).
    e = e.split("?", 1)[0]
    # URL-encoded mailto links sometimes wrap the address.
    e = e.replace("%20", "").strip()
    # Trim stray punctuation/brackets picked up from surrounding text.
    e = e.strip(".,;:()<>[]{}\"'` \t\r\n")
    return e.lower()


def _is_valid_email(email: str) -> bool:
    """True only for a plausible, useful business email (filters the junk)."""
    if not email or email.count("@") != 1:
        return False

    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return False

    # Retina/asset filename artifacts ("logo@2x.png", "sprite.png@...").
    if RETINA_RE.search(email) or ASSET_RE.search(email):
        return False

    tld = domain.rsplit(".", 1)[-1]
    if tld in ASSET_EXTS:
        return False
    # Real TLDs are alphabetic; this also rejects numeric/garbage tails.
    if not re.fullmatch(r"[a-z]{2,24}", tld):
        return False

    if local in SKIP_LOCALS or email in SKIP_LOCALS:
        return False
    if local in PLACEHOLDER_LOCALS:
        return False

    for junk in JUNK_DOMAIN_SUBSTRINGS:
        if junk in domain:
            return False

    return True


def _registrable_domain(url: str) -> str:
    """
    The site's domain (last two labels, www stripped), lowercased.
    Used only to prefer same-domain emails — a heuristic, not a hard rule.
    """
    try:
        host = (urlparse(url).netloc or "").lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        parts = host.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return host
    except Exception:
        return ""


def _pick_contact_url(homepage_html: Optional[str], base: str) -> Optional[str]:
    """
    Choose a single contact page to try as the one allowed extra fetch.

    Prefer a real "contact" link discovered on the homepage; otherwise fall
    back to /contact. Returns None if there's nothing sensible to try.
    """
    # Prefer an actual contact link on the page (handles /contact-us, /kontakt,
    # /get-in-touch, etc. without a second guess-and-fetch).
    if homepage_html:
        soup = BeautifulSoup(homepage_html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(" ").strip().lower()
            if href.lower().startswith("mailto:") or href.startswith("#"):
                continue
            if "contact" in href.lower() or "contact" in text:
                resolved = urljoin(base, href)
                # Only follow links that stay on the same site.
                if _registrable_domain(resolved) == _registrable_domain(base):
                    return resolved

    # Fallback: the single most common contact path.
    return urljoin(base, "/contact")


# ───────────────────────── CLI entry point for testing ─────────────────────────

if __name__ == "__main__":
    """
    Test find_email() against real URLs and report the hit rate.

    Usage:
        python src/email_finder.py <url> [<url> ...]
    """
    if len(sys.argv) < 2:
        print("Usage: python src/email_finder.py <url> [<url> ...]")
        sys.exit(1)

    urls = sys.argv[1:]
    hits = 0
    print(f"Testing find_email() on {len(urls)} URL(s)...\n")
    for url in urls:
        start = time.time()
        email = find_email(url)
        elapsed = time.time() - start
        if email:
            hits += 1
            print(f"  ✅ {email:<40s} ({elapsed:4.1f}s)  {url}")
        else:
            print(f"  ⬜ {'(none)':<40s} ({elapsed:4.1f}s)  {url}")

    rate = hits / len(urls) * 100 if urls else 0
    print(f"\nHit rate: {hits}/{len(urls)} = {rate:.0f}%")
