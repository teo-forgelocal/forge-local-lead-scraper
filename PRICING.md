# Forge Local — Pricing Strategy

## The thesis
Build a better product at a better price to disrupt the lead-gen SaaS market.
Recurring monthly subscription model, SaaS-style. No one-time sales.

## Competitive landscape

| Tool | Price | What you get |
|------|-------|--------------|
| D7 Lead Finder | $9.90/mo | 100 leads/day (~$300/year) |
| Apollo.io | $49-99/mo | ~200 contacts/month with email |
| Phantombuster | $59-200/mo | Google Maps scrapers, broader automation |
| Outscraper | $25-249/mo | Similar scraping tools |

## Forge Local positioning
- Better lead quality (website scoring, "fake site" detection, tier system)
- Better UX (custom dashboard, daily email reports, color-coded sheets)
- Better value (more leads per dollar than Apollo/Phantombuster)
- Underserved markets angle (Tier 1 cities ignored by big agencies)

## Proposed tier structure (subject to revision)

### Starter — $39/mo
- 50 leads/day
- 1 niche, 1 state at a time
- Email-only reports
- For: solo agency owners, freelancers testing the waters

### Pro — $99/mo
- 200 leads/day
- Multi-niche, multi-state
- Full tier control (T1/T2/T3 toggling)
- Custom Google Sheets output
- For: established small agencies running real outreach

### Agency — $299/mo
- 500 leads/day
- All Pro features
- GoHighLevel direct CRM integration
- Email enrichment included (Hunter/Apollo via API)
- Demo-site auto-generation
- Multi-user team access
- For: full marketing agencies serving multiple clients

## Founder pricing (launch hook)
- First 50 customers: locked-in lifetime founder pricing
- Starter: $19/mo for life
- Pro: $49/mo for life
- Agency: $149/mo for life
- After 50: prices rise to standard tiers above

## Costs (real numbers to track)
- Google Places API: $200/mo free credit covers ~330 daily runs
  - Per run cost: $0.40-0.60 (15 API calls @ ~$0.032 each)
  - Per lead cost: ~$0.003 in raw API
- VPS hosting (when SaaS): $10-20/mo
- Email infrastructure: $20-40/mo (Instantly/Smartlead for outreach)
- Total operating cost at scale: $30-60/mo + per-user API
- Margin at $99/mo Pro tier: ~90%+

## Revenue projections (rough)
- 10 paying customers × $99 = $990/mo
- 50 paying customers × $99 = $4,950/mo
- 100 paying customers (mixed tiers, avg $79) = $7,900/mo
- Goal: 100 paying customers within 6 months of dashboard launch