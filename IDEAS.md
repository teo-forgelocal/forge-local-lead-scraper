# Forge Local — Ideas & Future Features

Capture ideas here as they come up. Don't act on them mid-build.
Revisit this list when planning V2 / the dashboard.

## 🚨 MUST FIX BEFORE BETA LAUNCH

### Business-level deduplication
**Status:** Critical bug. Currently only city-level dedup exists.

**The problem:** Agent tracks which cities it has scraped, but NOT which 
individual businesses. If buyers run the agent multiple times per day, or 
if cities overlap geographically (e.g. Springdale + Fayetteville), they 
will receive the same businesses in multiple sheets. This will FRUSTRATE 
buyers and damage trust during pre-launch.

**The fix:**
1. Add a `seen_businesses` storage layer (SQLite for local v1, Supabase 
   for SaaS).
2. Store every Google Place ID returned by the scraper, per user.
3. Before adding a business to today's output, check if its Place ID is 
   already in the user's history. If yes, skip.
4. Add option for buyer to set "dedup window" — e.g. "show me businesses 
   I haven't seen in the last 30 days" vs "ever."

**Effort:** ~30-45 minutes of code.

**Priority:** P0 — must ship before any beta testers get access.

**Why this matters:** A buyer running the agent daily will hit duplicates 
within 1-2 weeks. They will think the product is broken. They will 
churn. They will not buy again.

## V2 candidates (dashboard era)
- Web dashboard with login
- Run history view (cities + states already scraped)
- Toggle/form UI for niche, state, tier selection
- Status reports for current and past runs
- Per-user accounts with isolated data
- Multi-tenant: invite team members to a shared workspace

## Lead pipeline ideas
- **Email enrichment (Phase 5 / post-validation):**
  - Why: Google Places API does not return business emails by policy.
  - Approach: After 1-2 weeks of running the agent, evaluate lead quality.
    If keepers/day > ~20 and worth pursuing, add Hunter.io, Apollo.io, or
    Dropcontact integration as a step between scrape() and write_to_sheet().
  - Cost: $34-49/month minimum for usable hit rates on small businesses.
  - Hit rate expectation: 20-40% for sub-150k city local businesses.
  - Until then: manually look up emails for ~10-20 keepers/day via
    Apollo free tier (60/mo) or contact-page scraping.
- Optional: scrape business websites for `info@` / `contact@` emails as
  a free bonus enrichment step (catches the easy ones, ~50% coverage at best
  since target leads often have no website).
- Direct GoHighLevel CRM integration (push keeper leads via GHL API).
- Automated demo-site generation per keeper lead (Claude generates HTML
  template with their logo from Google Maps + business name).
- Outreach sequences from warmed dedicated domain (NOT GHL bulk sender).

## Product/marketing
- TikTok build-in-public series
- First 10 beta users via TikTok audience
- Pricing tiers TBD

## Random / parking lot
- (add ideas here as they come up)