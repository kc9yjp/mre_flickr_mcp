# Boost Plan for the Top 5 — Notes for Later

Follow-up to `README_ranking_strategies.md` and the five true-top-5 photo READMEs
(`README_sky_and_cosmos.md` etc.). Recorded 2026-07-17 so a future session can execute
without re-deriving anything.

## The thesis

The top five convert viewers into favers at **4–7%**, versus ~0.1% for the most-viewed
photos on the account (Lady Jane at the Lake: 12,942 views, 9 faves). They are
**reach-limited, not quality-limited** — Winter Morning turns ~1 in 14 views into a fave,
so every 1,000 new views ≈ 70 faves. Boosting = distribution, not improvement.

## Where each photo stands (2026-07-17)

| Photo | Faves | Views | Fave rate | Groups | Headroom |
|---|---|---|---|---|---|
| Sky and Cosmos | 306 | 5,204 | 5.9% | **109** | Groups ~saturated; boost via search + off-Flickr |
| OBX Sunrise 2024 | 289 | 8,636 | 3.3% | 67 | Moderate |
| Oak Park Sunset | 233 | 9,001 | 2.6% | **39** | High — 2nd priority |
| Sun through the leaves | 211 | 8,506 | 2.5% | 62 | Moderate |
| Winter Morning | 206 | 3,016 | **6.8%** | **31** | **Highest — start here** |

Best converters (Winter Morning, Sky and Cosmos) are the least/most-saturated in groups
respectively — Winter Morning is the clearest target: fewest groups, fewest views,
highest fave rate, fastest fave velocity (~27/month since Dec 2025).

## On-Flickr moves

1. **Group placement where headroom exists** — order: Winter Morning (31) → Oak Park
   Sunset (39) → Sun through the leaves (62) → OBX Sunrise (67). Follow the house rules:
   numbered group proposals, user picks the numbers, max 3 photos per group per day.
2. **Threshold groups via `/flickr-boost`** — all five qualify for elite tiers: 100+/200
   fave clubs; 1,000/3,000/5,000/8,000-view milestone groups. Members of those groups are
   the fave-dispensing audience; qualification is the entry ticket.
3. **Search-title tuning** — Sky and Cosmos is under-discovered (#1 faves on only 5.2k
   views). Add plainly searchable phrasing ("white cosmos flowers from below",
   "pink winter sunrise snowy street"). Search traffic is proven on this account (13k
   views on Lady Jane at the Lake).
4. **Landing-surface placement** — set Sky and Cosmos as photostream cover / profile
   showcase; pin a small "Best of" album at the top of the photostream so profile
   visitors hit the proven five.
5. **Activity nudges** — reply to standing comments (Sky and Cosmos has 48) to resurface
   in followers' activity feeds; add all five to an own-curated gallery for one more
   discovery surface.

## Off-Flickr moves

1. **Reddit** (largest free reach): r/itookapicture (Sky and Cosmos, Winter Morning),
   r/CloudPorn (Oak Park Sunset), r/SkyPorn, r/oceans / r/waves (OBX Sunrise). Upload
   natively, Flickr link in a comment.
2. **Local Chicago/Oak Park press & socials** — NBC5/WGN/FOX32 weather accounts repost
   viewer sky photos (Oak Park Sunset, Winter Morning are the genre); Oak Park Facebook
   groups; Wednesday Journal; village socials.
3. **Instagram** — feed Winter Morning and Sky and Cosmos through the existing
   Instagram-album workflow (album id 72157652110141900; remember the "instagram" tag).
4. **Print/shows** — Winter Morning as holiday card; Sky and Cosmos to Oak Park Art
   League / café wall shows; county fair photo competition.
5. **Publish the critic series** — the five photo READMEs are essays with embedded
   images; posted to a blog/site they market the photostream with backlinks.

## LIVE: scheduled group adds for OBX Sunrise 2024 (queued 2026-07-17)

Photo 53889678803 is queued into **25 groups at 2/day, 5:00 PM Chicago, Jul 17 – Jul 29
2026, random order** via the server's `pending_group_adds` queue. The background refresh
loop now flushes due queue items automatically (`_flush_queue_for_user` in
`scripts/tools/sync.py`, added 2026-07-17) — no Claude session needed for the adds to
post. Inspect progress via the `/queue` web page, the `get_group_queue` MCP tool, or
`docker compose logs -f flickr-mcp | grep "Group-add queue"`. Do **not** re-suggest these
25 groups for this photo; when the run completes (~Jul 29), re-check faves vs. the
300 target and consider the 9,000/10,000 Views Unlimited rungs (photo was at 8,636
views when queued).

## Execution order (when picked up)

1. Reconnect MCP (`/mcp`) if stale.
2. Draft numbered group proposals for Winter Morning, then Oak Park Sunset.
3. Run `/flickr-boost` threshold pass across all five.
4. Search-title tweaks on Sky and Cosmos (read existing metadata first, per house rules).
5. Off-Flickr: pick one Reddit sub per photo; queue the two Instagram candidates.
