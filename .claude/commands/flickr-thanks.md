Review recent comments on your photos and ensure each one has a reply.

Note: `get_recent_activity` only tracks activity on recently *uploaded* photos and is unreliable — do not use it. Instead, query the local DB directly.

1. Call `search_photos` with `sort_by="date_uploaded"`, `limit=100` to get the 100 most recently uploaded photos. Filter client-side to those with `comments > 0`. Also call `search_photos` with `sort_by="date_taken"`, `limit=100` as a second pass to catch older photos that may have received new comments. Deduplicate by photo ID.

2. For each photo with `comments > 0`, call `get_photo_comments` to get the full comment thread.

3. For each comment thread, check whether you have already replied by looking for any comment with `author_nsid == "45293338@N00"` that appears *after* the commenter's comment. Skip photos where all comments already have a reply.

4. For comments that have **no reply yet**, present them one at a time:
   - Show: photo title, commenter username, comment text
   - Open the photo in the browser using AppleScript: `osascript -e 'tell application "Safari" to set URL of current tab of front window to "<photo_url>"'`
   - Suggest at least 5 short reply options with variety (emoji-only, brief thanks, specific acknowledgment, warm, casual). Each suggestion must be formatted as a Flickr reply: `[<author_url>] <message>` using the `author_url` field from `get_photo_comments`.
   - Wait for user to pick a number, type custom text, or skip

5. Once confirmed, post the reply with `add_comment`. The comment must start with `[<author_url>]` to notify the commenter.

6. After each reply, ask "next?" and move to the next unreplied comment.

Keep a running count of replies posted this session.

Note: Never include self-promotional URLs in comments.
