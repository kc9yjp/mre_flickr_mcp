Review recent comments on your photos and ensure each one has a reply.

1. Call `get_recent_activity` with `timeframe="7d"` to get recent activity.

2. Filter to only `type="comment"` events. Collect the unique photo IDs that have comments.

3. For each photo with comments, call `get_photo_comments` to get the full comment thread.

4. For each comment thread, check whether you (ejwettstein / nsid 45293338@N00) have already replied. A reply counts if any comment in the thread by you appears *after* the commenter's comment.

5. For comments that have **no reply yet**, present them one at a time:
   - Show: photo title, commenter username, comment text
   - Open the photo in the browser using AppleScript: `osascript -e 'tell application "Safari" to set URL of current tab of front window to "<photo_url>"'`
   - Suggest at least 5 short reply options with variety (emoji-only, brief thanks, specific acknowledgment, warm, casual). Each suggestion must be formatted as a Flickr reply: `[<author_url>] <message>` using the `author_url` field from `get_photo_comments`.
   - Wait for user to pick a number, type custom text, or skip

6. Once confirmed, post the reply with `add_comment`. The comment must start with `[<author_url>]` to notify the commenter.

7. After each reply, ask "next?" and move to the next unreplied comment.

Keep a running count of replies posted this session.

Note: Never include self-promotional URLs in comments.
