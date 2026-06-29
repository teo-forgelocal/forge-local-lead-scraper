"""
The Scorer — judges website quality for each business.

Given a list of Business objects from the scraper, this module analyzes
each business's web presence and assigns:

  - A bucket: HOT / WARM / COOL
  - A numeric score (0-100, used for sorting within bucket)
  - A short human-readable reason

Bucket meanings:
  🔴 HOT   — No website at all, OR website is broken/dead
  🟠 WARM  — Has a "website" but it's a fake/placeholder (Linktree, Square,
             Facebook, Google Business profile) or it's outdated/poor quality
  🟡 COOL  — Has a real, functional website (may still be improvable, but
             the business isn't desperate for one)

Higher score = hotter lead. So a business with no website scores 100,
a broken site scores 90s, a Linktree scores 70s, a real-but-old site scores
30-50s, and a modern decent site scores 0-20s.

This module deliberately does NOT use heavy tools like Selenium or
headless browsers — those would slow scoring to seconds per business
and add fragile dependencies. We use simple HTTP fetching + regex
analysis of HTML, which is fast and reliable for our purposes.
"""

import re
import sys
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

# Add the src directory to Python's path so we can import from scraper.py
sys.path.insert(0, str(Path(__file__).parent))
from scraper import Business


# ───────────────────────── Bucket definitions ─────────────────────────

class Bucket(str, Enum):
    HOT = "temp:hot"
    WARM = "temp:warm"
    COOL = "temp:cool"


# ───────────────────────── Fake-website host patterns ─────────────────────────
#
# A "fake website" is one where the business technically has a URL but it's
# not actually their own website — it's a free placeholder hosted by a third
# party. These get flagged as warm leads because the business has zero
# real web presence they own.
#
# Pattern matching is done case-insensitively against the URL's hostname.

# Tier A — Definitely not a real website. Always warm.
DEFINITELY_FAKE_HOSTS = {
    "linktr.ee",
    "linktree.com",
    "beacons.ai",
    "bio.link",
    "lnk.bio",
    "milkshake.app",
    "carrd.co",
    "instagram.com",
    "facebook.com",
    "m.facebook.com",
    "fb.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "yelp.com",
    "yelp.to",
    "google.com",
    "g.page",  # Google Business shortlinks
    "poi.place",  # Google-generated "business profile" URLs
    "poi.place",  # Google-generated "business profile" URLs
    "edan.io",  # Another business directory placeholder host
    "bizon.site",  # Another directory placeholder
    "maps.google.com",
    "maps.app.goo.gl",
    "business.site",  # Google My Business free sites
    "negocio.site",
    "squareup.com",  # Square booking pages (not a real website)
}

# Tier B — Free templates. Could be a real site, could be a placeholder.
# We flag these but mark them with a softer "might be okay" reason.
FREE_TEMPLATE_HOSTS = {
    "square.site",  # Square's free site template — usually just a booking page
    "wixsite.com",
    "wix.com",
    "weebly.com",
    "godaddysites.com",
    "myshopify.com",  # Shopify default subdomain (no custom domain)
    "webnode.com",
    "jimdo.com",
    "jimdofree.com",
    "site123.me",
}


# ───────────────────────── Result dataclass ─────────────────────────

@dataclass
class ScoredBusiness:
    """A business plus its scoring result."""
    business: Business
    bucket: Bucket
    score: int           # 0-100, higher = hotter lead
    reason: str          # one-line human description
    site_status: str     # "no_website" | "broken" | "fake_*" | "ok"

    def as_row(self) -> dict:
        """Flatten to a dict suitable for writing to a spreadsheet row."""
        b = self.business
        return {
            "bucket": self.bucket.value,
            "score": self.score,
            "name": b.name,
            "address": b.address,
            "phone": b.phone or "",
            "email": b.email or "",
            "website": b.website or "",
            "site_status": self.site_status,
            "reason": self.reason,
            "rating": b.rating if b.rating else "",
            "review_count": b.review_count if b.review_count else "",
            "business_status": b.business_status or "",
            "place_id": b.place_id,
        }


# ───────────────────────── Core scoring functions ─────────────────────────

def score_business(business: Business, verbose: bool = False) -> ScoredBusiness:
    """
    Evaluate a single business's web presence and assign bucket/score/reason.
    """
    website = business.website

    # ── Case 1: No website at all ──
    if not website:
        return ScoredBusiness(
            business=business,
            bucket=Bucket.HOT,
            score=100,
            reason="No website listed on Google",
            site_status="no_website",
        )

   # ── Case 2: Check if it's a known fake-website host ──
    host = _extract_hostname(website)

    matched_fake = _matches_fake_host(host, DEFINITELY_FAKE_HOSTS)
    if matched_fake:
        return ScoredBusiness(
            business=business,
            bucket=Bucket.WARM,
            score=75,
            reason=f"Not a real website — {matched_fake} (Linktree/Facebook/Square/etc.)",
            site_status=f"fake_{matched_fake}",
        )

    matched_template = _matches_fake_host(host, FREE_TEMPLATE_HOSTS)
    if matched_template:
        return ScoredBusiness(
            business=business,
            bucket=Bucket.WARM,
            score=70,
            reason=f"Free template on {matched_template} — may need real site",
            site_status=f"template_{matched_template}",
        )

    # ── Case 3: Try to fetch the website ──
    fetch_result = _fetch_site(website, verbose=verbose)

    if fetch_result["status"] == "broken":
        return ScoredBusiness(
            business=business,
            bucket=Bucket.HOT,
            score=90,
            reason=f"Website broken/unreachable ({fetch_result['detail']})",
            site_status="broken",
        )

    # If the site redirected to a fake host, catch it now
    final_host = _extract_hostname(fetch_result.get("final_url", website))
    if final_host != host:
        matched_fake = _matches_fake_host(final_host, DEFINITELY_FAKE_HOSTS)
        if matched_fake:
            return ScoredBusiness(
                business=business,
                bucket=Bucket.WARM,
                score=75,
                reason=f"Redirects to {matched_fake} — not a real website",
                site_status=f"fake_redirect_{matched_fake}",
            )
        matched_template = _matches_fake_host(final_host, FREE_TEMPLATE_HOSTS)
        if matched_template:
            return ScoredBusiness(
                business=business,
                bucket=Bucket.WARM,
                score=70,
                reason=f"Redirects to {matched_template} template",
                site_status=f"template_redirect_{matched_template}",
            )

    # ── Case 4: Site loaded successfully — analyze content ──
    issues = []
    score = 30  # starting baseline for "has a real site"

    if not fetch_result["uses_https"]:
        issues.append("no HTTPS")
        score += 25

    if not fetch_result["has_viewport"]:
        issues.append("not mobile-friendly")
        score += 20

    if fetch_result["old_copyright_year"]:
        year = fetch_result["old_copyright_year"]
        issues.append(f"copyright dated {year}")
        score += 15

    if fetch_result["page_size"] < 5000:
        # Suspiciously tiny page — probably a placeholder or coming-soon page
        issues.append("very thin content")
        score += 15

    # Cap at 100 just in case
    score = min(score, 100)

    if issues:
        return ScoredBusiness(
            business=business,
            bucket=Bucket.WARM,
            score=score,
            reason="Has site but: " + ", ".join(issues),
            site_status="ok_outdated",
        )
    else:
        return ScoredBusiness(
            business=business,
            bucket=Bucket.COOL,
            score=score,
            reason="Has a functional website",
            site_status="ok",
        )


def _extract_hostname(url: str) -> str:
    """Pull out the hostname from a URL, lowercased, www stripped."""
    try:
        parsed = urlparse(url if "://" in url else "http://" + url)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _matches_fake_host(host: str, fake_host_set: set) -> Optional[str]:
    """
    Check if `host` matches any host in `fake_host_set`, either exactly
    or as a subdomain (e.g. valkyrie.square.site matches square.site).
    Returns the matched fake host string, or None.
    """
    if not host:
        return None
    for fake in fake_host_set:
        if host == fake or host.endswith("." + fake):
            return fake
    return None


def _fetch_site(url: str, verbose: bool = False) -> dict:
    """
    Try to load the site and gather basic quality signals.
    Returns a dict with:
      - status: "ok" | "broken"
      - detail: explanation if broken
      - uses_https: bool
      - has_viewport: bool (mobile-friendly indicator)
      - old_copyright_year: int or None (year if older than 2020, else None)
      - page_size: int (bytes of HTML)
    """
    result = {
        "status": "broken",
        "detail": "",
        "final_url": "",
        "uses_https": False,
        "has_viewport": False,
        "old_copyright_year": None,
        "page_size": 0,
    }

    # Normalize: if no scheme, assume http and we'll see if it redirects to https
    fetch_url = url if "://" in url else "http://" + url

    try:
        response = requests.get(
            fetch_url,
            timeout=15,
            allow_redirects=True,
            headers={
                # Some sites block default Python user agents.
                "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"),
            },
        )
    except requests.exceptions.SSLError:
        result["detail"] = "SSL error"
        return result
    except requests.exceptions.ConnectionError:
        result["detail"] = "connection refused / DNS failure"
        return result
    except requests.exceptions.Timeout:
        result["detail"] = "timeout (>15s)"
        return result
    except requests.RequestException as e:
        result["detail"] = f"request failed: {type(e).__name__}"
        return result

    if response.status_code >= 400:
        result["detail"] = f"HTTP {response.status_code}"
        return result

   # Site loaded successfully — analyze it
    result["status"] = "ok"
    final_url = response.url  # after redirects
    result["final_url"] = final_url
    result["uses_https"] = final_url.startswith("https://")
    result["page_size"] = len(response.content)

    html = response.text.lower()

    # Mobile viewport check — modern responsive sites include this meta tag
    result["has_viewport"] = '<meta name="viewport"' in html or "<meta name='viewport'" in html

    # Copyright year detection — look for "© 20XX" patterns
    # If the most recent year on the page is < 2020, flag as outdated.
    copyright_years = re.findall(r"(?:©|copyright|&copy;)\s*(\d{4})", html)
    # Keep only plausible years FIRST — a page can have a 4-digit match outside
    # this range (e.g. "© 1885" founding year), which would leave nothing to
    # max() over and crash. Guard against the empty case.
    valid_years = [int(y) for y in copyright_years if 1990 <= int(y) <= 2030]
    if valid_years:
        latest_year = max(valid_years)
        if latest_year < 2020:
            result["old_copyright_year"] = latest_year

    if verbose:
        print(f"   Fetched {final_url} → "
              f"HTTPS={result['uses_https']}, "
              f"viewport={result['has_viewport']}, "
              f"size={result['page_size']:,}b")

    return result


def score_businesses(businesses: list[Business], verbose: bool = False) -> list[ScoredBusiness]:
    """Score a whole batch of businesses. Returns sorted hottest-first."""
    scored = []
    for i, biz in enumerate(businesses, 1):
        if verbose:
            print(f"\n[{i}/{len(businesses)}] Scoring: {biz.name}")
        try:
            scored.append(score_business(biz, verbose=verbose))
        except Exception as e:
            # A single weird site must never abort the whole run (and lose the
            # API spend). Keep the lead, flag it for manual review.
            print(f"⚠️  Scoring failed for {biz.name}: {type(e).__name__} — flagged for review",
                  file=sys.stderr)
            scored.append(ScoredBusiness(
                business=biz,
                bucket=Bucket.WARM,
                score=50,
                reason=f"Scoring error ({type(e).__name__}) — review manually",
                site_status="error",
            ))

    # Sort: hotter buckets first, then higher score within bucket
    bucket_order = {Bucket.HOT: 0, Bucket.WARM: 1, Bucket.COOL: 2}
    scored.sort(key=lambda s: (bucket_order[s.bucket], -s.score))
    return scored


# ───────────────────────── CLI entry point for testing ─────────────────────────

if __name__ == "__main__":
    """
    Run scraper + scorer together for a quick end-to-end test.

    Usage:
        python src/scorer.py "barbers" "Fayetteville" "Arkansas" 10
    """
    if len(sys.argv) != 5:
        print("Usage: python src/scorer.py <niche> <city> <state> <max_results>")
        print('Example: python src/scorer.py "barbers" "Fayetteville" "Arkansas" 10')
        sys.exit(1)

    from scraper import scrape_businesses

    niche = sys.argv[1]
    city = sys.argv[2]
    state = sys.argv[3]
    max_results = int(sys.argv[4])

    print("─" * 60)
    print(f"PHASE 1: Scraping")
    print("─" * 60)
    businesses = scrape_businesses(niche, city, state, max_results=max_results)

    print()
    print("─" * 60)
    print(f"PHASE 2: Scoring")
    print("─" * 60)
    scored = score_businesses(businesses, verbose=True)

    # Print sorted results grouped by bucket
    print()
    print("─" * 60)
    print(f"RESULTS")
    print("─" * 60)

    by_bucket = {Bucket.HOT: [], Bucket.WARM: [], Bucket.COOL: []}
    for s in scored:
        by_bucket[s.bucket].append(s)

    for bucket in (Bucket.HOT, Bucket.WARM, Bucket.COOL):
        items = by_bucket[bucket]
        print(f"\n{bucket.value} — {len(items)} leads")
        for s in items:
            b = s.business
            print(f"  [{s.score:3d}] {b.name}")
            print(f"        Reason: {s.reason}")
            if b.website:
                print(f"        Site:   {b.website}")
            print(f"        Phone:  {b.phone or '(none)'}")

    print()
    print(f"Total: {len(scored)} businesses scored.")