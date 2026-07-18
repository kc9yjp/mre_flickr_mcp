Review your recent Flickr favorites grouped by photo owner, and decide who to follow or mark as a friend.

1. Call `get_faves` with limit=50, paging through (page=1, 2, 3, ...) to cover the last ~150 faves (3 pages) by default. If the user asks for a bigger or smaller window ("last 500", "just today"), adjust the page count.

2. Group the results by `owner` (NSID), counting distinct faved photos per owner and keeping a couple of example titles per owner.

3. Split owners into two buckets, ordered by fave count descending:
   - **Dozens** (10+ faved photos in the window) — strong friend candidates.
   - **A few** (2-9 faved photos) — follow candidates worth a look.
   Skip owners with only 1 fave — not a signal yet.

4. For each "dozens" owner:
   - Call `get_person_info` for username/realname.
   - Show: username, realname, count of faved photos, example titles.
   - Open their profile in Safari: `osascript -e 'tell application "Safari" to set URL of current tab of front window to "https://www.flickr.com/photos/<nsid>/"'`
   - Recommend marking as a friend. Wait for the user to confirm, downgrade to a plain follow, or skip.
   - Confirm → `follow_contact` with `is_friend=true`
   - Follow only → `follow_contact` with no friend flag
   - Skip → no action, move on

5. For each "a few" owner, same profile lookup + browser open, but just ask follow or skip:
   - Follow → `follow_contact`
   - Skip → no action, move on

6. Keep a running tally of new follows and new friends, and report the total at the end.

Note: `follow_contact` is safe to call on someone you already follow — it won't create a duplicate, and passing `is_friend=true` will just update their friend status.
