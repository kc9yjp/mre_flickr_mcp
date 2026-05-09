Find photos that qualify for view-count or fave-count threshold groups, and add 1-2 per group session.

## Threshold groups (already joined)

| Group | ID | Threshold |
|---|---|---|
| 10,000 Views Unlimited | 2337493@N25 | 10,000+ views |
| 5,000 Views | 48333387@N00 | 5,000+ views |
| 2,000 Views Unlimited | 2337875@N25 | 2,000+ views |
| Flickr's Finest (100+ Faves) | 910466@N22 | 100+ faves |
| 100 faves minimum | 14707878@N20 | 100+ faves |
| 50+ Favorites | 2888626@N21 | 50+ faves |
| 250 faves | 2838082@N25 | 250+ faves |
| 1000 views + 4 faves landscape | 59076347@N00 | 1,000+ views AND 4+ faves, landscape/landmarks only (no people, no animals) |

## Workflow

1. Ask the user which group(s) they want to boost today, or default to the most selective ones first (250 faves → 100 faves → 10k views → 5k views → 2k views).

2. Use `search_photos` sorted by `favorites` or `views` to find qualifying candidates.

3. For each selected group, suggest **1-2 photos maximum** that:
   - Meet the threshold
   - Are a good stylistic fit for the group (landscape groups: no people/animals)
   - Have not been mentioned earlier in this conversation as already submitted

4. Show the candidate photo title, views, faves, and photo page URL. Ask the user to confirm before adding.

5. If Flickr returns "already in pool" — note it and move to the next candidate.

6. After adding, remind the user: **1-2 per group per day max** to stay within group rules and avoid spam flags.

## Notes
- The local DB does not track group membership, so "already in pool" errors are expected and harmless.
- For the landscape group (1000 views + 4 faves), skip any photo with people or animals as the main subject.
- Do not add the same photo to more than 2-3 groups in a single session.
