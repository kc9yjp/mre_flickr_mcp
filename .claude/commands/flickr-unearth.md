Review private photos from oldest to newest and decide which ones to publish.

Use `search_photos` with `is_public=false`, `sort_by=random`, `limit=50` to get a random batch of private photos. Skip any already reviewed this session.

For each candidate:

1. Fetch the image using `fetch_photo_image` so you can see it visually.
2. Open the photo page in the browser so the user can see it at full resolution:
   `osascript -e 'tell application "Safari" to set URL of current tab of front window to "<photo-page-url>"'`
   If that fails (Safari not open), try opening in Chrome via AppleScript:
   `osascript -e 'tell application "Google Chrome" to set URL of active tab of front window to "<photo-page-url>"'`
3. Give a brief honest visual assessment — composition, light, subject, era (older smartphone shots get more latitude). Note what makes it worth sharing (or not).
4. Give a clear recommendation: **publish** or **keep private**. Wait for the user to decide.

If the user says **publish**:
- Suggest a concise title, 1-2 sentence description, and relevant tags (location, subject, style, equipment if apparent). Wait for confirmation.
- Apply with `update_photo`, then `set_visibility` with `is_public=true`.
- Search groups with `find_groups` (2-3 keyword searches). Number all group choices including the catch-all groups (Anything at all, all photos of whatever no 30/60, Seen On a Walk). Only add groups the user picks by number.
- Open the photo in the browser using AppleScript: `osascript -e 'tell application "Safari" to set URL of current tab of front window to "<photo-page-url>"'`

If the user says **keep private** (or **skip**): move to the next photo.

After each photo, ask "next?" and repeat with the next oldest unreviewed private photo.

Keep a running count of how many have been published this session.

Remember: artsy shooter, Chicago/Oak Park area, no year tags, concatenated lowercase compound tags (oakpark not oak-park), no linktree links.
