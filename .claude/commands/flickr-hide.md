Find the next weak photo candidate using the `find_weak_photos` MCP tool (require_zero_favorites=true, limit=30). Skip any photos that have already been reviewed this session (have a title that's not blank and not equal to the photo ID, or have been seen before in this conversation).

For the top unreviewed candidate:

1. Fetch the image using `fetch_photo_image` so you can see it visually.
2. Open it in Safari using osascript (navigate current tab, don't open new tab).
3. Give an honest visual assessment — look at composition, light, subject, and technical quality. Factor in the era it was shot (early smartphone photos get more latitude). Note what works and what doesn't.
4. Give a clear recommendation: keep public or make private. Explain briefly why.
5. Wait for the user to decide before taking any action.

If the user says **private**: suggest a concise title, description, and tags, then apply with `update_photo`, then call `set_visibility` to make it private.

If the user says **keep**: suggest metadata. Once confirmed, apply with `update_photo`. Ask if they want to add to groups.

After each photo, ask "next?" and repeat from step 1 with the next unreviewed candidate.

Keep a running count of how many have been made private this session.
