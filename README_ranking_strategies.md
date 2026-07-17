# Photo Ranking Strategies — Notes for Later

Born out of the top-5-by-faves README exercise (July 2026). The five photo READMEs
(`README_sunrise_outer_banks.md` etc.) were selected by a plain favorites sort; this file
records the alternative strategies discussed, so a future session can implement or run them.

**Context:** 8,216 public photos (71,968 total), date range 1967 → present. All five
all-time fave leaders are from summer 2013 — partly genuine, partly 13 years of compounding.

## Baseline (what was used)

`get_popular_photos(sort=favorites)` — effectively `ORDER BY favorites DESC LIMIT 5`.
A pure crowd vote, structurally biased toward old photos.

**Tool wart found:** the response only includes a `views` field no matter which key it
sorted by — when sorting by favorites you can't see the fave counts in the output. Fix:
always return views, favorites, and comments.

## Metric strategies

| Strategy | What it measures | Caveat |
|---|---|---|
| Raw favorites | Absolute crowd approval | Age bias; rewards search/Explore luck |
| Raw views | Reach | Mostly thumbnail appeal + search traffic, not merit |
| Raw comments | Social engagement | Inflated by group award threads — partly measures group posting |
| Fave rate (faves ÷ views) | Conversion: of those who saw it, how many loved it | Small samples explode; needs a floor or shrinkage |
| Wilson / Bayesian fave rate | Confidence-adjusted conversion (IMDb-style) | Needs a prior weight `m` tuned to the stream |
| Faves per year since upload | Age-normalized approval | Surfaces whether recent work outperforms the 2013 bloc |
| Stranger faves | Faves from non-contacts = organic appeal, minus reciprocity | Needs `get_photo_faves` per photo (`you_follow` flag); API-costly at scale, fine on a shortlist |
| Composite strength score | Weighted faves + comments + views | Mirror image of `find_weak_photos`'s weakness score |
| Flickr interestingness | Flickr's own black-box blend | API only (`sort=interestingness-desc`); not in local DB |
| The critic | Ignore the numbers | See curation strategies below |

### Worked example — fave rate reshuffles the current top five

| Photo | Faves | Views | Fave rate | Faves rank → rate rank |
|---|---|---|---|---|
| Sunrise in the Outer Banks | 154 | 12,274 | 1.25% | #1 → #1 |
| Mrs Photo 516 | 14 | 3,448 | 0.41% | #3 → **#2** |
| Lighthouse at Manteo | 36 | 10,453 | 0.34% | #2 → #3 |
| Morning sun portrait | 5 | 3,694 | 0.14% | #5 → #4 |
| Lady Jane at the Lake | 9 | 12,942 | 0.07% | #4 → **#5** |

Fave rate agrees with the critic: *Mrs Photo 516* climbs, the most-viewed photo falls to
last. Views ≠ approval.

## Data availability

- **Pure SQL over local DB** (photos table has views, favorites, comments, date_uploaded):
  raw sorts, fave rate, Wilson/Bayesian, per-year, composite score.
- **Flickr API required:** stranger faves (per-photo `get_photo_faves`), interestingness.

## Implementation sketch (server-side, not client-side jq)

Extend `get_popular_photos` in `scripts/flickr_mcp.py`:

- New sort modes: `fave_rate`, `per_year`, `bayesian` (shrinkage prior pulled toward the
  stream's mean fave rate; add a `min_views` floor to keep noise out).
- Always include `views`, `favorites`, `comments` in every row of the response (fixes the
  wart above).
- Optional someday: a `strength_score` mode mirroring `find_weak_photos`.

## Curation strategies (a numbers-blind "critic's five")

The top-5 exercise critiqued a crowd-made shortlist — the critic never surveyed the stream.
To actually curate:

1. **Stat-guided hunting** — use the DB to find where sleepers live (decent fave rate on
   low views, zero-engagement photos with promising tags/eras), then look only at those.
   Fast, but numbers still pre-filter.
2. **Stratified sample (preferred)** — sample ~250 public photos balanced across decades
   and subjects (film-scan era → 2013 Rebel XT/iPod era → recent phone era), visually
   review, shortlist ~20, study those at full res, then write `README_critics_five.md`
   with five picks and a written case for each — plus a verdict on whether the crowd's
   five deserved their seats.
3. **Full survey in installments** — review all 8,216 public photos in batches across
   sessions, `/flickr-unearth`-style but with a critic's eye. No sampling asterisk;
   weeks of sessions.
